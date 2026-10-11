"""Model-selected original drawing objects, converted to native calculator fields.

No expected takeoff values or test answers are used. All numerical dimensions,
coordinates, nominal diameters and elevations are read from the original facts.
The explicitly selected handles record this model's current drawing interpretation.
"""
import json, hashlib, re, sys, datetime, math
from pathlib import Path
ROOT=Path(__file__).resolve().parent
INP=ROOT.parent/'inputs'
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def read(p):return json.loads(Path(p).read_text())
def write(p,x):Path(p).write_text(json.dumps(x,ensure_ascii=False,indent=2))
def rows(case):return {r['entity_handle']:r for r in read(ROOT/case/'observed_raw_facts.json')['entities']}
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def src(case):return str(INP/case/'drawing.dxf')

def prepare_pipe():
    r=rows('pipe'); origin=src('pipe'); digest=sha(origin)
    selected=['A0','A1','A2','A3','A4']
    notes={h:r[h]['text'] for h in ['95','96','97','98','99','F8','F9','FA','FB','FC','FD','FE','FF','100','101']}
    candidates=[]; decisions=[]
    for h,e in r.items():
        if e['layer']!='PIPING' or e['entity_type']!='LINE':continue
        space='F1' if h in ['A0','A1','D3'] else ('F2' if h in ['A2','A3','A4'] else ('SYSTEM-NTS' if h in ['D6','D7','D8','D9'] else 'LEGEND'))
        oid='SEG-CAD-'+h
        candidates.append({'object_id':oid,'system':'DRN' if h in ['D3','EE'] else 'SUP','entity_type':'LINE','start':[v*.001 for v in e['start']],'end':[v*.001 for v in e['end']],'length':e['raw_length']*.001,'space_id':space,'source_ids':['SRC-P01','CAD-'+h]})
        if h in selected:decision='include'; basis='P-01 plan '+space+' new SUP centerline: direct SUP callout, continuous route from named end/riser, plan geometry and dimensions agree; actual model visual inspection.'
        elif h=='D3':decision='exclude';basis='Existing DRN DN80 at unmarked crossing. CAD-D4, CAD-D5 and note CAD-FF explicitly exclude DRN from SUP.'
        elif h in ['D6','D7','D8','D9']:decision='exclude';basis='System diagram NOT TO SCALE CAD-99/CAD-FC; same S-IN/R-A/RED-A/U-OUT route. Use topology and CL labels, never add schematic drawn lengths.'
        else:decision='exclude';basis='Legend examples only CAD-EB and note CAD-101; no installed physical centerline.'
        decisions.append({'object_id':oid,'decision':decision,'basis':basis,'source_id':'SRC-P01'})
    evidence=[{'evidence_id':'EV-PNG','path':str(INP/'pipe'/'drawing.png'),'sha256':sha(INP/'pipe'/'drawing.png'),'source_sha256':digest,'kind':'drawing_view','locator':'P-01 full sheet; F1 left, F2 middle, system right, notes below','observation':'Host view_image actually displayed the supplied PNG. S-IN to R-A to reducer to U-OUT; DRN crosses without junction. System diagram gives pipe CL elevations.'}, {'evidence_id':'EV-CAD','path':str(ROOT/'pipe'/'observed_raw_facts.json'),'sha256':sha(ROOT/'pipe'/'observed_raw_facts.json'),'source_sha256':digest,'kind':'cad_property','locator':'Model; CAD-A0..A4; CAD-AD..B1; CAD-E0/E1; CAD-F8..101','observation':'Exact source line geometry, text and coordinates retained by original handle.'}]
    entries=[]
    for d in decisions:
        h=d['object_id'].replace('SEG-CAD-','')
        entries.append({'adjudication_id':'MR-'+h,'subject_id':'RULE-'+h,'professional_owner':'qto-pipe-networks','decision':d['decision'],'status':'active','decision_origin':'model','recognized_by':'GPT-6 Astra Pro / ai_baseline_takeoff','recognized_at':now(),'source_id':'SRC-P01','evidence_ids':['EV-PNG','EV-CAD'],'object_ids':['CAD-'+h],'basis':d['basis'],'scope_basis':'New SUP S-IN to U-OUT, including R-A between F1 and F2, P-01 Rev A notes 1-10.','identity_basis':'Original CAD handle and named plan/system identifiers; each physical extent has one quantity outlet.','cross_check':'Visual PNG plus source DXF geometry/text; not layer or proximity alone.','uncertainties':[]})
    ledger={'schema_version':'qto-model-recognition-ledger/1.0','ledger_id':'MODEL-PIPE-P01','ledger_version':'1','source_sha256':digest,'adoption_scope':'working','review_status':'unreviewed','evidence':evidence,'entries':entries,'notes':notes,'true_human_intervention':[]}
    write(ROOT/'pipe'/'model_recognition.json',ledger)
    write(ROOT/'pipe'/'object_audit.input.json',{'schema_version':'qto-pipe-object-selection-input/1.0','audit_id':'AUDIT-P01','unit':'m','parameters':{'connection_tolerance':.001,'repeat_min_count':3,'signature_precision':9},'candidates':candidates,'dispositions':decisions,'source_ids':['SRC-P01','MODEL-PIPE-P01']})

def assemble_pipe():
    r=rows('pipe'); digest=sha(src('pipe'))
    ledger=ROOT/'pipe'/'model_recognition.json'
    recognition={'schema_version':'qto-model-recognition-ledger/1.0','ledger_path':str(ledger),'ledger_sha256':sha(ledger),'decision_origin':'model','recognized_by':['GPT-6 Astra Pro / ai_baseline_takeoff'],'adoption_scope':'working','review_status':'unreviewed','formal_adoption_allowed':False}
    segments=[]; matches=[]
    for h,t,space in [('A0','AD','F1'),('A1','AE','F1'),('A2','AF','F2'),('A3','B0','F2'),('A4','B1','F2')]:
        e=r[h];sid='SEG-CAD-'+h;dn='DN'+re.search(r'DN\s*(\d+)',r[t]['text']).group(1)
        segments.append({'segment_id':sid,'physical_id':'P01-'+space+'-'+h,'extent_id':'whole_original_line','system':'SUP','space_id':space,'start':[v*.001 for v in e['start']],'end':[v*.001 for v in e['end']],'length':e['raw_length']*.001,'source_ids':['SRC-P01','CAD-'+h],'dn_seeds':[]})
        matches.append({'candidate_id':'MATCH-'+t+'-'+h,'segment_id':sid,'dn':dn,'source_id':'CAD-'+t,'disposition':'seed','evidence_level':'confirmed','method':'model_direct_plan_callout_binding','threshold_sensitive':False,'reason':'Visually aligned direct SUP callout on this uninterrupted plan segment; corroborated by RED-A boundary and system diagram; no nearest-distance threshold used.','diagnostics':{'text_record':'CAD-'+t,'plan':space,'text':r[t]['text']}})
    elev_sources=[]; elev_ends=[]
    for h,floor in [('E0','F1'),('E1','F2')]:
        v=float(re.search(r'\+([0-9.]+)\s*m',r[h]['text']).group(1));ref='R-A-'+floor+'-PIPE-CL';fid='FACT-'+h
        f={'id':fid,'value':v,'unit':'m','datum_id':'P01-PROJECT-DATUM','reference_id':ref,'measurement_kind':'pipe_center','system':'SUP','level':'confirmed'}
        elev_sources.append({'id':'SRC-'+h,'path':src('pipe'),'sha256':digest,'locator':'P-01 Model CAD-'+h+' bbox='+str(r[h]['bbox'])+'; note CAD-FB; '+r[h]['text'],'facts':[f]})
        elev_ends.append({k:v for k,v in f.items() if k!='id'}|{'fact_id':fid,'source_ids':['SRC-'+h],'basis':'Direct original PIPE CL label; project datum explicitly defined at CAD-FB. No FFL substitution.'})
    route={'id':'ROUTE-R-A','measurement_kind':'route_shape','route_kind':'vertical','from_reference_id':elev_ends[0]['reference_id'],'to_reference_id':elev_ends[1]['reference_id'],'system':'SUP','level':'confirmed'}
    elev_sources.append({'id':'SRC-RA-ROUTE','path':src('pipe'),'sha256':digest,'locator':'P-01 CAD-DE/DF straight vertical; CAD-FA same physical riser; CAD-FE no unshown offsets','facts':[route]})
    elevations={'schema':'qto-elevation-link/1.0','calculation_id':'CALC-RA-CL','system':'SUP','from':elev_ends[0],'to':elev_ends[1],'target_datum':'P01-PROJECT-DATUM','sources':elev_sources,'datum_transforms':[],'route':{'route_id':'ROUTE-R-A','kind':'vertical','source_ids':['SRC-RA-ROUTE'],'basis':'CAD-DE/DF explicitly straight vertical between indicated pipe CLs; same riser by note 3.','level':'confirmed'}}
    write(ROOT/'pipe'/'elevation_facts.input.json',elevations)
    payload={'schema_version':'qto-pipe-network-inference-input/1.2','network_id':'P01-NEW-SUP','unit':'m','parameters':{'snap_tolerance':.001,'gap_max':.05,'terminal_attach_max':.01,'valve_attach_max':.01},'recognition_basis':recognition,'object_selection':read(ROOT/'pipe'/'object_audit.output.json'),'segments':segments,'roots':[{'root_id':'S-IN','system':'SUP','space_id':'F1','segment_id':'SEG-CAD-A0','point':segments[0]['start'],'source_id':'CAD-A9;CAD-F8','evidence_level':'confirmed','root_role':'network_root'}],'terminals':[],'change_nodes':[{'change_id':'RED-A','system':'SUP','space_id':'F2','segment_id':'SEG-CAD-A2','point':segments[2]['end'],'evidence_level':'confirmed','source_id':'CAD-B2;CAD-B3;CAD-FE'}],'valves':[],'gap_exclusions':[],'dn_match_candidates':matches,'dn_match_policy':{'policy_id':'P01-VISUAL-DIRECT-CALLOUTS','detected_candidate_count':len(matches),'source_ids':['CAD-AD','CAD-AE','CAD-AF','CAD-B0','CAD-B1','CAD-B3','CAD-FE'],'basis':'All five relevant plan SUP callouts explicitly mapped; system duplicates are corroboration, DRN outside scope, legend not installed.','sensitivity_scenarios':[]},'endpoint_handoff':{'handoff_id':'P01-LENGTH-ONLY','required':False,'source_instance_count':0,'source_endpoint_port_count':0,'source_physical_instance_count':0,'delivered_count':0,'not_applicable_count':0,'unresolved_count':0,'source_workpackage_complete':True,'source_id':'CAD-100;CAD-101','reason':'No equipment/fixture point workpackage in supplied scope. S-IN and U-OUT are measurement limits only; no appliance quantities requested.'},'cross_view_links':[{'link_id':'R-A-F1-F2','physical_id':'P01-R-A','system':'SUP','a':{'segment_id':'SEG-CAD-A1','end':'end'},'b':{'segment_id':'SEG-CAD-A2','end':'start'},'basis':'Same R-A identifier CAD-A6/A8/DC and explicit note CAD-FA; F1 end and F2 start are unique matching riser ports.','source_ids':['CAD-A5','CAD-A6','CAD-A7','CAD-A8','CAD-DC','CAD-FA'],'dn':'DN'+re.search(r'DN\s*(\d+)',r['DD']['text']).group(1),'dn_continuity':True,'dn_continuity_source_id':'CAD-DD;CAD-AE;CAD-AF','elevation_facts':elevations}],'scope_notes':{'quantity_basis':'continuous pipe centerline, inclusive through inline fittings; no fittings allowances; from S-IN to U-OUT only','excluded':['DRN','system schematic drawn lengths','legend geometry','architectural/dimension lines','symbols as lengths','supports','insulation','price','waste'],'source_sha256':digest},'source_catalog':{'SRC-P01':{'path':src('pipe'),'sha256':digest},'cad_facts':str(ROOT/'pipe'/'observed_raw_facts.json')}}
    write(ROOT/'pipe'/'network.input.json',payload)

def prepare_surfaces():
    r=rows('surfaces');original=src('surfaces');digest=sha(original)
    g=r['9A']['geometry'];bottom=g['parts'][0]
    if bottom['start'][1]!=bottom['end'][1]:raise ValueError('Selected base edge is not horizontal')
    outer=r['9A']['bbox'];division=r['9B']['start'][0]
    edge={'edge_id':'EDGE-RCP-SOUTH','coordinate_system_id':'A01-MODEL-PROJECTION','start':bottom['start'],'end':bottom['end'],'unit':'mm','source_handle':'9A','source_part_index':0,'source_sha256':digest}
    origins={'CL-11':outer[0],'CL-24':division}; limits={'CL-11':division,'CL-24':outer[2]}
    frames={};dimensions={}
    for mat in origins:
        xmin=origins[mat];xmax=limits[mat]
        frames[mat]={'coordinate_system_id':'A01-MODEL-PROJECTION','origin_m':[xmin*.001,outer[1]*.001,0.],'x_axis':[1.,0.,0.],'up_axis':[0.,1.,0.],'source_edge_id':edge['edge_id'],'source_edge_direction':'same','source_edge_interval_m':[(xmin-outer[0])*.001,(xmax-outer[0])*.001],'vertical_range_m':[0.,(outer[3]-outer[1])*.001],'vertical_origin_m':0.}
        dimensions[mat]={'length_m':(xmax-xmin)*.001,'height_m':(outer[3]-outer[1])*.001}
    geometry={'source_path':original,'source_sha256':digest,'raw_unit':'mm','transformation':'Original source WCS millimetres to m with factor 0.001; source Model XY represents horizontal ceiling projection; no scaling of S-2.','edges':{edge['edge_id']:edge},'surface_frames':frames,'outer_handle':'9A','divider_handle':'9B','raw_entities':{h:r[h] for h in ['9A','9B','9E','AE','BE','BF','C6','CC','D2','D8','A7','AD','B7','BD']},'recognized_by':'GPT-6 Astra Pro / ai_baseline_takeoff','review_status':'unreviewed'}
    geo_path=ROOT/'surfaces'/'bound_geometry.json';write(geo_path,geometry)
    coverage={'source_path':original,'source_sha256':digest,'review_status':'unreviewed','recognized_by':'GPT-6 Astra Pro / ai_baseline_takeoff','actual_visual_review':'Supplied full-sheet PNG viewed with host view_image; original CAD entities and notes checked.','included':[{'surface':'SURF-CL-11','plan':'RCP-01','boundary_handles':['9A','9B'],'material_handles':['9C','E7','E8'],'opening_handles':['9E','A1','E9']},{'surface':'SURF-CL-24','plan':'RCP-01','boundary_handles':['9A','9B'],'material_handles':['9D','E7','E8'],'retained_access':['AE','B1','EA','EB','E3','E5']}],'exclusions':[{'handles':['BE','BF','C0','DD','E1','E2','EC'],'reason':'Low screen P-1 top 1.700 is below ceiling finish 2.800; continuous finish above. Its projected footprint does not divide/deduct ceiling.'},{'handles':['D9','DC','E3','F1','F2','F6'],'reason':'Detail schematic and legend are supporting evidence, not additional installed finishes.'}],'scope_closed':'Outer internal finish rectangle and material division are complete; only SH-1 is a deductive opening; AP-1 retained; no returns or extra layers per EE/EF/F0.','unresolved_in_requested_quantity_scope':[],'raw_extraction_limit':'Unused _CLOSEDFILLED SOLID definition unsupported in neutral extractor; no INSERT exists in Model, not part of ceiling boundary.'}
    coverage_path=ROOT/'surfaces'/'coverage.json';write(coverage_path,coverage)
    def source(sid,path,role,file_role,neutral):return {'source_id':sid,'path':str(path),'sha256':sha(path),'file_role':file_role,'source_role':role,'selection':'selected','source_type':'本轮原图证据','reason':'本任务指定原图或由本轮读取直接形成的证据','neutral_facts':neutral,'not_used_impact':'不使用将失去相应对象定位或范围依据'}
    sources=[source('SRC-A01',original,'project_drawing','dxf','原图边界、材料标签、说明、详图、尺寸与单位'),source('SRC-GEO',geo_path,'calculation_evidence','json','源边及本次模型解释的局部坐标系，保留原实体和哈希'),source('SRC-COVERAGE',coverage_path,'coverage_report','json','本轮模型沿图核对的纳入、扣减、保留与排除范围')]
    def ev(eid,sid,cl,loc,fact,keys):return {'evidence_id':eid,'source_id':sid,'evidence_class':cl,'fact_status':'reference','locator':loc,'fact':fact,'extraction':'Host image visual review plus native CAD extraction and source-bound coordinate conversion','quality':'本轮模型工作识别；未人工复核','object_keys':keys,'limitation':'可复核工作证据；不宣称工程正式采用或真实工程准确率'}
    evidence=[ev('EV-ID','SRC-A01','source_identity','A-01 Model CAD-95/96',r['95']['text']+'; '+r['96']['text'],['A01'])]
    items=[]
    for mat,label in [('CL-11','acoustic ceiling panel / 吸声吊顶板'),('CL-24','painted gypsum-board ceiling finish / 涂饰石膏板吊顶饰面')]:
        surf='SURF-'+mat;layer='LAYER-'+mat;edgeid=edge['edge_id']
        evidence.extend([ev('EV-GEO-'+mat,'SRC-GEO','surface_geometry','/surface_frames/'+mat+'; /edges/'+edgeid+'; CAD-9A/9B; dimensions C6/CC/D8','Boundary from outer 9A and full-height material division 9B, located on RCP-01 internal finish boundary.',[surf,edgeid]),ev('EV-LAYER-'+mat,'SRC-A01','layer_assignment','RCP-01 '+('CAD-9C' if mat=='CL-11' else 'CAD-9D')+'; finish schedule CAD-E7/E8',mat+' = '+label+'; divider is solid finish line, not P-1 projection.',[surf,layer]),ev('EV-COVER-'+mat,'SRC-COVERAGE','coverage_complete','coverage.json /included; /exclusions; A-01 RCP-01 and S-2','Requested one underside finish layer by horizontal net material area; source notes explicitly close excluded accessory scope.',[surf])])
        refs=['EV-ID','EV-GEO-'+mat,'EV-LAYER-'+mat,'EV-COVER-'+mat]
        openings=[]
        if mat=='CL-11':
            b=r['9E']['bbox'];o=frames[mat]['origin_m']
            openings=[{'opening_id':'SH-1','rectangle_m':[b[0]*.001-o[0],b[1]*.001-o[1],b[2]*.001-o[0],b[3]*.001-o[1]]}]
            evidence.append(ev('EV-OPEN-SH1','SRC-GEO','opening_union','raw_entities/9E; A-01 CAD-A1, CAD-E9, dimensions CAD-A7/AD','The only deductive shaft SH-1 has a closed rectangle inside CL-11; exact footprint deducted once; crossing diagonals are symbol only.',[surf,'SH-1']))
            refs.append('EV-OPEN-SH1')
        else:
            evidence.append(ev('EV-RETAIN-AP1','SRC-A01','requirement','CAD-AE/B1 and CAD-EA/EB; S-2 CAD-E3/E5','AP-1 flush removable panel carries the same finish as CL-24 and is explicitly retained in this material area.',[surf,'AP-1',layer]))
            refs.append('EV-RETAIN-AP1')
        item={'item_id':'QTY-'+mat,'name':mat+' '+label+'净面积','object_type':'ceiling_finish','physical_surface_id':surf,'layer_id':layer,'side_scope':'下表面饰面','measurement_basis':'horizontal_projection','method':'rectangle_openings_net','unit':'m²','scope_status':'complete','adoption_role':'reference','quantity_role':'physical_area','result_use':'本合成原图限定范围内的模型工作计算，候选规则试用；未人工复核，不进入正式采用量','evidence_refs':refs,'rule_refs':['R-SE-001','R-SE-002','R-SE-003','R-SE-008'],'unresolved_refs':[],'coverage_evidence_ref':'EV-COVER-'+mat,'frame_binding':{'frame_evidence_ref':'EV-GEO-'+mat,'source_pointer':'/surface_frames/'+mat,'edge_evidence_ref':'EV-GEO-'+mat,'edge_source_pointer':'/edges/'+edgeid},'local_coordinate_frame':frames[mat],'inputs':dimensions[mat]|{'openings':openings,'other_deductions_area_m2':0.,'reveal_or_return_area_m2':0.}}
        if openings:item['opening_union_evidence_ref']='EV-OPEN-SH1'
        items.append(item)
    rules=[{'rule_id':i,'name':n,'source':'当前qto-surfaces-envelope规则及A-01说明','decision':'试用','maturity':m,'execution':'按本图明确设计物理口径形成参考工作量','evidence_locator':l} for i,n,m,l in [('R-SE-001','表面/层/侧别/口径唯一','试运行','CAD-E7/E8/ED'),('R-SE-002','毛面扣实际洞口并集','试运行','CAD-9E/E9；AP-1按EA/EB保留'),('R-SE-003','天棚水平净饰面面积','候选','CAD-ED/EE/EF/F0；S-2的E0/E1/E2'),('R-SE-008','范围闭合核对','试运行','coverage.json')]]
    manifest={'manifest_version':'qto-surfaces-envelope-manifest/1.0','task':{'project_id':'A01-SYNTHETIC-STUDIO','business_discipline':'建筑吊顶饰面','main_objects':'工作室吊顶下表面单层饰面','system':'Studio suspended ceiling','building':'图示工作室','zone':'RCP-01 CL-11 与 CL-24','drawing_type':'反射平面、做法表、说明、S-2详图','drawing_version':'A-01 REV A','output_dimension':'按材料的水平净饰面面积','applicable_rule_status':'confirmed'},'sources':sources,'evidence':evidence,'rules':rules,'unresolved':[],'items':items,'recognition_status':{'origin':'model','review_status':'unreviewed','formal_adoption_allowed':False}}
    write(ROOT/'surfaces'/'surfaces.input.json',manifest)

if __name__=='__main__':
    if sys.argv[1]=='prepare':prepare_pipe();prepare_surfaces()
    elif sys.argv[1]=='assemble_pipe':assemble_pipe()
    else:raise ValueError('Unknown phase')
