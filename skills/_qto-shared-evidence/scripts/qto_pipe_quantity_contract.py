"""Pipe accessory quantity certainty, separate from a readable DN attribute.

These contracts combine declared evidence. They do not recognize a valve,
decide project scope, or turn model working results into formal adoption.
"""

from collections import Counter

LEVEL_RANK = {"confirmed": 3, "inferred": 2, "scenario": 1}
QUANTITY_LEVELS = {"confirmed": "确认量", "inferred": "规则推算量", "scenario": "方案暂估量"}
LEVEL_BY_QUANTITY = {value: key for key, value in QUANTITY_LEVELS.items()}


def valve_quantity_level(valve):
    """Return the weakest certainty of a valve's classified quantity.

The existing inference contract defaults an omitted instance_level to
confirmed. An explicitly null instance/DN/optional scope/type level stays
unresolved; invalid levels are errors. Old stored valve records may describe
DN certainty only through dn_quantity_level. Missing scope/type declarations
do not manufacture either new evidence or new project requirements.
    """
    levels = [valve.get("instance_level", "confirmed")]
    if "dn_evidence_level" in valve:
        levels.append(valve["dn_evidence_level"])
    else:
        value = valve.get("dn_quantity_level")
        if value is not None and value not in {*LEVEL_BY_QUANTITY, "未决量"}:
            raise ValueError("阀门 dn_quantity_level 无效")
        levels.append(LEVEL_BY_QUANTITY.get(value))
    for field in ("scope_evidence_level", "type_evidence_level"):
        if field in valve:
            levels.append(valve[field])
    for level in levels:
        if level is not None and (not isinstance(level, str) or level not in LEVEL_RANK):
            raise ValueError("阀门实例/范围/属性层级须为 confirmed、inferred、scenario 或未决")
    if (any(level is None for level in levels) or not valve.get("dn")
            or not valve.get("valve_type") or valve["valve_type"] == "未分类"):
        return "未决量"
    return QUANTITY_LEVELS[min(levels, key=LEVEL_RANK.get)]


def valve_source_identity(valve):
    """Stable source-instance key when supplied, never a block definition key.

Source hashes distinguish equal handles in different drawings. Existing
callers without a source hash retain their source_id namespace. Expanded
CADINST record IDs already contain insertion paths, so a redundant path cannot
rename that same source instance. For unexpanded source IDs, an explicitly
separate instance_path keeps two inserts of one definition distinct.
Surrounding whitespace follows the inference reader's text normalization;
only a supplied source hash is case-insensitive, not opaque source/object IDs.
    """
    object_id = valve.get("source_object_id")
    if object_id is None:
        return None
    source_hash = valve.get("source_sha256")
    namespace = source_hash or valve.get("source_id")
    instance_path = valve.get("instance_path", "")
    if (not isinstance(object_id, str) or not object_id.strip()
            or not isinstance(namespace, str) or not namespace.strip()
            or not isinstance(instance_path, str)):
        raise ValueError("阀门源对象身份须包含有效来源、源实例对象编号及插入路径")
    namespace, object_id, instance_path = namespace.strip(), object_id.strip(), instance_path.strip()
    if source_hash:
        namespace = namespace.lower()
    if object_id.startswith("CADINST-") and "/" in object_id[len("CADINST-"):]:
        instance_path = ""
    return namespace, object_id, instance_path


def _valve_group_key(row, quantity_level):
    for field in ("system", "valve_type"):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ValueError("阀门分组缺少有效 " + field)
    family = row.get("block_family")
    dn = row.get("dn")
    if family is not None and not isinstance(family, str):
        raise ValueError("阀门块族必须为文本或空值")
    if dn is not None and not isinstance(dn, str):
        raise ValueError("阀门 DN 必须为文本或空值")
    return row["system"], row["valve_type"], family, dn or "DN待定", quantity_level


def validate_valve_summary(inference):
    """Reconcile stored counts with instance evidence, including legacy output.

    A user-facing table may faithfully copy an inconsistent engine summary.
    Both result gates must therefore validate the underlying evidence first.
    This validates declared state and arithmetic, not actual drawing identity.
    """
    errors = []
    valves = inference.get("valves", [])
    rows = inference.get("valve_summary", [])
    if not isinstance(valves, list) or not isinstance(rows, list):
        return ["valves 与 valve_summary 必须为数组"]
    expected = Counter()
    valve_ids = set()
    source_ids = set()
    for index, valve in enumerate(valves):
        if not isinstance(valve, dict):
            errors.append(f"valves[{index}] 必须是对象")
            continue
        valve_id = valve.get("valve_id")
        if not isinstance(valve_id, str) or not valve_id.strip():
            errors.append(f"valves[{index}] 缺少有效 valve_id")
        elif valve_id in valve_ids:
            errors.append(f"阀门实例编号重复: {valve_id}")
        else:
            valve_ids.add(valve_id)
        try:
            level = valve_quantity_level(valve)
            if "quantity_level" in valve and valve["quantity_level"] != level:
                errors.append(f"{valve_id} 阀门数量层级与实例/范围/属性证据不一致，应为{level}")
            if "dn_evidence_level" in valve and "dn_quantity_level" in valve:
                dn_level = QUANTITY_LEVELS.get(valve["dn_evidence_level"], "未决量")
                if valve["dn_quantity_level"] != dn_level:
                    errors.append(f"{valve_id} 阀门 DN 层级与 DN 证据不一致")
            expected[_valve_group_key(valve, level)] += 1
            source_id = valve_source_identity(valve)
            if source_id is not None:
                if source_id in source_ids:
                    errors.append(f"阀门源实例重复计数: {valve_id}")
                source_ids.add(source_id)
        except (TypeError, ValueError) as exc:
            errors.append(f"valves[{index}]: {exc}")

    actual = Counter()
    seen_groups = set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            errors.append(f"valve_summary[{index}] 必须是对象")
            continue
        try:
            level = row.get("quantity_level")
            if not isinstance(level, str) or level not in {*LEVEL_BY_QUANTITY, "未决量"}:
                raise ValueError("阀门汇总数量层级无效")
            key = _valve_group_key(row, level)
            count = row.get("instance_count")
            if isinstance(count, bool) or not isinstance(count, int) or count < 1:
                raise ValueError("阀门汇总实例数必须为正整数")
            if key in seen_groups:
                errors.append("阀门汇总分组重复")
            seen_groups.add(key)
            actual[key] += count
        except (TypeError, ValueError) as exc:
            errors.append(f"valve_summary[{index}]: {exc}")
    if actual != expected:
        errors.append("阀门汇总的系统、类型、块族、DN、数量层级或实例数与逐实例证据不一致")

    metrics = inference.get("metrics", {})
    if not isinstance(metrics, dict):
        errors.append("管网推算证据 metrics 必须是对象")
    elif valves or rows or "valve_count" in metrics:
        count = metrics.get("valve_count")
        if isinstance(count, bool) or not isinstance(count, int) or count != len(valves):
            errors.append("阀门明细数与 metrics.valve_count 不一致")
    return errors
