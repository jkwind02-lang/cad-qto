"""Optional third-party quantity comparison for qto-review-view/1.1.

Checks that professional-supplied numbers agree with each other in both
directions (item <-> detail rows <-> member objects, source rows <-> items).
It never computes, rounds or rewrites an engineering quantity; the only values
it fills are the display total and the estimate-dominant flag, both by fixed
rules written in the contract.
"""
import math, re

LEVELS = ('确认量', '规则推算量', '方案暂估量', '未决量')
TOTAL_LEVELS = LEVELS[:3]
CONCLUSIONS = ('一致', '多算', '少算', '第三方无量', '图上无', '缺图', '归入他项')
SIGNED = ('一致', '多算', '少算')
HANDLERS = {'decide': '需要你定口径', 'confirm': '需补资料或确认', 'assumed': '已按假设计入', 'closed': '已处理，仅留记录'}
ROLES = ('overview', 'section', 'filter')
SOURCE_KINDS = ('审计清单', '招标清单', '对方报量', '甲方核定量', '其他')
AGGREGATIONS = ('leaf', 'sum_members', 'declared')
VERDICTS = ('同意', '要改', '待定')
RATE_TOLERANCE = 0.0005
MEMBER_COLUMN = re.compile(r'(id|title|status|source|detail|(attributes|quantities)\..+)')
MAX_LISTED = 40


class ComparisonError(ValueError):
 """Carries every problem so the professional can fix them in one pass."""
 def __init__(self, problems):
  self.problems = problems
  counts = {}
  for p in problems: counts[p['check']] = counts.get(p['check'], 0) + 1
  head = '；'.join(f"{k} {v} 处" for k, v in sorted(counts.items()))
  shown = '\n'.join(f"[{p['check']}] {p['where']}：{p['message']}" for p in problems[:MAX_LISTED])
  more = f'\n……另有 {len(problems) - MAX_LISTED} 处，见检查报告 problems' if len(problems) > MAX_LISTED else ''
  super().__init__(f'对照数据未通过双向校验（{head}）\n{shown}{more}')


def text(v): return isinstance(v, str) and bool(v.strip())
def number(v): return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
def num_or_none(v): return v is None or number(v)
def close(a, b):
 if a is None or b is None: return a is None and b is None
 return abs(a - b) <= 1e-6 * max(1.0, abs(a), abs(b))
def total_of(values):
 present = [v for v in values if v is not None]
 return sum(present) if present else None


def normalize_comparison(view, object_keys):
 """Validate view['comparison'] in place; returns the generation report."""
 c = view['comparison']; problems = []; exceptions = []
 def bad(check, where, message): problems.append({'check': check, 'where': where, 'message': message})
 def note(kind, where, detail): exceptions.append({'kind': kind, 'where': where, 'detail': detail})
 if not isinstance(c, dict): raise ComparisonError([{'check': 'C3-0', 'where': 'comparison', 'message': '须为对象'}])
 for k in ('dimensions', 'sources', 'items', 'issues', 'member_tables'):
  c.setdefault(k, [])
  if not isinstance(c[k], list): bad('C3-0', 'comparison.' + k, '须为数组'); c[k] = []
 terms = c.setdefault('display_terms', {})
 if not isinstance(terms, dict) or not all(text(v) for v in terms.values()): bad('C3-0', 'display_terms', '须为文字映射'); c['display_terms'] = {}
 c['display_terms'].setdefault('third_party', '第三方')
 labels = c.setdefault('handler_labels', {})
 if not isinstance(labels, dict) or not all(k in HANDLERS and text(v) for k, v in labels.items()): bad('C3-0', 'handler_labels', '键只能是 decide/confirm/assumed/closed，值为文字'); labels = c['handler_labels'] = {}
 c['handler_labels'] = {k: labels.get(k, v) for k, v in HANDLERS.items()}
 th = c.setdefault('thresholds', {})
 if not isinstance(th, dict): bad('C3-0', 'thresholds', '须为对象'); th = c['thresholds'] = {}
 th.setdefault('difference_rate_alert', 0.2)
 for k in ('difference_rate_alert', 'consistent_band'):
  if k in th and not (number(th[k]) and th[k] >= 0): bad('C3-0', 'thresholds.' + k, '须为非负数字'); th.pop(k)
 by_unit = th.setdefault('consistent_band_by_unit', {})
 if not isinstance(by_unit, dict) or not all(text(u) and number(b) and b >= 0 for u, b in by_unit.items()): bad('C3-0', 'thresholds.consistent_band_by_unit', '须为 {单位: 非负数字}'); by_unit = th['consistent_band_by_unit'] = {}

 # Dimensions: closed value domains.
 dimvals = {}; roles = {r: 0 for r in ROLES}
 for i, dim in enumerate(c['dimensions']):
  where = f'dimensions[{i}]'
  if not isinstance(dim, dict) or not text(dim.get('id')): bad('C3-0', where, '维度须有 id'); continue
  if dim['id'] in dimvals: bad('C3-0', where, '维度编号重复：' + dim['id']); continue
  if not text(dim.get('title')): bad('C3-0', where, '维度须有 title')
  if dim.get('role') not in ROLES: bad('C3-0', where, 'role 只能是 overview/section/filter')
  else: roles[dim['role']] += 1
  ids = {}
  for j, v in enumerate(dim.get('values') if isinstance(dim.get('values'), list) else []):
   if not isinstance(v, dict) or not text(v.get('id')) or v['id'] in ids: bad('C3-0', f'{where}.values[{j}]', '维度值编号缺失或重复'); continue
   v.setdefault('title', v['id'])
   if 'order' in v and not number(v['order']): bad('C3-0', f'{where}.values[{j}]', 'order 须为数字')
   if 'merged_from' in v and not (isinstance(v['merged_from'], list) and all(isinstance(x, str) for x in v['merged_from'])): bad('C3-0', f'{where}.values[{j}]', 'merged_from 须为文字数组')
   ids[v['id']] = v
  if not isinstance(dim.get('values'), list): bad('C3-0', where, '维度须有 values 数组')
  dimvals[dim['id']] = set(ids)
 if c['dimensions'] and roles['overview'] != 1: bad('C3-0', 'dimensions', 'overview 维度须恰好一个')
 if roles['section'] > 1: bad('C3-0', 'dimensions', 'section 维度最多一个')

 def check_dims(dims, where, allow_list=False):
  if dims is None: return {}
  if not isinstance(dims, dict): bad('C3-0', where, 'dimensions 须为对象'); return {}
  for k, v in dims.items():
   if k not in dimvals: bad('C3-0', where, '未声明的维度：' + str(k)); continue
   values = v if allow_list and isinstance(v, list) else [v]
   for x in values:
    if not isinstance(x, str) or x not in dimvals[k]: bad('C3-0', where, f'维度 {k} 的值不在值域内：{x}')
  return dims

 # Third-party sources.
 sources = {}; source_order = []
 for i, s in enumerate(c['sources']):
  where = f'sources[{i}]'
  if not isinstance(s, dict) or not text(s.get('id')) or s['id'] in sources: bad('C3-0', where, '来源编号缺失或重复'); continue
  if s.get('kind') not in SOURCE_KINDS: bad('C3-0', where, 'kind 只能是：' + '、'.join(SOURCE_KINDS))
  if not text(s.get('title')): bad('C3-0', where, '来源须有 title')
  files = s.get('files')
  if not isinstance(files, list) or not files or not all(isinstance(f, dict) and text(f.get('title')) and isinstance(f.get('sha256'), str) and re.fullmatch('[0-9a-fA-F]{64}', f['sha256']) for f in files):
   bad('C3-0', where, 'files 须列出来源文件的标题和 sha256')
  rows = s.get('row_ids')
  if not isinstance(rows, list) or not all(text(r) for r in rows): bad('C3-3', where, 'row_ids 须为文字数组'); rows = []
  elif len(set(rows)) != len(rows): bad('C3-3', where, 'row_ids 有重复编号')
  if s.get('row_ids_origin', 'independent') not in ('independent', 'items'): bad('C3-0', where, 'row_ids_origin 只能是 independent/items')
  if s.get('row_ids_origin') == 'items': note('来源行编号取自条目', s['id'], '来源行没有独立抽取，C3-3 只能查重号和越界，查不出漏行')
  sources[s['id']] = s; source_order.append(s['id'])
 member_tables = set()
 for i, t in enumerate(c['member_tables']):
  if not isinstance(t, dict) or not text(t.get('id')) or t['id'] in member_tables or not text(t.get('title')): bad('C3-0', f'member_tables[{i}]', '台账表头须有不重复的 id 和 title'); continue
  cols = t.get('columns')
  if not isinstance(cols, list) or not cols or not all(isinstance(x, dict) and isinstance(x.get('key'), str) and MEMBER_COLUMN.fullmatch(x['key']) and text(x.get('title')) for x in cols):
   bad('C3-0', f'member_tables[{i}]', 'columns 只能取对象已有字段（id/title/status/source/detail/attributes.*/quantities.*）')
  member_tables.add(t['id'])

 objects = view.get('objects', []); bykey = {o['catalog_key']: o for o in objects}
 for o in objects:
  if 'dimensions' in o: check_dims(o['dimensions'], '对象 ' + o['catalog_key'])

 # Items, segments and detail rows.
 items = {}; row_seen = {}; detail_ids = set(); member_rows = {}
 for i, it in enumerate(c['items']):
  where = f'items[{i}]'
  if not isinstance(it, dict) or not text(it.get('id')) or it['id'] in items: bad('C3-0', where, '条目编号缺失或重复'); continue
  iid = it['id']; where = '条目 ' + iid; items[iid] = it
  if it.get('kind') not in ('third_party', 'extra'): bad('C3-0', where, 'kind 只能是 third_party/extra')
  for k in ('name', 'features'):
   if k in it and it[k] is not None and not isinstance(it[k], str): bad('C3-0', where, k + ' 须为文字')
  if it.get('kind') == 'extra' and not text(it.get('name')): bad('C3-0', where, '清单外条目须有 name')
  it['dimensions'] = check_dims(it.get('dimensions'), where)
  ours = it.get('ours')
  if not isinstance(ours, dict) or not text(ours.get('unit')): bad('C3-0', where, 'ours.unit 必填'); ours = it['ours'] = {'unit': None, 'levels': {}}
  lv = ours.get('levels', {})
  if not isinstance(lv, dict) or not set(lv) <= set(LEVELS): bad('C3-0', where, 'ours.levels 的键只能是' + '、'.join(LEVELS)); lv = {}
  for k in LEVELS:
   if not num_or_none(lv.get(k)): bad('C3-0', where, f'{k} 须为有限数字或 null'); lv[k] = None
  ours['levels'] = {k: lv.get(k) for k in LEVELS}
  total = total_of(ours['levels'][k] for k in TOTAL_LEVELS)
  if 'total' in ours and ours['total'] is not None and not number(ours['total']): bad('C3-0', where, 'ours.total 须为数字或 null')
  elif 'total' in ours and not close(ours['total'], total): bad('C3-5', where, f"本次合计 {ours['total']} 与确认量+规则推算量+方案暂估量 {total} 不符")
  ours['total'] = total
  e = ours['levels']['方案暂估量'] or 0; a = (ours['levels']['确认量'] or 0) + (ours['levels']['规则推算量'] or 0)
  dominant = e > 0 and e >= a
  if 'estimate_dominant' in it and it['estimate_dominant'] is not dominant: bad('C3-5', where, '以暂估为主标记与规则（方案暂估量>0 且 ≥ 确认量+规则推算量）不符')
  it['estimate_dominant'] = dominant
  segs = it.get('third_parties', [])
  if not isinstance(segs, list): bad('C3-0', where, 'third_parties 须为数组'); segs = it['third_parties'] = []
  if it.get('kind') == 'extra' and segs: bad('C3-8', where, '清单外条目不能有来源段')
  if it.get('kind') == 'third_party' and not segs: bad('C3-8', where, '第三方条目至少一个来源段')
  seg_sources = []
  for j, seg in enumerate(segs):
   sw = f'{where} 来源段[{j}]'
   if not isinstance(seg, dict) or seg.get('source_id') not in sources: bad('C3-0', sw, '来源编号不存在'); continue
   sid = seg['source_id']; sw = f'{where} 来源 {sid}'
   if sid in seg_sources: bad('C3-8', sw, '同一来源只能有一段')
   seg_sources.append(sid)
   rows = seg.get('rows')
   if not isinstance(rows, list) or not rows: bad('C3-8', sw, 'rows 至少一行'); rows = []
   for r in rows:
    if not isinstance(r, dict) or not text(r.get('row_id')) or not text(r.get('unit')) or not num_or_none(r.get('quantity')): bad('C3-8', sw, '来源行须有 row_id、unit，quantity 为数字或 null'); continue
    for k in ('seq', 'code', 'name', 'features'):
     if k in r and r[k] is not None and not isinstance(r[k], str): bad('C3-0', sw, f'来源行 {k} 须为文字')
    row_seen.setdefault(sid, []).append((r['row_id'], iid))
   unit = seg.get('unit'); qty = seg.get('quantity')
   if not text(unit) or not num_or_none(qty): bad('C3-8', sw, '来源段须有 unit，quantity 为数字或 null'); continue
   good_rows = [r for r in rows if isinstance(r, dict) and text(r.get('unit'))]
   if any(r['unit'] != unit for r in good_rows): bad('C3-8', sw, '来源行单位与来源段单位不同')
   present = [r.get('quantity') for r in good_rows if number(r.get('quantity'))]
   expect = sum(present) if present else None
   if not close(qty, expect): bad('C3-8', sw, f'来源段数量 {qty} 与各行数量之和 {expect} 不符')
   if present and len(present) < len(good_rows):
    if not text(seg.get('quantity_note')): bad('C3-8', sw, '部分来源行没有数量，须写 quantity_note')
    else: note('部分来源行无量', sw, seg['quantity_note'])
   conclusion = seg.get('conclusion')
   if conclusion is not None and conclusion not in CONCLUSIONS: bad('C3-5', sw, '结论只能取：' + '、'.join(CONCLUSIONS)); conclusion = False
   same_unit = unit == ours.get('unit')
   diff = seg.get('difference')
   if diff is not None and not (isinstance(diff, dict) and num_or_none(diff.get('value')) and num_or_none(diff.get('rate'))): bad('C3-5', sw, 'difference 须为 {value, rate}，数字或 null'); diff = None
   seg['difference'] = diff
   if not same_unit:
    if not text(seg.get('unit_mismatch_note')): bad('C3-5', sw, f'单位 {unit} 与本次单位 {ours.get("unit")} 不同，须写 unit_mismatch_note')
    else: note('单位不同', sw, seg['unit_mismatch_note'] + ('；结论由专业判，不按带宽' if conclusion in SIGNED else ''))
    if diff and (diff.get('value') is not None or diff.get('rate') is not None): bad('C3-5', sw, '单位不同时 difference 须为 null')
    if conclusion is None: bad('C3-5', sw, '单位不同不能按带宽判，须写结论')
   else:
    value = diff.get('value') if diff else None
    # Uniform band (contract 5.4): one rule for the whole page, never a per-item judgement.
    judged = qty not in (None, 0) and total is not None and (conclusion is None or conclusion in SIGNED)
    if judged:
     band = by_unit.get(unit, th.get('consistent_band'))
     if band is None: bad('C3-5', sw, '按带宽判结论须给 thresholds.consistent_band')
     else:
      r = (total - qty) / qty
      auto = '一致' if (close(total, qty) if band == 0 else abs(r) <= band + 1e-12) else '多算' if r > 0 else '少算'
      if conclusion is None: conclusion = seg['conclusion'] = auto
      elif conclusion != auto: bad('C3-5', sw, f'结论“{conclusion}”与统一带宽判定“{auto}”不同（差额率 {r:.4%}，带宽 {band:.2%}）')
     if value is None: bad('C3-5', sw, '按带宽判的段须有差额')
    elif conclusion is None: bad('C3-5', sw, '来源数量为空或 0、或本次没有合计，不能按带宽判，须写结论')
    elif conclusion in SIGNED: bad('C3-5', sw, f'结论“{conclusion}”只能用于同单位、来源数量不为 0 且本次有合计的段')
    if value is not None:
     base = 0.0 if qty is None and conclusion == '第三方无量' else qty   # contract 5: no third-party quantity counts as 0
     if total is None or base is None: bad('C3-5', sw, '有差额时本次合计和来源数量都须有值（仅“第三方无量”的来源数量可空，按 0 计）')
     else:
      if not close(value, total - base): bad('C3-5', sw, f'差额 {value} 不等于本次合计 {total} − 来源数量 {base}')
      rate = diff.get('rate')
      if base == 0:
       if rate is not None: bad('C3-5', sw, '来源数量为空或 0 时差额率须为 null')
      elif rate is None or abs(rate - value / base) > RATE_TOLERANCE + 1e-9: bad('C3-5', sw, f'差额率 {rate} 与差额 ÷ 来源数量 {value / base:.6f} 不符')
   if conclusion is False: conclusion = None
   corrected = seg.get('third_party_corrected', False)
   if not isinstance(corrected, bool): bad('C3-5', sw, 'third_party_corrected 须为 true/false'); corrected = False
   seg['third_party_corrected'] = corrected
   if corrected:
    if not text(seg.get('correction_note')): bad('C3-5', sw, '标为第三方条目有误已修正，须写 correction_note')
    else: note('第三方条目有误已修正', sw, seg['correction_note'])
   seg.setdefault('redirect_to', [])
   if not isinstance(seg['redirect_to'], list) or not all(text(x) for x in seg['redirect_to']) or len(set(seg['redirect_to'])) != len(seg['redirect_to']): bad('C3-5', sw, 'redirect_to 须为不重复的条目编号数组'); seg['redirect_to'] = []
   if conclusion == '归入他项' and not seg['redirect_to']: bad('C3-5', sw, '结论“归入他项”须写 redirect_to')
   if iid in seg['redirect_to']: bad('C3-5', sw, '去向不能是自己')
   ev = seg.get('search_evidence')
   if conclusion == '图上无':
    if not isinstance(ev, dict): bad('C3-5', sw, '结论“图上无”须附检索依据（drawings、targets、method、hits）')
    else:
     missing = [k for k in ('drawings', 'targets') if not (text(ev.get(k)) or (isinstance(ev.get(k), list) and ev[k] and all(text(x) for x in ev[k])))]
     if not text(ev.get('method')): missing.append('method')
     if not (number(ev.get('hits')) and ev['hits'] >= 0): missing.append('hits')
     if missing: bad('C3-5', sw, '检索依据缺：' + '、'.join(missing))
   elif ev is not None and not isinstance(ev, dict): bad('C3-5', sw, 'search_evidence 须为对象或 null')
   if conclusion == '缺图' and not text(seg.get('missing_drawing_note')): bad('C3-5', sw, '结论“缺图”须写 missing_drawing_note')
   if conclusion == '第三方无量' and qty not in (None, 0): bad('C3-5', sw, '结论“第三方无量”时来源数量须为 null 或 0')
  if seg_sources and [s for s in source_order if s in seg_sources] != seg_sources: bad('C3-8', where, '来源段须按 sources 的顺序排列')

  # Detail rows: item -> rows (C3-1), rows -> members (C3-2).
  rows = it.get('detail_rows', [])
  if not isinstance(rows, list): bad('C3-0', where, 'detail_rows 须为数组'); rows = it['detail_rows'] = []
  by_level = {k: [] for k in LEVELS}
  for j, r in enumerate(rows):
   rw = f'{where} 明细[{j}]'
   if not isinstance(r, dict) or not text(r.get('id')) or r['id'] in detail_ids: bad('C3-0', rw, '明细行编号缺失或全视图重复'); continue
   detail_ids.add(r['id']); rw = f'{where} 明细 {r["id"]}'
   if r.get('level') not in LEVELS: bad('C3-1', rw, '层级只能是' + '、'.join(LEVELS)); continue
   if not num_or_none(r.get('quantity')): bad('C3-1', rw, '数量须为有限数字或 null'); continue
   if r.get('unit') != ours.get('unit'): bad('C3-1', rw, f'明细单位 {r.get("unit")} 与条目单位 {ours.get("unit")} 不同'); continue
   for k in ('basis', 'area', 'drawing', 'locator', 'relation', 'note'):
    if k in r and r[k] is not None and not isinstance(r[k], str): bad('C3-0', rw, k + ' 须为文字')
   r['dimensions'] = check_dims(r.get('dimensions'), rw)
   by_level[r['level']].append(r.get('quantity'))
   members = r.setdefault('member_keys', [])
   if not isinstance(members, list) or not all(isinstance(k, str) for k in members) or len(set(members)) != len(members): bad('C3-2', rw, 'member_keys 须为不重复的文字数组'); continue
   missing = [k for k in members if k not in object_keys]
   if missing: bad('C3-2', rw, '成员对象不存在：' + '、'.join(missing[:5])); continue
   agg = r.get('aggregation') or ('leaf' if not members else None)
   if agg not in AGGREGATIONS: bad('C3-2', rw, '有成员时须声明 aggregation（sum_members/declared）'); continue
   r['aggregation'] = agg
   if r.get('member_table') is not None and r['member_table'] not in member_tables: bad('C3-2', rw, 'member_table 不存在')
   splits = r.get('shared_member_split', [])
   if not isinstance(splits, list) or not all(isinstance(s, dict) and s.get('member_key') in members and number(s.get('share')) and 0 < s['share'] <= 1 and text(s.get('basis')) for s in splits): bad('C3-4', rw, 'shared_member_split 每项须有本行成员、0～1 的份额和依据'); splits = []
   share = {s['member_key']: s['share'] for s in splits}
   for k in members: member_rows.setdefault(k, []).append((r['id'], share.get(k)))
   if agg == 'leaf' and members: bad('C3-2', rw, 'leaf 明细不能有成员')
   if agg == 'declared':
    if not text(r.get('aggregation_reason')): bad('C3-2', rw, 'declared 明细须写 aggregation_reason')
    else: note('明细数量不由成员合计', rw, r['aggregation_reason'])
   if agg == 'sum_members':
    name = r.get('member_quantity_name')
    if not members or not text(name): bad('C3-2', rw, 'sum_members 须有成员和 member_quantity_name'); continue
    values = []
    for k in members:
     hit = [q for q in bykey[k].get('quantities', []) if q.get('name') == name and q.get('unit') == r['unit'] and q.get('level') == r['level']]
     if len(hit) != 1: bad('C3-2', rw, f'成员 {k} 没有唯一的数量项 {name}（{r["unit"]}，{r["level"]}）'); values = None; break
     values.append(None if hit[0].get('value') is None else hit[0]['value'] * share.get(k, 1))
    if values is not None:
     expect = None if any(v is None for v in values) else sum(values)
     if not close(r.get('quantity'), expect): bad('C3-2', rw, f'明细数量 {r.get("quantity")} 与成员合计 {expect} 不符')
   if splits: note('成员拆分', rw, '；'.join(f"{s['member_key']} 份额 {s['share']}：{s['basis']}" for s in splits))
   for k in members:
    o = bykey[k]; od = o.get('dimensions') or {}
    diff_dims = [d for d in od if d in it['dimensions'] and od[d] != it['dimensions'][d]]
    if diff_dims:
     if text(o.get('dimension_mismatch_note')): note('对象维度与条目不同', '对象 ' + k, o['dimension_mismatch_note'])
     else: bad('C3-7', '对象 ' + k, f'维度 {"、".join(diff_dims)} 与所属条目 {iid} 不同，须写 dimension_mismatch_note')
  for lvl in LEVELS:
   got = ours['levels'][lvl]; vals = by_level[lvl]
   expect = total_of(vals) if vals else None
   if vals and not close(got, expect): bad('C3-1', where, f'{lvl} {got} 与明细合计 {expect} 不符')
   if not vals and got not in (None, 0): bad('C3-1', where, f'{lvl} 为 {got}，但没有该层级的明细行')

 # Member objects belong to at most one detail row unless split explicitly (C3-4).
 for k, uses in member_rows.items():
  if len(uses) > 1:
   if any(s is None for _, s in uses): bad('C3-4', '对象 ' + k, '属于多行明细：' + '、'.join(r for r, _ in uses) + '；须在每行写 shared_member_split')
   elif not close(sum(s for _, s in uses), 1.0): bad('C3-4', '对象 ' + k, '拆分份额合计不是 1')
 # Source rows appear exactly once (C3-3).
 for sid, s in sources.items():
  declared = s.get('row_ids') if isinstance(s.get('row_ids'), list) else []
  seen = {}
  for rid, iid in row_seen.get(sid, []): seen.setdefault(rid, []).append(iid)
  dup = [f'{r}（{"、".join(v)}）' for r, v in seen.items() if len(v) > 1]
  miss = [r for r in declared if r not in seen]; extra = [r for r in seen if r not in set(declared)]
  if dup: bad('C3-3', '来源 ' + sid, f'{len(dup)} 行出现多次：' + '；'.join(dup[:5]))
  if miss: bad('C3-3', '来源 ' + sid, f'{len(miss)} 行没有出现在任何条目里：' + '、'.join(miss[:5]))
  if extra: bad('C3-3', '来源 ' + sid, f'{len(extra)} 行不在 row_ids 里：' + '、'.join(extra[:5]))
 # Redirect targets exist (C3-5).
 for iid, it in items.items():
  for seg in it.get('third_parties', []) if isinstance(it.get('third_parties'), list) else []:
   for t in (seg.get('redirect_to') or []) if isinstance(seg, dict) else []:
    if t not in items: bad('C3-5', f'条目 {iid}', '去向条目不存在：' + t)
    elif items[t].get('ours', {}).get('total') in (None, 0): note('去向条目无本次量', f'条目 {iid} → {t}', '去向条目本次合计为空或 0')

 # Issues -> items (C3-6).
 issues = {}; groups = {g['id'] for g in view.get('groups', [])}; issue_index = {}
 for i, q in enumerate(c['issues']):
  where = f'issues[{i}]'
  if not isinstance(q, dict) or not text(q.get('id')) or q['id'] in issues: bad('C3-6', where, '问题编号缺失或重复'); continue
  qid = q['id']; where = '问题 ' + qid; issues[qid] = q
  if q.get('handler') not in HANDLERS: bad('C3-6', where, 'handler 只能是 decide/confirm/assumed/closed')
  if not text(q.get('title')) or not text(q.get('text')): bad('C3-6', where, 'title 和 text 必填')
  for k in ('impact_text', 'current_practice', 'status'):
   if k in q and q[k] is not None and not isinstance(q[k], str): bad('C3-6', where, k + ' 须为文字')
  imp = q.get('impact')
  if imp is not None and not (isinstance(imp, dict) and number(imp.get('value')) and text(imp.get('unit'))): bad('C3-6', where, 'impact 须为 {value, unit} 或 null')
  ids = q.setdefault('item_ids', [])
  if not isinstance(ids, list) or len(set(ids)) != len(ids): bad('C3-6', where, 'item_ids 须为不重复的数组'); ids = q['item_ids'] = []
  for t in ids:
   if t not in items: bad('C3-6', where, '关联条目不存在：' + str(t))
   else: issue_index.setdefault(t, []).append(qid)
  q['dimensions'] = check_dims(q.get('dimensions'), where, allow_list=True)
  refs = q.setdefault('source_refs', [])
  if not isinstance(refs, list) or not all(text(r) or (isinstance(r, dict) and text(r.get('ref')) and isinstance(r.get('text', ''), str)) for r in refs): bad('C3-6', where, 'source_refs 每项为编号文字或 {ref, text}')
  if q.get('group_id') is not None and q['group_id'] not in groups: bad('C3-6', where, '问题组不存在：' + str(q['group_id']))
 for iid, it in items.items(): it['issue_ids'] = issue_index.get(iid, [])

 # Uncertain content should reach an open issue (C3-9, reminder only; contract 0 and 14: resolving it is the page's first job).
 open_issues = {qid for qid, q in issues.items() if q.get('handler') != 'closed'}
 uncertain = 0; unlinked = 0
 for iid, it in items.items():
  if not isinstance(it.get('ours'), dict) or not isinstance(it['ours'].get('levels'), dict): continue
  rows = it.get('detail_rows') if isinstance(it.get('detail_rows'), list) else []
  tags = []
  if it['ours']['levels'].get('未决量') not in (None, 0) or any(isinstance(r, dict) and r.get('level') == '未决量' for r in rows): tags.append('有未决量')
  if it.get('estimate_dominant') is True: tags.append('以暂估为主')
  if any(isinstance(s, dict) and s.get('conclusion') == '缺图' for s in it.get('third_parties') or []): tags.append('缺图')
  it['uncertain'] = tags
  it['uncertain_unlinked'] = bool(tags) and not any(q in open_issues for q in it['issue_ids'])
  if tags: uncertain += 1
  if it['uncertain_unlinked']: unlinked += 1; note('不确定条目未挂问题', f'条目 {iid}', '、'.join(tags) + '；没有挂在未关闭的问题上')

 if problems: raise ComparisonError(problems)
 counts = {'items': sum(1 for it in items.values() if it.get('kind') == 'third_party'), 'extra_items': sum(1 for it in items.values() if it.get('kind') == 'extra'),
           'detail_rows': len(detail_ids), 'issues': len(issues), 'open_issues': len(open_issues), 'sources': len(sources),
           'source_rows': sum(len(v) for v in row_seen.values()), 'member_objects': len(member_rows),
           'uncertain_items': uncertain, 'uncertain_without_issue': unlinked}
 return {'counts': counts, 'exceptions': exceptions}
