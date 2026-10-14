"""Synthetic qto-human-review-evidence/1.0 files for tests; no project data."""
from __future__ import annotations
import hashlib, json
from pathlib import Path

REVIEWED_AT='2026-10-14T19:32:00+08:00'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def write_review(tmp, project, rows, *, reviewer='张工', annotation_key='A1', decision_type='quantity_confirmation', name='review'):
    """Write one saved user annotation plus exported evidence confirming each run row; return approvals by node id."""
    tmp=Path(tmp)
    confirmations=[{'node_id':r['id'],'calculation_fingerprint':r['fingerprint'],'review_object_key':'obj-'+r['id'],
                    'result_fingerprint':'c'*64,'quantity':r['value'],'unit':r['unit']} for r in rows]
    feedback={'schema':'qto-review-feedback/2.0','project_id':project,'review_id':'R1','source_sha256':'d'*64,
              'annotations':{annotation_key:{'decision_type':decision_type,'reviewer':reviewer,'reviewed_at':REVIEWED_AT,
                                             'confirmed_result':'确认当前数量','reason':'对照图纸逐项核对',
                                             'quantity_confirmations':confirmations}},'drafts':{}}
    fb=tmp/f'{name}_feedback.json';fb.write_text(json.dumps(feedback,ensure_ascii=False),encoding='utf-8')
    records=[{'record_id':f"{name}-{r['id']}",'reviewer':reviewer,'reviewer_type':'human','reviewed_at':REVIEWED_AT,
              'decision':'confirm_quantity','node_id':r['id'],'calculation_fingerprint':r['fingerprint'],
              'review_object_key':'obj-'+r['id'],'result_fingerprint':'c'*64,'quantity':r['value'],'unit':r['unit'],
              'source_feedback':{'path':fb.name,'sha256':sha(fb),'annotation_key':annotation_key,'saved_at':REVIEWED_AT}} for r in rows]
    ev=tmp/f'{name}_evidence.json'
    ev.write_text(json.dumps({'schema':'qto-human-review-evidence/1.0','project_id':project,'records':records},ensure_ascii=False),encoding='utf-8')
    return {r['id']:{'fingerprint':r['fingerprint'],'reviewer':reviewer,'reviewer_type':'human','reviewed_at':REVIEWED_AT,
                     'evidence':{'path':ev.name,'sha256':sha(ev),'record_id':f"{name}-{r['id']}"}} for r in rows}


def inherited_refs(tmp, approvals):
    """review_evidence references as a previous run carries them (absolute evidence path)."""
    return {k:{'path':str((Path(tmp)/a['evidence']['path']).resolve()),'sha256':a['evidence']['sha256'],
               'record_id':a['evidence']['record_id'],'reviewer':a['reviewer'],'reviewed_at':a['reviewed_at']}
            for k,a in approvals.items()}


def edit_evidence(tmp, approval, change):
    """Apply change(record) to the evidence record and re-hash, so only the semantic check can reject it."""
    ev=Path(tmp)/approval['evidence']['path'];doc=json.loads(ev.read_text(encoding='utf-8'))
    for record in doc['records']:
        if record['record_id']==approval['evidence']['record_id']:change(record)
    ev.write_text(json.dumps(doc,ensure_ascii=False),encoding='utf-8')
    approval['evidence']['sha256']=sha(ev)
    return approval
