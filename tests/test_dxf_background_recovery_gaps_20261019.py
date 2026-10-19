"""Anonymous recovery failures and legal controls; these are background-only checks.

The clipping implementation deliberately refuses active/unknown clips. It never
uses these envelopes as measurement geometry or claims native CAD rendering.
"""
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import shutil
import sys

import ezdxf
from ezdxf.xclip import XClip
import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT/'skills/_qto-shared-evidence/scripts'
sys.path.insert(0, str(SCRIPTS))
import qto_dxf_background as bg


def target(bounds):
    return dict(scene_id='S', sheet_bounds=bounds, dx=0, dy=0,
                cfg={**bg.DEFAULTS, 'large_block_layers': bg.LARGE_BLOCK_LAYERS},
                include=[], exclude=[], symbol_layers=[], items=[], stats=Counter())


def build(doc, tmp_path, bounds=(-1000, -1000, 1000, 1000), others=()):
    path=tmp_path/'anonymous.dxf'; doc.saveas(path)
    before=path.read_bytes()
    targets=[target(bounds), *(target(b) for b in others)]
    bg.build_dxf_backgrounds(path, hashlib.sha256(before).hexdigest(), targets, set(), 1, 6)
    assert before == path.read_bytes()
    return targets


def deep_doc(n=13, affine=False, empty=False):
    doc=ezdxf.new('R2010'); leaf=doc.blocks.new('LEAF')
    if not empty: leaf.add_line((0, 0), (100, 0))
    block=leaf
    for i in range(n):
        parent=doc.blocks.new('LEVEL'+str(i)); parent.add_blockref(block.name,(0,0)); block=parent
    if affine: block.add_mtext('CONTROL', dxfattribs={'insert': (10, 50), 'char_height': 2})
    doc.modelspace().add_blockref(block.name,(0,0),dxfattribs={'xscale':2 if affine else 1})
    return doc


def clip_doc(mode='enabled', nested=False, affine=False):
    doc=ezdxf.new('R2010'); leaf=doc.blocks.new('CLIPPED')
    if mode != 'empty': leaf.add_line((0,0),(100,0))
    if affine: leaf.add_mtext('CONTROL',dxfattribs={'insert': (0,0), 'char_height':2})
    layout=doc.blocks.new('PARENT') if nested else doc.modelspace()
    ref=layout.add_blockref(leaf.name, (100,0) if nested else (0,0), dxfattribs={'xscale':2 if affine else 1})
    if mode != 'none':
        clip=XClip(ref); clip.set_block_clipping_path([(0,-1),(10,1)])
        if mode == 'disabled': clip.disable_clipping()
        if mode == 'inverted': clip.invert_clipping_path(ignore_acad_compatibility=True)
    if nested: doc.modelspace().add_blockref(layout.name,(500,0))
    return doc


@pytest.mark.parametrize('affine', [False, True])
def test_missing_nested_definition_reaches_target_even_when_valid_bbox_is_elsewhere(tmp_path, affine):
    doc=ezdxf.new('R2010'); block=doc.blocks.new('PARENT',base_point=(10,0))
    block.add_line((0,0),(1,0))
    block.add_blockref('ABSENT',(30,0))
    if affine: block.add_mtext('CONTROL',dxfattribs={'insert':(0,10),'char_height':2})
    ref=doc.modelspace().add_blockref(block.name,(100,50),dxfattribs={'xscale':2 if affine else 1})
    q=ref.matrix44().transform((30,0,0))
    main, away=build(doc,tmp_path,[q.x-1,q.y-1,q.x+1,q.y+1],[[500,500,600,600]])
    assert main['restoration'] == {'xref_missing':1} and not main['items']
    assert not away['restoration'] and not away['items']
    assert bg.restoration_summary([main])['not_shown_total'] == 1


@pytest.mark.parametrize('kind', ['off','frozen','nonplot','nested_off'])
def test_hidden_missing_definition_is_silent(tmp_path,kind):
    doc=ezdxf.new('R2010'); layer=doc.layers.new('HIDDEN')
    if kind in ('off','nested_off'): layer.off()
    elif kind=='frozen': layer.freeze()
    else: layer.dxf.plot=0
    if kind=='nested_off':
        parent=doc.blocks.new('PARENT'); parent.add_blockref('ABSENT',(0,0),dxfattribs={'layer':'HIDDEN'})
        doc.modelspace().add_blockref(parent.name,(0,0))
    else: doc.modelspace().add_blockref('ABSENT',(0,0),dxfattribs={'layer':'HIDDEN'})
    t,=build(doc,tmp_path)
    assert not t['items'] and not t['restoration']


def test_missing_top_level_uses_wcs_insert_and_target_scope(tmp_path):
    doc=ezdxf.new('R2010')
    ref=doc.modelspace().add_blockref('ABSENT',(30,40,10),dxfattribs={'extrusion':(.2,.3,1)})
    q=ref.ocs().to_wcs(ref.dxf.insert)
    t, away=build(doc,tmp_path,[q.x-.1,q.y-.1,q.x+.1,q.y+.1],[[1000,1000,1100,1100]])
    assert t['restoration'] == {'xref_missing':1} and not away['restoration']


@pytest.mark.parametrize('deep', [False,True])
def test_defined_empty_block_is_complete(tmp_path,deep):
    if deep: doc=deep_doc(empty=True)
    else:
        doc=ezdxf.new('R2010'); doc.blocks.new('EMPTY'); doc.modelspace().add_blockref('EMPTY',(0,0))
    t,=build(doc,tmp_path)
    assert not t['items'] and not t['restoration']


@pytest.mark.parametrize('affine',[False,True])
def test_depth_limit_is_explicit_and_scoped_to_lost_branch_not_its_anchor(tmp_path,affine):
    doc=deep_doc(affine=affine)
    t,away=build(doc,tmp_path,[40,-2,60,2],[[2000,2000,2100,2100]])
    dw=bg._Drawing(tmp_path/'anonymous.dxf')
    assert dw.definition_display(next(iter(dw.msp))) is affine
    assert t['restoration']=={'proxy':1} and not t['items']
    assert not away['restoration']


@pytest.mark.parametrize('affine',[False,True])
def test_legal_depth_retains_geometry(tmp_path,affine):
    t,=build(deep_doc(11,affine),tmp_path)
    assert t['items'] and not t['restoration']



def test_deep_size_check_cannot_poison_later_shallow_reference_classification(tmp_path):
    doc=deep_doc(); leaf=doc.blocks.get('LEAF')
    for entity in leaf: entity.dxf.layer='PIPE'
    for i in range(1000): leaf.add_line((0,i),(100,i),dxfattribs={'layer':'PIPE'})
    doc.modelspace().add_blockref('LEVEL2',(500,0))
    path=tmp_path/'anonymous.dxf'; doc.saveas(path)
    dw=bg._Drawing(path); walker=bg._Walker(dw,{**bg.DEFAULTS,'large_block_layers':bg.LARGE_BLOCK_LAYERS},set(),1)
    deep,shallow=list(dw.msp)
    assert walker.run(deep)[0]=='large'
    classification,items,skipped,_=walker.run(shallow)
    assert classification=='large' and not items
    assert sum(k=='other_layers' for k,_ in skipped)==1001


def test_recursive_definition_does_not_hang_or_disappear_silently(tmp_path):
    doc=ezdxf.new('R2010'); b=doc.blocks.new('LOOP'); b.add_line((0,0),(10,0)); b.add_blockref('LOOP',(0,0))
    doc.modelspace().add_blockref('LOOP',(0,0))
    t,away=build(doc,tmp_path,others=[[2000,2000,2100,2100]])
    assert t['restoration']=={'proxy':1} and not away['restoration']


@pytest.mark.parametrize('affine',[False,True])
@pytest.mark.parametrize('nested',[False,True])
def test_active_clip_never_emits_unclipped_geometry_and_warning_has_clip_scope(tmp_path,affine,nested):
    offset=600 if nested else 0
    t,outside,away=build(clip_doc(nested=nested,affine=affine),tmp_path,[offset+4,-2,offset+8,2],
                       [[offset+50,-2,offset+60,2],[2000,2000,2100,2100]])
    assert not t['items'] and t['restoration']=={'proxy':1}
    assert not outside['items'] and not outside['restoration']
    assert not away['items'] and not away['restoration']



def test_rejected_nested_affine_branch_uses_original_matrix_envelope(tmp_path):
    # A clipped ancestor encloses a rotated child under non-uniform scale.
    # Keep the warning at a known raw endpoint even when decomposition would shear it.
    doc=ezdxf.new('R2010'); leaf=doc.blocks.new('LEAF'); leaf.add_line((0,0),(100,0))
    parent=doc.blocks.new('PARENT'); child=parent.add_blockref(leaf.name,(20,0),dxfattribs={'rotation':33})
    root=doc.modelspace().add_blockref(parent.name,(500,300),dxfattribs={'xscale':3,'yscale':.5,'rotation':27})
    XClip(root).set_block_clipping_path([(-1000,-1000),(1000,1000)])
    from ezdxf.math import Matrix44
    point=Matrix44.chain(child.matrix44(),root.matrix44()).transform((100,0,0))
    t,away=build(doc,tmp_path,[point.x-.1,point.y-.1,point.x+.1,point.y+.1],[[3000,3000,3100,3100]])
    assert not t['items'] and t['restoration']=={'proxy':1} and not away['restoration']


@pytest.mark.parametrize('mode',['none','disabled'])
@pytest.mark.parametrize('nested',[False,True])
def test_no_clip_and_disabled_clip_remain_drawn(tmp_path,mode,nested):
    t,=build(clip_doc(mode,nested=nested),tmp_path)
    assert t['items'] and not t['restoration']


def test_enabled_clip_on_defined_empty_block_loses_nothing(tmp_path):
    t,=build(clip_doc('empty'),tmp_path)
    assert not t['items'] and not t['restoration']


def test_inverted_clip_is_rejected_with_branch_envelope_scope(tmp_path):
    t,away=build(clip_doc('inverted'),tmp_path,[50,-2,60,2],[[2000,2000,2100,2100]])
    assert not t['items'] and t['restoration']=={'proxy':1} and not away['restoration']


@pytest.mark.parametrize('affine',[False,True])
def test_unknown_clip_state_is_rejected_with_bounded_warning(tmp_path,monkeypatch,affine):
    def broken(self): raise ValueError('anonymous unreadable spatial filter')
    monkeypatch.setattr(XClip,'is_clipping_enabled',property(broken))
    t,away=build(clip_doc(affine=affine),tmp_path,[50,-2,60,2],[[2000,2000,2100,2100]])
    assert not t['items'] and t['restoration']=={'proxy':1} and not away['restoration']



@pytest.mark.parametrize('kind',['clip','depth'])
def test_rejected_branch_with_only_hidden_content_loses_nothing(tmp_path,kind):
    doc=clip_doc() if kind=='clip' else deep_doc()
    doc.layers.new('HIDDEN').off()
    for block in doc.blocks:
        if not block.is_any_layout:
            for entity in block:
                if entity.dxftype() != 'INSERT': entity.dxf.layer='HIDDEN'
    t,=build(doc,tmp_path)
    assert not t['items'] and not t['restoration']



def test_hidden_content_cannot_expand_rejected_branch_warning_to_another_target(tmp_path):
    doc=clip_doc(); doc.layers.new('HIDDEN').off()
    doc.blocks.get('CLIPPED').add_line((1000,0),(1010,0),dxfattribs={'layer':'HIDDEN'})
    XClip(next(iter(doc.modelspace()))).set_block_clipping_path([(-10,-10),(2000,10)])
    t,hidden=build(doc,tmp_path,[-1,-2,101,2],[[999,-2,1011,2]])
    assert t['restoration']=={'proxy':1} and not hidden['restoration']



def extension_doc(kind, nested=False, affine=False):
    doc=clip_doc('none',nested=nested,affine=affine)
    ref=next(e for e in doc.entitydb.values() if e.dxftype()=='INSERT' and e.dxf.name=='CLIPPED')
    extension=ref.new_extension_dict()
    if kind=='unrelated_extension': extension.add_dictionary('CUSTOM_NOTES')
    elif kind=='unknown_filter': extension.add_xrecord('ACAD_FILTER')
    else:
        filters=extension.add_dictionary('ACAD_FILTER')
        if kind=='unknown_spatial': filters.add_new_dict('SPATIAL',hard_owned=True)
        elif kind=='unknown_spatial_xrecord': filters['SPATIAL']=doc.objects.add_xrecord()
        else: filters.add_new_dict('CUSTOM_FILTER',hard_owned=True)
    return doc


@pytest.mark.parametrize('kind',['unknown_spatial','unknown_spatial_xrecord','unknown_filter'])
@pytest.mark.parametrize('nested',[False,True])
@pytest.mark.parametrize('affine',[False,True])
def test_unrecognized_spatial_payload_is_rejected_after_real_save(tmp_path,kind,nested,affine):
    doc=extension_doc(kind,nested,affine)
    t,away=build(doc,tmp_path,others=[[2000,2000,2100,2100]])
    loaded=ezdxf.readfile(tmp_path/'anonymous.dxf')
    ref=next(e for e in loaded.entitydb.values() if e.dxftype()=='INSERT' and e.has_extension_dict)
    # This real persisted structure is the regression: library False means unknown here.
    assert XClip(ref).is_clipping_enabled is False
    assert not t['items'] and t['restoration']=={'proxy':1} and not away['restoration']


@pytest.mark.parametrize('kind',['unrelated_extension','filter_without_spatial'])
@pytest.mark.parametrize('nested',[False,True])
@pytest.mark.parametrize('affine',[False,True])
def test_non_clipping_extension_dictionaries_remain_drawn_after_real_save(tmp_path,kind,nested,affine):
    t,=build(extension_doc(kind,nested,affine),tmp_path)
    assert t['items'] and not t['restoration']



@pytest.mark.parametrize('state',[None,2])
def test_real_saved_unknown_spatial_status_cannot_claim_disabled_or_known_clip_scope(tmp_path,state):
    doc=clip_doc('disabled')
    ref=next(iter(doc.modelspace()))
    spatial=ref.get_extension_dict()['ACAD_FILTER']['SPATIAL']
    if state is not None: spatial.dxf.unprotected_set('is_clipping_enabled',state)
    path=tmp_path/'anonymous.dxf'; doc.saveas(path)
    if state is None:
        # Remove group 71 from this persisted SPATIAL_FILTER, leaving its status unknown.
        lines=path.read_text(encoding='utf-8').splitlines(); pairs=list(zip(lines[0::2],lines[1::2])); inside=False; result=[]
        for code,value in pairs:
            if code.strip()=='0': inside=value.strip()=='SPATIAL_FILTER'
            if inside and code.strip()=='71': continue
            result.extend([code,value])
        path.write_text('\n'.join(result)+'\n',encoding='utf-8')
    loaded=ezdxf.readfile(path)
    saved=next(iter(loaded.modelspace())).get_extension_dict()['ACAD_FILTER']['SPATIAL']
    assert saved.dxf.get('is_clipping_enabled')==state
    main,away=target([50,-2,60,2]),target([2000,2000,2100,2100])
    bg.build_dxf_backgrounds(path,hashlib.sha256(path.read_bytes()).hexdigest(),[main,away],set(),1,6)
    assert not main['items'] and main['restoration']=={'proxy':1} and not away['restoration']


def cli_case(tmp_path,source,expected):
    """Actual builder and HTML consumer CLI; assertion failures cannot become collection success."""
    sys.path.insert(0,str(ROOT/'tests'))
    import test_review_scene_mapping_20261016 as fx
    nroot=fx.neutral(tmp_path); folder=nroot/'D1'; dxf=folder/'source.dxf'
    dxf.write_bytes(source.read_bytes()); sha=hashlib.sha256(dxf.read_bytes()).hexdigest()
    manifest=folder/'CAD中性取证清单.json'; mf=json.loads(manifest.read_text(encoding='utf-8'))
    mf['exchange_dxf']={'path':dxf.name,'sha256':sha}; manifest.write_text(json.dumps(mf,ensure_ascii=False),encoding='utf-8')
    mp,m=fx.mapping(tmp_path)
    fx.write_csv(tmp_path/'tables/sheets.csv',['图页','图名','楼层','类型','x1','y1','x2','y2'],
                 [['MAIN','Anonymous','','平面',-1000,-1000,1000,1000],['AWAY','Away','','平面',2000,2000,3000,3000]])
    fx.write_csv(tmp_path/'tables/pts.csv',['编号','图页','x','y'],[['CONTROL','MAIN',0,0],['AWAY_CONTROL','AWAY',2500,2500]])
    m['objects']=[m['objects'][-1]]; m['background']={'source':'dxf'}
    mp.write_text(json.dumps(m,ensure_ascii=False),encoding='utf-8')
    commands=[['build_review_scenes_from_tables.py','--mapping',mp,'--neutral-root',nroot,'--output',tmp_path/'view.json','--report',tmp_path/'scene-report.json'],
              ['build_qto_review_html.py','--input',tmp_path/'view.json','--output',tmp_path/'page.html','--local-only','--report',tmp_path/'html-report.json']]
    logs=[]
    for i,command in enumerate(commands):
        args=[sys.executable,'-B','-s','-X','utf8',str(SCRIPTS/command[0]),*map(str,command[1:])]
        result=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=120)
        (tmp_path/f'cli-{i}.stdout.log').write_text(result.stdout,encoding='utf-8')
        (tmp_path/f'cli-{i}.stderr.log').write_text(result.stderr,encoding='utf-8')
        logs.append(dict(args=args,exit_code=result.returncode))
        (tmp_path/'actual-cli.json').write_text(json.dumps(logs,ensure_ascii=False,indent=2),encoding='utf-8')
        assert result.returncode==0,(result.stdout,result.stderr)
    ui_report=tmp_path/'ui-input.json'
    ui_report.write_text(json.dumps({'artifacts':{'normalized_view':str(tmp_path/'view.json'),'html':str(tmp_path/'page.html')}}),encoding='utf-8')
    node=shutil.which('node')
    assert node, 'Node is required for actual HTML consumer assertions'
    args=[node,str(ROOT/'tests/run_html_review_ui_tests.mjs'),'--root',str(ROOT/'skills'),'--report',str(ui_report),'--recovery-only','--recovery-output',str(tmp_path/'ui-result.json')]
    ui=subprocess.run(args,stdin=subprocess.DEVNULL,capture_output=True,text=True,encoding='utf-8',timeout=120)
    (tmp_path/'cli-2.stdout.log').write_text(ui.stdout,encoding='utf-8'); (tmp_path/'cli-2.stderr.log').write_text(ui.stderr,encoding='utf-8')
    logs.append(dict(args=args,exit_code=ui.returncode));(tmp_path/'actual-cli.json').write_text(json.dumps(logs,ensure_ascii=False,indent=2),encoding='utf-8')
    assert ui.returncode==0,(ui.stdout,ui.stderr)
    view=json.loads((tmp_path/'view.json').read_text(encoding='utf-8'))
    report=json.loads((tmp_path/'scene-report.json').read_text(encoding='utf-8'))
    htmlreport=json.loads((tmp_path/'html-report.json').read_text(encoding='utf-8'))
    main=next(s for s in view['scenes'] if s['id']=='D1|MAIN')
    away=next(s for s in view['scenes'] if s['id']=='D1|AWAY')
    assert main['restoration']['not_shown_total']==sum(expected.values())
    assert {k:v for k,v in main['restoration']['not_shown'].items() if v}==expected
    assert away['restoration']['not_shown_total']==0
    assert next(s for s in report['scenes'] if s['id']==main['id'])['restoration']==main['restoration']
    assert htmlreport['quantity_changes']==0 and htmlreport['native_browser_validation']=='not performed by this builder'
    assert '须回原图' in (tmp_path/'page.html').read_text(encoding='utf-8')
    assert dxf.read_bytes()==source.read_bytes()
    return main


@pytest.mark.parametrize('case',['missing','depth','clip','empty_control','depth_control','disabled_control','unknown_spatial','unrelated_extension','filter_without_spatial'])
def test_actual_builder_and_consumer_cli_recovery_and_controls(tmp_path,case):
    source=tmp_path/'input.dxf'
    if case=='missing':
        doc=ezdxf.new(); doc.modelspace().add_blockref('ABSENT',(0,0)); expected={'xref_missing':1}
    elif case in ('depth','depth_control'): doc=deep_doc(13 if case=='depth' else 11); expected={'proxy':1} if case=='depth' else {}
    elif case in ('clip','disabled_control'): doc=clip_doc('enabled' if case=='clip' else 'disabled'); expected={'proxy':1} if case=='clip' else {}
    elif case in ('unknown_spatial','unrelated_extension','filter_without_spatial'):
        doc=extension_doc(case,nested=True,affine=True); expected={'proxy':1} if case=='unknown_spatial' else {}
    else:
        doc=ezdxf.new(); doc.blocks.new('EMPTY'); doc.modelspace().add_blockref('EMPTY',(0,0)); expected={}
    doc.saveas(source); cli_case(tmp_path,source,expected)
