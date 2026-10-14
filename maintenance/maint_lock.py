#!/usr/bin/env python3
"""cad-qto maintenance lock: atomic acquire, heartbeat, status, release and user-confirmed takeover.

The lock is <插件维护>/维护中.json. Only a successful O_CREAT|O_EXCL create acquires it.
A heartbeat helper may be bound to a verified session process; it stops (without deleting
the lock) as soon as that process is gone or the lock no longer carries its owner token.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maint_common import (  # noqa: E402
    append_jsonl, create_exclusive, detect_host_session, dump_json, now_iso, parse_iso,
    resolve_maint_dir, write_json_atomic,
)

LOCK_NAME = '维护中.json'
HISTORY_NAME = '维护锁历史.jsonl'
SCHEMA = 'cad-qto-maintenance-lock/1.0'
HEARTBEAT_SECONDS = 60
STALE_SECONDS_DAEMON = 2 * 3600
STALE_SECONDS_STEPS = 6 * 3600
MODE_DAEMON = '辅助进程心跳（绑定会话进程）'
MODE_STEPS = '按工具调用和关键步骤刷新；无可靠会话常驻进程，不启动后台保活'

EXIT_OK = 0
EXIT_HELD_BY_OTHER = 3
EXIT_NOT_OWNER = 4
EXIT_NO_LOCK = 5
EXIT_ENV_ERROR = 6


# ---------- process identity (pid + creation time, guards against pid reuse) ----------

def _win_process_created(pid: int) -> str | None:
    import ctypes
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    handle = kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return None
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)) or code.value != 259:  # STILL_ACTIVE
            return None
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel32.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
            return None
        ticks = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
        return datetime.fromtimestamp((ticks - 116444736000000000) / 10_000_000, timezone.utc).isoformat()
    finally:
        kernel32.CloseHandle(handle)


def _win_parent_map() -> dict[int, int]:
    import ctypes
    from ctypes import wintypes

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [('dwSize', wintypes.DWORD), ('cntUsage', wintypes.DWORD), ('th32ProcessID', wintypes.DWORD),
                    ('th32DefaultHeapID', ctypes.c_size_t), ('th32ModuleID', wintypes.DWORD),
                    ('cntThreads', wintypes.DWORD), ('th32ParentProcessID', wintypes.DWORD),
                    ('pcPriClassBase', ctypes.c_long), ('dwFlags', wintypes.DWORD),
                    ('szExeFile', ctypes.c_wchar * 260)]

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
    snapshot = kernel32.CreateToolhelp32Snapshot(0x2, 0)  # TH32CS_SNAPPROCESS
    entry = PROCESSENTRY32W()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
    parents: dict[int, int] = {}
    try:
        ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
        while ok:
            parents[entry.th32ProcessID] = entry.th32ParentProcessID
            ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        kernel32.CloseHandle(snapshot)
    return parents


def process_created(pid: int) -> str | None:
    """Creation time of a live process, or None when it is not running."""
    if os.name == 'nt':
        return _win_process_created(pid)
    stat = Path(f'/proc/{pid}/stat')
    if not stat.exists():
        return None
    return 'proc-start-ticks:' + stat.read_text().rsplit(')', 1)[1].split()[19]


def is_ancestor(pid: int) -> bool:
    if os.name == 'nt':
        parents = _win_parent_map()
    else:
        parents = {}
        for entry in Path('/proc').iterdir():
            if entry.name.isdigit():
                try:
                    parents[int(entry.name)] = int((entry / 'stat').read_text().rsplit(')', 1)[1].split()[1])
                except (OSError, ValueError, IndexError):
                    continue
    current, seen = os.getpid(), set()
    while current and current not in seen:
        if current == pid:
            return True
        seen.add(current)
        current = parents.get(current, 0)
    return False


# ---------- lock file ----------

def lock_path(maint_dir: Path) -> Path:
    return maint_dir / LOCK_NAME


def read_lock(maint_dir: Path) -> dict | None:
    path = lock_path(maint_dir)
    for _ in range(10):
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return None
        except (PermissionError, json.JSONDecodeError):
            time.sleep(0.2)  # writer mid-replace on Windows
    raise RuntimeError(f'锁文件无法读取或不是合法 JSON：{path}')


def rewrite_existing(path: Path, data: dict) -> None:
    """Update the lock in place without O_CREAT, so a heartbeat can never resurrect a released lock."""
    payload = dump_json(data).encode('utf-8')
    fd = os.open(path, os.O_RDWR | getattr(os, 'O_BINARY', 0))  # FileNotFoundError once released
    try:
        os.ftruncate(fd, 0)
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, payload)
    finally:
        os.close(fd)


def remove_lock(path: Path) -> None:
    for attempt in range(25):
        try:
            path.unlink()
            return
        except PermissionError:  # a heartbeat holds the file open for a few milliseconds on Windows
            if attempt == 24:
                raise
            time.sleep(0.2)


def owns(lock: dict, token: str | None) -> bool:
    if token:
        return lock.get('owner_token') == token
    _, session = detect_host_session()
    return bool(session) and lock.get('session_id') == session


def lock_age_seconds(lock: dict) -> float:
    return (datetime.now(timezone.utc) - parse_iso(lock['heartbeat_at'])).total_seconds()


def describe(lock: dict, token: str | None) -> dict:
    age = lock_age_seconds(lock)
    stale_after = lock.get('stale_after_seconds', STALE_SECONDS_DAEMON)
    bound = lock.get('bound_process') or {}
    bound_alive = None
    if bound.get('pid'):
        bound_alive = process_created(int(bound['pid'])) == bound.get('created_at')
    return {
        'held': True,
        'owned_by_current': owns(lock, token),
        'host': lock.get('host'), 'model': lock.get('model'), 'session_id': lock.get('session_id'),
        'scope': lock.get('scope'), 'started_at': lock.get('started_at'), 'heartbeat_at': lock.get('heartbeat_at'),
        'heartbeat_age_seconds': round(age),
        'suspected_abnormal': age > stale_after,
        'bound_process_alive': bound_alive,
        'note': '连续无心跳只标疑似异常，不自动判定会话结束；接管须经用户确认。' if age > stale_after else '',
    }


def refresh(maint_dir: Path, token: str | None) -> dict:
    lock = read_lock(maint_dir)
    if lock is None:
        raise LookupError('当前没有维护锁')
    if not owns(lock, token):
        raise PermissionError('维护锁不属于当前会话')
    lock['heartbeat_at'] = now_iso()
    rewrite_existing(lock_path(maint_dir), lock)
    return lock


# ---------- commands ----------

def cmd_acquire(args, maint_dir: Path) -> int:
    host, session = detect_host_session()
    host = args.host or host
    session = args.session_id or session
    generated = False
    if not session:
        session, generated = f'generated-{uuid.uuid4()}', True
    if not host:
        print('无法判断宿主，请用 --host 指定 Claude 或 Codex', file=sys.stderr)
        return EXIT_ENV_ERROR
    bound = None
    if args.bind_pid:
        pid = int(os.environ.get('CLAUDE_PID', '0')) if args.bind_pid == 'auto' else int(args.bind_pid)
        created = process_created(pid) if pid else None
        if not pid or not created:
            print(f'绑定进程不存在：{args.bind_pid}', file=sys.stderr)
            return EXIT_ENV_ERROR
        if not is_ancestor(pid):
            print(f'进程 {pid} 不是当前命令的祖先进程，不能证明属于本会话', file=sys.stderr)
            return EXIT_ENV_ERROR
        bound = {'pid': pid, 'created_at': created, 'verified': 'ancestor-of-acquire-command'}
    now = now_iso()
    lock = {
        'schema': SCHEMA,
        'host': host, 'model': args.model, 'hostname': socket.gethostname(),
        'session_id': session, 'session_id_generated': generated,
        'owner_token': str(uuid.uuid4()),
        'started_at': now, 'heartbeat_at': now, 'scope': args.scope,
        'heartbeat_mode': MODE_DAEMON if bound else MODE_STEPS,
        'stale_after_seconds': STALE_SECONDS_DAEMON if bound else STALE_SECONDS_STEPS,
        'bound_process': bound,
    }
    try:
        create_exclusive(lock_path(maint_dir), dump_json(lock))
    except FileExistsError:
        current = read_lock(maint_dir)
        print(json.dumps({'status': 'HELD', **(describe(current, None) if current else {})}, ensure_ascii=False, indent=2))
        return EXIT_HELD_BY_OTHER
    print(json.dumps({'status': 'ACQUIRED', 'lock': lock}, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_status(args, maint_dir: Path) -> int:
    lock = read_lock(maint_dir)
    if lock is None:
        print(json.dumps({'held': False}, ensure_ascii=False))
        return EXIT_NO_LOCK
    print(json.dumps(describe(lock, args.token), ensure_ascii=False, indent=2))
    return EXIT_OK if owns(lock, args.token) else EXIT_HELD_BY_OTHER


def cmd_heartbeat(args, maint_dir: Path) -> int:
    try:
        lock = refresh(maint_dir, args.token)
    except LookupError as exc:
        print(exc, file=sys.stderr)
        return EXIT_NO_LOCK
    except PermissionError as exc:
        print(exc, file=sys.stderr)
        return EXIT_NOT_OWNER
    print(json.dumps({'status': 'REFRESHED', 'heartbeat_at': lock['heartbeat_at']}, ensure_ascii=False))
    return EXIT_OK


def cmd_release(args, maint_dir: Path) -> int:
    lock = read_lock(maint_dir)
    if lock is None:
        print('当前没有维护锁', file=sys.stderr)
        return EXIT_NO_LOCK
    if not owns(lock, args.token):
        print('只有持锁会话可以释放维护锁', file=sys.stderr)
        return EXIT_NOT_OWNER
    released = now_iso()
    append_jsonl(maint_dir / HISTORY_NAME, {'event': 'released', 'released_at': released,
                                            'commit': args.commit, 'note': args.note, 'lock': lock})
    remove_lock(lock_path(maint_dir))
    print(json.dumps({'status': 'RELEASED', 'released_at': released}, ensure_ascii=False))
    return EXIT_OK


def cmd_takeover(args, maint_dir: Path) -> int:
    if not args.user_confirmed:
        print('接管必须经用户确认：用 --user-confirmed 写明用户的确认内容', file=sys.stderr)
        return EXIT_ENV_ERROR
    old = read_lock(maint_dir)
    if old is None:
        print('当前没有维护锁，直接 acquire 即可', file=sys.stderr)
        return EXIT_NO_LOCK
    helper = old.get('heartbeat_helper') or {}
    if helper.get('pid') and process_created(int(helper['pid'])) == helper.get('created_at'):
        print(f"原持锁会话的心跳辅助进程 {helper['pid']} 仍在运行，说明其绑定会话进程仍在；先核实原会话状态", file=sys.stderr)
        return EXIT_HELD_BY_OTHER
    host, session = detect_host_session()
    now = now_iso()
    new = {'schema': SCHEMA,
           'host': args.host or host, 'model': args.model, 'hostname': socket.gethostname(),
           'session_id': args.session_id or session or f'generated-{uuid.uuid4()}',
           'session_id_generated': not (args.session_id or session),
           'owner_token': str(uuid.uuid4()), 'started_at': now, 'heartbeat_at': now,
           'scope': args.scope or old.get('scope'), 'heartbeat_mode': MODE_STEPS,
           'stale_after_seconds': STALE_SECONDS_STEPS, 'bound_process': None,
           'taken_over_from': {'owner_token': old.get('owner_token'), 'session_id': old.get('session_id')}}
    append_jsonl(maint_dir / HISTORY_NAME, {'event': 'taken_over', 'at': now, 'user_confirmed': args.user_confirmed,
                                            'new_owner_token': new['owner_token'], 'lock': old})
    if read_lock(maint_dir) != old:
        print('锁在接管过程中发生变化，已停止；请重新核对', file=sys.stderr)
        return EXIT_HELD_BY_OTHER
    write_json_atomic(lock_path(maint_dir), new)
    print(json.dumps({'status': 'TAKEN_OVER', 'lock': new}, ensure_ascii=False, indent=2))
    return EXIT_OK


def cmd_start_heartbeat(args, maint_dir: Path) -> int:
    lock = read_lock(maint_dir)
    if lock is None or not owns(lock, args.token):
        print('没有本会话持有的维护锁', file=sys.stderr)
        return EXIT_NOT_OWNER
    bound = lock.get('bound_process')
    if not bound:
        print('锁未绑定会话进程，按关键步骤刷新心跳，不启动辅助进程', file=sys.stderr)
        return EXIT_ENV_ERROR
    command = [sys.executable, str(Path(__file__).resolve()), '--maint-dir', str(maint_dir), '_daemon',
               '--token', lock['owner_token'], '--interval', str(args.interval)]
    kwargs = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL, 'close_fds': True}
    if os.name == 'nt':
        # Hidden, detached, outside the caller's console and job where allowed.
        kwargs['creationflags'] = 0x00000008 | 0x00000200 | 0x01000000  # DETACHED | NEW_GROUP | BREAKAWAY_FROM_JOB
    else:
        kwargs['start_new_session'] = True
    try:
        child = subprocess.Popen(command, **kwargs)
    except OSError:
        if os.name != 'nt':
            raise
        kwargs['creationflags'] = 0x00000008 | 0x00000200
        child = subprocess.Popen(command, **kwargs)
    print(json.dumps({'status': 'HELPER_STARTED', 'helper_pid': child.pid}, ensure_ascii=False))
    return EXIT_OK


def cmd_daemon(args, maint_dir: Path) -> int:
    history = maint_dir / HISTORY_NAME
    helper = {'pid': os.getpid(), 'created_at': process_created(os.getpid()), 'started_at': now_iso(),
              'interval_seconds': args.interval}
    while True:
        lock = read_lock(maint_dir)
        if lock is None or lock.get('owner_token') != args.token:
            append_jsonl(history, {'event': 'heartbeat_helper_exit', 'at': now_iso(), 'reason': '锁已释放或不再属于本持有者',
                                   'owner_token': args.token})
            return EXIT_OK
        bound = lock.get('bound_process') or {}
        if process_created(int(bound.get('pid', 0))) != bound.get('created_at'):
            append_jsonl(history, {'event': 'heartbeat_helper_exit', 'at': now_iso(),
                                   'reason': '绑定的会话进程已结束；停止心跳，不删除锁', 'owner_token': args.token,
                                   'bound_process': bound})
            return EXIT_OK
        lock['heartbeat_at'] = now_iso()
        lock['heartbeat_helper'] = helper  # written by the helper itself, so no read-modify-write race with the starter
        latest = read_lock(maint_dir)
        if latest is None or latest.get('owner_token') != args.token:
            continue  # released or taken over just now; the next loop records the exit
        try:
            rewrite_existing(lock_path(maint_dir), lock)
        except FileNotFoundError:
            continue  # released between read and write; the next loop records the exit
        time.sleep(args.interval)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='cad-qto 维护锁')
    parser.add_argument('--maint-dir')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('acquire')
    p.add_argument('--host', choices=['Claude', 'Codex'])
    p.add_argument('--model', required=True)
    p.add_argument('--scope', required=True)
    p.add_argument('--session-id')
    p.add_argument('--bind-pid', help="'auto' 使用经祖先关系核实的 CLAUDE_PID，或显式进程号")
    for name in ('status', 'heartbeat'):
        p = sub.add_parser(name)
        p.add_argument('--token')
    p = sub.add_parser('release')
    p.add_argument('--token')
    p.add_argument('--commit')
    p.add_argument('--note')
    p = sub.add_parser('takeover')
    p.add_argument('--user-confirmed')
    p.add_argument('--host', choices=['Claude', 'Codex'])
    p.add_argument('--model', required=True)
    p.add_argument('--scope')
    p.add_argument('--session-id')
    p = sub.add_parser('start-heartbeat')
    p.add_argument('--token')
    p.add_argument('--interval', type=int, default=HEARTBEAT_SECONDS)
    p = sub.add_parser('_daemon')
    p.add_argument('--token', required=True)
    p.add_argument('--interval', type=int, default=HEARTBEAT_SECONDS)
    args = parser.parse_args(argv)
    try:
        maint_dir = resolve_maint_dir(args.maint_dir)
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        return EXIT_ENV_ERROR
    handlers = {'acquire': cmd_acquire, 'status': cmd_status, 'heartbeat': cmd_heartbeat, 'release': cmd_release,
                'takeover': cmd_takeover, 'start-heartbeat': cmd_start_heartbeat, '_daemon': cmd_daemon}
    return handlers[args.command](args, maint_dir)


if __name__ == '__main__':
    sys.exit(main())
