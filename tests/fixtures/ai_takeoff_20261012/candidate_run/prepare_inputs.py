#!/usr/bin/env python3
"""Source-bound task adapter. Engineering selections below were made by the model
after actually viewing both supplied PNGs. This is not an automatic classifier.
Coordinates, dimensions, DN values and elevations are fetched from source records.
Native plugin programs calculate the resulting quantities.
"""
import datetime, hashlib, json, math, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
INPUTS = ROOT.parent / 'inputs'
PLUGIN = Path('/workspace/scratch/f95ce6ecc4b5/cad-qto-ai-candidate')
MODEL = 'ChatGPT / GPT-6 Astra Pro; execution agent /root/ai_candidate_takeoff'

def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def save(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def load_records(task):
    p = ROOT/task/'neutral/中性布局图元.ndjson'
    rows = [json.loads(x) for x in p.read_text().splitlines() if x.strip()]
    source_sha = sha(INPUTS/task/'drawing.dxf')
    assert all(r['source_sha256'] == source_sha for r in rows)
    assert len({r['entity_handle'] for r in rows}) == len(rows)
    return {r['entity_handle']:r for r in rows}

def text_value(records, handle):
    r=records[handle]
    assert r['entity_type']=='TEXT'
    return r['text']

def line(records,handle):
    r=records[handle]
    assert r['entity_type']=='LINE'
    p=r['geometry']['parts']
    assert len(p)==1 and p[0]['kind']=='line'
    return p[0]

def rectangle(records,handle):
    r=records[handle]
    g=r['geometry']
    assert g['closed'] and len(g['parts'])==4
    pts=[p['start'][:2] for p in g['parts']]
    xs,ys=sorted({p[0] for p in pts}),sorted({p[1] for p in pts})
    assert len(xs)==2 and len(ys)==2
    assert set(map(tuple,pts))=={(x,y) for x in xs for y in ys}
    return [xs[0],ys[0],xs[-1],ys[-1]]

def source_rows(task):
    return [{'path':str(INPUTS/task/name),'sha256':sha(INPUTS/task/name),'bytes':(INPUTS/task/name).stat().st_size,'role':role} for name,role in [('drawing.dxf','authoritative original drawing'),('drawing.png','viewed unmarked drawing image'),('task.md','user task')]]

now=datetime.datetime.now(datetime.timezone.utc).isoformat()

# PIPE: explicit model selections from the observed plan / system / legend.
p=load_records('pipe')
assert 'millimetres' in text_value(p,'96')
assert 'same physical riser' in text_value(p,'FA')
assert 'straight vertical' in text_value(p,'DE')
levels={k:float(re.search(r'\+([0-9.]+)\s*m',text_value(p,h)).group(1)) for k,h in [('F1','E0'),('F2','E1')]}
r1=p['A5']['geometry']['parts'][0]['center']
r2=p['A7']['geometry']['parts'][0]['center']
shift=[r2[i]-r1[i] for i in range(2)]
assert shift[1]==0
selection=[('A0','AD','BA','F1','S-IN to first plan elbow'),('A1','AE','C0','F1','first plan elbow to R-A'),('A2','AF','C6','F2','R-A to RED-A centre'),('A3','B0','CC','F2','RED-A centre to upper plan elbow'),('A4','B1','D2','F2','upper plan elbow to U-OUT')]
segments=[]
dn_candidates=[]
details=[]
for handle,dnh,dimh,level,name in selection:
    raw=line(p,handle)
    def mapped(pt):
        return [(pt[0]-(shift[0] if level=='F2' else 0))/1000,(pt[1]-(shift[1] if level=='F2' else 0))/1000,levels[level]]
    start,end=mapped(raw['start']),mapped(raw['end'])
    length_m=math.dist(start,end)
    dimension_mm=float(text_value(p,dimh))
    assert math.isclose(length_m*1000,dimension_mm,abs_tol=1e-7)
    dn='DN'+re.search(r'DN\s*(\d+)',text_value(p,dnh)).group(1)
    sid='PIPE-'+handle
    refs=['CAD-'+handle,'CAD-'+dnh,'CAD-'+dimh,'CAD-F9','CAD-FD']
    segments.append({'segment_id':sid,'system':'SUP','start':start,'end':end,'length':length_m,'source_ids':refs,'physical_id':sid,'extent_id':'centreline','space_id':'P01-SUP-3D','dn_seeds':[]})
    dn_candidates.append({'candidate_id':'DN-'+handle,'segment_id':sid,'dn':dn,'source_id':'CAD-'+dnh,'disposition':'seed','evidence_level':'inferred','assumption_id':'A-MODEL-PIPE','method':'model visual and source-segment binding','threshold_sensitive':False,'reason':'Explicit adjacent plan label and continuous segment bounded by elbows/reducer; cross-checked against system labels.'})
    details.append({'object_id':sid,'view':level+' plan','location':name,'entity_handle':handle,'layout':'Model','source_ids':refs,'source_start_mm':raw['start'],'source_end_mm':raw['end'],'raw_length_mm':dimension_mm,'dimension_handle':dimh,'dn':dn,'dn_text_handle':dnh,'calculation_start_m':start,'calculation_end_m':end,'length_m':length_m,'quantity_type':'horizontal','source_sha256':sha(INPUTS/'pipe/drawing.dxf')})
vstart=[r1[0]/1000,r1[1]/1000,levels['F1']]
vend=[r1[0]/1000,r1[1]/1000,levels['F2']]
vdn='DN'+re.search(r'DN\s*(\d+)',text_value(p,'DD')).group(1)
vrefs=['CAD-A5','CAD-A6','CAD-A7','CAD-A8','CAD-DB','CAD-DC','CAD-DD','CAD-DE','CAD-DF','CAD-E0','CAD-E1','CAD-FA','CAD-FB','CAD-FE']
segments.append({'segment_id':'PIPE-RA-V','system':'SUP','start':vstart,'end':vend,'length':math.dist(vstart,vend),'source_ids':vrefs,'physical_id':'R-A','extent_id':'F1-CL-to-F2-CL','space_id':'P01-SUP-3D','dn_seeds':[]})
dn_candidates.append({'candidate_id':'DN-RA-V','segment_id':'PIPE-RA-V','dn':vdn,'source_id':'CAD-DD','disposition':'seed','evidence_level':'inferred','assumption_id':'A-MODEL-PIPE','method':'same identified physical riser, system DN label and plan-to-system correspondence','threshold_sensitive':False,'reason':'R-A links both plans and the straight vertical between pipe CL elevations.'})
details.append({'object_id':'PIPE-RA-V','view':'F1/F2 plans + system diagram','location':'One R-A riser, F1 PIPE CL to F2 PIPE CL','entity_handles':['A5','A7','DB','D7'],'source_ids':vrefs,'layout':'Model','dn':vdn,'quantity_type':'vertical','start_pipe_cl_m':levels['F1'],'end_pipe_cl_m':levels['F2'],'calculation_start_m':vstart,'calculation_end_m':vend,'length_m':math.dist(vstart,vend),'formula':'abs(F2 PIPE CL - F1 PIPE CL)','drawn_system_line_length_used':False,'source_sha256':sha(INPUTS/'pipe/drawing.dxf')})
exclude={'D3':'Existing DRN DN80; separate system and unmarked crossing is not a connection (D4,D5,FF).','D6':'NTS system view of F1 plan route; correspondence only (FC).','D7':'NTS system riser graphic; real vertical separately derived from E0,E1,DE,DF.','D8':'NTS system view before reducer; F2 source A2 owns quantity.','D9':'NTS system view after reducer; F2 sources A3,A4 own quantity.','EC':'Legend SUP example only (EB,101).','EE':'Legend DRN example only (EB,101).'}
assert set(h for h,r in p.items() if r['layer']=='PIPING')==set([x[0] for x in selection])|set(exclude)
audit_candidates=[{'object_id':s['segment_id'],'system':s['system'],'entity_type':'LINE','start':s['start'],'end':s['end'],'length':s['length'],'source_ids':s['source_ids'],'space_id':'P01-SUP-3D'} for s in segments]
dispositions=[{'object_id':s['segment_id'],'decision':'include','basis':'Model recognition of new SUP scope; source-bound observed route / pipe-CL vertical, no human sign-off.','source_id':'LEDGER-PIPE-01'} for s in segments]
for h,reason in exclude.items():
    g=line(p,h); start=[v/1000 for v in g['start']];end=[v/1000 for v in g['end']]
    audit_candidates.append({'object_id':'EXCLUDE-'+h,'system':'DRAWING-REFERENCE','entity_type':'LINE','start':start,'end':end,'length':math.dist(start,end),'source_ids':['CAD-'+h],'space_id':'P01-DRAWING'})
    dispositions.append({'object_id':'EXCLUDE-'+h,'decision':'exclude','basis':reason,'source_id':'LEDGER-PIPE-01'})
pipe_ledger={'schema_version':'qto-model-recognition-ledger/1.0','ledger_id':'LEDGER-PIPE-01','ledger_version':'1','source_sha256':sha(INPUTS/'pipe/drawing.dxf'),'adoption_scope':'working','review_status':'unreviewed','formal_adoption_allowed':False,'recognized_by':MODEL,'recorded_at_utc':now,'actual_observation':'Original supplied PNG was opened using view_image and visually inspected by this model before the source selections were authored. Native DXF neutral records supplied exact coordinates, text and handles.','image_path':str(INPUTS/'pipe/drawing.png'),'image_sha256':sha(INPUTS/'pipe/drawing.png'),'entries':[{'object_id':d['object_id'],'decision_origin':'model','decision':'include','evidence_ids':d['source_ids'],'scope_basis':d['location']} for d in details]+[{'object_id':'CAD-'+h,'decision_origin':'model','decision':'exclude','basis':reason} for h,reason in exclude.items()],'human_interventions':[],'unresolved_quantity_items':[],'limitations':['This task used live model image understanding plus a task-specific source adapter; it is not an independently benchmarked automatic detector.','No professional human review or business adoption was performed.']}
save(ROOT/'pipe/recognition_ledger.json',pipe_ledger)
basis={'schema_version':'qto-model-recognition-ledger/1.0','ledger_path':str(ROOT/'pipe/recognition_ledger.json'),'ledger_sha256':sha(ROOT/'pipe/recognition_ledger.json'),'decision_origin':'model','recognized_by':[MODEL],'adoption_scope':'working','review_status':'unreviewed','formal_adoption_allowed':False}
save(ROOT/'pipe/object_audit_input.json',{'schema_version':'qto-pipe-object-selection-input/1.0','audit_id':'AUDIT-PIPE-01','unit':'m','source_ids':['LEDGER-PIPE-01',sha(INPUTS/'pipe/drawing.dxf')],'parameters':{'connection_tolerance':0.000001},'candidates':audit_candidates,'dispositions':dispositions})
pipe_input={'schema_version':'qto-pipe-network-inference-input/1.2','network_id':'P01-WEST-SERVICE-SUPPLY','unit':'m','recognition_basis':basis,'parameters':{'snap_tolerance':0.000001,'gap_max':0.001,'terminal_attach_max':0.000001},'segments':segments,'dn_match_candidates':dn_candidates,'dn_match_policy':{'policy_id':'EXPLICIT-SOURCE-BINDINGS','detected_candidate_count':len(dn_candidates),'source_ids':['LEDGER-PIPE-01'],'thresholds':{'method':'explicit model-bound source labels; no nearest-distance classification'},'sensitivity_scenarios':[]},'change_nodes':[{'change_id':'RED-A','system':'SUP','point':segments[2]['end'],'evidence_level':'inferred','source_id':'CAD-B2/B3/FE'}],'roots':[{'root_id':'S-IN','system':'SUP','point':segments[0]['start'],'evidence_level':'inferred','source_id':'CAD-A9/AA/F8/100','root_role':'network_root'}],'terminals':[{'terminal_id':'U-OUT','system':'SUP','point':segments[4]['end'],'terminal_type':'measurement end port','port_dn':dn_candidates[4]['dn'],'evidence_level':'inferred','port_evidence_level':'inferred','source_id':'CAD-AB/AC/B1/100','propagation_scope':'interface_only'}],'endpoint_handoff':{'handoff_id':'PIPE-SOURCE-ENDPORT','required':False,'reason':'No point-object workpackage; the single U-OUT end port is source-derived solely to close this pipe measurement route.','source_instance_count':1,'source_endpoint_port_count':1,'source_physical_instance_count':1,'delivered_count':1,'not_applicable_count':0,'unresolved_count':0,'source_id':'CAD-AB/AC/100'},'valves':[],'gap_exclusions':[],'assumptions':[{'assumption_id':'A-MODEL-PIPE','type':'model source interpretation','basis':'Source drawing notes and explicit same-riser identity; no added design parameter.','status':'working/unreviewed'}]}
save(ROOT/'pipe/native_input_pre_audit.json',pipe_input)
save(ROOT/'pipe/source_bound_details.json',{'sources':source_rows('pipe'),'objects':details,'exclusions':[{'entity_handle':h,'reason':r} for h,r in exclude.items()],'cross_view_mapping':{'physical_riser':'R-A','F1_handle':'A5','F2_handle':'A7','system_handle':'DB','F2_plan_translation_mm_to_F1':[-x for x in shift],'mapping_basis_handles':['FA','DE','DF','E0','E1'],'system_lengths_not_to_scale':True},'rules':[{'rule_id':'R-PN-003','decision':'试用','maturity':'试运行'},{'rule_id':'R-PN-004','decision':'试用','maturity':'候选'},{'rule_id':'R-PN-005','decision':'试用','maturity':'候选'}],'unresolved':[],'human_interventions':[]})

# SURFACES: two material regions, one genuine opening, one retained access panel.
s=load_records('surfaces')
outer=rectangle(s,'9A'); divider=line(s,'9B'); shaft=rectangle(s,'9E'); access=rectangle(s,'AE')
split=divider['start'][0]
assert divider['end'][0]==split and sorted([divider['start'][1],divider['end'][1]])==[outer[1],outer[3]]
for actual,h in [(split-outer[0],'C6'),(outer[2]-split,'CC'),(outer[2]-outer[0],'D2'),(outer[3]-outer[1],'D8'),(shaft[2]-shaft[0],'A7'),(shaft[3]-shaft[1],'AD'),(access[2]-access[0],'B7'),(access[3]-access[1],'BD')]:
    assert math.isclose(actual,float(text_value(s,h)),abs_tol=1e-9)
assert 'retained' in text_value(s,'EB') and 'no ceiling deduction' in text_value(s,'EC')
surface_facts={'source_path':str(INPUTS/'surfaces/drawing.dxf'),'source_sha256':sha(INPUTS/'surfaces/drawing.dxf'),'raw_unit':'mm','conversion_to_m':0.001,'raw_boundary_handles':['9A','9B'],'raw_shaft_handle':'9E','raw_access_handle':'AE','raw_boundary_mm':outer,'raw_shaft_mm':shaft,'raw_access_panel_mm':access,'edges':{'EDGE-9A-0':{'edge_id':'EDGE-9A-0','coordinate_system_id':'A01-RCP-Model-metres','start':[outer[0]/1000,outer[1]/1000,0],'end':[outer[2]/1000,outer[1]/1000,0],'unit':'m','source_handle':'9A','source_geometry_part_index':0}},'surface_frames':{},'surfaces':{},'feature_decisions':{'SH-1':{'action':'deduct true open shaft footprint','source_handles':['9E','A1','A7','AD','E9']},'AP-1':{'action':'retain same-finish flush panel as material area','source_handles':['AE','B1','EA','EB','E3','E5']},'P-1':{'action':'retain ceiling continuous above low screen','source_handles':['BE','BF','C0','DD','E0','E1','E2','EC']}}}
surface_defs=[('CL-11',[outer[0],outer[1],split,outer[3]],'acoustic ceiling panel','9C'),('CL-24',[split,outer[1],outer[2],outer[3]],'painted gypsum-board ceiling finish','9D')]
for material,rect,name,labelh in surface_defs:
    frame={'coordinate_system_id':'A01-RCP-Model-metres','origin_m':[rect[0]/1000,rect[1]/1000,0],'x_axis':[1,0,0],'up_axis':[0,1,0],'source_edge_id':'EDGE-9A-0','source_edge_direction':'same','source_edge_interval_m':[(rect[0]-outer[0])/1000,(rect[2]-outer[0])/1000],'vertical_range_m':[0,(rect[3]-rect[1])/1000],'vertical_origin_m':0}
    surface_facts['surface_frames'][material]=frame
    surface_facts['surfaces'][material]={'bounds_mm':rect,'label_handle':labelh,'material':name,'local_coordinate_meaning':'x/y are the RCP CAD drawing-plane axes, not building elevation; ceiling elevation is independently noted +2.800.','openings':[{'opening_id':'SH-1','rectangle_m':[(shaft[0]-rect[0])/1000,(shaft[1]-rect[1])/1000,(shaft[2]-rect[0])/1000,(shaft[3]-rect[1])/1000]}] if material=='CL-11' else []}
save(ROOT/'surfaces/source_bound_geometry.json',surface_facts)
coverage={'model':MODEL,'recorded_at_utc':now,'review_status':'unreviewed','source_sha256':sha(INPUTS/'surfaces/drawing.dxf'),'observed_views':['Entire supplied PNG: RCP-01','Entire S-2 ceiling/screen/access detail','Finish schedule and all project notes','Legend'],'covered_surfaces':['SURF-CL-11','SURF-CL-24'],'material_boundary_handles':['9A','9B'],'quantity_feature_handles':['9E','AE','BE','BF'],'coverage_conclusion':'Outer finish boundary is closed; solid divider partitions it into two nonoverlapping materials. All tagged RCP features accounted for. S-2 supplies applicability only, not additional area.','excluded':['Detail drawing as an additional surface','Legend symbol as an opening','Low screen footprint as a ceiling deduction','Shaft reveals, vertical returns, extra layers, bulkheads, unshown projections per EE','Framing, substrate bodies, panel equipment counts, procurement/loss/prices per EF,F0 and task'],'unresolved_quantity_items':[],'neutral_pending_disposition':[{'record_id':'CAD-7B','block':'_CLOSEDFILLED','action':'noninstantiated default arrow-block definition; no INSERT entities occur in Model; not a finish or opening'},{'record_id':'CAD-98/CAD-99','action':'Native hatch-region adapter run; two successfully extracted regions. Area ownership is still determined by solid boundary and finish schedule.'}],'human_interventions':[],'actual_observation':'Both original task PNGs were actually viewed by this model. This record is model work, not human sign-off.'}
save(ROOT/'surfaces/coverage.json',coverage)

def src(id,path,role,file_role,reason):
    return {'source_id':id,'path':str(path),'sha256':sha(path),'file_role':file_role,'source_role':role,'source_type':file_role,'selection':'selected','reason':reason,'neutral_facts':'Current source identity, raw handles/coordinates/text, or model working interpretation as explicitly stated','not_used_impact':'No unsupported original-source substitution'}

sources=[src('SRC-DXF',INPUTS/'surfaces/drawing.dxf','project_drawing','dxf','User-supplied original A-01 Rev A'),src('SRC-GEOM',ROOT/'surfaces/source_bound_geometry.json','calculation_evidence','json','Source-bound coordinate conversion and selected surface frames'),src('SRC-COVER',ROOT/'surfaces/coverage.json','coverage_report','json','Model coverage statement, no human review'),src('SRC-REGIONS',ROOT/'surfaces/hatch_regions_selected.json','calculation_evidence','json','Native HATCH geometry evidence; no material semantics inferred by tool')]
ev=[]
def evidence(eid,source,kind,locator,fact,keys):
    ev.append({'evidence_id':eid,'source_id':source,'evidence_class':kind,'fact_status':'reference','locator':locator,'fact':fact,'extraction':'Actual model image observation with native DXF neutral coordinates and task adapter; no human confirmation','quality':'source-bound working evidence','object_keys':keys,'limitation':'Professional meaning is model interpretation of this drawing; not formal adoption.'})
evidence('EV-SOURCE','SRC-DXF','source_identity','Model TEXT 95,96','A-01 STUDIO SUSPENDED CEILING Rev A; native millimetres, full-size plan, schematic detail.',['SURF-CL-11','SURF-CL-24'])
items=[]
for material,rect,name,labelh in surface_defs:
    sid='SURF-'+material; lid='LAYER-'+material
    keys=[sid,'EDGE-9A-0']
    evidence('EV-GEOM-'+material,'SRC-GEOM','surface_geometry','/surface_frames/'+material+'; raw Model handles 9A,9B; dimensions C6,CC,D2,D8','Closed rectangle from the internal finish boundary and solid material divider; source-bound local frame.',keys)
    evidence('EV-LAYER-'+material,'SRC-DXF','layer_assignment','Model TEXT '+labelh+',E7,E8,ED; RCP-01','Material '+material+' is '+name+' in its bounded RCP zone; horizontal net finish area.',[sid,lid])
    evidence('EV-COVER-'+material,'SRC-COVER','coverage_complete','/covered_surfaces; whole RCP and S-2','All edges and tagged features accounted for in this material region.',[sid])
    refs=['EV-SOURCE','EV-GEOM-'+material,'EV-LAYER-'+material,'EV-COVER-'+material]
    openings=surface_facts['surfaces'][material]['openings']
    if openings:
        evidence('EV-OPEN-'+material,'SRC-GEOM','opening_union','/surfaces/'+material+'/openings; Model 9E,A1,A7,AD,E9','One rectangular actual shaft opening, deduplicated and wholly inside this zone.',[sid,'SH-1'])
        refs.append('EV-OPEN-'+material)
    item={'item_id':'QTY-'+material,'name':material+' '+name+' net ceiling finish area','object_type':'ceiling_finish','physical_surface_id':sid,'layer_id':lid,'side_scope':'underside finish','measurement_basis':'horizontal_net_finish_boundary','method':'rectangle_openings_net','unit':'m²','scope_status':'complete','adoption_role':'reference','quantity_role':'physical_area','result_use':'Current model working quantity for synthetic task; unreviewed, not formally adopted.','evidence_refs':refs,'rule_refs':['R-SE-001','R-SE-002','R-SE-003','R-SE-008'],'unresolved_refs':[],'coverage_evidence_ref':'EV-COVER-'+material,'local_coordinate_frame':surface_facts['surface_frames'][material],'frame_binding':{'frame_evidence_ref':'EV-GEOM-'+material,'source_pointer':'/surface_frames/'+material,'edge_evidence_ref':'EV-GEOM-'+material,'edge_source_pointer':'/edges/EDGE-9A-0'},'inputs':{'length_m':(rect[2]-rect[0])/1000,'height_m':(rect[3]-rect[1])/1000,'openings':openings,'other_deductions_area_m2':0,'reveal_or_return_area_m2':0},'precision':'0.01 m² displayed; 0.000000001 m² calculation output'}
    if openings:item['opening_union_evidence_ref']='EV-OPEN-'+material
    items.append(item)
rules=[{'rule_id':rid,'name':name,'source':'Current qto-surfaces-envelope skill and A-01 drawing notes E8-F0','decision':'试用','maturity':maturity,'execution':'Reference working calculation using explicitly stated geometric finish-area scope','evidence_locator':'A-01 Model notes E8-F0; RCP handles 9A,9B,9E,AE'} for rid,name,maturity in [('R-SE-001','Unique surface, layer, side and basis','试运行'),('R-SE-002','Gross minus union of actual openings','试运行'),('R-SE-003','Ceiling design finish boundaries','候选'),('R-SE-008','Complete coverage evidence','试运行')]]
manifest={'manifest_version':'qto-surfaces-envelope-manifest/1.0','task':{'project_id':'A01-STUDIO','business_discipline':'Ceiling finishes','main_objects':'Two horizontal ceiling finish material zones','system':'Studio ceiling','building':'A-01 Studio','zone':'RCP-01','drawing_type':'Reflected plan, schedule, notes and S-2 detail','drawing_version':'Rev A synthetic drawing','output_dimension':'Material, surface, square metres','applicable_rule_status':'confirmed'},'sources':sources,'evidence':ev,'rules':rules,'unresolved':[],'items':items,'recognition_provenance':{'decision_origin':'model','model':MODEL,'review_status':'unreviewed','formal_adoption_allowed':False,'human_interventions':[]}}
save(ROOT/'surfaces/native_manifest.json',manifest)
print(json.dumps({'status':'source-bound native input preparation complete','pipe_segment_count':len(segments),'pipe_exclusions':len(exclude),'surface_count':len(items),'at_utc':now},ensure_ascii=False))
