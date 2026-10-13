"""Source-backed tests of bounded evidence delivery, not component recognition."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import ezdxf
import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "skills" / "_qto-shared-evidence" / "scripts"
sys.path.insert(0, str(SHARED))
import qto_recognition_workbench as workbench
import read_cad_scope as reader


def put(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    return path


def make_index(tmp_path, *, lines=5, unknown=0, points=None, name="drawing"):
    doc = ezdxf.new()
    doc.units = 4
    model = doc.modelspace()
    for number in range(lines):
        model.add_line((number * 10, 0), (number * 10 + 10, 0), dxfattribs={"layer": "路线观察"})
    for number in range(unknown):
        model.add_spline(fit_points=[(number, 1), (number + 1, 4), (number + 2, 1)])
    if points is not None:
        model.add_lwpolyline(points, dxfattribs={"layer": "复杂轮廓"})
    source = tmp_path / (name + ".dxf")
    doc.saveas(source)
    index = workbench.build_index({"path": str(source), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()})
    index_path = put(tmp_path / (name + "-index.json"), index)
    return index, index_path


def load_ref(ref):
    path = Path(ref["path"])
    assert path.is_file()
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == ref["sha256"]
    if path.suffix == ".jsonl":
        return [json.loads(line) for line in data.splitlines()]
    return json.loads(data)


def check_response(response, budget, artifact_dir):
    data = reader.serialize_response(response)
    assert len(data) == response["response_bytes"] <= budget
    assert len(response["objects"]) <= response["limits"]["max_objects"]
    assert response["answer_status"] == "evidence_only_model_interpretation_required"
    assert response["unanswered_question_count"] == 1
    assert response["coverage"]["engineering_coverage_complete"] is False
    assert "omitted_object_ids" not in response
    assert "unlocated_object_ids" not in response
    manifest = load_ref(response["manifest_ref"])
    inventory = load_ref(response["page_inventory_ref"])
    for item in response["objects"]:
        assert Path(item["detail_ref"]["path"]).resolve().is_relative_to(artifact_dir.resolve())
        full = load_ref(item["detail_ref"])
        assert full["object_id"] == item["object_id"]
    return manifest, inventory


def test_large_index_keeps_selected_and_unknown_in_files(tmp_path):
    index, index_path = make_index(tmp_path, lines=1500, unknown=250)
    artifacts = tmp_path / "取证"
    result = reader.read_scope(index, {"question": "哪些对象还需要图面解释？"}, artifacts,
                               max_objects=7, max_bytes=8000, index_path=index_path)
    manifest, page = check_response(result, 8000, artifacts)
    selected = load_ref(manifest["records"]["selected"])
    unlocated = load_ref(manifest["records"]["unlocated"])
    assert len(selected) == 1750
    assert len(unlocated) == result["coverage"]["unlocated_count"] == 250
    assert len(result["objects"]) == 7
    assert result["page"]["remaining_count"] == 1743
    assert page["omitted_line_ranges"] == [[8, 1750]]
    assert all("geometry" not in row for row in result["objects"])
    assert "object_ids" not in manifest  # Full inventories are separate actual files.


def test_all_pages_partition_actual_source_objects_without_loss_or_duplicates(tmp_path):
    index, index_path = make_index(tmp_path, lines=173)
    query = {"question": "逐页查看选定路线的图元依据", "layers": ["路线观察"]}
    artifacts = tmp_path / "pages"
    actual, cursor, tokens = [], None, set()
    while True:
        result = reader.read_scope(index, query, artifacts, cursor=cursor, max_objects=11,
                                   max_bytes=12000, index_path=index_path)
        manifest, page = check_response(result, 12000, artifacts)
        all_selected = load_ref(manifest["records"]["selected"])
        ids = [row["object_id"] for row in result["objects"]]
        assert ids == page["returned_object_ids"]
        assert result["page"]["previously_returned_count"] == len(actual)
        omitted = [all_selected[n - 1]["object_id"] for first, last in page["omitted_line_ranges"]
                   for n in range(first, last + 1)]
        assert not set(ids) & set(omitted)
        assert set(ids) | set(omitted) == {r["object_id"] for r in all_selected}
        actual.extend(ids)
        cursor = result["page"]["next_cursor"]
        if cursor is None:
            break
        assert ids and cursor not in tokens
        tokens.add(cursor)
    assert actual == sorted(row["object_id"] for row in index["objects"])
    assert len(actual) == len(set(actual)) == 173
    assert result["page"]["remaining_count"] == 0
    assert result["unanswered_question_count"] == 1  # Evidence paging does not answer semantics.


def test_single_large_polyline_detail_moves_to_full_file(tmp_path):
    points = [(n, n % 2 * 3) for n in range(6000)]
    index, _ = make_index(tmp_path, lines=0, points=points)
    artifacts = tmp_path / "complex"
    result = reader.read_scope(index, {"question": "完整曲线怎样构成？"}, artifacts, mode="detail", max_bytes=6000)
    check_response(result, 6000, artifacts)
    row = result["objects"][0]
    assert row["representation"] == "summary"
    assert row["detail_delivery_reason"] == "response_byte_budget"
    full = load_ref(row["detail_ref"])
    assert full == index["objects"][0]
    assert len(full["geometry"]["parts"]) == 5999
    assert Path(row["detail_ref"]["path"]).stat().st_size > 6000
    assert result["page"]["next_cursor"] is None


def test_small_detail_is_inline_and_lossless(tmp_path):
    index, _ = make_index(tmp_path, lines=2)
    result = reader.read_scope(index, {"question": "核对中心线坐标和源记录"}, tmp_path / "detail", mode="detail")
    assert all(r["representation"] == "detail" for r in result["objects"])
    for row in result["objects"]:
        full = load_ref(row["detail_ref"])
        assert {key: row[key] for key in full} == full


def test_separate_context_windows_keep_remote_header_and_split_code(tmp_path):
    doc=ezdxf.new();doc.units=4;m=doc.modelspace()
    labels=[m.add_text(t,dxfattribs={'insert':xy}) for t,xy in
            [('Floor',(20,10000)),('F',(20,0)),('1',(30,0)),('Other',(500,0))]]
    source=tmp_path/'table.dxf';doc.saveas(source)
    index=workbench.build_index({'path':str(source),'sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    q={'question':'read model-selected table windows','context_regions':[
       {'id':'column','role':'model_selected_heading','bounds':[10,9990,50,10010]},
       {'id':'cell','role':'model_selected_code','bounds':[10,-10,50,10]},
       {'id':'row','role':'model_selected_row','bounds':[0,-20,100,20]}]}
    cursor=None;seen=[];refs=[]
    while True:
        response=reader.read_scope(index,q,tmp_path/'contexts',cursor=cursor,max_objects=1,max_bytes=10000)
        check_response(response,10000,tmp_path/'contexts')
        context=load_ref(response['context_regions_ref']);refs.append(response['context_regions_ref'])
        for row in response['objects']:
            seen.append(row['object_id'])
            assert row['context_region_ids']==[r['id'] for r in context['regions'] if row['object_id'] in r['object_ids']]
        cursor=response['page']['next_cursor']
        if not cursor:break
    assert set(seen)=={'CADINST-Model/'+e.dxf.handle for e in labels[:3]}
    assert len(seen)==3 and all(r==refs[0] for r in refs)
    assert all(r['semantic_status']=='model_selected_context_not_verified_table_structure' for r in context['regions'])


def test_large_context_object_retains_membership_in_summary_fallback(tmp_path):
    index,_=make_index(tmp_path,lines=0,points=[(n,n%2*3) for n in range(6000)])
    q={'question':'bounded complex member','context_regions':[
        {'id':'cell','role':'model_selected_area','bounds':[-1,-1,6001,5]}]}
    response=reader.read_scope(index,q,tmp_path/'context-large',mode='detail',max_bytes=6500)
    check_response(response,6500,tmp_path/'context-large')
    assert response['objects'][0]['representation']=='summary'
    assert response['objects'][0]['context_region_ids']==['cell']


@pytest.mark.parametrize('regions', [[],[{'id':'x','role':'row'}],
    [{'id':'x','role':'row','bounds':[0,0,1,1]},{'id':'x','role':'column','bounds':[0,0,1,1]}],
    [{'id':'x','role':'row','bounds':[2,0,1,1]}]])
def test_invalid_context_windows_stop(tmp_path,regions):
    index,_=make_index(tmp_path)
    with pytest.raises(ValueError):
        reader.read_scope(index,{'question':'invalid context','context_regions':regions},tmp_path/'invalid-context')


def test_unlocated_objects_remain_pending_when_spatial_query_matches_nothing(tmp_path):
    index, _ = make_index(tmp_path, lines=3, unknown=4)
    query = {"question": "此窗口是否有相关对象？", "bounds": [1000, 1000, 2000, 2000]}
    artifacts = tmp_path / "empty-window"
    result = reader.read_scope(index, query, artifacts)
    manifest, page = check_response(result, 24000, artifacts)
    assert result["objects"] == []
    assert result["page"]["next_cursor"] is None
    assert result["coverage"]["unlocated_may_affect_query"] is True
    assert result["coverage"]["unlocated_count"] == 4
    assert len(load_ref(manifest["records"]["not_selected"])) == 7
    assert len(load_ref(page["unlocated_records_ref"])) == 4


def test_context_padding_keeps_explicit_unlocated_seed_and_expands_located_seed(tmp_path):
    index, index_path = make_index(tmp_path, lines=3, unknown=1)
    unknown = next(row for row in index["objects"] if row["entity_type"] == "SPLINE")
    assert unknown["bbox"] is None
    first_line = next(row for row in index["objects"] if row["entity_type"] == "LINE")
    for seeds in ([unknown["object_id"]], [unknown["object_id"], first_line["object_id"]]):
        query = {"question": "保留明确指定但尚不能定位的曲线，并读取邻域依据", "object_ids": seeds,
                 "context_padding_raw": 100}
        artifacts = tmp_path / "seed-context"
        response = reader.read_scope(index, query, artifacts, index_path=index_path)
        manifest, _ = check_response(response, 24000, artifacts)
        returned = {row["object_id"] for row in response["objects"]}
        expected = set(seeds) if len(seeds) == 1 else {row["object_id"] for row in index["objects"]}
        assert returned == expected
        assert {row["object_id"] for row in load_ref(manifest["records"]["selected"])} == expected
        assert response["coverage"]["unlocated_count"] == 1
        assert load_ref(next(row["detail_ref"] for row in response["objects"]
                             if row["object_id"] == unknown["object_id"])) == unknown


@pytest.mark.parametrize("change", ["question", "bounds", "types", "mode", "objects_limit", "bytes_limit", "index"])
def test_cursor_rejects_another_request(tmp_path, change):
    index, _ = make_index(tmp_path)
    query = {"question": "查看此段的来源几何"}
    artifacts = tmp_path / "bound"
    first = reader.read_scope(index, query, artifacts, max_objects=1)
    kwargs = {"max_objects": 1}
    changed = dict(query)
    if change == "question":
        changed["question"] = "另一个工程问题"
    elif change == "bounds":
        changed["bounds"] = [-1, -1, 100, 100]
    elif change == "types":
        changed["entity_types"] = ["LINE"]
    elif change == "mode":
        kwargs["mode"] = "detail"
    elif change == "objects_limit":
        kwargs["max_objects"] = 2
    elif change == "bytes_limit":
        kwargs["max_bytes"] = 16000
    else:
        index, _ = make_index(tmp_path, name="another")
    with pytest.raises(ValueError, match="cursor"):
        reader.read_scope(index, changed, artifacts, cursor=first["page"]["next_cursor"], **kwargs)


@pytest.mark.parametrize("token", ["!", "not-a-cursor", "e30", "A" * 1025])
def test_malformed_cursors_fail_instead_of_restarting(tmp_path, token):
    index, _ = make_index(tmp_path)
    with pytest.raises(ValueError, match="cursor"):
        reader.read_scope(index, {"question": "查看几何"}, tmp_path / "invalid", cursor=token)


def test_source_change_rejected_even_when_pack_exists(tmp_path):
    index, _ = make_index(tmp_path)
    query = {"question": "继续查看"}
    artifacts = tmp_path / "source-binding"
    first = reader.read_scope(index, query, artifacts, max_objects=1)
    Path(index["source"]["path"]).write_bytes(b"changed source")
    with pytest.raises(ValueError, match="已改变"):
        reader.read_scope(index, query, artifacts, cursor=first["page"]["next_cursor"], max_objects=1)


def test_authoritative_source_change_is_also_rejected(tmp_path):
    index, _ = make_index(tmp_path)
    authoritative = tmp_path / "authoritative.dwg"
    authoritative.write_bytes(b"authoritative identity fixture; not a DWG recognition test")
    index["authoritative_source"] = {"path": str(authoritative), "sha256": hashlib.sha256(authoritative.read_bytes()).hexdigest()}
    index["index_sha256"] = workbench.index_digest(index)
    reader.read_scope(index, {"question": "核对来源"}, tmp_path / "authoritative")
    authoritative.write_bytes(b"changed")
    with pytest.raises(ValueError, match="已改变"):
        reader.read_scope(index, {"question": "核对来源"}, tmp_path / "authoritative")


def test_cache_reused_without_dxf_rebuild_and_modified_pack_rejected(tmp_path, monkeypatch):
    index, index_path = make_index(tmp_path)
    query = {"question": "复用当前证据索引"}
    artifacts = tmp_path / "reuse"
    first = reader.read_scope(index, query, artifacts)  # Same pack usable through API and CLI index_path.
    manifest = load_ref(first["manifest_ref"])
    path = Path(manifest["records"]["selected"]["path"])
    old_stat = path.stat().st_mtime_ns
    monkeypatch.setattr(workbench, "build_index", lambda *a, **k: pytest.fail("reader rebuilt DXF"))
    monkeypatch.setattr(workbench, "read_dxf_with_diagnostics", lambda *a, **k: pytest.fail("reader parsed DXF"))
    second = reader.read_scope(index, query, artifacts, index_path=index_path)
    assert first == second
    assert path.stat().st_mtime_ns == old_stat
    path.write_bytes(b"changed cached evidence\n")
    with pytest.raises(ValueError, match="取证产物已改变"):
        reader.read_scope(index, query, artifacts)


@pytest.mark.parametrize("budget", [1024, 1500])
def test_too_small_budget_errors_without_an_empty_repeating_page(tmp_path, budget):
    index, _ = make_index(tmp_path)
    with pytest.raises(ValueError, match="不足.*metadata"):
        reader.read_scope(index, {"question": "严格预算"}, tmp_path / "too-small", max_bytes=budget)


@pytest.mark.parametrize("kwargs", [{"max_objects": 0}, {"max_objects": True}, {"max_objects": 1001},
                                   {"max_bytes": 262145}, {"max_bytes": 100}, {"max_bytes": True}])
def test_absolute_limits_are_enforced(tmp_path, kwargs):
    index, _ = make_index(tmp_path)
    with pytest.raises(ValueError, match="必须"):
        reader.read_scope(index, {"question": "预算"}, tmp_path / "limits", **kwargs)


def test_long_unicode_question_and_metadata_count_toward_budget(tmp_path):
    index, _ = make_index(tmp_path, lines=1)
    with pytest.raises(ValueError, match="不足"):
        reader.read_scope(index, {"question": "证据问题" * 1000}, tmp_path / "question", max_bytes=6000)


def test_neighbors_are_source_candidates_with_explicit_page_coverage(tmp_path):
    index, _ = make_index(tmp_path, lines=3)
    query = {"question": "这些端点是否存在连接候选？", "neighbor_distance_raw": 0}
    artifacts = tmp_path / "neighbors"
    full = reader.read_scope(index, query, artifacts, max_objects=3)
    check_response(full, 24000, artifacts)
    candidates = load_ref(full["neighbors"]["artifact_ref"])
    assert len(candidates) == full["neighbors"]["candidate_count"] == 2
    assert all(item["status"] == "candidate_only" for item in candidates)
    one = reader.read_scope(index, query, artifacts, max_objects=1)
    assert one["neighbors"]["candidate_count"] == 0
    assert one["neighbors"]["cross_page_pairs_evaluated"] is False
    assert one["neighbors"]["coverage"] == "returned_page_line_part_endpoints_only"
    assert one["page"]["remaining_count"] == 2


def test_existing_query_semantics_and_partial_index_boundary_are_preserved(tmp_path):
    doc = ezdxf.new()
    block = doc.blocks.new("parent")
    block.add_line((0, 0), (10, 0))
    model = doc.modelspace()
    model.add_blockref("parent", (0, 0))
    model.add_text("DN32", dxfattribs={"insert": (5, 1)})
    path = tmp_path / "scope.dxf"
    doc.saveas(path)
    index = workbench.build_index({"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()},
                                  scope_bounds=[-1, -1, 20, 20])
    parent = next(r["object_id"] for r in index["objects"] if r["entity_type"] == "INSERT")
    queries = [{"object_ids": [parent], "include_descendants": True},
               {"text_pattern": "DN32", "context_padding_raw": 10}]
    for number, fields in enumerate(queries):
        actual = reader.read_scope(index, {"question": "按问题看对象", **fields}, tmp_path / str(number))
        expected = workbench.context(index, fields)
        assert [r["object_id"] for r in actual["objects"]] == [r["object_id"] for r in expected["objects"]]
    with pytest.raises(ValueError, match="超出"):
        reader.read_scope(index, {"question": "扩大范围", "bounds": [-2, -2, 30, 30]}, tmp_path / "outside")


def run_cli(index_path, query_path, output, artifacts, *extra):
    return subprocess.run([sys.executable, "-B", str(SHARED / "read_cad_scope.py"), "--index", str(index_path),
                           "--query", str(query_path), "--output", str(output), "--artifact-dir", str(artifacts), *extra],
                          capture_output=True)


def test_cli_output_and_stdout_have_the_same_strict_byte_budget_and_svg(tmp_path):
    index, index_path = make_index(tmp_path, lines=80)
    query_path = put(tmp_path / "query.json", {"question": "查看此区域的线段"})
    output, artifacts, svg = tmp_path / "response.json", tmp_path / "artifacts", tmp_path / "view.svg"
    process = run_cli(index_path, query_path, output, artifacts, "--max-bytes", "8000", "--max-objects", "60", "--svg", str(svg))
    assert process.returncode == 0, process.stderr.decode()
    assert process.stdout == output.read_bytes()
    response = json.loads(process.stdout)
    check_response(response, 8000, artifacts)
    assert len(process.stdout) == response["response_bytes"] <= 8000
    assert hashlib.sha256(svg.read_bytes()).hexdigest() == response["preview"]["sha256"]
    assert index["index_sha256"] in svg.read_text(encoding="utf-8")
    assert response["preview"]["coverage"] == "returned_page_only"


@pytest.mark.parametrize("target", ["index", "query", "source", "receipt"])
def test_cli_cannot_overwrite_inputs(tmp_path, target):
    index, index_path = make_index(tmp_path)
    query_path = put(tmp_path / "query.json", {"question": "文件保护"})
    receipt = Path(index["source"]["path"] + ".export.json")
    receipt.write_bytes(b"conversion receipt")
    output = {"index": index_path, "query": query_path, "source": Path(index["source"]["path"]), "receipt": receipt}[target]
    before = output.read_bytes()
    process = run_cli(index_path, query_path, output, tmp_path / "protected")
    assert process.returncode == 2
    assert output.read_bytes() == before


def test_svg_protects_hardlinked_source_and_declared_evidence(tmp_path):
    index, index_path = make_index(tmp_path)
    source = Path(index["source"]["path"])
    alias = tmp_path / "alias.svg"
    os.link(source, alias)
    before = source.read_bytes()
    with pytest.raises(ValueError, match="不能覆盖"):
        reader.read_scope(index, {"question": "来源保护"}, tmp_path / "hardlink", index_path=index_path, svg_path=alias)
    assert source.read_bytes() == before
    evidence = tmp_path / "evidence.svg"
    evidence.write_bytes(b"prior evidence")
    index["evidence"] = [{"path": str(evidence)}]
    index["index_sha256"] = workbench.index_digest(index)
    with pytest.raises(ValueError, match="不能覆盖"):
        reader.read_scope(index, {"question": "证据保护"}, tmp_path / "evidence", svg_path=evidence)
    assert evidence.read_bytes() == b"prior evidence"


def test_explicit_output_cannot_destroy_a_generated_pack(tmp_path):
    index, index_path = make_index(tmp_path)
    query = {"question": "产物保护"}
    query_path = put(tmp_path / "query.json", query)
    artifacts = tmp_path / "pack"
    response = reader.read_scope(index, query, artifacts)
    target = Path(response["manifest_ref"]["path"])
    before = target.read_bytes()
    process = run_cli(index_path, query_path, target, artifacts)
    assert process.returncode == 2
    assert target.read_bytes() == before


@pytest.mark.parametrize("hardlink", [False, True])
def test_explicit_output_protects_evidence_from_another_query(tmp_path, hardlink):
    index, index_path = make_index(tmp_path)
    artifacts = tmp_path / "evidence-packs"
    first = reader.read_scope(index, {"question": "先前问题的证据"}, artifacts)
    evidence = Path(first["objects"][0]["detail_ref"]["path"])
    before = evidence.read_bytes()
    output = evidence
    if hardlink:
        output = tmp_path / "aliased-evidence.json"
        os.link(evidence, output)
    query_path = put(tmp_path / "different-query.json", {"question": "新的问题"})
    process = run_cli(index_path, query_path, output, artifacts)
    assert process.returncode == 2
    assert evidence.read_bytes() == before


def test_artifact_symlink_cannot_escape_requested_directory(tmp_path):
    index, _ = make_index(tmp_path)
    query = {"question": "目录边界"}
    artifacts = tmp_path / "safe"
    response = reader.read_scope(index, query, artifacts)
    folder = Path(response["manifest_ref"]["path"]).parent
    shutil.rmtree(folder)
    outside = tmp_path / "outside"
    outside.mkdir()
    folder.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="越出"):
        reader.read_scope(index, query, artifacts)
    assert list(outside.iterdir()) == []


def test_changed_index_file_and_unsupported_query_fields_rejected(tmp_path):
    index, index_path = make_index(tmp_path)
    changed = copy.deepcopy(index)
    changed["objects"][0]["raw_length"] += 1
    put(index_path, changed)
    with pytest.raises(ValueError, match="index_path"):
        reader.read_scope(index, {"question": "索引身份"}, tmp_path / "changed-index", index_path=index_path)
    with pytest.raises(ValueError, match="未支持字段"):
        reader.read_scope(index, {"question": "不支持参数", "guessed_thickness": 0.2}, tmp_path / "unsupported")
