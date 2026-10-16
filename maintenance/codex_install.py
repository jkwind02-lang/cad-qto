"""Codex-side install/verify/rollback, run by whichever host publishes (registration 20261016-1229).
Uses the native CLI and fresh skills/list, no model turn.

Never edits a plugin cache. A rollback re-installs a verified prior package through
the same marketplace. Only this plugin's config table is restored, never config.toml.
"""
from __future__ import annotations

import json
import os
import platform
import queue
import re
import shutil
import subprocess
import threading
import time
import tomllib
from pathlib import Path

from maint_common import REPO_ROOT, now_iso, read_json, resolve_maint_dir, sha256_file, write_json_atomic
from release_package import ReleaseError, verify_dir

PLUGIN = 'cad-qto'


def require_owner():
    import maint_lock
    try:
        maint_lock.refresh(resolve_maint_dir(), os.environ.get('CAD_QTO_LOCK_TOKEN'))
    except (LookupError, PermissionError) as exc:
        raise ReleaseError(f'Codex 安装操作需要本会话维护锁：{exc}') from exc


def cli_prefix():
    exe = shutil.which('codex')
    if not exe:
        raise ReleaseError('找不到 codex 命令')
    if Path(exe).suffix.lower() in ('.cmd', '.bat', '.ps1'):
        package = Path(exe).parent / 'node_modules/@openai/codex'
        arch = 'arm64' if platform.machine().lower() in ('arm64', 'aarch64') else 'x64'
        triple = 'aarch64-pc-windows-msvc' if arch == 'arm64' else 'x86_64-pc-windows-msvc'
        native = package / f'node_modules/@openai/codex-win32-{arch}/vendor/{triple}/bin/codex.exe'
        if native.is_file():
            return [str(native)]
        script, node = package / 'bin/codex.js', shutil.which('node')
        if script.is_file() and node:
            return [node, str(script)]
        raise ReleaseError('无法安全解析 Codex npm 启动器；不通过拼接 shell 命令运行')
    return [exe]


def codex_env(home):
    return dict(os.environ, CODEX_HOME=str(home))


QUERY_TIMEOUT = 60  # read-only listings; 0.162.1 answers a market-scoped list in under a second


def codex(arguments, home, timeout=120, step=None):
    try:
        result = subprocess.run([*cli_prefix(), *arguments], env=codex_env(home),
                                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                encoding='utf-8', errors='replace', timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise ReleaseError(f"{step or 'Codex CLI 调用'}超时（{timeout} 秒）：codex {' '.join(arguments)}") from exc
    if result.returncode:
        raise ReleaseError(f"codex {' '.join(arguments[:3])} 失败：{(result.stderr or result.stdout)[:1200]}")
    try:
        return json.loads(result.stdout)
    except ValueError as exc:
        raise ReleaseError('Codex CLI 未返回合法 JSON') from exc


def context(args, section):
    home = Path(args.codex_home or section.get('codex_home') or os.environ.get('CODEX_HOME')
                or Path.home() / '.codex').resolve()
    market = args.codex_marketplace or section.get('marketplace') or 'personal'
    if not re.fullmatch(r'[a-zA-Z0-9_-]+', market):
        raise ReleaseError('Codex 市场名称不合法')
    if section.get('codex_home') and home != Path(section['codex_home']).resolve():
        raise ReleaseError('安装核对记录属于另一 CODEX_HOME；隔离演练须用独立 --check-file')
    if section.get('marketplace') and market != section['marketplace']:
        raise ReleaseError('安装核对记录属于另一 Codex 市场')
    return home, market, f'{PLUGIN}@{market}'


def state(home, market):
    # An unscoped `plugin list` also queries every remote marketplace and timed out (registration 20261016-0005).
    # A scoped list of an unregistered market returns empty lists, so first install still reaches `marketplace add`.
    listing = codex(['plugin', 'list', '--marketplace', market, '--json'], home, QUERY_TIMEOUT,
                    f'安装状态查询（插件列表，限 {market} 市场）')
    markets = codex(['plugin', 'marketplace', 'list', '--json'], home, QUERY_TIMEOUT,
                    '安装状态查询（市场列表；CLI 不支持按名称筛选，为全量）').get('marketplaces', [])
    installed = [p for p in listing.get('installed', []) if p.get('pluginId') == f'{PLUGIN}@{market}']
    roots = [m for m in markets if m.get('name') == market]
    if len(installed) > 1 or len(roots) > 1:
        raise ReleaseError('Codex 同名安装项或市场不唯一，拒绝猜测来源')
    return {'plugins': installed, 'marketplace': roots[0] if roots else None}


def plugin_block(home, plugin_id):
    path = home / 'config.toml'
    text = path.read_text(encoding='utf-8-sig') if path.exists() else ''
    header = f'[plugins.{json.dumps(plugin_id)}]'
    pattern = re.compile(r'^' + re.escape(header) + r'[^\n]*\n(?:(?!^\[).*(?:\n|$))*', re.M)
    found = pattern.search(text)
    return found.group() if found else None


def restore_plugin_block(home, plugin_id, block):
    """Restore only the exact plugin table; retain every other current config value."""
    path = home / 'config.toml'
    raw = path.read_bytes() if path.exists() else b''
    text = raw.decode('utf-8-sig')
    header = f'[plugins.{json.dumps(plugin_id)}]'
    pattern = re.compile(r'^' + re.escape(header) + r'[^\n]*\n(?:(?!^\[).*(?:\n|$))*', re.M)
    replacement = block or ''
    updated, count = pattern.subn(lambda _: replacement, text, count=1)
    if not count and block:
        updated = text.rstrip() + '\n\n' + block
    before, after = tomllib.loads(text), tomllib.loads(updated)
    for data in (before, after):
        data.get('plugins', {}).pop(plugin_id, None)
        if data.get('plugins') == {}:
            data.pop('plugins')
    if before != after:
        raise ReleaseError('恢复会影响其他配置，拒绝写入')
    if path.exists() and path.read_bytes() != raw:
        raise ReleaseError('配置在恢复期间被更新，未覆盖')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(updated, encoding='utf-8', newline='\n')


def cache_path(home, market, item):
    version = item.get('version')
    if not isinstance(version, str) or not version or any(c in version for c in '/\\:'):
        raise ReleaseError('Codex 安装项缺少安全的版本标识')
    path = (home / 'plugins/cache' / market / PLUGIN / version).resolve()
    if not path.is_relative_to((home / 'plugins/cache').resolve()) or not path.is_dir():
        raise ReleaseError(f'Codex 实际缓存不存在或越界：{path}')
    return path


def file_snapshot(directory):
    return {'files': [{'path': p.relative_to(directory).as_posix(), 'sha256': sha256_file(p)}
                      for p in sorted(directory.rglob('*')) if p.is_file()
                      and '__pycache__' not in p.parts and p.suffix not in ('.pyc', '.pyo')]}


def expected_entries(record):
    return {Path(row['path']).parts[1] for row in record['files']
            if row['path'].startswith('skills/') and row['path'].endswith('/SKILL.md')
            and len(Path(row['path']).parts) == 3}


def fresh_skills(home, plugin_id, cache, expected, enabled=True):
    """Start one local app-server; only initialize and skills/list, never thread/turn."""
    proc = subprocess.Popen([*cli_prefix(), 'app-server', '--listen', 'stdio://'], env=codex_env(home),
                            cwd=REPO_ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True, encoding='utf-8', errors='replace',
                            creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    messages = queue.Queue()
    def read_stdout():
        for line in proc.stdout:
            try:
                messages.put(json.loads(line))
            except ValueError:
                pass
    threading.Thread(target=read_stdout, daemon=True).start()
    threading.Thread(target=lambda: list(proc.stderr), daemon=True).start()
    def request(identifier, method, params):
        proc.stdin.write(json.dumps({'id': identifier, 'jsonrpc': '2.0', 'method': method, 'params': params}) + '\n')
        proc.stdin.flush()
        deadline = time.monotonic() + 45
        while time.monotonic() < deadline:
            try:
                result = messages.get(timeout=min(1, max(.01, deadline-time.monotonic())))
            except queue.Empty:
                continue
            if result.get('id') == identifier:
                if 'error' in result:
                    raise ReleaseError(f'Codex skills/list 失败：{result["error"]}')
                return result['result']
        raise ReleaseError('Codex 新加载会话发现入口超时')
    try:
        request(1, 'initialize', {'clientInfo': {'name': 'cad-qto-install-check', 'version': '1.0'},
                                  'capabilities': {'experimentalApi': True}})
        proc.stdin.write('{"jsonrpc":"2.0","method":"initialized","params":{}}\n');proc.stdin.flush()
        data = request(2, 'skills/list', {'cwds': [str(REPO_ROOT)], 'forceReload': True})
        selected, errors = [], []
        for group in data.get('data', []):
            selected.extend(s for s in group.get('skills', []) if s.get('pluginId') == plugin_id)
            errors.extend(e for e in group.get('errors', []) if 'cad-qto' in str(e.get('path', '')))
        names = {s['name'].split(':')[-1] for s in selected}
        if enabled:
            passed = names == set(expected) and len(selected) == len(expected) and all(
                s.get('enabled') is True and Path(s['path']).resolve().is_relative_to(cache) for s in selected)
        else:
            passed = not any(s.get('enabled') for s in selected)
        return {'status': 'PASS' if passed and not errors else 'FAIL', 'entry_count': len(selected),
                'names': sorted(names), 'errors': errors, 'method': 'fresh app-server skills/list; no model turn'}
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.terminate();proc.wait(timeout=8)


def verify_installed(check, home, market, record=None, enabled=True):
    record = record or read_json(Path(check['release_record']))
    current = state(home, market)
    if len(current['plugins']) != 1:
        return {'status': 'FAIL', 'reason': '未找到唯一 Codex 安装项', 'state': current}
    item = current['plugins'][0]
    cache = cache_path(home, market, item)
    report = verify_dir(cache, record, ['**/__pycache__/*.pyc'])
    loaded = fresh_skills(home, item['pluginId'], cache, expected_entries(record), enabled)
    source = read_json(cache / '.codex-plugin/plugin.json')
    version_ok = source['version'] == item['version']
    if 'business_version' in record:
        version_ok = version_ok and source['version'].split('+')[0] == record['business_version']
    ok = report['status'] == loaded['status'] == 'PASS' and version_ok and item.get('enabled') is enabled
    return {'status': 'PASS' if ok else 'FAIL', 'installed_version': source['version'], 'enabled': item.get('enabled'),
            'install_path': str(cache), 'cache_check': report, 'fresh_load': loaded,
            'readme_present': (cache/'README.md').exists(), 'docs_present': (cache/'docs').exists()}


def checked_record(args, check):
    if sha256_file(args.record) != check['release_record_sha256']:
        raise ReleaseError('冻结发布记录的哈希已变化')
    record = read_json(args.record)
    if record['source_commit'] != check['source_commit'] or record['business_version'] != check['business_version']:
        raise ReleaseError('发布记录与安装核对记录身份不一致')
    if record.get('untested_rehearsal'):
        raise ReleaseError('不安装未受测演练包')
    return record


def cmd_install(args, path, check, save):
    require_owner()
    record = checked_record(args, check)
    section = check['hosts']['Codex'];home, market, plugin_id = context(args, section)
    release = args.release_dir.resolve()
    if verify_dir(release, record)['status'] != 'PASS':
        raise ReleaseError('安装前发布包文件核对未通过')
    if section.get('before') and section.get('status') not in ('已恢复',):
        raise ReleaseError('该安装批次已经开始；请 verify 或 rollback，不覆盖最初恢复依据')
    before = state(home, market)
    root = Path(before['marketplace']['root']) if before['marketplace'] else args.codex_marketplace_root
    if root is None:
        raise ReleaseError('Codex 市场未注册，须显式给出 --codex-marketplace-root')
    catalog = read_json(root / '.agents/plugins/marketplace.json')
    choices = [p for p in catalog.get('plugins', []) if p.get('name') == PLUGIN]
    if catalog.get('name') != market or len(choices) != 1:
        raise ReleaseError('Codex 市场名称或插件条目与目标不一致')
    selected_source = choices[0].get('source', {})
    if selected_source.get('source') != 'local' or (root / selected_source.get('path', '')).resolve() != release:
        raise ReleaseError('Codex 市场实际来源与本次发布目录不一致，安装前拒绝')
    backup = None
    if before['plugins']:
        item = before['plugins'][0];cache = cache_path(home, market, item)
        before['package_snapshot'] = file_snapshot(cache)
        if args.rollback_release_dir:
            backup = args.rollback_release_dir.resolve()
            if verify_dir(backup, before['package_snapshot'], ['**/__pycache__/*.pyc'])['status'] != 'PASS':
                raise ReleaseError('指定上一版回滚包与原安装字节不一致')
        else:
            backup = path.parent / (path.stem + '_Codex回滚包')
            if backup.exists():
                raise ReleaseError('回滚包目录已存在，拒绝覆盖')
            shutil.copytree(cache, backup, ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        before['rollback_package'] = str(backup)
    before['plugin_config_block'] = plugin_block(home, plugin_id)
    section.update(status='安装中', before=before, codex_home=str(home), marketplace=market,
                   release_dir=str(release), started_at=now_iso(), actions=[])
    save(path, check)
    try:
        if before['marketplace'] is None:
            root = args.codex_marketplace_root
            if not root:
                raise ReleaseError('Codex 市场未注册，须显式给出 --codex-marketplace-root')
            section['actions'].append('marketplace_add_started');save(path, check)
            codex(['plugin', 'marketplace', 'add', str(root.resolve()), '--json'], home)
            section['actions'].append('marketplace_added');save(path, check)
        section['actions'].append('plugin_add_started');save(path, check)
        selected = codex(['plugin', 'add', plugin_id, '--json'], home)
        section['actions'].append('plugin_updated' if before['plugins'] else 'plugin_installed');save(path, check)
        # CLI add can succeed while selecting another same-named source; verify actual bytes and paths.
        if selected.get('pluginId') != plugin_id:
            raise ReleaseError('Codex 实际安装标识与目标不一致')
        result = verify_installed(check, home, market, record)
    except (ReleaseError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        result = {'status': 'FAIL', 'reason': str(exc)}
    section.update(status='已安装并核对' if result['status'] == 'PASS' else '安装或核对失败',
                   verification=result, finished_at=now_iso())
    save(path, check)
    return {'status': result['status'], 'check': str(path), 'verification': result}


def cmd_verify(args, path, check, save):
    require_owner();record = checked_record(args, check)
    section = check['hosts']['Codex'];home, market, _ = context(args, section)
    result = verify_installed(check, home, market, record)
    section.update(status='已安装并核对' if result['status'] == 'PASS' else '安装或核对失败',
                   verification=result, verified_at=now_iso(), codex_home=str(home), marketplace=market)
    save(path, check)
    return {'status': result['status'], 'check': str(path), 'verification': result}


def restore_release(section, check, record):
    backup = Path(section['before']['rollback_package']).resolve()
    snapshot = section['before']['package_snapshot']
    if verify_dir(backup, snapshot, ['**/__pycache__/*.pyc'])['status'] != 'PASS':
        raise ReleaseError('回滚包已改变，拒绝恢复')
    current = Path(section['release_dir']).resolve()
    old_source = Path(section['before']['plugins'][0]['source']['path']).resolve()
    if current != old_source:
        if verify_dir(old_source, snapshot, ['**/__pycache__/*.pyc'])['status'] != 'PASS':
            raise ReleaseError('原来源不同且未恢复为上一版；先恢复共同发布来源再重试')
        return
    if verify_dir(current, snapshot, ['**/__pycache__/*.pyc'])['status'] == 'PASS':
        return
    # A one-host release rollback (install_check release-rollback) moves Claude off this state first.
    if check['hosts']['Claude'].get('status') == '已安装并核对':
        raise ReleaseError('Claude 已使用本次共同发布来源；须先协调共同发布回退（release-rollback），不能单侧改共同来源')
    section['rollback_displaced_package'] = swap_in_previous_release(current, backup, snapshot, record, 'Codex')


def swap_in_previous_release(current, backup, snapshot, record, label):
    """Put the verified previous package back into the shared release directory; return the displaced path.

    Shared by the Codex rollback and the Claude upgrade rollback of a one-host release install.
    """
    if verify_dir(current, record)['status'] != 'PASS':
        raise ReleaseError('当前发布目录出现非本批次变化，拒绝覆盖')
    if current == current.parent or current.is_symlink() or getattr(current, 'is_junction', lambda: False)():
        raise ReleaseError('拒绝恢复根目录或目录联接')
    stage = current.with_name(current.name + f'_{label}恢复准备')
    displaced = current.with_name(current.name + f'_{label}回退前发布包')
    if stage.exists() or displaced.exists():
        raise ReleaseError('恢复临时目录已存在，未覆盖；请核对前次恢复状态')
    # Both exact absolute destinations stay in the known release parent; no recursive move across shells.
    if stage.parent.resolve() != current.parent or displaced.parent.resolve() != current.parent:
        raise ReleaseError('恢复路径越界')
    shutil.copytree(backup, stage)
    if verify_dir(stage, snapshot)['status'] != 'PASS':
        raise ReleaseError('恢复准备包核对失败')
    current.rename(displaced)
    try:
        stage.rename(current)
    except OSError:
        displaced.rename(current)
        raise
    return str(displaced)


def cmd_rollback(args, path, check, save):
    require_owner();record = checked_record(args, check)
    section = check['hosts']['Codex'];home, market, plugin_id = context(args, section)
    before = section.get('before')
    if before is None:
        raise ReleaseError('缺少安装前状态，拒绝猜测回滚')
    done = []
    try:
        if before['plugins']:
            restore_release(section, check, record)
            codex(['plugin', 'add', plugin_id, '--json'], home);done.append('previous_plugin_reinstalled')
        elif ('plugin_installed' in section.get('actions', []) or
              ('plugin_add_started' in section.get('actions', []) and state(home, market)['plugins'])):
            codex(['plugin', 'remove', plugin_id, '--json'], home);done.append('new_plugin_removed')
        if ('marketplace_added' in section.get('actions', []) or
            (before.get('marketplace') is None and 'marketplace_add_started' in section.get('actions', [])
             and state(home, market)['marketplace'] is not None)):
            codex(['plugin', 'marketplace', 'remove', market, '--json'], home);done.append('new_marketplace_removed')
        restore_plugin_block(home, plugin_id, before['plugin_config_block'])
        after = state(home, market)
        if before['plugins']:
            old = before['plugins'][0]
            result = verify_installed(check, home, market, before['package_snapshot'], old.get('enabled', True))
            passed = result['status'] == 'PASS' and result['installed_version'] == old['version']
        else:
            result = {'status': 'PASS' if not after['plugins'] else 'FAIL', 'state': after}
            passed = result['status'] == 'PASS' and (after['marketplace'] is None) == (before['marketplace'] is None)
    except (ReleaseError, OSError, ValueError, subprocess.TimeoutExpired) as exc:
        passed = False;result = {'status': 'FAIL', 'reason': str(exc)}
    section.update(status='已恢复' if passed else '恢复未完成',
                   rollback={'at': now_iso(), 'done': done, 'verification': result})
    save(path, check)
    return {'status': 'PASS' if passed else 'FAIL', 'check': str(path), 'rollback': section['rollback']}
