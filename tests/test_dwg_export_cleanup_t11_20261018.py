"""T11: current exporter, real Windows locks, fake process boundary, both PowerShell editions.

No installed CAD engine or project drawing is opened. A failed cleanup is tested
independently of export success/failure and never changes the primary outcome.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
EXPORTER = ROOT / 'skills/_qto-shared-evidence/scripts/export_dwg_exchange_dxf.ps1'
READER = ROOT / 'skills/_qto-shared-evidence/scripts/read_cad_basic.py'
SHELLS = [s for s in dict.fromkeys([shutil.which('pwsh'), shutil.which('powershell')]) if s]

WRAPPER = r'''
param($Exporter,$Source,$Target,$Fixture,$Mode,$Protected)
$ErrorActionPreference='Stop'
# Load hashing before installing the CAD-discovery mock. PowerShell 5.1's
# Utility module itself queries Get-Command while importing Get-FileHash.
Import-Module Microsoft.PowerShell.Utility -ErrorAction Stop
$global:T11Lock=$null
function Get-Command { param($Name) [pscustomobject]@{Source='fake-owned-accoreconsole.exe'} }
function Start-Process {
 param($FilePath,$ArgumentList,$RedirectStandardOutput,$RedirectStandardError,$WindowStyle,$WorkingDirectory,[switch]$PassThru)
 if($WindowStyle -ne 'Hidden'){throw 'visible conversion is forbidden'}
 if($ArgumentList -match [regex]::Escape($Source)){throw 'authoritative source was opened'}
 [IO.File]::WriteAllText($RedirectStandardOutput,'T11_PRIMARY_EXPORT_SENTINEL')
 [IO.File]::WriteAllText($RedirectStandardError,'')
 if($Mode -eq 'truncated_locked'){
  [IO.File]::WriteAllText((Join-Path $WorkingDirectory 'exchange.dxf'),"0`nSECTION`n2`nENTITIES`n")
 }else{[IO.File]::Copy($Fixture,(Join-Path $WorkingDirectory 'exchange.dxf'))}
 if($Mode -in @('success_locked','failure_locked','truncated_locked','source_changed_locked','release_on_retry')){
  $global:T11Lock=[IO.File]::Open((Join-Path $WorkingDirectory 'source.dwg'),[IO.FileMode]::Open,[IO.FileAccess]::Read,[IO.FileShare]::Read)
 }
 if($Mode -eq 'source_changed_locked'){[IO.File]::AppendAllText($Source,'test-source-change')}
 if($Mode -eq 'child_junction'){
  New-Item -ItemType Junction -Path (Join-Path $WorkingDirectory 'unrelated-link') -Value $Protected | Out-Null
 }
 $p=[pscustomobject]@{ExitCode=$(if($Mode -eq 'failure_locked'){17}else{0})}
 $p|Add-Member ScriptMethod WaitForExit {param($Milliseconds) if($null -ne $Milliseconds){return $true}}
 return $p
}
if($Mode -eq 'release_on_retry'){
 function Start-Sleep {
  param($Milliseconds)
  # Actual file removal fails once while this invocation's handle is open;
  # release it at the bounded retry boundary, without a helper process.
  $global:T11Lock.Dispose();$global:T11Lock=$null
 }
}
try{& $Exporter -SourceDwg $Source -OutputDxf $Target -ExportBackend coreconsole -TimeoutSeconds 1}
finally{if($null -ne $global:T11Lock){$global:T11Lock.Dispose();$global:T11Lock=$null}}
'''


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def powershell_env(shell):
    env = dict(os.environ)
    if Path(shell).stem.lower() == 'powershell':
        # A native 5.1 environment isolates T11 from the unrelated inherited
        # pwsh module-path problem, which can hide Get-FileHash before export.
        env['PSModulePath'] = os.pathsep.join([
            str(Path(os.environ['USERPROFILE']) / 'Documents/WindowsPowerShell/Modules'),
            str(Path(os.environ['ProgramFiles']) / 'WindowsPowerShell/Modules'),
            str(Path(shell).parent / 'Modules'),
        ])
    return env


def probe(tmp_path, shell, mode):
    folder = tmp_path / '中文路径 空格'
    folder.mkdir()
    source = folder / '原图.dwg'
    source.write_bytes(b'controlled synthetic source, not a CAD drawing')
    target = folder / '交换.dxf'
    fixture = folder / '合法交换样例.dxf'
    doc = ezdxf.new('R2018'); doc.units = 4
    doc.modelspace().add_line((0, 0), (100, 0))
    doc.saveas(fixture)
    protected = folder / '不归清理的目录'
    protected.mkdir()
    keep = protected / '保留.txt'; keep.write_text('unrelated fixture', encoding='utf-8')
    protected_hash = sha(keep)
    source_before = sha(source)
    wrapper = tmp_path / '导出进程边界.ps1'
    wrapper.write_text(WRAPPER, encoding='utf-8-sig')
    run = subprocess.run([shell, '-NoProfile', '-File', str(wrapper), str(EXPORTER), str(source),
                          str(target), str(fixture), mode, str(protected)], capture_output=True, env=powershell_env(shell),
                         stdin=subprocess.DEVNULL, timeout=45)
    assert sha(keep) == protected_hash
    if mode != 'source_changed_locked':
        assert sha(source) == source_before
    success = Path(str(target) + '.export.json')
    failed = Path(str(target) + '.导出失败.json')
    receipt_path = success if success.exists() else failed
    assert receipt_path.exists(), run.stdout + run.stderr
    receipt = json.loads(receipt_path.read_text(encoding='utf-8'))
    (tmp_path / '导出标准输出.txt').write_bytes(run.stdout)
    (tmp_path / '导出标准错误.txt').write_bytes(run.stderr)
    (tmp_path / '测试实际进程回执.json').write_text(json.dumps({
        'shell': shell, 'mode': mode, 'actual_exit_code': run.returncode,
        'export_status': receipt['export_status'], 'cleanup': receipt['cleanup'],
        'primary_error': receipt.get('export_error'), 'receipt': str(receipt_path),
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    return run, receipt, source, target, success, failed


@pytest.mark.skipif(sys.platform != 'win32' or not SHELLS, reason='Windows file-lock integration')
@pytest.mark.parametrize('shell', SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize('mode,cleanup,attempts', [
    ('success_clean', 'CLEANED', 1),
    ('success_locked', 'RETAINED', 3),
    ('release_on_retry', 'CLEANED', 2),
    ('child_junction', 'REFUSED', 1),
])
def test_success_remains_readable_with_independent_cleanup_state(tmp_path, shell, mode, cleanup, attempts):
    run, receipt, source, target, success, failed = probe(tmp_path, shell, mode)
    assert run.returncode == 0, run.stdout + run.stderr
    assert receipt['status'] == 'PASS' and receipt['export_status'] == 'SUCCEEDED'
    assert receipt['backend_completed'] is True
    assert receipt['source_immutable_verified'] is True and receipt['dxf_completion_verified'] is True
    assert success.exists() and not failed.exists() and sha(target) == receipt['output_sha256']
    result = receipt['cleanup']
    assert result['status'] == cleanup and result['attempts'] == attempts
    assert Path(result['stage_directory']).is_dir() is (cleanup != 'CLEANED')
    assert result['retained'] is (cleanup != 'CLEANED')
    assert bool(result['warning']) is (cleanup != 'CLEANED')
    # Use the real public reader on the receipt-bound output, with no CAD invocation.
    if mode == 'success_locked':
        out = tmp_path / '实际读取'
        read = subprocess.run([sys.executable, '-B', str(READER), '--source-dxf', str(target),
                               '--source-dwg', str(source), '--output-dir', str(out)],
                              capture_output=True, stdin=subprocess.DEVNULL, timeout=60)
        assert read.returncode in (0, 3), read.stdout + read.stderr
        summary = json.loads((out / 'CAD基础读取回执.json').read_text(encoding='utf-8'))
        assert summary['inputs_unchanged'] is True
        assert summary['exchange_dxf']['sha256'] == receipt['output_sha256']


@pytest.mark.skipif(sys.platform != 'win32' or not SHELLS, reason='Windows file-lock integration')
@pytest.mark.parametrize('shell', SHELLS, ids=lambda s: Path(s).stem)
@pytest.mark.parametrize('mode,primary', [
    ('failure_locked', 'T11_PRIMARY_EXPORT_SENTINEL'),
    ('truncated_locked', 'DXF'),
    ('source_changed_locked', '权威源'),
])
def test_primary_failure_is_preserved_when_cleanup_also_fails(tmp_path, shell, mode, primary):
    run, receipt, _, target, success, failed = probe(tmp_path, shell, mode)
    assert run.returncode != 0
    assert receipt['status'] == 'FAIL' and receipt['export_status'] == 'FAILED'
    assert primary in receipt['export_error']['message']
    if mode != 'source_changed_locked':
        assert primary in run.stderr.decode('utf-8', errors='replace')
    assert receipt['cleanup']['status'] == 'RETAINED' and receipt['cleanup']['attempts'] == 3
    assert receipt['cleanup']['retained'] is True and receipt['cleanup']['warning']
    assert Path(receipt['cleanup']['stage_directory']).is_dir()
    assert not success.exists() and failed.exists() and not target.exists()
    assert receipt['output_published'] is False
    assert primary in Path(receipt['diagnostic_path']).read_text(encoding='utf-8')
    assert receipt['backend_completed'] is (mode != 'failure_locked')
    assert receipt['source_immutable_verified'] is (mode != 'source_changed_locked')


@pytest.mark.skipif(sys.platform != 'win32' or not SHELLS, reason='PowerShell parser integration')
@pytest.mark.parametrize('shell', SHELLS, ids=lambda s: Path(s).stem)
def test_exporter_keeps_utf8_bom_and_parses_in_both_editions(tmp_path, shell):
    assert EXPORTER.read_bytes().startswith(b'\xef\xbb\xbf')
    parser = tmp_path / '语法核查.ps1'
    parser.write_text(r'''
param($Target)
$tokens=$null;$errors=$null
[void][Management.Automation.Language.Parser]::ParseFile($Target,[ref]$tokens,[ref]$errors)
if($errors.Count){$errors|ForEach-Object {$_.Message};exit 1}
''', encoding='utf-8-sig')
    run = subprocess.run([shell, '-NoProfile', '-File', str(parser), str(EXPORTER)],
                         capture_output=True, stdin=subprocess.DEVNULL, timeout=30, env=powershell_env(shell))
    assert run.returncode == 0, run.stdout + run.stderr
