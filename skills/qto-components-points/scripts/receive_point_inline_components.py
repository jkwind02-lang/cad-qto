#!/usr/bin/env python3
"""Run the real pipe consumer and reconcile explicitly evidenced inline instances.

Does not invent valves or connected_segment_ids from spatial candidates. Duct
receivers are deliberately not simulated by the pipe consumer.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "_qto-shared-evidence/scripts"))
from point_host_attachment import digest, pipe_producer, reject_reparse_chain, validate_pipe_request, verify_pipe_consumption


ACTUAL_NAME = "管网实际消费结果.json"
RECEIPT_NAME = "管网在线附件数量接收.json"
LOG_NAME = "实际管网消费日志.json"
OVERLAP_NAME = Path(ACTUAL_NAME).stem + "_共线重合.csv"
FIXED_OUTPUTS = (ACTUAL_NAME, RECEIPT_NAME, LOG_NAME, OVERLAP_NAME)
SUMMARY_PREFIX = Path(ACTUAL_NAME).stem + "_"
SUMMARY_SUFFIX = "_复核摘要.md"


def protected_sources(report_path, network_input, producer):
    """Freeze explicit inputs and recursively declared path/SHA source evidence."""
    records = {}
    queue = [(report_path, None), (network_input, None), (producer, None)]
    while queue:
        raw, expected = queue.pop()
        path = Path(raw).resolve(strict=True)
        key = os.path.normcase(str(path))
        actual = digest(path)
        if expected is not None and actual != str(expected).lower():
            raise ValueError(f"受保护源证据SHA已变化:{path}")
        if key in records:
            continue
        info = path.stat()
        records[key] = {"path": str(path), "sha256": actual, "identity": (info.st_dev, info.st_ino)}
        if path.suffix.lower() != ".json":
            continue
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (ValueError, UnicodeError):
            continue  # an evidence file may have a JSON-like name without being a dependency manifest
        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("path"), str) and node.get("sha256") is not None:
                    ref = Path(node["path"])
                    queue.append((ref if ref.is_absolute() else path.parent/ref, node["sha256"]))
                for child in node.values():
                    walk(child)
            elif isinstance(node, list):
                for child in node:
                    walk(child)
        walk(value)
    return records


def check_sources_unchanged(records):
    for row in records.values():
        path = Path(row["path"])
        info = path.stat()
        if (info.st_dev, info.st_ino) != row["identity"] or digest(path) != row["sha256"]:
            raise ValueError(f"计算期间受保护输入或源证据已变化，拒绝发布:{path}")


def preflight_outputs(output, records, extra_names=()):
    reject_reparse_chain(output)
    if os.path.lexists(output) and not output.is_dir():
        raise ValueError("输出目录已被其他文件占用")
    if output.is_dir() and next(output.iterdir(), None) is not None:
        raise ValueError("输出目录必须为新目录或确实为空；拒绝覆盖任何已有文件或链接")
    names = set(FIXED_OUTPUTS) | set(extra_names)
    if output.is_dir():
        names.update(p.name for p in output.iterdir() if p.name.startswith(SUMMARY_PREFIX) and p.name.endswith(SUMMARY_SUFFIX))
    for name in names:
        if Path(name).name != name:
            raise ValueError("生产者输出名称不得越出指定目录")
        path = output/name
        reject_reparse_chain(path)
        if os.path.lexists(path):
            # Includes ordinary files, directories, dangling symlinks and every hardlink alias.
            raise ValueError(f"输出已存在（含链接别名），拒绝覆盖:{path}")
        if os.path.normcase(str(path.resolve())) in records:
            raise ValueError(f"输出与受保护输入或源证据路径冲突:{path}")


def publish_new_outputs(output, payloads, records):
    # Check the entire producer/receipt/log plan together, then reserve every target exclusively before writing bytes.
    preflight_outputs(output, records, payloads)
    check_sources_unchanged(records)
    output.mkdir(parents=True, exist_ok=True)
    handles = []
    try:
        for name in payloads:
            target = output/name
            reject_reparse_chain(target)
            handles.append((target, target.open("xb")))
        check_sources_unchanged(records)
        for (target, handle), payload in zip(handles, payloads.values()):
            handle.write(payload)
            handle.flush()
    finally:
        for _, handle in handles:
            handle.close()


def consume_staged(report_path, network_input, output, stage, records):
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    data = json.loads(network_input.read_text(encoding="utf-8-sig"))
    requested = validate_pipe_request(report, data, network_input.parent, report_path.parent)
    actual_path, receipt_path = stage/ACTUAL_NAME, output/RECEIPT_NAME
    producer = pipe_producer()
    run = subprocess.run([sys.executable, "-B", str(producer), "--input", str(network_input), "--output", str(actual_path)],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=180)
    log = {"command": run.args, "returncode": run.returncode, "stdout": run.stdout, "stderr": run.stderr,
           "protected_sources": [{"path": r["path"], "sha256": r["sha256"]} for r in records.values()]}
    payloads = {LOG_NAME: (json.dumps(log, ensure_ascii=False, indent=2)+"\n").encode("utf-8")}
    if run.returncode != 0:
        publish_new_outputs(output, payloads, records)
        raise ValueError("实际管网消费失败，见实际管网消费日志.json")
    result = json.loads(actual_path.read_text(encoding="utf-8-sig"))
    items = []
    for row, raw, host, evidence_ref in requested:
        matches = [(i, v) for i, v in enumerate(result.get("valves", [])) if v.get("source_object_id") == row["physical_id"]]
        if len(matches) != 1:
            raise ValueError("真实管网输出未形成唯一阀件数量行")
        index, actual = matches[0]
        if actual.get("dn") != host["spec"] or actual.get("valve_type") in (None, "", "未分类"):
            raise ValueError("真实管网输出阀件规格或类别未形成可用分组")
        identity = row["instance_identity"]
        items.append({"physical_id": row["physical_id"], "source_sha256": report["source_sha256"],
                      "source_instance_path": identity["instance_path"], "source_record_id": identity["source_record_id"],
                      "segment_id": host["segment_id"], "system": actual["system"], "spec": actual["dn"],
                      "quantity_type": "inline_accessory", "unit": identity["unit"], "quantity": 1,
                      "quantity_level": identity["quantity_level"], "status": "received",
                      "layout": report["layout"], "space_id": report["space_id"], "request_sha256": row["request_sha256"],
                      "network_valve_index": index, "network_valve_id": actual["valve_id"], "connection_evidence": evidence_ref})
    receipt = {"schema_version": "qto-network-component-reception/1.0", "professional_owner": "qto-pipe-networks",
               "workpackage_id": data["network_id"], **{k: report[k] for k in ("source_sha256", "layout", "space_id", "coordinate_unit", "coordinate_system")},
               "producer": str(producer), "producer_sha256": digest(producer),
               "network_input": {"path": str(network_input), "sha256": digest(network_input)},
               "network_result": {"path": str(actual_path), "sha256": digest(actual_path)},
               "attachment_request": {"path": str(report_path), "sha256": digest(report_path)},
               "quantity_items": items,
               "quantity_scope": {"request_physical_ids": sorted(row["physical_id"] for row in report["rows"]),
                                  "received_inline_physical_ids": sorted(row["physical_id"] for row in items),
                                  "outside_request_physical_ids": sorted(set(v["source_object_id"] for v in result["valves"])
                                                                       - {row["physical_id"] for row in report["rows"]})},
               "business_adoption_allowed": False,
               "meaning": "实际管网消费明细对账；数量工作接收不代表人工复核、真实DWG或正式采用"}
    verify_pipe_consumption(receipt, stage, replay=False)
    receipt["network_result"]["path"] = str(output/ACTUAL_NAME)
    payloads[ACTUAL_NAME] = actual_path.read_bytes()
    overlap_request = data.get("overlap_review")
    publish_overlap = isinstance(overlap_request, dict) and overlap_request.get("enabled") is True
    for path in stage.iterdir():
        if path == actual_path:
            continue
        if not path.is_file() or not (path.name == OVERLAP_NAME or
                                     (path.name.startswith(SUMMARY_PREFIX) and path.name.endswith(SUMMARY_SUFFIX))):
            raise ValueError("实际生产者产生未声明输出，拒绝发布")
        if path.name == OVERLAP_NAME and not publish_overlap:
            continue
        payloads[path.name] = path.read_bytes()
    payloads[RECEIPT_NAME] = (json.dumps(receipt, ensure_ascii=False, indent=2)+"\n").encode("utf-8")
    log["published_artifacts"] = [{"path": str(output/name), "sha256": hashlib.sha256(raw).hexdigest()}
                                   for name, raw in payloads.items() if name != LOG_NAME]
    payloads[LOG_NAME] = (json.dumps(log, ensure_ascii=False, indent=2)+"\n").encode("utf-8")
    publish_new_outputs(output, payloads, records)
    return receipt_path


def consume(report_path, network_input, output):
    requested = Path(output)
    requested = requested if requested.is_absolute() else Path.cwd()/requested
    reject_reparse_chain(requested)  # also catches an alias component before a lexical '..'
    output = Path(os.path.abspath(requested))  # do not dereference aliases before checking them
    records = protected_sources(report_path, network_input, pipe_producer())
    preflight_outputs(output, records)
    check_sources_unchanged(records)
    with tempfile.TemporaryDirectory(prefix="point-inline-consumer-") as directory:
        return consume_staged(report_path, network_input, output, Path(directory), records)


def main():
    parser = argparse.ArgumentParser(description="实际运行管网consumer并核对inline唯一数量接收")
    parser.add_argument("--attachment-report", type=Path, required=True)
    parser.add_argument("--network-input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        path = consume(args.attachment_report.resolve(), args.network_input.resolve(), args.output_dir)
        print(json.dumps({"status": "WORKING_RECEPTION_CHECKED", "receipt": str(path)}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError, subprocess.TimeoutExpired) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
