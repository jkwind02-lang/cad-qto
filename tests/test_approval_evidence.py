"""Approval identity and human-review evidence contract (A phase, backend only).

Counterexamples reproduced on commit 3ae0b15: actor values such as '', ' ', 0, True, [], {},
'Claude', 'Codex' or 'GPT-6' were judged current, and a forged current was inherited by the
next run. Synthetic data only; no project drawings.
"""
from __future__ import annotations
import copy, json, os, subprocess, sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/_qto-shared-evidence/scripts'))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from recompute_qto_dependencies import recalculate
from qto_actor_identity import is_model_identity
from review_evidence_helpers import write_review, edit_evidence, sha

SCRIPT=ROOT/'skills/_qto-shared-evidence/scripts/recompute_qto_dependencies.py'


def graph():
    return {'schema':'qto-calculation-graph/1.0','project_id':'synthetic','sources':[{'id':'drawing','sha256':'a'*64}],
            'rules':[{'id':'geometry','sha256':'b'*64,'status':'active'}],
            'nodes':[{'id':'low','unit':'m','value':3.,'source_ids':['drawing']},
                     {'id':'high','unit':'m','value':7.2,'source_ids':['drawing']},
                     {'id':'horizontal','unit':'m','value':20.,'source_ids':['drawing']},
                     {'id':'vertical','unit':'m','variables':{'a':'low','b':'high'},'expression':'abs(b-a)','rule_ids':['geometry']},
                     {'id':'total','unit':'m','variables':{'h':'horizontal','v':'vertical'},'expression':'h+v','rule_ids':['geometry']}]}


def rows(run):
    return {r['id']:r for r in run['nodes']}


def approve(tmp_path, d, ids, **kw):
    base=rows(recalculate(d));approvals=write_review(tmp_path,d['project_id'],[base[i] for i in ids],**kw)
    for node in d['nodes']:
        if node['id'] in approvals:node['approval']=approvals[node['id']]
    return approvals


@pytest.mark.parametrize('actor',['',' ',0,1,True,False,[],{},'model','codex','Claude','Claude Opus 5.5','Codex','GPT-6','张工'])
def test_legacy_actor_approval_is_never_current_and_keeps_value(actor):
    d=graph();fp=rows(recalculate(d))['horizontal']['fingerprint']
    d['nodes'][2]['approval']={'fingerprint':fp,'actor':actor}
    run=recalculate(d);h=rows(run)['horizontal']
    assert h['review_state']=='unreviewed' and h['value']==20 and h['review_evidence'] is None
    assert run['status']=='REVIEW_REQUIRED' and run['approval_issues'][0]['node_id']=='horizontal'
    assert run['business_adoption_allowed'] is False and rows(run)['total']['value']==pytest.approx(24.2)


@pytest.mark.parametrize('approval',[None,'',0,[],{}])
def test_malformed_approval_reports_issue(approval):
    d=graph();d['nodes'][2]['approval']=approval
    run=recalculate(d)
    if approval is None:
        assert run['approval_issues']==[] and run['status']=='CALCULATED'
    else:
        assert rows(run)['horizontal']['review_state']=='unreviewed' and run['approval_issues']


def test_forged_current_is_not_inherited_without_evidence():
    d=graph();fp=rows(recalculate(d))['horizontal']['fingerprint']
    d['nodes'][2]['approval']={'fingerprint':fp,'actor':'Claude'}
    first=recalculate(d)
    first['nodes'][2]['review_state']='current'  # what the old code produced and then inherited
    del d['nodes'][2]['approval'];second=recalculate(d,first)
    assert rows(second)['horizontal']['review_state']=='unreviewed'
    assert second['approval_issues'][0]['source']=='inherited' and second['status']=='REVIEW_REQUIRED'


def test_valid_human_evidence_is_current_and_inherited_after_reverification(tmp_path):
    d=graph();approve(tmp_path,d,['horizontal'])
    first=recalculate(d,None,tmp_path);h=rows(first)['horizontal']
    assert h['review_state']=='current' and first['approval_issues']==[] and first['status']=='CALCULATED'
    assert h['review_evidence']['reviewer']=='张工' and Path(h['review_evidence']['path']).is_absolute()
    del d['nodes'][2]['approval'];second=recalculate(d,first,tmp_path,tmp_path)
    assert rows(second)['horizontal']['review_state']=='current' and second['approval_issues']==[]


def test_inherited_evidence_tampered_after_first_run_is_downgraded(tmp_path):
    d=graph();approvals=approve(tmp_path,d,['horizontal']);first=recalculate(d,None,tmp_path)
    ev=tmp_path/approvals['horizontal']['evidence']['path'];ev.write_text(ev.read_text(encoding='utf-8')+' ',encoding='utf-8')
    del d['nodes'][2]['approval'];second=recalculate(d,first,tmp_path,tmp_path)
    assert rows(second)['horizontal']['review_state']=='unreviewed'
    assert any('哈希' in i for i in second['approval_issues'][0]['issues'])


def test_inherited_evidence_file_missing_is_downgraded(tmp_path):
    d=graph();approvals=approve(tmp_path,d,['horizontal']);first=recalculate(d,None,tmp_path)
    (tmp_path/approvals['horizontal']['evidence']['path']).unlink()
    del d['nodes'][2]['approval'];second=recalculate(d,first,tmp_path,tmp_path)
    assert rows(second)['horizontal']['review_state']=='unreviewed' and second['approval_issues']


def test_transitive_change_marks_evidenced_reviews_stale(tmp_path):
    d=graph();approve(tmp_path,d,['low','high','horizontal','vertical','total'])
    first=recalculate(d,None,tmp_path);assert all(r['review_state']=='current' for r in first['nodes'])
    for node in d['nodes']:node.pop('approval',None)
    d['nodes'][1]['value']=8.2;second=recalculate(d,first,tmp_path,tmp_path);v=rows(second)
    assert set(second['stale_reviews'])=={'high','vertical','total'} and v['total']['value']==pytest.approx(25.2)
    assert v['horizontal']['review_state']=='current' and v['low']['review_state']=='current'
    assert second['approval_issues']==[] and second['status']=='REVIEW_REQUIRED'


def test_approval_for_old_fingerprint_is_stale_not_current(tmp_path):
    d=graph();approve(tmp_path,d,['high']);d['nodes'][1]['value']=8.2
    run=recalculate(d,None,tmp_path)
    assert rows(run)['high']['review_state']=='stale' and run['stale_reviews']==['high'] and run['approval_issues']==[]


@pytest.mark.parametrize('change,needle',[
    (lambda r:r.update(node_id='low'),'其他计算节点'),
    (lambda r:r.update(calculation_fingerprint='e'*64),'指纹'),
    (lambda r:r.update(quantity=21.),'数量'),
    (lambda r:r.update(unit='mm'),'单位'),
    (lambda r:r.update(reviewer_type='model'),'reviewer_type'),
    (lambda r:r.update(reviewer='Claude Opus 5.5'),'模型或助手'),
    (lambda r:r.update(reviewed_at='2026-10-14T19:32:00'),'时区'),
    (lambda r:r.update(decision='method_opinion'),'decision'),
    (lambda r:r.pop('source_feedback'),'source_feedback'),
])
def test_evidence_record_must_bind_node_value_unit_and_human_decision(tmp_path,change,needle):
    d=graph();approvals=approve(tmp_path,d,['horizontal'])
    edit_evidence(tmp_path,approvals['horizontal'],change);d['nodes'][2]['approval']=approvals['horizontal']
    run=recalculate(d,None,tmp_path)
    assert rows(run)['horizontal']['review_state']=='unreviewed' and rows(run)['horizontal']['value']==20
    assert any(needle in i for i in run['approval_issues'][0]['issues'])


def test_evidence_hash_mismatch_and_other_project_are_rejected(tmp_path):
    d=graph();approvals=approve(tmp_path,d,['horizontal'])
    d['nodes'][2]['approval']=copy.deepcopy(approvals['horizontal']);d['nodes'][2]['approval']['evidence']['sha256']='f'*64
    assert rows(recalculate(d,None,tmp_path))['horizontal']['review_state']=='unreviewed'
    other=graph();other['project_id']='other';other['nodes'][2]['approval']=approvals['horizontal']
    run=recalculate(other,None,tmp_path)
    assert rows(run)['horizontal']['review_state']=='unreviewed'


def test_method_opinion_annotation_is_not_quantity_confirmation(tmp_path):
    d=graph();approve(tmp_path,d,['horizontal'],decision_type='method_opinion')
    run=recalculate(d,None,tmp_path)
    assert rows(run)['horizontal']['review_state']=='unreviewed'
    assert any('数量确认' in i for i in run['approval_issues'][0]['issues'])


def test_tampered_source_feedback_is_rejected(tmp_path):
    d=graph();approve(tmp_path,d,['horizontal'])
    fb=tmp_path/'review_feedback.json';fb.write_text(fb.read_text(encoding='utf-8').replace('张工','李工'),encoding='utf-8')
    run=recalculate(d,None,tmp_path)
    assert rows(run)['horizontal']['review_state']=='unreviewed' and any('原批注' in i for i in run['approval_issues'][0]['issues'])


def test_approval_fields_must_match_record(tmp_path):
    d=graph();approve(tmp_path,d,['horizontal']);d['nodes'][2]['approval']['reviewer']='李工'
    run=recalculate(d,None,tmp_path)
    assert rows(run)['horizontal']['review_state']=='unreviewed'


def test_relative_evidence_without_base_dir_is_not_resolved_from_cwd(tmp_path,monkeypatch):
    d=graph();approve(tmp_path,d,['horizontal']);monkeypatch.chdir(tmp_path)
    run=recalculate(d)
    assert rows(run)['horizontal']['review_state']=='unreviewed' and any('相对路径' in i for i in run['approval_issues'][0]['issues'])


def test_cli_resolves_evidence_relative_to_input_not_cwd(tmp_path):
    d=graph();approve(tmp_path,d,['horizontal'])
    source=tmp_path/'graph.json';source.write_text(json.dumps(d,ensure_ascii=False),encoding='utf-8')
    elsewhere=tmp_path/'elsewhere';elsewhere.mkdir();out=tmp_path/'out'/'run.json'
    result=subprocess.run([sys.executable,str(SCRIPT),'--input',str(source),'--output',str(out)],cwd=elsewhere,
                          stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8')
    assert result.returncode==0,result.stdout+result.stderr
    run=json.loads(out.read_text(encoding='utf-8'))
    assert rows(run)['horizontal']['review_state']=='current'
    assert '审批校验问题' in out.with_suffix('.md').read_text(encoding='utf-8')


@pytest.mark.parametrize('name',['张工','Wang Kai','Bai','Mai Lin','Kaito','李四'])
def test_real_names_are_not_model_identities(name):
    assert not is_model_identity(name)


@pytest.mark.parametrize('name',['ai','AI','LLM','model','Assistant','my ai helper','claude','Claude Opus 5.5','codex','Codex CLI',
                                 'gpt-5','GPT-6','ChatGPT','anthropic','OpenAI','模型','AI助手','人工智能复核'])
def test_model_identities_are_detected(name):
    assert is_model_identity(name)


@pytest.mark.parametrize('value',[None,0,True,[],{}])
def test_non_text_is_not_a_model_name_but_still_invalid_reviewer(value):
    assert not is_model_identity(value)


def test_adjudication_confirmers_use_the_shared_rule():
    for rel in ['skills/qto-pipe-networks/scripts/build_pipe_inference_input.py',
                'skills/qto-components-points/scripts/build_component_instances_from_neutral.py',
                'skills/_qto-shared-evidence/scripts/qto_human_review_evidence.py']:
        text=(ROOT/rel).read_text(encoding='utf-8')
        assert 'from qto_actor_identity import is_model_identity' in text and 'MODEL_ACTORS' not in text
