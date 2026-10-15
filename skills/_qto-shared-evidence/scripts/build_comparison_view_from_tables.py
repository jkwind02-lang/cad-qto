"""Build a qto-review-view/1.1 comparison view from professional CSV tables plus a column mapping.

Only moves and renames values (contract docs/维护/复核页审核视图契约.md, section 12); it never
computes an engineering quantity. The result is checked by the shared contract before it is written.
"""
import argparse, copy, csv, hashlib, json
from pathlib import Path
from qto_review_contract import normalize_view, VIEW_SCHEMA_COMPARISON
from qto_review_comparison import ComparisonError, LEVELS

MAPPING_SCHEMA = 'qto-comparison-table-mapping/1.0'
TRUE, FALSE = {'是', 'true', 'True', 'TRUE', '1', 'Y', 'y'}, {'', '否', 'false', 'False', 'FALSE', '0', 'N', 'n'}
ROLES = ('items', 'source_rows', 'detail_rows', 'redirects', 'issues', 'issue_links', 'issue_sources', 'issue_impacts', 'members')
ITEM_KEYS = ('id', 'kind', 'source_id', 'name', 'features', 'unit', 'quantity_name', 'ours_note', *LEVELS, 'total', 'estimate_dominant',
             'row_id', 'seq', 'code', 'row_name', 'row_features', 'row_unit', 'row_quantity', 'tp_quantity', 'tp_unit', 'difference', 'difference_rate',
             'conclusion', 'disposition_note', 'third_party_corrected', 'correction_note', 'unit_mismatch_note', 'quantity_note', 'missing_drawing_note',
             'search_drawings', 'search_targets', 'search_method', 'search_hits', 'search_unread', 'search_uncovered', 'search_record_ref', 'search_note', 'redirect_to')
ITEM_LEVEL_KEYS = ('kind', 'name', 'features', 'unit', 'quantity_name', 'ours_note', *LEVELS, 'total', 'estimate_dominant')
KEYS = {'items': ITEM_KEYS,
        'source_rows': ('item_id', 'source_id', 'row_id', 'seq', 'code', 'row_name', 'row_features', 'row_unit', 'row_quantity'),
        'detail_rows': ('id', 'item_id', 'level', 'quantity', 'unit', 'basis', 'area', 'drawing', 'locator', 'relation', 'note', 'aggregation',
                        'aggregation_reason', 'member_keys', 'member_quantity_name', 'member_table'),
        'redirects': ('item_id', 'target_item_id', 'source_id'),
        'issues': ('id', 'handler', 'title', 'text', 'impact_text', 'current_practice', 'source_refs', 'status', 'group_id', 'item_ids'),
        'issue_links': ('issue_id', 'item_id'), 'issue_sources': ('issue_id', 'ref', 'text'), 'issue_impacts': ('issue_id', 'value', 'unit', 'note'),
        'members': ('catalog_key', 'object_id', 'detail_id', 'title', 'category', 'status', 'professional', 'detail', 'source')}
NUMBERS = {*LEVELS, 'total', 'row_quantity', 'tp_quantity', 'difference', 'difference_rate', 'search_hits', 'quantity', 'value'}
BOOLEANS = {'estimate_dominant', 'third_party_corrected'}
LISTS = {'redirect_to', 'member_keys', 'source_refs', 'item_ids'}


class TableError(ValueError):
 pass


def sha_file(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_table(spec, role, base, dims, problems, files):
 """Read one CSV into rows of canonical keys; bad cells are collected, never guessed."""
 path = (base / spec['path']).resolve(); files.append({'role': role, 'path': spec['path'], 'sha256': sha_file(path)})
 cols, consts = spec.get('columns', {}), spec.get('constants', {})
 maps, seps = spec.get('value_maps', {}), spec.get('list_separators', {})
 keys = [*KEYS.get(role, ()), *('dim.' + d for d in dims)]
 out = []
 with open(path, encoding='utf-8-sig', newline='') as f:
  reader = csv.DictReader(f); header = set(reader.fieldnames or [])
  missing = sorted({c for k, c in cols.items() if c not in header})
  if missing: problems.append({'check': 'TABLE', 'where': spec['path'], 'message': '映射的列不存在：' + '、'.join(missing)}); return out
  for n, raw in enumerate(reader, start=2):
   where = f"{spec['path']} 第 {n} 行"; rec = {'_where': where, '_raw': raw}
   for k in keys:
    if k in consts: value = consts[k]
    else:
     col = cols.get(k, k)
     if col not in header: continue
     value = (raw.get(col) or '').strip()
    if isinstance(value, str) and value in maps.get(k, {}): value = maps[k][value]
    try: rec[k] = convert(k, value, seps.get(k), role)
    except TableError as e: problems.append({'check': 'TABLE', 'where': where, 'message': f'{k}：{e}'})
   out.append(rec)
 return out


def convert(key, value, sep, role):
 if not isinstance(value, str): return value
 if key in NUMBERS:
  if value == '': return None
  text = value.replace(',', '')
  try: return float(text[:-1]) / 100 if text.endswith('%') else float(text)
  except ValueError: raise TableError(f'不是数字：{value}') from None
 if key in BOOLEANS:
  if value in TRUE: return True
  if value in FALSE: return False
  raise TableError(f'是否列只能写 是/否、true/false、1/0、Y/N：{value}')
 if key in LISTS or sep:
  return [x.strip() for x in value.split(sep or ';') if x.strip()]
 return value


def opt(rec, key):
 v = rec.get(key)
 return None if v in ('', None, []) else v


def build_view(mapping, base, base_view):
 problems, files = [], []
 comp_map = copy.deepcopy(mapping.get('comparison', {}))
 dims = [d['id'] for d in comp_map.get('dimensions', []) if isinstance(d, dict) and d.get('id')]
 tables = mapping.get('tables', {})
 unknown = sorted(set(tables) - set(ROLES) - {v['table'] for d in comp_map.get('dimensions', []) for v in [d.get('values_from')] if isinstance(v, dict)})
 if unknown: problems.append({'check': 'TABLE', 'where': 'tables', 'message': '不认识的表：' + '、'.join(unknown)})
 data = {role: [r for spec in tables.get(role, []) for r in read_table(spec, role, base, dims, problems, files)] for role in ROLES}
 dimset = lambda rec: {d: rec['dim.' + d] for d in dims if opt(rec, 'dim.' + d) is not None}

 # Items: one row per item x source segment; item-level values must agree across rows.
 items, order = {}, []
 for rec in data['items']:
  iid = opt(rec, 'id')
  if iid is None: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '缺少条目编号 id'}); continue
  if iid not in items:
   order.append(iid)
   levels = {k: rec.get(k) for k in LEVELS}
   ours = {'unit': opt(rec, 'unit'), 'levels': levels}
   for k, dest in (('quantity_name', 'quantity_name'), ('ours_note', 'note'), ('total', 'total')):
    if opt(rec, k) is not None: ours[dest] = rec[k]
   it = {'id': iid, 'kind': opt(rec, 'kind') or 'third_party', 'ours': ours, 'third_parties': [], 'dimensions': dimset(rec), 'detail_rows': [], '_first': rec}
   for k in ('name', 'features'):
    if opt(rec, k) is not None: it[k] = rec[k]
   if 'estimate_dominant' in rec and rec['estimate_dominant'] is not None: it['estimate_dominant'] = rec['estimate_dominant']
   items[iid] = it
  else:
   first = items[iid]['_first']
   diff = [k for k in (*ITEM_LEVEL_KEYS, *('dim.' + d for d in dims)) if first.get(k) != rec.get(k)]
   if diff: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': f'条目 {iid} 的条目级列与前一行不同：' + '、'.join(diff)})
  it = items[iid]
  if it['kind'] == 'extra': continue
  sid = opt(rec, 'source_id')
  if sid is None: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '第三方条目缺少 source_id'}); continue
  rows = []
  if opt(rec, 'row_id') is not None:
   rows.append({'row_id': rec['row_id'], 'seq': opt(rec, 'seq'), 'code': opt(rec, 'code'), 'name': opt(rec, 'row_name') or opt(rec, 'name'),
                'features': opt(rec, 'row_features') or opt(rec, 'features'), 'unit': opt(rec, 'row_unit') or opt(rec, 'unit'), 'quantity': rec.get('row_quantity')})
  unit = opt(rec, 'tp_unit') or (rows[0]['unit'] if rows else None) or opt(rec, 'unit')
  qty = rec.get('tp_quantity') if 'tp_quantity' in rec and rec.get('tp_quantity') is not None else (rows[0]['quantity'] if len(rows) == 1 else None)
  seg = {'source_id': sid, 'rows': rows, 'unit': unit, 'quantity': qty, '_tp_given': rec.get('tp_quantity') is not None, '_where': rec['_where']}
  value, rate = rec.get('difference'), rec.get('difference_rate')
  seg['difference'] = None if value is None and rate is None else {'value': value, 'rate': rate}
  for k in ('conclusion', 'disposition_note', 'correction_note', 'unit_mismatch_note', 'quantity_note', 'missing_drawing_note'):
   if opt(rec, k) is not None: seg[k] = rec[k]
  if rec.get('third_party_corrected') is not None: seg['third_party_corrected'] = rec['third_party_corrected']
  ev = {dest: rec[src] for src, dest in (('search_drawings', 'drawings'), ('search_targets', 'targets'), ('search_method', 'method'), ('search_hits', 'hits'),
                                         ('search_unread', 'unread'), ('search_uncovered', 'uncovered'), ('search_record_ref', 'record_ref'), ('search_note', 'note')) if opt(rec, src) is not None}
  if ev: seg['search_evidence'] = ev
  seg['redirect_to'] = list(rec.get('redirect_to') or [])
  it['third_parties'].append(seg)

 def segment_of(iid, sid, where):
  it = items.get(iid)
  if it is None: problems.append({'check': 'TABLE', 'where': where, 'message': '条目不存在：' + str(iid)}); return None
  hits = [s for s in it['third_parties'] if s['source_id'] == sid] if sid else it['third_parties']
  if len(hits) != 1: problems.append({'check': 'TABLE', 'where': where, 'message': f'条目 {iid} 找不到唯一的来源段{("（" + sid + "）") if sid else "；多来源时须写 source_id"}'}); return None
  return hits[0]
 for rec in data['source_rows']:
  seg = segment_of(opt(rec, 'item_id'), opt(rec, 'source_id'), rec['_where'])
  if seg is None: continue
  if seg.get('_inline_checked') is None:
   seg['_inline_checked'] = True
   if seg['rows']: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '该来源段在对照条目表里已写了单行来源列，来源行表与它只能用一种'}); continue
  seg['rows'].append({'row_id': rec.get('row_id'), 'seq': opt(rec, 'seq'), 'code': opt(rec, 'code'), 'name': opt(rec, 'row_name'), 'features': opt(rec, 'row_features'),
                      'unit': opt(rec, 'row_unit'), 'quantity': rec.get('row_quantity')})
 for it in items.values():
  for seg in it['third_parties']:
   if len(seg['rows']) > 1 and not seg.pop('_tp_given'): problems.append({'check': 'TABLE', 'where': seg['_where'], 'message': f"条目 {it['id']} 来源段有多行来源行，须在 tp_quantity 写该段数量"})
   if len(seg['rows']) == 1 and seg['quantity'] is None and not seg.get('_tp_given'): seg['quantity'] = seg['rows'][0]['quantity']
   for k in ('_tp_given', '_where', '_inline_checked'): seg.pop(k, None)
   seg['rows'] = [{k: v for k, v in r.items() if v is not None or k == 'quantity'} for r in seg['rows']]
 for rec in data['redirects']:
  seg = segment_of(opt(rec, 'item_id'), opt(rec, 'source_id'), rec['_where'])
  if seg is not None and opt(rec, 'target_item_id') is not None and rec['target_item_id'] not in seg['redirect_to']: seg['redirect_to'].append(rec['target_item_id'])

 # Detail rows.
 details = {}
 for rec in data['detail_rows']:
  it = items.get(opt(rec, 'item_id'))
  if it is None: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '明细挂接的条目不存在：' + str(rec.get('item_id'))}); continue
  row = {'id': opt(rec, 'id'), 'level': opt(rec, 'level'), 'quantity': rec.get('quantity'), 'unit': opt(rec, 'unit'), 'dimensions': dimset(rec), 'member_keys': list(rec.get('member_keys') or [])}
  for k in ('basis', 'area', 'drawing', 'locator', 'relation', 'note', 'aggregation', 'aggregation_reason', 'member_quantity_name', 'member_table'):
   if opt(rec, k) is not None: row[k] = rec[k]
  it['detail_rows'].append(row); details[row['id']] = row

 # Member ledger rows become unlocated objects unless the base view already has that object.
 objects = copy.deepcopy(base_view.get('objects', [])) if base_view else []
 known = {o['catalog_key'] for o in objects}
 member_specs = tables.get('members', [])
 for rec in data['members']:
  key = opt(rec, 'catalog_key') or (('成员|' + rec['object_id']) if opt(rec, 'object_id') else None)
  if key is None: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '成员缺少 catalog_key 或 object_id'}); continue
  if key not in known:
   spec = next(s for s in member_specs if rec['_where'].startswith(s['path'] + ' '))
   quantities = []
   for q in spec.get('quantities', []):
    try: value = convert('quantity', (rec['_raw'].get(q['column']) or '').strip(), None, 'members')
    except TableError as e: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': f"{q['name']}：{e}"}); value = None
    quantities.append({'name': q['name'], 'value': value, 'unit': q['unit'], 'level': q['level']})
   objects.append({'id': opt(rec, 'object_id') or key, 'catalog_key': key, 'professional': opt(rec, 'professional'), 'kind': 'unlocated', 'scene_id': None, 'points': [],
                   'title': opt(rec, 'title') or opt(rec, 'object_id') or key, 'category': opt(rec, 'category') or '成员台账', 'status': opt(rec, 'status') or '台账行',
                   'detail': opt(rec, 'detail') or '成员台账行', 'source': opt(rec, 'source') or rec['_where'], 'quantities': quantities,
                   'attributes': {a: (rec['_raw'].get(col) or '') for a, col in spec.get('attributes', {}).items()}, **({'dimensions': dimset(rec)} if dimset(rec) else {})})
   known.add(key)
  did = opt(rec, 'detail_id')
  if did is not None:
   if did not in details: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '成员挂接的明细行不存在：' + did})
   elif key not in details[did]['member_keys']: details[did]['member_keys'].append(key)

 # Issues.
 issues, iorder = {}, []
 for rec in data['issues']:
  qid = opt(rec, 'id')
  if qid is None or qid in issues: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '问题编号缺失或重复'}); continue
  q = {'id': qid, 'handler': opt(rec, 'handler'), 'title': opt(rec, 'title'), 'text': opt(rec, 'text'), 'item_ids': list(rec.get('item_ids') or []),
       'dimensions': {d: rec['dim.' + d] for d in dims if opt(rec, 'dim.' + d) is not None}, 'source_refs': list(rec.get('source_refs') or [])}
  for k in ('impact_text', 'current_practice', 'status', 'group_id'):
   if opt(rec, k) is not None: q[k] = rec[k]
  issues[qid] = q; iorder.append(qid)
 for rec in data['issue_links']:
  q = issues.get(opt(rec, 'issue_id'))
  if q is None: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '关联的问题不存在：' + str(rec.get('issue_id'))})
  elif opt(rec, 'item_id') is not None and rec['item_id'] not in q['item_ids']: q['item_ids'].append(rec['item_id'])
 for rec in data['issue_sources']:
  q = issues.get(opt(rec, 'issue_id'))
  if q is None: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '原条目对应的问题不存在：' + str(rec.get('issue_id'))})
  elif opt(rec, 'ref') is not None: q['source_refs'].append({'ref': rec['ref'], 'text': rec.get('text') or ''})
 for rec in data['issue_impacts']:
  q = issues.get(opt(rec, 'issue_id'))
  if q is None: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '影响对应的问题不存在：' + str(rec.get('issue_id'))}); continue
  part = {'value': rec.get('value')}
  for k in ('unit', 'note'):
   if opt(rec, k) is not None: part[k] = rec[k]
  q.setdefault('impact', []).append(part)

 # Dimension values: explicit, from data, or from a lookup table.
 for d in comp_map.get('dimensions', []):
  src = d.pop('values_from', None)
  if src is None: continue
  values = {}
  if src == 'data':
   pools = [*(i['dimensions'] for i in items.values()), *(r['dimensions'] for i in items.values() for r in i['detail_rows']), *(q['dimensions'] for q in issues.values()), *(o.get('dimensions', {}) for o in objects)]
   for dd in pools:
    v = dd.get(d['id'])
    for x in (v if isinstance(v, list) else [v]):
     if x is not None: values.setdefault(x, {'id': x, 'title': x})
  elif isinstance(src, dict):
   for spec in tables.get(src['table'], []):
    path = (base / spec['path']).resolve(); files.append({'role': 'dimension:' + d['id'], 'path': spec['path'], 'sha256': sha_file(path)})
    with open(path, encoding='utf-8-sig', newline='') as f:
     for raw in csv.DictReader(f):
      vid = (raw.get(src['id']) or '').strip()
      if not vid: continue
      v = values.setdefault(vid, {'id': vid, 'title': (raw.get(src.get('title', src['id'])) or vid).strip()})
      if src.get('order') and (raw.get(src['order']) or '').strip(): v['order'] = float(raw[src['order']])
      if src.get('merged_from'):
       m = (raw.get(src['merged_from']) or '').strip()
       if m and m != vid and m not in v.setdefault('merged_from', []): v['merged_from'].append(m)
  else: problems.append({'check': 'TABLE', 'where': 'dimensions.' + d['id'], 'message': 'values_from 只能是 "data" 或 {table, id, …}'})
  d['values'] = [*d.get('values', []), *(v for k, v in values.items() if k not in {x['id'] for x in d.get('values', [])})]

 # Sources: row ids from an independent table, or (weaker) from the items themselves.
 for s in comp_map.get('sources', []):
  origin = s.pop('row_ids_from', None); table = s.pop('row_ids_table', None)
  if table:
   path = (base / table['path']).resolve(); files.append({'role': 'row_ids:' + s['id'], 'path': table['path'], 'sha256': sha_file(path)})
   with open(path, encoding='utf-8-sig', newline='') as f:
    s['row_ids'] = [(r.get(table.get('column', 'row_id')) or '').strip() for r in csv.DictReader(f) if (r.get(table.get('column', 'row_id')) or '').strip()]
   s['row_ids_origin'] = 'independent'
  elif origin == 'items':
   s['row_ids'] = [r['row_id'] for i in order for seg in items[i]['third_parties'] if seg['source_id'] == s['id'] for r in seg['rows']]
   s['row_ids_origin'] = 'items'
  elif origin is not None: problems.append({'check': 'TABLE', 'where': 'sources.' + s['id'], 'message': 'row_ids_from 只能是 "items"'})

 if problems: raise ComparisonError(problems)
 for it in items.values(): it.pop('_first', None)
 v = mapping['view']
 view = {'schema': VIEW_SCHEMA_COMPARISON, **{k: v[k] for k in ('project_id', 'review_id', 'title', 'revision') if k in v},
         'sources': copy.deepcopy(v.get('sources', [])), 'scenes': [], 'objects': objects, 'groups': [], 'receipts': {}, 'summary': copy.deepcopy(v.get('summary', [])),
         'scope_notes': copy.deepcopy(v.get('scope_notes', []))}
 if base_view:
  for k in ('scenes', 'groups', 'background_sets', 'receipts', 'default_category'):
   if k in base_view: view[k] = copy.deepcopy(base_view[k])
  have = {s['id']: s for s in view['sources']}
  for s in base_view.get('sources', []):
   if s['id'] in have and have[s['id']]['sha256'].lower() != s['sha256'].lower(): raise ValueError('基础视图与映射的来源编号相同但哈希不同：' + s['id'])
   have.setdefault(s['id'], s)
  view['sources'] = list(have.values())
 comp_map['items'] = [items[i] for i in order]
 comp_map['issues'] = [issues[q] for q in iorder]
 view['comparison'] = comp_map
 return view, {'tables': files, 'rows_read': {r: len(data[r]) for r in ROLES if data[r]}}


def main():
 p = argparse.ArgumentParser(); p.add_argument('--mapping', required=True, type=Path); p.add_argument('--output', required=True, type=Path)
 p.add_argument('--base-view', type=Path); p.add_argument('--report', type=Path); a = p.parse_args()
 report = {'status': 'INPUT_REQUIRED', 'quantity_changes': 0}
 try:
  if a.output.exists(): raise ValueError('输出视图已存在，请用新的版本名')
  if a.report and a.report.resolve() in {x.resolve() for x in (a.mapping, a.output, a.base_view) if x}: raise ValueError('检查报告路径不能覆盖映射、视图或基础视图')
  mapping = json.loads(a.mapping.read_text(encoding='utf-8-sig'))
  if mapping.get('schema') != MAPPING_SCHEMA: raise ValueError('映射文件 schema 须为 ' + MAPPING_SCHEMA)
  if not isinstance(mapping.get('view'), dict) or not isinstance(mapping.get('tables'), dict): raise ValueError('映射文件须有 view 和 tables')
  base_view = json.loads(a.base_view.read_text(encoding='utf-8-sig')) if a.base_view else None
  view, info = build_view(mapping, a.mapping.resolve().parent, base_view)
  normalized = normalize_view(view, a.base_view.resolve().parent if a.base_view else None)
  a.output.parent.mkdir(parents=True, exist_ok=True)
  with a.output.open('x', encoding='utf-8', newline='\n') as f: json.dump(view, f, ensure_ascii=False, indent=1)
  check = normalized['comparison']['generation_check']
  report.update(status='PASS', output=str(a.output), mapping_sha256=sha_file(a.mapping), **info, comparison_counts=check['counts'], comparison_exceptions=check['exceptions'],
                result_fingerprint=normalized['result_fingerprint'])
  code = 0
 except (OSError, ValueError, KeyError, TypeError) as e:
  report['error'] = str(e)
  if hasattr(e, 'problems'): report['problems'] = e.problems
  code = 2
 if a.report:
  a.report.parent.mkdir(parents=True, exist_ok=True); a.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
 summary = {k: report[k] for k in ('status', 'output', 'comparison_counts', 'error') if k in report}
 if 'problems' in report: summary['problem_count'] = len(report['problems'])
 print(json.dumps(summary, ensure_ascii=False)); return code


if __name__ == '__main__': raise SystemExit(main())
