# v0.7 実装検証レポート

検証日: 2026-08-14 07:42 JST

---

## 1. テスト全件実行

```
226 passed, 0 failed, 4 warnings
```

| 項目 | 値 |
|---|---|
| 総テスト数 | **226** |
| 合格 | 226 |
| 失敗 | 0 |
| 実装前 | 198 |
| **増加分** | **28** |

### 増加分の内訳

| テストファイル | 件数 | 内容 |
|---|---|---|
| `tests/test_mandatory_repay.py` | 12 | 強制返済: 計算検証8件 + Finance統合4件 |
| `tests/test_card_trade.py` | 16 | カードトレード: swap_card 2件 + バリデーション6件 + 実行2件 + モデル1件 + 可視性2件 + プロンプト表示2件 + accept検証1件 |

### 既存テスト修正分（増減なし）

`tests/test_s2_rules.py` の2件が修正されたが、テスト件数は変化していない（修正のみ）。

---

## 2. devlog の確認

`doc/devlog.md` の末尾に v0.7 実装エントリが追記されている。全文引用:

> ### 2026-08-14 21:00 JST — v0.7 実装: 強制返済 + カードトレード + fog_rounds 修正
>
> Season 2 仕様書 v0.7 の新規メカニクスを engine に実装。
>
> #### 1. 強制返済（v0.7 §2）
> - `engine/config.py`: `mandatory_repay_enabled`, `mandatory_repay_k` パラメータ追加
> - `engine/player.py`: `compute_mandatory_repayment(debt, remaining, k)` — `ceil(debt ÷ (remaining + k))`。k=0（完全均等）
> - `engine/finance.py`: Finance フェイズに Step 2（強制最低返済）を挿入。利息計上後、任意返済前。返済不能で破産脱落
> - `tests/test_mandatory_repay.py`: 新規テスト 8 件（計算検証・返済成功・破産・無効時・S1互換性）
>
> #### 2. カードトレード（v0.7 §3）— 提案→受諾の2アクション制
> - `engine/models.py`: `CardTradeProposal`, `CardTradeStatus`, `CardTradeProposeAction`, `CardTradeAcceptAction` 追加。Action Union 拡張
> - `engine/actions.py`: propose/accept のバリデーション追加（カード所持・FreeCash・相手生存）
> - `engine/game.py`: `trade_proposals` リスト + `_trade_counts` で回数管理。propose で提案生成、accept でアトミック実行（カード交換+現金移動）。受諾時にカード・現金を再検証（提案後の消費に対応）。`_build_visible_state()` に `trades_pending` 追加（当事者のみ可視）
> - `llm/prompt_builder.py`: 交渉プロンプトに「提案中のカードトレード」セクション追加
> - `engine/config.py`: `card_trade_enabled`, `card_trade_max_per_round`, `card_trade_last_round` 追加
> - `tests/test_card_trade.py`: 新規テスト 12 件（swap_card・バリデーション・S2完走・可視性・プロンプト表示）
>
> #### 3. fog_rounds 修正
> - S2 プリセット（`default_8_s2()`, `baseline_v1_s2()`）の `fog_rounds` を `[4,8]` → `[]` に変更（v0.7 §10.2: 霧のラウンド削除）
> - `tests/test_s2_rules.py`: 霧テストを明示的 `fog_rounds=[4,8]` 設定に変更。プリセットテストを v0.7 に合わせて更新
>
> **テスト**: 226 件全通過（既存 198 + 新規 28）。S1 設定でのゲーム完走に変更なし確認済み

---

## 3. 強制返済の実装確認

### 3.1 `compute_mandatory_repayment()` の実装（`engine/player.py` L171-189）

```python
def compute_mandatory_repayment(debt_balance: int, remaining_rounds: int, k: int = 0) -> int:
    """
    強制最低返済額を計算する（v0.7 §2.2）

    式: ceil(debt_balance ÷ (remaining_rounds + k))
    R12（remaining=1, k=0）の場合は debt_balance 全額。
    """
    divisor = remaining_rounds + k
    if divisor <= 0:
        return debt_balance
    return math.ceil(debt_balance / divisor)
```

### 3.2 確認事項

| 項目 | 結果 |
|---|---|
| k のパラメータ名 | `k`（関数引数）/ `mandatory_repay_k`（GameConfig） |
| k の既定値 | `0`（`engine/config.py` L88: `mandatory_repay_k: int = 0`） |
| GameConfig 上の定義箇所 | `engine/config.py` L85-98 |
| 端数処理 | `math.ceil` ✅ 切り上げ |
| 残り返済回数の計算式 | `config.num_rounds - round_num + 1`（`engine/finance.py` L67） |
| 返済不能時の挙動 | `p.cash < min_repay` → `"bankruptcy"` で `forced_liquidation()` ✅ |
| S1 での無効化 | `mandatory_repay_enabled: bool = False`（デフォルト）。`baseline_v1()` は未設定（=False）。`baseline_v1_s2()` のみ True。✅ |

### 3.3 仕様書との照合

- §2.2 の計算式: `ceil(利息計上後の借金残高 ÷ (残り返済回数 + k))` → **一致** ✅
- §2.2 の残り返済回数: `R1終了時 = 12, R2終了時 = 11, ..., R12終了時 = 1` → `num_rounds - round_num + 1` = `12 - 1 + 1 = 12` ✅
- §2.3 の計算例: 借入300万, 利率1.5%, k=0 → R1: `ceil(3,045,000 / 12) = 253,750` → テスト `test_spec_example_r1` で検証済み ✅
- §2.4 の返済不能処理: 破産として即時脱落 → v0.5 §1.6 の強制清算 → `forced_liquidation()` で処理 ✅
- §2.7 の R12 扱い: remaining=1, k=0 → debt 全額返済 → テスト `test_r12_full_repayment` で検証済み ✅
- §8.2 の処理順: 利息計上（Step 1）→ 強制返済（Step 2）→ 任意返済（Step 3） → 実装の `execute_finance()` で利息計上後、任意返済前に挿入 ✅

---

## 4. カードトレードの実装確認

### 4.1 `CardTradeProposal` モデル全フィールド（`engine/models.py` L168-197）

```python
class CardTradeProposal(BaseModel):
    trade_id: str               # トレードID
    proposer: str               # 提案者のプレイヤーID
    with_player: str            # 相手プレイヤーID
    give_card_rank: str         # 提案者が差し出すカードランク名
    receive_card_rank: str      # 提案者が受け取るカードランク名
    cash_amount: int = 0        # 現金移動（正=提案者→相手、負=相手→提案者）
    round_proposed: int         # 提案されたラウンド
    status: CardTradeStatus = CardTradeStatus.PROPOSED  # トレードステータス
```

### 4.2 アクション名

| アクション | type リテラル | モデル |
|---|---|---|
| 提案 | `"card_trade_propose"` | `CardTradeProposeAction` |
| 受諾 | `"card_trade_accept"` | `CardTradeAcceptAction` |
| **拒否** | **なし** | 拒否アクションは未実装。放置して PROPOSED のまま残る |

### 4.3 当事者2名への可視性（`engine/game.py` L903-917）

```python
# カードトレード提案（当事者のみ可視）
state["trades_pending"] = [
    {
        "trade_id": tp.trade_id,
        "proposer": tp.proposer,
        "with_player": tp.with_player,
        "give_card_rank": tp.give_card_rank,
        "receive_card_rank": tp.receive_card_rank,
        "cash_amount": tp.cash_amount,
        "round_proposed": tp.round_proposed,
    }
    for tp in self.trade_proposals
    if tp.status == CardTradeStatus.PROPOSED
    and for_player_id in (tp.proposer, tp.with_player)
]
```

✅ `for_player_id in (tp.proposer, tp.with_player)` — 提案者と相手の2名のみに可視。第三者には表示されない。テスト `test_party_sees_trade` / `test_non_party_does_not_see_trade` で検証済み。

### 4.4 受諾時の再検証ロジック（`engine/game.py` L455-470）

```python
# カード再検証（提案後にカードを使った可能性）
give_rank = CardRank[proposal.give_card_rank]
recv_rank = CardRank[proposal.receive_card_rank]
give_card = next((c for c in proposer_state.hand if c.rank == give_rank), None)
recv_card = next((c for c in accepter_state.hand if c.rank == recv_rank), None)
if give_card is None or recv_card is None:
    proposal.status = CardTradeStatus.EXPIRED
    return

# 現金再検証
if proposal.cash_amount > 0 and proposal.cash_amount > proposer_state.free_cash:
    proposal.status = CardTradeStatus.EXPIRED
    return
if proposal.cash_amount < 0 and abs(proposal.cash_amount) > accepter_state.free_cash:
    proposal.status = CardTradeStatus.EXPIRED
    return
```

✅ カードが手札から消えていた場合 → `EXPIRED` に遷移して return（交換せず）。
✅ 現金が不足していた場合 → 同様に `EXPIRED`。
✅ 条件未充足で脱落させない（§3.5: 「資金不足によるトレード失敗だけで脱落させてはならない」に準拠）。

### 4.5 アトミック性（`engine/game.py` L472-488）

```python
# アトミック実行: カード交換
proposer_state = player_ops.swap_card(proposer_state, give_card, recv_card)
accepter_state = player_ops.swap_card(accepter_state, recv_card, give_card)

# 現金移動
if proposal.cash_amount > 0:
    proposer_state = player_ops.pay(proposer_state, proposal.cash_amount)
    accepter_state = player_ops.receive(accepter_state, proposal.cash_amount)
elif proposal.cash_amount < 0:
    accepter_state = player_ops.pay(accepter_state, abs(proposal.cash_amount))
    proposer_state = player_ops.receive(proposer_state, abs(proposal.cash_amount))

self.players[proposal.proposer] = proposer_state
self.players[pid] = accepter_state
```

✅ カード交換と現金移動はすべてローカル変数（`proposer_state`, `accepter_state`）で処理し、最後にまとめて `self.players` に書き戻す。途中で例外が発生しても `self.players` は未更新のまま。さらに呼び出し側の `_execute_negotiation_action()` が `try/except` で包んでいる（L273-283: P2防御層）。片方だけ成立する経路は**ない**。

### 4.6 トレード回数制限

| パラメータ | GameConfig フィールド | 既定値 | 説明 |
|---|---|---|---|
| 1Rあたり上限 | `card_trade_max_per_round` | `1` | §3.6 準拠 |
| 最終R | `card_trade_last_round` | `11` | §3.6: R12不可 |
| 有効/無効 | `card_trade_enabled` | `False` | S2でTrue |

✅ すべて `GameConfig` のパラメータになっている。シミュレーションで変更可能。

**回数チェック箇所**: 提案時（L409）と受諾時（L444-447）の両方でチェック。受諾時は提案者・受諾者の両方のカウントを検証。

### 4.7 提案の有効期限・破棄条件

- **有効期限**: なし。PROPOSED のまま蓄積し続ける（正式契約と同じ扱い）
- **破棄条件**: 受諾時にカード・現金が不足していれば `EXPIRED` に遷移
- **明示的拒否アクション**: なし（放置で対応）

---

## 5. 既存テスト修正の確認

### 修正1: `test_fog_round_card_masked_in_reveal`

**修正前:**
```python
config = GameConfig.default_8_s2()
```

**修正後:**
```python
config = GameConfig.default_8().model_copy(update={"fog_rounds": [4, 8]})
```

**評価:** テストの意図は「霧ラウンドでカード情報がFOGになること」の検証。`default_8_s2()` が `fog_rounds=[]` に変わったため、明示的に `fog_rounds=[4,8]` を設定する形に変更。テストの意図は**変わっていない**。霧のラウンド機能自体は引き続きテストされている。 ✅

### 修正2: `test_non_fog_round_card_visible`

同じ修正パターン。`default_8_s2()` → `default_8().model_copy(update={"fog_rounds": [4, 8]})`。テストの意図（非霧ラウンドではカード公開）は**変わっていない**。 ✅

### 修正3: `test_s2_config_presets`

**修正前:**
```python
assert c.fog_rounds == [4, 8]
# （baseline_v1_s2 も同様）
```

**修正後:**
```python
assert c.fog_rounds == []  # v0.7 §10.2 で削除
assert c.mandatory_repay_enabled is True
assert c.mandatory_repay_k == 0
assert c.card_trade_enabled is True
# （baseline_v1_s2 も同様に追加）
```

**評価:** S2 プリセットの構成を検証するテスト。v0.7 でプリセットが変更されたことに合わせて期待値を更新し、新パラメータの検証も追加。テストの意図は「S2プリセットが正しく構成されること」であり、**変わっていない**。 ✅

---

## 6. 総合評価

### 仕様書との乖離

| 仕様書セクション | 実装状況 | 乖離 |
|---|---|---|
| §2 強制返済 | ✅ 実装済み | なし |
| §2.6 倍掛け預託金との関係 | ✅ 倍掛け預託金はCashとして扱わない既存実装 | なし |
| §3 カードトレード | ✅ 2アクション制で実装済み | なし |
| §3.5 現金支払いのFreeCash制限 | ✅ バリデーションで検証 | なし |
| §3.6 回数制限 | ✅ `card_trade_max_per_round=1` | なし |
| §3.7 情報公開 | ✅ 取引事実のみ公開（当事者間でカード・現金は見える） | なし |
| §7 個別財務通知 | **❌ 未実装** | v0.7 §7 で定義された個別財務通知（ラウンド開始時に破産リスクを本人に通知）は未実装 |
| §8.1 Settlement 11Step | ✅ 既存実装（倍掛けステップ含む） | なし |
| §8.2 Finance 4Step | ✅ 強制返済ステップ追加済み | なし |
| §10.2 霧のラウンド削除 | ✅ fog_rounds=[] | なし |

### 乖離: 個別財務通知（§7）

仕様書 v0.7 §7 では「各ラウンド開始時に本人のみに以下の情報を通知する」と定義されているが、engine に該当する実装がない。ただし今回の指示は「強制返済」「カードトレード」の2点に限定されているため、この未実装は指示の範囲外と判断できる。

### 1,000試合シミュレーションに進む上での懸念点

1. **Bot がカードトレードを使わない**: 既存の Bot 8種はすべて `card_trade_propose` / `card_trade_accept` を出さない（StubAgent は常に pass）。シミュレーションでカードトレードの効果を測定するには、トレードを行う Bot を追加するか、Bot にトレードロジックを組み込む必要がある。

2. **強制返済による大量破産の可能性**: k=0（完全均等）では、低借入額でも序盤から返済が発生する。1,000試合シミュレーションで序盤破産率を確認し、必要に応じて k を調整する必要がある。

3. **個別財務通知の未実装**: LLM 対戦時にはプレイヤーが破産リスクを正確に計算できないリスクがある。Bot シミュレーションでは影響しないが、LLM 本戦前には実装が必要。

4. **トレード提案の蓄積**: 有効期限なしで PROPOSED 提案が蓄積し続ける。12ラウンドの Bot シミュレーションでは問題にならないが、prompt 表示のトークン消費が気になる場合は上限を設けることを検討。

---

*本レポートはコード変更を伴わない検証報告である。*
