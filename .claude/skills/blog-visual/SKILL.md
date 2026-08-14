---
name: blog-visual
description: 嘘八百万—談合カード—のブログ/SNS/OGP用カード画像(PNG)を生成する。モデル紹介・タイトル・最終リザルト・名言・LIE DETECTED・運命の一手カードが必要なとき、または「カード画像を作って」「アイキャッチを出して」「◯◯の紹介カードが欲しい」と言われたときに使う。
---

# blog-visual — 談合カード ビジュアル生成

試合後ブログやSNS告知に使う**カード画像(PNG)**を、HTML/CSS を Chromium で
レンダリングして生成する。文字はプログラムがソースデータから正確に載せ、
キャラ絵は既存の emotion アセットを合成する。

## いつ使うか
- モデル紹介 / シリーズタイトル / 最終リザルト / 名言 / LIE DETECTED / 運命の一手 のカードが欲しいとき。
- ブログのアイキャッチ・OGP・X/YouTube サムネ用の画像が必要なとき（これらは PNG 必須）。

## 実行方法（scripts/blog.py --cards）
すべて `.devrelay-output/{game_tag}_card_*.png` に出力される。**フォアグラウンドで実行**する。

```bash
# 定番セット（モデル/タイトル/リザルト/名言）を PNG で
uv run python scripts/blog.py --cards --card all --card-player P08

# 全8体のモデルカード
uv run python scripts/blog.py --cards --card model --card-player all

# 単体
uv run python scripts/blog.py --cards --card title            # banner+square
uv run python scripts/blog.py --cards --card result           # 最終順位（可変高）
uv run python scripts/blog.py --cards --card quote  --card-player P02

# opt-in（PNG専用）
uv run python scripts/blog.py --cards --card lie --card-player P08 \
    --lie-actual "R12でM02に自らFLUSHを出し賞金を独占。"      # 実際は任意。無指定なら宛先/Rのみ
uv run python scripts/blog.py --cards --card showdown \
    --showdown "P08,P02,12,313333"                            # left,right,round,一人当たり額(円)

# 従来SVG（プレビュー/装飾用・後方互換）
uv run python scripts/blog.py --cards --card title --format svg
```

主なオプション: `--format {png,svg}`(既定png) / `--card {all,model,title,result,quote,lie,showdown}` /
`--card-player PXX|all` / `--card-emotion {anger,doubt,ease,joy,panic,sadness,smirk}`(未指定時: lie/showdown=smirk[ニヤリ/悪い顔], その他=ease。明示指定は全カードで最優先) /
`--quote-text "…"`(名言/嘘の引用を明示) / `--lie-actual "…"` / `--showdown "L,R,round,額"`。

## 生成後の必須チェック（毎回）
生成PNGを **Read で目視** し、次を確認する:
1. 日本語が化けない・はみ出さない・見切れない。
2. 数値がソースデータと一致（例: Kimi=Moonshot/kimi-k2.6/$0.95/$4.0、順位=free_cash基準）。
3. ファイルサイズが過大でない（モデルカードで概ね1〜1.5MB以下。emotion絵は data URI で縮小済み）。

## 厳守事項（`reference/brand.md` に詳細）
- **数値は必ずソースデータから**。自分で計算・推測・捏造しない（順位・価格・額・ラウンド）。
- SVG で長文の段組みをしない（折り返しはブラウザに任せる）。
- AI画像に文字を描かせない（キャラ絵は絵、文字はプログラム）。
- 事実の捏造をしない。特に `--lie-actual` は観測できた事実がある時だけ使う。

## 実装の所在
- CLI 分岐: `scripts/blog.py` の `run_cards`/`_run_cards_png`/`_run_cards_svg`
- レンダラ: `engine/blog/cards_html.py`（`CardRenderer` = Chromium を1回だけ起動）
- テンプレ: `engine/blog/templates/*.html` + `base.css`
- パレット/キャッチ/価格ロジック: `engine/blog/cards_svg.py`（PNG/SVG 共用）
