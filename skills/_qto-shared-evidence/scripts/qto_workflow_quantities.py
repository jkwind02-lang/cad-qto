"""Reconcile drawing observations and expected quantities with native results.

This is a working ledger, not a calculator or an engineering completeness proof.
The model supplies scope, physical correspondence, ownership and measurement
meaning; existing specialist programs supply every quantity.  Numbers are read
from current, hash-checked JSON artifacts, never from a caller's claimed total.
Even a fully reconciled declared scope remains unreviewed and not formally
adopted.  No drawing identity or disjoint extent is inferred from a label.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import re

SCHEMA = "qto-project-workflow/1.0"
LEVELS = {"confirmed": 0, "inferred": 1, "scenario": 2}
LEVEL_ALIASES = {**{key: key for key in LEVELS},
                 "确认量": "confirmed", "规则推算量": "inferred", "方案暂估量": "scenario"}
ROLES = {"geometric", "project_rule", "procurement_reference"}
DISPOSITIONS = {"included", "excluded", "evidence", "container", "unresolved"}
IDENTITY_FIELDS = {"id", "physical_id", "成果编号", "成果项编号", "item_id", "object_id", "subject_id", "target_id", "构件编号"}
SEMANTIC_FIELDS = ("scope_id", "physical_id", "extent_id", "quantity_kind",
                   "measurement_basis_id", "scenario_id", "quantity_role", "layer_id", "side")
TOTAL_FIELDS = ("scope_id", "owner_skill", "unit", "quantity_kind", "measurement_basis_id",
                "scenario_id", "quantity_role", "quantity_level", "layer_id", "side")


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _records(document, field, with_id=True):
    rows = document.get(field, [])
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError(f"{field} 必须是对象数组")
    if with_id:
        ids = [row.get("id") for row in rows]
        if any(not _text(value) for value in ids):
            raise ValueError(f"{field} 每项必须有非空 id")
        if len(set(ids)) != len(ids):
            raise ValueError(f"{field} 存在重复 id，不能覆盖前一条记录")
    return rows


def _strings(value):
    return isinstance(value, list) and all(_text(item) for item in value) and len(set(value)) == len(value)


def _json_pairs(pairs):
    answer = {}
    for key, value in pairs:
        if key in answer:
            raise ValueError(f"JSON 有重复字段 {key!r}")
        answer[key] = value
    return answer


def _reject_constant(value):
    raise ValueError(f"JSON 含非有限常量 {value}")


class _Artifacts:
    """Read each file once per snapshot; declarations cannot manufacture hashes."""

    def __init__(self, declarations, observations):
        self.declarations = declarations
        self.observations = observations
        self.loaded = {}
        self.files = {}
        self.json_files = {}
        self.errors = {}

    def read(self, artifact_id, kind=None, as_json=False):
        if artifact_id not in self.declarations:
            raise ValueError(f"产物 {artifact_id!r} 未登记")
        declaration = self.declarations[artifact_id]
        if kind is not None and declaration.get("kind") != kind:
            raise ValueError(f"产物 {artifact_id} 应为 {kind}，实际为 {declaration.get('kind')!r}")
        if artifact_id in self.errors:
            raise ValueError(self.errors[artifact_id])
        if artifact_id not in self.loaded:
            try:
                observed = self.observations.get(artifact_id)
                if not isinstance(observed, dict):
                    raise ValueError("执行器没有观察到该产物")
                if observed.get("status") != "current" or observed.get("exists") is not True:
                    raise ValueError(f"产物不是 current：{observed.get('status', 'missing')}")
                path_value = observed.get("path")
                if not _text(path_value) or not Path(path_value).is_absolute():
                    raise ValueError("执行器须提供绝对文件路径")
                path = Path(path_value).resolve()
                declared_path = declaration.get("path")
                if not _text(declared_path):
                    raise ValueError("产物登记缺少 path")
                if Path(declared_path).is_absolute() and Path(declared_path).resolve() != path:
                    raise ValueError("执行器路径与产物登记不一致")
                digest = observed.get("sha256")
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                    raise ValueError("执行器未提供有效实际 SHA-256")
                digest = digest.lower()
                if path not in self.files:
                    raw = path.read_bytes()
                    self.files[path] = (raw, hashlib.sha256(raw).hexdigest())
                raw, actual = self.files[path]
                if actual != digest:
                    raise ValueError("文件已在执行器观察后变化，SHA-256 不一致")
                self.loaded[artifact_id] = {"path": str(path), "sha256": actual, "raw": raw}
            except (OSError, ValueError) as error:
                self.errors[artifact_id] = f"{artifact_id}：{error}"
                raise ValueError(self.errors[artifact_id]) from error
        loaded = self.loaded[artifact_id]
        if not as_json:
            return loaded
        key = (loaded["path"], loaded["sha256"])
        if key not in self.json_files:
            try:
                self.json_files[key] = json.loads(loaded["raw"].decode("utf-8-sig"),
                    parse_constant=_reject_constant, object_pairs_hook=_json_pairs)
            except (UnicodeError, ValueError) as error:
                self.errors[artifact_id] = f"{artifact_id}：不是有效有限 JSON：{error}"
                raise ValueError(self.errors[artifact_id]) from error
        return self.json_files[key]


def _pointer(document, pointer):
    """RFC 6901 lookup plus ancestors for conservative native unit/level checks."""
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        raise ValueError("result_pointer/unit_pointer/level_pointer 须为 JSON Pointer")
    tokens = [] if not pointer else pointer[1:].split("/")
    value, ancestors, decoded = document, [], []
    for raw in tokens:
        if re.search(r"~(?:[^01]|$)", raw):
            raise ValueError(f"JSON Pointer 转义无效：{pointer}")
        token = raw.replace("~1", "/").replace("~0", "~")
        ancestors.append(value)
        decoded.append(token)
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and re.fullmatch(r"0|[1-9][0-9]*", token):
            try:
                value = value[int(token)]
            except (IndexError, ValueError) as error:
                raise ValueError(f"JSON Pointer 数组位置不存在：{pointer}") from error
        else:
            raise ValueError(f"JSON Pointer 未找到实际结果：{pointer}")
    return value, ancestors, decoded


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _nearest(ancestors, key):
    for node in reversed(ancestors):
        if isinstance(node, dict) and key in node:
            return True, node[key]
    return False, None


class _BindingError(ValueError):
    """The model must re-associate an existing native row before using it."""


def _encoded_pointer(tokens):
    return "".join("/" + token.replace("~", "~0").replace("/", "~1") for token in tokens)


def _json_value(value):
    if value is None or isinstance(value, (str, bool)) or _finite_number(value):
        return True
    if isinstance(value, list):
        return all(_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _json_value(item) for key, item in value.items())
    return False


def _same_json(first, second):
    """Exact JSON comparison; bool is not a number and no tolerance hides edits."""
    if isinstance(first, dict) and isinstance(second, dict):
        return first.keys() == second.keys() and all(_same_json(first[key], second[key]) for key in first)
    if isinstance(first, list) and isinstance(second, list):
        return len(first) == len(second) and all(_same_json(a, b) for a, b in zip(first, second))
    if _finite_number(first) and _finite_number(second):
        return first == second
    return type(first) is type(second) and first == second


def _record_contract(native, row):
    """Known native rows have explicit minimal semantics, not guessed labels."""
    tokens = row["tokens"]
    if (isinstance(native, dict) and "unique_instance_count" in native
            and len(tokens) == 2 and tokens[0] == "groups"):
        return "component_count.groups", {"category", "system", "spec", "unit", "quantity_level"}
    if (isinstance(native, dict) and native.get("constant_width_only") is True
            and len(tokens) == 4 and tokens[0] == "items" and tokens[2] == "layers"):
        return "roads_reference.layers", {"name", "thickness_m"}
    # For other rows, the caller explicitly binds the complete direct context
    # outside the quantity branch.  A numerical value alone is not an identity.
    return "explicit_row_context", set(row["value"]) - {row["branch"]}


def _claim_identity(native, claim, ancestors, tokens):
    """Bind every array ancestor by stable IDs or explicit native row context."""
    array_rows = []
    for position, node in enumerate(ancestors):
        if not isinstance(node, list):
            continue
        child = node[int(tokens[position])]  # _pointer already checked the index.
        if not isinstance(child, dict):
            raise _BindingError("结果指针经过无对象身份的数组值，不能仅凭位置绑定数量；须输出带稳定身份/语义上下文的对象行或字典路径")
        array_rows.append(dict(tokens=tokens[:position + 1], value=child, siblings=node,
            branch=tokens[position + 1] if position + 1 < len(tokens) else None,
            identity_fields=IDENTITY_FIELDS & child.keys()))
    identified_rows = [row for row in array_rows if row["identity_fields"]]
    pointer = claim.get("identity_pointer")
    has_expected = "expected_identity" in claim
    supplied = claim.get("identity_bindings", [])
    if not isinstance(supplied, list) or any(not isinstance(row, dict) for row in supplied):
        raise _BindingError("identity_bindings 须为身份指针与预期身份的对象数组")
    bindings = list(supplied)
    if (pointer is not None) != has_expected:
        raise _BindingError("identity_pointer 与 expected_identity 必须同时提供")
    if pointer is not None:
        bindings.insert(0, dict(identity_pointer=pointer, expected_identity=claim["expected_identity"]))
    if identified_rows and not bindings:
        raise _BindingError("结果指针经过带身份编号的数组项，须提供 identity_pointer + expected_identity；嵌套祖先用 identity_bindings 补齐")
    covered, verified, values = set(), [], {}
    row_identities = defaultdict(dict)
    for binding in bindings:
        if "identity_pointer" not in binding or "expected_identity" not in binding:
            raise _BindingError("每个 identity_bindings 项须同时提供 identity_pointer 与 expected_identity")
        try:
            identity, _, identity_tokens = _pointer(native, binding["identity_pointer"])
        except ValueError as error:
            raise _BindingError(str(error)) from error
        expected = binding["expected_identity"]
        if not (_text(expected) or _finite_number(expected)):
            raise _BindingError("expected_identity 须为非空文本或有限实数身份值")
        if type(identity) is not type(expected) or identity != expected:
            raise _BindingError(f"结果项身份与预期不符：实际 {identity!r}，预期 {expected!r}；须重新对应原生成果")
        matches = {number for number, row in enumerate(identified_rows)
                   if len(identity_tokens) == len(row["tokens"]) + 1 and identity_tokens[:-1] == row["tokens"]
                   and identity_tokens[-1] in row["identity_fields"]}
        if identified_rows and not matches:
            raise _BindingError("identity_pointer 须指向本数量所在数组元素自己的身份字段，不能借用其他行身份")
        covered.update(matches)
        for number in matches:
            row_identities[number][identity_tokens[-1]] = expected
        if binding["identity_pointer"] not in values:
            verified.append(dict(identity_pointer=binding["identity_pointer"], expected_identity=expected, native_identity=identity))
            values[binding["identity_pointer"]] = identity
    if len(covered) != len(identified_rows):
        missing = [_encoded_pointer(row["tokens"]) for number, row in enumerate(identified_rows) if number not in covered]
        raise _BindingError(f"带身份编号的数组祖先尚未完整绑定：{missing!r}；须在 identity_bindings 中补齐，局部重复编号不能代替楼层/分区身份")
    for number, row in enumerate(identified_rows):
        expected = row_identities[number]
        matches = [sibling for sibling in row["siblings"] if isinstance(sibling, dict)
                   and all(field in sibling and type(sibling[field]) is type(value) and sibling[field] == value
                           for field, value in expected.items())]
        if len(matches) != 1:
            raise _BindingError(f"数组项身份在同层不唯一：{_encoded_pointer(row['tokens'])}；须补充实际稳定身份，不能用数量或数组位置区分")

    supplied_records = claim.get("record_bindings", [])
    if not isinstance(supplied_records, list) or any(not isinstance(row, dict) for row in supplied_records):
        raise _BindingError("record_bindings 须为 record_pointer 与 expected_fields 的对象数组")
    records, verified_records = {}, []
    for binding in supplied_records:
        expected = binding.get("expected_fields")
        if not isinstance(expected, dict) or not expected or not _json_value(expected):
            raise _BindingError("record_bindings.expected_fields 须为非空、有限 JSON 字段对象")
        try:
            record, _, record_tokens = _pointer(native, binding.get("record_pointer"))
        except ValueError as error:
            raise _BindingError(str(error)) from error
        matching_rows = [row for row in array_rows if row["tokens"] == record_tokens]
        if not matching_rows or not isinstance(record, dict):
            raise _BindingError("record_pointer 须指向本数量实际经过的数组对象行，不能借用其他行的语义")
        row = matching_rows[0]
        contract, required = _record_contract(native, row)
        if not required:
            raise _BindingError("无 ID 数组行没有数量分支以外的完整语义上下文，不能将数量本身作为身份；须补充原生稳定身份或语义输出")
        if not required <= expected.keys():
            raise _BindingError(f"{contract} 的 record_bindings 缺少必要字段：{sorted(required - expected.keys())!r}")
        if row["branch"] in expected and row["branch"] not in required:
            raise _BindingError("record_bindings 不能把本次数量分支作为语义身份；只绑定该行的原生上下文字段")
        if any(field not in record or not _same_json(record[field], value) for field, value in expected.items()):
            raise _BindingError(f"结果行语义与 record_bindings 预期不符：{binding['record_pointer']}；须重新对应原生成果，不得沿用数组位置")
        # Optional extra fields cannot make duplicate known semantic rows appear
        # unique by binding their different measured values.
        matches = [sibling for sibling in row["siblings"] if isinstance(sibling, dict)
                   and all(field in sibling and _same_json(sibling[field], record[field]) for field in required)]
        if len(matches) != 1:
            raise _BindingError(f"结果行语义在同层不唯一：{binding['record_pointer']}；须先输出能区分实际部位的稳定身份或分项成果")
        key = tuple(record_tokens)
        if key in records:
            raise _BindingError(f"record_bindings 重复绑定同一数组行：{binding['record_pointer']}")
        records[key] = binding
        verified_records.append(dict(record_pointer=binding["record_pointer"], expected_fields=expected,
            native_fields={field: record[field] for field in expected}, required_fields=sorted(required), contract=contract))
    missing_records = [_encoded_pointer(row["tokens"]) for row in array_rows
                       if (not row["identity_fields"] or _record_contract(native, row)[0] != "explicit_row_context")
                       and tuple(row["tokens"]) not in records]
    if missing_records:
        raise _BindingError(f"无 ID 数组祖先尚未绑定语义：{missing_records!r}；须提供 record_bindings，不能仅凭位置采用数值")
    return values.get(pointer), verified, verified_records


def reconcile(document, artifacts, task_statuses=None):
    """Return separate observation/quantity gaps and traceable working subtotals.

    ``artifacts`` is the executor's observed map, not document-authored hashes.
    Physical ``objects[].extent_id`` permits an explicit source-observation split
    with retained basis/reason; this records a model judgment, never proves the
    portions disjoint.  Non-CAD scopes may declare inventory_disposition as
    not_applicable with inventory_basis_refs and reason.  Negative native values
    require target allow_negative=True, project_rule role and a current basis.
    Structural duplicate IDs raise ValueError; incomplete work stays in the
    returned gaps so independent valid quantities remain usable as subtotals.
    In a workflow_tracked executor context, the result producer's observed
    transitive input IDs must include the scope/object/target bases and source
    observation indexes.  Merely registering a current rule file cannot make an
    older calculation consume that rule.  Pure file callers do not prove this
    execution dependency property.
    """
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA:
        raise ValueError(f"需要 schema_version={SCHEMA}")
    if not isinstance(artifacts, dict) or (task_statuses is not None and not isinstance(task_statuses, dict)):
        raise ValueError("artifacts/task_statuses 必须是执行器观察映射")
    task_statuses = task_statuses or {}
    rows = {field: _records(document, field) for field in (
        "artifacts", "scopes", "work_packages", "objects", "quantity_targets", "quantity_claims")}
    dispositions = _records(document, "observation_dispositions", with_id=False)
    maps = {field: {row["id"]: row for row in values} for field, values in rows.items()}
    store = _Artifacts(maps["artifacts"], artifacts)
    scopes, objects, targets = maps["scopes"], maps["objects"], maps["quantity_targets"]
    issues, conflicts, splits, actions = [], [], [], []
    action_keys = set()

    def issue(code, message, **context):
        value = dict(code=code, message=message, **context)
        issues.append(value)
        return value

    def action(actor, code, message, **context):
        key = json.dumps([actor, code, context], sort_keys=True, ensure_ascii=False)
        if key not in action_keys:
            actions.append(dict(actor=actor, code=code, action=message, **context))
            action_keys.add(key)

    def basis_errors(record, refs_key="basis_refs"):
        errors = []
        refs = record.get(refs_key)
        if not _strings(refs) or not refs:
            errors.append(f"{refs_key} 须引用至少一个实际依据产物")
        else:
            for ref in refs:
                try:
                    store.read(ref)
                except ValueError as error:
                    errors.append(str(error))
                    action("program", "refresh_basis", "恢复或刷新实际依据文件，并重跑其受影响任务", artifact_id=ref)
        if not _text(record.get("reason")):
            errors.append("缺少模型工作解释 reason")
        return errors

    # The denominator is the workbench's actual, source-bound object list.  A
    # second artifact alias for the same native observation is not a new object.
    indexes, lookup, inventory = {}, {}, {}
    scope_problems = defaultdict(list)
    scope_inventory = defaultdict(set)
    inventory_exempt = set()
    index_failures = {}

    def load_index(artifact_id):
        if artifact_id in indexes:
            return indexes[artifact_id]
        if artifact_id in index_failures:
            raise ValueError(index_failures[artifact_id])
        try:
            value = store.read(artifact_id, "index", as_json=True)
            if not isinstance(value, dict) or not isinstance(value.get("objects"), list):
                raise ValueError("实例索引缺少实际 objects 数组")
            native = value["objects"]
            ids = [item.get("object_id") for item in native if isinstance(item, dict)]
            if len(ids) != len(native) or any(not _text(oid) for oid in ids) or len(ids) != len(set(ids)):
                raise ValueError("实例索引 object_id 缺失或重复")
            # Reuse the existing workbench contract, including both source file
            # hashes and the index's own content digest; do not invent an index.
            from qto_recognition_workbench import _verified_index
            verified = _verified_index(value)
            if value.get("source_identity_issues"):
                raise ValueError("实例索引仍有原图身份问题，须解决后绑定工程对象")
            indexes[artifact_id] = (value, verified)
            return indexes[artifact_id]
        except (OSError, KeyError, TypeError, ValueError) as error:
            index_failures[artifact_id] = f"{artifact_id}：{error}"
            raise ValueError(index_failures[artifact_id]) from error

    for scope_id, scope in scopes.items():
        # Scope basis needs a title and actual references; a separate reason is
        # optional in the base schema, because title already states the scope.
        if not _text(scope.get("title")):
            scope_problems[scope_id].append("缺少范围说明 title")
        scope_basis = dict(scope, reason=scope.get("reason") or scope.get("title"))
        scope_problems[scope_id].extend(basis_errors(scope_basis))
        ids = scope.get("inventory_artifact_ids", [])
        if not _strings(ids):
            raise ValueError(f"范围 {scope_id} 的 inventory_artifact_ids 须为不重复编号数组")
        if scope.get("inventory_disposition") == "not_applicable":
            exemption_errors = basis_errors(scope, "inventory_basis_refs")
            if exemption_errors:
                scope_problems[scope_id].extend(exemption_errors)
            elif ids:
                scope_problems[scope_id].append("索引不适用说明与已声明 CAD 索引同时存在，须明确本范围类型")
            else:
                inventory_exempt.add(scope_id)
        elif scope.get("inventory_disposition") not in (None, "required"):
            scope_problems[scope_id].append("inventory_disposition 无效")
        if not ids and scope_id not in inventory_exempt:
            scope_problems[scope_id].append("尚未建立原图观察索引范围")
            action("model", "establish_inventory", "明确图种、图幅及局部范围，调用原图索引工具建立观察分母", scope_id=scope_id)
        for artifact_id in ids:
            try:
                index, native = load_index(artifact_id)
            except ValueError as error:
                scope_problems[scope_id].append(str(error))
                action("program", "refresh_inventory", "从当前源图重建有效观察索引，保留无定位及未展开对象", scope_id=scope_id, artifact_id=artifact_id)
                continue
            for object_id, row in native.items():
                identity = (index["source"]["sha256"].strip().lower(), object_id)
                key = (scope_id, identity)
                lookup[(scope_id, artifact_id, object_id)] = key
                scope_inventory[scope_id].add(key)
                if key not in inventory:
                    inventory[key] = dict(scope_id=scope_id, artifact_id=artifact_id, artifact_ids=[],
                        object_id=object_id, source=index["source"], authoritative_source=index["authoritative_source"],
                        entity_type=row.get("entity_type"), source_path=row.get("source_path"),
                        bbox=row.get("bbox"), geometry_status=row.get("geometry_status"),
                        issues=row.get("issues", []), native=row)
                inventory[key]["artifact_ids"].append(artifact_id)
        required_tasks = scope.get("required_task_ids", [])
        if not _strings(required_tasks):
            raise ValueError(f"范围 {scope_id} 的 required_task_ids 须为不重复编号数组")
        for task_id in required_tasks:
            status = task_statuses.get(task_id, "pending")
            if status != "completed":
                scope_problems[scope_id].append(f"必需图种/工作任务 {task_id} 尚未完成：{status}")
                action("model", "complete_required_task", "完成本范围指定的读图/工作任务；已有局部数量不能代替此任务", scope_id=scope_id, task_id=task_id, task_status=status)

    object_errors = defaultdict(list)
    object_keys = defaultdict(set)
    observation_owners = defaultdict(set)
    object_refs = defaultdict(list)
    for physical_id, obj in objects.items():
        scope_id = obj.get("scope_id")
        if scope_id not in scopes:
            object_errors[physical_id].append("工程对象引用了未知范围")
        object_errors[physical_id].extend(basis_errors(obj))
        if obj.get("extent_id") is not None and not _text(obj.get("extent_id")):
            object_errors[physical_id].append("显式部位 extent_id 须为非空文本")
        observations = obj.get("observations", [])
        if not isinstance(observations, list) or any(not isinstance(row, dict) for row in observations):
            object_errors[physical_id].append("observations 须为原图观察引用数组")
            continue
        if not observations and scope_id not in inventory_exempt:
            object_errors[physical_id].append("CAD 范围的工程对象尚无原图观察对应")
        for observation in observations:
            artifact_id, object_id = observation.get("artifact_id"), observation.get("object_id")
            if not _text(artifact_id) or not _text(object_id):
                object_errors[physical_id].append("观察引用缺少 artifact_id/object_id")
                continue
            key = lookup.get((scope_id, artifact_id, object_id))
            if key is None:
                object_errors[physical_id].append(f"观察 {artifact_id}/{object_id} 不在本范围的有效实际索引中")
                continue
            if key in object_keys[physical_id]:
                # Aliases may be retained as evidence, but cannot create another
                # observation, another physical object, or another quantity.
                continue
            object_keys[physical_id].add(key)
            observation_owners[key[1]].add(physical_id)
            native = inventory[key]
            object_refs[physical_id].append(dict(artifact_id=artifact_id, object_id=object_id,
                source=native["source"], authoritative_source=native["authoritative_source"]))

    conflicted_observations = set()
    for identity, physical_ids in observation_owners.items():
        if len(physical_ids) < 2:
            continue
        owners = [objects[pid] for pid in sorted(physical_ids)]
        extents = [obj.get("extent_id") for obj in owners]
        explicit_split = (all(_text(extent) for extent in extents) and len(set(extents)) == len(extents)
                          and all(not basis_errors(obj) for obj in owners))
        context = dict(source_sha256=identity[0], object_id=identity[1], physical_ids=sorted(physical_ids))
        if explicit_split:
            splits.append(dict(**context, interpretations=[dict(physical_id=obj["id"], extent_id=obj["extent_id"],
                basis_refs=obj["basis_refs"], reason=obj["reason"]) for obj in owners],
                status="model_working_interpretation", disjointness_proven=False, formal_adoption=False))
        else:
            conflicted_observations.add(identity)
            conflicts.append(dict(code="source_observation_multiple_objects", message="不同工程对象共用同一原图观察，未说明可区分的实际部位", **context))
            for physical_id in physical_ids:
                object_errors[physical_id].append("原图观察同时归属多个工程对象，存在重计风险")
            action("model", "resolve_object_identity", "复核多视图是否为同一实物；如为部位拆分，逐项给出 extent_id、依据和解释", **context)

    grouped_dispositions = defaultdict(list)
    for ordinal, disposition in enumerate(dispositions):
        scope_id = disposition.get("scope_id")
        key = lookup.get((scope_id, disposition.get("artifact_id"), disposition.get("object_id")))
        if key is None:
            issue("unknown_observation_disposition", "观察处置未引用本范围有效索引中的真实对象", disposition_index=ordinal, scope_id=scope_id)
            scope_problems[scope_id].append("存在无效的原图观察处置引用")
            action("model", "repair_observation_reference", "重新定位真实索引对象，修正观察处置引用", disposition_index=ordinal, scope_id=scope_id)
            continue
        grouped_dispositions[key].append(disposition)

    unprocessed, processed_keys = [], set()
    inventory_uncertainties = []
    for key, observation in inventory.items():
        current = grouped_dispositions.get(key, [])
        errors = []
        if not current:
            errors.append("实际索引对象尚无纳入/排除/证据/容器处置")
        if key[1] in conflicted_observations:
            errors.append("该原图观察存在工程对象归属冲突")
        for disposition in current:
            role = disposition.get("disposition")
            errors.extend(basis_errors(disposition))
            if role not in DISPOSITIONS or role == "unresolved":
                errors.append("观察仍未决或处置类型无效")
            if role == "included":
                physical_id = disposition.get("physical_id")
                candidates = {pid for pid in observation_owners.get(key[1], set()) if key in object_keys[pid]}
                if physical_id is None and len(candidates) == 1:
                    physical_id = next(iter(candidates))
                if physical_id not in candidates:
                    errors.append("纳入观察没有唯一且一致的工程对象对应")
                elif object_errors[physical_id]:
                    errors.append("纳入观察对应的工程对象依据/身份尚不完整")
        if len(current) > 1:
            roles = {item.get("disposition") for item in current}
            pids = [item.get("physical_id") for item in current]
            split_ok = (roles == {"included"} and all(_text(pid) and pid in objects for pid in pids)
                        and len(set(pids)) == len(pids)
                        and all(_text(objects[pid].get("extent_id")) for pid in pids)
                        and len({objects[pid]["extent_id"] for pid in pids}) == len(pids)
                        and key[1] not in conflicted_observations)
            if not split_ok:
                errors.append("同一原图观察被重复或矛盾处置")
                conflicts.append(dict(code="observation_disposition_conflict", message=errors[-1],
                    scope_id=key[0], artifact_id=observation["artifact_id"], object_id=observation["object_id"]))
        public = {field: value for field, value in observation.items() if field != "native"}
        if errors:
            unprocessed.append(dict(public, reasons=list(dict.fromkeys(errors))))
            action("model", "process_observation", "检查原图对象并明确范围、归属及处置；未定位对象也须保留", scope_id=key[0],
                artifact_id=observation["artifact_id"], object_id=observation["object_id"], reasons=list(dict.fromkeys(errors)))
        else:
            processed_keys.add(key)
        native = observation["native"]
        if native.get("expansion_state") == "pending_scope_uncertain" or (
                native.get("entity_type") == "INSERT" and native.get("bounds_complete") is False):
            inventory_uncertainties.append(dict(public, reason="容器范围/子对象展开不完整，处置容器不能证明子对象不存在"))
            scope_problems[key[0]].append("存在子对象范围不完整的容器")
            action("model", "resolve_container_scope", "补读外参、块定义或局部范围，确认尚未展开的对象是否属于算量范围", scope_id=key[0],
                artifact_id=observation["artifact_id"], object_id=observation["object_id"])

    for physical_id, keys in object_keys.items():
        if keys - processed_keys:
            object_errors[physical_id].append("工程对象关联的原图观察仍未完成有效处置")

    target_errors = defaultdict(list)
    semantic_targets = defaultdict(list)
    for target_id, target in targets.items():
        errors = target_errors[target_id]
        for field in ("scope_id", "work_package_id", "physical_id", "extent_id", "quantity_kind",
                      "measurement_basis_id", "scenario_id", "unit", "quantity_role", "disposition"):
            if not _text(target.get(field)):
                errors.append(f"量项缺少 {field}")
        for field in ("layer_id", "side"):
            if target.get(field) is not None and not _text(target[field]):
                errors.append(f"{field} 须为非空文本或省略")
        errors.extend(basis_errors(target))
        scope_id, physical_id = target.get("scope_id"), target.get("physical_id")
        if scope_id not in scopes:
            errors.append("量项引用未知范围")
        obj = objects.get(physical_id)
        if obj is None or obj.get("scope_id") != scope_id:
            errors.append("量项未绑定本范围的工程对象")
        else:
            errors.extend(object_errors[physical_id])
            if obj.get("extent_id") is not None and target.get("extent_id") != obj["extent_id"]:
                errors.append("量项部位与工程对象显式拆分部位不一致")
        package = maps["work_packages"].get(target.get("work_package_id"))
        if (package is None or not _text(package.get("owner_skill"))
                or not _strings(package.get("scope_ids")) or scope_id not in package["scope_ids"]):
            errors.append("量项未归属本范围的有效专业工作包")
        if target.get("quantity_role") not in ROLES:
            errors.append("quantity_role 须区分几何量、项目规则量和采购参考量")
        if target.get("disposition") not in {"required", "not_applicable", "excluded"}:
            errors.append("量项 disposition 无效")
        if "allow_negative" in target and not isinstance(target["allow_negative"], bool):
            errors.append("allow_negative 须为显式布尔值")
        if all(_text(target.get(field)) for field in SEMANTIC_FIELDS if field not in {"layer_id", "side"}):
            # Unit and owner deliberately do not participate: changing either
            # cannot turn one actual quantity obligation into two quantities.
            key = tuple(target.get(field) for field in SEMANTIC_FIELDS)
            semantic_targets[key].append(target_id)

    for target_ids in semantic_targets.values():
        if len(target_ids) > 1:
            conflict = dict(code="duplicate_quantity_target", message="同一实物、部位和计量语义有多个量项，单位或专业归属不能用于重复计量", target_ids=target_ids,
                work_package_ids=sorted({targets[tid]["work_package_id"] for tid in target_ids}),
                units=sorted({targets[tid]["unit"] for tid in target_ids}))
            conflicts.append(conflict)
            for target_id in target_ids:
                target_errors[target_id].append(conflict["message"])
            action("model", "resolve_quantity_ownership", "确定同一实际量项的唯一专业归属；单位换算应在原生计算成果中明确，不能重复报量", target_ids=target_ids)

    claims_by_target = defaultdict(list)
    claim_errors = defaultdict(list)
    for claim in rows["quantity_claims"]:
        if claim.get("target_id") not in targets:
            issue("orphan_quantity_claim", "成果引用没有对应的应算量项", claim_id=claim["id"], target_id=claim.get("target_id"))
            action("model", "declare_quantity_target", "先说明该成果的实物、部位、计量口径和专业归属，再登记量项引用", claim_id=claim["id"])
            continue
        claims_by_target[claim["target_id"]].append(claim)
    for target_id, claims in claims_by_target.items():
        if len(claims) > 1:
            conflicts.append(dict(code="multiple_claims_for_target", message="同一应算量项引用了多份数量成果，尚未确定采用哪一份工作成果",
                target_id=target_id, claim_ids=[claim["id"] for claim in claims]))
            target_errors[target_id].append("同一量项有多个数量成果引用，不能累加或自动择一")
            action("model", "choose_current_claim", "按来源、版本和适用范围确定一个数量成果；其他视图保留为证据", target_id=target_id)

    candidates = []
    for claim in rows["quantity_claims"]:
        claim_id, target_id = claim["id"], claim.get("target_id")
        if target_id not in targets:
            continue
        target = targets[target_id]
        errors = claim_errors[claim_id]
        if target.get("disposition") != "required":
            errors.append("已排除/不适用量项不能再声明数量")
            conflicts.append(dict(code="claim_on_excluded_target", message=errors[-1], target_id=target_id, claim_id=claim_id))
        if claim.get("owner_work_package_id") != target.get("work_package_id"):
            errors.append("成果声明的专业归属与量项归属不同")
            conflicts.append(dict(code="claim_owner_mismatch", message=errors[-1], target_id=target_id, claim_id=claim_id))
        declared_level = claim.get("quantity_level")
        if declared_level not in LEVELS:
            errors.append("成果声明缺少有效 quantity_level，未决不得当作确认量")
        try:
            native = store.read(claim.get("artifact_id"), "result", as_json=True)
            execution = artifacts[claim["artifact_id"]]
            if execution.get("workflow_tracked") is True:
                obj = objects.get(target.get("physical_id"), {})
                scope = scopes.get(target.get("scope_id"), {})
                required_refs = set()
                for record in (target, obj, scope):
                    refs = record.get("basis_refs", [])
                    if _strings(refs):
                        required_refs.update(refs)
                source_observations = obj.get("observations", [])
                if isinstance(source_observations, list):
                    required_refs.update(row["artifact_id"] for row in source_observations
                        if isinstance(row, dict) and _text(row.get("artifact_id")))
                consumed_refs = execution.get("producer_input_ids")
                missing_refs = sorted(required_refs - set(consumed_refs)) if _strings(consumed_refs) else sorted(required_refs)
                if missing_refs or not _strings(consumed_refs):
                    message = (f"量项依据/观察索引未被本原生成果的实际生产任务及祖先消费：{missing_refs!r}；"
                               "须补真实依赖并重算，不能只更新依据文件哈希")
                    if not _strings(consumed_refs):
                        message += "；执行器尚未提供有效 producer_input_ids"
                    errors.append(message)
                    action("model", "declare_quantity_dependencies", "将量项、工程对象、范围依据及原图观察索引接入实际计算/模型准备任务依赖，再重算受影响成果", target_id=target_id,
                        artifact_id=claim.get("artifact_id"), claim_id=claim_id, missing_basis_refs=missing_refs, reason=message)
                    continue
            quantity, ancestors, tokens = _pointer(native, claim.get("result_pointer"))
            if not _finite_number(quantity):
                raise ValueError("实际结果必须是有限实数；bool/null/NaN/Inf 不能作为已算数量或自动转为零")
            native_identity, identity_bindings, record_bindings = _claim_identity(native, claim, ancestors, tokens)
            if quantity < 0 and not (target.get("quantity_role") == "project_rule"
                    and target.get("allow_negative") is True and not basis_errors(target)):
                raise ValueError("负值尚无有符号项目规则量的明确依据；须说明规则并显式 allow_negative=true")
            native_units = []
            found_unit, inherited_unit = _nearest(ancestors, "unit")
            if (len(tokens) == 3 and tokens[0] == "groups" and tokens[2] == "derived_area_m2"
                    and any(binding["contract"] == "component_count.groups" for binding in record_bindings)):
                # This native calculator defines two metrics in one group:
                # group.unit belongs to count; derived_area_m2 is always m2.
                # Do not infer arbitrary units from field suffixes or let an
                # explicit unit_pointer override the native metric's unit.
                found_unit, inherited_unit = True, "m2"
            if found_unit:
                native_units.append(inherited_unit)
            if claim.get("unit_pointer") is not None:
                native_units.append(_pointer(native, claim["unit_pointer"])[0])
            if any(not _text(unit) or unit != target.get("unit") for unit in native_units):
                raise ValueError(f"实际单位 {native_units!r} 与量项单位 {target.get('unit')!r} 不一致，未执行单位换算")
            native_levels = []
            # Named level buckets and native quantity metadata also constrain
            # an explicit level_pointer; it cannot point elsewhere to upgrade.
            for node in ancestors:
                if isinstance(node, dict) and "quantity_level" in node:
                    native_levels.append(node["quantity_level"])
            native_levels.extend(token for token in tokens if token in LEVEL_ALIASES or token in {"未决量", "unresolved"})
            if claim.get("level_pointer") is not None:
                native_levels.append(_pointer(native, claim["level_pointer"])[0])
            canonical_levels = []
            for level in native_levels:
                canonical = LEVEL_ALIASES.get(level) if isinstance(level, str) else None
                if canonical is None:
                    raise ValueError(f"原生成果层级 {level!r} 尚未决或无效，不能汇总为确认/推算/方案量")
                canonical_levels.append(canonical)
            weakest = max(canonical_levels, key=LEVELS.get) if canonical_levels else None
            final_level = (max([declared_level] + canonical_levels, key=LEVELS.get)
                           if declared_level in LEVELS else None)
            if final_level and final_level != declared_level:
                issue("native_quantity_level_limits_claim", "原生成果层级较弱，工作汇总已按较弱层级保留", claim_id=claim_id,
                    declared_quantity_level=declared_level, quantity_level=final_level)
            package = maps["work_packages"].get(target.get("work_package_id"), {})
            loaded = store.loaded[claim["artifact_id"]]
            candidates.append(dict(id=claim_id, claim_id=claim_id, target_id=target_id,
                artifact_id=claim["artifact_id"], path=loaded["path"], sha256=loaded["sha256"],
                result_pointer=claim["result_pointer"], unit_pointer=claim.get("unit_pointer"), level_pointer=claim.get("level_pointer"),
                identity_pointer=claim.get("identity_pointer"), expected_identity=claim.get("expected_identity"), native_identity=native_identity,
                identity_bindings=identity_bindings, record_bindings=record_bindings,
                quantity=quantity, unit=target.get("unit"), quantity_level=final_level,
                declared_quantity_level=declared_level, native_quantity_level=weakest,
                unit_basis="native_result" if native_units else "target_declaration",
                quantity_level_basis="native_and_claim" if native_levels else "claim_declaration",
                **{field: target.get(field) for field in SEMANTIC_FIELDS},
                work_package_id=target.get("work_package_id"), owner_skill=package.get("owner_skill"),
                basis_refs=target.get("basis_refs", []), reason=target.get("reason"),
                object_basis_refs=objects.get(target.get("physical_id"), {}).get("basis_refs", []),
                object_reason=objects.get(target.get("physical_id"), {}).get("reason"),
                observations=object_refs[target.get("physical_id")],
                status="working", review_state="unreviewed", formal_adoption=False))
        except _BindingError as error:
            errors.append(str(error))
            action("model", "rebind_native_result", "核对本量项与每层原生成果行的身份/语义并更新绑定；原生缺少可区分字段时补充程序输出，不能沿用数组位置", target_id=target_id,
                artifact_id=claim.get("artifact_id"), claim_id=claim_id, reason=str(error))
        except (OSError, TypeError, ValueError) as error:
            errors.append(str(error))
            action("program", "resolve_native_result", "检查原生计算输入与成果，刷新失效文件或补齐未决条件后重算；不能手填总量补账", target_id=target_id,
                artifact_id=claim.get("artifact_id"), claim_id=claim_id, reason=str(error))

    # One native scalar cannot be allocated in full to two separate physical
    # objects/parts.  Separate roles, sides or layers are explicit quantities and
    # may legitimately refer to the same measured geometry as their basis.
    native_claims = defaultdict(list)
    for entry in candidates:
        key = (entry["path"], entry["sha256"], entry["result_pointer"], entry["quantity_kind"],
               entry["measurement_basis_id"], entry["scenario_id"], entry["quantity_role"], entry["layer_id"], entry["side"])
        native_claims[key].append(entry)
    for entries in native_claims.values():
        if len({entry["target_id"] for entry in entries}) > 1:
            conflict = dict(code="native_result_reused", message="同一原生数量值被完整分配给多个量项，缺少实际分项计算成果",
                target_ids=[entry["target_id"] for entry in entries], claim_ids=[entry["claim_id"] for entry in entries],
                artifact_ids=sorted({entry["artifact_id"] for entry in entries}), result_pointer=entries[0]["result_pointer"])
            conflicts.append(conflict)
            for entry in entries:
                claim_errors[entry["claim_id"]].append(conflict["message"])
            action("model", "resolve_native_allocation", "复核该指针是否为合计或单项；拆分量项须由原生程序输出对应分项，不能重复分配整个值", target_ids=conflict["target_ids"])

    result_index = [entry for entry in candidates if not target_errors[entry["target_id"]] and not claim_errors[entry["claim_id"]]]
    accepted = {entry["target_id"] for entry in result_index}
    unaccounted = []
    for target_id, target in targets.items():
        errors = list(target_errors[target_id])
        claims = claims_by_target.get(target_id, [])
        for claim in claims:
            errors.extend(claim_errors[claim["id"]])
        if target.get("disposition") == "required" and target_id not in accepted:
            if not claims:
                errors.append("应算量项尚未引用任何实际程序成果")
                action("program", "calculate_quantity_target", "执行本工作包已有专业计算程序，产出并登记实际分项数值指针", target_id=target_id,
                    work_package_id=target.get("work_package_id"), scope_id=target.get("scope_id"))
        elif not errors:
            continue
        if errors:
            unaccounted.append(dict(target_id=target_id, **{field: target.get(field) for field in SEMANTIC_FIELDS},
                work_package_id=target.get("work_package_id"), unit=target.get("unit"), disposition=target.get("disposition"),
                reasons=list(dict.fromkeys(errors)), claim_ids=[claim["id"] for claim in claims]))
            if target_errors[target_id]:
                action("model", "resolve_target_basis", "补齐本量项的范围、实物对应、实际部位、计量依据及唯一专业归属", target_id=target_id,
                    scope_id=target.get("scope_id"), reasons=list(dict.fromkeys(target_errors[target_id])))

    grouped = defaultdict(list)
    for entry in result_index:
        grouped[tuple(entry[field] for field in TOTAL_FIELDS)].append(entry)
    totals = []
    for key, entries in grouped.items():
        try:
            values = [entry["quantity"] for entry in entries]
            quantity = sum(values) if all(isinstance(value, int) for value in values) else math.fsum(values)
        except OverflowError:
            quantity = math.inf
        if not _finite_number(quantity):
            issue("subtotal_overflow", "有效单项的分组总量超出有限数值范围，未生成该总量", claim_ids=[entry["claim_id"] for entry in entries])
            action("program", "resolve_subtotal_overflow", "检查数量数量级并在专业程序中明确单位及合理分组", claim_ids=[entry["claim_id"] for entry in entries])
            continue
        totals.append(dict(zip(TOTAL_FIELDS, key), quantity=quantity,
            claim_ids=[entry["claim_id"] for entry in entries], target_ids=[entry["target_id"] for entry in entries],
            physical_ids=sorted({entry["physical_id"] for entry in entries}),
            status="working", review_state="unreviewed", formal_adoption=False))

    scope_status = []
    for scope_id, scope in scopes.items():
        scoped_targets = [target for target in targets.values() if target.get("scope_id") == scope_id]
        required = [target for target in scoped_targets if target.get("disposition") == "required"]
        gaps = [row for row in unaccounted if row.get("scope_id") == scope_id]
        observations_left = [row for row in unprocessed if row["scope_id"] == scope_id]
        problems = list(scope_problems[scope_id])
        if not scoped_targets:
            problems.append("尚未声明应算/不适用/排除量项清单，不能以空账证明无漏项")
            action("model", "declare_scope_quantity_targets", "结合图种、实体构造及各专业职责列出本范围应算量项，并说明不适用或排除项目", scope_id=scope_id)
        if gaps:
            problems.append(f"{len(gaps)} 个量项未有效对账")
        if observations_left:
            problems.append(f"{len(observations_left)} 个实际原图观察未有效处置")
        inventory_ids = scope.get("inventory_artifact_ids", [])
        inventory_state = ("not_applicable" if scope_id in inventory_exempt else "missing" if not inventory_ids else
                           "partial" if any(aid in index_failures for aid in inventory_ids) else "current")
        scope_status.append(dict(scope_id=scope_id, title=scope.get("title"),
            status="pending" if problems else "reconciled_declared_scope", reasons=list(dict.fromkeys(problems)),
            inventory_state=inventory_state, inventory_artifact_ids=inventory_ids,
            inventory_count=len(scope_inventory[scope_id]) if inventory_state in {"current", "partial"} else None,
            processed_observation_count=len(scope_inventory[scope_id] & processed_keys),
            unprocessed_observation_count=len(observations_left),
            declared_target_count=len(scoped_targets), required_target_count=len(required),
            accounted_target_count=sum(target["id"] in accepted for target in required),
            unaccounted_target_count=len(gaps), required_task_statuses={tid: task_statuses.get(tid, "pending") for tid in scope.get("required_task_ids", [])},
            inventory_disposition_reason=scope.get("reason") if scope_id in inventory_exempt else None,
            inventory_basis_refs=scope.get("inventory_basis_refs", []) if scope_id in inventory_exempt else [],
            engineering_completeness_proven=False, status_meaning="仅对模型声明范围的原图观察与量项作工作对账，不证明所有图种、实物或计量义务已被发现",
            formal_adoption=False))
    if not scopes:
        issue("scope_not_established", "项目尚未声明工程范围，不能给出工程完整率")
        action("model", "establish_scope", "建立工程范围、图种任务及专业工作包，并关联真实来源依据")
    for physical_id, errors in object_errors.items():
        if errors:
            issue("incomplete_object_correspondence", "工程对象对应尚有缺口", physical_id=physical_id,
                scope_id=objects[physical_id].get("scope_id"), reasons=list(dict.fromkeys(errors)))
    return dict(schema_version=SCHEMA, kind="quantity_reconciliation", result_index=result_index, totals=totals,
        unaccounted_targets=unaccounted, unprocessed_observations=unprocessed, ownership_conflicts=conflicts,
        source_observation_splits=splits, inventory_uncertainties=inventory_uncertainties,
        scope_status=scope_status, issues=issues, next_actions=actions,
        status="working", review_state="unreviewed", formal_adoption=False, engineering_completeness_proven=False,
        semantics="原生程序数量的工作对账；不作工程识别自动认同、正式采用、采购转换或定价")
