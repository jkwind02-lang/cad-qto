#!/usr/bin/env python3
"""Validate the shared neutral-evidence resource without making business decisions."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
from pathlib import Path


REQUIRED_REFERENCES = (
    "公共资源调用与停止条件.md",
    "图纸与资料范围筛选.md",
    "CAD中性事实提取.md",
    "PDF图片与表格提取.md",
    "项目文档条款提取.md",
    "缓存身份与复用条件.md",
    "证据引用要求.md",
    "通用成果字段与状态.md",
    "推算假定与发布分层协议.md",
    "工程量成果最小字段.schema.json",
)

REQUIRED_SCRIPTS = (
    "validate_shared_evidence.py",
    "validate_minimum_result.py",
)

PLANNED_CONSUMERS = (
    "qto-coordinator",
    "qto-earthwork-terrain",
    "qto-roads-siteworks",
    "qto-piles-groundwork",
    "qto-concrete-formwork",
    "qto-rebar",
    "qto-structural-steel",
    "qto-masonry-secondary",
    "qto-surfaces-envelope",
    "qto-framing-supports",
    "qto-pipe-networks",
    "qto-duct-air-systems",
    "qto-electrical-routes",
    "qto-components-points",
)

FORBIDDEN_RUNTIME_MARKERS = (
    "construction" + "-qto-legacy-",
    "construction" + "-qto-retired-source-",
    "construction" + "-qto-preexisting-backup-",
    ".agents/" + "skills" + "-" + "disabled",
    ".agents" + "\\skills" + "-" + "disabled",
)

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def fail_closed(root: Path, missing: list[str]) -> int:
    result = {
        "status": "INPUT_REQUIRED",
        "stop": True,
        "silent_fallback": False,
        "shared_root": str(root),
        "missing_resources": missing,
        "results_produced": 0,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 2


def normalize(path: Path) -> str:
    return os.path.normcase(os.path.abspath(os.path.normpath(str(path))))


def is_reparse_point(path: Path) -> bool:
    if path.is_symlink():
        return True
    attributes = getattr(path.lstat(), "st_file_attributes", 0)
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse_flag)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "shared_root",
        nargs="?",
        type=Path,
        default=Path(__file__).resolve().parents[1],
    )
    args = parser.parse_args()

    input_root = Path(
        os.path.abspath(os.path.normpath(str(args.shared_root)))
    )
    if not input_root.is_dir():
        return fail_closed(input_root, [str(input_root)])
    root_is_reparse = is_reparse_point(input_root)
    root = input_root.resolve()

    missing: list[str] = []
    for name in REQUIRED_REFERENCES:
        path = root / "references" / name
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(f"references/{name}")
    for name in REQUIRED_SCRIPTS:
        path = root / "scripts" / name
        if not path.is_file() or path.stat().st_size == 0:
            missing.append(f"scripts/{name}")
    if missing:
        return fail_closed(root, missing)

    issues: list[str] = []
    if root_is_reparse:
        issues.append(f"公共目录根本身不得是重解析点: {input_root}")
    skill_files = sorted(root.rglob("SKILL.md"))
    if skill_files:
        issues.extend(f"公共目录不得包含 SKILL.md: {path}" for path in skill_files)

    reparse_points = [
        path
        for path in root.rglob("*")
        if is_reparse_point(path)
    ]
    if reparse_points:
        issues.extend(f"公共目录不得包含符号链接: {path}" for path in reparse_points)

    files = sorted(path for path in root.rglob("*") if path.is_file())
    generated_cache_files = [
        path
        for path in files
        if "__pycache__" in path.parts or path.suffix.lower() == ".pyc"
    ]
    if generated_cache_files:
        issues.extend(
            f"公共目录不得包含解释器生成缓存: {path}"
            for path in generated_cache_files
        )
    hashes: dict[str, str] = {}
    broken_links: list[str] = []
    runtime_markers: list[str] = []
    for path in files:
        relative = path.relative_to(root).as_posix()
        hashes[relative] = sha256(path)
        if path.suffix.lower() in {".md", ".json", ".py"}:
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            for marker in FORBIDDEN_RUNTIME_MARKERS:
                if marker.lower() in lowered:
                    runtime_markers.append(f"{relative}: {marker}")
            if path.suffix.lower() == ".md":
                for target in LINK_RE.findall(text):
                    if target.startswith(("http://", "https://", "#")):
                        continue
                    target_path = (path.parent / target.split("#", 1)[0]).resolve()
                    if os.path.commonpath((str(root), str(target_path))) != str(root):
                        broken_links.append(f"{relative} -> 路径逃逸: {target}")
                    elif not target_path.exists():
                        broken_links.append(f"{relative} -> 缺失: {target}")

    if runtime_markers:
        issues.extend(f"检测到旧归档运行时标记: {item}" for item in runtime_markers)
    if broken_links:
        issues.extend(f"本地链接问题: {item}" for item in broken_links)

    suite_root = root.parent
    root_norm = normalize(root)
    relative_resolution: dict[str, bool] = {}
    for consumer in PLANNED_CONSUMERS:
        resolved = Path(
            os.path.normpath(
                str(suite_root / consumer / ".." / "_qto-shared-evidence")
            )
        )
        relative_resolution[consumer] = normalize(resolved) == root_norm
    if not all(relative_resolution.values()):
        issues.append("至少一个计划消费者的同级相对路径解析失败")

    relocation_root = Path("D:/阶段2 路径探针/含中文与空格/新体系候选")
    relocated_shared = relocation_root / "_qto-shared-evidence"
    relocation_resolution: dict[str, bool] = {}
    for consumer in PLANNED_CONSUMERS:
        resolved = Path(
            os.path.normpath(
                str(relocation_root / consumer / ".." / "_qto-shared-evidence")
            )
        )
        relocation_resolution[consumer] = normalize(resolved) == normalize(
            relocated_shared
        )
    if not all(relocation_resolution.values()):
        issues.append("整体搬迁后的同级相对路径结构解析失败")

    try:
        json.loads(
            (root / "references" / "工程量成果最小字段.schema.json").read_text(
                encoding="utf-8"
            )
        )
    except (OSError, json.JSONDecodeError) as exc:
        issues.append(f"成果测试结构不可解析: {exc}")

    result = {
        "status": "PASS" if not issues else "FAIL",
        "stop": bool(issues),
        "shared_root": str(root),
        "independent_skill": False,
        "skill_md_count": len(skill_files),
        "reparse_point_count": len(reparse_points),
        "root_is_reparse_point": root_is_reparse,
        "generated_cache_file_count": len(generated_cache_files),
        "required_reference_count": len(REQUIRED_REFERENCES),
        "required_script_count": len(REQUIRED_SCRIPTS),
        "file_count": len(files),
        "local_link_issues": len(broken_links),
        "old_archive_runtime_markers": len(runtime_markers),
        "planned_consumer_count": len(PLANNED_CONSUMERS),
        "relative_resolution_pass": sum(relative_resolution.values()),
        "relocation_resolution_pass": sum(relocation_resolution.values()),
        "files_sha256": hashes,
        "issues": issues,
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if not issues else 1


if __name__ == "__main__":
    sys.exit(main())
