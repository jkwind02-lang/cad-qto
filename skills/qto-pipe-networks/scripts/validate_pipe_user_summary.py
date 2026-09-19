#!/usr/bin/env python3
"""Validate that a user-facing pipe summary exposes required business dimensions."""

from __future__ import annotations

import argparse
import json
import sys
import zipfile
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PKG_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"


def load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"JSON 顶层必须是对象: {path}")
    return value


def xlsx_text(path: Path) -> dict[str, str]:
    with zipfile.ZipFile(path) as archive:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in archive.namelist():
            root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in root.findall(f"{{{MAIN_NS}}}si"):
                shared.append("".join(node.text or "" for node in item.iter(f"{{{MAIN_NS}}}t")))
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        targets = {
            item.attrib["Id"]: item.attrib["Target"]
            for item in rels.findall(f"{{{PKG_REL_NS}}}Relationship")
        }
        result: dict[str, str] = {}
        sheets = workbook.find(f"{{{MAIN_NS}}}sheets")
        for sheet in sheets or []:
            name = sheet.attrib["name"]
            rel_id = sheet.attrib[f"{{{REL_NS}}}id"]
            target = targets[rel_id].replace("\\", "/")
            worksheet_path = target.lstrip("/") if target.startswith("/xl/") else f"xl/{target.lstrip('/')}"
            root = ET.fromstring(archive.read(worksheet_path))
            values: list[str] = []
            for cell in root.iter(f"{{{MAIN_NS}}}c"):
                cell_type = cell.attrib.get("t")
                if cell_type == "inlineStr":
                    value = "".join(node.text or "" for node in cell.iter(f"{{{MAIN_NS}}}t"))
                else:
                    node = cell.find(f"{{{MAIN_NS}}}v")
                    value = node.text if node is not None and node.text is not None else ""
                    if cell_type == "s" and value:
                        value = shared[int(value)]
                if value:
                    values.append(value)
            result[name] = "\n".join(values)
        return result


def summary_text(path: Path) -> dict[str, str]:
    suffix = path.suffix.lower()
    if suffix == ".xlsx":
        return xlsx_text(path)
    if suffix in (".md", ".txt"):
        return {path.stem: path.read_text(encoding="utf-8")}
    if suffix == ".json":
        data = load_json(path)
        tables = data.get("tables")
        if isinstance(tables, list):
            return {
                str(item.get("name", f"table-{index}")): json.dumps(item, ensure_ascii=False)
                for index, item in enumerate(tables)
                if isinstance(item, dict)
            }
        return {path.stem: json.dumps(data, ensure_ascii=False)}
    raise ValueError("用户汇总只支持 xlsx、md、txt 或 json")


def has_group(text: str, alternatives: tuple[str, ...]) -> bool:
    return any(value in text for value in alternatives)


def scoped_text(sheets: dict[str, str], name_terms: tuple[str, ...]) -> str:
    selected = [text for name, text in sheets.items() if any(term in name for term in name_terms)]
    if selected:
        return "\n".join(selected)
    if len(sheets) == 1:
        return next(iter(sheets.values()))
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="检查管网用户可见汇总是否暴露必要业务维度")
    parser.add_argument("--inference", required=True, type=Path)
    parser.add_argument("--summary", required=True, type=Path)
    args = parser.parse_args()
    errors: list[str] = []
    checks: dict[str, bool] = {}
    try:
        inference = load_json(args.inference.resolve())
        sheets = summary_text(args.summary.resolve())
        all_text = "\n".join(f"{name}\n{text}" for name, text in sheets.items())
        metrics = inference.get("metrics", {})
        pipe_text = scoped_text(sheets, ("管道长度", "DN分层", "管网长度"))
        checks["pipe_length_dimensions"] = all(
            has_group(pipe_text, group)
            for group in (
                ("系统",),
                ("DN", "规格"),
                ("数量层级", "确认量", "规则推算量", "方案暂估量", "未决量"),
                ("长度", "中心线"),
            )
        )
        if not checks["pipe_length_dimensions"]:
            errors.append("用户汇总缺少管道系统、DN、数量层级或长度维度")

        if metrics.get("topology_bridge_count", 0):
            gap_text = scoped_text(sheets, ("断口", "虚拟桥"))
            checks["gap_dimensions"] = all(
                has_group(gap_text, group)
                for group in (
                    ("断口", "虚拟桥"),
                    ("分类",),
                    ("方案", "是否计", "计入"),
                    ("增量", "数量影响", "虚拟长度"),
                )
            )
            if not checks["gap_dimensions"]:
                errors.append("存在断口候选，但用户汇总缺少分类、方案或数量影响")

        if metrics.get("valve_count", 0):
            valve_text = scoped_text(sheets, ("阀门", "附件"))
            checks["valve_dimensions"] = all(
                has_group(valve_text, group)
                for group in (
                    ("阀门", "在线附件"),
                    ("块族", "图块族"),
                    ("候选类型", "阀门类型", "附件类型"),
                    ("DN", "规格"),
                    ("实例数", "数量"),
                    ("数量层级", "确认量", "规则推算量", "方案暂估量", "未决量"),
                )
            )
            if not checks["valve_dimensions"]:
                errors.append("存在阀门或附件实例，但用户汇总缺少块族、候选类型、DN或数量层级")

        if metrics.get("fitting_candidate_count", 0):
            fitting_text = scoped_text(sheets, ("管件", "变径"))
            checks["fitting_dimensions"] = all(
                has_group(fitting_text, group)
                for group in (
                    ("管件", "变径"),
                    ("管件类型", "节点类型", "候选类型"),
                    ("DN组合", "上下游规格", "连接规格"),
                    ("候选数", "实例数", "数量"),
                    ("数量层级", "确认量", "规则推算量", "方案暂估量", "未决量"),
                )
            )
            if not checks["fitting_dimensions"]:
                errors.append("存在管件候选，但用户汇总缺少类型、DN组合或数量层级")

        output = {
            "status": "PASS" if not errors else "FAIL",
            "summary": str(args.summary.resolve()),
            "inference": str(args.inference.resolve()),
            "sheet_names": list(sheets),
            "checks": checks,
            "errors": errors,
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
        return 0 if not errors else 2
    except (OSError, ValueError, json.JSONDecodeError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(json.dumps({"status": "FAIL", "errors": [str(exc)]}, ensure_ascii=False, indent=2))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
