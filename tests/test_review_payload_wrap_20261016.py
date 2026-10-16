"""登记 20261016_复核页大载荷封装耗时偏长：页面载荷 Base64 改为固定宽度切片，与原 textwrap 分行逐字相同、耗时随长度线性。"""
import base64
import random
import sys
import textwrap
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'skills/_qto-shared-evidence/scripts'))
from build_qto_review_html import wrap_base64  # noqa: E402


def old_wrap(data):
    return '\n'.join(textwrap.wrap(base64.b64encode(data).decode(), 180))


def test_fixed_width_lines_equal_the_old_textwrap_output():
    rnd = random.Random(20261016)
    # Base64 lengths 0, 4, 180, 184, 360, 364 … around each line boundary, plus random sizes
    sizes = [0, 1, 2, 3, 134, 135, 136, 269, 270, 271, 3072, 6144] + [rnd.randrange(1, 50_000) for _ in range(40)]
    for n in sizes:
        data = rnd.randbytes(n)
        assert wrap_base64(data) == old_wrap(data), n
        assert base64.b64decode(wrap_base64(data)) == data


def test_lines_are_at_most_180_and_only_the_last_may_be_shorter():
    lines = wrap_base64(bytes(range(256)) * 50).split('\n')
    assert all(len(x) == 180 for x in lines[:-1]) and 0 < len(lines[-1]) <= 180


def test_large_payload_wraps_in_linear_time():
    """8 MB gzip payload ≈ 11 M Base64 chars: the old path needed ~3×10^11 byte copies; slicing is well under a second."""
    data = random.Random(1).randbytes(8 * 1024 * 1024)
    start = time.perf_counter()
    text = wrap_base64(data)
    assert time.perf_counter() - start < 5
    assert len(text.split('\n', 1)[0]) == 180
