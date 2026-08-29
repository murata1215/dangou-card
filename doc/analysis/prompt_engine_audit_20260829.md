# プロンプト修正前のエンジン実挙動監査（2026-08-29）

## この文書について

- 目的: `doc/prompt_dump/actual_prompts_20260829.md`（現行プロンプトの実文面）と仕様書
  v0.5 / v0.7 を突き合わせたところ、プロンプト文面だけでは真偽を確定できないエンジン挙動が
  10項目残った。次タスク（プロンプト文言の一括修正）で「仕様書には書いてあるがエンジンは違う」
  文言を書き足すと LLM に誤情報を教えることになるため、本監査で**実装コードを一次情報として**
  10項目を確定し、修正作業の前提を固める。
- 一次情報: `engine/` 配下の実装コード（`llm/prompt_builder.py` の記述内容や仕様書ではない）。
  すべて `path/to/file.py:行番号` で引用する。
- 調査時点: HEAD（2026-08-29時点のワーキングツリー）。コード変更・テスト実行・API呼び出しは
  一切行っていない、完全な読み取り専用調査。
- 「一致」列の凡例: ○=現行プロンプトの記述と一致 / △=部分一致（重要な例外や条件が未記載）/
  ×=現行プロンプトの記述または沈黙が実装と食い違う。

---

## 結論サマリ表

| # | 項目 | 結論（1行） | プロンプト文面との一致 |
|---|---|---|---|
| 1 | 発行料の課金タイミング | **提案(propose)実行時に提案者から10万即時徴収**。署名成立時ではない。返還なし | ○ |
| 2 | 署名待ち提案の寿命 | **失効しない**（PROPOSED のまま全12R残る）。失効通知も無い | × |
| 3 | round_num 検証 | **検証ゼロ**。過去R/範囲外Rを指定しても受理され、監査されないまま**契約を永久に解除不能にする** | × |
| 4 | 高騰の少人数時判定 | `alive<=3 → 全員参加のみ発動` / `alive>3 → 参加 > 生存/2`（float・strict >）。**生存1人なら参加1人で発動** | ○ |
| 5 | contract_cancel 可否 | `status=ACTIVE` かつ**全義務が**未履行・未失効・`round_num >= 現在R`。同意は**Rを跨いで永続** | △ |
| 6 | 公開情報の描画 | **仕様§6.4「倍掛けは全員へ公開」が negotiation/commit/reflection で未実装**。`initial_loans`・トレード成立事実も全プロンプト非表示 | × |
| 7 | terms の details 検証 | `type_b_card` の card_rank のみ厳格（enum必須・不正は提案ごと不成立）。**market_id / amount / obligor は無検証** | △ |
| 8 | card_trade の実挙動 | `cash_amount` 負値可（**受諾側が払う**）。宛先上限5は**検証あり**。**ラウンド末に自動失効**。拒否/失効の提案者通知は無し | △ |
| 9 | 強制返済不能 | 不足で `bankruptcy`（公示「破産」）。**R12も Step2 の強制最低返済が先に走るため、債務完済不能は「条件未達」ではなく「破産」** | × |
| 10 | Settlement 実処理順 | 実装は**8ステップ**。高騰判定は Step2 に内包、**倍掛けは Settlement の外**。型Aスナップショットは**倍掛け預託の減算より前** | △ |

---

## 1. 正式契約の発行料（10万円）の課金タイミング

### 結論
`contract_propose` の**実行時**に提案者から `config.contract_fee` を即時徴収。署名成立時ではなく、
未署名のまま残っても**返還されない**。負担は提案者のみで分割はない。

### 根拠
- `engine/config.py:70-71` — `contract_fee: int = 100_000`（S2実測値も100000）
- `engine/game.py:571-584` — `ContractProposeAction` 分岐の冒頭で徴収が `create_contract` より前:
  ```python
  # 発行料を徴収
  p = player_ops.pay(p, self.config.contract_fee)
  self.players[pid] = p
  # 契約オブジェクト生成
  all_parties = [pid] + [pp for pp in action.with_players if pp != pid]
  contract = contract_ops.create_contract(...)
  ```
- `engine/contracts.py:88-119` `sign_contract()` — 手数料に関する記述はゼロ。返還パスも存在しない。
- 現金不足時: `engine/actions.py:110-114`
  ```python
  if player.cash < config.contract_fee:
      return ActionResult(False, "Insufficient cash for contract fee")
  ```
  → **validate 段階で不成立**。`ActionResult.consumes_action` は既定 True のため
  `engine/game.py` 側の `if result.consumes_action:` により**アクション枠は消費される**。
  例外は投げず `_record_action_failure` 経由で `my_failed_actions` に記録される。
- FreeCash 非適用（`player.cash` そのもので判定）。
- 相手が脱落済みの場合は `engine/actions.py:119-124` で**資金チェックの後**に弾かれる
  （成立しえない契約に10万を払わせないための順序、と実装コメントに明記）。

### 一致判定
○（system prompt「発行料10万（提案者負担）」と一致）

### 補足
「提案しただけで即座に費消され、署名されなくても戻らない」点はプロンプトに明記が無い。

### 関連テスト
`tests/test_acceptance.py::test_15`（`engine/actions.py:117-118` のコメントが参照）

---

## 2. 署名待ち提案の寿命

### 結論
**失効しない**。`PROPOSED` のまま最終Rまで残り続け、失効通知も存在しない。

### 根拠
- `engine/game.py:477-479` — ラウンド末の自動失効は**カードトレードだけ**に実装されている:
  ```python
  # ラウンド末: 未受諾のトレード提案を自動失効（v0.7.1）
  for tp in self.trade_proposals:
      if tp.status == CardTradeStatus.PROPOSED and tp.round_proposed == round_num:
          tp.status = CardTradeStatus.EXPIRED
  ```
  契約に対する同等の処理は `engine/game.py` 全体に存在しない
  （`ContractStatus` を書き換えるのは `sign_contract`→ACTIVE と `request_cancel`→CANCELLED のみ）。
- `engine/contracts.py:110-114` — PROPOSED から遷移する唯一のパス（全当事者署名→ACTIVE）。
  `ContractStatus.EXPIRED` に相当する遷移コードは無い。
- 通知: `engine/game.py:619-621` の `notice["kind"]` は `"cancel_completed"` / `"cancel_requested"` の
  2種のみ。提案の失効・拒否に対応する kind は無い。
- 当事者が脱落した場合: 契約は PROPOSED のまま。`elim_ops.expire_obligations_for_player()`
  （`engine/settlement.py:169-174`）は**義務**の `is_expired` を立てるだけで契約 status は不変。
  さらに `contract_cancel` は `status != ACTIVE` を弾く（`engine/contracts.py:385-386`）ため、
  **PROPOSED の死に契約は解除もできない**。
- 可視性: `engine/game.py:1993-1995` — `contracts_pending` は `status == PROPOSED` かつ当事者、
  という条件のみで**ラウンド絞り込みが無い**。よって R3 の提案が R12 まで毎ラウンド提示され続ける。

### 一致判定
×（プロンプトは寿命に一切言及せず、LLMは「そのうち消える」と誤解しうる）

### 関連テスト
`tests/test_prompt_pending_contracts.py` / `tests/test_my_contracts.py`
（PROPOSED は `my_contracts` から除外され `contracts_pending` にのみ載ることは検証済み。
失効に関するテストは存在しない）

---

## 3. `contract_propose` の round_num 検証

### 結論
**検証は一切無い**。任意の整数が受理される。

### 根拠
- `engine/contracts.py:41-75` `create_contract()` — `round_num=term["round_num"]`（:72）として
  そのまま格納。範囲チェックは無い。検証しているのは `type_b_card` の `card_rank` だけ（:58-64）。
- `engine/actions.py:110-125` — `ContractProposeAction` の validate に round_num の検証は無い。

### 挙動の帰結

| 指定 | 監査/執行されるか | 副作用 |
|---|---|---|
| `round_num < 現在R`（過去R） | **されない**。`get_active_type_b_obligations`（`engine/contracts.py:122-150`）/ `get_active_type_a_obligations` とも `ob.round_num == round_num` の**完全一致**でのみ拾う | **`can_cancel_contract` の `ob.round_num < round_num` に引っかかり（`engine/contracts.py:392-396`）、その契約は成立した瞬間から永久に解除不能**。発行料10万も戻らない |
| `round_num > num_rounds`（13以上） | されない（そのRが来ない） | 解除は可能（`>= 現在R` のため）。ただし永遠に空義務として残る |
| `round_num == 現在R` | **される**。型Bは今RのSettlement Step3、型Aは同Step5 | 正常 |

- 同R内の順序保証: `engine/game.py:254-267` のラウンドループが
  `market_open → negotiation → commit → settlement → finance → reflection` の順。
  Negotiation で作った `round_num == 現在R` の義務は、同じRの Commit/Settlement で確実に判定される。
- 型Bは `engine/settlement.py:156-165`（Step3）、型Aは `engine/settlement.py:195-235`（Step5）で執行。

### 一致判定
×（「過去Rを指定すると解除不能な死に契約になる」という重大な罠がプロンプトに無い）

### 関連テスト
該当なし（round_num 境界のテストは未実装）

---

## 4. 市場高騰の少人数時判定

### 結論
`alive_count <= 3` なら**全員参加のみ**発動、それ以外は `参加者 > 生存者/2`（**float除算・strict >**）。
最小参加人数の下限ガードは無い。

### 根拠
- `engine/settlement.py:23-40`
  ```python
  def _should_surge(num_participants: int, alive_count: int, config: GameConfig) -> bool:
      if not config.surge_enabled:
          return False
      if alive_count <= config.surge_full_participation_max_alive:
          return num_participants == alive_count
      return num_participants > alive_count / 2
  ```
- `engine/config.py:86-90` — `surge_enabled` / `surge_full_participation_max_alive`。
  S2実測値: `surge_enabled=True`, `surge_full_participation_max_alive=3`
  （`GameConfig.baseline_v1_s2(12)` で確認）。
- 判定に使う生存者数は `engine/settlement.py:114-118` — **当該Settlementで脱落確定した者を除いた**生存数:
  ```python
  alive_count = sum(1 for p in players.values()
                    if p.is_alive and p.player_id not in eliminated_this_settlement)
  ```
- 倍率と適用先: `engine/market.py:100-102` — `total_pool *= 2`。
  `total_pool = 基本賞金 + 繰越 + Entry Fee合計` の**全部**に掛かる（基本賞金のみではない）。

### 発動表（S2設定・`_should_surge` を全参加人数で実評価）

| 生存者数 | 高騰する参加人数 | 判定式 |
|---|---|---|
| 1 | **1**（＝空き巣1人でも発動） | `n == 1` |
| 2 | **2**（全員） | `n == 2` |
| 3 | **3**（全員） | `n == 3` |
| 4 | 3, 4 | `n > 2.0` |
| 5 | 3, 4, 5 | `n > 2.5` |
| 12 | 7〜12 | `n > 6.0` |

### 一致判定
○（`llm/prompt_builder.py:944` の「同一市場への参加者数が生存者数の半分を超えると…2倍
（3人以下の場合は全員参加のみ発動）」は正しい）

### 補足
「生存1人＝参加1人で高騰」という極端ケースはプロンプトから読み取れない。

### 関連テスト
`tests/test_s2_rules.py::TestMarketSurge` / `TestSurgeFullParticipation`

---

## 5. `contract_cancel` の可否条件

### 結論
`status==ACTIVE` かつ、**全義務**が「未履行 かつ 未失効 かつ `round_num >= 現在R`」のときのみ解除可能。
解除同意は**ラウンドを跨いで永続**する。

### 根拠
`engine/contracts.py:385-397`
```python
if contract.status != ContractStatus.ACTIVE:
    return False, f"Contract {contract.contract_id} is not active"
for ob in contract.obligations:
    if ob.is_fulfilled:  return False, "... has a fulfilled obligation"
    if ob.is_expired:    return False, "... has an expired obligation"
    if ob.round_num < round_num:
        return False, f"... has an audited obligation (R{ob.round_num})"
```
- "監査済み" の判定は**`ob.round_num < round_num` という導出**（永続フラグを持たない）。
  同関数の docstring（`engine/contracts.py:370-376`）が明言:
  「型B義務は正常履行でも `is_fulfilled` が立たない（`audit_type_b` は違反者のみ返す）ため、
  `round_num <` も見る必要がある」。
  → **`type_b_no_market` が期限を非違反で通過した場合も「監査済み」扱いになり、解除を恒久的にブロックする**。
- `round_num == 現在R` の義務はブロックしない（Negotiation は Commit/Settlement より前なので未判定）。
- 呼び出し元: `engine/actions.py:131-152`。**提案時と最終同意時の両方**で `validate_action` が走るため
  再チェックが効く。

### ダンプのパターンB `C_4A7F21` は実装上ありうるか
**YES**。R5型B不参加（past）+ R7型B（due）+ R9型A（upcoming）を持ちつつ `cancel_requested_by=[P03]`
の状態は次の経路で到達する: R5以前の Negotiation で P03 が解除同意（当時R5義務は
`round_num >= 現在R` なのでOK）→ P07 が同意しないまま R5 Settlement を通過 → R7 時点では
`5 < 7` で解除不能。`cancel_requested_by` は残るので、「同意1/2 だが永久に解除できない契約」が
正当に成立する。

### 同意の持ち越し
**Rを跨いで永続**。`engine/contracts.py:434` が
`new_requested = list(contract.cancel_requested_by) + [requester]` と**追記のみ**で、
ラウンド境界でのリセット処理はコードベースに存在しない。成立条件は「**生存する**全当事者の同意」
（`engine/contracts.py:435-437`、脱落者の同意は不要）。

### 一致判定
△（「一度でも履行/違反した契約は解除不可」は正しいが、**「期限を過ぎた義務が1つでもあれば、
履行していても永久に解除不可」**がプロンプトに無い）

### 関連テスト
`tests/test_contract_cancel.py::TestCanCancelContract` /
`TestRequestCancel::test_two_party_first_consent_stays_active` / `TestReCheckAtFinalConsent`

---

## 6. 公開情報の描画マトリクス

`_build_visible_state()`（`engine/game.py:1890-2120`）のキーと、4ビルダーが**実際に文字列へ
落としているか**の対応。

| 項目 | `_build_visible_state()` のキー | negotiation | commit | double_up | reflection |
|---|---|---|---|---|---|
| 初期借入額 | `initial_loans` | 非表示 | 非表示 | 非表示 | 非表示 |
| 総賞金予算 | キー無し（市場ごとの `prize_pool` のみ） | 非表示 | 非表示 | 非表示 | 非表示 |
| 使用済み全カード（プレイヤー別） | `used_cards` | 非表示 | 常時（`llm/prompt_builder.py:1343-1348`） | 非表示 | 非表示 |
| カードトレード**成立**事実（当事者名） | キー無し（`trades_pending` は PROPOSED のみ） | 非表示 | 非表示 | 非表示 | 非表示 |
| 市場高騰の発生 | `last_round_results[*].surged` | 条件付き（前R有り、`:1045`→`:493`） | 条件付き（`:1284`→`:493`） | 非表示 | 条件付き（`:1432`→`:493`） |
| 倍掛け: 自分の預託中 | `double_ups`（自分分） | 常時（`:1060-1063`） | 常時（`:1312-1314`） | 常時（`:1824-1828`） | 常時（`:1484-1486`） |
| 倍掛け: 他者の預託中 | `double_ups`（他者分） | **非表示** | **非表示** | 条件付き（他者預託1件以上、`:1884-1893`） | **非表示** |
| 倍掛け: 前Rの成功/失敗 | キー無し（`resolved` は visible_state から除外） | 非表示 | 非表示 | 非表示 | 非表示 |
| 契約の存在と当事者名 | `contracts_public` | 非表示 | 非表示 | 非表示 | 条件付き（1件以上、`:1452-1463`） |
| 公開報奨 | `bounties_public` | 条件付き（`is_active` が1件以上、`:1166-1181`） | 非表示 | 非表示 | 非表示 |
| 脱落者と理由種別 | `eliminated_players` | 条件付き（1名以上、`:1095`→`:546-572`） | 非表示 | 非表示 | 条件付き（`:1429`→`:546-572`） |

### 特記事項

1. **倍掛けの他者預託**: `double_ups` キーは `engine/game.py:1965-1969` で**全プレイヤー分**が入る
   （`{"player_id", "deposit", "success_round"}`、`resolved` 済みは除外）。しかし
   `llm/prompt_builder.py` で他者分を読むのは`:1884-1893` の1箇所だけ（`build_double_up_prompt` 内）。
   `:1061` / `:1312` / `:1484` はいずれも `if du.get("player_id") == player_state.player_id` で
   自分に絞っている。
   → **`build_negotiation_prompt` は他者の倍掛け預託を一切描画しない**。
   `doc/prompt_dump/actual_prompts_20260829.md` パターンBのメタ情報にある
   「倍掛け預託ブロック: 発火（P05の他者預託）」は**誤り**（本文に該当ブロックが無いのが正しい挙動）。
   さらに `build_double_up_prompt` は**そのRの勝者にしか送られない**ため、仕様 v0.7 §6.4
   「倍掛けの選択事実と預託額は全プレイヤーへ公開」は**交渉フェイズにおいて実質未実装**
   （§6.7 の設計意図「欲を公開して交渉の議題にする」が機能していない）。

2. **初期借入額**: 仕様 v0.7 §10.1「借入額は公開情報」だが、`initial_loans` を読むコードは
   `llm/prompt_builder.py` に**1箇所も存在しない**（grep 0件）。

3. **カードトレードの成立事実**: `card_trade_accept` は `engine/game.py:838-843` でログには残るが、
   `_round_messages` にも `visible_state` にも積まれない。`trades_pending` は
   `status == PROPOSED` の当事者限定（`engine/game.py:2092-2094`）。→ 第三者は成立を知りえない。

4. **脱落理由のラベル**: `llm/prompt_builder.py:558-559`
   `{"contract_violation": "契約違反", "bankruptcy": "破産", "condition_not_met": "条件未達"}`。
   実装上の脱落理由文字列はこの3種で全部（→ 項目9参照）。

### 一致判定
×（上記1〜3の3項目が仕様と乖離）

### 関連テスト
`tests/test_prompt_market_info.py`（`base_prize`/`carryover`/`surged`/AUTO COMMIT表示）、
`tests/test_dead_target_and_feedback.py`（脱落者ブロック）

---

## 7. 契約 terms の details バリデーション

### 結論
厳格に検証されるのは `type_b_card` の `card_rank` と `ob_type` の enum 変換だけ。他は全て無検証。

### 根拠
`engine/contracts.py:41-75`

| 対象 | 検証 | 不正時の挙動 |
|---|---|---|
| `type_b_card` の details キー名 | `card_rank` が本命。`card` / `rank` は自動正規化（:48-52） | 3つとも無ければ次段で ValueError |
| `card_rank` の値 | `CardRank.__members__` 完全一致（:58-64）。**大文字 enum 名のみ**（`HIGH_CARD`〜`ROYAL_FLUSH`）。小文字・数値・日本語名は不可 | `ValueError` → `engine/game.py:525-538` のP2防御層が捕捉 → **アクション不成立**（`consumed=True`、理由「実行時エラーにより不成立」）。**発行料10万は徴収済みで戻らない**（:574 が `create_contract` より前） |
| `market_id`（`type_b_market` / `type_b_no_market`） | **無検証**。存在しない市場IDでも受理 | 監査時（`engine/contracts.py:185-189`）は単に一致しないだけ。`type_b_market` なら**必ず違反→即脱落**、`type_b_no_market` なら**必ず非違反** |
| `amount`（`type_a_payment`） | **無検証**（負値・0・文字列すべて受理） | 実行時 `ob.details.get("amount", 0)`。負値は**逆向き送金**として成立しうる。文字列は算術で例外 |
| `obligor` | **`parties` との突合なし**（:69） | 当事者でないIDを書いても受理される。監査/執行時に該当者不在で素通り |
| `ob_type` | `ObligationType(term["ob_type"])`（:71）で enum 変換 → 未知値は `ValueError` | P2防御層で不成立（発行料は消費済み） |
| `counterparty` / `round_num` | 無検証 | 項目3参照 |

### 一致判定
△（`card_rank` が厳格である旨はプロンプトに反映済み。**不正 terms でも発行料10万が戻らない**点、
`market_id` 無検証で「必ず違反する契約」が作れる点は未記載）

### 関連テスト
`card_rank` 検証の直接テストは見当たらず（`tests/test_contract_cancel.py` は正当値で構築するのみ）

---

## 8. `card_trade_propose` の実挙動

### 結論
`cash_amount` は**符号で支払方向を表す**（正＝提案者が払う／負＝受諾者が払う）。
宛先上限5は検証あり。提案は**ラウンド末に自動失効**。拒否・失効の**提案者通知は無い**。

### 根拠
- パラメータ: `with_players` / `give_card` / `receive_card` / `cash_amount`
  （`CardTradeProposeAction`。パーサは `with` も受ける）。
- 検証: `engine/actions.py:193-220`
  ```python
  if len(action.with_players) > config.card_trade_broadcast_max:
      return ActionResult(False, f"Too many targets: ...")
  ...
  if action.cash_amount > 0 and action.cash_amount > player.free_cash:
      return ActionResult(False, "Insufficient free cash for trade payment")
  ```
  → `card_trade_broadcast_max`（S2実測5）は確かに強制されている。
  ただし FreeCash チェックは `cash_amount > 0`（提案者が払う側）のときだけ。**負値は提案時に無検査**。
- 受諾時の再検証と執行: `engine/game.py:805-823`
  ```python
  if proposal.cash_amount > 0 and proposal.cash_amount > proposer_state.free_cash:  # → EXPIRED
  if proposal.cash_amount < 0 and abs(...) > accepter_state.free_cash:              # → EXPIRED
  ...
  if proposal.cash_amount > 0:   # proposer→accepter へ支払い
  elif proposal.cash_amount < 0: # accepter→proposer へ支払い
  ```
  → **負値のときのみ受諾者の FreeCash が問われる**。
- 描画の向き: `llm/prompt_builder.py:1197-1212`
  ```python
  if cash > 0:  f"{proposer}の {give} + {cash//10000}万円 ⇄ {with_player}の {receive}"
  elif cash < 0: f"{proposer}の {give} ⇄ {with_player}の {receive} + {abs(cash)//10000}万円"
  ```
  → **「＋N万円」が付いている側が払う**。したがって
  **`P02の FLUSH ⇄ P07の ROYAL_FLUSH ＋60万円` は「P07（受諾者）が60万円を払う」**
  （`cash_amount < 0` のケース）。
  `doc/prompt_dump/actual_prompts_20260829.md` パターンBの説明「P02 が 60万円を払う」は**誤り**。
- 寿命: `engine/game.py:477-479` — Negotiation フェイズ終了時（＝ラウンド末）に、
  そのRに提案された PROPOSED をすべて EXPIRED。前Rの提案は残らない。
- 先着受諾と残りの自動失効: `engine/game.py:831-837` — 同一 `offer_id` の他提案を EXPIRED に。
  先着順で確定。
- 通知: `card_trade_reject`（`engine/game.py:850-857`）も上記の自動失効も**提案者向けの通知・
  `my_failed_actions` 追加を行わない**。提案者は `trades_pending` の `target_statuses`
  （`engine/game.py:2107-2111`、提案者にのみ公開）でステータスを見るしかないが、
  その `trades_pending` 自体が `PROPOSED` のものしか載らないため**失効した瞬間に画面から消える**
  （拒否されたのか期限切れなのか判別できない）。
- 他の上限: `card_trade_max_per_round`（受諾側の回数）、`card_trade_last_round`（R12不可）を
  `engine/game.py:784-787` で検査。

### 一致判定
△（描画の向きは実装と整合。**寿命がR末**・**拒否/失効の通知が無い**点が未記載。
`llm/prompt_builder.py:938` の「最大5人へ同時提案でき、最初に受諾した相手と即時成立」は正しい）

### 関連テスト
`tests/test_card_trade.py`（`TestProposalCreation` / `TestProposalAcceptance` /
`TestPromptRendering`）、`tests/test_response_parser_card_trade.py`

---

## 9. 強制返済不能時の処理

### 結論
不足なら `"bankruptcy"`（公示ラベル「破産」）で強制清算。**R12でも Step2 の強制最低返済が先に走る
ため、債務を完済できないケースは「条件未達」ではなく「破産」になる**。

### 根拠
- 最低返済額: `engine/player.py:177-195`
  `compute_mandatory_repayment = ceil(debt_balance / (remaining_rounds + k))`、
  `remaining = config.num_rounds - round_num + 1`。S2は `mandatory_repay_k = 0`。
  → **R12 では divisor = 1 なので「残債全額」が最低返済額**。
- 不能時: `engine/finance.py:62-83`
  ```python
  if p.cash < min_repay:
      logger.log("MANDATORY_REPAY_FAILED", ...)
      p, contracts, record = elim_ops.forced_liquidation(p, "bankruptcy", round_num, contracts)
  ```
  → Step2 は `if config.mandatory_repay_enabled:` の中で、**R12を除外していない**（:62）。
- R12 の生還判定は Step2 の**後**: `engine/finance.py:116-173`。
  Step2 を通過した時点で残債は必ず 0 になっているため、`condition_not_met` が実際に発火するのは
  **`cash < survival_cash`（200万円）のケースのみ**。

### 脱落理由の全種別（3つ）

| 文字列 | 公示ラベル | 発生箇所 |
|---|---|---|
| `contract_violation` | 契約違反 | `engine/settlement.py:165`（型B違反・Step3）、`:232`（型A履行不能・Step5）、`engine/game.py:982`（AUTO COMMIT失敗） |
| `bankruptcy` | 破産 | `engine/finance.py:80`（強制最低返済不能）、`engine/game.py:904`（Entry Fee 支払不能） |
| `condition_not_met` | 条件未達 | `engine/finance.py:146` / `:170`（R12 生還判定） |

ラベル対応表は `llm/prompt_builder.py:558-559`。

### 一致判定
×（プロンプトは R12 を「債務0＋現金200万で生還」としか説明しておらず、
**「R12 に残債を払えない＝破産で先に落ちる」**という分岐が無い）

### 関連テスト
`tests/test_mandatory_repay.py::TestComputeMandatoryRepayment` /
`TestMandatoryRepayFinance::test_bankruptcy_on_insufficient_cash`

---

## 10. Settlement の実処理順

### 結論
`execute_settlement()` は**8ステップ**。高騰判定は Step2 に内包。**倍掛けは Settlement の外**
（`_phase_settlement` が `execute_settlement` の**後**に `_process_double_up` を呼ぶ）。
型Aのスナップショットは**倍掛け預託の減算より前**に取得される。

### 実装の順序

| 実装Step | 内容 | 位置 |
|---|---|---|
| 1 | Reveal（参加者・使用カード公開） | `engine/settlement.py:90-102` |
| 2 | Market Settlement（**高騰判定→Entry Fee加算→勝敗→賞金支払い→カード消滅→繰越確定**） | `:105-153`（高騰は`:114-123`） |
| 3 | 型B監査 → 違反者を `eliminated_this_settlement` へ | `:156-175` |
| 4 | 型A判定用スナップショット（`cash` / `free_cash`） | `:178-192` |
| 5 | 型A Atomic執行 → 履行不能者を脱落へ | `:195-235` |
| 6 | 公開報奨の判定・支払い | `:238-281` |
| 7 | 脱落公示 | `:284-294` |
| 8 | 脱落時強制清算 | `:297-306` |
| （外） | 倍掛け: 前Rの預託を解決 → 今Rの勝者に選択提示 → 預託を現金から減算 | `engine/game.py:1076-1078` → `_process_double_up`（Step1 `:1168-`／Step2 `:1226-1274`） |
| （外） | Finance（利息→強制最低返済→任意返済 / R12生還判定） | `engine/game.py:264` → `engine/finance.py` |

### v0.7 §8.1 の11ステップ説との差分
- 「高騰判定」は独立ステップではなく Step2 の内側
- 「倍掛け選択」「前R倍掛け判定」は Settlement の外（直後の別処理）
- 「強制清算」は Settlement Step8 に在るが、「強制返済」（Finance）はさらにその後の別フェイズ
- 実質の順序自体は矛盾しない（型B監査→スナップショット→型A→報奨→公示→清算 は一致）

### スナップショットと倍掛け預託の前後関係 — 確定
- スナップショット取得: `engine/settlement.py:178-192`（`execute_settlement` 内、Step4）
- 預託の現金減算: `engine/game.py:1260` `p = player_ops.pay(p, eligible_prize)`
  （`_process_double_up` Step2）
- 呼び出し順: `engine/game.py:1047-1078` — `execute_settlement(...)` が先、
  `_process_double_up(...)` が後。
- → **スナップショットは預託減算より前**。よって**倍掛けの預託は型A支払能力の判定に一切影響しない**
  （型A実行時点で賞金は手元にある扱い）。一方で**Finance（強制最低返済）は預託の後**に走るため、
  **預託は強制返済の原資を確実に削る**（v0.7 §6.6「預託金は Entry Fee / 契約 / 強制返済に使用不可」
  と整合）。

### 「DOUBLE 選択直後に型A履行不能で脱落」は構造上発生しない
型Aの執行と脱落判定（Step5）は倍掛け選択（`_process_double_up`）より前に完了しているため、
この順序では起こりえない。なお、別要因で脱落した場合の預託金は没収される:
`engine/game.py:1179-1189` — 翌Rの解決時に `if not p.is_alive:` で
`result: "forfeit_eliminated"` としてログを残すのみで、払い戻しも清算原資への算入も無い
（預託時点で `pay()` により現金から抜けているため、強制清算の対象財産にも含まれない）。

### 一致判定
△（プロンプトの `build_double_up_prompt:1830` の「同一ラウンドの処理順の事実」注記は
Finance との前後関係を正しく伝えている。ただし「型A判定には影響しない」ことは未記載）

### 関連テスト
`tests/test_s2_rules.py::TestS2Integration`、`tests/test_atomic.py`、
`tests/test_cycle8_double_up_logging.py::test_eliminated_logs_outcome_reason`

---

## 付録: 次タスク（プロンプト修正）への申し送り

- **優先度高（×判定の4項目）**: #2（署名待ち提案は永久に消えない）、#3（過去round_num指定は
  発行料を失ったうえ永久解除不能な死に契約になる）、#6（倍掛け他者預託がnegotiationで非表示、
  仕様§6.4未実装）、#9（R12でも残債完済不能なら「条件未達」ではなく「破産」）。
- **ダンプ文書の訂正が必要な箇所**: `doc/prompt_dump/actual_prompts_20260829.md` パターンBの
  メタ情報に2箇所の誤りがある。
  1. 「倍掛け預託ブロック: 発火（P05の他者預託）」→ 誤り。negotiation では他者の倍掛けは描画されない
     （本文にそのブロックが無いのが正しい実装挙動）。
  2. カードトレードの支払方向説明「P02が60万円を払う」→ 誤り。`cash_amount < 0` の描画では
     「＋N万円」が付いている側（P07・受諾者）が払う。
- **中優先度（△判定）**: #5・#7・#8・#10 は「実装は正しいが例外条件が未記載」というパターンが
  共通しているため、プロンプト修正時は「基本ルール＋主要な例外を1文追加」の形で対応するのが
  対称性原則（Cycle 5）と整合的。
