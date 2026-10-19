"""Actual duct consumption reconciliation; source binding is shared with point T20."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"_qto-shared-evidence/scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]/"qto-components-points/scripts"))
from point_host_attachment import digest, frozen_file, nonempty, request_digest, validate_pipe_request

def duct_producer():
    return Path(__file__).resolve().with_name("infer_duct_fittings_vertical.py")

def duct_dependencies():
    paths=[duct_producer(),Path(__file__).resolve(),Path(__file__).with_name("build_duct_network_from_neutral.py").resolve(),Path(__file__).with_name("duct_generated_hosts.py").resolve(),Path(__file__).with_name("duct_network_extensions.py").resolve(),
           Path(__file__).resolve().parents[2]/"qto-components-points/scripts/point_host_attachment.py"]
    return [{"path":str(p),"sha256":digest(p)} for p in paths]


def validate_duct_request(report, data, network_base, report_base):
    if data.get("schema_version") != "qto-duct-fitting-vertical-input/1.0":
        raise ValueError("风管实际接收必须使用风管输入契约")
    combined={**data,"segments":data.get("segments",[])+data.get("drawn_curve_segments",[])}
    requested=validate_pipe_request(report, combined, network_base, report_base, allow_generated_duct_hosts=True)
    for row,raw,host,evidence_ref in requested:
        if host.get("generated_duct_host"):
            actual=next(s for s in combined["segments"] if s["segment_id"]==host["segment_id"])
            if actual.get("geometry")!=host.get("geometry"):
                raise ValueError("生成/曲线宿主的完整几何与实际风管输入不同")
        if raw.get("valve_type") == row["instance_identity"].get("category"):
            continue
        value,_=frozen_file(raw.get("component_classification_evidence"),network_base,"component_classification_evidence")
        identity=row["instance_identity"]
        expected={"schema_version":"qto-inline-component-classification/1.0","source_sha256":report["source_sha256"],
                  "physical_id":row["physical_id"],"source_instance_path":identity["instance_path"],
                  "source_record_id":identity["source_record_id"],"component_type":raw.get("valve_type"),
                  "layout":report["layout"],"space_id":report["space_id"]}
        if any(value.get(k)!=v for k,v in expected.items()) or not nonempty(value.get("basis")) or not nonempty(value.get("source_locator")):
            raise ValueError("风管附件类别与原点位识别不同，且缺少对应源实例的分类依据")
    return requested

def verify_duct_consumption(receipt, base, *, replay=True, current_outlets=None):
    """Recheck actual producer output and its quantity rows. A profile cannot assert receipt."""
    if receipt.get("professional_owner") != "qto-duct-air-systems":
        raise ValueError("风管接收专业不符")
    if receipt.get("producer_dependencies") != duct_dependencies():
        raise ValueError("风管消费者或数量接收依赖已变化，须重跑实际消费")
    inp, inp_ref = frozen_file(receipt.get("network_input"), base, "network_input")
    result, result_ref = frozen_file(receipt.get("network_result"), base, "network_result")
    request, _ = frozen_file(receipt.get("attachment_request"), base, "attachment_request")
    if receipt.get("workpackage_id") != inp.get("package_id"):
        raise ValueError("风管接收workpackage_id与实际输入包号不一致")
    validated_request = validate_duct_request(request, inp, Path(inp_ref["path"]).parent,
                                             Path(receipt["attachment_request"]["path"]).parent)
    original_profile, _ = frozen_file(request.get("profile_ref"), Path(receipt["attachment_request"]["path"]).parent, "attachment_request.profile_ref")
    if original_profile.get("point_host_attachment", {}).get("receptions"):
        raise ValueError("接收凭据必须从未引用接收结果的原始请求生成，禁止循环依赖")
    for key in ("source_sha256", "coordinate_system", "coordinate_unit", "layout", "space_id"):
        if inp.get(key) != receipt.get(key) or request.get(key) != receipt.get(key):
            raise ValueError("实际风管消费输入、原始请求及接收结果的来源/单位/范围不一致")
    if inp.get("unit") != receipt.get("coordinate_unit"):
        raise ValueError("实际风管消费单位与接收坐标单位不一致")
    producer = duct_producer()
    if receipt.get("producer_sha256") != digest(producer):
        raise ValueError("接收结果生产者版本已变化，必须重跑实际风管消费")
    if replay:
        with tempfile.TemporaryDirectory(prefix="point-reception-") as directory:
            output = Path(directory) / "replay.json"
            try:
                run = subprocess.run([sys.executable, "-B", str(producer), "--input", inp_ref["path"], "--output", str(output)],
                                     stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", timeout=180)
            except subprocess.TimeoutExpired as exc:
                raise ValueError("实际风管消费重放超时，数量接收未核验") from exc
            if run.returncode != 0 or not output.is_file():
                raise ValueError("接收结果实际风管消费重放失败:" + run.stderr[-500:])
            if json.loads(output.read_text(encoding="utf-8-sig")) != result:
                raise ValueError("接收结果与相同版本实际风管消费输出不一致")
    valves = result.get("valves")
    if not isinstance(valves, list) or result.get("metrics", {}).get("valve_count") != len(valves):
        raise ValueError("风管实际valves明细和数量不一致")
    if sum(r.get("instance_count", 0) for r in result.get("valve_summary", [])) != len(valves):
        raise ValueError("风管实际阀件汇总与明细不守恒")
    request_rows = request.get("rows")
    items = receipt.get("quantity_items")
    raw_valves = inp.get("valves")
    if not isinstance(request_rows, list) or not isinstance(items, list) or not isinstance(raw_valves, list):
        raise ValueError("接收对账缺少请求、数量行或实际输入全集")
    def unique(values, key, label):
        result = {}
        for row in values:
            if not isinstance(row, dict) or not nonempty(row.get(key)) or row[key] in result:
                raise ValueError(f"{label}.{key}必须非空且全集唯一")
            result[row[key]] = row
        return result
    request_by_pid = unique(request_rows, "physical_id", "原始请求")
    items_by_pid = unique(items, "physical_id", "接收数量行")
    unique(items, "network_valve_id", "接收数量行")
    indices = [row.get("network_valve_index") for row in items]
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in indices) or len(set(indices)) != len(indices):
        raise ValueError("接收数量行的实际valves行定位重复")
    raw_by_id = unique(raw_valves, "valve_id", "真实风管输入")
    actual_by_id = unique(valves, "valve_id", "真实风管结果")
    raw_by_pid = unique(raw_valves, "source_object_id", "真实风管输入")
    actual_by_pid = unique(valves, "source_object_id", "真实风管结果")
    if set(raw_by_id) != set(actual_by_id) or set(raw_by_pid) != set(actual_by_pid):
        raise ValueError("真实风管输入与结果全集物理身份/阀件身份不守恒")
    for valve_id, raw in raw_by_id.items():
        if actual_by_id[valve_id]["source_object_id"] != raw["source_object_id"]:
            raise ValueError("真实风管输入与结果物理ID对应关系不一致")
    original_outlets = {}
    for row in original_profile.get("point_host_attachment", {}).get("outlets", []):
        original_outlets.setdefault(row.get("physical_id"), []).append(row.get("quantity_outlet"))
    for pid, bound in request_by_pid.items():
        if original_outlets.get(pid) != [bound.get("quantity_outlet")]:
            raise ValueError("原始请求与冻结profile的物理对象数量出口不一致")
    for pid in actual_by_pid:
        if pid in request_by_pid and request_by_pid[pid].get("quantity_outlet") != "inline_network":
            raise ValueError("真实风管valves全集含本报告独立设备或未定出口，禁止漏报造成双计")
        if current_outlets is not None and pid in current_outlets:
            declared = current_outlets[pid]
            if len(declared) != 1 or declared[0].get("quantity_outlet") != "inline_network":
                raise ValueError("真实风管valves全集命中本轮独立设备或多出口，禁止跨请求漏报双计")
    inside = set(actual_by_pid) & set(request_by_pid)
    if set(items_by_pid) != inside:
        raise ValueError("本报告inline实际明细与接收数量行必须逐对象一一守恒，禁止漏报或夹入")
    outside = set(actual_by_pid) - set(request_by_pid)
    coverage = receipt.get("quantity_scope")
    if coverage is None and outside:
        raise ValueError("真实风管结果含报告外对象，必须明确声明报告覆盖及旁路范围")
    if coverage is not None:
        expected_scope = {"request_physical_ids": set(request_by_pid), "received_inline_physical_ids": set(items_by_pid),
                          "outside_request_physical_ids": outside}
        if not isinstance(coverage, dict):
            raise ValueError("quantity_scope必须为对象")
        for key, expected in expected_scope.items():
            values = coverage.get(key)
            if not isinstance(values, list) or any(not nonempty(v) for v in values) or len(values) != len(set(values)) or set(values) != expected:
                raise ValueError(f"quantity_scope.{key}未与实际全集及报告范围逐对象核对")
    input_segments = {row.get("segment_id"): row for row in inp.get("segments", [])+inp.get("drawn_curve_segments",[])}
    rows = []
    for item in items:
        index = item.get("network_valve_index")
        if isinstance(index, bool) or not isinstance(index, int) or index < 0 or index >= len(valves):
            raise ValueError("风管数量接收行缺少实际valves原始行定位")
        actual = valves[index]
        if item.get("network_valve_id") != actual.get("valve_id"):
            raise ValueError("接收数量行阀件ID与实际原始行定位不一致")
        raw = raw_by_id.get(actual.get("valve_id"), {})
        matches = [row for row in request.get("rows", []) if row.get("physical_id") == item.get("physical_id")]
        if len(matches) != 1 or matches[0].get("request_sha256") != item.get("request_sha256") or request_digest(matches[0]) != item.get("request_sha256"):
            raise ValueError("接收行与冻结的原始挂接请求不一致")
        bound = matches[0]
        if bound.get("quantity_outlet") != "inline_network" or bound.get("host_status") != "unique_spatial_candidate" or len(bound.get("candidate_segments", [])) != 1:
            raise ValueError("原始挂接请求不是唯一可核inline候选")
        host = bound["candidate_segments"][0]
        source_segment = input_segments.get(item.get("segment_id"), {})
        segment_expected = {"source_sha256": receipt["source_sha256"], "source_instance_path": host["instance_path"],
                            "source_record_id": host["source_record_id"], "start": host["start"], "end": host["end"],
                            "system": host["system"], "space_id": receipt["space_id"]}
        if any(source_segment.get(k) != v for k, v in segment_expected.items()) or raw.get("point") != bound["placement"]["geometry_point"]:
            raise ValueError("实际风管输入源管段或点位坐标不匹配完整路径几何")
        evidence, _ = frozen_file(raw.get("connection_evidence"), Path(inp_ref["path"]).parent, "connection_evidence")
        if (evidence.get("source_sha256") != receipt["source_sha256"] or evidence.get("physical_id") != item["physical_id"]
                or evidence.get("segment_id") != item["segment_id"] or evidence.get("evidence_id") != raw.get("connection_source_id")
                or evidence.get("relationship") != "inline_connection" or evidence.get("layout") != receipt["layout"]
                or evidence.get("space_id") != receipt["space_id"] or not evidence.get("basis") or not evidence.get("source_locator")):
            raise ValueError("接收行显式在线连接依据与原始对象和范围不一致")
        if (actual.get("source_object_id") != item.get("physical_id")
                or actual.get("connection_method") != "evidence_bound_connection"
                or actual.get("attached_segment_ids") != [item.get("segment_id")]
                or actual.get("system") != item.get("system") or actual.get("dn") != item.get("spec")
                or actual.get("valve_type") in (None, "", "未分类")
                or raw.get("source_sha256") != item.get("source_sha256")
                or raw.get("source_instance_path") != item.get("source_instance_path")
                or raw.get("source_record_id") != item.get("source_record_id")
                or raw.get("quantity_unit") != item.get("unit")
                or raw.get("attachment_request_sha256") != item.get("request_sha256")):
            raise ValueError("风管接收行未与实际消费输入和结果逐对象匹配")
        identity = bound["instance_identity"]
        expected_item = {"source_sha256": request["source_sha256"], "source_instance_path": identity["instance_path"],
                         "source_record_id": identity["source_record_id"], "quantity_type": "inline_accessory", "unit": identity["unit"],
                         "quantity": 1, "status": "received", "layout": request["layout"], "space_id": request["space_id"]}
        levels = {"方案暂估量": 0, "规则推算量": 1, "确认量": 2}
        if (any(item.get(k) != v for k, v in expected_item.items()) or isinstance(item.get("quantity"), bool)
                or item.get("quantity_level") not in levels
                or levels[item["quantity_level"]] > levels.get(identity.get("quantity_level"), -1)):
            raise ValueError("接收数量类型/离散单位/层级与原始实例不一致")
        rows.append(item)
    return rows

