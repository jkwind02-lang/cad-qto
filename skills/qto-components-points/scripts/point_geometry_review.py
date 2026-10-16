"""Geometry review receipt that binds a point count input to the adapter's same-place review (registration 20261016-1044 R1).

The adapter writes the receipt into 点位唯一实例计数输入.json; count_reconciled_instances.py recomputes the instance-set
fingerprint. A hand-made instance list, or one with a single point added or moved, no longer matches, so a later batch
never inherits an earlier batch's review. Arithmetic counting itself is unchanged.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

SCHEMA = "qto-point-geometry-review/1.0"
READY_SCALES = ("bound", "explicit_output_units")   # the adapter's only statuses that ran the same-place review


def instance_set_sha256(rows: list[dict[str, Any]]) -> str:
    """physical_id plus insertion point, sorted; any added, removed or moved instance changes it."""
    items = []
    for row in rows:
        point = row.get("point")
        coords = [round(float(value), 6) for value in point] if isinstance(point, list) else None
        items.append([str(row.get("physical_id")), coords])
    items.sort(key=lambda item: item[0])
    payload = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def evaluate(rows: list[dict[str, Any]], receipt: Any) -> dict[str, Any]:
    """matched / mismatch / absent, and whether the work quantity may enter a summary."""
    if receipt is None:
        return {"status": "absent", "work_quantity_ready": False,
                "reason": "计数输入没有几何审查凭据；只是算术复核，不能当作经同位置审查的工作量"}
    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA:
        return {"status": "mismatch", "work_quantity_ready": False, "reason": f"geometry_review 不是 {SCHEMA}"}
    actual = instance_set_sha256(rows)
    problems = []
    if receipt.get("instance_set_sha256") != actual:
        problems.append("实例集指纹与凭据不符（实例增删、改动坐标，或凭据来自另一批）")
    if receipt.get("instance_count") != len(rows):
        problems.append(f"实例数 {len(rows)} 与凭据 {receipt.get('instance_count')} 不符")
    if problems:
        return {"status": "mismatch", "work_quantity_ready": False, "reason": "；".join(problems),
                "instance_set_sha256": actual}
    blockers = []
    scale = receipt.get("scale_status")
    if scale == "unbound":
        blockers.append("同位置审查尺度未绑定")
    elif scale not in READY_SCALES:   # missing, empty or unknown: not proof that the review ran at a known scale
        blockers.append(f"凭据的尺度状态 {scale!r} 不是 {' 或 '.join(READY_SCALES)}")
    if receipt.get("pending_group_count") != 0:
        blockers.append(f"未处置重复候选组 {receipt.get('pending_group_count')}")
    if receipt.get("workpackage_complete") is not True:
        blockers.append("点位工作包不完整")
    return {"status": "matched", "work_quantity_ready": not blockers,
            "reason": "；".join(blockers) if blockers else "凭据相符，未处置组为 0，工作包完整",
            "instance_set_sha256": actual}
