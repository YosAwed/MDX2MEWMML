"""合成 MDX バイナリを生成するヘルパー (テスト用)。"""

from __future__ import annotations

VOICE_OFFSET = 34
DEFAULT_VOICE = bytes([0] + [0] * 26)


def build_mdx(
    tracks: list[bytes],
    title: str = 'Test',
    pdx: str = 'TEST',
    voice: bytes | None = None,
) -> bytes:
    """
    最小構成の MDX ファイルを組み立てる。

    音色は offset 34 (テーブル直後)、未使用チャンクは末尾 offset を指す。
    """
    if not tracks:
        raise ValueError('tracks must not be empty')
    if len(tracks) > 16:
        raise ValueError('tracks max 16')

    voice_data = voice if voice is not None else DEFAULT_VOICE

    header = title.encode('shift_jis', errors='replace') + b'\r\n\x1a'
    header += pdx.encode('ascii', errors='replace') + b'\x00'
    offsetstart = len(header)

    track_offsets: list[int] = []
    cursor = VOICE_OFFSET + len(voice_data)
    for track in tracks:
        track_offsets.append(cursor)
        cursor += len(track)
    end_offset = cursor

    chunks = [VOICE_OFFSET] + track_offsets + [end_offset] * (16 - len(track_offsets))
    chunks = chunks[:17]
    table = b''.join(value.to_bytes(2, 'big') for value in chunks)

    body = voice_data + b''.join(tracks)
    return header + table + body


def rest(ticks: int) -> bytes:
    """ticks (1-128) の休符 1 バイト。MDX rest byte = ticks - 1。"""
    if not 1 <= ticks <= 128:
        raise ValueError('rest ticks must be 1..128')
    return bytes([ticks - 1])


def note(note_num: int, ticks: int) -> bytes:
    """note_num 0-95, ticks 1-256。"""
    if not 0 <= note_num <= 95:
        raise ValueError('note_num out of range')
    if not 1 <= ticks <= 256:
        raise ValueError('ticks out of range')
    return bytes([0x80 + note_num, ticks - 1])


def tempo(timer_b: int = 215) -> bytes:
    """timer_b=215 → BPM 約 119 (120 に最も近い整数 timer_b)"""
    return bytes([0xff, timer_b & 0xff])


def end_no_loop() -> bytes:
    return bytes([0xf1, 0x00, 0x00])


def key_off_disable() -> bytes:
    return bytes([0xf7])


def portamento(value: int) -> bytes:
    raw = value & 0xffff
    return bytes([0xf2, (raw >> 8) & 0xff, raw & 0xff])


def pan(value: int) -> bytes:
    return bytes([0xfc, value & 0xff])


def repeat_start(count: int = 2) -> bytes:
    return bytes([0xf6, count & 0xff, 0x00])


def repeat_end(back_offset: int) -> bytes:
    """0xF5: 相対オフセット (負値) で F6 の count バイト位置へ。"""
    raw = back_offset & 0xffff
    return bytes([0xf5, (raw >> 8) & 0xff, raw & 0xff])


def sync_wait() -> bytes:
    return bytes([0xee])


def sync_send(target_channel: int) -> bytes:
    return bytes([0xef, target_channel & 0xff])


def key_on_delay(ticks: int) -> bytes:
    return bytes([0xf0, ticks & 0xff])


def pcm_enable() -> bytes:
    return bytes([0xe8])


def extended_mml(sub: int, val: int) -> bytes:
    return bytes([0xe7, sub & 0xff, val & 0xff])


def global_loop(back_offset: int) -> bytes:
    raw = back_offset & 0xffff
    return bytes([0xf1, (raw >> 8) & 0xff, raw & 0xff])
