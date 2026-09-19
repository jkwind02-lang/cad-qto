"""Deterministic spatial candidate queries; proximity is never engineering evidence."""
from __future__ import annotations

import heapq
import itertools
import math
from collections import defaultdict


def point(value):
    if not isinstance(value, (list, tuple)) or len(value) not in (2, 3):
        raise ValueError('坐标须为二维或三维数组')
    if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in value):
        raise ValueError('坐标须为有限数值')
    return tuple(map(float, value))


class PointIndex:
    """Uniform grid with an exact-distance final test and bounded cell traversal."""
    def __init__(self, points, cell_size, groups=None):
        self.points = [point(p) for p in points]
        self.dim = len(self.points[0]) if self.points else 3
        if any(len(p) != self.dim for p in self.points):
            raise ValueError('同一空间索引坐标维度不一致')
        if isinstance(cell_size, bool) or not math.isfinite(cell_size) or cell_size <= 0:
            raise ValueError('网格边长须大于零')
        self.cell_size = float(cell_size)
        self.groups = list(groups) if groups is not None else [None] * len(self.points)
        if len(self.groups) != len(self.points):
            raise ValueError('分区数与点数不一致')
        self.cells = defaultdict(list)
        self.group_ids = defaultdict(list)
        self.distance_checks = 0
        for i, (p, g) in enumerate(zip(self.points, self.groups)):
            self.cells[(g, self._cell(p))].append(i)
            self.group_ids[g].append(i)

    def _cell(self, p):
        return tuple(math.floor(x / self.cell_size) for x in p)

    def query(self, p, radius, group=None):
        p = point(p)
        if len(p) != self.dim or isinstance(radius, bool) or not math.isfinite(radius) or radius < 0:
            raise ValueError('索引查询坐标或半径无效')
        lo = self._cell([x - radius for x in p])
        hi = self._cell([x + radius for x in p])
        count = math.prod(b-a+1 for a,b in zip(lo,hi))
        if count > max(4096, len(self.group_ids.get(group, []))*4):
            ids = self.group_ids.get(group, [])
        else:
            ids = itertools.chain.from_iterable(self.cells.get((group, c), ()) for c in itertools.product(*(range(a,b+1) for a,b in zip(lo,hi))))
        result = []
        for i in ids:
            self.distance_checks += 1
            if math.dist(p, self.points[i]) <= radius:
                result.append(i)
        return sorted(result)

    def pairs(self, radius):
        for i, (p,g) in enumerate(zip(self.points,self.groups)):
            for j in self.query(p, radius, g):
                if j > i:
                    yield i,j


def nearby_pairs(points, tolerance, groups=None):
    values = list(points)
    if not values:
        return
    index = PointIndex(values, max(tolerance, 1e-9), groups)
    yield from index.pairs(tolerance)


def box_distance(p, box):
    d = len(p)
    return math.sqrt(sum(max(box[i]-p[i], 0., p[i]-box[i+d])**2 for i in range(d)))


class BoundsIndex:
    """Median-split AABB tree. Callers perform the exact curve test themselves."""
    def __init__(self, entries):
        self.entries = list(entries)  # (id, minX,minY,minZ,maxX,maxY,maxZ)
        self.boxes = {}
        for key,b in self.entries:
            if key in self.boxes or len(b) not in (4,6) or any(not isinstance(x,(int,float)) or isinstance(x,bool) or not math.isfinite(x) for x in b):
                raise ValueError('包围盒或对象编号无效')
            d = len(b)//2
            if any(b[i]>b[i+d] for i in range(d)):
                raise ValueError('包围盒上下界倒置')
            self.boxes[key] = tuple(b)
        if len({len(b) for b in self.boxes.values()}) > 1:
            raise ValueError('索引包围盒维度混合')
        self.dim = len(self.entries[0][1])//2 if self.entries else 3
        self.checks = 0
        def build(items):
            if not items: return None
            d = self.dim
            box = tuple(min(b[i] for _,b in items) for i in range(d)) + tuple(max(b[i+d] for _,b in items) for i in range(d))
            if len(items) <= 8: return (box, items, None, None)
            axis = max(range(d),key=lambda i:box[i+d]-box[i])
            items = sorted(items,key=lambda x:((x[1][axis]+x[1][axis+d])/2,str(x[0])))
            mid = len(items)//2
            return (box, None, build(items[:mid]), build(items[mid:]))
        self.root = build(self.entries)

    def query_box(self, box):
        d = self.dim
        if len(box) != 2*d: raise ValueError('包围盒查询维度不一致')
        def intersects(a,b): return all(a[i] <= b[i+d] and b[i] <= a[i+d] for i in range(d))
        stack = [self.root] if self.root else []
        result = []
        while stack:
            node = stack.pop(); self.checks += 1
            if not intersects(node[0], box): continue
            if node[1] is not None:
                result.extend(k for k,b in node[1] if intersects(b,box))
            else: stack.extend((node[2],node[3]))
        return sorted(result,key=str)

    def nearest(self, p, exact_distance, limit=2):
        p = point(p)
        if len(p)!=self.dim: raise ValueError('最近对象查询维度不一致')
        if limit < 1: raise ValueError('limit须大于零')
        sequence = itertools.count(); heap = []; best = []
        if self.root: heapq.heappush(heap,(box_distance(p,self.root[0]),next(sequence),self.root))
        while heap:
            lower,_,node = heapq.heappop(heap); self.checks += 1
            if len(best)>=limit and lower > best[-1][0]+1e-10: break
            if node[1] is not None:
                for k,b in node[1]:
                    if len(best)>=limit and box_distance(p,b)>best[-1][0]+1e-10: continue
                    dist = exact_distance(k)
                    if not math.isfinite(dist) or dist<0: raise ValueError('精确距离函数返回无效值')
                    best.append((dist,k)); best.sort(key=lambda v:(v[0],str(v[1]))); best = best[:limit]
            else:
                for child in node[2:]:
                    heapq.heappush(heap,(box_distance(p,child[0]),next(sequence),child))
        return best
