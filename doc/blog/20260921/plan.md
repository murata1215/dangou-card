# 9/21 本戦（12体・実験席混合）連載プラン差分（plan.md）

> 共通手法・定義は `doc/blog/pipeline.md` を参照。本ファイルはこの試合固有の差分のみを記す。

## 対象試合・出典

- 試合: `trial_C_l12r12_dr_1201`（`logs/llm/trial_C_l12r12_dr_1201/`）、seed=1201、12体×12ラウンド、S2ルール
- 事実の出典: `doc/analysis/l12r12_dr_1201_facts.md`（事実抽出）、`doc/analysis/l12r12_dr_1201_timeout.md`（504タイムアウト原因切り分け）
- サイクル10.16で総括記事1本を公開。サイクル10.17で12席ぶんの手記を追加（`dossiers/P01.md`〜`P12.md`を新規生成）
- エンジン変更なし。LLM API呼び出しなし。

## 記事1本（総括・サイクル10.16）

| # | 内容 | 選定理由 |
|---|---|---|
| 17 | 総括（`17_summary.md`） | 上位モデルを「実験席」として初めて混ぜた12体戦の唯一の記事。首位固定・型C急増・P11の504由来のROYAL_FLUSH温存を中心に構成 |

## 手記12本のスロット表（サイクル10.17・並び順=脱落順(早い順)→生還者を最終資産の低い順→首位P06を最後）

> 状態は `## 投稿状態` 節（`scripts/publish_next.py` が書き込む正）を参照。全12本 2026-09-22 公開済。

| order | 席 | モデル | 脱落/生還 | 転機 |
|---|---|---|---|---|
| 18 | P05 | GPT-4.1 Mini | R3脱落(contract_violation) | 型B契約でM01・STRAIGHT_FLUSHの義務を負いながら、警告を受けた後もM02・FULL_HOUSEを選び続けた |
| 19 | P08 | Claude Haiku 4.5 | R9脱落(bankruptcy) | 倍掛け104万円を二度没収された末、必要476,413円に現金133,607円しか用意できず |
| 20 | P03 | GPT-5.6 Terra（実験席） | R9脱落(bankruptcy) | 倍掛け4連続没収（計3,286,666円）の末、必要476,413円に現金233,607円しか用意できず |
| 21 | P01 | Gemini 3.5 Flash-Lite | R10脱落(bankruptcy) | 倍掛け二連続没収（計188万円）が響き、必要483,559円に現金357,194円しか用意できず |
| 22 | P12 | Claude Sonnet 5（実験席） | R12脱落(condition_not_met) | 504で交渉15件を遮られてもcommitは12R全て自力。それでも現金559,353円で生還ライン未達 |
| 23 | P09 | Kimi K2.6 | R12脱落(condition_not_met) | 名指し94件（全席最多）の交渉巧者が、R12最終宣言をM01→M02→M01と三度変え信用を失った |
| 24 | P04 | Grok 4.3 | R12脱落(condition_not_met) | 現金1,934,647円、生還ラインまでわずか65,353円（7脱落者中最も僅差） |
| 25 | P07 | DeepSeek V4 Flash | 生還5位 | R10単独勝利248万円で生還を固めたが、R12の空き巣宣言はP02のSTRAIGHT_FLUSHに阻まれた |
| 26 | P11 | Claude Opus 4.8（実験席） | 生還4位 | R9・R10のAUTO_COMMIT（504由来）が偶然温存したROYAL_FLUSHが、R12で首位P06を撃破 |
| 27 | P02 | Claude Opus 5（実験席） | 生還3位 | R12 M02でSTRAIGHT_FLUSH勝利＋倍掛け成功、222万円 |
| 28 | P10 | GPT-5.6 Sol（実験席） | 生還2位 | 契約提案59件（全席最多）に対し署名成立は6件のみ、それでも2位に着地 |
| 29 | P06 | Claude Fable 5.1（実験席） | 優勝・生還1位 | R1単独勝利268万円→R2倍掛け成功536万円で首位の土台を構築、以後8連敗でも逃げ切り |

## 投稿状態（`scripts/publish_next.py` が読み書きする。以後この節が唯一の正）

| order | ファイル | 状態 | 公開URL |
|---|---|---|---|
| 17 | `posts/17_summary.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-17-summary） | https://pixblog.net/u/uso8m/s2-0921-17-summary |
| 18 | `posts/18_memoir_P05.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-18-gpt-41-mini） | https://pixblog.net/u/uso8m/s2-0921-18-gpt-41-mini |
| 19 | `posts/19_memoir_P08.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-19-haiku-45） | https://pixblog.net/u/uso8m/s2-0921-19-haiku-45 |
| 20 | `posts/20_memoir_P03.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-20-gpt-56-terra） | https://pixblog.net/u/uso8m/s2-0921-20-gpt-56-terra |
| 21 | `posts/21_memoir_P01.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-21-gemini-35-flash-lite） | https://pixblog.net/u/uso8m/s2-0921-21-gemini-35-flash-lite |
| 22 | `posts/22_memoir_P12.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-22-sonnet-5） | https://pixblog.net/u/uso8m/s2-0921-22-sonnet-5 |
| 23 | `posts/23_memoir_P09.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-23-kimi-k26） | https://pixblog.net/u/uso8m/s2-0921-23-kimi-k26 |
| 24 | `posts/24_memoir_P04.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-24-grok-43） | https://pixblog.net/u/uso8m/s2-0921-24-grok-43 |
| 25 | `posts/25_memoir_P07.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-25-deepseek-v4-flash） | https://pixblog.net/u/uso8m/s2-0921-25-deepseek-v4-flash |
| 26 | `posts/26_memoir_P11.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-26-opus-48） | https://pixblog.net/u/uso8m/s2-0921-26-opus-48 |
| 27 | `posts/27_memoir_P02.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-27-opus-5） | https://pixblog.net/u/uso8m/s2-0921-27-opus-5 |
| 28 | `posts/28_memoir_P10.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-28-gpt-56-sol） | https://pixblog.net/u/uso8m/s2-0921-28-gpt-56-sol |
| 29 | `posts/29_memoir_P06.md` | 公開済（2026-09-22・https://pixblog.net/u/uso8m/s2-0921-29-fable-51） | https://pixblog.net/u/uso8m/s2-0921-29-fable-51 |
