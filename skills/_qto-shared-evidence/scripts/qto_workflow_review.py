"""One quantity index for readable results, review views and feedback work.

Geometry and engineering interpretations remain explicit model-owned inputs.
Feedback creates versioned preparation/calculation tasks; it never writes a
user's textual result over a quantity or grants formal adoption.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path

from qto_review_contract import normalize_view
from qto_review_revision import fingerprint
from qto_workflow_state import READ_ONLY_KINDS, SCOPE_NON_INPUT_FIELDS
from qto_workflow_tools import TOOLS, bindings


LEVELS = {"confirmed": "确认量", "inferred": "规则推算量", "scenario": "方案暂估量"}


def _label(row):
    return " / ".join(str(row.get(k, "")) for k in
                      ("quantity_kind", "quantity_role", "measurement_basis_id", "scenario_id", "quantity_level"))


def bind_view(document, execution, quantities, template, base_dir=None):
    if template.get("project_id") != document["project_id"]:
        raise ValueError("复核图面模板不属于当前项目")
    policy = document.get("review_binding_policy", "strict")
    if policy not in {"strict", "registered_only"}:
        raise ValueError("未知复核量项绑定策略")
    view = copy.deepcopy(template)
    template_summary = copy.deepcopy(view.get("workflow_binding", {}).get("unbound_template_summary", view.get("summary", [])))
    for source in view.get("sources", []):
        aid = source.get("artifact_id", source.get("id"))
        actual = execution["artifacts"].get(aid, {})
        if actual.get("status") != "current" or actual.get("sha256") != str(source.get("sha256", "")).lower():
            raise ValueError("复核模板来源不是当前已核验文件；先重读定位，不能只换哈希：" + str(aid))
    by_target = {r["target_id"]: r for r in quantities.get("result_index", [])}
    targets = {r["id"]: r for r in document.get("quantity_targets", [])}
    physical_objects = {r["id"]: r for r in document.get("objects", [])}
    scopes = {r["id"]: r for r in document.get("scopes", [])}
    packages = {r["id"]: r for r in document.get("work_packages", [])}
    producers = {aid: t["id"] for t in document.get("tasks", []) for aid in t.get("output_ids", [])}

    def refs(record, field):
        value = record.get(field, [])
        return value if isinstance(value, list) else []

    def actual_files(ids):
        result = {}
        for aid in sorted({value for value in ids if isinstance(value, str)}):
            actual = execution["artifacts"].get(aid, {})
            producer = producers.get(aid)
            result[aid] = {"path": actual.get("path"), "sha256": actual.get("sha256"),
                           "status": actual.get("status", "missing"),
                           "producer_fingerprint": execution["tasks"].get(producer, {}).get("fingerprint")}
        return result

    def basis(target):
        physical = physical_objects.get(target["physical_id"], {})
        observations = refs(physical, "observations")
        scope_ids = {sid for sid in (target.get("scope_id"), physical.get("scope_id")) if isinstance(sid, str)}
        return {"target_files": actual_files(refs(target, "basis_refs")),
                "physical_object": physical,
                "object_files": actual_files(refs(physical, "basis_refs")),
                "observation_indexes": actual_files(o["artifact_id"] for o in observations
                                                    if isinstance(o, dict) and isinstance(o.get("artifact_id"), str)),
                "scopes": {sid: {
                    # Adding an independent required task/inventory listing is
                    # not a revision of every previously reviewed object.
                    "declaration": {k: v for k, v in scopes.get(sid, {}).items() if k not in SCOPE_NON_INPUT_FIELDS},
                    "files": actual_files([*refs(scopes.get(sid, {}), "basis_refs"),
                                           *refs(scopes.get(sid, {}), "inventory_basis_refs")]),
                } for sid in sorted(scope_ids)}}

    mapping = {}
    for binding in document.get("review_bindings", []):
        key = binding["catalog_key"]
        if key in mapping:
            raise ValueError("复核对象绑定重复：" + key)
        mapping[key] = binding
    object_keys = {o["catalog_key"] for o in view.get("objects", [])}
    if set(mapping) - object_keys:
        raise ValueError("登记的复核对象不在当前图面模板中")
    quantity_outlets = set()
    unbound = []
    for obj in view.get("objects", []):
        binding = mapping.get(obj["catalog_key"])
        if not binding:
            if obj.get("quantities") and policy == "strict":
                raise ValueError("数量对象必须绑定真实量项，不能沿用手抄数字：" + obj["catalog_key"])
            if obj.get("attributes", {}).get("workflow_target_ids"):
                raise ValueError("已有量项绑定不能静默退回未绑定记录：" + obj["catalog_key"])
            unbound.append(obj["catalog_key"])
            continue
        ids = binding.get("target_ids", [])
        if not ids or len(set(ids)) != len(ids) or set(ids) - set(targets):
            raise ValueError("复核绑定量项缺失、重复或未知")
        evidence_only = binding.get("role") == "evidence"
        if not evidence_only and quantity_outlets & set(ids):
            raise ValueError("同一量项在复核页只能有一个数量出口；其他视图使用 role=evidence")
        if not evidence_only:
            quantity_outlets.update(ids)
        obj["quantities"] = []
        dependencies = []
        origins = []
        for tid in ids:
            target = targets[tid]
            if target["physical_id"] != obj.get("id"):
                raise ValueError("复核图形对象身份与量项实物不符，须重新对应来源对象：" + tid)
            owner = packages[target["work_package_id"]]["owner_skill"]
            if owner != obj.get("professional"):
                raise ValueError("复核数量主责专业与量项不符：" + tid)
            row = by_target.get(tid)
            if not evidence_only:
                obj["quantities"].append({"name": target.get("title", target["quantity_kind"]),
                                           "value": row["quantity"] if row else None,
                                           "unit": target["unit"],
                                           "level": LEVELS.get(row["quantity_level"], row["quantity_level"]) if row else "未决",
                                           "target_id": tid, "measurement_basis_id": target["measurement_basis_id"],
                                           "quantity_role": target["quantity_role"], "scenario_id": target["scenario_id"]})
            producer = producers.get(row["artifact_id"]) if row else None
            dependencies.append({"target": target, "actual_basis": basis(target),
                                 "artifact_sha256": row["sha256"] if row else None,
                                 "producer_fingerprint": execution["tasks"].get(producer, {}).get("fingerprint"),
                                 "result_pointer": row["result_pointer"] if row else None})
            if row:
                origins.append({k: row.get(k) for k in ("target_id", "artifact_id", "path", "sha256", "result_pointer")})
        obj.setdefault("attributes", {}).update({"workflow_task_id": document["task_id"],
                                                  "workflow_target_ids": ids,
                                                  "workflow_dependency_fingerprint": fingerprint(dependencies),
                                                  "quantity_sources": origins,
                                                  "evidence_only_view": evidence_only,
                                                  "formal_adoption_allowed": False})
        obj["status"] = "工作量待复核" if all(t in by_target for t in ids) else "部分量项未决"
        obj["adoption_result"] = "未作正式采用"
        obj["adoption_reason"] = "本页数值来自当前专业产物；来源解释、工作复核和正式采用分别记录"
    # No second transcription of totals. Keep dissimilar bases/scenarios apart.
    view["summary"] = [{"label": _label(row), "value": row.get("quantity"), "unit": row["unit"],
                        "scope": " / ".join(str(row.get(k, "")) for k in ("scope_id", "owner_skill"))}
                       for row in quantities.get("totals", [])]
    missing = sorted(set(by_target) - quantity_outlets)
    if missing:
        raise ValueError("当前有效量项未进入复核页数量出口：" + ", ".join(missing))
    view.setdefault("scope_notes", []).append("工作流程对账只覆盖已声明范围及实际登记的原图索引；未读范围、未知对象和未决量项仍须处理。")
    view["workflow_binding"] = {"project_id": document["project_id"], "task_id": document["task_id"],
                                "revision_id": document["revision_id"], "quantity_adoption_changed": False}
    if policy == "registered_only":
        view["workflow_binding"].update(binding_policy=policy,
            bound_catalog_keys=sorted(mapping), unbound_catalog_keys=sorted(unbound),
            unbound_template_summary=template_summary,
            scope_note="仅登记对象的量项接入本轮执行链；其余旧记录按原样保留，尚未建立可执行量项映射。")
        bound_summary = [{**row, "label": "本轮绑定量项：" + row["label"],
                          "scope": "仅本轮已登记量项，不与原页面汇总相加；" + row["scope"]} for row in view["summary"]]
        view["summary"] = template_summary + bound_summary
    # Existing snapshot hashing includes attributes, so dependency changes also
    # invalidate feedback when the displayed number happens to remain unchanged.
    return normalize_view(view, base_dir)


def _verified_json(artifact):
    path = Path(artifact["path"])
    raw = path.read_bytes()
    if artifact.get("status") != "current" or hashlib.sha256(raw).hexdigest() != artifact.get("sha256"):
        raise ValueError("产物缺失、失效或读取期间发生变化")
    return json.loads(raw.decode("utf-8-sig"))


def _selected_catalog_keys(action, handoff, objects):
    """Retain the actual clicked view; a physical ID is not a view selection."""
    selected = set(action.get("selected_object_keys", []))
    excluded = set(action.get("excluded_object_keys", []))
    if selected or excluded:
        return selected - excluded, []
    annotation = handoff.get("feedback", {}).get("annotations", {}).get(action.get("annotation_key"), {})
    snapshot_key = (annotation.get("object_snapshot") or {}).get("catalog_key")
    action_key = action.get("object_catalog_key")
    if action_key and snapshot_key and action_key != snapshot_key:
        raise ValueError("单对象意见的交接键与原始对象快照不一致")
    key = action_key or snapshot_key
    if key:
        if key in objects and objects[key]["id"] not in action.get("object_ids", []):
            raise ValueError("单对象意见的复核对象键与物理身份不一致")
        return {key}, []
    # Older single-object handoffs may omit the snapshot. Retain only an
    # unambiguous one-view, one-target mapping, never all views of a physical ID.
    unresolved = []
    for physical_id in sorted(set(action.get("object_ids", []))):
        matches = [key for key, obj in objects.items() if obj["id"] == physical_id]
        if (len(matches) == 1 and
                len(objects[matches[0]].get("attributes", {}).get("workflow_target_ids", [])) == 1):
            selected.add(matches[0])
        else:
            unresolved.append({"object_id": physical_id, "candidate_catalog_keys": sorted(matches)})
    return selected, unresolved


def feedback_patch(document, execution, artifact_id, workflow_sha256):
    artifact = execution["artifacts"].get(artifact_id)
    if not artifact:
        raise ValueError("未登记意见交接产物")
    tasks = {t["id"]: t for t in document["tasks"]}
    producers = {aid: t for t in document["tasks"] for aid in t.get("output_ids", [])}
    producer = producers.get(artifact_id, {})
    if producer.get("action", {}).get("tool") != "review_feedback":
        raise ValueError("先用登记的 review_feedback 工具核对当前视图及真实反馈，再接入后续任务")
    handoff = _verified_json(artifact)
    if handoff.get("schema") != "qto-review-handoff/1.0" or handoff.get("project_id") != document["project_id"]:
        raise ValueError("意见交接的契约或项目身份不符")
    view_id = producer["action"]["arguments"]["view"]
    view_file = execution["artifacts"][view_id]
    original_view = normalize_view(_verified_json(view_file), Path(view_file["path"]).parent)
    if (original_view.get("workflow_binding", {}).get("task_id") != document["task_id"] or
            original_view["result_fingerprint"] != handoff.get("result_fingerprint") or
            original_view["source_sha256"] != handoff.get("source_sha256")):
        raise ValueError("意见必须绑定本任务通过统一数量索引生成的实际复核版本")
    original_objects = {o["catalog_key"]: o for o in original_view["objects"]}
    targets = {r["id"]: r for r in document.get("quantity_targets", [])}
    claims = {r["target_id"]: r for r in document.get("quantity_claims", [])}
    old_artifacts = {a["id"]: a for a in document["artifacts"]}
    packages = {p["id"]: p for p in document["work_packages"]}
    reviews = {r["catalog_key"]: r for r in document.get("review_bindings", [])}
    imported = {}
    imported_unmapped = {}
    for row in document.get("review_imports", []):
        imported.setdefault(row["action_fingerprint"], set()).update(row["target_ids"])
        imported_unmapped.setdefault(row["action_fingerprint"], set()).update(row.get("unmapped_catalog_keys", []))
    partial = document.get("review_binding_policy") == "registered_only"
    if partial != (original_view.get("workflow_binding", {}).get("binding_policy") == "registered_only"):
        raise ValueError("本次绑定策略不同于实际受批注版本，须先明确迁移意见")
    upsert = {k: [] for k in ("artifacts", "tasks", "issues", "quantity_targets", "quantity_claims", "scopes", "review_imports")}
    grouped, scope_new_tasks = {}, {}
    fresh_objects = None
    for action in handoff.get("actions", []):
        action_fp = fingerprint({"artifact": artifact["sha256"], "action": action})
        selected_keys, unresolved_selection = _selected_catalog_keys(action, handoff, original_objects)
        if not selected_keys and not unresolved_selection:
            # An explicitly empty/all-excluded group never expands to object_ids.
            continue
        unknown = selected_keys - set(original_objects)
        chosen = set()
        unmapped = set()
        for key in selected_keys & set(original_objects):
            old = original_objects[key]
            old_targets = set(old.get("attributes", {}).get("workflow_target_ids", []))
            if not old_targets:
                if partial and key not in reviews:
                    unmapped.add(key)
                    continue
                raise ValueError("旧复核对象缺少量项身份，须先作明确迁移：" + key)
            if (key not in reviews or old_targets != set(reviews[key]["target_ids"]) or
                    any(t not in targets or targets[t]["physical_id"] != old["id"] for t in old_targets)):
                raise ValueError("当前对象/量项绑定不同于实际受批注对象，不能重解释旧意见：" + key)
            remaining = old_targets - imported.get(action_fp, set())
            if remaining:
                # Compare actual current dependency-bound objects, not merely a
                # catalog key or a caller's unchanged rule/evidence identifier.
                if fresh_objects is None:
                    from qto_workflow_quantities import reconcile
                    current_q = reconcile(document, execution["artifacts"], execution["task_statuses"])
                    current_view = bind_view(document, execution, current_q, original_view, Path(view_file["path"]).parent)
                    fresh_objects = {o["catalog_key"]: o for o in current_view["objects"]}
                if fresh_objects[key]["result_fingerprint"] != old["result_fingerprint"]:
                    raise ValueError("受批注对象的实际数量/依赖已变，旧意见仅供历史参考：" + key)
                chosen.update(remaining)
        pending = unmapped - imported_unmapped.get(action_fp, set())
        if pending:
            upsert["issues"].append({"id": "review-unmapped-" + action_fp[:16], "kind": "read_more",
                "cause_id": action_fp, "status": "waiting_mapping",
                "question": "逐条核对工程意见；本轮尚无真实量项绑定，不能按记录编号造计算目标。非数量证据可有据解释，不强制重算。",
                "target_ids": [], "catalog_keys": sorted(pending), "attempt_refs": [artifact_id],
                "review_action": copy.deepcopy(action)})
            upsert["review_imports"].append({"id": "import-unmapped-" + fingerprint([action_fp, sorted(pending)])[:24],
                "action_fingerprint": action_fp, "artifact_id": artifact_id, "sha256": artifact["sha256"],
                "target_ids": [], "unmapped_catalog_keys": sorted(pending), "status": "waiting_mapping"})
        if unknown or unresolved_selection:
            upsert["issues"].append({"id": "review-map-" + action_fp[:16], "kind": "read_more", "cause_id": action_fp,
                                      "question": "核对意见的实际复核对象和量项；缺少快照且物理编号对应多个视图/量项时须由模型补对应，不能扩大选择范围",
                                      "target_ids": sorted(chosen), "attempt_refs": [artifact_id], "unmatched_object_ids": sorted(unknown),
                                      "unresolved_selection": unresolved_selection})
        if not chosen:
            continue
        upsert["review_imports"].append({"id": "import-" + fingerprint([action_fp, sorted(chosen)])[:24],
                                          "action_fingerprint": action_fp, "artifact_id": artifact_id,
                                          "sha256": artifact["sha256"], "target_ids": sorted(chosen)})
        for wp in {targets[t]["work_package_id"] for t in chosen}:
            group = grouped.setdefault(wp, {"actions": [], "target_ids": set()})
            applicable = sorted(t for t in chosen if targets[t]["work_package_id"] == wp)
            group["actions"].append({"action": action, "target_ids": applicable})
            group["target_ids"].update(applicable)
    for wp, group in grouped.items():
        selected_targets = group["target_ids"]
        token = "review-" + fingerprint({"artifact": artifact["sha256"], "wp": wp, "actions": group["actions"]})[:16]
        decision_id, decide_task = token + "-decision", token + "-interpret"
        upsert["artifacts"].append({"id": decision_id, "kind": "decision", "path": f"work/review/{token}/decision.json"})
        upsert["tasks"].append({"id": decide_task, "title": "解释真实意见并确定本次修订范围", "stage": "review",
                                 "work_package_id": wp, "actor": "model", "depends_on": [],
                                 "input_ids": [artifact_id], "output_ids": [decision_id],
                                 "instruction": "逐条保留评审原话、作者与确认来源、对象与例外、需改事实/方法及依据。多意见同作业一起解释；矛盾先定位，方法认可不等于数字确认。",
                                 "review_actions": group["actions"], "selected_target_ids": sorted(selected_targets)})
        for tid in sorted(selected_targets):
            revised_target = copy.deepcopy(targets[tid])
            revised_target["basis_refs"] = list(dict.fromkeys([*revised_target.get("basis_refs", []), decision_id]))
            revised_target["reason"] = revised_target.get("reason", "") + "；本次修订另见实际意见及解释记录 " + decision_id
            upsert["quantity_targets"].append(revised_target)
        job_ids = {producers[claims[tid]["artifact_id"]]["id"] for tid in selected_targets
                   if tid in claims and claims[tid]["artifact_id"] in producers}
        rerun_outputs, terminal_tasks, revised_targets = [], [], set()
        for job_id in sorted(job_ids):
            original = tasks[job_id]
            if original.get("actor") != "program" or not original.get("action"):
                continue
            tool, args = bindings(original)
            if tool["owner_skill"] != packages[wp]["owner_skill"]:
                continue
            clone_id, replace_inputs = job_id + "-" + token, {}
            for field in tool["inputs"]:
                if field in {"input", "manifest"} and field in args:
                    old = args[field]
                    original_artifact = old_artifacts[old]
                    kind = original_artifact["kind"].lower().replace("-", "_")
                    if kind in READ_ONLY_KINDS - {"source_input"}:
                        continue
                    if old in replace_inputs:
                        continue
                    # Independent jobs may consume the same original input.
                    # Their revised inputs need separate identities/producers:
                    # each preparation may apply a different selected scope.
                    # Two flags in one job still share one revised artifact.
                    new = old + "-" + fingerprint({"job_id": job_id, "review_token": token})[:24]
                    replace_inputs[old] = new
                    revised_input = {"id": new, "kind": "input",
                                     "path": f"work/review/{token}/{clone_id}/{field}.json"}
                    if "read_only" in original_artifact:
                        revised_input["read_only"] = False
                    # A user's protected original manifest remains untouched;
                    # the model prepares a separate, editable professional input.
                    upsert["artifacts"].append(revised_input)
            if not replace_inputs:
                continue
            prep_id = clone_id + "-prepare"
            upsert["tasks"].append({"id": prep_id, "title": "按已解释意见准备新版本专业输入", "stage": "recognize",
                                     "actor": "model", "work_package_id": wp, "depends_on": [decide_task],
                                     "input_ids": list(dict.fromkeys([decision_id, *original["input_ids"]])),
                                     "output_ids": list(replace_inputs.values()),
                                     "instruction": "保留原输入与来源，仅对所选对象作有据修订；例外和其余对象保留原意。保存稳定对象ID，核对结果数组定位及间接文件依赖。",
                                     "selected_target_ids": sorted(selected_targets), "revises_task_id": job_id})
            clone = copy.deepcopy(original)
            clone.update({"id": clone_id, "title": original["title"] + "（意见处理后复算）",
                          "depends_on": list(dict.fromkeys([*original.get("depends_on", []), prep_id])),
                          "input_ids": list(dict.fromkeys([decision_id, *[replace_inputs.get(a, a) for a in original["input_ids"]]]))})
            replace_outputs = {}
            for field in tool["outputs"]:
                old = args[field]
                new = old + "-" + token
                replace_outputs[old] = new
                upsert["artifacts"].append({"id": new, "kind": old_artifacts[old]["kind"],
                                             "path": f"work/review/{token}/{clone_id}/{field}{Path(old_artifacts[old]['path']).suffix}"})
            clone["output_ids"] = list(replace_outputs.values())
            clone["action"]["arguments"] = {field: replace_inputs.get(value, replace_outputs.get(value, value))
                                              if isinstance(value, str) else value for field, value in args.items()}
            upsert["tasks"].append(clone)
            terminal_tasks.append(clone_id)
            rerun_outputs.extend(clone["output_ids"])
            for claim in document.get("quantity_claims", []):
                if claim["target_id"] in selected_targets and claim["artifact_id"] in replace_outputs:
                    updated = copy.deepcopy(claim)
                    updated["artifact_id"] = replace_outputs[claim["artifact_id"]]
                    updated["previous_result_reference"] = {"artifact_id": claim["artifact_id"],
                                                               "sha256": execution["artifacts"][claim["artifact_id"]].get("sha256"),
                                                               "result_pointer": claim["result_pointer"]}
                    upsert["quantity_claims"].append(updated)
                    revised_targets.add(claim["target_id"])
        receipt_id, finish_id = token + "-receipt", token + "-backcheck"
        upsert["artifacts"].append({"id": receipt_id, "kind": "evidence", "path": f"work/review/{token}/receipt.json"})
        upsert["tasks"].append({"id": finish_id, "title": "回查新旧数量差异及例外，形成处理回执", "stage": "reconcile",
                                 "actor": "model", "work_package_id": wp, "depends_on": terminal_tasks or [decide_task],
                                 "input_ids": [decision_id, *rerun_outputs], "output_ids": [receipt_id],
                                 "instruction": "按对象和单位核对新旧数量、所选对象及例外，记录已处理与未处理项，再从统一索引生成新版复核图面；不得自动正式采用。"})
        upsert["issues"].append({"id": token + "-process", "kind": "review_feedback", "cause_id": token,
                                  "question": "完成意见解释、版本化输入、实际复算与差异回执", "target_ids": sorted(selected_targets),
                                  "task_ids": [decide_task, *terminal_tasks, finish_id], "attempt_refs": [artifact_id],
                                  "resolution_artifact_id": receipt_id})
        remaining = selected_targets - revised_targets
        if remaining:
            upsert["issues"].append({"id": token + "-calculator", "kind": "read_more", "cause_id": token,
                                      "question": "这些量项缺少可复用的已登记专业作业；补齐实际执行路径，不能仅凭解释回执关闭",
                                      "target_ids": sorted(remaining), "attempt_refs": [artifact_id]})
        for scope_id in {targets[t]["scope_id"] for t in selected_targets}:
            scope_new_tasks.setdefault(scope_id, set()).update([decide_task, *terminal_tasks, finish_id])
    for scope in document.get("scopes", []):
        if scope["id"] in scope_new_tasks:
            changed = copy.deepcopy(scope)
            changed["required_task_ids"] = sorted(set(changed.get("required_task_ids", [])) | scope_new_tasks[scope["id"]])
            upsert["scopes"].append(changed)
    return {"project_id": document["project_id"], "task_id": document["task_id"],
            "expected_workflow_sha256": workflow_sha256,
            "reason": "接入本版本真实意见，按实际所选对象形成解释、输入修订、专业重算和回执；例外保留原有效出口",
            "upsert": upsert, "quantity_changes": 0,
            "historical_feedback_count": len(handoff.get("historical_annotations", {})),
            "note": "运行 amend 后由模型解释并修订输入，再 advance 实际计算；重复导入的已登记意见不再创建作业。"}


def _cell(value):
    return str(value if value is not None else "未决").replace("|", "\\|").replace("\n", " ")


def markdown_report(document, report):
    q = report["quantities"]
    lines = ["# " + document.get("title", "工程量工作进展"), "",
             f"项目：{document['project_id']}；任务：{document['task_id']}；修订：{document['revision_id']}。", "",
             "数值直接读取已核验专业产物。任务执行完成、已声明范围对账、工程语义复核和正式采用分别记录。", "",
             "## 当前工程量", "", "| 范围 / 专业 | 数量含义与口径 | 数量 | 单位 |", "|---|---|---:|---|"]
    for row in q.get("totals", []):
        lines.append("| " + " | ".join(_cell(x) for x in (str(row.get("scope_id", "")) + " / " + str(row.get("owner_skill", "")),
                                                            _label(row), row.get("quantity"), row.get("unit"))) + " |")
    if not q.get("totals"):
        lines += ["", "当前没有满足引用、数量出口及版本条件的数值。未决不作为零量。"]
    lines += ["", "## 作业状态", "", "| 工作任务 | 阶段 | 状态 |", "|---|---|---|"]
    for task in document.get("tasks", []):
        lines.append("| " + " | ".join(_cell(x) for x in (task["title"], task["stage"], report["execution"]["task_statuses"].get(task["id"], "pending"))) + " |")
    lines += ["", "## 尚需处理", ""]
    for field, title in (("unaccounted_targets", "未完成量项"), ("unprocessed_observations", "未处置观察"), ("ownership_conflicts", "数量归属冲突")):
        rows = q.get(field, [])
        lines.append(f"- {title}：{len(rows)} 项。完整定位见同名 JSON 报告。")
    for group in [*report["next"]["evidence_questions"], *report["next"]["project_questions"]]:
        who = "模型继续取证" if group["actor"] == "model" else "需项目方提供资料或作选择"
        lines.append("- " + who + "：" + "；".join(group["questions"]))
    lines += ["", "## 数值追溯", "", "| 量项 | 数量 | 单位 | 专业产物与定位 |", "|---|---:|---|---|"]
    for row in q.get("result_index", []):
        lines.append("| " + " | ".join(_cell(x) for x in (row["target_id"], row["quantity"], row["unit"], row["path"] + " # " + row["result_pointer"])) + " |")
    lines += ["", "本报告不证明未登记工程对象均已识别，也不自动给予正式采用。未读图种、资料缺口和专业能力边界按实际工作记录保留。", ""]
    return "\n".join(lines)
