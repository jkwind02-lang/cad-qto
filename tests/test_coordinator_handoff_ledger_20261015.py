"""C1（登记 20261015-1535 协调_同一能力按区域分包时交接缺接收对账）。

方法文字契约：协调器为全部数量工作包（含同一能力按区域分的包）维护交接清单，接收方确认或拒收，
汇总前对账；重复归属检查按交接清单和跨包对象编号查"两包都计"与"无出口"；协调输出列交接清单。
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / 'skills' / 'qto-coordinator'


def read(name):
    return (ROOT / name).read_text(encoding='utf-8')


def test_split_rule_defines_ledger_with_receiver_decision():
    text = read('references/跨能力范围拆分.md')
    assert '## 工作包之间的交接清单' in text and '按区域' in text
    for field in ('提出包、接收包', '接收状态', '接收记录', '待接收', '拒收'):
        assert field in text
    assert '归入他项' in text            # 口头"归入他项"也是交接
    assert '中位数' in text              # 接收方估算前先查已有定位事实
    assert '](重复归属检查.md)' in text and '](假定依赖与裁决队列.md)' in text


def test_duplicate_check_reconciles_ledger_and_object_numbers():
    text = read('references/重复归属检查.md')
    assert '交接清单对账' in text and '无出口' in text
    assert '全量对账' in text and '清单外新增' in text
    assert '所有交接都已接收或裁决后' in text


def test_coordinator_output_lists_the_ledger():
    assert '工作包之间的交接清单' in read('SKILL.md')
