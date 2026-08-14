# 倍掛けバグ調査レポート

> 2026-08-14 調査。LLM 短時間試行（trial_C_20260814_210120）のログを精査。

---

## 論点A: パース失敗とフォールバックの矛盾

### 結論: **バグではない。レポートの「parse fail」注記が誤り。**

### 事実

P03 (Kimi) の R1 倍掛け選択時の**生レスポンス全文**:

```
```json
{"strategy": {"reason": "R1で26万円獲得し現金136万円、借金120万円、Free Cash16万円。残り5ラウンドで借金120万円+利息を返済しつつ200万円以上の現金を確保する必要がある。倍掛け成功で52万円なら大きく前進する。残りカードは非常に強力（ROYAL_FLUSH, STRAIGHT_FLUSH, FOUR_OF_A_KIND等）で、R2で賞金獲得は十分可能。特に空き巣を除く成功条件でも、強カードで他者がいる市場に参加すれば勝率は高い。26万円をそのまま持っていても返済圧力は変わらず、倍掛けの期待値は正。他プレイヤーに『強いカードを持っている』と示唆する効果もあり、今後の交渉で有利に働く可能性も。", "emotion": "楽"}, "choice": "DOUBLE"}
```
```

- マークダウンのコードブロック (` ```json ... ``` `) でラップされているが、`extract_json()` はパターン1（完全なコードブロック）として正しくパース
- `choice` フィールドは `"DOUBLE"` → engine に `True` が渡り、`DOUBLE_UP_CHOSEN` イベントが記録された
- **パースは成功している。レポートの「parse fail」は誤記。**

### 全プレイヤーの倍掛けレスポンス原文

#### P01 (GPT-4.1 Mini) — 4回全てパース成功

R1:
```json
{"strategy": {"reason": "次ラウンド以降の賞金獲得に自信があり、資金効率を高めるために倍掛けを選択。失敗時の損失は許容範囲であり、リスクを取る価値があると判断。", "emotion": "楽"}, "choice": "DOUBLE"}
```

R3:
```json
{"strategy": {"reason": "現金153万円、借金82万円、Free Cash70万円と余裕があり、次ラウンドで市場獲得も狙えるため、リスクを取って賞金を倍にしたい。成功すれば資金増強と交渉力アップに繋がる。", "emotion": "楽"}, "choice": "DOUBLE"}
```

R4:
```json
{"strategy": {"reason": "現金に余裕があり、次ラウンドでの市場獲得に自信があるため、倍掛けで資産増を狙う。残りカードは強力で勝利の可能性が高い。", "emotion": "楽"}, "choice": "DOUBLE"}
```

R5:
```json
{"strategy": {"reason": "現金に余裕があり、次ラウンドでの市場獲得見込みもあるため、リスクを取って賞金を倍に増やす選択をします。残りラウンドが少なく、最終資産最大化のために積極的に動きたい。", "emotion": "楽"}, "choice": "DOUBLE"}
```

#### P02 (DeepSeek V3 (L)) — 4回全てパース成功、全て TAKE

R1: `"choice": "TAKE"` (理由: 余裕なく確実に確保)
R3: `"choice": "TAKE"` (理由: Free Cash 8万円しかなく Entry Fee 支払えなくなるリスク)
R4: `"choice": "TAKE"` (理由: 安全策で生還条件優先)
R5: `"choice": "TAKE"` (理由: 残り1ラウンドで確実に生還優先)

#### P03 (Kimi K2.6) — 1回パース成功、DOUBLE

R1: `"choice": "DOUBLE"` (上記引用の通り)

#### P04 (Claude Haiku 4.5) — 2回全てパース成功

R1: `"choice": "TAKE"` (マークダウンコードブロックでラップ。理由: リスク評価で安全策)
R2: `"choice": "DOUBLE"` (マークダウンコードブロックでラップ。理由: 高ランクカード5枚残存で成功率高い)

#### P05 (DeepSeek V3) — 3回全てパース成功

R1: `"choice": "TAKE"` (理由: Free Cash 16万円で没収は痛い)
R2: `"choice": "DOUBLE"` (理由: 余裕があり期待値が正)
R4: `"choice": "DOUBLE"` (理由: 生還厳しくリスクを取る価値あり)

#### P06 (Gemini 3.5 Flash) — 3回全てパース成功、全て TAKE

R1: `"choice": "TAKE"` (マークダウンコードブロック)
R2: `"choice": "TAKE"` (マークダウンコードブロック)
R3: `"choice": "TAKE"` (マークダウンコードブロック)

### パース処理の確認

`llm/llm_agent.py` の `choose_double_up()`:

```python
data = extract_json(text)
if data:
    choice = str(data.get("choice", "")).upper().strip()
    if choice == "DOUBLE":
        return True
    if choice == "TAKE":
        return False
    # 無効な choice → 警告ログ + return False
    return False
# JSON パース失敗 → 警告ログ + return False
return False
```

**パース失敗時は確実に False（TAKE）が返る実装になっている。** `extract_json()` は ` ```json ``` ` ラップにも対応済み。

---

## 論点B: ソロ市場での倍掛け成功

### 結論: **バグ（実装漏れ）。**

### 仕様書の定義

`doc/uso8000000_dangou_card_spec_v0_7_season2.md` L280:

> **単独参加市場（参加者が本人1人のみ）での賞金獲得は、倍掛けの成功判定に含めない。**

L282:
> 同ランク山分けでも1円以上獲得なら **成功**（ただし単独参加市場の除外が優先）

### engine の実装

`engine/game.py` L743-792 (`_process_double_up`):

```python
# L743-750: ソロ市場の検出と勝者の集計
for mr in market_results:
    if len(mr.participants) == 1:
        solo_markets.add(mr.market_id)
    for winner_id in mr.winners:
        round_winners[winner_id] = round_winners.get(winner_id, 0) + mr.prize_per_winner
        # ← ソロ市場の賞金もここに加算される

# L767-768: 成功判定
if dep.player_id in round_winners and round_winners[dep.player_id] > 0:
    # ← round_winners にはソロ市場賞金が含まれている。除外されていない。
    payout = dep.deposit_amount * 2
    dep.success = True
    # L771-776: 判定"後"にフラグを設定（メトリクス用のみ）
    won_markets = winner_markets.get(dep.player_id, [])
    all_solo = all(m in solo_markets for m in won_markets)
    dep.from_solo_market = all_solo
```

**問題**: `round_winners` にソロ市場での賞金が含まれたまま成功判定が行われている。`from_solo_market` フラグは判定後に設定されるだけで、**判定自体からソロ市場を除外する処理が存在しない**。

### 該当3件のログ

#### R5: P01 の倍掛け判定（R4 で DOUBLE → R5 で判定）

R5 の市場結果:
```
M01: participants=1, winners=['P01'], prize=420,000
M02: participants=1, winners=['P02'], prize=420,000
M03: participants=1, winners=['P05'], prize=420,000
```

**全3市場が participants=1（ソロ市場）**。P01 は M01 でソロ勝利。

```json
{"event_type": "DOUBLE_UP_RESOLVED", "round_num": 5,
 "data": {"player_id": "P01", "result": "success", "deposit": 100000, "payout": 200000, "from_solo_market": true}}
```

→ **仕様上は失敗すべき。** ソロ市場のみでの賞金獲得は成功判定に含めない。

#### R5: P05 の倍掛け判定（R4 で DOUBLE → R5 で判定）

P05 は M03 でソロ勝利（上記と同じ R5 の市場結果）。

```json
{"event_type": "DOUBLE_UP_RESOLVED", "round_num": 5,
 "data": {"player_id": "P05", "result": "success", "deposit": 420000, "payout": 840000, "from_solo_market": true}}
```

→ **仕様上は失敗すべき。**

#### R6: P01 の倍掛け判定（R5 で DOUBLE → R6 で判定）

R6 の市場結果:
```
M01: participants=1, winners=['P01'], prize=不明（最終R3倍）
M02: participants=1, winners=['P02']
M03: participants=1, winners=['P05']
```

**全3市場が participants=1（ソロ市場）**。

```json
{"event_type": "DOUBLE_UP_RESOLVED", "round_num": 6,
 "data": {"player_id": "P01", "result": "success", "deposit": 220000, "payout": 440000, "from_solo_market": true}}
```

→ **仕様上は失敗すべき。**

### 影響範囲

| ラウンド | プレイヤー | 預託額 | 実際の結果 | 仕様上の正しい結果 | 過剰支払い |
|---|---|---|---|---|---|
| R5 | P01 | 100,000 | 成功 (+200,000) | **失敗** (-100,000) | 300,000 |
| R5 | P05 | 420,000 | 成功 (+840,000) | **失敗** (-420,000) | 1,260,000 |
| R6 | P01 | 220,000 | 成功 (+440,000) | **失敗** (-220,000) | 660,000 |
| **合計** | | | | | **2,220,000** |

注: 過剰支払い = payout + 本来没収されるべき deposit

### 修正方針（提案のみ、未実施）

`_process_double_up` の L743-750 で `round_winners` を構築する際に、ソロ市場の賞金を除外する:

```python
for mr in market_results:
    if len(mr.participants) == 1:
        solo_markets.add(mr.market_id)
    for winner_id in mr.winners:
        # ソロ市場の賞金は round_winners に加算しない
        if mr.market_id not in solo_markets:
            round_winners[winner_id] = round_winners.get(winner_id, 0) + mr.prize_per_winner
        if winner_id not in winner_markets:
            winner_markets[winner_id] = []
        winner_markets[winner_id].append(mr.market_id)
```

ただし `solo_markets` はループ内で逐次構築されるため、上記のままでは先に処理された市場のソロ判定が後続に影響する問題がある。正確には2パスにするか、先に全市場のソロ判定を完了させてから `round_winners` を構築する必要がある。

---

## 論点C: 借入額ログの欠落

### 結論: **バグではない。レポート集計の解釈違い。**

### 事実

`LOAN_CHOSEN` イベントには `loan_amount` が正しく記録されている:

```
P01 (GPT-4.1 Mini): 1,200,000円
P02 (DeepSeek V3 (L)): 5,000,000円
P03 (Kimi K2.6): 1,200,000円
P04 (Claude Haiku 4.5): 4,000,000円
P05 (DeepSeek V3): 10,000,000円
P06 (Gemini 3.5 Flash): 3,000,000円
```

レポート (`doc/llm_short_trial_report.md`) で「0円」と記載されたのは、集計スクリプト（`regenerate_phase_c_report` またはレポート作成時の手動集計）が `LOAN_CHOSEN` イベントの `data.loan_amount` を正しく読み取っていないか、別のフィールド名を参照したためと推定される。

---

## まとめ

| 論点 | 結論 | 深刻度 |
|---|---|---|
| A: パース失敗 | **バグではない**。レポートの「parse fail」注記が誤り | - |
| B: ソロ市場除外 | **バグ（実装漏れ）**。仕様 §6.2 の除外条件が未実装 | **高** |
| C: 借入額0円 | **バグではない**。レポート集計の解釈違い | 低 |
