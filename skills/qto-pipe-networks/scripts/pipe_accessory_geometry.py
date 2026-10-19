"""Actual instance outlines for serial-gap evidence; AABBs only locate candidates."""
import math


def instance_shapes(name, definitions, origins, maximum, matrix, geometry, cache):
    def collect(block, stack):
        if block in stack or len(stack)>=maximum or block not in definitions:
            return [],[],False
        if block in cache: return cache[block]
        shapes,points,complete=[],[],True
        for row in definitions[block]:
            if row.get('entity_type')=='INSERT':
                child=row.get('block_name')
                nested,ports,ok=collect(child,(*stack,block))
                transform=geometry.matrix_for_insert(row,origins.get(child,[0,0,0]))
                shapes.extend({**shape,'vertices':[geometry.transform_point(p,transform) for p in shape['vertices']]} for shape in nested)
                points.extend(geometry.transform_point(p,transform) for p in ports)
                complete=complete and ok
            elif row.get('entity_type')=='POINT':
                if all(isinstance(row.get(k), (int,float)) for k in ('x','y')):
                    points.append([row['x'],row['y'],row.get('z') or 0.])
            else:
                g=row.get('geometry') or {};parts=g.get('parts',[])
                if g.get('closed') and len(parts)>=3 and all(p.get('kind')=='line' for p in parts):
                    shapes.append({'kind':'closed_polygon','source_shape_id':block+'/'+str(row.get('record_id')),
                                   'vertices':[p['start'] for p in parts]})
                elif row.get('entity_type') not in {'TEXT','MTEXT','ATTRIB','ATTDEF'}:
                    complete=False
        result=shapes,points,complete
        cache[block]=result
        return result
    shapes,ports,complete=collect(name,())
    return ({'shapes':[{**s,'vertices':[geometry.transform_point(p,matrix) for p in s['vertices']]} for s in shapes],
             'point_candidates':[geometry.transform_point(p,matrix) for p in ports],
             'complete_source_outline':complete})


def polygon_axis_intervals(vertices, origin, axis, gap):
    def sub(a,b):return [x-y for x,y in zip(a,b)]
    def dot(a,b):return sum(x*y for x,y in zip(a,b))
    def cross(a,b):return [a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]]
    if not isinstance(vertices,list) or len(vertices)<3: raise ValueError('覆盖轮廓须有至少3个实际顶点')
    pts=[]
    for p in vertices:
        if not isinstance(p,list) or len(p)!=3 or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in p):
            raise ValueError('覆盖轮廓须为有限三维WCS顶点')
        pts.append(p)
    normal=None
    for i in range(1,len(pts)-1):
        n=cross(sub(pts[i],pts[0]),sub(pts[i+1],pts[0]));size=math.sqrt(dot(n,n))
        if size>1e-12: normal=[v/size for v in n];break
    if normal is None:return []
    if abs(dot(axis,normal))>1e-8 or abs(dot(sub(origin,pts[0]),normal))>1e-8:return []
    if any(abs(dot(sub(p,pts[0]),normal))>1e-8 for p in pts):return []
    perpendicular=cross(normal,axis)
    points=[(dot(sub(p,origin),axis),dot(sub(p,origin),perpendicular)) for p in pts]
    cuts=[]
    for a,b in zip(points,points[1:]+points[:1]):
        if abs(a[1])<=1e-10:cuts.append(a[0])
        if (a[1]<0<b[1]) or (b[1]<0<a[1]):cuts.append(a[0]+(b[0]-a[0])*(-a[1])/(b[1]-a[1]))
    cuts=sorted(set(cuts))
    def inside(x):
        state=False
        for a,b in zip(points,points[1:]+points[:1]):
            if abs(a[1])<=1e-10 and abs(b[1])<=1e-10 and min(a[0],b[0])-1e-10<=x<=max(a[0],b[0])+1e-10:return True
            if (a[1]>0)!=(b[1]>0) and x < a[0]+(b[0]-a[0])*(-a[1])/(b[1]-a[1]):state=not state
        return state
    return [[max(0.,a),min(gap,b)] for a,b in zip(cuts,cuts[1:]) if b>a and inside((a+b)/2) and min(gap,b)>max(0.,a)]
