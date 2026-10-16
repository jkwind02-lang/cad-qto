"""第五批 K7：合成视图的 shared_objects 不把不同图纸上的同号句柄当成同一实物（登记 20261017-0215）。

两包图面视图由合成的中性布局转换而来（同 test_shared_sheets_20261016）；E-B 的图页改挂到另一张图纸 D9，模拟两张图的
句柄重号（电气 r4：D006 的 3BFE 与 D010 的 3BFE）。不含项目工程量。
"""
import copy, json

import test_shared_sheets_20261016 as ss

Q = ss.Q


def merged(tmp_path, edit, move=True):
    """E-A as converted; E-B carries the same objects under its own catalog keys, moved onto drawing D9 unless move=False.
    edit(objects of E-A by id, objects of E-B by id, E-B view) runs before both views are written."""
    a, _ = ss.package(tmp_path, 'E-A')
    va = json.loads(a.read_text(encoding='utf-8')); vb = copy.deepcopy(va)
    for o in vb['objects']: o['catalog_key'] = 'B|' + o['catalog_key']
    if move:
        rename = {s['id']: 'D9|' + s['id'].split('|', 1)[1] for s in vb['scenes']}
        old = {s['source_id'] for s in vb['scenes']}
        vb['sources'] += [{**copy.deepcopy(x), 'id': 'D9'} for x in vb['sources'] if x.get('id') in old][:1]
        for s in vb['scenes']: s['id'], s['source_id'] = rename[s['id']], 'D9'
        for o in vb['objects']:
            if o.get('scene_id'): o['scene_id'] = rename[o['scene_id']]
    edit({o['id']: o for o in va['objects']}, {o['id']: o for o in vb['objects']}, vb)
    a.write_text(json.dumps(va, ensure_ascii=False), encoding='utf-8')
    b = tmp_path / 'E-B.json'; b.write_text(json.dumps(vb, ensure_ascii=False), encoding='utf-8')
    code, rep, _ = ss.merge(tmp_path, a, b)
    assert code == 0, rep
    return rep['shared_objects'], va


def rename(by, new, quantities=True):
    o = by['S1']; o['id'] = new
    if quantities: o['quantities'] = Q
    return o


def drawing_of(view):
    return next(s['source_id'] for s in view['scenes'] if s['id'] == next(o['scene_id'] for o in view['objects'] if o['id'] == '3BFE'))


# ---------- counter-examples ----------

def test_same_handle_on_two_drawings_is_a_suspected_collision_not_a_double_count(tmp_path):
    so, va = merged(tmp_path, lambda a, b, vb: (rename(a, '3BFE'), rename(b, '3BFE')))
    key = next(o['catalog_key'] for o in va['objects'] if o['id'] == '3BFE')
    assert so['multi_quantity'] == 0 and 'warning' not in so and 'multi_quantity_rows' not in so
    assert so['suspected_handle_collisions'] == 1
    assert so['suspected_handle_collision_rows'] == [{'object_id': '3BFE', 'drawings': sorted([drawing_of(va), 'D9']), 'records': [key, 'B|' + key]}]
    assert '不算跨包重复' in so['collision_note'] and '全局编号' in so['collision_note']


def test_handle_twice_on_one_drawing_is_still_a_double_count_beside_the_collision(tmp_path):
    def edit(a, b, vb):
        rename(a, '3BFE'); twin = rename(b, '3BFE')
        vb['objects'].append({**copy.deepcopy(twin), 'catalog_key': 'B2|' + twin['catalog_key']})   # a second package record on D9
    so, va = merged(tmp_path, edit)
    key = next(o['catalog_key'] for o in va['objects'] if o['id'] == '3BFE')
    assert so['multi_quantity_rows'] == [{'object_id': '3BFE', 'drawing': 'D9', 'records': ['B|' + key, 'B2|B|' + key]}]
    assert so['multi_quantity'] == 1 and so['suspected_handle_collisions'] == 1


# ---------- legal ----------

def test_a_global_number_on_two_drawings_is_still_one_object(tmp_path):
    so, va = merged(tmp_path, lambda a, b, vb: (rename(a, 'AL1-2'), rename(b, 'AL1-2')))
    key = next(o['catalog_key'] for o in va['objects'] if o['id'] == 'AL1-2')
    assert so['multi_quantity_rows'] == [{'object_id': 'AL1-2', 'records': [key, 'B|' + key]}]
    assert so.get('suspected_handle_collisions', 0) == 0 and 'suspected_handle_collision_rows' not in so


def test_same_handle_on_one_drawing_is_still_a_double_count(tmp_path):
    so, _ = merged(tmp_path, lambda a, b, vb: (rename(a, '3BFE'), rename(b, '3BFE')), move=False)
    assert [r['object_id'] for r in so['multi_quantity_rows']] == ['3BFE'] and 'drawing' not in so['multi_quantity_rows'][0]
    assert so.get('suspected_handle_collisions', 0) == 0


def test_a_handle_whose_drawing_is_unknown_is_kept_together(tmp_path):
    def edit(a, b, vb):
        rename(a, '3BFE')
        rename(b, '3BFE').update(scene_id=None, kind='unlocated', points=[], unlocated_reason='no_geometry')
    so, _ = merged(tmp_path, edit)
    assert [r['object_id'] for r in so['multi_quantity_rows']] == ['3BFE'] and so.get('suspected_handle_collisions', 0) == 0


def test_a_collision_with_quantities_on_one_side_only_is_not_listed(tmp_path):
    so, _ = merged(tmp_path, lambda a, b, vb: (rename(a, '3BFE'), rename(b, '3BFE', quantities=False)))
    assert so['multi_quantity'] == 0 and so.get('suspected_handle_collisions', 0) == 0
