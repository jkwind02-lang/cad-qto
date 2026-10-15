"""Read saved HTML/JSON feedback as data; never executes HTML or changes results."""
import argparse,json
from pathlib import Path
from qto_review_contract import normalize_view,validate_feedback,extract_json_file

def main():
 p=argparse.ArgumentParser();p.add_argument('--view',required=True,type=Path);p.add_argument('--feedback',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
 try:
  if a.output.exists():raise ValueError('交接输出已存在，请用新的版本名')
  view=normalize_view(json.loads(a.view.read_text(encoding='utf-8-sig')),a.view.parent)
  fb,unmatched=validate_feedback(extract_json_file(a.feedback),view)
  current={k:r for k,r in fb['annotations'].items() if r.get('applicability_state')=='current'}
  audit=lambda r:r.get('item_snapshot') is not None or r.get('issue_snapshot') is not None
  actions=[{'annotation_key':k,'instruction':r.get('instruction'),'user_result':r['confirmed_result'],'user_reason':r['reason'],'object_ids':r.get('object_ids',[]),'selected_object_keys':r.get('selected_object_keys',[]),'excluded_object_keys':r.get('excluded_object_keys',[]),'scope':r.get('scope'),'updated_at':r.get('updated_at'),'processing':'供所属专业复核并重算；未自动写回数量'} for k,r in current.items() if not audit(r)]
  def audit_action(k,r):return {'annotation_key':k,'verdict':r.get('verdict') or None,'user_result':r.get('confirmed_result') or r.get('verdict'),'user_reason':r['reason'],'updated_at':r.get('updated_at'),'processing':'供所属专业复核；未自动写回数量'}
  item_actions=[{**audit_action(k,r),'item_id':r['item_snapshot']['item_id'],'excluded_detail_row_ids':r.get('excluded_detail_row_ids',[])} for k,r in current.items() if r.get('item_snapshot') is not None]
  issue_actions=[{**audit_action(k,r),'issue_id':r['issue_snapshot']['issue_id']} for k,r in current.items() if r.get('issue_snapshot') is not None]
  out={'schema':'qto-review-handoff/1.0','project_id':view['project_id'],'review_id':view['review_id'],'source_sha256':view['source_sha256'],'result_fingerprint':view['result_fingerprint'],'feedback':fb,'actions':actions,'historical_annotations':{k:r for k,r in fb['annotations'].items() if r.get('applicability_state')!='current'},'unmatched':unmatched,'quantity_changes':0,'drafts_are_not_decisions':True}
  if 'comparison' in view:out.update(item_actions=item_actions,issue_actions=issue_actions)
  a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':'REVIEW_REQUIRED' if unmatched else 'PASS','saved':len(fb['annotations']),'actionable':len(actions)+len(item_actions)+len(issue_actions),'drafts':len(fb['drafts']),'unmatched':len(unmatched),'quantity_changes':0},ensure_ascii=False));return 0
 except (OSError,ValueError,KeyError,TypeError) as e:print(json.dumps({'status':'INPUT_REQUIRED','error':str(e),**({'problems':e.problems} if hasattr(e,'problems') else {})},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
