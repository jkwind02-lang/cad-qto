"""Source-bound boxed callouts, with explicit drawing-specific tolerances.

This helper does not infer engineering meaning or installation quantities.
Coordinates and optional tolerances use the common parent's coordinate system.
"""
import math
import re
from measure_pipe_detail_routes import Drawing, require
from qto_cad_geometry import entity_geometry, transform_point


def _sub(a, b):
    return [x-y for x, y in zip(a, b)]


def _dot(a, b):
    return sum(x*y for x, y in zip(a, b))


def _epsilon(points):
    # Floating point resolution only, not an engineering drafting tolerance.
    extent = max((math.dist(points[0], p) for p in points), default=0.)
    return max(extent*1e-12, max(math.ulp(v) for p in points for v in p)*16)


def _rectangle(entity):
    if not entity.closed or len(entity) != 4 or any(p[4] for p in entity.get_points()):
        return None
    points = [list(p) for p in entity.vertices_in_wcs()]
    eps = _epsilon(points)
    u, v = _sub(points[1], points[0]), _sub(points[3], points[0])
    w, h = math.sqrt(_dot(u, u)), math.sqrt(_dot(v, v))
    if min(w, h) <= eps or abs(_dot(u, v)) > eps*max(w, h):
        return None
    opposite = [o+x+y for o, x, y in zip(points[0], u, v)]
    if math.dist(opposite, points[2]) > eps:
        return None
    u, v = [x/w for x in u], [x/h for x in v]
    normal = [u[1]*v[2]-u[2]*v[1], u[2]*v[0]-u[0]*v[2], u[0]*v[1]-u[1]*v[0]]
    return points[0], u, v, normal, w, h, eps


def _project(frame, point):
    origin, u, v, normal, *_ = frame
    d = _sub(point, origin)
    return _dot(d, u), _dot(d, v), abs(_dot(d, normal))


def _boundary_gap(x, y, w, h):
    if 0 <= x <= w and 0 <= y <= h:
        return min(x, y, w-x, h-y)
    return math.hypot(max(-x, 0, x-w), max(-y, 0, y-h))


def bind_callouts(doc, parent_path, instance_paths, label_pattern, box_layer=None,
                  endpoint_tolerance=0., gap_limit_raw=0., text_height_fraction=0.,
                  tolerance_basis=None):
    """Return all binding states; gaps require a caller-supplied tolerance basis.

    Defaults require source geometry contact (up to floating point resolution).
    Explicit raw limits are in parent coordinates, not world/drawing units.
    If both gap bounds are supplied the smaller one applies. A nonzero gap never
    establishes a pipe connection; this function only associates a caption.
    """
    require(all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0
                for v in (endpoint_tolerance, gap_limit_raw, text_height_fraction)), '引线容差无效')
    require(text_height_fraction <= 1, '文字高度比例须在0到1之间')
    if any((endpoint_tolerance, gap_limit_raw, text_height_fraction)):
        require(isinstance(tolerance_basis, str) and tolerance_basis.strip(), '非零容差须说明本次图纸依据')
    require(isinstance(parent_path, list), '父路径必须是列表')
    require(isinstance(instance_paths, list) and instance_paths, '没有待绑定实物')
    drawing = Drawing(doc)
    if parent_path:
        root, _ = drawing.resolve(parent_path)
        require(root.dxftype() == 'INSERT', '父路径必须是块实例')
        require(root.dxf.row_count == root.dxf.column_count == 1, '阵列父块须先展开')
        block = doc.blocks[root.dxf.name]
    else:
        block = doc.modelspace()
    pattern = re.compile(label_pattern)
    labels = []
    for e in block.query('TEXT MTEXT'):
        value = e.dxf.text if e.dxftype() == 'TEXT' else e.plain_text()
        if pattern.fullmatch(value):
            labels.append((e, value, e.dxf.height if e.dxftype() == 'TEXT' else e.dxf.char_height))
    leaders = [(e, [list(e.dxf.start), list(e.dxf.end)]) for e in block.query('LINE')]
    boxes = []
    for e in block.query('LWPOLYLINE'):
        if not e.closed and len(e) >= 2 and not any(p[4] for p in e.get_points()):
            pts = [list(p) for p in e.vertices_in_wcs()]
            leaders.append((e, [pts[0], pts[-1]]))
        elif box_layer is None or e.dxf.layer == box_layer:
            frame = _rectangle(e)
            if frame:
                boxes.append((e, frame))
    seen, results = set(), []
    for path in instance_paths:
        require(path[:-1] == parent_path and tuple(path) not in seen, '实物路径不在指定父块或重复')
        seen.add(tuple(path))
        instance, matrix = drawing.resolve(path)
        require(instance.dxftype() == 'INSERT', '实物须为块实例')
        require(instance.dxf.row_count == instance.dxf.column_count == 1, '阵列块不能作为一个物理实例绑定')
        loc = entity_geometry(instance)['anchor']
        candidates = []
        for leader, ends in leaders:
            distances = [math.dist(loc, p) for p in ends]
            leader_eps = _epsilon([loc]+ends)
            eps = leader_eps
            if min(distances) > endpoint_tolerance+eps or max(distances) <= endpoint_tolerance+eps:
                continue
            tip = ends[distances.index(max(distances))]
            for box, frame in boxes:
                _, _, _, _, w, h, box_eps = frame
                eps = max(leader_eps, box_eps)
                x, y, z = _project(frame, tip)
                if z > endpoint_tolerance+eps:
                    continue
                gap = _boundary_gap(x, y, w, h)
                for label, value, height in labels:
                    limits = ([gap_limit_raw] if gap_limit_raw else []) + ([height*text_height_fraction] if text_height_fraction else [])
                    limit = min(limits) if limits else 0.
                    lx, ly, lz = _project(frame, entity_geometry(label)['anchor'])
                    if -eps <= lx <= w+eps and -eps <= ly <= h+eps and lz <= endpoint_tolerance+eps and gap <= limit+eps:
                        candidates.append(dict(label_path=parent_path+[label.dxf.handle], text=value,
                            leader_path=parent_path+[leader.dxf.handle], box_path=parent_path+[box.dxf.handle],
                            leader_box_gap_raw=gap, gap_limit_raw=limit, endpoint_gap_raw=min(distances),
                            endpoint_tolerance_raw=endpoint_tolerance, numerical_epsilon_raw=eps,
                            tolerance_basis=tolerance_basis, coordinate_space='parent'))
        unique = {tuple(c['label_path']): c for c in candidates}
        results.append(dict(instance_path=path, location_wcs=transform_point(loc, matrix),
                            state='bound' if len(unique) == 1 else 'ambiguous' if unique else 'unmatched',
                            candidates=list(unique.values())))
    claims = {}
    for row in results:
        if row['state'] == 'bound':
            claims.setdefault(tuple(row['candidates'][0]['label_path']), []).append(row)
    for rows in claims.values():
        if len(rows) > 1:
            for row in rows:
                row['state'] = 'shared_caption_requires_review'
    return results
