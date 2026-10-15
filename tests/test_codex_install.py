"""Codex install boundaries and restore logic, no real host mutation in unit tests."""
import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'maintenance'))
import codex_install as ci
from release_package import ReleaseError


def test_context_cannot_redirect_existing_check_to_another_home(tmp_path):
    args = argparse.Namespace(codex_home=tmp_path/'other', codex_marketplace=None)
    with pytest.raises(ReleaseError, match='另一 CODEX_HOME'):
        ci.context(args, {'codex_home': str(tmp_path/'original'), 'marketplace': 'personal'})


def test_restore_changes_only_target_plugin_table(tmp_path):
    p = tmp_path/'config.toml'
    p.write_text('model = "keep"\n[plugins."cad-qto@personal"]\nenabled = true\n'
                 '[plugins."other@personal"]\nenabled = true\n', encoding='utf-8')
    ci.restore_plugin_block(tmp_path, 'cad-qto@personal', '[plugins."cad-qto@personal"]\nenabled = false\n')
    assert 'model = "keep"' in p.read_text(encoding='utf-8')
    assert '[plugins."other@personal"]\nenabled = true' in p.read_text(encoding='utf-8')
    assert 'enabled = false' in ci.plugin_block(tmp_path, 'cad-qto@personal')
    ci.restore_plugin_block(tmp_path, 'cad-qto@personal', None)
    assert ci.plugin_block(tmp_path, 'cad-qto@personal') is None
    assert '[plugins."other@personal"]\nenabled = true' in p.read_text(encoding='utf-8')


def test_cache_path_refuses_version_traversal(tmp_path):
    with pytest.raises(ReleaseError):
        ci.cache_path(tmp_path, 'personal', {'version': '../../other'})


def test_record_hash_tamper_is_rejected(tmp_path):
    p=tmp_path/'record.json';p.write_text('{}', encoding='utf-8')
    with pytest.raises(ReleaseError, match='哈希'):
        ci.checked_record(argparse.Namespace(record=p), {'release_record_sha256': '0'*64})


def test_entry_expectation_comes_from_runtime_manifest_paths():
    assert ci.expected_entries({'files':[{'path':'skills/a/SKILL.md'}, {'path':'skills/b/SKILL.md'},
                                         {'path':'skills/_shared/rules.md'}]}) == {'a','b'}


def test_failed_first_install_removes_only_added_plugin_and_restores_config(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr(ci, 'require_owner', lambda:None)
    monkeypatch.setattr(ci, 'checked_record', lambda a,c:{})
    monkeypatch.setattr(ci, 'codex', lambda arguments,home:calls.append(arguments) or {})
    monkeypatch.setattr(ci, 'state', lambda h,m:{'plugins':[], 'marketplace':None})
    args=argparse.Namespace(codex_home=tmp_path, codex_marketplace='test')
    check={'hosts':{'Codex':{'before':{'plugins':[], 'marketplace':None,'plugin_config_block':None},
                            'actions':['marketplace_added','plugin_installed']}}}
    result=ci.cmd_rollback(args,tmp_path/'check.json',check,lambda p,c:None)
    assert result['status']=='PASS'
    assert calls == [['plugin','remove','cad-qto@test','--json'],
                     ['plugin','marketplace','remove','test','--json']]


def test_failed_rollback_cannot_report_success(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, 'require_owner', lambda:None)
    monkeypatch.setattr(ci, 'checked_record', lambda a,c:{})
    def fail(*a):raise ReleaseError('restore failed')
    monkeypatch.setattr(ci, 'codex', fail)
    args=argparse.Namespace(codex_home=tmp_path,codex_marketplace='test')
    check={'hosts':{'Codex':{'before':{'plugins':[],'plugin_config_block':None},'actions':['plugin_installed']}}}
    result=ci.cmd_rollback(args,tmp_path/'check.json',check,lambda p,c:None)
    assert result['status']=='FAIL' and check['hosts']['Codex']['status']=='恢复未完成'


def test_rollback_checks_partial_install_when_cli_response_was_lost(tmp_path, monkeypatch):
    calls=[]
    monkeypatch.setattr(ci, 'require_owner', lambda:None)
    monkeypatch.setattr(ci, 'checked_record', lambda a,c:{})
    states=iter([{'plugins':[{'pluginId':'cad-qto@test'}], 'marketplace':{'name':'test'}},
                 {'plugins':[], 'marketplace':{'name':'test'}},
                 {'plugins':[], 'marketplace':None}])
    monkeypatch.setattr(ci, 'state', lambda h,m:next(states))
    monkeypatch.setattr(ci, 'codex', lambda arguments,home:calls.append(arguments) or {})
    args=argparse.Namespace(codex_home=tmp_path,codex_marketplace='test')
    check={'hosts':{'Codex':{'before':{'plugins':[],'marketplace':None,'plugin_config_block':None},
                            'actions':['marketplace_add_started','plugin_add_started']}}}
    result=ci.cmd_rollback(args,tmp_path/'check.json',check,lambda p,c:None)
    assert result['status']=='PASS'
    assert calls == [['plugin','remove','cad-qto@test','--json'],
                     ['plugin','marketplace','remove','test','--json']]


# Fake CLI: an unscoped `plugin list` hangs like the remote-market query in registration 20261016-0005;
# a scoped list still returns foreign entries so the pluginId filter is exercised too.
FAKE_CODEX = '''import json, os, sys, time
args = sys.argv[1:]
with open(os.environ['FAKE_CODEX_LOG'], 'a', encoding='utf-8') as f: f.write(json.dumps(args) + '\\n')
if args[:2] == ['plugin', 'list']:
    if '--marketplace' not in args or os.environ.get('FAKE_CODEX_SLOW'): time.sleep(20)
    m = args[args.index('--marketplace') + 1]
    items = [{'pluginId': 'cad-qto@other', 'version': '9.9.9'}, {'pluginId': 'tool@personal', 'version': '1.0.0'},
             {'pluginId': 'cad-qto@' + m, 'version': '0.3.7'}] if m == 'personal' else []
    print(json.dumps({'installed': items, 'available': []}))
elif args[:3] == ['plugin', 'marketplace', 'list']:
    print(json.dumps({'marketplaces': [{'name': 'personal', 'root': 'C:/u'}, {'name': 'other', 'root': 'C:/o'}]}))
'''


@pytest.fixture
def fake_codex(tmp_path, monkeypatch):
    script, log = tmp_path/'fake_codex.py', tmp_path/'calls.jsonl'
    script.write_text(FAKE_CODEX, encoding='utf-8')
    monkeypatch.setattr(ci, 'cli_prefix', lambda:[sys.executable, str(script)])
    monkeypatch.setenv('FAKE_CODEX_LOG', str(log))
    monkeypatch.setattr(ci, 'QUERY_TIMEOUT', 5)
    return lambda:[json.loads(line) for line in log.read_text(encoding='utf-8').splitlines()]


def test_state_scopes_plugin_list_to_target_market_and_filters_foreign_entries(tmp_path, fake_codex):
    current = ci.state(tmp_path, 'personal')
    assert current == {'plugins': [{'pluginId': 'cad-qto@personal', 'version': '0.3.7'}],
                       'marketplace': {'name': 'personal', 'root': 'C:/u'}}
    assert fake_codex() == [['plugin', 'list', '--marketplace', 'personal', '--json'],
                            ['plugin', 'marketplace', 'list', '--json']]


def test_state_of_unregistered_market_is_empty_so_install_registers_it(tmp_path, fake_codex):
    # Legal contrast: codex-cli 0.162.1 answers a scoped list of an unknown market with empty lists (exit 0).
    assert ci.state(tmp_path, 'fresh') == {'plugins': [], 'marketplace': None}
    assert fake_codex()[0] == ['plugin', 'list', '--marketplace', 'fresh', '--json']


def test_query_timeout_names_the_step(tmp_path, fake_codex, monkeypatch):
    monkeypatch.setenv('FAKE_CODEX_SLOW', '1')
    monkeypatch.setattr(ci, 'QUERY_TIMEOUT', 1)
    with pytest.raises(ReleaseError, match=r'安装状态查询（插件列表，限 personal 市场）超时（1 秒）'):
        ci.state(tmp_path, 'personal')
