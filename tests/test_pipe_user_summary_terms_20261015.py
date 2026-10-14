"""validate_pipe_user_summary: validator-run statement in the opening and no borrowed human wording.

Registered 20261014-2103 (two entries): a pilot summary left the required validators unrun without
saying so in its opening, and wrote model DN decisions as "人工裁定". Synthetic summaries only.
"""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'skills/qto-pipe-networks/scripts/validate_pipe_user_summary.py'

GOOD = """# 八层给排水 管网成果汇总

必需校验器运行情况：validate_minimum_result.py 已运行并通过；validate_pipe_result.py 已运行并通过。设计说明 PDF 已读取。

## 管道长度（DN分层）

| 系统 | DN | 数量层级 | 中心线长度 m |
|---|---|---|---|
| 废水 | DN25 | 确认量 | 1.8 |

## 识别说明

5 条 DN 文字与管段的对应关系为模型判断，待人工确认。
"""


def run(tmp_path, summary_text, *extra):
    inference = tmp_path / '推算证据.json'
    inference.write_text(json.dumps({'metrics': {}}), encoding='utf-8')
    summary = tmp_path / '汇总.md'
    summary.write_text(summary_text, encoding='utf-8')
    completed = subprocess.run([sys.executable, '-B', str(SCRIPT), '--inference', str(inference), '--summary', str(summary), *extra],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8')
    return completed.returncode, json.loads(completed.stdout)


def test_statement_in_opening_and_model_wording_pass(tmp_path):
    code, report = run(tmp_path, GOOD)
    assert code == 0, report
    assert report['checks']['validator_run_statement'] is True
    assert report['checks']['human_decision_terms'] is True


def test_missing_validator_statement_in_opening_fails(tmp_path):
    moved = GOOD.replace('必需校验器运行情况：validate_minimum_result.py 已运行并通过；validate_pipe_result.py 已运行并通过。', '')
    moved += '\n## 附录\n\nvalidate_minimum_result.py 未运行。\n'
    code, report = run(tmp_path, moved)
    assert code == 2 and report['checks']['validator_run_statement'] is False
    assert any('首段缺少必需校验器运行情况' in error for error in report['errors'])


def test_unrun_validator_declared_in_opening_is_accepted_as_statement(tmp_path):
    declared = GOOD.replace('validate_minimum_result.py 已运行并通过；validate_pipe_result.py 已运行并通过',
                            'validate_minimum_result.py、validate_pipe_result.py 未运行，本成果只作调用链验证，不可交付')
    code, _ = run(tmp_path, declared)
    assert code == 0


def test_model_decision_written_as_human_ruling_fails(tmp_path):
    borrowed = GOOD.replace('为模型判断，待人工确认', '经人工裁定')
    code, report = run(tmp_path, borrowed)
    assert code == 2 and report['checks']['human_decision_terms'] is False
    assert any('人工裁定' in error for error in report['errors'])


def test_pending_human_confirmation_is_not_a_misuse(tmp_path):
    pending = GOOD + '\n主干 DN100 的变径位置需人工确认；立管归属请人工确认后重算。\n'
    code, report = run(tmp_path, pending)
    assert code == 0, report


def test_human_wording_allowed_with_review_evidence(tmp_path):
    borrowed = GOOD.replace('为模型判断，待人工确认', '已人工确认')
    evidence = tmp_path / '人工复核凭据.json'
    evidence.write_text(json.dumps({'schema_version': 'qto-human-review-evidence/1.0', 'records': []}), encoding='utf-8')
    code, report = run(tmp_path, borrowed, '--human-review-evidence', str(evidence))
    assert code == 0, report
    bogus = tmp_path / '假凭据.json'
    bogus.write_text(json.dumps({'schema_version': 'something-else/1.0'}), encoding='utf-8')
    code, report = run(tmp_path, borrowed, '--human-review-evidence', str(bogus))
    assert code == 2 and any('凭据' in error for error in report['errors'])
