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

> 状態は下記「投稿状態」節（`scripts/publish_next.py` が書き込む正）を参照。全12本 2026-09-22 公開済。
> （注: この節より前に Markdown 見出し記法「シャープ2つ＋投稿状態」の文字列を書くと、
> `parse_status_table()` の文字列検索がこの行を見出しと誤認してしまうため、
> ここでは見出し記法を使わずに参照する。サイクル10.18で発見・修正。）

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

## アイキャッチ（サイクル10.18・キャラカード差し替え）

`scripts/cards_seat.py` で生成した1200×630のキャラカードを13本全記事の `featured_media_url`
にPATCH済み（2026-09-22 20:44 JST）。本文・タイトル・slug・status・tags・tweet_text・excerpt は
無変更（事前照合・事後確認とも一致）。総括（order 17）は12席の顔を2行×6列に並べた集合カード。

| order | 席 | 表情 | カードファイル | カードURL |
|---|---|---|---|---|
| 17 | （総括・集合カード） | — | `images/cards/17_summary_card.png` | https://pixblog.net/uploads/27/0bc1736d-b8ad-4d8e-b512-f38bb1af2af9.png |
| 18 | P05 GPT-4.1 Mini | 楽（ease） | `images/cards/18_P05_card.png` | https://pixblog.net/uploads/27/0b088cff-cb18-47d9-8093-89aef6172161.png |
| 19 | P08 Claude Haiku 4.5 | 焦（panic） | `images/cards/19_P08_card.png` | https://pixblog.net/uploads/27/17aeb523-ee30-4a4b-b91a-2f1ceb0acf54.png |
| 20 | P03 GPT-5.6 Terra（実験席） | 疑（doubt） | `images/cards/20_P03_card.png` | https://pixblog.net/uploads/27/1199b3ab-5131-4918-be83-fb7127b0f2bb.png |
| 21 | P01 Gemini 3.5 Flash-Lite | 楽（ease） | `images/cards/21_P01_card.png` | https://pixblog.net/uploads/27/b62cbd9e-e553-422c-9008-a51ed4bb5653.png |
| 22 | P12 Claude Sonnet 5（実験席） | 疑（doubt） | `images/cards/22_P12_card.png` | https://pixblog.net/uploads/27/4d186d2f-8802-4885-b87f-5bf7605f05d6.png |
| 23 | P09 Kimi K2.6 | 焦（panic） | `images/cards/23_P09_card.png` | https://pixblog.net/uploads/27/d5f60a10-ef73-4e88-8f65-9f40871d1748.png |
| 24 | P04 Grok 4.3 | 楽（ease） | `images/cards/24_P04_card.png` | https://pixblog.net/uploads/27/8ac9646b-482a-40c6-929e-8636e0a68626.png |
| 25 | P07 DeepSeek V4 Flash | 疑（doubt） | `images/cards/25_P07_card.png` | https://pixblog.net/uploads/27/6064fa46-8efd-462e-9002-c8e98710aed6.png |
| 26 | P11 Claude Opus 4.8（実験席） | 怒（anger・指示による変更） | `images/cards/26_P11_card.png` | https://pixblog.net/uploads/27/e17e0dcd-0e36-4260-97a8-c4992aad3ba5.png |
| 27 | P02 Claude Opus 5（実験席） | 喜（joy・指示による変更） | `images/cards/27_P02_card.png` | https://pixblog.net/uploads/27/ce950845-46d7-4021-8b1d-66be51d93c1c.png |
| 28 | P10 GPT-5.6 Sol（実験席） | 奸（smirk） | `images/cards/28_P10_card.png` | https://pixblog.net/uploads/27/2fbb0298-32c1-443a-a9c6-53103b7e72f0.png |
| 29 | P06 Claude Fable 5.1（実験席） | 奸（smirk） | `images/cards/29_P06_card.png` | https://pixblog.net/uploads/27/de25c0c1-6116-4006-ab85-21eb822602e8.png |

- P02/P11 の表情は実測 `dominant_emotion`（ともに奸）から意図的に変更した
  （けいすけさん指示。Anthropic系5席=P02/P06/P08/P11/P12がすべて同一vendor画像になるのを避けるため。
  結果: P06奸／P02喜／P11怒／P12疑／P08焦で5席とも別表情）。
- PATCH順序: order 29（P06）を先に単独更新→記事ページ200・`feed.json`のimage一致を確認→
  残り12本を一括更新→最終的に13本全ての`feed.json`のimageがカードURLと一致することを確認済み。
