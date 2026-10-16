"""成果必交项“插件使用与临时处理”（登记 20261015-0825 主，0759、1407 并入）。

成果用合成混凝土算例（不含项目工程量）；本版缺这一节只报提醒，有这一节就核字段。
"""
import copy, importlib.util, json, subprocess, sys
from pathlib import Path
import jsonschema
import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gct_for_usage', ROOT / 'tests' / 'test_general_capability_transfer.py')
gct = importlib.util.module_from_spec(spec); spec.loader.exec_module(gct)
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
from validate_minimum_result import document_warnings, validate_document  # noqa: E402

ROW = {'环节': '系统图回路表抽取', '涉及Skill与插件脚本': 'qto-electrical-routes；无脚本', '卡点': '插件没有配电系统图回路表的抽取工具',
       '改用方法与位置': '项目脚本 _中间/wp/_工具_电气/extract_circuits.py', '影响数量': '是：各回路电缆长度', '登记': '20261015-0825_claude_电气_配电系统图回路表无抽取工具.md'}
SECTION = {'插件版本': '0.3.10', '提交': '9382aa3', '记录': [ROW]}


@pytest.fixture(scope='module')
def base(tmp_path_factory):
    _, _, result = gct.cf.calculate(gct.concrete_manifest(tmp_path_factory.mktemp('cf')))
    assert validate_document(result) == []
    return result


def with_section(base, section):
    d = copy.deepcopy(base); d['插件使用与临时处理'] = section; return d


# ---------- legal controls ----------

@pytest.mark.parametrize('register', [ROW['登记'], '待协调登记', '不进插件：项目特有的图签格式'])
def test_section_with_each_register_value_passes(base, register):
    d = with_section(base, {**SECTION, '记录': [dict(ROW, 登记=register)]})
    assert validate_document(d) == [] and document_warnings(d) == []
    jsonschema.Draft202012Validator(gct.RESULT_SCHEMA).validate(d)


def test_nothing_to_report_is_one_row_of_none(base):
    none_row = {k: '无' for k in ROW}
    d = with_section(base, {**SECTION, '换版': [{'插件版本': '0.3.9', '提交': '9e1c98e', '换版点': 'E-A-CBL2 起用新版'}], '记录': [none_row]})
    assert validate_document(d) == [] and document_warnings(d) == []


def test_missing_section_is_only_a_warning_for_now(base):
    assert validate_document(base) == []
    assert document_warnings(base) == ['成果有数值，但没有“插件使用与临时处理”一节（本版先提醒，下一版升为必填）']
    empty = copy.deepcopy(base)
    for q in empty['工程量结果']:
        q['结果']['数值'] = None
    assert document_warnings(empty) == []   # no number formed, nothing to deliver yet


def test_cli_reports_warnings_without_failing(base, tmp_path):
    p = tmp_path / 'r.json'; p.write_text(json.dumps(base, ensure_ascii=False), encoding='utf-8')
    r = subprocess.run([sys.executable, str(ROOT / 'skills/_qto-shared-evidence/scripts/validate_minimum_result.py'), str(p)],
                       stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding='utf-8', timeout=120)
    out = json.loads(r.stdout)
    assert r.returncode == 0 and out['status'] == 'PASS' and out['reports'][0]['warnings'] == document_warnings(base)


# ---------- counter-examples ----------

@pytest.mark.parametrize('change,message', [
    (lambda s: s.update(插件版本='0.3'), '插件使用与临时处理.插件版本须形如 0.3.10'),
    (lambda s: s.update(提交='HEAD'), '插件使用与临时处理.提交须是 7～40 位十六进制提交号'),
    (lambda s: s.update(记录=[]), '插件使用与临时处理.记录须是非空数组，没有卡点写一行“无”'),
    (lambda s: s['记录'][0].update(卡点=' '), '插件使用与临时处理.记录[0]缺少: 卡点'),
    (lambda s: s['记录'][0].pop('影响数量'), '插件使用与临时处理.记录[0]缺少: 影响数量'),
    (lambda s: s['记录'][0].update(登记='已登记'), '插件使用与临时处理.记录[0]的登记只能是登记文件名、待协调登记、不进插件：<理由> 或 无'),
    (lambda s: s['记录'][0].update(登记='不进插件：'), '插件使用与临时处理.记录[0]的登记只能是登记文件名、待协调登记、不进插件：<理由> 或 无'),
    (lambda s: s['记录'].append({k: '无' for k in ROW}), '插件使用与临时处理.记录写了“无”，整表只能有这一行'),
    (lambda s: s.update(换版=[{'插件版本': '0.3.9', '提交': '9e1c98e'}]), '插件使用与临时处理.换版[0]缺少: 换版点'),
    (lambda s: s.update(换版='0.3.9'), '插件使用与临时处理.换版必须是数组'),
])
def test_bad_section_fields_are_errors(base, change, message):
    s = copy.deepcopy(SECTION); change(s)
    assert validate_document(with_section(base, s)) == [message]


def test_section_must_be_an_object(base):
    assert validate_document(with_section(base, ['无'])) == ['插件使用与临时处理必须是对象']
