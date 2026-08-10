# Step 1 受け入れ検証レポート

検証日: 2026-08-09
対象: ルールエンジン実装（engine/ tests/ scripts/）
仕様書: doc/uso8000000_dangou_card_spec_v0_5_frozen.md v0.5

---

## Part A: コマンド実行結果

### A-1. `uv run pytest -v`

```
============================= test session starts ==============================
platform linux -- Python 3.12.13, pytest-9.1.1, pluggy-1.6.0 -- /home/uso8m/dangou-card/.venv/bin/python
cachedir: .pytest_cache
rootdir: /home/uso8m/dangou-card
configfile: pyproject.toml
collecting ... collected 33 items

tests/test_acceptance.py::TestAcceptance::test_01_transfer_free_cash_insufficient PASSED [  3%]
tests/test_acceptance.py::TestAcceptance::test_02_auto_commit_with_contract PASSED [  6%]
tests/test_acceptance.py::TestAcceptance::test_03_bankruptcy_entry_fee PASSED [  9%]
tests/test_acceptance.py::TestAcceptance::test_04_obligation_expiry_partial PASSED [ 12%]
tests/test_acceptance.py::TestAcceptance::test_05_type_b_violation_elimination PASSED [ 15%]
tests/test_acceptance.py::TestAcceptance::test_06_type_a_free_cash_insufficient PASSED [ 18%]
tests/test_acceptance.py::TestAcceptance::test_07_bounty_r1_free_cash_zero PASSED [ 21%]
tests/test_acceptance.py::TestAcceptance::test_08_atomic_combined_obligation PASSED [ 24%]
tests/test_acceptance.py::TestAcceptance::test_09_snapshot_no_receipt_as_source PASSED [ 27%]
tests/test_acceptance.py::TestAcceptance::test_10_forced_liquidation PASSED [ 30%]
tests/test_acceptance.py::TestAcceptance::test_11_auto_commit_contract_constrained PASSED [ 33%]
tests/test_acceptance.py::TestAcceptance::test_12_contradictory_contracts_elimination PASSED [ 36%]
tests/test_acceptance.py::TestAcceptance::test_13_r12_auto_repayment_survival PASSED [ 39%]
tests/test_acceptance.py::TestAcceptance::test_14_anon_broadcast_insufficient PASSED [ 42%]
tests/test_acceptance.py::TestAcceptance::test_15_contract_fee_insufficient PASSED [ 45%]
tests/test_acceptance.py::TestAcceptance::test_16_transfer_immediate_settlement PASSED [ 48%]
tests/test_atomic.py::TestAtomicExecution::test_single_obligation_success PASSED [ 51%]
tests/test_atomic.py::TestAtomicExecution::test_single_obligation_failure PASSED [ 54%]
tests/test_atomic.py::TestAtomicExecution::test_multiple_obligors_independent PASSED [ 57%]
tests/test_atomic.py::TestAtomicExecution::test_atomic_all_or_nothing PASSED [ 60%]
tests/test_atomic.py::TestAtomicExecution::test_excluded_players_not_executed PASSED [ 63%]
tests/test_free_cash.py::TestFreeCash::test_free_cash_calculation PASSED [ 66%]
tests/test_free_cash.py::TestFreeCash::test_free_cash_zero_at_start PASSED [ 69%]
tests/test_free_cash.py::TestFreeCash::test_transfer_exactly_free_cash PASSED [ 72%]
tests/test_free_cash.py::TestFreeCash::test_transfer_one_over_free_cash PASSED [ 75%]
tests/test_free_cash.py::TestFreeCash::test_repay_not_free_cash_limited PASSED [ 78%]
tests/test_free_cash.py::TestFreeCash::test_bounty_free_cash_limited PASSED [ 81%]
tests/test_obligations.py::TestObligationExpiry::test_obligor_eliminated_expires PASSED [ 84%]
tests/test_obligations.py::TestObligationExpiry::test_counterparty_eliminated_expires PASSED [ 87%]
tests/test_obligations.py::TestObligationExpiry::test_unrelated_player_no_effect PASSED [ 90%]
tests/test_obligations.py::TestObligationExpiry::test_fulfilled_obligation_not_expired PASSED [ 93%]
tests/test_obligations.py::TestObligationExpiry::test_three_party_partial_expiry PASSED [ 96%]
tests/test_obligations.py::TestObligationExpiry::test_type_b_obligation_also_expires PASSED [100%]

============================== 33 passed in 0.04s ==============================
```

**結果: 33 passed, 0 failed**

### A-2. `uv run pytest --collect-only -q`（§12テストケース対応）

```
tests/test_acceptance.py::TestAcceptance::test_01_transfer_free_cash_insufficient
tests/test_acceptance.py::TestAcceptance::test_02_auto_commit_with_contract
tests/test_acceptance.py::TestAcceptance::test_03_bankruptcy_entry_fee
tests/test_acceptance.py::TestAcceptance::test_04_obligation_expiry_partial
tests/test_acceptance.py::TestAcceptance::test_05_type_b_violation_elimination
tests/test_acceptance.py::TestAcceptance::test_06_type_a_free_cash_insufficient
tests/test_acceptance.py::TestAcceptance::test_07_bounty_r1_free_cash_zero
tests/test_acceptance.py::TestAcceptance::test_08_atomic_combined_obligation
tests/test_acceptance.py::TestAcceptance::test_09_snapshot_no_receipt_as_source
tests/test_acceptance.py::TestAcceptance::test_10_forced_liquidation
tests/test_acceptance.py::TestAcceptance::test_11_auto_commit_contract_constrained
tests/test_acceptance.py::TestAcceptance::test_12_contradictory_contracts_elimination
tests/test_acceptance.py::TestAcceptance::test_13_r12_auto_repayment_survival
tests/test_acceptance.py::TestAcceptance::test_14_anon_broadcast_insufficient
tests/test_acceptance.py::TestAcceptance::test_15_contract_fee_insufficient
tests/test_acceptance.py::TestAcceptance::test_16_transfer_immediate_settlement
tests/test_atomic.py::TestAtomicExecution::test_single_obligation_success
tests/test_atomic.py::TestAtomicExecution::test_single_obligation_failure
tests/test_atomic.py::TestAtomicExecution::test_multiple_obligors_independent
tests/test_atomic.py::TestAtomicExecution::test_atomic_all_or_nothing
tests/test_atomic.py::TestAtomicExecution::test_excluded_players_not_executed
tests/test_free_cash.py::TestFreeCash::test_free_cash_calculation
tests/test_free_cash.py::TestFreeCash::test_free_cash_zero_at_start
tests/test_free_cash.py::TestFreeCash::test_transfer_exactly_free_cash
tests/test_free_cash.py::TestFreeCash::test_transfer_one_over_free_cash
tests/test_free_cash.py::TestFreeCash::test_repay_not_free_cash_limited
tests/test_free_cash.py::TestFreeCash::test_bounty_free_cash_limited
tests/test_obligations.py::TestObligationExpiry::test_obligor_eliminated_expires
tests/test_obligations.py::TestObligationExpiry::test_counterparty_eliminated_expires
tests/test_obligations.py::TestObligationExpiry::test_unrelated_player_no_effect
tests/test_obligations.py::TestObligationExpiry::test_fulfilled_obligation_not_expired
tests/test_obligations.py::TestObligationExpiry::test_three_party_partial_expiry
tests/test_obligations.py::TestObligationExpiry::test_type_b_obligation_also_expires

33 tests collected in 0.02s
```

§12の16テストケースは `test_01_` 〜 `test_16_` で1:1対応。テスト名とdocstringに仕様書番号・シナリオを明記済み。

### A-3. `uv run python scripts/dry_run.py`

```
=== 談合カード ドライラン ===
プレイヤー数: 12
ラウンド数: 12
総賞金: 11,520,000円
生還条件: 借金0 + 現金3,000,000円以上
シード: 42
---

=== 結果 ===
生還者: 0人

脱落者: 12人
  P01: 理由=condition_not_met, R12
  P02: 理由=condition_not_met, R12
  ...（P03〜P11同様）
  P12: 理由=condition_not_met, R12

JSONLログ出力: logs/dry_run_seed42_12p.jsonl
イベント数: 566
```

全員StubAgent（最低借入120万・交渉pass・最低カード最低市場）のため、全員条件未達で脱落。期待通り。

#### JSONL先頭20行

```json
{"event_type":"GAME_START","timestamp":"2026-08-09T07:20:18.931329+00:00","round_num":0,"phase":"setup","step":null,"data":{"config":{"num_players":12,"num_rounds":12,"total_prize":11520000,"survival_cash":3000000,"interest_rate":0.015,"entry_fee":100000},"seed":42}}
{"event_type":"LOAN_CHOSEN","timestamp":"...","round_num":0,"phase":"setup","step":null,"data":{"player_id":"P01","loan_amount":1200000}}
{"event_type":"LOAN_CHOSEN","timestamp":"...","round_num":0,"phase":"setup","step":null,"data":{"player_id":"P02","loan_amount":1200000}}
{"event_type":"LOAN_CHOSEN","timestamp":"...","round_num":0,"phase":"setup","step":null,"data":{"player_id":"P03","loan_amount":1200000}}
{"event_type":"LOAN_CHOSEN","timestamp":"...","round_num":0,"phase":"setup","step":null,"data":{"player_id":"P04","loan_amount":1200000}}
{"event_type":"LOAN_CHOSEN","timestamp":"...","round_num":0,"phase":"setup","step":null,"data":{"player_id":"P05","loan_amount":1200000}}
{"event_type":"LOAN_CHOSEN","timestamp":"...","round_num":0,"phase":"setup","step":null,"data":{"player_id":"P06","loan_amount":1200000}}
{"event_type":"LOAN_CHOSEN","timestamp":"...","round_num":0,"phase":"setup","step":null,"data":{"player_id":"P07","loan_amount":1200000}}
{"event_type":"LOAN_CHOSEN","timestamp":"...","round_num":0,"phase":"setup","step":null,"data":{"player_id":"P08","loan_amount":1200000}}
{"event_type":"LOAN_CHOSEN","timestamp":"...","round_num":0,"phase":"setup","step":null,"data":{"player_id":"P09","loan_amount":1200000}}
{"event_type":"LOAN_CHOSEN","timestamp":"...","round_num":0,"phase":"setup","step":null,"data":{"player_id":"P10","loan_amount":1200000}}
{"event_type":"LOAN_CHOSEN","timestamp":"...","round_num":0,"phase":"setup","step":null,"data":{"player_id":"P11","loan_amount":1200000}}
{"event_type":"LOAN_CHOSEN","timestamp":"...","round_num":0,"phase":"setup","step":null,"data":{"player_id":"P12","loan_amount":1200000}}
{"event_type":"MARKET_OPEN","timestamp":"...","round_num":1,"phase":"market_open","step":null,"data":{"markets":[{"market_id":"M01","base_prize":240000,"carryover":0,"prize_pool":240000},{"market_id":"M02","base_prize":240000,"carryover":0,"prize_pool":240000},{"market_id":"M03","base_prize":240000,"carryover":0,"prize_pool":240000}]}}
{"event_type":"NEGOTIATION_ACTION","timestamp":"...","round_num":1,"phase":"negotiation","step":null,"data":{"player_id":"P11","action":"pass","turn":1}}
{"event_type":"NEGOTIATION_ACTION","timestamp":"...","round_num":1,"phase":"negotiation","step":null,"data":{"player_id":"P01","action":"pass","turn":1}}
{"event_type":"NEGOTIATION_ACTION","timestamp":"...","round_num":1,"phase":"negotiation","step":null,"data":{"player_id":"P06","action":"pass","turn":1}}
{"event_type":"NEGOTIATION_ACTION","timestamp":"...","round_num":1,"phase":"negotiation","step":null,"data":{"player_id":"P07","action":"pass","turn":1}}
{"event_type":"NEGOTIATION_ACTION","timestamp":"...","round_num":1,"phase":"negotiation","step":null,"data":{"player_id":"P10","action":"pass","turn":1}}
{"event_type":"NEGOTIATION_ACTION","timestamp":"...","round_num":1,"phase":"negotiation","step":null,"data":{"player_id":"P05","action":"pass","turn":1}}
```

#### JSONL最終5行

```json
{"event_type":"SURVIVAL_CHECK","timestamp":"...","round_num":12,"phase":"finance","step":null,"data":{"player_id":"P11","result":"eliminated","reason":"condition_not_met","cash":85247,"debt":0}}
{"event_type":"FORCED_LIQUIDATION","timestamp":"...","round_num":12,"phase":"finance","step":null,"data":{"player_id":"P11","reason":"condition_not_met","round":12,"cash_before":85247,"debt_before":0,"debt_repaid":0,"bad_debt":0,"cash_confiscated":85247,"cards_destroyed":0}}
{"event_type":"SURVIVAL_CHECK","timestamp":"...","round_num":12,"phase":"finance","step":null,"data":{"player_id":"P12","result":"eliminated","reason":"condition_not_met","cash":85247,"debt":0}}
{"event_type":"FORCED_LIQUIDATION","timestamp":"...","round_num":12,"phase":"finance","step":null,"data":{"player_id":"P12","reason":"condition_not_met","round":12,"cash_before":85247,"debt_before":0,"debt_repaid":0,"bad_debt":0,"cash_confiscated":85247,"cards_destroyed":0}}
{"event_type":"GAME_END","timestamp":"...","round_num":12,"phase":"end","step":null,"data":{"survivors":[],"eliminated":[{"player_id":"P01","reason":"condition_not_met","round":12},...,{"player_id":"P12","reason":"condition_not_met","round":12}]}}
```

### A-4. seed再現性検証

`--seed 99` で2回実行。タイムスタンプを除外して全行比較。

```
Lines: run1=566, run2=566
RESULT: 完全一致（タイムスタンプ除外）
```

**結果: 同一seedで完全再現可能**。タイムスタンプのみが非決定的フィールド。

### A-5. .env / APIキー参照チェック

```bash
$ grep -rn "\.env\|dotenv\|API_KEY\|OPENAI\|ANTHROPIC" engine/ scripts/ tests/ --include="*.py"
（出力なし — exit code 1）
```

**結果: ゼロ件。安全**

### A-6. pyproject.toml dependencies

```toml
[project]
name = "dangou-card"
version = "0.1.0"
description = "Add your description here"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "pydantic>=2.13.4",
    "pytest>=9.1.1",
]
```

外部依存は pydantic + pytest のみ。最小限の要件を満たしている。

### A-7. doc/issues.md 全文

```markdown
# Issues

## 仕様の曖昧点・保守的解釈

- [x] 利息端数処理: 仕様に明記なし → 切り上げ（借り手に不利な保守的解釈）を採用
- [x] 同ランク均等分配の端数: 仕様に明記なし → 切り捨て（余りはシステム没収）を採用
- [x] 報奨達成者が複数いる場合: 仕様に明記なし → 最初の1人にのみ支払いを採用

## 改善

- [ ] Negotiation中のrepayアクションとFinanceフェイズの連携の精緻化
- [ ] 報奨条件の拡張（現在は3種のみ実装）
- [ ] 観戦者用の情報出力強化
```

---

## Part B: 急所コードの抜粋

### B-1. Free Cash の計算式

**ファイル: `engine/models.py` L146-155**

```python
    @computed_field
    @property
    def free_cash(self) -> int:
        """
        自由資金（§2.4）

        Free Cash = max(0, 現金残高 − 借金残高[利息込み])
        借金横流し防止のため、借金分を差し引いた残りのみが自由に使える。
        """
        return max(0, self.cash - self.debt_balance)
```

`debt_balance` は `"""借金残高（利息込み）"""` と定義されており（L133-134）、利息計上後の残高を使用している。仕様§2.4の `Free Cash = max(0, 現金残高 − 借金残高[利息込み])` に一致。

### B-2. Settlement 8Step処理の骨格

**ファイル: `engine/settlement.py` L23-276（全文が8Step処理）**

骨格（コメントヘッダのみ抜粋）:

```python
def execute_settlement(...):
    eliminated_this_settlement: set[str] = set()     # L66

    # Step 1: Reveal                                  # L70
    # Step 2: Market Settlement                        # L81
    # Step 3: 行動契約(型B)監査                         # L124
    # Step 4: 型A判定用スナップショット                  # L146
    # Step 5: 型A契約のAtomic執行                       # L163
    # Step 6: 公開報奨の判定・支払い                     # L206
    # Step 7: 脱落処理の公示                            # L252
    # Step 8: 脱落時強制清算（§1.6）                     # L265
```

**Step 3で脱落確定者がStep 5から除外される箇所（L140-143, L165-167）:**

```python
    # Step 3: L140-143
    for violator_id in violators:
        contracts = elim_ops.expire_obligations_for_player(violator_id, contracts)
        eliminated_this_settlement.add(violator_id)

    # Step 5: L165-167 — excluded_playersに渡す
    type_a_obs = contract_ops.get_active_type_a_obligations(
        contracts, round_num, excluded_players=eliminated_this_settlement,
    )
```

`eliminated_this_settlement` セットがStep 3で蓄積され、Step 5の`get_active_type_a_obligations`に`excluded_players`として渡されることで除外が実現。

### B-3. 型A契約のAtomic執行

**ファイル: `engine/contracts.py` L217-273**

```python
def execute_type_a_atomic(
    obligations: list[Obligation],
    snapshots: dict[str, dict[str, int]],
) -> tuple[list[Obligation], list[str], dict[str, int]]:
    # 義務者ごとに支払合計を算出
    obligor_totals: dict[str, int] = {}
    obligor_obligations: dict[str, list[Obligation]] = {}
    for ob in obligations:
        amount = ob.details.get("amount", 0)
        obligor_totals[ob.obligor] = obligor_totals.get(ob.obligor, 0) + amount
        obligor_obligations.setdefault(ob.obligor, []).append(ob)

    failed_obligors: list[str] = []
    payments: dict[str, int] = {}

    # 各義務者について判定
    for obligor, total in obligor_totals.items():
        snap = snapshots.get(obligor, {"cash": 0, "free_cash": 0})
        if total <= snap["free_cash"]:
            # 全件執行可能 → 一括支払い
            payments[obligor] = payments.get(obligor, 0) - total
            for ob in obligor_obligations[obligor]:
                amount = ob.details.get("amount", 0)
                payments[ob.counterparty] = payments.get(ob.counterparty, 0) + amount
        else:
            # 1円でも不足 → 全件履行不能 → 脱落
            failed_obligors.append(obligor)
    ...
```

義務者ごとに`obligor_totals`で合算し、`snap["free_cash"]`（スナップショット時点のFreeCash）と比較。§6.6の「義務者ごとに合算してAtomic判定」に一致。

**スナップショット取得タイミング（`engine/settlement.py` L146-156）:**

```python
    # Step 4（Step 2 Market Settlement の後に実行される）
    # 市場賞金反映後のCash/FreeCashを固定（§6.6）
    snapshots: dict[str, dict[str, int]] = {}
    for pid, p in players.items():
        if p.is_alive and pid not in eliminated_this_settlement:
            snapshots[pid] = {
                "cash": p.cash,
                "free_cash": p.free_cash,
            }
```

Step 2（市場賞金支払い）の後、Step 3（型B監査）の後、Step 4でスナップショット取得。市場賞金が反映済みのCash/FreeCashが固定される。§5.2 Step 4の「市場賞金反映後」に一致。

### B-4. 契約失効ロジック（義務単位）

**ファイル: `engine/elimination.py` L76-106**

```python
def expire_obligations_for_player(
    player_id: str,
    contracts: list[Contract],
) -> list[Contract]:
    """
    脱落者に関連する未履行義務を失効させる（§6.3）

    - 脱落者が義務者(obligor)または相手方(counterparty)である未履行義務のみ失効
    - 生存者間の義務は継続
    - 「3者契約で生贄を脱落させて全体解除」は不可能
    """
    updated: list[Contract] = []
    for contract in contracts:
        new_obligations: list[Obligation] = []
        for ob in contract.obligations:
            if (not ob.is_fulfilled and not ob.is_expired and
                    (ob.obligor == player_id or ob.counterparty == player_id)):
                # 脱落者が関わる未履行義務を失効
                new_obligations.append(ob.model_copy(update={"is_expired": True}))
            else:
                new_obligations.append(ob)
        updated.append(contract.model_copy(update={"obligations": new_obligations}))
    return updated
```

条件: `obligor == player_id or counterparty == player_id` かつ `not is_fulfilled` かつ `not is_expired` の場合のみ失効。それ以外の義務（生存者間）は継続。§6.3に一致。

### B-5. 自動代行Commit

**ファイル: `engine/autocommit.py` L15-53**

```python
def compute_legal_commits(
    player: PlayerState,
    contracts: list[Contract],
    markets: list[Market],
    round_num: int,
) -> list[MarketCommit]:
    # 当該プレイヤーの当該ラウンドの型B義務を取得
    type_b_obs = get_all_type_b_for_player(contracts, player.player_id, round_num)

    legal: list[MarketCommit] = []

    # 全(市場, カード)の組合せを列挙
    for market in markets:
        for card in player.hand:
            commit = MarketCommit(
                player_id=player.player_id,
                market_id=market.market_id,
                card=card,
            )
            # この組合せが全型B義務を満たすかチェック
            if _satisfies_all_obligations(commit, type_b_obs):
                legal.append(commit)

    return legal
```

先に`get_all_type_b_for_player`で型B義務を取得し、全(市場, カード)組合せを列挙、義務を満たすもののみを合法候補に残す。§4.4の「契約を満たす合法Commit集合をシステムが計算」に一致。

### B-6. R12 Finance

**ファイル: `engine/finance.py` L45-142**

```python
    # --- Step 1: 利息計上 ---                          # L45-60
    for pid, p in list(players.items()):
        ...
        p = player_ops.apply_interest(p, config.interest_rate)
        ...

    if round_num < 12:                                  # L62
        # --- R1-11: 任意返済 ---                        # L63-82
        ...
    else:
        # --- R12: 自動返済 ---                          # L84-100
        for pid, p in list(players.items()):
            if not p.is_alive:
                continue
            if p.debt_balance > 0:
                repay_amount = min(p.cash, p.debt_balance)
                p = player_ops.repay_debt(p, repay_amount)
                ...

        # --- R12: 生還/脱落判定 ---                     # L102-142
        for pid, p in list(players.items()):
            ...
            if p.debt_balance > 0:                      # Debt=0チェック
                survived = False
            if survived and p.cash < config.survival_cash:  # Cash≧300万チェック
                survived = False
            ...
```

処理順: 利息計上 → 自動返済 → Debt=0確認 → Cash≧300万確認 → 判定。§5.3に一致。

### B-7. 脱落時強制清算

**ファイル: `engine/elimination.py` L14-73**

```python
def forced_liquidation(p, reason, round_num, contracts):
    # --- Step 1: 借金返済 ---                          # L48-54
    repayment = min(p.cash, p.debt_balance)
    p = p.model_copy(update={
        "cash": p.cash - repayment,
        "debt_balance": p.debt_balance - repayment,
    })

    # --- Step 2: 貸倒れ ---                            # L56-57
    record["bad_debt"] = p.debt_balance

    # --- Step 3: 残金没収 ---                          # L59-61
    record["cash_confiscated"] = p.cash
    p = p.model_copy(update={"cash": 0, "debt_balance": 0})

    # --- Step 4: カード消滅 ---                        # L63-65
    record["cards_destroyed"] = len(p.hand)
    p = p.model_copy(update={"hand": []})

    # --- Step 5: 脱落状態にする ---                    # L67-68
    p = player_ops.eliminate(p, reason, round_num)

    # --- Step 6: 義務失効（§6.3） ---                  # L70-71
    updated_contracts = expire_obligations_for_player(p.player_id, contracts)
```

処理順: 返済→貸倒れ→没収→カード消滅→脱落→義務失効。§1.6に一致。

### B-8. 報奨バリデーション（「Xを脱落させた者へ」型の拒否）

**ファイル: `engine/models.py` L290-299**

```python
class BountyConditionType(str, Enum):
    """報奨条件の種別"""
    MARKET_WIN_AGAINST = "market_win_against"
    """特定プレイヤーと同一市場に参加し、より高いカードで勝利"""

    SAME_MARKET = "same_market"
    """特定プレイヤーと同一市場に参加"""

    PLAYER_ELIMINATED = "player_eliminated"
    """特定プレイヤーが脱落"""
```

**明示的な拒否バリデーション: 未実装**。`PLAYER_ELIMINATED` はイベント型（`BountyType.EVENT`）でのみ使用される設計意図だが、達成者型（`BountyType.ACHIEVEMENT`）と `PLAYER_ELIMINATED` の組合せを明示的に禁止するコードは存在しない。

enum制限で3種のみに限定されているため、仕様§7.2が禁止する「Xを脱落させた者へ」型（因果帰属が機械判定不能）は、そもそもBountyConditionTypeに存在しない。ただし `ACHIEVEMENT × PLAYER_ELIMINATED` の組合せに対する防御は不足。

---

## Part C: 仕様書照合と自己申告

### C-1. 仕様照合（Part B各項目）

| # | 対象 | 仕様書セクション | 判定 | 備考 |
|---|---|---|---|---|
| B-1 | FreeCash計算 | §2.4 | ✅ 一致 | `max(0, Cash - DebtBalance)`、利息込み残高を使用 |
| B-2 | Settlement 8Step | §5.2 | ✅ 一致 | 8Stepの順序が厳密に実装されている |
| B-3 | 型A Atomic執行 | §6.6 | ✅ 一致 | 義務者ごと合算、スナップショットFreeCash基準 |
| B-4 | 義務失効 | §6.3 | ✅ 一致 | 義務単位で失効、生存者間は継続 |
| B-5 | 自動代行Commit | §4.4 | ✅ 一致 | 合法集合先行計算→優先順位選択 |
| B-6 | R12 Finance | §5.3 | ✅ 一致（微小懸念あり） | 利息→自動返済→生還判定の順序は正しい |
| B-7 | 強制清算 | §1.6 | ✅ 一致 | 返済→貸倒れ→没収→カード消滅→義務失効 |
| B-8 | 報奨拒否 | §7.2 | ⚠️ 懸念あり | 「Xを脱落させた者へ」型の明示的拒否が未実装 |

### C-2. 検出した懸念事項

#### 懸念1: 達成者型 × PLAYER_ELIMINATED の組合せ防御（低リスク）

- **仕様**: §7.2 `「Xを脱落させた者へ」型は禁止（因果帰属が機械判定不能）`
- **実装**: `BountyConditionType` enumが3種のみで暗黙的に制限されているが、`BountyType.ACHIEVEMENT` × `BountyConditionType.PLAYER_ELIMINATED` の組合せを明示的にバリデーションで拒否するコードがない
- **影響**: LLMがこの組合せでbounty_postを出力した場合、バリデーションを通過してしまう可能性がある。ただし `evaluate_achievement_bounties()` 内に `PLAYER_ELIMINATED` の判定ロジックがないため、掲載されても発火しない（機能的には無害）
- **推奨**: `actions.py` のbounty_postバリデーションに `if bounty_type == "achievement" and condition_type == "player_eliminated": reject` を追加

#### 懸念2: finance.py の round_num ハードコード（極低リスク）

- **箇所**: `engine/finance.py` L62 `if round_num < 12:`
- **仕様**: §5.3ではラウンド数は12固定
- **影響**: `config.num_rounds` を使うべきだが、仕様上12ラウンド固定のため実害なし
- **推奨**: `if round_num < config.num_rounds:` に修正（将来のパラメータ変更に備えて）

### C-3. 独自解釈・保守的解釈（issues.md記載分と一致）

| # | 曖昧点 | 採用した解釈 | 根拠 |
|---|---|---|---|
| 1 | 利息の端数処理（仕様に明記なし） | 切り上げ（`math.ceil`） | 借り手に不利な保守的解釈 |
| 2 | 同ランク均等分配の端数（仕様に明記なし） | 切り捨て（`//` 整数除算） | 余りはシステム没収。§1.5の純流出に含む |
| 3 | 報奨達成者が複数いる場合（仕様に明記なし） | 最初の1人にのみ支払い | 最も保守的な解釈。1報奨1支払い |

---

## 総合判定

| 項目 | 結果 |
|---|---|
| 受け入れテスト16件 | **全件PASSED** |
| 追加テスト17件 | **全件PASSED** |
| ドライラン（12人×12R） | **完走、JSONL 566イベント出力** |
| seed再現性 | **完全一致** |
| .env/APIキー安全性 | **ゼロ件** |
| 外部依存最小性 | **pydantic + pytest のみ** |
| 仕様照合（8箇所） | **7/8 一致、1件軽微な懸念** |
