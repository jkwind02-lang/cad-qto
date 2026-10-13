"""Partial project review keeps unbound evidence without fabricating quantities."""
import copy
import pytest
import test_workflow_integration as integration

def test_registered_only_preserves_unbound_values_and_null(tmp_path):
    document, state, view = integration.reviewed_wall_batch(tmp_path, count=4)
    extra = copy.deepcopy(view['objects'][0])
    extra.update(id='other', catalog_key='unbound-view', quantities=[
        {'name':'existing geometric reference','value':7.0,'unit':'m2','level':'reference'},
        {'name':'unknown material area','value':None,'unit':'m2','level':'unresolved'}], attributes={})
    view['objects'].append(extra)
    execution, quantities, _ = integration.workflow.inspect(document, state, tmp_path)
    with pytest.raises(ValueError, match='数量对象必须绑定'):
        integration.bind_view(document, execution, quantities, view)
    document['review_binding_policy']='registered_only'
    actual = integration.bind_view(document, execution, quantities, view)
    assert actual['objects'][-1]['quantities']==extra['quantities']
    assert actual['objects'][-1]['attributes']=={}
    assert actual['workflow_binding']['unbound_catalog_keys']==['unbound-view']
    assert len(actual['workflow_binding']['bound_catalog_keys'])==4
    assert all(o['attributes'].get('formal_adoption_allowed') is False for o in actual['objects'][:-1])

def partial_feedback(root):
    document, state, view = integration.reviewed_wall_batch(root, count=4)
    extra=copy.deepcopy(view['objects'][0])
    extra.update(id='other',catalog_key='unbound-view',quantities=[
        {'name':'not yet mapped','value':None,'unit':'m3','level':'unresolved'}],attributes={})
    view['objects'].append(extra)
    view['groups'][0]['member_keys'].append('unbound-view')
    document['review_binding_policy']='registered_only'
    for a in document['artifacts']:
        if a['id'] in ['view','feedback','handoff']:a['path']='partial-'+a['id']+'.json'
    execution,quantities,_=integration.workflow.inspect(document,state,root)
    bound=integration.bind_view(document,execution,quantities,view)
    integration.put(root,'partial-view.json',bound)
    state=integration.state_engine.checkpoint(document,state,'build-view',root,'model',
        'explicit partial binding without modifying other quantities',integration.workflow.tool_versions(document))
    fb=integration.empty_feedback(bound);g=bound['groups'][0]
    fb['annotations']['G-note']={
        'group_id':'G','confirmed_result':'Review selected wall and unresolved evidence',
        'reason':'Assistant review, no formal adoption','instruction':'请助手复核','updated_at':'2026-10-13T12:00:00Z',
        'scope':'selected_members','selected_object_keys':['W00-view','unbound-view'],
        'excluded_object_keys':['W01-view','W02-view','W03-view'],
        'object_ids':['W00','other'],'member_snapshot':copy.deepcopy(g['member_snapshot']),
        'result_fingerprint':g['result_fingerprint'],
        'author_role':'assistant','author_source':'explicit reviewer','author_evidence':'fixture',
        'user_confirmation':'unconfirmed','user_confirmation_evidence':''}
    integration.put(root,'partial-feedback.json',fb)
    state,evidence=integration.workflow.run_one(document,state,'read-feedback',root)
    assert evidence['returncode']==0
    return document,state,bound

def test_partial_feedback_preserves_selection_and_records_mapping_gap_once(tmp_path):
    document,state,view=partial_feedback(tmp_path)
    execution,_,_=integration.workflow.inspect(document,state,tmp_path)
    patch=integration.feedback_patch(document,execution,'handoff','a'*64)
    assert [x['id'] for x in patch['upsert']['quantity_targets']]==['W00-volume']
    pending=[x for x in patch['upsert']['issues'] if x.get('status')=='waiting_mapping']
    assert len(pending)==1 and pending[0]['catalog_keys']==['unbound-view']
    assert pending[0]['target_ids']==[]
    assert pending[0]['review_action']['selected_object_keys']==['W00-view','unbound-view']
    assert pending[0]['review_action']['excluded_object_keys']==['W01-view','W02-view','W03-view']
    assert pending[0]['review_action']['author_role']=='assistant'
    amended=integration.workflow.amend(document,patch,'a'*64)
    execution,_,_=integration.workflow.inspect(amended,state,tmp_path)
    again=integration.feedback_patch(amended,execution,'handoff','b'*64)
    assert not any(again['upsert'].values())

def test_partial_policy_cannot_be_applied_to_already_reviewed_strict_view(tmp_path):
    document,state,_=integration.reviewed_wall_batch(tmp_path,count=4)
    document['review_binding_policy']='registered_only'
    execution,_,_=integration.workflow.inspect(document,state,tmp_path)
    with pytest.raises(ValueError,match='绑定策略不同'):
        integration.feedback_patch(document,execution,'handoff','a'*64)
