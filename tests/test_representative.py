"""代表 synthetic fixture の変換スモークテスト。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import mdx2mml as m2m  # noqa: E402
from tests.fixtures.build_mdx import (  # noqa: E402
    build_mdx,
    end_no_loop,
    extended_mml,
    note,
    pcm_enable,
    rest,
    sync_send,
    sync_wait,
    tempo,
)
from tests.test_mdx2mml import _convert_bytes  # noqa: E402


REPRESENTATIVE_DIR = Path(__file__).parent / 'fixtures' / 'representative'


@pytest.fixture(scope='module', autouse=True)
def write_representative_binaries():
    """手動確認用に .mdx を tests/fixtures/representative/ へ書き出す。"""
    REPRESENTATIVE_DIR.mkdir(parents=True, exist_ok=True)
    samples = {
        'basic.mdx': build_mdx([tempo(215) + rest(48) + note(12, 48) + end_no_loop()], title='Basic'),
        'dual.mdx': build_mdx([
            rest(48) + note(12, 48) + end_no_loop(),
            rest(48) + end_no_loop(),
        ], title='Dual'),
        'sync.mdx': build_mdx([
            rest(24) + sync_wait() + rest(24) + end_no_loop(),
            rest(48) + sync_send(0) + end_no_loop(),
        ], title='Sync'),
        'pcm.mdx': build_mdx([
            rest(24) + end_no_loop(),
            pcm_enable() + note(0, 24) + extended_mml(1, 2) + end_no_loop(),
        ], title='PCM'),
    }
    for name, data in samples.items():
        (REPRESENTATIVE_DIR / name).write_bytes(data)
    yield


@pytest.mark.parametrize(
    'builder',
    [
        lambda: build_mdx([tempo(215) + rest(48) + note(12, 48) + end_no_loop()]),
        lambda: build_mdx([
            rest(48) + note(12, 48) + end_no_loop(),
            rest(48) + end_no_loop(),
        ]),
        lambda: build_mdx([
            rest(24) + sync_wait() + rest(24) + end_no_loop(),
            rest(48) + sync_send(0) + end_no_loop(),
        ]),
    ],
)
def test_representative_align_zero_desync(builder):
    _, report = _convert_bytes(builder(), align_tracks=True)
    assert report.max_desync == 0


def test_representative_files_exist():
    for name in ('basic.mdx', 'dual.mdx', 'sync.mdx', 'pcm.mdx'):
        assert (REPRESENTATIVE_DIR / name).exists()
