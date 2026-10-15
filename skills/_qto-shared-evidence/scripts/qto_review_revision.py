"""Bind comments to engineering-result snapshots, not only drawing files.

A changed comment target is historical evidence, never an implicit approval of
its replacement. Presentation titles and document revision labels are excluded.
"""
from __future__ import annotations

import hashlib
import json

OBJECT_FIELDS = (
    "id", "catalog_key", "professional", "scene_id", "kind", "points", "holes",
    "quantities", "attributes", "status", "source", "detail", "adoption_result",
    "adoption_reason", "adoption_note", "rule_ids", "evidence_ids",
)


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def bind_result_versions(view):
    """Recompute, never trust caller-supplied result fingerprints."""
    scenes = {s["id"]: s for s in view["scenes"]}
    bykey = {}
    for obj in view["objects"]:
        value = {key: obj.get(key) for key in OBJECT_FIELDS}
        scene = scenes.get(obj.get("scene_id"), {})
        value["coordinate_context"] = {key: scene.get(key) for key in ("source_id", "coordinate_unit", "y_axis", "transform")}
        obj["result_fingerprint"] = fingerprint(value)
        bykey[obj["catalog_key"]] = obj
    for group in view["groups"]:
        group["result_fingerprint"] = fingerprint({
            "id": group["id"], "proposal": group.get("proposal"), "reason": group.get("reason"),
            "members": sorted((k, bykey[k]["result_fingerprint"]) for k in group["member_keys"]),
        })
        for snap in group["member_snapshot"]:
            snap["result_fingerprint"] = bykey[snap["catalog_key"]]["result_fingerprint"]
    total = {
        "objects": sorted((k, obj["result_fingerprint"]) for k, obj in bykey.items()),
        "groups": sorted((g["id"], g["result_fingerprint"]) for g in view["groups"]),
        "summary": view.get("summary", []),
    }
    comparison = view.get("comparison")
    if comparison is not None:
        # Added only for 1.1 views so existing 1.0 fingerprints and saved comments stay valid.
        bind_comparison_versions(comparison, bykey)
        total["items"] = sorted((i["id"], i["result_fingerprint"]) for i in comparison["items"])
        total["issues"] = sorted((q["id"], q["result_fingerprint"]) for q in comparison["issues"])
    view["result_fingerprint"] = fingerprint(total)
    view["result_version_schema"] = "qto-review-result-version/1.0"
    return view


SEGMENT_FIELDS = (
    "source_id", "rows", "unit", "quantity", "difference", "conclusion", "third_party_corrected",
    "correction_note", "disposition_note", "redirect_to", "search_evidence", "missing_drawing_note",
    "unit_mismatch_note", "quantity_note",
)
DETAIL_FIELDS = (
    "id", "level", "quantity", "unit", "basis", "aggregation", "aggregation_reason", "member_keys",
    "member_quantity_name", "shared_member_split", "dimensions",
)
ISSUE_FIELDS = ("id", "handler", "title", "text", "impact", "impact_text", "current_practice", "item_ids")


def bind_comparison_versions(comparison, bykey):
    """Item and issue snapshots; display terms, labels and thresholds are excluded on purpose."""
    files = {s["id"]: sorted(f["sha256"].lower() for f in s["files"]) for s in comparison["sources"]}
    for item in comparison["items"]:
        segments = []
        for seg in item["third_parties"]:
            value = {key: seg.get(key) for key in SEGMENT_FIELDS}
            value["source_files"] = files.get(seg["source_id"])
            segments.append(value)
        rows = [{key: row.get(key) for key in DETAIL_FIELDS} for row in item["detail_rows"]]
        members = sorted({k for row in item["detail_rows"] for k in row.get("member_keys", [])})
        item["result_fingerprint"] = fingerprint({
            "id": item["id"], "kind": item["kind"], "name": item.get("name"), "features": item.get("features"),
            "ours": {key: item["ours"].get(key) for key in ("unit", "levels", "total", "quantity_name", "note")},
            "dimensions": item.get("dimensions", {}), "segments": segments, "detail_rows": rows,
            "members": [(k, bykey[k]["result_fingerprint"]) for k in members],
        })
    for issue in comparison["issues"]:
        issue["result_fingerprint"] = fingerprint({key: issue.get(key) for key in ISSUE_FIELDS})


def assess_record(record, view):
    """Called after structural validation; Python/JS parity has golden tests."""
    objects = {o["catalog_key"]: o for o in view["objects"]}
    groups = {g["id"]: g for g in view["groups"]}
    audit_snapshot = record.get("item_snapshot") or record.get("issue_snapshot")
    if audit_snapshot is not None:
        comparison = view.get("comparison") or {}
        if record.get("item_snapshot") is not None:
            pool, ident, label = comparison.get("items", []), audit_snapshot.get("item_id"), "对照条目"
        else:
            pool, ident, label = comparison.get("issues", []), audit_snapshot.get("issue_id"), "问题"
        current = next((x for x in pool if x["id"] == ident), None)
        if current is None:
            return "unmatched", label + "已不在当前成果中，须由所属专业重新对应"
        if audit_snapshot.get("result_fingerprint") != current["result_fingerprint"] or record.get("result_fingerprint") != current["result_fingerprint"]:
            return "stale", label + "的第三方量、本次量、结论或明细已变化；旧意见仅保留为历史参考"
        return "current", "已绑定当前成果快照；仍须所属专业复核，不自动改量"
    target = groups.get(record.get("group_id")) if record.get("group_id") else objects.get((record.get("object_snapshot") or {}).get("catalog_key"))
    if target is None:
        return "unmatched", "历史对象或新增标记须由所属专业重新对应，不自动沿用确认"
    snapshots = record.get("member_snapshot", []) if record.get("group_id") else [record.get("object_snapshot", {})]
    for snap in snapshots:
        current = objects.get(snap.get("catalog_key"))
        if current is None:
            return "unmatched", "批注成员不在当前成果中，须先核对迁移"
        if current["id"] != snap.get("object_id"):
            raise ValueError("批注成员快照与当前对象身份不一致")
        if snap.get("result_fingerprint") != current.get("result_fingerprint"):
            return "stale", "对象数量、规格、几何或采用依据已变化；旧意见仅保留为历史参考"
    if not record.get("result_fingerprint") or record["result_fingerprint"] != target.get("result_fingerprint"):
        return "stale", "意见未绑定当前成果版本，须重新确认；不得自动继承旧数字确认"
    return "current", "已绑定当前成果快照；仍须所属专业复核，不自动改量"


def classify_feedback(package, view, unmatched):
    seen = {r["annotation_key"] for r in unmatched}
    for field in ("annotations", "drafts"):
        for key, record in package.get(field, {}).items():
            state, reason = assess_record(record, view)
            record["applicability_state"] = state
            record["applicability_reason"] = reason
            if field == "annotations" and state != "current" and key not in seen:
                unmatched.append({"annotation_key": key, "reason": reason, "state": state})
                seen.add(key)
    # The package header describes the current view, not approval applicability.
    # The original per-record fingerprints are deliberately NOT rebound here.
    package["current_result_fingerprint"] = view["result_fingerprint"]
    return package, unmatched
