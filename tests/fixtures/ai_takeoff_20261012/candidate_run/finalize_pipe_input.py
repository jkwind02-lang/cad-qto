#!/usr/bin/env python3
"""Bind the actual native audit and validate the complete working-recognition ledger."""
import datetime, hashlib, json, sys
from pathlib import Path
ROOT=Path(__file__).resolve().parent
PLUGIN=Path('/workspace/scratch/f95ce6ecc4b5/cad-qto-ai-candidate')
sys.path.insert(0,str(PLUGIN/'skills/_qto-shared-evidence/scripts'))
from model_recognition_basis import load_working_entries, recognition_basis
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def save(p,d):Path(p).write_text(json.dumps(d,ensure_ascii=False,indent=2)+'\n')
draft=read(ROOT/'pipe/recognition_ledger.json')
details=read(ROOT/'pipe/source_bound_details.json')
source=ROOT.parent/'inputs/pipe/drawing.dxf'
image=ROOT.parent/'inputs/pipe/drawing.png'
stream=ROOT/'pipe/neutral/中性布局图元.ndjson'
ledger={'schema_version':'qto-model-recognition-ledger/1.0','ledger_id':'LEDGER-PIPE-01','ledger_version':'1','source_sha256':sha(source),'adoption_scope':'working','review_status':'unreviewed','evidence':[{'evidence_id':'EV-VIEW','path':str(image),'sha256':sha(image),'source_sha256':sha(source),'kind':'drawing_view','locator':'Full P-01 Rev A PNG, F1/F2 plans, system diagram, all notes and legend','observation':draft['actual_observation']},{'evidence_id':'EV-CAD','path':str(stream),'sha256':sha(stream),'source_sha256':sha(source),'kind':'cad_property','locator':'Model native neutral records by entity_handle','observation':'Exact line endpoints, source DN labels, plan dimensions, riser IDs and pipe-CL elevation notes read directly from the source-bound native stream.'}],'entries':[],'human_interventions':[],'formal_adoption_allowed':False}
for index,entry in enumerate(draft['entries'],1):
    oid=entry['object_id']
    ledger['entries'].append({'adjudication_id':f'MODEL-{index:03d}','subject_id':oid,'professional_owner':'qto-pipe-networks','source_id':'SRC-PIPE-DXF','recognized_by':draft['recognized_by'],'recognized_at':draft['recorded_at_utc'],'status':'active','decision_origin':'model','decision':entry['decision'],'object_ids':[oid],'evidence_ids':['EV-VIEW','EV-CAD'],'source_object_ids':entry.get('evidence_ids',[]),'basis':entry.get('basis',entry.get('scope_basis')),'scope_basis':'P-01 notes F8,FC,FD,FE,FF,100,101 and same physical riser FA define selection','identity_basis':'Unique CAD handles plus R-A same-physical-riser mapping; plan segments own horizontal quantity, system diagram supplies real elevation only.','cross_check':'Plan source lengths equal directly associated dimension texts; source endpoint continuity, reducer centre and complete start/end limits checked against viewed PNG.','uncertainties':[]})
path=ROOT/'pipe/working_recognition_ledger_v1.json'
save(path,ledger)
load_working_entries(ledger,path,sha(source))
inp=read(ROOT/'pipe/native_input_pre_audit.json')
inp['recognition_basis']=recognition_basis(ledger,path)
inp['object_selection']=read(ROOT/'pipe/object_audit_result.json')
save(ROOT/'pipe/native_input.json',inp)
print('Complete native working-recognition ledger validated; audit output bound into final native input.')
