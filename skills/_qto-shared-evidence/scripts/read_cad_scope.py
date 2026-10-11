#!/usr/bin/env python3
"""Bounded question evidence from an existing, source-verified CAD index.

The response is a page of observations, not an answer about engineering meaning.
Complete records and coverage ledgers live in immutable, source-bound artifacts.
No DXF rebuild, professional classification, or engineering default is performed.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import itertools
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile

import qto_recognition_workbench as workbench


SCHEMA = "qto-cad-scope-response/1.0"
PACK_SCHEMA = "qto-cad-scope-pack/1.0"
DEFAULT_MAX_OBJECTS = 60
DEFAULT_MAX_BYTES = 24000
HARD_MAX_OBJECTS = 1000
HARD_MAX_BYTES = 262144
MIN_MAX_BYTES = 1024
QUERY_FIELDS = {
    "question", "bounds", "object_ids", "text_pattern", "entity_types", "layers",
    "include_descendants", "context_padding_raw", "neighbor_distance_raw",
}
ZERO_HASH = "0" * 64


def _encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def serialize_response(response):
    """The CLI wire format: compact UTF-8 JSON, including one trailing newline."""
    data = _encoded(response)
    if response.get("response_bytes") != len(data):
        raise ValueError("response_bytes 与实际响应编码不一致")
    return data


def _size(response):
    response["response_bytes"] = 0
    for _ in range(10):
        size = len(_encoded(response))
        if response["response_bytes"] == size:
            return size
        response["response_bytes"] = size
    raise ValueError("响应字节数无法稳定")


def _integer(value, name, minimum, maximum):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f"{name} 必须为 {minimum} 到 {maximum} 的整数")


def _query(query):
    if not isinstance(query, dict) or set(query) - QUERY_FIELDS:
        raise ValueError("query 含未支持字段；分页限制通过独立参数传入")
    if not isinstance(query.get("question"), str) or not query["question"].strip():
        raise ValueError("question 必须说明本次需要取证的问题")
    for name in ("object_ids", "entity_types", "layers"):
        if name in query:
            values = query[name]
            if (not isinstance(values, list) or any(not isinstance(v, str) or not v for v in values)
                    or len(set(values)) != len(values)):
                raise ValueError(f"{name} 必须是不重复的非空字符串数组")
    if "include_descendants" in query and not isinstance(query["include_descendants"], bool):
        raise ValueError("include_descendants 必须是布尔值")
    for name in ("context_padding_raw", "neighbor_distance_raw"):
        if name in query:
            workbench._number(query[name], name)
    if "text_pattern" in query and not isinstance(query["text_pattern"], str):
        raise ValueError("text_pattern 必须是字符串")
    try:
        if query.get("text_pattern"):
            re.compile(query["text_pattern"])
    except re.error as exc:
        raise ValueError("text_pattern 不是有效正则表达式") from exc
    # Reject non-JSON and non-finite parameters before deriving cache identities.
    _encoded(query)


def _select(index, objects, query):
    bounds = query.get("bounds")
    if bounds is not None:
        if (not isinstance(bounds, list) or len(bounds) != 4
                or any(isinstance(n, bool) or not isinstance(n, (int, float)) or not math.isfinite(n) for n in bounds)
                or bounds[0] > bounds[2] or bounds[1] > bounds[3]):
            raise ValueError("bounds 需要 [xmin,ymin,xmax,ymax] 有限范围")
        indexed = index.get("index_scope", {}).get("bounds")
        if indexed and any(bounds[i] < indexed[i] if i < 2 else bounds[i] > indexed[i] for i in range(4)):
            raise ValueError("查询超出已索引局部范围；须另行扩大索引，不能将未展开区域当作空图")
    ids = query.get("object_ids")
    if ids is not None and any(oid not in objects for oid in ids):
        raise ValueError("object_ids 引用了不存在的真实实例")
    wanted = None if ids is None else set(ids)
    pattern = re.compile(query["text_pattern"]) if query.get("text_pattern") else None
    rows = list(objects.values())
    selected = [r for r in rows if (wanted is None or r["object_id"] in wanted)
                and (bounds is None or workbench._intersects(r.get("bbox"), bounds))
                and (pattern is None or pattern.search(r.get("text") or ""))
                and (not query.get("entity_types") or r["entity_type"] in query["entity_types"])
                and (not query.get("layers") or r["effective_layer"] in query["layers"])]
    if query.get("include_descendants"):
        parents = {r["object_id"] for r in selected}

        def has_selected_parent(oid):
            while True:
                if oid in parents:
                    return True
                if "/" not in oid:
                    return False
                oid = oid.rsplit("/", 1)[0]

        selected = [r for r in rows if has_selected_parent(r["object_id"])]
    if "context_padding_raw" in query:
        padding = query["context_padding_raw"]
        seed_ids = {r["object_id"] for r in selected}
        windows = [[r["bbox"][0] - padding, r["bbox"][1] - padding,
                    r["bbox"][2] + padding, r["bbox"][3] + padding] for r in selected if r.get("bbox")]
        # Context expands the question's targets. An unlocated seed has no
        # usable search window, but remains evidence that needs interpretation.
        selected = [r for r in rows if r["object_id"] in seed_ids
                    or any(workbench._intersects(r.get("bbox"), b) for b in windows)]
    return sorted(selected, key=lambda r: r["object_id"])


def _protected_files(index, index_path=None, query_path=None):
    protected = {Path(p).resolve() for p in (index_path, query_path) if p is not None}
    bases = {Path.cwd(), Path(index_path).resolve().parent if index_path else Path.cwd()}

    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if isinstance(item, str) and (key in {"path", "file"} or key.endswith("_path")):
                    path = Path(item)
                    protected.update((base / path).resolve() for base in bases)
                elif isinstance(item, (dict, list)):
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    # Existing index object source_path arrays are entity identities, not files.
    visit({k: v for k, v in index.items() if k != "objects"})
    protected.update(Path(str(p) + ".export.json") for p in list(protected) if p.suffix.lower() == ".dxf")
    return protected


class _Store:
    def __init__(self, root, protected):
        self.root = Path(root).resolve()
        self.protected = protected
        if self.root.exists() and not self.root.is_dir():
            raise ValueError("artifact_dir 必须是目录")

    def guard(self, path, *, external=False):
        path = Path(path).resolve()
        if not external and not path.is_relative_to(self.root):
            raise ValueError("证据产物路径越出 artifact_dir")
        for source in self.protected:
            if path == source or (path.exists() and source.exists() and path.samefile(source)):
                raise ValueError("输出不能覆盖 index、query、来源或已有输入证据")
        if path.exists() and not path.is_file():
            raise ValueError("输出路径不是普通文件")
        return path

    def output(self, path):
        """Explicit response/preview files may not replace any generated evidence."""
        path = self.guard(path, external=True)
        if path.is_relative_to(self.root):
            parts = path.relative_to(self.root).parts
            if parts and re.fullmatch(r"scope-[0-9a-f]{64}", parts[0]):
                raise ValueError("显式输出不能覆盖取证包证据或占用其保留目录")
        # resolve() handles symbolic links; hard links need inode comparison.
        if path.exists() and path.stat().st_nlink > 1:
            for folder in self.root.glob("scope-*"):
                if not re.fullmatch(r"scope-[0-9a-f]{64}", folder.name) or folder.is_symlink():
                    continue
                for evidence in folder.rglob("*"):
                    if evidence.is_file() and path.samefile(evidence):
                        raise ValueError("显式输出不能覆盖取证包证据的硬链接")
        return path

    def write(self, path, chunks):
        """Compare derived bytes when reusing artifacts; never trust a cache label."""
        path = self.guard(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256()
        temporary = None
        stream = None
        try:
            if not path.exists():
                fd, name = tempfile.mkstemp(prefix=".scope-", dir=path.parent)
                temporary = Path(name)
                stream = os.fdopen(fd, "wb")
            for chunk in chunks:
                digest.update(chunk)
                if stream is not None:
                    stream.write(chunk)
            if stream is not None:
                stream.close()
                stream = None
            expected = digest.hexdigest()
            if temporary is not None:
                # A concurrent identical job may already have produced the file.
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    pass
            self.guard(path)
            if _file_hash(path) != expected:
                raise ValueError(f"已有取证产物已改变，不能沿用或覆盖；请使用新 artifact_dir: {path}")
            return {"path": str(path), "sha256": expected}
        finally:
            if stream is not None:
                stream.close()
            if temporary is not None:
                temporary.unlink(missing_ok=True)

    def json(self, path, value):
        return self.write(path, [_encoded(value)])


def _cursor(binding, offset):
    value = {"version": 1, "binding": binding, "offset": offset}
    value["checksum"] = _digest(value)
    return base64.urlsafe_b64encode(_encoded(value)).decode("ascii").rstrip("=")


def _offset(cursor, binding, count):
    if cursor is None:
        return 0
    try:
        if not isinstance(cursor, str) or len(cursor) > 1024 or not re.fullmatch(r"[A-Za-z0-9_-]+", cursor):
            raise ValueError("cursor 编码无效")
        value = json.loads(base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4)))
        if not isinstance(value, dict) or set(value) != {"version", "binding", "offset", "checksum"}:
            raise ValueError("cursor 结构无效")
        checksum = value.pop("checksum")
        if type(value["version"]) is not int or value["version"] != 1 or checksum != _digest(value) or value["binding"] != binding:
            raise ValueError("cursor 与 index、query、mode 或分页限制不一致")
        position = value["offset"]
        if isinstance(position, bool) or not isinstance(position, int) or not 0 <= position < count:
            raise ValueError("cursor 页位置无效")
        return position
    except (ValueError, TypeError, KeyError, UnicodeError) as exc:
        raise ValueError("cursor 无效或不属于当前读取请求: " + str(exc)) from exc


def _excerpt(value, limit):
    text = str(value)
    return text.encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _summary(row, detail_ref, *, minimal=False):
    result = {key: row.get(key) for key in ("object_id", "entity_type", "geometry_status")}
    result.update(representation="summary", detail_ref=detail_ref,
                  issue_count=len(row.get("issues") or []), semantic_status="uninterpreted")
    if not minimal:
        for key in ("bbox", "anchor", "raw_length", "visible_in_entity"):
            result[key] = row.get(key)
        for key, limit in (("text", 360), ("effective_layer", 120), ("block_name", 120)):
            if row.get(key) is not None:
                result[key] = _excerpt(row[key], limit)
                if result[key] != row[key]:
                    result[key + "_truncated"] = True
        result["geometry_part_count"] = len((row.get("geometry") or {}).get("parts", []))
        result["attribute_count"] = len(row.get("attributes") or {})
        result["issues_excerpt"] = [_excerpt(v, 160) for v in (row.get("issues") or [])[:2]]
    return result


def _neighbors(rows, distance):
    """Match workbench line-part candidates; never infer a junction or connection."""
    grid = {}
    for row in rows:
        for part_index, part in enumerate((row.get("geometry") or {}).get("parts", [])):
            if part["kind"] != "line":
                continue
            for end in ("start", "end"):
                point = part[end]
                key = tuple(math.floor(x / distance) for x in point) if distance else tuple(point)
                cells = (tuple(key[i] + d[i] for i in range(3)) for d in itertools.product((-1, 0, 1), repeat=3)) if distance else [key]
                for cell in cells:
                    for oid, index, endpoint, other in grid.get(cell, []):
                        if oid == row["object_id"]:
                            continue
                        separation = math.dist(point, other)
                        if separation <= distance:
                            yield {"a": {"object_id": oid, "part": index, "end": endpoint},
                                   "b": {"object_id": row["object_id"], "part": part_index, "end": end},
                                   "distance_raw": separation, "status": "candidate_only"}
                grid.setdefault(key, []).append((row["object_id"], part_index, end, point))


def read_scope(index, query, artifact_dir, *, cursor=None, mode="summary",
               max_objects=DEFAULT_MAX_OBJECTS, max_bytes=DEFAULT_MAX_BYTES,
               index_path=None, query_path=None, svg_path=None):
    """Return a bounded page; full records and inventories are actual disk artifacts.

    Cursors bind index/query/mode and both limits. Use serialize_response() for
    the exact byte contract. Source and reused artifact bytes are rechecked
    on every call; existing index reuse does not mean incremental DXF parsing.
    """
    _integer(max_objects, "max_objects", 1, HARD_MAX_OBJECTS)
    _integer(max_bytes, "max_bytes", MIN_MAX_BYTES, HARD_MAX_BYTES)
    if mode not in {"summary", "detail"}:
        raise ValueError("mode 必须为 summary 或 detail")
    _query(query)
    objects = workbench._verified_index(index)
    if len(objects) != len(index["objects"]):
        raise ValueError("索引存在重复实例身份")
    if index_path is not None:
        stored = workbench._json(index_path)
        if stored.get("index_sha256") != index["index_sha256"] or workbench.index_digest(stored) != index["index_sha256"]:
            raise ValueError("index_path 与当前 index 不一致")
    if query_path is not None and workbench._json(query_path) != query:
        raise ValueError("query_path 与当前 query 不一致")
    selected = _select(index, objects, query)
    query_digest = _digest(query)
    pack_digest = _digest({"schema": PACK_SCHEMA, "index": index["index_sha256"], "query": query_digest})
    binding = _digest({"schema": SCHEMA, "pack": pack_digest, "mode": mode,
                       "max_objects": max_objects, "max_bytes": max_bytes})
    start = _offset(cursor, binding, len(selected))
    store = _Store(artifact_dir, _protected_files(index, index_path, query_path))
    folder = store.root / ("scope-" + pack_digest)
    if svg_path is not None:
        svg_path = store.output(svg_path)
    unlocated = sorted((r for r in objects.values() if r.get("bbox") is None), key=lambda r: r["object_id"])
    selected_ids = {r["object_id"] for r in selected}
    outside = sorted((r for r in objects.values() if r["object_id"] not in selected_ids), key=lambda r: r["object_id"])
    records = {
        "selected": store.write(folder / "selected.jsonl", (_encoded(r) for r in selected)),
        "not_selected": store.write(folder / "not-selected.jsonl", (_encoded({"object_id": r["object_id"],
            "entity_type": r["entity_type"], "geometry_status": r.get("geometry_status"),
            "meaning": "not_selected_by_query; not_proof_of_absence"}) for r in outside)),
        "unlocated": store.write(folder / "unlocated.jsonl", (_encoded(r) for r in unlocated)),
    }
    counts = {"indexed": len(objects), "matched": len(selected), "not_selected": len(outside),
              "unlocated": len(unlocated), "matched_with_issues": sum(bool(r.get("issues")) for r in selected)}
    manifest = {"schema_version": PACK_SCHEMA, "index_sha256": index["index_sha256"],
                "query_sha256": query_digest, "query": query, "source": index["source"],
                "authoritative_source": index["authoritative_source"], "layout": index["layout"],
                "raw_unit": index["raw_unit"], "index_scope": index.get("index_scope"),
                "source_identity_issues": index["source_identity_issues"],
                "conversion_warnings": index.get("conversion_warnings", []), "counts": counts, "records": records,
                "meaning": "Source-bound observations; query matches and file hashes do not confirm engineering meaning. Unlocated records may overlap selected records."}
    manifest_ref = store.json(folder / "manifest.json", manifest)
    page_objects = []
    payloads = []

    def response_for(shown):
        end = start + len(shown)
        name = binding + f"-{start}-{end}"
        response = {"schema_version": SCHEMA, "question": query["question"], "answer_status": "evidence_only_model_interpretation_required",
                    "unanswered_question_count": 1, "index_sha256": index["index_sha256"], "query_sha256": query_digest,
                    "source_sha256": index["source"]["sha256"], "authoritative_source_sha256": index["authoritative_source"]["sha256"],
                    "index_reuse": "verified_existing_index_no_dxf_rebuild", "layout": index["layout"], "raw_unit": index["raw_unit"],
                    "index_scope": index.get("index_scope"), "mode": mode, "objects": shown,
                    "page": {"offset": start, "returned_count": len(shown), "matched_count": len(selected),
                             "previously_returned_count": start, "remaining_count": len(selected) - end,
                             "not_returned_in_page_count": len(selected) - len(shown),
                             "next_cursor": _cursor(binding, end) if end < len(selected) else None},
                    "coverage": {"unlocated_count": len(unlocated), "matched_with_issues_count": counts["matched_with_issues"],
                                 "source_identity_issue_count": len(index["source_identity_issues"]),
                                 "conversion_warning_count": len(index.get("conversion_warnings", [])),
                                 "unlocated_may_affect_query": bool(unlocated), "engineering_coverage_complete": False,
                                 "meaning": "Returned evidence only; unread pages, unlocated objects and semantic interpretation remain separate."},
                    "manifest_ref": manifest_ref, "page_inventory_ref": {"path": str(folder / "pages" / (name + ".json")), "sha256": ZERO_HASH},
                    "limits": {"max_objects": max_objects, "max_bytes": max_bytes, "encoding": "compact_utf8_json_with_newline"}}
        if "neighbor_distance_raw" in query:
            response["neighbors"] = {"coverage": "returned_page_line_part_endpoints_only", "status": "candidate_only",
                                     "cross_page_pairs_evaluated": False, "candidate_count": 9999999999999999999,
                                     "artifact_ref": {"path": str(folder / "pages" / (name + "-neighbors.jsonl")), "sha256": ZERO_HASH}}
        else:
            response["neighbors"] = {"coverage": "not_computed", "cross_page_pairs_evaluated": False}
        if svg_path is not None:
            response["preview"] = {"path": str(svg_path), "sha256": ZERO_HASH, "coverage": "returned_page_only"}
        _size(response)
        return response

    for row in selected[start:start + max_objects]:
        payload = _encoded(row)
        detail_path = folder / "objects" / (hashlib.sha256(row["object_id"].encode("utf-8")).hexdigest() + ".json")
        detail_ref = {"path": str(detail_path), "sha256": hashlib.sha256(payload).hexdigest()}
        shown = (_summary(row, detail_ref) if mode == "summary" else
                 {**row, "representation": "detail", "semantic_status": "uninterpreted", "detail_ref": detail_ref})
        candidate = response_for(page_objects + [shown])
        if candidate["response_bytes"] > max_bytes:
            if page_objects:
                break
            shown = _summary(row, detail_ref)
            shown["detail_delivery_reason"] = "response_byte_budget"
            candidate = response_for([shown])
            if candidate["response_bytes"] > max_bytes:
                shown = _summary(row, detail_ref, minimal=True)
                shown["detail_delivery_reason"] = "response_byte_budget"
                candidate = response_for([shown])
            if candidate["response_bytes"] > max_bytes:
                raise ValueError(f"max_bytes={max_bytes} 不足以容纳 metadata 和一个对象引用；本页至少需要 {candidate['response_bytes']} 字节")
        page_objects.append(shown)
        payloads.append((detail_path, payload, row))
    response = response_for(page_objects)
    if response["response_bytes"] > max_bytes:
        raise ValueError(f"max_bytes={max_bytes} 不足以容纳最小 metadata；至少需要 {response['response_bytes']} 字节")
    for path, payload, _ in payloads:
        store.write(path, [payload])
    if "neighbor_distance_raw" in query:
        neighbor_count = 0

        def chunks():
            nonlocal neighbor_count
            for item in _neighbors([r for _, _, r in payloads], query["neighbor_distance_raw"]):
                neighbor_count += 1
                yield _encoded(item)

        response["neighbors"]["artifact_ref"] = store.write(response["neighbors"]["artifact_ref"]["path"], chunks())
        response["neighbors"]["candidate_count"] = neighbor_count
    end = start + len(page_objects)
    omitted_ranges = ([[1, start]] if start else []) + ([[end + 1, len(selected)]] if end < len(selected) else [])
    inventory = {"schema_version": "qto-cad-scope-page-inventory/1.0", "index_sha256": index["index_sha256"],
                 "query_sha256": query_digest, "binding": binding, "manifest_ref": manifest_ref,
                 "selected_records_ref": records["selected"], "unlocated_records_ref": records["unlocated"],
                 "returned_object_ids": [r["object_id"] for r in page_objects],
                 "returned_line_range": [start + 1, end] if end > start else None,
                 "omitted_line_ranges": omitted_ranges, "line_numbering": "one_based_inclusive",
                 "neighbors": response["neighbors"], "meaning": "Omitted ranges resolve every matched object not returned on this page; unlocated records are separate and may overlap."}
    response["page_inventory_ref"] = store.json(response["page_inventory_ref"]["path"], inventory)
    if svg_path is not None:
        preview = workbench.render_svg({"objects": [r for _, _, r in payloads], "query": query,
                                        "index_sha256": index["index_sha256"]}).encode("utf-8")
        svg_path.parent.mkdir(parents=True, exist_ok=True)
        store.output(svg_path)
        svg_path.write_bytes(preview)
        response["preview"]["sha256"] = hashlib.sha256(preview).hexdigest()
    if _size(response) > max_bytes:
        raise ValueError("内部错误：最终响应超过 max_bytes")
    return response


def main():
    parser = argparse.ArgumentParser(description="从已有 CAD 索引按问题读取有字节上限的证据页")
    parser.add_argument("--index", type=Path, required=True)
    parser.add_argument("--query", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--cursor")
    parser.add_argument("--mode", choices=("summary", "detail"), default="summary")
    parser.add_argument("--max-objects", type=int, default=DEFAULT_MAX_OBJECTS)
    parser.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    parser.add_argument("--svg", type=Path)
    args = parser.parse_args()
    try:
        index, query = workbench._json(args.index), workbench._json(args.query)
        store = _Store(args.artifact_dir, _protected_files(index, args.index, args.query))
        output = store.output(args.output)
        if args.svg is not None and (output == args.svg.resolve() or
                (output.exists() and args.svg.exists() and output.samefile(args.svg))):
            raise ValueError("JSON 与 SVG 输出不能使用同一路径")
        response = read_scope(index, query, args.artifact_dir, cursor=args.cursor, mode=args.mode,
                              max_objects=args.max_objects, max_bytes=args.max_bytes,
                              index_path=args.index, query_path=args.query, svg_path=args.svg)
        data = serialize_response(response)
        output.parent.mkdir(parents=True, exist_ok=True)
        store.output(output)
        output.write_bytes(data)
        sys.stdout.buffer.write(data)
        return 0
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
