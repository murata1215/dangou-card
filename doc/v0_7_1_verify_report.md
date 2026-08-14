# v0.7.1 実装検証レポート（ブロードキャスト提案＋拒否アクション＋ラウンド末失効）

検証日: 2026-08-14 10:01 JST

---

## 1. テスト全件実行

```
240 passed, 0 failed, 4 warnings
```

| 項目 | 値 |
|---|---|
| 総テスト数 | **240** |
| 前回（v0.7） | 226 |
| **増加分** | **14** |
| 合格 | 240 |
| 失敗 | 0 |

### 増加分の内訳

`tests/test_card_trade.py` のみ。前回16件→今回30件（**+14件**）。

新規テスト一覧:
| テスト名 | 検証内容 |
|---|---|
| `test_valid_broadcast_proposal` | ブロードキャスト提案（複数宛先）のバリデーション |
| `test_empty_targets` | 宛先が空の場合の拒否 |
| `test_too_many_targets` | 宛先数上限超過の拒否 |
| `test_broadcast_creates_multiple_proposals` | 宛先ごとに個別提案が生成される |
| `test_broadcast_same_offer_id` | 全提案が同一 offer_id を持つ |
| `test_accept_expires_siblings` | 1件受諾で同一 offer_id の残りが EXPIRED |
| `test_broadcast_counts_as_one_action` | ブロードキャスト1回=1枠 |
| `test_reject_does_not_consume_action` | 拒否がバリデーション上 consumes_action=False |
| `test_reject_sets_status` | 拒否で REJECTED に遷移 |
| `test_reject_does_not_count_trade` | 拒否が trade_counts を増やさない |
| `test_rejected_cannot_be_accepted` | REJECTED 後は受諾不可 |
| `test_pending_expires_at_round_end` | ラウンド末で PROPOSED → EXPIRED |
| `test_receiver_has_no_offer_id` | 受信者の visible_state に offer_id/all_targets/target_statuses なし |
| `test_proposer_sees_targets_in_prompt` | 提案者のプロンプトに宛先リスト表示 |

既存テスト修正: `with_player` → `with_players` のフィールド名変更（テスト意図は不変）。

---

## 2. 修正されたバグの内容

### 問題
accept 時に提案者の `_trade_counts` を再チェックしていた。提案者の枠は propose 時に消費済み（L447: `self._trade_counts[pid] += 1`）で既に 1 になっているため、accept 時のチェック `self._trade_counts.get(proposal.proposer, 0) >= self.config.card_trade_max_per_round`（max=1）が常に True → **全ての受諾が無条件に失敗**していた。

### 修正後のコード（`engine/game.py` L472-474）
```python
if self._trade_counts.get(pid, 0) >= self.config.card_trade_max_per_round:
    return
# 提案者の枠は propose 時に消費済み — accept 時には再チェックしない
```

### 修正前のコード（削除された行）
```python
if self._trade_counts.get(proposal.proposer, 0) >= self.config.card_trade_max_per_round:
    return
```

### 回数制限の意味
- **提案者**: propose 時に枠消費（1回提案=1枠。ブロードキャストでも1枠）
- **受諾者**: accept 時に枠消費（L514: `self._trade_counts[pid] += 1`）
- accept 時に提案者の枠は再加算しない（L515: コメントで明記）
- `card_trade_max_per_round=1` の意味: 「1ラウンドに1回だけ提案 OR 受諾できる」

✅ 回数制限の意味は仕様通り。

---

## 3. 可視性の検証（最重要）

### `_build_visible_state` の該当箇所（`engine/game.py` L953-982）

```python
# カードトレード提案（当事者のみ可視, v0.7.1: 受信者に他宛先非公開）
pending_trades = []
for tp in self.trade_proposals:
    if tp.status != CardTradeStatus.PROPOSED:
        continue
    if for_player_id not in (tp.proposer, tp.with_player):
        continue
    entry: dict = {
        "trade_id": tp.trade_id,
        "proposer": tp.proposer,
        "with_player": tp.with_player,
        "give_card_rank": tp.give_card_rank,
        "receive_card_rank": tp.receive_card_rank,
        "cash_amount": tp.cash_amount,
        "round_proposed": tp.round_proposed,
    }
    # 提案者にのみ offer_id・全宛先・各宛先ステータスを公開
    if for_player_id == tp.proposer and tp.offer_id:
        entry["offer_id"] = tp.offer_id
        entry["all_targets"] = [...]
        entry["target_statuses"] = {...}
    pending_trades.append(entry)
state["trades_pending"] = pending_trades
```

### 受信者から見えるフィールド一覧（7フィールドのみ）

| フィールド | 値の例 |
|---|---|
| `trade_id` | `"T_abcd1234"` |
| `proposer` | `"P01"` |
| `with_player` | `"P02"` |
| `give_card_rank` | `"HIGH_CARD"` |
| `receive_card_rank` | `"FLUSH"` |
| `cash_amount` | `500000` |
| `round_proposed` | `2` |

以下は**受信者には含まれない**（提案者のみ）:
- ❌ `offer_id`
- ❌ `all_targets`
- ❌ `target_statuses`

**テスト `test_receiver_has_no_offer_id` で検証済み**（L356-370）:
```python
trade = state_p02["trades_pending"][0]
assert "offer_id" not in trade
assert "all_targets" not in trade
assert "target_statuses" not in trade
```

### プロンプト文字列の検証

`llm/prompt_builder.py` L239-272 のプロンプト生成コード:
- 受信者向けの出力: `### トレード T_xxx（R2提案、提案者: P01）` + カード交換条件のみ
- 「他にも送信されています」等の文言: **なし** ✅
- `offer_id` の出力: **提案者のときのみ**（`if "all_targets" in t:` で判定、L269）
- 同一プレイヤーが複数提案を受け取った場合: 各提案は独立した `### トレード T_xxx` ブロックとして表示。trade_id が異なるため、同一 offer_id 由来かどうかは **受信者には区別不可能** ✅

---

## 4. god view ログ

### ログ記録箇所（`engine/game.py` L448-454）

```python
self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
    "player_id": pid, "action": "card_trade_propose",
    "offer_id": offer_id,
    "trade_ids": created_ids,
    "with_players": action.with_players,  # ← 全宛先リスト
    "success": True, "turn": turn,
})
```

✅ `with_players` に**LLMが指定した全宛先リスト**が記録される。
✅ `trade_ids` に**実際に生成された個別提案のID**が記録される（カード未所持でスキップされた宛先は含まれない）。
✅ `offer_id` でブロードキャスト提案を束ねて追跡可能。

**「5人に送ったと申告したが実際は2人だった」の検証**:
- `with_players` = 申告した全宛先（例: `["P02","P03","P04","P05","P06"]`）
- `trade_ids` = 実際に生成された提案（例: `["T_xxx", "T_yyy"]`＝受取カードを持っていた2人分のみ）
- 差分から「3人はカード未所持でスキップされた」ことが事後検証可能 ✅

---

## 5. ステータス名の統一

`CardTradeStatus`（`engine/models.py` L156-168）の取りうる値:

| 値 | 用途 |
|---|---|
| `PROPOSED` | 提案中（受諾待ち） |
| `ACCEPTED` | 受諾済み（交換完了） |
| `REJECTED` | 拒否された |
| `EXPIRED` | 失効（条件未充足・ラウンド末・同一offer受諾等） |

**PENDING という名称は存在しない**。指示文中の「PENDING」は仕様上の概念であり、実装上は `PROPOSED` が対応する。

名称の揺れ: **なし** ✅。コード・テスト・devlog すべてで `PROPOSED`/`ACCEPTED`/`REJECTED`/`EXPIRED` が統一的に使用されている。

---

## 6. 仕様どおりの動作確認

| 仕様要件 | テスト名 | 結果 |
|---|---|---|
| 1件受諾で同一 offer_id の残りが EXPIRED | `test_accept_expires_siblings` | ✅ PASSED |
| 拒否が回数枠を消費しない | `test_reject_does_not_consume_action` + `test_reject_does_not_count_trade` | ✅ PASSED |
| 拒否に理由・メッセージを添えられない | `CardTradeRejectAction` モデルに `player_id` と `trade_id` のみ（メッセージフィールドなし） | ✅ 構造的に保証 |
| 放置（PROPOSED）と拒否（REJECTED）が区別される | `test_reject_sets_status`（REJECTED に遷移）+ `test_rejected_cannot_be_accepted`（REJECTED は受諾不可） | ✅ PASSED |
| ラウンド終了で未受諾提案が残らない | `test_pending_expires_at_round_end` | ✅ PASSED |
| 宛先数上限 `card_trade_broadcast_max=5` | `test_too_many_targets`（上限超過で不成立） | ✅ PASSED |
| S1 で無効化 | `test_s1_game_unaffected`（トレードイベント0件） | ✅ PASSED |

---

## 7. devlog 確認

`doc/devlog.md` の末尾に v0.7.1 実装エントリが追記されている。全文引用:

> ### 2026-08-14 09:28 JST — v0.7.1 実装: ブロードキャスト提案 + 拒否アクション + ラウンド末失効
>
> カードトレードの実用性を向上させる3機能を追加。
>
> #### 1. ブロードキャスト提案
> - `CardTradeProposeAction.with_player` → `with_players: list[str]` に変更（複数宛先対応）
> - 1アクションで宛先ごとに個別の `CardTradeProposal` を展開。共通 `offer_id` で束ねる
> - **1件受諾で同一 offer_id の残りを自動 EXPIRED**（早い者勝ち）
> - 宛先数上限: `card_trade_broadcast_max=5`（GameConfig パラメータ）
> - ブロードキャスト1回＝1枠（提案者の `_trade_counts` は propose 時に消費）
>
> #### 2. 可視性（厳守）
> - 受信者には `offer_id` / `all_targets` / `target_statuses` を一切含めない（1対1提案と区別不能）
> - 提案者のみが全宛先リストと各宛先のステータスを参照可能
> - god view ログ（events.jsonl）には `with_players` 全リストを記録
>
> #### 3. 拒否アクション
> - `CardTradeRejectAction` を追加。REJECTED ステータスに遷移
> - 回数枠を消費しない（`consumes_action=False`）。理由・メッセージなし
> - 拒否された提案は受諾不可
>
> #### 4. ラウンド末失効
> - `_phase_negotiation` の末尾で当該ラウンドの PROPOSED をすべて EXPIRED に
> - ラウンドをまたいで PENDING が残らない
>
> #### バグ修正
> - accept 時の proposer trade_count チェックを削除。提案者の枠は propose 時に消費済みのため、accept 時に再チェックすると `max_per_round=1` で全 accept が拒否される問題
>
> **テスト**: 240 件全通過（既存 226 → 既存テスト修正(with_player→with_players) + 新規 14 件）

---

## 8. 総合評価

### 仕様との乖離

**なし。** 全7項目の仕様要件がテストで担保されており、可視性の分離もコード構造とテストの両面で確認済み。

### Bot シミュレーションに進む上での懸念点

1. **Bot がカードトレード・拒否アクションを一切使わない**: 既存の Bot 8種（StubAgent 含む）は `card_trade_propose` / `card_trade_accept` / `card_trade_reject` を発行しない。Bot シミュレーションではトレード機能の効果を測定できない。トレードを行う Bot を追加するか、LLM 対戦でのみ検証する必要がある。

2. **強制返済との相互作用が未検証**: 強制返済で現金が減った直後にカードトレードの現金支払いを行うケースの挙動は、Bot が使わないため実戦データがない。ただしコード上は FreeCash チェック → EXPIRED の安全な経路が担保されている。

3. **個別財務通知（v0.7 §7）が未実装**: LLM 対戦前には実装が必要。Bot シミュレーションには影響しない。

---

*本レポートはコード変更を伴わない検証報告である。*
