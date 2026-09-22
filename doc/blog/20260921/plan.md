# 9/21 本戦（12体・実験席混合）連載プラン差分（plan.md）

> 共通手法・定義は `doc/blog/pipeline.md` を参照。本ファイルはこの試合固有の差分のみを記す。

## 対象試合・出典

- 試合: `trial_C_l12r12_dr_1201`（`logs/llm/trial_C_l12r12_dr_1201/`）、seed=1201、12体×12ラウンド、S2ルール
- 事実の出典: `doc/analysis/l12r12_dr_1201_facts.md`（事実抽出）、`doc/analysis/l12r12_dr_1201_timeout.md`（504タイムアウト原因切り分け）
- 本試合は手記なし、**総括記事1本のみ**（他シリーズと異なり手記スロット表は作らない）
- サイクル10.16。エンジン変更なし。LLM API呼び出しなし。

## 記事1本

| # | 内容 | 選定理由 |
|---|---|---|
| 17 | 総括（`17_summary.md`） | 上位モデルを「実験席」として初めて混ぜた12体戦の唯一の記事。首位固定・型C急増・P11の504由来のROYAL_FLUSH温存を中心に構成 |

## 投稿状態（`scripts/publish_next.py` が読み書きする。以後この節が唯一の正）

| order | ファイル | 状態 | 公開URL |
|---|---|---|---|
| 17 | `posts/17_summary.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-17-summary） | https://pixblog.net/u/uso8m/s2-0921-17-summary |
