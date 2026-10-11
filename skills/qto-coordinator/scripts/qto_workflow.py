"""Persistent, artifact-backed takeoff workflow; AI judgments stay explicit.

This is an execution coordinator, not a recognition model or quantity engine.
Single-profession long tasks may also use it without changing professional owner.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

SHARED = Path(__file__).resolve().parents[2] / "_qto-shared-evidence" / "scripts"
sys.path.insert(0, str(SHARED))
import qto_workflow_state as state_engine
import qto_workflow_tools as tool_engine
from qto_workflow_dependencies import validate_dependencies
from qto_workflow_quantities import reconcile

SCHEMA = "qto-project-workflow/1.0"
STAGES = {v: n for n, v in enumerate(("read", "recognize", "calculate", "reconcile", "review", "deliver"))}
DEFAULT_TOOLS = {v["owner_skill"]: k for k, v in tool_engine.TOOLS.items()
                 if k in {"pipe_infer", "component_count", "duct_reference", "electrical_paths",
                          "concrete_formwork", "rebar", "structural_steel", "masonry_reference",
                          "surfaces_envelope", "framing_reference", "earthwork_terrain",
                          "piles_reference", "roads_reference"}}
COLLECTIONS = {"artifacts", "scopes", "work_packages", "tasks", "objects", "quantity_targets",
               "quantity_claims", "issues", "review_bindings", "review_imports"}


def now():
    return datetime.now(timezone.utc).isoformat()


def read(path):
    def invalid(value):
        raise ValueError("JSON 含非有限数值：" + value)
    return json.loads(Path(path).read_text(encoding="utf-8-sig"), parse_constant=invalid)


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).is_file() else None


def atomic(path, value, expected=None, *, create=False):
    path = Path(path).resolve()
    if create and path.exists():
        raise ValueError("文件已存在，请恢复当前任务或使用新的任务路径：" + str(path))
    if not create and file_hash(path) != expected:
        raise ValueError("文件已由其他执行分支更新；重新读取后再提交：" + str(path))
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".qto-write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(encoded(value))
            stream.flush()
            os.fsync(stream.fileno())
        if create:
            # A competing creator must not be overwritten after the first check.
            os.link(temp, path)
        else:
            os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


@contextmanager
def lease(path):
    lock = Path(str(Path(path).resolve()) + ".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        with lock.open("x", encoding="utf-8") as stream:
            stream.write(now())
    except FileExistsError as exc:
        raise ValueError("另一执行分支正在更新此文件：" + str(path)) from exc
    try:
        yield
    finally:
        lock.unlink(missing_ok=True)


@contextmanager
def workflow_reader(path):
    """Independent state branches share a plan; amendments wait for all readers."""
    readers = Path(str(Path(path).resolve()) + ".readers")
    with lease(path):
        readers.mkdir(parents=True, exist_ok=True)
        marker = readers / (uuid.uuid4().hex + ".json")
        marker.write_bytes(encoded({"pid": os.getpid(), "started_at": now()}))
    try:
        yield
    finally:
        marker.unlink(missing_ok=True)


def require_idle_workflow(path):
    readers = Path(str(Path(path).resolve()) + ".readers")
    if readers.exists() and any(readers.iterdir()):
        raise ValueError("当前台账仍有执行分支在运行；完成后再应用修改包。异常退出遗留的租约须核实进程已结束后清理。")


def tool_versions(document):
    packages = {p["id"]: p for p in document.get("work_packages", [])}
    ids = [t["action"]["tool"] for t in document.get("tasks", []) if t.get("action")]
    for task in document.get("tasks", []):
        if task.get("action"):
            if task.get("actor") != "program":
                raise ValueError("登记工具只允许在 program 任务运行")
            tool, args = tool_engine.bindings(task)
            if tool["owner_skill"] != "_qto-shared-evidence" and packages.get(task["work_package_id"], {}).get("owner_skill") != tool["owner_skill"]:
                raise ValueError("专业计算工具必须由对应专业工作包负责")
    return tool_engine.fingerprints(ids, owner_skills=[p["owner_skill"] for p in packages.values()])


def inspect_execution(document, state, root, versions):
    execution = state_engine.inspect_workflow(document, state, root, versions)
    contracts = {t["id"]: t for t in document.get("tasks", [])}
    # The state engine supplies a topological order. Missing actual file
    # dependencies block only their task and descendants, not unrelated packages.
    for tid, row in execution["tasks"].items():
        task = contracts[tid]
        errors = []
        if task.get("action"):
            dependency_check = validate_dependencies(task, execution["artifacts"], root)
            row["file_dependencies"] = dependency_check
            errors = dependency_check["errors"]
            row["dependency_errors"] = errors
        waiting = [dep for dep in row["depends_on"] if execution["tasks"][dep]["status"] != "completed"]
        if errors or waiting:
            if errors:
                row["reasons"].extend(error for error in errors if error not in row["reasons"])
            if waiting:
                message = "依赖任务尚未完成或真实文件依赖未闭合：" + ", ".join(waiting)
                if message not in row["reasons"]:
                    row["reasons"].append(message)
            row["status"] = ("stale" if row["previous_receipt_id"] or row["historical_receipt_ids"] else
                             "blocked" if errors or any(execution["tasks"][dep]["status"] == "blocked" for dep in waiting) else "pending")
            row.update(can_run=False, can_checkpoint=False, current_receipt_id=None)
    consumed = {}
    for tid, row in execution["tasks"].items():
        dependencies = set(row["input_ids"])
        for dep in row["depends_on"]:
            dependencies.update(consumed[dep])
            dependencies.update(execution["tasks"][dep]["output_ids"])
        consumed[tid] = dependencies
    for artifact in execution["artifacts"].values():
        producer = artifact["producer_task_id"]
        artifact["workflow_tracked"] = True
        artifact["producer_input_ids"] = sorted(consumed.get(producer, set()))
        if producer and artifact["file_status"] == "present" and execution["tasks"][producer]["status"] != "completed":
            artifact["status"] = "stale"
    execution["task_statuses"] = {tid: row["status"] for tid, row in execution["tasks"].items()}
    for status in ("ready", "blocked", "stale"):
        execution[status + "_task_ids"] = [tid for tid, row in execution["tasks"].items() if row["status"] == status]
    execution["runnable_task_ids"] = [tid for tid, row in execution["tasks"].items() if row["can_run"]]
    return execution


def inspect(document, state, root):
    versions = tool_versions(document)
    execution = inspect_execution(document, state, root, versions)
    quantities = reconcile(document, execution["artifacts"], execution["task_statuses"])
    return execution, quantities, versions


def issue_queue(document, execution, quantities):
    """Group actual missing facts, with AI attempts before project questions."""
    groups = {}
    targets = {r["id"]: r for r in document.get("quantity_targets", [])}
    for issue in document.get("issues", []):
        resolved = issue.get("resolution_artifact_id")
        if resolved and execution["artifacts"].get(resolved, {}).get("status") == "current":
            continue
        for ref in [*issue.get("attempt_refs", []), *([resolved] if resolved else [])]:
            if ref not in execution["artifacts"]:
                raise ValueError("问题引用未登记产物：" + str(ref))
        missing_targets = set(issue.get("target_ids", [])) - set(targets)
        if missing_targets:
            raise ValueError("问题引用未知量项：" + ", ".join(sorted(missing_targets)))
        attempts = issue.get("attempt_refs", [])
        tried = bool(attempts) and all(execution["artifacts"][r]["status"] == "current" for r in attempts)
        kind = issue.get("kind", "read_more")
        actor = "user" if kind in {"missing_source", "design_conflict", "measurement_choice"} and tried else "model"
        key = (issue.get("cause_id", issue["id"]), actor)
        group = groups.setdefault(key, {"id": key[0], "actor": actor, "kind": kind,
                                       "questions": [], "issue_ids": [], "task_ids": [], "target_ids": [],
                                       "attempt_refs": [], "affected_quantities": []})
        group["issue_ids"].append(issue["id"])
        group["questions"].append(issue.get("question", issue.get("reason", "继续定位缺失事实")))
        for field in ("task_ids", "target_ids", "attempt_refs"):
            group[field] = sorted(set(group[field]) | set(issue.get(field, [])))
    for group in groups.values():
        # Separate target identities and units; never invent a combined impact score.
        group["affected_quantities"] = [{"target_id": key, "quantity_kind": targets[key]["quantity_kind"],
                                           "unit": targets[key]["unit"], "impact": "待该事实解决后重算"}
                                          for key in group["target_ids"]]
    return sorted(groups.values(), key=lambda g: (g["actor"] == "user", -len(g["task_ids"]), g["id"]))


def next_actions(document, state, execution, quantities, limit=12):
    tasks = {t["id"]: t for t in document.get("tasks", [])}
    actions = []
    for tid in execution.get("runnable_task_ids", execution["ready_task_ids"]):
        task = tasks[tid]
        error = execution["tasks"][tid].get("last_error")
        actions.append({"task_id": tid, "actor": "model" if error else task["actor"],
                        "stage": task["stage"], "title": task["title"],
                        "instruction": ("按运行错误补齐或修正输入后重试" if error else task.get("instruction", task["title"])),
                        "action": task.get("action"), "last_error": error,
                        "task_fingerprint": execution["tasks"][tid]["fingerprint"],
                        "input_ids": task["input_ids"], "output_ids": task["output_ids"]})
    for tid, row in execution["tasks"].items():
        if row.get("dependency_errors"):
            task = tasks[tid]
            actions.append({"task_id": tid, "actor": "model", "stage": task["stage"], "title": "闭合计算器真实文件依赖",
                            "instruction": "核对下列实际文件引用，登记产物并通过 amend 加入对应任务 input_ids，再继续执行。",
                            "dependency_errors": row["dependency_errors"], "action": None,
                            "task_fingerprint": row["fingerprint"], "input_ids": task["input_ids"], "output_ids": task["output_ids"]})
    actions.sort(key=lambda a: (a["actor"] == "user", STAGES.get(a["stage"], 99), a["task_id"]))
    issues = issue_queue(document, execution, quantities)
    return {"actions": actions[:limit], "additional_ready_actions": max(0, len(actions) - limit),
            "evidence_questions": [g for g in issues if g["actor"] == "model"][:limit],
            "project_questions": [g for g in issues if g["actor"] == "user"][:limit],
            "additional_issue_groups": max(0, len(issues) - 2 * limit),
            "quantity_next_actions": quantities.get("next_actions", [])[:limit],
            "blocked_task_ids": execution["blocked_task_ids"],
            "note": "任务完成仅说明对应文件已形成；原图语义、范围完整和正式采用分别复核。"}


def snapshot(document, state, root):
    execution, quantities, versions = inspect(document, state, root)
    queue = next_actions(document, state, execution, quantities)
    return {"schema_version": "qto-project-workflow-report/1.0", "project_id": document["project_id"],
            "task_id": document["task_id"], "revision_id": document["revision_id"],
            "generated_at": now(), "execution": execution, "quantities": quantities,
            "next": queue, "formal_adoption_allowed": False}


def run_one(document, state, task_id, root, protected_paths=(), *, workflow_path=None, expected_workflow_sha256=None):
    execution, quantities, versions = inspect(document, state, root)
    if task_id not in execution.get("runnable_task_ids", execution["ready_task_ids"]):
        raise ValueError("任务尚不可执行：" + task_id)
    task = next(t for t in document["tasks"] if t["id"] == task_id)
    if task["actor"] != "program" or not task.get("action"):
        raise ValueError("此任务需要模型取证/解释或真实用户决定；不能当作程序任务跳过")
    before = execution["tasks"][task_id]["fingerprint"]

    def unchanged():
        if workflow_path is not None and file_hash(workflow_path) != expected_workflow_sha256:
            raise ValueError("运行期间项目作业台账发生变化，输出未发布；请先恢复当前版本")
        fresh_versions = tool_versions(document)
        current = inspect_execution(document, state, root, fresh_versions)
        if task_id not in current.get("runnable_task_ids", current["ready_task_ids"]) or current["tasks"][task_id]["fingerprint"] != before:
            raise ValueError("运行期间来源、输入、依赖或工具已改变，输出尚未发布；请重新计算")

    evidence = tool_engine.execute(task, execution["artifacts"], root,
                                   before_publish=unchanged, protected_paths=protected_paths)
    updated = state_engine.checkpoint(document, state, task_id, root, "program",
                                      "登记工具实际运行成功；" + evidence["run_directory"],
                                      tool_versions(document), expected_task_fingerprint=before,
                                      execution_files=evidence["execution_files"])
    updated.setdefault("attempts", []).append({"task_id": task_id, "task_fingerprint": before,
                                                "success": True, "recorded_at": now(), **evidence})
    return updated, evidence


def starter(project_id, task_id, title, scope, source_paths, skills, root, mode="production"):
    if not source_paths or not skills:
        raise ValueError("新任务至少需当前实际来源和一个有依据的专业；不要默认建立全部专业")
    if len(set(skills)) != len(skills) or any(s not in DEFAULT_TOOLS for s in skills):
        raise ValueError("专业入口未知或重复")
    artifacts = [{"id": f"source-{i + 1}", "kind": "source", "path": str(Path(p).resolve())}
                 for i, p in enumerate(source_paths)]
    source_ids = [a["id"] for a in artifacts]
    tasks, packages = [], []
    for i, skill in enumerate(skills):
        wp = f"wp-{i + 1}"
        package = {"id": wp, "owner_skill": skill, "scope_ids": ["scope-1"]}
        packages.append(package)
        read_id, prepare_id, calc_id, check_id = [wp + "-" + k for k in ("read", "prepare", "calculate", "backcheck")]
        evidence_id, prepared_id = wp + "-reading", wp + "-input"
        artifacts += [{"id": evidence_id, "kind": "evidence", "path": f"work/{wp}/reading.json"},
                      {"id": prepared_id, "kind": "input", "path": f"work/{wp}/input.json"}]
        tasks.append({"id": read_id, "work_package_id": wp, "title": "读图确认范围、说明及相关视图", "stage": "read", "actor": "model",
                      "depends_on": [], "input_ids": source_ids, "output_ids": [evidence_id],
                      "instruction": "运用图文理解和工程知识，选择能解决当前问题的总览、说明、图例或局部视图；需要看图样时可用 cad_view，需要精确实体时用索引/查询。保存已获事实、定位和未读范围，按新证据调整任务，不把目录或 actor=model 标签当成已识图。"})
        tasks.append({"id": prepare_id, "work_package_id": wp, "title": "完整识别一个代表范围并准备专业输入", "stage": "recognize", "actor": "model",
                      "depends_on": [read_id], "input_ids": [*source_ids, evidence_id], "output_ids": [prepared_id],
                      "instruction": f"读取 {skill}/SKILL.md，按问题选择专业策略，自主解释对象、关系、适用构造和尺寸。根数、净长等是从图纸求解的目标，不要求用户预填；用工具取得或推导数值并编制原生输入。可从代表范围、表格或节点切入，保留例外；发现新问题可调整任务和回到取证。"})
        key = DEFAULT_TOOLS[skill]
        tool = tool_engine.TOOLS[key]
        arguments = {tool["inputs"][0]: prepared_id}
        output_ids = []
        for field in tool["outputs"]:
            aid = wp + "-" + field
            suffix = ".csv" if "csv" in field or field == "detail-out" else ".json"
            artifacts.append({"id": aid, "kind": "result" if field in {"output", "result-out"} else "evidence", "path": f"work/{wp}/{field}{suffix}"})
            arguments[field] = aid
            output_ids.append(aid)
        tasks.append({"id": calc_id, "work_package_id": wp, "title": "运行专业计算器", "stage": "calculate", "actor": "program",
                      "depends_on": [prepare_id], "input_ids": [prepared_id, *source_ids], "output_ids": output_ids,
                      "action": {"tool": key, "arguments": arguments}})
        review_id = wp + "-backcheck-evidence"
        artifacts.append({"id": review_id, "kind": "evidence", "path": f"work/{wp}/backcheck.json"})
        tasks.append({"id": check_id, "work_package_id": wp, "title": "沿系统回图查漏、查重与异常复核", "stage": "reconcile", "actor": "model",
                      "depends_on": [calc_id], "input_ids": [*source_ids, prepared_id, *output_ids], "output_ids": [review_id],
                      "instruction": "选择能发现输入误判或遗漏的独立检查，例如从末端反查支路、从节点反查构件或对比尺寸链；依据异常主动补看图纸、修改解释并重算。核对实际覆盖和原生结果，保留未决；不以重复同一算术或生成复核页代替看图检查。"})
    return {"schema_version": SCHEMA, "project_id": project_id, "task_id": task_id,
            "revision_id": "1", "task_mode": mode, "title": title,
            "artifacts": artifacts, "scopes": [{"id": "scope-1", "title": scope,
                                                 "basis_refs": source_ids, "inventory_artifact_ids": [],
                                                 "required_task_ids": [t["id"] for t in tasks]}],
            "work_packages": packages, "tasks": tasks, "objects": [], "observation_dispositions": [],
            "quantity_targets": [], "quantity_claims": [], "issues": [], "review_bindings": [],
            "note": "这是可修改的文件执行骨架，不是模型的固定推理流程。模型可按新证据调整任务、方法和顺序；模板不证明任何构造存在或模型已完成识图。"}


def amend(document, patch, current_hash):
    if patch.get("expected_workflow_sha256") != current_hash:
        raise ValueError("修改包未绑定当前工作台账版本；先重读当前文件")
    if any(patch.get(k) != document.get(k) for k in ("project_id", "task_id")):
        raise ValueError("修改包不属于本项目/任务")
    if not isinstance(patch.get("reason"), str) or not patch["reason"].strip():
        raise ValueError("修改须说明依据和影响")
    result = copy.deepcopy(document)
    for operation in ("upsert", "remove"):
        rows = patch.get(operation, {})
        if not isinstance(rows, dict) or set(rows) - COLLECTIONS:
            raise ValueError("仅允许修改已登记的工作集合")
        for field, changes in rows.items():
            if not isinstance(changes, list):
                raise ValueError("集合修改必须为数组")
            existing = {r["id"]: r for r in result.get(field, [])}
            changed_ids = [r["id"] if operation == "upsert" else r for r in changes]
            if len(set(changed_ids)) != len(changed_ids):
                raise ValueError("同一修改包重复操作同一编号")
            for row, key in zip(changes, changed_ids):
                if operation == "upsert":
                    existing[key] = row
                elif key not in existing:
                    raise ValueError("不能删除不存在的编号：" + key)
                else:
                    del existing[key]
            result[field] = list(existing.values())
    if "observation_dispositions" in patch:
        if not isinstance(patch["observation_dispositions"], list):
            raise ValueError("观察处置账必须为数组；替换须完整保留未处理对象")
        result["observation_dispositions"] = patch["observation_dispositions"]
    result["revision_id"] = str(int(document["revision_id"]) + 1) if str(document["revision_id"]).isdigit() else now()
    result.setdefault("amendments", []).append({"at": now(), "previous_sha256": current_hash, "reason": patch["reason"]})
    return result


def protect_controls(document, root, paths):
    actual = {(Path(root) / a["path"]).resolve() for a in document.get("artifacts", [])}
    controls = [Path(p).resolve() for p in paths if p]
    if len(controls) != len(set(controls)) or actual & set(controls):
        raise ValueError("工作台账、状态、报告或修改包路径不得彼此重合或覆盖登记产物")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["init", "next", "status", "resume", "record", "run", "advance", "merge", "amend", "report", "tools", "bind-review", "import-review"])
    parser.add_argument("--workflow", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--task")
    parser.add_argument("--actor", choices=["model", "user"])
    parser.add_argument("--note")
    parser.add_argument("--expected-task-fingerprint")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--incoming-state", type=Path)
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--max-jobs", type=int, default=8)
    parser.add_argument("--project-id")
    parser.add_argument("--task-id")
    parser.add_argument("--title", default="本轮工程量工作")
    parser.add_argument("--scope")
    parser.add_argument("--source", type=Path, action="append", default=[])
    parser.add_argument("--skill", action="append", default=[])
    parser.add_argument("--mode", choices=["production", "independent_assessment"], default="production")
    parser.add_argument("--view", type=Path)
    parser.add_argument("--handoff-artifact")
    a = parser.parse_args(argv)
    try:
        if a.command == "tools":
            print(json.dumps(tool_engine.TOOLS, ensure_ascii=False, indent=2))
            return 0
        if not a.workflow or not a.state:
            raise ValueError("必须提供 --workflow 和 --state")
        root = (a.root or a.workflow.parent).resolve()
        if a.command == "init":
            if not a.project_id or not a.task_id or not a.scope:
                raise ValueError("init 必须说明项目、当前任务身份及实际范围")
            document = starter(a.project_id, a.task_id, a.title, a.scope, a.source, a.skill, root, a.mode)
            protect_controls(document, root, [a.workflow, a.state])
            root.mkdir(parents=True, exist_ok=True)
            state = state_engine.initial_state(document)
            inspect(document, state, root)
            if a.state.exists() or a.workflow.exists():
                raise ValueError("任务文件已存在；请使用 resume 或新任务路径")
            atomic(a.workflow, document, create=True)
            atomic(a.state, state, create=True)
            print(json.dumps({"status": "INITIALIZED", "workflow": str(a.workflow), "state": str(a.state),
                              "next": "运行 next，模型按实际来源补对象、量项与证据；不要填写预期答案"}, ensure_ascii=False))
            return 0
        document = read(a.workflow)
        state = read(a.state)
        protect_controls(document, root, [a.workflow, a.state, None if a.command == "bind-review" else a.output, a.patch, a.incoming_state])
        if a.command in {"next", "status", "resume", "report", "bind-review", "import-review"}:
            report = snapshot(document, state, root)
            if a.command in {"bind-review", "import-review"}:
                from qto_workflow_review import bind_view, feedback_patch
                if a.command == "bind-review":
                    if not a.view or not a.output:
                        raise ValueError("bind-review 需要 --view 和新 --output")
                    if a.output.resolve() == a.view.resolve():
                        raise ValueError("不得覆盖图面模板")
                    if a.output.resolve() in {a.workflow.resolve(), a.state.resolve()}:
                        raise ValueError("复核视图不得覆盖工作台账或状态")
                    if not a.output.resolve().is_relative_to(root.resolve()):
                        raise ValueError("复核视图须写入当前项目目录")
                    output_ids = [aid for aid, row in report["execution"]["artifacts"].items() if Path(row["path"]) == a.output.resolve()]
                    if output_ids:
                        aid = output_ids[0]
                        producer = report["execution"]["artifacts"][aid]["producer_task_id"]
                        task = next((t for t in document["tasks"] if t["id"] == producer), None)
                        if not task or task["actor"] != "model" or task["stage"] != "review" or not report["execution"]["tasks"][producer]["can_checkpoint"]:
                            raise ValueError("登记复核视图须是当前可执行模型 review 任务的产物")
                        input_paths = {Path(report["execution"]["artifacts"][ref]["path"]) for ref in task["input_ids"]}
                        if a.view.resolve() not in input_paths:
                            raise ValueError("图面模板须登记为该复核任务的实际输入")
                        if {row["artifact_id"] for row in report["quantities"]["result_index"]} - set(task["input_ids"]):
                            raise ValueError("复核任务须声明所绑定原生结果的全部文件依赖")
                    value = bind_view(document, report["execution"], report["quantities"], read(a.view), a.view.parent)
                else:
                    if not a.handoff_artifact or not a.output:
                        raise ValueError("import-review 需要 --handoff-artifact 和新 --output 修改包")
                    value = feedback_patch(document, report["execution"], a.handoff_artifact, file_hash(a.workflow))
                atomic(a.output, value, create=True)
                print(json.dumps({"status": "PREPARED", "output": str(a.output), "quantity_changes": 0}, ensure_ascii=False))
                return 0
            if a.command == "report":
                if not a.output:
                    raise ValueError("report 需要新 --output 路径")
                from qto_workflow_review import markdown_report
                md = a.output.with_suffix(".md")
                protect_controls(document, root, [a.workflow, a.state, a.output, md])
                if a.output.exists() or md.exists():
                    raise ValueError("报告路径已存在，请选择新的 JSON 和 Markdown 路径")
                atomic(a.output, report, create=True)
                with md.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(markdown_report(document, report))
                print(json.dumps({"status": "SAVED", "report": str(a.output), "readable_report": str(md)}, ensure_ascii=False))
            elif a.output:
                atomic(a.output, report, create=True)
                print(json.dumps({"status": "SAVED", "report": str(a.output), "next": report["next"]}, ensure_ascii=False))
            else:
                print(json.dumps({"project_id": document["project_id"], "task_id": document["task_id"],
                                  "task_statuses": report["execution"]["task_statuses"],
                                  "scope_status": report["quantities"].get("scope_status"),
                                  "next": report["next"]}, ensure_ascii=False, indent=2))
            return 0
        if a.command == "amend":
            if not a.patch:
                raise ValueError("amend 需要 --patch")
            with lease(a.workflow):
                require_idle_workflow(a.workflow)
                document = read(a.workflow)
                old_hash = file_hash(a.workflow)
                changed = amend(document, read(a.patch), old_hash)
                protect_controls(changed, root, [a.workflow, a.state, a.patch])
                inspect(changed, read(a.state), root)
                history = a.workflow.parent / (a.workflow.stem + ".history") / (old_hash + ".json")
                if not history.exists():
                    atomic(history, document, create=True)
                atomic(a.workflow, changed, old_hash)
            print(json.dumps({"status": "AMENDED", "revision_id": changed["revision_id"], "next": "resume"}, ensure_ascii=False))
            return 0
        with workflow_reader(a.workflow), lease(a.state):
            document = read(a.workflow)
            workflow_hash = file_hash(a.workflow)
            state = read(a.state)
            old_hash = file_hash(a.state)
            if a.command == "record":
                if not a.task or not a.actor or not a.note:
                    raise ValueError("record 须指定 --task、实际 --actor 和 --note")
                task = next((t for t in document["tasks"] if t["id"] == a.task), None)
                if not task or task["actor"] != a.actor or task.get("action"):
                    raise ValueError("record 仅记录同角色的模型/用户工作产物；程序任务必须 run 实际执行")
                execution, _, versions = inspect(document, state, root)
                if not execution["tasks"][a.task]["can_checkpoint"]:
                    raise ValueError("当前任务依赖尚未闭合，不能记录完成")
                state = state_engine.checkpoint(document, state, a.task, root, a.actor, a.note, versions,
                                                expected_task_fingerprint=a.expected_task_fingerprint)
                outcome = {"recorded": a.task, "quantity_adoption_changed": False}
            elif a.command == "merge":
                if not a.incoming_state:
                    raise ValueError("merge 需要 --incoming-state")
                state = state_engine.merge_states(document, state, read(a.incoming_state), root, tool_versions(document))
                outcome = {"merged": str(a.incoming_state)}
            else:
                if a.max_jobs < 1 or a.max_jobs > 100:
                    raise ValueError("max-jobs 须为 1 至 100")
                if a.command == "run" and not a.task:
                    raise ValueError("run 需要 --task")
                if a.command == "run" and not any(t["id"] == a.task and t["actor"] == "program" and t.get("action") for t in document["tasks"]):
                    raise ValueError("run 必须选择已登记的 program 工具任务")
                completed, failures, attempted = [], [], set()
                for _ in range(1 if a.command == "run" else a.max_jobs):
                    execution, quantities, versions = inspect(document, state, root)
                    tasks = {t["id"]: t for t in document["tasks"]}
                    available = [tid for tid in execution.get("runnable_task_ids", execution["ready_task_ids"]) if tasks[tid]["actor"] == "program" and tid not in attempted]
                    tid = a.task if a.command == "run" else next(iter(available), None)
                    if tid is None:
                        break
                    attempted.add(tid)
                    try:
                        state, result = run_one(document, state, tid, root, [a.workflow, a.state],
                                                workflow_path=a.workflow, expected_workflow_sha256=workflow_hash)
                        completed.append({"task_id": tid, **result})
                    except (OSError, ValueError) as exc:
                        failure = {"task_id": tid, "task_fingerprint": execution["tasks"].get(tid, {}).get("fingerprint"),
                                   "tool": tasks[tid]["action"]["tool"], "success": False, "error": str(exc), "recorded_at": now()}
                        state.setdefault("attempts", []).append(failure)
                        failures.append(failure)
                outcome = {"completed": completed, "failures": failures,
                           "next": "读取 next；模型继续取证、补输入或解释问题，其他独立工作可继续"}
            if file_hash(a.workflow) != workflow_hash:
                raise ValueError("工作台账在记录期间发生变化；本次状态不覆盖当前版本，请重新恢复")
            atomic(a.state, state, old_hash)
        print(json.dumps({"status": "PARTIAL" if outcome.get("failures") else "SAVED", **outcome}, ensure_ascii=False, indent=2))
        return 2 if outcome.get("failures") else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "ACTION_REQUIRED", "error": str(exc), "quantity_changes": 0}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
