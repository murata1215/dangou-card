# 1ラウンドの思考順序・ターン構造の調査

調査日: 2026-08-14 08:11 JST

---

## 1. 1ラウンドのフェーズ構成

`engine/game.py` L136-148 の `run()` メソッドより:

```python
for round_num in range(1, self.config.num_rounds + 1):
    self.current_round = round_num
    alive_count = sum(1 for p in self.players.values() if p.is_alive)
    if alive_count == 0:
        break

    self._phase_market_open(round_num)    # Phase 1
    self._phase_negotiation(round_num)    # Phase 2
    self._phase_commit(round_num)         # Phase 3
    self._phase_settlement(round_num)     # Phase 4
    self._phase_finance(round_num)        # Phase 5
```

| Phase | 名前 | LLM/Bot が思考・行動 | システム処理のみ |
|---|---|---|---|
| 1 | Market Open | ❌ | ✅ 3市場と賞金を公開 |
| 2 | **Negotiation** | **✅** DM・broadcast・送金・返済・契約提案/署名・カードトレード・報奨・pass | — |
| 3 | **Commit** | **✅** 「市場+カード」を秘密提出 | Entry Fee 支払い |
| 4 | Settlement | 倍掛け選択のみ✅ | Reveal・勝敗・型B監査・型A執行・報奨・脱落 |
| 5 | Finance | ❌ | 利息・強制返済・任意返済・R12生還判定 |

---

## 2. 交渉フェーズの順序構造（最重要）

### 2.1 実装の引用（`engine/game.py` L199-270）

```python
def _phase_negotiation(self, round_num: int) -> None:
    """Phase 2: Negotiation（§5.1）
    1アクション×最大10巡、毎巡ランダム手番。
    passは枠を消費しない。全員連続パスで早期終了。"""
    self.pending_repayments = {}
    self._action_counts = {pid: 0 for pid in self.players}
    self._anon_broadcast_counts = {pid: 0 for pid in self.players}
    self._trade_counts = {pid: 0 for pid in self.players}
    self._round_messages = []

    alive_ids = [pid for pid, p in self.players.items() if p.is_alive]

    for turn in range(1, self.config.negotiation_max_turns + 1):
        # 毎巡ランダム手番（§5.1）
        turn_order = self.rng.shuffle_turn_order(alive_ids)
        all_passed = True

        for pid in turn_order:
            p = self.players[pid]
            if not p.is_alive:
                continue
            if self._action_counts.get(pid, 0) >= self.config.negotiation_max_actions:
                continue

            agent = self.agents[pid]
            visible_state = self._build_visible_state(round_num, for_player_id=pid)
            action = agent.negotiate(p, round_num, turn, visible_state)

            # 検証 → 実行（省略）
```

### 2.2 処理方式

**逐次処理（sequential）である。同時処理ではない。**

- 1人ずつ順番に `agent.negotiate()` を呼び出し、アクションを即時実行する
- `_build_visible_state()` は**各プレイヤーの思考直前に毎回構築**される（L229）
- したがって**後手のプレイヤーは先手のプレイヤーの行動結果を見た上で思考する**

### 2.3 順序の決定方法

```python
turn_order = self.rng.shuffle_turn_order(alive_ids)
```

`engine/rng.py` L30-44:
```python
def shuffle_turn_order(self, player_ids: list[str]) -> list[str]:
    """Negotiationの手番順をランダムシャッフルする（§5.1）
    毎巡ランダム手番。同一seedなら同じ順序が再現される。"""
    shuffled = list(player_ids)
    self._negotiation_rng.shuffle(shuffled)
    return shuffled
```

- **毎巡（毎ターン）ランダムシャッフル**される
- `_negotiation_rng` は専用のサブRNG（`random.Random` インスタンス）
- 同一seed → 同一順序が再現される（決定的）
- ラウンドをまたいでも同じRNGインスタンスが連続使用されるため、**固定順ではなく変動する**
- ただし `alive_ids` は `self.players.items()` の順序（辞書の挿入順 = P01→P08）が毎回渡される

### 2.4 可視状態の更新タイミング

```python
# L229: 各プレイヤーの思考直前に毎回構築
visible_state = self._build_visible_state(round_num, for_player_id=pid)
```

- `_build_visible_state()` は `self._round_messages`（DM・broadcast の蓄積リスト）を含む
- DM やbroadcast の実行時に `self._round_messages.append(msg_record)` される（L500-504）
- したがって**同一ターン内で、先に行動したプレイヤーの DM を後のプレイヤーが読める**

---

## 3. ターン数

| パラメータ | GameConfig フィールド | 既定値 |
|---|---|---|
| 最大巡数 | `negotiation_max_turns` | `10` |
| 1人あたり最大アクション数 | `negotiation_max_actions` | `10` |

- 全員が連続 pass した場合、早期終了（`all_passed` フラグ）
- 実質的に **最大10巡 × 最大10アクション/人**

---

## 4. DM（1対1のやりとり）

### 4.1 実装箇所

`engine/game.py` L496-508（`_execute_negotiation_action_inner` 内）:

```python
elif hasattr(action, 'message'):
    from engine.models import DmAction, BroadcastAction
    msg_record: dict = {
        "sender": pid, "type": action.type,
        "message": action.message, "turn": turn,
    }
    if isinstance(action, DmAction):
        msg_record["to"] = action.to
    self._round_messages.append(msg_record)
    self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
        "player_id": pid, "action": action.type,
        "success": True, "turn": turn,
    })
```

### 4.2 確認事項

| 項目 | 結果 |
|---|---|
| 1ラウンドあたりの DM 送信回数上限 | **`negotiation_max_actions` = 10 に含まれる**（DM 専用の上限は未設定） |
| フェーズ・タイミング | Negotiation フェイズのみ |
| 相手が読むタイミング | **同一ターン内**。`_round_messages` に即座に追加され、同じターンの後続プレイヤーの `_build_visible_state()` に含まれる |

### 4.3 DM の可視範囲

`llm/prompt_builder.py` の `build_negotiation_prompt()` ではメッセージを全件表示している（L191-202）:

```python
messages = visible_state.get("messages", [])
if messages:
    lines.append("\n## 今ラウンドのメッセージ")
    for msg in messages[-10:]:
        sender = msg.get("sender", "?")
        mtype = msg.get("type", "?")
        text = msg.get("message", "")[:200]
        if mtype == "dm":
            to = msg.get("to", "?")
            lines.append(f"  [{sender}→{to}] {text}")
```

**注意**: DM の `message` フィールド（本文テキスト）は `visible_state["messages"]` に含まれるが、prompt 表示では最新10件のみ（`messages[-10:]`）。また `_build_visible_state()` の `messages` は全件を返すが、**DM の本文が全プレイヤーに見えるかどうかは `build_negotiation_prompt` の実装次第**。

現状の実装では、**DM の本文は全プレイヤーの prompt に表示されている**（`[P01→P02] ○○` の形式で）。厳密には DM は当事者のみに見えるべきだが、現在の実装では全員に見えている。

> ※ これは v0.5 §8 の「DM は秘匿情報」に対する既知の実装上の差異だが、本調査のスコープ外。

---

## 5. 提案→受諾の同一ラウンド内完結性

### 5.1 正式契約（contract）

- 提案: `contract_propose` → `self.contracts` に PROPOSED 状態で追加
- 受諾: `contract_sign` → 提案中の契約に署名

**同一ラウンド内で完結可能。** 逐次処理のため:
1. ターン N で P01 が `contract_propose` → 契約が `self.contracts` に追加
2. ターン N の P01 以降、または ターン N+1 以降で P02 が `visible_state["contracts_pending"]` に契約を見る
3. P02 が `contract_sign` → 成立

**同一ターンでの提案+受諾**: 可能。先手が提案し、同一ターンの後手が受諾できる。ただし手番順がランダムなので、「P01 が先手、P02 が後手」になるかはターンごとのシャッフル次第。

### 5.2 カードトレード

同様の構造:
1. `card_trade_propose` → `self.trade_proposals` に PROPOSED 状態で追加
2. `card_trade_accept` → trade_id で検索し、条件再検証の上アトミック実行

**同一ラウンド内で完結可能。同一ターン内でも可能**（先手が提案、後手が受諾）。

### 5.3 タイミングの公平性

同一ターン内で先手が提案し後手が受諾できるため、**ターン内の手番順が提案→受諾の成立速度に影響する**。ただし手番順は毎ターンランダムシャッフルされるため、特定プレイヤーが常に有利/不利になることはない。

---

## 6. 順序による有利不利の痕跡

### 6.1 Bot シミュレーションデータ（1,000試合、8人版）

`logs/sim_20260809_170843/summary.csv`（8種 Bot × 1,000試合）:

| 座席 | 生存率 | 平均最終Cash |
|---|---|---|
| P01 | 31.3% | 1,059,913 |
| P02 | 30.8% | 1,011,217 |
| P03 | 34.5% | 1,186,082 |
| P04 | 32.6% | 1,080,396 |
| P05 | 33.5% | 1,104,039 |
| P06 | 34.6% | 1,172,939 |
| P07 | 31.5% | 1,053,813 |
| P08 | 32.9% | 1,101,953 |

**偏差**: 生存率は 30.8%〜34.6%（差 3.8pp）、平均最終Cash は 1,011,217〜1,186,082（差 17.2%）。

**ただしこのデータでは座席と Bot 種別が固定対応**（P01=HighPrizeHunter, P02=EmptyMarketHunter, P03=Betrayal, ...）であるため、座席の有利不利と Bot 戦略の優劣が分離できない。

**結論: 座席順による構造的有利不利の有無は、このデータからは判断不能。** ミラーマッチ（同一 Bot を全席に配置）のデータが必要だが、手元に座席別集計がないため未検証。

### 6.2 Season 1 LLM 本戦（1試合のみ）

trial_C_053055: 生還者は P02（Claude Sonnet 5）のみ。N=1 のため統計的判断は不可能。

---

## 7. 「早い者勝ち」実装の可否評価

### 7.1 現在のターン構造の整理

- 交渉フェイズは**逐次処理**（1人ずつ順に思考・即実行）
- 手番順は**毎巡ランダムシャッフル**
- 同一ターン内で先手の提案を後手が受諾可能
- 後手は先手の行動結果（DM含む）を見て思考する

### 7.2 「早い者勝ち」方式の評価

**想定**: P01 が同一内容のトレード提案を P02 と P03 の両方に送る。最初に受諾した1名のみ成立、残りは自動失効。

**現在の構造での実装可能性**:

✅ **実装可能。ただし「公平性」に若干の制約がある。**

- P02 と P03 のどちらが先に行動するかはターン内の手番順（ランダム）で決まる
- 先に手番が回ったプレイヤーが受諾可能。後手は「既に ACCEPTED になった提案」を見ることになる
- **手番順のランダム性により、長期的には公平**（特定プレイヤーが常に先手にはならない）
- ただし**同一ターン内では一方が構造的に有利**（そのターンたまたま先に手番が回った方）

### 7.3 代替方式の提案

#### 方式 A: 現状のまま早い者勝ち（推奨）

- 最初に `card_trade_accept` したプレイヤーのみ成立。残りの同一カードを対象とした提案は条件再検証で EXPIRED
- **実装コスト**: ゼロ（現在の実装がそのまま機能する。カード再検証で「提案者のカードが既にない」→ EXPIRED）
- **公平性**: ターン内の手番順（ランダム）に依存するが、巡ごとにシャッフルされるので長期的に公平
- **ゲーム性**: 「早く受諾しないと他に取られる」という緊張感を生む。DM で事前に合意形成→受諾のレースが発生

#### 方式 B: 同時受諾 → ランダム1名成立

- 同一ターンに複数の受諾があった場合、ランダムに1名を選んで成立
- **実装コスト**: 中。ターン内の即時実行を変更し、ターン末にバッチ処理する必要がある
- **公平性**: 完全に公平（同時受諾なら運で決まる）
- **ゲーム性**: 受諾の「早さ」が意味を持たなくなるため、レースの緊張感が薄れる

#### 方式 C: 提案者が複数受諾から選択

- 受諾は即座に「受諾意思表示」としてキューに入り、次の提案者の手番で「承認」する
- **実装コスト**: 高。3アクション制（提案→受諾意思→承認）となり複雑化
- **公平性**: 提案者に裁量権があり公平
- **ゲーム性**: 提案者有利。複雑だが交渉の駆け引きは深まる

#### 方式 D: 1人にしか提案できない制約

- 同一カードについて複数人への同時提案を禁止
- **実装コスト**: 低。提案時に「同一 give_card_rank で PROPOSED の提案が存在する場合は拒否」
- **公平性**: 完全に公平（早い者勝ち問題が発生しない）
- **ゲーム性**: 「誰に提案するか」の意思決定が重くなる。複数候補への打診ができないため交渉が遅くなる

### 7.4 推奨

**方式 A（現状のまま）が最も実装コストが低く、ゲーム性も自然。** 手番順のランダム性が長期的な公平性を担保しており、同一ターン内の微小な有利不利は許容範囲。

現在の実装は**既にこの方式で動作している**:
1. P01 が P02 へ FLUSH ⇄ HIGH_CARD のトレードを提案
2. P01 が P03 へ FLUSH ⇄ ONE_PAIR のトレードを提案
3. P02 が先に受諾 → FLUSH が P02 に渡る
4. P03 が受諾を試みる → カード再検証で「P01 が FLUSH を持っていない」→ EXPIRED

カード再検証ロジック（`engine/game.py` L455-462）がこの「早い者勝ち」の自動失効を既に実現している。

---

*本レポートはコード変更を伴わない調査報告である。*
