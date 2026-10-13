"""Hash real files without mixing path and handle ctime domains."""
import hashlib
import os
import sys
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "skills/_qto-shared-evidence/scripts"))
import qto_workflow_state as workflow


def snapshot(path):
    return workflow._file_snapshot(
        {"artifacts": {"source": {"path": str(path), "kind": "source"}}}, path.parent
    )["source"]


def shifted(st, delta):
    values = {name: getattr(st, name) for name in dir(st) if name.startswith("st_")}
    values["st_ctime_ns"] += delta
    return SimpleNamespace(**values)


def test_stable_file_accepts_distinct_path_and_handle_ctime(tmp_path, monkeypatch):
    path = tmp_path / "source.dxf"
    path.write_bytes(b"unchanged drawing evidence")
    original = os.fstat
    monkeypatch.setattr(os, "fstat", lambda fd: shifted(original(fd), 123456789))
    result = snapshot(path)
    assert result["file_status"] == "present"
    assert result["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize("api", ["path", "handle"])
def test_ctime_only_change_within_either_api_is_rejected(tmp_path, monkeypatch, api):
    path = tmp_path / "source.dxf"
    path.write_bytes(b"same size and restored mtime")
    if api == "handle":
        original = os.fstat
        calls = []
        def changed(fd):
            calls.append(fd)
            return shifted(original(fd), 123456789 + (1 if len(calls) > 1 else 0))
        monkeypatch.setattr(os, "fstat", changed)
    else:
        original = Path.stat
        original_fstat = os.fstat
        reads = []
        def observed(fd):
            reads.append(fd)
            return original_fstat(fd)
        def changed(self, *args, **kwargs):
            st = original(self, *args, **kwargs)
            if self == path:
                return shifted(st, 1 if len(reads) >= 2 else 0)
            return st
        monkeypatch.setattr(os, "fstat", observed)
        monkeypatch.setattr(Path, "stat", changed)
    result = snapshot(path)
    assert result["reason"] == "artifact changed while hashing"
    assert result["sha256"] is None


@pytest.mark.parametrize("stage", ["before_open", "after_close"])
def test_real_replacement_with_same_size_and_mtime_is_rejected(tmp_path, monkeypatch, stage):
    path = tmp_path / "source.dxf"
    path.write_bytes(b"revision A")
    old = path.stat()
    replacement = tmp_path / "replacement.dxf"
    replacement.write_bytes(b"revision B")
    os.utime(replacement, ns=(old.st_atime_ns, old.st_mtime_ns))
    original = Path.open
    @contextmanager
    def changing_open(self, *args, **kwargs):
        if self == path and stage == "before_open":
            os.replace(replacement, path)
        with original(self, *args, **kwargs) as stream:
            yield stream
        if self == path and stage == "after_close":
            os.replace(replacement, path)
    monkeypatch.setattr(Path, "open", changing_open)
    result = snapshot(path)
    assert result["reason"] == "artifact changed while hashing"
    assert result["sha256"] is None


def test_real_same_size_write_during_read_is_rejected(tmp_path, monkeypatch):
    path = tmp_path / "source.dxf"
    path.write_bytes(b"A" * (1024 * 1024 + 7))
    old = path.stat()
    original = Path.open
    class ChangingStream:
        def __init__(self, stream):
            self.stream = stream
            self.changed = False
        def fileno(self):
            return self.stream.fileno()
        def read(self, count):
            data = self.stream.read(count)
            if not self.changed:
                self.changed = True
                with original(path, "r+b") as writer:
                    writer.seek(1024 * 1024)
                    writer.write(b"changed")
                os.utime(path, ns=(old.st_atime_ns, old.st_mtime_ns + 2000000000))
            return data
    @contextmanager
    def changing_open(self, *args, **kwargs):
        with original(self, *args, **kwargs) as stream:
            yield ChangingStream(stream) if self == path else stream
    monkeypatch.setattr(Path, "open", changing_open)
    result = snapshot(path)
    assert result["reason"] == "artifact changed while hashing"
    assert result["sha256"] is None
