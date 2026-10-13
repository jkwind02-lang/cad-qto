"""Read saved HTML/JSON feedback as data; never executes HTML or changes results."""
import argparse,json
from pathlib import Path
from qto_review_contract import normalize_view,validate_feedback,extract_json_file

def build_review_actions(feedback):
 actions=[]
 for key,record in feedback['annotations'].items():
  if record.get('applicability_state')!='current':continue
  actions.append({'annotation_key':key,'instruction':record.get('instruction'),
   'review_result':record['confirmed_result'],'review_reason':record['reason'],
   # Compatibility aliases carry content, not evidence of user authorship.
   'user_result':record['confirmed_result'],'user_reason':record['reason'],
   **{field:record[field] for field in ('author_role','author_source','author_evidence','user_confirmation','user_confirmation_evidence')},
   'object_ids':record.get('object_ids',[]),'object_catalog_key':(record.get('object_snapshot') or {}).get('catalog_key'),
   'selected_object_keys':record.get('selected_object_keys',[]),'excluded_object_keys':record.get('excluded_object_keys',[]),
   'scope':record.get('scope'),'updated_at':record.get('updated_at'),
   'processing':'供所属专业复核并重算；未自动写回数量'})
 return actions

def main():
 p=argparse.ArgumentParser();p.add_argument('--view',required=True,type=Path);p.add_argument('--feedback',required=True,type=Path);p.add_argument('--output',required=True,type=Path);a=p.parse_args()
 try:
  if a.output.exists():raise ValueError('交接输出已存在，请用新的版本名')
  view=normalize_view(json.loads(a.view.read_text(encoding='utf-8-sig')),a.view.parent)
  fb,unmatched=validate_feedback(extract_json_file(a.feedback),view)
  actions=build_review_actions(fb)
  out={'schema':'qto-review-handoff/1.0','project_id':view['project_id'],'review_id':view['review_id'],'source_sha256':view['source_sha256'],'result_fingerprint':view['result_fingerprint'],'feedback':fb,'actions':actions,'historical_annotations':{k:r for k,r in fb['annotations'].items() if r.get('applicability_state')!='current'},'unmatched':unmatched,'quantity_changes':0,'drafts_are_not_decisions':True,'legacy_user_fields_are_content_aliases':True,'author_metadata_is_explicit_declaration':True}
  a.output.parent.mkdir(parents=True,exist_ok=True);a.output.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':'REVIEW_REQUIRED' if unmatched else 'PASS','saved':len(fb['annotations']),'actionable':len(actions),'drafts':len(fb['drafts']),'unmatched':len(unmatched),'quantity_changes':0},ensure_ascii=False));return 0
 except (OSError,ValueError,KeyError,TypeError) as e:print(json.dumps({'status':'INPUT_REQUIRED','error':str(e)},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
