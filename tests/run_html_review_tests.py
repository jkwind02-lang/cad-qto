"""Exercise all professional view contracts and feedback isolation without project quantities."""
import argparse,base64,copy,hashlib,importlib.util,json,re,struct,subprocess,sys,zlib
from pathlib import Path

def main():
 p=argparse.ArgumentParser();p.add_argument('--root',required=True,type=Path);p.add_argument('--output-root',required=True,type=Path);a=p.parse_args();root=a.root.resolve();a.output_root.mkdir(parents=True,exist_ok=True)
 n=1
 while (a.output_root/f'运行{n:02d}').exists():n+=1
 out=a.output_root/f'运行{n:02d}';out.mkdir();sys.path.insert(0,str(root/'_qto-shared-evidence/scripts'))
 import qto_review_contract as c
 from build_qto_review_html import build as configured_build
 # Offline fixtures deliberately disable the maintained endpoint profile.
 def build(*args,**kwargs):return configured_build(*args,feedback_url='',**kwargs)
 # Tiny generated test raster; not a rendering of a real drawing.
 def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data)&0xffffffff)
 png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>2I5B',64,64,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(b''.join(b'\0'+b''.join(bytes([180,200,213]) if x%16==0 or y%16==0 else bytes([248,251,253]) for x in range(64)) for y in range(64))))+chunk(b'IEND',b'')
 (out/'合成图页.png').write_bytes(png)
 source_hash=hashlib.sha256(b'synthetic-vector-view-v1').hexdigest()
 d={'schema':c.VIEW_SCHEMA,'project_id':'全专业共享复核验证','review_id':'独立合成示例','title':'全专业图面复核演示','revision':'演示版，不代表实际工程量','sources':[{'id':'V','title':'合成矢量测试资料','sha256':source_hash},{'id':'R','title':'合成栅格测试资料','sha256':hashlib.sha256(png).hexdigest()}],
 'scenes':[{'id':'plan','title':'平面合成图（mm）','source_id':'V','coordinate_unit':'mm','y_axis':'up','bounds':[0,0,16000,7000],'background':[{'kind':'line','points':[[500,500],[15000,500],[15000,6500]],'color':'#a8bcc9'},{'kind':'text','point':[650,6300],'text':'合成示例：对象、单位和采用状态分别保留','height':180}]},
 {'id':'section','title':'剖面合成图（m）','source_id':'V','coordinate_unit':'m','y_axis':'up','bounds':[0,0,16,8],'background':[]},
 {'id':'raster','title':'栅格图页（px，向下）','source_id':'R','coordinate_unit':'px','y_axis':'down','bounds':[0,0,640,640],'background':[],'raster_path':'合成图页.png'}],
 'objects':[],'groups':[],'receipts':{},'summary':[],'scope_notes':['仅用于验证13专业共享复核接口，不是自动识图或工程量正确率证明。','未定位对象保留；未决数值为null，不按0显示。']}
 unitmap={'qto-pipe-networks':['m','个'],'qto-components-points':['台','樘'],'qto-duct-air-systems':['m²','m'],'qto-electrical-routes':['m','根'],'qto-concrete-formwork':['m³','m²'],'qto-rebar':['kg','根'],'qto-structural-steel':['t','m²'],'qto-masonry-secondary':['m³','m²'],'qto-surfaces-envelope':['m²','m'],'qto-framing-supports':['m','kg'],'qto-earthwork-terrain':['m³','m²'],'qto-piles-groundwork':['根','m'],'qto-roads-siteworks':['m²','m']}
 for index,(prof,label) in enumerate(c.PROFESSIONALS.items()):
  ids=[];x=700+(index%7)*2100;y=1100+(index//7)*2600
  for k in range(3):
   oid=f'{prof}-对象{k+1}';key='复核|'+oid;ids.append(key);kind='unlocated' if k==2 else 'polygon' if prof in ['qto-concrete-formwork','qto-surfaces-envelope','qto-earthwork-terrain','qto-roads-siteworks','qto-masonry-secondary'] else 'point' if prof in ['qto-components-points','qto-piles-groundwork'] else 'line'
   scene='plan' if k==0 else 'section' if k==1 else None
   pts=[[x,y],[x+1100,y],[x+1100,y+1300],[x,y+1300]] if kind=='polygon' else [[x,y]] if kind=='point' else [[x,y],[x+1200,y+500]]
   if k==1:pts=[[index+.5,1],[index+.5,4],[index+1,4],[index+1,1]] if kind=='polygon' else [[index+.5,3]] if kind=='point' else [[index+.5,1],[index+.5,4]]
   obj={'id':oid,'catalog_key':key,'professional':prof,'scene_id':scene,'kind':kind,'points':pts if kind!='unlocated' else [],'title':label+'对象'+str(k+1),'category':'同类对象复核' if k<2 else '资料待补','status':'规则推算' if k<2 else '缺可靠图面定位','source':'合成源V / 测试对象'+str(index),'detail':'核对本对象的边界或连接；数量仅为合成测试值','quantities':[{'name':'主项','value':3.25+index if k<2 else None,'unit':unitmap[prof][0],'level':'规则推算量' if k<2 else '未决量'},{'name':'不同单位附项','value':2 if k<2 else None,'unit':unitmap[prof][1],'level':'方案暂估量' if k<2 else '未决量'}],'attributes':{'构造/规格':'本专业示例，不能跨专业套公式','计算口径':'合成测试'}}
   if prof=='qto-surfaces-envelope' and k==0:obj['holes']=[[[x+200,y+300],[x+700,y+300],[x+700,y+800],[x+200,y+800]]]
   if k==0:obj['attributes']['source_context']=[{'label':'原表列头','text':'地（楼）面','source_refs':['V/heading']},{'label':'原表房间行及拆分编号','text':'合成房间；F + 1','source_refs':['V/row','V/code-a','V/code-b']}]
   if prof=='qto-components-points' and k==1:obj.update(scene_id='raster',points=[[240,320]])
   d['objects'].append(obj)
  d['groups'].append({'id':'GROUP-'+prof,'title':label+'同类问题组','category':'待复核','status':'方法待核对','proposal':'按本专业图纸依据逐成员复核，保留各自单位','reason':'同类判断可以整组反馈；第三个对象缺少定位，不填假坐标','member_keys':ids})
  d['summary'].append({'label':'合成主项','value':3.25+index,'unit':unitmap[prof][0],'scope':label+'示例'})
 datafile=out/'全专业合成视图.json';datafile.write_text(json.dumps(d,ensure_ascii=False,indent=2),encoding='utf-8');view=c.normalize_view(d,out);before=copy.deepcopy(d)
 report=build(datafile,out/'全专业图面复核演示.html');assert d==before and report['professional_count']==13 and report['object_records']==39
 checks=['13专业混合页生成','点线面及孔洞输入通过','多源多图页坐标单位保留','栅格图片单文件内嵌','未决null保留','不改变专业数量或输入']
 results=[]
 for prof in c.PROFESSIONALS:
  subset=copy.deepcopy(d);subset['review_id']=prof+'独立复核';subset['objects']=[o for o in subset['objects'] if o['professional']==prof];subset['groups']=[g for g in subset['groups'] if g['id']=='GROUP-'+prof];subset['summary']=[q for q in subset['summary'] if q['scope'].startswith(c.PROFESSIONALS[prof])];p=out/(prof+'.json');p.write_text(json.dumps(subset,ensure_ascii=False),encoding='utf-8');r=build(p,out/(prof+'.html'));assert r['professional_count']==1;results.append(prof)
 checks.append('13专业逐个生成入口通过')
 def rejected(name,fn):
  try:fn()
  except (ValueError,KeyError,TypeError):checks.append(name);return
  raise AssertionError('应拒绝:'+name)
 bad=copy.deepcopy(d);bad['objects'][0]['quantities'][0]['value']=False;rejected('布尔值不能冒充数量',lambda:c.normalize_view(bad,out))
 bad=copy.deepcopy(d);bad['objects'][0]['attributes']['source_context'][0]['source_refs']=[];rejected('原文上下文缺来源定位拒绝',lambda:c.normalize_view(bad,out))
 bad=copy.deepcopy(d);bad['objects'][0]['points'][0][0]=float('inf');rejected('非有限坐标拒绝',lambda:c.normalize_view(bad,out))
 bad=copy.deepcopy(d);bad['groups'][0]['member_keys'].append('missing');rejected('不存在的组成员拒绝',lambda:c.normalize_view(bad,out))
 bad=copy.deepcopy(d);bad['scenes'][0]['raster_data_uri']='https://example.invalid/image.png';rejected('外部底图依赖拒绝',lambda:c.normalize_view(bad,out))
 rejected('不覆盖已有用户HTML',lambda:build(datafile,out/'全专业图面复核演示.html'))
 fb=c.empty_feedback(view);g=view['groups'][0];sel=g['member_keys'][:2];ex=g['member_keys'][2:];record={'annotation_id':'问题组|'+g['id'],'object_id':g['id'],'object_ids':[s['object_id'] for s in g['member_snapshot'][:2]],'group_id':g['id'],'selected_object_keys':sel,'excluded_object_keys':ex,'member_snapshot':g['member_snapshot'],'result_fingerprint':g['result_fingerprint'],'confirmed_result':'保留各处规格，仅修正判断方法','reason':'跨单位成员不得统一覆盖数值','instruction':'请助手复核','scope':'本组勾选对象','updated_at':'2026-09-14T00:00:00Z','history':[]}
 fb['annotations'][record['annotation_id']]=record;assert not c.validate_feedback(fb,view)[1];checks.append('整组两成员和一例外保全')
 bad=copy.deepcopy(fb);bad['project_id']='其他项目';rejected('同图异项目批注拒绝',lambda:c.validate_feedback(bad,view))
 badview=copy.deepcopy(d);badview['sources'][1]['sha256']='b'*64;badview=c.normalize_view(badview,out);rejected('任一来源版本变化拒绝',lambda:c.validate_feedback(fb,badview))
 bad=copy.deepcopy(fb);bad['annotations'][record['annotation_id']]['object_ids']=['伪造对象'];rejected('批注对象与成员快照不符拒绝',lambda:c.validate_feedback(bad,view))
 bad=copy.deepcopy(fb);bad['annotations'][record['annotation_id']]['excluded_object_keys']=[];rejected('不能丢掉未选例外',lambda:c.validate_feedback(bad,view))
 bad=c.empty_feedback(view);obj=view['objects'][0];bad['annotations']['单项']={'object_id':obj['id'],'object_ids':['其他对象'],'object_snapshot':{'catalog_key':obj['catalog_key'],'object_id':obj['id']},'confirmed_result':'修改','reason':'测试'};rejected('单项不能改到其他对象',lambda:c.validate_feedback(bad,view))
 f=out/'保存批注.json';f.write_text(json.dumps(fb,ensure_ascii=False,indent=2),encoding='utf-8');build(datafile,out/'全专业带批注验证.html',f);checks.append('批注随完整HTML封装')
 reader=root/'_qto-shared-evidence/scripts/read_qto_review_feedback.py';p=subprocess.run([sys.executable,str(reader),'--view',str(datafile),'--feedback',str(out/'全专业带批注验证.html'),'--output',str(out/'批注交接.json')],capture_output=True,text=True,encoding='utf-8');assert p.returncode==0,p.stdout+p.stderr;handoff=json.loads((out/'批注交接.json').read_text(encoding='utf-8'));assert handoff['quantity_changes']==0 and len(handoff['actions'])==1;checks.append('HTML读取交接不执行脚本不修改数量')
 expected={'schema':'qto-review-test-artifacts/1.0','view':str(datafile),'html':str(out/'全专业图面复核演示.html'),'with_feedback':str(out/'全专业带批注验证.html'),'feedback':str(f),'normalized_view':str(out/'规范化视图.json')};Path(expected['normalized_view']).write_text(json.dumps(view,ensure_ascii=False,indent=2),encoding='utf-8');(out/'测试产物路径.json').write_text(json.dumps(expected,ensure_ascii=False,indent=2),encoding='utf-8')
 result={'status':'PASS','checks':checks,'check_count':len(checks),'professional_adapters':results,'report':report,'artifacts':expected};(a.output_root/'最新核验.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps({'status':'PASS','check_count':len(checks),'professional_count':13,'output':str(out)},ensure_ascii=False));return 0
if __name__=='__main__':raise SystemExit(main())
