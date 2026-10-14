"""Shared helpers for cad-qto maintenance tools (lock, release, install checks).

Maintenance tools are not runtime files and are never packaged.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
MAINT_DIR_ENV = 'CAD_QTO_MAINT_DIR'
MAINT_DIR_NAME = '插件维护'


def resolve_maint_dir(explicit: str | None = None, must_exist: bool = True) -> Path:
    """--maint-dir > CAD_QTO_MAINT_DIR > <repo>/../../插件维护 (the documented local layout)."""
    if explicit:
        path = Path(explicit)
    elif os.environ.get(MAINT_DIR_ENV):
        path = Path(os.environ[MAINT_DIR_ENV])
    else:
        path = REPO_ROOT.parent.parent / MAINT_DIR_NAME
    path = path.resolve()
    if must_exist and not path.is_dir():
        raise FileNotFoundError(f'插件维护目录不存在：{path}（可用 --maint-dir 或环境变量 {MAINT_DIR_ENV} 指定）')
    return path


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')


def parse_iso(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError(f'时间缺少时区：{value}')
    return parsed


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def git(*args: str, cwd: Path | None = None, check: bool = True, input_bytes: bytes | None = None) -> bytes:
    result = subprocess.run(
        ['git', *args], cwd=cwd or REPO_ROOT, input=input_bytes,
        stdin=None if input_bytes is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败：{result.stderr.decode('utf-8', 'replace').strip()}")
    return result.stdout


def read_json(path: Path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def dump_json(data) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2) + '\n'


def write_json_atomic(path: Path, data, retries: int = 10) -> None:
    """Write via a sibling temp file and os.replace; retry briefly when a reader holds the target on Windows."""
    path = Path(path)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    tmp.write_text(dump_json(data), encoding='utf-8', newline='\n')
    for attempt in range(retries):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == retries - 1:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.2)


def create_exclusive(path: Path, text: str) -> None:
    """Create a new file atomically; raises FileExistsError only when the file already exists."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, 'O_BINARY', 0), 0o644)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(text.encode('utf-8'))


def append_jsonl(path: Path, record) -> None:
    with open(path, 'a', encoding='utf-8', newline='\n') as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + '\n')


def detect_host_session() -> tuple[str | None, str | None]:
    """Session identity actually available in this process; never assume a variable exists."""
    if os.environ.get('CLAUDE_CODE_SESSION_ID'):
        return 'Claude', os.environ['CLAUDE_CODE_SESSION_ID']
    if os.environ.get('CODEX_THREAD_ID'):
        return 'Codex', os.environ['CODEX_THREAD_ID']
    return None, None
