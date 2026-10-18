"""Build a qto-review-view/1.1 comparison view from professional CSV tables plus a column mapping.

Only moves and renames values (contract docs/维护/复核页审核视图契约.md, section 12); it never
computes an engineering quantity. The result is checked by the shared contract before it is written.
"""
import argparse, copy, csv, hashlib, json, math, os, re
from collections import Counter, defaultdict
from pathlib import Path
from qto_review_contract import normalize_view, VIEW_SCHEMA_COMPARISON
from qto_cross_drawing_registration import canonical_context_key
from qto_review_comparison import ComparisonError, LEVELS, close, COMPARABILITY_KEYS
COMPARISON_FIELDS = tuple('compare_' + k for k in COMPARABILITY_KEYS)

MAPPING_SCHEMA = 'qto-comparison-table-mapping/1.0'
TRUE, FALSE = {'是', 'true', 'True', 'TRUE', '1', 'Y', 'y'}, {'', '否', 'false', 'False', 'FALSE', '0', 'N', 'n'}
ROLES = ('items', 'source_rows', 'detail_rows', 'redirects', 'issues', 'issue_links', 'issue_sources', 'issue_impacts', 'members',
         'common_issues', 'issue_options', 'common_issue_links', 'member_links', 'member_refs', 'detail_aggregation')
EXCLUDED_KEYS = ('excluded_rule_ref', 'excluded_issue_id', 'excluded_note')
AGG_KEYS = ('aggregation', 'aggregation_reason', 'member_quantity_name', 'member_table')   # member_table names the ledger whose title the drill-down shows
ITEM_KEYS = ('id', 'kind', 'source_id', 'name', 'features', 'unit', 'quantity_name', 'ours_note', *LEVELS, 'total', 'estimate_dominant',
             'row_id', 'seq', 'code', 'row_name', 'row_features', 'row_unit', 'row_quantity', 'tp_quantity', 'tp_unit', 'difference', 'difference_rate',
             'conclusion', 'disposition_note', 'third_party_corrected', 'correction_note', 'unit_mismatch_note', 'quantity_note', 'missing_drawing_note',
             'search_drawings', 'search_targets', 'search_method', 'search_hits', 'search_unread', 'search_uncovered', 'search_record_ref', 'search_note', 'redirect_to',
             *COMPARISON_FIELDS, 'comparison_reason', 'scope_basis')
ITEM_LEVEL_KEYS = ('kind', 'name', 'features', 'unit', 'quantity_name', 'ours_note', *LEVELS, 'total', 'estimate_dominant')
KEYS = {'items': ITEM_KEYS,
        'source_rows': ('item_id', 'source_id', 'row_id', 'seq', 'code', 'row_name', 'row_features', 'row_unit', 'row_quantity'),
        'detail_rows': ('id', 'item_id', 'level', 'quantity', 'unit', 'basis', 'area', 'drawing', 'locator', 'relation', 'note', 'aggregation',
                        'aggregation_reason', 'member_keys', 'member_quantity_name', 'member_table', 'excluded', *EXCLUDED_KEYS),
        'redirects': ('item_id', 'target_item_id', 'source_id'),
        'issues': ('id', 'handler', 'nature', 'common_issue_id', 'rule_ref', 'plugin_issue_ref', 'title', 'text', 'impact_text', 'current_practice',
                   'current_option', 'source_refs', 'status', 'group_id', 'item_ids'),
        'issue_links': ('issue_id', 'item_id'), 'issue_sources': ('issue_id', 'ref', 'text'), 'issue_impacts': ('issue_id', 'value', 'unit', 'note'),
        'members': ('catalog_key', 'object_id', 'detail_id', 'title', 'category', 'status', 'professional', 'detail', 'source'),
        'common_issues': ('id', 'nature', 'question', 'text', 'handler', 'current_option', 'rule_ref', 'status', 'item_ids'),
        'issue_options': ('issue_id', 'option_id', 'text', 'value', 'unit', 'note'),
        'common_issue_links': ('common_issue_id', 'item_id'),
        'member_links': ('catalog_key', 'detail_id', 'quantity_name', 'level', 'quantity'),
        'member_refs': ('catalog_key', 'detail_id'),
        'detail_aggregation': ('detail_id', *AGG_KEYS)}
NUMBERS = {*LEVELS, 'total', 'row_quantity', 'tp_quantity', 'difference', 'difference_rate', 'search_hits', 'quantity', 'value'}
BOOLEANS = {'estimate_dominant', 'third_party_corrected', 'excluded', *COMPARISON_FIELDS}
LISTS = {'redirect_to', 'member_keys', 'source_refs', 'item_ids'}


class TableError(ValueError):
 pass


def sha_file(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()


SPEC_FIELDS = {'path', 'columns', 'constants', 'value_maps', 'list_separators'}


def mapping_warnings(mapping):
 """Keys the mapping writes but the conversion does not read: listed as reminders in the report instead of being dropped silently."""
 dims = {d['id'] for d in mapping.get('comparison', {}).get('dimensions', []) if isinstance(d, dict) and d.get('id')}
 out = []
 for role, specs in mapping.get('tables', {}).items():
  if role not in ROLES or not isinstance(specs, list): continue
  allowed = {*KEYS.get(role, ()), *('dim.' + d for d in dims)}
  for n, spec in enumerate(specs):
   if not isinstance(spec, dict): continue
   where = f"tables.{role}[{n}]（{spec.get('path', '')}）"
   extra = sorted(set(spec) - SPEC_FIELDS - ({'quantities', 'attributes'} if role == 'members' else set()))
   if extra: out.append({'where': where, 'keys': extra, 'message': '不认识的映射字段，没有使用：' + '、'.join(extra)})
   for part in ('columns', 'constants', 'value_maps', 'list_separators'):
    keys = sorted(k for k in (spec.get(part) or {}) if k not in allowed)
    if keys: out.append({'where': f'{where}.{part}', 'keys': keys, 'message': '这张表不认识这些标准键，没有读取：' + '、'.join(keys) + '（维度键须先在 comparison.dimensions 里声明）'})
 return out


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
  if key in COMPARISON_FIELDS and value == '': return None
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
  if any(rec.get(k) is not None for k in COMPARISON_FIELDS):
   seg['comparability'] = {k:rec.get('compare_'+k) for k in COMPARABILITY_KEYS}
   if opt(rec,'comparison_reason') is not None: seg['comparability']['reason'] = rec['comparison_reason']
  for k in ('conclusion', 'disposition_note', 'correction_note', 'unit_mismatch_note', 'quantity_note', 'missing_drawing_note', 'scope_basis'):
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
  # Decided not counted (contract 5.1, 12.2): the flag column plus three basis columns form one object.
  if rec.get('excluded'): row['excluded'] = {k[9:]: rec[k] for k in EXCLUDED_KEYS if opt(rec, k) is not None}
  elif any(opt(rec, k) is not None for k in EXCLUDED_KEYS): problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': 'excluded 为假或空时不能写 excluded_rule_ref、excluded_issue_id、excluded_note'})
  it['detail_rows'].append(row); details[row['id']] = row
 for rec in data['detail_aggregation']:
  row = details.get(opt(rec, 'detail_id'))
  if row is None: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '汇总方式对应的明细行不存在：' + str(rec.get('detail_id'))}); continue
  for k in AGG_KEYS:
   if opt(rec, k) is None: continue
   if k in row and row[k] != rec[k]: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': f'明细 {row["id"]} 的 {k} 在明细表里是“{row[k]}”，汇总方式表写“{rec[k]}”'})
   else: row[k] = rec[k]

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
 # Member links (contract 12.2): "this quantity of this member feeds this detail row"; level and quantity come from the link.
 bykey = {o['catalog_key']: o for o in objects}
 for rec in data['member_links']:
  key, did, name, level = (opt(rec, k) for k in ('catalog_key', 'detail_id', 'quantity_name', 'level'))
  o, row = bykey.get(key), details.get(did)
  if o is None or row is None or name is None or level is None:
   problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '挂接须有已存在的成员 catalog_key、明细行 detail_id、quantity_name 和 level' + (f'（成员 {key} 不存在）' if key and o is None else f'（明细 {did} 不存在）' if did and row is None else '')}); continue
  hit = [q for q in o.setdefault('quantities', []) if q.get('name') == name and q.get('level') == level]
  if not hit: o['quantities'].append({'name': name, 'value': rec.get('quantity'), 'unit': row.get('unit'), 'level': level})
  elif not close(hit[0].get('value'), rec.get('quantity')): problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': f'成员 {key} 已有数量项 {name}（{level}）为 {hit[0].get("value")}，挂接表写 {rec.get("quantity")}，不猜'}); continue
  if key not in row['member_keys']: row['member_keys'].append(key)
 # Member refs (scene mapping contract 10, user 2026-10-16): routes and trays are only located, never counted, so the row must be declared.
 for rec in data['member_refs']:
  key, did = opt(rec, 'catalog_key'), opt(rec, 'detail_id'); row = details.get(did)
  if key not in bykey or row is None:
   problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '定位挂接须有已存在的对象 catalog_key 和明细行 detail_id' + (f'（对象 {key} 不存在）' if key and key not in bykey else f'（明细 {did} 不存在）' if did else '')}); continue
  if key not in row['member_keys']: row['member_keys'].append(key)
  refs = row.setdefault('locate_only_keys', [])
  if key not in refs: refs.append(key)

 # Issues.
 issues, iorder = {}, []
 for rec in data['issues']:
  qid = opt(rec, 'id')
  if qid is None or qid in issues: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '问题编号缺失或重复'}); continue
  q = {'id': qid, 'handler': opt(rec, 'handler'), 'title': opt(rec, 'title'), 'text': opt(rec, 'text'), 'item_ids': list(rec.get('item_ids') or []),
       'dimensions': {d: rec['dim.' + d] for d in dims if opt(rec, 'dim.' + d) is not None}, 'source_refs': list(rec.get('source_refs') or [])}
  for k in ('impact_text', 'current_practice', 'status', 'group_id', 'nature', 'common_issue_id', 'rule_ref', 'plugin_issue_ref', 'current_option'):
   if opt(rec, k) is not None: q[k] = rec[k]
  issues[qid] = q; iorder.append(qid)
 # Common issues (contract 6.2, 12.2): one row each; options and direct item links come from their own tables.
 commons, corder = {}, []
 for rec in data['common_issues']:
  gid = opt(rec, 'id')
  if gid is None or gid in commons or gid in issues: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '共性问题编号缺失、重复或与个别问题重号'}); continue
  g = {'id': gid, 'item_ids': list(rec.get('item_ids') or []), 'dimensions': {d: rec['dim.' + d] for d in dims if opt(rec, 'dim.' + d) is not None}}
  for k in ('nature', 'question', 'text', 'handler', 'current_option', 'rule_ref', 'status'):
   if opt(rec, k) is not None: g[k] = rec[k]
  commons[gid] = g; corder.append(gid)
 for rec in data['common_issue_links']:
  g = commons.get(opt(rec, 'common_issue_id'))
  if g is None: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '挂接的共性问题不存在：' + str(rec.get('common_issue_id'))})
  elif opt(rec, 'item_id') is not None and rec['item_id'] not in g['item_ids']: g['item_ids'].append(rec['item_id'])
 for rec in data['issue_options']:
  owner = issues.get(opt(rec, 'issue_id')) or commons.get(opt(rec, 'issue_id'))
  if owner is None or opt(rec, 'option_id') is None or opt(rec, 'text') is None: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': '做法须有已存在的问题编号（个别或共性问题）、option_id 和 text'}); continue
  options = owner.setdefault('options', []); o = next((x for x in options if x['id'] == rec['option_id']), None)
  if o is None: o = {'id': rec['option_id'], 'text': rec['text'], 'impact': None}; options.append(o)
  elif o['text'] != rec['text']: problems.append({'check': 'TABLE', 'where': rec['_where'], 'message': f"做法 {o['id']} 各行的 text 须相同"}); continue
  # One row is one impact segment; a row with no value and no note only declares the option.
  if rec.get('value') is not None or opt(rec, 'note') is not None:
   part = {'value': rec.get('value'), **{k: rec[k] for k in ('unit', 'note') if opt(rec, k) is not None}}
   o['impact'] = [*(o['impact'] or []), part]
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
   pools = [*(i['dimensions'] for i in items.values()), *(r['dimensions'] for i in items.values() for r in i['detail_rows']), *(q['dimensions'] for q in issues.values()),
            *(g['dimensions'] for g in commons.values()), *(o.get('dimensions', {}) for o in objects)]
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
  # Contract 12.1: {"path", "column"} is the standard form; a bare path string means column row_id.
  if isinstance(table, str) and table.strip(): table = {'path': table}
  elif table is not None and not (isinstance(table, dict) and isinstance(table.get('path'), str) and table['path'].strip() and isinstance(table.get('column', 'row_id'), str)):
   problems.append({'check': 'TABLE', 'where': 'sources.' + str(s.get('id')), 'message': 'row_ids_table 写成 {"path": "表路径", "column": "row_id"}（column 可省），或直接写表路径文字'}); table = None
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
  for k in ('scenes', 'groups', 'background_sets', 'receipts', 'default_category', 'review_workflow'):
   if k in base_view: view[k] = copy.deepcopy(base_view[k])
  have = {s['id']: s for s in view['sources']}
  for s in base_view.get('sources', []):
   if s['id'] in have and have[s['id']]['sha256'].lower() != s['sha256'].lower(): raise ValueError('基础视图与映射的来源编号相同但哈希不同：' + s['id'])
   have.setdefault(s['id'], s)
  view['sources'] = list(have.values())
 if 'review_workflow' in v: view['review_workflow'] = copy.deepcopy(v['review_workflow'])
 comp_map['items'] = [items[i] for i in order]
 comp_map['issues'] = [issues[q] for q in iorder]
 if commons: comp_map['common_issues'] = [commons[g] for g in corder]
 view['comparison'] = comp_map
 info = {'tables': files, 'rows_read': {r: len(data[r]) for r in ROLES if data[r]}}
 if data['member_refs']:
  # Scene mapping contract 10: how many objects the locate-only links placed, and which declared rows still have none.
  info['member_refs'] = {'objects': len({k for r in details.values() for k in r.get('locate_only_keys', [])}),
                         'rows': [r['id'] for r in details.values() if r.get('locate_only_keys')],
                         'declared_rows_without_members': [r['id'] for r in details.values() if r.get('aggregation') == 'declared' and not r['member_keys']]}
 return view, info


SCENE_SAME = ('title', 'source_id', 'coordinate_unit', 'y_axis', 'bounds', 'background', 'background_refs', 'raster_path', 'raster_data_uri')


def load_scene_reports(paths):
 """Scene conversion reports (build_review_scenes_from_tables --report): background key -> (report name, background filters)."""
 out = {}
 for path in paths:
  rep = json.loads(path.read_text(encoding='utf-8-sig'))
  if not isinstance(rep.get('scenes'), list): raise ValueError('图面转换检查报告没有 scenes：' + path.name)
  for s in rep['scenes']:
   if isinstance(s, dict) and s.get('background_key'): out.setdefault(s['background_key'], (path.name, s.get('background_filters') or []))
 return out


def _short(v):
 s = json.dumps(v, ensure_ascii=False)
 return s if len(s) <= 80 else s[:77] + '…'


def filter_difference(fa, fb, va, vb):
 """Background filters of two copies of one sheet, part by part: list keys as set differences, other keys as both values."""
 if len(fa) != len(fb): return [f'底图分 {len(fa)} 部分与 {len(fb)} 部分']
 out = []
 for n, (a, b) in enumerate(zip(fa, fb), 1):
  part = f'第 {n} 部分（图页 {a.get("sheet")}）' if len(fa) > 1 else ''
  for k in sorted(set(a) | set(b)):
   x, y = a.get(k), b.get(k)
   if x == y: continue
   if isinstance(x, list) and isinstance(y, list) and all(isinstance(i, str) for i in x + y):
    only_a, only_b = [i for i in x if i not in y], [i for i in y if i not in x]
    out.append(f'{part}{k} ' + '，'.join([*([f'只在 {va}：{"、".join(only_a)}'] if only_a else []), *([f'只在 {vb}：{"、".join(only_b)}'] if only_b else [])] or ['顺序不同']))
   else: out.append(f'{part}{k} {_short(x)} 与 {_short(y)}')
 return out


def scene_difference(a, b, va, vb, filters):
 """Why two copies of one scene id differ: the differing fields, then details (values, background filter set differences when the scene reports give them)."""
 fields = [k for k in SCENE_SAME if a.get(k) != b.get(k)]
 details = []
 for k in fields:
  if k == 'background': details.append(f'background 图元 {len(a.get(k) or [])} 与 {len(b.get(k) or [])} 个')
  elif k == 'raster_data_uri': details.append('raster_data_uri 图像不同')
  elif k != 'background_refs': details.append(f'{k} {_short(a.get(k))} 与 {_short(b.get(k))}')
 if 'background_refs' in fields:
  ra, rb = a.get('background_refs') or [], b.get('background_refs') or []
  missing = [r for r in ra + rb if r not in (filters or {})]
  if not filters: details.append('底图过滤条件未知：加 --scene-report 给出两包的图面转换检查报告，可列出过滤条件的差集')
  elif missing: details.append('底图键 ' + '、'.join(missing) + ' 在给出的图面转换检查报告里找不到')
  else:
   fd = filter_difference([p for r in ra for p in filters[r][1]], [p for r in rb for p in filters[r][1]], va, vb)
   details.append('底图过滤条件：' + '；'.join(fd) if fd else '底图过滤条件相同，底图键不同来自图纸或交换 DXF 的哈希（两包用的图纸文件不是同一份）')
 return fields, details


def check_shared_scenes(paths, filters):
 """Read-only check before a merge: every scene id used by two or more base views, each later copy compared with the first."""
 first, rows = {}, []
 for path in paths:
  base = json.loads(path.read_text(encoding='utf-8-sig'))
  for s in base.get('scenes', []):
   # Raster paths are relative to each view file; compare the files they point at, as the merge does.
   if s.get('raster_path'): s = {**s, 'raster_path': str((path.resolve().parent / s['raster_path']).resolve())}
   if s['id'] not in first: first[s['id']] = (path.name, s); continue
   va, a = first[s['id']]
   fields, details = scene_difference(a, s, va, path.name, filters)
   rows.append({'scene': s['id'], 'views': [va, path.name], 'same': not fields, **({'fields': fields, 'details': details} if fields else {})})
 return rows


def merge_base_views(paths, out_dir, filters=None):
 """Several existing views (contract 12.1): scenes, objects and groups are only moved; any id clash stops the merge.
 Raster paths are rewritten relative to the output view so the merged view still finds its images."""
 merged = {'scenes': [], 'objects': [], 'groups': [], 'background_sets': {}, 'receipts': {}, 'sources': [], 'renamed_groups': [], 'base_views': [], 'merged_scenes': []}
 seen = {'scenes': {}, 'objects': {}, 'groups': {}}; src = {}
 for path in paths:
  base = json.loads(path.read_text(encoding='utf-8-sig')); name = path.name; renamed_for_base = {}
  merged['base_views'].append({'view': name, **{f: len(base.get(f, [])) for f in ('scenes', 'objects', 'groups')}})
  for field, ident, label in (('scenes', 'id', '图页编号'), ('objects', 'catalog_key', '对象 catalog_key'), ('groups', 'id', '问题组编号')):
   for x in base.get(field, []):
    x = copy.deepcopy(x)
    # Each package numbers its own problem groups from G01, so a clashing group of a later view is renamed "<file stem>|<id>", content unchanged.
    if field == 'groups' and x[ident] in seen[field]:
     new = path.stem + '|' + x[ident]
     if new in seen[field]: raise ValueError(f'几份基础视图的{label}相同，改名后仍重复：{new}')
     merged['renamed_groups'].append({'view': name, 'from': x[ident], 'to': new}); renamed_for_base[x[ident]] = new; x[ident] = new
    if field == 'scenes' and x.get('raster_path'):
     target = (path.resolve().parent / x['raster_path']).resolve()
     try: x['raster_path'] = os.path.relpath(target, out_dir)
     except ValueError: x['raster_path'] = str(target)
    # Packages that use the same sheet emit the same scene (scene mapping contract 10): identical ones become one page.
    if field == 'scenes' and x[ident] in seen[field]:
     first = next(s for s in merged['scenes'] if s['id'] == x[ident])
     diff, details = scene_difference(first, x, seen[field][x[ident]], name, filters)
     if not diff:
      merged['merged_scenes'].append({'scene': x[ident], 'views': [seen[field][x[ident]], name]}); continue
     raise ValueError(f'几份基础视图的图页编号相同而内容不同：{x[ident]}（{seen[field][x[ident]]} 与 {name}，不同的是 {"、".join(diff)}）。' + '；'.join(details))
    if x[ident] in seen[field]: raise ValueError(f'几份基础视图的{label}相同：{x[ident]}（{seen[field][x[ident]]} 与 {name}）')
    seen[field][x[ident]] = name
    merged[field].append(x)
  for field in ('background_sets', 'receipts'):
   for k, v in base.get(field, {}).items():
    if k in merged[field] and merged[field][k] != v: raise ValueError(f'几份基础视图的 {field} 键相同而内容不同：{k}（{name}）')
    merged[field][k] = copy.deepcopy(v)
  if 'review_workflow' in base:
   workflow = copy.deepcopy(base['review_workflow'])
   for matter in workflow.get('decisions', []):
    for ref in matter.get('refs', []):
     if ref.get('kind') == 'group' and ref.get('id') in renamed_for_base: ref['id'] = renamed_for_base[ref['id']]
   if 'review_workflow' in merged and merged['review_workflow'] != workflow: raise ValueError('几份基础视图的审核事项工作流不同，须由项目显式统一稳定身份及范围，不自动合并')
   merged['review_workflow'] = workflow
  for s in base.get('sources', []):
   if s['id'] in src and src[s['id']]['sha256'].lower() != s['sha256'].lower(): raise ValueError('几份基础视图的来源编号相同但哈希不同：' + s['id'])
   src.setdefault(s['id'], s)
  if 'default_category' in base: merged.setdefault('default_category', base['default_category'])
 merged['sources'] = list(src.values())
 return merged


TWIN_TOL = {'mm': 1, 'cm': 0.1, 'm': 0.001}   # same object, same sheet: points this close are one marker on the page (review-app.js)
HANDLE_LIKE = re.compile(r'[0-9A-Fa-f]{1,16}')   # a bare CAD handle names one object only within its own drawing


def shared_object_stats(view, cap=50):
 """Records sharing one object id (scene mapping contract 6): how many objects several records carry; those where two or more records
 carry quantities (may be counted twice across packages); point records of one sheet farther apart than the unit tolerance.
 An id that looks like a bare CAD handle is one object per drawing (registration 20261017-0215): the same handle on two drawings is
 listed as a suspected handle collision, not as one object. Other ids (box numbers, identity keys) stay one object across drawings;
 so does a handle with a record whose drawing is unknown (unlocated), so a real double count is not hidden."""
 drawing_of = {s['id']: s.get('source_id') or s['id'].split('|', 1)[0] for s in view.get('scenes', [])}
 by_id = defaultdict(list)
 for o in view.get('objects', []):
  context = canonical_context_key([(o.get('attributes') or {}).get(k) for k in ('object_type', 'version', 'floor', 'scope_id')])
  by_id[(o['id'], context)].append(o)
 groups, collisions = {}, []
 for (k, context), v in sorted(by_id.items(), key=lambda x: str(x[0])):
  drs = [drawing_of.get(o.get('scene_id')) for o in v]
  if len(v) > 1 and HANDLE_LIKE.fullmatch(str(k)) and None not in drs and len(set(drs)) > 1:
   for d in sorted(set(drs)): groups[(k, d, context)] = [o for o, x in zip(v, drs) if x == d]
   carrying = sorted({x for o, x in zip(v, drs) if o.get('quantities')})
   if len(carrying) > 1: collisions.append({'object_id': k, 'drawings': carrying, 'records': [o['catalog_key'] for o in v if o.get('quantities')]})
  else: groups[(k, None, context)] = v
 shared = {key: v for key, v in groups.items() if len(v) > 1}
 units = {s['id']: s.get('coordinate_unit') for s in view.get('scenes', [])}
 multi = [{'object_id': k, **({'drawing': d} if d else {}), 'records': [o['catalog_key'] for o in v if o.get('quantities')]}
          for (k, d, _), v in shared.items() if sum(1 for o in v if o.get('quantities')) > 1]
 apart = []
 for (k, _, _), v in shared.items():
  per_scene = defaultdict(list)
  for o in v:
   if o.get('kind') == 'point' and o.get('scene_id') and o.get('points'): per_scene[o['scene_id']].append(o)
  for sid, os_ in sorted(per_scene.items()):
   tol, clusters = TWIN_TOL.get(units.get(sid), 0), []
   for o in os_:
    p = o['points'][0]
    c = next((c for c in clusters if math.hypot(c[0]['points'][0][0] - p[0], c[0]['points'][0][1] - p[1]) <= tol), None)
    if c is None: clusters.append([o])
    else: c.append(o)
   if len(clusters) > 1: apart.append({'scene': sid, 'object_id': k, 'positions': len(clusters), 'records': [o['catalog_key'] for o in os_]})
 out = {'objects': len(shared), 'records': sum(len(v) for v in shared.values()),
        'by_record_count': {str(n): c for n, c in sorted(Counter(len(v) for v in shared.values()).items())},
        'multi_quantity': len(multi), 'coordinate_mismatch': len(apart), 'suspected_handle_collisions': len(collisions)}
 if multi: out.update(multi_quantity_rows=multi[:cap], warning=f'{len(multi)} 个实物有两条以上记录带数量，可能跨包重复计量，请核对 multi_quantity_rows')
 if apart: out['coordinate_mismatch_rows'] = apart[:cap]
 if collisions:
  out.update(suspected_handle_collision_rows=collisions[:cap],
             collision_note=f'{len(collisions)} 个编号像 CAD 句柄，在不同图纸上重号且各带数量：按不同实物计，不算跨包重复，不计入 multi_quantity；'
                            '如果它是跨图共用的全局编号，请改成不像句柄的写法（如加图纸或系统前缀），见 suspected_handle_collision_rows')
 return out


def build(a, report, filters):
 """Mapping + tables (+ base views) -> one comparison view; fills the report and returns the exit code."""
 if not a.mapping or not a.output: raise ValueError('要写 --mapping 和 --output（只核同号图页用 --check-shared-sheets）')
 if a.output.exists(): raise ValueError('输出视图已存在，请用新的版本名')
 mapping = json.loads(a.mapping.read_text(encoding='utf-8-sig'))
 if mapping.get('schema') != MAPPING_SCHEMA: raise ValueError('映射文件 schema 须为 ' + MAPPING_SCHEMA)
 if not isinstance(mapping.get('view'), dict) or not isinstance(mapping.get('tables'), dict): raise ValueError('映射文件须有 view 和 tables')
 warnings = mapping_warnings(mapping)
 if warnings: report['mapping_warnings'] = warnings
 out_dir = a.output.resolve().parent
 base_view = merge_base_views(a.base_view, out_dir, filters) if a.base_view else None
 view, info = build_view(mapping, a.mapping.resolve().parent, base_view)
 if base_view: info['base_views'] = base_view['base_views']
 if base_view and base_view['renamed_groups']: info['renamed_groups'] = base_view['renamed_groups']
 if base_view and base_view['merged_scenes']: info['merged_scenes'] = base_view['merged_scenes']
 from qto_cross_drawing_registration import annotate_number_drift
 number_drifts = annotate_number_drift(view)
 normalized = normalize_view(view, out_dir if a.base_view else None)
 a.output.parent.mkdir(parents=True, exist_ok=True)
 with a.output.open('x', encoding='utf-8', newline='\n') as f: json.dump(view, f, ensure_ascii=False, indent=1)
 check = normalized['comparison']['generation_check']
 report.update(status='PASS', output=str(a.output), mapping_sha256=sha_file(a.mapping), **info, comparison_counts=check['counts'], comparison_exceptions=check['exceptions'],
               result_fingerprint=normalized['result_fingerprint'], shared_objects=shared_object_stats(view),
               suspected_number_drifts={'count': len(number_drifts), 'rows': number_drifts})
 return 0


def main():
 p = argparse.ArgumentParser(); p.add_argument('--mapping', type=Path); p.add_argument('--output', type=Path)
 p.add_argument('--base-view', type=Path, action='append', default=[], help='可以写多次，并入几份已有视图'); p.add_argument('--report', type=Path)
 p.add_argument('--scene-report', type=Path, action='append', default=[], help='各包的图面转换检查报告，可写多次；同号图页底图不同时据此列出过滤条件的差集')
 p.add_argument('--check-shared-sheets', action='store_true', help='只核几份基础视图的同号图页是否相同，不合成；不用 --mapping、--output，有不同的退出码 2')
 a = p.parse_args()
 report = {'status': 'INPUT_REQUIRED', 'quantity_changes': 0}
 try:
  if a.report and a.report.resolve() in {x.resolve() for x in (a.mapping, a.output, *a.base_view, *a.scene_report) if x}: raise ValueError('检查报告路径不能覆盖映射、视图、基础视图或图面转换检查报告')
  filters = load_scene_reports(a.scene_report)
  if a.check_shared_sheets:
   if a.mapping or a.output: raise ValueError('--check-shared-sheets 只核不合成，不写 --mapping、--output')
   if len(a.base_view) < 2: raise ValueError('--check-shared-sheets 至少要两份 --base-view')
   rows = check_shared_scenes(a.base_view, filters); diff = [r for r in rows if not r['same']]
   report.update(status='INPUT_REQUIRED' if diff else 'PASS', mode='check_shared_sheets', base_views=[x.name for x in a.base_view],
                 shared_sheets={'checked': len(rows), 'same': len(rows) - len(diff), 'different': len(diff)}, shared_sheet_rows=rows)
   if diff: report['error'] = f'{len(diff)} 处同号图页内容不同：' + '、'.join(f'{r["scene"]}（{" 与 ".join(r["views"])}）' for r in diff)
   code = 2 if diff else 0
  else:
   code = build(a, report, filters)
 except (OSError, ValueError, KeyError, TypeError) as e:
  report['error'] = str(e)
  if hasattr(e, 'problems'): report['problems'] = e.problems
  code = 2
 if a.report:
  a.report.parent.mkdir(parents=True, exist_ok=True); a.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
 summary = {k: report[k] for k in ('status', 'output', 'comparison_counts', 'shared_sheets', 'error') if k in report}
 if 'problems' in report: summary['problem_count'] = len(report['problems'])
 if 'mapping_warnings' in report: summary['mapping_warning_count'] = len(report['mapping_warnings'])
 so = report.get('shared_objects') or {}
 if so.get('multi_quantity') or so.get('coordinate_mismatch') or so.get('suspected_handle_collisions'):
  summary['shared_objects'] = {k: so[k] for k in ('objects', 'multi_quantity', 'coordinate_mismatch', 'suspected_handle_collisions')}
 print(json.dumps(summary, ensure_ascii=False)); return code


if __name__ == '__main__': raise SystemExit(main())
