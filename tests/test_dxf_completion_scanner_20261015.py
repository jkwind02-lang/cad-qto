"""Compiled DXF completion scanner used by export_dwg_exchange_dxf.ps1; both PowerShell editions.

Registered 20261014-2341: the PowerShell per-line scan took longer than the export. The compiled
scanner must keep every former rejection and still accept a complete ASCII DXF.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCANNER = ROOT / 'skills/_qto-shared-evidence/scripts/DxfCompletion.cs'
SHELLS = [s for s in dict.fromkeys([shutil.which('pwsh'), shutil.which('powershell')]) if s]

CASES = {
    'complete': ("0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nSECTION\n2\nENTITIES\n0\nLINE\n8\n0\n0\nENDSEC\n0\nEOF\n", None),
    'complete_crlf_with_unicode_value': ("0\r\nSECTION\r\n2\r\nENTITIES\r\n0\r\nTEXT\r\n1\r\n示例文字\r\n0\r\nENDSEC\r\n0\r\nEOF\r\n", None),
    'truncated_value': ("0\nSECTION\n2\nENTITIES\n0", "DXF组码缺值"),
    'unclosed_section': ("0\nSECTION\n2\nENTITIES\n0\nEOF\n", "DXF节未闭合即结束"),
    'no_eof': ("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n", "DXF未完整导出"),
    'no_entities': ("0\nSECTION\n2\nHEADER\n0\nENDSEC\n0\nEOF\n", "DXF未完整导出"),
    'data_after_eof': ("0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n0\nSECTION\n", "DXF EOF后仍有数据"),
    'nested_section': ("0\nSECTION\n2\nENTITIES\n0\nSECTION\n2\nBLOCKS\n0\nENDSEC\n0\nEOF\n", "DXF节未关闭"),
    'stray_endsec': ("0\nENDSEC\n0\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", "无对应起点的ENDSEC"),
    'non_numeric_code': ("AcDb\nSECTION\n2\nENTITIES\n0\nENDSEC\n0\nEOF\n", "DXF组码非法"),
    'missing_section_name': ("0\nSECTION\n0\nENDSEC\n0\nEOF\n", "DXF节名缺失"),
}


@pytest.mark.skipif(sys.platform != 'win32' or not SHELLS, reason='Windows PowerShell integration')
@pytest.mark.parametrize('shell', SHELLS, ids=lambda s: Path(s).stem)
def test_compiled_scanner_matches_former_rules(tmp_path, shell):
    folder = tmp_path / 'cases'
    folder.mkdir()
    for name, (text, _) in CASES.items():
        (folder / f'{name}.dxf').write_bytes(text.encode('utf-8'))
    probe = tmp_path / 'probe.ps1'
    probe.write_text('''
param($Scanner, $Folder)
$ErrorActionPreference = 'Stop'
Add-Type -Path $Scanner -ErrorAction Stop
$result = @{}
foreach ($file in Get-ChildItem -LiteralPath $Folder -Filter *.dxf) {
    $result[$file.BaseName] = [QtoDxfCompletion]::Check($file.FullName)
}
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$result | ConvertTo-Json -Compress
''', encoding='utf-8-sig')
    run = subprocess.run([shell, '-NoProfile', '-File', str(probe), str(SCANNER), str(folder)],
                         capture_output=True, stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stdout + run.stderr
    verdicts = json.loads(run.stdout.decode('utf-8'))
    for name, (_, expected) in CASES.items():
        if expected is None:
            assert verdicts[name] is None, (name, verdicts[name])
        else:
            assert verdicts[name] and expected in verdicts[name], (name, verdicts[name])
