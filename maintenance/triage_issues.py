#!/usr/bin/env python3
"""List pending plugin-issue registrations grouped by Skill, flag blocking / quantity-affecting ones,
point out likely duplicates, and (with --claim) move chosen entries into 处理中/.
--audit checks the 维护回写 fields; --resolve moves an entry to 已处理/ only when they are complete
and the fix is released on both hosts (or declined).

Registrations live outside Git in <插件维护>/问题登记/{待处理,处理中,已处理,不进插件}/.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maint_common import resolve_maint_dir  # noqa: E402

STATES = ('待处理', '处理中', '已处理', '不进插件')

# 维护回写节：`## 维护回写（日期，宿主，模型）` 下逐行 `- 字段: 值`，项目回流脚本按字段读取。
WRITEBACK_HEADING = '## 维护回写'
WRITEBACK_FIELDS = ('处理版本', '处理状态', '需复跑', '复跑状态', '复跑记录')
HANDLING_STATES = re.compile(r'^(代码已修复|单侧已安装|双侧发布完成|部分修复：.+|不修复：.+)$')
RERUN_STATES = re.compile(r'^(未发布，待复跑|已发布，待复跑|已复跑|未能复跑：.+|本项目未触发|不需要：.+)$')
RESOLVABLE = re.compile(r'^(双侧发布完成|不修复：.+)$')


def entry_name(given: str, folder: Path) -> tuple[str, str | None]:
    """--claim / --resolve 按文件名找登记；给了路径时只认正好在 folder 下的，否则报“只传文件名”。"""
    name = Path(given).name
    if name == given or Path(given).parent.resolve() == folder.resolve():
        return name, None
    return name, f'只传文件名（这份不在 {folder.name}/ 下）：{given}'


def front_matter(path: Path) -> dict:
    text = path.read_text(encoding='utf-8-sig')
    match = re.match(r'---\n(.*?)\n---', text, re.S)
    fields = {}
    for line in (match.group(1) if match else '').splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            fields[key.strip()] = value.split('#', 1)[0].strip()
    return fields


def writeback(text: str) -> dict:
    """Fields of the last 维护回写 section and the problems that keep it from being complete."""
    sections = re.findall(rf'^{WRITEBACK_HEADING}[^\n]*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    if not sections:
        return {'present': False, 'fields': {}, 'problems': ['缺少维护回写节']}
    fields = {}
    for line in sections[-1].splitlines():
        match = re.match(r'^-\s*([^:：]+?)\s*[:：]\s*(.*?)\s*$', line)
        if match:
            fields[match.group(1)] = match.group(2)
    problems = [f'缺少字段：{name}' for name in WRITEBACK_FIELDS if not fields.get(name)]
    if fields.get('处理状态') and not HANDLING_STATES.match(fields['处理状态']):
        problems.append(f"处理状态取值无效：{fields['处理状态']}")
    if fields.get('复跑状态') and not RERUN_STATES.match(fields['复跑状态']):
        problems.append(f"复跑状态取值无效：{fields['复跑状态']}")
    return {'present': True, 'fields': fields, 'problems': problems}


def audit(maint_dir: Path) -> dict:
    report = {}
    for state in ('处理中', '已处理'):
        rows = []
        for path in sorted((maint_dir / '问题登记' / state).glob('*.md')):
            result = writeback(path.read_text(encoding='utf-8-sig'))
            rows.append({'file': path.name, 'complete': not result['problems'], 'problems': result['problems']})
        report[state] = {'total': len(rows), 'complete': sum(row['complete'] for row in rows),
                         'incomplete': [row for row in rows if not row['complete']]}
    return report


def triage(maint_dir: Path) -> dict:
    pending = maint_dir / '问题登记' / '待处理'
    groups, items = defaultdict(list), []
    for path in sorted(pending.glob('*.md')):
        meta = front_matter(path)
        title = path.stem.split('_', 3)[-1]
        item = {'file': path.name, 'skill': meta.get('涉及Skill', '未填'), 'type': meta.get('类型', '未填'),
                'blocking': meta.get('阻断程度') == '阻断', 'affects_quantity': meta.get('影响识图或数量') == '是',
                'title': title}
        items.append(item)
        groups[item['skill']].append(item)
    duplicates = []
    for skill, members in groups.items():
        seen = {}
        for item in members:
            key = (item['type'], re.sub(r'\W', '', item['title']))
            if key in seen:
                duplicates.append([seen[key], item['file']])
            else:
                seen[key] = item['file']
    return {'pending': len(items), 'by_skill': {k: [i['file'] for i in v] for k, v in sorted(groups.items())},
            'blocking': [i['file'] for i in items if i['blocking']],
            'affects_quantity': [i['file'] for i in items if i['affects_quantity']],
            'possible_duplicates': duplicates}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='整理插件问题登记')
    parser.add_argument('--maint-dir')
    parser.add_argument('--claim', nargs='*', default=[], help='移入 处理中/ 的登记文件名（给路径时须正好在 待处理/ 下）；任一份不能移就整批不动')
    parser.add_argument('--resolve', nargs='*', default=[],
                        help='维护回写字段齐全且处理状态为双侧发布完成或不修复时，从 处理中/ 移入 已处理/')
    parser.add_argument('--audit', action='store_true', help='列出 处理中/、已处理/ 中维护回写字段不全的登记')
    args = parser.parse_args(argv)
    maint_dir = resolve_maint_dir(args.maint_dir)
    for state in STATES:
        (maint_dir / '问题登记' / state).mkdir(parents=True, exist_ok=True)
    if args.audit:
        print(json.dumps(audit(maint_dir), ensure_ascii=False, indent=2))
        return 0
    pending, doing, done = (maint_dir / '问题登记' / state for state in ('待处理', '处理中', '已处理'))
    if args.resolve:
        refused, names = {}, []
        for given in args.resolve:
            name, bad = entry_name(given, doing)
            names.append(name)
            if bad:
                refused[given] = [bad]
                continue
            source = doing / name
            if not source.exists():
                refused[given] = ['处理中没有该登记']
                continue
            result = writeback(source.read_text(encoding='utf-8-sig'))
            problems = list(result['problems'])
            if not problems and not RESOLVABLE.match(result['fields']['处理状态']):
                problems.append(f"处理状态为{result['fields']['处理状态']}，双侧发布完成或不修复后才移入已处理")
            if (done / name).exists():
                problems.append('已处理已有同名登记')
            if problems:
                refused[given] = problems
        if refused:
            print(json.dumps({'status': 'REFUSED', 'refused': refused}, ensure_ascii=False, indent=2), file=sys.stderr)
            return 1
        for name in names:
            (doing / name).rename(done / name)
        print(json.dumps({'status': 'RESOLVED', 'resolved': names}, ensure_ascii=False, indent=2))
        return 0
    refused, names = {}, []
    for given in args.claim:
        name, bad = entry_name(given, pending)
        names.append(name)
        if bad:
            refused[given] = [bad]
        elif not (pending / name).exists():
            refused[given] = ['待处理没有该登记']
        elif (doing / name).exists():
            refused[given] = ['处理中已有同名登记']
    if refused:   # 整批拒绝，不部分移动
        print(json.dumps({'status': 'REFUSED', 'refused': refused}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    for name in names:
        (pending / name).rename(doing / name)
    print(json.dumps({**triage(maint_dir), 'claimed': names}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
