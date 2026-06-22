"""mdx2mml.py 回帰テスト (標準ライブラリ + pytest)。"""

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
    global_loop,
    key_off_disable,
    key_on_delay,
    note,
    pan,
    pcm_enable,
    portamento,
    repeat_end,
    repeat_start,
    rest,
    sync_send,
    sync_wait,
    tempo,
)


@pytest.mark.parametrize(
    ('ticks', 'expected'),
    [
        (48, ['4']),
        (24, ['8']),
        (36, ['8.']),
    ],
)
def test_ticks_to_mml_lengths(ticks, expected):
    assert m2m.ticks_to_mml_lengths(ticks) == expected


@pytest.mark.parametrize(
    ('timer_b', 'scale', 'expected'),
    [
        (215, 1.0, 119),
        (215, 0.5, 60),
        (215, 2.0, 238),
    ],
)
def test_timer_b_to_bpm(timer_b, scale, expected):
    assert m2m.timer_b_to_bpm(timer_b, scale) == expected


@pytest.mark.parametrize(
    ('pan_value', 'expected'),
    [
        (0, '; P0 ; (no output)'),
        (1, 'P0'),
        (2, 'P127'),
        (3, 'P64'),
    ],
)
def test_format_pan(pan_value, expected):
    assert m2m.format_pan(pan_value) == expected


def test_cmd_len_rest_and_note():
    data = bytes([0x10, 0x90, 0x2f])
    assert m2m.cmd_len(data, 0) == 1
    assert m2m.cmd_len(data, 1) == 2


def _convert_bytes(mdx_bytes: bytes, **kwargs) -> tuple[dict, m2m.ConversionReport]:
    align_tracks = kwargs.pop('align_tracks', True)
    mdx = m2m.MDXFile(mdx_bytes)
    conv = m2m.MDX2MewMML(mdx, **kwargs)
    channels = conv.convert()
    track_limit = 16 if conv.include_pcm else 8
    channels = m2m.apply_sync_wait_padding(channels, mdx, track_limit=track_limit)
    if align_tracks:
        channels = m2m.align_tracks_channels(channels)
    report = m2m.build_conversion_report(
        channels,
        unsupported=conv.unsupported_commands,
        duration_errors=conv.duration_approx_errors,
        aligned=align_tracks,
    )
    channels = m2m.remove_internal_markers(channels)
    return channels, report


def test_basic_track_tick_length():
    track = rest(48) + note(12, 48) + end_no_loop()
    mdx_bytes = build_mdx([track])
    channels, report = _convert_bytes(mdx_bytes)
    assert 'A' in channels
    assert report.channel_timelines['A'].total_ticks == 96


def test_tempo_marker():
    track = tempo(215) + rest(48) + end_no_loop()
    mdx_bytes = build_mdx([track])
    mdx = m2m.MDXFile(mdx_bytes)
    conv = m2m.MDX2MewMML(mdx)
    tokens = conv.convert_track(0, mdx.tracks[0])
    tempo_events = m2m.extract_tempo_events(tokens)
    assert tempo_events == [(0, 'T119')]


def test_key_off_tie():
    track = note(12, 24) + key_off_disable() + note(14, 24) + end_no_loop()
    mdx_bytes = build_mdx([track])
    channels, _ = _convert_bytes(mdx_bytes)
    joined = ' '.join(channels['A'])
    assert '&' in joined


def test_portamento_option():
    track = portamento(4096) + note(12, 48) + end_no_loop()
    mdx_bytes = build_mdx([track])
    channels, _ = _convert_bytes(mdx_bytes, emit_portamento=True)
    joined = ' '.join(channels['A'])
    assert '_' in joined


def test_local_repeat_doubles_notes():
    body = note(12, 24)
    # F6 count=2, note, F5 back to count byte
    back = -(len(body) + 3)
    track = repeat_start(2) + body + repeat_end(back) + end_no_loop()
    mdx_bytes = build_mdx([track])
    channels, report = _convert_bytes(mdx_bytes)
    assert report.channel_timelines['A'].total_ticks == 48


def test_align_tracks_pads_shorter_channel():
    track_a = rest(48) + note(12, 48) + end_no_loop()
    track_b = rest(48) + end_no_loop()
    mdx_bytes = build_mdx([track_a, track_b])
    channels, report = _convert_bytes(mdx_bytes, align_tracks=True)
    assert report.max_desync == 0
    assert report.channel_timelines['A'].total_ticks == report.channel_timelines['B'].total_ticks


def test_no_align_tracks_reports_desync():
    track_a = rest(48) + note(12, 48) + end_no_loop()
    track_b = rest(48) + end_no_loop()
    mdx_bytes = build_mdx([track_a, track_b])
    _, report = _convert_bytes(mdx_bytes, align_tracks=False)
    assert report.max_desync > 0


def test_sync_wait_padding():
    track_a = rest(24) + sync_wait() + rest(24) + end_no_loop()
    track_b = rest(48) + sync_send(0) + end_no_loop()
    mdx_bytes = build_mdx([track_a, track_b])
    channels, report = _convert_bytes(mdx_bytes)
    assert report.channel_timelines['A'].total_ticks == report.channel_timelines['B'].total_ticks


def test_extended_and_pcm_commands():
    track_fm = rest(24) + end_no_loop()
    track_pcm = pcm_enable() + note(0, 24) + extended_mml(1, 2) + end_no_loop()
    mdx_bytes = build_mdx([track_fm, track_pcm])
    mdx = m2m.MDXFile(mdx_bytes)
    assert len(mdx.tracks) >= 2
    conv = m2m.MDX2MewMML(mdx, include_pcm=True)
    conv.convert_track(1, mdx.tracks[1])
    assert conv.unsupported_commands[0xE8] >= 1
    assert conv.unsupported_commands[0xE7] >= 1


def test_key_on_delay_before_note():
    track = key_on_delay(12) + note(12, 48) + end_no_loop()
    mdx_bytes = build_mdx([track])
    channels, report = _convert_bytes(mdx_bytes)
    assert any('; k12' in token for token in channels['A'])
    assert report.channel_timelines['A'].total_ticks == 48


def test_format_conversion_report():
    track = rest(48) + end_no_loop()
    mdx_bytes = build_mdx([track, rest(24) + end_no_loop()])
    _, report = _convert_bytes(mdx_bytes, align_tracks=False)
    text = m2m.format_conversion_report(report)
    assert 'DESYNC' in text
    assert 'ch A' in text


def test_analyze_timeline_segments_with_global_loop():
    tokens = ['R8', m2m.GLOBAL_LOOP_MARKER, 'c4', 'R8']
    timeline = m2m.analyze_channel_timeline('A', tokens)
    assert timeline.segment_ticks == [24, 72]
