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

# ── Tick → 音符長変換テーブル ─────────────────────────────────────
# 全音符 = 192 ticks, 4分音符 = 48 ticks
# MewMMLPad の仕様内にある 1〜32 分音符と付点だけを出力する。
TICK_TO_LEN: dict = {
    192: "1",
    144: "2.",
    96:  "2",
    72:  "4.",
    48:  "4",
    36:  "8.",
    24:  "8",
    18:  "16.",
    12:  "16",
    9:   "32.",
    6:   "32",
}


def is_tempo_token(token: str) -> bool:
    """MewMMLPad のテンポ指定 Tn かどうかを判定する。"""
    return token.startswith('T') and len(token) > 1 and token[1:].isdigit()


TEMPO_MARKER_PREFIX = '$$TEMPO:'


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


def timer_b_to_bpm(n: int) -> int:
    """
    タイマーB値 → BPM。
    mdx_compiler.c: timer_b = 256 - 78125/(16*bpm) の逆算:
      bpm = 78125 / (16 * (256 - n))
    """
    div = 16 * (256 - n)
    if div <= 0:
        return 120
    return max(1, min(round(78125 / div), 9999))


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

    def __init__(self, mdx: MDXFile):
        self.mdx = mdx

    @staticmethod
    def track_name(i: int) -> str:
        """FM 0-7 → A-H。MewMMLPad に PCM/ADPCM トラックはない。"""
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
                    lp = sp + signed_ofs + 1
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

            # グローバルループマーカー
            if pos == loop_point:
                flush_rest()
                tokens.append('; === LOOP POINT ===')
                octave = -1

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

                note_name = NOTE_NAMES_MEW[note_num % 12]
                # ポルタメント後処理
                if portamento and track_idx < 8:
                    nn = note_num + portamento * (ticks + 1) // 16384
                    nn = max(0, min(95, nn))
                    o2 = note_octave(nn)
                    target_octave = ''
                    if o2 != octave:
                        target_octave = ('>' if o2 > octave else '<') * abs(o2 - octave)
                        octave = o2
                    len_str = ticks_to_mml_len(ticks)
                    tie = '&' if next_key_off else ''
                    tokens.append(f'{note_name}{len_str}_{target_octave}{NOTE_NAMES_MEW[nn % 12]}{tie}')
                    portamento = 0
                    next_key_off = False
                else:
                    len_parts = ticks_to_mml_lengths(ticks)
                    note_token = '&'.join(f'{note_name}{len_str}' for len_str in len_parts)
                    if next_key_off:
                        note_token += '&'
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
                tokens.append(make_tempo_marker(elapsed_ticks, timer_b_to_bpm(tb)))

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
                pan_mml = {0: '; P0 ; (no output)', 1: 'PR63', 2: 'PL63', 3: 'PC'}
                tokens.append(pan_mml.get(pv, 'PC'))

            elif b == 0xfb:
                # ボリューム設定
                vol = data[pos + 1] if pos + 1 < n else 0
                if vol < 16:
                    # 相対ボリューム 0-15 → V0-127
                    tokens.append(f'V{round(vol * 127 / 15)}')
                else:
                    # OPM TL 系: 255-vol = TL (0=大, 127=小) → 反転して V0-127
                    tl = 255 - vol
                    tokens.append(f'V{max(0, min(127, 127 - tl))}')

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
                # リピート開始 (byte2 = count)
                tokens.append('[')
                octave = -1

            elif b == 0xf5:
                # リピート終端: 相対オフセットで 0xf6 の count バイトを参照
                if pos + 2 < n:
                    raw16 = (data[pos + 1] << 8) | data[pos + 2]
                    ofs16 = (raw16 - 65536 if raw16 >= 32768 else raw16) + 1
                    target = pos + ofs16
                    if 0 <= target < n and data[target] > 2:
                        tokens.append(f']{data[target]}')
                    else:
                        tokens.append(']')
                else:
                    tokens.append(']')
                octave = -1

            elif b == 0xf4:
                # リピート脱出 (MewMMLPad 非対応 → コメント)
                tokens.append('; /')

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
                # データ終端 → 変換終了
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
        """FM 8 トラックを {チャンネル名: トークンリスト} に変換する。"""
        result = {}
        for i, td in enumerate(self.mdx.tracks[:8]):
            ch = self.track_name(i)
            toks = self.convert_track(i, td)
            is_percussion, comment = self.detect_opm_percussion(td)
            if is_percussion:
                toks.insert(0, comment)
            if toks:
                result[ch] = toks
        return result

# ══════════════════════════════════════════════════════════════════
# MewMMLPad MML フォーマッター
# ══════════════════════════════════════════════════════════════════

def format_mewmml(
    channels: dict,
    title: str = '',
    pdx: str = '',
    line_width: int = 120,
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
        '; ; で始まるトークン = 変換不可コマンド (デチューン等)',
        '',
    ]

    valid_channels = [(ch, tokens) for ch, tokens in channels.items() if ch in 'ABCDEFGH']
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
        header += ['O4', 'L8', 'V100']

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

        # 行折り返し。演奏行は必ずチャンネル文字 A-H で始める。
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
            elif cur.endswith('&'):
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

def convert(mdx_path: str, output_path: str = None, dump: bool = False) -> str:
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
    conv = MDX2MewMML(mdx)
    channels = conv.convert()
    for ch, toks in channels.items():
        print(f'      ch {ch}: {len(toks)} トークン')

    print('[4/4] テキスト整形中...')
    mml_text = format_mewmml(channels, title=mdx.title, pdx=mdx.pdx_filename)

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
        '''
    )
    ap.add_argument('mdx_file')
    ap.add_argument('-o', '--output', default=None)
    ap.add_argument('--dump', action='store_true')
    args = ap.parse_args()

    try:
        convert(args.mdx_file, args.output, args.dump)
    except (FileNotFoundError, ValueError) as e:
        print(f'エラー: {e}', file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
