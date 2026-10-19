#!/usr/bin/env python3
"""Calculate source-bound water/air/tray wall or slab crossings in a new output folder."""
from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
import sys

from qto_penetration_sources import digest, reject_alias
from qto_penetrations import calculate

OUTPUTS = ('穿越计算结果.json', '穿越位置明细.csv', '公共入口调用记录.json')


def preflight(directory, protected):
    directory = reject_alias(directory)
    paths = [reject_alias(directory / name) for name in OUTPUTS]
    for target in paths:
        for name in protected:
            original = Path(name)
            if target == original or (target.exists() and original.exists() and target.samefile(original)):
                raise ValueError('输出与受保护来源/其他输出同物或别名: ' + str(target))
    for i, target in enumerate(paths):
        for other in paths[:i]:
            if target == other or (target.exists() and other.exists() and target.samefile(other)):
                raise ValueError('多个输出路径同物')
    if directory.exists() and (not directory.is_dir() or any(directory.iterdir())):
        raise ValueError('公共穿越输出须另存新目录或空目录')
    return directory, paths


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cad-manifest', required=True, type=Path)
    parser.add_argument('--request', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        # Reject source/output aliases before reading a request or running an
        # upstream producer, then repeat against all discovered dependencies.
        manifest = reject_alias(args.cad_manifest)
        request_path = reject_alias(args.request)
        preflight(args.output_dir, [manifest, request_path])
        request = json.loads(request_path.read_text(encoding='utf-8-sig'))
        result, source = calculate(manifest, request, request_path.parent, request_path)
        directory, paths = preflight(args.output_dir, source.protected)
        source.unchanged()
        directory.mkdir(parents=True, exist_ok=True)
        payloads = {paths[0]: json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n',
                    paths[2]: json.dumps(dict(schema='qto-penetration-invocation/1.0',
                        request=dict(path=str(request_path),sha256=digest(request_path)),
                        manifest=dict(path=str(manifest),sha256=digest(manifest)),
                        producer=dict(path=str(Path(__file__).resolve()),sha256=digest(__file__)),
                        output_status=result['status'], business_adoption_allowed=False),ensure_ascii=False,indent=2)+'\n'}
        for target,payload in payloads.items():
            with target.open('x', encoding='utf-8', newline='\n') as stream:
                stream.write(payload)
        fields=['id','discipline','route_physical_id','barrier_physical_id','floor_id','space_id',
                'classification','coordinate_unit','point','entry','exit','opening_refs',
                'new_opening_required','new_opening_candidate','sleeve_quantity','firestop_quantity','firestop_volume']
        with paths[1].open('x',encoding='utf-8-sig',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader()
            for event in result['events']:
                writer.writerow({k:json.dumps(event[k],ensure_ascii=False) if isinstance(event.get(k),(dict,list)) else event.get(k)for k in fields})
        print(json.dumps(dict(status=result['status'],events=len(result['events']),output_dir=str(directory),
                              quantities=result['quantities'],business_adoption_allowed=False),ensure_ascii=False))
        # A valid partial candidate calculation is operational success, with
        # REVIEW_REQUIRED in both stdout and result; it is never full acceptance.
        return 0
    except (ValueError, OSError, KeyError, TypeError, IndexError, StopIteration, OverflowError) as exc:
        print(json.dumps(dict(status='INPUT_REQUIRED',error=str(exc),business_adoption_allowed=False),ensure_ascii=False),file=sys.stderr)
        return 2


if __name__=='__main__':
    raise SystemExit(main())
