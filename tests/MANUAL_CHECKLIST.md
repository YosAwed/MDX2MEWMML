# MewMMLPad 手動試聴チェックリスト

合成 fixture (`tests/fixtures/build_mdx.py`) および手元の実曲 MDX で、変換精度を確認する手順です。

## 事前準備

```bash
python mdx2mml.py song.mdx --report 2>report.txt
```

`report.txt` で次を確認します。

- `DESYNC: none`（または `--no-align-tracks` 使用時は意図した差分のみ）
- `max desync: 0`（既定の `--align-tracks` 有効時）

## チェック項目

| # | 確認箇所 | 手順 | 合格基準 |
|---|---|---|---|
| 1 | 冒頭 | MDX 先頭 4 小節を MewMMLPad / 元 MDX で比較 | 小節頭の音符配置が一致 |
| 2 | テンポ変更 | `T` コマンド付近 | テンポ変化タイミングが大きくずれない |
| 3 | ローカルリピート | `0xF6`/`0xF5` を含む曲 | リピート後のフレーズが 1 周分多くない/少なくない |
| 4 | グローバルループ | `0xF1` ループあり | ループ点以降が 2 回目以降も同じ位置から始まる |
| 5 | Sync | `0xEE`/`0xEF` を含む曲 | チャンネル間で拍が揃う（`--report` で DESYNC 0） |
| 6 | 終端 | 曲末 2 小節 | 全 FM チャンネルが同時に終わる |
| 7 | パン | 左右パン使用曲 | 左= `P0`、右= `P127`、中央= `P64` |
| 8 | ポルタメント | `--portamento` 指定時 | ベンド方向がおおむね一致 |

## 代表 synthetic fixture（自動テスト済み）

`tests/test_representative.py` が以下を生成・変換します。

- **basic**: 休符 + 単音 + テンポ
- **dual**: 2 トラック長不一致（align 後に一致）
- **sync**: Sync wait / send ペア
- **pcm**: 9ch 相当（仮想 `B` トラック）の E7/E8

## 実曲を追加する場合

1. 権利・サイズに問題ない MDX を `tests/fixtures/representative/` に置く
2. `python mdx2mml.py tests/fixtures/representative/foo.mdx --report` を実行
3. 上表 1–8 を試聴確認
