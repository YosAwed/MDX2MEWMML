#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mdx2mml.py  ―  MDX (MXDRV2 / Sharp X68000) → MewMMLPad MML コンバーター
==========================================================================
vampirefrog/mdxtools (mdx.c / mdx_decompiler.c) の仕様に完全準拠。
MewMMLPad v1.0.x のコマンド体系に合わせて出力します。

MDX フォーマット参考:
  https://github.com/vampirefrog/mdxtools
  http://www16.atwiki.jp/mxdrv/pages/23.html

使用方法:
  python mdx2mml.py song.mdx
  python mdx2mml.py song.mdx -o output.mml
  python mdx2mml.py song.mdx --dump
"""

import struct
import sys
import argparse
from collections import Counter
from pathlib import Path

# ══════════════════════════════════════════════════════════════════
# MDX 定数 (mdxtools/mdx.c / mdx_decompiler.c より)
# ══════════════════════════════════════════════════════════════════

# ── ノート名テーブル ──────────────────────────────────────────────
# MDX の note_num 0 = D# (最低音)
# mdx_decompiler.c: note_names[] = {"d+","e","f","f+","g","g+","a","a+","b","c","c+","d"}
NOTE_NAMES_MDX = ["d+", "e", "f", "f+", "g", "g+", "a", "a+", "b", "c", "c+", "d"]

# MewMMLPad は大文字音符 + "#" or "+" でシャープ, "-" でフラット
NOTE_NAMES_MEW = ["D+", "E", "F", "F+", "G", "G+", "A", "A+", "B", "C", "C+", "D"]
NOTE_NAMES_MEW_LOWER = [name.lower() for name in NOTE_NAMES_MEW]

# ── Tick → 音符長変換テーブル ─────────────────────────────────────
# 全音符 = 192 ticks, 4分音符 = 48 ticks
# MewMMLPad の数値音長で表せるものはできるだけ exact に出力する。
TICK_TO_LEN: dict = {
    192: "1",
    144: "2.",
    96:  "2",
    72:  "4.",
    64:  "3",
    48:  "4",
    36:  "8.",
    32:  "6",
    24:  "8",
    18:  "16.",
    16:  "12",
    12:  "16",
    9:   "32.",
    8:   "24",
    6:   "32",
    4:   "48",
    3:   "64",
    2:   "96",
    1:   "192",
}


def is_tempo_token(token: str) -> bool:
    """MewMMLPad のテンポ指定 Tn かどうかを判定する。"""
    return token.startswith('T') and len(token) > 1 and token[1:].isdigit()


TEMPO_MARKER_PREFIX = '$$TEMPO:'
REPEAT_ESCAPE_MARKER = '$$REPEAT_ESCAPE'
GLOBAL_LOOP_MARKER = '$$GLOBAL_LOOP'


def make_tempo_marker(tick: int, bpm: int) -> str:
    """整形時に同時刻テンポを間引くための内部トークンを作る。"""
    return f'{TEMPO_MARKER_PREFIX}{tick}:T{bpm}'


def parse_tempo_marker(token: str) -> tuple[int, str] | None:
    """内部テンポトークンなら (tick, Tn) を返す。"""
    if not token.startswith(TEMPO_MARKER_PREFIX):
        return None
    rest = token[len(TEMPO_MARKER_PREFIX):]
    tick_text, sep, tempo = rest.partition(':')
    if not sep or not tick_text.isdigit() or not is_tempo_token(tempo):
        return None
    return int(tick_text), tempo

# ── コマンドバイト合計バイト数テーブル (mdx.c: mdx_cmd_len) ──────
# 0x00-0x7f: 1 byte (rest)
# 0x80-0xdf: 2 bytes (note + duration)
# 0xea-0xec: 2 or 6 bytes (条件付き、別処理)
CMD_LEN: dict = {
    0xe6: 1,
    0xe7: 3,
    0xe8: 1,
    0xe9: 2,
    0xed: 2,
    0xee: 1,
    0xef: 2,
    0xf0: 2,
    0xf1: 3,
    0xf2: 3,
    0xf3: 3,
    0xf4: 3,
    0xf5: 3,
    0xf6: 3,
    0xf7: 1,
    0xf8: 2,
    0xf9: 1,
    0xfa: 1,
    0xfb: 2,
    0xfc: 2,
    0xfd: 2,
    0xfe: 3,
    0xff: 2,
}

# ══════════════════════════════════════════════════════════════════
# ユーティリティ関数
# ══════════════════════════════════════════════════════════════════

def cmd_len(data: bytes, pos: int) -> int:
    """mdx.c: mdx_cmd_len() と同等。負値はエラー/終端。"""
    if pos >= len(data):
        return -1
    c = data[pos]
    if c <= 0x7f:
        return 1
    if c <= 0xdf:
        return 2
    if c in (0xea, 0xeb, 0xec):
        if pos + 1 >= len(data):
            return -1
        return 2 if data[pos + 1] in (0x80, 0x81) else 6
    return CMD_LEN.get(c, 1)


def note_octave(n: int) -> int:
    """note_num → オクターブ番号 (mdx_decompiler.c: note_octave)"""
    return (n + 3) // 12


def ticks_to_mml_len(ticks: int) -> str:
    """Tick 数を MewMMLPad 音符長文字列に変換。テーブル外は最近傍。"""
    if ticks in TICK_TO_LEN:
        return TICK_TO_LEN[ticks]
    best = min(TICK_TO_LEN.keys(), key=lambda t: abs(t - ticks))
    return TICK_TO_LEN[best]


def ticks_to_mml_lengths(ticks: int) -> list[str]:
    """
    Tick 数を MewMMLPad 仕様内の音価列へ変換する。
    完全一致できない場合は、許容音価の合計が元 tick に最も近くなるよう分解する。
    """
    if ticks <= 0:
        return ["32"]
    if ticks in TICK_TO_LEN:
        return [TICK_TO_LEN[ticks]]

    values = sorted(TICK_TO_LEN.keys(), reverse=True)
    max_tick = max(ticks, max(values))
    best: dict[int, tuple[int, list[int]]] = {0: (abs(ticks), [])}

    for total in range(1, max_tick + 1):
        best_score = (abs(ticks - total), 10_000, [])
        for v in values:
            prev = total - v
            if prev < 0 or prev not in best:
                continue
            _, parts = best[prev]
            candidate = (abs(ticks - total), len(parts) + 1, parts + [v])
            if candidate[:2] < best_score[:2]:
                best_score = candidate
        if best_score[2]:
            best[total] = (best_score[0], best_score[2])

    _, parts = min(best.values(), key=lambda item: (item[0], len(item[1])))
    if not parts:
        parts = [min(values)]
    return [TICK_TO_LEN[p] for p in sorted(parts, reverse=True)]


def timer_b_to_bpm(n: int, scale: float = 1.0) -> int:
    """
    タイマーB値 → BPM。
    mdx_compiler.c: timer_b = 256 - 78125/(16*bpm) の逆算:
      bpm = 78125 / (16 * (256 - n))
    """
    div = 16 * (256 - n)
    if div <= 0:
        return 120
    return max(1, min(round((78125 / div) * scale), 9999))


def format_pan(pan_value: int) -> str:
    """MDX の出力位相を MewMMLPad の P0-P127 形式へ変換する。"""
    return {
        0: '; P0 ; (no output)',
        1: 'P0',
        2: 'P127',
        3: 'P64',
    }.get(pan_value, 'P64')


def format_volume(volume: int, command: str) -> str:
    """MDX 音量値を 0-127 に正規化して Vn などで出力する。"""
    if volume < 16:
        normalized = round(volume * 127 / 15)
    else:
        tl = 255 - volume
        normalized = max(0, min(127, 127 - tl))
    return f'{command}{normalized}'


def nonnegative_int(text: str) -> int:
    """argparse 用の 0 以上 int パーサー。"""
    value = int(text)
    if value < 0:
        raise argparse.ArgumentTypeError('0以上の整数を指定してください')
    return value


def parse_repeat_end(token: str) -> int | None:
    """']' または ']n' なら repeat count を返す。"""
    if not token.startswith(']'):
        return None
    count_text = token[1:]
    if not count_text:
        return 2
    return int(count_text) if count_text.isdigit() else None


def expand_repeats(tokens: list[str], expand_escape: bool = True) -> list[str]:
    """
    ローカルリピートを展開する。

    [ pre / post ]n は pre+post を n-1 回、最後に pre を出力する。
    グローバルループ点がリピート内部に入る MDX があるため、通常リピートも展開する。
    """
    result = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok != '[':
            result.append(tok)
            i += 1
            continue

        depth = 1
        j = i + 1
        while j < len(tokens) and depth:
            if tokens[j] == '[':
                depth += 1
            elif parse_repeat_end(tokens[j]) is not None:
                depth -= 1
            j += 1

        if depth:
            result.append(tok)
            i += 1
            continue

        end_token = tokens[j - 1]
        count = parse_repeat_end(end_token) or 2
        inner = expand_repeats(tokens[i + 1:j - 1], expand_escape)

        if REPEAT_ESCAPE_MARKER not in inner or not expand_escape:
            for _ in range(max(0, count)):
                result.extend(inner)
        else:
            escape_idx = inner.index(REPEAT_ESCAPE_MARKER)
            pre = inner[:escape_idx]
            post = [t for t in inner[escape_idx + 1:] if t != REPEAT_ESCAPE_MARKER]
            for _ in range(max(0, count - 1)):
                result.extend(pre)
                result.extend(post)
            result.extend(pre)

        i = j
    return result


LEN_TO_TICK = {v: k for k, v in TICK_TO_LEN.items()}


def token_duration_ticks(token: str) -> int:
    """変換後 MML トークンのおおよその tick 長を返す。"""
    if not token or token.startswith(';') or token.startswith('$'):
        return 0
    if token in ('[', '<', '>', '(', ')'):
        return 0
    if parse_repeat_end(token) is not None:
        return 0
    if token[0] not in 'RrCcDdEeFfGgAaBb':
        return 0

    total = 0
    for part in token.split('&'):
        if not part:
            continue
        if part[0] not in 'RrCcDdEeFfGgAaBb':
            continue
        head = part.split('_', 1)[0].rstrip('^&')
        idx = 1
        if idx < len(head) and head[idx] in '+-#':
            idx += 1
        length = head[idx:]
        total += LEN_TO_TICK.get(length, 24)
    return total


def tokens_duration_ticks(tokens: list[str]) -> int:
    """リピート構文を考慮してトークン列の tick 長を返す。"""
    total = 0
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if tok != '[':
            total += token_duration_ticks(tok)
            i += 1
            continue

        depth = 1
        j = i + 1
        while j < len(tokens) and depth:
            if tokens[j] == '[':
                depth += 1
            elif parse_repeat_end(tokens[j]) is not None:
                depth -= 1
            j += 1

        if depth:
            i += 1
            continue

        count = parse_repeat_end(tokens[j - 1]) or 2
        total += tokens_duration_ticks(tokens[i + 1:j - 1]) * count
        i = j
    return total


def loop_body_duration_ticks(tokens: list[str]) -> int:
    """グローバルループ点以降の tick 長を返す。"""
    if GLOBAL_LOOP_MARKER not in tokens:
        return 0
    marker_idx = tokens.index(GLOBAL_LOOP_MARKER)
    return tokens_duration_ticks(tokens[marker_idx + 1:])


def rest_tokens_for_ticks(ticks: int) -> list[str]:
    """指定 tick 分の休符トークンを作る。"""
    return ['R' + length for length in ticks_to_mml_lengths(ticks)] if ticks > 0 else []


def expand_global_loop(
    tokens: list[str],
    loop_count: int,
    target_loop_ticks: int | None = None,
) -> list[str]:
    """グローバルループ点以降を指定回数ぶん展開し、必要なら区間末尾を休符で揃える。"""
    if GLOBAL_LOOP_MARKER not in tokens:
        return tokens
    marker_idx = tokens.index(GLOBAL_LOOP_MARKER)
    intro = tokens[:marker_idx]
    loop_body = tokens[marker_idx + 1:]
    loop_ticks = tokens_duration_ticks(loop_body)
    pad_ticks = max(0, (target_loop_ticks or loop_ticks) - loop_ticks)
    loop_body_with_pad = loop_body + rest_tokens_for_ticks(pad_ticks)
    expanded = list(intro)
    for _ in range(max(0, loop_count)):
        expanded.extend(loop_body_with_pad)
    return expanded


def split_global_segments(tokens: list[str]) -> list[list[str]]:
    """内部グローバルループマーカーでトークン列を分割する。"""
    segments = [[]]
    for token in tokens:
        if token == GLOBAL_LOOP_MARKER:
            segments.append([])
        else:
            segments[-1].append(token)
    return segments


def align_global_loop_segments(channels: dict[str, list[str]]) -> dict[str, list[str]]:
    """F1 ループ境界ごとに全チャンネルの長さを休符で揃える。"""
    if not any(GLOBAL_LOOP_MARKER in tokens for tokens in channels.values()):
        return channels

    split = {ch: split_global_segments(tokens) for ch, tokens in channels.items()}
    segment_count = max((len(segments) for segments in split.values()), default=0)
    target_ticks = []
    for index in range(segment_count):
        target_ticks.append(max(
            (
                tokens_duration_ticks(segments[index])
                for segments in split.values()
                if index < len(segments)
            ),
            default=0,
        ))

    aligned = {}
    for ch, segments in split.items():
        tokens = []
        for index, target in enumerate(target_ticks):
            segment = segments[index] if index < len(segments) else []
            tokens.extend(segment)
            tokens.extend(rest_tokens_for_ticks(target - tokens_duration_ticks(segment)))
        aligned[ch] = tokens
    return aligned


def remove_internal_markers(channels: dict[str, list[str]]) -> dict[str, list[str]]:
    """出力前に内部マーカーを取り除く。"""
    return {
        ch: [token for token in tokens if token != GLOBAL_LOOP_MARKER]
        for ch, tokens in channels.items()
    }


def read_until(data: bytes, pos: int, terminator: bytes) -> tuple:
    """pos から terminator バイト列まで読む。(str, 次pos) を返す。"""
    end = pos
    tl = len(terminator)
    while end + tl <= len(data):
        if data[end:end + tl] == terminator:
            raw = data[pos:end]
            return raw.decode('shift_jis', errors='replace'), end + tl
        end += 1
    return data[pos:].decode('shift_jis', errors='replace'), len(data)

# ══════════════════════════════════════════════════════════════════
# MDX ファイルパーサー
# ══════════════════════════════════════════════════════════════════

class MDXFile:
    """MDX バイナリを解析してトラック/音色データへの参照を保持する。"""

    def __init__(self, data: bytes):
        self.data = data
        self.title = ""
        self.pdx_filename = ""
        self.tracks: list = []       # list of bytes (各トラックのバイト列)
        self.voices: dict = {}       # voice_id → 27 bytes
        self.num_voices = 0
        self._parse()

    def _parse(self):
        data = self.data

        # タイトル: \x0d\x0a\x1a で終端
        self.title, pos = read_until(data, 0, b'\x0d\x0a\x1a')

        # PDX ファイル名: \x00 で終端
        self.pdx_filename, pos = read_until(data, pos, b'\x00')

        # LZX 圧縮チェック
        if data[pos + 4:pos + 7] == b'LZX':
            raise ValueError("LZX 圧縮 MDX は非対応です。解凍してから変換してください。")

        offsetstart = pos

        # 17 エントリのオフセットテーブル: [0]=音色, [1..16]=各トラック
        NCHUNKS = 17
        chunks = []
        for i in range(NCHUNKS):
            ofs = (data[offsetstart + i * 2] << 8) | data[offsetstart + i * 2 + 1]
            abs_ofs = offsetstart + ofs
            clen = max(0, len(data) - abs_ofs) if abs_ofs < len(data) else 0
            chunks.append({'offset': ofs, 'len': clen})

        # num_tracks 計算: トラック[1..10]の最小オフセットから
        valid_ofs = [chunks[i]['offset'] for i in range(1, 11) if chunks[i]['len'] > 0]
        min_ofs = min(valid_ofs) if valid_ofs else 2
        num_tracks = min((min_ofs - 2) // 2, 16)

        # 各チャンクの実長を精緻化
        for i in range(NCHUNKS):
            if not chunks[i]['len'] or i > num_tracks + 1:
                chunks[i]['len'] = 0
                continue
            for j in range(NCHUNKS):
                if not chunks[j]['len']:
                    continue
                if (chunks[i]['offset'] < chunks[j]['offset'] and
                        chunks[i]['len'] > chunks[j]['offset'] - chunks[i]['offset']):
                    chunks[i]['len'] = chunks[j]['offset'] - chunks[i]['offset']

        # 音色データ (27 bytes/音色, 先頭バイト = voice_id)
        v_abs = offsetstart + chunks[0]['offset']
        v_len = chunks[0]['len']
        self.num_voices = v_len // 27
        for i in range(self.num_voices):
            vp = v_abs + i * 27
            if vp + 27 <= len(data):
                vid = data[vp]
                self.voices[vid] = data[vp:vp + 27]

        # トラックデータ
        self.tracks = []
        for i in range(num_tracks):
            c = chunks[i + 1]
            if c['len'] <= 0:
                break
            a = offsetstart + c['offset']
            self.tracks.append(data[a:a + c['len']])

# ══════════════════════════════════════════════════════════════════
# MDX → MewMMLPad MML 変換器
# ══════════════════════════════════════════════════════════════════

class MDX2MewMML:
    """
    MDXFile の各トラックを MewMMLPad MML トークン列に変換する。
    mdxtools/mdx_decompiler.c のロジックを Python で再現し、
    MewMMLPad コマンド体系に適合させたもの。
    """

    def __init__(
        self,
        mdx: MDXFile,
        tempo_scale: float = 1.0,
        note_case: str = 'lower',
        volume_command: str = 'V',
        expand_repeat_escape: bool = True,
        global_loop_count: int = 2,
        include_pcm: bool = True,
    ):
        self.mdx = mdx
        self.tempo_scale = tempo_scale
        self.note_names = NOTE_NAMES_MEW_LOWER if note_case == 'lower' else NOTE_NAMES_MEW
        self.volume_command = volume_command
        self.expand_repeat_escape = expand_repeat_escape
        self.global_loop_count = global_loop_count
        self.include_pcm = include_pcm

    @staticmethod
    def track_name(i: int) -> str:
        """FM 0-7 → A-H、PCM/ADPCM 8-15 → 仮想 I-P。"""
        return chr(ord('A') + i)

    def detect_opm_percussion(self, track_data: bytes) -> tuple[bool, str]:
        """
        OPM 音色でドラム/パーカッション的に使われているトラックを推定する。

        MDX には「この FM トラックはドラム」という確定フラグがないため、
        短音、音色切替、音程反復、疎なリズムを組み合わせたヒューリスティックにする。
        """
        notes = []
        voice_changes = 0
        note_voices = []
        rest_ticks = 0
        portamento_count = 0
        current_voice = None

        pos = 0
        n = len(track_data)
        safety = 0
        while pos < n:
            safety += 1
            if safety > 500_000:
                break

            r = cmd_len(track_data, pos)
            if r <= 0:
                break

            b = track_data[pos]
            if b <= 0x7f:
                rest_ticks += b + 1
            elif b <= 0xdf:
                ticks = (track_data[pos + 1] + 1) if pos + 1 < n else 1
                note_num = b - 0x80
                notes.append((note_num, ticks))
                note_voices.append(current_voice)
            elif b == 0xfd:
                current_voice = track_data[pos + 1] if pos + 1 < n else 0
                voice_changes += 1
            elif b == 0xf2:
                portamento_count += 1
            elif b == 0xf1:
                break

            pos += r

        note_count = len(notes)
        if note_count < 8:
            return False, ''

        note_nums = [note for note, _ in notes]
        durations = [ticks for _, ticks in notes]
        note_ticks = sum(durations)
        total_ticks = note_ticks + rest_ticks

        short_ratio = sum(t <= 24 for t in durations) / note_count
        very_short_ratio = sum(t <= 12 for t in durations) / note_count
        long_ratio = sum(t >= 48 for t in durations) / note_count
        avg_duration = note_ticks / note_count

        pitch_counts = Counter(note_nums)
        unique_notes = len(pitch_counts)
        most_common_ratio = pitch_counts.most_common(1)[0][1] / note_count

        used_voices = [v for v in note_voices if v is not None]
        unique_voices = len(set(used_voices))
        voice_change_rate = voice_changes / note_count

        note_fill_ratio = note_ticks / total_ticks if total_ticks else 1.0

        score = 0
        reasons = []

        if short_ratio >= 0.8:
            score += 3
            reasons.append(f'短音{short_ratio:.0%}')
        elif short_ratio >= 0.6:
            score += 2
            reasons.append(f'短音{short_ratio:.0%}')
        elif short_ratio >= 0.45:
            score += 1

        if very_short_ratio >= 0.5:
            score += 1
        if avg_duration <= 18:
            score += 1
        if long_ratio >= 0.25:
            score -= 2

        voice_signature = False
        if unique_voices >= 3:
            score += 2
            voice_signature = True
            reasons.append(f'音色{unique_voices}種')
        elif unique_voices == 2 and voice_change_rate >= 0.15:
            score += 1
            voice_signature = True
            reasons.append('音色切替あり')

        if voice_change_rate >= 0.35:
            score += 2
            voice_signature = True
        elif voice_change_rate >= 0.15:
            score += 1
            voice_signature = True

        pitch_signature = False
        if most_common_ratio >= 0.45:
            score += 1
            pitch_signature = True
            reasons.append(f'反復音高{most_common_ratio:.0%}')
        if unique_notes <= 4:
            score += 1
            pitch_signature = True
            reasons.append(f'音高{unique_notes}種')

        if note_fill_ratio <= 0.5:
            score += 1
            reasons.append(f'休符多め{1 - note_fill_ratio:.0%}')

        if portamento_count:
            score -= 2

        if score < 5 or not (voice_signature or pitch_signature):
            return False, ''

        reason_text = ', '.join(dict.fromkeys(reasons)) or '短音/反復パターン'
        return True, f'; 推定: OPMドラム/パーカッションの可能性 (score={score}; {reason_text})'

    def convert_track(self, track_idx: int, track_data: bytes) -> list:
        data = track_data
        n = len(data)
        tokens = []

        octave = -1            # 現オクターブ (-1=未設定)
        rest_ticks = 0         # 蓄積レスト tick 数
        next_key_off = False   # 0xf7 フラグ (次の音符に & を付ける)
        portamento = 0         # ポルタメント値 (符号付き int)
        elapsed_ticks = 0      # テンポ同時発生判定用のトラック内 tick
        repeat_stack = []      # 実行中ローカルリピート
        loop_stack_snapshot = None
        loop_state_snapshot = None
        global_loops_done = 0

        # ── グローバルループ点を先行スキャン ─────────────────────
        loop_point = -1
        sp = 0
        while sp < n:
            r = cmd_len(data, sp)
            if r <= 0:
                break
            if data[sp] == 0xf1:
                if sp + 2 < n:
                    hi, lo = data[sp + 1], data[sp + 2]
                    raw = (hi << 8) | lo
                    signed_ofs = raw - 65536 if raw >= 32768 else raw
                    # mdxtools mdx_driver.c 準拠: pos += ofs + 3 でループ点に飛ぶ。
                    # 旧コードは +1 で 2 バイト早い位置に着地し、L 直前の音符を
                    # ループ外に取り残してトラック間の tick がずれていた。
                    lp = sp + signed_ofs + 3
                    if 0 <= lp < n and hi != 0:
                        loop_point = lp
                break
            sp += r

        # ── レスト出力ヘルパー ──────────────────────────────────
        def flush_rest():
            nonlocal rest_ticks
            if rest_ticks > 0:
                tokens.extend('R' + length for length in ticks_to_mml_lengths(rest_ticks))
                rest_ticks = 0

        # ── メイン変換ループ ─────────────────────────────────────
        pos = 0
        safety = 0
        while pos < n:
            safety += 1
            if safety > 500_000:
                tokens.append('; (イベント上限)')
                break

            r = cmd_len(data, pos)
            if r <= 0:
                break

            b = data[pos]

            # グローバルループ点。リピート途中を指す場合があるため、
            # 初回到達時のリピート状態を F1 ループ時に復元する。
            if pos == loop_point:
                flush_rest()
                if loop_stack_snapshot is None:
                    loop_stack_snapshot = [dict(item) for item in repeat_stack]
                    loop_state_snapshot = {
                        'next_key_off': next_key_off,
                        'portamento': portamento,
                    }

            # ── Rest 0x00-0x7f (1 byte) ──────────────────────────
            if b <= 0x7f:
                ticks = b + 1
                rest_ticks += ticks
                elapsed_ticks += ticks
                if rest_ticks >= 0x7f:
                    flush_rest()
                pos += 1
                continue

            # ── Note 0x80-0xdf (2 bytes) ─────────────────────────
            if b <= 0xdf:
                flush_rest()
                ticks = (data[pos + 1] + 1) if pos + 1 < n else 1
                note_num = b - 0x80
                o = note_octave(note_num)

                # オクターブ変更
                if o != octave:
                    if octave == -1:
                        tokens.append(f'O{o}')
                    elif o < octave:
                        tokens.extend(['<'] * (octave - o))
                    else:
                        tokens.extend(['>'] * (o - octave))
                    octave = o

                note_name = self.note_names[note_num % 12]
                # ポルタメント後処理
                if portamento and track_idx < 8:
                    len_parts = ticks_to_mml_lengths(ticks)
                    note_token = '&'.join(f'{note_name}{len_str}' for len_str in len_parts)
                    tokens.append(note_token)
                    portamento = 0
                    next_key_off = False
                else:
                    len_parts = ticks_to_mml_lengths(ticks)
                    note_token = '&'.join(f'{note_name}{len_str}' for len_str in len_parts)
                    tokens.append(note_token)
                    next_key_off = False

                elapsed_ticks += ticks
                pos += 2
                continue

            # ── Commands 0xe0-0xff ────────────────────────────────
            flush_rest()

            if b == 0xff:
                # テンポ設定
                tb = data[pos + 1] if pos + 1 < n else 200
                tokens.append(make_tempo_marker(elapsed_ticks, timer_b_to_bpm(tb, self.tempo_scale)))

            elif b == 0xfe:
                # OPM レジスタ直書き (MewMMLPad 非対応 → コメント)
                reg = data[pos + 1] if pos + 1 < n else 0
                val = data[pos + 2] if pos + 2 < n else 0
                tokens.append(f'; y{reg},{val}')

            elif b == 0xfd:
                # 音色番号 → @{num} (プログラムチェンジ)
                tokens.append(f'@{data[pos + 1] if pos + 1 < n else 0}')

            elif b == 0xfc:
                # 出力位相 (パン): 1=右, 2=左, 3=両方(センター), 0=無音
                pv = data[pos + 1] if pos + 1 < n else 3
                tokens.append(format_pan(pv))

            elif b == 0xfb:
                # ボリューム設定
                vol = data[pos + 1] if pos + 1 < n else 0
                tokens.append(format_volume(vol, self.volume_command))

            elif b == 0xfa:
                tokens.append(')')   # ボリューム上げ

            elif b == 0xf9:
                tokens.append('(')   # ボリューム下げ

            elif b == 0xf8:
                # サウンド長 (ゲートタイム)
                qv = data[pos + 1] if pos + 1 < n else 8
                if qv <= 8:
                    tokens.append(f'Q{qv}')
                else:
                    tokens.append(f'; @q{256 - qv}')

            elif b == 0xf7:
                # キーオフ無効 → 次の音符に & (タイ) を付ける
                next_key_off = True

            elif b == 0xf6:
                # リピート開始 (byte2 = count)。MewMML 側の構文にせず、
                # MDX バイトコードとして展開する。
                count = data[pos + 1] if pos + 1 < n else 2
                repeat_stack.append({'start': pos + r, 'remaining': max(1, count)})

            elif b == 0xf5:
                # リピート終端: 相対オフセットで 0xf6 の count バイトを参照
                if repeat_stack:
                    current_repeat = repeat_stack[-1]
                    if current_repeat['remaining'] > 1:
                        current_repeat['remaining'] -= 1
                        next_key_off = False
                        portamento = 0
                        pos = current_repeat['start']
                        continue
                    repeat_stack.pop()

            elif b == 0xf4:
                # リピート脱出。最後の繰り返しでは ] の直後へスキップする。
                # mdxtools mdx_driver.c 準拠:
                #   pos += 3; pos += ofs + 2  →  最終 pos = f5_pos + 3
                # 直接そこに飛ばし、リピートスタックを明示的に pop する。
                # ポルタメント / キーオフ抑止フラグはループ外へ持ち越すため温存する。
                if repeat_stack and repeat_stack[-1]['remaining'] == 1 and pos + 2 < n:
                    raw16 = (data[pos + 1] << 8) | data[pos + 2]
                    signed = raw16 - 65536 if raw16 >= 32768 else raw16
                    target = pos + signed + 5
                    if 0 <= target <= n:
                        repeat_stack.pop()
                        pos = target
                        continue

            elif b == 0xf3:
                # デチューン (符号付き 16-bit)
                if pos + 2 < n:
                    raw = (data[pos + 1] << 8) | data[pos + 2]
                    det = raw - 65536 if raw >= 32768 else raw
                    tokens.append(f'; D{det}')

            elif b == 0xf2:
                # ポルタメント (符号付き 16-bit)
                if pos + 2 < n:
                    raw = (data[pos + 1] << 8) | data[pos + 2]
                    portamento = raw - 65536 if raw >= 32768 else raw

            elif b == 0xf1:
                # データ終端。ループポインタがあれば有限回だけ再演奏する。
                extra_loop_count = max(0, self.global_loop_count - 1)
                if loop_point >= 0 and global_loops_done < extra_loop_count:
                    flush_rest()
                    tokens.append(GLOBAL_LOOP_MARKER)
                    global_loops_done += 1
                    repeat_stack = [dict(item) for item in (loop_stack_snapshot or [])]
                    if loop_state_snapshot:
                        next_key_off = loop_state_snapshot['next_key_off']
                        portamento = loop_state_snapshot['portamento']
                    pos = loop_point
                    continue
                break

            elif b == 0xf0:
                # キーオンディレイ (MewMMLPad 非対応 → コメント)
                kd = data[pos + 1] if pos + 1 < n else 0
                tokens.append(f'; k{kd}')

            elif b == 0xef:
                # Sync send
                ch = data[pos + 1] if pos + 1 < n else 0
                chn = chr(ord('A') + ch) if ch < 8 else chr(ord('P') + ch - 8)
                tokens.append(f'; S{chn}')

            elif b == 0xee:
                tokens.append('; W')  # Sync wait

            elif b == 0xed:
                # ADPCM / ノイズ周波数
                val = data[pos + 1] if pos + 1 < n else 0
                if track_idx < 8:
                    tokens.append(f'; w{val & 0x1f}')
                else:
                    tokens.append(f'; F{val}')

            elif b in (0xea, 0xeb, 0xec):
                # LFO 系 (OPM LFO / Amplitude LFO / Pitch LFO)
                if pos + 1 < n:
                    mode = data[pos + 1]
                    labels = {0xec: ('MPOF', 'MPON', 'MP'), 0xeb: ('MAOF', 'MAON', 'MA'), 0xea: ('MHOF', 'MHON', 'MH')}
                    of_str, on_str, base = labels[b]
                    if mode == 0x80:
                        tokens.append(f'; {of_str}')
                    elif mode == 0x81:
                        tokens.append(f'; {on_str}')
                    elif pos + 5 < n:
                        p1 = (data[pos + 2] << 8) | data[pos + 3]
                        p2 = data[pos + 4]
                        p3 = data[pos + 5] - 256 if data[pos + 5] >= 128 else data[pos + 5]
                        tokens.append(f'; {base}{mode},{p1},{p3}')

            elif b == 0xe9:
                # LFO ディレイ
                tokens.append(f'; MD{data[pos + 1] if pos + 1 < n else 0}')

            # 0xe8 (PCM enable), 0xe7 (extended MML), 0xe6 → 無視

            pos += r

        flush_rest()
        return tokens

    def convert(self) -> dict:
        """各トラックを {チャンネル名: トークンリスト} に変換する。"""
        result = {}
        track_limit = 16 if self.include_pcm else 8
        for i, td in enumerate(self.mdx.tracks[:track_limit]):
            ch = self.track_name(i)
            toks = self.convert_track(i, td)
            is_percussion, comment = self.detect_opm_percussion(td) if i < 8 else (False, '')
            if is_percussion:
                toks.insert(0, comment)
            if toks:
                result[ch] = toks

        return remove_internal_markers(result)

# ══════════════════════════════════════════════════════════════════
# MewMMLPad MML フォーマッター
# ══════════════════════════════════════════════════════════════════

def format_mewmml(
    channels: dict,
    title: str = '',
    pdx: str = '',
    line_width: int = 120,
    volume_command: str = 'V',
) -> str:
    """
    MewMMLPad テキスト形式に整形する。

    各チャンネル行:
      A T120 O4 L8 V100 [MML...]
      B O4 L8 V100 [MML...]
    コメント行は単独行に分離する。
    """
    lines = [
        '; ============================================================',
        f'; Title   : {title or "(無題)"}',
        f'; PDX     : {pdx or "(なし)"}',
        '; Converted: MDX (MXDRV2) → MewMMLPad  [mdx2mml.py]',
        '; ============================================================',
        '; MewMMLPad で開いてそのまま再生できます。',
        '; @n = MIDI プログラムチェンジ番号',
        f'; {volume_command}n = 変換後ボリューム',
        '; ; で始まるトークン = 変換不可コマンド (デチューン等)',
        '',
    ]

    valid_channel_names = 'ABCDEFGHIJKLMNOP'
    valid_channels = [(ch, tokens) for ch, tokens in channels.items() if ch in valid_channel_names]
    has_any_tempo = any(
        parse_tempo_marker(t) is not None or is_tempo_token(t)
        for _, tokens in valid_channels
        for t in tokens
    )
    default_tempo_channel = valid_channels[0][0] if valid_channels and not has_any_tempo else None
    emitted_tempo_ticks = set()

    for ch, tokens in valid_channels:
        lines.append(f'; --- Channel {ch} ---')

        body_tokens = list(tokens)
        while body_tokens and body_tokens[0].startswith(';'):
            lines.append(body_tokens.pop(0))

        header = []
        if ch == default_tempo_channel:
            header.append('T120')
        header += ['O4', 'L8', f'{volume_command}100']

        channel_tokens = []
        for tok in body_tokens:
            marker = parse_tempo_marker(tok)
            if marker is None:
                channel_tokens.append(tok)
                continue

            tick, tempo = marker
            if tick in emitted_tempo_ticks:
                continue
            emitted_tempo_ticks.add(tick)
            channel_tokens.append(tempo)

        all_toks = header + channel_tokens

        # 行折り返し。演奏行は必ずチャンネル文字で始める。
        cur = ch
        def emit_current():
            nonlocal cur
            if cur == ch:
                return
            lines.append(cur[:-1] if cur.endswith('&') else cur)
            cur = ch

        for tok in all_toks:
            if tok.startswith(';'):
                emit_current()
                lines.append(tok)
            elif cur.endswith(('&', '^')):
                cur += tok
            elif len(cur) + 1 + len(tok) > line_width:
                emit_current()
                cur = f'{ch} {tok}'
            else:
                cur += ' ' + tok
        emit_current()
        lines.append('')

    return '\n'.join(lines)

# ══════════════════════════════════════════════════════════════════
# メイン変換処理
# ══════════════════════════════════════════════════════════════════

def convert(
    mdx_path: str,
    output_path: str = None,
    dump: bool = False,
    tempo_scale: float = 1.0,
    note_case: str = 'lower',
    volume_command: str = 'V',
    expand_repeat_escape: bool = True,
    global_loop_count: int = 2,
    include_pcm: bool = True,
) -> str:
    path = Path(mdx_path)
    if not path.exists():
        raise FileNotFoundError(f'MDX ファイルが見つかりません: {mdx_path}')

    print(f'[1/4] 読み込み中: {path.name}  ({path.stat().st_size:,} bytes)')
    data = path.read_bytes()

    print('[2/4] MDX バイナリをパース中...')
    mdx = MDXFile(data)
    print(f'      タイトル    : {mdx.title or "(無題)"}')
    print(f'      PDX ファイル: {mdx.pdx_filename or "(なし)"}')
    print(f'      トラック数  : {len(mdx.tracks)}')
    print(f'      音色数      : {mdx.num_voices}')

    print('[3/4] MewMMLPad MML に変換中...')
    conv = MDX2MewMML(
        mdx,
        tempo_scale=tempo_scale,
        note_case=note_case,
        volume_command=volume_command,
        expand_repeat_escape=expand_repeat_escape,
        global_loop_count=global_loop_count,
        include_pcm=include_pcm,
    )
    channels = conv.convert()
    for ch, toks in channels.items():
        print(f'      ch {ch}: {len(toks)} トークン')

    print('[4/4] テキスト整形中...')
    mml_text = format_mewmml(
        channels,
        title=mdx.title,
        pdx=mdx.pdx_filename,
        volume_command=volume_command,
    )

    out = Path(output_path) if output_path else path.with_suffix('.mml')
    out.write_text(mml_text, encoding='utf-8')
    print(f'\n✓ 出力完了: {out}')

    if dump:
        print('\n── MML ──────────────────────────────────────────────')
        print(mml_text)

    return mml_text

# ══════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════

def main():
    ap = argparse.ArgumentParser(
        description='MDX (MXDRV2/X68000) → MewMMLPad MML コンバーター',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog='''
使用例:
  python mdx2mml.py song.mdx
  python mdx2mml.py song.mdx -o output.mml
  python mdx2mml.py song.mdx --dump
  python mdx2mml.py song.mdx --tempo-scale 0.5 --note-case upper --volume-command V --global-loop-count 3
        '''
    )
    ap.add_argument('mdx_file')
    ap.add_argument('-o', '--output', default=None)
    ap.add_argument('--dump', action='store_true')
    ap.add_argument(
        '--tempo-scale',
        type=float,
        choices=(0.5, 1.0, 2.0),
        default=1.0,
        help='テンポを 0.5倍 / 1倍 / 2倍で出力する',
    )
    ap.add_argument(
        '--note-case',
        choices=('lower', 'upper'),
        default='lower',
        help='音符名の大小文字。既定は lower',
    )
    ap.add_argument(
        '--volume-command',
        choices=('V', 'v', '@V'),
        default='V',
        help='MDX ボリュームの出力コマンド。既定は V',
    )
    ap.add_argument(
        '--no-expand-repeat-escape',
        action='store_true',
        help='リピート脱出を展開せずコメントとして残す',
    )
    ap.add_argument(
        '--global-loop-count',
        type=nonnegative_int,
        default=2,
        help='グローバルループ点以降の総再生回数。既定は 2',
    )
    ap.add_argument(
        '--no-pcm',
        action='store_true',
        help='PCM/ADPCM トラック(仮想I-P)を出力しない',
    )
    args = ap.parse_args()

    try:
        convert(
            args.mdx_file,
            args.output,
            args.dump,
            tempo_scale=args.tempo_scale,
            note_case=args.note_case,
            volume_command=args.volume_command,
            expand_repeat_escape=not args.no_expand_repeat_escape,
            global_loop_count=args.global_loop_count,
            include_pcm=not args.no_pcm,
        )
    except (FileNotFoundError, ValueError) as e:
        print(f'エラー: {e}', file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
