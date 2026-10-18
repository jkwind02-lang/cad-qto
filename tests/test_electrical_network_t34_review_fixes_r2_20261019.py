"""FIRE-01/02/04: bounded source-backed regression, reusing R1 extraction helpers."""
import copy
import importlib
import json
import os
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get('CAD_QTO_T34_R1_SOURCE', ROOT))
RUNTIME = Path(os.environ.get('CAD_QTO_T34_R2_RUNTIME', ROOT))
sys.path.insert(0, str(BASE / 'tests'))
import test_electrical_network_roles_t34_20261019 as old
if 'CAD_QTO_T34_R2_RUNTIME' in os.environ:
    for module in ('infer_electrical_network_roles', 'match_electrical_network_expressions',
                   'partition_electrical_route_network', 'bind_electrical_objects', 'qto_electrical_evidence'):
        sys.modules.pop(module, None)
sys.path.insert(0, str(RUNTIME / 'skills/qto-electrical-routes/scripts'))
role = importlib.import_module('infer_electrical_network_roles')
match = importlib.import_module('match_electrical_network_expressions')


@pytest.fixture(scope='module')
def paired(tmp_path_factory):
    folder = tmp_path_factory.mktemp('t34_r2_pair')
    expressions=[]; results=[]
    for label, second in [('PRIMARY-SHEET', False), ('SECONDARY-SHEET', True)]:
        mf, request, basis, _ = old.fixture(folder/label, second_shape=second)
        request['network_request']['sheets'][0]['sheet_id'] = label
        request['connection_contexts'][0]['sheet_id'] = label
        request['is_region_complete'] = False
        request['network_request']['scope_statement'] = '只处理本层局部两设备已画路径；不证明整图完整'
        result = role.infer_roles(mf, request)
        rp = folder/label/'角色结果.json';role.write(rp,result)
        expressions.append(old.expression(label,result,rp,basis));results.append(result)
    req = dict(schema=match.REQUEST_SCHEMA, expressions=expressions, decisions=[])
    old.decision(req,*results)
    req['decisions'][0]['primary_expression']='PRIMARY-SHEET'
    req['decisions'][0]['secondary_expression']='SECONDARY-SHEET'
    return folder, req, results


def mutate_result(tmp_path, request, index, mutate):
    request=copy.deepcopy(request)
    expression=request['expressions'][index]
    result=role.read(expression['role_result']['path']);mutate(result)
    path=tmp_path/f'表达{index}.json';role.write(path,result)
    expression['role_result']=dict(path=str(path),sha256=old.sha(path))
    request['decisions'][0]['role_result_sha256'][expression['id']]=old.sha(path)
    return request


def test_distinct_real_source_sheets_are_comparable_and_preserved(paired):
    _,request,_=paired
    result=match.match_expressions(request)
    assert result['candidates'][0]['conflicts']==[]
    assert len(result['decisions'])==1
    assert {e['sheet_id']for e in result['expressions']}=={'PRIMARY-SHEET','SECONDARY-SHEET'}
    assert all(e['source_sheet']['sheet_id']==e['sheet_id']for e in result['expressions'])
    assert sum(i['known_length_m']for i in result['reference_calculation']['items'])==pytest.approx(20)
    assert all(c['no_edge_lost']for c in result['coverage'])


def test_forged_common_sheet_id_does_not_bypass_own_source_sheet(paired,tmp_path):
    _,request,_=paired
    request=mutate_result(tmp_path,request,1,lambda r:r['connections'][0]['context'].update(sheet_id='PRIMARY-SHEET'))
    with pytest.raises(ValueError,match='自身原图'):
        match.match_expressions(request)


def test_real_sheet_name_with_false_bounds_is_rejected(paired,tmp_path):
    _,request,_=paired
    request=mutate_result(tmp_path,request,1,lambda r:r['generated_network_request']['sheets'][0].update(bounds=[100,100,101,101]))
    with pytest.raises(ValueError,match='边界'):
        match.match_expressions(request)


@pytest.mark.parametrize('field',['floor','revision','scope_id','system'])
def test_engineering_context_conflicts_remain_closed(paired,tmp_path,field):
    _,request,_=paired
    def change(result):
        if field=='system':
            result['connections'][0]['system']='DIFFERENT'
        else:
            result['connections'][0]['context'][field]='DIFFERENT'
            if field=='floor':result['generated_network_request']['sheets'][0]['floor']='DIFFERENT'
    request=mutate_result(tmp_path,request,1,change)
    result=match.match_expressions(request)
    assert field in result['candidates'][0]['conflicts']
    assert result['decisions']==[]
    assert all(d['destination']=='retained_independent_working_expression'for d in result['edge_dispositions'])


@pytest.fixture(scope='module')
def code_cases(tmp_path_factory):
    folder=tmp_path_factory.mktemp('t34_r2_codes')
    mf,request,basis,lines=old.fixture(folder/'源',branch=True)
    spec=request['network_request']['circuit_specs'][0]
    spec.update(kind='wire',system='报警',spec='匿名双绞线',parallel_count=2,extras=[],pending_components=[])
    evidence=role.evidence_for(mf,request['network_request'])
    other=next(t['source_id']for t in evidence.texts if t['text']=='3K')
    conflict=copy.deepcopy(request)
    conflict['text_edge_bindings'].append(dict(id='CONFLICT',text_source_id=other,expression_id='K3',
        source_ranges=copy.deepcopy(request['text_edge_bindings'][0]['source_ranges']),basis_source_ids=[basis]))
    return folder,mf,request,conflict


def test_conflict_invalidates_existing_global_specs_only_on_affected_atoms(code_cases):
    _,mf,_,request=code_cases
    result=role.infer_roles(mf,request)
    conflict_edges={a['id']for a in result['edges']if a['code_status']=='CONFLICT'}
    assert len(conflict_edges)==1
    assert sum(a['length_m']for a in result['network_result']['physical_segments'])==pytest.approx(14)
    q=result['network_result']['circuit_quantities'][0]
    assert q['complete_length_m'] is None
    assert q['known_length_m']==pytest.approx(18)  # unaffected right branch: (5+4)m * explicit 2
    assert all(p['spec'] is None and p['parallel_count'] is None for p in q['segment_parameters']if p['path_id']in conflict_edges)
    assert all(not(set(c['path_ids'])&conflict_edges)for c in result['network_result']['ledger']['circuits'])
    assert len(result['network_result']['ledger']['circuits'])==2


def test_unbound_nearby_other_code_does_not_invalidate_source_parameters(code_cases):
    _,mf,request,_=code_cases
    result=role.infer_roles(mf,request)
    assert not any(a['code_status']=='CONFLICT'for a in result['edges'])
    assert result['network_result']['circuit_quantities'][0]['complete_length_m']==pytest.approx(28)


def test_conflict_reaches_real_existing_t35_child_exit_and_quantities(code_cases,tmp_path):
    _,mf,_,request=code_cases
    path=tmp_path/'请求.json';role.write(path,request)
    done=old.run([RUNTIME/'skills/qto-electrical-routes/scripts/infer_electrical_network_roles.py',
        '--cad-manifest',mf,'--request',path,'--output-dir',tmp_path/'结果'])
    assert done.returncode==3,done.stdout+done.stderr
    assert role.read(tmp_path/'结果/实际调用回执.json')['calls'][0]['returncode']==3
    child=role.read(tmp_path/'结果/既有网络计算/混连网络拆分结果.json')
    assert child['circuit_quantities'][0]['complete_length_m'] is None
    assert child['circuit_quantities'][0]['known_length_m']==pytest.approx(18)


def test_legacy_conflict_rows_cannot_be_reexported_by_match(paired,tmp_path):
    _,request,_=paired
    request=mutate_result(tmp_path,request,0,lambda r:[a.update(code_status='CONFLICT')for a in r['edges']])
    result=match.match_expressions(request)
    assert result['decisions']==[]
    assert not any(i['expression_id']=='PRIMARY-SHEET'for i in result['selected_working_ledger']['circuits'])
    primary=next(c for c in result['coverage']if c['expression_id']=='PRIMARY-SHEET')
    assert primary['unresolved_calculation_edge_ids']==primary['all_edge_ids']


def test_role_unit_and_partial_scope_are_visible_in_final_normalized_html(paired,tmp_path):
    folder,_,_=paired
    # Reuse the actual original request source/points; no new extraction or full-chain rerun.
    mf=folder/'PRIMARY-SHEET/中性事实/CAD中性取证清单.json'
    result=role.read(folder/'PRIMARY-SHEET/角色结果.json')
    output=tmp_path/'投影';output.mkdir()
    (output/'既有网络计算').mkdir()
    role.write(output/'既有网络计算/路径台账.json',result['network_result']['ledger'])
    role.net.export_review(mf,result['generated_network_request'],result['network_result'],output/'既有网络计算')
    receipts=[];role.export_role_review(result,output,receipts)
    view=role.read(output/'火警网络复核视图.json');check=role.read(output/'火警网络场景检查.json')
    attrs=view['objects'][0]['attributes']
    assert len(attrs['endpoint_roles'])==2 and all(e['roles']==['项目报警角色']for e in attrs['endpoint_roles'])
    assert attrs['unit_disclosure']['original_drawing_unit']['insunits_code']==6
    assert attrs['unit_disclosure']['unit_basis_type']=='drawing_header_unit'
    assert attrs['scope_disclosure']['is_region_complete'] is False
    assert '局部' in attrs['scope_disclosure']['scope_statement']
    assert check['identity_closed'] and check['result_fingerprint']==view['result_fingerprint']
    assert all(r['returncode']==0 for r in receipts)
