"""Dual-host (Claude / Codex) compatibility checks; fast, no model calls, run by the pre-commit hook.

Covers: both plugin manifests and the local marketplace agree; entry frontmatter; relative links
close over the shipped package; runtime manifest completeness; PowerShell 5.1/7 parsing; the
approval contract wiring; HTML version reading; maintenance entry files.
"""
from __future__ import annotations
import codecs, json, re, shutil, subprocess, sys
from pathlib import Path
from urllib.parse import unquote
import pytest

ROOT=Path(__file__).resolve().parents[1]
SKILLS=ROOT/'skills'
sys.path.insert(0,str(SKILLS/'_qto-shared-evidence/scripts'))
sys.path.insert(0,str(ROOT/'maintenance'))
from release_package import ROOT_WHITELIST, business_version, check_versions

MANIFEST=json.loads((ROOT/'运行文件清单.json').read_text(encoding='utf-8'))
PACKAGE={'skills/'+e['path'] for e in MANIFEST['files']}|set(ROOT_WHITELIST)|{'发布来源.json'}
ENTRIES=sorted(p.parent.name for p in SKILLS.glob('*/SKILL.md'))


def load(rel):
    return json.loads((ROOT/rel).read_text(encoding='utf-8'))


def test_manifests_and_marketplace_share_one_business_version():
    info=check_versions({p:(ROOT/p).read_bytes() for p in ROOT_WHITELIST if p.endswith('.json') and p!='运行文件清单.json'})
    assert info['business_version']==business_version(load('.codex-plugin/plugin.json')['version'])
    market=load('.claude-plugin/marketplace.json')
    assert market['plugins'][0]['source']=='./' and market['name']=='cad-qto-local'
    assert load('.codex-plugin/plugin.json')['skills']=='./skills/'


def test_entry_count_matches_manifest_and_shared_dir_is_not_an_entry():
    expected=MANIFEST['professional_skills']+MANIFEST['coordinator_skills']+MANIFEST['basic_read_skills']
    assert len(ENTRIES)==expected==15 and '_qto-shared-evidence' not in ENTRIES
    assert not (SKILLS/'_qto-shared-evidence/SKILL.md').exists()


@pytest.mark.parametrize('entry',ENTRIES)
def test_entry_frontmatter_and_issue_pointer(entry):
    text=(SKILLS/entry/'SKILL.md').read_text(encoding='utf-8')
    match=re.match(r'---\n(.*?)\n---\n',text,re.S)
    assert match,entry
    fields=dict(re.findall(r'^(\w[\w-]*):\s*(.+)$',match.group(1),re.M))
    assert fields.get('name')==entry and len(fields.get('description','').strip())>10
    assert '插件问题登记.md' in text.rsplit('\n## ',1)[-1],'SKILL 末尾缺少插件问题登记指引'


def test_runtime_manifest_lists_exactly_the_runtime_files():
    actual={p.relative_to(SKILLS).as_posix() for p in SKILLS.rglob('*') if p.is_file()
            and '__pycache__' not in p.parts and p.suffix not in {'.pyc','.pyo'}}
    listed={e['path'] for e in MANIFEST['files']}
    assert actual==listed,{'unlisted':sorted(actual-listed)[:10],'missing':sorted(listed-actual)[:10]}


def markdown_links(path):
    text=path.read_text(encoding='utf-8')
    for target in re.findall(r'\]\(([^)\s]+)\)',text):
        if re.match(r'[a-z]+:',target) or target.startswith('#'):
            continue
        yield unquote(target.split('#',1)[0])


def test_relative_links_in_shipped_documents_resolve_inside_the_package():
    broken=[]
    for rel in sorted(PACKAGE):
        if not rel.endswith('.md') or not (ROOT/rel).exists():
            continue
        for target in markdown_links(ROOT/rel):
            resolved=(ROOT/rel).parent.joinpath(target).resolve()
            try:
                inside=resolved.relative_to(ROOT.resolve()).as_posix()
            except ValueError:
                broken.append((rel,target,'出包'));continue
            if inside not in PACKAGE and not any(p.startswith(inside.rstrip('/')+'/') for p in PACKAGE):
                broken.append((rel,target,'不在发布包'))
    assert not broken,broken[:20]


def ps1_files():
    return sorted(SKILLS.rglob('*.ps1'))


@pytest.mark.parametrize('script',ps1_files(),ids=lambda p:p.name)
def test_non_ascii_powershell_scripts_carry_utf8_bom(script):
    data=script.read_bytes()
    assert data.isascii() or data.startswith(codecs.BOM_UTF8),'Windows PowerShell 5.1 会把无 BOM 的 UTF-8 按 ANSI 读取'


SHELLS=[s for s in dict.fromkeys([shutil.which('pwsh'),shutil.which('powershell')]) if s]


@pytest.mark.skipif(not SHELLS,reason='no PowerShell available')
@pytest.mark.parametrize('shell',SHELLS,ids=lambda s:Path(s).stem)
def test_powershell_scripts_parse_on_each_available_shell(shell):
    paths=';'.join(f"'{str(p)}'" for p in ps1_files())
    command=('$bad=0; foreach($f in @('+paths+')){$t=$null;$e=$null;'
             '[void][System.Management.Automation.Language.Parser]::ParseFile($f,[ref]$t,[ref]$e);'
             'if($e.Count){$bad+=$e.Count; Write-Output ($f+": "+$e[0].Message)}}; exit $bad')
    proc=subprocess.run([shell,'-NoProfile','-NonInteractive','-Command',command],stdin=subprocess.DEVNULL,capture_output=True)
    assert proc.returncode==0,proc.stdout.decode('utf-8','replace')+proc.stderr.decode('utf-8','replace')


def test_approval_contract_wiring():
    from recompute_qto_dependencies import recalculate
    d={'schema':'qto-calculation-graph/1.0','project_id':'p','sources':[{'id':'s','sha256':'a'*64}],'rules':[],
       'nodes':[{'id':'n','unit':'m','value':1.,'source_ids':['s']}]}
    fp=recalculate(d)['nodes'][0]['fingerprint']
    for actor in ['Claude','Codex','GPT-6','张工']:
        d['nodes'][0]['approval']={'fingerprint':fp,'actor':actor}
        run=recalculate(d)
        assert run['nodes'][0]['review_state']=='unreviewed' and run['approval_issues']
    for rel in ['qto-pipe-networks/scripts/build_pipe_inference_input.py','qto-components-points/scripts/build_component_instances_from_neutral.py']:
        assert 'is_model_identity' in (SKILLS/rel).read_text(encoding='utf-8')


def test_html_version_prefers_release_source_and_reports_conflicts(tmp_path):
    from build_qto_review_html import plugin_version
    assert plugin_version(ROOT)==business_version(load('.codex-plugin/plugin.json')['version'])
    (tmp_path/'.codex-plugin').mkdir();(tmp_path/'.claude-plugin').mkdir()
    (tmp_path/'.codex-plugin/plugin.json').write_text('{"version":"0.3.0+codex.1"}',encoding='utf-8')
    (tmp_path/'.claude-plugin/plugin.json').write_text('{"version":"0.3.0"}',encoding='utf-8')
    assert plugin_version(tmp_path)=='0.3.0'
    (tmp_path/'.claude-plugin/plugin.json').write_text('{"version":"0.2.9"}',encoding='utf-8')
    with pytest.raises(ValueError,match='不一致'):
        plugin_version(tmp_path)
    (tmp_path/'发布来源.json').write_text('{"business_version":"0.3.0"}',encoding='utf-8')
    assert plugin_version(tmp_path)=='0.3.0'


def test_maintenance_entry_files():
    agents=ROOT/'AGENTS.md'
    assert agents.exists() and agents.stat().st_size<=8*1024,'AGENTS.md 须在约 8KB 以内'
    assert (ROOT/'CLAUDE.md').read_text(encoding='utf-8').strip()=='@AGENTS.md'
    assert not (ROOT/'发布来源.json').exists(),'发布来源.json 只在发布包内生成'
    for rel in ['.githooks/pre-commit','.githooks/pre-push']:
        assert (ROOT/rel).read_bytes().startswith(b'#!/bin/sh\n')
