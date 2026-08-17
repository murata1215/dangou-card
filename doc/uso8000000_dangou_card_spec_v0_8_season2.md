# 嘘八百万 —談合カード— 仕様書 v0.8（Season 2 完全版）

> 更新日: 2026-08-16 18:49 JST
> 対象コミット: `131dbda`（2026-08-16 12:01:43 +0900, "feat: コストログ化 Stage 3 — 試合サマリー＆ビューワーにコスト内訳パネル"）
> ステータス: **現行実装を正とする完全版**。今後の変更は本ファイルを直接更新するか、次バージョン（v0.9）で差分反映する。

---

## §0. この文書について

### 0.1 位置づけ

本書は Season 2（S2）ルールセットの **完全版仕様書** である。これまでの `v0.5_frozen`（Season 1 確定版）→ `v0.6_season2`（S2初版・v0.5差分）→ `v0.7_season2`（S2確定版・v0.6差分）という差分参照の連鎖を断ち切り、**v0.5〜v0.7を読まなくても本書単体で現在の Season 2 の挙動が分かること** を目的とする。

- `doc/uso8000000_dangou_card_spec_v0_5_frozen.md`：Season 1 の確定仕様（凍結・歴史的参照用）
- `doc/uso8000000_dangou_card_spec_v0_6_season2.md`：S2初版（歴史的参照用、霧のラウンド等は後に削除）
- `doc/uso8000000_dangou_card_spec_v0_7_season2.md`：S2確定版（歴史的参照用、Settlement Step数表記等に現行との差異あり）
- **本書 `v0_8_season2.md`：現行 S2 の完全版。以後はこれを正とする**

v0.5/v0.6/v0.7 は削除・上書きしない。過去の設計意図を追う場合のみ参照する。

### 0.2 最重要方針

**現行のコード・テスト・設定が唯一の正である。** 本書の記述は `engine/` `llm/` `viewer/` `scripts/` の実装コードおよび `tests/` の受け入れテストと直接照合して書かれている。過去の仕様書の記述は信用せず、コードと矛盾する場合はコードを優先した。

コードだけでは意図が読み取れない箇所は `rules/project.md`・`doc/changelog.md`・関連する個別 devlog を参照して設計意図を補ったが、それでも断定できない項目には **`要確認`** を明示し、§17 にまとめた。

### 0.3 Season 1 との関係

Season 1（S1）は `engine/config.py` の `GameConfig` 既定値（各種フラグ `False`・逓増賞金スケジュール等）に対応する。Season 2 は S1 のエンジン基盤の上に、`GameConfig` の拡張フィールド（市場高騰・最終市場倍率・強制返済・カードトレード・倍掛け・CoT）を有効化したものである。エンジンコードは S1/S2 共通で、**設定値の差だけで両モードを表現する**設計になっている。

### 0.4 バージョン表記の注意

`GameConfig` には「既定値（`default_20()` 等）」と「実運用で使う S2 プリセット（`baseline_v1_s2()` / `default_8_s2()`）」の2系統があり、**両者の値は一致しない**（例: 生還条件の現金額や賞金スケジュール）。本書では両方を明記し、実際のトライアル実行（`--ruleset S2`）でどちらが使われるかを §3/§15 で明確にする。

---

## §1. ゲーム概要

### 1.1 目的とゲーム性

20体（実運用トライアルでは可変人数）の AI エージェントが、12ラウンドにわたり「市場」への参加・カードの提出・借入と返済・交渉と契約・裏切りを繰り返しながら生還を目指す、経済シミュレーション型対戦ゲームである。

### 1.2 プレイヤー数

`GameConfig` 既定は **20**（`engine/config.py:21`）。ただし実運用の LLM トライアル（`scripts/llm_trial.py`）は roster（座席割り当てモデルの一覧）の長さで人数を決定しており、直近の検証トライアルは 6〜9体規模で実施されている。20体版は README/CLAUDE.md 上の看板構成であり、実際に日常的に回しているのは少人数版が多い（`要確認 #1`）。

### 1.3 ラウンド数・市場数

- ラウンド数: **12**（固定）
- 1ラウンドあたりの市場数: **3**

### 1.4 カード

各プレイヤーは同一構成の12枚の手札（役カード、ランク1〜10）を持つ。1ラウンドに1枚を市場へ提出し、12ラウンドで丁度使い切る設計。詳細は §4。

### 1.5 勝敗・生還条件

- **生還条件**: R12 の Finance フェイズ終了時点で「借金残高 = 0」かつ「現金 ≥ 生還条件額」の両方を満たすこと。
- 生還条件額は既定 300万円だが、実運用プリセットでは **200万円**（§2.1・§15参照、`要確認 #2`）。
- 生還者の中では最終資産（現金）が高いほど順位が上とされる（勝敗の明示的なスコア式はエンジン内に無く、資産の多寡で評価する運用）。
- **全員生還は算術的に成立しない**設計（総賞金予算 < 全員が生還条件を満たすために必要な総額）。協調による全員生存は原理的に不可能。

### 1.6 1ラウンドの基本的な流れ

各ラウンドは以下の **5 Phase** で進行する（詳細は §7）。

1. **Market Open** — 3つの市場の賞金額を公開（前ラウンドの carryover を反映）
2. **Negotiation** — 最大10巡の交渉（DM・全体発言・送金・契約提案/署名・カードトレード・報奨・匿名通信・返済・pass）
3. **Commit** — 全生存プレイヤーが「参加する市場」と「使用するカード」を秘密裏に提出
4. **Settlement** — 8Step の決済処理（公開・勝敗判定・契約監査・報奨判定・脱落処理）
5. **Finance** — 利息計上・返済処理（R12のみ生還判定）

---

# Part I: ゲームルール

## §2. 資金・借入・返済

*根拠: `engine/config.py`, `engine/player.py`, `engine/finance.py`, `engine/elimination.py`*

### 2.1 初期資金・借入

- ゲーム開始前、各プレイヤーは **`loan_min`〜`loan_max`** の範囲で借入額を選択する。
  - 既定値・実運用プリセット共通: 120万円〜1,000万円
- 借入額 = 初期現金 = 初期借金残高（`initial_loan` として公開情報）。
- 開始時点では全員 Free Cash = 0（現金 = 借金残高のため）。

### 2.2 利息

- 毎ラウンドの Finance フェイズ Step 1 で、借金残高に **1.5%（`interest_rate = 0.015`）の複利** を計上する。
- 借金残高が 0 以下のプレイヤーには利息を計上しない。

### 2.3 返済

**R1〜R11**:
- 強制返済が有効な場合（S2 実運用は有効）、Finance Step 2 で **強制最低返済** を実行（§2.5）。
- Negotiation フェイズ中は任意の `repay` アクションで追加返済が可能（Free Cash 制限なし。システム向け支払いのため）。
- 追加の借入（ローン増額）は不可。

**R12**:
- Finance フェイズで強制最低返済を実行した後、**残りの借金残高全額を自動返済**（`min(cash, debt_balance)` を自動充当。返済し忘れによる脱落を防ぐ設計）。
- その後、生還判定（§2.6）を行う。

### 2.4 Free Cash（自由資金）

```
Free Cash = max(0, 現金残高 − 借金残高[利息込み])
```

（`engine/models.py` `PlayerState.free_cash` の `computed_field`）

- **適用対象**（Free Cash の範囲内でのみ実行可能）: 送金（`transfer`）、金銭契約（型A、即時・将来問わず）、報奨の預託、カードトレード提案の現金付与（正の `cash_amount`）
- **非適用**（システム向け支払いのためFree Cashを超えて現金残高から直接支払われる）: Entry Fee、利息、借金返済、契約発行料、匿名通信費

借金を横流しして他プレイヤーへ送金することを防止する設計。

### 2.5 強制最低返済（S2実運用で有効, `mandatory_repay_enabled`）

*根拠: `engine/player.py` `compute_mandatory_repayment()`, `engine/finance.py:63-93`*

- 毎ラウンドの Finance Step 2 で、以下の式で最低返済額を強制的に計上する:

```
remaining = num_rounds − round_num + 1
min_repay = ceil(debt_balance / (remaining + k))
```

- `k`（緩和パラメータ）は実運用では **0**（完全均等返済）。
- R12 時点では `remaining = 1` となり、`min_repay` は実質「残債全額」に近づく。
- 現金が `min_repay` に満たない場合、**即座に破産脱落**（`elimination_reason = "bankruptcy"`）。強制清算（§2.7）が実行される。
- 返済に成功した場合は現金・借金残高が更新され、`MANDATORY_REPAY` イベントとしてログされる。

### 2.6 R12 生還判定

Finance フェイズの最後（自動返済の後）に、生存中の各プレイヤーについて以下を順に判定する（`engine/finance.py:136-173`）。

1. `debt_balance > 0` ならアウト（理由: `condition_not_met`）
2. （1をパスした場合のみ）`cash < survival_cash` ならアウト（理由: `condition_not_met`）
3. 両方満たせば生還（`SURVIVAL_CHECK` イベントに `result: "survived"` を記録）

不合格の場合は強制清算（§2.7）を経て脱落する。

### 2.7 脱落条件・強制清算

脱落理由は以下の3種類の文字列リテラルで区別される（`PlayerState.elimination_reason`）。

| 理由 | 発生条件 |
|---|---|
| `bankruptcy` | Entry Fee が支払えない（Commitフェイズ）／強制最低返済額を支払えない（Financeフェイズ） |
| `contract_violation` | 型B契約の義務違反、または型A契約の履行不能（Atomic執行失敗）、または合法Commit集合が空（AUTO_COMMIT_FAILURE） |
| `condition_not_met` | R12終了時点で借金≠0 または 現金 < 生還条件額 |

脱落が確定すると `engine/elimination.py` の **強制清算**（forced liquidation）が実行される（`elimination.py:14-73`）。

1. 現金で借金を可能な限り返済（`min(cash, debt_balance)`）
2. 残債は貸倒れとして記録（`bad_debt`）
3. 残った現金はシステムが没収（生存者への再分配は無い）
4. 未使用の手札カードは全て消滅
5. `is_alive = False`、`elimination_reason`、`elimination_round` を設定
6. 当該プレイヤーが当事者となっている未履行の契約義務を全て失効させる（`expire_obligations_for_player()`。義務者・相手方いずれの立場でも失効対象）

脱落は全体に公示され、脱落者IDと理由種別が公開情報になる。

---

## §3. 市場ルール

*根拠: `engine/market.py`, `engine/settlement.py`*

### 3.1 市場の生成（Market Open フェイズ）

各ラウンドで3市場（`M01`〜`M03`）が生成される。

```
round_total = prize_tiers[round_num - 1]
if round_num == num_rounds and final_market_multiplier > 1:
    round_total *= final_market_multiplier   # R12のみ（§3.6）
```

`round_total` を `market_distribution` の方式で3市場に配分する（既定 `"equal"` = 均等配分、端数は最初の市場に加算）。`"weighted"`（50:30:20）・`"random"` も実装されているが、S2 実運用の既定は `"equal"`（`要確認 #10`）。

各市場の賞金プールは:

```
prize_pool = base_prize + carryover
```

### 3.2 賞金スケジュール（既定 vs 実運用）

| | 既定 `GameConfig()` | 実運用 `baseline_v1_s2()` |
|---|---|---|
| 方式 | 逓増（R1-4: 120万 / R5-8: 160万 / R9-12: 200万・4R区切り） | **フラット**（総額を12等分。端数は最終Rに加算） |
| 総額（20人時） | 19,200,000円 | `19,200,000 × (人数/20) × 2.0` を12分割 |
| survival_cash | 300万円 | **200万円** |

（`要確認 #3`: v0.5 系の逓増スケジュール記述と実運用のフラット×2.0スケジュールが不一致。実運用が正として扱う。）

### 3.3 Entry Fee とプール加算

- 市場に参加するには **Entry Fee 10万円** の支払いが必須（現金から直接、Free Cash制限なし）。
- Entry Fee は当該市場の賞金プールへ加算される: `total_pool = prize_pool + entry_fee × 参加者数`。
- Entry Fee を支払えない場合は即座に破産脱落（`bankruptcy`）。

### 3.4 市場高騰（サージ, S2実運用で有効）

*根拠: `engine/settlement.py:23-40` `_should_surge()`*

```python
def _should_surge(num_participants, alive_count, config):
    if not config.surge_enabled:
        return False
    if alive_count <= config.surge_full_participation_max_alive:
        return num_participants == alive_count   # 少人数時: 全員参加のみ高騰
    return num_participants > alive_count / 2     # 通常時: 参加者 > 生存者/2
```

- `surge_full_participation_max_alive` は実運用 **3**。生存者数が3人以下の局面では「その市場に生存者全員が参加した場合のみ」高騰する。
- それ以外（生存者4人以上）は「その市場の参加者数 > 生存者数 ÷ 2」で高騰。
- 発動効果: 当該市場の `total_pool` が **2倍**。
- 判定はプレイヤーには**事前に見えない**（Commit時点では未確定。Settlement Step 2 で判定・確定）。結果は `MarketResult.surged` として市場決着後に公開される。

### 3.5 勝者判定・同点分配・carryover

- 参加者のうち、提出したカードの **ランクが最も高い** プレイヤー（複数なら同ランク全員）が勝者。
- 勝者が複数の場合、`total_pool` を勝者数で **切り捨て除算**して均等分配（端数はプールに残り、次ラウンドへは繰り越されない＝実質没収）。
- 参加者0人の市場: `total_pool`（この場合は entry fee 加算なしなので `prize_pool` そのもの）が**そのまま次ラウンドの同一市場へ carryover**する。
- 参加者1人の市場（いわゆる「空き巣」）: その1人が全額を獲得する。
- **R12の carryover は消滅する**（次ラウンドが無いため。`engine/game.py:717`）。

### 3.6 R12 最終市場（実運用で有効, `final_market_multiplier = 3`）

- R12（最終ラウンド）の **基本賞金 `round_total` のみ**が3倍になる（§3.1 の式参照）。
- **carryover には倍率が掛からない**（倍率適用は carryover 加算より前）。
- 市場高騰と併用可能: 3倍後のプールに対してさらに高騰による2倍が乗る（最大 ×6 相当）。
- このルールは LLM プロンプトの RULES_SUMMARY に条件付きで注入される（`final_market_multiplier > 1` のときのみ表示。§9.3参照）。

### 3.7 表示上の仕様とゲームロジックの区別

- 市場高騰バッジ（ビューワー上の「高騰×2」表示）は **表示専用機能**であり、`MARKET_RESULT` イベントの `surged` フラグをそのまま描画するのみ。ゲームロジックには一切影響しない（§12参照）。
- ビューワーの「借入額・純資産表示」も同様に表示専用の近似計算を含む場合があるため、正確な資金状態はログの `SNAPSHOT`/`INTEREST` イベントを直接参照すること。

---

## §4. カード

*根拠: `engine/cards.py`, `engine/models.py`, `engine/player.py`*

### 4.1 役・枚数

各プレイヤーは同一構成の **12枚** を保持する。

| rank値 | 役名 | 枚数 | card_id 例 |
|---|---|---|---|
| 1 | HIGH_CARD | 2 | `HIGH_CARD_1`, `HIGH_CARD_2` |
| 2 | ONE_PAIR | 2 | `ONE_PAIR_1`, `ONE_PAIR_2` |
| 3 | TWO_PAIR | 1 | `TWO_PAIR_1` |
| 4 | THREE_OF_A_KIND | 1 | `THREE_OF_A_KIND_1` |
| 5 | STRAIGHT | 1 | `STRAIGHT_1` |
| 6 | FLUSH | 1 | `FLUSH_1` |
| 7 | FULL_HOUSE | 1 | `FULL_HOUSE_1` |
| 8 | FOUR_OF_A_KIND | 1 | `FOUR_OF_A_KIND_1` |
| 9 | STRAIGHT_FLUSH | 1 | `STRAIGHT_FLUSH_1` |
| 10 | ROYAL_FLUSH | 1 | `ROYAL_FLUSH_1` |

数値が大きいほど強い（`CardRank` は `IntEnum`）。

### 4.2 使用・消費

- 1ラウンドにつき必ず1枚を Commit で提出（市場と組で秘密提出）。
- 提出したカードは Settlement Step 2 で消滅する（勝敗に関わらず、参加者全員のカードが消滅）。
- 12ラウンドで手札12枚を過不足なく使い切る設計。
- 未使用カードは秘匿情報、使用済みカードは全プレイヤーに公開される（カウンティング材料）。

### 4.3 card_id と rank の扱い（重要実装仕様）

**card_id は手札内でグローバルに一意ではない。** 全プレイヤーが同一の card_id 体系（例えば全員が `TWO_PAIR_1` を持つ）のデッキ構成であるため、カードトレードで相手のカードを受け取ると、**同一 card_id が自分の手札内に2枚存在しうる**（自分の元の `TWO_PAIR_1` は既に消費済みで無い場合を除き、理論上は衝突しうる）。

これに対応するため、以下の2つの実装仕様が定められている（`rules/project.md` の不変条件）。

1. **`use_card()` は card_id が一致するカードを「1枚だけ」除去する**（`engine/player.py`）。全一致除去にすると、トレードで生じた重複カードを1回使用しただけで2枚同時に消滅し、手札がドレインする重大バグになる（過去に R12 で AUTO_COMMIT_FAILURE を引き起こした真因）。
2. **`swap_card()` は受領カードの card_id が手札内で衝突する場合、`_t`・`_t2`… のサフィックスでリネームしてから加える**（`rank` は不変。表示上のランクや判定には一切影響しない、識別子の衝突回避のみ）。

新規に手札を操作するコードを書く場合、card_id での一致判定が「1件のみ」を意図しているか必ず確認すること。

### 4.4 カードトレード（S2実運用で有効, `card_trade_enabled`）

*根拠: `engine/models.py` `CardTradeProposal`/`CardTradeStatus`, `engine/player.py` `swap_card()`, `engine/actions.py`*

- **1枚 ⇄ 1枚の交換のみ**。オプションで現金を付与できる（`cash_amount`：正なら提案者→相手、負なら相手→提案者）。現金付与がある場合は Free Cash 制限が適用される。
- 提案（`card_trade_propose`）→ 受諾（`card_trade_accept`）／拒否（`card_trade_reject`）の2アクション制。受諾時にアトミックに交換が実行される。
- **ブロードキャスト提案**: `with_players` に複数の宛先を指定可能（上限 `card_trade_broadcast_max = 5`）。同一 `offer_id` で束ねられ、**最初に受諾した相手とのみ成立**、他の宛先への提案は自動的に `EXPIRED` になる。
- **1プレイヤーにつき1ラウンド1回まで**（`card_trade_max_per_round = 1`）。
- **R12は不可**（`card_trade_last_round = 11`。最終ラウンドでの交換は無意味＝提出前提のため）。
- 可視性: トレード提案の「存在」は当事者間で見えるが、受信者には `offer_id`／全宛先リスト（`all_targets`）／他の宛先の応答状況（`target_statuses`）は非公開。
- ステータス遷移: `PROPOSED` → `ACCEPTED` / `REJECTED` / `EXPIRED`（未応答のままラウンド末を迎えた場合も失効）。

---

## §5. 契約・交渉

*根拠: `engine/contracts.py`, `engine/models.py`, `engine/autocommit.py`, `engine/actions.py`*

### 5.1 契約の生成〜成立

1. **提案**（`contract_propose`）: 提案者が発行料 **10万円**（`contract_fee`）を負担し、相手（`with_players`）と義務のリスト（`terms`）を指定して提案する。契約は `PROPOSED` 状態で生成される。
2. **署名**（`contract_sign`）: 各当事者が個別に署名。**全当事者が署名し終えると `ACTIVE`** に遷移する（提案者は自動署名済み扱い）。
3. **可視性**: `PROPOSED` 状態の契約内容（義務の詳細）は**当事者にのみ**プロンプトに注入される（`contracts_pending` として交渉プロンプトに表示。§9.3）。契約の「存在」と「当事者名」自体は成立後に全体公示されるが、義務の中身は当事者以外には非公開のまま。

### 5.2 contract_id / obligation_id

- `contract_id = "C_" + uuid4().hex[:8]`（例: `C_a1b2c3d4`）
- `obligation_id = f"{contract_id}_OB{i+1:02d}"`（例: `C_a1b2c3d4_OB01`）

### 5.3 型A・型B の義務種別と details スキーマ

契約は「義務（Obligation）」の集合として管理される。義務は `ob_type` ごとに以下の `details` キーを持つ（**現行の正しいキー名**。2026-08-15 に `type_b_card` のキー不一致バグが修正され、以下に統一されている）。

| ob_type | 種別 | details キー | 意味 |
|---|---|---|---|
| `type_a_payment` | 型A（金銭） | `{"amount": int}` | 指定ラウンドに `obligor` から `counterparty` へ自動送金 |
| `type_b_market` | 型B（行動） | `{"market_id": str}` | 指定ラウンドに指定市場へ参加する義務 |
| `type_b_card` | 型B（行動） | `{"card_rank": str}` | 指定ラウンドに指定ランクのカードを使用する義務 |
| `type_b_no_market` | 型B（行動） | `{"market_id": str}` | 指定ラウンドに指定市場へ参加**しない**義務 |

**正規化ルール（重要実装仕様）**: `create_contract()` の入口で、LLMが `"card"` や `"rank"` 等の同義語キーで送っても自動的に `"card_rank"` へ正規化される。無効なランク名が指定された場合は `ValueError` で契約提案自体が拒否される（判定ロジック・自動代行Commit・ビューワー表示・契約義務可視化ブロックの4つの消費者全てが `card_rank` を参照するため、入口1箇所での正規化で全消費者に効く設計）。

### 5.4 義務の対象ラウンドと執行タイミング

各 `Obligation` は `round_num` を持ち、**そのラウンドの Settlement で判定・執行**される（型Aは即時実行、型Bは監査）。

### 5.5 型B の監査（Settlement Step 3）

- `TYPE_B_MARKET`: 該当プレイヤーの commit の `market_id` が `details.market_id` と一致するか
- `TYPE_B_CARD`: 該当プレイヤーの commit のカード `rank.name` が `details.card_rank` と一致するか
- `TYPE_B_NO_MARKET`: 該当プレイヤーの commit の `market_id` が `details.market_id` と**異なる**か
- 違反者はこの時点で**即座に脱落確定**（`contract_violation`）。以降のStepで全義務が失効する。

### 5.6 型A の Atomic 執行（Settlement Step 4〜5）

- Step 4 で「市場賞金反映後」のスナップショット（Cash / Free Cash）を固定する。**同一 Settlement 内で受け取る予定の型A受取金は、支払い原資にできない**（受取と支払いが同時に発生する場合、受取分をあてにした支払いは不可）。
- Step 5 で義務者（`obligor`）ごとに、そのラウンドの全 `type_a_payment` 義務の金額を合算し、スナップショット時点の Free Cash と比較する。
  - 合算額 ≤ Free Cash → **全件を一括執行**（all-or-nothing）
  - 合算額 > Free Cash → **全件が不履行**となり、義務者は `contract_violation` で脱落

### 5.7 合法Commit集合・AUTO_COMMIT_FAILURE

- `engine/autocommit.py` の `compute_legal_commits()` が、そのプレイヤーが持つ全ての「有効な型B義務」を同時に満たす `(market, card)` の組み合わせを列挙する。
- LLM の応答が不正・パース不能な場合、上記の合法集合から自動代行選択される（優先順位: ①最低ランク ②最低賞金 ③市場ID順）。
- **合法集合が空の場合**（矛盾する複数の型B義務を負っている等）、Commit不能となり **AUTO_COMMIT_FAILURE** → `contract_violation` で即座に脱落する。

---

## §6. 倍掛け（DOUBLE UP）

*根拠: `engine/game.py:720-850` `_process_double_up()`, `engine/models.py` `DoubleUpDeposit`*

S2実運用で有効（`double_up_enabled`）。

### 6.1 提示条件

- 対象は **そのラウンドの市場勝者**（`prize_won > 0`）に限る。
- 提示は **R11まで**（R12は最終ラウンドのため提示されず、自動的に TAKE 相当＝即時受領）。

### 6.2 選択

賞金を獲得した直後、勝者エージェントに `choose_double_up()` が呼ばれ、以下の二択を提示する。

- **TAKE**: 賞金をそのまま即時受領（既定の動作。パース失敗時のフォールバックもこちら）
- **DOUBLE**: 賞金を受け取らず、システムへ**預託**する

### 6.3 成功判定（翌ラウンド）

- 預託した賞金は **翌ラウンドの市場結果**で判定される。
- **成功条件**: 翌ラウンドで「ソロ市場（参加者1人の市場）を除く」市場で1円でも賞金を獲得すること（`non_solo_winners` に含まれること）。
- **成功** → 預託額の **2倍**を払い出し。
- **失敗**（翌R非勝利、またはソロ市場のみでの勝利）→ 預託額は**没収**（全額喪失）。
- 預託者が翌ラウンドまでに脱落していた場合も没収（`forfeit_eliminated`）。

### 6.4 ソロ市場除外の意図

参加者1人の市場（空き巣）での勝利は「対戦せずに確実に得られる」性質のため、これを倍掛け成功条件に含めると空き巣を連発するだけの安全な必勝戦術が成立してしまう。これを防ぐため、ソロ市場での勝利は倍掛け成功判定から明示的に除外されている（過去バグ調査 `doc/double_up_bug_investigation.md` を経て実装修正済み）。

### 6.5 連鎖禁止

倍掛けの2倍払い出しによって得た賞金は、**その場では再度倍掛けの対象にできない**。当該ラウンドの `eligible_prize` は「その勝利で得た賞金 − 今ラウンド解決された倍掛け払い出し分」として算出され、無限に倍賭けを連鎖させることはできない。

### 6.6 公開性

倍掛けの選択・預託額は `DOUBLE_UP_CHOSEN` / `DOUBLE_UP_RESOLVED` イベントとして全員に公開される（他プレイヤーの現在の預託状況が負けている側にも見える）。

---

## §7. ラウンド進行の確定処理順

*根拠: `engine/game.py`, `engine/settlement.py`, `engine/finance.py`*

### 7.1 5 Phase（`engine/game.py:144-148`）

1. Market Open
2. Negotiation
3. Commit
4. Settlement（8Step。§7.2）
5. Finance（§2.2〜§2.6）

### 7.2 Settlement 8Step（`engine/settlement.py:62-69`）

> 旧版仕様書（v0.7 §8）は「11Step」と表記しているが、**現行実装は8Stepである**（`要確認 #4`。本書は実装に従う）。

1. **Reveal** — 全市場の参加者・使用カードを公開
2. **Market Settlement** — Entry Fee加算 → 高騰判定 → 勝敗判定 → 賞金支払い → 参加カード消滅
3. **型B監査** — 違反者を脱落確定、当該プレイヤーの全義務を即時失効
4. **スナップショット** — 市場賞金反映後の Cash / Free Cash を固定（型A判定の基準値）
5. **型A Atomic執行** — 義務者別に合算 → Free Cash判定 → 一括執行 or 不履行
6. **報奨判定** — 達成者型・イベント型（同一Settlement内の脱落も条件として発火しうる）
7. **脱落公示**
8. **強制清算** — §2.7参照

### 7.3 倍掛け処理のタイミング

倍掛けの「前ラウンド預託の解決」と「今ラウンド勝者への提示」は Settlement 内（Step 2の市場結果確定後）で処理される（`_process_double_up()` は `execute_settlement()` の呼び出し元である `game.py` 側から Step 2 の結果を受けて実行）。

### 7.4 R12特有の処理順

R12 は Settlement 後の Finance フェイズが「強制返済 → **自動返済（全額充当）** → **生還判定**」という特殊な順序になる（§2.3・§2.6）。また倍掛けは R12 では提示されず、carryover は消滅する。

---

## §8. 公開情報・秘匿情報

### 8.1 公開情報

- 各プレイヤーの初期借入額（`initial_loan`）
- 総賞金予算・各ラウンドの市場と賞金額
- 使用済みの全カード（誰が何を使ったか）
- 各市場の参加者・使用カード（決着後）
- 勝者と獲得額
- 契約の存在と当事者名（義務内容は非公開。当事者のみ閲覧可）
- 公開報奨（匿名時は掲載者を除く）
- 全体チャット（broadcast）
- AUTO COMMIT の発生
- 脱落者IDと理由種別
- カードトレードの成立事実（詳細金額・カード種別は当事者外には非公開）
- 倍掛けの選択・預託額・成否

### 8.2 秘匿情報

- 未使用カード（自分以外）
- 現金・借金残高・Free Cash
- DM（direct message）内容
- 契約の義務詳細（非当事者に対して）
- 匿名通信・匿名報奨の掲載者
- 次ラウンドのCommit内容（提出まで）
- **LLMの reasoning（CoT思考過程）** — 他プレイヤーには絶対に見せない（§10参照）
- カードトレードのブロードキャスト提案における他宛先の応答状況

---

# Part II: エージェント仕様

## §9. LLMエージェント仕様

*根拠: `llm/prompt_builder.py`, `llm/response_parser.py`, `llm/llm_agent.py`, `llm/models.py`, `llm/adapters.py`*

### 9.1 観測できる情報 / できない情報

エージェントに渡されるプロンプトには以下が含まれる（§8.1の公開情報 + 自分自身の秘匿情報）。

- 市場情報（ID・賞金プール。`carryover > 0` のときは「基本◯万 + 前R繰越◯万」の内訳を付記。§3.4/§4.7の高騰・繰越で賞金プールが基本額から変動していることが数字だけでなく理由付きで分かる）
- **前ラウンドの市場決着結果**（`_render_last_round_results()` によりnegotiation/commit両フェーズの「## 市場」直後に注入。commits（`{player_id, card_rank}`）があれば`P01[ROYAL_FLUSH]★, P02[FLUSH]★, ...`の形式で参加者ID+使用カード+勝者マークを列挙、無ければ参加者IDのみ、どちらも無ければ人数のみ（後方互換）。高騰の有無、参加者0の場合は「不成立→繰越」を明示。霧ラウンド（`config.fog_rounds`）ではcard_rankを`"FOG"`に伏せる。R1は前ラウンドが存在しないため何も表示されない。2026-08-17: これはこれまで一度もプロンプトに含まれておらず、§8.1が公開情報と定義する「各市場の参加者・使用カード（決着後）、勝者と獲得額」が実際には周知されていなかった実装漏れの是正）
- **自分の**状態（現金・借金残高・Free Cash・手札） — 他プレイヤーの同項目は含まれない
- 生存者一覧
- 直近のメッセージ履歴（DM・broadcast、直近10件）
- **自分が当事者**の提案中契約（`contracts_pending`）・カードトレード提案
- 使用済みカード一覧（全プレイヤー分・公開情報）
- **自分自身**の署名済み未履行契約義務一覧（`_render_obligations_block()` によりnegotiation/commit/double_upの3フェーズ全てに注入。現ラウンドの義務は強調表示され、同一ラウンドに矛盾するtype_b_card義務が複数ある場合は警告が出る）
- （double_up時）他プレイヤーの現在の倍掛け預託状況（金額のみ、reasoningは含まれない）
- **引き継ぎメモリ**（`config.memory_enabled=True`のときのみ。negotiation/commitプロンプトに「## あなたの記憶（前ラウンドから引き継いだメモ / これが唯一の記憶です）」として自分が前ラウンド終了後に書いた自由記述メモ（最大`memory_max_chars`字、既定1000字）を注入。詳細は§9.6）

**含まれないもの**: 他プレイヤーの現金・借金・Free Cash・未使用手札・DM内容（非当事者）・非当事者の契約義務詳細・**他プレイヤーの reasoning**・**他プレイヤーの引き継ぎメモリ**（`memory`はエージェントインスタンス内に閉じ、`visible_state`を経由しないため構造的に混入しない）。

プロンプトにはモデル名は一切含まれない（匿名化。プレイヤーIDと目的のみ提示）。

### 9.2 意思決定フェーズ

| フェーズ | 呼び出し | 主な出力 |
|---|---|---|
| `loan_choice` | ゲーム開始前1回 | 借入額 |
| `negotiation` | 各ラウンド最大10巡 | dm/broadcast/transfer/repay/contract_propose/contract_sign/anonymous_broadcast/bounty_post/bounty_cancel/card_trade_propose/card_trade_accept/card_trade_reject/pass |
| `commit` | 各ラウンド1回 | market_commit（market_id + card） |
| `double_up` | 勝利時（R12除く） | DOUBLE / TAKE |
| `reflection` | 各ラウンド終了後（Settlement/Finance完了後。`memory_enabled=True`かつ最終ラウンド以外の生存者のみ） | 次ラウンドへ持ち越す自由記述メモ（§9.6） |

### 9.3 JSON応答スキーマ（主要例）

共通エンベロープ:

```json
{"strategy": {"reason": "...", "emotion": "楽"}, "action": {"type": "...", ...}}
```

`enable_cot=True` の場合は先頭に `"reasoning"` フィールドが追加される（§10）。

主なアクション例:

```json
{"type": "pass"}
{"type": "dm", "to": "P07", "message": "..."}
{"type": "broadcast", "message": "..."}
{"type": "transfer", "to": "P07", "amount": 500000}
{"type": "repay", "amount": 1000000}
{"type": "contract_propose", "with": ["P07"], "terms": [
  {"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment", "round_num": 5, "details": {"amount": 500000}},
  {"obligor": "P07", "counterparty": "P01", "ob_type": "type_b_card", "round_num": 5, "details": {"card_rank": "FLUSH"}}
]}
{"type": "contract_sign", "contract_id": "C_a1b2c3d4"}
{"type": "bounty_post", "amount": 500000, "bounty_type": "achievement", "condition_type": "market_win_against", "condition": {"target_player": "P07"}, "round_num": 5}
{"type": "card_trade_propose", "with_players": ["P07"], "give_card": "ONE_PAIR", "receive_card": "FLUSH", "cash_amount": 0}
{"type": "card_trade_accept", "trade_id": "T_..."}
{"type": "market_commit", "market_id": "M01", "card": "ONE_PAIR"}
{"choice": "DOUBLE"}  // double_up専用スキーマ（strategy/actionではなくchoiceキー）
```

`emotion` は7種のいずれか: `喜` `怒` `哀` `楽` `焦` `疑` `奸`（2026-08-12 に「奸（ニヤリ/smirk）」が追加され6→7種に拡張）。無効な値は `平静` に正規化される。

### 9.4 エラー・無効JSON時の扱い

- `response_parser.py` は複数パターン（コードブロック・切り詰め補完・生JSON・埋め込みJSON）で抽出を試みる。全て失敗すると `ParseError`。
- パースエラー時は correction hint を付けて **最大2回まで再送**（`MAX_RETRIES = 2`）。
- 応答が `finish_reason == "length"`（出力トークン上限で切り詰め）の場合、専用のヒントを追加。
- 再送も尽きた場合:
  - `negotiation` → `PassAction` にフォールバック
  - `commit` → 合法Commit集合からの自動代行（AUTO_COMMIT。§5.7）
  - `double_up` → `TAKE`（False）にフォールバック
- 未知のアクション種別を受け取った場合は警告ログを出して `PassAction` 扱い。
- APIレベルのエラー（timeout/429/5xx/接続エラー）は指数バックオフ（1s, 2s）で最大2回リトライ（`API_MAX_RETRIES = 2`）。

### 9.5 モデルレジストリの役割

`llm/models.py` の `MODEL_REGISTRY` は、モデルキー（`M1`〜`M6`, `L1`〜`L6`）から実際のAPI呼び出しに必要な情報（`model_id`, `provider`, `adapter_type`, 単価, `env_key`, `base_url` 等）へ変換する唯一の対応表である。ゲームロジック（`engine/`）はモデル種別を一切意識せず、`llm_agent.py` がこのレジストリを介してアダプタを選択する。詳細は §11。

### 9.6 引き継ぎメモリ（Handover Memory）

*根拠: `engine/config.py`, `engine/negotiation.py`, `engine/game.py`, `llm/prompt_builder.py`, `llm/response_parser.py`, `llm/llm_agent.py`, `tests/test_memory.py`*

2026-08-17に発見された「LLMコールは1-shotで会話履歴を持たず、`_round_messages`は毎ラウンドNegotiation冒頭でクリア、戦略メモ（`strategy_history`）は同一ラウンド内のみ参照、過去の契約違反・脱落理由もvisible_stateに一切含まれない」ため、AIは実質的に毎ラウンド「初対面」として振る舞い、談合ゲームの核心である「裏切りを覚えて次に活かす」が成立していなかった問題への対処。

- **`config.memory_enabled`**（既定`False`、S2プリセットのみ`True`）でON/OFFを切り替える。`False`の間は本節の挙動は一切発生せず、旧挙動と完全に同一
- **Reflectionフェイズ**: `run()`のフェイズ順は`market_open → negotiation → commit → settlement → finance → reflection`。各ラウンドのSettlement/Finance完了後、生存プレイヤー全員に対して`agent.reflect(player_state, round_num, visible_state)`を呼ぶ。**最終ラウンドはスキップ**（次ラウンドが存在せず使われないため）。1体の例外はゲーム進行を止めず握りつぶす
- **材料**: 前ラウンドから引き継いだmemory（あれば）＋当ラウンドの会話全件（`_round_messages`、DM/broadcast問わず件数制限なし）＋当ラウンドの契約義務・正式契約の状況＋当ラウンドの市場決着結果（§9.1の`commits`形式）＋自分の資金状況（決着・利息計上後）
- **出力**: `{"memory": "..."}`形式の自由記述。フォーマットは強制しない（箇条書き・散文・表いずれも可。何を書き何を書かないかはモデルの判断に委ねられ、ここでモデル間の性能差が現れる）。`memory_max_chars`（既定1000字）を超える分は切り詰める
- **抽出**: `extract_memory()`はJSON `{"memory": "..."}`が取れなければ応答テキストそのものを採用し、`ParseError`を投げない（自由記述専用の緩い抽出経路。通常のaction系レスポンスとは別系統）
- **保持**: `LLMAgent._memory`は常に最新の1枚のみ（累積しない）。ラウンドを重ねてもプロンプトへの注入コストは一定
- **失敗時の安全側動作**: API失敗・空応答・memoryキーが空文字のいずれの場合も、`self._memory`は**変更されない**（前ラウンドのメモをそのまま維持。空文字での上書きは絶対にしない）
- **注入先**: `negotiation`・`commit`プロンプトの「## あなたの記憶（前ラウンドから引き継いだメモ / これが唯一の記憶です）」セクションに注入される。`double_up`（TAKE/DOUBLEの純粋なリスク判断）には注入されない
- **秘匿性**: `memory`は`LLMAgent`インスタンス内（`self._memory`）に閉じ、`visible_state`を経由しないため、他プレイヤーのプロンプトに構造的に混入しえない
- **観戦ログ**: `reflection`フェイズの呼び出しは既存の`LLMLogger.log_call()`を通るため`phase="reflection"`としてログに残る。応答に`action`キーが無いため、viewerの`_collect_round_messages()`（dm/broadcast収集ロジック）には拾われず、viewer側の変更は不要

---

## §10. CoT（Chain-of-Thought）/ reasoning

*根拠: `engine/config.py`, `llm/prompt_builder.py`, `llm/response_parser.py`, `llm/llm_agent.py`, `llm/llm_logger.py`, `tests/test_cot.py`*

### 10.1 `enable_cot` フラグ

`GameConfig.enable_cot`（既定 `False`）を `True` にすると、LLMへの各プロンプト（`loan_choice`/`negotiation`/`commit`/`double_up`）に「JSON先頭に `reasoning` フィールドを含め、行動決定前に状況・他者の意図・リスク・最善手を日本語で論理的に考えよ」という指示が追加される。トライアル実行時は `--cot` フラグで有効化する。

### 10.2 ネイティブ thinking との別軸性

この `reasoning` フィールドは **プロンプトで明示的に要求するテキスト思考**であり、モデル側のネイティブ思考機能（例: Anthropic extended thinking、Gemini/Grok の thinking トークン）とは**別軸の概念**である。

- Kimi K2.6（M5/L5）はネイティブ thinking が `extra_params={"thinking": {"type": "disabled"}}` で**明示的に無効化**されている（2026-08-13 に長考によるJSON率低下問題を根治するため）。
- Haiku 4.5（L1）は現行コードで extended thinking 用のパラメータが設定されておらず、ネイティブ思考は有効化されていない。
- したがって、これらのモデルでも `enable_cot=True` であれば `reasoning`（プロンプト誘導型のテキスト思考）自体は出力されうる。§11.2 の Think トークン集計（`total_tokens - input - output`）はネイティブ thinking の課金トークンのみを指し、`reasoning` フィールドのテキスト量とは別物である点に注意。

### 10.3 保存先・秘匿境界（最重要制約）

- `reasoning` は `response_parser.py` により `strategy["_reasoning"]` に格納される。
- `llm_agent.py` を経由して **`llm_logger.py`（神視点のみが閲覧可能な per-player JSONLログ）にのみ記録**される。
- **`engine/game.py` の `_build_visible_state()`、`NEGOTIATION_ACTION` イベント、他プレイヤー向けの一切のプロンプトには reasoning/`_reasoning` を含めてはならない。** これは `rules/project.md` に明記された不変条件であり、新しい可視化経路（ビューワーAPI・新イベント種別・プロンプトテンプレート）を追加する際は `strategy` 辞書をそのまま公開しないことが求められる。
- 担保テスト: `tests/test_cot.py::TestCoTNoLeak`（3件）。
- 2026-08-16 の6ベンダー混合スモークテスト（seed=601）で情報リーク **0件** を確認済み。

### 10.4 god-view / 本人ログ / ビューワーでの扱い

- **god-view（開発者用の生ログ）**: `llm_logs/game01_P0N_llm_calls.jsonl` に `reasoning` フィールドとして平文で記録される（§12参照）。
- **本人（そのプレイヤー自身）への再表示**: 現行実装では、自分自身の過去の `reasoning` を次の意思決定時のプロンプトに再注入する仕組みは無い（`strategy_history` には `_reasoning` を含まない形で戦略メモのみ保持）。
- **ビューワー（観戦UI）**: `viewer/log_parser.py` は `reasoning` フィールドを一切抽出・返却しない。UIに表示されるのは `emotion` と `response_text` の先頭200字プレビューのみであり、reasoning テキストそのものはAPIレスポンスにも含まれない。

---

# Part III: 実験基盤・運用

> 以下 §11〜§13 は **ゲームルールではなく、LLM実験・観戦・運用のための基盤機能**である。ゲームバランスや勝敗条件には一切影響しない。

## §11. コスト計測

*根拠: `llm/models.py`, `llm/adapters.py`, `llm/llm_agent.py`, `llm/llm_logger.py`*

### 11.1 トークン内訳

各API呼び出しから以下を捕捉する（`llm/adapters.py`）。

| フィールド | 意味 |
|---|---|
| `input_tokens` | プロンプト入力トークン数 |
| `output_tokens` | 応答出力トークン数（completion） |
| `total_tokens` | プロバイダが返す合計トークン数 |
| `cache_read_input_tokens` | プロンプトキャッシュのヒット分（割引単価対象） |
| `cache_creation_input_tokens` | プロンプトキャッシュの新規書き込み分 |
| `reasoning_tokens` | プロバイダが個別に報告する思考トークン数（参考値。コスト計算には `total_tokens` との差分方式を用いる） |

プロバイダ差異:
- **Anthropic**: thinkingは `output_tokens` に内包 → `total_tokens = input + output` として構築（アダプタ側で計算）。
- **OpenAI互換（OpenAI/xAI/Moonshot/DeepSeek）**: reasoningは `completion_tokens`（=output_tokens）に内包 → `total_tokens` はAPIレスポンスの値をそのまま使用するが、実質 `input + output` と一致する。
- **Gemini**（OpenAI互換エンドポイント経由）: thinkingが `completion_tokens` に含まれず、`total_tokens` が `input + output` を上回る場合がある。

### 11.2 `estimate_cost()` の計算方法（`llm/models.py:160-195`）

```python
def estimate_cost(model, input_tokens, output_tokens,
                   cache_read_input_tokens=0, total_tokens=0) -> float:
    effective_cached_price = model.cached_input_price or model.input_price
    effective_reasoning_price = model.reasoning_price or model.output_price

    uncached_input = input_tokens - cache_read_input_tokens
    input_cost = uncached_input * model.input_price \
                + cache_read_input_tokens * effective_cached_price
    output_cost = output_tokens * model.output_price

    effective_total = total_tokens if total_tokens > 0 else (input_tokens + output_tokens)
    separate_thinking = max(0, effective_total - input_tokens - output_tokens)
    thinking_cost = separate_thinking * effective_reasoning_price

    return (input_cost + output_cost + thinking_cost) / 1_000_000
```

**二重計上防止のロジック**: `separate_thinking = max(0, total_tokens - input_tokens - output_tokens)`。

- OpenAI系・Anthropic・DeepSeek・Kimi: thinking/reasoning が `output_tokens`（completion）に内包されているため、この差分は **0** になり、二重課金は発生しない。
- Gemini・Grok: thinking が completion 外で報告される設計のため、差分が**真の thinking トークン数**として加算される。

（seed=603検証トライアルで実測確認済み: Gemini 3.5 Flash Think=11,421、Grok 4.3 Think=3,847（reasoning_tokens報告値と一致）、他4社（Kimi/DeepSeek/Haiku/GPT-4.1 Mini）Think=0。）

### 11.3 モデル単価スナップショット（2026-08-16時点、`MODEL_REGISTRY`）

| Key | model_id | 表示名 | Provider | Input $/1M | Output $/1M | Cached Input $/1M | Reasoning $/1M |
|---|---|---|---|---|---|---|---|
| M1 | claude-sonnet-5 | Claude Sonnet 5 | Anthropic | 2.00 | 10.00 | — (=input) | — (=output) |
| M2 | gpt-4.1 | GPT-4.1 | OpenAI | 2.00 | 8.00 | 0.50 | — (=output) |
| M3 | gemini-3.5-flash | Gemini 3.5 Flash | Google | 1.50 | 9.00 | — (=input, `要確認 #7`) | — (=output) |
| M4 | grok-4.5 | Grok 4.5 | xAI | 2.00 | 6.00 | 0.30 | — (=output) |
| M5 | kimi-k2.6 | Kimi K2.6 | Moonshot | 0.95 | 4.00 | — (=input) | — (=output) |
| M6 | deepseek-v4-flash | DeepSeek V4 Flash | DeepSeek | 0.14 | 0.28 | 0.0028 | — (=output) |
| L1 | claude-haiku-4-5-20251001 | Claude Haiku 4.5 | Anthropic | 1.00 | 5.00 | 0.10 | — (=output) |
| L2 | gpt-4.1-mini | GPT-4.1 Mini | OpenAI | 0.40 | 1.60 | 0.10 | — (=output) |
| L3 | gemini-3.5-flash-lite | Gemini 3.5 Flash-Lite | Google | 0.30 | 2.50 | — (=input, `要確認 #7`) | — (=output) |
| L4 | grok-4.3 | Grok 4.3 | xAI | 1.25 | 2.50 | 0.20 | — (=output) |
| L5 | kimi-k2.6 | Kimi K2.6 (L) | Moonshot | 0.95 | 4.00 | — (=input) | — (=output) |
| L6 | deepseek-v4-flash | DeepSeek V4 Flash (L) | DeepSeek | 0.14 | 0.28 | 0.0028 | — (=output) |
| L7 | gemini-2.5-flash-lite | Gemini 2.5 Flash-Lite | Google | 0.10 | 0.40 | — (=input) | — (=output) |

L7は2026-08-16、6体→18体の低コストインフラ負荷試験用に追加された枠であり、ロスター仕様書v1.0の12モデル（M1〜M6, L1〜L6）には含まれない。

「— (=input)」「— (=output)」は `cached_input_price`/`reasoning_price` が `None` のため、`estimate_cost()` が入力/出力単価をそのまま代用することを示す。**`reasoning_price` は現行全モデルで `None`**（`要確認 #8`）。

DeepSeek は `deepseek-chat` を指定すると `deepseek-v4-flash` にリダイレクトされることが実測確認されており、`model_id` を `deepseek-v4-flash` に変更した上で単価もV4 Flashのものに是正済み（旧V3単価からの変更、2026-08-16）。

### 11.4 単価の一次情報とログ内スナップショット

`llm_logger.py` の各ログエントリには `unit_price_input`/`unit_price_output`（呼び出し時点の単価スナップショット）が記録される。過去ログを再集計する際はこのスナップショットを使うか、`MODEL_REGISTRY` の現行値のどちらを使うかで結果が変わりうるため、集計目的に応じて明示的に選択すること。

---

## §12. ログ・ビューワー

*根拠: `llm/llm_logger.py`, `engine/events.py`, `viewer/log_parser.py`, `viewer/server.py`, `viewer/static/`*

### 12.1 主なログファイル

トライアル出力ディレクトリ `logs/llm/trial_{A|B|C}_{YYYYMMDD}_{HHMMSS}/` 配下:

| ファイル | 内容 |
|---|---|
| `game{NN}_events.jsonl` | god-view のゲームイベント全記録（`GameEvent` の時系列ログ） |
| `game{NN}_seat_map.json` | 座席とモデルの対応表（Phase Cのみ） |
| `llm_logs/game{NN}_P{NN}_llm_calls.jsonl` | プレイヤー別のLLM呼び出しログ（プロンプト・応答・トークン・コスト・reasoning等） |
| `trial_{X}_report.md` | トライアル終了後に自動生成されるサマリーレポート |

### 12.2 god-view / player別ログ / events の違い

- **events.jsonl**: ゲーム内で発生した全イベント（市場結果・脱落・契約締結等）を記録。**reasoning は含まれない**。公開情報のみに近いが、開発・監査用途のため本来非公開の情報（例: 型B義務の詳細）も含まれる場合がある。運用上は開発者専用ログ。
- **player別 llm_calls.jsonl**: そのプレイヤーの意思決定に使われたプロンプト全文・応答全文・reasoning・トークン内訳・コストを記録。**神視点専用ログ**であり、他プレイヤーやビューワー閲覧者には公開しない前提。
- **ビューワーAPI**: 上記2種のログを加工し、秘匿情報（reasoning等）を除いた集計値のみを返す。

### 12.3 ビューワーが表示する主要情報

FastAPIサーバー（`viewer/server.py`、既定ポート **9025**、実運用 systemd では **9023**。§13.3参照）が提供するエンドポイント:

| エンドポイント | 内容 |
|---|---|
| `GET /` | ランディングページ（LP） |
| `GET /watch` | 観戦UI本体 |
| `GET /api/games` | トライアル一覧 |
| `GET /api/games/{trial_dir}/{game_id}/state` | 現在のゲーム状態（プレイヤーパネル用） |
| `GET /api/games/{trial_dir}/{game_id}/rounds` | ラウンドごとの状態（市場結果・高騰バッジ等） |
| `GET /api/games/{trial_dir}/{game_id}/commentary` | AI実況台本 |
| `GET /api/games/{trial_dir}/{game_id}/cost` | **コスト内訳**（モデル別・プレイヤー別。Stage 3で追加） |
| `GET /api/games/{trial_dir}/{game_id}/players/{pid}/timeline` | プレイヤー個別のコール履歴タイムライン |

### 12.4 市場高騰バッジ

`get_round_states()` が `MARKET_RESULT` イベントの `surged` フラグをそのまま `target["surged"]` として返し、UI側（`index.html`）が該当市場行に「高騰×2」バッジ（オレンジ太字、`.sit-surge` スタイル）を条件描画する。**表示専用**でありゲームロジックには影響しない（§3.7参照）。

### 12.5 Thinking / コスト内訳（Stage 3）

- プレイヤーパネルの `stats.cost` の隣に、`thinking_tokens > 0` のプレイヤーのみ 🧠 バッジ（例: `$0.0234 🧠15k`）を表示。
- ヘッダーの合計コスト表示をクリックするとモーダルが開き、以下を表示する:
  - **モデル別テーブル**: Model | Calls | Input | Cached | Output | Thinking | Cost（Thinking列は強調色）
  - **プレイヤー別テーブル**: Player | Model | Calls | Cost | Tokens(in/out/think)
  - **合計**: USD額 と 円換算（×150固定レート、§15参照）
- `get_cost_breakdown()`（`viewer/log_parser.py`）は `llm/models.py` の `estimate_cost()` と `MODEL_REGISTRY` を import して再利用しており、**独自のコスト計算式を重複実装していない**。

### 12.6 reasoning の秘匿境界（ビューワー側）

`get_game_state()`/`get_player_timeline()`/`get_cost_breakdown()` のいずれも `reasoning`/`_reasoning`/`response_text` 全文を返却しない。`get_player_timeline()` が返す `reasoning_preview` は `response_text` 先頭200字のプレビューであり、パース後の reasoning フィールド抽出ではない（構造的にreasoning本文がAPIレスポンスに乗る経路は存在しない）。

### 12.7 認証

`VIEWER_TOKEN` 環境変数が設定されている場合のみ、`?token=` クエリまたは `X-Viewer-Token` ヘッダでの照合が有効になる（未設定時は認証なし・公開アクセス）。

---

## §13. トライアル運用

*根拠: `scripts/llm_trial.py`, `scripts/run_trial.sh`, `scripts/check_trial.sh`*

### 13.1 Phase 種別

| Phase | 構成 | 目的 |
|---|---|---|
| A | LLM×1（既定 Haiku） + Random×(N-1) | 疎通試験 |
| B | LLM×2（既定 Haiku） + Random×(N-2) | 交渉試験 |
| C | LLM×N（全席LLM。roster指定） | 本番相当の全LLM対戦 |

### 13.2 主要CLIオプション（`scripts/llm_trial.py`）

| オプション | 説明 |
|---|---|
| `--phase {A,B,C}` | 実行フェーズ |
| `--ruleset {S1,S2}` | `S2` 指定時 `GameConfig.baseline_v1_s2(num_players=...)` を使用（既定 `S1` は `baseline_v1()`） |
| `--roster "M3,L2,M6,..."` | Phase C の座席割り当て（モデルキーのカンマ区切り）。省略時は既定8モデルロスター |
| `--rounds N` | ラウンド数の上書き（デバッグ用の早期打ち切り等） |
| `--cot` | `config.enable_cot = True` |
| `--games N` | 実行試合数 |
| `--seed N` | 乱数シード（既定42） |

**S2設定の単一ソース化ルール（不変条件）**: S2のゲーム設定は `engine/config.py` の `GameConfig.default_8_s2()` / `baseline_v1_s2()` プリセットのみが正であり、スクリプト側で個別に `GameConfig(...)` を再構築してS2相当のパラメータを再現することは禁止されている（過去にプリセット更新がスクリプトへ伝播せず、古い設定のままシミュレーションが回り続けるバグが発生したため）。

### 13.3 出力ディレクトリと生成物

`logs/llm/trial_{phase}_{YYYYMMDD}_{HHMMSS}/` 配下に §12.1 のファイル群が生成される。Phase Cではさらに `trial_C_report.md`（座席⇔モデル対応表、生還者一覧、モデル別集計（JSON率/AUTO/ERR/Calls/Input/Output/Cache/Think/Cost）、行動観察、strategy memoサマリを含む）が生成される。

### 13.4 デタッチ起動（`scripts/run_trial.sh`）

**長時間トライアル（12ラウンドフル試合やコスト$1超）は必ずデタッチ起動すること**（不変条件）。開発セッション（Claude/DevRelay）はSIGALRMタイムアウトを持ち、フォアグラウンド/子プロセスとして起動した長時間の `llm_trial.py` はセッションタイムアウトに巻き込まれてkillされる（過去にR10全体が停止する事故が発生）。

```bash
bash scripts/run_trial.sh --phase C --ruleset S2 --roster "M3,L2,M6,L5,L1,L4" --rounds 12 --cot --games 1 --seed 603
```

内部では `setsid nohup uv run python scripts/llm_trial.py "$@" > "$LOG_FILE" 2>&1 &` により親プロセスから完全に切り離して起動する。PIDは `logs/llm/run_YYYYMMDD_HHMMSS.pid`、標準出力は `logs/llm/run_YYYYMMDD_HHMMSS.log` に記録される。

### 13.5 進捗確認（`scripts/check_trial.sh`）

```bash
bash scripts/check_trial.sh                                        # 最新のtrial_C_*を自動検出
bash scripts/check_trial.sh logs/llm/trial_C_20260816_161614        # 特定ディレクトリを指定
```

PIDの生存確認（`kill -0`）、events.jsonl からの現在ラウンド・完了状態・生存者数の抽出、座席マップ表示、直近ログの末尾表示を行う。

---

# Part IV: 付録

## §14. 設計判断・不変条件

`rules/project.md` に明記されている不変条件、および実装から読み取れる重要な設計判断を列挙する。

1. **devlog は1サイクル1ファイル**。`doc/devlog/YYYY-MM-DD_HHMMSS.md` を新規作成し、`INDEX.md` 末尾に1行追記する。既存devlogへの追記・一括読み込みは禁止（コンテキスト肥大防止）。
2. **S2設定の単一ソース化**（§13.2参照）。
3. **契約 details キーの正規化は入口1箇所で行う**（§5.3参照）。
4. **EventLogger は `output_path` 指定時のみ逐次追記+flush**。未指定時はメモリ保持のみ（`simulate.py` 等の高速シミュレーション経路に副作用を与えない設計）。進行中ゲームをビューワーで観戦させたい場合は `EventLogger(output_path=...)` を明示する必要がある。
5. **card_id は手札内でも一意でない**（§4.3参照）。`use_card()` は1枚のみ除去、`swap_card()` は衝突時にリネーム（rank不変）。
6. **LLMLogger の post-hoc 更新には `save()` 呼び出しが必須**。`log_call()` は逐次書き込みだが、emotion/reasoningは `_update_last_log_emotion()` によりメモリ上のエントリのみ後付け更新される。ファイルへの最終反映には試合終了後の `LLMLogger.save()`（in-memory entriesでの全書き直し）が必要（過去にPhase Cでこの呼び出しが欠落し、emotion/reasoningがファイルに反映されないバグがあった）。
7. **長時間トライアルは必ずデタッチ起動する**（§13.4参照）。
8. **CoT reasoningフィールドは秘匿情報。情報リーク厳禁**（§10.3参照）。
9. **全員生還は算術的に不可能**（§1.5参照）。協調による全員生存の「温い均衡」は成立しない設計。
10. **場への純注入は市場賞金のみ**。利息・各種手数料・没収金は場からの純流出（総資産はゲーム進行とともに減少する構造）。

---

## §15. 確定パラメータ一覧

### 15.1 `GameConfig` 既定値 と `baseline_v1_s2()`（実運用S2プリセット）

| パラメータ | 既定 `GameConfig()` | `baseline_v1_s2()` |
|---|---|---|
| num_players | 20 | 引数で指定（既定8） |
| num_rounds | 12 | 12 |
| num_markets | 3 | 3 |
| loan_min / loan_max | 1,200,000 / 10,000,000 | 同左 |
| interest_rate | 0.015 | 同左 |
| entry_fee | 100,000 | 同左 |
| total_prize（20人時） | 19,200,000 | `19,200,000 × (n/20) × 2.0`（表示用フィールド、実効性は `要確認 #9`） |
| prize_tiers | 逓増（4R区切り: 120万/160万/200万） | フラット（総額を12等分＋端数調整） |
| market_distribution | "equal" | "equal" |
| survival_cash | 3,000,000 | **2,000,000** |
| contract_fee | 100,000 | 同左 |
| anon_broadcast_fee | 100,000 | 同左 |
| anon_broadcast_limit | 2 | 同左 |
| anon_bounty_surcharge | 0.10 | 同左 |
| fog_rounds | `[]` | `[]`（v0.7で削除。フィールドは残存, `要確認 #5`） |
| surge_enabled | False | **True** |
| surge_full_participation_max_alive | 0 | **3** |
| final_market_multiplier | 1 | **3** |
| double_up_enabled | False | **True** |
| mandatory_repay_enabled | False | **True** |
| mandatory_repay_k | 0 | 0 |
| card_trade_enabled | False | **True** |
| card_trade_max_per_round | 1 | 1 |
| card_trade_last_round | 11 | 11 |
| card_trade_broadcast_max | 5 | 5 |
| enable_cot | False | False（`--cot` で個別に上書き） |
| negotiation_max_actions | 10 | 10 |
| negotiation_max_turns | 10 | 10 |

### 15.2 その他の確定値

| 項目 | 値 |
|---|---|
| カード枚数 | 12枚/人（HIGH_CARD×2, ONE_PAIR×2, 他8役×1） |
| 円換算レート（ビューワー・レポート表示用） | 1 USD = ¥150（固定） |
| Free Cash 計算式 | `max(0, cash - debt_balance)` |
| 強制返済計算式 | `ceil(debt_balance / (remaining_rounds + k))`、`remaining_rounds = num_rounds - round_num + 1` |
| MAX_RETRIES（JSON不正時） | 2 |
| API_MAX_RETRIES（通信エラー時） | 2 |
| COST_LIMIT_PER_GAME | $5.0（1エージェントあたり） |
| COST_LIMIT_TOTAL | $12.0（試合全体） |
| ビューワー既定ポート | 9025（コード既定）/ 9023（systemd実運用, `要確認 #6`） |

---

## §16. v0.7 からの主要変更一覧

以下は `doc/changelog.md` および関連 devlog（v0.7策定 2026-08-13以降）に基づく、v0.7時点からの主要変更点である。歴史的経緯の詳細は各リンク先を参照。

| # | 変更 | 概要 | 日付 |
|---|---|---|---|
| 1 | **正式契約が交渉プロンプトに非表示だったバグの修正** | `PROPOSED` 状態の契約が当事者にすら見えず、署名が発生しない状態だった（`contract_investigation_report.md`）。`_build_visible_state()` に `for_player_id` を追加し、当事者にのみ `contracts_pending` を公開するよう修正 | 2026-08-14 |
| 2 | **強制返済（v0.7 §2）実装** | `mandatory_repay_enabled`/`mandatory_repay_k`、`compute_mandatory_repayment()`、Finance Step 2として組込み | 2026-08-14 |
| 3 | **カードトレード（v0.7 §3, v0.7.1）実装** | 1枚⇄1枚交換、ブロードキャスト提案対応、`swap_card()` | 2026-08-14 |
| 4 | **fog_rounds の削除反映** | S2プリセットの `fog_rounds` を `[4,8]` → `[]` に変更（v0.7 §10.2の設計判断をコードへ反映） | 2026-08-14 |
| 5 | **市場高騰の少人数全員参加要件・境界値変更** | `surge_full_participation_max_alive` 導入、S2プリセットで 4→3 に調整 | 2026-08-14 |
| 6 | **カードトレード・倍掛けのLLM対応** | それまでBot専用だった2アクションをLLMエージェントからも発行可能に（`response_parser.py`/`llm_agent.py`拡張） | 2026-08-14 |
| 7 | **倍掛けのソロ市場除外バグ修正** | 空き巣市場での勝利が誤って倍掛け成功条件に含まれていた問題を修正（§6.4参照） | 2026-08-14 |
| 8 | **S2設定の単一ソース化** | `simulate.py`等が個別にS2設定を再構築していた問題を解消、プリセット関数への統一 | 2026-08-14 |
| 9 | **type_b_card details キー不一致バグ修正（重大）** | `"card"` vs `"card_rank"` の不一致により正しくカードを提出したプレイヤーが常に契約違反判定されるバグ（R7全滅の主因）を修正。`create_contract()` 入口正規化を実装（§5.3参照） | 2026-08-15 |
| 10 | **契約義務の可視化（毎プロンプト注入）** | 署名済み未履行義務一覧を negotiation/commit/double_up 全フェーズのプロンプトに注入 | 2026-08-15 |
| 11 | **最終市場3倍ルールの周知漏れ修正** | RULES_SUMMARYに記載が漏れておりエージェントが事前に知り得なかった問題を修正（§9.3, §3.6参照） | 2026-08-15 |
| 12 | **EventLoggerの逐次追記化** | `output_path` 指定時の即時追記+flush対応。進行中ゲームのビューワー観戦を可能に | 2026-08-15 |
| 13 | **ビューワーの市場高騰バッジ追加** | `surged` フラグの表示（§3.7, §12.4参照） | 2026-08-15 |
| 14 | **card_id トレード重複バグ修正（最重要）** | `use_card()`の全一致除去による手札ドレインを1件除去に修正、`swap_card()`のリネーム処理を追加（§4.3参照） | 2026-08-16 |
| 15 | **コスト単価是正（Gemini/Grok/DeepSeek）** | Gemini 3.5系が旧世代単価のまま10〜15倍過小だった問題を是正。Grok 4.3単価是正。DeepSeek `deepseek-chat`→`deepseek-v4-flash`のmodel_id・単価是正（§11.3参照） | 2026-08-16 |
| 16 | **トライアルのデタッチ起動化** | `scripts/run_trial.sh`/`scripts/check_trial.sh` 新規追加（§13.4参照） | 2026-08-16 |
| 17 | **CoT(reasoning)実装** | `enable_cot` フラグ・`--cot` CLI引数・秘匿境界の実装（§10参照） | 2026-08-16 |
| 18 | **コストログ化 Stage 1** | トークン内訳（cached/reasoning/total）の捕捉、ログスキーマ拡張 | 2026-08-16 |
| 19 | **コストログ化 Stage 2** | `ModelInfo` に `cached_input_price`/`reasoning_price` 追加、`estimate_cost()` のキャッシュ割引・thinking二重計上防止ロジック実装、確定単価反映（§11.2参照） | 2026-08-16 |
| 20 | **コストログ化 Stage 3** | ビューワーへのコスト内訳モーダル（モデル別・プレイヤー別）追加、`get_cost_breakdown()`実装、試合サマリーレポートへのトークン列拡張（§11.2・§12.5参照） | 2026-08-16 |

---

## §17. 要確認事項一覧

以下はコードの記載のみからは断定できず、実装間・ドキュメント間で不一致が見られた項目である。ゲーム運営方針として確定させる場合は、別途意思決定の上で本書を更新すること。

| # | 論点 | 詳細 | 根拠 |
|---|---|---|---|
| 1 | **プレイヤー数の正式仕様** | CLAUDE.md/README は「AI 20体」を謳うが、`GameConfig` 既定も20人。一方、実運用の Phase C トライアルは roster長（直近6〜9体規模）で動いている。「20体」は将来の本番構成であり日常検証は縮小版という理解でよいか | CLAUDE.md, `engine/config.py:21`, `scripts/llm_trial.py:1003` |
| 2 | **survival_cash の正式値** | 既定 300万円 vs 実運用 `baseline_v1_s2()` の 200万円。どちらを仕様上の正式値として案内するか | `engine/config.py:56,210` |
| 3 | **賞金スケジュールの正式仕様** | 既定は逓増（v0.5 §4.1由来）、実運用 `baseline_v1` はフラット×2.0。両者は別物であり、v0.5/v0.6/v0.7の逓増記述は実運用と一致しない | `engine/config.py:47-50,200-211` |
| 4 | **Settlement のStep数表記** | 実装は8Step、v0.7 §8は「11Step」と表記。本書は実装(8Step)を正としたが、旧仕様書との数値差は要周知 | `engine/settlement.py:62-69` vs v0.7 §8 |
| 5 | **fog_rounds フィールドの扱い** | v0.7で霧のラウンドは設計上削除されたが、`GameConfig.fog_rounds` フィールドおよび Settlement Step 1 のマスク処理コードは実装として残存している（現在は常に空リストなので無効化状態）。将来的に削除するか、「無効化済みの休眠機能」として維持するか | `engine/config.py:73`, `engine/settlement.py` |
| 6 | **ビューワーの既定ポートと実運用ポートの不一致** | コード既定は9025だが、実運用の systemd unit は9023を使用（環境変数で上書き）。ドキュメント上どちらを案内するか統一が必要 | `viewer/server.py:31`, `~/.config/systemd/user/dangou-viewer.service` |
| 7 | **Gemini系（M3/L3）の cached_input_price 未設定** | Google の Context Caching API は別課金体系（$/1Mトークン/時間）であり、現行の `cached_input_price` フィールドの意味論と整合しないため未設定（TODOコメントあり）。是正方針は未確定 | `llm/models.py` M3/L3 定義部 |
| 8 | **reasoning_price が全モデルで None** | 現行は全モデルで `reasoning_price is None`（output_price を代用）。Gemini/Grok のネイティブthinkingトークンが実際に output と同一単価で課金されているか要検証（プロバイダ料金表の再確認が必要） | `llm/models.py` |
| 9 | **`total_prize` フィールドの実効性** | `baseline_v1()` では `total_prize` と `prize_tiers` の合計が一致するよう構築されているが、実際の賞金支払いロジックは `prize_tiers` のみを参照する。`total_prize` は表示・検証用の付随フィールドであり、単独で賞金総額を変更しても効果が無い認識でよいか | `engine/config.py:44,200-211`, `engine/market.py` |
| 10 | **market_distribution の "weighted"/"random" の運用状況** | コード上は実装されているが、S2実運用は常に `"equal"`。他方式が今後使われる予定があるか不明 | `engine/config.py:52`, `engine/market.py:41-57` |

---

*本書は `doc/uso8000000_dangou_card_spec_v0_7_season2.md` までの差分参照方式を廃止し、Season 2 の現行実装を単独で説明する完全版として新規作成された。今後のルール変更は、実装変更と同一のcommit/サイクルで本書へ反映することを推奨する。*
