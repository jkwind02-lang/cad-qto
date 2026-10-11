#!/usr/bin/env python3
"""Verify this selected archive and its recorded numeric comparisons.

No model is invoked, no calculator is run, and no archived receipt is rewritten.
This is an explicit maintenance command, not a pytest case.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path


def pointer(value, path):
    for raw in path.lstrip('/').split('/') if path else []:
        key = raw.replace('~1', '/').replace('~0', '~')
        value = value[int(key)] if isinstance(value, list) else value[key]
    return value


def verify(root):
    manifest = json.loads((root / 'archive_manifest.json').read_text(encoding='utf-8'))
    failures = []
    for record in manifest['files']:
        path = root / record['archive_path']
        actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        if actual != record['sha256']:
            failures.append({'file': record['archive_path'], 'reason': 'missing or SHA256 mismatch'})
    assessment = json.loads((root / 'assessment.json').read_text(encoding='utf-8'))
    comparison_count = 0
    for run in assessment['runs'].values():
        for case in run['cases'].values():
            for row in case['quantity_comparisons']:
                values = []
                for source in row['native_sources']:
                    payload = json.loads((root / source['archive_path']).read_text(encoding='utf-8'))
                    values.append(pointer(payload, source['json_pointer']))
                actual = math.fsum(values)
                reference = row['reference_quantity']
                comparison_count += 1
                if not math.isclose(actual, row['native_quantity'], rel_tol=0, abs_tol=1e-12):
                    failures.append({'item': row['physical_object_key'], 'reason': 'recorded native value differs'})
                if (abs(actual - reference) <= row['absolute_tolerance']) != row['within_tolerance']:
                    failures.append({'item': row['physical_object_key'], 'reason': 'recorded comparison differs'})
    return {
        'status': 'PASS' if not failures else 'FAIL',
        'archive_files_checked': len(manifest['files']),
        'mapped_numeric_comparisons_checked': comparison_count,
        'failures': failures,
        'scope': 'Selected archived bytes and recorded JSON-pointer arithmetic only; no model rerun, engineering review, or omitted-file verification.',
        'all_original_freeze_files_archived': False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', type=Path, default=Path(__file__).resolve().parent)
    args = parser.parse_args()
    report = verify(args.package.resolve())
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'PASS' else 1


if __name__ == '__main__':
    raise SystemExit(main())
