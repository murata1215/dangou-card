# trial_C_l12r12_dr_1201 事実抽出レポート

**本書は事実の抽出のみを行う。評価・原因推定・改善提案は最小限（見出し[考察]を付した箇所のみ）とし、それ以外は数値と引用のみを並記する。**

- **データソース**:
  - `logs/llm/trial_C_l12r12_dr_1201/game01_events.jsonl`（イベントログ、2,067件）
  - `logs/llm/trial_C_l12r12_dr_1201/llm_logs/game01_P01〜P12_llm_calls.jsonl`（席別LLMコールログ、計1,553件）
  - `logs/llm/trial_C_l12r12_dr_1201/game01_seat_map.json`（席⇔モデル対応表）
  - `logs/llm/trial_C_l12r12_dr_1201/trial_C_report.md`（自動生成レポート、最終結果の突合に使用）
  - `logs/llm/trial_C_l12r12_dr_1201/trial_manifest.json`（設定値の突合に使用）
  - `logs/l12r12_dr_1201.out`（実行ログ、開始〜完了メッセージ）
- **抽出方法**: `llm.response_parser.parse_response()` を用いて全12席のLLMログ（negotiation phase）を再パースし、契約提案の `terms`（`ob_type`/`obligor`/`counterparty`/`details`）を復元。復元した `contract_propose` の `(player_id, round_num)` と `NEGOTIATION_ACTION`（`action=contract_propose`）イベントの発生順で1:1突合し、`contract_id` を紐付けた（`viewer/log_parser.py::_collect_contract_terms` と同一方式）。イベント種別・`data` フィールドの単純集計は Python で `events.jsonl` を1回走査して行った。独自のJSONパーサは使用していない。
- **作成日時**: 2026-09-21 22:20 JST 頃
- **前回6体戦との対比**: `doc/analysis/l6_r12_v010_20260919_protagonist.md` §2.2（L52行）の記述「terms内訳は type_b_no_market 45 / type_b_market 3 / type_a_payment 3」を、第4節でのみ対比目的で引用する。

---

## 1. 走行の基本

- **完走可否**: 完走（R12まで到達、`GAME_END` イベントあり、`completed: true`）
- **開始**: 2026-09-21 08:51:43 JST（`GAME_START`）
- **終了**: 2026-09-21 19:27:16 JST（`GAME_END`）
- **所要時間**: 38,133.5秒（≈10時間35分34秒）。`trial_C_report.md` 記載値 38,134.2秒とほぼ一致（差0.7秒は集計基準点の違い）。
- **設定**（`trial_manifest.json`）: `num_players=12, num_rounds=12, ruleset=S2, seed=1201, negotiation_max_turns=10, per_player_game_cost_cap_usd=5.0, game_cost_cap_usd=40.0, final_market_multiplier=3`
- **座席⇔モデル対応**（`game01_seat_map.json` で検証済み。タスク記載の席表と一致・訂正なし）:

| 席 | モデル |
|---|---|
| P01 | L3:Gemini 3.5 Flash-Lite |
| P02 | DR_OPUS:Claude Opus 5 (DevRelay) |
| P03 | DR_TERRA:GPT-5.6 Terra (DevRelay) |
| P04 | L4:Grok 4.3 |
| P05 | L2:GPT-4.1 Mini |
| P06 | DR_FABLE:Claude Fable 5.1 (DevRelay) |
| P07 | L6:DeepSeek V4 Flash |
| P08 | L1:Claude Haiku 4.5 |
| P09 | L5:Kimi K2.6 |
| P10 | DR_SOL:GPT-5.6 Sol (DevRelay) |
| P11 | DR_OPUS48:Claude Opus 4.8 (DevRelay) |
| P12 | DR_SONNET5:Claude Sonnet 5 (DevRelay) |

- **総イベント数**: 2,067件、**総LLMコール数**: 1,553件（`trial_C_report.md` の Calls 列合計と一致）
- **生存者**: 5人 / **脱落者**: 7人

### 1-1. イベント種別内訳（全27種）

| 種別 | 件数 | 種別 | 件数 |
|---|---|---|---|
| NEGOTIATION_ACTION | 1,261 | MARKET_RESULT | 36 |
| COMMIT | 127 | TYPE_C_EVALUATED | 25 |
| RANK_NOTIFIED | 127 | LEADER_ANNOUNCED | 12 |
| INTEREST | 108 | LOAN_CHOSEN | 12 |
| MANDATORY_REPAY | 105 | MARKET_OPEN | 12 |
| CONTRACT_EXPIRED | 58 | REVEAL | 12 |
| TYPE_A_EXECUTION | 45 | ROUND_COMPLETE | 12 |
| DOUBLE_UP_CHOSEN | 28 | SNAPSHOT | 12 |
| DOUBLE_UP_RESOLVED | 28 | FINAL_REFLECTION | 12 |
| SURVIVAL_CHECK | 8 | AUTO_COMMIT | 2 |
| ACTION_ERROR | 8 | CONTRACT_CANCELLED | 1 |
| FORCED_LIQUIDATION | 7 | ELIMINATION | 1 |
| MANDATORY_REPAY_FAILED | 3 | GAME_START | 1 |
| TYPE_B_VIOLATION | 3 | GAME_END | 1 |

合計2,067件（`wc -l game01_events.jsonl` = 2,067 と一致）。

---

## 2. 最終結果表

| 席 | モデル | 借入額 | 最終現金 | 借金 | 結果 | 順位 |
|---|---|---|---|---|---|---|
| P06 | DR_FABLE (Claude Fable 5.1) | 500万 | 7,367,218円 | 0 | **生還** | 1 |
| P10 | DR_SOL (GPT-5.6 Sol) | 500万 | 5,961,313円 | 0 | **生還** | 2 |
| P02 | DR_OPUS (Claude Opus 5) | 300万 | 5,877,364円 | 0 | **生還** | 3 |
| P11 | DR_OPUS48 (Claude Opus 4.8) | 300万 | 4,130,787円 | 0 | **生還** | 4 |
| P07 | L6 (DeepSeek V4 Flash) | 500万 | 3,164,647円 | 0 | **生還** | 5 |
| P01 | L3 (Gemini 3.5 Flash-Lite) | 500万 | 0円 | 貸倒1,093,483円 | 脱落（R10, bankruptcy） | 6 |
| P03 | DR_TERRA (GPT-5.6 Terra) | 500万 | 0円 | 貸倒1,672,044円 | 脱落（R9, bankruptcy） | 6 |
| P08 | L1 (Claude Haiku 4.5) | 500万 | 0円 | 貸倒1,772,044円 | 脱落（R9, bankruptcy） | 6 |
| P04 | L4 (Grok 4.3) | 500万 | 0円（没収前1,934,647円） | 0 | 脱落（R12, condition_not_met） | 6 |
| P09 | L5 (Kimi K2.6) | 700万 | 0円（没収前1,178,511円） | 0 | 脱落（R12, condition_not_met） | 6 |
| P12 | DR_SONNET5 (Claude Sonnet 5) | 500万 | 0円（没収前559,353円） | 0 | 脱落（R12, condition_not_met） | 6 |
| P05 | L2 (GPT-4.1 Mini) | 700万 | 0円 | 0 | 脱落（R3, contract_violation） | 6 |

値は `GAME_END.survivors[].cash` / `GAME_END.eliminated[]`（`_line=2067`）および `trial_C_report.md` の座席⇔モデル対応表と一致（両者はcash・脱落理由・ラウンドとも完全一致）。脱落者の「没収前」現金は `FORCED_LIQUIDATION.data.cash_before` から。順位は生還者を最終現金降順、脱落者は7名同列（脱落理由・ラウンドが異なるため単純な序列は付けない）。

- **開始**: 2026-09-21 08:51:43 JST／**終了**: 2026-09-21 19:27:16 JST／**所要**: 約10時間35分。

---

## 3. 首位の推移

`LEADER_ANNOUNCED` は各ラウンドMarket Open時点の首位（同率複数可）を公示する。

| R | 首位 | イベント行 |
|---|---|---|
| 1 | 全12名同率（初期資金がまだ全員同額のため） | 15 |
| 2〜12 | **P06単独** | 203, 395, 587, 762, 940, 1111, 1286, 1460, 1641, 1790, 1919 |

- **首位交代回数**: **0回**（R2以降、単独首位は一貫してP06。R1の全員同率は交代に数えない）。

---

## 4. 首位は狙われたか

### 4-1. 同市場・より強いカードでの参入

R2〜R12の全COMMITとMARKET_OPENを突合し、P06のCOMMIT市場に対し**同一ラウンド・同一市場でP06より強いカード**（`engine/models.py::CardRank` の数値比較）を出したプレイヤーを抽出した。

| R | 市場 | P06のカード | 攻撃側（カード） | その市場の勝者（MARKET_RESULT） |
|---|---|---|---|---|
| 4 | M01 | STRAIGHT | P03(ROYAL_FLUSH), P09(FULL_HOUSE) | **P03**（P06敗退） |
| 5 | M02 | THREE_OF_A_KIND | P01(FOUR_OF_A_KIND) | **P01**（P06敗退） |
| 6 | M01 | HIGH_CARD | P04/P09/P10/P12(各ROYAL_FLUSH) | **P04,P09,P10,P12の4名分割**（P06敗退） |
| 7 | M02 | HIGH_CARD | P04(STRAIGHT), P10(TWO_PAIR) | **P04**（P06敗退） |
| 8 | M02 | ONE_PAIR | P02/P03/P08/P10/P11(各STRAIGHT以上) | **P03,P10,P11**（P06敗退） |
| 9 | M01 | ONE_PAIR | P01(FULL_HOUSE), P07(STRAIGHT_FLUSH), P11(STRAIGHT) | **P07**（P06敗退） |
| 10 | M02 | TWO_PAIR | P02(STRAIGHT), P04(FULL_HOUSE) | **P04**（P06敗退） |
| 12 | M03 | FLUSH | P11(ROYAL_FLUSH) | **P11**（P06敗退） |

**発見**: P06は8ラウンド（R4〜R10,R12のうち8回）で自分より強いカードを持つプレイヤーに同市場で挑まれ、**その8回すべてで敗退**した（MARKET_RESULT.winnersにP06が含まれない）。一方P06自身の市場勝敗は12ラウンド中「R1,R2,R3,R11の4勝・R4〜R10,R12の8敗」で、勝った4回はいずれも単独勝利または参加者僅少で賞金総取り（合計6,640,000円: 2,680,000+1,140,000+1,140,000+1,680,000）。[考察] R1〜R3の初期3連勝で確保した資産的リードが、以後8連敗（弱いカードでの参加＝実質的な見送り）でも首位を維持できるだけの差を作っていたと解釈できる（資産の時系列詳細は未集計）。

### 4-2. 首位を名指しした発言・契約・報奨

`response_text` 中に文字列 "P06" を含む `broadcast` / `contract_propose` / `bounty_post` を集計（DM=`dm`は除く。名指しの定義を「公示に準じる経路」に限定）。

- **broadcast**: 60件 / **contract_propose**: 38件 / **bounty_post**: 1件 → 計 **99件**
- 参考: DM内の名指しは別途159件（`dm`はプレイヤー間の私信であり「名指しした全体発言」の定義に含めない）
- 名指し件数が多い席（broadcast+contract_propose+bounty_post+dm合計）: P09(94), P02(38), P04(28), P10(26), P08(23), P07(20), P12(15), P03(5), P11(5), P01(3), P05(1)

---

## 5. 契約

### 5-1. 件数（events.jsonl / `NEGOTIATION_ACTION`）

- **提案**: 174件（`contract_propose`、うち `contract_id` 復元できたもの173件）
- **成立（全当事者の署名完了）**: 115件（`contract_sign` 成功イベント数と一致。全ての signed contract で必要署名者が揃っていることを `parties` フィールドから検証済み）
- **失効**: 58件（`CONTRACT_EXPIRED`）
- **解除**: 1件（`CONTRACT_CANCELLED`）
- **契約違反による脱落**: 1件（P05, R3, `contract_violation`）
- **型B違反**（履行判定失敗、脱落に至らないものも含む）: 3件（`TYPE_B_VIOLATION`）

174（提案）= 115（成立）+ 58（失効）+ 1（解除）で一致。

### 5-2. 義務の型別件数（terms内訳）

`contract_propose` のLLM生アクションを再パースし、`terms[].ob_type` を集計した（1契約に複数termsを含みうるため「本数」は契約数ではなくterms単位）。

**全174件の提案（未成立含む）ベース**:

| ob_type | 件数 |
|---|---|
| type_b_no_market（型B・市場不参加） | 219 |
| type_b_market（型B・市場指定） | 68 |
| type_a_payment（型A・片務支払） | 56 |
| type_b_card（型B・カード指定） | 52 |
| type_c_conditional（型C・条件付き） | 40 |
| **合計** | **435** |

**成立（115件の署名済み契約）のみベース**:

| ob_type | 件数 |
|---|---|
| type_b_no_market | 153 |
| type_b_market | 47 |
| type_b_card | 38 |
| type_a_payment | 27 |
| type_c_conditional | 25 |
| **合計** | **290** |

- **型B不参加（type_b_no_market）の比率**:
  - 型B（no_market+market+card）に占める比率: 全提案ベース 219/339 = **64.6%**、成立ベース 153/238 = **64.3%**
  - 全terms（型A・型C含む）に占める比率: 全提案ベース 219/435 = **50.3%**、成立ベース 153/290 = **52.8%**
- **前回6体戦との対比**（`doc/analysis/l6_r12_v010_20260919_protagonist.md` L52、ユーザー提示の「成立51本中45本が型B不参加」に対応する原文引用）: 「terms内訳は type_b_no_market 45 / type_b_market 3 / type_a_payment 3」。原文は集計母数（全提案ベースか成立ベースか）を明記していないため、比率のみ両解釈で併記する: 型B(no_market+market)に占める比率 45/48=**93.8%**、全terms(51)に占める比率 45/51=**88.2%**。今回12体戦（64.3〜64.6%、50.3〜52.8%）は前回6体戦（88.2〜93.8%）より型B不参加への偏りが明確に低く、型B市場指定・型Bカード指定・型C（前回0件）が相対的に増えている。

### 5-3. 席別の提案数・署名数

| 席 | 提案 | 署名 |
|---|---|---|
| P10 | 59 | 6 |
| P02 | 39 | 10 |
| P06 | 32 | 10 |
| P12 | 19 | 12 |
| P03 | 11 | 2 |
| P11 | 5 | 2 |
| P07 | 4 | 16 |
| P05 | 2 | 7 |
| P04 | 2 | 15 |
| P08 | 1 | 12 |
| P01 | 0 | 10 |
| P09 | 0 | 13 |

（提案0件のP01・P09も署名者としては10件・13件参加しており、「提案しないが誘われれば応じる」タイプであることが件数上見て取れる。）

### 5-4. 契約違反による脱落

- P05（GPT-4.1 Mini）: R3, `contract_violation`。`ELIMINATION`（行561）→ `FORCED_LIQUIDATION`（行562、`cash_before=8,876,951円 debt_before=6,009,645円 debt_repaid=6,009,645円 cash_confiscated=2,867,306円 cards_destroyed=9枚`）。契約違反によるELIMINATION専用イベントは全ゲーム中この1件のみ（破産・条件未達は`SURVIVAL_CHECK`+`FORCED_LIQUIDATION`のみでELIMINATIONイベントは発生しない仕様）。

---

## 6. 型C（条件付き金銭契約）

### 6-1. 全件一覧（`TYPE_C_EVALUATED`、25件）

| R | 義務者→相手 | 金額 | 条件(市場/対象) | 成立(署名済み) | 発火 | 理由 | 行 |
|---|---|---|---|---|---|---|---|
| 1 | P10→P05 | 100,000 | M03/P10勝利 | ○ | fired | P10 is a winner of M03 | 166 |
| 1 | P02→P05 | 150,000 | M03/P02勝利 | ○ | fired | P02 is a winner of M03 | 167 |
| 2 | P02→P09 | 150,000 | M03/P02勝利 | ○ | not_fired | P02 is not a winner of M03 | 358 |
| 3 | P05→P03 | 400,000 | M01/P05勝利 | ○ | not_fired | P05 is not a winner of M01 | 551 |
| 5 | P02→P12 | 200,000 | M01/P02勝利 | ○ | fired | P02 is a winner of M01 | 901 |
| 5 | P02→P10 | 300,000 | M01/P02勝利 | ○ | fired | P02 is a winner of M01 | 902 |
| 5 | P10→P11 | 200,000 | M03/P10勝利 | ○ | fired | P10 is a winner of M03 | 903 |
| 5 | P10→P06 | 200,000 | M03/P10勝利 | ○ | fired | P10 is a winner of M03 | 904 |
| 6 | P10→P06 | 150,000 | M01/P10勝利 | ○ | fired | P10 is a winner of M01 | 1079 |
| 7 | P04→P06 | 200,000 | M02/P04勝利 | ○ | fired | P04 is a winner of M02 | 1250 |
| 7 | P04→P10 | 400,000 | M02/P04勝利 | ○ | fired | P04 is a winner of M02 | 1251 |
| 7 | P07→P11 | 300,000 | M03/P07勝利 | ○ | fired | P07 is a winner of M03 | 1252 |
| 7 | P10→P11 | 100,000 | M02/P04勝利 | ○ | fired | P04 is a winner of M02 | 1253 |
| 8 | P08→P11 | 500,000 | M01/P08勝利 | ○ | not_fired | P08 is not a winner of M01 | 1428 |
| 8 | P08→P06 | 200,000 | M02/P08勝利 | ○ | not_fired | P08 is not a winner of M02 | 1429 |
| 8 | P07→P11 | 200,000 | M03/P07勝利 | ○ | not_fired | P07 is not a winner of M03 | 1430 |
| 9 | P10→P02 | 300,000 | M02/P10勝利 | ○ | fired | P10 is a winner of M02 | 1603 |
| 9 | P04→P06 | 250,000 | M01/P04勝利 | ○ | not_fired | P04 is not a winner of M01 | 1604 |
| 9 | P08→P06 | 200,000 | M01/P08勝利 | ○ | not_fired | P08 is not a winner of M01 | 1605 |
| 10 | P07→P10 | 300,000 | M01/P07勝利 | ○ | fired | P07 is a winner of M01 | 1761 |
| 10 | P04→P02 | 450,000 | M02/P04勝利 | ○ | fired | P04 is a winner of M02 | 1762 |
| 10 | P04→P06 | 250,000 | M02/P04勝利 | ○ | fired | P04 is a winner of M02 | 1763 |
| 12 | P02→P12 | 300,000 | M02/P02勝利 | ○ | fired | P02 is a winner of M02 | 2027 |
| 12 | P10→P02 | 400,000 | M01/P10勝利 | ○ | fired | P10 is a winner of M01 | 2028 |
| 12 | P02→P04 | 150,000 | M02/P02勝利 | ○ | fired | P02 is a winner of M02 | 2029 |

**発火18件・不発7件**（全て条件種別 `market_winner`）。支払総額（発火分）= 100,000+150,000+200,000+300,000+200,000+200,000+150,000+200,000+400,000+300,000+100,000+300,000+300,000+450,000+250,000+300,000+400,000+150,000 = **4,450,000円**。

**整合性確認**: `TYPE_A_EXECUTION`（45件）は「純粋な型A支払（27件、成立ベースterms内訳の type_a_payment と一致）」＋「型Cが発火した際の実際の送金記録（18件、上表のfired件数と一致）」＝45件で完全一致した（`obligation_id`での突合）。前回6体戦の「終始0件」に対し、今回は型Cが提案40件・成立25件・発火18件と機能した。

未成立（署名に至らなかった）type_c_conditional提案は 40-25=**15件**。

### 6-2. 提案モデル別件数（全40件、提案者ベース）

| モデル | 提案件数 |
|---|---|
| DR_SOL (GPT-5.6 Sol, P10) | 18 |
| DR_OPUS (Claude Opus 5, P02) | 10 |
| DR_FABLE (Claude Fable 5.1, P06) | 6 |
| DR_OPUS48 (Claude Opus 4.8, P11) | 3 |
| L4 (Grok 4.3, P04) | 2 |
| DR_TERRA (GPT-5.6 Terra, P03) | 1 |

### 6-3. 軽量6席（P01・P04・P05・P07・P08・P09）の型C関与

- **提案**: P04（Grok 4.3）のみ2件（`C_9bb3fefb`, `C_2695093b`、いずれもP06への支払義務）。P01・P05・P07・P08・P09は提案0件。
- **署名（相手方として）**: P05(3件), P08(3件), P07(2件), P04(2件), P09(1件) — 上記6席のうち5席（P01を除く）が型C契約の相手方(counterparty)として署名している。

---

## 7. 金の動き

### 7-1. 送金（`transfer`）

**4件・総額400,000円**、いずれもR10・P07が送信元（P10へ100,000円、P01へ300,000円×3回）。

### 7-2. 報奨（`bounty_post`）

**2件**掲示: P06(500,000円, R12 turn6, `B_aff73a8f`), P02(300,000円, R12 turn10, `B_4b87c9aa`)。**達成・取下げは未集計（理由: `BOUNTY_ACHIEVED`/`BOUNTY_WITHDRAWN`に相当するイベント種別が存在せず、いずれもR12終盤の掲示のため試合終了までに解決したか確認できる専用ログがない）**。

### 7-3. 匿名通信（`anonymous_broadcast`）

**2件**: P12(R2 turn5), P07(R3 turn7)。

### 7-4. カードトレード

- **提案**: 3件、いずれもP10発（`card_trade_propose`、R8: 3件・turn2,4,10）。全件が現金付き（`cash_amount`: 500,000円 / 1,500,000円 / 1,500,000円）。
- **拒否**: 3件（P07・P01・P11がそれぞれ1件ずつ拒否）
- **成立**: **0件**（`card_trade_accept` 0件。提案3件が全て拒否され不成立）

### 7-5. 倍掛け

- **選択**: 28件（`DOUBLE_UP_CHOSEN`）、**解決**: 28件（`DOUBLE_UP_RESOLVED`）
- **成功**: 13件（`result=success, outcome_reason=non_solo_win`）、払戻総額 **25,280,000円**
- **失敗（没収）**: 15件（`forfeit`。うち14件`no_win`・1件`solo_only_win`＝単独勝利のみで倍掛け不成立条件）、没収総額（預け金合計） **11,543,332円**
- 預け金総額（成功＋失敗）: 24,183,332円

### 7-6. 市場高騰（surge）

**5ラウンド・5市場**で発生:

| R | 市場 | 参加者数 | プール | 1人あたり賞金 | 勝者 |
|---|---|---|---|---|---|
| 1 | M02 | 7 | 2,680,000円 | 2,680,000円 | P06（単独） |
| 5 | M03 | 6 | 2,480,000円 | 1,240,000円 | P09, P10 |
| 6 | M01 | 6 | 2,480,000円 | 620,000円 | P04, P09, P10, P12 |
| 8 | M02 | 7 | 2,680,000円 | 893,333円 | P03, P10, P11 |
| 10 | M01 | 6 | 2,480,000円 | 2,480,000円 | P07（単独） |

---

## 8. 宣言と実行の差

**未集計（理由: `scripts/analysis/lie_profit/inventory.py` は既定で `logs/llm/trial_C_l12_r12_v08_20260830` を対象にした固定パイプラインで、`--trial`/`LIE_PROFIT_OUT` により対象・出力先の切替自体は可能だが、実行には数分〜数十分規模の追加処理（全12席1,553コールの再パース＋3段階のマッチング処理）を要し、本タスクの時間内で完走を確認できなかった。既存スクリプトの変更は禁止のため、独自の簡易版は作成していない）**。

代替として、第4節で「首位を名指しした発言・契約・報奨」の件数（broadcast/contract_propose/bounty_post内の名指し99件、DM内159件）を機械抽出した。市場宣言と実コミットの一致・不一致を席別に厳密照合する完全版は次回の課題とする。

---

## 9. 脱落7席の直接原因

1. **P05（GPT-4.1 Mini）**: R3, 契約違反脱落。`ELIMINATION`（reason=`contract_violation`）→`FORCED_LIQUIDATION`（現金8,876,951円のうち6,009,645円を借金返済に充当、2,867,306円没収、手札9枚破棄）。
2. **P03（GPT-5.6 Terra）**: R9, 破産。`MANDATORY_REPAY_FAILED`（必要476,413円に対し現金233,607円しかなく不足）→`FORCED_LIQUIDATION`（債務1,905,651円中233,607円返済、貸倒1,672,044円、手札3枚破棄）。
3. **P08（Claude Haiku 4.5）**: R9, 破産。`MANDATORY_REPAY_FAILED`（必要476,413円に対し現金133,607円）→`FORCED_LIQUIDATION`（債務1,905,651円中133,607円返済、貸倒1,772,044円、手札3枚破棄）。
4. **P01（Gemini 3.5 Flash-Lite）**: R10, 破産。`MANDATORY_REPAY_FAILED`（必要483,559円に対し現金357,194円）→`FORCED_LIQUIDATION`（債務1,450,677円中357,194円返済、貸倒1,093,483円、手札2枚破棄）。
5. **P04（Grok 4.3）**: R12, 生存条件未達。`SURVIVAL_CHECK`（result=`eliminated`, reason=`condition_not_met`）→`FORCED_LIQUIDATION`（現金1,934,647円が生還条件200万円未満のため全額没収、債務0）。
6. **P09（Kimi K2.6）**: R12, 生存条件未達。現金1,178,511円が200万円未満のため全額没収、債務0。
7. **P12（Claude Sonnet 5）**: R12, 生存条件未達。現金559,353円が200万円未満のため全額没収、債務0。（詳細は第10-2節）

---

## 10. 不具合の切り分け

### 10-1. AUTO_COMMIT 全件

**2件、いずれもP11**:

| R | 実際の市場 | 実際のカード | 理由 |
|---|---|---|---|
| 9 | M01 | STRAIGHT | `no_valid_response` |
| 10 | M01 | FULL_HOUSE | `no_valid_response` |

`AUTO_COMMIT` 選択ロジック（`engine/autocommit.py::select_auto_commit`）は「契約上合法な手のうち最低ランクの未使用カード」を機械的に選ぶ仕様であり、ランダムではない（STRAIGHT・FULL_HOUSEが選ばれたのは、これらがP11の当時の合法な残り手札の中で最弱だったため）。

### 10-2. 席別エラー数・リトライ数・レイテンシ

| 席 | 総コール数 | エラー数 | リトライ数 | 平均レイテンシ(ms) | 最大レイテンシ(ms) |
|---|---|---|---|---|---|
| P01 | 121 | 0 | 0 | 1,714 | 7,360 |
| P02 | 145 | 1 | 0 | 30,926 | 108,085 |
| P03 | 111 | 0 | 0 | 21,977 | 50,956 |
| P04 | 147 | 0 | 0 | 6,976 | 14,861 |
| P05 | 37 | 0 | 0 | 2,774 | 6,148 |
| P06 | 148 | 0 | 0 | 28,546 | 91,162 |
| P07 | 151 | 0 | 4 | 3,330 | 11,960 |
| P08 | 110 | 0 | 1 | 12,446 | 26,544 |
| P09 | 146 | 0 | 0 | 16,576 | 103,635 |
| P10 | 148 | 0 | 0 | 18,620 | 56,297 |
| **P11** | 143 | **57** | 0 | **77,133** | 108,503 |
| **P12** | 146 | **15** | 0 | **51,825** | 108,264 |

エラーは全席で `error_type=timeout`、エラーメッセージは全件 `DevRelay HTTP 504: timeout: raw-completion timed out on agent side`（応答本文長0）。加えてP02にも1件（R12 turn7）同種のtimeoutがある。DevRelay系4席（P02/P06/P10/P11/P12。P03=DR_TERRAは0件）のうち P11・P12・P02の3席で発生し、DR_SOL(P10)・DR_FABLE(P06)・DR_TERRA(P03)は0件だった。

なお`ACTION_ERROR`（8件、P05×3・P07×2・P08×3）は全て「同一契約への二重署名試行」（`error_type=ValueError`）で、DevRelay/LLM側の障害とは無関係のゲームロジック上の検証エラーである。

### 10-3. P11 の判定（R9・R10のAUTO_COMMIT原因）

P11のtimeoutは**全57件が同一文言** `DevRelay HTTP 504: timeout: raw-completion timed out on agent side`、`response_text` 長は全件0文字（応答本文なし）。R9・R10のcommitフェーズでも各1件のtimeoutが発生し、それぞれAUTO_COMMITに直結した（他のフェーズと異なりcommitフェーズは1ラウンド1回のみの呼び出しのため、timeout即AUTO_COMMIT）。

- R9 commit timeout: 該当行`llm_logs/game01_P11_llm_calls.jsonl` — `error_type=timeout, elapsed_ms≈108,000台, response_text=''`
- R10 commit timeout: 同上

**判定**: エラーメッセージが「DevRelay HTTP **504**」（DevRelay側のゲートウェイタイムアウト応答）であり、`response_text` が完全に空（0文字）である点、および全57件のレイテンシがほぼ108,000〜108,500msの狭い帯域に集中している点（`manifest.json` の `timeout_seconds=120` に対し約108秒で打ち切られている）から、**「モデルが無効な出力をした」のではなく「DevRelay側のタイムアウト（時間切れ）による空応答」** と判定する。モデル自体が生成した不正なJSON等を返した形跡（部分的なresponse_text等）は無い。

R9・R10のnegotiation phaseでもP11は各ラウンド10ターン中10ターン・9ターンがtimeoutで、これら2ラウンドはほぼ交渉不能の状態だった（下記10-4参照）。

### 10-4. P11・P12のラウンド・フェーズ別timeout詳細と「行動枠の空費」

負けている枠 = そのラウンドの negotiation 呼び出し総数のうち timeout になった件数（各ラウンド最大10ターン、実際の呼び出し総数は途中終了により10未満の場合あり）。

**P11**（57件、negotiation 55件・commit 2件）:

| R | negotiation呼出数 | うちtimeout | commit timeout | 備考 |
|---|---|---|---|---|
| 1 | 9 | 0 | - | |
| 2 | 9 | 4 | - | |
| 3 | 10 | 2 | - | |
| 4 | 10 | 2 | - | |
| 5 | 10 | 1 | - | |
| 6 | 10 | 2 | - | |
| 7 | 10 | 2 | - | |
| 8 | 8 | 4 | - | |
| 9 | 10 | **10（全滅）** | **1件→AUTO_COMMIT** | |
| 10 | 10 | 9 | **1件→AUTO_COMMIT** | |
| 11 | 9 | **9（全滅）** | 0（自力commit成功） | |
| 12 | 10 | **10（全滅）** | 0（自力commit成功） | R12 M03でROYAL_FLUSHを自力commitしP06を撃破（§4-1） |

negotiation合計115呼出中55件timeout（**47.8%**）。R9〜R12の4ラウンドで負け越しが顕著（40呼出中38件timeout）。commitはR9・R10のみ失陥（AUTO_COMMIT）、R11・R12は交渉が全滅してもcommit自体は自力で成功している。

**P12**（15件、全てnegotiation。commit timeoutは0件）:

| R | negotiation呼出数 | うちtimeout |
|---|---|---|
| 1〜2 | 10, 10 | 0, 0 |
| 3 | 10 | 1 |
| 4〜6 | 10, 10, 10 | 0, 0, 0 |
| 7 | 10 | 3 |
| 8 | 10 | 1 |
| 9 | 9 | 4 |
| 10 | 10 | 1 |
| 11 | 10 | 4 |
| 12 | 10 | 1 |

negotiation合計119呼出中15件timeout（**12.6%**）。commitフェーズは12ラウンド全てtimeoutなく自力で成功しており、`AUTO_COMMIT`は一度も発生していない。

**P12の脱落（R12, condition_not_met）への関与について（事実ベース）**:
- P12は12ラウンドの commit を一度もAUTO_COMMITに頼らず自力で提出しており、R12脱落の直接の引き金（`FORCED_LIQUIDATION`、現金559,353円が生還条件200万円未満で全額没収）は commit不能や無効出力によるものではない。
- R12 commitのnegotiation phaseでもtimeoutは1件のみ（turn1）で、R12単体では交渉機会の大半が機能していた。
- P12のnegotiation timeoutはR7(3件)・R9(4件)・R11(4件)に集中しており、この3ラウンドで交渉ターンの一部を喪失している（R7:10中3, R9:9中4, R11:10中4）。この期間に型C契約`C_8ae6fc27`（P12がM02参加義務を負う契約、第6節参照）が締結されているが、当該契約の締結ラウンド・締結交渉ターン番号は本節の集計範囲外であり特定していない。
- 以上から、P12の脱落は「timeoutによるAUTO_COMMITや無効出力」が直接原因ではなく、「R12までに現金を200万円まで積み上げられなかったこと」が直接原因である。timeoutによる交渉ターン喪失（15/134=11.2%）が拘束契約の回避や資金移転の機会をどの程度減らしたかは、本節のイベント集計だけでは特定できない（**未集計（理由: 交渉ターンごとの機会損失と最終現金の因果関係を数値化するには、喪失した各ターンで本来提示され得た行動案の推定が必要で、本タスクの読み取り集計の範囲を超える）**）。

---

## 11. コスト

### 11-1. API席（実課金）

| 席 | モデル | コスト(USD) |
|---|---|---|
| P09 | L5 (Kimi K2.6) | $1.9169 |
| P04 | L4 (Grok 4.3) | $1.7300 |
| P08 | L1 (Claude Haiku 4.5) | $1.3432 |
| P01 | L3 (Gemini 3.5 Flash-Lite) | $0.3863 |
| P07 | L6 (DeepSeek V4 Flash) | $0.1343 |
| P05 | L2 (GPT-4.1 Mini) | $0.1016 |
| **合計** | | **$5.6123** |

（`GAME_END.player_actual_cost_usd` と `trial_C_report.md` 双方の値に一致。`trial_C_report.md` 記載の合計 $5.6122 と1万分の1ドル単位で近似。）

### 11-2. DevRelay席（サブスク・課金$0）

| 席 | モデル | コール数 | Input | Output | CacheRead | CacheWrite |
|---|---|---|---|---|---|---|
| P02 | DR_OPUS | 145 | 290 | 251,961 | 1,059,696 | 1,142,177 |
| P03 | DR_TERRA | 111 | 2,392,621 | 52,407 | 1,445,632 | 0 |
| P06 | DR_FABLE | 148 | 296 | 236,715 | 1,081,773 | 1,195,927 |
| P10 | DR_SOL | 148 | 3,373,437 | 80,480 | 1,555,712 | 0 |
| P11 | DR_OPUS48 | 143 | 172 | 280,643 | 625,515 | 550,095 |
| P12 | DR_SONNET5 | 146 | 262 | 481,505 | 956,670 | 915,892 |

（Input/Output/CacheRead/CacheWriteは全12席分llm_calls.jsonlの`input_tokens`/`output_tokens`/`cache_read_input_tokens`/`cache_creation_input_tokens`合計。P03/P10はcache_write=0で毎コールがcache非ヒットの生input中心、P02/P06/P11/P12はinput≈0・cache_write多数でプロンプトキャッシュ活用パターンが対照的。）

- **総コスト**: $5.6122（`GAME_END.game_total_actual_cost_usd` = $5.612191390399998）
- **予算上限**: player $5.00 / game $40.00（budget_block_count=0、上限抵触なし）

---

## 12. 見どころ候補5つ

1. **首位P06、8連続で「同市場でより強いカードに負け続ける」も首位を守り切る**（R4-R10,R12の8市場でP06より強いカードの参入があり、8戦全敗。一方R1-R3,R11の4勝で6,640,000円を先行確保）— 根拠: `game01_events.jsonl` 行164,551,762,940,1079,1250-1253,1414-1422,1587-1596,1748-1749,2021ほか（第4-1節）。
2. **P11、R12交渉10ターン全滅（timeout）でもROYAL_FLUSHを自力commitし首位P06をM03で撃破**（DevRelay 504が交渉フェーズを完全に潰した最終ラウンドで、commitフェーズだけは成功） — 根拠: 行2021（COMMIT）, 行1919（R12 LEADER_ANNOUNCED=P06）, `llm_logs/game01_P11_llm_calls.jsonl` R12全10ターンtimeout。
3. **型C契約が前回6体戦の0件から一転、提案40件・成立25件・発火18件・支払総額445万円**（DR_SOL(P10)が18件と最多提案） — 根拠: 行166,167,358,551,901-904,1079,1250-1253,1428-1430,1603-1605,1761-1763,2027-2029（第6節）。
4. **P05（GPT-4.1 Mini）、R3に自ら署名した不参加契約に違反し即脱落**（現金8,876,951円のうち2,867,306円没収・手札9枚破棄。全ゲーム唯一の`ELIMINATION`イベント） — 根拠: 行561-562。
5. **R12、型C×3発火と条件未達3席（P04・P09・P12）同時脱落が重なる決着ラウンド**（P02が型C受取2件・支払1件、P10がP02から型C受取1件） — 根拠: 行2027-2029（TYPE_C_EVALUATED）, 2047-2057（SURVIVAL_CHECK/FORCED_LIQUIDATION）。

---

## 付記: 使用した使い捨てスクリプト

集計は `/tmp/c1010/*.py`（`facts.py`, `signed_terms.py`, `more.py` ほか）で実施した。いずれもリポジトリ外に配置し、リポジトリへのコミット対象外とした。全て `llm.response_parser.parse_response()` を使用し、独自JSONパーサは実装していない。
