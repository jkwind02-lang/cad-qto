#!/usr/bin/env python3
"""Build a reproducible source-bound symbol/legend census (no quantity adoption)."""
import argparse
import json
from pathlib import Path
from qto_symbol_geometry import build_census, protected_inputs


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = build_census(args.request)
        protected = protected_inputs(report)
        output = args.output.resolve()
        aliases_input = args.output.exists() and any(path.exists() and args.output.samefile(path) for path in protected)
        if output in protected or aliases_input:
            raise ValueError("output cannot overwrite source, manifest, neutral stream, request or view evidence")
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        print(json.dumps({"census_status": report["census_status"], **report["coverage"]}, ensure_ascii=False))
        return 0 if report["census_status"] == "SCOPED_CENSUS_COMPLETE" else 3
    except (ValueError, OSError, TypeError, KeyError) as exc:
        print(json.dumps({"status": "INVALID_INPUT", "error": str(exc)}, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
