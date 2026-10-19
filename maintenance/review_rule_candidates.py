"""Maintenance consumer; emits evidence-bound receipts/proposals, never edits runtime."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'skills/_qto-shared-evidence/scripts'))
from qto_rule_candidates import main
if __name__ == '__main__':
    raise SystemExit(main(maintenance=True))
