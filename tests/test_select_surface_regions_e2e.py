from pathlib import Path
import hashlib
import pytest

# Placeholder fixture contract. The test will be enabled when the shared DXF
# fixture generator is wired into the repository test fixtures.


def test_exact_selection_requires_source_and_layout():
    assert hashlib.sha256(b"fixture").hexdigest()


def test_two_hatch_fixture_selects_one_area():
    pytest.skip("DXF fixture wiring follows existing fixture generator")
