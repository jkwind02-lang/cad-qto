#!/usr/bin/env python3
"""Build, verify and publish a cad-qto release package from one committed revision.

- build:     extract runtime files (运行文件清单.json, paths under skills/) plus an explicit root
             whitelist from a full commit id; check missing / tampered / extra files; generate
             发布来源.json; freeze a release record in <插件维护>/发布记录/.
- verify:    compare any directory (package, release dir, host cache) with a release record.
- publish:   verify the package, then replace the single current release directory, keeping one
             previous package for rollback; on failure restore the previous directory.
- fingerprint / run-tests: tree fingerprint of tested bytes and a recorded full regression,
             so the hook can check "tested == committed" before an authorized push.

Never copies the working tree, never uses a raw git archive as the package, never pushes.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from maint_common import (  # noqa: E402
    REPO_ROOT, create_exclusive, detect_host_session, dump_json, git, now_iso, read_json,
    resolve_maint_dir, sha256_bytes, sha256_file,
)

MANIFEST = '运行文件清单.json'
RUNTIME_PREFIX = 'skills/'
# Root files packaged next to skills/. README.md stays in the repository: it links to docs/ that
# are not shipped, and 使用说明.md is the self-contained install-version usage document.
ROOT_WHITELIST = ('.codex-plugin/plugin.json', '.claude-plugin/plugin.json', '.claude-plugin/marketplace.json',
                  '使用说明.md', MANIFEST)
SOURCE_RECORD = '发布来源.json'
SOURCE_SCHEMA = 'cad-qto-release-source/1.0'
RECORD_SCHEMA = 'cad-qto-release-record/1.0'
TEST_SCHEMA = 'cad-qto-test-run/1.0'
RECORD_DIR = '发布记录'
TEST_DIR = '受测记录'
ROLLBACK_DIR = '回滚包'
FULL_SHA = re.compile(r'[0-9a-f]{40}')


class ReleaseError(RuntimeError):
    pass


# ---------- git object access ----------

def require_commit(commit: str) -> str:
    if not FULL_SHA.fullmatch(commit or ''):
        raise ReleaseError('必须给出 40 位完整提交号')
    if git('cat-file', '-t', commit, check=False).strip() != b'commit':
        raise ReleaseError(f'提交不存在：{commit}')
    return commit


def commit_tree(commit: str) -> dict[str, str]:
    """path -> blob id for every file in the commit."""
    out = git('ls-tree', '-r', '-z', '--full-tree', commit)
    files = {}
    for item in out.split(b'\0'):
        if not item:
            continue
        meta, path = item.split(b'\t', 1)
        mode, kind, blob = meta.decode().split()
        if kind == 'blob':
            files[path.decode('utf-8')] = blob
    return files


def read_blobs(blobs: list[str]) -> list[bytes]:
    if not blobs:
        return []
    proc = subprocess.run(['git', 'cat-file', '--batch'], cwd=REPO_ROOT, input=('\n'.join(blobs) + '\n').encode(),
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    data, pos, result = proc.stdout, 0, []
    for _ in blobs:
        header_end = data.index(b'\n', pos)
        _, kind, size = data[pos:header_end].decode().split()
        if kind != 'blob':
            raise ReleaseError(f'对象不是文件：{kind}')
        start = header_end + 1
        result.append(data[start:start + int(size)])
        pos = start + int(size) + 1
    return result


def worktree_dirty() -> list[str]:
    return [line for line in git('status', '--porcelain=v1', '-uall').decode('utf-8', 'replace').splitlines() if line]


# ---------- fingerprints ----------

def fingerprint_entries(entries: dict[str, str]) -> str:
    return sha256_bytes(''.join(f'{p}\t{h}\n' for p, h in sorted(entries.items())).encode('utf-8'))


def tree_fingerprint(commit: str | None = None) -> dict:
    """Fingerprint of every Git-visible file, from the working tree or from a commit."""
    if commit:
        tree = commit_tree(commit)
        paths = sorted(tree)
        entries = dict(zip(paths, (sha256_bytes(b) for b in read_blobs([tree[p] for p in paths]))))
    else:
        listed = git('ls-files', '-z', '-c', '-o', '--exclude-standard').decode('utf-8').split('\0')
        entries = {p: sha256_file(REPO_ROOT / p) for p in sorted(set(listed)) if p and (REPO_ROOT / p).is_file()}
    runtime = {p: h for p, h in entries.items() if p.startswith(RUNTIME_PREFIX) or p in ROOT_WHITELIST}
    return {'tree_fingerprint': fingerprint_entries(entries), 'package_source_fingerprint': fingerprint_entries(runtime),
            'file_count': len(entries)}


def find_test_record(maint_dir: Path, fingerprint: str) -> dict | None:
    path = maint_dir / TEST_DIR / f'{fingerprint}.json'
    return read_json(path) if path.exists() else None


# ---------- build ----------

def business_version(version: str) -> str:
    return str(version).split('+', 1)[0]


def check_versions(root_bytes: dict[str, bytes]) -> dict:
    codex = json.loads(root_bytes['.codex-plugin/plugin.json'])
    claude = json.loads(root_bytes['.claude-plugin/plugin.json'])
    market = json.loads(root_bytes['.claude-plugin/marketplace.json'])
    entry = [p for p in market.get('plugins', []) if p.get('name') == 'cad-qto']
    if codex.get('name') != 'cad-qto' or claude.get('name') != 'cad-qto' or len(entry) != 1:
        raise ReleaseError('插件名称或本地市场条目不一致')
    versions = {'codex': codex.get('version'), 'claude': claude.get('version'), 'claude_marketplace': entry[0].get('version')}
    if len({business_version(v) for v in versions.values()}) != 1:
        raise ReleaseError(f'两份插件清单或市场清单的业务版本不一致：{versions}')
    return {'business_version': business_version(versions['codex']), 'manifest_versions': versions}


def build(commit: str, out: Path, maint_dir: Path, record_dir: Path, allow_dirty: bool, allow_untested: bool) -> dict:
    commit = require_commit(commit)
    dirty = worktree_dirty()
    if dirty and not allow_dirty:
        raise ReleaseError('工作区有未提交改动，拒绝发布：\n' + '\n'.join(dirty[:20]))
    if out.exists():
        raise ReleaseError(f'输出目录已存在：{out}')
    tree = commit_tree(commit)
    missing_root = [p for p in ROOT_WHITELIST if p not in tree]
    if missing_root:
        raise ReleaseError(f'提交中缺少根白名单文件：{missing_root}')
    root_bytes = dict(zip(ROOT_WHITELIST, read_blobs([tree[p] for p in ROOT_WHITELIST])))
    manifest_raw = root_bytes[MANIFEST]
    manifest = json.loads(manifest_raw)
    entries = manifest['files']
    if manifest.get('runtime_file_count') != len(entries) or len({e['path'] for e in entries}) != len(entries):
        raise ReleaseError('运行文件清单计数不符或路径重复')
    versions = check_versions(root_bytes)

    problems = {'missing': [], 'tampered': [], 'extra': []}
    runtime_paths = [RUNTIME_PREFIX + e['path'] for e in entries]
    problems['missing'] = [p for p in runtime_paths if p not in tree]
    present = [(e, p) for e, p in zip(entries, runtime_paths) if p in tree]
    blobs = read_blobs([tree[p] for _, p in present])
    out.mkdir(parents=True)
    files = []
    for (entry, path), data in zip(present, blobs):
        digest = sha256_bytes(data)
        if digest != entry['sha256'] or len(data) != entry['bytes']:
            problems['tampered'].append({'path': path, 'manifest_sha256': entry['sha256'], 'actual_sha256': digest})
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append({'path': path, 'sha256': digest, 'bytes': len(data), 'origin': 'runtime_manifest'})
    for path, data in root_bytes.items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        files.append({'path': path, 'sha256': sha256_bytes(data), 'bytes': len(data), 'origin': 'root_whitelist'})
    source = {'schema': SOURCE_SCHEMA, 'business_version': versions['business_version'], 'source_commit': commit,
              'runtime_manifest_sha256': sha256_bytes(manifest_raw), 'manifest_versions': versions['manifest_versions']}
    source_bytes = dump_json(source).encode('utf-8')
    (out / SOURCE_RECORD).write_bytes(source_bytes)
    files.append({'path': SOURCE_RECORD, 'sha256': sha256_bytes(source_bytes), 'bytes': len(source_bytes), 'origin': 'generated'})

    expected = {f['path'] for f in files}
    actual = {p.relative_to(out).as_posix() for p in out.rglob('*') if p.is_file()}
    problems['extra'] = sorted(actual - expected)
    problems['missing'] += sorted(expected - actual)
    for item in files:  # re-hash from disk: what is on disk is what gets installed
        if sha256_file(out / item['path']) != item['sha256']:
            problems['tampered'].append({'path': item['path'], 'reason': '写盘后哈希变化'})

    fingerprints = tree_fingerprint(commit)
    test_run = find_test_record(maint_dir, fingerprints['tree_fingerprint'])
    tested = bool(test_run and test_run.get('status') == 'PASS')
    if any(problems.values()):
        shutil.rmtree(out)
        raise ReleaseError('提取检查未通过，不发布：' + json.dumps(problems, ensure_ascii=False)[:2000])
    if not tested and not allow_untested:
        shutil.rmtree(out)
        raise ReleaseError(f"该提交没有通过的完整回归记录（受测指纹 {fingerprints['tree_fingerprint']}）；先运行 run-tests")
    host, session = detect_host_session()
    record = {
        'schema': RECORD_SCHEMA, 'business_version': versions['business_version'], 'source_commit': commit,
        'manifest_versions': versions['manifest_versions'], 'runtime_manifest_sha256': source['runtime_manifest_sha256'],
        'runtime_file_count': len(entries), 'package_file_count': len(files),
        'tested_fingerprint': fingerprints['tree_fingerprint'],
        'test_record': None if not test_run else {'status': test_run['status'], 'recorded_at': test_run['recorded_at'],
                                                  'summary': test_run.get('summary')},
        'untested_rehearsal': not tested,
        'allowed_differences': [],  # host suffixes etc. are added only with evidence from an install rehearsal
        'files': sorted(files, key=lambda f: f['path']),
        'built_by': {'host': host, 'session_id': session}, 'built_at': now_iso(),
        'note': '包内容记录冻结后不改；两边安装结果另见同版本安装核对记录。',
    }
    record_dir.mkdir(parents=True, exist_ok=True)
    record_path = record_dir / f"{versions['business_version']}_{commit[:12]}.json"
    if record_path.exists():
        frozen = read_json(record_path)
        if frozen['files'] != record['files'] or frozen['source_commit'] != commit:
            shutil.rmtree(out)
            raise ReleaseError(f'发布记录已冻结且内容不同，拒绝覆盖：{record_path}')
        record = frozen
    else:
        create_exclusive(record_path, dump_json(record))
    return {'status': 'PASS', 'package': str(out), 'record': str(record_path), 'business_version': versions['business_version'],
            'package_file_count': len(files), 'untested_rehearsal': not tested}


# ---------- verify ----------

def verify_dir(directory: Path, record: dict, exempt: list[str] | None = None) -> dict:
    exempt = exempt or []
    expected = {f['path']: f for f in record['files']}
    actual = {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()}
    is_exempt = lambda p: any(fnmatch.fnmatch(p, pattern) for pattern in exempt)
    report = {'missing': sorted(set(expected) - actual), 'tampered': [], 'extra': [], 'exempted': []}
    for path in sorted(actual):
        if path in expected:
            if sha256_file(directory / path) != expected[path]['sha256']:
                report['tampered'].append(path)
        elif is_exempt(path):
            report['exempted'].append(path)
        else:
            report['extra'].append(path)
    report['status'] = 'PASS' if not (report['missing'] or report['tampered'] or report['extra']) else 'FAIL'
    report['checked_files'] = len(expected)
    return report


# ---------- publish ----------

def publish(package: Path, record_path: Path, target: Path, maint_dir: Path) -> dict:
    record = read_json(record_path)
    report = verify_dir(package, record)
    if report['status'] != 'PASS':
        raise ReleaseError('临时包与发布记录不符，不替换发布目录：' + json.dumps(report, ensure_ascii=False)[:1500])
    rollback_root = maint_dir / ROLLBACK_DIR
    rollback_root.mkdir(parents=True, exist_ok=True)
    previous = None
    if target.exists():
        old_source = target / SOURCE_RECORD
        label = read_json(old_source)['business_version'] if old_source.exists() else 'pre-dual-host'
        previous = rollback_root / f'{label}_{now_iso()[:19].replace(":", "")}'
        shutil.move(str(target), str(previous))
    try:
        shutil.copytree(package, target)
        after = verify_dir(target, record)
        if after['status'] != 'PASS':
            raise ReleaseError('替换后发布目录核对失败：' + json.dumps(after, ensure_ascii=False)[:1500])
    except Exception:
        if target.exists():
            shutil.rmtree(target)
        if previous is not None:
            shutil.move(str(previous), str(target))
        raise
    kept = sorted(p for p in rollback_root.iterdir() if p.is_dir())
    return {'status': 'PASS', 'target': str(target), 'previous_package': str(previous) if previous else None,
            'rollback_packages': [str(p) for p in kept],
            'note': '只保留上一版必要回滚包；更早的回滚包按清理要求另行确认后处理。'}


# ---------- tests ----------

def junit_summary(path: Path) -> dict:
    root = ET.parse(path).getroot()
    suites = [root] if root.tag == 'testsuite' else list(root.iter('testsuite'))
    total = {k: sum(int(s.get(k, 0)) for s in suites) for k in ('tests', 'failures', 'errors', 'skipped')}
    return total


def run_tests(maint_dir: Path, with_feedback_service: bool, model: str | None) -> dict:
    before = tree_fingerprint()
    work = REPO_ROOT / '.codex_work' / 'maintenance-tests'
    work.mkdir(parents=True, exist_ok=True)
    junit = work / 'pytest.xml'
    steps = [('compileall', [sys.executable, '-m', 'compileall', '-q', 'skills', 'tests', 'maintenance']),
             ('pytest', [sys.executable, '-m', 'pytest', 'tests', '-q', '-p', 'no:cacheprovider', f'--junitxml={junit}']),
             ('html_contract', [sys.executable, 'tests/run_html_review_tests.py', '--root', 'skills',
                                '--output-root', str(work / 'html-review')])]
    node = shutil.which('node')
    if node:
        steps.append(('html_ui', [node, 'tests/run_html_review_ui_tests.mjs', '--root', 'skills',
                                  '--report', str(work / 'html-review' / '最新核验.json')]))
    if with_feedback_service and node:
        steps.append(('feedback_service', [node, '--test', *sorted(str(p) for p in (REPO_ROOT / 'feedback-service/tests').glob('*.test.mjs'))]))
    results = []
    for name, command in steps:
        log = work / f'{name}.log'
        with open(log, 'wb') as handle:
            proc = subprocess.run(command, cwd=REPO_ROOT, stdin=subprocess.DEVNULL, stdout=handle, stderr=subprocess.STDOUT)
        results.append({'step': name, 'returncode': proc.returncode, 'log': str(log)})
    after = tree_fingerprint()
    summary = junit_summary(junit) if junit.exists() else None
    status = 'PASS' if all(r['returncode'] == 0 for r in results) and before == after and summary \
        and summary['failures'] == 0 and summary['errors'] == 0 else 'FAIL'
    host, session = detect_host_session()
    record = {'schema': TEST_SCHEMA, 'status': status, 'tree_fingerprint': after['tree_fingerprint'],
              'package_source_fingerprint': after['package_source_fingerprint'], 'fingerprint_stable': before == after,
              'summary': summary, 'steps': results, 'node_available': bool(node),
              'feedback_service_checked': bool(with_feedback_service and node),
              'head': git('rev-parse', 'HEAD').decode().strip(), 'host': host, 'session_id': session, 'model': model,
              'python': platform.python_version(), 'recorded_at': now_iso()}
    target_dir = maint_dir / TEST_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    path = target_dir / f"{after['tree_fingerprint']}.json"
    if status == 'PASS' or not path.exists():  # never let a failed rerun overwrite a passing record
        path.write_text(dump_json(record), encoding='utf-8', newline='\n')
    return {**record, 'record': str(path)}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description='cad-qto 发布包工具')
    parser.add_argument('--maint-dir')
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('build')
    p.add_argument('--commit', required=True)
    p.add_argument('--out', required=True, type=Path)
    p.add_argument('--record-dir', type=Path, help='默认 <插件维护>/发布记录；演练时指定临时目录')
    p.add_argument('--allow-dirty', action='store_true', help='仅限演练：工作区不干净也从提交提取')
    p.add_argument('--allow-untested', action='store_true', help='仅限演练：记录标为未受测，不能用于正式发布')
    p = sub.add_parser('verify')
    p.add_argument('--dir', required=True, type=Path)
    p.add_argument('--record', required=True, type=Path)
    p.add_argument('--exempt', action='append', default=[], help='宿主生成的非运行元数据，glob，可重复')
    p = sub.add_parser('publish')
    p.add_argument('--package', required=True, type=Path)
    p.add_argument('--record', required=True, type=Path)
    p.add_argument('--target', required=True, type=Path)
    p = sub.add_parser('fingerprint')
    p.add_argument('--commit')
    p = sub.add_parser('run-tests')
    p.add_argument('--with-feedback-service', action='store_true')
    p.add_argument('--model')
    args = parser.parse_args(argv)
    try:
        maint_dir = resolve_maint_dir(args.maint_dir)
        if args.command == 'build':
            result = build(args.commit, args.out.resolve(), maint_dir, (args.record_dir or maint_dir / RECORD_DIR).resolve(),
                           args.allow_dirty, args.allow_untested)
        elif args.command == 'verify':
            result = verify_dir(args.dir.resolve(), read_json(args.record), args.exempt)
        elif args.command == 'publish':
            if read_json(args.record).get('untested_rehearsal'):
                raise ReleaseError('演练记录未受测，不能发布到正式发布目录')
            result = publish(args.package.resolve(), args.record.resolve(), args.target.resolve(), maint_dir)
        elif args.command == 'fingerprint':
            result = tree_fingerprint(require_commit(args.commit) if args.commit else None)
        else:
            result = run_tests(maint_dir, args.with_feedback_service, args.model)
    except (ReleaseError, FileNotFoundError, json.JSONDecodeError) as exc:
        print(json.dumps({'status': 'FAIL', 'error': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('status', 'PASS') == 'PASS' else 1


if __name__ == '__main__':
    sys.exit(main())
