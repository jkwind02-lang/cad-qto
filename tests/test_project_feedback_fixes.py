"""Generic regressions distilled from drawing feedback; no project answers."""
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

import ezdxf
import pytest
from test_audit_regressions import row
from test_manual_routes import ROOT, base, line, run_engine


def test_leader_retains_wcs_evidence_without_pipe_length():
    e = ezdxf.new().modelspace().add_leader([(1, 2, 3), (5, 6, 3)])
    r = row(e)
    assert r['raw_geometry']['vertices'] == [[1, 2, 3], [5, 6, 3]]
    assert r['bbox'] == [1, 2, 5, 6]
    assert r['raw_length'] is None and r['geometry'] is None
    assert r['extrusion'] is None and not r['geometry_issues']


def test_wipeout_retains_mask_boundary_without_pipe_length():
    e = ezdxf.new().modelspace().add_wipeout([(1, 2), (5, 2), (5, 6), (1, 6)])
    r = row(e)
    assert r['bbox'] == pytest.approx([1, 2, 5, 6])
    assert len(r['raw_geometry']['boundary_vertices']) >= 4
    assert r['raw_length'] is None and r['measurement_shape'] == 'not_applicable'
    assert r['extrusion'] is None and not r['geometry_issues']


def cropped_network():
    a = line('A', (-3, 0), (0, 0))
    b = line('B', (0, 0), (0, 4))
    for s, dn in [(a, 'DN40'), (b, 'DN15')]:
        s['dn_seeds'] = [{'dn': dn, 'evidence_level': 'inferred', 'source_id': 'label:' + s['segment_id']}]
    return base([a, b])


def boundary(**extra):
    return dict(boundary_id='scope-east', point=[0, 0, 0], space_id='plan',
                system='W', source_id='drawing:outside-port', evidence_level='inferred', **extra)


@pytest.mark.parametrize('mode', ['explicit', 'legacy_root'])
def test_scope_boundary_preserves_length_and_stops_false_elbow_and_dn_spread(tmp_path, mode):
    d = cropped_network()
    if mode == 'explicit':
        d['scope_boundary_nodes'] = [boundary()]
    else:
        r = boundary(); r.pop('boundary_id')
        d['roots'] = [dict(r, root_id='scope-root', root_role='scope_interface')]
    o = run_engine(tmp_path, d)
    assert o['metrics']['raw_length'] == 7
    assert o['metrics']['dn_assigned_segment_count'] == 2
    f = o['fitting_candidates']
    assert len(f) == 1 and f[0]['type'] == '边界管件未决'
    assert f[0]['quantity_level'] == '未决量' and not f[0]['connection_complete']
    assert o['scope_boundary_nodes'][0]['status'] == 'INCOMPLETE'
    assert o['workflow_readiness']['status'] == 'REVIEW_REQUIRED_WITH_RESULTS'
    assert any(x['type'] == '范围边界管件待核实' for x in o['decision_queue'])


def test_ordinary_change_node_does_not_hide_known_elbow(tmp_path):
    d = cropped_network()
    d['change_nodes'] = [dict(boundary(), change_id='size-change')]
    o = run_engine(tmp_path, d)
    assert o['fitting_candidates'][0]['type'] == '弯头或变径组合候选'


def test_boundary_partition_cannot_bind_other_drawing(tmp_path):
    d = cropped_network(); r = boundary(); r['space_id'] = 'another-floor'
    d['scope_boundary_nodes'] = [r]
    o = run_engine(tmp_path, d)
    assert o['scope_boundary_nodes'][0]['status'] == 'UNBOUND'
    assert o['metrics']['raw_length'] == 7


@pytest.mark.parametrize('bad', ['duplicate', 'unit', 'source'])
def test_invalid_boundary_declarations_are_rejected(tmp_path, bad):
    d = cropped_network(); r = boundary()
    if bad == 'unit': r['coordinate_unit'] = 'mm'
    if bad == 'source': r.pop('source_id')
    d['scope_boundary_nodes'] = [r, copy.deepcopy(r)] if bad == 'duplicate' else [r]
    p = tmp_path / 'input.json'; p.write_text(json.dumps(d), encoding='utf-8')
    out = tmp_path / 'output.json'
    run = subprocess.run([sys.executable, '-B', str(ROOT/'skills/qto-pipe-networks/scripts/infer_pipe_network.py'),
                          '--input', str(p), '--output', str(out)], capture_output=True)
    assert run.returncode != 0 and not out.exists()


PS = shutil.which('pwsh') or shutil.which('powershell')
# The caller prefers pwsh but falls back to Windows PowerShell 5.1, so the exporter must work on both.
SHELLS = [s for s in dict.fromkeys([shutil.which('pwsh'), shutil.which('powershell')]) if s]


@pytest.mark.skipif(sys.platform != 'win32' or not PS, reason='Windows PowerShell export integration')
@pytest.mark.parametrize('shell', SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize('mode', ['success', 'failure', 'timeout', 'existing', 'truncated', 'unclosed', 'bad_pair'])
def test_export_unicode_paths_source_protection_and_failure_atomicity(tmp_path, mode, shell):
    folder = tmp_path/'中文路径 带空格'; folder.mkdir()
    source = folder/'原始 图纸.dwg'; source.write_bytes(b'authoritative-dwg-fixture')
    target = folder/'交换 成果.dxf'
    if mode == 'existing': target.write_bytes(b'previous-result')
    before = (source.read_bytes(), source.stat().st_mtime_ns)
    wrapper = tmp_path/'probe.ps1'
    wrapper.write_text(r'''
param($Exporter, $Source, $Target, $Mode)
$ErrorActionPreference='Stop'
function Get-Command { param($Name) [pscustomobject]@{Source='fake-accoreconsole.exe'} }
function Start-Process {
 param($FilePath,$ArgumentList,$RedirectStandardOutput,$RedirectStandardError,$WindowStyle,$WorkingDirectory,[switch]$PassThru)
 if($WindowStyle -ne 'Hidden'){throw 'window must stay hidden'}
 if($ArgumentList -match [regex]::Escape($Source)){throw 'opened authoritative source'}
 $tokens=[IO.File]::ReadAllLines((Join-Path $WorkingDirectory 'export-dxf.scr'))
 if(($tokens -join '|') -ne '_.FILEDIA|0|_.CMDECHO|0|_.DXFOUT|exchange.dxf|_V|2018|16|_.QUIT|_N'){throw 'wrong export prompt sequence'}
 [IO.File]::WriteAllText($RedirectStandardOutput,'probe')
 [IO.File]::WriteAllText($RedirectStandardError,'')
 $dxf = "0`nSECTION`n2`nENTITIES`n0`nENDSEC`n0`nEOF`n"
 if ($Mode -eq 'truncated') { $dxf = "0`nSECTION`n2`nENTITIES`n" }
 if ($Mode -eq 'unclosed') { $dxf = "0`nSECTION`n2`nENTITIES`n0`nEOF`n" }
 if ($Mode -eq 'bad_pair') { $dxf = "0`nSECTION`n2`nENTITIES`n0`nENDSEC`n0" }
 [IO.File]::WriteAllText((Join-Path $WorkingDirectory 'exchange.dxf'), $dxf)
 $proc=[pscustomobject]@{ExitCode=0;TimedOut=($Mode -eq 'timeout')}
 if($Mode -eq 'failure'){$proc.ExitCode=1}
 $proc | Add-Member ScriptMethod WaitForExit { param($Milliseconds) if($null -eq $Milliseconds){return}; return (-not $this.TimedOut) }
 $proc | Add-Member ScriptMethod Kill { $this.TimedOut=$false }
 return $proc
}
& $Exporter -SourceDwg $Source -OutputDxf $Target -ExportBackend coreconsole -TimeoutSeconds 1
''', encoding='utf-8-sig')
    run = subprocess.run([shell, '-NoProfile', '-File', str(wrapper),
                          str(ROOT/'skills/_qto-shared-evidence/scripts/export_dwg_exchange_dxf.ps1'),
                          str(source), str(target), mode], capture_output=True)
    assert (source.read_bytes(), source.stat().st_mtime_ns) == before
    assert not list(folder.glob('.qto-dwg-export-*'))
    if mode == 'success':
        assert run.returncode == 0, run.stdout + run.stderr
        assert b'SECTION' in target.read_bytes()
    else:
        assert run.returncode != 0
        assert target.read_bytes() == b'previous-result' if mode == 'existing' else not target.exists()


@pytest.mark.skipif(sys.platform != 'win32' or not PS, reason='Windows export integration')
@pytest.mark.parametrize('shell', SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize('backend',['oda','oda-audit'])
def test_oda_only_opens_staged_copy_and_records_audit(tmp_path,backend,shell):
    source=tmp_path/'original.dwg';source.write_bytes(b'drawing')
    target=tmp_path/'converted.dxf';wrapper=tmp_path/'oda-probe.ps1'
    wrapper.write_text(r'''
param($Exporter,$Source,$Target,$Backend)
$ErrorActionPreference='Stop'
function Get-Command { param($Name) [pscustomobject]@{Source='fake-oda.exe'} }
function Start-Process {
 param($FilePath,$ArgumentList,$WindowStyle,[switch]$PassThru,$WorkingDirectory,$RedirectStandardOutput,$RedirectStandardError)
 if($WindowStyle -ne 'Hidden'){throw 'not hidden'}
 if($ArgumentList[4] -ne '0' -or $ArgumentList[6] -ne 'source.dwg'){throw 'unbounded source selection'}
 if($ArgumentList[5] -ne $(if($Backend -eq 'oda-audit'){'1'}else{'0'})){throw 'wrong audit setting'}
 $inputFolder=$ArgumentList[0].Trim('"');$outputFolder=$ArgumentList[1].Trim('"')
 if(-not (Test-Path -LiteralPath (Join-Path $inputFolder 'source.dwg'))){throw 'no staged source'}
 [IO.File]::WriteAllText((Join-Path $outputFolder 'source.dxf'),"0`nSECTION`n2`nENTITIES`n0`nENDSEC`n0`nEOF`n")
 $p=[pscustomobject]@{ExitCode=0}
 $p | Add-Member ScriptMethod WaitForExit { param($Milliseconds) if($null -ne $Milliseconds){return $true} }
 return $p
}
& $Exporter -SourceDwg $Source -OutputDxf $Target -ExportBackend $Backend
''',encoding='utf-8-sig')
    p=subprocess.run([shell,'-NoProfile','-File',str(wrapper),str(ROOT/'skills/_qto-shared-evidence/scripts/export_dwg_exchange_dxf.ps1'),str(source),str(target),backend],capture_output=True)
    assert p.returncode==0,p.stdout+p.stderr
    assert source.read_bytes()==b'drawing' and target.exists()
    receipt=json.loads(Path(str(target)+'.export.json').read_text(encoding='utf-8'))
    assert receipt['dxf_completion_verified'] is True
    assert receipt['exchange_geometry_review_required']==(backend=='oda-audit')
