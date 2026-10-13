"""Author declarations never turn a review intent into user approval."""
import copy
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / "skills/_qto-shared-evidence/scripts"
sys.path.insert(0, str(SHARED))
import qto_review_contract as contract
from read_qto_review_feedback import build_review_actions


def fixture():
    view = contract.normalize_view({
        "schema": contract.VIEW_SCHEMA, "project_id": "fixture", "review_id": "fixture-review",
        "title": "脱敏反馈", "revision": "1", "sources": [{"id": "S", "title": "图", "sha256": "a" * 64}],
        "scenes": [{"id": "plan", "title": "平面", "source_id": "S", "coordinate_unit": "m", "y_axis": "up", "bounds": [0, 0, 5, 5], "background": []}],
        "objects": [{"id": "O", "catalog_key": "K", "professional": "qto-components-points", "scene_id": "plan", "kind": "point", "points": [[1, 1]], "title": "对象", "category": "复核", "status": "候选", "detail": "脱敏依据", "source": "S", "quantities": [{"name": "实例", "value": 1, "unit": "个", "level": "候选"}], "attributes": {}}],
        "groups": [], "summary": [],
    })
    obj = view["objects"][0]
    package = contract.empty_feedback(view)
    package["annotations"]["K"] = {
        "object_id": "O", "object_ids": ["O"], "object_snapshot": {"catalog_key": "K", "object_id": "O", "result_fingerprint": obj["result_fingerprint"]},
        "confirmed_result": "监督助手评审文字；不是用户确认", "reason": "显式交接；正文不供身份推断",
        "instruction": "确认现结果", "scope": "仅所选对象", "entry_origin": "用户在HTML保存",
        "result_fingerprint": obj["result_fingerprint"], "updated_at": "2026-01-01T00:00:00Z", "history": [],
    }
    return view, package


def node_call(record, save=False):
    code = "require(process.argv[1]);const p=JSON.parse(require('fs').readFileSync(0,'utf8'));try{const r=p.save?QtoReviewCore.saveRecord(undefined,p.record,'K','now'):QtoReviewCore.recordProvenance(p.record);console.log(JSON.stringify({result:r}));}catch(e){console.log(JSON.stringify({error:e.message}));}"
    result = subprocess.run(["node", "-e", code, str(ROOT / "skills/_qto-shared-evidence/assets/review/review-core.js")], input=json.dumps({"record": record, "save": save}, ensure_ascii=False), text=True, capture_output=True, encoding="utf8", check=True)
    return json.loads(result.stdout)


def test_legacy_origin_and_body_do_not_establish_author_or_user_confirmation():
    view, package = fixture()
    feedback, unmatched = contract.validate_feedback(package, view)
    record = feedback["annotations"]["K"]
    assert not unmatched and record["applicability_state"] == "current"
    assert record["author_role"] == record["user_confirmation"] == "unknown"
    action = build_review_actions(feedback)[0]
    assert action["author_role"] == action["user_confirmation"] == "unknown"
    assert action["instruction"] == "确认现结果"
    assert action["review_result"] == action["user_result"] == package["annotations"]["K"]["confirmed_result"]


def test_explicit_assistant_provenance_survives_save_and_reader():
    view, package = fixture()
    record = package["annotations"]["K"]
    record.update(author_role="assistant", author_source="explicit_handoff", author_evidence="脱敏交接明确助手身份", user_confirmation="unconfirmed", user_confirmation_evidence="交接声明尚未由用户确认")
    saved = node_call(record, save=True)["result"]
    assert saved["entry_origin"] == "HTML保存"
    package["annotations"]["K"] = saved
    feedback, _ = contract.validate_feedback(package, view)
    action = build_review_actions(feedback)[0]
    for key in ("author_role", "author_source", "author_evidence", "user_confirmation", "user_confirmation_evidence"):
        assert action[key] == record[key]
    assert action["user_confirmation"] == "unconfirmed"


@pytest.mark.parametrize("fields", [
    {}, {"author_role": "assistant"}, {"author_role": "user", "user_confirmation": "unconfirmed"},
    {"user_confirmation": "confirmed", "user_confirmation_evidence": "用户显式确认记录"},
])
def test_python_javascript_provenance_parity(fields):
    assert node_call(fields)["result"] == contract.record_provenance(fields)


@pytest.mark.parametrize("fields", [
    {"author_role": "system"}, {"author_role": None}, {"author_source": {}},
    {"user_confirmation": True}, {"user_confirmation": "confirmed"},
])
def test_invalid_provenance_and_unsupported_confirmation_fail_in_both_contracts(fields):
    assert "error" in node_call(fields)
    with pytest.raises(ValueError):
        contract.record_provenance(fields)


def test_unknown_old_history_is_kept_without_relabelling():
    _, package = fixture()
    old = package["annotations"]["K"]
    next_record = copy.deepcopy(old)
    next_record.update(author_role="assistant", author_source="html_editor", user_confirmation="unconfirmed")
    code = "require(process.argv[1]);const p=JSON.parse(require('fs').readFileSync(0,'utf8'));console.log(JSON.stringify(QtoReviewCore.saveRecord(p.old,p.next,'K','later')));"
    saved = subprocess.run(["node", "-e", code, str(ROOT / "skills/_qto-shared-evidence/assets/review/review-core.js")], input=json.dumps({"old": old, "next": next_record}), capture_output=True, text=True, check=True)
    result = json.loads(saved.stdout)
    assert result["history"][0]["entry_origin"] == "用户在HTML保存"
    assert result["history"][0].get("author_role", "unknown") == "unknown"
    assert result["author_role"] == "assistant"


def test_reader_cli_retains_explicit_role_without_changing_quantity(tmp_path):
    view, package = fixture()
    package["annotations"]["K"].update(author_role="assistant", author_source="explicit_handoff", user_confirmation="unconfirmed")
    for name, value in (("view.json", view), ("feedback.json", package)):
        (tmp_path / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf8")
    output = tmp_path / "handoff.json"
    result = subprocess.run([sys.executable, str(SHARED / "read_qto_review_feedback.py"), "--view", str(tmp_path / "view.json"), "--feedback", str(tmp_path / "feedback.json"), "--output", str(output)], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    handoff = json.loads(output.read_text(encoding="utf8"))
    assert handoff["quantity_changes"] == 0
    assert handoff["actions"][0]["author_role"] == "assistant"
    assert handoff["legacy_user_fields_are_content_aliases"] is True
    assert handoff["author_metadata_is_explicit_declaration"] is True
