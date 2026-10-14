#!/usr/bin/env python3
"""Register one plugin problem in <插件维护>/问题登记/待处理/ without overwriting anyone's entry.

The maintenance directory is never guessed from the installed package location: pass --maint-dir
(the host's global rule names it) or set CAD_QTO_MAINT_DIR. Writes a temp file first, then links it
to the final name; an existing name gets a numeric suffix. Does not touch plugin files.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

REQUIRED_FIELDS = ('登记时间', '发现宿主', '项目与任务', '插件版本', '涉及Skill', '类型', '影响识图或数量', '阻断程度')


def clean(part: str) -> str:
    return re.sub(r'[\s<>:"/\\|?*]+', '_', part.strip()).strip('._') or '未命名'


def register(maint_dir: Path, host: str, topic: str, title: str, body: str, when: datetime) -> Path:
    if not body.startswith('---'):
        raise ValueError('登记正文须以模板的 --- 字段区开头')
    header = body.split('---', 2)[1] if body.count('---') >= 2 else ''
    missing = [f for f in REQUIRED_FIELDS if not re.search(rf'^{f}\s*:', header, re.M)]
    if missing:
        raise ValueError(f'登记缺少字段：{missing}')
    folder = maint_dir / '问题登记' / '待处理'
    if not folder.is_dir():
        raise FileNotFoundError(f'登记目录不存在：{folder}')
    base = f"{when:%Y%m%d-%H%M}_{clean(host).lower()}_{clean(topic)}_{clean(title)}"
    temp = folder / f'.{base}.{os.getpid()}.tmp'
    temp.write_text(body if body.endswith('\n') else body + '\n', encoding='utf-8', newline='\n')
    try:
        for number in range(1, 1000):
            target = folder / (f'{base}.md' if number == 1 else f'{base}_{number}.md')
            try:
                os.link(temp, target)  # atomic and never replaces an existing entry
                return target
            except FileExistsError:
                continue
        raise FileExistsError('同名登记过多')
    finally:
        temp.unlink(missing_ok=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='登记插件问题（不修改插件）')
    parser.add_argument('--maint-dir', default=os.environ.get('CAD_QTO_MAINT_DIR'))
    parser.add_argument('--host', required=True, choices=['Claude', 'Codex'])
    parser.add_argument('--topic', required=True, help='专业或 Skill 简称，如 管网')
    parser.add_argument('--title', required=True, help='问题简述')
    parser.add_argument('--body-file', required=True, type=Path)
    args = parser.parse_args(argv)
    if not args.maint_dir:
        print(json.dumps({'status': 'INPUT_REQUIRED', 'error': '未指定插件维护目录：传 --maint-dir 或设置 CAD_QTO_MAINT_DIR；'
                          '仍找不到时把登记全文交给用户转交'}, ensure_ascii=False))
        return 2
    try:
        path = register(Path(args.maint_dir), args.host, args.topic, args.title,
                        args.body_file.read_text(encoding='utf-8-sig'), datetime.now())
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({'status': 'REGISTERED', 'path': str(path)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
