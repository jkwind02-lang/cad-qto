#!/usr/bin/env python3
"""Per-version install check records for both hosts, with the Claude install implemented.

The record lives next to the release record (<版本>_<提交>_安装核对.json). Each host writes only its
own section. Codex install, verification and rollback are Codex's (plan C1): those commands here
refuse with "待 Codex 接续" and never report success. A version counts as released on both hosts
only when both sections are verified.

Claude steps use the claude CLI; pass --claude-config-dir to rehearse in an isolated
CLAUDE_CONFIG_DIR without touching the real installation.
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
    path = check_path(args.record)
    if path.exists():
        return {'status': 'PASS', 'check': str(path), 'note': '已存在，未改写'}
    check = {'schema': SCHEMA, 'business_version': record['business_version'], 'source_commit': record['source_commit'],
             'release_record': str(args.record.resolve()), 'release_record_sha256': sha256_file(args.record),
             'hosts': {'Claude': {'status': '待执行'},
                       'Codex': {'status': CODEX_PENDING, 'note': 'Codex 的安装、缓存核对与恢复由 Codex 执行并回写（方案 C1）'}},
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
    path = check_path(args.record)
    check = load_check(path)
    section = check['hosts']['Claude']
    release_dir = args.release_dir.resolve()
    source = read_json(release_dir / '发布来源.json')
    if source['source_commit'] != check['source_commit']:
        raise ReleaseError('发布目录的来源提交与安装核对记录不一致')
    before = claude_state(args.claude_config_dir)
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
    path = check_path(args.record)
    check = load_check(path)
    result = claude_verify_installed(check, args.claude_config_dir)
    section = check['hosts']['Claude']
    section.update(verification=result, verified_at=now_iso(),
                   status='已安装并核对' if result['status'] == 'PASS' else '安装或核对失败')
    save(path, check)
    return {'status': result['status'], 'check': str(path), 'verification': result}


def cmd_claude_rollback(args) -> dict:
    """Undo only what this install did for Claude: a new install is removed, an added marketplace is removed.

    An upgrade is rolled back by first restoring the previous release directory (release_package publish of
    the kept rollback package) and then running claude-install again; this command reports that need.
    """
    path = check_path(args.record)
    check = load_check(path)
    section = check['hosts']['Claude']
    actions = section.get('actions', [])
    done, pending = [], []
    try:
        if 'plugin_installed' in actions:
            claude(['plugin', 'uninstall', PLUGIN_ID, '--keep-data'], args.claude_config_dir)
            done.append('plugin_uninstalled')
        if 'plugin_updated' in actions:
            pending.append('先用回滚包恢复上一版发布目录，再运行 claude-install 重装上一版')
        if 'marketplace_added' in actions:
            claude(['plugin', 'marketplace', 'remove', MARKETPLACE], args.claude_config_dir)
            done.append('marketplace_removed')
        after = claude_state(args.claude_config_dir)
        restored = not pending and after['plugins'] == section.get('before', {}).get('plugins') \
            and (after['marketplace'] is None) == (section.get('before', {}).get('marketplace') is None)
    except ReleaseError as exc:
        restored, after = False, {'error': str(exc)}
    section.update(rollback={'done': done, 'pending': pending, 'after': after, 'at': now_iso()},
                   status='已恢复' if restored else '恢复未完成')
    save(path, check)
    return {'status': 'PASS' if restored else 'FAIL', 'check': str(path), 'done': done, 'pending': pending}


def cmd_codex(args) -> dict:
    return {'status': CODEX_PENDING, 'reason': 'Codex 安装、核对与恢复接口由 Codex 接入（方案 C1）；此处不执行、不判成功'}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='cad-qto 安装核对')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('init', 'claude-install', 'claude-verify', 'claude-rollback', 'codex-install', 'codex-verify', 'codex-rollback'):
        p = sub.add_parser(name)
        p.add_argument('--record', required=True, type=Path, help='发布记录 JSON')
        if name.startswith('claude-'):
            p.add_argument('--claude-config-dir', help='隔离演练用 CLAUDE_CONFIG_DIR；正式安装不填')
        if name == 'claude-install':
            p.add_argument('--release-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    handlers = {'init': cmd_init, 'claude-install': cmd_claude_install, 'claude-verify': cmd_claude_verify,
                'claude-rollback': cmd_claude_rollback}
    try:
        result = handlers.get(args.command, cmd_codex)(args)
    except (ReleaseError, FileNotFoundError, json.JSONDecodeError) as exc:
        result = {'status': 'FAIL', 'error': str(exc)}
    print(dump_json(result), end='')
    return {'PASS': 0, CODEX_PENDING: 3}.get(result['status'], 1)


if __name__ == '__main__':
    sys.exit(main())
