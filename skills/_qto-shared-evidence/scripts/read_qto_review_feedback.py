"""Read saved HTML/JSON feedback as data; never executes HTML or changes results."""
import argparse,json
from pathlib import Path
from qto_review_contract import normalize_view,validate_feedback,extract_json_file

def comparison_actions(view,current):
 """Contract 9.1: a common answer covers its whole scope; exceptions, conflicts and overlaps are listed, never resolved here."""
 c=view['comparison'];commons={g['id']:g for g in c.get('common_issues',[])};nature={g:x.get('nature') for g,x in commons.items()}
 nature.update({q['id']:q.get('nature') for q in c.get('issues',[])})
 conflicts=[{'annotation_key':k,'candidates':r['merge_conflict']['candidates'],'processing':'合并时两份意见不同，须用户重新保存其中一条；未采用任何一份'} for k,r in current.items() if r.get('merge_conflict')]
 live={k:r for k,r in current.items() if not r.get('merge_conflict')}
 def base(k,r):return {'annotation_key':k,'verdict':r.get('verdict') or None,'option_id':r.get('option_id') or None,'user_result':r.get('confirmed_result') or r.get('verdict'),'user_reason':r['reason'],'updated_at':r.get('updated_at'),'processing':'供所属专业复核；未自动写回数量'}
 item_actions=[{**base(k,r),'item_id':r['item_snapshot']['item_id'],'excluded_detail_row_ids':r.get('excluded_detail_row_ids',[]),'page_view':r.get('page_view'),**({'exception_of':r['exception_of']} if r.get('exception_of') else {})} for k,r in live.items() if r.get('item_snapshot') is not None]
 issue_actions=[{**base(k,r),'issue_id':r['issue_snapshot']['issue_id'],'nature':nature.get(r['issue_snapshot']['issue_id']),**({'exception_of':r['exception_of']} if r.get('exception_of') else {})} for k,r in live.items() if r.get('issue_snapshot') is not None]
 exc={(a.get('exception_of'),'item',a['item_id']) for a in item_actions}|{(a.get('exception_of'),'issue',a['issue_id']) for a in issue_actions}
 common_actions=[];covered={}
 for k,r in live.items():
  snap=r.get('common_issue_snapshot')
  if snap is None:continue
  gid=snap['common_issue_id'];scope=commons[gid]['scope'];old=snap.get('scope') or {}
  qids=[x for x in scope['issue_ids'] if (gid,'issue',x) not in exc];iids=[x for x in scope['item_ids'] if (gid,'item',x) not in exc]
  added={'issue_ids':[x for x in scope['issue_ids'] if x not in old.get('issue_ids',[])],'item_ids':[x for x in scope['item_ids'] if x not in old.get('item_ids',[])]}
  common_actions.append({**base(k,r),'common_issue_id':gid,'nature':nature.get(gid),'scope':{'issue_ids':qids,'item_ids':iids},'added_since_answer':added})
  # Inherited actions name the common answer they come from (via); the professional still re-checks each one.
  issue_actions+=[{**base(k,r),'issue_id':x,'nature':nature.get(x),'via':gid} for x in qids]
  item_actions+=[{**base(k,r),'item_id':x,'excluded_detail_row_ids':[],'page_view':None,'via':gid} for x in iids]
  for x in iids:covered.setdefault(x,[]).append({'common_issue_id':gid,'verdict':r.get('verdict') or None,'option_id':r.get('option_id') or None})
 overlaps=[{'item_id':x,'answers':a,'processing':'同一条目有两处共性答复，一处按现做法、一处不按现做法，请核对；只提醒'} for x,a in covered.items() if {'同意','要改'}<={y['verdict'] for y in a}]
 return {'item_actions':item_actions,'issue_actions':issue_actions,'common_issue_actions':common_actions,'conflicts':conflicts,'overlaps':overlaps}

def main():
 p=argparse.ArgumentParser();p.add_argument('--view',required=True,type=Path);p.add_argument('--feedback',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
 try:
  if a.output.exists():raise ValueError('交接输出已存在，请用新的版本名')
  view=normalize_view(json.loads(a.view.read_text(encoding='utf-8-sig')),a.view.parent)
  fb,unmatched=validate_feedback(extract_json_file(a.feedback),view)
  current={k:r for k,r in fb['annotations'].items() if r.get('applicability_state')=='current'}
  audit=lambda r:any(r.get(k) is not None for k in ('item_snapshot','issue_snapshot','common_issue_snapshot'))
  actions=[{'annotation_key':k,'instruction':r.get('instruction'),'user_result':r['confirmed_result'],'user_reason':r['reason'],'object_ids':r.get('object_ids',[]),'selected_object_keys':r.get('selected_object_keys',[]),'excluded_object_keys':r.get('excluded_object_keys',[]),'scope':r.get('scope'),'updated_at':r.get('updated_at'),'processing':'供所属专业复核并重算；未自动写回数量'} for k,r in current.items() if not audit(r)]
  out={'schema':'qto-review-handoff/1.0','project_id':view['project_id'],'review_id':view['review_id'],'source_sha256':view['source_sha256'],'result_fingerprint':view['result_fingerprint'],'feedback':fb,'actions':actions,'historical_annotations':{k:r for k,r in fb['annotations'].items() if r.get('applicability_state')!='current'},'unmatched':unmatched,'quantity_changes':0,'drafts_are_not_decisions':True}
  extra=comparison_actions(view,current) if 'comparison' in view else {}
  out.update(extra)
  actionable=len(actions)+sum(len(extra.get(k,[])) for k in ('item_actions','issue_actions','common_issue_actions'))
  a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
  status='REVIEW_REQUIRED' if unmatched or extra.get('conflicts') else 'PASS'
  print(json.dumps({'status':status,'saved':len(fb['annotations']),'actionable':actionable,'drafts':len(fb['drafts']),'unmatched':len(unmatched),'conflicts':len(extra.get('conflicts',[])),'overlaps':len(extra.get('overlaps',[])),'quantity_changes':0},ensure_ascii=False));return 0
 except (OSError,ValueError,KeyError,TypeError) as e:print(json.dumps({'status':'INPUT_REQUIRED','error':str(e),**({'problems':e.problems} if hasattr(e,'problems') else {})},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
