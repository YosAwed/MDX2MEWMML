# MDX2MEWMML

MDX (MXDRV2 / Sharp X68000) ファイルを [MewMMLPad](https://github.com/mewlist) 用の MML テキストに変換する Python スクリプトです。

## 概要

Sharp X68000 の MXDRV2 音楽ドライバが扱う `.mdx` バイナリファイルを解析し、MewMMLPad VST3 プラグイン / スタンドアローンで直接再生・編集できる MML テキストに変換します。

MDX フォーマットの解析は [vampirefrog/mdxtools](https://github.com/vampirefrog/mdxtools) の `mdx.c` / `mdx_decompiler.c` 仕様に完全準拠しています。

## 動作環境

- Python 3.10 以上（型ヒント `X | Y` 構文を使用）
- 外部ライブラリ不要（標準ライブラリのみ）

## 使い方

```bash
# 基本: song.mdx → song.mml を同じフォルダに出力
python mdx2mml.py song.mdx

# 出力先を指定
python mdx2mml.py song.mdx -o output.mml

# 変換結果を標準出力にも表示
python mdx2mml.py song.mdx --dump
```

## 変換仕様

### MDX フォーマット (MXDRV2)

| 項目 | 値 |
|---|---|
| タイトル終端 | `\x0d\x0a\x1a` |
| オフセットテーブル | 17 エントリ固定 ([0]=音色, [1..16]=トラック) |
| 音色データ | 27 bytes/音色 (先頭バイト = voice_id) |
| レスト | `0x00`–`0x7f` (1byte), duration = byte+1 ticks |
| ノート | `0x80`–`0xdf` (2byte), note_num = byte-0x80 |
| ノート名起点 | D# (レ♯) = note 0 ← MDX独自仕様 |
| 全音符 tick 数 | 192 ticks (4分音符 = 48 ticks) |
| テンポ計算式 | `BPM = 78125 / (16 × (256 - timer_b))` |

### 主要コマンド対応表

| MDX コマンド | 内容 | MewMMLPad 出力 |
|---|---|---|
| `0xFF` | テンポ設定 | `T{BPM}` |
| `0xFD` | FM音色番号 | `@{num}` |
| `0xFC` | パン (1=右/2=左/3=両) | `PR63` / `PL63` / `PC` |
| `0xFB` | ボリューム | `V{0-127}` |
| `0xFA` / `0xF9` | ボリューム増減 | `)` / `(` |
| `0xF8` | ゲートタイム (Q) | `Q{1-8}` |
| `0xF7` | キーオフ無効 (レガート) | 次の音符に `&` |
| `0xF6` / `0xF5` | ループ開始/終了 | `[...]n` |
| `0xF4` | ループ脱出 | `; /` (コメント) |
| `0xF3` | デチューン | `; D{val}` (コメント) |
| `0xF2` | ポルタメント | `{note}{len}_{target}` で近似 |
| `0xF1` | トラック終端 | 変換終了 |

### MewMMLPad 非対応コマンド

以下は MewMMLPad に対応コマンドがないため、`;` コメント行として出力されます:
- `0xFE`: OPM レジスタ直書き (`y{reg},{val}`)
- `0xF0`: キーオンディレイ
- `0xEF`: Sync send
- `0xEE`: Sync wait
- `0xED`: ADPCM/ノイズ周波数
- `0xEA`–`0xEC`: LFO (OPM/Amplitude/Pitch)
- `0xE9`: LFO ディレイ

## 出力例

```
; ============================================================
; Title   : My X68000 Song
; PDX     : MYPCM
; Converted: MDX (MXDRV2) → MewMMLPad  [mdx2mml.py]
; ============================================================

; --- Channel A ---
A T120 O4 L8 V100 @0 O4 C4 D4 E4 F4 G4 A4 B4 > C4

; --- Channel B ---
B O4 L8 V100 @0 O3 [C4 E4 G4]4
```

## 既知の制限

- **LZX 圧縮 MDX 非対応**: DMDX などで解凍してから使用してください。
- **PCM/ADPCM チャンネル**: MewMMLPad に PCM トラックはないため、MDX の 9ch 以降は出力しません。
- **テンポ指定**: MewMMLPad で複数トラック同時に `T` を置くと不安定になるため、同じ tick の `T` は最初の 1 件だけ出力します。
- **トリプレット/特殊 tick**: MewMMLPad 仕様内の音価 (`1`〜`32` と付点) の組み合わせに近似されます。
- **OPM 音色**: FM 音色データは変換されません。MewMMLPad の内蔵シンセを使用してください。

## 参考資料

- [vampirefrog/mdxtools](https://github.com/vampirefrog/mdxtools) — MDX フォーマット実装参考
- [MDX フォーマット仕様 (atwiki)](http://www16.atwiki.jp/mxdrv/pages/23.html)
- [MewMMLPad](https://github.com/mewlist) — 出力先 MML プラグイン

## ライセンス

MIT License
