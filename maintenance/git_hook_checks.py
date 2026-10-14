#!/usr/bin/env python3
"""Checks run by .githooks (enable once per clone: git config core.hooksPath .githooks).

pre-commit: the committing session must hold the maintenance lock (session id from the host
            environment, or CAD_QTO_LOCK_TOKEN for a person committing by hand); refreshes the
            heartbeat; runs the fast host-compatibility tests.
pre-push:   every pushed commit must have a passing full-regression record for its exact tree
            fingerprint (release_package.py run-tests). Hooks never push or deploy anything.
Hooks only assist: direct file edits are caught by the git status check at maintenance start,
the release tool's clean-tree refusal and the per-file release record.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maint_common import REPO_ROOT, resolve_maint_dir  # noqa: E402
import maint_lock  # noqa: E402
import release_package  # noqa: E402

ZERO = '0' * 40


def pre_commit() -> int:
    try:
        maint_dir = resolve_maint_dir()
    except FileNotFoundError as exc:
        print(f'[cad-qto pre-commit] {exc}', file=sys.stderr)
        return 1
    token = os.environ.get('CAD_QTO_LOCK_TOKEN')
    try:
        maint_lock.refresh(maint_dir, token)
    except LookupError:
        print('[cad-qto pre-commit] 没有维护锁：先运行 python maintenance/maint_lock.py acquire ...', file=sys.stderr)
        return 1
    except PermissionError:
        print('[cad-qto pre-commit] 维护锁属于其他会话；人工提交可设置 CAD_QTO_LOCK_TOKEN 为本次锁标识', file=sys.stderr)
        return 1
    proc = subprocess.run([sys.executable, '-m', 'pytest', 'tests/test_host_compat.py', '-q', '-p', 'no:cacheprovider'],
                          cwd=REPO_ROOT, stdin=subprocess.DEVNULL)
    if proc.returncode:
        print('[cad-qto pre-commit] 兼容检查未通过', file=sys.stderr)
    return proc.returncode


def pre_push(stdin_lines: list[str]) -> int:
    maint_dir = resolve_maint_dir()
    failed = []
    for line in stdin_lines:
        parts = line.split()
        if len(parts) != 4 or parts[1] == ZERO:  # deletions push no content
            continue
        commit = parts[1]
        fingerprint = release_package.tree_fingerprint(commit)['tree_fingerprint']
        record = release_package.find_test_record(maint_dir, fingerprint)
        if not record or record.get('status') != 'PASS':
            failed.append(f'{commit[:12]}（受测指纹 {fingerprint[:12]}）')
    if failed:
        print('[cad-qto pre-push] 以下提交没有通过的完整回归记录，先运行 python maintenance/release_package.py run-tests：\n  '
              + '\n  '.join(failed), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    hook = sys.argv[1] if len(sys.argv) > 1 else ''
    if hook == 'pre-commit':
        sys.exit(pre_commit())
    if hook == 'pre-push':
        sys.exit(pre_push(sys.stdin.read().splitlines()))
    print(f'未知钩子：{hook}', file=sys.stderr)
    sys.exit(2)
