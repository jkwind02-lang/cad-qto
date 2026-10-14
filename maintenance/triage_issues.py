#!/usr/bin/env python3
"""List pending plugin-issue registrations grouped by Skill, flag blocking / quantity-affecting ones,
point out likely duplicates, and (with --claim) move chosen entries into 处理中/.

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


def front_matter(path: Path) -> dict:
    text = path.read_text(encoding='utf-8-sig')
    match = re.match(r'---\n(.*?)\n---', text, re.S)
    fields = {}
    for line in (match.group(1) if match else '').splitlines():
        if ':' in line:
            key, value = line.split(':', 1)
            fields[key.strip()] = value.split('#', 1)[0].strip()
    return fields


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
    parser.add_argument('--claim', nargs='*', default=[], help='移入 处理中/ 的登记文件名')
    args = parser.parse_args(argv)
    maint_dir = resolve_maint_dir(args.maint_dir)
    for state in STATES:
        (maint_dir / '问题登记' / state).mkdir(parents=True, exist_ok=True)
    moved = []
    for name in args.claim:
        source = maint_dir / '问题登记' / '待处理' / name
        target = maint_dir / '问题登记' / '处理中' / name
        if target.exists():
            print(f'处理中已有同名登记，未移动：{name}', file=sys.stderr)
            return 1
        source.rename(target)
        moved.append(name)
    print(json.dumps({**triage(maint_dir), 'claimed': moved}, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
