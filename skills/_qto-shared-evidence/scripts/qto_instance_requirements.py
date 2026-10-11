"""Apply sourced, model-interpreted requirements to actual physical instances.

This is deliberately a finite count/applicability calculator, not a professional
rule library. Length, area and support-layout requests remain specialist tasks.
All identities, clauses, facts and exceptions come from this run's evidence.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from collections import defaultdict
from pathlib import Path

from model_recognition_basis import file_sha, load_working_entries, required_text, texts

SCHEMA = "qto-instance-requirements/1.0"
RESULT_SCHEMA = "qto-instance-requirements-result/1.0"
COUNT_UNITS = {"个", "只", "件", "套", "组", "根", "台", "樘"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False, separators=(",", ":")).encode()).hexdigest()


def rows(value, name):
    if not isinstance(value, list) or any(not isinstance(v, dict) for v in value):
        raise ValueError(name + " 必须是对象数组")
    return value


def identity_map(value, key, name):
    out = {}
    for row in rows(value, name):
        ident = required_text(row.get(key), name + "." + key)
        if ident in out:
            raise ValueError(name + " 编号重复: " + ident)
        out[ident] = row
    return out


def resolve(base, path):
    return (base / required_text(path, "path")).resolve()


class Evidence:
    def __init__(self, payload, base):
        self.sources = identity_map(payload.get("sources"), "source_id", "sources")
        self.ledgers, self.entries, self.catalog = {}, {}, {}
        docs = {}
        for sid, source in self.sources.items():
            if source.get("status") != "selected":
                raise ValueError(sid + " 来源必须明确 selected")
            document = required_text(source.get("document_id"), sid + ".document_id")
            revision = required_text(source.get("revision"), sid + ".revision")
            path = resolve(base, source.get("path"))
            if file_sha(path) != source.get("sha256"):
                raise ValueError(sid + " 来源文件哈希不一致")
            state = (revision, source["sha256"])
            if document in docs and docs[document] != state:
                raise ValueError(document + " 同一文件身份有冲突版本/内容")
            docs[document] = state
        for item in rows(payload.get("recognition_ledgers"), "recognition_ledgers"):
            sid = item.get("source_id")
            if sid not in self.sources:
                raise ValueError("台账引用未登记来源")
            path = resolve(base, item.get("path"))
            if file_sha(path) != item.get("sha256"):
                raise ValueError("台账文件哈希不一致")
            ledger, entries = load_working_entries(json.loads(path.read_text(encoding="utf-8-sig")),
                                                   path, self.sources[sid]["sha256"])
            lid = ledger["ledger_id"]
            if lid in self.ledgers:
                raise ValueError("台账编号重复: " + lid)
            self.ledgers[lid] = {"path": str(path), "sha256": item["sha256"], "source_id": sid}
            self.entries[lid] = entries
            for ev in ledger["evidence"]:
                self.catalog[lid + ":" + ev["evidence_id"]] = {
                    **ev, "path": str(resolve(path.parent, ev["path"])), "source_id": sid,
                    "document_id": self.sources[sid]["document_id"],
                    "revision": self.sources[sid]["revision"],
                }

    def refs(self, values, name):
        out = texts(values, name)
        if any(v not in self.catalog for v in out):
            raise ValueError(name + " 引用了未登记的图面证据")
        return out

    def decision(self, row, object_id):
        pointer = row.get("recognition", {})
        lid, eid = pointer.get("ledger_id"), pointer.get("entry_id")
        entry = self.entries.get(lid, {}).get(eid)
        if not entry or object_id not in entry["object_ids"] or entry["decision"] != "include":
            raise ValueError(str(object_id) + " 缺少对应的模型工作识别 include 记录")
        refs = self.refs(row.get("evidence_refs"), str(object_id) + ".evidence_refs")
        allowed = {lid + ":" + v for v in entry["evidence_ids"]}
        if not set(refs) <= allowed:
            raise ValueError(str(object_id) + " 证据未绑定对应模型识别记录")
        return refs

    def anchor(self, row):
        anchor = row.get("identity_anchor", {})
        lid = row["recognition"]["ledger_id"]
        entry = self.entries[lid][row["recognition"]["entry_id"]]
        if anchor.get("source_id") != self.ledgers[lid]["source_id"] or anchor.get("object_id") not in entry["object_ids"]:
            raise ValueError("实例 identity_anchor 须绑定识别台账中的原始对象身份")
        source = self.sources[anchor["source_id"]]
        if row.get("subject_kind") != "port" and "port_key" in row:
            raise ValueError("只有真实 port 主体可使用 port_key，实例或附件不得用它拆分源身份")
        return (source["document_id"], source["revision"], source["sha256"], anchor["object_id"])

    def port_binding(self, row):
        if row["subject_kind"] != "port":
            return False
        pointer = row["recognition"]
        entry = self.entries[pointer["ledger_id"]][pointer["entry_id"]]
        bindings = entry.get("port_bindings", [])
        expected = {"subject_id": row["subject_id"], "anchor_object_id": row["identity_anchor"]["object_id"],
                    "port_key": row["port_key"], "parent_physical_id": row["parent_physical_id"]}
        return any(all(binding.get(k) == v for k, v in expected.items()) for binding in rows(bindings, "port_bindings"))


def fact_map(raw, evidence, allowed, name):
    if not isinstance(raw, dict):
        raise ValueError(name + " facts 必须是对象")
    out = {}
    for key, fact in raw.items():
        required_text(key, name + ".fact")
        if not isinstance(fact, dict) or "value" not in fact:
            raise ValueError(name + " 事实须带 value，缺事实用 null")
        value = fact["value"]
        if value is not None and not isinstance(value, (str, bool, int, float)):
            raise ValueError(name + " 事实只支持标量")
        digest(value)  # reject NaN and infinity
        refs = evidence.refs(fact.get("evidence_refs"), name + "." + key)
        if not set(refs) <= set(allowed):
            raise ValueError(name + " 事实证据超出该对象识别记录")
        out[key] = {"value": value, "evidence_refs": refs}
    return out


def conditions(raw):
    out = []
    for condition in rows(raw, "conditions"):
        required_text(condition.get("fact"), "condition.fact")
        op = condition.get("op")
        if op not in {"eq", "in"}:
            raise ValueError("条件仅支持有明确事实值的 eq / in")
        value = condition.get("value")
        values = value if op == "in" else [value]
        if not isinstance(values, list) or not values or any(v is None or not isinstance(v, (str, bool, int, float)) for v in values):
            raise ValueError("条件比较值必须是非空标量")
        digest(values)
        out.append(condition)
    return out


def matches(conds, facts):
    missing = []
    for c in conds:
        v = facts.get(c["fact"], {}).get("value")
        if v is None:
            missing.append(c["fact"])
        elif (c["op"] == "eq" and (type(v) != type(c["value"]) or v != c["value"])) or (
                c["op"] == "in" and not any(type(v) == type(x) and v == x for x in c["value"])):
            return False, []
    return (None if missing else True), sorted(set(missing))


def slot(rule, sources):
    position = rule["installation_position"]
    source = sources.get(position.get("source_id"), {})
    return (rule["professional_owner"], rule["accessory_role"], position["relation"],
            source.get("document_id"), source.get("revision"), source.get("sha256"), position.get("object_id"))


def _prepare(payload, base):
    if payload.get("schema") != SCHEMA:
        raise ValueError("逐实例要求输入版本无效")
    for key in ("scope_id", "run_id"):
        required_text(payload.get(key), key)
    evidence = Evidence(payload, base)
    observations = identity_map(payload.get("subjects"), "subject_id", "subjects")
    groups = defaultdict(list)
    anchors = {}
    for oid, raw in observations.items():
        refs = evidence.decision(raw, oid)
        row = copy.deepcopy(raw)
        for key in ("physical_id", "system", "scope_id"):
            required_text(row.get(key), oid + "." + key)
        if row["scope_id"] != payload["scope_id"]:
            raise ValueError(oid + " 不属于声明范围")
        if row.get("subject_kind") not in {"instance", "port"}:
            raise ValueError("subject_kind 必须 instance / port")
        if row["subject_kind"] == "port":
            required_text(row.get("parent_physical_id"), oid + ".parent_physical_id")
            required_text(row.get("port_key"), oid + ".port_key")
        anchor = evidence.anchor(row)
        bound_port = evidence.port_binding(row)
        for previous in anchors.get(anchor, []):
            if previous["physical_id"] != row["physical_id"]:
                if not (bound_port and previous["bound_port"] and row.get("port_key") != previous["port_key"]
                        and row.get("parent_physical_id") == previous["parent_physical_id"]):
                    raise ValueError("同一来源实例/端口被改名为多个物理实例；共享设备源锚的独立端口须有台账 port_bindings")
        anchors.setdefault(anchor, []).append({"physical_id": row["physical_id"], "bound_port": bound_port,
            "port_key": row.get("port_key"), "parent_physical_id": row.get("parent_physical_id")})
        row["facts"] = fact_map(row.get("facts"), evidence, refs, oid)
        groups[row["physical_id"]].append(row)
    subjects = {}
    port_owners = {}
    for pid, group in groups.items():
        signature = {(r["system"], r["scope_id"], r["subject_kind"], r.get("parent_physical_id"), r.get("port_key")) for r in group}
        if len(signature) != 1:
            raise ValueError(pid + " 同一物理对象多视图身份冲突")
        row = copy.deepcopy(group[0])
        row["observation_ids"] = sorted(r["subject_id"] for r in group)
        row["evidence_refs"] = sorted({e for r in group for e in r["evidence_refs"]})
        row["conflicting_facts"] = []
        all_keys = {k for r in group for k in r["facts"]}
        row["facts"] = {}
        for key in all_keys:
            facts = [r["facts"][key] for r in group if key in r["facts"]]
            known = {digest(f["value"]): f["value"] for f in facts if f["value"] is not None}
            if len(known) > 1:
                row["conflicting_facts"].append(key)
            row["facts"][key] = {"value": next(iter(known.values())) if len(known) == 1 else None,
                                  "evidence_refs": sorted({e for f in facts for e in f["evidence_refs"]})}
        if row["subject_kind"] == "port":
            port = (row["parent_physical_id"], row["port_key"])
            if port in port_owners:
                raise ValueError("同设备同端口被赋予多个物理编号，须先解决身份")
            port_owners[port] = pid
        subjects[pid] = row
    rules = identity_map(payload.get("requirements"), "requirement_id", "requirements")
    for rid, rule in rules.items():
        evidence.decision(rule, rid)
        for key in ("requirement_key", "version", "system", "scope_id", "accessory_type", "accessory_role", "professional_owner", "basis"):
            required_text(rule.get(key), rid + "." + key)
        if rule["scope_id"] != payload["scope_id"]:
            raise ValueError(rid + " 条款不属于声明范围")
        if rule.get("subject_kind") not in {"instance", "port"}:
            raise ValueError("条款 target subject_kind 无效")
        position = rule.get("installation_position", {})
        if position != {"relation": "subject_interface"}:
            if position.get("relation") != "source_object":
                raise ValueError("附件安装位置须 subject_interface 或有来源的 source_object")
            evidence.refs(position.get("evidence_refs"), "installation_position.evidence_refs")
            if position.get("source_id") not in evidence.sources:
                raise ValueError("安装位置来源未登记")
            required_text(position.get("object_id"), "installation_position.object_id")
            entry = evidence.entries[rule["recognition"]["ledger_id"]][rule["recognition"]["entry_id"]]
            ledger = evidence.ledgers[rule["recognition"]["ledger_id"]]
            source = evidence.sources[position["source_id"]]
            ledger_source = evidence.sources[ledger["source_id"]]
            same_source = all(source[k] == ledger_source[k] for k in ("document_id", "revision", "sha256"))
            position_refs = set(position["evidence_refs"])
            if (position["object_id"] not in entry["object_ids"] or not same_source
                    or not position_refs <= set(rule["evidence_refs"])):
                raise ValueError("独立串联安装位未绑定源对象识别")
        conditions(rule.get("when", []))
        for ex in rows(rule.get("exceptions", []), "exceptions"):
            required_text(ex.get("reason"), "exception.reason")
            evidence.refs(ex.get("evidence_refs"), "exception.evidence_refs")
            if not set(ex["evidence_refs"]) <= set(rule["evidence_refs"]):
                raise ValueError("例外依据未绑定条款识别记录")
            if not conditions(ex.get("when")):
                raise ValueError("例外必须有明确适用条件")
        quantity = rule.get("quantity", {})
        if quantity.get("mode") == "count":
            if type(quantity.get("per_subject")) is not int or quantity["per_subject"] <= 0:
                raise ValueError("逐实例件数必须正整数，不能用零掩盖缺事实")
            if quantity.get("unit") not in COUNT_UNITS:
                raise ValueError("计数模式只支持件数单位；长度面积等交专业几何计算")
        elif quantity.get("mode") == "specialist_measurement":
            required_text(quantity.get("unit"), "specialist unit")
            texts(quantity.get("required_facts"), "specialist required_facts")
            if "per_subject" in quantity:
                raise ValueError("专业计量不得夹带每实例固定量")
        else:
            raise ValueError("quantity.mode 必须 count / specialist_measurement")
        spec = rule.get("specification")
        if not isinstance(spec, dict) or not spec:
            raise ValueError("规格必须显式绑定事实或源条款值")
        for key, binding in spec.items():
            if not isinstance(binding, dict):
                raise ValueError("规格绑定必须对象")
            if set(binding) == {"from_fact"}:
                required_text(binding["from_fact"], "from_fact")
            elif set(binding) == {"value", "evidence_refs"}:
                evidence.refs(binding["evidence_refs"], "specification evidence")
                if not set(binding["evidence_refs"]) <= set(rule["evidence_refs"]):
                    raise ValueError("固定规格依据未绑定条款识别记录")
                if binding["value"] is None or not isinstance(binding["value"], (str, bool, int, float)):
                    raise ValueError("条款规格必须明确，未知请绑定缺失事实")
                digest(binding["value"])
            else:
                raise ValueError("规格只能 from_fact 或 value+evidence_refs")
    existing = identity_map(payload.get("existing_accessories", []), "observation_id", "existing_accessories")
    links = defaultdict(dict)
    accessory_claims = {}
    accessory_anchors = {}
    existing_record_ids = {}
    for oid, item in existing.items():
        evidence.decision(item, oid)
        if "port_key" in item or item.get("subject_kind") not in {None, "instance"}:
            raise ValueError("既有附件必须是物理实例，不得通过端口字段拆分数量")
        for key in ("physical_id", "subject_physical_id", "requirement_key", "system", "scope_id", "result_record_id", "unit"):
            required_text(item.get(key), oid + "." + key)
        target = subjects.get(item["subject_physical_id"])
        if target is None or item["system"] != target["system"] or item["scope_id"] != target["scope_id"]:
            raise ValueError(oid + " 附件绑定到不存在/错系统/错范围对象")
        valid = [r for r in rules.values() if r["requirement_key"] == item["requirement_key"] and
                 r["system"] == item["system"] and r["subject_kind"] == target["subject_kind"]]
        if not valid:
            raise ValueError(oid + " 没有适用的需求槽")
        if len({slot(r, evidence.sources) for r in valid}) != 1:
            raise ValueError(oid + " requirement_key 对应多个安装位置或附件角色")
        physical = item["physical_id"]
        key = (item["subject_physical_id"], *slot(valid[0], evidence.sources))
        anchor = evidence.anchor(item)
        if anchor in accessory_anchors and accessory_anchors[anchor] != physical:
            raise ValueError("同一图示附件源对象被改名重复计数")
        accessory_anchors[anchor] = physical
        if item["result_record_id"] in existing_record_ids and existing_record_ids[item["result_record_id"]] != physical:
            raise ValueError("同一既有数量记录被配给多个物理附件")
        existing_record_ids[item["result_record_id"]] = physical
        if physical in accessory_claims and accessory_claims[physical] != key:
            raise ValueError(physical + " 同一图示附件被分配到多个需求槽")
        accessory_claims[physical] = key
        previous = links[key].get(physical)
        if previous and (previous["result_record_id"] != item["result_record_id"] or previous.get("specification") != item.get("specification")):
            raise ValueError(physical + " 多视图附件数量出口或规格冲突")
        if previous:
            previous["evidence_refs"] = sorted(set(previous["evidence_refs"] + item["evidence_refs"]))
        else:
            links[key][physical] = copy.deepcopy(item)
    return evidence, subjects, rules, links


def evaluate(payload, base_dir=".", previous=None):
    """Recompute all counts; previous is used only to report changed result IDs."""
    evidence, subjects, rules, links = _prepare(payload, Path(base_dir).resolve())
    candidates = defaultdict(list)
    physical_positions = {}
    for rid, rule in rules.items():
        for pid, subject in subjects.items():
            if rule["system"] != subject["system"] or rule["subject_kind"] != subject["subject_kind"]:
                continue
            applicable, missing = matches(rule.get("when", []), subject["facts"])
            issues = ["condition_fact_missing:" + k for k in missing]
            excluded_by = []
            if applicable is not False:
                for ex in rule.get("exceptions", []):
                    match, absent = matches(ex["when"], subject["facts"])
                    if match is True:
                        applicable = False
                        excluded_by.append(ex["reason"])
                        issues = []
                        break
                    if match is None:
                        applicable = None
                        issues.extend("exception_fact_missing:" + k for k in absent)
            specification = {}
            refs = set(rule["evidence_refs"] + subject["evidence_refs"])
            for ex in rule.get("exceptions", []):
                refs.update(ex["evidence_refs"])
            for key, binding in rule["specification"].items():
                if "from_fact" in binding:
                    fact = subject["facts"].get(binding["from_fact"], {})
                    value = fact.get("value")
                    refs.update(fact.get("evidence_refs", []))
                    if value is None and applicable is not False:
                        issues.append("specification_fact_missing:" + binding["from_fact"])
                    specification[key] = value
                else:
                    specification[key] = binding["value"]
                    refs.update(binding["evidence_refs"])
            quantity = rule["quantity"]
            status = "excluded" if applicable is False else "unresolved" if applicable is None else "determined"
            if applicable is not False and quantity["mode"] == "specialist_measurement":
                status = "unresolved"
                issues.append("specialist_geometry_required")
            count = quantity["per_subject"] if status == "determined" else None
            if applicable is not False and rule["installation_position"]["relation"] == "source_object":
                position_key = slot(rule, evidence.sources)
                if position_key in physical_positions and physical_positions[position_key] != pid:
                    raise ValueError("同一来源物理安装位分配到多个主体，须先解决共享附件归属")
                physical_positions[position_key] = pid
            row = {
                "subject_physical_id": pid, "subject_observation_ids": subject["observation_ids"],
                "requirement_key": rule["requirement_key"], "requirement_keys": [rule["requirement_key"]], "requirement_ids": [rid],
                "accessory_role": rule["accessory_role"], "installation_position": rule["installation_position"],
                "professional_owner": rule["professional_owner"], "system": subject["system"],
                "scope_id": subject["scope_id"], "accessory_type": rule["accessory_type"],
                "specification": specification, "specification_complete": all(v is not None for v in specification.values()),
                "status": status, "required_quantity": count, "additional_quantity": count,
                "unit": quantity["unit"], "mode": quantity["mode"],
                "excluded_by": excluded_by, "issues": issues, "evidence_refs": sorted(refs),
                "dependency_signature": digest({"subject": subject, "rule": rule,
                    "source_hashes": {k: v["sha256"] for k, v in evidence.sources.items()}}),
                "quantity_level": "不适用" if status == "excluded" else "规则推算量" if count is not None else "未决量",
                "adoption_scope": "working", "review_status": "unreviewed", "formal_adoption_allowed": False,
                "required_facts": quantity.get("required_facts", []),
            }
            candidates[(pid, *slot(rule, evidence.sources))].append(row)
    records = []
    for key, group in sorted(candidates.items()):
        applicable_group = [g for g in group if g["status"] != "excluded"]
        row = copy.deepcopy((applicable_group or group)[0])
        row["record_id"] = "instance-requirement:" + digest(list(key))[:24]
        row["requirement_ids"] = sorted(r for g in group for r in g["requirement_ids"])
        row["requirement_keys"] = sorted({r for g in group for r in g["requirement_keys"]})
        row["clause_evaluations"] = [{"requirement_ids": g["requirement_ids"], "status": g["status"],
            "excluded_by": g["excluded_by"], "issues": g["issues"]} for g in group]
        row["required_facts"] = sorted({f for g in applicable_group for f in g["required_facts"]})
        row["issues"] = sorted({i for g in (applicable_group or group) for i in g["issues"]})
        row["evidence_refs"] = sorted({e for g in group for e in g["evidence_refs"]})
        row["dependency_signature"] = digest(sorted(g["dependency_signature"] for g in group))
        comparison = ("status", "required_quantity", "unit", "mode", "specification", "accessory_type", "professional_owner")
        if len({digest({k: g[k] for k in comparison}) for g in applicable_group}) > 1:
            row.update(status="unresolved", required_quantity=None, additional_quantity=None, quantity_level="未决量")
            row["issues"].append("conflicting_requirements_same_physical_slot")
        matched = list(links.get(key, {}).values())
        row["matched_existing"] = matched
        row["existing_count"] = len(matched)
        row["evidence_refs"] = sorted(set(row["evidence_refs"]) | {e for m in matched for e in m["evidence_refs"]})
        row["dependency_signature"] = digest([row["dependency_signature"], matched])
        if matched and row["status"] == "excluded":
            row["issues"].append("drawing_accessory_present_for_excluded_requirement")
        if row["status"] == "determined":
            incompatible = any(m.get("specification") != row["specification"] or m.get("accessory_type") != row["accessory_type"]
                               or m["unit"] != row["unit"] for m in matched)
            if len(matched) > row["required_quantity"] or incompatible:
                row.update(status="unresolved", additional_quantity=None)
                row["issues"].append("existing_accessory_count_or_specification_conflict")
            else:
                row["additional_quantity"] = row["required_quantity"] - len(matched)
        row["quantity_owner"] = "existing_and_requirement_remainder" if matched else "instance_requirement"
        records.append(row)
    referenced_subjects = {r["subject_physical_id"] for r in records}
    unassigned = sorted(set(subjects) - referenced_subjects)
    output = {
        "schema": RESULT_SCHEMA, "run_id": payload["run_id"], "scope_id": payload["scope_id"],
        "input_sha256": digest(payload), "records": records,
        "sources": {k: {**v, "path": str(resolve(Path(base_dir).resolve(), v["path"]))} for k, v in evidence.sources.items()},
        "evidence_catalog": evidence.catalog, "recognition_ledgers": evidence.ledgers,
        "unassigned_subject_physical_ids": unassigned,
        "scope_complete": payload.get("scope_complete") is True and not unassigned and bool(records)
                          and all(r["status"] != "unresolved" and (r["status"] == "excluded" or r["specification_complete"])
                                  and not r["issues"] for r in records),
        "coverage_declaration": {"declared_complete": payload.get("scope_complete") is True,
                                 "verification": "declared_subjects_and_requirements_only; source-reading completeness requires review"},
        "adoption_scope": "working", "review_status": "unreviewed", "formal_adoption_allowed": False,
    }
    if previous is not None:
        if previous.get("schema") != RESULT_SCHEMA or previous.get("scope_id") != payload["scope_id"]:
            raise ValueError("前次结果版本或范围不一致")
        before = {r["record_id"]: r["dependency_signature"] for r in previous["records"]}
        after = {r["record_id"]: r["dependency_signature"] for r in records}
        output["recompute"] = {"changed_or_added": sorted(k for k in after if before.get(k) != after[k]),
                               "removed": sorted(set(before) - set(after)), "method": "full_recompute_dependency_diff"}
    output["result_sha256"] = digest(output)
    return output


def verify_result(result, base_dir="."):
    value = dict(result)
    expected = value.pop("result_sha256", None)
    if value.get("schema") != RESULT_SCHEMA or digest(value) != expected:
        raise ValueError("附属量结果内容签名不一致")
    for source in value["sources"].values():
        if file_sha(resolve(Path(base_dir), source["path"])) != source["sha256"]:
            raise ValueError("附属量原始来源已变化，需重算")
    for item in list(value["recognition_ledgers"].values()) + list(value["evidence_catalog"].values()):
        if file_sha(Path(item["path"])) != item["sha256"]:
            raise ValueError("识别或证据文件已变化，需重算")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--previous", type=Path)
    args = parser.parse_args()
    try:
        if args.output.exists():
            raise ValueError("输出已存在，请使用新版本路径")
        data = json.loads(args.input.read_text(encoding="utf-8-sig"))
        previous = json.loads(args.previous.read_text(encoding="utf-8-sig")) if args.previous else None
        result = evaluate(data, args.input.parent, previous)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps({"status": "PASS", "records": len(result["records"]), "scope_complete": result["scope_complete"]}, ensure_ascii=False))
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(json.dumps({"status": "INPUT_REQUIRED", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
