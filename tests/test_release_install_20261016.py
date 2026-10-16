"""One-host release install (registration 20261016-1229): Claude then Codex, both or neither.

Host CLIs are replaced by fakes that write the host sections the way the real sub-commands do;
the real-CLI rehearsal with isolated CLAUDE_CONFIG_DIR / CODEX_HOME lives in the maintenance record.
"""
import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'maintenance'))
import codex_install
import install_check as ic
from release_package import ReleaseError


def make_check(tmp_path, claude='待执行', codex='待执行'):
    record = tmp_path/'0.9.9_abc.json'
    record.write_text(json.dumps({'business_version': '0.9.9', 'source_commit': 'a'*40, 'files': []}), encoding='utf-8')
    path = ic.check_path(record)
    path.write_text(json.dumps({'schema': ic.SCHEMA, 'release_record': str(record), 'source_commit': 'a'*40,
                                'hosts': {'Claude': {'status': claude}, 'Codex': {'status': codex}},
                                'overall': '未完成'}, ensure_ascii=False), encoding='utf-8')
    return argparse.Namespace(record=record, check_file=None, claude_config_dir=None, codex_home=None,
                              codex_marketplace=None, release_dir=tmp_path, codex_marketplace_root=None,
                              rollback_release_dir=None), path


def host_step(path, host, status, calls, name):
    def run(*_):
        calls.append(name)
        check = ic.load_check(path)
        check['hosts'][host]['status'] = status
        ic.save(path, check)
        return {'status': 'PASS' if status in ('已安装并核对', '已恢复') else 'FAIL'}
    return run


@pytest.fixture
def hosts(tmp_path, monkeypatch):
    args, path = make_check(tmp_path)
    calls = []
    monkeypatch.setattr(codex_install, 'require_owner', lambda: None)
    monkeypatch.setattr(ic, 'lock_owner', lambda: 'Claude test-model')

    def wire(claude_install='已安装并核对', codex_install_status='已安装并核对',
             claude_rollback='已恢复', codex_rollback='已恢复'):
        monkeypatch.setattr(ic, 'cmd_claude_install', host_step(path, 'Claude', claude_install, calls, 'claude-install'))
        monkeypatch.setattr(ic, 'cmd_claude_rollback', host_step(path, 'Claude', claude_rollback, calls, 'claude-rollback'))
        steps = {'codex-install': host_step(path, 'Codex', codex_install_status, calls, 'codex-install'),
                 'codex-rollback': host_step(path, 'Codex', codex_rollback, calls, 'codex-rollback')}
        monkeypatch.setattr(ic, 'cmd_codex', lambda a, command=None: steps[command]())
    return args, path, calls, wire


def test_both_hosts_installed_in_fixed_order_and_marked_done(hosts):
    args, path, calls, wire = hosts
    wire()
    result = ic.cmd_release_install(args)
    check = ic.load_check(path)
    assert result['status'] == 'PASS' and calls == ['claude-install', 'codex-install']
    assert check['overall'] == '双宿主发布完成'
    assert check['release_install']['executed_by'] == 'Claude test-model'
    assert check['release_install']['status'] == '两边已安装并核对'


def test_codex_failure_rolls_back_both_and_never_writes_done(hosts):
    args, path, calls, wire = hosts
    wire(codex_install_status='安装或核对失败')
    result = ic.cmd_release_install(args)
    check = ic.load_check(path)
    assert result['status'] == 'FAIL'
    # Codex is undone first, then Claude, which had already been verified.
    assert calls == ['claude-install', 'codex-install', 'codex-rollback', 'claude-rollback']
    assert check['overall'] == '未完成'
    assert {h: s['status'] for h, s in check['hosts'].items()} == {'Claude': '已恢复', 'Codex': '已恢复'}
    assert check['release_install']['status'] == '发布未完成：已恢复'


def test_claude_failure_never_starts_codex(hosts):
    args, path, calls, wire = hosts
    wire(claude_install='安装或核对失败')
    result = ic.cmd_release_install(args)
    check = ic.load_check(path)
    assert result['status'] == 'FAIL' and calls == ['claude-install', 'claude-rollback']
    assert check['hosts']['Codex']['status'] == '待执行'
    assert check['overall'] == '未完成'


def test_rollback_failure_stays_unfinished_not_verified(hosts):
    args, path, calls, wire = hosts
    wire(codex_install_status='安装或核对失败', claude_rollback='待回退')
    result = ic.cmd_release_install(args)
    check = ic.load_check(path)
    assert result['status'] == 'FAIL' and result['rollback']['status'] == 'FAIL'
    assert check['hosts']['Claude']['status'] == '恢复未完成'
    assert check['release_install']['status'] == '发布未完成：恢复未完成'
    assert check['overall'] == '未完成'


def test_rollback_step_that_raises_is_recorded_as_unfinished(hosts, monkeypatch):
    args, path, calls, wire = hosts
    wire(codex_install_status='安装或核对失败')
    steps = {'codex-install': host_step(path, 'Codex', '安装或核对失败', calls, 'codex-install')}
    def codex(a, command=None):
        if command == 'codex-rollback':
            raise ReleaseError('rollback package changed')
        return steps[command]()
    monkeypatch.setattr(ic, 'cmd_codex', codex)
    ic.cmd_release_install(args)
    check = ic.load_check(path)
    assert check['hosts']['Codex']['status'] == '恢复未完成'
    assert check['hosts']['Claude']['status'] == '已恢复'


def test_started_batch_is_refused(tmp_path, monkeypatch):
    args, path = make_check(tmp_path, claude='已安装并核对')
    monkeypatch.setattr(codex_install, 'require_owner', lambda: None)
    with pytest.raises(ReleaseError, match='该批次已开始'):
        ic.cmd_release_install(args)


def test_missing_lock_fails_before_any_install(tmp_path, monkeypatch):
    args, path = make_check(tmp_path)
    def no_lock():
        raise ReleaseError('Codex 安装操作需要本会话维护锁')
    monkeypatch.setattr(codex_install, 'require_owner', no_lock)
    monkeypatch.setattr(ic, 'cmd_claude_install', lambda a: pytest.fail('must not install'))
    with pytest.raises(ReleaseError, match='维护锁'):
        ic.cmd_release_install(args)


@pytest.mark.parametrize('command', ['release-verify', 'release-rollback'])
def test_missing_lock_refuses_verify_and_rollback_before_touching_either_host(tmp_path, monkeypatch, capsys, command):
    # Cross-check B4A-M3-01: both entries used to run the Claude half first and meet the lock only on the Codex half.
    args, path = make_check(tmp_path, claude='已安装并核对', codex='已安装并核对')
    before = path.read_bytes()
    def no_lock():
        raise ReleaseError('Codex 安装操作需要本会话维护锁')
    monkeypatch.setattr(codex_install, 'require_owner', no_lock)
    for name in ('cmd_claude_verify', 'cmd_claude_rollback', 'cmd_claude_install'):
        monkeypatch.setattr(ic, name, lambda *a, n=name: pytest.fail(f'{n} must not run without the lock'))
    monkeypatch.setattr(ic, 'cmd_codex', lambda *a, **k: pytest.fail('Codex side must not run without the lock'))
    assert ic.main([command, '--record', str(args.record)]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result['status'] == 'FAIL' and '维护锁' in result['error']
    assert path.read_bytes() == before


def write_tree(root, files):
    for name, text in files.items():
        p = root/name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')


def test_claude_upgrade_rollback_puts_previous_package_back_and_reinstalls(tmp_path, monkeypatch):
    old_files, new_files = {'a.txt': 'old', 'skills/x/SKILL.md': 'x1'}, {'a.txt': 'new', 'skills/x/SKILL.md': 'x2'}
    release, backup, cache = tmp_path/'rel'/'cad-qto', tmp_path/'rollback'/'0.9.8', tmp_path/'cache'/'0.9.8'
    write_tree(release, new_files); write_tree(backup, old_files); write_tree(cache, old_files)
    snapshot = ic.package_snapshot(cache)
    args, path = make_check(tmp_path, claude='待回退', codex='已恢复')
    record = json.loads(args.record.read_text(encoding='utf-8'))
    record['files'] = ic.package_snapshot(release)['files']
    args.record.write_text(json.dumps(record), encoding='utf-8')
    check = ic.load_check(path)
    old = {'id': ic.PLUGIN_ID, 'version': '0.9.8', 'enabled': True, 'scope': 'user', 'installPath': str(cache)}
    check['hosts']['Claude'].update(actions=['marketplace_updated', 'plugin_updated'], release_dir=str(release),
                                    before={'plugins': [old], 'marketplace': {'name': ic.MARKETPLACE},
                                            'package_snapshot': snapshot})
    ic.save(path, check)
    calls = []
    monkeypatch.setattr(ic, 'claude', lambda a, c, check=True: calls.append(a))
    monkeypatch.setattr(ic, 'claude_state', lambda c: {'plugins': [old], 'marketplace': {'name': ic.MARKETPLACE}})
    args.rollback_release_dir = backup
    result = ic.cmd_claude_rollback(args)
    assert result['status'] == 'PASS', result
    assert (release/'a.txt').read_text(encoding='utf-8') == 'old'
    assert (tmp_path/'rel'/'cad-qto_Claude回退前发布包'/'a.txt').read_text(encoding='utf-8') == 'new'
    assert calls == [['plugin', 'marketplace', 'update', ic.MARKETPLACE],
                     ['plugin', 'uninstall', ic.PLUGIN_ID, '--keep-data'],
                     ['plugin', 'install', ic.PLUGIN_ID, '--scope', 'user']]
    assert ic.load_check(path)['hosts']['Claude']['status'] == '已恢复'


def test_claude_upgrade_rollback_without_previous_package_stays_pending(tmp_path, monkeypatch):
    args, path = make_check(tmp_path, claude='待回退')
    check = ic.load_check(path)
    check['hosts']['Claude'].update(actions=['plugin_updated'], before={'plugins': [], 'marketplace': None})
    ic.save(path, check)
    monkeypatch.setattr(ic, 'claude_state', lambda c: {'plugins': [], 'marketplace': None})
    result = ic.cmd_claude_rollback(args)
    assert result['status'] == 'FAIL' and result['pending']
    assert ic.load_check(path)['hosts']['Claude']['status'] == '恢复未完成'


def test_claude_side_refuses_to_swap_source_codex_still_uses(tmp_path):
    release, backup = tmp_path/'rel', tmp_path/'bk'
    write_tree(release, {'a.txt': 'new'}); write_tree(backup, {'a.txt': 'old'})
    snapshot = ic.package_snapshot(backup)
    check = {'hosts': {'Codex': {'status': '已安装并核对'}}, 'release_record': str(tmp_path/'r.json')}
    with pytest.raises(ReleaseError, match='Codex 已使用本次共同发布来源'):
        ic.restore_previous_release_dir(check, {'release_dir': str(release)}, backup, snapshot)
    assert (release/'a.txt').read_text(encoding='utf-8') == 'new'


def test_parser_offers_one_host_release_commands():
    for name in ('release-install', 'release-verify', 'release-rollback'):
        with pytest.raises(SystemExit) as exc:
            ic.main([name, '--help'])
        assert exc.value.code == 0
