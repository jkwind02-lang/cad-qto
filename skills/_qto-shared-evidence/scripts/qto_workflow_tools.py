"""Run registered QTO tools with artifact references, never shell commands.

The registry describes existing CLI contracts; it contains no engineering
defaults. Outputs are staged, checked and published only after successful runs.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

SKILLS = Path(__file__).resolve().parents[2]
SHARED = SKILLS / "_qto-shared-evidence" / "scripts"


def spec(owner, script, inputs, outputs, *, positional=(), optional=(), prefix=(),
         stdout=None, values=None, preserve=False):
    return {"owner_skill": owner, "script": script, "inputs": inputs, "outputs": outputs,
            "positional": positional, "optional": optional, "prefix": prefix,
            "stdout": stdout, "values": values or {}, "preserve_outputs": preserve}


TOOLS = {
    "pipe_infer": spec("qto-pipe-networks", "infer_pipe_network.py", ["input"], ["output"]),
    "component_count": spec("qto-components-points", "count_reconciled_instances.py", ["input"], ["output"], positional=["input"], stdout="output"),
    "endpoint_handoff": spec("qto-components-points", "build_network_endpoint_handoff.py", ["input"], ["output"]),
    "duct_reference": spec("qto-duct-air-systems", "calculate_straight_duct_reference.py", ["input"], ["output"], positional=["input"], stdout="output"),
    "electrical_paths": spec("qto-electrical-routes", "calculate_electrical_paths.py", ["input"], ["output"], positional=["input"]),
    "concrete_formwork": spec("qto-concrete-formwork", "calculate_concrete_formwork_pairs.py", ["manifest"], ["evidence-out", "detail-csv", "result-out"]),
    "rebar": spec("qto-rebar", "calculate_rebar_items.py", ["manifest"], ["result-out", "evidence-out", "detail-out"], positional=["manifest"]),
    "structural_steel": spec("qto-structural-steel", "calculate_structural_steel_items.py", ["manifest"], ["evidence-out", "detail-csv", "result-out"]),
    "masonry_reference": spec("qto-masonry-secondary", "calculate_masonry_reference.py", ["input"], ["output"], positional=["input"]),
    "surfaces_envelope": spec("qto-surfaces-envelope", "calculate_surfaces_envelope.py", ["manifest"], ["output"]),
    "framing_reference": spec("qto-framing-supports", "calculate_framing_reference.py", ["input"], ["output"], positional=["input"], stdout="output"),
    "earthwork_terrain": spec("qto-earthwork-terrain", "calculate_earthwork_terrain.py", ["manifest"], ["output"]),
    "piles_reference": spec("qto-piles-groundwork", "calculate_pile_groups_reference.py", ["input"], ["output"], positional=["input"], stdout="output"),
    "roads_reference": spec("qto-roads-siteworks", "calculate_road_layers_reference.py", ["input"], ["output"], positional=["input"], stdout="output"),
    "cad_index": spec("_qto-shared-evidence", "qto_recognition_workbench.py", ["source"], ["output"], prefix=["index"], values={"layout": "text", "bounds": "bounds"}),
    "cad_view": spec("_qto-shared-evidence", "render_cad_view.py", ["source", "query"], ["svg", "png", "output"], values={"pixels": "positive_integer"}),
    "cad_scope": spec("_qto-shared-evidence", "read_cad_scope.py", ["index", "query"], ["output"], values={"cursor": "text", "mode": "mode", "max-objects": "positive_integer", "max-bytes": "positive_integer"}),
    "pipe_prepare": spec("_qto-shared-evidence", "qto_recognition_workbench.py", ["index", "operations", "ledger"], ["output"], prefix=["apply"]),
    "correspondence": spec("_qto-shared-evidence", "qto_object_correspondence.py", ["input"], ["output"]),
    "instance_requirements": spec("_qto-shared-evidence", "qto_instance_requirements.py", ["input", "previous"], ["output"], optional=["previous"]),
    "dependency_recalculate": spec("_qto-shared-evidence", "recompute_qto_dependencies.py", ["input", "previous"], ["output"], optional=["previous"]),
    "review_html": spec("_qto-shared-evidence", "build_qto_review_html.py", ["input", "feedback"], ["output"], optional=["feedback"], preserve=True),
    "review_feedback": spec("_qto-shared-evidence", "read_qto_review_feedback.py", ["view", "feedback"], ["output"], preserve=True),
}


def tool_path(tool_id):
    if tool_id not in TOOLS:
        raise ValueError("未登记的工具：" + str(tool_id))
    tool = TOOLS[tool_id]
    path = SKILLS / tool["owner_skill"] / "scripts" / tool["script"]
    if not path.is_file():
        raise ValueError("当前安装缺少运行工具：" + str(path))
    return path


def fingerprints(tool_ids=None, *, owner_skills=()):
    """Conservative dependency closure: owner scripts + shared runtime resources.

    Shared changes invalidate program jobs conservatively. This does not claim
    static analysis of Python imports or object-level semantic change detection.
    """
    shared_files = sorted(p for p in (SKILLS / "_qto-shared-evidence").rglob("*")
                          if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"})
    shared = [(p.relative_to(SKILLS).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest()) for p in shared_files]
    plugin = SKILLS.parent / ".codex-plugin/plugin.json"
    if plugin.is_file():
        shared.append((".codex-plugin/plugin.json", hashlib.sha256(plugin.read_bytes()).hexdigest()))
    runner = SKILLS / "qto-coordinator/scripts/qto_workflow.py"
    if runner.is_file():
        shared.append(("qto-coordinator/scripts/qto_workflow.py", hashlib.sha256(runner.read_bytes()).hexdigest()))
    answer = {}
    for key in sorted(TOOLS if tool_ids is None else set(tool_ids)):
        path = tool_path(key)
        own = [(p.relative_to(SKILLS).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
               for p in sorted(path.parent.glob("*.py"))]
        raw = json.dumps([TOOLS[key], shared, own], sort_keys=True, ensure_ascii=False).encode("utf-8")
        answer[key] = hashlib.sha256(raw).hexdigest()
    allowed_owners = {row["owner_skill"] for row in TOOLS.values()} | {"qto-coordinator"}
    for owner in sorted(set(owner_skills)):
        if owner not in allowed_owners:
            raise ValueError("工作包须使用当前已安装的专业或协调入口：" + str(owner))
        own = [(p.relative_to(SKILLS).as_posix(), hashlib.sha256(p.read_bytes()).hexdigest())
               for p in sorted((SKILLS / owner).rglob("*"))
               if p.is_file() and "__pycache__" not in p.parts and p.suffix not in {".pyc", ".pyo"}]
        raw = json.dumps([owner, shared, own], sort_keys=True, ensure_ascii=False).encode("utf-8")
        answer["model:" + owner] = hashlib.sha256(raw).hexdigest()
    return answer


def bindings(task):
    action = task.get("action") or {}
    key = action.get("tool")
    tool_path(key)
    tool = TOOLS[key]
    args = action.get("arguments", {})
    if not isinstance(args, dict):
        raise ValueError("action.arguments 必须是字段到产物编号的映射")
    allowed = set(tool["inputs"] + tool["outputs"]) | set(tool["values"])
    if set(args) - allowed:
        raise ValueError("工具参数未登记：" + ", ".join(sorted(set(args) - allowed)))
    required = set(tool["inputs"] + tool["outputs"]) - set(tool["optional"])
    if required - set(args):
        raise ValueError("缺少工具参数：" + ", ".join(sorted(required - set(args))))
    for field in tool["inputs"]:
        if field in args and args[field] not in task.get("input_ids", []):
            raise ValueError(field + " 必须引用本任务 input_ids 中的实际产物")
    for field in tool["outputs"]:
        if args[field] not in task.get("output_ids", []):
            raise ValueError(field + " 必须引用本任务 output_ids 中的产物")
    if len({args[k] for k in tool["outputs"]}) != len(tool["outputs"]):
        raise ValueError("工具的多个输出不能指向同一产物")
    if set(args[k] for k in tool["outputs"]) != set(task.get("output_ids", [])):
        raise ValueError("任务全部 output_ids 必须由登记工具实际产生")
    for field, kind in tool["values"].items():
        if field not in args:
            continue
        value = args[field]
        if kind == "positive_integer":
            valid = type(value) is int and value > 0
        elif kind == "bounds":
            valid = (isinstance(value, list) and len(value) == 4 and
                     all(type(x) in (int, float) and math.isfinite(x) for x in value) and
                     value[2] > value[0] and value[3] > value[1])
        elif kind == "mode":
            valid = value in ("summary", "detail")
        else:
            valid = isinstance(value, str) and bool(value.strip()) and "\0" not in value
        if not valid:
            raise ValueError("工具参数无效：" + field)
    return tool, args


def _inside(path, root):
    return path == root or root in path.parents


def execute(task, artifacts, root, *, before_publish=None, protected_paths=(), timeout=300):
    """Execute one known CLI. before_publish must recheck task/input identity.

    Returns execution evidence, not a completed task receipt. The caller records
    the receipt only after verifying actual final outputs and dependencies again.
    """
    root = Path(root).resolve()
    tool, args = bindings(task)
    key = task["action"]["tool"]
    outputs = {args[k]: Path(artifacts[args[k]]["path"]).resolve() for k in tool["outputs"]}
    protected = {Path(p).resolve() for p in protected_paths}
    inputs = {Path(v["path"]).resolve() for aid, v in artifacts.items() if aid not in outputs}
    for path in outputs.values():
        if not _inside(path, root) or path == root or path in inputs or path in protected:
            raise ValueError("输出须在项目工作目录内且不能覆盖输入/工作台账：" + str(path))
        if path.is_dir():
            raise ValueError("运行输出必须是文件")
        if tool["preserve_outputs"] and path.exists():
            raise ValueError("复核产物须使用新版本路径，以保留批注：" + str(path))
    run_root = root / ".qto-runs"
    if not _inside(run_root.resolve(), root):
        raise ValueError("运行目录不能通过符号链接指向项目外")
    run_root.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix="run-", dir=run_root))
    staged = {aid: folder / (str(i) + "-" + path.name) for i, (aid, path) in enumerate(outputs.items())}
    # Leases are by actual destination, shared even by independent state files.
    leases = []
    try:
        for path in sorted(outputs.values()):
            lock = run_root / ("output-" + hashlib.sha256(str(path).encode()).hexdigest() + ".lock")
            with lock.open("x", encoding="utf-8") as stream:
                stream.write(str(folder))
            leases.append(lock)
        command = [sys.executable, "-B", str(tool_path(key)), *tool["prefix"]]
        for field in [*tool["inputs"], *tool["outputs"], *tool["values"]]:
            if field not in args or field == tool["stdout"]:
                continue
            if field in tool["inputs"]:
                value = str(artifacts[args[field]]["path"])
                if key in {"cad_index", "cad_view"} and field == "source" and Path(value).suffix.lower() == ".dxf":
                    binding = folder / "source-binding.json"
                    binding.write_text(json.dumps({"path": value, "sha256": artifacts[args[field]]["sha256"]}, ensure_ascii=False), encoding="utf-8")
                    value = str(binding)
            elif field in tool["outputs"]:
                value = str(staged[args[field]])
            else:
                value = args[field]
            if field not in tool["positional"]:
                command.append("--" + field)
            command.extend(str(v) for v in value) if isinstance(value, list) else command.append(str(value))
        if key == "cad_scope":
            command += ["--artifact-dir", str(folder / "evidence")]
        stdout_path = folder / "stdout.log"
        stderr_path = folder / "stderr.log"
        try:
            with stdout_path.open("wb") as out, stderr_path.open("wb") as err:
                run = subprocess.run(command, cwd=root, stdout=out, stderr=err, timeout=timeout, check=False)
        except subprocess.TimeoutExpired as exc:
            raise ValueError(f"工具 {key} 超时；输出未发布，运行记录 {folder}") from exc
        if run.returncode != 0:
            tail = (stderr_path.read_text(encoding="utf-8", errors="replace") or
                    stdout_path.read_text(encoding="utf-8", errors="replace"))[-4000:]
            raise ValueError(f"工具 {key} 返回 {run.returncode}；运行记录 {folder}；{tail}")
        if tool["stdout"]:
            staged[args[tool["stdout"]]].write_bytes(stdout_path.read_bytes())
        for aid, path in staged.items():
            if not path.is_file() or path.stat().st_size == 0:
                raise ValueError("工具未产生非空输出：" + aid)
            if outputs[aid].suffix.lower() == ".json":
                def invalid(value):
                    raise ValueError("JSON 输出含非有限数值：" + value)
                json.loads(path.read_text(encoding="utf-8-sig"), parse_constant=invalid)
        execution_files = {}
        for path in folder.rglob("*"):
            if path.is_file() and path not in {stdout_path, stderr_path, folder / "source-binding.json"}:
                if not _inside(path.resolve(), folder):
                    raise ValueError("运行附属证据不得指向本次运行目录外")
                execution_files[str(path.resolve())] = hashlib.sha256(path.read_bytes()).hexdigest()
        if before_publish:
            before_publish()
        for aid, path in outputs.items():
            if path.resolve() != path:
                raise ValueError("运行期间输出目录发生变化；输出未发布")
            actual_hash = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            if actual_hash != artifacts[aid].get("sha256"):
                raise ValueError("运行期间输出被其他分支或用户修改；保留当前文件并重新对应：" + aid)
        for aid, path in outputs.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                archive = folder / "previous" / staged[aid].name
                archive.parent.mkdir(exist_ok=True)
                shutil.copy2(path, archive)
            # Some native evidence files bind sibling outputs by absolute path
            # and hash. Keep immutable run copies so those references survive
            # publication and later revisions of the user-facing output path.
            fd, temporary = tempfile.mkstemp(prefix=".qto-publish-", dir=path.parent)
            os.close(fd)
            try:
                shutil.copyfile(staged[aid], temporary)
                os.replace(temporary, path)
            finally:
                Path(temporary).unlink(missing_ok=True)
        return {"tool": key, "returncode": run.returncode, "run_directory": str(folder),
                "execution_files": execution_files,
                "outputs": {aid: str(path) for aid, path in outputs.items()},
                "stdout": str(stdout_path), "stderr": str(stderr_path), "quantity_adoption_changed": False}
    finally:
        for lock in leases:
            lock.unlink(missing_ok=True)
