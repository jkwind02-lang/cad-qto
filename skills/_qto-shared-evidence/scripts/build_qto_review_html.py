"""Build a portable review page from professional-owned results. No quantity mutation."""
import argparse,base64,gzip,html,json,re,textwrap
from html.parser import HTMLParser
from pathlib import Path
from qto_review_contract import normalize_view,validate_feedback,empty_feedback,extract_json_file

def build(data_path,output,feedback_path=None):
 data_path=Path(data_path).resolve();output=Path(output).resolve()
 if output.exists():raise ValueError('目标HTML已存在；请使用新版本名，保护已有批注')
 view=normalize_view(json.loads(data_path.read_text(encoding='utf-8-sig')),data_path.parent)
 feedback=empty_feedback(view);unmatched=[]
 if feedback_path:feedback,unmatched=validate_feedback(extract_json_file(feedback_path),view)
 feedback['drawing_revision']=view['revision']
 assets=Path(__file__).resolve().parents[1]/'assets/review'
 template=(assets/'review-shell.html').read_text(encoding='utf-8')
 raw=json.dumps(view,ensure_ascii=False,separators=(',',':')).encode();payload='\n'.join(textwrap.wrap(base64.b64encode(gzip.compress(raw,mtime=0)).decode(),180))
 page=template.replace('__PAGE_TITLE__',html.escape(view['title'])).replace('__PAYLOAD__',payload)
 page=page.replace('__FEEDBACK__',json.dumps(feedback,ensure_ascii=False,indent=0).replace('<','\\u003c'))
 page=page.replace('__REVIEW_CORE__',(assets/'review-core.js').read_text(encoding='utf-8')).replace('__REVIEW_APP__',(assets/'review-app.js').read_text(encoding='utf-8'))
 assert json.loads(gzip.decompress(base64.b64decode(payload)))==view
 class IdParser(HTMLParser):
  def __init__(self):super().__init__();self.ids=[]
  def handle_starttag(self,tag,attrs):
   value=dict(attrs).get('id')
   if value:self.ids.append(value)
 parser=IdParser();parser.feed(page);ids=parser.ids;refs=set(re.findall(r"\$\('([^']+)'\)",page));assert len(ids)==len(set(ids));assert refs.issubset(ids),refs-set(ids)
 page.encode('utf-8');output.parent.mkdir(parents=True,exist_ok=True)
 with output.open('x',encoding='utf-8',newline='\n') as f:f.write(page)
 longest=max(map(len,page.splitlines()));warnings=[]
 if output.stat().st_size>2_000_000:warnings.append('HTML超过2MB提醒值；资料完整保留，需按用途检查实际打开性能')
 if longest>10_000:warnings.append('存在超过10000字符的行，需复核源码预览性能')
 return {'status':'PASS_WITH_WARNINGS' if warnings or unmatched else 'PASS','output':str(output),'source_sha256':view['source_sha256'],'object_records':len(view['objects']),'distinct_object_ids':len({o['id'] for o in view['objects']}),'professional_count':len({o['professional'] for o in view['objects']}),'scene_count':len(view['scenes']),'group_count':len(view['groups']),'saved_annotations':len(feedback['annotations']),'drafts':len(feedback['drafts']),'bytes':output.stat().st_size,'longest_line':longest,'unmatched_historical_feedback':unmatched,'warnings':warnings,'quantity_changes':0,'native_browser_validation':'not performed by this builder'}

def main():
 p=argparse.ArgumentParser();p.add_argument('--input',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--feedback',type=Path);p.add_argument('--report',type=Path);a=p.parse_args()
 try:
  if a.report and a.report.resolve() in {p.resolve() for p in [a.input,a.output,a.feedback] if p is not None}:raise ValueError('检查报告路径不能覆盖来源、批注或HTML成果')
  report=build(a.input,a.output,a.feedback)
  if a.report:a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps(report,ensure_ascii=False,indent=2));return 0
 except (OSError,ValueError,TypeError,KeyError,AssertionError) as e:print(json.dumps({'status':'INPUT_REQUIRED','error':str(e)},ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
