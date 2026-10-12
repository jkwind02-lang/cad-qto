"""Consume declared point handoffs; never infer physical electrical endpoints."""
from __future__ import annotations
import copy
import hashlib
import json
import math
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'_qto-shared-evidence/scripts'))
from qto_cad_source_contract import verify_source_file,canonical_scale
from model_recognition_basis import validate_working_basis


def text(v,label):
    if not isinstance(v,str) or not v.strip():raise ValueError(label+' must be non-empty text')
    return v.strip()


def count(v,label):
    if not isinstance(v,int) or isinstance(v,bool) or v<0:raise ValueError(label+' must be a nonnegative integer')
    return v


def rows(values,label):
    if not isinstance(values,list):raise ValueError(label+' must be an array')
    out={}
    for row in values:
        if not isinstance(row,dict):raise ValueError(label+' row must be an object')
        tid=text(row.get('terminal_id'),label+'.terminal_id')
        if tid in out:raise ValueError('duplicate terminal identity: '+tid)
        out[tid]=row
    return out


def normalized_terminal(row):
    value=copy.deepcopy(row)
    for key in ('terminal_id','physical_id','port_id','system'):
        value[key]=text(value.get(key),'terminal.'+key)
    p=value.get('point')
    if not isinstance(p,list) or len(p) not in (2,3) or any(isinstance(n,bool) or not isinstance(n,(float,int)) or not math.isfinite(n) for n in p):
        raise ValueError('terminal point must be finite 2D/3D coordinates')
    unit=value.get('coordinate_unit')
    if unit is not None:
        scale=canonical_scale(unit);value['point']=[n*scale for n in p];value['coordinate_unit']='m'
    return value


def receive(data,paths,items,base_dir='.'):
    ref=data.get('source_handoff')
    if ref is None:
        if data.get('terminals'):
            raise ValueError('declared terminals require a verifiable source_handoff; they cannot be silently ignored')
        return None
    if not isinstance(ref,dict):raise ValueError('source_handoff must be a file identity object')
    path=Path(text(ref.get('path'),'source_handoff.path'))
    if not path.is_absolute():path=Path(base_dir)/path
    path=path.resolve();verify_source_file({'path':str(path),'sha256':ref.get('sha256')},'electrical endpoint handoff')
    source=json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(source,dict) or source.get('schema_version') not in ['qto-network-endpoint-handoff/1.0','qto-network-endpoint-handoff/1.1']:
        raise ValueError('unsupported endpoint handoff schema')
    if source.get('consumer_skill')!='qto-electrical-routes':raise ValueError('handoff belongs to another consumer')
    if source.get('provider_skill')!='qto-components-points':raise ValueError('unsupported endpoint provider')
    validate_working_basis(source.get('recognition_basis'))
    delivered=rows(source.get('terminals'),'source terminals');provided=rows(data.get('terminals'),'provided terminals')
    if set(delivered)!=set(provided):raise ValueError('provided terminals do not match delivered source identities')
    source_count=count(source.get('source_instance_count'),'source_instance_count')
    delivered_count=count(source.get('delivered_count'),'delivered_count')
    unresolved=count(source.get('unresolved_count'),'unresolved_count')
    na=count(source.get('not_applicable_count'),'not_applicable_count')
    if delivered_count!=len(delivered) or source_count!=delivered_count+unresolved+na:
        raise ValueError('source endpoint counts do not reconcile')
    port_count=count(source.get('source_endpoint_port_count',source_count),'source_endpoint_port_count')
    if port_count!=source_count:raise ValueError('source port count does not reconcile')
    dispositions=source.get('dispositions')
    if not isinstance(dispositions,list) or len(dispositions)!=source_count:raise ValueError('source port dispositions are incomplete')
    disposition_keys=set();actual={'deliver':0,'unresolved':0,'not_applicable':0};physical=set();delivered_keys=set()
    for d in dispositions:
        if not isinstance(d,dict):raise ValueError('source disposition must be an object')
        key=(text(d.get('physical_id'),'physical_id'),text(d.get('port_id'),'port_id'))
        if key in disposition_keys:raise ValueError('duplicate source physical port disposition')
        disposition_keys.add(key);physical.add(key[0]);kind=d.get('network_disposition')
        if kind not in actual:raise ValueError('invalid source disposition')
        actual[kind]+=1
        if kind=='deliver':delivered_keys.add(key)
    if actual!={'deliver':delivered_count,'unresolved':unresolved,'not_applicable':na}:raise ValueError('source dispositions and counts disagree')
    if count(source.get('source_physical_instance_count',len(physical)),'source_physical_instance_count')!=len(physical):
        raise ValueError('source physical instance count does not reconcile')
    if {(text(t.get('physical_id'),'physical_id'),text(t.get('port_id'),'port_id')) for t in delivered.values()}!=delivered_keys:
        raise ValueError('delivered terminal physical ports do not match dispositions')
    used_paths={pid for item in items for pid in item['path_ids']}
    graph_nodes={node for pid in used_paths for node in (paths[pid]['from_node'],paths[pid]['to_node'])}
    bound=[];unbound=[];coordinate_checked=[];without_coordinates=[]
    for tid,row in provided.items():
        before=normalized_terminal(delivered[tid]);after=normalized_terminal(row)
        # Identity and all declared semantics must come from the saved provider.
        # Physical unit migration is allowed, but it cannot rename another port.
        bp=before.pop('point');ap=after.pop('point')
        if before!=after or len(bp)!=len(ap) or any(not math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-9) for a,b in zip(ap,bp)):
            raise ValueError('terminal facts differ from source handoff: '+tid)
        if tid not in graph_nodes:
            unbound.append(tid);continue
        bound.append(tid);points=[]
        for pid in used_paths:
            p=paths[pid]
            for node_key,point_key in [('from_node','from_point_m'),('to_node','to_point_m')]:
                if p[node_key]==tid and point_key in p:
                    q=p[point_key]
                    if not isinstance(q,list) or len(q)!=len(ap) or any(isinstance(n,bool) or not isinstance(n,(int,float)) or not math.isfinite(n) for n in q):
                        raise ValueError('path endpoint coordinate is invalid: '+pid)
                    points.append(q)
        if points:
            if row.get('coordinate_unit') is None:raise ValueError('source terminal coordinate unit is missing')
            if any(any(not math.isclose(a,b,rel_tol=1e-12,abs_tol=1e-9) for a,b in zip(ap,q)) for q in points):
                raise ValueError('path endpoint coordinate differs from terminal: '+tid)
            coordinate_checked.append(tid)
        else:without_coordinates.append(tid)
    if 'delivered_count' in ref and count(ref['delivered_count'],'declared delivered_count')!=delivered_count:
        raise ValueError('declared delivered count differs from provider')
    if 'received_count' in ref and count(ref['received_count'],'declared received_count')!=len(bound):
        raise ValueError('declared received count differs from graph-bound terminals')
    complete=source.get('source_workpackage_complete',True)
    if not isinstance(complete,bool):raise ValueError('source_workpackage_complete must be boolean')
    if not isinstance(source.get('required'),bool):raise ValueError('source required must be boolean')
    return dict(schema='qto-electrical-endpoint-receipt/1.0',handoff_id=source.get('handoff_id'),
        source_path=str(path),source_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),source_physical_instance_count=len(physical),
        source_endpoint_port_count=source_count,delivered_count=delivered_count,received_count=len(bound),
        source_unresolved_count=unresolved,not_applicable_count=na,unbound_terminal_ids=unbound,bound_terminal_ids=bound,
        coordinate_checked_terminal_ids=coordinate_checked,coordinate_unchecked_terminal_ids=without_coordinates,
        identity_receipt_complete=complete and unresolved==0 and not unbound,
        coordinate_receipt_complete=not without_coordinates and not unbound,
        recognition_basis=source.get('recognition_basis'),business_adoption_allowed=False,
        meaning='Fresh provider port facts matched to nodes of consumed paths; endpoint identity/coordinates do not prove drawing recognition or installed terminals')
