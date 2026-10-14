#!/usr/bin/env python3
"""Count every runtime-readable QTO Markdown file under one Skill root."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import defaultdict
from pathlib import Path


VERSION = "qto-runtime-text-audit/1.0.1"
TEXT_EXTENSIONS = {".md", ".json", ".yaml", ".yml", ".csv", ".txt"}


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--baseline", type=int, default=182726)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    files = []
    totals = defaultdict(int)
    skill_dirs = sorted(path for path in args.root.glob("qto-*") if path.is_dir())
    for skill_dir in skill_dirs:
        skill_md = skill_dir / "SKILL.md"
        if skill_md.exists():
            text = skill_md.read_text(encoding="utf-8-sig")
            files.append({"path": str(skill_md), "bucket": "skill_md", "characters": len(text), "sha256": sha256_file(skill_md)})
            totals["skill_md"] += len(text)
        reference_dir = skill_dir / "references"
        if reference_dir.exists():
            for path in sorted(item for item in reference_dir.rglob("*") if item.is_file() and item.suffix.lower() in TEXT_EXTENSIONS):
                text = path.read_text(encoding="utf-8-sig")
                files.append({"path": str(path), "bucket": "professional_references", "characters": len(text), "sha256": sha256_file(path)})
                totals["professional_references"] += len(text)

    shared = args.root / "_qto-shared-evidence" / "references"
    if shared.exists():
        for path in sorted(item for item in shared.rglob("*") if item.is_file() and item.suffix.lower() in TEXT_EXTENSIONS):
            text = path.read_text(encoding="utf-8-sig")
            files.append({"path": str(path), "bucket": "shared_references", "characters": len(text), "sha256": sha256_file(path)})
            totals["shared_references"] += len(text)

    total = sum(totals.values())
    report = {
        "schema_version": "qto-runtime-text-audit/1.0",
        "auditor_version": VERSION,
        "root": str(args.root),
        "baseline_characters": args.baseline,
        "current_characters": total,
        "delta_characters": total - args.baseline,
        "within_total_budget": total <= args.baseline,
        "buckets": dict(sorted(totals.items())),
        "file_count": len(files),
        "files": files,
        "scope_rule": "all qto-*/SKILL.md and all runtime-readable text files under professional/shared references, including Markdown and Schema JSON",
        "downstream_rule": "moving text to any references directory remains in the same runtime budget; only scripts/schema are outside this text count",
        "content_ratio_status": "REPORT_ONLY_UNTIL_CLASSIFICATION_RULES_ARE_FROZEN",
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
