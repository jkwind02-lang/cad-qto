"""E4（登记 20261015-0825 电气_常见计量口径无选项与默认、20261015-0825 电气_防雷接地无识图指引）。

方法文字契约：电气《计量口径选择》每题有选项、计算式、常见清单写法、默认和算后反推，
电缆预留一题给"按计量规范表 / 按项目规定 / 不计"三个选项和默认值；《防雷接地识图》覆盖
多视图去重、屋面网格位置、混线拆分、引下线双向核对和高度来源；SKILL.md 与规则表接入两份指引。
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'skills' / 'qto-electrical-routes'


def read(name):
    return (ROOT / name).read_text(encoding='utf-8')


def questions():
    text = read('references/计量口径选择.md')
    parts = re.split(r'^### ', text, flags=re.M)[1:]
    return {part.split('\n', 1)[0]: part for part in parts}


def test_each_choice_has_options_default_and_back_calculation():
    found = questions()
    titles = '\n'.join(found)
    for topic in ('引下线', '保护管', '电缆头', '系统调试', '二期', '层高', '电缆预留'):
        assert topic in titles, topic
    for title, body in found.items():
        assert '| 选项 |' in body, title
        assert '- 默认：' in body, title
        assert '- 反推：' in body, title


def test_cable_reserve_has_three_options_and_a_default():
    body = next(body for title, body in questions().items() if '电缆预留' in title)
    assert '| A 按计量规范表 |' in body and '| B 按项目规定 |' in body and '| C 不计 |' in body
    assert '- 默认：A' in body and '不并入' in body
    assert '不凭记忆' in body            # 规范表值从原文取


def test_lightning_guide_covers_reported_pitfalls():
    text = read('references/防雷接地识图.md')
    for phrase in ('一个图框里有几个视图', '屋面网格可能画在别的楼层图上', '接地层混着别的线',
                   '双向核对', '高度先查本图', '端子箱'):
        assert phrase in text, phrase


def test_skill_and_rule_table_wire_in_both_guides():
    skill = read('SKILL.md')
    assert '](references/计量口径选择.md)' in skill and '](references/防雷接地识图.md)' in skill
    rules = read('references/专业规则与成熟状态.md')
    assert 'R-ER-007' in rules and 'R-ER-008' in rules
    assert '计量口径选择.md' in read('references/常规计算逻辑.md')
