"""Build review-view scenes, background and objects from a scene mapping plus project tables.

Contract: docs/维护/图面视图映射契约.md (qto-review-scene-mapping/1.0). The script only moves rows, applies
declared coordinate transforms, looks geometry up by CAD handle and clips the drawing background by sheet
bounds. It never computes an engineering quantity, guesses a sheet or an origin, or invents a coordinate:
anything it cannot place becomes an unlocated object with a reason code.
"""
import argparse, base64, csv, gzip, hashlib, json, math, os, re
from collections import Counter, defaultdict
from pathlib import Path
from qto_review_contract import normalize_view, VIEW_SCHEMA_COMPARISON, PROFESSIONALS
from qto_cad_geometry import conic_point

MAPPING_SCHEMA = 'qto-review-scene-mapping/1.0'
TOP_FIELDS = {'schema', 'view', 'drawings', 'sheets', 'scenes', 'background', 'objects', 'groups'}
SPEC_FIELDS = {'path', 'columns', 'constants', 'value_maps', 'list_separators'}
SHEET_KEYS = ('sheet_id', 'drawing', 'title', 'floor', 'sheet_kind', 'x1', 'y1', 'x2', 'y2', 'raster_path')
OBJECT_KEYS = ('object_id', 'catalog_key', 'sheet', 'drawing', 'kind', 'title', 'category', 'status', 'detail', 'source', 'professional',
               'unlocated_reason', 'unlocated_note', 'x', 'y', 'x1', 'y1', 'x2', 'y2', 'points', 'handle')
OBJECT_FIELDS = SPEC_FIELDS | {'key', 'filter', 'geometry', 'transform', 'coords', 'group_by', 'attributes', 'quantities', 'professional'}
GROUP_KEYS = ('id', 'title', 'category', 'status', 'proposal', 'reason')
GROUP_FIELDS = SPEC_FIELDS | {'members'}
DRAWING_FIELDS = {'id', 'neutral_dir', 'coordinate_unit', 'precision', 'title', 'background'}
SCENE_FIELDS = {'id', 'drawing', 'title', 'base', 'overlays', 'background'}
OVERLAY_FIELDS = {'sheet', 'dx', 'dy', 'layers_include', 'layers_exclude'}
BG_FIELDS = {'layers_include', 'layers_exclude', 'text_max_len', 'include_types'}
NUMBER_KEYS = {'x', 'y', 'x1', 'y1', 'x2', 'y2'}
SHEET_KINDS = ('平面', '系统图', '表与说明', '图例', '详图', '其他')
SKIP_TYPES = {'HATCH', 'DIMENSION', 'OLE2FRAME'}
POINT_TYPES = {'INSERT', 'TEXT', 'MTEXT', 'POINT', 'ATTRIB', 'ATTDEF'}
LINE_TYPES = {'LINE', 'LWPOLYLINE', 'POLYLINE', 'ARC', 'SPLINE', 'ELLIPSE'}
UNIT_MM = {'mm': 1, 'm': 1000}
HANDLE_RE = re.compile(r'"entity_handle":\s*"([^"]*)"')
TEMPLATE_RE = re.compile(r'\{([^{}]+)\}')


class InputError(ValueError):
 def __init__(self, problems): super().__init__(f'{len(problems)} 处输入问题'); self.problems = problems


def sha_file(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def text(v): return isinstance(v, str) and bool(v.strip())


# ---------- mapping reminders (same rule as the comparison mapping, contract 12.1) ----------
def mapping_warnings(m):
 out = []
 def extra(where, have, allowed, what='字段'):
  keys = sorted(set(have) - set(allowed))
  if keys: out.append({'where': where, 'keys': keys, 'message': f'不认识的映射{what}，没有使用：' + '、'.join(keys)})
 extra('映射顶层', m, TOP_FIELDS)
 if isinstance(m.get('background'), dict): extra('background', m['background'], BG_FIELDS)
 for n, d in enumerate(m.get('drawings') or []):
  if isinstance(d, dict): extra(f'drawings[{n}]', d, DRAWING_FIELDS)
 for n, s in enumerate(m.get('scenes') or []):
  if isinstance(s, dict):
   extra(f'scenes[{n}]', s, SCENE_FIELDS)
   for k, o in enumerate(s.get('overlays') or []):
    if isinstance(o, dict): extra(f'scenes[{n}].overlays[{k}]', o, OVERLAY_FIELDS)
 for role, fields, keys in (('sheets', SPEC_FIELDS, SHEET_KEYS), ('objects', OBJECT_FIELDS, OBJECT_KEYS), ('groups', GROUP_FIELDS, GROUP_KEYS)):
  for n, spec in enumerate(m.get(role) or []):
   if not isinstance(spec, dict): continue
   where = f"{role}[{n}]（{spec.get('path', '')}）"; extra(where, spec, fields)
   for part in ('columns', 'constants', 'value_maps'):
    extra(f'{where}.{part}', spec.get(part) or {}, keys, '标准键')
 return out


# ---------- tables ----------
def read_rows(spec, keys, base, problems, files, role):
 """Rows of canonical keys. Constants may hold {列名} templates filled from the same row; nothing is guessed."""
 if not isinstance(spec, dict) or not text(spec.get('path')): problems.append({'check': 'S-0', 'where': role, 'message': '表须写 path'}); return [], set()
 path = (base / spec['path']).resolve()
 if not path.is_file(): problems.append({'check': 'S-0', 'where': spec['path'], 'message': '表文件不存在'}); return [], set()
 files.append({'role': role, 'key': spec.get('key'), 'path': spec['path'], 'sha256': sha_file(path)})
 cols, consts, maps = spec.get('columns') or {}, spec.get('constants') or {}, spec.get('value_maps') or {}
 out = []
 with open(path, encoding='utf-8-sig', newline='') as f:
  reader = csv.DictReader(f); header = set(reader.fieldnames or [])
  need = {c for c in cols.values() if isinstance(c, str)} | {c for c in (spec.get('filter') or {})} | set(spec.get('group_by') or [])
  need |= set((spec.get('attributes') or {}).values()) | {q.get('column') for q in spec.get('quantities') or [] if isinstance(q, dict)}
  need |= {c for v in consts.values() if isinstance(v, str) for c in TEMPLATE_RE.findall(v)}
  missing = sorted(c for c in need if c and c not in header)
  if missing: problems.append({'check': 'S-0', 'where': spec['path'], 'message': '映射用到的列不存在：' + '、'.join(missing)}); return [], header
  for n, raw in enumerate(reader, start=2):
   rec = {'_n': n, '_where': f"{spec['path']} 第 {n} 行", '_raw': raw}
   for k in keys:
    if k in consts:
     v = consts[k]
     if isinstance(v, str) and TEMPLATE_RE.search(v): v = TEMPLATE_RE.sub(lambda mt: (raw.get(mt.group(1)) or '').strip(), v)
    else:
     col = cols.get(k, k)
     if col not in header: continue
     v = (raw.get(col) or '').strip()
    if isinstance(v, str) and v in (maps.get(k) or {}): v = maps[k][v]
    if k in NUMBER_KEYS and isinstance(v, str):
     if v == '': v = None
     else:
      try: v = float(v.replace(',', ''))
      except ValueError: problems.append({'check': 'S-0', 'where': rec['_where'], 'message': f'{k} 不是数字：{v}'}); v = None
    rec[k] = v
   out.append(rec)
 return out, header


def opt(rec, k):
 v = rec.get(k)
 return None if v in ('', None) else v


# ---------- geometry helpers ----------
def chain(polys, eps=1e-6):
 """Join consecutive polylines whose end meets the next start; the rest stay separate parts."""
 out = []
 for q in polys:
  q = [list(p) for p in q]
  if out and math.dist(out[-1][-1], q[0]) <= eps: out[-1].extend(q[1:])
  else: out.append(q)
 return [q for q in out if len(q) >= 2]


def part_points(p):
 if p.get('kind') == 'line': return [p['start'][:2], p['end'][:2]]
 if p.get('kind') == 'ellipse_arc':
  a0, sw = p.get('start_param', 0.0), p.get('sweep_param', 2 * math.pi)
  k = max(4, int(12 * abs(sw) / (2 * math.pi)) + 1)
  return [list(conic_point(p, a0 + sw * i / k)[:2]) for i in range(k + 1)]
 pts = p.get('points') or []
 return [q[:2] for q in pts] if len(pts) >= 2 else None


def record_polys(d):
 """Polylines of a neutral layout record in WCS; splines fall back to fit or control points."""
 polys = [q for q in (part_points(p) for p in (d.get('geometry') or {}).get('parts', []) or []) if q]
 if not polys and d.get('entity_type') == 'SPLINE':
  raw = d.get('raw_geometry') or {}
  pts = raw.get('fit_points') or raw.get('control_points') or []
  if len(pts) >= 2: polys = [[q[:2] for q in pts]]
 polys = chain(polys)
 if polys and (d.get('geometry') or {}).get('closed') and len(polys) == 1 and polys[0][0] != polys[0][-1]: polys[0].append(polys[0][0])
 return polys


def record_geometry(d):
 """(kind, parts, note) for a handle lookup (contract 6)."""
 et = d.get('entity_type')
 if et in POINT_TYPES:
  ins = (d.get('raw_geometry') or {}).get('insert')
  p = ins[:2] if isinstance(ins, list) and len(ins) >= 2 else [d.get('x'), d.get('y')]
  if all(isinstance(v, (int, float)) for v in p): return 'point', [[list(p)]], None
 if et == 'CIRCLE':
  c = next((p.get('center') for p in (d.get('geometry') or {}).get('parts', []) or [] if p.get('center')), None)
  if c: return 'point', [[c[:2]]], None
 if et in LINE_TYPES:
  polys = record_polys(d)
  if polys: return 'line', polys, None
 b = d.get('bbox')
 if isinstance(b, list) and len(b) >= 4: return 'point', [[[(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]]], f'{et} 取外包框中心'
 return None, None, None


def scan_layout(path, handles, targets, counters):
 """One streaming pass: resolve the wanted handles and clip background for every target scene of this drawing."""
 found = {}
 with open(path, encoding='utf-8') as f:
  for line in f:
   hm = HANDLE_RE.search(line); h = hm.group(1) if hm else None
   want_h = h in handles
   if not want_h and not targets: continue
   d = None
   if want_h:
    d = json.loads(line)
    prev = found.get(h)
    # Prefer the top-level record; a handle met twice at top level is reported, the first one kept.
    if prev is None or (prev.get('parent_block') and not d.get('parent_block')): found[h] = d
    elif not d.get('parent_block') and not prev.get('parent_block'): counters['handle_duplicates'] += 1
   if not targets: continue
   # Neutral layout lines are compact JSON; this cheap test skips block children and paper space before parsing.
   if '"parent_block":null' not in line or '"layout":"Model"' not in line: continue
   d = d or json.loads(line)
   b = d.get('bbox')
   if not b or d.get('parent_block') or d.get('layout') != 'Model': continue
   for t in targets:
    s = t['sheet_bounds']
    if b[0] < s[0] or b[2] > s[2] or b[1] < s[1] or b[3] > s[3]: continue
    take_bg(d, t)
 return found


def take_bg(d, t):
 et, lay = d['entity_type'], d.get('layer') or ''
 f = t['filter']
 if f['include'] and not any(r.search(lay) for r in f['include']): return
 if any(r.search(lay) for r in f['exclude']): return
 dx, dy, nd = t['dx'], t['dy'], t['precision']
 rnd = (lambda v: round(v)) if nd == 0 else (lambda v: round(v, nd))
 if et == 'INSERT': t['stats']['inserts_not_expanded'] += 1; return
 if et in SKIP_TYPES and et not in f['types']: t['stats']['skipped_' + et] += 1; return
 if et in ('TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'):
  s = (d.get('text') or '').strip()
  if not s: return
  ins = (d.get('raw_geometry') or {}).get('insert') or [d.get('x'), d.get('y')]
  if not all(isinstance(v, (int, float)) for v in ins[:2]): return
  item = {'kind': 'text', 'point': [rnd(ins[0] + dx), rnd(ins[1] + dy)], 'text': s[:f['text_max_len']]}
  if d.get('rotation'): item['rotation'] = float(d['rotation'])
  t['items'].append(item); t['stats']['background_items'] += 1; t['stats']['background_points'] += 1; return
 for q in record_polys(d):
  pts = [[rnd(x + dx), rnd(y + dy)] for x, y in q]
  if len(pts) >= 2: t['items'].append({'kind': 'line', 'points': pts}); t['stats']['background_items'] += 1; t['stats']['background_points'] += len(pts)


def regexes(v, where, problems):
 out = []
 for r in v or []:
  try: out.append(re.compile(r))
  except (re.error, TypeError) as e: problems.append({'check': 'S-0', 'where': where, 'message': f'图层正则无效：{r}（{e}）'})
 return out


# ---------- build ----------
def build(m, base, neutral_root, problems, files):
 view_cfg = m.get('view') if isinstance(m.get('view'), dict) else {}
 for k in ('project_id', 'review_id', 'title', 'revision'):
  if not text(view_cfg.get(k)): problems.append({'check': 'S-0', 'where': 'view', 'message': k + ' 必须填写'})
 default_prof = view_cfg.get('professional')
 # Drawings and their authoritative sources (contract 3).
 drawings = {}
 for n, d in enumerate(m.get('drawings') or []):
  if not isinstance(d, dict) or not text(d.get('id')) or d['id'] in drawings: problems.append({'check': 'S-0', 'where': f'drawings[{n}]', 'message': '图纸须有不重复的 id'}); continue
  unit = d.get('coordinate_unit', 'mm')
  if unit not in UNIT_MM: problems.append({'check': 'S-0', 'where': f'drawings[{n}]', 'message': 'coordinate_unit 只能是 mm 或 m'}); continue
  folder = neutral_root / (d.get('neutral_dir') or d['id']); manifest = folder / 'CAD中性取证清单.json'; layout = folder / '中性布局图元.ndjson'
  if not manifest.is_file() or not layout.is_file(): problems.append({'check': 'S-0', 'where': f'drawings[{n}]', 'message': f'中性事实不全：{folder}'}); continue
  src = json.loads(manifest.read_text(encoding='utf-8-sig')).get('authoritative_source') or {}
  if not re.fullmatch('[0-9a-fA-F]{64}', str(src.get('sha256', ''))): problems.append({'check': 'S-0', 'where': f'drawings[{n}]', 'message': '中性取证清单没有原图 SHA256'}); continue
  drawings[d['id']] = {'id': d['id'], 'unit': unit, 'precision': d.get('precision', 0 if unit == 'mm' else 3), 'layout': layout, 'sha256': src['sha256'].lower(),
                       'title': d.get('title') or f"{d['id']}（{Path(str(src.get('path', ''))).name}）", 'background': d.get('background') or {}}
 # Sheets (contract 4).
 sheets = {}
 for spec in m.get('sheets') or []:
  rows, _ = read_rows(spec, SHEET_KEYS, base, problems, files, 'sheets')
  for r in rows:
   sid, dr = opt(r, 'sheet_id'), opt(r, 'drawing')
   if sid is None or dr not in drawings: problems.append({'check': 'S-0', 'where': r['_where'], 'message': '图页须有 sheet_id 和已声明的 drawing'}); continue
   if opt(r, 'sheet_kind') is not None and r['sheet_kind'] not in SHEET_KINDS: problems.append({'check': 'S-0', 'where': r['_where'], 'message': 'sheet_kind 只能是 ' + '、'.join(SHEET_KINDS)}); continue
   xs = [r.get(k) for k in ('x1', 'y1', 'x2', 'y2')]
   if any(v is not None for v in xs) and not all(v is not None for v in xs): problems.append({'check': 'S-0', 'where': r['_where'], 'message': '图框范围 x1、y1、x2、y2 要么都写，要么都不写'}); continue
   bounds = [min(xs[0], xs[2]), min(xs[1], xs[3]), max(xs[0], xs[2]), max(xs[1], xs[3])] if xs[0] is not None else None
   if bounds and (bounds[2] <= bounds[0] or bounds[3] <= bounds[1]): problems.append({'check': 'S-0', 'where': r['_where'], 'message': '图框范围无效'}); continue
   rec = {'sheet_id': sid, 'drawing': dr, 'title': opt(r, 'title') or sid, 'floor': opt(r, 'floor'), 'sheet_kind': opt(r, 'sheet_kind'), 'bounds': bounds, 'raster_path': opt(r, 'raster_path')}
   key = (dr, sid)
   if key in sheets and sheets[key] != rec: problems.append({'check': 'S-0', 'where': r['_where'], 'message': f'图页 {dr}|{sid} 在几张图页表里内容不同'}); continue
   sheets[key] = rec
 by_sid = defaultdict(set)
 for dr, sid in sheets: by_sid[sid].add(dr)
 # Composite scenes (contract 5): which sheet goes into which scene, with the declared offset.
 global_bg = m.get('background') or {}
 home, composites = {}, {}
 for n, s in enumerate(m.get('scenes') or []):
  where = f'scenes[{n}]'
  if not isinstance(s, dict) or not text(s.get('id')) or s.get('drawing') not in drawings or (s['drawing'], s.get('base')) not in sheets:
   problems.append({'check': 'S-0', 'where': where, 'message': '叠图须有 id、已声明的 drawing 和图页表里的 base'}); continue
  scene_id = f"{s['drawing']}|{s['id']}"
  if scene_id in composites: problems.append({'check': 'S-0', 'where': where, 'message': '叠图编号重复：' + scene_id}); continue
  members = [(s['base'], 0.0, 0.0, {})] + [(o.get('sheet'), o.get('dx', 0), o.get('dy', 0), o) for o in s.get('overlays') or [] if isinstance(o, dict)]
  for sid, dx, dy, o in members:
   key = (s['drawing'], sid)
   if key not in sheets or not all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (dx, dy)): problems.append({'check': 'S-0', 'where': where, 'message': f'叠图图页 {sid} 不在图页表里，或 dx、dy 不是数字'}); continue
   if key in home: problems.append({'check': 'S-0', 'where': where, 'message': f'图页 {sid} 已属于叠图 {home[key][0]}，一张图页只能属于一个场景'}); continue
   if sheets[key]['bounds'] is None: problems.append({'check': 'S-0', 'where': where, 'message': f'叠图图页 {sid} 须有图框范围'}); continue
   home[key] = (scene_id, float(dx), float(dy), o)
  composites[scene_id] = s
 # Objects (contract 6).
 objects, report_tables, source_use = [], [], defaultdict(list)
 pending = defaultdict(set)   # drawing -> handles to look up
 drafts = []
 keys_seen = set()
 for n, spec in enumerate(m.get('objects') or []):
  if not isinstance(spec, dict) or not text(spec.get('key')) or spec['key'] in keys_seen: problems.append({'check': 'S-0', 'where': f'objects[{n}]', 'message': '对象表须有不重复的 key'}); continue
  keys_seen.add(spec['key'])
  rows, header = read_rows(spec, OBJECT_KEYS, base, problems, files, 'objects')
  tinfo = {'key': spec['key'], 'path': spec.get('path'), 'rows': len(rows), 'objects': 0, 'skipped': Counter(), 'unlocated': Counter()}
  report_tables.append(tinfo)
  coords = spec.get('coords', 'drawing')
  if coords not in ('drawing', 'scene'): problems.append({'check': 'S-0', 'where': spec['key'], 'message': 'coords 只能是 drawing 或 scene'}); continue
  tr = spec.get('transform')
  if tr is not None and not (isinstance(tr, dict) and set(tr) <= {'scale', 'dx', 'dy'} and all(isinstance(tr.get(k, 0), (int, float)) for k in ('scale', 'dx', 'dy'))):
   problems.append({'check': 'S-0', 'where': spec['key'], 'message': 'transform 须为 {scale, dx, dy} 数字'}); continue
  scale, tdx, tdy = (tr or {}).get('scale', 1), (tr or {}).get('dx', 0), (tr or {}).get('dy', 0)
  filt = []
  for col, pat in (spec.get('filter') or {}).items():
   try: filt.append((col, re.compile(pat)))
   except (re.error, TypeError) as e: problems.append({'check': 'S-0', 'where': spec['key'], 'message': f'filter 正则无效：{pat}（{e}）'})
  mode = (spec.get('geometry') or {}).get('mode') if isinstance(spec.get('geometry'), dict) else None
  if mode not in (None, 'xy', 'segment', 'points', 'handle', 'none'): problems.append({'check': 'S-0', 'where': spec['key'], 'message': 'geometry.mode 只能是 xy、segment、points、handle、none'}); continue
  group_by = spec.get('group_by') or []
  if not isinstance(group_by, list) or not all(isinstance(c, str) for c in group_by): problems.append({'check': 'S-0', 'where': spec['key'], 'message': 'group_by 须为列名列表'}); continue
  if group_by and spec.get('quantities'): problems.append({'check': 'S-0', 'where': spec['key'], 'message': 'group_by 合并的对象不带数量：各段长度写进 attributes 只作显示，数量以明细为准'}); continue
  # Rows -> drafts (one per row, or one per group).
  groups = {}
  for r in rows:
   raw = r['_raw']
   if any(not p.search((raw.get(c) or '').strip()) for c, p in filt): tinfo['skipped']['filter'] += 1; continue
   if not any((v or '').strip() for v in raw.values()): tinfo['skipped']['空行'] += 1; continue
   # Geometry from coordinate columns (contract 6, order 1), else handle (order 2), else none.
   g_kind, polys, handle = None, None, opt(r, 'handle')
   use = mode or ('xy' if r.get('x') is not None and r.get('y') is not None else 'segment' if all(r.get(k) is not None for k in ('x1', 'y1', 'x2', 'y2'))
                  else 'points' if opt(r, 'points') else 'handle' if handle else 'none')
   tfm = lambda p: [p[0] * scale + tdx, p[1] * scale + tdy]
   try:
    if use == 'xy' and r.get('x') is not None and r.get('y') is not None: g_kind, polys = 'point', [[tfm([r['x'], r['y']])]]
    elif use == 'segment' and all(r.get(k) is not None for k in ('x1', 'y1', 'x2', 'y2')): g_kind, polys = 'line', [[tfm([r['x1'], r['y1']]), tfm([r['x2'], r['y2']])]]
    elif use == 'points' and opt(r, 'points'):
     pts = json.loads(r['points'])
     if not (isinstance(pts, list) and len(pts) >= 2 and all(isinstance(p, list) and len(p) >= 2 and all(isinstance(v, (int, float)) for v in p[:2]) for p in pts)): raise ValueError('points 须为 [[x,y],…] 至少两点')
     g_kind, polys = 'polygon' if opt(r, 'kind') == 'polygon' else 'line', [[tfm(p[:2]) for p in pts]]
   except ValueError as e: problems.append({'check': 'S-0', 'where': r['_where'], 'message': str(e)}); continue
   if use == 'handle' and not handle: use = 'none'
   gkey = tuple((raw.get(c) or '').strip() for c in group_by) if group_by else ('#', r['_n'])
   if gkey not in groups: groups[gkey] = {'rows': [], 'spec': spec, 'coords': coords}
   groups[gkey]['rows'].append({'rec': r, 'kind': g_kind, 'polys': polys, 'use': use, 'handle': handle})
   if use == 'handle':
    dr = opt(r, 'drawing') or (next(iter(by_sid[opt(r, 'sheet')])) if len(by_sid.get(opt(r, 'sheet'), ())) == 1 else None)
    if dr in drawings: pending[dr].add(handle)
  for gkey, g in groups.items():
   kinds = {x['kind'] for x in g['rows'] if x['kind']} | ({'?'} if any(x['use'] == 'handle' for x in g['rows']) else set())
   if group_by and kinds - {'line', '?'}: problems.append({'check': 'S-0', 'where': f"{spec['key']} 合并组 {gkey}", 'message': 'group_by 只用于线；点和面不能合并'}); continue
   drafts.append(g)
 # Handle lookups: one pass per drawing, shared with the background pass below when possible (contract 6, 7).
 resolve_counter = Counter()
 # Sheets and object placement need handle results first, so handles are looked up before scene bounds are known.
 found = {}
 for dr, hs in pending.items():
  found[dr] = scan_layout(drawings[dr]['layout'], hs, [], resolve_counter)
 # Drafts -> objects.
 draws = []
 cat_keys = set()
 for g in drafts:
  spec, rows = g['spec'], g['rows']; first = rows[0]['rec']; tinfo = next(t for t in report_tables if t['key'] == spec['key'])
  sid = opt(first, 'sheet'); dr = opt(first, 'drawing')
  if dr is None and sid is not None and len(by_sid.get(sid, ())) == 1: dr = next(iter(by_sid[sid]))
  sheet = sheets.get((dr, sid)) if sid is not None else None
  reason, note, polys, kind, notes, hids = opt(first, 'unlocated_reason'), opt(first, 'unlocated_note'), [], None, [], []
  for x in rows:
   if x['use'] == 'handle':
    if sheet is None: x['reason'] = 'sheet_unknown'; continue
    d = found.get(dr, {}).get(x['handle'])
    if d is None: x['reason'] = 'handle_not_found'; continue
    hids.append(d.get('record_id') or d.get('entity_handle'))
    if d.get('parent_block'): x['reason'] = 'handle_in_block'; continue
    k, ps, nt = record_geometry(d)
    if k is None: x['reason'] = 'no_geometry'; continue
    x['kind'], x['polys'] = k, ps
    if nt: notes.append(nt)
   elif x['kind'] is None: x['reason'] = 'no_geometry'
  have = [x for x in rows if x['kind']]
  if reason is not None and reason not in ('estimated', 'no_geometry'):
   problems.append({'check': 'S-0', 'where': first['_where'], 'message': 'unlocated_reason 只能由项目声明 estimated 或 no_geometry，其余由脚本判定'}); continue
  if len(rows) > 1 and any(x['kind'] != 'line' for x in have):
   problems.append({'check': 'S-0', 'where': first['_where'], 'message': 'group_by 只用于线；合并组里有句柄回取到点或面的行'}); continue
  # Declared reason first, then an unknown sheet, then whatever the geometry lookup found.
  if reason is None and sheet is None: reason = 'sheet_unknown'
  if reason is None and have:
   kind = 'line' if len(rows) > 1 or have[0]['kind'] == 'line' else have[0]['kind']
   polys = [q for x in have for q in x['polys']]
   if kind == 'line': polys = chain(polys)
   if kind == 'line' and not polys: kind = None
  if kind is None and reason is None:
   reasons = [x.get('reason') for x in rows if x.get('reason')]
   reason = Counter(reasons).most_common(1)[0][0] if reasons else 'no_geometry'
  # Placement: overlay shift for drawing coordinates (handles are always drawing coordinates).
  scene_id, dx, dy = None, 0.0, 0.0
  if sheet is not None:
   hk = (sheet['drawing'], sheet['sheet_id'])
   if hk in home: scene_id, dx, dy, _ = home[hk]
   else: scene_id = f"{sheet['drawing']}|{sheet['sheet_id']}"
  if kind and (g['coords'] == 'drawing' or any(x['use'] == 'handle' for x in have)) and (dx or dy):
   polys = [[[p[0] + dx, p[1] + dy] for p in q] for q in polys]
  oid = opt(first, 'object_id')
  if oid is None: problems.append({'check': 'S-0', 'where': first['_where'], 'message': '缺少 object_id'}); continue
  ckey = opt(first, 'catalog_key') or f"{spec['key']}|{sid or '-'}|{oid}"
  if ckey in cat_keys:
   problems.append({'check': 'S-2', 'where': first['_where'], 'message': f'catalog_key 重复：{ckey}（同一对象多行请写 group_by，或让 catalog_key 带上能区分的列）'}); continue
  cat_keys.add(ckey)
  for k in ('title', 'category', 'status'):
   if opt(first, k) is None: problems.append({'check': 'S-0', 'where': first['_where'], 'message': f'对象缺少 {k}（可用常量）'})
  prof = opt(first, 'professional') or spec.get('professional') or default_prof
  if prof not in PROFESSIONALS: problems.append({'check': 'S-0', 'where': first['_where'], 'message': f'专业无效：{prof}'}); continue
  lines = '、'.join(str(x['rec']['_n']) for x in rows)
  src = opt(first, 'source') or ' '.join(v for v in (dr, sid, ('句柄 ' + '、'.join(dict.fromkeys(x['handle'] for x in rows if x['handle']))) if any(x['handle'] for x in rows) else None) if v) + f"（{spec['path']} 第 {lines} 行）"
  if hids: src += '；中性记录 ' + '、'.join(dict.fromkeys(str(h) for h in hids))
  detail = opt(first, 'detail')
  if detail is None: detail = '；'.join(dict.fromkeys(notes)) or (f"{spec['key']}：{len(rows)} 行合并" if len(rows) > 1 else spec['key'])
  # A merged line lists each row's value (e.g. segment lengths) as text; it never adds them up.
  attrs = {a: '、'.join(dict.fromkeys((x['rec']['_raw'].get(c) or '').strip() for x in rows)) for a, c in (spec.get('attributes') or {}).items()}
  quantities = []
  for q in spec.get('quantities') or []:
   v = (first['_raw'].get(q.get('column')) or '').strip()
   try: value = None if v == '' else float(v.replace(',', ''))
   except ValueError: problems.append({'check': 'S-0', 'where': first['_where'], 'message': f"{q.get('name')} 不是数字：{v}"}); value = None
   quantities.append({'name': q.get('name'), 'value': value, 'unit': q.get('unit'), 'level': q.get('level')})
  o = {'id': oid, 'catalog_key': ckey, 'professional': prof, 'scene_id': scene_id, 'kind': kind or 'unlocated',
       'points': polys[0] if kind else [], 'title': opt(first, 'title'), 'category': opt(first, 'category'), 'status': opt(first, 'status'),
       'detail': detail, 'source': src, 'quantities': quantities, 'attributes': attrs,
       '_rows': [(spec['key'], x['rec']['_n']) for x in rows], '_bounds_known': bool(sheet and sheet['bounds'])}
  if kind == 'line' and len(polys) > 1: o['parts'] = polys
  if kind == 'polygon': o['points'] = polys[0]
  if not kind:
   o['unlocated_reason'] = reason
   if note: o['unlocated_note'] = note
   tinfo['unlocated'][reason] += 1
  tinfo['objects'] += 1
  for rk in o['_rows']: source_use[rk].append(ckey)
  draws.append(o)
 # Scenes actually used (contract 4: no empty pages) and their bounds.
 used = {}
 for o in draws:
  if o['scene_id']: used.setdefault(o['scene_id'], []).append(o)
 scenes, targets_by_drawing, scene_meta = [], defaultdict(list), {}
 for scene_id, objs in sorted(used.items()):
  if scene_id in composites:
   s = composites[scene_id]; base_sheet = sheets[(s['drawing'], s['base'])]; dr = s['drawing']
   bounds, basis, title = base_sheet['bounds'], None, s.get('title') or base_sheet['title']
   parts = [(sid, dx, dy, o) for (d0, sid), (sc, dx, dy, o) in home.items() if sc == scene_id]
  else:
   dr, sid = scene_id.split('|', 1); sh = sheets[(dr, sid)]
   bounds, basis = sh['bounds'], None
   title = sh['title'] + f"（{dr} {sid}" + (f"，{sh['floor']}" if sh['floor'] else '') + '）'
   parts = [(sid, 0.0, 0.0, {})]
  if bounds is None:
   pts = [p for o in objs if o['kind'] != 'unlocated' for q in (o.get('parts') or [o['points']]) for p in q]
   if not pts:
    for o in objs: o['scene_id'] = None
    continue
   xs, ys = [p[0] for p in pts], [p[1] for p in pts]; pad = max(0.05 * max(max(xs) - min(xs), max(ys) - min(ys)), 1000 / UNIT_MM[drawings[dr]['unit']])
   bounds, basis = [min(xs) - pad, min(ys) - pad, max(xs) + pad, max(ys) + pad], '按对象外包框推定'
  # Located objects outside the declared page become unlocated (contract 8); the raw coordinate is kept as an attribute.
  if basis is None:
   tol = 0.02 * math.hypot(bounds[2] - bounds[0], bounds[3] - bounds[1])
   for o in objs:
    if o['kind'] == 'unlocated': continue
    pts = [p for q in (o.get('parts') or [o['points']]) for p in q]
    if any(p[0] < bounds[0] - tol or p[0] > bounds[2] + tol or p[1] < bounds[1] - tol or p[1] > bounds[3] + tol for p in pts):
     o['attributes']['原坐标'] = json.dumps(pts[0], ensure_ascii=False)
     o.update(kind='unlocated', points=[], unlocated_reason='outside_sheet'); o.pop('parts', None)
     next(t for t in report_tables if t['key'] == o['_rows'][0][0])['unlocated']['outside_sheet'] += 1
  drw = drawings[dr]
  stats = Counter()
  sc = {'id': scene_id, 'title': title, 'source_id': dr, 'coordinate_unit': drw['unit'], 'y_axis': 'up', 'bounds': bounds, 'background': []}
  if basis: sc['bounds_basis'] = basis
  if scene_id not in composites and sheets[(dr, scene_id.split('|', 1)[1])]['sheet_kind']: sc['sheet_kind'] = sheets[(dr, scene_id.split('|', 1)[1])]['sheet_kind']
  if scene_id not in composites and sheets[(dr, scene_id.split('|', 1)[1])]['raster_path']: sc['raster_path'] = str((base / sheets[(dr, scene_id.split('|', 1)[1])]['raster_path']).resolve())
  items, filters_desc = [], []
  for sid, dx, dy, ov in parts:
   cfg = {**{k: v for k, v in global_bg.items()}, **(drw['background'] or {}), **((composites.get(scene_id) or {}).get('background') or {}), **{k: v for k, v in ov.items() if k in ('layers_include', 'layers_exclude')}}
   where = f'{scene_id} 底图（{sid}）'
   flt = {'include': regexes(cfg.get('layers_include'), where, problems), 'exclude': regexes(cfg.get('layers_exclude'), where, problems),
          'types': set(cfg.get('include_types') or []), 'text_max_len': int(cfg.get('text_max_len', 30))}
   sb = sheets[(dr, sid)]['bounds'] or bounds
   targets_by_drawing[dr].append({'sheet_bounds': sb, 'dx': dx, 'dy': dy, 'precision': drw['precision'], 'filter': flt, 'items': items, 'stats': stats})
   filters_desc.append({'sheet': sid, 'bounds': sb, 'dx': dx, 'dy': dy, 'layers_include': cfg.get('layers_include') or [], 'layers_exclude': cfg.get('layers_exclude') or [],
                        'include_types': sorted(flt['types']), 'text_max_len': flt['text_max_len']})
  scene_meta[scene_id] = {'scene': sc, 'items': items, 'stats': stats, 'filters': filters_desc, 'drawing': dr}
  scenes.append(sc)
 for dr, ts in targets_by_drawing.items():
  scan_layout(drawings[dr]['layout'], set(), ts, resolve_counter)
 background_sets = {}
 for scene_id, meta in scene_meta.items():
  key = hashlib.sha256(json.dumps([drawings[meta['drawing']]['sha256'], meta['filters']], ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16]
  background_sets[key] = meta['items']; meta['scene']['background_refs'] = [key]; meta['key'] = key
 used_drawings = sorted({s['source_id'] for s in scenes} | {o['scene_id'].split('|', 1)[0] for o in draws if o['scene_id']})
 # Groups (contract 9).
 groups = []
 by_key = {o['catalog_key']: o for o in draws}
 for spec in m.get('groups') or []:
  rows, _ = read_rows(spec, GROUP_KEYS, base, problems, files, 'groups')
  mem = defaultdict(list)
  if isinstance(spec.get('members'), dict):
   mrows, _ = read_rows(spec['members'], ('group_id', 'catalog_key'), base, problems, files, 'groups.members')
   for r in mrows:
    if opt(r, 'catalog_key') not in by_key: problems.append({'check': 'S-0', 'where': r['_where'], 'message': '问题组成员不存在：' + str(r.get('catalog_key'))}); continue
    mem[opt(r, 'group_id')].append(r['catalog_key'])
  for r in rows:
   gid = opt(r, 'id')
   g = {'id': gid, **{k: opt(r, k) for k in ('title', 'category', 'status', 'proposal', 'reason')}, 'member_keys': list(dict.fromkeys(mem.get(gid, [])))}
   groups.append(g)
 sources = [{'id': d, 'title': drawings[d]['title'], 'sha256': drawings[d]['sha256']} for d in used_drawings if d in drawings]
 view = {'schema': VIEW_SCHEMA_COMPARISON, **{k: view_cfg[k] for k in ('project_id', 'review_id', 'title', 'revision') if k in view_cfg},
         'sources': sources, 'scenes': scenes, 'background_sets': background_sets, 'objects': draws, 'groups': groups, 'receipts': {}, 'summary': [],
         'scope_notes': list(view_cfg.get('scope_notes') or [])}
 # Bidirectional checks S-1..S-5 (contract 12).
 checks = {}
 total_rows = sum(t['rows'] for t in report_tables); skipped = sum(sum(t['skipped'].values()) for t in report_tables)
 used_rows = len(source_use); doubled = [k for k, v in source_use.items() if len(v) > 1]
 dropped = total_rows - used_rows - skipped
 checks['S-1'] = {'rows': total_rows, 'rows_in_objects': used_rows, 'skipped': skipped, 'pass': not dropped}
 if dropped: problems.append({'check': 'S-1', 'where': '对象表', 'message': f'{dropped} 行既没进对象也没列为跳过（多为上面已报的行级错误）'})
 if doubled: problems.append({'check': 'S-2', 'where': '对象表', 'message': f'{len(doubled)} 行被两个对象引用'})
 checks['S-2'] = {'objects': len(draws), 'all_have_rows': all(o['_rows'] for o in draws), 'rows_used_twice': len(doubled), 'pass': not doubled and all(o['_rows'] for o in draws)}
 outside = []
 for o in draws:
  if o['kind'] == 'unlocated': continue
  sc = next(s for s in scenes if s['id'] == o['scene_id']); b = sc['bounds']; tol = 0.02 * math.hypot(b[2] - b[0], b[3] - b[1])
  if any(p[0] < b[0] - tol or p[0] > b[2] + tol or p[1] < b[1] - tol or p[1] > b[3] + tol for q in (o.get('parts') or [o['points']]) for p in q): outside.append(o['catalog_key'])
 checks['S-3'] = {'located': sum(o['kind'] != 'unlocated' for o in draws), 'outside_scene': outside, 'pass': not outside}
 if outside: problems.append({'check': 'S-3', 'where': '对象', 'message': '定位对象超出场景范围：' + '、'.join(outside[:5])})
 hcount = Counter()
 for g in drafts:
  for x in g['rows']:
   if x['use'] == 'handle': hcount[x.get('reason') or 'found'] += 1
 checks['S-4'] = {'handle_rows': sum(hcount.values()), **hcount, 'duplicate_top_level_handles': resolve_counter['handle_duplicates'], 'pass': True}
 checks['S-5'] = {'scenes': len(scenes), 'pass': all(k in background_sets for s in scenes for k in s.get('background_refs', []))}
 for o in draws: o.pop('_rows'); o.pop('_bounds_known')
 info = {'tables': [{**{k: v for k, v in t.items() if k not in ('skipped', 'unlocated')}, 'skipped': dict(t['skipped']), 'unlocated': dict(t['unlocated'])} for t in report_tables],
         'scenes': [{'id': s['id'], 'title': s['title'], 'objects': dict(Counter(o['kind'] for o in used.get(s['id'], []))), **dict(scene_meta[s['id']]['stats']),
                     **({'bounds_basis': s['bounds_basis']} if s.get('bounds_basis') else {}), 'background_key': scene_meta[s['id']]['key'], 'background_filters': scene_meta[s['id']]['filters']} for s in scenes],
         'objects': dict(Counter(o['kind'] for o in draws)), 'unlocated': dict(Counter(o.get('unlocated_reason') for o in draws if o['kind'] == 'unlocated')),
         'checks': checks}
 return view, info


def size_estimate(view):
 raw = json.dumps(view, ensure_ascii=False, separators=(',', ':')).encode(); gz = gzip.compress(raw, mtime=0)
 return {'raw_json_bytes': len(raw), 'gzip_bytes': len(gz), 'html_bytes_estimate': len(base64.b64encode(gz)) + 160_000}


def main():
 p = argparse.ArgumentParser(description='映射加项目表格生成图面视图（图面视图映射契约）')
 p.add_argument('--mapping', required=True, type=Path); p.add_argument('--neutral-root', required=True, type=Path)
 p.add_argument('--output', type=Path); p.add_argument('--report', type=Path); p.add_argument('--estimate', action='store_true', help='只出检查报告和体量预估，不写视图')
 a = p.parse_args()
 report = {'status': 'INPUT_REQUIRED', 'quantity_changes': 0}
 problems, files = [], []
 try:
  if not a.estimate and not a.output: raise ValueError('须给 --output，或用 --estimate 只预估')
  if a.output and a.output.exists(): raise ValueError('输出视图已存在，请用新的版本名')
  if a.report and a.report.resolve() in {x.resolve() for x in (a.mapping, a.output) if x}: raise ValueError('检查报告路径不能覆盖映射或视图')
  m = json.loads(a.mapping.read_text(encoding='utf-8-sig'))
  if not isinstance(m, dict) or m.get('schema') != MAPPING_SCHEMA: raise ValueError('映射文件 schema 须为 ' + MAPPING_SCHEMA)
  warnings = mapping_warnings(m)
  if warnings: report['mapping_warnings'] = warnings
  view, info = build(m, a.mapping.resolve().parent, a.neutral_root.resolve(), problems, files)
  if problems: raise InputError(problems)
  base = a.output.resolve().parent if a.output else a.mapping.resolve().parent
  # Raster paths are given relative to the sheet table's mapping; the written view keeps them relative to itself.
  for s in view['scenes']:
   if s.get('raster_path'):
    try: s['raster_path'] = os.path.relpath(s['raster_path'], base)
    except ValueError: pass
  normalized = normalize_view(view, base)
  info['checks']['S-6'] = {'normalize_view': 'PASS'}
  report.update(status='PASS', mapping_sha256=sha_file(a.mapping), input_tables=files, **info, size_estimate=size_estimate(normalized), result_fingerprint=normalized['result_fingerprint'])
  if not a.estimate:
   a.output.parent.mkdir(parents=True, exist_ok=True)
   with a.output.open('x', encoding='utf-8', newline='\n') as f: json.dump(view, f, ensure_ascii=False, separators=(',', ':'))
   report['output'] = str(a.output)
  else: report['status'] = 'ESTIMATE'
  code = 0
 except InputError as e:
  report['error'] = str(e); report['problems'] = e.problems; code = 2
 except (OSError, ValueError, KeyError, TypeError) as e:
  report['error'] = str(e); code = 2
  if problems: report['problems'] = problems
 if a.report:
  a.report.parent.mkdir(parents=True, exist_ok=True); a.report.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding='utf-8')
 summary = {k: report[k] for k in ('status', 'output', 'objects', 'unlocated', 'size_estimate', 'error') if k in report}
 if 'problems' in report: summary['problem_count'] = len(report['problems']); summary['first_problems'] = report['problems'][:5]
 if 'mapping_warnings' in report: summary['mapping_warning_count'] = len(report['mapping_warnings'])
 print(json.dumps(summary, ensure_ascii=False)); return code


if __name__ == '__main__': raise SystemExit(main())
