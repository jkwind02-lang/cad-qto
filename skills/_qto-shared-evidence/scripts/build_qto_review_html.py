"""Build a portable review page from professional-owned results. No quantity mutation."""
import argparse,base64,gzip,html,json,re,textwrap,uuid
from urllib.parse import urlsplit
from html.parser import HTMLParser
from pathlib import Path
from qto_review_contract import normalize_view,validate_feedback,empty_feedback,extract_json_file,pack_payload,unpack_payload,PACK_MAGIC

def plugin_version(root):
 """Common business version: release source record first, else both host manifests; a conflict is reported, never hidden."""
 root=Path(root);source=root/'发布来源.json'
 if source.exists():
  version=json.loads(source.read_text(encoding='utf-8-sig')).get('business_version')
  if not isinstance(version,str) or not version.strip():raise ValueError('发布来源.json缺少business_version')
  return version
 found={}
 for host,rel in (('Codex','.codex-plugin/plugin.json'),('Claude','.claude-plugin/plugin.json')):
  path=root/rel
  if path.exists():found[host]=str(json.loads(path.read_text(encoding='utf-8-sig')).get('version','')).split('+')[0]
 if not found:return '未提供'
 if len(set(found.values()))>1:raise ValueError('两份插件清单的业务版本不一致：'+'，'.join(f'{k}={v}' for k,v in found.items()))
 return next(iter(found.values())) or '未提供'

def build(data_path,output,feedback_path=None,feedback_url=None,feedback_backup_url=None,feedback_provider=None):
 data_path=Path(data_path).resolve();output=Path(output).resolve()
 if output.exists():raise ValueError('目标HTML已存在；请使用新版本名，保护已有批注')
 view=normalize_view(json.loads(data_path.read_text(encoding='utf-8-sig')),data_path.parent)
 feedback=empty_feedback(view);unmatched=[]
 if feedback_path:feedback,unmatched=validate_feedback(extract_json_file(feedback_path),view)
 feedback['drawing_revision']=view['revision']
 assets=Path(__file__).resolve().parents[1]/'assets/review'
 if feedback_url is None:
  profile_path=assets/'feedback-endpoints.json'
  profile=json.loads(profile_path.read_text(encoding='utf-8-sig')) if profile_path.exists() else {}
  if profile.get('enabled'):
   feedback_url=profile.get('url');feedback_backup_url=feedback_backup_url or profile.get('backup_url');feedback_provider=feedback_provider or profile.get('provider')
 def check_endpoint(value):
  if not value:return ''
  u=urlsplit(value)
  if u.scheme!='https' or not u.hostname or u.username or u.password or u.query or u.fragment or not re.fullmatch(r'[a-zA-Z0-9_/-]*',u.path):raise ValueError('反馈地址必须是HTTPS服务地址，不得含账号、查询参数或片段')
  return value.rstrip('/')
 endpoint=check_endpoint(feedback_url);backup=check_endpoint(feedback_backup_url)
 if backup and (not endpoint or backup==endpoint):raise ValueError('备用地址需要不同的主站地址')
 provider=feedback_provider or ('tencent' if backup else 'cloudflare')
 if provider not in ('tencent','cloudflare'):raise ValueError('不支持的反馈主站类型')
 version=plugin_version(Path(__file__).resolve().parents[3])
 config={'url':endpoint,'provider':provider,'backup_url':backup,'client_id':str(uuid.uuid4()),'plugin_version':version}
 template=(assets/'review-shell.html').read_text(encoding='utf-8')
 raw=json.dumps(view,ensure_ascii=False,separators=(',',':')).encode();packed=pack_payload(view);packed_gz=gzip.compress(packed,mtime=0)
 payload='\n'.join(textwrap.wrap(base64.b64encode(packed_gz).decode(),180))
 page=template.replace('__PAGE_TITLE__',html.escape(view['title'])).replace('__PAYLOAD__',payload)
 page=page.replace('__FEEDBACK__',json.dumps(feedback,ensure_ascii=False,indent=0).replace('<','\\u003c'))
 page=page.replace('__REVIEW_CORE__',(assets/'review-core.js').read_text(encoding='utf-8')).replace('__REVIEW_AUDIT__',(assets/'review-audit.js').read_text(encoding='utf-8')).replace('__REVIEW_APP__',(assets/'review-app.js').read_text(encoding='utf-8'))
 page=page.replace('__PROBLEM_CONFIG__',json.dumps(config,ensure_ascii=False).replace('<','\\u003c')).replace('__PROBLEM_FEEDBACK__',(assets/'problem-feedback.js').read_text(encoding='utf-8'))
 assert unpack_payload(gzip.decompress(base64.b64decode(payload)))==view,'页面载荷解包后与视图不一致'
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
 report={'status':'PASS_WITH_WARNINGS' if warnings or unmatched else 'PASS','output':str(output),'schema':view['schema'],'source_sha256':view['source_sha256'],'object_records':len(view['objects']),'distinct_object_ids':len({o['id'] for o in view['objects']}),'professional_count':len({o['professional'] for o in view['objects']}),'scene_count':len(view['scenes']),'group_count':len(view['groups']),'saved_annotations':len(feedback['annotations']),'drafts':len(feedback['drafts']),'bytes':output.stat().st_size,'size_estimate':{'raw_json_bytes':len(raw),'gzip_bytes':len(gzip.compress(raw,mtime=0)),'payload_gzip_bytes':len(packed_gz),'background_packed':packed.startswith(PACK_MAGIC),'html_bytes':output.stat().st_size},'longest_line':longest,'unmatched_historical_feedback':unmatched,'warnings':warnings,'quantity_changes':0,'native_browser_validation':'not performed by this builder'}
 if 'comparison' in view:
  check=view['comparison']['generation_check'];report.update(comparison_counts=check['counts'],comparison_exceptions=check['exceptions'],consistent_band={k:view['comparison']['thresholds'].get(k) for k in ('consistent_band','count_units')},legacy_search_evidence=view['comparison'].get('legacy_search_evidence'))
 return report

def main():
 p=argparse.ArgumentParser();p.add_argument('--input',required=True,type=Path);p.add_argument('--output',required=True,type=Path);p.add_argument('--feedback',type=Path);p.add_argument('--feedback-url');p.add_argument('--feedback-backup-url');p.add_argument('--feedback-provider',choices=['tencent','cloudflare']);p.add_argument('--local-only',action='store_true');p.add_argument('--report',type=Path);a=p.parse_args()
 try:
  if a.report and a.report.resolve() in {p.resolve() for p in [a.input,a.output,a.feedback] if p is not None}:raise ValueError('检查报告路径不能覆盖来源、批注或HTML成果')
  if a.local_only and (a.feedback_url or a.feedback_backup_url):raise ValueError('仅本地保存不能同时指定在线地址')
  report=build(a.input,a.output,a.feedback,'' if a.local_only else a.feedback_url,None if a.local_only else a.feedback_backup_url,a.feedback_provider)
  if a.report:a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps(report,ensure_ascii=False,indent=2));return 0
 except (OSError,ValueError,TypeError,KeyError,AssertionError) as e:
  out={'status':'INPUT_REQUIRED','error':str(e)}
  if hasattr(e,'problems'):out['problems']=e.problems
  if a.report and hasattr(e,'problems'):a.report.parent.mkdir(parents=True,exist_ok=True);a.report.write_text(json.dumps(out,ensure_ascii=False,indent=2),encoding='utf-8')
  print(json.dumps(out,ensure_ascii=False));return 2
if __name__=='__main__':raise SystemExit(main())
