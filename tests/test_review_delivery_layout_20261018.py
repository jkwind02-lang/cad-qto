"""Shared delivery UI contract: synthetic project with independent list and exact row location."""
import copy
import base64
import struct
import zlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'skills/_qto-shared-evidence/scripts'))
sys.path.insert(0,str(Path(__file__).parent))
import build_qto_review_html as builder
import qto_review_contract as contract
import review_delivery_fixture as fixture

def layout_view():
    v=fixture.view();d=v['delivery']
    def chunk(kind,data):return struct.pack('>I',len(data))+kind+data+struct.pack('>I',zlib.crc32(kind+data))
    png=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',1,1,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(b'\x00\xff\xff\xff\x00'))+chunk(b'IEND',b'')
    v['scenes'][0]['raster_data_uri']='data:image/png;base64,'+base64.b64encode(png).decode()
    v['scenes'][0]['background']=[{'kind':'line','points':[[0,25],[100,25]],'dash':[2,1],'width':1.2,'color':'#c0c0c0'},
        {'kind':'polygon','points':[[10,12],[12,12],[12,14]],'fill':'#eeeeee'},
        {'kind':'text','point':[15,15],'text':'旋转中文','height':0.6,'rotation':30,'width_factor':0.8},
        {'kind':'text','point':[20,15],'text':'变换中文','height':0.6,'text_axes':[[1,0.2],[0.1,1]]}]

    extra=copy.deepcopy(v['objects'][0]);extra.update(id='EXTRA',catalog_key='EXTRA',title='第二处管段',points=[[10,20],[90,20]])
    third=copy.deepcopy(v['objects'][0]);third.update(id='PIPE3',catalog_key='PIPE3',title='另一支管',points=[[10,80],[90,80]])
    unlocated=copy.deepcopy(v['objects'][0]);unlocated.update(id='U',catalog_key='U',title='无定位管段',kind='unlocated',scene_id=None,points=[])
    v['objects'] += [extra,third,unlocated];d['current_member_keys'] += ['EXTRA','PIPE3','U']
    d['quantity_rows'][0]['member_keys']=['PIPE','EXTRA']
    row=copy.deepcopy(d['quantity_rows'][0]);row.update(id='M3',title='另一支管明细',quantity=8,member_keys=['PIPE3']);d['quantity_rows'].append(row)
    row=copy.deepcopy(d['quantity_rows'][0]);row.update(id='M4',title='无可靠定位明细',quantity=5,member_keys=['U']);d['quantity_rows'].append(row)
    def item(i,title,quantity,original,unit='m',current=(),reference=()):
        return {'id':i,'title':title,'unit':unit,'quantity':quantity,'original_quantity':original,'scope':fixture.scope('WL1','WL2'),
                'coverage':{'state':'partial','computed_scope':['WL1'],'uncomputed_scope':['WL2']},'current_row_ids':list(current),'reference_row_ids':list(reference),'historical_row_ids':[],
                'dimensions':{'楼栋':'合成仓库','系统':'支管'},'display':{'specification':'合成规格，不代表工程采用'}}
    d['items']=[item('I1','一对多关联清单',20,18,current=['M1','M3'],reference=['R1','R2']),item('I2','原清单无图无量',None,6),item('I3','无可靠定位清单',5,5,current=['M4']),item('I4','设备数量',2,2,unit='台',current=['M2'])]
    d['items'][0]['historical_row_ids']=['H1']
    d['items'][1]['coverage']={'state':'unknown','computed_scope':[],'uncomputed_scope':['WL1','WL2']}
    d['items'][3]['scope']=fixture.scope('泵房');d['items'][3]['coverage']={'state':'complete','computed_scope':['泵房'],'uncomputed_scope':[]}
    for i in range(1,104):d['items'].append(item('PAGE'+str(i),'末页清单'+str(i),None,1))
    for x in d['items']:
        if x['quantity'] is None:x['coverage']={'state':'unknown','computed_scope':[],'uncomputed_scope':['WL1','WL2']}
    statitems=[{'item_id':x['id'],'unit':x['unit'],'current_quantity':x['quantity'],'original_quantity':x['original_quantity'],'coverage_state':x['coverage']['state'],'comparison_state':'not_comparable','reason':'范围尚未逐段证实一致，不能默认可比','delta':None,'rate':None,'source_segments':[],'tags':['all','not_comparable']} for x in d['items']]
    d['comparison_statistics']={'schema':'qto-review-statistics/1.0','total_items':len(d['items']),'overlapping_slices':True,'items':statitems,'counts':{'all':107,'not_comparable':107},'by_unit':[{'unit':'m','total_items':106,'counts':{}},{'unit':'台','total_items':1,'counts':{}}], 'slices':[{'id':'all','title':'全部清单','item_ids':[x['id'] for x in d['items']],'count':107},{'id':'not_comparable','title':'不可比','item_ids':[x['id'] for x in d['items']],'count':107}]}
    d['coverage_check']={'schema':'qto-review-coverage/1.0','status':'unknown','baseline_state':'unavailable','reason':'缺少设计覆盖基准，不能推断无遗漏','items':[{'item_id':x['id'],'status':'unknown','expected_scope':[],'covered_scope':[],'missing_scope':[],'row_ids':x['current_row_ids'],'findings':[]} for x in d['items']],'findings':[],'counts':{}}
    return v

def test_shared_delivery_layout_and_exact_member_selection(tmp_path):
    v=layout_view();source=tmp_path/'另一项目通用输入.json';source.write_text(json.dumps(v,ensure_ascii=False),encoding='utf-8')
    output=tmp_path/'另一项目共享直出.html';builder.build(source,output,feedback_url='')
    normalized=contract.normalize_view(v);source.write_text(json.dumps(normalized,ensure_ascii=False),encoding='utf-8')
    p=subprocess.run(['node',str(ROOT/'tests/run_review_delivery_layout_ui_tests.mjs'),'--html',str(output),'--view',str(source)],capture_output=True,encoding='utf-8',stdin=subprocess.DEVNULL)
    assert p.returncode==0,p.stdout+p.stderr
