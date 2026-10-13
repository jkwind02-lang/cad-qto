"""Resume evidence-bound QTO tasks without turning progress into adoption.

This is a file and task execution ledger, not another quantity/formula graph.
Receipts prove which real files and declared task contract were checkpointed;
they do not prove that a model's interpretation or an engineering result is true.
The caller owns tool registration, execution, permissions and atomic state writes.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from collections import defaultdict, deque
from datetime import datetime, timezone
from pathlib import Path


SCHEMA = "qto-project-workflow/1.0"
STATE_SCHEMA = "qto-project-workflow-state/1.0"
STAGES = {"read", "recognize", "calculate", "reconcile", "review", "deliver"}
ACTORS = {"model", "program", "user"}
MODES = {"production", "independent_assessment"}
READ_ONLY_KINDS = {
    "source", "rule", "source_input", "source_drawing", "source_document", "rule_source",
}
# These lists describe closure and discovered inventory, not prerequisites of
# every task in the scope. In particular an index must not depend on itself.
SCOPE_NON_INPUT_FIELDS = {
    "inventory_artifact_ids", "required_task_ids", "status", "completed",
    "updated_at", "progress", "review_state",
}


def _text(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(label + " must be a nonempty string")
    return value


def _digest(value):
    try:
        encoded = json.dumps(value, sort_keys=True, ensure_ascii=False,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("workflow values must be finite JSON data") from exc
    return hashlib.sha256(encoded).hexdigest()


def _sha(value, label):
    if (not isinstance(value, str) or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)):
        raise ValueError(label + " must be a lowercase SHA-256")
    return value


def _execution_files_contract(value):
    if not isinstance(value, dict):
        raise ValueError("execution_files must map absolute paths to SHA-256 values")
    for path, sha in value.items():
        _text(path, "execution evidence path")
        if "\x00" in path or not Path(path).is_absolute():
            raise ValueError("execution evidence requires absolute file paths")
        _sha(sha, "execution evidence SHA-256")
    return value


def _ids(values, label):
    if not isinstance(values, list):
        raise ValueError(label + " must be an array")
    for value in values:
        _text(value, label + " reference")
    if len(set(values)) != len(values):
        raise ValueError(label + " contains duplicate references")
    return values


def _rows(values, label):
    if not isinstance(values, list):
        raise ValueError(label + " must be an array")
    result = {}
    for row in values:
        if not isinstance(row, dict):
            raise ValueError(label + " rows must be objects")
        key = _text(row.get("id"), label + ".id")
        if key in result:
            raise ValueError(label + " has duplicate id: " + key)
        result[key] = row
    return result


def _references(values, available, label):
    refs = _ids(values, label)
    missing = set(refs) - set(available)
    if missing:
        raise ValueError(label + " references unknown ids: " + ", ".join(sorted(missing)))
    return refs


def _identity(document):
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA:
        raise ValueError("unsupported project workflow schema_version")
    values = {name: _text(document.get(name), name)
              for name in ("project_id", "task_id", "revision_id", "task_mode")}
    if values["task_mode"] not in MODES:
        raise ValueError("task_mode must be production or independent_assessment")
    return values


def _plan(document):
    identity = _identity(document)
    _digest(document)  # Reject NaN, executable objects and other non-JSON inputs.
    artifacts = _rows(document.get("artifacts"), "artifacts")
    packages = _rows(document.get("work_packages"), "work_packages")
    scopes = _rows(document.get("scopes"), "scopes")
    tasks = _rows(document.get("tasks"), "tasks")
    for key, artifact in artifacts.items():
        _text(artifact.get("kind"), key + ".kind")
        if "read_only" in artifact and not isinstance(artifact["read_only"], bool):
            raise ValueError(key + ".read_only must be boolean")
        path = _text(artifact.get("path"), key + ".path")
        if "\x00" in path:
            raise ValueError(key + ".path contains NUL")
    for key, package in packages.items():
        _text(package.get("owner_skill"), key + ".owner_skill")
        _references(package.get("scope_ids", []), scopes, key + ".scope_ids")
    for key, scope in scopes.items():
        _text(scope.get("title"), key + ".title")
        for field in ("basis_refs", "inventory_artifact_ids"):
            _references(scope.get(field, []), artifacts, key + "." + field)
        _references(scope.get("required_task_ids", []), tasks, key + ".required_task_ids")

    producers = {}
    inputs = {}
    for key, task in tasks.items():
        _text(task.get("title"), key + ".title")
        if task.get("work_package_id") not in packages:
            raise ValueError(key + " references an unknown work_package_id")
        if task.get("stage") not in STAGES or task.get("actor") not in ACTORS:
            raise ValueError(key + " has an invalid stage or actor")
        direct = _references(task.get("input_ids", []), artifacts, key + ".input_ids")
        outputs = _references(task.get("output_ids", []), artifacts, key + ".output_ids")
        _references(task.get("depends_on", []), tasks, key + ".depends_on")
        for aid in outputs:
            kind = artifacts[aid]["kind"].lower().replace("-", "_")
            if kind in READ_ONLY_KINDS or artifacts[aid].get("read_only") is True:
                raise ValueError(key + " cannot write a source/rule/read-only artifact: " + aid)
            if aid in producers:
                raise ValueError("duplicate producer for artifact " + aid + ": " + producers[aid] + ", " + key)
            producers[aid] = key
        basis = set()
        for scope_id in packages[task["work_package_id"]].get("scope_ids", []):
            basis.update(scopes[scope_id].get("basis_refs", []))
        # A task may create scope evidence; hash its output separately, never
        # smuggle its bytes into its own pre-execution fingerprint.
        inputs[key] = set(direct) | (basis - set(outputs))
        action = task.get("action")
        if action is not None:
            if not isinstance(action, dict):
                raise ValueError(key + ".action must be an object")
            _text(action.get("tool"), key + ".action.tool")
            args = action.get("arguments", {})
            if not isinstance(args, dict) or any(not isinstance(k, str) or not k for k in args):
                raise ValueError(key + ".action.arguments must have nonempty string keys")
            for value in args.values():
                if isinstance(value, str) and value in artifacts and value not in inputs[key] | set(outputs):
                    raise ValueError(key + " action references an undeclared input/output artifact: " + value)

    dependencies = {}
    children = defaultdict(list)
    degree = {}
    for key, task in tasks.items():
        deps = set(task.get("depends_on", []))
        deps.update(producers[aid] for aid in inputs[key] if aid in producers)
        if key in deps:
            raise ValueError("task dependency cycle/self input: " + key)
        dependencies[key] = sorted(deps)
        degree[key] = len(deps)
        for dep in deps:
            children[dep].append(key)
    queue = deque(sorted(key for key, count in degree.items() if count == 0))
    order = []
    while queue:
        key = queue.popleft()
        order.append(key)
        for child in sorted(children[key]):
            degree[child] -= 1
            if degree[child] == 0:
                queue.append(child)
    if len(order) != len(tasks):
        raise ValueError("task dependency cycle: " + ", ".join(sorted(k for k in tasks if degree[k])))
    return {"identity": identity, "artifacts": artifacts, "packages": packages,
            "scopes": scopes, "tasks": tasks, "producers": producers,
            "inputs": inputs, "dependencies": dependencies, "order": order}


def _receipt_id(receipt):
    return _digest({key: value for key, value in receipt.items() if key != "receipt_id"})


def _validate_state(plan, state):
    if not isinstance(state, dict) or state.get("schema_version") != STATE_SCHEMA:
        raise ValueError("unsupported workflow state schema_version")
    for key in ("project_id", "task_id", "task_mode"):
        if state.get(key) != plan["identity"][key]:
            raise ValueError("state " + key + " differs; a new task cannot inherit another task's receipts")
    _text(state.get("revision_id"), "state.revision_id")
    if state.get("business_adoption_allowed") is not False:
        raise ValueError("task progress must not authorize business adoption")
    _digest(state)
    receipts = state.get("receipts")
    if not isinstance(receipts, list):
        raise ValueError("state.receipts must be an array")
    by_id = {}
    for row in receipts:
        if not isinstance(row, dict):
            raise ValueError("receipt must be an object")
        rid = _sha(row.get("receipt_id"), "receipt.receipt_id")
        if rid in by_id or rid != _receipt_id(row):
            raise ValueError("duplicate or modified receipt: " + rid)
        for field, key in (("project_id", "project_id"), ("workflow_task_id", "task_id"), ("task_mode", "task_mode")):
            if row.get(field) != plan["identity"][key]:
                raise ValueError("receipt belongs to another project/task/mode")
        _text(row.get("task_id"), "receipt.task_id")
        _sha(row.get("task_fingerprint"), "receipt.task_fingerprint")
        if row.get("actor") not in ACTORS or row.get("actor") != row.get("actor_role"):
            raise ValueError("receipt actor must match its declared task role")
        _text(row.get("note"), "receipt.note")
        _text(row.get("recorded_at"), "receipt.recorded_at")
        if row.get("actor_role") not in ACTORS or row.get("business_adoption_allowed") is not False:
            raise ValueError("invalid receipt actor_role/adoption boundary")
        for label in ("inputs", "outputs"):
            files = row.get(label)
            if not isinstance(files, dict) or (label == "outputs" and not files):
                raise ValueError("receipt must record real output files and input identities")
            for aid, file in files.items():
                _text(aid, "receipt artifact id")
                if not isinstance(file, dict):
                    raise ValueError("receipt file must be an object")
                _text(file.get("path"), "receipt file path")
                _sha(file.get("sha256"), "receipt file SHA-256")
        if "execution_files" in row:
            _execution_files_contract(row["execution_files"])
            if row["execution_files"] and row["actor_role"] != "program":
                raise ValueError("execution evidence is only valid for a program receipt")
        by_id[rid] = row
    for row in receipts:
        prior = row.get("previous_receipt_id")
        if prior is not None and (prior not in by_id or by_id[prior]["task_id"] != row["task_id"]):
            raise ValueError("receipt predecessor is missing or belongs to another task")
    heads = state.get("task_receipts")
    if not isinstance(heads, dict):
        raise ValueError("state.task_receipts must be an object")
    for task_id, rid in heads.items():
        if rid not in by_id or by_id[rid]["task_id"] != task_id:
            raise ValueError("invalid active task receipt: " + str(task_id))
    attempts = state.get("attempts", [])
    if not isinstance(attempts, list):
        raise ValueError("state.attempts must be an array")
    for attempt in attempts:
        if not isinstance(attempt, dict):
            raise ValueError("attempt must be an object")
        _text(attempt.get("task_id"), "attempt.task_id")
        _sha(attempt.get("task_fingerprint"), "attempt.task_fingerprint")
        _text(attempt.get("tool"), "attempt.tool")
        _text(attempt.get("recorded_at"), "attempt.recorded_at")
        if not isinstance(attempt.get("success"), bool):
            raise ValueError("attempt.success must be boolean")
        if attempt["success"] is False:
            _text(attempt.get("error"), "attempt.error")
    return by_id


def _file_snapshot(plan, root):
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError("workflow root must be an existing directory")
    resolved = {}
    paths = {}
    inodes = {}
    for aid, artifact in plan["artifacts"].items():
        path = Path(artifact["path"])
        try:
            path = (path if path.is_absolute() else root / path).resolve()
        except (OSError, RuntimeError) as exc:
            raise ValueError("invalid artifact path " + aid + ": " + str(exc)) from exc
        path_key = os.path.normcase(str(path))
        if path_key in paths:
            raise ValueError("duplicate resolved artifact paths: " + paths[path_key] + ", " + aid)
        paths[path_key] = aid
        row = {"path": str(path), "exists": False, "sha256": None,
               "kind": artifact["kind"], "file_status": "missing", "reason": None}
        try:
            stat = path.stat()
            row["exists"] = True
            if not path.is_file():
                row.update(file_status="invalid", reason="artifact is not a regular file")
            else:
                inode = (stat.st_dev, stat.st_ino)
                # Also prevent an output hard link from overwriting an input
                # under another path; resolve() alone cannot catch hard links.
                if stat.st_ino and inode in inodes:
                    raise ValueError("artifact paths alias the same file: " + inodes[inode] + ", " + aid)
                inodes[inode] = aid
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    before = os.fstat(stream.fileno())
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                    after = os.fstat(stream.fileno())
                current = path.stat()
                # On Windows stat(path) and fstat(fd) may expose different
                # meanings of ctime (creation vs metadata change time). Compare
                # ctime within each API, not across them. File identity, size
                # and mtime still have to agree across all four observations:
                # this also rejects a path replacement around the open handle.
                identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
                stable_identity = len({identity(s) for s in (stat, before, after, current)}) == 1
                stable_path = stat.st_ctime_ns == current.st_ctime_ns
                stable_handle = before.st_ctime_ns == after.st_ctime_ns
                if not (stable_identity and stable_path and stable_handle):
                    row.update(file_status="invalid", reason="artifact changed while hashing")
                else:
                    row.update(file_status="present", sha256=digest.hexdigest())
        except FileNotFoundError:
            row.update(exists=False, file_status="missing", reason="artifact file is missing")
        except OSError as exc:
            row.update(file_status="invalid", reason=str(exc))
        resolved[aid] = row
    return resolved


def _file_identity(row):
    return {key: row[key] for key in ("path", "sha256")}


def _file_contract(aid, plan, files, content=True):
    row = {"id": aid, "kind": plan["artifacts"][aid]["kind"], "path": files[aid]["path"]}
    if content:
        row.update(sha256=files[aid]["sha256"], file_status=files[aid]["file_status"])
    return row


def _inspect_execution_files(expected, root, input_paths):
    """Check immutable run evidence without registering it as a task input.

    Only the runner's local run directories qualify. In particular this cannot
    be used to bless an undeclared input or relabel a consumed source as output
    evidence. Other tasks may later register a generated sidecar as their input.
    """
    _execution_files_contract(expected)
    if not expected:
        return {}, []
    root = Path(root).resolve()
    run_root = root / ".qto-runs"
    inputs = {Path(path).resolve() for path in input_paths}
    input_inodes = set()
    for path in inputs:
        try:
            stat = path.stat()
            if stat.st_ino:
                input_inodes.add((stat.st_dev, stat.st_ino))
        except OSError:
            pass  # Missing actual inputs are already blocked by task inspection.
    declarations, reasons = {}, []
    for name in expected:
        path = Path(name)
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(run_root)
            if (resolved != path or len(relative.parts) < 2 or
                    not relative.parts[0].startswith("run-") or len(relative.parts[0]) <= 4):
                raise ValueError("not a canonical runner output path")
            if resolved in inputs:
                raise ValueError("a consumed input cannot be execution evidence")
            if path.exists():
                stat = path.stat()
                if stat.st_ino and (stat.st_dev, stat.st_ino) in input_inodes:
                    raise ValueError("execution evidence aliases a consumed input")
            declarations[name] = {"kind": "execution_evidence", "path": name}
        except (OSError, RuntimeError, ValueError) as exc:
            reasons.append("invalid execution evidence " + name + ": " + str(exc))
    try:
        files = _file_snapshot({"artifacts": declarations}, root)
    except ValueError as exc:
        return {}, [*reasons, "invalid execution evidence files: " + str(exc)]
    for name, row in files.items():
        if row["file_status"] != "present":
            reasons.append("execution evidence is unavailable: " + name)
        elif row["sha256"] != expected[name]:
            reasons.append("execution evidence was modified: " + name)
    return files, reasons


def _tool_versions(tool_fingerprints):
    if tool_fingerprints is None:
        return {}
    if not isinstance(tool_fingerprints, dict):
        raise ValueError("tool_fingerprints must be an object")
    for key, value in tool_fingerprints.items():
        _text(key, "tool id")
        _sha(value, "tool fingerprint")
    return tool_fingerprints


def _inspect(plan, state, root, tool_fingerprints):
    receipts = _validate_state(plan, state)
    tools = _tool_versions(tool_fingerprints)
    files = _file_snapshot(plan, root)
    tasks = {}
    history = defaultdict(list)
    for receipt in state["receipts"]:
        history[receipt["task_id"]].append(receipt["receipt_id"])
    for key in plan["order"]:
        task = plan["tasks"][key]
        package = plan["packages"][task["work_package_id"]]
        scopes = {sid: {k: v for k, v in plan["scopes"][sid].items() if k not in SCOPE_NON_INPUT_FIELDS}
                  for sid in sorted(package.get("scope_ids", []))}
        deps = plan["dependencies"][key]
        outputs = task.get("output_ids", [])
        tool = (task.get("action") or {}).get("tool") if task["actor"] == "program" else None
        signature = {
            "workflow_identity": {k: plan["identity"][k] for k in ("project_id", "task_id", "task_mode")},
            "task_contract": task, "work_package": package, "scope_declarations": scopes,
            "inputs": {aid: _file_contract(aid, plan, files) for aid in sorted(plan["inputs"][key])},
            "outputs": {aid: _file_contract(aid, plan, files, content=False) for aid in sorted(outputs)},
            "dependencies": {dep: {"fingerprint": tasks[dep]["fingerprint"],
                "outputs": {aid: _file_contract(aid, plan, files)
                            for aid in plan["tasks"][dep].get("output_ids", [])}} for dep in deps},
            "tool": {"id": tool, "sha256": tools.get(tool)} if tool else None,
            "model_runtime": tools.get("model:" + package["owner_skill"]) if task["actor"] == "model" else None,
        }
        fingerprint = _digest(signature)
        rid = state["task_receipts"].get(key)
        receipt = receipts.get(rid)
        blockers = []
        wait_for = [dep for dep in deps if tasks[dep]["status"] != "completed"]
        if not outputs:
            blockers.append("task has no real output artifact to checkpoint")
        if tool and tool not in tools:
            blockers.append("registered tool fingerprint is missing: " + tool)
        for aid in sorted(plan["inputs"][key]):
            if files[aid]["file_status"] != "present" and aid not in plan["producers"]:
                blockers.append("input " + aid + ": " + (files[aid]["reason"] or files[aid]["file_status"]))
        for aid in outputs:
            if files[aid]["file_status"] == "invalid":
                blockers.append("output " + aid + ": " + files[aid]["reason"])
        reasons = list(blockers)
        if wait_for:
            reasons.append("dependencies are not completed: " + ", ".join(wait_for))
        execution_files = {}
        if receipt:
            if receipt["task_fingerprint"] != fingerprint:
                reasons.append("task contract, actual inputs, scope, rule or tool version changed")
            if set(receipt["outputs"]) != set(outputs):
                reasons.append("checkpoint output set changed")
            for aid in outputs:
                if files[aid]["file_status"] != "present":
                    reasons.append("checkpoint output is unavailable: " + aid)
                elif receipt["outputs"].get(aid) != _file_identity(files[aid]):
                    reasons.append("checkpoint output was modified or relocated: " + aid)
            execution_files, execution_errors = _inspect_execution_files(
                receipt.get("execution_files", {}), root,
                [files[aid]["path"] for aid in plan["inputs"][key]])
            reasons.extend(execution_errors)
            status = "stale" if reasons else "completed"
        elif history.get(key):
            reasons.append("historical receipt was not accepted as current progress")
            status = "stale"
        elif blockers or any(tasks[dep]["status"] == "blocked" for dep in wait_for):
            status = "blocked"
        elif wait_for:
            status = "pending"
        else:
            status = "ready"
        can_run = not blockers and not wait_for and status != "completed"
        row = {"status": status, "fingerprint": fingerprint, "reasons": reasons,
               "depends_on": deps, "input_ids": sorted(plan["inputs"][key]),
               "output_ids": list(outputs), "can_run": can_run,
               "can_checkpoint": not blockers and not wait_for,
               "current_receipt_id": rid if status == "completed" else None,
               "previous_receipt_id": rid, "historical_receipt_ids": history.get(key, [])}
        if receipt and receipt.get("execution_files"):
            row["execution_files"] = execution_files
        matching = [a for a in state.get("attempts", [])
                    if a["task_id"] == key and a["task_fingerprint"] == fingerprint]
        if matching:
            latest = max(matching, key=lambda a: (a["recorded_at"], _digest(a)))
            row["last_attempt"] = copy.deepcopy(latest)
            if latest["success"] is False and (not receipt or latest["recorded_at"] > receipt["recorded_at"]):
                row["last_error"] = latest["error"]
        tasks[key] = row
    for aid, file in files.items():
        producer = plan["producers"].get(aid)
        file["producer_task_id"] = producer
        if file["file_status"] == "invalid":
            file["status"] = "invalid"
        elif file["file_status"] == "missing":
            file["status"] = "missing"
        else:
            file["status"] = "current" if producer is None or tasks[producer]["status"] == "completed" else "stale"
    statuses = {key: value["status"] for key, value in tasks.items()}
    return {"schema_version": "qto-project-workflow-inspection/1.0", **plan["identity"],
            "task_statuses": statuses, "tasks": tasks, "artifacts": files,
            "ready_task_ids": [k for k, v in statuses.items() if v == "ready"],
            "stale_task_ids": [k for k, v in statuses.items() if v == "stale"],
            "blocked_task_ids": [k for k, v in statuses.items() if v == "blocked"],
            "runnable_task_ids": [k for k, v in tasks.items() if v["can_run"]],
            "historical_receipt_count": len(state["receipts"]),
            "removed_task_ids": sorted(set(history) - set(tasks)),
            "business_adoption_allowed": False}


def initial_state(document):
    """Create a new task identity; completed flags in a document carry no trust."""
    plan = _plan(document)
    return {"schema_version": STATE_SCHEMA, **plan["identity"], "receipts": [],
            "task_receipts": {}, "attempts": [], "business_adoption_allowed": False}


def inspect_workflow(document, state, root, tool_fingerprints=None):
    """Read actual files and classify tasks; never mutate the document or state."""
    return _inspect(_plan(document), state, root, tool_fingerprints)


def checkpoint(document, state, task_id, root, actor, note,
               tool_fingerprints=None, expected_task_fingerprint=None, execution_files=None):
    """Record real output identities after dependencies and inputs are rechecked.

    actor must match the declared role; it is not an authenticated approval.
    expected_task_fingerprint should be captured immediately before a program is
    run, so changed input bytes during that run cannot receive a fresh receipt.
    execution_files binds actual immutable files retained by the runner. A
    program checkpoint that omits it preserves its previous binding; a new run
    supplies its own path/hash map. These files never replace declared inputs.
    """
    actor = _text(actor, "checkpoint actor")
    note = _text(note, "checkpoint note")
    plan = _plan(document)
    if task_id not in plan["tasks"]:
        raise ValueError("unknown checkpoint task: " + str(task_id))
    if actor not in ACTORS or actor != plan["tasks"][task_id]["actor"]:
        raise ValueError("checkpoint actor must match the task's declared actor role")
    inspection = _inspect(plan, state, root, tool_fingerprints)
    task = inspection["tasks"][task_id]
    if expected_task_fingerprint is not None:
        _sha(expected_task_fingerprint, "expected_task_fingerprint")
        if expected_task_fingerprint != task["fingerprint"]:
            raise ValueError("task inputs/contract changed during execution; expected task fingerprint differs")
    if not task["can_checkpoint"]:
        raise ValueError("cannot checkpoint " + task_id + ": " + "; ".join(task["reasons"]))
    files = inspection["artifacts"]
    for aid in task["input_ids"]:
        if files[aid]["status"] != "current":
            raise ValueError("checkpoint input is not current: " + aid)
    for aid in task["output_ids"]:
        if files[aid]["file_status"] != "present":
            raise ValueError("checkpoint requires an actual output file: " + aid)
    previous = next((r for r in state["receipts"]
                     if r["receipt_id"] == state["task_receipts"].get(task_id)), {})
    prior_execution = previous.get("execution_files", {})
    if actor == "program" and execution_files is None:
        execution_files = prior_execution
    if execution_files is not None:
        _execution_files_contract(execution_files)
        if actor != "program":
            raise ValueError("only a program checkpoint can bind execution_files")
        if prior_execution and not execution_files:
            raise ValueError("program checkpoint cannot discard previously bound execution evidence")
        _, execution_errors = _inspect_execution_files(
            execution_files, root, [files[aid]["path"] for aid in task["input_ids"]])
        if execution_errors:
            raise ValueError("; ".join(execution_errors))
    receipt = {
        "project_id": plan["identity"]["project_id"],
        "workflow_task_id": plan["identity"]["task_id"],
        "task_mode": plan["identity"]["task_mode"], "revision_id": plan["identity"]["revision_id"],
        "task_id": task_id, "task_fingerprint": task["fingerprint"],
        "previous_receipt_id": state["task_receipts"].get(task_id),
        "actor": actor, "actor_role": plan["tasks"][task_id]["actor"], "note": note,
        "recorded_at": datetime.now(timezone.utc).isoformat(timespec="microseconds"),
        "inputs": {aid: _file_identity(files[aid]) for aid in task["input_ids"]},
        "outputs": {aid: _file_identity(files[aid]) for aid in task["output_ids"]},
        "business_adoption_allowed": False,
    }
    if execution_files:
        receipt["execution_files"] = copy.deepcopy(execution_files)
    receipt["receipt_id"] = _receipt_id(receipt)
    result = copy.deepcopy(state)
    result["revision_id"] = plan["identity"]["revision_id"]
    result["receipts"].append(receipt)
    result["task_receipts"][task_id] = receipt["receipt_id"]
    return result


def _is_ancestor(ancestor, descendant, receipts):
    seen = set()
    while descendant is not None and descendant not in seen:
        if descendant == ancestor:
            return True
        seen.add(descendant)
        descendant = receipts[descendant].get("previous_receipt_id")
    return False


def merge_states(document, left, right, root, tool_fingerprints=None):
    """Merge independent branch progress, preserving rejected receipts as history.

    Divergent checkpoints of the same task are a conflict, even if one branch's
    output has since overwritten the other. Fast-forward receipt ancestry is OK.
    Only receipts still valid against actual files become active merged progress.
    """
    plan = _plan(document)
    left_receipts = _validate_state(plan, left)
    right_receipts = _validate_state(plan, right)
    all_receipts = {**left_receipts, **right_receipts}
    candidates = {}
    for key in set(left["task_receipts"]) | set(right["task_receipts"]):
        a, b = left["task_receipts"].get(key), right["task_receipts"].get(key)
        if a and b and a != b:
            if _is_ancestor(a, b, all_receipts):
                candidates[key] = b
            elif _is_ancestor(b, a, all_receipts):
                candidates[key] = a
            else:
                raise ValueError("parallel state conflict: divergent receipts for task " + key)
        else:
            candidates[key] = a or b
    result = initial_state(document)
    for key in (set(left) | set(right)) - set(result):
        if key in left and key in right and left[key] != right[key]:
            raise ValueError("parallel state extension conflict: " + key)
        result[key] = copy.deepcopy(left[key] if key in left else right[key])
    result["receipts"] = sorted(copy.deepcopy(list(all_receipts.values())),
                                key=lambda r: (r["recorded_at"], r["receipt_id"]))
    attempts = {_digest(a): a for a in left.get("attempts", []) + right.get("attempts", [])}
    result["attempts"] = sorted(copy.deepcopy(list(attempts.values())),
                                key=lambda a: (a["recorded_at"], a["task_id"], _digest(a)))
    result["task_receipts"] = candidates
    inspection = _inspect(plan, result, root, tool_fingerprints)
    result["task_receipts"] = {key: rid for key, rid in candidates.items()
                               if key in inspection["tasks"] and inspection["tasks"][key]["status"] == "completed"}
    return result
