"""Native MINSERT physical identity; zero-spacing is not an extra instance."""
import copy
import json
from pathlib import Path
import shutil
import sys

import ezdxf
import pytest

from test_pipe_diagram_source_parts_batch14 import curve_workbench
from test_pipe_physical_network_t29_batch13 import (ROOT, PIPE, si_cli, write,
    fixture, candidate, chain, PRODUCTION_REQUEST, CONSUME_REQUEST)
from measure_pipe_diagram_lengths import Drawing, sha, measure
from qto_recognition_workbench import build_index


@pytest.mark.parametrize('grid,duplicate,expected', [((0, -1300), False, 2),
    ((1200, 0), False, 2), ((0, 0), False, 1),
    ((1e-12, -1e-12), False, 4), ((0, 0), True, 2), ((-900, -1300), False, 4)])
def test_index_matches_exact_native_minsert_semantics_without_global_position_merge(tmp_path, grid, duplicate, expected):
    d=ezdxf.new('R2010'); inner=d.blocks.new('INNER'); leaf=inner.add_line((0,0),(10,0))
    outer=d.blocks.new('OUTER'); nested=outer.add_blockref('INNER',(3,4))
    root=d.modelspace().add_blockref('OUTER',(100,200),dxfattribs={'rotation':31})
    root.dxf.row_count=root.dxf.column_count=2
    root.dxf.row_spacing,root.dxf.column_spacing=grid
    if duplicate: d.modelspace().add_entity(root.copy())
    path=tmp_path/'source.dxf';d.saveas(path);source={'path':str(path),'sha256':sha(path)}
    native=sum(len(list(e.multi_insert())) for e in d.modelspace().query('INSERT'))
    index=build_index(source)
    routes=[o for o in index['objects'] if o['source_handle']==leaf.dxf.handle]
    assert len(routes)==native==expected
    assert len({tuple(o['source_path']) for o in routes})==expected
    if grid[0]==0: assert all('[0,' in o['source_path'][0] for o in routes)
    if grid[1]==0: assert all(o['source_path'][0].endswith(',0]') for o in routes)
    # A forbidden alias must also fail the standalone diagram source resolver.
    if grid[0]==0:
        with pytest.raises(ValueError,match='零间距'):
            Drawing(d).resolve([root.dxf.handle+'[1,0]',nested.dxf.handle,leaf.dxf.handle])
    if grid[1]==0:
        with pytest.raises(ValueError,match='零间距'):
            Drawing(d).resolve([root.dxf.handle+'[0,1]',nested.dxf.handle,leaf.dxf.handle])


@pytest.mark.parametrize('grid,duplicate,expected', [((0,-1300),False,2),
    ((1200,0),False,2),((0,0),False,1),((1e-12,-1e-12),False,4),
    ((0,0),True,2),((-900,-1300),False,4)])
def test_zero_spacing_and_distinct_inserts_fresh_public_workbench_infer_t29_cli(tmp_path,grid,duplicate,expected):
    c=curve_workbench(tmp_path/'source','array',array_grid=grid,duplicate_insert=duplicate)
    d=ezdxf.readfile(c['source'])
    assert sum(e.mcount for e in d.modelspace().query('INSERT'))==expected
    assert len(c['route_objects'])==len(c['result']['segments'])==expected
    p,_=fixture(tmp_path,'separate',physical='separate',ports=('Q0','Q1'))
    out,request=chain(tmp_path,[candidate(tmp_path,p,'separate')],c['extra'])
    base=[r for r in out['quantity_items'] if any(v['origin']=='base' for v in r['representations'])]
    assert len(base)==expected and [r['quantity'] for r in base]==pytest.approx([10]*expected)
    assert sum(r['quantity'] for r in base)==pytest.approx(expected*10)
    assert out['preserved_existing_network']==c['result']
    assert out['formal_total_quantity'] is None and out['business_adoption_allowed'] is False
    if grid[0]==0 or grid[1]==0:
        production=json.loads(Path(json.loads(Path(request['network_result']['path']).read_text(encoding='utf-8'))['source_request']['path']).read_text(encoding='utf-8'))
        changed=copy.deepcopy(production)
        span=changed['base_extent_bindings'][0]['source_span']
        span['path'][0]=span['path'][0].replace('[0,0]','[1,0]' if grid[0]==0 else '[0,1]')
        ip=tmp_path/'collapsed-alias.json';write(ip,changed);target=tmp_path/'alias-must-not-exist.json'
        run=si_cli(tmp_path,PIPE/'produce_pipe_diagram_network.py','--input',ip,'--output',target)
        assert run.returncode==2 and not target.exists()


@pytest.mark.parametrize('method', ['dimension','unresolved'])
def test_standalone_diagram_cannot_reintroduce_zero_spacing_alias(tmp_path,method):
    p,_=fixture(tmp_path)
    path=Path(p['drawing']['path']);d=ezdxf.readfile(path);block=d.blocks.new('NATIVE_ARRAY')
    line=block.add_line((0,0),(10,0));dim=block.add_aligned_dim((0,0),(10,0),distance=1,text='2m');dim.render()
    root=d.modelspace().add_blockref('NATIVE_ARRAY',(0,0));root.dxf.row_count=2;root.dxf.row_spacing=0
    d.saveas(path);p['drawing']['sha256']=sha(path)
    row=p['segments'][0];row['source_span']={'path':[root.dxf.handle+'[1,0]',line.dxf.handle]}
    row['length_facts']=[{'method':'dimension','annotation_path':[root.dxf.handle+'[1,0]',dim.dimension.dxf.handle],
        'basis':'native alias counterexample','level':'confirmed'}] if method=='dimension' else []
    ip=tmp_path/'alias-request.json';write(ip,p);output=tmp_path/'must-not-exist.json'
    run=si_cli(tmp_path,PIPE/'measure_pipe_diagram_lengths.py','--input',ip,'--output',output)
    assert run.returncode==2 and not output.exists() and '零间距' in run.stderr


def test_infer_source_gate_reads_exact_native_zero_spacing_and_rejects_alias(tmp_path):
    from infer_pipe_network import verify_geometric_unit_source_binding
    from qto_geometric_unit_evidence import verify_geometric_unit_credential
    c=curve_workbench(tmp_path/'source','array',array_grid=(0,-1300))
    checked=verify_geometric_unit_credential(c['credential'],c['scope'])
    verify_geometric_unit_source_binding(c['input'],c['credential'],checked)
    forged=copy.deepcopy(c['input'])
    def alias(value):
        if isinstance(value,str):return value.replace('[0,0]','[1,0]')
        if isinstance(value,list):return [alias(v) for v in value]
        if isinstance(value,dict):return {k:alias(v) for k,v in value.items()}
        return value
    forged['segments'][0]=alias(forged['segments'][0])
    with pytest.raises(ValueError,match='零间距'):
        verify_geometric_unit_source_binding(forged,c['credential'],checked)
    ip=tmp_path/'forged-infer.json';write(ip,forged);target=tmp_path/'forged-infer-must-not-exist.json'
    done=si_cli(tmp_path,PIPE/'infer_pipe_network.py','--input',ip,'--output',target)
    assert done.returncode==2 and not target.exists()


def test_zero_spacing_output_source_module_and_scope_drift_still_reject(tmp_path):
    c=curve_workbench(tmp_path/'source','array',array_grid=(0,-1300))
    p,_=fixture(tmp_path,'separate',physical='separate',ports=('Q0','Q1'))
    out,request=chain(tmp_path,[candidate(tmp_path,p,'separate')],c['extra'])
    assert len(out['quantity_items'])==3
    ip=tmp_path/'consume.json';write(ip,request)
    clone=tmp_path/'same-bytes-code'
    shutil.copytree(ROOT/'skills',clone/'skills')
    consumer=clone/'skills/qto-pipe-networks/scripts/consume_pipe_physical_network.py'
    done=si_cli(tmp_path,consumer,'--input',ip,'--output',tmp_path/'same-byte-valid.json')
    assert done.returncode==0,done.stderr
    changed=clone/'skills/qto-pipe-networks/scripts/pipe_diagram_source_parts.py'
    original=changed.read_bytes();changed.write_bytes(original+b'\n# private source module drift\n')
    done=si_cli(tmp_path,consumer,'--input',ip,'--output',tmp_path/'drift-must-not-exist.json')
    assert done.returncode==2 and not (tmp_path/'drift-must-not-exist.json').exists()
    changed.write_bytes(original)
    altered=copy.deepcopy(request);altered['scope_id']='other-scope';write(ip,altered)
    done=si_cli(tmp_path,consumer,'--input',ip,'--output',tmp_path/'scope-must-not-exist.json')
    assert done.returncode==2 and not (tmp_path/'scope-must-not-exist.json').exists()
    write(ip,request)
    c['source'].write_bytes(c['source'].read_bytes()+b'\n')
    done=si_cli(tmp_path,consumer,'--input',ip,'--output',tmp_path/'source-must-not-exist.json')
    assert done.returncode==2 and not (tmp_path/'source-must-not-exist.json').exists()


@pytest.mark.parametrize('issue',['opaque','conflict'])
def test_zero_spacing_physical_fold_does_not_reduce_declared_cell_unit_search(tmp_path,issue):
    c=curve_workbench(tmp_path/'source','array',array_issue=issue,array_grid=(0,-1300))
    assert c['unit_rejection'].returncode==2
    unit=json.loads((tmp_path/'source/credential.json').read_text(encoding='utf-8-sig'))
    assert unit['allows_length_measurement'] is False and unit['raw_unit'] is None
    assert len(unit['unit_text_search']['checked_array_cells'])==4
    assert not (tmp_path/'source/base-input.json').exists()
