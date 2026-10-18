#!/usr/bin/env python3
"""Host-neutral coordination CLI. No agents, installation, hooks or quantity computation."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

from coordination_contract import (atomic_json, atom, backflow, collect_issues, load_json,
                                   validate_delivery, validate_handoffs)


def initialize(data, output):
    if data.get('schema') != 'qto-coordination-init/1.0' or not atom(data.get('project_id')):
        raise ValueError('初始化须声明schema=qto-coordination-init/1.0及project_id')
    caps = data.get('capabilities')
    packages = data.get('packages')
    if not isinstance(caps, list) or not all(atom(x) for x in caps) or len(caps) != len(set(caps)) or not isinstance(packages, list):
        raise ValueError('capabilities与packages须为显式数组；未知可为空，不按惯例补专业')
    ids = set()
    for p in packages:
        if (not isinstance(p, dict) or not atom(p.get('package_id')) or p['package_id'] in ids
                or p.get('skill') not in caps or not p.get('actual_scope') or not atom(p.get('responsibility'))):
            raise ValueError('已确认工作包须有唯一编号、已输入能力、实际范围及责任；缺件时只列潜在能力')
        ids.add(p['package_id'])
    out = Path(output)
    out.mkdir(parents=True, exist_ok=False)
    atomic_json(out / '协调配置.json', dict(data, automatic_agent_creation=False,
                readiness='READY_FOR_COORDINATION' if packages else 'INPUT_REQUIRED'))
    atomic_json(out / '跨包接收对账.json', {'schema': 'qto-package-reconciliation/1.0', 'project_id': data['project_id'],
                'packages': [dict(p, result_file=None, result_sha256=None) for p in packages],
                'handoffs': [], 'baseline': None})
    atomic_json(out / '插件问题收件输入.json', {'schema': 'qto-plugin-issue-intake/1.0', 'project_id': data['project_id'],
                'host': data.get('host', 'Codex'), 'packages': []})
    atomic_json(out / '项目触发适配模板.json', {'schema': 'qto-project-hook-plan/1.0', 'mode': 'off',
                'project_id': data['project_id'], 'project_root': str(out.resolve()),
                'on_since': None, 'eligible_session_ids': [], 'host': 'manual-cli',
                'action': '仅项目模板；须经宿主真实接口适配后另行安装，不修改全局配置'})
    (out / '使用说明.md').write_text('# 协调工作区\n\n工作包来自输入配置，不自动启动代理。\n'
        '先补齐实际成果路径、SHA256、原编号基准、交接和接收成果行，再执行handoffs。\n'
        '数字成果交付清单用delivery；待协调登记用collect，默认只生成隔离维护演练原单。\n'
        '未知保持null。模板生成不表示交付或工程量验收通过。\n', encoding='utf-8')
    return {'schema': 'qto-coordination-init-receipt/1.0', 'status': 'PASS', 'output_dir': str(out.resolve()),
            'package_count': len(packages), 'capability_count': len(caps), 'automatic_agent_creation': False}


def hook_plan(data):
    if data.get('schema') != 'qto-project-hook-plan/1.0' or data.get('mode') not in ('off', 'test', 'on'):
        raise ValueError('触发模板须有schema及off/test/on模式')
    if not atom(data.get('project_root')) or not atom(data.get('project_id')):
        raise ValueError('须限定项目路径与项目身份')
    if data['mode'] == 'on':
        datetime.fromisoformat(data.get('on_since', ''))
        if not data.get('eligible_session_ids') or not all(atom(x) for x in data['eligible_session_ids']):
            raise ValueError('on须显式on_since和允许的新会话ID；不影响存量会话')
    return {'schema': 'qto-project-hook-plan-check/1.0', 'status': 'PASS', 'mode': data['mode'],
            'project_root': data['project_root'], 'applied': False, 'global_config_changed': False,
            'host_loading_verified': False, 'instruction': '共同CLI可用；仅输出项目级触发适配模板，未安装宿主钩子'}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    subs = p.add_subparsers(dest='command', required=True)
    for name in ('init', 'delivery', 'handoffs', 'collect', 'backflow', 'hook-plan'):
        s = subs.add_parser(name)
        s.add_argument('--input', required=True, type=Path)
        s.add_argument('--out', type=Path, help='机器可读回执；不能覆盖输入')
        if name == 'init':
            s.add_argument('--out-dir', required=True, type=Path)
        if name == 'collect':
            s.add_argument('--state-dir', required=True, type=Path)
            s.add_argument('--maint-dir', type=Path, help='显式指定真实维护目录；省略则隔离维护演练')
    a = p.parse_args(argv)
    try:
        data = load_json(a.input)
        if not isinstance(data, dict):
            raise ValueError('请求须为JSON对象')
        if a.out and a.out.resolve() == a.input.resolve():
            raise ValueError('回执不能覆盖输入')
        if a.command == 'init':
            result = initialize(data, a.out_dir)
        elif a.command == 'collect':
            result = collect_issues(data, a.input.parent, a.state_dir, a.maint_dir)
        elif a.command == 'hook-plan':
            result = hook_plan(data)
        else:
            result = {'delivery': validate_delivery, 'handoffs': validate_handoffs, 'backflow': backflow}[a.command](data, a.input.parent)
        if a.out:
            atomic_json(a.out, result)
        print(json.dumps(result, ensure_ascii=False))
        return {'PASS': 0, 'FAIL': 2, 'UNVERIFIED': 3}.get(result['status'], 2)
    except (ValueError, OSError, KeyError, TypeError, AttributeError, ImportError) as exc:
        print(json.dumps({'schema': 'qto-coordination-check/1.0', 'status': 'FAIL', 'error': str(exc)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    sys.exit(main())
