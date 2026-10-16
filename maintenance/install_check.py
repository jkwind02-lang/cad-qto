#!/usr/bin/env python3
"""Per-version install check records and the Claude / Codex install, verify and rollback adapters.

The record lives next to the release record (<版本>_<提交>_安装核对.json), one section per host.
A version counts as released on both hosts only when both sections are verified.

The publishing host installs both sides in one run (release-install: Claude first, then Codex;
registration 20261016-1229). If either side fails, release-install rolls both back to their state
before this run and neither section is written as verified. The per-host commands stay for
re-verification and single-side repair.

Claude steps use the claude CLI; pass --claude-config-dir to rehearse in an isolated
CLAUDE_CONFIG_DIR without touching the real installation. Codex steps use --codex-home;
give isolated rehearsals their own --check-file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maint_common import dump_json, now_iso, read_json, sha256_file, write_json_atomic  # noqa: E402
from release_package import ReleaseError, verify_dir  # noqa: E402

SCHEMA = 'cad-qto-install-check/1.0'
PLUGIN = 'cad-qto'
MARKETPLACE = 'cad-qto-local'
PLUGIN_ID = f'{PLUGIN}@{MARKETPLACE}'
# Files a host may add inside its own cache copy; anything else outside the record is "extra".
CLAUDE_CACHE_EXEMPT: list[str] = []
EXPECTED_ENTRIES = 15
CODEX_PENDING = '待 Codex 接续'


def check_path(record_path: Path) -> Path:
    return record_path.with_name(record_path.stem + '_安装核对.json')


def operation_path(args) -> Path:
    return getattr(args, 'check_file', None) or check_path(args.record)


def load_check(path: Path) -> dict:
    if not path.exists():
        raise ReleaseError(f'安装核对记录不存在，先运行 init：{path}')
    return read_json(path)


def save(path: Path, check: dict) -> None:
    hosts = check['hosts']
    done = all(hosts[h].get('status') == '已安装并核对' for h in ('Claude', 'Codex'))
    check['overall'] = '双宿主发布完成' if done else '未完成'
    check['updated_at'] = now_iso()
    write_json_atomic(path, check)


def cmd_init(args) -> dict:
    record = read_json(args.record)
    path = operation_path(args)
    if path.exists():
        return {'status': 'PASS', 'check': str(path), 'note': '已存在，未改写'}
    check = {'schema': SCHEMA, 'business_version': record['business_version'], 'source_commit': record['source_commit'],
             'release_record': str(args.record.resolve()), 'release_record_sha256': sha256_file(args.record),
             'hosts': {'Claude': {'status': '待执行'},
                       'Codex': {'status': '待执行'}},
             'note': '发布宿主用 release-install 一次装两边；单侧命令只用于复核和单侧修复',
             'overall': '未完成', 'created_at': now_iso()}
    save(path, check)
    return {'status': 'PASS', 'check': str(path)}


# ---------- Claude ----------

def claude_env(config_dir: str | None) -> dict:
    env = dict(os.environ)
    if config_dir:
        env['CLAUDE_CONFIG_DIR'] = config_dir
    return env


def claude(args_list: list[str], config_dir: str | None, check: bool = True) -> subprocess.CompletedProcess:
    exe = shutil.which('claude')
    if not exe:
        raise ReleaseError('找不到 claude 命令')
    proc = subprocess.run([exe, *args_list], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          encoding='utf-8', errors='replace', env=claude_env(config_dir), timeout=300)
    if check and proc.returncode != 0:
        raise ReleaseError(f"claude {' '.join(args_list)} 失败：{(proc.stderr or proc.stdout).strip()[:800]}")
    return proc


def claude_state(config_dir: str | None) -> dict:
    plugins = json.loads(claude(['plugin', 'list', '--json'], config_dir).stdout or '[]')
    markets = json.loads(claude(['plugin', 'marketplace', 'list', '--json'], config_dir).stdout or '[]')
    return {'plugins': [p for p in plugins if p.get('id', '').split('@')[0] == PLUGIN],
            'marketplace': next((m for m in markets if m.get('name') == MARKETPLACE), None)}


def package_snapshot(directory: Path) -> dict | None:
    if not directory.is_dir():
        return None
    return {'files': [{'path': p.relative_to(directory).as_posix(), 'sha256': sha256_file(p)}
                      for p in sorted(directory.rglob('*')) if p.is_file()
                      and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')]}


def claude_verify_installed(check: dict, config_dir: str | None) -> dict:
    record = read_json(Path(check['release_record']))
    state = claude_state(config_dir)
    installed = [p for p in state['plugins'] if p.get('id') == PLUGIN_ID]
    if len(installed) != 1:
        return {'status': 'FAIL', 'reason': f'未找到唯一安装项 {PLUGIN_ID}', 'state': state}
    item = installed[0]
    cache = Path(item['installPath'])
    report = verify_dir(cache, record, CLAUDE_CACHE_EXEMPT)
    details = claude(['plugin', 'details', PLUGIN], config_dir, check=False)  # a fresh CLI process loads the install
    skills = re.search(r'Skills \((\d+)\)', details.stdout or '')
    version_ok = record['business_version'] == str(item.get('version', '')).split('+')[0]
    loaded = {'returncode': details.returncode, 'skills': int(skills.group(1)) if skills else None,
              'version_line': (details.stdout or '').splitlines()[0] if details.stdout else None}
    ok = report['status'] == 'PASS' and version_ok and loaded['skills'] == EXPECTED_ENTRIES and item.get('enabled') is not False
    return {'status': 'PASS' if ok else 'FAIL', 'install_path': str(cache), 'installed_version': item.get('version'),
            'enabled': item.get('enabled'), 'scope': item.get('scope'), 'version_matches_record': version_ok,
            'cache_check': report, 'fresh_load': loaded}


def cmd_claude_install(args) -> dict:
    path = operation_path(args)
    check = load_check(path)
    section = check['hosts']['Claude']
    release_dir = args.release_dir.resolve()
    source = read_json(release_dir / '发布来源.json')
    if source['source_commit'] != check['source_commit']:
        raise ReleaseError('发布目录的来源提交与安装核对记录不一致')
    before = claude_state(args.claude_config_dir)
    # Bytes of the version being replaced: an upgrade rollback reinstalls it and checks against this.
    old = next((p for p in before['plugins'] if p.get('id') == PLUGIN_ID), None)
    before['package_snapshot'] = package_snapshot(Path(old['installPath'])) if old and old.get('installPath') else None
    section.update(status='安装中', before=before, release_dir=str(release_dir),
                   isolated_config_dir=args.claude_config_dir, started_at=now_iso())
    save(path, check)
    actions = []
    try:
        market = before['marketplace']
        if market is None:
            claude(['plugin', 'marketplace', 'add', str(release_dir)], args.claude_config_dir)
            actions.append('marketplace_added')
        else:
            location = Path(market.get('path') or market.get('installLocation') or '').resolve()
            if location != release_dir:
                raise ReleaseError(f'已有同名市场 {MARKETPLACE} 指向其他来源：{location}；不覆盖，请人工核对')
            claude(['plugin', 'marketplace', 'update', MARKETPLACE], args.claude_config_dir)
            actions.append('marketplace_updated')
        if any(p.get('id') == PLUGIN_ID for p in before['plugins']):
            claude(['plugin', 'update', PLUGIN_ID], args.claude_config_dir)
            actions.append('plugin_updated')
        else:
            claude(['plugin', 'install', PLUGIN_ID], args.claude_config_dir)
            actions.append('plugin_installed')
        result = claude_verify_installed(check, args.claude_config_dir)
    except (ReleaseError, subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        result = {'status': 'FAIL', 'reason': str(exc)}
    section.update(actions=actions, verification=result, finished_at=now_iso(),
                   status='已安装并核对' if result['status'] == 'PASS' else '安装或核对失败')
    save(path, check)
    return {'status': result['status'], 'check': str(path), 'actions': actions, 'verification': result}


def cmd_claude_verify(args) -> dict:
    path = operation_path(args)
    check = load_check(path)
    result = claude_verify_installed(check, args.claude_config_dir)
    section = check['hosts']['Claude']
    section.update(verification=result, verified_at=now_iso(),
                   status='已安装并核对' if result['status'] == 'PASS' else '安装或核对失败')
    save(path, check)
    return {'status': result['status'], 'check': str(path), 'verification': result}


def restore_previous_release_dir(check: dict, section: dict, backup: Path, snapshot: dict) -> str:
    """Make the shared release directory hold the previous package again; the Codex rollback may already have."""
    import codex_install
    current = Path(section['release_dir']).resolve()
    if verify_dir(current, snapshot)['status'] == 'PASS':
        return 'release_dir_already_previous'
    if check['hosts']['Codex'].get('status') == '已安装并核对':
        raise ReleaseError('Codex 已使用本次共同发布来源；须先协调共同发布回退（release-rollback），不能单侧改共同来源')
    if verify_dir(backup, snapshot)['status'] != 'PASS':
        raise ReleaseError('上一版回滚包与 Claude 原安装字节不一致，拒绝恢复')
    record = read_json(Path(check['release_record']))
    section['rollback_displaced_package'] = codex_install.swap_in_previous_release(current, backup, snapshot, record,
                                                                                   'Claude')
    return 'release_dir_restored'


def cmd_claude_rollback(args) -> dict:
    """Undo only what this install did for Claude: a new install is removed, an added marketplace is removed.

    An upgrade is undone by putting the previous package back into the release directory (from
    --rollback-release-dir, checked against the bytes Claude had before) and reinstalling it; without
    that package the step stays pending and the status is "恢复未完成".
    """
    path = operation_path(args)
    check = load_check(path)
    section = check['hosts']['Claude']
    actions = section.get('actions', [])
    before = section.get('before', {})
    config_dir = args.claude_config_dir
    done, pending = [], []
    try:
        if 'plugin_installed' in actions:
            claude(['plugin', 'uninstall', PLUGIN_ID, '--keep-data'], config_dir)
            done.append('plugin_uninstalled')
        if 'plugin_updated' in actions:
            old = next((p for p in before.get('plugins', []) if p.get('id') == PLUGIN_ID), None)
            snapshot = before.get('package_snapshot')
            backup = getattr(args, 'rollback_release_dir', None)
            if not (old and snapshot and backup):
                pending.append('先用回滚包恢复上一版发布目录，再重装上一版（缺安装前字节快照或 --rollback-release-dir）')
            else:
                done.append(restore_previous_release_dir(check, section, backup.resolve(), snapshot))
                claude(['plugin', 'marketplace', 'update', MARKETPLACE], config_dir)
                # `plugin update` only moves forward; reinstall gives back exactly what the marketplace holds now.
                claude(['plugin', 'uninstall', PLUGIN_ID, '--keep-data'], config_dir)
                claude(['plugin', 'install', PLUGIN_ID, '--scope', old.get('scope') or 'user'], config_dir)
                if old.get('enabled') is False:
                    claude(['plugin', 'disable', PLUGIN_ID], config_dir)
                done.append('previous_plugin_reinstalled')
        if 'marketplace_added' in actions:
            claude(['plugin', 'marketplace', 'remove', MARKETPLACE], config_dir)
            done.append('marketplace_removed')
        after = claude_state(config_dir)
        if 'previous_plugin_reinstalled' in done:
            now = next((p for p in after['plugins'] if p.get('id') == PLUGIN_ID), None)
            cache = verify_dir(Path(now['installPath']), before['package_snapshot']) if now else {'status': 'FAIL'}
            restored = (not pending and now is not None and now.get('version') == old.get('version')
                        and now.get('enabled') == old.get('enabled') and cache['status'] == 'PASS')
            after['cache_check'] = cache
        else:
            restored = not pending and after['plugins'] == before.get('plugins') \
                and (after['marketplace'] is None) == (before.get('marketplace') is None)
    except (ReleaseError, subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        restored, after = False, {'error': str(exc)}
    section.update(rollback={'done': done, 'pending': pending, 'after': after, 'at': now_iso()},
                   status='已恢复' if restored else '恢复未完成')
    save(path, check)
    return {'status': 'PASS' if restored else 'FAIL', 'check': str(path), 'done': done, 'pending': pending}


def cmd_codex(args, command: str | None = None) -> dict:
    import codex_install
    path = operation_path(args)
    check = load_check(path)
    handlers = {'codex-install': codex_install.cmd_install, 'codex-verify': codex_install.cmd_verify,
                'codex-rollback': codex_install.cmd_rollback}
    return handlers[command or args.command](args, path, check, save)


# ---------- one-host release install (registration 20261016-1229) ----------

ORDER = ('Claude', 'Codex')
UNTOUCHED = ('待执行', '已恢复')
STEP_ERRORS = (ReleaseError, OSError, ValueError, KeyError, subprocess.TimeoutExpired, json.JSONDecodeError)


def run_step(step) -> dict:
    try:
        return step()
    except STEP_ERRORS as exc:
        return {'status': 'FAIL', 'error': str(exc)}


def write_log(path: Path, **fields) -> dict:
    """Sub-commands save their own copy of the record; reload before adding the run log."""
    check = load_check(path)
    check.setdefault('release_install', {}).update(fields)
    save(path, check)
    return check


def lock_owner() -> str:
    import maint_lock
    from maint_common import resolve_maint_dir
    lock = maint_lock.read_lock(resolve_maint_dir()) or {}
    return f"{lock.get('host', '未知宿主')} {lock.get('model', '未知模型')}"


def rollback_both(args, path: Path) -> dict:
    """Return every side this run touched to its state before the run, Codex first, then Claude.

    Both touched sides first leave "已安装并核对", so neither single-host guard blocks restoring the
    shared release directory; a side whose rollback fails ends as "恢复未完成", never as verified.
    """
    check = load_check(path)
    touched = [h for h in ORDER if check['hosts'][h].get('status') not in UNTOUCHED]
    for host in touched:
        check['hosts'][host]['status'] = '待回退'
    save(path, check)
    results = {}
    for host in reversed(touched):
        results[host] = run_step(lambda: cmd_codex(args, 'codex-rollback') if host == 'Codex' else cmd_claude_rollback(args))
        check = load_check(path)
        if results[host]['status'] != 'PASS' and check['hosts'][host].get('status') == '待回退':
            check['hosts'][host]['status'] = '恢复未完成'
            save(path, check)
    ok = all(r['status'] == 'PASS' for r in results.values())
    return {'status': 'PASS' if ok else 'FAIL', 'touched': touched,
            'hosts': {h: {'status': r['status'], 'detail': r} for h, r in results.items()}}


def cmd_release_install(args) -> dict:
    """Install and verify both hosts from one session: Claude, then Codex; both or neither."""
    import codex_install
    path = operation_path(args)
    check = load_check(path)
    started = {h: check['hosts'][h].get('status') for h in ORDER if check['hosts'][h].get('status') not in UNTOUCHED}
    if started:
        raise ReleaseError(f'该批次已开始：{started}；用 release-verify 或 release-rollback，不覆盖最初恢复依据')
    codex_install.require_owner()  # the Codex half needs the lock; fail before anything changes
    write_log(path, executed_by=lock_owner(), order=list(ORDER), started_at=now_iso(), steps=[], status='安装中',
              rollback=None)
    steps = []
    for host in ORDER:
        result = run_step(lambda: cmd_claude_install(args) if host == 'Claude' else cmd_codex(args, 'codex-install'))
        steps.append({'host': host, 'command': f'{host.lower()}-install', 'status': result['status'],
                      'error': result.get('error') or result.get('verification', {}).get('reason')})
        write_log(path, steps=steps)
        if result['status'] != 'PASS':
            break
    if all(s['status'] == 'PASS' for s in steps) and len(steps) == len(ORDER):
        check = write_log(path, status='两边已安装并核对', finished_at=now_iso())
        return {'status': 'PASS', 'check': str(path), 'overall': check['overall'], 'steps': steps}
    rollback = rollback_both(args, path)
    check = write_log(path, status='发布未完成：已恢复' if rollback['status'] == 'PASS' else '发布未完成：恢复未完成',
                      rollback=rollback, finished_at=now_iso())
    return {'status': 'FAIL', 'check': str(path), 'overall': check['overall'], 'steps': steps, 'rollback': rollback}


def cmd_release_verify(args) -> dict:
    import codex_install
    codex_install.require_owner()  # before the Claude side rewrites its part of the check record
    results = {'Claude': run_step(lambda: cmd_claude_verify(args)),
               'Codex': run_step(lambda: cmd_codex(args, 'codex-verify'))}
    ok = all(r['status'] == 'PASS' for r in results.values())
    return {'status': 'PASS' if ok else 'FAIL', 'check': str(operation_path(args)), 'overall': load_check(operation_path(args))['overall'],
            'hosts': {h: r['status'] for h, r in results.items()}, 'detail': results}


def cmd_release_rollback(args) -> dict:
    import codex_install
    codex_install.require_owner()  # before any host is marked 待回退 or Claude is rolled back
    path = operation_path(args)
    rollback = rollback_both(args, path)
    check = write_log(path, status='已整批回退' if rollback['status'] == 'PASS' else '整批回退：恢复未完成',
                      rollback=rollback, finished_at=now_iso())
    return {'status': rollback['status'], 'check': str(path), 'overall': check['overall'], 'rollback': rollback}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='cad-qto 安装核对')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'release-install', 'release-verify', 'release-rollback', 'claude-install', 'claude-verify',
                 'claude-rollback', 'codex-install', 'codex-verify', 'codex-rollback'):
        p = sub.add_parser(name)
        p.add_argument('--record', required=True, type=Path, help='发布记录 JSON')
        p.add_argument('--check-file', type=Path, help='隔离演练用独立安装核对记录，避免覆盖正式状态')
        both = name.startswith('release-')
        if both or name.startswith('claude-'):
            p.add_argument('--claude-config-dir', help='隔离演练用 CLAUDE_CONFIG_DIR；正式安装不填')
        if both or name.startswith('codex-'):
            p.add_argument('--codex-home', type=Path, help='隔离演练用 CODEX_HOME；正式安装不填')
            p.add_argument('--codex-marketplace', help='Codex 市场名，默认 personal')
        if name in ('release-install', 'claude-install', 'codex-install'):
            p.add_argument('--release-dir', required=True, type=Path)
        if name in ('release-install', 'codex-install'):
            p.add_argument('--codex-marketplace-root', type=Path, help='不存在的市场由 CLI 从此根目录注册')
        if name in ('release-install', 'release-rollback', 'claude-rollback', 'codex-install'):
            p.add_argument('--rollback-release-dir', type=Path,
                           help='发布工具保留的上一版发布包：Codex 免另存一套，两边升级失败时据此换回上一版')
    args = parser.parse_args(argv)
    handlers = {'init': cmd_init, 'release-install': cmd_release_install, 'release-verify': cmd_release_verify,
                'release-rollback': cmd_release_rollback, 'claude-install': cmd_claude_install,
                'claude-verify': cmd_claude_verify, 'claude-rollback': cmd_claude_rollback}
    try:
        result = handlers.get(args.command, cmd_codex)(args)
    except (ReleaseError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result = {'status': 'FAIL', 'error': str(exc)}
    print(dump_json(result), end='')
    return {'PASS': 0, CODEX_PENDING: 3}.get(result['status'], 1)


if __name__ == '__main__':
    sys.exit(main())
