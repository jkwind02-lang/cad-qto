#!/usr/bin/env python3
"""Build a reconciled endpoint-port handoff from point instances to a network skill."""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence" / "scripts"))
from model_recognition_basis import validate_working_basis


class InputError(ValueError):
    pass


def fail(message: str) -> None:
    raise InputError(message)


def text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        fail(f"{label} 必须是非空文本")
    return value.strip()


def point(value: Any, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) not in (2, 3):
        fail(f"{label} 必须是二维或三维坐标")
    values: list[float] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)) or not math.isfinite(item):
            fail(f"{label} 必须是有限数值坐标")
        values.append(float(item))
    return values


def normalized_ports(raw: dict[str, Any], physical_id: str) -> list[dict[str, Any]]:
    ports = raw.get("network_ports")
    if ports is None:
        return [raw]
    if not isinstance(ports, list) or not ports:
        fail(f"{physical_id}.network_ports 必须是非空数组")
    result = []
    for index, port in enumerate(ports):
        if not isinstance(port, dict):
            fail(f"{physical_id}.network_ports[{index}] 必须是对象")
        merged = dict(raw)
        merged.pop("network_ports", None)
        merged.update(port)
        result.append(merged)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="生成点位到管网的端点事实交接")
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        data = json.loads(args.input.resolve().read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            fail("输入 JSON 顶层必须是对象")
        validate_working_basis(data.get("recognition_basis"))
        source_complete = data.get("source_workpackage_complete", True)
        if not isinstance(source_complete, bool):
            fail("source_workpackage_complete 必须是布尔值")
        unmatched_ids = data.get("source_unmatched_recognition_object_ids", [])
        if not isinstance(unmatched_ids, list) or any(not isinstance(item, str) or not item.strip() for item in unmatched_ids):
            fail("source_unmatched_recognition_object_ids 必须是非空文本数组")
        source_counts = {}
        for key in ("source_unresolved_instance_count", "source_block_expansion_failure_count",
                    "source_pending_duplicate_instance_count", "source_unresolved_grouping_count"):
            value = data.get(key, 0)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                fail(f"{key} 必须是非负整数")
            source_counts[key] = value
        if source_complete and (unmatched_ids or any(source_counts.values())):
            fail("来源有未承接识别对象、未决实例、块展开失败、待处置重复或未合并子块时，source_workpackage_complete 不得为 true")
        if data.get("schema_version") not in {
            "qto-network-endpoint-handoff-input/1.0",
            "qto-network-endpoint-handoff-input/1.1",
        }:
            fail("schema_version 必须为 qto-network-endpoint-handoff-input/1.0 或 1.1")
        handoff_id = text(data.get("handoff_id"), "handoff_id")
        source_id = text(data.get("source_id"), "source_id")
        consumer_skill = text(data.get("consumer_skill"), "consumer_skill")
        if consumer_skill not in ("qto-pipe-networks", "qto-duct-air-systems", "qto-electrical-routes"):
            fail("consumer_skill 不是受支持的网络 Skill")
        raw_instances = data.get("instances")
        if not isinstance(raw_instances, list):
            fail("instances 必须是数组")

        physical_ids: set[str] = set()
        endpoint_ids: set[str] = set()
        terminals: list[dict[str, Any]] = []
        dispositions: list[dict[str, Any]] = []
        not_applicable_count = 0
        unresolved_count = 0
        port_count = 0
        for index, raw in enumerate(raw_instances):
            if not isinstance(raw, dict):
                fail(f"instances[{index}] 必须是对象")
            physical_id = text(raw.get("physical_id"), f"instances[{index}].physical_id")
            if physical_id in physical_ids:
                fail(f"physical_id 重复: {physical_id}")
            physical_ids.add(physical_id)
            category = text(raw.get("category"), f"{physical_id}.category")
            base_system = text(raw.get("system"), f"{physical_id}.system")
            evidence = text(raw.get("evidence"), f"{physical_id}.evidence")

            for port_index, port in enumerate(normalized_ports(raw, physical_id), 1):
                port_count += 1
                system = text(port.get("system", base_system), f"{physical_id}.port[{port_index}].system")
                disposition = port.get("network_disposition", "unresolved")
                if disposition not in ("deliver", "not_applicable", "unresolved"):
                    fail(f"{physical_id}.port[{port_index}].network_disposition 无效")
                port_id = text(
                    port.get("port_id", f"{physical_id}-PORT-{port_index}"),
                    f"{physical_id}.port[{port_index}].port_id",
                )

                if disposition == "deliver":
                    endpoint_id = text(port.get("endpoint_id"), f"{physical_id}.{port_id}.endpoint_id")
                    if endpoint_id in endpoint_ids:
                        fail(f"endpoint_id 重复: {endpoint_id}")
                    endpoint_ids.add(endpoint_id)
                    terminal = {
                        "terminal_id": endpoint_id,
                        "physical_id": physical_id,
                        "port_id": port_id,
                        "system": system,
                        "point": point(port.get("point", raw.get("point")), f"{physical_id}.{port_id}.point"),
                        "terminal_type": text(port.get("terminal_type", category), f"{physical_id}.{port_id}.terminal_type"),
                        "evidence_level": text(
                            port.get("evidence_level"), f"{physical_id}.{port_id}.evidence_level"
                        ),
                        "source_id": evidence,
                        "port_role": text(port.get("port_role"), f"{physical_id}.{port_id}.port_role"),
                        "propagation_scope": text(
                            port.get("propagation_scope"), f"{physical_id}.{port_id}.propagation_scope"
                        ),
                    }
                    if terminal["evidence_level"] not in ("confirmed", "inferred", "scenario"):
                        fail(f"{physical_id}.{port_id}.evidence_level 必须是 confirmed、inferred 或 scenario")
                    for optional in ("port_dn", "assumption_id", "port_source_id", "port_evidence_level", "space_id", "segment_id", "source_segment_id", "coordinate_unit"):
                        value = port.get(optional, raw.get(optional))
                        if value is not None:
                            terminal[optional] = value
                    terminals.append(terminal)
                    reason = "已生成网络端点事实"
                else:
                    reason = text(port.get("reason"), f"{physical_id}.{port_id}.reason")
                    if disposition == "not_applicable":
                        not_applicable_count += 1
                    else:
                        unresolved_count += 1
                for key in ("port_dn", "port_role", "port_source_id"):
                    value = port.get(key, raw.get(key))
                    if value is not None:
                        text(value, f"{physical_id}.{port_id}.{key}")
                for key in ("evidence_level", "port_evidence_level"):
                    value = port.get(key, raw.get(key))
                    if value is not None and value not in ("confirmed", "inferred", "scenario"):
                        fail(f"{physical_id}.{port_id}.{key} 无效")
                known_point = port.get("point", raw.get("point"))
                if known_point is not None:
                    point(known_point, f"{physical_id}.{port_id}.point")
                dispositions.append(
                    {
                        "physical_id": physical_id,
                        "port_id": port_id,
                        "category": category,
                        "system": system,
                        "network_disposition": disposition,
                        "reason": reason,
                        "evidence": evidence,
                        # A missing location does not erase known size or role.
                        "known_facts": {
                            key: port.get(key, raw.get(key))
                            for key in ("port_dn", "port_role", "port_source_id", "port_evidence_level",
                                        "evidence_level", "propagation_scope", "space_id", "coordinate_unit",
                                        "point", "source_segment_id", "assumption_id")
                            if port.get(key, raw.get(key)) is not None
                        },
                    }
                )

        output = {
            "schema_version": "qto-network-endpoint-handoff/1.1",
            "handoff_id": handoff_id,
            "provider_skill": "qto-components-points",
            "consumer_skill": consumer_skill,
            "status": "READY" if not unresolved_count and source_complete else "REVIEW_REQUIRED",
            "recognition_basis": data.get("recognition_basis"),
            "source_workpackage_complete": source_complete,
            # 来源未决实例与端口未决分开计；未承接对象逐个保留 ID，消费方不能把它们当已处置
            "source_unresolved_instance_count": source_counts["source_unresolved_instance_count"],
            "source_block_expansion_failure_count": source_counts["source_block_expansion_failure_count"],
            "source_pending_duplicate_instance_count": source_counts["source_pending_duplicate_instance_count"],
            "source_unresolved_grouping_count": source_counts["source_unresolved_grouping_count"],
            "source_unmatched_recognition_object_ids": unmatched_ids,
            "source_gaps": [
                {"object_id": object_id, "kind": "unmatched_recognition_object",
                 "reason": "模型工作识别台账列出但本交接来源未承接（非块对象、范围外或未匹配规则）；须经独立计数与交接接入"}
                for object_id in unmatched_ids
            ],
            "required": bool(port_count),
            "reason": None if port_count else "本交接尚无可输出端口；是否已完成识别须同时检查 source_workpackage_complete，不表示端口工程量为零",
            "source_physical_instance_count": len(raw_instances),
            "source_endpoint_port_count": port_count,
            "source_instance_count": port_count,
            "delivered_count": len(terminals),
            "not_applicable_count": not_applicable_count,
            "unresolved_count": unresolved_count,
            "source_id": source_id,
            "terminals": terminals,
            "dispositions": dispositions,
            "meaning": "按端口逐项对账；同一设备可交接多个网络端口，未决端口不阻断其他端点和网络基线继续计算",
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
                    "source_physical_instance_count": len(raw_instances),
                    "source_endpoint_port_count": port_count,
                    "delivered_count": len(terminals),
                    "unresolved_count": unresolved_count,
                    "output": str(output_path),
                },
                ensure_ascii=False,
            )
        )
        return 0
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
