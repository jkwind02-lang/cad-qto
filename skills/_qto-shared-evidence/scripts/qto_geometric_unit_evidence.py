#!/usr/bin/env python3
"""Source-bound, inferred coordinate units; never a drawing-unit declaration.

The physical scale depends on a source paper-size / printed plot-scale hypothesis.
An independent DIMENSION checks geometric consistency, not an independent SI
unit definition. No user-entered span, paper size or scale is accepted as proof.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import math
import re
import sys
from pathlib import Path

from qto_cad_source_contract import canonical_scale, unit_known, unit_tokens, verify_source_file, verify_unit_declaration_texts
from qto_cad_geometry import block_reference_facts, dimension_measurement_facts, read_dxf_with_diagnostics

REQUEST_SCHEMA = 'qto-geometric-unit-request/1.0'
EVIDENCE_SCHEMA = 'qto-geometric-unit-evidence/1.0'
ISO_A_MM = {'A0': (841., 1189.), 'A1': (594., 841.), 'A2': (420., 594.),
            'A3': (297., 420.), 'A4': (210., 297.)}
TEXT_TYPES = {'TEXT', 'MTEXT', 'ATTRIB', 'ATTDEF'}
TOLERANCE = .01
OPAQUE_TEXT_CARRIERS = {'ACAD_PROXY_ENTITY', 'OLE2FRAME', 'IMAGE', 'PDFUNDERLAY', 'DWFUNDERLAY',
                       'DGNUNDERLAY', 'ACAD_TABLE', 'TABLE', 'MULTILEADER', 'MLEADER', 'SHAPE'}
ARRAY_KNOWN_NON_TEXT_ENTITIES = {'LINE', 'ARC', 'CIRCLE', 'ELLIPSE', 'LWPOLYLINE', 'POLYLINE',
                               'VERTEX', 'SEQEND', 'SOLID', 'TRACE', '3DFACE', 'POINT',
                               'HATCH', 'SPLINE', 'XLINE', 'RAY', 'LEADER', 'MESH'}


class GeometricUnitUnresolved(ValueError):
    def __init__(self, message, partial_evidence):
        super().__init__(message)
        self.partial_evidence = partial_evidence


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def runtime_python_dependencies(entry_paths, search_paths):
    """Deterministic local Python import closure, including function-scope imports."""
    roots=[Path(p).resolve() for p in search_paths]
    pending=[Path(p).resolve() for p in entry_paths]
    seen={}
    while pending:
        path=pending.pop()
        if str(path) in seen:
            continue
        seen[str(path)]=_sha(path)
        tree=ast.parse(path.read_text(encoding='utf-8-sig'),filename=str(path))
        for node in ast.walk(tree):
            names=([alias.name for alias in node.names] if isinstance(node,ast.Import) else
                   [node.module] if isinstance(node,ast.ImportFrom) and node.module else [])
            if isinstance(node,ast.ImportFrom):
                names += [(node.module+'.' if node.module else '')+a.name for a in node.names if a.name!='*']
            for name in names:
                local=path.parent
                if isinstance(node,ast.ImportFrom):
                    for _ in range(max(0,node.level-1)):
                        local=local.parent
                for root in [local,*roots]:
                    stem=root.joinpath(*name.split('.'))
                    candidates=[stem.with_suffix('.py'),stem/'__init__.py']
                    found=next((p for p in candidates if p.is_file()),None)
                    if found:
                        pending.append(found.resolve())
                        for i in range(1,len(name.split('.'))):
                            init=root.joinpath(*name.split('.')[:i],'__init__.py')
                            if init.is_file():pending.append(init.resolve())
                        break
    return dict(sorted(seen.items()))


def verify_runtime_python_dependencies(frozen, current):
    """Compare complete module identities by bytes across identical checkouts."""
    def identities(values):
        if not isinstance(values,dict) or not values:
            raise ValueError('缺完整生产代码依赖')
        result={}
        for path,digest in values.items():
            verify_source_file(dict(path=path,sha256=digest),'专业生产代码依赖')
            normalized=Path(path).as_posix()
            if '/skills/' not in normalized:
                raise ValueError('生产代码依赖不属于插件源码')
            key='skills/'+normalized.split('/skills/',1)[1]
            if key in result:raise ValueError('生产代码依赖存在重复模块')
            result[key]=digest
        return result
    if identities(frozen)!=identities(current):
        raise ValueError('专业生产代码或依赖字节变化，旧凭据不可消费')
    return frozen


def geometric_unit_runtime_dependencies():
    """The executing code fixes the complete direct unit-verifier dependency set."""
    folder = Path(__file__).resolve().parent
    names = ('qto_geometric_unit_evidence.py', 'qto_cad_source_contract.py', 'qto_cad_geometry.py')
    return [{'module': '_qto-shared-evidence/scripts/' + name, 'path': str(folder / name),
             'sha256': _sha(folder / name)} for name in names]


def verify_geometric_unit_runtime_dependencies(dependencies):
    expected = {r['module']: r['sha256'] for r in geometric_unit_runtime_dependencies()}
    if (not isinstance(dependencies, list) or len(dependencies) != len(expected)
            or any(not isinstance(r, dict) or not isinstance(r.get('module'), str) for r in dependencies)):
        raise ValueError('几何单位缺少完整运行代码字节依赖身份，须重建单位凭据并重新实际消费')
    supplied = {r['module']: r.get('sha256') for r in dependencies}
    if len(supplied) != len(dependencies) or supplied != expected:
        raise ValueError('几何单位核验代码或直接依赖字节已变化，旧凭据不可复用，须重新实际消费')
    for row in dependencies:
        verify_source_file(row, '几何单位生产代码依赖')
    return dependencies


def verify_geometric_unit_runtime_identity(credential):
    if not isinstance(credential, dict):
        raise ValueError('几何单位运行依赖必须绑定结构化原凭据')
    return verify_geometric_unit_runtime_dependencies(credential.get('runtime_dependencies'))


def _text(entity):
    return entity.plain_text() if entity.dxftype() == 'MTEXT' else str(entity.dxf.get('text', ''))


def _unit_text_candidate(text):
    # M1521 is a door/code identifier, not a metre declaration. A numerical
    # prefix (e.g. -0.9m) remains a real local-unit annotation.
    words = [(a,b,v) for a,b,v in unit_tokens(text, with_spans=True) if b == len(text) or not text[b].isdigit()]
    marker = re.search(r'单位|繪圖單位|绘图单位', text)
    organizational_label = re.fullmatch(r'\s*(?:建设|建設|设计|設計|施工|监理|監理|编制|編制|审图|審圖)单位\s*[:：]?\s*[^\n]*', text)
    return bool(words or marker and not organizational_label)


def _finite(values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def _owner_context_issue(owner):
    if owner.has_xdata('AcadAnnotative'):
        return '注释可见性上下文尚未恢复'
    xdata = getattr(owner, 'xdata', None)
    if xdata is not None and any('BLOCKREP' in k.upper() for k in xdata.data):
        return '动态块可见性上下文尚未恢复'
    if owner.has_extension_dict:
        keys = [str(k).upper() for k in owner.get_extension_dict().keys()]
        if any(any(marker in k for marker in ('ACAD_FILTER', 'SPATIAL', 'CONTEXT_DATA', 'ENHANCEDBLOCK', 'BLOCKREP')) for k in keys):
            return '动态/裁剪或注释上下文尚未恢复'
    return None


def _visible(doc, entity):
    layer = doc.layers.get(entity.dxf.get('layer', '0'))
    if (layer is None or layer.is_off() or layer.is_frozen() or bool(entity.dxf.get('invisible', 0))
            or entity.dxftype() in {'ATTRIB', 'ATTDEF'} and int(entity.dxf.get('flags', 0)) & 1):
        raise ValueError('所选尺度证据及其 INSERT 链须在当前 Model 可见；关闭/冻结图层或隐藏源实体不能作本页尺度证据')
    context_issue = _owner_context_issue(entity)
    if context_issue:
        raise ValueError('所选尺度证据的' + context_issue + '，不能仅凭图层开启判定可见')


def _instance_context_issue(doc, entity):
    """Do not infer native clipping/dynamic visibility from cached definition bounds."""
    try:
        facts = block_reference_facts(entity)
        if facts['dynamic_evidence'] or facts['anonymous_dynamic_candidate']:
            return '动态块可见性状态尚未原生核验'
        block = doc.blocks.get(entity.dxf.name)
        for owner in (entity, block.block_record if block is not None else None):
            issue = _owner_context_issue(owner) if owner is not None else None
            if issue:
                return '块实例或定义的' + issue + '，包围盒及图层状态不能证明可见'
    except Exception:
        return '块实例可见性上下文无法读取'
    return None


def _insert_cell_context(entity):
    rows, columns = int(entity.dxf.get('row_count', 1)), int(entity.dxf.get('column_count', 1))
    if rows <= 0 or columns <= 0:
        raise ValueError('INSERT阵列行列数必须为正数，当前实例未核清')
    if rows*columns > 100000:
        raise ValueError('INSERT阵列超过完整cell核验上限，不能以局部遍历宣称单位文字已全查')
    if rows != 1 or columns != 1:
        if tuple(entity.dxf.get('extrusion', (0, 0, 1))) != (0, 0, 1):
            raise ValueError('非XY阵列的文字放置上下文尚未恢复')
        if not _finite([float(entity.dxf.get(k, 0.)) for k in ('row_spacing', 'column_spacing', 'rotation')]):
            raise ValueError('INSERT阵列间距或旋转不是有限值')
    return rows, columns


def _insert_cell(entity, row, column):
    """Same default-OCS offsets as the source workbench, without cell deduping."""
    from ezdxf.math import Vec3
    rows, columns = _insert_cell_context(entity)
    if not (0 <= row < rows and 0 <= column < columns):
        raise ValueError('INSERT阵列cell越界')
    if rows == columns == 1:
        return entity, (0., 0., 0.)
    offset = Vec3(column*entity.dxf.column_spacing, row*entity.dxf.row_spacing)
    offset = offset.rotate_deg(entity.dxf.rotation)
    virtual = entity.copy()
    virtual.dxf.insert += offset
    virtual.dxf.row_count = virtual.dxf.column_count = 1
    return virtual, tuple(offset)


def _ref(doc, value, allow_array_cells=True):
    """Resolve a real source entity through a checked planar similarity chain."""
    if (not isinstance(value, dict) or not isinstance(value.get('handle'), str)
            or re.fullmatch(r'[0-9A-Fa-f]+', value['handle']) is None):
        raise ValueError('源实体引用须为 {handle, insert_handles:[]}，不能用自填两数或 bbox')
    handles = value.get('insert_handles', [])
    if not isinstance(handles, list) or any(not isinstance(h, str) for h in handles):
        raise ValueError('insert_handles 必须是从 Model 到定义的真实 INSERT 句柄链')
    matrices, owner, seen = [], doc.modelspace().block_record_handle, set()
    chain, canonical_handles, last_insert, last_offset = [], [], None, (0., 0., 0.)
    for token in handles:
        match = re.fullmatch(r'([0-9A-Fa-f]+)(?:\[(0|[1-9]\d*),(0|[1-9]\d*)\])?', token)
        if match is None:
            raise ValueError('INSERT链须为原句柄或规范句柄[行,列]，不能省略/伪造cell身份')
        handle = match[1].upper()
        item = doc.entitydb.get(handle)
        if item is None or item.dxftype() != 'INSERT' or item.dxf.owner != owner or handle in seen:
            raise ValueError('INSERT 链不是当前 Model 中的唯一有效源实例路径')
        _visible(doc, item)
        context_issue = _instance_context_issue(doc, item)
        if context_issue:
            raise ValueError(context_issue)
        seen.add(handle)
        rows, columns = _insert_cell_context(item)
        is_array = rows != 1 or columns != 1
        if is_array != (match[2] is not None):
            raise ValueError('真实阵列须逐cell引用，普通INSERT不能虚构cell身份')
        if is_array and not allow_array_cells:
            raise ValueError('该调用未启用所选阵列cell证据')
        row, column = (int(match[2]), int(match[3])) if is_array else (0, 0)
        actual, offset = _insert_cell(item, row, column)
        if ((row != 0 and item.dxf.row_spacing == 0)
                or (column != 0 and item.dxf.column_spacing == 0)):
            raise ValueError('阵列零间距cell不是独立原生实例，须使用规范0行/0列，不能以别名选择证据')
        sx, sy, sz = [float(item.dxf.get(k, 1.)) for k in ('xscale', 'yscale', 'zscale')]
        rotation = float(item.dxf.get('rotation', 0.))
        if (not _finite((sx, sy, sz, rotation)) or min(abs(sx), abs(sy), sz) <= 0
                or not math.isclose(abs(sx), abs(sy), rel_tol=1e-10)
                or tuple(item.dxf.get('extrusion', (0, 0, 1))) != (0, 0, 1)):
            raise ValueError('几何单位尚不支持非等比、非平面或Z镜像 INSERT')
        block = doc.blocks.get(item.dxf.name)
        if block is None or block.block.is_xref or block.name.upper().startswith('*D'):
            raise ValueError('外参、缺失定义或尺寸渲染块不能作为单位图框/图签源')
        owner = block.block_record_handle
        matrices.append(actual.matrix44())
        canonical_handles.append(handle + (f'[{row},{column}]' if is_array else ''))
        chain.append(dict(handle=handle, instance_token=canonical_handles[-1], block_name=block.name,
                          row=row, column=column, row_count=rows, column_count=columns,
                          row_spacing=float(item.dxf.get('row_spacing', 0.)),
                          column_spacing=float(item.dxf.get('column_spacing', 0.)),
                          source_insert=list(item.dxf.insert), cell_insert=list(actual.dxf.insert),
                          offset_in_parent_coordinates=list(offset),
                          matrix44_to_parent=list(actual.matrix44())))
        last_insert, last_offset = item, offset
    entity = doc.entitydb.get(value['handle'].upper())
    attribute = (entity is not None and entity.dxftype() == 'ATTRIB' and last_insert is not None
                 and entity.dxf.get('owner') == last_insert.dxf.handle
                 and any(a is entity for a in last_insert.attribs))
    if entity is None or (not attribute and entity.dxf.get('owner') != owner):
        raise ValueError('源实体句柄不属于指定 Model / INSERT 实例')
    _visible(doc, entity)
    if attribute:
        # ATTRIB placement is already in its owning INSERT's parent system.
        # The owning INSERT matrix must never transform it a second time.
        from ezdxf.math import Matrix44
        matrices = [*matrices[:-1], Matrix44.translate(*last_offset)]
    def transform(point):
        point = tuple(point)
        for matrix in reversed(matrices):
            point = tuple(matrix.transform(point))
        if not _finite(point):
            raise ValueError('源几何坐标不是有限值')
        return point
    transform.source_context = dict(
        source_ref=dict(handle=entity.dxf.handle, insert_handles=canonical_handles),
        instance_path='/' + '/'.join([*canonical_handles, entity.dxf.handle]),
        source_entity_type=entity.dxftype(), source_owner_handle=entity.dxf.owner,
        insert_chain=chain, placement_matrix44_chain=[list(m) for m in matrices],
        placement_rule='ATTRIB_parent_coordinates_plus_own_cell_offset_once_then_ancestors'
                       if attribute else 'source_coordinates_through_complete_INSERT_chain')
    return entity, transform


def _source_context(entity, transform, points=None):
    """Keep native placement and the exact matrices used by the source replay."""
    result = dict(transform.source_context)
    if points is None and entity.dxftype() in TEXT_TYPES:
        points = _text_points(entity, lambda p: tuple(p))
    if points is not None:
        result['source_placement_points'] = [list(p) for p in points]
        result['placement_points_wcs'] = [list(transform(p)) for p in points]
    return result


def _canonical_search_path(doc, path):
    """Only exact zero source spacing aliases share a selection identity.

    The scanner still inventories every declared cell. Distinct INSERTs and
    nonzero (including tiny or negative) spacings are never merged.
    """
    tokens = []
    for token in path.split('/'):
        match = re.fullmatch(r'([0-9A-Fa-f]+)\[(\d+),(\d+)\]', token)
        if match:
            item = doc.entitydb.get(match[1].upper())
            row, column = int(match[2]), int(match[3])
            if item is not None and item.dxftype() == 'INSERT':
                row = 0 if item.dxf.row_spacing == 0 else row
                column = 0 if item.dxf.column_spacing == 0 else column
            token = f'{match[1].upper()}[{row},{column}]'
        tokens.append(token)
    return '/'.join(tokens)


def _text_points(entity, transform, offset=(0., 0., 0.)):
    if entity.dxftype() in {'TEXT', 'ATTRIB', 'ATTDEF'}:
        if tuple(entity.dxf.get('extrusion', (0, 0, 1))) != (0, 0, 1):
            raise ValueError('源 TEXT 非XY位置上下文尚未核清')
        _, first, second = entity.get_placement()
        points = (first,) if second is None else (first, second)
    else:
        points = (entity.dxf.insert,)
    return [transform(tuple(p[i]+offset[i] for i in range(3))) for p in points]


def _point(entity, transform):
    return _text_points(entity, transform)[0]


def _text_in_frame(entity, transform, box, offset=(0., 0., 0.)):
    return all(_inside(p, box) for p in _text_points(entity, transform, offset))


def _dimension_render_context(doc, entity):
    """Default-OCS DIM.insert translates the picture, never definition points.

    DXF group 12 is the OCS insertion of cloned linear-dimension pictures.
    For the supported default XY OCS this is a finite XYZ translation before
    the ordinary parent INSERT chain. The restored picture must be coplanar
    with the frame; a Z coordinate is not silently projected onto that plane.
    No source entity is edited or rendered.
    """
    if tuple(entity.dxf.get('extrusion', (0, 0, 1))) != (0, 0, 1):
        raise ValueError('非XY尺寸渲染上下文尚未支持')
    offset = tuple(entity.dxf.get('insert', (0., 0., 0.)))
    if len(offset) != 3 or not _finite(offset):
        raise ValueError('尺寸渲染平移须为默认XY OCS中的有限XYZ向量')
    block = doc.blocks.get(entity.dxf.get('geometry', ''))
    if block is None or block.block.is_xref:
        raise ValueError('原尺寸渲染文字所在块缺失或为未读外参')
    for owner in (block.block_record, block.block):
        issue = _owner_context_issue(owner)
        if issue:
            raise ValueError('尺寸渲染块定义的' + issue)
    return block, offset


class _FrameBounds(list):
    """Legacy AABB sequence plus the checked source rectangle, never its surrogate."""
    def __init__(self, bounds, vertices, source_entities=()):
        super().__init__(bounds)
        self.vertices = vertices
        self.source_entities = list(source_entities)


def _inside(point, box, strict=False):
    x, y = point[:2]
    vertices = getattr(box, 'vertices', None)
    if vertices:
        distances = []
        span = max(math.dist(a, b) for a, b in zip(vertices, vertices[1:] + vertices[:1]))
        eps = max(span*1e-10, 1e-8)
        if len(point) > 2 and (not _finite(point) or abs(point[2]-vertices[0][2]) > eps):
            return False
        for a, b in zip(vertices, vertices[1:] + vertices[:1]):
            length = math.dist(a[:2], b[:2])
            distances.append(((b[0]-a[0])*(y-a[1])-(b[1]-a[1])*(x-a[0])) / length)
        return (all(v > eps for v in distances) or all(v < -eps for v in distances)) if strict else (
            all(v >= -eps for v in distances) or all(v <= eps for v in distances))
    if strict:
        return box[0] < x < box[2] and box[1] < y < box[3]
    return box[0] <= x <= box[2] and box[1] <= y <= box[3]


def _frame(doc, frame):
    refs = frame.get('entity_refs') if isinstance(frame, dict) else None
    if not isinstance(refs, list) or len(refs) not in (1, 4):
        raise ValueError('frame.entity_refs 必须引用一条闭合矩形多段线或四条源 LINE')
    edges, identities, contexts = [], [], []
    for ref in refs:
        entity, transform = _ref(doc, ref)
        identities.append(json.dumps(ref, sort_keys=True))
        if entity.dxftype() == 'LINE' and len(refs) == 4:
            native_points = [tuple(entity.dxf.start), tuple(entity.dxf.end)]
            edges.append(tuple(transform(p) for p in native_points))
        elif entity.dxftype() == 'LWPOLYLINE' and len(refs) == 1 and entity.closed:
            if tuple(entity.dxf.get('extrusion', (0, 0, 1))) != (0, 0, 1):
                raise ValueError('图框多段线必须位于 XY 平面')
            vertices = list(entity.get_points('xyb'))
            if any(abs(v[2]) > 1e-12 for v in vertices):
                raise ValueError('含圆弧的多段线不是标准矩形图框')
            native_points = [(v[0], v[1], entity.dxf.get('elevation', 0.)) for v in vertices]
            points = [transform(p) for p in native_points]
            if len(points) == 5 and math.dist(points[0], points[-1]) < 1e-7:
                points.pop()
            if len(points) != 4:
                raise ValueError('图框须恰有四个不同角点')
            edges.extend(zip(points, points[1:] + points[:1]))
        else:
            raise ValueError('图框当前只支持真实 LINE 四边或闭合矩形 LWPOLYLINE')
        contexts.append(_source_context(entity, transform, native_points))
    if len(set(identities)) != len(identities):
        raise ValueError('图框边不能重复引用同一源对象')
    points = [p for edge in edges for p in edge]
    box = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
    span = max(box[2] - box[0], box[3] - box[1])
    if span <= 0:
        raise ValueError('图框退化')
    eps = max(span * 1e-9, 1e-8)
    if max(p[2] for p in points) - min(p[2] for p in points) > eps:
        raise ValueError('图框各边必须同处一个 XY 平面')
    corners = []
    for p in points:
        if not any(math.dist(p, c) <= eps for c in corners):
            corners.append(p)
    if len(corners) != 4:
        raise ValueError('图框须由四个相连且不同的真实角点组成')
    found = []
    for a, b in edges:
        ia = next((i for i, c in enumerate(corners) if math.dist(a, c) <= eps), None)
        ib = next((i for i, c in enumerate(corners) if math.dist(b, c) <= eps), None)
        if ia is None or ib is None or ia == ib or abs(a[2]-b[2]) > eps:
            raise ValueError('四边必须闭合成同一 XY 平面内的真实矩形')
        found.append(frozenset((ia, ib)))
    if len(set(found)) != 4:
        raise ValueError('图框缺边或重复边')
    neighbors = {i: [next(iter(edge-{i})) for edge in found if i in edge] for i in range(4)}
    if any(len(v) != 2 for v in neighbors.values()):
        raise ValueError('图框四边未形成唯一闭合矩形')
    order = [0, neighbors[0][0]]
    while len(order) < 4:
        candidates = [i for i in neighbors[order[-1]] if i not in order]
        if len(candidates) != 1:
            raise ValueError('图框四边不是唯一闭合矩形')
        order.append(candidates[0])
    if order[0] not in neighbors[order[-1]]:
        raise ValueError('图框缺少闭合边')
    polygon = [corners[i] for i in order]
    vectors = [(b[0]-a[0], b[1]-a[1]) for a, b in zip(polygon, polygon[1:]+polygon[:1])]
    lengths = [math.hypot(*v) for v in vectors]
    if (min(lengths) <= eps or any(abs(a[0]*b[0]+a[1]*b[1]) > la*lb*1e-9
            for a, b, la, lb in zip(vectors, vectors[1:]+vectors[:1], lengths, lengths[1:]+lengths[:1]))):
        raise ValueError('图框四边必须互相垂直，不能把非矩形 bbox 当标准幅面')
    return _FrameBounds(box, polygon, contexts), lengths[0], lengths[1]


def _scope(scope, box):
    if not isinstance(scope, dict) or scope.get('layout', 'Model') != 'Model':
        raise ValueError('几何单位当前只用于 Model，纸空间/视口比例需独立证明，不能直接套图签比例')
    b = scope.get('bounds')
    keys = ('xmin', 'ymin', 'xmax_exclusive', 'ymax_exclusive')
    if not isinstance(b, dict) or not all(k in b for k in keys) or not _finite([b[k] for k in keys]):
        raise ValueError('几何单位必须绑定有限的当前图框 scope.bounds')
    values = [b[k] for k in keys]
    scope_corners = ((values[0], values[1]), (values[2], values[1]), (values[2], values[3]), (values[0], values[3]))
    if (values[0] >= values[2] or values[1] >= values[3] or not all(_inside(p, box) for p in scope_corners)):
        raise ValueError('几何单位凭据只适用于所引图框真实内部（含旋转/镜像框），不能扩展到其他图框或整图')
    if scope.get('boundary_policy', 'contained') != 'contained':
        raise ValueError('几何单位只允许 contained 边界；跨框整对象长度不能沿用')


def _paper_size_evidence(doc, request, box):
    """Read paper dimensions from explicit source semantics, never component sizes.

    A paper declaration states the physical sheet only. It is not a drawing
    coordinate SI declaration and cannot open professional consumption.
    """
    refs = request.get('paper_size_text_refs', [])
    if not isinstance(refs, list):
        raise ValueError('paper_size_text_refs 须为本框真实纸张幅面源引用数组')
    rows, used = [], set()
    for ref in refs:
        entity, transform = _ref(doc, ref)
        if entity.dxftype() not in TEXT_TYPES or not _text_in_frame(entity, transform, box):
            raise ValueError('纸张幅面原文须在当前图框内且与图框共面可见')
        text = _text(entity).strip()
        match = re.fullmatch(r'(?:图纸幅面|圖紙幅面|纸张幅面|紙張幅面|纸张尺寸|紙張尺寸)\s*[:：]\s*'
                             r'(\d+(?:\.\d+)?)\s*[×xX*]\s*(\d+(?:\.\d+)?)\s*(?:mm|毫米)\s*[。.]?', text, re.I)
        if match is None:
            raise ValueError('须为明确纸张幅面/纸张尺寸的双边毫米原文；构件宽高、尺寸覆盖和自填幅面不是纸张来源')
        sides = [float(match[1]), float(match[2])]
        if not _finite(sides) or min(sides) <= 0:
            raise ValueError('源纸张幅面双边须为有限正数')
        context = _source_context(entity, transform)
        identity = context['instance_path']
        if identity in used:
            raise ValueError('纸张幅面源实例不能重复')
        used.add(identity)
        rows.append(dict(source_ref=ref, source_text=text, instance_path=identity, paper_size_mm=sides,
                         source_context=context,
                         evidence_class='source_paper_size_declaration',
                         physical_unit_proven_independently=False,
                         semantics='physical_paper_size_only_not_coordinate_unit'))
    if rows and any(sorted(r['paper_size_mm']) != sorted(rows[0]['paper_size_mm']) for r in rows[1:]):
        raise ValueError('同框纸张幅面原文互相冲突，不能选择有利的一条')
    return rows


def _search_unit_texts(doc, frame_box):
    """Search every encoded namespace, then judge applicability through active instances.

    Unused definitions are inventoried but cannot become an applicable declaration.
    Unlocated, unread text carriers in an active instance remain unresolved.
    """
    from ezdxf import bbox
    from itertools import product
    counts, all_hits = {}, []
    for block in doc.blocks:
        counts[block.name] = 0
        for entity in block:
            candidates = []
            if entity.dxftype() in TEXT_TYPES:
                candidates.append(_text(entity))
            if entity.dxftype() == 'INSERT':
                candidates.extend(_text(a) for a in entity.attribs)
            if entity.dxftype() == 'DIMENSION':
                candidates.append(str(entity.dxf.get('text', '')))
            for text in candidates:
                counts[block.name] += 1
                if _unit_text_candidate(text):
                    all_hits.append({'handle': entity.dxf.handle, 'block': block.name, 'text': text})
    hits, uninspected, excluded, noncoordinate, active_text_count = [], [], [], [], 0
    checked_array_cells = []

    def transform(point, matrices):
        for matrix in reversed(matrices):
            point = tuple(matrix.transform(point))
        return tuple(point)

    def extent(entity, matrices):
        try:
            bounds = bbox.extents([entity], fast=True)
            if not bounds.has_data:
                return None
            points = [transform(p, matrices) for p in product(*zip(bounds.extmin, bounds.extmax))]
            values = [min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)]
            return values if _finite(values) else None
        except Exception:
            return None

    def outside(box):
        return box is not None and (box[2] < frame_box[0] or box[0] > frame_box[2]
                                    or box[3] < frame_box[1] or box[1] > frame_box[3])

    def unknown(entity, matrices, path, reason, geometry=None):
        box = extent(geometry if geometry is not None else entity, matrices)
        row = {'handle': entity.dxf.handle, 'entity_type': entity.dxftype(), 'instance_path': path, 'bbox': box, 'reason': reason}
        if outside(box):
            excluded.append({**row, 'disposition': 'proven_outside_frame'})
        else:
            uninspected.append(row)

    def visit(entity, matrices=(), path='', stack=()):
        nonlocal active_text_count
        kind = entity.dxftype()
        path = path + '/' + str(entity.dxf.handle)
        strings = [_text(entity)] if kind in TEXT_TYPES else [str(entity.dxf.get('text', ''))] if kind == 'DIMENSION' else []
        for text in strings:
            active_text_count += 1
            if not _unit_text_candidate(text):
                continue
            box = extent(entity, matrices)
            row = {'handle': entity.dxf.handle, 'entity_type': kind, 'instance_path': path,
                   'canonical_instance_path': _canonical_search_path(doc, path), 'bbox': box, 'text': text}
            if outside(box):
                excluded.append({**row, 'disposition': 'proven_outside_frame'})
            elif (not re.search(r'坐标|座標|绘图|繪圖|图纸|圖紙|尺寸|其余|其餘', text)
                  and (re.fullmatch(r'\s*[-+±]?\d+(?:\.\d+)?\s*(?:mm|cm|m|ft|in|毫米|厘米|米|英尺|英寸)\s*', text, re.I)
                       or re.search(r'材料|图例|圖例|工程量|数量|數量|标高|標高|管长|管長', text))):
                noncoordinate.append({**row, 'disposition': 'local_numeric_or_material_elevation_annotation',
                                      'used_to_infer_coordinate_unit': False})
            else:
                hits.append(row)
        if kind in OPAQUE_TEXT_CARRIERS:
            unknown(entity, matrices, path, '可能承载适用单位文字，内容尚未读清')
        elif '[' in path and kind not in TEXT_TYPES | ARRAY_KNOWN_NON_TEXT_ENTITIES | {'INSERT', 'DIMENSION'}:
            unknown(entity, matrices, path, '数组cell内未知实际载体尚未证明不承载适用单位文字')
        if kind == 'INSERT':
            name = entity.dxf.name
            block = doc.blocks.get(name)
            context_issue = _instance_context_issue(doc, entity)
            try:
                rows, columns = _insert_cell_context(entity)
            except (ValueError, ArithmeticError) as exc:
                # The array itself has not been bounded cell by cell. Its
                # surrogate bbox must not turn an incomplete scan into absence.
                uninspected.append(dict(handle=entity.dxf.handle, entity_type=kind,
                    instance_path=path, bbox=None, reason=str(exc)))
                return
            is_array = rows != 1 or columns != 1
            for row in range(rows):
                for column in range(columns):
                    cell_path = path + (f'[{row},{column}]' if is_array else '')
                    try:
                        virtual, offset = _insert_cell(entity, row, column)
                        if is_array:
                            checked_array_cells.append(dict(handle=entity.dxf.handle,
                                instance_path=cell_path, row=row, column=column,
                                parent_instance_path=path.rsplit('/', 1)[0],
                                offset_in_parent_coordinates=list(offset)))
                        from ezdxf.math import Matrix44
                        # ATTRIB positions are already in the INSERT's parent
                        # coordinate system. Apply the cell offset once, then
                        # only the ancestors, never this INSERT matrix again.
                        attribute_matrices = (*matrices, Matrix44.translate(*offset))
                        for attrib in entity.attribs:
                            visit(attrib, attribute_matrices, cell_path, stack)
                        if context_issue:
                            unknown(entity, matrices, cell_path, context_issue + '，不能证明已完整读取适用单位文字', virtual)
                        if block is None or block.block.is_xref or name in stack or len(stack) >= 32:
                            unknown(entity, matrices, cell_path, '未读取cell内外参、缺块、循环或完整父链文字', virtual)
                        else:
                            child_matrices = (*matrices, virtual.matrix44())
                            for child in block:
                                visit(child, child_matrices, cell_path, (*stack, name))
                    except (ValueError, ArithmeticError):
                        unknown(entity, matrices, cell_path, '阵列cell或块实例变换不能核验文字适用范围')
        elif kind == 'DIMENSION':
            try:
                block, offset = _dimension_render_context(doc, entity)
                from ezdxf.math import Matrix44
                render_matrices = (*matrices, Matrix44.translate(*offset))
                for child in block:
                    visit(child, render_matrices, path, stack)
            except (ValueError, ArithmeticError):
                unknown(entity, matrices, path, '尺寸渲染块或其平移/注释上下文尚未核清，不能完成单位文字搜索')

    for entity in doc.modelspace():
        visit(entity)
    return {'method': 'all_layouts_all_definitions_inventory_then_active_Model_instance_scope',
            'text_count': sum(counts.values()), 'by_block_count': counts, 'whole_document_unit_text_hits': all_hits,
            'active_text_count': active_text_count, 'unit_text_matches': hits, 'uninspected_active_text_carriers': uninspected,
            'non_coordinate_unit_texts': noncoordinate,
            'checked_array_cells': checked_array_cells,
            'excluded_outside_frame': excluded, 'status': 'CONFLICT' if hits else 'UNRESOLVED' if uninspected else 'COMPLETE',
            'absence_scope': 'current_frame_active_Model_instances',
            'limitations': ['未使用块定义和其他图页不作本框单位声明；当前框可能承载文字但未读清的对象必须未决']}


def verify_geometric_unit_evidence(request, manifest, scope):
    if not isinstance(request, dict) or request.get('schema_version') != REQUEST_SCHEMA:
        raise ValueError('geometry_evidence.schema_version 必须为 ' + REQUEST_SCHEMA)
    if not str(request.get('basis') or '').strip() or request.get('measurement_semantics') != 'plan_to_scale':
        raise ValueError('须写明 basis 并选择 plan_to_scale；系统图/示意图不能以图长推实际管长')
    raw_unit = request.get('raw_unit')
    metres = canonical_scale(raw_unit)
    if manifest.get('schema_version') != 'qto-cad-neutral-evidence/1.0' or manifest.get('source_identity_issues'):
        raise ValueError('几何单位需要有效且没有源身份冲突的 CAD 中性取证清单')
    for key in ('authoritative_source', 'exchange_dxf'):
        verify_source_file(manifest.get(key), key)
    if manifest.get('exchange_conversion'):
        verify_source_file(manifest['exchange_conversion'], 'exchange_conversion')
        if manifest['exchange_conversion'].get('geometry_review_required'):
            raise ValueError('修复转换副本几何仍待核验，不能建立几何单位凭据')
    doc, diagnostics = read_dxf_with_diagnostics(Path(manifest['exchange_dxf']['path']))
    if int(doc.header.get('$INSUNITS', 0) or 0) != 0 or int((manifest.get('drawing_unit') or {}).get('insunits_code', 0) or 0) != 0:
        raise ValueError('INSUNITS 已有声明；几何单位不得覆盖原声明，冲突须先解决')
    if unit_known((manifest.get('drawing_unit') or {}).get('raw_unit')):
        raise ValueError('清单仍声称已有单位，与源 INSUNITS=0 冲突，须重新取证')
    if diagnostics.get('source_identity_issues') or diagnostics.get('status') == 'PARTIAL':
        raise ValueError('原 DXF 读取或源身份有未决，不能建立几何单位凭据')
    box, width, height = _frame(doc, request.get('frame'))
    _scope(scope, box)
    scale_entity, scale_transform = _ref(doc, request.get('scale_text'))
    if scale_entity.dxftype() not in TEXT_TYPES or not _text_in_frame(scale_entity, scale_transform, box):
        raise ValueError('图签比例须引用当前图框内的真实源文字')
    scale_text = _text(scale_entity).strip()
    match = re.fullmatch(r'(?:比例\s*[:：]?\s*)?1\s*[:：]\s*(\d+(?:\.\d+)?)', scale_text)
    if match is None:
        raise ValueError('图签比例须为唯一 1:N 源文字；多比例/不按比例暂不支持')
    denominator = float(match[1])
    if not math.isfinite(denominator) or denominator <= 0:
        raise ValueError('图签比例分母须为正数')
    paper_sources = _paper_size_evidence(doc, request, box)
    papers = ({'SOURCE_DECLARED': paper_sources[0]['paper_size_mm']} if paper_sources else ISO_A_MM)
    options = []
    for name, sides in papers.items():
        for paper in dict.fromkeys((tuple(sides), tuple(sides[::-1]))):
            ratios = [p * denominator / 1000. / span for p, span in zip(paper, (width, height))]
            if all(abs(v / metres - 1.) <= TOLERANCE + 1e-12 for v in ratios) and abs(ratios[0]/ratios[1]-1.) <= TOLERANCE + 1e-12:
                options.append({'standard': 'source_explicit_paper_size' if paper_sources else 'ISO 216 A-series',
                                'paper': name, 'paper_size_mm': list(paper),
                                'implied_metres_per_unit_by_axis': ratios})
    if len(options) != 1:
        raise ValueError('标准幅面两轴×源图签比例与候选单位不唯一或误差超过1%（不能凭一边吻合）')
    titles = request.get('view_text_refs')
    if not isinstance(titles, list) or not titles:
        raise ValueError('须以 view_text_refs 引用当前比例平面图图名；不能把系统图比例当实际图长')
    title_texts, title_sources = [], []
    for ref in titles:
        entity, transform = _ref(doc, ref)
        if entity.dxftype() not in TEXT_TYPES or not _text_in_frame(entity, transform, box):
            raise ValueError('图名来源不在当前图框内')
        title_texts.append(_text(entity))
        title_sources.append(_source_context(entity, transform))
    if not any('平面' in t for t in title_texts) or any(re.search(r'系统图|系統圖|原理|示意|不按比例', t) for t in title_texts):
        raise ValueError('当前图名未证明按比例平面图，或含系统/示意/原理语义，不能按图长计量')
    refs = request.get('dimension_refs')
    if not isinstance(refs, list) or not refs:
        raise ValueError('仅有图框幅面/比例是一类证据；须引用第二类独立 DIMENSION 几何自洽证据')
    dimensions, used = [], set()
    for ref in refs:
        if not isinstance(ref, dict):
            raise ValueError('dimension_refs 须逐项给出真实源句柄对象')
        entity, transform = _ref(doc, ref)
        identity = transform.source_context['instance_path']
        if entity.dxftype() != 'DIMENSION' or identity in used:
            raise ValueError('第二类证据须为不重复的原 DIMENSION，不能重用图框或图签数值')
        used.add(identity)
        _, render_offset = _dimension_render_context(doc, entity)
        if entity.has_xdata('AcadAnnotative'):
            raise ValueError('注释性尺寸的当前可见比例尚未核清，不能作本页尺度证据')
        facts = dimension_measurement_facts(entity)
        if not facts['linear_measurement_usable'] or facts['text_mode'] != 'automatic':
            raise ValueError('尺寸须为无覆盖的线性尺寸，正DIMLFAC且定义点、缓存/样式无冲突')
        lfac = facts['linear_factor']
        if not isinstance(lfac, (float, int)) or not math.isfinite(lfac) or lfac <= 0:
            raise ValueError('DIMLFAC须为有限正数；负倍率涉及纸空间适用上下文，尚未支持')
        source_points = {k: facts['definition_points_wcs'][k] for k in ('defpoint2', 'defpoint3')}
        a, b = [transform(source_points[k]) for k in ('defpoint2', 'defpoint3')]
        if not all(_inside(p, box, strict=True) for p in (a, b)):
            raise ValueError('独立尺寸定义点须在同一图框内部，不能拿图框边本身循环佐证')
        rendered = facts['rendered_text_candidates']
        if len(rendered) != 1 or re.fullmatch(r'\+?(?:\d+(?:\.\d*)?|\.\d+)', rendered[0]['text'].strip()) is None:
            raise ValueError('须有源文件中已有的唯一纯数值尺寸渲染文字，不能现场生成或自填尺寸数值')
        rendered_entity = doc.entitydb[rendered[0]['entity_handle']]
        _visible(doc, rendered_entity)
        rendered_points = _text_points(rendered_entity, transform, render_offset)
        rendered_point = rendered_points[0]
        if not all(_inside(p, box) for p in rendered_points):
            raise ValueError('原尺寸渲染文字不在当前图框内，不能作本页独立尺寸证据')
        number, source_measured = float(rendered[0]['text']), facts['raw_measurement']
        expected_display = source_measured * lfac
        if not math.isfinite(expected_display) or expected_display <= 0 or number <= 0 or abs(number / expected_display - 1.) > TOLERANCE:
            raise ValueError('原尺寸渲染数值与独立定义点实测×源DIMLFAC不一致（容差1%）')
        origin, axis = transform((0., 0., 0.)), transform((1., 0., 0.))
        instance_scale = math.dist(origin, axis)
        measured = source_measured * instance_scale
        # Displayed values remain the actual source string. INSERT transforms
        # geometry, not the represented physical dimension or its numeric text.
        # A positive source DIMLFAC is accepted only where the unmodified
        # display also agrees with actual world geometry. It is never applied
        # again to world endpoints, the drawing-unit factor or any quantity.
        if not math.isfinite(measured) or measured <= 0 or abs(number / measured - 1.) > TOLERANCE:
            raise ValueError('块内尺寸源显示数值与 INSERT 世界几何尺度不自洽，单位保持未知；不能随实例倍率缩放标注物理值')
        dims = float(entity.override().get('dimscale', doc.header.get('$DIMSCALE', 1.)))
        if not math.isfinite(dims) or dims <= 0:
            raise ValueError('注释比例/自适应 DIMSCALE 尚不支持')
        dimensions.append({'source_ref': ref, 'definition_points_wcs': {'defpoint2': a, 'defpoint3': b},
                           'source_context': _source_context(entity, transform, list(source_points.values())),
                           'source_definition_points': source_points, 'source_measurement': source_measured,
                           'source_display_number': number, 'source_expected_display_number': expected_display,
                           'insert_world_scale': instance_scale,
                           'rendered_text_point_wcs': rendered_point,
                           'rendered_text_points_wcs': rendered_points,
                           'rendered_text_source_placement_points': _text_points(rendered_entity, lambda p: tuple(p)),
                           'dimension_render_offset_ocs': render_offset,
                           'dimension_render_offset_role': 'translate_existing_default_OCS_picture_only_not_definition_or_display_number',
                           'measurement': measured, 'rendered_source': rendered[0], 'dimlfac': lfac, 'dimscale': dims,
                           'dimlfac_role': 'source_display_multiplier_checked_against_world_geometry_not_unit_or_quantity_factor',
                           'dimscale_role': 'annotation_display_only_not_a_length_multiplier',
                           'evidence_class': 'independent_dimension_geometric_consistency', 'physical_unit_proven_independently': False})
    search = _search_unit_texts(doc, box)
    if paper_sources:
        paper_ids = {r['instance_path'] for r in paper_sources}
        matches = search['unit_text_matches']
        if not paper_ids <= {r['canonical_instance_path'] for r in matches}:
            raise ValueError('纸张幅面引用未在当前活动源实例搜索中闭合')
        search['checked_paper_size_matches'] = [dict(r, disposition='source_paper_size_only_not_coordinate_SI')
                                               for r in matches if r['canonical_instance_path'] in paper_ids]
        search['unit_text_matches'] = [r for r in matches if r['canonical_instance_path'] not in paper_ids]
        search['status'] = ('CONFLICT' if search['unit_text_matches'] else
                            'UNRESOLVED' if search['uninspected_active_text_carriers'] else 'COMPLETE')
    # A printed numeric DIM is not an SI definition. Optional coordinate-unit
    # clauses must be real, visible, active source instances inside this frame.
    coordinate_units, cited_instances = [], set()
    refs = request.get('coordinate_unit_refs', [])
    if not isinstance(refs, list):
        raise ValueError('coordinate_unit_refs 须为实际坐标单位源引用数组')
    for ref in refs:
        entity, transform = _ref(doc, ref, allow_array_cells=True)
        if entity.dxftype() not in TEXT_TYPES or not _text_in_frame(entity, transform, box):
            raise ValueError('坐标单位原文须在当前图框内且可见')
        value = _text(entity).strip()
        # Do not interpret dimensions, material quantities or conditional/mixed
        # notes as coordinate declarations. This narrow grammar is intentional.
        if re.fullmatch(r'(?:本图|本圖)?\s*(?:坐标|座標|绘图|繪圖)\s*单位\s*[:：为為]?\s*(?:mm|cm|m|ft|in|毫米|厘米|米|英尺|英寸)\s*[。.]?', value, re.I) is None:
            raise ValueError('须为明确单一坐标/绘图单位原文；尺寸、标高或材料单位不证明坐标 SI')
        verify_unit_declaration_texts([value], raw_unit)
        context = _source_context(entity, transform)
        identity = context['instance_path']
        if identity in cited_instances:
            raise ValueError('坐标单位源实例重复')
        cited_instances.add(identity)
        coordinate_units.append(dict(source_ref=ref, source_text=value, instance_path=identity,
                                     source_context=context,
                                     physical_unit_proven_independently=True))
    if coordinate_units:
        matches = search['unit_text_matches']
        if not cited_instances <= {r['canonical_instance_path'] for r in matches}:
            raise ValueError('坐标单位引用未在当前活动源实例搜索中闭合')
        search['checked_coordinate_unit_matches'] = [r for r in matches if r['canonical_instance_path'] in cited_instances]
        search['unit_text_matches'] = [r for r in matches if r['canonical_instance_path'] not in cited_instances]
        search['status'] = ('CONFLICT' if search['unit_text_matches'] else
                            'UNRESOLVED' if search['uninspected_active_text_carriers'] else 'COMPLETE')
    result = {'schema_version': EVIDENCE_SCHEMA, 'status': 'INFERRED', 'raw_unit': raw_unit,
            'runtime_dependencies': geometric_unit_runtime_dependencies(),
            'metres_per_drawing_unit': metres, 'evidence_level': 'inferred', 'quantity_level': '规则推算量',
            'formal_adoption': False, 'scope': scope, 'request': request,
            'source': {k: manifest[k] for k in ('authoritative_source', 'exchange_dxf')},
            'frame': {**options[0], 'bbox': box, 'polygon_wcs': box.vertices,
                      'source_entities': box.source_entities,
                      'paper_size_source_evidence': paper_sources,
                      'axis_span_basis': 'source_rectangle_edges_in_world_coordinates',
                      'actual_axis_spans': [width, height], 'checked_axes': 2,
                      'scale_text': scale_text, 'scale_source_context': _source_context(scale_entity, scale_transform),
                      'scale_denominator': denominator, 'tolerance': TOLERANCE},
            'dimensions': dimensions, 'unit_text_search': search, 'view_texts': title_texts,
            'view_source_contexts': title_sources,
            'coordinate_unit_evidence': coordinate_units,
            'physical_unit_basis': 'source_coordinate_declaration' if coordinate_units else None,
            'allows_length_measurement': bool(coordinate_units),
            'basis': ('已回源核对本框明确坐标SI声明；' if coordinate_units else
                       '图上无坐标单位声明；局部数值/材料/标高单位未作坐标单位依据；' if search['non_coordinate_unit_texts'] else '图上无单位文字；')
                + '在标准幅面及图签比例假设下由双轴几何推定单位，原尺寸只作独立几何自洽；规则推算，未经人工采用'}
    if search['status'] != 'COMPLETE':
        partial = {**result, 'schema_version': 'qto-geometric-scale-corroboration/1.0', 'status': 'UNIT_UNRESOLVED',
                   'candidate_raw_unit': raw_unit, 'raw_unit': None, 'metres_per_drawing_unit': None,
                   'factor_to_output': None, 'length': None, 'evidence_level': None, 'quantity_level': '未决量',
                   'allows_length_measurement': False,
                   'basis': '仅保存原图框/比例/尺寸几何佐证；单位文字冲突或可能承载适用单位的内容未读清，单位和长度仍未知'}
        reason = ('原图本框仍有单位文字（含材料、图例不同适用对象），不能冒充无单位声明'
                  if search['status'] == 'CONFLICT' else '本框可能承载单位文字的 OLE/代理/外参等内容尚未核清，单位保持未知')
        raise GeometricUnitUnresolved(reason, partial)
    return result


def verify_geometric_unit_consumption(credential, manifest, scope, factor, output_unit='m'):
    """Actual professional consumption: replay source, scope, units and code bytes.

    A geometry-only hypothesis remains useful corroboration but cannot open this
    physical measurement interface. Existing explicit-unit paths are untouched.
    """
    if not isinstance(scope, dict) or not isinstance(credential, dict) or scope != credential.get('scope'):
        raise ValueError('专业实际消费必须显式绑定同一 measurement_scope')
    checked = verify_geometric_unit_credential(credential, scope)
    if checked.get('physical_unit_basis') != 'source_coordinate_declaration' or not checked.get('allows_length_measurement'):
        partial=dict(checked,schema_version='qto-geometric-scale-corroboration/1.0',status='UNIT_UNRESOLVED',
                     candidate_raw_unit=checked.get('raw_unit'),raw_unit=None,metres_per_drawing_unit=None,
                     factor_to_output=None,length=None,quantity_level='未决量',evidence_level=None,
                     allows_length_measurement=False)
        raise GeometricUnitUnresolved('仅图框与 DIM 几何自洽没有独立坐标 SI 依据，单位保持未决',partial)
    ref = credential['cad_manifest']
    original = json.loads(Path(ref['path']).read_text(encoding='utf-8-sig'))
    if original != manifest:
        raise ValueError('几何单位凭据与专业实际读取 CAD 清单版本不一致')
    expected = checked['metres_per_drawing_unit'] / canonical_scale(output_unit)
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not math.isfinite(factor) or not math.isclose(factor, expected, rel_tol=1e-12, abs_tol=0.):
        raise ValueError('专业单位倍率与回源凭据不一致，不能混入根数或长度系数')
    dependencies = [*checked['runtime_dependencies'], ref,
                    *[v for k, v in manifest.items() if k in ('authoritative_source', 'exchange_dxf', 'exchange_conversion') and isinstance(v, dict)],
                    *[v for v in manifest.get('artifacts', {}).values() if isinstance(v, dict) and 'path' in v]]
    fingerprints = {}
    for value in dependencies:
        row = dict(value)
        path = Path(row['path'])
        if not path.is_absolute():
            path = Path(ref['path']).parent / path
        row['path'] = str(path.resolve())
        verify_source_file(row, '几何单位实际消费依赖')
        fingerprints[row['path']] = row['sha256']
    return dict(schema_version='qto-geometric-unit-consumption/1.0',
                credential=credential, checked_evidence=checked, scope=scope,
                factor_to_output=expected, output_unit=output_unit,
                dependency_fingerprints=fingerprints, formal_adoption=False)


def verify_geometric_unit_credential(credential, expected_scope=None):
    """Recompute from source, never trust an evidence file's self-reported PASS/scale."""
    if not isinstance(credential, dict) or credential.get('schema_version') != EVIDENCE_SCHEMA:
        raise ValueError('无效几何单位凭据')
    verify_geometric_unit_runtime_identity(credential)
    ref = credential.get('cad_manifest')
    verify_source_file(ref, '几何单位 CAD 清单')
    manifest = json.loads(Path(ref['path']).read_text(encoding='utf-8-sig'))
    scope = credential.get('scope')
    if expected_scope is not None and scope != expected_scope:
        raise ValueError('几何单位凭据 scope 与本次实际消费范围不一致')
    result = verify_geometric_unit_evidence(credential.get('request'), manifest, scope)
    # Keep the original producer code locations after proving equal executing
    # bytes, so a byte-identical checkout can replay the frozen result exactly.
    result['runtime_dependencies'] = credential['runtime_dependencies']
    return result


def main():
    parser = argparse.ArgumentParser(description='从原DXF生成可复核的几何单位推定凭据（不生成工程量）')
    for name in ('cad-manifest', 'request', 'scope', 'output'):
        parser.add_argument('--'+name, required=True, type=Path)
    args = parser.parse_args()
    protected = [args.cad_manifest, args.request, args.scope]
    try:
        manifest = json.loads(args.cad_manifest.read_text(encoding='utf-8-sig'))
        protected.extend(Path(manifest[k]['path']) for k in ('authoritative_source', 'exchange_dxf'))
        if manifest.get('exchange_conversion'):
            protected.append(Path(manifest['exchange_conversion']['path']))
        protected.extend(Path(ref['path']) for ref in (manifest.get('artifacts') or {}).values() if isinstance(ref, dict) and ref.get('path'))
        for source in protected:
            if args.output.resolve() == source.resolve() or (args.output.exists() and source.exists() and args.output.samefile(source)):
                raise ValueError('输出不得覆盖或经链接覆盖原图/请求/范围/清单')
        request = json.loads(args.request.read_text(encoding='utf-8-sig'))
        scope = json.loads(args.scope.read_text(encoding='utf-8-sig'))
        checked = verify_geometric_unit_evidence(request, manifest, scope)
        checked['cad_manifest'] = {'path': str(args.cad_manifest.resolve()), 'sha256': _sha(args.cad_manifest)}
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(checked, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(json.dumps({'status': checked['status'], 'raw_unit': checked['raw_unit'], 'output': str(args.output)}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        if getattr(exc, 'partial_evidence', None) is not None:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(exc.partial_evidence, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
