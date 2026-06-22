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

# 変換精度レポート (tick 長・DESYNC・非対応コマンド)
python mdx2mml.py song.mdx --report

# テンポ BPM 表示のみ半分、音符名大文字、全体ループ3回
python mdx2mml.py song.mdx --tempo-scale 0.5 --note-case upper --global-loop-count 3
```

## テスト

```bash
python -m pytest tests/ -v
```

合成 MDX fixture は `tests/fixtures/build_mdx.py`、手動試聴手順は `tests/MANUAL_CHECKLIST.md` を参照してください。

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
| `0xFC` | パン (1=左/2=右/3=両) | `P0` / `P127` / `P64` |
| `0xFB` | ボリューム | `V{0-127}` |
| `0xFA` / `0xF9` | ボリューム増減 | `)` / `(` |
| `0xF8` | ゲートタイム (Q) | `Q{1-8}` |
| `0xF7` | キーオフ無効 (レガート) | 次の音符に `&` |
| `0xF6` / `0xF5` | ループ開始/終了 | ループを展開 |
| `0xF4` | ループ脱出 | ループを展開 |
| `0xF3` | デチューン | `; D{val}` (コメント) |
| `0xF2` | ポルタメント | 既定では音価のみ近似、`--portamento` で `{source}{len}_{target}` |
| `0xF0` | キーオンディレイ | 次音符直前に `; k{n}` (タイムライン tick は不変) |
| `0xEF` | Sync send | `; S{ch}` (コメント) |
| `0xEE` | Sync wait | 内部処理後、他 ch に合わせて休符パディング |
| `0xE8` | PCM enable | PCM トラックで `; PCM enable` |
| `0xE7` | Extended MML | `; E7 sub,val` (コメント) |
| `0xF1` | トラック終端 | 変換終了 |

### MewMMLPad 非対応コマンド

以下は MewMMLPad に対応コマンドがないため、`;` コメント行または内部タイミング調整として扱います:

- `0xFE`: OPM レジスタ直書き (`y{reg},{val}`)
- `0xF0`: キーオンディレイ (`k{n}` コメント)
- `0xEF`: Sync send (`S{ch}` コメント)
- `0xEE`: Sync wait (休符パディングに反映)
- `0xED`: ADPCM/ノイズ周波数
- `0xEA`–`0xEC`: LFO (OPM/Amplitude/Pitch)
- `0xE9`: LFO ディレイ
- `0xE8`: PCM enable (`PCM enable` コメント)
- `0xE7`: Extended MML (`E7 sub,val` コメント)

### OPM ドラム/パーカッション推定

FM トラックのうち、短音が多い・音色切替が多い・少数音程の反復が多いなどの条件に合うものは、OPM ドラム/パーカッションの可能性としてチャンネル見出し下に `; 推定: OPMドラム/パーカッションの可能性 ...` コメントを出力します。MDX には確定フラグがないため、この判定はヒューリスティックです。

### 変換オプション

- `--tempo-scale 0.5|1|2`: 出力する **BPM 数値のみ** を 1/2 倍・等倍・2 倍に補正します。音価 tick は変更しません。
- `--note-case lower|upper`: 音符名を小文字または大文字で出力します。既定は `lower` です。
- `--volume-command V|v|@V`: MDX のボリュームを出力するコマンドを選びます。既定は MewMMLPad 仕様にある `V` です。
- `--global-loop-count N`: グローバルループ点以降を合計 `N` 回ぶん再生するように展開します。既定は `2` です。
- `--no-align-tracks`: チャンネル間 tick 揃え (休符パディング) を無効化します。既定では有効です。
- `--report`: 変換精度レポート (チャンネル tick 長・DESYNC・非対応コマンド) を stderr に出力します。
- `--no-pcm`: 9ch 以降の PCM/ADPCM トラックを仮想 `I`-`P` チャンネルとして出力しません。
- `--portamento`: MDX のポルタメント (`0xF2`) を MewMMLPad の即ベンド構文 (`a4_b`) に変換します。

## 出力例

```
; ============================================================
; Title   : My X68000 Song
; PDX     : MYPCM
; Converted: MDX (MXDRV2) → MewMMLPad  [mdx2mml.py]
; ============================================================

; --- Channel A ---
A T120 O4 L8 V100 @0 O4 c4 d4 e4 f4 g4 a4 b4 > c4

; --- Channel B ---
B O4 L8 V100 @0 O3 c4 e4 g4 c4 e4 g4
```

## 既知の制限

- **LZX 圧縮 MDX 非対応**: DMDX などで解凍してから使用してください。
- **PCM/ADPCM チャンネル**: MDX の 9ch 以降は仮想 `I`-`P` チャンネルとしてノート変換します。MewMMLPad の有効トラックは `A`-`H` のため、この仮想チャンネルは再生対象外です。PDX サンプル自体は変換しません。
- **テンポ指定**: MewMMLPad で複数トラック同時に `T` を置くと不安定になるため、同じ tick の `T` は最初の 1 件だけ出力します。`--tempo-scale` は BPM 表示のみ変更し、音価長は変えません。
- **特殊 tick**: MDX の任意 tick 長に近づけるため、必要に応じて `12`、`24`、`48`、`192` などの数値音長やタイ分解を使います。分解誤差は `--report` に表示されます。
- **リピート/全体ループ**: ローカルリピート (`0xF6`/`0xF5`/`0xF4`) はバイトコードとして展開します。グローバルループ点以降は `--global-loop-count` 回ぶん展開します。既定で FM チャンネル間 tick を休符で揃え、`--no-align-tracks` で無効化できます。
- **Sync (`0xEE`/`0xEF`)**: wait 位置で他チャンネルに合わせて休符を挿入します。send 自体はコメント出力です。
- **キーオンディレイ (`0xF0`)**: 発音開始のみ遅延し、MDX タイムライン tick は変えません。次音符前に `; k{n}` を付けます。
- **OPM ドラム/パーカッション推定**: FM トラックの演奏パターンからの推定であり、誤検出/未検出の可能性があります。
- **OPM 音色**: FM 音色データは変換されません。MewMMLPad の内蔵シンセを使用してください。

## 参考資料

- [vampirefrog/mdxtools](https://github.com/vampirefrog/mdxtools) — MDX フォーマット実装参考
- [MDX フォーマット仕様 (atwiki)](http://www16.atwiki.jp/mxdrv/pages/23.html)
- [MewMMLPad](https://github.com/mewlist) — 出力先 MML プラグイン

## ライセンス

MIT License
