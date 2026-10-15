"""S3（登记 20261015-1535 公共取证_图上无等否定结论和反向核对范围缺证据要求）。

方法文字契约：共享《通用成果字段与状态》规定否定结论的检索记录、反向核对覆盖范围和待确认处理；
每个专业的《结果检查与输出》都有这一检查项并链接到共享规则，PDF 提取规则也指向它。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / 'skills' / '_qto-shared-evidence' / 'references' / '通用成果字段与状态.md'
SECTION = '## 否定结论与检索证据'


def section_text():
    text = SHARED.read_text(encoding='utf-8')
    start = text.index(SECTION)
    end = text.index('\n## ', start + len(SECTION))
    return text[start:end], text


def test_shared_rule_names_the_retrieval_record_and_its_consequence():
    section, whole = section_text()
    for item in ('检索图纸', '检索对象', '方法', '命中数', '未覆盖'):
        assert item in section
    assert '覆盖的图纸集合' in section and '未核对' in section
    assert '待确认：在所列范围内未检索到' in section and '已确认零值' in section
    assert '清单外' in section   # 先查本包和相邻包已计的同一规格
    assert re.search(r'^15\. 否定结论.*检索记录', whole, re.M)


def test_every_professional_check_list_points_to_the_rule():
    files = sorted((ROOT / 'skills').glob('qto-*/references/结果检查与输出.md'))
    assert len(files) == 13
    for path in files:
        text = path.read_text(encoding='utf-8')
        assert '否定结论' in text and '否定结论与检索证据' in text, path
        links = re.findall(r'\]\(([^)]*通用成果字段与状态\.md)\)', text)
        assert links and all((path.parent / link).resolve() == SHARED.resolve() for link in links), path


def test_pdf_extraction_rule_points_to_the_rule():
    text = (SHARED.parent / 'PDF图片与表格提取.md').read_text(encoding='utf-8')
    assert '否定结论与检索证据' in text
