#!/usr/bin/env python3
"""Audit preselected CAD centerline candidates before pipe inference.

The audit never turns a suspicious curve into a confirmed pipe automatically.
It keeps normal objects available for partial calculation and exposes repeated,
disconnected curve families for an explicit include/exclude/unresolved decision.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any


CURVE_TYPES = {"ARC", "CIRCLE", "ELLIPSE", "SPLINE"}


class InputError(ValueError):
    pass


def fail(message: str) -> None:
    raise InputError(message)


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} 必须是非空文本")
    return value.strip()


def finite(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        fail(f"{label} 必须是有限数值")
    number = float(value)
    if not math.isfinite(number):
        fail(f"{label} 必须是有限数值")
    return number


def point(value: Any, label: str) -> tuple[float, ...]:
    if not isinstance(value, list) or len(value) not in (2, 3):
        fail(f"{label} 必须是二维或三维坐标")
    return tuple(finite(item, label) for item in value)


def distance(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right):
        fail("同一对象审计中的坐标维度必须一致")
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"无法读取输入 JSON: {exc}")
    if not isinstance(value, dict):
        fail("输入 JSON 顶层必须是对象")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="审计管道中心线候选中的重复断开曲线族")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()

    try:
        data = load(args.input.resolve())
        if data.get("schema_version") != "qto-pipe-object-selection-input/1.0":
            fail("schema_version 必须为 qto-pipe-object-selection-input/1.0")
        audit_id = text(data.get("audit_id"), "audit_id")
        unit = text(data.get("unit"), "unit")
        parameters = data.get("parameters", {})
        if not isinstance(parameters, dict):
            fail("parameters 必须是对象")
        connection_tolerance = finite(
            parameters.get("connection_tolerance", 0.01), "connection_tolerance"
        )
        repeat_min_count = parameters.get("repeat_min_count", 3)
        signature_precision = parameters.get("signature_precision", 6)
        if (
            connection_tolerance < 0
            or isinstance(repeat_min_count, bool)
            or not isinstance(repeat_min_count, int)
            or repeat_min_count < 2
            or isinstance(signature_precision, bool)
            or not isinstance(signature_precision, int)
            or not 0 <= signature_precision <= 12
        ):
            fail("对象审计阈值无效")

        raw_candidates = data.get("candidates")
        if not isinstance(raw_candidates, list) or not raw_candidates:
            fail("candidates 必须是非空数组")
        candidates: dict[str, dict[str, Any]] = {}
        endpoints: list[tuple[str, tuple[float, ...]]] = []
        for index, raw in enumerate(raw_candidates):
            if not isinstance(raw, dict):
                fail(f"candidates[{index}] 必须是对象")
            object_id = text(raw.get("object_id"), f"candidates[{index}].object_id")
            if object_id in candidates:
                fail(f"object_id 重复: {object_id}")
            start = point(raw.get("start"), f"{object_id}.start")
            end = point(raw.get("end"), f"{object_id}.end")
            length = finite(raw.get("length"), f"{object_id}.length")
            if length <= 0 or distance(start, end) <= 0:
                fail(f"{object_id} 的长度和端点距离必须大于0")
            source_ids = raw.get("source_ids")
            if not isinstance(source_ids, list) or not source_ids or any(
                not isinstance(value, str) or not value.strip() for value in source_ids
            ):
                fail(f"{object_id}.source_ids 必须是非空文本数组")
            entity_type = text(raw.get("entity_type"), f"{object_id}.entity_type").upper()
            record = {
                "object_id": object_id,
                "system": text(raw.get("system"), f"{object_id}.system"),
                "entity_type": entity_type,
                "measurement_shape": text(
                    raw.get("measurement_shape", "centerline"), f"{object_id}.measurement_shape"
                ),
                "start": start,
                "end": end,
                "length": length,
                "source_ids": source_ids,
                "geometry_signature": raw.get("geometry_signature"),
            }
            candidates[object_id] = record
            endpoints.extend([(object_id, start), (object_id, end)])

        connected_endpoint_count: dict[str, int] = defaultdict(int)
        for left_index, (left_id, left_point) in enumerate(endpoints):
            for right_id, right_point in endpoints[left_index + 1 :]:
                if left_id == right_id:
                    continue
                if distance(left_point, right_point) <= connection_tolerance:
                    connected_endpoint_count[left_id] += 1
                    connected_endpoint_count[right_id] += 1

        families: dict[tuple[Any, ...], list[str]] = defaultdict(list)
        for object_id, item in candidates.items():
            if item["entity_type"] not in CURVE_TYPES:
                continue
            signature = item["geometry_signature"]
            if isinstance(signature, dict) and signature:
                signature_key = json.dumps(signature, ensure_ascii=False, sort_keys=True)
            elif isinstance(signature, str) and signature.strip():
                signature_key = signature.strip()
            else:
                signature_key = (
                    round(item["length"], signature_precision),
                    round(distance(item["start"], item["end"]), signature_precision),
                )
            families[(item["entity_type"], signature_key)].append(object_id)

        suspicious_groups: list[dict[str, Any]] = []
        suspicious_ids: set[str] = set()
        for (entity_type, signature), object_ids in sorted(
            families.items(), key=lambda item: (str(item[0]), item[1])
        ):
            disconnected = [
                object_id for object_id in object_ids if connected_endpoint_count[object_id] == 0
            ]
            if len(disconnected) < repeat_min_count:
                continue
            suspicious_ids.update(disconnected)
            suspicious_groups.append(
                {
                    "group_id": f"ANOM-{len(suspicious_groups) + 1:04d}",
                    "entity_type": entity_type,
                    "geometry_signature": signature,
                    "object_ids": sorted(disconnected),
                    "object_count": len(disconnected),
                    "total_length": sum(candidates[value]["length"] for value in disconnected),
                    "reason": "固定几何曲线重复出现且两端均未接入其他候选中心线，疑似符号或端点图形",
                    "default_disposition": "unresolved",
                }
            )

        dispositions: dict[str, dict[str, Any]] = {}
        for index, raw in enumerate(data.get("dispositions", [])):
            if not isinstance(raw, dict):
                fail(f"dispositions[{index}] 必须是对象")
            object_id = text(raw.get("object_id"), f"dispositions[{index}].object_id")
            if object_id not in candidates:
                fail(f"dispositions 引用了不存在对象: {object_id}")
            if object_id in dispositions:
                fail(f"dispositions 对象重复: {object_id}")
            decision = text(raw.get("decision"), f"{object_id}.decision")
            if decision not in ("include", "exclude", "unresolved"):
                fail(f"{object_id}.decision 必须是 include、exclude 或 unresolved")
            dispositions[object_id] = {
                "decision": decision,
                "basis": text(raw.get("basis"), f"{object_id}.basis"),
                "source_id": text(raw.get("source_id"), f"{object_id}.source_id"),
            }

        included_ids: list[str] = []
        excluded_ids: list[str] = []
        unresolved_ids: list[str] = []
        decisions: list[dict[str, Any]] = []
        for object_id in sorted(candidates):
            explicit = dispositions.get(object_id)
            if explicit:
                decision = explicit["decision"]
                basis = explicit["basis"]
                source_id = explicit["source_id"]
            elif object_id in suspicious_ids:
                decision = "unresolved"
                basis = "对象审计命中重复断开曲线族，等待项目证据裁决"
                source_id = "AUTO-OBJECT-AUDIT"
            else:
                decision = "include"
                basis = "未命中重复断开曲线异常，保留为已筛选中心线候选"
                source_id = "AUTO-OBJECT-AUDIT"
            {"include": included_ids, "exclude": excluded_ids, "unresolved": unresolved_ids}[
                decision
            ].append(object_id)
            decisions.append(
                {
                    "object_id": object_id,
                    "decision": decision,
                    "basis": basis,
                    "source_id": source_id,
                    "connected_endpoint_match_count": connected_endpoint_count[object_id],
                }
            )

        source_ids = data.get("source_ids")
        if not isinstance(source_ids, list) or not source_ids:
            fail("source_ids 必须是非空数组")
        output = {
            "schema_version": "qto-pipe-object-selection-audit/1.0",
            "audit_id": audit_id,
            "unit": unit,
            "status": "READY" if not unresolved_ids else "REVIEW_REQUIRED",
            "meaning": "未决异常对象不进入确认中心线，但其他已筛选对象可继续计算",
            "candidate_count": len(candidates),
            "included_count": len(included_ids),
            "excluded_count": len(excluded_ids),
            "unresolved_count": len(unresolved_ids),
            "included_segment_ids": included_ids,
            "excluded_object_ids": excluded_ids,
            "unresolved_object_ids": unresolved_ids,
            "source_ids": source_ids,
            "parameters": {
                "connection_tolerance": connection_tolerance,
                "repeat_min_count": repeat_min_count,
                "signature_precision": signature_precision,
            },
            "anomaly_groups": suspicious_groups,
            "decisions": decisions,
        }
        output_path = args.output.resolve()
        if output_path == args.input.resolve():
            fail("输出路径不得覆盖输入")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "status": output["status"],
                    "candidate_count": len(candidates),
                    "included_count": len(included_ids),
                    "excluded_count": len(excluded_ids),
                    "unresolved_count": len(unresolved_ids),
                    "output": str(output_path),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except InputError as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
