# Free Cash 参照箇所の棚卸し（v0.9 設計準備）

- 作成日: 2026-08-30
- 種別: 読み取り専用の調査文書（コード・テスト・設定は無変更）
- 目的: v0.9 で Free Cash（`max(0, cash − debt_balance)`）を廃止し、プレイヤー間の価値移転
  （送金・型A契約・報奨預託・カードトレード現金）を現金ベースへ変更する構想の実装指示書を
  書く前に、依存箇所を漏れなく洗い出し、置き換え方式の是非を評価する。
- 対象外: `scripts/llm_trial.py` 関連プロセス・`logs/`（実行中の LLM 本戦に触れないため）。
  本調査中に `pytest` / `dry_run.py` / `simulate.py` / ボット試合は一切実行していない。

---

## 項目1: `engine/` の free_cash 参照（全件）

| 種別 | 箇所 | 内容 |
|---|---|---|
| **定義** | `engine/models.py:248-257` | `PlayerState.free_cash` は `@computed_field` の `@property`。`return max(0, self.cash - self.debt_balance)`。docstring に「借金横流し防止のため、借金分を差し引いた残りのみが自由に使える」と明記 |
| **判定関数** | `engine/player.py:53-63` | `can_pay_free_cash(player, amount) -> bool` = `player.free_cash >= amount`。同ファイル `:40-50` の `can_pay()`（Entry Fee等システム向け・`cash >= amount`）と対を成す |
| **ゲート①送金** | `engine/actions.py:110-111` | `action.amount > player.free_cash` なら `ActionResult(False, "Insufficient free cash for transfer")` |
| **ゲート②報奨預託** | `engine/actions.py:246-249` | 匿名掲載時は `deposited = amount + ceil(amount * config.anon_bounty_surcharge)` を算出してから `deposited > player.free_cash` で判定 |
| **ゲート③トレード提案** | `engine/actions.py:293-294` | `action.cash_amount > 0`（自分が現金を払う側）のときのみ `cash_amount > player.free_cash` を判定。受け取る側（負値）や交換のみ（0）はここでは判定しない |
| **ゲート④トレード成立時・提案者側再検証** | `engine/game.py:857` | `proposal.cash_amount > 0 and proposal.cash_amount > proposer_state.free_cash` → `EXPIRED`（v0.8 E10で提案者へ通知） |
| **ゲート⑤トレード成立時・受諾者側再検証** | `engine/game.py:870` | `proposal.cash_amount < 0 and abs(proposal.cash_amount) > accepter_state.free_cash` → `EXPIRED`（同上、受諾者へ通知） |
| **ゲート⑥型A契約 Atomic 執行** | `engine/settlement.py:358-364`（スナップショット撮影）→ `engine/contracts.py:302-315`（判定） | Settlement Step4 で生存者全員の `{"cash": p.cash, "free_cash": p.free_cash}` を固定。Step5 で義務者ごとの支払合計 `total` と `snap["free_cash"]` を比較し、`total <= snap["free_cash"]` なら義務者の全件を一括履行、1円でも不足なら**全件履行不能→義務者は脱落対象** |
| 表示のみ | `engine/commentary/prompts.py:159`, `engine/commentary/trace.py:28,202` | 実況生成用の決算後残高フォーマット（`現金○, FreeCash○`） |
| 表示のみ | `engine/blog/round_situation.py:73-75,80,103` | ブログのラウンド別現金推移グラフ。SNAPSHOT の `free_cash` を採用（`cash` は市場コミット等で拘束された分を含む総額で生還判定と乖離するため、と明記） |

**支払可否を左右する実ゲートは6箇所**（①〜⑥）。残りはログ・実況・ブログ表示専用で判定ロジックには関与しない。

---

## 項目2: `llm/prompt_builder.py` の Free Cash

### 文字列出力（25箇所）

`:750`（トレード失効通知の側ラベル「Free Cash不足」）/ `:867`（借入額公開時の「現金・借金残高・Free Cashは秘匿」注記）/
`:1173`（不能アクション理由「transfer・bounty_post（Free Cash 0）」）/
`:1304` `:1309`（借入・利息セクション。`:1309`「開始時は全員Free Cash=0（Cash=借入額=Debt）」）/
`:1311-1313`（RULES_SUMMARY の「## Free Cash = max(0, 現金 − 借金残高[利息込み])」節。定義・適用対象・非適用対象を明記、約275字）/
`:1317`（Negotiation説明中の「Free Cash十分か確認すれば避けられる」）/
`:1324`（スナップショット説明「市場賞金・倍掛け払出反映後のCash/Free Cashを固定」）/
`:1334-1335`（型A/型B説明。型Aは「支払能力は執行時のFree Cashで判定」）/
`:1343-1344`（匿名通信費「Free Cash制限外」／公開報奨「Free Cash制限対象」）/
`:1351`（秘匿情報一覧「現金・借金残高・Free Cash」）/
`:1360`（送金「双方のCash/Free Cashへ即反映」）/
`:1395`（トレード提案の cash_amount 注記「Free Cash以内」）/
`:1402` `:1407` `:1408`（アクション便益・コスト表。transfer / bounty_post / card_trade の3項目）/
`:1555` `:2050` `:2406`（「あなたの状態」欄の「Free Cash: ○万円」表示、3箇所で同一パターン）/
`:1750`（トレード受諾候補への「（Free Cash ○円以内で可）」）

### 分岐（3箇所）

- `:1169-1173` — `if player_state.free_cash > 0:` で `transfer` / `bounty_post` を可能アクション一覧へ追加。0円なら代わりに「transfer・bounty_post（Free Cash 0）」を不能理由として列挙
- `:1750` — トレード受諾候補向けの立場説明で `cash < 0`（自分が払う）のとき Free Cash 額を注記
- `:2405-2406` — **`free_cash` を `PlayerState` の computed field ではなく、ローカル変数として `max(0, player_state.cash - player_state.debt_balance)` で再計算**している唯一の箇所（double_up 系プロンプト内）。他の22箇所は全て `player_state.free_cash` を直接参照

### 契約発行料との連動

`:1313` の「非適用（システム向け支払い）」リストは末尾に `{contract_fee_freecash_note}` を埋め込んでおり、`:1440-1451` で `config.contract_fee == 0` なら空文字列、非0なら「、契約発行料」を追加する分岐になっている。S2プリセット（`engine/config.py:308`、`baseline_v1_s2`）は `contract_fee: 0` のため、S2実戦では「契約発行料」の語は出力されない。**`entry_fee`（100,000円、`config.py:51`）とは別の設定値であり、混同しないこと**（S2で0円になるのは契約発行料のみ。Entry Feeは通常どおり課金される）。

---

## 項目3: 借入フェイズのプロンプト

- `build_loan_prompt`（`llm/prompt_builder.py:1501-1512`）に **Free Cash の記述は無い**。内容は借入額レンジ・利息率・生還条件・JSON出力例のみで、`config.free_cash_*` 系の値も参照していない
- ゲーム開始時点で LLM が Free Cash 概念に触れるのは **system prompt 側のみ**（`:1309` の「開始時は全員Free Cash=0」＋ `:1311-1313` の定義節）。ユーザープロンプト（`build_loan_prompt`）は無関係
- → v0.9 で Free Cash を廃止・変更する場合、`build_loan_prompt` 自体の書き換えは不要。system prompt の2箇所（定義節と初期状態注記）を直せば足りる

---

## 項目4: テスト

`free_cash` / `FreeCash` / `Free Cash` / `自由資金` を含むテストは **11ファイル・82箇所**。

| ファイル | 件数 | 性格 |
|---|---|---|
| `tests/test_free_cash.py` | 23 | Free Cash 仕様そのものを検証する中核ファイル。廃止・変更時は全面書き換え対象 |
| `tests/test_acceptance.py` | 21 | 受け入れテスト（§2.4 Free Cash 定義の受け入れ条件を含む） |
| `tests/test_atomic.py` | 7 | 型A Atomic 執行。スナップショットの `free_cash` キーに依存 |
| `tests/test_cycle5_prompt_salience.py` | 8 | プロンプト中の Free Cash 文言・提示条件 |
| `tests/test_prompt_action_paths.py` | 8 | `:1169-1173` の分岐（可能/不能アクション列挙）を検証 |
| `tests/test_viewer.py` | 5 | ビューア側の `free_cash` 表示・`debt` 逆算ロジック |
| `tests/test_card_trade.py` | 3 | トレードの現金支払いゲート（③④⑤） |
| `tests/test_prompt_v08.py` | 3 | v0.8 プロンプト文面の Free Cash 関連文言 |
| `tests/test_commentary.py` | 2 | 実況生成の残高フォーマット |
| `tests/test_s2_rules.py` | 1 | |
| `tests/test_llm.py` | 1 | |

---

## 項目5: `bots/` と `scripts/`

- **`bots/`（8体）に Free Cash ゲートは0件。** `bots/base.py:43` にコメントで「借金残高がゼロなら返済不要。FreeCash非適用（§2.4: システム向け支払い）」とあるのみで、いずれのボットも `free_cash` / `can_pay_free_cash` を判定に使っていない
- `scripts/simulate.py` / `scripts/dry_run.py` も **参照0件**
- `scripts/blog.py:756-764` — 表示用途。survival_check が無い場合のフォールバックとしてスナップショットの `free_cash` を使用（「生cashは借金控除前で過大表示になるため」とコメント）
- `scripts/commentary.py:146` — 実況テキスト生成の残高フォーマット（`engine/commentary/prompts.py:159` と同型）
- `scripts/final_reflection_smoke.py:123` — テストダミーデータの日本語文中に「Free Cash」の語が1件（コード判定ではなく文字列）
- `scripts/dump_prompts.py:423` — ダンプ文書内の注記文字列（「Free Cash 0の席」）
- → **ボット・シミュレーション本体（判定ロジック）は Free Cash 廃止の影響を受けない。** 影響が出るのはブログ・実況の表示部分のみ

---

## 項目6: 置き換え方式の評価

### 決定的な事実 — Entry Fee は既に cash から実引き落としされている

```
engine/game.py:992    if not player_ops.can_pay(p, self.config.entry_fee):   # 不足→破産脱落（forced_liquidation）
engine/game.py:1008   p = player_ops.pay(p, self.config.entry_fee)           # 実際に cash を減算
engine/player.py:66-79  pay() は player.model_copy(update={"cash": player.cash - amount}) で cash を直接減らす
```

Commit フェイズ（`_phase_commit`, `engine/game.py:976` 以下）で、生存プレイヤー全員に対し `:992` の支払能力チェック→ `:1008` の実引き落としが**その場で**行われる。Entry Fee は「請求されるが未控除」ではなく、Commit の時点で cash から即座に消える。

### なぜ `free_cash = max(0, cash − entry_fee)` への単純置換は不可か（3つの理由）

1. **フェイズによって意味が反転し、Settlement 側で二重控除になる。**
   送金・報奨・トレードのゲート①〜⑤（`actions.py` / `game.py:857,870`）は Negotiation フェイズ＝**Commit 前**に評価される。この時点で `cash − entry_fee` を計算するのは「これから払う今RのEntry Feeを残しておく」という予約の意味で筋が通る。
   一方、型A Atomic のスナップショット（ゲート⑥、`settlement.py:358-364`）は **Settlement フェイズ＝Commit 後**に撮影される。この時点の `p.cash` には**既に今RのEntry Feeが引かれている**（`:1008` で先に減算済み）。ここでさらに `entry_fee` を引く定義を適用すると、同じEntry Feeを2回差し引くことになり、本来払えるはずの型A義務が `contracts.py:312-314`（「1円でも不足→全件履行不能→脱落」）に該当して**理不尽な脱落**を生む。

2. **借金横流し防止という現行 Free Cash の設計目的が失われる。**
   `engine/models.py:251-256` のdocstringに明記の通り、現行 Free Cash は「借入金をそのまま他人へ流すこと」を防ぐための仕組み。`entry_fee` 基準に差し替えると、R1開始時点で借入400万円のうち390万円（400万−Entry Fee 10万）を初手で送金できてしまう。v0.9が目指す「現金ベース化」の趣旨が「プレイヤー間移転の可否判定を借金構造から切り離す」ことだとしても、`entry_fee` を代替の閾値にするのは元の防止目的と整合しない中途半端な案になる。

3. **`free_cash` computed field の定義を変えると表示・ログ系が無警告で壊れる。**
   - `viewer/log_parser.py:396-406` と `:1365-1376` は **`debt = cash − free_cash` で借金残高を逆算**している（`# debt = cash - free_cash（free_cash > 0 のとき正確）` とコメントあり）。`free_cash` の定義式を変えれば、この逆算結果（ビューアの借金表示）が黙って不正確になる。
   - `engine/blog/round_situation.py:73-75` は「生還ライン（現金≧survival_cash）と同じ土俵」として明示的に `free_cash` を選んでいる。定義を変えると、この対応関係も崩れる。
   - これらは全て `free_cash` という**名前とキー**に依存しており、値の意味を変えても検知するテストが直接は存在しない（`tests/test_viewer.py` はビューア出力の形は見るが、意味的な整合は見ていない）。

### 推奨方針

**A. `PlayerState.free_cash` の定義（`max(0, cash − debt_balance)`）は変更しない。** 表示・ログ・ビューアの後方互換を保つ。

**B. 「プレイヤー間移転の支払可否判定」を Free Cash から切り離し、`GameConfig` でモード切替できる別概念にする。**

```
GameConfig.free_cash_mode: Literal["debt", "cash", "entry_fee"] = "debt"
  "debt"      … 現行と同一（max(0, cash − debt_balance)）。既定値とし、既存1547テストを無改修で通す
  "cash"      … v0.9 本命。素の cash をそのまま判定に使う（借金横流し防止を撤廃）
  "entry_fee" … 参考実装。Negotiation期のみ「未払いの今R Entry Fee」を予約し、Settlement期（Commit後）
                 では控除しない（理由1の二重控除を避けるため、フェイズによって計算式を変える必要がある）
```

- `engine/player.py` に `spendable_cash(player: PlayerState, config: GameConfig, *, phase: str) -> int` を新設し、`can_pay_free_cash` はこれを呼ぶ薄いラッパに変更
- ゲート①〜⑤（`actions.py:110,248,293`／`game.py:857,870`）を `spendable_cash(..., phase="negotiation")` 経由に置換
- ゲート⑥は `settlement.py:358-364` のスナップショットへ **`"spendable"` キーを追加**（`cash` / `free_cash` は現状維持のまま残す＝ビューア・ブログ互換を壊さない）。`contracts.py:302-315` の比較対象を `snap["spendable"]` に変更

**C. プロンプト側は文言を集約してから差し替える。** `llm/prompt_builder.py:1311-1313` の Free Cash 定義節を `free_cash_mode` に応じた文言テンプレートへ、`:1169-1173` の分岐条件を `spendable_cash(...) > 0` へ、`:2405-2406` のベタ書き再計算を共通ヘルパ（`spendable_cash` 呼び出し）へ統一する。文字列出力25箇所のうち、定義・適用条件に触れる `:1173,1304,1309,1311-1313,1317,1334-1335,1343-1344,1360,1395,1402,1407,1408,1750` は用語または値の見直しが必要。純粋な「あなたの状態」表示（`:1555,2050,2406` の「Free Cash: ○万円」行）は `spendable_cash` の値をそのまま出す形で維持できる。

**D. 影響ファイル一覧（v0.9 実装指示書の骨子）**

| ファイル | 変更内容 |
|---|---|
| `engine/config.py` | `GameConfig.free_cash_mode` フィールド追加 |
| `engine/player.py` | `spendable_cash()` 新設、`can_pay_free_cash()` をラッパ化 |
| `engine/models.py` | `free_cash` は無変更（docstringに `free_cash_mode` との関係を注記する程度） |
| `engine/actions.py` | ゲート①②③を `spendable_cash()` 経由に |
| `engine/game.py` | ゲート④⑤（`:857,870`）を `spendable_cash()` 経由に |
| `engine/settlement.py` | スナップショットに `spendable` キー追加（`:358-364`） |
| `engine/contracts.py` | Atomic判定を `snap["spendable"]` 参照に変更（`:302-315`） |
| `llm/prompt_builder.py` | 定義節・分岐・「あなたの状態」表示、計25箇所を `free_cash_mode` 対応に |
| `viewer/log_parser.py` | `spendable` キーが増える場合の追随（`cash`/`free_cash` ベースの既存ロジックは無改修でよい） |
| `scripts/blog.py`, `engine/blog/round_situation.py`, `engine/commentary/*` | 表示に `spendable` を使うか `free_cash` のままにするかの方針決定が必要（現状は維持でも壊れない） |
| `tests/` | 該当11ファイル・82箇所。`test_free_cash.py` は `free_cash_mode="debt"` 前提のケースと `"cash"` 前提のケースへの分割が必要になる見込み |
| `bots/`, `scripts/simulate.py`, `scripts/dry_run.py` | **無改修**（項目5の通りFree Cashゲート非依存） |

既定値 `free_cash_mode="debt"` を維持する限り、上記変更後も現行1547テストは無改修で通る設計とし、v0.9 実戦投入時に `"cash"` へ切り替える段階移行を推奨する。
