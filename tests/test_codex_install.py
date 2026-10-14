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
