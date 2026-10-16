#!/usr/bin/env python3
"""Search evidence for negative conclusions ("图上无", "未见") in neutral CAD evidence packages.

Contract: _qto-shared-evidence/references/否定结论检索.md. Reads layout records, block definitions (hits expanded by the
number of insertions, nested included) and block attributes; places every hit on the sheet table; lists objects it could
not read; writes one record per requested conclusion with a reverse check. It never decides whether a hit is the same
object or which bill item it belongs to: matches against existing results are only listed as candidates.
"""
from __future__ import annotations

import argparse, csv, hashlib, json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import qto_cad_geometry as cad_geometry  # noqa: E402

VERSION = '1.1.3'
INPUT_SCHEMA, OUTPUT_SCHEMA = 'qto-negative-search-input/1.0', 'qto-negative-search/1.0'
KINDS = ('spec', 'tray', 'box', 'model', 'pipe', 'text', 'block')
SHEET_KINDS = ('平面', '系统图', '表与说明', '图例', '详图', '其他')
OFF_SHEET = '台账外'
STATUS = {'no_hit_full': '全覆盖未命中', 'no_hit_partial': '待确认', 'hit': '有命中'}
JUDGMENTS = ('非同一对象', '同一对象')
UNREAD_TYPES = {'ACAD_PROXY_ENTITY': '代理对象', 'PROXY': '代理对象', 'OLE2FRAME': '嵌入对象', 'OLEFRAME': '嵌入对象', 'IMAGE': '图片', 'PDFUNDERLAY': '底图参考', 'DWFUNDERLAY': '底图参考', 'DGNUNDERLAY': '底图参考'}
NON_DRAWING = r'印章|签章|水印|签名|会签|STAMP|SEAL|WATERMARK|SIGNATURE'
MAX_DEPTH, SAMPLES = 16, 10
MTEXT_CODE = re.compile(r'\\[ACcFfHhQTWp][^;\\]*;|\\[LlOoKkNnPpXx~]|[{}]')
SPEC_GROUP = re.compile(r'(\d+(?:\.\d+)?)\s*[xX×*]\s*(\d+(?:\.\d+)?)')


TEXT_BASIS = '文字插入点'
HIT_DETAIL = '否定结论检索命中明细.csv'
HIT_COLUMNS = ['检索记录编号', '条目', '命中类别', '图号', '图页', '图页名', '图页类型', '句柄', '块内句柄', '图层', '文字', '统一写法',
               '经由', '定位依据', '判定', '判定序号', '判定依据']


class InputError(ValueError):
    def __init__(self, problems, detail=None):
        super().__init__('；'.join(problems[:5])); self.problems = problems; self.detail = detail


def sha_file(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()


FLAT_MAP = str.maketrans({'×': 'X', '*': 'X', '（': '(', '）': ')', '＋': '+', '－': '-', '—': '-'})


def flat(s):
    """Text in one comparable form: MTEXT codes gone, upper case, multiply signs as X, ASCII brackets, no spaces, hyphens or
    underscores, no diameter marks (JDG%%C20 and JDGΦ20 read as JDG20; %%C is AutoCAD's code for the diameter sign)."""
    s = MTEXT_CODE.sub(' ', str(s)).upper().translate(FLAT_MAP)
    return re.sub(r'[\s\-_]+|%%C|[ΦφØø∅]', '', s)


def flat_sep(s):
    """flat() keeping one '-' where the text had spaces, hyphens or underscores, so a box number matches as whole words:
    1B-LPB-N-3 inside 01-F2-1B-LPB-N-3 or before a size, not inside 11B-LPB-N-3 (registration 20261017-0015)."""
    s = re.sub(r'%%C|[ΦφØø∅]', '', MTEXT_CODE.sub(' ', str(s)).upper().translate(FLAT_MAP))
    return re.sub(r'[\s\-_]+', '-', s).strip('-')


def bounded(core, first, last, whole=False):
    """Edges so 4x150 never matches 4x1500 and AL1 never matches BAL12. whole: a spec must not be part of a longer
    spec either (4x150 is not 4x150+1x70, 1x70 is not the tail of 4x150+1x70); those go to the secondary search."""
    pre = (r'(?<![0-9.+])' if whole else r'(?<![0-9.])') if first.isdigit() else r'(?<![A-Z0-9])' if first.isalnum() else ''
    post = r'(?![0-9.])' if last.isdigit() else r'(?![A-Z])' if last.isalnum() else ''
    if whole and last.isdigit(): post += r'(?!\+\d+(?:\.\d+)?X)'
    return pre + core + post


def word_bounded(core, first, last):
    """Box numbers on flat_sep text, as whole words: no letter or digit may touch either end, so 1B-PPB-N-Z is not
    1B-PPB-N-Z1 and 1B-LPB-N-3 is not A1B-LPB-N-3 or 11B-LPB-N-3; a digit end also refuses a decimal point."""
    pre = r'(?<![A-Z0-9.])' if first.isdigit() else r'(?<![A-Z0-9])' if first.isalnum() else ''
    post = r'(?![A-Z0-9.])' if last.isdigit() else r'(?![A-Z0-9])' if last.isalnum() else ''
    return pre + core + post


def section_core(term):
    """`4x150+1x70` -> regex on flat text; None when the term has no a×b group."""
    groups = SPEC_GROUP.findall(term)
    if not groups: return None
    return r'\+'.join(re.escape(a) + 'X' + re.escape(b) for a, b in groups)


def variants(item):
    """Main and secondary patterns (on flat text), block-name patterns and a readable list of the written forms covered."""
    kind, term = item['kind'], str(item.get('term') or '')
    main, second, shown, notes = [], [], [], []
    aliases = [flat(a) for a in item.get('aliases') or []]
    if kind in ('spec', 'tray'):
        core = section_core(term)
        if not core: raise ValueError(f'{item["item_id"]}：{kind} 的 term 里找不到 a×b 规格')
        start = SPEC_GROUP.search(term).start()
        sec = SPEC_GROUP.sub(lambda m: m.group(1) + 'x' + m.group(2), term[start:]).strip()
        models = [m for m in [flat(term[:start]), *aliases] if m]
        par = r'(?:\d+\()?'
        if kind == 'spec' and models:
            main.append(bounded('(?:' + '|'.join(map(re.escape, models)) + ')' + par + core + r'\)?', models[0][0], '0', whole=True))
            notes.append('型号 ' + '、'.join(dict.fromkeys([term[:start].strip(' -'), *(item.get('aliases') or [])])))
            second.append(bounded(core, '0', '0'))
        else:
            main.append(bounded(core, '0', '0', whole=True))
            second.append(bounded(core, '0', '0'))
        shown += [sec, sec.replace('x', '×'), sec.replace('x', '*'), sec.replace('x', 'X'), f'n({sec})']
        notes.append('大小写不分，空格、连字符省略；规格须写全，是更长规格的一部分的（如 4x150 之于 4x150+1x70）只算次级')
    elif kind in ('box', 'model', 'pipe'):
        forms = [flat(term), *aliases]
        if not forms[0]: raise ValueError(f'{item["item_id"]}：term 为空')
        cores = [re.escape(f) for f in forms]
        if kind == 'pipe':   # SC20 also written SC%%C20, SCΦ20 (flat drops the mark) or SC DN20
            cores = [re.sub(r'^([A-Z]+)(\d)', r'\1(?:DN)?\2', c) for c in cores]
        if kind == 'box':
            # whole words on flat_sep (registration 20261017-0015): a separator may sit between any two characters, and
            # the edge test then sees the separator, so a short number inside a full number with a prefix is found
            sep_cores = ['-?'.join(re.escape(ch) for ch in f) for f in forms]
            main.append((word_bounded('(?:' + '|'.join(sep_cores) + ')', forms[0][0], forms[0][-1]), 'flat_sep'))
        else:
            main.append(bounded('(?:' + '|'.join(cores) + ')', forms[0][0], forms[0][-1]))
        shown += [term, *(item.get('aliases') or [])]
        notes.append('大小写不分，空格、连字符、下划线、直径符号省略' + ('；管材与管径之间可带 DN' if kind == 'pipe' else ''))
        if kind == 'box':
            notes.append('箱号按整段词匹配：前面是连字符、空格、下划线或文字开头时，完整编号里的短号也算命中（如 01-F2-1B-LPB-N-3 里的 '
                         '1B-LPB-N-3），后面隔开接尺寸的也算（1A-GPB-N-2 600*2000*400）；两端紧挨字母或数字的不算（11B-LPB-N-3、'
                         'A1B-LPB-N-3 不是 1B-LPB-N-3，1B-PPB-N-Z1 不是 1B-PPB-N-Z）；'
                         '不带分隔符连写的完整编号（01F21BLPBN3）分不出短号从哪开始，只作次级')
            # digit runs from the written form: flat() glues F2-1B into F21B, while the text keeps the hyphen there
            segs = [s for s in flat_sep(term).split('-') if s]
            near = '-?'.join('-?'.join(r'\d+' if u.isdigit() else re.escape(u) for u in re.findall(r'\d+|.', s)) for s in segs)
            if any(ch.isdigit() for ch in forms[0]): second.append((bounded(near, forms[0][0], forms[0][-1]), 'flat_sep')); notes.append('近似编号（数字不同或末尾多出编号）只作次级')
    elif kind == 'text':
        for t in [term, *(item.get('aliases') or [])]:
            if t: main.append(re.escape(flat(t))); shown.append(t)
    elif kind == 'block':
        pass
    main += [flat_regex(p) for p in item.get('patterns') or []]
    sec = item.get('secondary') or {}
    second += [flat_regex(p) for p in sec.get('patterns') or []]
    blocks = [re.compile(p, re.I) for p in item.get('block_names') or []]
    if kind == 'block' and not blocks: raise ValueError(f'{item["item_id"]}：block 类要写 block_names')
    if not main and not blocks: raise ValueError(f'{item["item_id"]}：没有可检索的写法')
    # (pattern, text form): user patterns and every kind but box read flat(); box numbers read flat_sep()
    fielded = lambda ps: [(re.compile(p), 'flat') if isinstance(p, str) else (re.compile(p[0]), p[1]) for p in ps]
    return {'main': fielded(main), 'second': fielded(second), 'blocks': blocks,
            'exclude': [re.compile(flat_regex(p)) for p in item.get('exclude') or []],
            'second_exclude': [re.compile(flat_regex(p)) for p in sec.get('exclude') or []],
            'shown': list(dict.fromkeys(shown)), 'notes': notes,
            'second_note': sec.get('description') or {'spec': '同截面任意型号，含更长规格的一部分', 'tray': '更长规格的一部分', 'box': '近似编号'}.get(kind, '次级写法')}


def flat_regex(p):
    """User patterns are written for flat text; check they compile."""
    re.compile(p); return p


# ---------- reading ----------

def read_sheets(spec, base, did, problems):
    if not spec: return []
    path = base / spec['path']; cols = {'sheet_id': 'sheet_id', 'title': 'title', 'sheet_kind': 'sheet_kind', 'x1': 'x1', 'y1': 'y1', 'x2': 'x2', 'y2': 'y2', 'layout': 'layout', **(spec.get('columns') or {})}
    rows = []
    try:
        with path.open(encoding='utf-8-sig', newline='') as f:
            for n, r in enumerate(csv.DictReader(f), 2):
                raw = (r.get(cols['sheet_kind']) or '').strip()
                kind = (spec.get('kind_map') or {}).get(raw, raw) or '其他'
                if kind not in SHEET_KINDS: problems.append(f'{did} 图页表第 {n} 行图页类型 {kind} 不认识（{"、".join(SHEET_KINDS)}）'); kind = '其他'
                try: box = [float(r[cols[k]]) for k in ('x1', 'y1', 'x2', 'y2')] if all((r.get(cols[k]) or '').strip() for k in ('x1', 'y1', 'x2', 'y2')) else None
                except ValueError: problems.append(f'{did} 图页表第 {n} 行范围不是数'); box = None
                if box: box = [min(box[0], box[2]), min(box[1], box[3]), max(box[0], box[2]), max(box[1], box[3])]
                rows.append({'sheet': r.get(cols['sheet_id']) or '', 'title': r.get(cols['title']) or '', 'kind': kind, 'box': box, 'layout': (r.get(cols['layout']) or '').strip()})
    except OSError as e: problems.append(f'{did} 图页表读不了：{e}')
    return rows


def place(sheets, layout, x, y):
    """Sheet of a point, as the point coverage ledger picks it: of the model-space sheets holding the point the one with
    the smallest area (registration 20261017-0012); a paper-space layout row goes to its layout's sheet."""
    if layout and layout != 'Model':
        s = next((s for s in sheets if s['layout'] == layout), None)
        if s: return s
    if x is None or y is None: return None
    hits = [s for s in sheets if s['box'] and (not s['layout'] or s['layout'] == 'Model')
            and s['box'][0] <= x <= s['box'][2] and s['box'][1] <= y <= s['box'][3]]
    return min(hits, key=lambda s: (s['box'][2] - s['box'][0]) * (s['box'][3] - s['box'][1])) if hits else None


def apply(m, x, y): return (m[0] * x + m[1] * y + m[3], m[4] * x + m[5] * y + m[7])


def chain(a, b):
    """a then b (b is the outer insert): world = b(a(p)); 4x4 row-major with the translation in the fourth column."""
    return [sum(b[r * 4 + k] * a[k * 4 + c] for k in range(4)) for r in range(4) for c in range(4)]


def texts_of(r):
    """(text, via) pairs a record carries: its own text and every attribute value."""
    out = []
    if r.get('entity_type') in ('TEXT', 'MTEXT', 'ATTRIB') and r.get('text'): out.append((r['text'], 'text'))
    for tag, val in (r.get('attributes') or {}).items():
        if val not in (None, ''): out.append((str(val), 'attribute:' + str(tag)))
    return out


def read_drawing(d, base, non_drawing, overrides, problems):
    """One drawing: layout text occurrences, block text occurrences, the nested INSERT graph and what could not be read."""
    nd = base / d['neutral_dir']
    lay, blk = nd / '中性布局图元.ndjson', nd / '中性块定义图元.ndjson'
    if not lay.exists() or not blk.exists(): return None
    man = json.loads((nd / 'CAD中性取证清单.json').read_text(encoding='utf-8-sig')) if (nd / 'CAD中性取证清单.json').exists() else {}
    meta = {str(b.get('block_name')): b for b in man.get('blocks', []) if isinstance(b, dict) and b.get('block_name')}
    empty = frozenset(n for n, b in meta.items() if cad_geometry.is_empty_definition(b))
    folders = {}   # block -> folded extent rows, the point coverage ledger's way (definitions can be hundreds of MB)
    occ, binst, parents, inserted, unread = [], defaultdict(list), defaultdict(list), Counter(), Counter()
    tops = defaultdict(list)
    for path, layer_kind in ((lay, 'layout'), (blk, 'block')):
        with path.open(encoding='utf-8') as f:
            for line in f:
                r = json.loads(line); et = r.get('entity_type') or ''
                parent = r.get('parent_block') if layer_kind == 'block' else None
                if parent and r.get('row_type', 'entity') == 'entity':
                    folder = folders.get(parent)
                    if folder is None:
                        folder = folders[parent] = cad_geometry.DefinitionExtentFolder(empty, cad_geometry.DYNAMIC_STATE_CODES)
                    folder.add(r)
                own = {'block': r['block_name'], 'matrix': r.get('insert_matrix_wcs')} if et == 'INSERT' and r.get('block_name') else {}
                if et == 'INSERT' and r.get('block_name'):
                    m = r.get('insert_matrix_wcs')
                    if layer_kind == 'layout':
                        if m: tops[r['block_name']].append((m, r.get('layout'), r.get('entity_handle')))
                        inserted[r['block_name']] += 1
                    elif parent and m: parents[r['block_name']].append((parent, m)); inserted[r['block_name']] += 1
                    rb = (r.get('raw_geometry') or {}).get('block_reference') or {}
                    if rb.get('xref') and rb.get('definition_state') != 'available': unread[('外部参照缺失', r.get('layer') or '', classify(r, non_drawing, overrides, d['id']))] += 1
                kind = UNREAD_TYPES.get(et) or ('代理对象' if 'PROXY' in str(r.get('object_name') or '').upper() else None)
                if kind: unread[(kind, r.get('layer') or '', classify(r, non_drawing, overrides, d['id']))] += 1
                for text, via in texts_of(r):
                    # an INSERT's attribute values are placed with the INSERT (own carries its block and matrix)
                    rec = {'text': text, 'flat': flat(text), 'flat_sep': flat_sep(text), 'via': via, 'layer': r.get('layer'),
                           'handle': r.get('entity_handle'), 'x': r.get('x'), 'y': r.get('y'), 'layout': r.get('layout'), **own}
                    if layer_kind == 'layout': occ.append(rec)
                    elif parent and not parent.startswith('*D'): binst[parent].append(rec)   # dimension blocks only hold measured values
                if et == 'INSERT' and r.get('block_name'):
                    rec = {'layer': r.get('layer'), 'handle': r.get('entity_handle'), 'x': r.get('x'), 'y': r.get('y'), 'layout': r.get('layout'), 'via': 'block', **own}
                    if layer_kind == 'layout': occ.append({**rec, 'text': None, 'flat': '', 'flat_sep': ''})
                    elif parent: binst[parent].append({**rec, 'text': None, 'flat': '', 'flat_sep': ''})
    definitions = {name: folder.rows() for name, folder in folders.items()}
    origins = {n: (b.get('origin') or [0.0, 0.0, 0.0]) for n, b in meta.items()}
    extents = {}

    def extent(name):
        """(local box, notes) by cad_geometry.definition_extent for sheet placement (skip_unknown), the rule of the
        coverage ledger."""
        try:
            return cad_geometry.definition_extent(name, definitions, origins, MAX_DEPTH, extents, (), empty, cad_geometry.DYNAMIC_STATE_CODES,
                                                  skip_unknown=True)
        except (ValueError, TypeError, IndexError):
            return None, frozenset()
    return {'occ': occ, 'blocks': binst, 'parents': parents, 'tops': tops, 'inserted': inserted, 'unread': unread, 'extent': extent,
            'neutral': {'dir': str(nd), 'layout_sha256': sha_file(lay), 'block_sha256': sha_file(blk)},
            'unserialized_blocks': sum(1 for b in man.get('blocks', []) if b.get('serialized') is False)}


def classify(r, non_drawing, overrides, did):
    for o in overrides:
        if o['drawing'] == did and (not o.get('handles') or r.get('entity_handle') in o['handles']) and (not o.get('entity_type') or o['entity_type'] == r.get('entity_type')) \
                and (not o.get('layer') or o['layer'] == r.get('layer')):
            return o['class']
    name = ' '.join(str(r.get(k) or '') for k in ('layer', 'block_name', 'object_name'))
    return 'non_drawing' if non_drawing.search(name) else 'may_contain'


def instances(dr, block, cache):
    """World matrices of every insertion of a block reached from a layout, nested insertions expanded."""
    if block in cache: return cache[block]
    cache[block] = []   # cycle guard
    out = [(m, lay, h) for m, lay, h in dr['tops'].get(block, [])]
    for parent, m in dr['parents'].get(block, []):
        for pm, lay, h in instances(dr, parent, cache):
            out.append((chain(m, pm), lay, h))
    cache[block] = out[:200000]
    return cache[block]


# ---------- search ----------

def match(o, pats, excl, var):
    if o['text'] is None: return any(p.search(o['block']) for p in var['blocks'])
    if not any(p.search(o[form]) for p, form in pats): return False
    return not any(p.search(o['flat']) for p in excl)


def anchor(dr, o, m=None):
    """(x, y, basis) of one occurrence, m the world matrix of the block holding it (None in a layout). A block, for its
    name or an attribute it carries, sits at the centre of its transformed definition extent and falls back to its
    insertion point, the point coverage ledger's rule (registration 20261017-0012); text sits at its own insertion point."""
    if o.get('x') is None or o.get('y') is None:
        return None, None, '无坐标'
    point = (o['x'], o['y']) if m is None else apply(m, o['x'], o['y'])
    if not o.get('block'):
        return point[0], point[1], TEXT_BASIS
    box, notes = dr['extent'](o['block']) if o.get('matrix') else (None, frozenset())
    world = None
    if box is not None:
        try:
            world = cad_geometry.transform_bounds(box, o['matrix'] if m is None else chain(o['matrix'], m))
        except (ValueError, TypeError):
            world = None
    (x, y), basis = cad_geometry.placement_point(world, notes, point)
    return x, y, basis


def search(item, var, drawings, data, sheets):
    hits, second, unplaced = [], [], Counter()
    for d in drawings:
        dr = data.get(d['id'])
        if dr is None: continue
        cache = {}
        for which, pats, excl in (('main', var['main'], var['exclude']), ('second', var['second'], var['exclude'] + var['second_exclude'])):
            if not pats and not (which == 'main' and var['blocks']): continue
            bag = hits if which == 'main' else second
            for o in dr['occ']:
                if (o['text'] is None and which == 'second') or not match(o, pats, excl, var): continue
                x, y, basis = anchor(dr, o)
                bag.append(hit(d, sheets[d['id']], o, x, y, o.get('layout'), 'layout' if o['text'] is not None else 'block', basis=basis))
            for name, rows in dr['blocks'].items():
                found = [o for o in rows if not (o['text'] is None and which == 'second') and match(o, pats, excl, var)]
                if not found: continue
                inst = instances(dr, name, cache)
                if not inst and not name.startswith('*'):
                    if which == 'main': unplaced[(d['id'], name)] += len(found)
                    continue
                if not inst:   # anonymous geometry blocks (tables, multileaders) sit in world coordinates and are drawn once
                    inst = [([1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1], 'Model', None)]
                for m, lay, top in inst:
                    for o in found:
                        x, y, basis = anchor(dr, o, m)
                        bag.append(hit(d, sheets[d['id']], o, x, y, lay, f'块 {name}（插入 {len(inst)} 次）', top, basis))
    return hits, second, unplaced


def hit(d, sheets, o, x, y, layout, via, top=None, basis=None):
    s = place(sheets, layout, x, y)
    scope = d.get('role', 'scope') == 'scope' and not (s and d.get('_exclude') and d['_exclude'].search(s['title']))
    how = {'layout': '布局文字', 'block': '块名'}.get(via, via)
    if o['text'] is not None and o['via'].startswith('attribute:'): how += ' 属性 ' + o['via'].split(':', 1)[1]
    return {'drawing': d['id'], 'group': d.get('group') or ('本次范围' if d.get('role', 'scope') == 'scope' else '参照'), 'scope': scope,
            'sheet': s['sheet'] if s else '', 'sheet_title': s['title'] if s else '', 'sheet_kind': s['kind'] if s else OFF_SHEET,
            'handle': top or o.get('handle'), 'inner_handle': o.get('handle') if top else None, 'layer': o.get('layer'),
            'text': o['text'] if o['text'] is not None else o.get('block'), 'via': how, 'placement_basis': basis}


def existing(item, var, tables):
    out = []
    for t in tables:
        for n, row in t['rows']:
            text = ' '.join(row.get(c) or '' for c in t['text_columns'])
            f = flat(text)
            forms = {'flat': f, 'flat_sep': flat_sep(text)}
            kind = ('main' if any(p.search(forms[form]) for p, form in var['main'])
                    else 'second' if any(p.search(forms[form]) for p, form in var['second']) else None)
            if kind and not any(p.search(f) for p in var['exclude'] + (var['second_exclude'] if kind == 'second' else [])):
                out.append({'source': f'{t["label"]}:{n}', 'id': row.get(t['id_column']) or '', 'text': text.strip()[:200], 'match': '同写法' if kind == 'main' else '次级'})
    return out


def judge(sec, judgments):
    """Each in-scope secondary hit takes the first judgment naming it: by top handle (optionally drawing, inner_handle)
    or by a regex on its flat text, whole or any whitespace-separated part of it (a box number followed by its size,
    01-F1-1A-GPB-N-2 600*2000*400, is matched by 1AGPBN2(?![0-9A-Z]); registration 20261017-0024). Returns how many
    hits each judgment took."""
    used = Counter()
    for h in sec:
        if not h['scope']: continue
        forms = None
        for k, j in enumerate(judgments):
            if j.get('handle'):
                ok = (str(j['handle']).upper() == str(h['handle'] or '').upper() and j.get('drawing') in (None, h['drawing'])
                      and (j.get('inner_handle') is None or str(j['inner_handle']).upper() == str(h['inner_handle'] or '').upper()))
            else:
                if forms is None:
                    forms = [flat(h['text'] or ''), *(flat(part) for part in MTEXT_CODE.sub(' ', str(h['text'] or '')).split())]
                ok = any(re.search(j['text_pattern'], form) is not None for form in forms)
            if ok:
                h['judgment'], h['judgment_basis'], h['judgment_index'] = j['judgment'], j['basis'], k; used[k] += 1; break
    return used


def detail_rows(record_id, item_id, main, stamp, sec):
    """One row per hit for HIT_DETAIL: every hit the record counts, not the samples (registration 20261017-0015)."""
    rows = []
    for category, hs in (('主命中', [h for h in main if h['scope']]), ('参照（主写法）', [h for h in main if not h['scope']]),
                         ('次级', [h for h in sec if h['scope']]), ('次级（参照）', [h for h in sec if not h['scope']]),
                         ('印章水印（不算命中）', stamp)):
        for h in hs:
            rows.append({'检索记录编号': record_id, '条目': item_id, '命中类别': category, '图号': h['drawing'], '图页': h['sheet'] or OFF_SHEET,
                         '图页名': h['sheet_title'], '图页类型': h['sheet_kind'], '句柄': h['handle'] or '', '块内句柄': h['inner_handle'] or '',
                         '图层': h['layer'] or '', '文字': h['text'] or '', '统一写法': flat(h['text'] or ''), '经由': h['via'],
                         '定位依据': h.get('placement_basis') or '', '判定': h.get('judgment') or ('未判' if category == '次级' else ''),
                         '判定序号': h.get('judgment_index', ''), '判定依据': h.get('judgment_basis') or ''})
    return rows


def summarize(hs, scope_only=True):
    xs = [h for h in hs if h['scope']] if scope_only else hs
    return len(xs), dict(Counter(h['sheet_kind'] for h in xs)), dict(Counter(h['group'] for h in hs))


def build(cfg, base):
    problems = []
    if cfg.get('schema') != INPUT_SCHEMA: raise InputError(['schema 须为 ' + INPUT_SCHEMA])
    drawings, items = cfg.get('drawings'), cfg.get('items')
    if not isinstance(drawings, list) or not drawings: problems.append('drawings 须为非空列表')
    if not isinstance(items, list) or not items: problems.append('items 须为非空列表')
    if problems: raise InputError(problems)
    ids = [d.get('id') for d in drawings]
    for k, c in Counter(ids).items():
        if not k or c > 1: problems.append(f'图纸编号 {k!r} 为空或重复')
    for d in drawings:
        if d.get('role', 'scope') not in ('scope', 'context'): problems.append(f'{d.get("id")} 的 role 只能是 scope 或 context')
        if d.get('exclude_sheet_titles'):
            try: d['_exclude'] = re.compile(d['exclude_sheet_titles'])
            except re.error as e: problems.append(f'{d.get("id")} 的 exclude_sheet_titles 不是正则：{e}')
    declared = cfg.get('drawing_set') or [d['id'] for d in drawings if d.get('role', 'scope') == 'scope']
    try: non_drawing = re.compile(cfg.get('non_drawing_pattern') or NON_DRAWING, re.I)
    except re.error as e: problems.append(f'non_drawing_pattern 不是正则：{e}'); non_drawing = re.compile(NON_DRAWING, re.I)
    overrides = cfg.get('unread_overrides') or []
    for n, o in enumerate(overrides):
        if o.get('class') not in ('non_drawing', 'may_contain') or not o.get('drawing') or not str(o.get('basis') or '').strip():
            problems.append(f'unread_overrides[{n}] 要写 drawing、class（non_drawing 或 may_contain）和 basis（核定依据）')
    vars_, seen = {}, Counter(i.get('item_id') for i in items)
    for i in items:
        if not i.get('item_id') or seen[i.get('item_id')] > 1: problems.append(f'条目编号 {i.get("item_id")!r} 为空或重复'); continue
        if i.get('kind') not in KINDS: problems.append(f'{i["item_id"]} 的 kind 只能是 {"、".join(KINDS)}'); continue
        if not str(i.get('conclusion') or '').strip(): problems.append(f'{i["item_id"]} 要写 conclusion（否定结论原文）')
        try: vars_[i['item_id']] = variants(i)
        except (ValueError, re.error) as e: problems.append(f'{i["item_id"]} 写法有误：{e}')
        for k, j in enumerate(i.get('secondary_judgments') or []):
            where = f'{i["item_id"]}.secondary_judgments[{k}]'
            if (not isinstance(j, dict) or j.get('judgment') not in JUDGMENTS or not str(j.get('basis') or '').strip()
                    or bool(j.get('handle')) == bool(j.get('text_pattern'))):
                problems.append(f'{where} 要写 judgment（{" 或 ".join(JUDGMENTS)}）、basis（依据），handle 与 text_pattern 二选一'); continue
            if j.get('text_pattern'):
                try: re.compile(j['text_pattern'])
                except re.error as e: problems.append(f'{where}.text_pattern 不是正则：{e}')
    tables = []
    for n, t in enumerate(cfg.get('existing_results') or []):
        try:
            with (base / t['path']).open(encoding='utf-8-sig', newline='') as f: rows = list(enumerate(csv.DictReader(f), 2))
            tables.append({'label': t.get('label') or Path(t['path']).stem, 'rows': rows, 'id_column': t.get('id_column') or '', 'text_columns': t.get('text_columns') or []})
            if not tables[-1]['text_columns']: problems.append(f'existing_results[{n}] 要写 text_columns')
        except (OSError, KeyError) as e: problems.append(f'existing_results[{n}] 读不了：{e}')
    sheets = {d['id']: read_sheets(d.get('sheets'), base, d['id'], problems) for d in drawings if d.get('id')}
    if problems: raise InputError(problems)
    data, scope_info = {}, []
    for d in drawings:
        dr = read_drawing(d, base, non_drawing, overrides, problems)
        data[d['id']] = dr
        kinds = Counter(s['kind'] for s in sheets[d['id']])
        scope_info.append({'drawing': d['id'], 'group': d.get('group') or '', 'role': d.get('role', 'scope'), 'read': dr is not None,
                           'sheets': len(sheets[d['id']]), 'sheet_kinds': dict(kinds), **({'neutral': dr['neutral'], 'unserialized_blocks': dr['unserialized_blocks']} if dr else {})})
    searched = {d['id'] for d in drawings if data.get(d['id']) is not None}
    uncovered = [{'drawing': x, 'reason': '没给中性取证包' if x not in ids else '中性取证包读不了'} for x in declared if x not in searched]
    unread = [{'drawing': d['id'], 'role': d.get('role', 'scope'), 'kind': k, 'layer': lay, 'class': cls, 'count': c}
              for d in drawings if data.get(d['id']) for (k, lay, cls), c in sorted(data[d['id']]['unread'].items())]
    scope_ids = {d['id'] for d in drawings if d.get('role', 'scope') == 'scope'}
    roles = {d['id']: d.get('role', 'scope') for d in drawings}
    may_contain = blocking_unread(unread, roles)
    kinds_searched = sorted({s['kind'] for d in drawings if d['id'] in searched and d['id'] in scope_ids for s in sheets[d['id']]})
    records, stale, detail = [], [], []
    for i in items:
        var = vars_[i['item_id']]
        hs, sec, unplaced = search(i, var, drawings, data, sheets)
        # Text on stamp and watermark layers is not drawing content (registration 2019, expectation 6): listed, never a hit.
        stamp = [h for h in hs if non_drawing.search(str(h['layer'] or ''))]
        key = lambda h: (h['drawing'], h['handle'], h['inner_handle'], h['text'], h['sheet'])
        seen_main = Counter(key(h) for h in hs)   # a main hit is not counted again as secondary
        hs, rest = [h for h in hs if h not in stamp], []
        for h in sec:
            if seen_main[key(h)]: seen_main[key(h)] -= 1
            elif not non_drawing.search(str(h['layer'] or '')): rest.append(h)
        sec = rest
        n, by_kind, by_group = summarize(hs)
        sn, _, _ = summarize(sec)
        judgments = i.get('secondary_judgments') or []
        used = judge(sec, judgments)
        stale += [f'{i["item_id"]}.secondary_judgments[{k}] 没有对上任何次级命中（句柄或写法写错，或判定已过时）' for k in range(len(judgments)) if not used[k]]
        scoped = [h for h in sec if h['scope']]
        same = sum(1 for h in scoped if h.get('judgment') == '同一对象')
        unjudged = sum(1 for h in scoped if not h.get('judgment'))
        # Full coverage also needs every secondary hit judged 非同一对象 (checkpoint 2: double counts hide in same-section rewrites).
        reasons = ([f'未覆盖的图 {len(uncovered)} 张'] if uncovered else []) + \
                  ([f'可能含图面内容的未读对象 {sum(u["count"] for u in may_contain)} 个'] if may_contain else []) + \
                  ([f'次级命中 {unjudged} 处未判定'] if unjudged else []) + ([f'次级命中 {same} 处判为同一对象'] if same else [])
        status = 'hit' if n else 'no_hit_partial' if reasons else 'no_hit_full'
        detail += detail_rows('NS-' + str(i['item_id']), i['item_id'], hs, stamp, sec)
        records.append({
            'record_id': 'NS-' + str(i['item_id']), 'item_id': i['item_id'], 'item': {k: i[k] for k in ('package', 'building', 'bill_no', 'name', 'features', 'bill_quantity') if k in i},
            'conclusion': i['conclusion'], 'kind': i['kind'], 'term': i.get('term'),
            'targets': {'written_forms': var['shown'], 'notes': var['notes'], 'patterns': [p.pattern for p, _ in var['main']],
                        'pattern_forms': [form for _, form in var['main']], 'block_names': [p.pattern for p in var['blocks']],
                        'layers': '全部图层', 'reads': ['布局图元文字', '块定义文字（按插入次数展开，含嵌套）', '块属性（全部属性值）']},
            'secondary': {'description': var['second_note'], 'patterns': [p.pattern for p, _ in var['second']], 'pattern_forms': [form for _, form in var['second']],
                          'exclude': [p.pattern for p in var['second_exclude']]} if var['second'] else None,
            'method': f'中性取证包检索：_qto-shared-evidence/scripts/search_negative_evidence.py {VERSION}；布局、块定义（按插入次数展开，含嵌套）、块属性三层，'
                      '命中按图页表定位（块名和块属性命中按块的图形范围中心、定不出用插入点，与点位覆盖台账同一算法；文字按其插入点；落进几张图页取面积最小的）',
            'drawings_searched': sorted(searched), 'drawing_roles': roles, 'sheet_kinds_searched': kinds_searched,
            'result': {'hits': n, 'hits_by_sheet_kind': by_kind, 'hits_by_group': by_group, 'context_hits': sum(1 for h in hs if not h['scope']),
                       'placement_basis_counts': dict(sorted(Counter(h.get('placement_basis') or '无坐标' for h in hs).items())),
                       'samples': hs[:SAMPLES], 'non_drawing_hits': len(stamp), 'non_drawing_samples': stamp[:SAMPLES], 'unplaced_block_hits': [{'drawing': a, 'block': b, 'texts': c} for (a, b), c in sorted(unplaced.items())],
                       'secondary_hits': sn, 'secondary_samples': scoped[:SAMPLES],
                       'secondary_judged': sn - unjudged, 'secondary_same_object': same, 'secondary_unjudged': unjudged,
                       'secondary_unjudged_samples': [h for h in scoped if not h.get('judgment')][:SAMPLES],
                       'existing_results': existing(i, var, tables)[:50], 'unread': unread},
            'secondary_judgments': [dict(j, matched=used[k]) for k, j in enumerate(judgments)],
            'uncovered': uncovered, 'status': status, 'status_text': STATUS[status], 'status_reasons': reasons if status == 'no_hit_partial' else []})
    if stale: raise InputError(stale, detail)   # the hit list still goes out, to write the judgments against
    reverse = {'requested': len(items), 'records': len(records), 'missing': [i['item_id'] for i in items if not any(r['item_id'] == i['item_id'] for r in records)],
               'declared_drawings': declared, 'searched_drawings': sorted(searched), 'outside_declared': sorted(searched - set(declared))}
    return {'schema': OUTPUT_SCHEMA, 'tool': {'script': '_qto-shared-evidence/scripts/search_negative_evidence.py', 'version': VERSION},
            'scope': scope_info, 'records': records, 'reverse_check': reverse,
            'summary': dict(Counter(r['status'] for r in records)), '_detail': detail}


def blocking_unread(unread, roles=None):
    """Unread objects that keep a search from full coverage: may-contain objects on scope drawings (Codex retest R1).
    Used by build() and record_problems(), so the generator and both consumers judge alike. A context drawing's unread
    objects stay listed but decide nothing; an entry counts as context only when it says role context and, where the
    record gives drawing_roles, that drawing is context there too. Entries without a role (records written before
    1.0.1) count as scope and still block."""
    roles = roles if isinstance(roles, dict) else {}
    return [u for u in unread if isinstance(u, dict) and u.get('class') == 'may_contain'
            and not (u.get('role') == 'context' and (not roles or roles.get(u.get('drawing')) == 'context'))]


def record_problems(rec):
    """Problems of one record as build() writes it: the six parts and a status that matches what was found.
    Shared by validate_minimum_result.py and the point coverage ledger; each message follows the record id."""
    if not isinstance(rec, dict):
        return ['不是对象']
    text = lambda v: isinstance(v, str) and bool(v.strip())
    result, targets = rec.get('result'), rec.get('targets')
    hits = result.get('hits') if isinstance(result, dict) else None
    missing = [label for label, ok in (
        ('结论', text(rec.get('conclusion'))),
        ('检索图纸', isinstance(rec.get('drawings_searched'), list) and bool(rec['drawings_searched'])),
        ('检索对象', isinstance(targets, dict) and bool(targets.get('written_forms') or targets.get('patterns') or targets.get('block_names'))),
        ('方法', text(rec.get('method'))),
        ('结果', isinstance(hits, int) and not isinstance(hits, bool) and hits >= 0 and isinstance(result.get('unread'), list)),
        ('未覆盖', isinstance(rec.get('uncovered'), list)),
    ) if not ok]
    if missing:
        return ['缺少: ' + '、'.join(missing)]
    status = rec.get('status')
    if status not in STATUS:
        return ['的检索状态非法']
    if (status == 'hit') != (hits > 0):
        return ['的检索状态与命中数不符']
    if status == 'no_hit_full' and (rec['uncovered'] or blocking_unread(result['unread'], rec.get('drawing_roles'))):
        return ['有未覆盖的图或可能含图面内容的未读对象，检索状态只能是待确认']
    if status == 'no_hit_full' and result.get('secondary_hits') and (result.get('secondary_unjudged') != 0 or result.get('secondary_same_object') != 0):
        return ['的次级命中没有逐条判为非同一对象，检索状态只能是待确认']
    return []


CSV_COLUMNS = ['检索记录编号','对应条目', '结论', '检索图纸', '检索对象', '方法', '结果', '命中样例', '未插入块定义命中', '次级检索', '已有成果比对', '未能读取', '未覆盖', '检索状态', '次级命中判定']


def csv_row(r, scope):
    res = r['result']
    groups = defaultdict(list)
    for s in scope: groups[s['group'] or s['role']].append(f'{s["drawing"]}（{s["sheets"]} 页' + ''.join(f'，{k} {v}' for k, v in sorted(s['sheet_kinds'].items())) + ('' if s['read'] else '，未读') + '）')
    sample = lambda hs: '；'.join(f'{h["drawing"]} {h["sheet"] or OFF_SHEET}（{h["sheet_kind"]}） {h["handle"]} {h["layer"]} 「{h["text"]}」 经由{h["via"]}' for h in hs)
    return {'检索记录编号': r['record_id'], '对应条目': r['item_id'], '结论': r['conclusion'],
            '检索图纸': '；'.join(f'{g}：' + '、'.join(v) for g, v in groups.items()),
            '检索对象': '、'.join(r['targets']['written_forms']) + ('；' + '；'.join(r['targets']['notes']) if r['targets']['notes'] else '') + ('；块名 ' + '、'.join(r['targets']['block_names']) if r['targets']['block_names'] else ''),
            '方法': r['method'],
            '结果': f'主命中 {res["hits"]}（' + '，'.join(f'{k} {res["hits_by_sheet_kind"].get(k, 0)}' for k in (*SHEET_KINDS, OFF_SHEET)) + '）' + (f'；参照图 {res["context_hits"]}' if res['context_hits'] else '') + (f'；印章水印图层 {res["non_drawing_hits"]}（不算命中）' if res['non_drawing_hits'] else ''),
            '命中样例': sample(res['samples']),
            '未插入块定义命中': '；'.join(f'{u["drawing"]} {u["block"]} {u["texts"]} 处' for u in res['unplaced_block_hits']) or '无',
            '次级检索': (f'{r["secondary"]["description"]}：{res["secondary_hits"]}；' + sample(res['secondary_samples'])) if r['secondary'] else '无',
            '已有成果比对': '；'.join(f'{e["source"]} {e["id"]} {e["text"]}（{e["match"]}）' for e in res['existing_results']) or '无',
            '未能读取': '；'.join(f'{u["drawing"]} {u["kind"]} {u["count"]}（{u["layer"] or "无图层"}，{"非图面内容" if u["class"] == "non_drawing" else "可能含图面内容"}{"，参照图，不改状态" if u.get("role") == "context" else ""}）' for u in res['unread']) or '无',
            '未覆盖': '；'.join(f'{u["drawing"]}（{u["reason"]}）' for u in r['uncovered']) or '无',
            '检索状态': r['status_text'] + (f'（{"；".join(r["status_reasons"])}）' if r['status_reasons'] else ''),
            '次级命中判定': '无次级命中' if not res['secondary_hits'] else
                f'已判 {res["secondary_judged"]}（非同一对象 {res["secondary_judged"] - res["secondary_same_object"]}，同一对象 {res["secondary_same_object"]}），未判 {res["secondary_unjudged"]}'
                + ''.join(f'；{j.get("handle") or j.get("text_pattern")} {j["judgment"]} {j["matched"]} 处：{j["basis"]}' for j in r['secondary_judgments'])}


def main():
    p = argparse.ArgumentParser(description='否定结论检索：在中性取证包里检索“图上无”类结论的对象，出检索记录')
    p.add_argument('--input', required=True, type=Path); p.add_argument('--output-dir', required=True, type=Path)
    a = p.parse_args()
    report = {'status': 'INPUT_REQUIRED'}
    jp, cp, dp = a.output_dir / '否定结论检索记录.json', a.output_dir / '否定结论检索记录.csv', a.output_dir / HIT_DETAIL

    def write_detail(rows):
        a.output_dir.mkdir(parents=True, exist_ok=True)
        with dp.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, HIT_COLUMNS); w.writeheader(); w.writerows(rows)
        return {'path': HIT_DETAIL, 'rows': len(rows), 'sha256': sha_file(dp)}
    try:
        cfg = json.loads(a.input.read_text(encoding='utf-8-sig'))
        if jp.exists() or cp.exists(): raise InputError(['输出目录里已有检索记录，请用新的目录'])
        out = build(cfg, a.input.resolve().parent)
        out['input'] = {'path': str(a.input), 'sha256': sha_file(a.input)}
        out['hit_detail'] = write_detail(out.pop('_detail'))
        jp.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding='utf-8')
        with cp.open('w', encoding='utf-8-sig', newline='') as f:
            w = csv.DictWriter(f, CSV_COLUMNS); w.writeheader(); w.writerows(csv_row(r, out['scope']) for r in out['records'])
        report = {'status': 'PASS', 'records': len(out['records']), 'summary': out['summary'], 'output': str(jp), 'hit_detail': str(dp)}
        code = 0
    except InputError as e:
        report.update(error=str(e), problems=e.problems); code = 2
        if e.detail is not None:   # stale judgments: no record, but every hit to write them against
            write_detail(e.detail); report['hit_detail'] = str(dp)
    except (OSError, ValueError, KeyError, TypeError) as e:
        report['error'] = str(e); code = 2
    print(json.dumps(report, ensure_ascii=False)); return code


if __name__ == '__main__': raise SystemExit(main())
