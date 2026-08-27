# Changelog

## 2026-08-27: Cycle 6.1: thinking×temperature競合の修正、xAI reserve実測反映、Phase2上限追随

Cycle 6監査で判明した3件の保留事項（Moonshot/Anthropicのthinking×temperature 400、
grok-4.5のタイムアウト、grok-4.6のreserve過小見積）と、けいすけさん承認プランの4項目を実装した。

- **Fix 1（thinking×temperature競合）**: `llm/adapters.py`に
  `THINKING_TEMPERATURE_LOCKED_PROVIDERS = ("Anthropic", "Moonshot")`と
  `_thinking_enabled_payload()`を追加。thinkingが有効化されたリクエストでは
  `temperature=1.0`を強制する。本走（`llm/llm_agent.py`）は`request_options`を渡さず
  Moonshot既定の`extra_params`は`{"thinking":{"type":"disabled"}}`のため、L1〜L6の
  送信内容は不変（新規snapshotテストで完全一致を固定）。
- **Fix 2**: `grok-4.5`(M4)の`timeout_seconds`を60→180へ引き上げ（H4と同型のxAI常時思考対策）。
- **Fix 3**: `hidden_thinking_reserve_tokens`をH4 1536→9728、M4 1024→2560へ引き上げ。
  Cycle 6実測（H4 reasoning_tokens=6756）とCycle 6.1検証スモーク実測（H4=7746、M4=2035）の
  両方を下回らないよう`ceil(max_measured * 1.2)`を512単位へ切り上げて算出。
- **予算追随**: `scripts/model_matrix.py`の`PHASE_DEFAULTS[2]["max_cost"]`を0.25→0.40へ
  引き上げ（CORE_18予約合計$0.24067398→$0.37276999、headroom 6.8%を確保。人間判断による調整、
  予約計算ロジック自体は無変更）。
- **検証スモーク**: L1/M5/H5/M4/H4の5モデル各1callを実施（上限$0.60、実費$0.275279）。
  M5/H5はFix 1により成功（temperature=1.0強制で400解消）。L1は別種のHTTP 400
  （"adaptive thinking is not supported on this model"、Fix 1のスコープ外のモデル能力制限）が
  残存し保留。M4/H4はタイムアウト・費用超過なく成功。
- **テスト**: 新規`tests/test_thinking_temperature.py`17件（API 0コール、L1〜L6の送信dict
  完全一致snapshotを含む）、`tests/test_registry_18.py`/`test_model_matrix.py`/
  `test_model_smoke.py`の期待値を新reserve値・新Phase2上限へ更新。全体`uv run pytest tests/ -q`
  1401 passed。
- 詳細は`doc/devlog/2026-08-27_193137.md`・`.devrelay-output/registry_fix_6_1_20260827_193137.md`を参照。
  ゲーム本走・Viewer再起動・commit/pushはCycle 6.1では実施していない（本コミットが初）。

## 2026-08-27: Cycle 6: モデルレジストリ監査（18体ロスター前提）

18体ロスター（6社×上位/中位/下位）へ進む前に、`llm/models.py`のレジストリ全21〜22
エントリを実在性・単価・思考設定・タイムアウトの観点で監査した。ゲームルール・`engine/`は無変更、
修正はレジストリ設定値のみ。

- けいすけさん提供の2026-08-16版価格表を`doc/llm_api_pricing.md`として新規作成（二次資料、
  単価変更の根拠にはしない）。
- `llm/models.py`を修正: H4 `grok-4.6`の`timeout_seconds`60→180（5.5実測61.2秒×2の裏付け）、
  TERRA `gpt-5.6-terra`を`max_completion_tokens`パラメータ・`supports_temperature=False`で
  修正（5.5のHTTP 400裏付け）、`kimi-k2.5`実在確認用の評価専用エントリ`K25_EVAL`を新規登録。
- 15モデル×1callスモーク（累計実費$0.3007、上限$1.00の約30%）で検証: H4が123.5秒で成功
  （旧60秒なら確実にタイムアウト）、TERRAのHTTP 400解消、`kimi-k2.5`の実在確認。
- **新規に3種の障害クラスを発見**（いずれも`llm/adapters.py`変更が必要でCycle 6の権限外のため
  未修正・報告のみ）: (1) Anthropic/Moonshot系がthinking有効時に`temperature`制約で400、
  (2) `gpt-4.1`系がreasoning_effort非対応で400、(3) `grok-4.5`(M4)も60秒タイムアウト。
  (1)と(3)はCycle 6.1で解消（上記参照）。
- 詳細は`doc/devlog/2026-08-27_074110.md`・`.devrelay-output/registry_audit_20260827_072456.md`を参照。

## 2026-08-26: Cycle 5.5: 上位・思考モデル replay probe

5.1〜5.4で軽量・非思考モデルの契約系アクション転換率が伸び悩んだため、「モデルの推論能力の
問題か」を切り分ける目的で上位・思考モデル4種へ同一手番（19件）をreplayした。

- `scripts/replay_probe.py`に`--model-override <key>`（`dataclasses.replace`でモデルのみ
  差し替え）と`--thinking {off,medium}`（provider別`request_options`テーブル）を追加。
  既定モードの出力は`diff -rq`でバイト一致を確認済み（非回帰）。
- `llm/models.py`にQ2で許可された4設定値のみで`gpt-5.6-terra`（TERRA）を新規登録。
- 4モデル1callスモークで**deepseek-v4-pro**（thinking有効でJSON崩壊→無効へフォールバックし成功）、
  **claude-sonnet-5**（成功）、**gpt-5.6-terra**（HTTP 400、対応はレジストリ変更が必要で範囲外→
  Cycle 6で解消）、**grok-4.6**（タイムアウト、同→Cycle 6で解消）を確認。
- 残った2モデル（deepseek-v4-pro=thinking off、claude-sonnet-5=thinking medium）で19件×2モデル
  ＝38callの本計測を実行。契約系変換率は両モデルとも0/19（合算0/38）で、5.4の非思考モデル
  patched 1/38（2.63%）を上回らなかった。実測コスト$0.474460（スモーク込み$0.501524、上限$4.00の
  約12.5%）。
- 詳細は`doc/devlog/2026-08-26_224230.md`・`.devrelay-output/replay_probe_report_5_5_20260826_224230.md`を参照。

## 2026-08-26: Cycle 5系: D1（契約系アクション消滅）のprompt側修正と、replay probeによる4回のA/B実測

`ac844b3`（Cycle 2-4）以降の clean L12×R12 実戦・全面監査で最重要欠陥 D1 を特定し、
その修正が実際に効くかを過去ログ再生ハーネスで4回計測した一連のサイクルをまとめる。

- **背景（D1）**: clean L12×R12（`trial_C_l12_r12_20260824`、$6.8108 / 1,449 call /
  1,697 event）で`contract_propose`11→0・`contract_sign`11→0・`card_trade_propose`1→0。
  funnelのATTEMPT=0（engineの拒否でもparser落ちでもない）、モデル出力の「契約」言及
  31.6%→6.2%。原因はpromptの便益記述ゼロ＋締め指示がS2の6アクションを1つも
  列挙していないことに絞り込まれた。
- **Cycle 5（修正本体）**: `llm/prompt_builder.py`に(1)便益とコストを同格に並べる
  新セクション、(2)失敗コスト文の条件文化（中立化）、(3)締め指示でその時点の選択可能
  アクション全列挙、(4)倍掛け成功条件の毎回再掲、(5)Handover Memoryへの「公式状態優先」
  明記、の5方向を実装した。**`engine/`は1行も変更していない**（仕様§13.2「S2は観察対象の
  まま、ルール変更による強制は行わない」に従う）。system_promptは5,901→7,079字。
- **Cycle 5.2**: 締め指示に「対応付け説明句」を追加（user_prompt+132〜179字、
  system_promptは7,079字のまま不変）。
- **予算追随**: system_prompt増加でCORE_18の予約合計が`PHASE_DEFAULTS[2]["max_cost"]`
  （$0.23）を超えるため、人間判断で**$0.23→$0.25 / per-model cap $0.03→$0.035**
  （`scripts/model_matrix.py`）。予約計算ロジック・他Phase・モデル単価は無変更。
  追随して`tests/test_phase2_schema.py`（h1 < 0.035、M3 $0.0158775、H3 $0.02597）と
  `tests/test_model_matrix.py`（H1 $0.031845、CORE_18合計$0.24124109・headroom
  $0.00932601 / 3.87%）の期待値を更新。
- **`scripts/replay_probe.py`（新規・API-free既定）**: `trial_C_l12_r12_20260824`の
  実promptを**読み取り専用で**再生し、baseline（当時のまま）とpatched（現行コードで
  再生成）を同一モデルへ投げて対比するA/Bハーネス。`--dry-run`（既定・API0件）/
  `--execute` / `--preflight-only`の3モード、事前見積ゲート（`--max-cost-usd`）とコール
  単位の予算予約による二重ガード、Wilson 95% CI、Cycle 5.4で**McNemar正確二項検定**
  （`math.comb`のみ・scipy不使用）と`--targets-file`による選抜差し替え（schema v1・
  role分割集計）を追加。既定27件モードの出力はbyte単位で不変（`diff -r`実測）。
- **計測結果（契約系変換率 = `contract_propose`/`card_trade_propose`/`bounty_post`）**:

  | Cycle | 対象の選び方 | N | baseline | patched | 検定 | 実測コスト |
  |---|---|---|---|---|---|---|
  | 5.1 | 契約を口にした手番27件 | 3 | 0/81 | 2/81 (2.47%) | p=0.50 | $0.528249 |
  | 5.3 | 同上（5.2の締め指示で） | 3 | 2/81 | 1/81 (1.23%) | p≈1.0 | $0.281507 |
  | 5.4 | **合意の返事の直後の手番38件** | 1 | 0/38 | 1/38 (2.63%) | b=1,c=0 p=1.0 | $0.283179 |

  5.4はrole別にproposer 0/19→1/19、replier 0/19→0/19。転換した1件は相手の破産危機を
  契機にしたもの。**いずれも上限$0.90内、有意差なし。**
- **結論**: prompt側のsalience修正だけでは、小型・非思考モデル群の契約系アクション
  選択率はほぼ動かない。engineの変更は依然として行っていない。
- **テスト**: 新規`tests/test_cycle5_prompt_salience.py`33件（5方向の文言を文字列
  アサーションで固定）、新規`tests/test_replay_probe_targets.py`39件（既定27件モードの
  golden order回帰・targets-file検証・McNemar golden vector・role分割paired集計）。
  全体**1343 passed**。
- 詳細は`doc/devlog/2026-08-25_053950.md` / `2026-08-25_054023.md` /
  `2026-08-26_064521.md` / `084625.md` / `095502.md` / `130007.md` / `165524.md`を参照。

## 2026-08-24: Cycle 4: Phase2予算上限調整 + anonymous_broadcast送信者本人self-marker

Cycle 3 Human Review（GO）で確定した2件の積み残しをまとめて実装した。

- **Part A（予算）**: Cycle 3のPrompt追加（129文字増）でCORE_18予約合計が`PHASE_DEFAULTS[2]["max_cost"]`
  （$0.22）を$0.0011987（0.54%）超過し`xfail(strict=True)`で可視化していた件、人間判断により
  `scripts/model_matrix.py`の同値を$0.23へ引き上げてxfailを解除した（headroom $0.0088013/3.83%を確保）。
  予約計算ロジック・他Phaseの`max_cost`・per-model cap・モデル単価は無変更。
- **Part B（self-marker）**: `anonymous_broadcast`の送信者本人が自分の匿名発言をメタデータ上識別できず、
  次ラウンドのreflection prompt（＝Handover Memory生成入力）で自分の発言を他人のものと誤認する余地が
  あった。`llm/prompt_builder.py`はvisible_stateしか受け取らず送信者情報が無いため、`engine/game.py`に
  `_anon_broadcast_owners: dict[int, str]`（`_round_messages`のindex→実送信者、`_god_transcript`には積まない
  私的情報）を追加し、`_reset_round_message_state()`で`_round_messages`と**同一関数内で同時にクリア**する
  設計とした（片方だけ残すと新ラウンドでindexが再利用された際に前ラウンドの所有者が誤適用される
  state corruptionを構造的に排除するため）。`_visible_messages()`は本人向けコピーにのみ`"is_mine": True`を
  付与（他プレイヤー・Bot経路はキー自体が無い、既存の`redacted`と同じ慣習）。`llm/prompt_builder.py`は
  `is_mine`が真のとき`[匿名/あなたが送信]`、それ以外は従来どおり`[匿名]`。`RULES_SUMMARY`・戦略助言文言は
  無変更。`engine/actions.py`/`config.py`/`settlement.py`/`viewer/**`は無変更。

新規`tests/test_anon_self_marker.py`（16件、owner登録・reject非登録・同一本文index判定・ラウンド境界同時
clear・stale owner非漏洩・再代入箇所のソーススキャン固定・本人marker/他人フィールド欠如・God View不変・
reflection/commit伝播・Viewer投影不変・戦略助言0件）全PASS。API-free人工ケース（repo外`/tmp/cycle4_synth/`、
実Engine/StubAgent/PromptBuilder使用・実APIコール0件）でHuman Review必須10ケースを実出力確認、特にR1で
P01が使ったindex=0をR2でP02が再利用した際に誤適用されないことを実証。回帰1271/1271PASS（xfail 0件）、
`dry_run.py`正常終了（12人・seed=42・578イベント、Cycle3から数値不変）。成果物:
`.devrelay-output/cycle4_anon_self_marker_review_20260824_224516.md`。実API0件・Cycle5/R12 Acceptance Test
起動なしで停止（詳細: `doc/devlog/2026-08-24_224516.md`）。

## 2026-08-24: Cycle 3: anonymous_broadcast / bounty / card_trade 利用経路監査

12体×R12 Acceptance Test前に、残る戦略アクション3種が「AIが使おうと思えば正しく使える」状態かを
API-freeで監査した。`llm/prompt_builder.py`のみ変更（Engine変更ゼロ）。

- **E1**: `anonymous_broadcast`のJSON形式をaction一覧に追加（schema/parser/engineは対応済みだが
  Promptに形式が無かった）。
- **E2**: Engineが既に構築していた`bounties_public`を`build_negotiation_prompt()`に描画（従来は
  bounty_idを取得する手段が皆無で`bounty_cancel`が構造的に不可能だった）。
- **E3**: `bounty_cancel`のJSON形式をaction一覧に追加。
- **E4-a/b**: 匿名通信費が現金払い・Free Cash制限外である事実を機能説明直下に再掲し、表示の参照元を
  `entry_fee`由来から`anon_broadcast_fee`由来へ修正（両設定値が偶然同額だったため従来は表面化していな
  かった表示バグ、`entry_fee != anon_broadcast_fee`の回帰テストで検証）。

card_tradeはコード修正なし（過去の指定失敗は`ACTION_CONSTRUCTION_ERROR`と確定、失敗feedback配線は
既存commitで解消済みを再確認）。新規`tests/test_prompt_action_paths.py`（14件）全PASS。Phase A（repo外
`/tmp/cycle3_phase_a.py`）でR12実戦ログの8段階マトリクスを独立再集計しPlan記載数値と完全一致、Phase D
（repo外`/tmp/cycle3_synth/`、実APIコール0件）で3機能のend-to-end実出力（秘匿境界・bounty往復・
card_trade往復）を確認。回帰1254/1254PASS+xfail1件（E1+E3のprompt増分でPhase2予算上限を0.54%超過する
既存の緊張関係を発見し`PHASE_DEFAULTS`は無変更のまま可視化、Cycle 4で人間判断により解消）、`dry_run.py`
正常終了（578イベント）。成果物: `.devrelay-output/cycle3_action_path_review_20260824_210659.md`。実API0件・
Cycle4/R12 Acceptance Test起動なしで停止（詳細: `doc/devlog/2026-08-24_210659.md`）。

## 2026-08-24: サイクル2.2: Cycle 2 wording-only fix（残る断定文2分岐+通常Finance不足文の中立化）

サイクル2.1で未対応のまま残っていた`_obligation_conflict_warnings()`の市場競合分岐・参加×不参加競合
分岐と、`_render_finance_block()`の通常Finance不足文の計3箇所を、`llm/prompt_builder.py`の文字列
リテラルのみで「事実＋ルール制約のみを提示し、違反・脱落という結果は書かない」文体へ統一した
（`⚠`・「必ず違反で脱落」・「矛盾」・「不足します」・`MANDATORY_REPAY_FAILED（強制清算・脱落）になります`
を除去、if条件・数値計算・処理順・Engineは無変更）。`build_double_up_prompt()`はサイクル2.1で対応済み
のため今回はtouchせず維持確認のみ。

`tests/test_prompt_finance.py`の既存3テストのassertを更新し、新規2テスト（Finance不足行が数値事実のみ
であることの直接確認／Cycle 2追加ブロック限定のJUDGMENT語彙12語スキャン）を追加。回帰1241/1241PASS
（新規2件）、`dry_run.py`正常終了、`git diff --stat engine/`は増減ゼロ、`llm/prompt_builder.py`内の`⚠`
出現数は6→3（残る3件はスコープ外指定箇所と完全一致）。API-free再チェック（repo外`/tmp/cycle2_recheck2/`）
で旧文0ヒット・新文出現・数値完全維持・語彙スキャン新規混入ゼロを確認し
`.devrelay-output/cycle2_wording_recheck2_20260824_130510.md`に出力（最終判定GO）。実API0件・commit/push
無し・Cycle3着手やR12実戦起動はせず停止（詳細: `doc/devlog/2026-08-24_130601.md`）。

## 2026-08-24: サイクル2.1: Cycle 2 wording-only fix（境界事例2文の中立化）

Cycle 2 Human Reviewで唯一の留保事項として残った、double-up Promptの不足警告文と契約義務の競合表示
（type_b_card分岐）の2文だけを、`llm/prompt_builder.py`で結果断定を含まない数値事実・ルール事実のみの
表現に置き換えた（ゲームルール・計算式・Engineは無変更のwording-only fix）。同関数内の他2分岐（市場
競合・参加/不参加競合）と`_render_finance_block()`の不足警告文はスコープ外として意図的に非変更のまま
残した（サイクル2.2で解消）。

影響した既存テスト3関数のみ最小更新。回帰1239/1239PASS（新規0件、純粋な文言更新のみ）、`dry_run.py`
正常終了、`git diff --stat engine/`は増減ゼロ。API-free再チェックスクリプトで旧文0ヒット・新文出現・
数値完全維持・助言語彙12語スキャンが増加していないことを確認し
`.devrelay-output/cycle2_wording_recheck_20260824_120820.md`に出力。実API0件・commit/push無し・Cycle3
着手やR12実戦起動はせず停止（詳細: `doc/devlog/2026-08-24_120934.md`）。

## 2026-08-24: Cycle 2: 契約・財務・倍掛けの「事故死」要因を除去

前回の12人R12実戦（`logs/llm/trial_C_l12_r12_20260822/`）で脱落した7名のうち、AUTO COMMIT経路を除く
残りが「盤面の事実がプロンプトに出ていない／engineの実装と食い違う」ことによる事故死だった。
`llm/prompt_builder.py`のみ変更（engineルールは無変更）。

- `_render_finance_block()`/`_compute_finance_forecast()`新設: 今Rの借金残高・利息・強制最低返済額
  （実額）・差引後現金見込みを`engine.player.apply_interest`/`compute_mandatory_repayment`を直接
  再利用して算出し、negotiation/commit/reflection/double_upの4プロンプトに注入。
- 「残りラウンド」表示のoff-by-one解消（`num_rounds - round_num + 1`に統一）。
- `build_double_up_prompt`に同一ラウンドのFinance処理順の事実とTAKE/DOUBLE双方のFinance後現金見込みを
  数値で併記。
- `_render_obligations_block()`の型B義務に手札との突合注記を付与し、矛盾検出を3型（type_b_card×2 /
  type_b_market×2異市場 / type_b_market+type_b_no_market同一市場）に拡張。
- `contracts_pending`に署名した場合の合流後義務プレビューを追加。
- Entry Feeの実額と自動徴収タイミング、自分の倍掛け預託額を財務ブロックに明記。

助言語彙（すべき/推奨します/危険/安全な/おすすめ/注意しましょう）は一切追加せず、機械スキャンで0件を
確認。Cycle 1未了タスクとして`tests/test_prompt_finance.py`（新規17件）・`tests/test_viewer.py`拡張
（4件）・`tests/test_trial_report.py`（新規9件、副産物として`generate_phase_c_report()`の
`UnboundLocalError`/複数試合トークン集計バグを発見・修正）も消化。

回帰1239/1239PASS（新規30件）、`dry_run.py`/`simulate.py --games 1000`正常終了、`git diff --stat`で
`engine/autocommit.py`・`engine/finance.py`・`engine/settlement.py`・`engine/contracts.py`・
`engine/actions.py`が全て0行を確認。人間レビュー用プロンプトスナップショット6点を
`.devrelay-output/cycle2_prompt_snapshots_20260824_072949.md`に出力。実API0件・commit/push無し・Cycle3
着手やR12実戦起動はせず、人間のGO判定待ちで停止（詳細: `doc/devlog/2026-08-24_072954.md`）。

## 2026-08-23: 全当事者合意による契約解除（contract_cancel）

実runでP11/P06の間に同一R・同一市場に両立不能な条件（TWO_PAIR要求とONE_PAIR要求）の契約が
二重に成立し、1ラウンドに出せるカードは1枚しかないためどちらかが必ず型B違反で脱落する事故が
発生した。旧契約を無効化する手段が一切なかった（`ContractStatus.COMPLETED`/`EXPIRED`は
enum定義のみで代入箇所ゼロ、`PROPOSED→ACTIVE`の一方向遷移のみ）ため、**生存する全当事者の
合意でACTIVE契約をCANCELLEDへ遷移させる**機能を追加した。

- **`engine/models.py`**: `ContractStatus.CANCELLED`を追加。`Contract`に
  `cancel_requested_by`/`cancelled_round`/`cancelled_turn`（全てdefault付き、既存データと
  後方互換）を追加。新規`ContractCancelAction`（`{"type": "contract_cancel", "contract_id": "..."}`、
  提案/署名の2段構成にはしない）を`Action`Unionへ追加。
- **`engine/contracts.py`**: `can_cancel_contract()`（可否を義務フラグと`round_num`から毎回導出、
  新しい永続状態は持たない）と`request_cancel()`（同意を1件集約、生存全当事者が揃った瞬間に
  `CANCELLED`へ遷移）を新規追加。既存6関数は無変更。
- **`engine/actions.py`/`engine/game.py`**: `validate_action()`に`contracts`引数
  （既存呼び出しはデフォルト`None`で無変更）と`ContractCancelAction`検証を追加。実行分岐、
  `contracts_public`/`my_contracts`のcancelled表示、post-game`contract_ledger`の「－解除」表示を追加。
- **`llm/response_parser.py`/`llm/prompt_builder.py`/`llm/phase2_schema.py`**: パーサ分岐、
  アクションカタログ・契約台帳ブロックへの解除表示（`🚫 解除済み`/`⏳ 解除同意 n/m`）、
  H1 light schemaのenum合計上限（27→28）・直列化バイト上限（1280→1320、実測1298バイト）を更新。
- **`viewer/`**: ラベル・秘匿判定・契約再構成に`contract_cancel`/`CONTRACT_CANCELLED`を追加、
  秘匿契約カードと公正証書パネルに解除済み/一部同意中の表示を追加（`.ct-cancelled`/
  `.ct-card-cancelled`）。Public/God情報境界は無変更（解除の事実は契約ID・当事者・statusと
  同じ既存の公開粒度）。

**Settlement 8Step・型B監査・型A Atomic執行・脱落判定・§6.3の義務失効は無変更**
（全ての義務選択関数が既に`status != ACTIVE`で弾いており、`CANCELLED`遷移だけで自動的に
判定対象外になる）。

### AUTO_PASS修正 + 実APIハーネス（Stage A〜D-1）

上記の実装直後、`contract_cancel`は`_round_messages`を生成しないため、相手が
`messages`件数の変化のみを起床トリガとするAUTO_PASS（空回り削減）の対象外に一度も
入らず、片側が解除を打診しても相手が永久にAUTO_PASSし続け全会一致に到達不能という
第2のバグが判明した（Phase A・`scripts/contract_cancel_smoke.py --repro`で再現）。
`llm/llm_agent.py`の`negotiate()`に`my_contract_notices`の件数増分を**第3の起床
トリガ**として追加し修正（`AUTO_PASS_ON_NO_NEWS`本体は無変更、起床条件のOR分岐が
1つ増えるのみ）。Phase Cで4席・10項目の本番経路スモーク（実API 0コール）が全通過。

続けて `scripts/contract_cancel_smoke.py` に `--real` モード（Stage D-0）を追加し、
実APIへ進む前に4層の機械的安全弁を設けた: `REAL_MODEL_ALLOWLIST={"L6"}`（許可外の
`--model`指定は実API 0コールで拒否）、`--run-id`必須+出力先`exist_ok=False`（二重
送信防止）、`HARD_MAX_CALLS=6`、実プロンプトを構築した`worst_case_cost`事前見積と
`--max-cost`/`--max-cost-per-call`の突合。API-freeな4モード（`--repro`/`--dry-run`/
`--mock`/`--scripted`）はすべて実API 0コールのまま継続green。

Stage D-1でL6（DeepSeek V4 Flash）による実APIトライアルを1回実行し**GO判定**:
P01=実`LLMAgent`(L6)、P02=`ScriptedCancelAgent`（R1から解除打診）、seed契約
CX（解除対象）+CK（デコイ）で本番経路（`validate_action → contract_ops.request_cancel
→ settlement`）を通した。実call数4（choose_loan 1 + negotiation turn1/turn2 各1 +
commit 1、turn3は通知なしで想定どおりAUTO_PASS）、総コスト$0.0013694、ParseError
0件。L6はturn2で通知により起床し正しい`contract_id:"CX"`（デコイCKとの取り違えなし）
で`contract_cancel`を発行、全会一致で`CANCELLED`到達、`CONTRACT_CANCELLED`はちょうど
1回、旧CX義務はsettlement/audit対象外（`TYPE_A_EXECUTION`/`TYPE_B_VIOLATION`とも
0件）、デコイCKはACTIVE維持、脱落0、budget block 0件。L1へは進まず停止
（詳細: `doc/devlog/2026-08-23_212143.md`・`2026-08-23_213502.md`）。

## 2026-08-23: 脱落済みplayer宛DM問題の根本修正（Engine D1-D4 + Viewer D5）

Viewer God ViewでP06@R9に脱落済みP09宛の秘密DMが多数、通常DMと見分けがつかない形で表示されていた
問題を調査したところ、表示バグ1件（D5）に加えEngine側の根本原因4件（D1-D4）が判明したため、
Planに基づき1サイクルでまとめて修正した。実データ確認では、P06はR7-R9で30枠中25枠を脱落済み
P09宛の不成立DMに浪費しており、これがR9の破産に直結していた。

- **D1（`engine/actions.py`）**: `ContractProposeAction`/`CardTradeProposeAction`の宛先生存
  チェックが`DmAction`/`TransferAction`と非対称だった（前者は脱落者を宛先にしても資金チェックしか
  通らず不成立にならないケースがあった）ため、両アクションへ生存チェックを追加して対称化した。
  `CardTradeProposeAction`は宛先が1人でも生存していれば成立させる（既存の
  `engine/game.py`側スキップに委ねる）。
- **D2（`engine/game.py` + `llm/prompt_builder.py` + `llm/llm_agent.py`）**: 不成立アクションの
  理由が従来イベントログにしか残らず、行動主体のLLM自身へフィードバックされていなかった。
  `_action_failures`（ラウンド単位、直近12件）を新設し`_build_visible_state()`経由で
  **本人にのみ**`my_failed_actions`/`my_action_budget`として公開、`build_negotiation_prompt`/
  `build_reflection_prompt`へ配線した。`AUTO_PASS_ON_NO_NEWS`最適化は新規失敗発生時にバイパスする
  よう修正（そうしないとフィードバックが積まれても気づく機会がないまま自動パスされうる）。
  §2.5（不成立アクションも行動枠を消費する仕様）自体は変更していない。
- **D3（`llm/prompt_builder.py`）**: 脱落は全体へ公示される、脱落者宛アクションは必ず不成立になる
  旨をRULES_SUMMARYへ明記し、`eliminated_players`を`_build_visible_state()`へ追加して
  `build_negotiation_prompt`/`build_reflection_prompt`へ配線した。
- **D4（`engine/game.py` + `llm/prompt_builder.py`）**: `Contract.status`は脱落後も`ACTIVE`のまま
  （§6.3により当該義務は自動失効するが状態機械自体は変更していない、既存仕様のまま）だが、
  表示上「まだ有効な契約」に見えて混乱を招くため、`contracts_public`へ`eliminated_parties`
  （派生表示キーのみ、Engine状態は無変更）を追加し、`build_reflection_prompt`の契約表示へ
  「脱落済み→当該義務は失効済み・解除交渉は不要」の注記を付けた。
- **D5（`viewer/log_parser.py` + `viewer/static/`）**: ViewerがLLMログ（本文）だけを見て
  Engineイベント（成否）とjoinしていなかったため、不成立DMが「送れた」ように通常DMと同列表示
  されていた。`_index_negotiation_outcomes()`/`_match_delivery()`（純関数、
  `(player_id, round_num, turn)`で索引・突合）を新設し、`_collect_round_messages()`へ`events`
  引数を追加して各メッセージへ`delivery`（`delivered`/`rejected`/`unverified`の3値）と
  `reject_reason`（rejected時のみ、god view限定）を付与した。`reject_reason`は宛先PIDを
  直接含みうるため（例: `"Target P09 is not alive"`）public viewには一切出さない。
  `"unverified"`（未検証）を`"rejected"`（不成立）と混同表示しないことを最重要の不変条件とし、
  `turn`を含まない旧形式ログ・イベントでは`delivery`フィールド自体を一切付与しない後方互換モードと
  した（機械的に埋めると新たな誤情報源になるため）。UIは状況パネル・プレイヤー詳細モーダルの両方へ
  delivery badge（✅成立/❌不成立/❔未検証）を表示するよう`index.html`/`style.css`を改修した。
- **重複排除**: `get_round_states()`内でevents有無に応じて2箇所に重複していたメッセージ投影ロジックを
  `_project_message(message, view)`へ統合した（純リファクタ、挙動不変を既存テストで確認）。
- **テスト**: `tests/test_dead_target_and_feedback.py`新設48件（D1-D4の全カバレッジ、
  `TestDmLoopScenario`で実際のバグシナリオをend-to-end再現）、`test_dm_secrecy.py`へ
  `my_failed_actions`の非当事者漏洩なしテストを1件追加、`tests/test_viewer.py`へ
  `TestNegotiationDeliveryJoin`20件（純関数単体・後方互換・god/public境界・round_totals拡張）を
  追加。全体回帰1014/1014 PASS（既存945 + 新規69）。
- **副産物**: RULES_SUMMARY文言追加によりシステムプロンプトのトークン数が変動し、
  `test_model_matrix.py`/`test_phase2_schema.py`のハードコード済みworst-caseコスト
  スナップショット2件（`0.0268`→`0.027485`、`0.014364`→`0.014568`、`0.023952`→`0.024224`）が
  実測値からずれたため更新した。本コミットを跨ぐキャッシュ率・コスト実測値は単純比較できない
  （意図的な一度きりの変更）。
- **実測確認**: `trial_C_l12_r12_20260822`（読み取り専用、LLM API送信なし）でdelivery分布が
  delivered:653/rejected:25/unverified:4となり、P06→P09のR7-R9全25件が`rejected`かつ理由
  `"Target P09 is not alive"`で一致することを確認。Viewerを再起動しcurlでgod/public両viewを
  実地確認、public viewでは`delivery`/`reject_reason`/`"P09"`のいずれも一切出現しないことを
  機械確認した。

## 2026-08-22: Viewerプレイヤー詳細「神視点の秘匿情報」player-scope化

プレイヤー詳細モーダルの「🔒 神視点の秘匿情報」セクションに、当該プレイヤーが送受信していない
DM・当事者でない契約が大量に混入していたバグを修正した。`get_player_round_detail()`の
`secret_events`だけが同関数内の他フィールド（`calls`/`result`）と異なり`pid`フィルタを持たず、
ラウンド全体の秘密DM・terms付き契約をそのまま返していたことが原因。実データ
（12人R12版trial）で計測したところ、P11@R3の秘密DM51件中本人関与は9件のみ、P06@R9は44件中
7件のみで、全12名平均でDM 78%・契約81%が無関係だった。

- **修正**: `viewer/log_parser.py`の`get_player_round_detail()`に、DM用（`sender == pid` または
  `to == pid`、`to`が`list`型の場合の互換処理込み）・契約用（`proposer == pid` または
  `pid in parties`）の絞り込みを追加。返り値に`"scope": "player"`と`"round_totals"`
  （隠れた件数のみ、本文は含めない）を新設し、god利用者が「まだ他にある」ことを把握できるように
  した。`viewer/server.py`および公開表示（`_safe_round_result()`等）は無変更。
- **付随修正**: `viewer/static/index.html`の`renderRoundDetail()`が`detail.final_reflection`
  （存在しないtop-levelキー）を参照しており、正しくは`detail.result.final_reflection`だったため
  FINAL_REFLECTIONセクションが一度も描画されていなかったバグも同時に修正。到達不能だった
  匿名発言の秘匿描画分岐（`anonymous_broadcast`は常に`visibility:"public"`のため`secret_events`
  へ絶対に入らない）も削除した。
- **UX判断**: round全体の神視点情報を別セクションへ分離する代替案は、既存の状況パネル
  （round全体のDM・契約・匿名実発信者をgodで表示済み）と重複するため見送った。
- **テスト**: `tests/test_viewer.py`に多人数fixture`_make_multi_player_trial()`と回帰8件
  （無関係DM除外・受信DM保持・送信DM保持・契約のparties/proposer両対応と視点対称性・
  round_totals不変条件・`to`型ぶれ境界・public無影響・FINAL_REFLECTION参照経路の否定的固定）を
  追加。全体回帰は945/945 PASS（既存937 + 新規8、失敗0）。
- **実測確認**: 修正後の実データでP11@R3が51件→9件、P06@R9が44件→7件、P05/P08@R12の契約が
  11件→0件になることを確認。ユーザー報告の具体的な無関係DM（P04→P05、P05→P04、P07→P04、
  P04→P07、P02→P05、P07→P02、P09→P05、P01→P03、P02→P06）がすべて非表示になることを個別照合。
- **反映**: `viewer/log_parser.py`がPythonモジュールのため`systemctl --user restart
  dangou-viewer.service`で反映済み。稼働中サービスへcurlでP11/R3を実測し修正を確認済み
  （`scope: "player"`, messages 9件, `round_totals.messages_hidden` 42）。
- **未実施**: 手動ブラウザ確認、`_safe_round_result()`の契約フィルタ変更（公開表示に影響するため
  意図的にスコープ外）、状況パネルへのDMプレイヤー絞り込みUI追加、commit/push。
- 詳細は `doc/devlog/2026-08-22_215059.md` を参照。

## 2026-08-22: Viewer God View認証の上位化・使い回し（sessionStorage方式）

Viewer God View解除UIをプレイヤー詳細モーダルの内部から切り離し、ヘッダー常設のトグルへ
移設した。従来はモーダルを閉じる／別プレイヤーを開く／試合を切り替えるたびにGod token認証
（`X-Viewer-God-Token`）が破棄され再入力が必要だったため運用負荷が高かった。バックエンド
（`viewer/server.py`/`viewer/log_parser.py`）は無変更のまま、静的ファイル
（`viewer/static/index.html`/`style.css`）のみでタブ全体1回の認証・使い回しを実現した。

- **UI**: God UIを`#modal-overlay`外・`.header`末尾へ移設（`VIEW: PUBLIC 🔒`/`VIEW: GOD 🔓`の
  常設トグル）。`#god-banner`はヘッダー直下の全幅バーとして常時可視化。要素idはすべて
  既存のまま流用し、既存テスト・CSSセレクタとの互換を維持した。
- **保存**: God tokenはタブ限定の`sessionStorage`にのみ保存（`localStorage`はテーマ保存専用の
  まま不変、URL・query stringには一切載せない）。入力欄の`value`は読み取り直後に必ずクリアし
  DOMへ残さない。
- **認証フロー**: `viewerFetch()`に403の一元downgrade（`forceGodLogout()`: 即座にpublicへ
  戻し`sessionStorage`削除、リトライループなし）を集約。ログインは実際のGod API呼び出しの
  200/403で判定し、専用の確認APIは新設していない（追加リクエスト0件）。リロード時は
  `sessionStorage`のtokenで楽観的にGOD状態から再開し、起動時に必ず呼ばれる`/rounds?view=god`
  応答で自動検証・自動復帰する。
- **破棄条件の見直し**: `closePlayerDetail()`と試合セレクタ変更ハンドラからGod状態の破棄を
  削除（ラウンド依存キャッシュのみリセット）。明示ログアウト（VIEWボタン）と403受信のみが
  God状態を破棄する。
- **文書改訂**: `rules/project.md`（God tokenの永続領域禁止規定にsessionStorage例外を明記）、
  `doc/viewer_operations.md`（操作手順をヘッダー常設トグル前提に更新）、`README.md`を
  同一サイクルで改訂した。
- **スコープ外**: `viewer/server.py`/`viewer/log_parser.py`は無変更（`view`パラメータの意味・
  403条件・redaction境界は不変）。HttpOnly Cookie方式（将来案）、POST_GAME_REFLECTIONの
  Viewer UI追加、手動ブラウザ確認・curl実測、Viewer再起動、commit/pushは未実施。
- **テスト**: `tests/test_viewer.py`に`TestGodViewSessionAuth`を新設（10ケース）。全体回帰は
  937/937 PASS（既存927 + 新規10、失敗0）。
- 詳細は `doc/devlog/2026-08-22_201253.md` を参照。

## 2026-08-22: FINAL_REFLECTION completion variant + POST_GAME_REFLECTION新設

FINAL_REFLECTIONをR12まで完走した生還者にも発火させるcompletion variantを追加した
（従来は脱落者限定のelimination variantのみ）。加えて、ゲーム完全終了後（`_finalize()`後）に
全12名（生還者＋脱落者）へ一律1回だけ、匿名通信の真の発信者・DM本文・非当事者向け契約条項
などの神視点情報を積極的に開示した上で答え合わせコメントを書かせる新フェイズ
`POST_GAME_REFLECTION`を新設した。既存のgod隔離原則（DM/匿名/契約は当事者以外に開示しない）
を維持したまま、本フェイズだけを唯一の意図的な例外として構造的に切り分けている。

- **収集**: `engine/game.py`に`self._god_transcript`（DM本文・匿名放送の真の発信者を蓄積、
  `_visible_messages()`/`_build_visible_state()`からは一切参照しない構造的隔離対象）を新設。
  `Game.run()`の通常完了経路のみ`_finalize()`直後に`_phase_post_game_reflection(result)`を
  呼ぶ（budget-abort/stop-after-round早期returnには非影響）。新設
  `_build_god_shared_block(result)`（12人分バイト同一の共有ブロック、`round_digest`は
  MARKET_RESULTのみに圧縮しprompt長爆発を回避）と`_build_post_game_context(pid, result, shared)`
  （per-player最大12件の`revelations`。`self.players`/`self.logger.events`/`_god_transcript`/
  `self.contracts`からの読み取り専用）で構成する。
- **API**: `llm/prompt_builder.py`に`build_post_game_reflection_prompt()`を新設。既存8つの
  `build_*`（すべてagent向け・god情報禁止）と並ぶ9つ目で、god情報をプロンプトに積める
  唯一の例外として設計し、allow-listテストで機械的に固定した。`llm/llm_agent.py`に
  `LLMAgent.post_game_reflect()`（結果は独立属性`self.post_game_reflection`）、
  `llm/response_parser.py`に`parse_post_game_reflection()`（既存7段salvageラダーを再利用、
  `self_assessment`/`biggest_revelation`/`changed_opinion`の3任意フィールドとroster検証つき
  `best_player`/`most_deceptive_player`を追加）を新設。`engine/config.py`に
  `post_game_reflection_enabled`（既定`False`）/`post_game_reflection_max_chars`(1000)/
  `post_game_reflection_max_tokens`(3000)を追加、両プリセットとも既定Falseのまま据え置き。
- **FINAL_REFLECTION拡張**: `_phase_final_reflection()`を2パス化し、R12末の生存者にも
  `_build_completion_context()`（本人が正当に知る公開情報のみ、他生還者の順位は含めない）経由で
  completion variantを1回発火。elimination/completionは`is_alive`の極性で構造的に排他。
- **スコープ外**: Viewerは本サイクルではPOST_GAME_REFLECTIONを一切表示しない
  （未対応`event_type`を黙ってスキップする既存ディスパッチに委ねる設計）。
  `post_game_reflection_enabled`のプリセット有効化・実API疎通試験・commit/pushは未実施。
- **テスト**: `tests/test_post_game_reflection.py`新設、`tests/test_final_reflection.py`に
  発火行列テスト追加、`tests/test_dm_secrecy.py`にbuilder allow-list厳密一致テストとgod情報
  混入テストを追加、`tests/test_viewer.py`に`TestPostGameReflectionViewer`を追加（Viewer側の
  非表示・旧ログ`variant`後方互換を確認）。`scripts/post_game_reflection_smoke.py`を新設し
  API無し3ゲート（parser-selftest/dry-run/mock）全PASS、実APIコール0件。全体回帰は
  927/927 PASS（既存を含む全件、失敗0）。
- 詳細は `doc/devlog/2026-08-22_200711.md` を参照。Viewer再起動・実API疎通試験は本サイクルでは
  実施していない。

## 2026-08-22: Viewer FINAL_REFLECTION表示（public/god二層設計）

Engine側で実装・実API試験済み（GO判定、`doc/trials/final_reflection_smoke2_2026-08-22.md`）の
FINAL_REFLECTION（脱落者の最終振り返り: `emotion`/`defeat_cause`/`comment`）をViewerで
閲覧可能にした。`comment`は実測2/2で§8.2秘匿情報（契約条項の復唱、交渉相手の特定）を
含んでいたため、本文（`comment`/`defeat_cause`）はgod限定、公開viewには非秘匿の骨組み
（`emotion`/`round`/`has_comment`/`truncated`/`availability`）のみを返す二層設計を採用した。

- **収集**: `viewer/log_parser.py`の`get_round_states()`イベントループに`FINAL_REFLECTION`
  分岐を追加。`add_elimination()`と同じ先勝ちdedupパターンの新設ヘルパ`add_final_reflection()`で
  top-level `round_num`により振り分け、emotionは`VALID_EMOTIONS`で読み取り時にも再検証する
  （`viewer/log_parser.py`内の`_extract_emotion()`は`奸`欠落のため意図的に不使用）。
- **API**: `_safe_round_result()`の返り値に`final_reflection`キーを追加しview別に出し分け
  （本文が実質空、またはイベント自体が無い場合は`None`）。`get_player_round_detail()`に
  発見性のための`final_reflection_round`ポインタを追加（追加I/Oなし）。既存キーは型・意味とも
  無変更の後方互換。
- **表示**: `viewer/static/index.html`の`renderRoundDetail()`にmemory-endセクションの後へ
  「脱落時コメント」セクションを追加（public: 感情+「本文は神視点のみ」ラベル、god: `🔒`付きで
  敗因・最後の言葉を表示）。ラウンドジャンプ列に💀マークを追加。`EMOTION_EMOJI`/`EMOTION_EN`への
  キー追加は既存テストとの乖離を避けるため行っていない。
- **スコープ外**: `engine/`/`llm/`/`bots/`は無変更（`VALID_EMOTIONS`の読み取り専用importのみ）、
  `viewer/server.py`も無変更。
- **テスト**: `tests/test_viewer.py`に独立fixtureの`TestFinalReflectionViewer`を新設し10ケース
  （完全データ、public境界回帰、部分欠損、emotion不正値のNone正規化、FRなし旧run互換、
  重複イベント1件化、ポインタと非該当round非表示、既存脱落reason表示との共存、empty時の
  `None`縮退、frontend文字列検査）。全体回帰は869/869 PASS（既存859+新規10）。
  smoke2実データ（読み取り専用）で公開viewに本文が一切出現しないことを機械的に再確認した。
- 詳細は `doc/devlog/2026-08-22_162917.md` を参照。Viewer再起動は本サイクルでは実施していない
  （次回反映には`systemctl --user restart dangou-viewer`が必要）。

## 2026-08-22: Handover Memory 3000文字化 + 3バグ修正

R12実戦ログ（`logs/llm/trial_C_l12_r12_20260822/game01`、reflection応答105件）を読み取り専用で分析し、ラウンド間の唯一の文脈継承路であるHandover Memory文字列が3つの実装バグで実質破壊されていたことが判明したため、`llm/response_parser.py`を中心に修正した。ゲームルール・勝敗ロジックは無変更。

- **WRAPPER_LEAK修正**: `extract_memory_with_status()`を新設し、`json.loads`が生改行入りJSON文字列値で失敗した場合に`return text.strip()`へ落ちて` ```json{"memory": " `等のラッパーがそのまま本文として保存される問題を修正した。フェンス剥がし（閉じフェンス有無両対応）→`"memory":"`探索→エスケープ復元、の手動サルベージ経路（`_strip_code_fence`/`_recover_memory_field`）を追加し、復旧不能な場合のみ空文字を返す。実測: 39件中36件を`ok_recovered`で救済、残りはWRONG_PAYLOAD側で別集計（後述）。
- **WRONG_PAYLOAD修正**: `{"memory"}`キーが無く`{"strategy": {...}}`等を丸ごと採用していたfall-through（意図的だった旧設計）を廃止し、`memory`キーが無い/文字列でない場合は`rejected_no_memory_key`として空文字を返すよう反転した。空文字は`LLMAgent.reflect()`の既存セーフティ（「空文字での上書きは絶対にしない」）にそのまま乗るため、`llm_agent.py`の判定ロジック自体は変更していない。実測3件（P05 R5/R7, P12 R3、いずれもgemini-3.5-flash-lite）は前ラウンドのMemoryを維持するfallbackとなった。
- **TRUNCATED修正**: `normalize_memory_with_truncation()`で`memory[:max_chars]`のハードカットを、`\n\n`/`\n`/`。`/`」`/`）`等の意味境界を優先する縮約（`_shrink_to_boundary`、keep_ratio=0.85のフロア付き、境界が見つからなければハードカットへフォールバック）に置換した。境界なしテキストで`len == max_chars`となる既存テスト互換は維持。実測は102件中1件のみ縮約発生（P04 R8: 3470字→2992字、文末`。`で終端）。
- **上限3000字化**: `engine/config.py`に単一ソース定数`HANDOVER_MEMORY_MAX_CHARS = 3000`を新設し、`GameConfig.memory_max_chars`の既定値および`viewer/log_parser.py`の表示上限をそこから参照するよう統一した（マジックナンバー`1000`の残存箇所ゼロを確認）。実測分布（上限なし）で3000字は102件中101件(99.0%)を無切断でカバーする。`viewer/log_parser.py`の`_extract_memory_text`は過去ログ再現のため旧fallback挙動自体は意図的に現状維持（コメント明記）。
- **プロンプト最小追記**: `build_reflection_prompt()`末尾に「末尾から切り詰められる」旨と優先順位ヒント2〜3行、および「古い情報を現在の事実として書かない」旨を追加。テンプレート強制・大規模redesignは行っていない。
- **Observability追加**: `_update_last_log_memory()`を新設し、`_update_last_log_emotion`と同じ`llm_logger._entries[-1]`後付け更新パターンで`memory_chars_raw`/`memory_chars_saved`/`memory_parse_status`/`memory_truncated`/`fallback_reason`/`memory_fallback_streak`をログへ記録する。連続fallback2回以上で`logger.warning`。
- **Before/After再評価**（`scripts/reeval_memory_fix.py`新設、読み取り専用・LLM API不使用、`logs/`無改変を確認済み）: WRAPPER_LEAK 39→0、WRONG_PAYLOAD 3→0、1000字上限ヒット52→3000字上限ヒット1、fallback 0→3件（いずれも旧Memory保持で文脈継続を確認）、保存文字数 mean 704→995 / median 985→1037 / p95 1000→2103。parse_status内訳: `ok`=66 / `ok_recovered`=36 / `rejected_no_memory_key`=3。
- **テスト**: `tests/test_memory.py`に新規ケースを追加し既存ケースは全て維持（64/64 PASS）。全体回帰は808/808 PASS（警告4件はいずれも既存・無関係のFastAPI/pytest/asyncio非推奨警告）。
- **コスト影響**: 実測tokens/char・実測モデル別饒舌さに基づく試算で、現実的ケース+2.4%（$5.99→$6.14）、最悪ケース（全モデルが3000字上限まで書く）+19.0%（$5.99→$7.13）。いずれも`game_cost_cap_usd=15.0`・`per_player_game_cost_cap_usd=3.0`の範囲内。
- 詳細は `doc/devlog/2026-08-22_123701.md` および `.devrelay-output/memory_fix_reeval_2026-08-22.md` を参照。

## 2026-08-21: Viewer詳細表示・神視点・運用の整備

- プレイヤー詳細にR1〜R12のラウンド表示と既存生ログタブを追加し、Handover Memory、全文reasoning、Native Thinking token、行動とラウンド結果を再構成できるようにした。実ログの`MARKET_RESULT.participants`が整数となる形式にも互換対応した。
- 通常Viewerを`view=public`として統一し、timeline・rounds・round-detailからDM本文／宛先、匿名発言の実発信者、契約条項をredactする。運用者向けの`view=god`は追加token認可でだけ許可し、構造化されたゲーム内秘匿情報を公開／秘匿ラベル付きで表示する。prompt、LLM生応答、認証値などゲーム外の情報は返さない。
- Viewer公開運用をuser systemdの`dangou-viewer.service`へ正本化し、再起動・疎通・障害切り分けと神視点tokenのrepo外保管・反映方法を`doc/viewer_operations.md`に記録した。Caddy設定は変更していない。
- Phase CのL1〜L6小規模試験用に、S2の12ラウンド設定を保った`--stop-after-round`、予算block中断、manifest／AFTER集計を追加した。R1〜R3実API試験はR3 Reflection後に正常停止し、Viewerの実ログ回帰にも利用している。

## 2026-08-20: L1〜L6 R12実戦試験の安全ランナーとAFTER集計

- `scripts/llm_trial.py` にPhase C限定の `--max-output-tokens` と
  `--abort-on-budget-block` を追加。`ModelInfo`の試験用copyへ出力上限を適用し、
  registryを変更せずL1〜L6全席を2,000 tokensに統一できるようにした。
- 実行前に `trial_manifest.json` を保存し、モデルID・単価・timeout・thinking設定・実効token上限・
  CoT・S2・cap・賞金設定を監査可能にした。APIキーその他の秘密情報は保存しない。
- `GameCostBudget(abort_on_block=True)` とGameのフェイズ境界を連携し、最初の予算block後は
  `GAME_ABORTED` と `GAME_END(completed=false)` を残して試合を中断する。既定Falseの通常試合は従来の
  fallback挙動を維持する。
- `ROUND_COMPLETE` を追加し、各ラウンド終了時の生存者数をイベントから直接集計可能にした。
  `scripts/l6_r12_report.py` は逐次保存済みログだけからAFTER比較用Markdownを再構成する（API送信なし）。
- Console BEFORE基準値を `doc/cost/api_console_baseline_2026-08-20_before_l6_r12.md` に独立保存した。
  対象テストはAPI送信なしで実行する。

## 2026-08-20: Phase 3 matrix strict化と監査集計の拡張

- Phase 3のadapter生成だけを `max_retries=0`・temperature fallback無効に固定し、Anthropic/OpenAI互換SDKを
  含めて1 logical callあたり最大1 transport sendとした。Phase 1/2、本戦、通常ゲームの既定retry挙動は変更しない。
- negotiation/commitのparse correctionを別logical callとして数え、モデル単位のPhase 3集計へrequested/response
  model、model match、logical calls、各correction数と合計を追加した。raw response・usage・finish reason・latency・
  costの既存LLMログ形式は維持する。
- APIなしの関連テスト221件と全726件がPASS（警告4件）。隔離コピーのdry-runはCORE_18全18モデル・外部API 0回で
  完了し、通常3call予約は最大H4 `$0.133300000`、合計`$0.631582840`であることを再確認した。
- Phase 3実APIと本戦はこのstrict化検証の対象外とし、実行には別承認を要する。

## 2026-08-20: Phase 2 Structured Outputs のモデル別適応と再試行監査

### 概要

Phase 2のJSON互換試験を、各providerのStructured Outputs制約とreasoning消費に合わせて局所適応した。
H1（Claude Opus 5）はcanonical schemaがprovider compileで拒否されたため、Phase 2だけflat transport
schemaと厳格normalizerを導入し、attempt 7でStructured Outputs compile・transport→canonical→parserを
通過した。Geminiの残存失敗モデルは、M3を`reasoning_effort="minimal"`・512 tokens、H3を`low`・
912 tokensとして次回の単発再試行へ備える。Phase 1/3、本戦、成功済みモデルのrequest挙動は変更しない。

### 詳細

- H1だけは19 required field・root object 1個のtransport schemaを使い、raw responseを監査用に保存してから
  action別whitelist normalizerで既存canonical dictへ戻す。既存`GAME2_USER`は使用せず、同一helperから
  専用prompt/schemaを実送信とreserve計算へ渡す。
- M3/H3はcanonical schemaを維持する。M3だけ`reasoning_effort="minimal"`、H3は最小設定の`low`のままとし、
  `thinking_level`/`thinking_budget`は併送しない。H3の912は実測hidden thinking 487 + visible JSON 400 +
  既存512-token reserveとの差25から導出したPhase 2限定値である。
- Phase 2はadapter/SDK retry=0、temperature fallback無効で、logical callあたりクライアント送信を最大1回に固定する。
  `--retry-failed`は失敗モデルだけを選択し、attempt番号・raw response・usage・model match・parse結果を
  `phase2_calls.jsonl`へ追記する。
- 新しい実request構成によるreserveはH1 `$0.026800000`、M3 `$0.014364000`、H3 `$0.023952000`。
  M3/H3の同時予約はtotal cap `$0.25`を超えるため、実APIはM3の結果と実費を確認してからH3を別承認で判定する。

### 検証

- H1 light schemaの13 action正規化、unused field拒否、JSON-in-string異常系、provider別payloadと非波及をfakeで固定。
- M3/H3のreasoning設定、Phase 2 token override、canonical schema、reserve計算を回帰テストへ追加。
- full pytest: 723 passed, 4 warnings。`git diff --check`成功。
- H1 attempt 7はPASS（`end_turn`、14,606ms、実費`$0.008715`）。M3 minimal/512のdry-runはAPI 0回で
  reserve・state/JSONL不変を確認済み。実APIの追加送信は実施していない。

## 2026-08-18: 事前予算ガード `worst_case_cost()` の hidden thinking 予約対応（予算上限は据置）

### 概要
前サイクルでPhase 1/2/3の**実績**コスト計算は`_usage_cost()`に統一済みだったが、**事前見積**
`worst_case_cost()`（`scripts/model_smoke.py`、`scripts/model_matrix.py`から`_worst_case_cost`
として再export）は「入力全額 + `max_tokens`全消費」のみを見込み、`max_tokens`の外側で課金される
hidden thinking（Gemini/xAIで実測185〜343token、Phase 1実測 max_tokens=64時点）を一切見込んで
いなかった。これによりPhase 3のH3（Gemini）で旧見積が実支出の56%しか捕捉できず、実際には予算
上限の112%まで超過するケースを確認。**本サイクルの目的は見積式の是正であり、予算上限の引き上げ
ではない**（`PHASE_DEFAULTS`/`DEFAULT_MAX_COST_TOTAL`は一切変更していない）。

### 詳細

**設計**: `ModelInfo`に`hidden_thinking_reserve_tokens: int = 0`を追加し、
`worst_case_cost()`が`estimate_cost(model, approx_input, max_tokens, total_tokens=
approx_input+max_tokens+reserve)`を呼ぶよう拡張（実質3行）。reserve=0のモデルは
数値が旧式と完全一致（複数max_tokensで非回帰確認済み）。

**対象モデル（CORE_18内の6件のみ、実測/API特性で正当化されるもののみ）**:
`M3`/`L3`/`H3`（Gemini）・`M4`/`L4`/`H4`（xAI）に`hidden_thinking_reserve_tokens=512`を設定。
512は実測最大343（xAI L4）に対する約1.5倍の**経験的安全マージン**であり、provider側で
thinking budget/reasoning effortの上限を送信していない以上、**provider保証の数学的worst-case
上限ではない**ことを`ModelInfo`のコメント・関数docstring・`rules/project.md`に明記した。
Anthropic/OpenAI/Kimi/DeepSeekの12モデルは、thinkingがoutput/completionに内包される
（または無効化済み）ことを実装・実測で確認済みのため、従来どおりの見積保証を維持する。
core外の`L7`（`gemini-2.5-flash-lite`）は本サイクルでは`reserve=0`のまま据置（既知の残余ギャップ）。

**予算上限は不変。現行上限下での影響（意図した是正結果として可視化）**:
- Phase 1: 新規ブロックなし
- Phase 2: `H4`が新規にper-model上限超過（`H1`/`H2`は変更前から超過、既存の欠陥）
- Phase 3: 許容コール数が減るのは`H3`(2→1)・`H4`(1→0)のみ。両者とも変更前から3コール完走
  不可であり、完走できていたモデルが完走不可になるケースはゼロ。H3は旧見積で実支出が上限の
  112%まで超過していたのが、新見積では1コールで止まり56%に収まる（予算を変えずに超過を解消）
- `model_smoke`（既定`--max-tokens 2000 --max-cost 0.10`）: `H3`が新規に実行前中断

**テスト**: `tests/test_model_smoke.py`に9件追加
（reserve=0モデル×複数max_tokensの完全一致・Gemini/xAI予約加算量の厳密一致・H2非二重計上・
既定0・実測343が512に収まる固定確認[512を絶対上限として検証はしない]・予約対象キー集合の厳密一致）。
`tests/test_model_matrix.py`に4件追加（`PHASE_DEFAULTS`不変の回帰・予約対象がCORE_18内である
ことの確認・reserve=0モデルの事前ガード非回帰・H3相当での早期ブロック実演）。既存の
`test_budgeted_adapter_blocks_next_call_when_budget_reached`（M3対象）はreserve導入で
worst見積自体が変わるため、`mm._worst_case_cost`から動的にmax_costを算出する形に修正。
`tests/test_registry_18.py`のフィールド数固定テストも新フィールドを追加して更新。
全663件PASS（実API呼び出し0件）。

### 変更
- `llm/models.py`: `ModelInfo`に`hidden_thinking_reserve_tokens: int = 0`を追加、
  `M3`/`L3`/`H3`/`M4`/`L4`/`H4`の6件のみ`=512`を設定
- `scripts/model_smoke.py`: `worst_case_cost()`をreserve対応に拡張、docstringに保証レベルの
  注意を明記
- `scripts/model_matrix.py`: 変更なし（`_worst_case_cost`は再exportのため自動反映。
  `PHASE_DEFAULTS`/`DEFAULT_MAX_COST_TOTAL`は据置）
- `tests/test_model_smoke.py`・`tests/test_model_matrix.py`・`tests/test_registry_18.py`:
  テスト追加・更新
- `rules/project.md`: 残余課題(1)を予約方式での緩和状況に更新、保証レベル区分・L7残余ギャップ・
  thinking_matrix suiteの残余リスクを明記

### 対象外（別課題として維持）
provider側thinking budget送信による完全保証／予算値変更（人間が別サイクルで判断）／
`max_tokens`clamp・残予算逆算／`L7`への予約設定／`llm/llm_agent.py`のAnthropic正規化漏れ／
リトライ失敗コールのusage欠落／モデル価格表見直し／Phase 2・3の実API実行。

## 2026-08-18: 18モデル体制（H1〜H6）整備・Phase 1/2 コスト計算修正・リトライ責務一本化

### 概要
Season 2 決勝編成向けに強6モデル（H1〜H6, フラッグシップ級）を `MODEL_REGISTRY` へ追加し、
6社18モデル体制（強6+中6+軽6）が確定した。これに伴い新設した `scripts/model_matrix.py`
（Phase 0〜3 段階的スモークテスト）で全18モデルの実API疎通・課金実測を行い、その過程で発覚した
コスト計算バグ（Gemini hidden thinking の計上漏れ・キャッシュ割引の未反映・Anthropic usage慣習差の
未正規化）とリトライ多重化の設計不備を是正した。

### 詳細

**18モデル体制整備:**
- `llm/models.py`: H1〜H6（強量級）6モデルを追加。`ModelInfo` に `max_tokens_param`
  （既定`"max_tokens"`、reasoning系は`"max_completion_tokens"`）と `supports_temperature`
  （既定True、reasoning系はFalse）を新設。H2 (`gpt-5.6-sol`) は実測で400エラーを確認したため
  この2フィールドをH2専用に上書き
- `engine/blog/cards_svg.py`: ブログカード生成が `MODEL_REGISTRY` の `tier`（H/M/L）を直接参照する
  ように変更。H1〜H6用のタグライン・価格訴求文・強量級ラベルを追加
- `scripts/api_check.py`: 全18モデル一括走査で高額なH系モデルを誤爆しないよう、既定では何も
  実行せず `--keys` での対象絞り込みか `--all`（明示指定）を必須化
- `scripts/llm_trial.py`: `SEASON2_ROSTER_18`（6社18モデルフルロスター）を新設
- `viewer/log_parser.py`: `model_id` 重複時（例: M5/L5がKimi K2.6を共有）に宣言順で先勝ち統一する
  `setdefault` へ修正（`get_model()` の逆引きと意味論を一致させる）
- `llm/llm_logger.py`: ログに `response_model`（API実返却モデル名）を追加記録

**scripts/model_matrix.py（新設）— 18モデル段階的スモークテスト:**
- Phase 0（在庫確認, $0）→ Phase 1（疎通確認）→ Phase 2（談合カードJSON互換）→ Phase 3（ミニゲーム統合）
  の4段階を `state.json` でゲート管理。`--resume`/`--force`/`--dry-run`/コスト・コール数上限ガード対応
- Phase 1 実測で以下を発見・修正:
  - xAIの `reasoning_tokens` がコスト計上から漏れていた（cache_read/total_tokensをestimate_costへ
    渡していなかった）→ `_usage_cost(model, usage)` を新設し、cache割引・thinking差分課金・
    Anthropicのusage慣習差（`input_tokens`にcache_readが含まれない）を正規化してからestimate_costへ
    渡す方式に統一
  - APIの実返却モデル名（`response_model`）を記録し、requested/returnedの一致判定
    （match/alias/mismatch）をレポートに追加
  - H2 (`gpt-5.6-sol`) が `max_tokens` パラメータを拒否する400エラーを実測 → `max_tokens_param`/
    `supports_temperature` フィールド追加で解消し、H2再テストで通過（全18モデル$0.0090で完走）
- **リトライ責務の一本化**: 従来はスクリプト外側ループ・アダプタ内部（`API_MAX_RETRIES=2`）・
  OpenAI SDK既定（`max_retries=2`）の3層が独立にリトライしており `--retries N` を素朴に伝播すると
  `(N+1)^3` の多重リトライになりうる欠陥があった。Phase 1呼び出し側で
  `create_adapter(model, max_retries=0)` を常に使い下位2層のリトライを強制的に無効化することで、
  `--retries N` → HTTP最大 `N+1` 回の1対1対応を保証する設計に統一（`llm/adapters.py`:
  `create_adapter(model_info, max_retries=None)` / `OpenAICompatAdapter.__init__(max_retries=None)`）。
  Phase 2/3・ゲーム本体・model_smoke等、Phase 1以外は従来のretry挙動を維持

**Phase 2 コスト計算修正（本サイクル）:**
- `scripts/model_matrix.py` の `run_phase2()` が旧式の `estimate_cost(info, input, output)` を
  直接呼んでおり、Phase 1で導入した `_usage_cost()` の恩恵（hidden thinking計上・cache割引・
  Anthropic正規化）を受けていなかったバグを1行修正で解消（`cost = _usage_cost(info, usage)`）
- Phase 2 は1モデルにつきAPI呼び出しがちょうど1回（parse correction再送・スクリプト層リトライなし）
  であることを確認したため、usage合算処理は不要と判断
- Phase 2 は未実行で成果物が存在しなかったため、履歴の再計算・書き換えは発生せず
- Phase 3 `BudgetedAdapter.complete` にも同一バグが残存することを確認したが、予算ガードロジックへの
  影響評価が必要なため今回はスコープ外として申し送り

**Phase 3 BudgetedAdapterコスト計算修正（本サイクル）:**
- `BudgetedAdapter.complete()`（`scripts/model_matrix.py`）が旧式の
  `estimate_cost(info, input, output)` を直接呼んでおり、`cache_read_input_tokens`/`total_tokens`
  未指定のため Gemini hidden thinking・xAI cache割引・Anthropic正規化が予算消費 `spent_usd` へ
  反映されていなかったバグを1行修正で解消（`cost = _usage_cost(self._model_info, usage)`）
- 事前ガード（`spent_usd + worst > max_cost` のworst-case予約チェック）は既に構造的に正しいため変更
  せず、事後の実コスト計上のみを修正。Phase 3 は1モデルあたり3〜7回のAPI呼び出しがあり
  `BudgetedAdapter` は既にコール単位で `spent_usd` を正しく累積しているため、集計ロジックの
  再設計は不要と判断
- Phase 3 は未実行で成果物が存在しなかったため、履歴の再計算・書き換えは発生せず
- これでPhase 1/2/3すべてが `_usage_cost()` に統一され、`scripts/model_matrix.py` からの
  `estimate_cost()` 直接呼び出しは `_usage_cost()` 内部のみに
- テスト14件追加（Gemini hidden thinking / xAI cache割引 / cache+reasoning併存 / Anthropic正規化 /
  通常ケース非回帰4件 / 欠損フィールド耐性 / 複数コール累積 / 予算到達ブロック / API例外時非課金 /
  state・JSONL・report反映2件）、全570件PASS
- 残余課題: `_worst_case_cost`（`scripts/model_smoke.py`）がhidden thinkingを見込まない事前予約の
  過小評価、および `llm/llm_agent.py` 自前コスト計算のAnthropic正規化漏れは別課題として
  `rules/project.md` に明記し保留

### 変更
- `llm/models.py`: H1〜H6追加、`max_tokens_param`/`supports_temperature`フィールド新設
- `llm/adapters.py`: `create_adapter`/`OpenAICompatAdapter` に `max_retries` パラメータ追加、
  `max_tokens_param`/`supports_temperature` に応じたリクエスト組み立て分岐
- `llm/llm_logger.py`: `response_model` フィールド記録
- `scripts/model_matrix.py`（新設）: Phase 0〜3 段階的スモークテストランナー、`_usage_cost()`
  （Phase 1/2共通）
- `scripts/discover_models.py`（新設）、`scripts/run_matrix.sh`（新設）
- `scripts/api_check.py`: `--keys`/`--all`/`--max-cost` オプション追加（既定は何もしない安全策）
- `scripts/llm_trial.py`: `SEASON2_ROSTER_18` 追加
- `engine/blog/cards_svg.py`: tier別タグライン・価格訴求文・強量級ラベル追加
- `viewer/log_parser.py`: model_id重複時の先勝ち統一（`setdefault`）
- テスト: `tests/test_llm.py`（H2パラメータポリシー・単一HTTPリクエスト保証等16件追加）、
  `tests/test_registry_18.py`（新設）、`tests/test_model_matrix.py`（新設。Phase1/2コスト計算
  検証を含む）。全556テストPASS
- レポート: `doc/model_matrix_report.md`、`.devrelay-output/phase1_h2_retest_report.md`、
  `.devrelay-output/phase2_cost_fix_report.md`

## 2026-08-17: DM本文の秘匿化（§8.2是正）— 密談が全員に筒抜けだったバグの修正

### 概要
引き継ぎメモリ導入の調査中に、`_build_visible_state()`の`messages`フィールドが`for_player_id`を
無視しており、**DM本文が全プレイヤーのプロンプトに`[P01→P02] 本文`として表示されていた**ことが
判明した。仕様§8.2:450は「DM（direct message）内容」を秘匿情報と定義しており、
`doc/turn_order_investigation.md`にも2026-08-14時点で既知の差異として記録されていたが未対応のまま
放置されていた。本修正で構造的（reasoning/memoryと同様、エンジン内でキーごと削除）に是正した。

このバグは前サイクルで導入した「引き継ぎメモリ」の意義にも直結する。DM本文が全員に見えている限り、
各AIのmemoryは主観的記憶ではなく「全体の議事録の要約」にしかなり得なかった。本修正により初めて
memoryが真に主観的な記憶として機能する。

調査で以下の関連事実も判明:
- Bot 8種は全種`DmAction`を一切使用しない（broadcastのみ）→ 本修正は`simulate.py`のBot統計に
  構造的に影響しない（1000試合の平均生還者数0.07/8が修正前後で完全一致することを確認済み）
- 匿名通信（`AnonymousBroadcastAction`）が`_round_messages`に積まれておらず、**本文が誰にも
  届いていなかった**（§7.1の完全な機能不全。手数料だけ徴収されていた）
- commit/double_upフェイズの`_build_visible_state()`呼び出しが`for_player_id`を渡しておらず、
  `my_obligations`キーが生成されないため契約義務ブロックが常に空だった（§9.1:476「negotiation/
  commit/double_upの3フェーズ全てに注入」という記述に対する実装漏れ）

### 変更
- **`engine/game.py`**: `_visible_messages(for_player_id)`を新設。DMは送信者・宛先以外に対し
  `message`キーを辞書から削除し`redacted: True`を付与して返す（空文字での上書きではなくキー欠落。
  reasoning/memoryと同じ「構造的秘匿」パターン）。sender/to/turn等のメタデータは残すため、
  「密談が行われている事実」は非当事者にも観測できる（同盟の兆候を読む戦略性の追加）。
  `for_player_id=None`（呼び出し側の既定）は誰とも一致しないため全DMが安全側（redacted）に倒れる。
  `_build_visible_state()`の`"messages"`フィールドをこの関数経由に差し替え。
  匿名通信を`_round_messages`に追加（`sender: None`で掲載者を秘匿しつつ本文は公開）。
  commit（`_phase_commit`）・倍掛け選択（`_process_double_up`、勝者ループ内に移動）の
  `_build_visible_state()`呼び出しに`for_player_id`を追加
- **`llm/prompt_builder.py`**: `_render_message_list()`が`redacted`フラグを見て
  `[P01→P02] （非公開のDM）`と描画（本文なし）。匿名通信は`[匿名] 本文`として描画（senderは出さない）
- **`tests/test_dm_secrecy.py`（新規21件）**: 非当事者へのDM本文非開示・当事者への開示・
  broadcast全員可視のリグレッション防止・redactedメタデータ保持・`for_player_id=None`の安全側
  フォールバック・negotiation/commit/reflection各プロンプトへの非漏洩・全プレイヤー横断の
  漏洩スキャン（`test_cot.py::TestCoTNoLeak`と同型）・匿名通信の配信と掲載者秘匿・
  commit/double_upの`my_obligations`可視化を検証
- **`tests/test_memory.py`（1件追加）**: reflectionフェイズでも非当事者にはDM本文が見えないことを検証

### 影響
- Bot戦（`simulate.py`）: DM未使用のため統計影響ゼロ（1000試合で完全一致を実測確認）
- LLM戦: コール数・プロンプト構造は不変（メッセージ件数は変わらずメタデータのみ残るため、
  `AUTO_PASS_ON_NO_NEWS`の判定ロジックへの影響なし）。DM本文が読めなくなる分、密談の再現性が
  上がり、裏切り・同盟の検証可能性（memoryとの整合確認）が高まる
- エンジンの賞金計算・契約判定ロジックは無変更

## 2026-08-17: 引き継ぎメモリ（Handover Memory）の導入 — AIに「1枚だけの記憶」を持たせる

### 概要
「R5のP01は、R1でP04に裏切られた記憶を持っているか？」を調査した結果、**持っていない**ことが判明した。
LLMコールは1-shot（チャット履歴を持たない）、`_round_messages`は毎ラウンドNegotiation冒頭でクリア、
戦略メモ（strategy_history）は同一ラウンド内のみ参照、過去の契約違反・脱落理由もvisible_stateに一切
含まれない。つまり現状のAIは毎ラウンド「初対面」として振る舞っており、談合ゲームの核心である
「裏切りを覚えていて次に活かす」という戦略性が構造的に欠落していた。

対処として、**前ラウンドのmemory＋当ラウンドの会話・契約・結果を材料に、次ラウンドへ持ち越す
自由記述メモ（500〜1000字）を1枚だけ書かせる「引き継ぎメモリ」**を導入した。全ラウンドの客観的な
公開履歴を配布する案（当初プラン）と比較検討した結果、(1) 秘匿情報の漏れリスクがほぼゼロ
（自分の主観メモが自分に返るだけ）、(2) 何を覚え何を捨てるかが戦略になりモデル間の性能差が出る、
(3) 常に最新1枚のみ保持のためラウンドを重ねてもトークンコストが増加しない、という理由で
「引き継ぎメモリ」案を採用した。

材料として渡す「当ラウンドの結果」を検証する過程で、前ラウンド結果の参加者表示が
`参加6人 → 勝者 P01` のように**人数へ潰されており**、§8.1が公開情報と定義する
「各市場の参加者・使用カード（決着後）」のうちカードが一度もプロンプトに描画されていなかった
実装漏れも発見し、あわせて是正した（Part 0）。

### 変更（Part 0: 前ラウンド結果の参加者ID・使用カードを可視化）
- **`engine/game.py`**: `_phase_settlement()`が構築する`_last_round_results["markets"][*]`に
  `commits: [{"player_id", "card_rank"}]`を追加（`mr.participants`から取得。既存の`participants`
  リストは後方互換のため維持）。霧ラウンド（`config.fog_rounds`）では`card_rank`を`"FOG"`に伏せる
- **`llm/prompt_builder.py`**: `_render_last_round_results()`の参加者描画を3段フォールバック化 —
  `commits`があれば`P01[ROYAL_FLUSH]★, P02[FLUSH]★, ...`とID+カード+勝者マークで列挙、
  無ければ`participants`のみ列挙、どちらも無ければ従来どおり人数のみ（後方互換）

### 変更（Part 1: 引き継ぎメモリ本体）
- **`engine/negotiation.py`**: `PlayerAgent.reflect()`を`choose_double_up()`と同様の非abstractデフォルト
  （no-op、`None`を返す）として追加。Bot 8種・`StubAgent`は無変更で動作する
- **`engine/game.py`**: `run()`のフェイズ順を`market_open → negotiation → commit → settlement →
  finance → `**`reflection`**に拡張。新設した`_phase_reflection(round_num)`は
  `config.memory_enabled=False`（既定）または最終ラウンドなら即return、生存者のみ
  `agent.reflect(player_state, round_num, visible_state)`を呼び、1体の例外はゲーム進行を止めず
  握りつぶす。エンジンの賞金計算・判定ロジックには一切影響しない
- **`engine/config.py`**: `memory_enabled: bool = False`（既定、S2プリセットのみTrue）、
  `memory_max_chars: int = 1000`を追加
- **`llm/prompt_builder.py`**: `build_reflection_prompt()`を新設（当ラウンドの会話全件・契約・
  市場結果・自分の資金状況を材料に`{"memory": "..."}`形式で自由記述を要求。文字数上限は
  `config.memory_max_chars`を明示）。`build_negotiation_prompt()`/`build_commit_prompt()`に
  `memory`引数を追加し、`_render_memory_block()`で「## あなたの記憶（前ラウンドから引き継いだメモ /
  これが唯一の記憶です）」セクションとして挿入（`memory=None`なら何も描画しない）。
  DM/broadcast描画ロジックを`_render_message_list()`として共通化（reflection/negotiation/commit
  3箇所で再利用）
- **`llm/response_parser.py`**: `extract_memory()`（JSON `{"memory": "..."}`が取れなければ応答テキスト
  そのものを採用し、`ParseError`を絶対に投げない自由記述専用の抽出経路）と`normalize_memory()`
  （`max_chars`超過分を切り詰め）を追加
- **`llm/llm_agent.py`**: `LLMAgent.__init__`に`self._memory: str = ""`（常に最新の1枚のみ）と
  `self.memory_history: list`（観戦・分析用、エージェントインスタンス内に閉じる）を追加。
  `negotiate()`/`commit()`のプロンプト構築に`memory=self._memory or None`を渡す。新設した`reflect()`は
  `_call_llm("reflection", ...)`を1回（リトライなし）呼び、応答が空またはmemoryが空文字なら
  **前ラウンドの`self._memory`をそのまま維持**（空文字での上書きは絶対にしない）
- **`llm/constants.py`**: 参照0件だった死んだ定数`CONTEXT_RECENT_ROUNDS`を削除
- **`tests/test_prompt_market_info.py`（6件追加）・`tests/test_memory.py`（新規35件）**:
  Part 0（commits記録・霧ラウンド伏せ・3段フォールバック描画）とPart 1（reflectionフェイズの
  発火条件・プロンプト内容・memory注入・API失敗時の維持・秘匿性・reasoning非混入・viewerの
  誤検出防止）を網羅的に検証

### 影響
`config.memory_enabled`の既定値は`False`のため、既存の全プリセット（`default_8/12/20`,
`baseline_v1`）の挙動は変わらない。S2プリセット（`default_8_s2`, `baseline_v1_s2`）でのみ
Reflectionフェイズが有効化され、1試合あたり約95コール（最終ラウンド除く生存者数分）・
約+$0.42（実測ベース試算）のコスト増となる（`COST_LIMIT_TOTAL=12.0`に対し十分な余裕あり）。
エンジンの賞金計算・契約判定ロジックは無変更。

## 2026-08-17: 繰越（キャリーオーバー）と前ラウンド結果がプレイヤーに周知されていなかった問題の是正

### 概要
`trial_C_20260815_202246 / game01` R4でM03の賞金が¥960,000（他市場の2倍）だった件を調査した結果、
高騰ではなく「R3でM03が参加者0＝流札し、¥480,000が繰り越された」ことが原因と確定した（賞金計算自体は仕様どおりで正しい）。
続けて「繰越はプレイヤーに周知されているのか」を確認したところ、**§8.1が「公開情報」と定義し、システムプロンプト自身も
「各市場の参加者・使用カード（決着後）、勝者と獲得額」を公開と明言しているにもかかわらず、visible_state/プロンプトの
どちらにも一度も出力されていない実装漏れ**であることが判明した。実ログの全文検索では「繰越」「キャリーオーバー」「流札」の
言及が全12R×9人ぶんでゼロ件だった一方、繰越で賞金が2倍に見えたR4だけ「高騰」への言及が89件に急増しており（R3は0件、R5は1件）、
情報欠落が「高騰への誘引」という誤解を生みM03への参加者集中を招いていたことも確認した。

### 変更
- **`engine/game.py`**: `_build_visible_state()`のmarketsに`base_prize`/`carryover`を追加（従来は合算済みの`prize_pool`のみ）。
  `Game.__init__`に`self._last_round_results: dict | None = None`を追加し、`_phase_settlement()`で当該ラウンドの
  `market_results`/`new_carryovers`から参加者・勝者・獲得額・高騰有無・次ラウンドへの繰越額のサマリを構築して保持、
  `_build_visible_state()`から`"last_round_results"`として次ラウンドのnegotiation/commit両フェイズに公開する。
  R1は前ラウンドが存在しないため`None`のまま。エンジンの賞金計算・判定ロジック自体は無変更
- **`llm/prompt_builder.py`**: `_render_market_line()`（市場行に`carryover>0`のときだけ「（基本◯万 + 前R繰越◯万）」の内訳を付記）と
  `_render_last_round_results()`（「## 前ラウンド（R{n}）の結果」セクションを新設し、市場ごとに参加人数・勝者・獲得額・高騰有無、
  参加0人の場合は「不成立。賞金◯万円は今ラウンドの{市場}へ繰越」を明示）を新設し、`build_negotiation_prompt()`/
  `build_commit_prompt()`の「## 市場」直後に挿入
- **`viewer/static/index.html`**: `carryover > 0`のとき賞金の直後に`sit-carry`バッジ（「繰越+¥480,000」）を表示。
  勝者が空でも`participants === 0`（流札）のときは従来の「（結果待ち）」ではなく
  「不成立（参加0）— 賞金は次ラウンドへ繰越」と表示するよう修正（`viewer/log_parser.py`は無変更、既に必要な値を保持していた）
- **`viewer/static/style.css`**: `.sit-carry { color: #4a9eff; font-weight: bold; font-size: 0.85em; }`を追加（`.sit-surge`の`#ff6b35`と区別）
- **`doc/uso8000000_dangou_card_spec_v0_8_season2.md`**: §9.1に「市場情報の内訳（基本＋繰越）」と「前ラウンドの市場決着結果」の
  記述を追加し、実装が§8.1の公開情報定義に追いついたことを明記
- **`tests/test_prompt_market_info.py`（新規、9件）**: visible_stateのbase_prize/carryover検証、R1で`last_round_results`が
  Noneであること、参加者0の市場を含む決着後に正しく記録されること、negotiation/commit両プロンプトへの内訳・前ラウンド結果の
  描画、追加情報に秘匿情報（reasoning・debt_balance等）が混入しないことを検証
- **`tests/test_viewer.py`（2件追加）**: index.html/style.cssに表示要素が存在すること、実ログ`trial_C_20260815_202246`の
  R3/M03（参加0）・R4/M03（carryover=480,000、prize_pool=base_prize+carryover）が正しくパースされることを検証

### 影響
プロンプトは1ラウンドあたり数行増えるのみ（トークン増は軽微）だが、エージェントが前ラウンドの決着結果と繰越の理由を
初めて観測できるようになるため、**今後の試合での挙動は過去ログ（trial_C_20260815_202246等）とは異なりうる**。
これはルールの追加・変更ではなく、既に公開情報と定義されていた情報の周知漏れの是正である。

### 検証
- `uv run pytest tests/test_prompt_market_info.py tests/test_viewer.py -v`: 新規11件PASSED
- `uv run pytest tests/ -q`: **382件PASSED**（既存371件 + 新規11件、失敗ゼロ、回帰なし）
- `uv run python scripts/dry_run.py`: Bot12人版が例外なく完走（イベント数566、繰越発生24件を確認）
- LLM API呼び出しはこの変更の検証には使用していない（Bot/ユニットテストのみで完結）

## 2026-08-17: DeepSeek V4 Flash（M6/L6）thinking暴走の是正 — 原因特定・8コール実験・恒久設定変更

### 概要
seed=701の6体12R試験で`L6`（DeepSeek V4 Flash）がAPIエラー0件にも関わらずJSON成立率P04 74%/P05 87%と低く、壁時計の78%を消費していた事象を、読み取り専用調査（実ログ集計・コード確認・DeepSeek公式仕様照合）で原因特定した。DeepSeek `deepseek-v4-flash`は**既定でthinking（effort=high）がON**であり、`llm/models.py`のM6/L6は`extra_params`未設定のためthinkingを無効化していなかった。thinkingトークンは`max_tokens=4000`枠を`completion_tokens`として消費するため、思考だけで4000に達すると`content`が空になり`finish_reason=length`となる。`llm/adapters.py`の`OpenAICompatAdapter`は`content`が空のとき生の`reasoning_content`（思考文）を本文として採用するフォールバックを持つため、JSONでない長文がゲーム側に渡りパースに失敗していた（seed=701実測: `length`時のみreasoning_tokens=output_tokens=4000、latency平均46秒 vs 通常7秒）。この仮説をL6のみ・8コール・$0.10上限の実験で検証し、thinking無効化により`reasoning_tokens=0`・JSON成功率100%・latencyが大幅改善することを確認したうえで、M6/L6双方に恒久設定変更を適用した。

### 変更
- **`scripts/model_smoke.py`**: `--suite thinking_matrix`モードを追加。thinking ON/OFF × max_tokens(4000/2000/1000)の8条件を、`llm/adapters.py`を変更せずに`extra_body`注入で再現する`complete_with_fields()`を新設し、`content`と`reasoning_content`を分離観測（既存アダプタは両者をフォールバックで混ぜてしまうため）。`HARD_MAX_CALLS`を5→8に引き上げ。条件別集計（JSON率/length率/fallback件数/latency個別値/コスト）をMDサマリに追加
- **`tests/test_model_smoke.py`**: フェイクSDKクラス群とAPI非依存テスト10件を追加（8条件のブレークダウン検証・`reasoning_effort`不使用の確認・レジストリ非破壊確認・content/reasoning_content分離検証・fallback記録検証・HARD_MAX_CALLS=8検証等）
- **実API実験（L6のみ・8コール・実測$0.001396）**: `baseline_4000`×2（thinking ON既定）/ `nothink_4000`×1 / `nothink_2000`×3 / `nothink_1000`×2。**8/8成功・0/8 length・8/8 JSON成功**。`nothink_*`は全条件で`reasoning_tokens=0`（無効化パラメータが効くことを確認）。latencyはbaseline 2.3〜5.0秒 → nothink 1.4〜2.8秒に改善（seed=701で観測した`length`時46秒からは大幅短縮）。副次的発見: baseline（thinking ON）の1コールは`finish_reason=stop`かつ成功にも関わらず`content`が空で`reasoning_content`側に最終回答があった＝既存フォールバックは失敗時専用ではなく成功時にも作用しうる
- **`llm/models.py`**: `M6`/`L6`（同一`model_id="deepseek-v4-flash"`）双方に`extra_params={"thinking": {"type": "disabled"}}`・`max_tokens=2000`を追加。`max_tokens`は実験で`nothink_1000`(2/2成功)/`nothink_2000`(3/3成功)双方が合格したが、コスト・latency差が実測上ごく僅かであることと、18体本番では交渉以外のフェイズ（commit/double_up等）や多様な相手を含み出力長のばらつきが増えることを踏まえ、より安全側の2000を採用
- **`tests/test_model_smoke.py`**: レジストリの恒久値変更に合わせ、L6の`extra_params`/`max_tokens`を`None`固定でチェックしていた2箇所を、恒久値（`{"thinking":{"type":"disabled"}}`/`2000`）を検証しつつ「実験のケース構築自体はレジストリを書き換えない」ことを検証する形に更新

### 検証
- `uv run pytest tests/test_model_smoke.py -v`: 20件PASSED
- `uv run pytest tests/ -q`: **371件PASSED**（既存361件 + 新規10件、失敗ゼロ、MODEL_REGISTRY変更後も回帰なし）
- 実APIコール数: **8回**（計画8回・実行8回、上限内）。実測コスト**$0.001396（約¥0.21）**、承認上限$0.10の約1.4%
- 判定: **L6×6を18体本番投入してOK**（M6/L6のthinking無効化＋max_tokens=2000適用済み）

## 2026-08-17: L7全滅の発覚とL3スモークテストによる代替確認、汎用モデルスモークスクリプト追加

### 概要
明け方に実行された seed=701 の6体低コスト負荷試験（`L7,L7,L6,L6,L2,L2`）で、`L7`（Gemini 2.5 Flash-Lite）が173コール全て404 NotFound（"no longer available to new users"）で全滅していたことが判明した（読み取り専用調査で特定、当日昨夜追加したL7定義自体は変更せず）。18体本番の前に代替候補`L3`（Gemini 3.5 Flash-Lite）を最小コストで実機検証するため、汎用モデルスモークテストスクリプトを新規作成し、L3に対して実際に4コールAPIを呼んで疎通・応答品質・コストを確認した。

### 変更
- **`scripts/model_smoke.py`（新規）**: `MODEL_REGISTRY`の1モデルを指定し、少数回のAPIコールで疎通・JSON成立率・latency・token内訳・コストを確認する汎用スクリプト。`--model`/`--calls`（絶対上限5）/`--max-cost`/`--max-tokens`/`--dry-run`をCLI指定可能。既存の`llm/models.py`（`get_model`/`estimate_cost`）・`llm/adapters.py`（`create_adapter`）・`llm/response_parser.py`（`extract_json`/`parse_response`）・`llm/prompt_builder.py`（`build_system_prompt`）をそのまま再利用し、新規ロジックはケース定義・予算ガード・記録処理のみ。`AdapterError`で200文字に切り詰められるエラーメッセージを、失敗時のみ同一クライアントで1回だけ生SDK呼び出しし直して全文（`status_code`/`body`/`response.text`）を取得する仕組みを追加（`llm/adapters.py`は無変更）。次コールの最悪コスト（`estimate_cost()`ベース）を事前試算し、`--max-cost`を超えるならAPIを呼ばずに中断するガードを実装
- **`tests/test_model_smoke.py`（新規）**: フェイクアダプタで`create_adapter`を差し替えるAPI非依存テスト10件。dry-run時にAPI未呼び出し・`--calls`ハード上限超過の拒否・予算超過ガード・成功時のJSONL記録内容とコスト一致・`AdapterError`失敗時の記録継続と非0終了・L3のレジストリ解決を検証
- **L3実機スモークテスト実行**: `uv run python scripts/model_smoke.py --model L3 --calls 4 --max-cost 0.10` を実行。ping 1回 + 18体S2相当のシステムプロンプト（約5,000字）を使った交渉フェイズ想定プロンプト3回、計4コール。**4/4成功、404/429/timeoutなし、JSON率100%、finish_reasonは全て`stop`**。実測コスト$0.003624（承認上限$0.10の3.6%）

### 検証
- `uv run pytest tests/test_model_smoke.py -v`: 10件PASSED
- `uv run pytest tests/ -q`: **361件PASSED**（既存351件 + 新規10件、失敗ゼロ）
- L3実APIコール: 計画4回・実行4回（上限5回以内、追加のエラー全文再取得コールは発火せず）
- 18体負荷試験のGemini枠は `L7` → `L3` へ切り替え可能と判断（`llm/models.py`のL3定義・L7定義はいずれも今回変更なし）

## 2026-08-16: L7（Gemini 2.5 Flash-Lite）追加 — 明日朝の6体→18体低コスト負荷試験の準備

### 概要
Season 2最大想定の18体でengine/llm/logging/viewerが耐えられるかを確認する低コスト負荷試験（6体→18体）を明日実施するため、ロースター仕様書v1.0の12モデル（M1〜M6, L1〜L6）とは別枠の負荷試験専用モデルとして`L7`（Gemini 2.5 Flash-Lite）を`MODEL_REGISTRY`に新規追加した。試合の起動・LLM API呼び出しは一切行っていない（コード追加とテスト・ドキュメント整備のみ）。

### 変更
- **`llm/models.py`**: `MODEL_REGISTRY`に`L7`エントリを新規追加。`model_id="gemini-2.5-flash-lite"`、`provider="Google"`、`adapter_type="gemini"`（既存の`GEMINI_OPENAI_BASE_URL`・`OpenAICompatAdapter`経路を再利用、コード変更なしで疎通可能）、単価は`input_price=0.10`/`output_price=0.40`（ユーザー指定、2026-08-16）。`cached_input_price`/`reasoning_price`/`extra_params`は未設定（L3/M3と同じ扱い）。既存の`L3`（Gemini 3.5 Flash-Lite）・`M3`（Gemini 3.5 Flash）は一切変更していない
- **`tests/test_llm.py`**: 新規クラス`TestL7Gemini25FlashLite`に7件追加（登録内容・単価・アダプタ選択・model_id逆引き・コスト計算・L3/M3不変の回帰・明日朝のロースター文字列がAPI非依存で全解決できること）。うち`test_load_test_rosters_resolve`は`scripts/llm_trial.py`のroster解析が`.strip()`していない仕様（カンマ後にスペースがあると`Unknown model`エラーになる）を踏まえ、明日使う6体/18体ロースター文字列にスペースが混入しないことを固定するテスト
- **`README.md`**: `## LLMトライアル実行（本番API）`節に`### 低コスト6体→18体 負荷試験（インフラ耐久確認）`を追記。6体スモーク（seed=701）→18体（seed=811、6体完了確認後）のコマンドと、roster空白禁止・CoT OFF・18体走行中はビューワーを開きっぱなしにしない旨の運用注意を記載
- **`doc/uso8000000_dangou_card_spec_v0_8_season2.md`**: §11.3単価表に`L7`行を追加し、負荷試験用に2026-08-16追加された12モデル外の枠であることを明記（「現行実装が正」の方針を維持）

### 検証
- `uv run pytest tests/test_llm.py -v -k "L7 or gemini or Gemini"`: 11件PASSED
- `uv run pytest tests/ -q`: **351件PASSED**（既存テストの失敗ゼロ、警告4件は既存かつ本変更と無関係）
- LLM API呼び出し・試合起動は今回一切実施していない

## 2026-08-16: card_idトレード重複バグ修正・Gemini単価是正・トライアルのデタッチ起動化・CoT(A)実装

### 概要
seed=502のR12 `AUTO_COMMIT_FAILURE`（P02/P07）の根本原因調査から着手し、カードトレード時のcard_id重複による手札ドレインバグを特定・修正。続けてコスト表示の単価検証でGemini 3.5系が旧世代(2.5)単価のまま10〜15倍過小だった問題を発見・是正し、Claudeセッションのタイムアウトで長時間トライアルが巻き込まれてkillされる問題をデタッチ起動ラッパーで解消。最後にCoT (Chain-of-Thought) の`reasoning`フィールドを`enable_cot`フラグで追加実装し、6ベンダー混合スモークテストで情報リーク0件・全ベンダーでの正常出力を確認した。

### 変更
- **card_idトレード重複バグ修正（最重要）**: 全プレイヤーが同一card_id体系のデッキを持つ設計上、カードトレードで相手のカードを受け取ると手札内に同一card_idが2枚生じ、`engine/player.py` の `use_card()` がcard_id一致の**全カードを除去**するロジックのため1枚使用で2枚消滅していた（`compute_legal_commits()`が空集合になりAUTO_COMMIT_FAILUREを誘発する真因）。`use_card()`を1枚のみ除去するよう修正し、`swap_card()`は受け取るカードのcard_idが手札と衝突する場合に`_t`/`_t2`...サフィックスでリネームするよう修正（rank不変）。回帰テスト3件追加（`tests/test_card_trade.py`: `test_swap_duplicate_card_id_renamed`/`test_use_card_removes_only_one`/`test_trade_then_use_no_drain`）
- **コスト算出単価の是正（Gemini）**: `llm/models.py` のGemini 3.5 Flash (M3)・Flash-Lite (L3) の単価がGemini 2.5世代のまま放置され、Google公式単価（2026-08時点 ai.google.dev/gemini-api/docs/pricing）に対し10〜15倍過小だった。M3: input $0.15→**$1.50**/output $0.60→**$9.00**、L3: input $0.075→**$0.30**/output $0.30→**$2.50**に修正。DeepSeek V3 (M6) は2026-08-17改定予定のためTODOコメントのみ追加、GPT-4.1 Miniは変更なし。過去のクリーントライアル(seed=501/502/503)のコスト表示は過小だった（seed=503のP02(Gemini)実測: 表示$0.1055→正しくは$1.16）。`tests/test_llm.py`のGemini単価テストを更新
- **トライアル試合のデタッチ起動化**: seed=504（trial_C_20260815_221824）のR10全体停止を調査した結果、LLM側エラーは0件で、Claude/DevRelayセッションのSIGALRMタイムアウトにより子プロセスの`llm_trial.py`ごとkillされたのが原因と断定（P03/P09のR3脱落は正当な矛盾契約によるAUTO_COMMIT_FAILUREで別原因）。`scripts/run_trial.sh`（新規）を`setsid nohup`で親プロセスから完全に切り離して起動するラッパーとして追加。stdout/stderrは`logs/llm/run_YYYYMMDD_HHMMSS.log`に、PIDは`.pid`に記録。`scripts/check_trial.sh`（新規）で最新trialのラウンド・完了状態・PID生存・座席マップ・ログ末尾を確認可能に
- **CoT(A)実装: reasoningフィールド追加**: 各LLMエージェントの応答JSONに`reasoning`フィールドを追加し、行動決定前の状況分析・他者意図推察・最善手の論理的推論を出力させる仕組みを実装。`engine/config.py`に`enable_cot: bool = False`フラグ（デフォルト無効・既存挙動完全維持）。`llm/prompt_builder.py`の`build_system_prompt()`/`build_loan_prompt()`/`build_negotiation_prompt()`/`build_commit_prompt()`/`build_double_up_prompt()`が`enable_cot=True`時にreasoning指示・JSON例を追加。`llm/response_parser.py`の`parse_response()`がトップレベルの`reasoning`を抽出し`strategy["_reasoning"]`に格納（欠落しても脱落しない任意フィールド、戻り値の型は不変で既存呼び出しを壊さない設計）。`llm/llm_agent.py`の`_update_last_log_emotion()`でreasoningもロガーに後付け記録。`scripts/llm_trial.py`に`--cot`フラグ追加（`config.enable_cot=True`を設定）。**情報リーク防止（最重要制約）**: reasoningは神視点のみに記録し、`_build_visible_state()`・`NEGOTIATION_ACTION`イベント・他プレイヤー向けプロンプトには一切含まれない設計（既存の可視化ロジックがそもそもstrategy/reasoningを含まない設計だったためコード変更不要、テスト`tests/test_cot.py`の`TestCoTNoLeak`3件で担保）。テスト20件新規追加（`tests/test_cot.py`）
- **LLMLoggerの逐次書き込み×post-hoc更新のタイミングズレ修正**: CoTスモークテスト実施中に発見。`llm/llm_logger.py`の`log_call()`は即時ファイル追記するが、emotion/reasoningは`_update_last_log_emotion()`でメモリ上のエントリのみ後付け更新されるため、ファイルには反映されないタイミングズレがあった。`save()`メソッドをin-memory entriesでファイル全書き直しする方式に変更。あわせて`scripts/llm_trial.py`の`run_trial_game_c()`（Phase C）に`agent.llm_logger.save()`呼び出しを追加（Phase A/Bは既存実装で呼ばれていたが、Phase Cのみ欠落していた）

### 検証
- 全テスト324件パス
- seed=502クリーン再走・seed=503実行: AUTO_COMMIT_FAILURE 0件、card_idドレインなし（トレード成立分がリネーム済みcard_idで正常に使用できることを確認）
- CoTスモークテスト（seed=601, trial_C_20260816_051835, 6社混合・S2・R1打ち切り、コスト$0.36）: 全6ベンダー（DeepSeek V3/Grok 4.3/Kimi K2.6/Gemini 3.5 Flash-Lite/Claude Haiku 4.5/GPT-4.1 Mini）がJSON率85〜92%を維持しつつreasoningを正常出力（negotiation/commitフェイズで11/12前後）。他プレイヤー向けプロンプト・イベントログへのreasoningリーク**0件**を確認

## 2026-08-15: ビューワー市場高騰バッジ追加

### 概要
市場高騰（サージ）が発動してプールが2倍になった市場が、ビューワーのラウンド状況パネルで判別できず賞金額の不整合に見える問題を修正。`MARKET_RESULT`イベントの`surged`フラグ（engineが既に出力済み）を利用し、高騰市場に「高騰×2」バッジを表示するのみの表示側修正（engine・ゲームロジックは不変）。

### 変更
- `viewer/log_parser.py`: `get_round_states()`のMARKET_RESULTパース時に`target["surged"] = data.get("surged", False)`を追加
- `viewer/static/index.html`: 市場行に`m.surged`ベースの条件付き「高騰×2」バッジを描画
- `viewer/static/style.css`: `.sit-surge`スタイル追加（オレンジ太字）

### 検証
- テスト24件パス

## 2026-08-15: type_b_cardキー不一致バグ修正・契約義務可視化・最終市場周知・EventLogger逐次追記化・pidLabel併記・ビューアdebt表示修正

### 概要
S2フルトライアル（9人全員LLM、seed=501）でR7に全員がcontract_violationで脱落する事象が発生。原因調査の結果、型B契約（type_b_card）のdetailsキー不一致という重大バグを特定・修正。あわせて再発防止と情報周知の3施策（契約義務可視化・最終市場3倍ルール周知・ビューア係員判別）を実施し、同一seedで再実行してR12完走・生還者4人まで改善したことを確認。

### 変更
- **type_b_cardキー不一致バグ修正（最重要）**: LLMが契約提案で `details: {"card": "ONE_PAIR"}` と送信していたが、判定ロジック `audit_type_b()` は `details.get("card_rank")` を参照するため常に `None` が返り、type_b_card義務を持つプレイヤーは正しくカードを提出しても必ず契約違反判定される致命的バグだった（R7全滅の主因）。`engine/contracts.py` の `create_contract()` で入口正規化（`"card"`/`"rank"` → `"card_rank"`）+ 無効なrank名は `ValueError` で契約提案自体を拒否するよう修正。判定ロジック・自動代行Commit・ビューワー表示・契約義務可視化ブロックの4消費者全てが `card_rank` を参照するため入口1箇所の正規化で全解決。`llm/prompt_builder.py` のRULES_SUMMARYにtype_b_card契約提案の正しいJSON例（`{"card_rank": "FLUSH"}`）を追加（テスト: `tests/test_llm.py` 7件追加）
- **契約義務の可視化（毎プロンプト注入）**: 署名済み・未履行の契約義務一覧を `engine/game.py` の `_build_visible_state()` に `my_obligations` として追加し、`llm/prompt_builder.py` の `_render_obligations_block()` ヘルパーで negotiation/commit/double_up の3プロンプト全てに注入。ラウンドでグルーピングし現ラウンドを強調（⬅ 今ラウンド）、同一ラウンドにtype_b_card義務が2件以上ある場合はコンフリクト警告を表示（テスト7件追加）
- **最終市場3倍ルールの周知漏れ修正**: RULES_SUMMARYに最終ラウンド市場3倍ルールの記載が漏れており、エージェントが事前に知れない状態だった。`{final_market_rule}` プレースホルダを追加し `final_market_multiplier > 1` のときのみ条件付き表示するよう修正（テスト2件追加）
- **EventLoggerの逐次追記化**: `engine/events.py` に `output_path` パラメータを追加し、`LLMLogger` と同パターンで `log()` 呼び出しごとに即時追記+flushするよう変更。従来はゲーム終了時の一括書き出しのみで、進行中ゲームをビューワーで観戦できない問題があった。`scripts/llm_trial.py` の `run_trial_game()`/`run_trial_game_c()` で `EventLogger(output_path=event_path)` を指定するよう変更（テスト4件追加）
- **ビューワーの公正証書当事者player_id併記**: 同一モデルを複数体投入した場合にモデル名だけでは個体判別できない問題を調査（エンジン内部はplayer_idで正しく管理されておりバグなし）。`viewer/static/index.html` の `pidLabel()` 関数を1行修正し、`P01（Gemini 3.5 Flash）` のようにplayer_id併記表示に変更
- **ビューワーの借入額・純資産(debt)表示修正**: `viewer/log_parser.py` の `get_game_state()`/`get_round_states()` に `debt`/`initial_loan` フィールドを追加。SNAPSHOTイベントの `cash - free_cash` 近似値を、INTERESTイベントの `old_debt`（SNAPSHOT時点の正確な借金残高）で補正する2段階ロジックを実装（`tests/test_viewer.py` のアサーションを近似式ベースから型・非負検証に更新）

### 検証
- S2フルトライアル（9人全員LLM、seed=501、全修正投入後）: R7全滅 → **R12完走・生還者4人**（Gemini 3.5 Flash上位独占、コスト$1.30、1010コール、JSON率99%）
- 再現性検証（seed=502、1本目）: **R12完走・生還者3人**（1位Gemini 3.5 Flash、コスト$1.25、1008コール、JSON率98%）。TYPE_B_VIOLATION 0件・APIエラー0件・契約バリデーション拒否0件を確認し、type_b_cardバグの再発なしを確認
- 全テストパス（既存 + 今回追加分約20件）

## 2026-08-14: 高騰境界値変更・S2設定統一・LLM対応拡充（カードトレード/倍掛け）・ソロ市場除外バグ修正

### 概要
市場高騰の境界人数調整に始まり、S2設定の二重管理解消、LLMエージェントに欠落していた2アクション（カードトレード・倍掛け）の実装、実際のLLM短時間試行（6体×6R×S2）、そして試行結果から発見した倍掛けのソロ市場除外バグの修正までを連続実施。

### 変更
- **高騰境界人数変更**: `engine/config.py` の `surge_full_participation_max_alive` を S2プリセット（`default_8_s2()`, `baseline_v1_s2()`）で 4→3 に変更。4人生存時の高騰率30.3%→1.3%が厳しすぎた問題への対処（`doc/surge_condition_change_report.md` に追記）
- **S2設定の単一ソース化**: `scripts/simulate.py`/`scripts/simulate_s2_comparison.py`/`scripts/llm_trial.py` が個別にS2設定を組み立てていた（v0.6相当の古い設定が混入）のを `GameConfig.baseline_v1_s2()` プリセットに統一。`llm_trial.py` に `--ruleset S1/S2`・`--roster` オプション追加
- **カードトレードのLLM対応**: `llm/response_parser.py` に `card_trade_propose/accept/reject` のパース処理、`llm/prompt_builder.py` にルール説明・書式を追加。従来LLMエージェントはこのアクションを一切発行できなかった（テスト: `tests/test_response_parser_card_trade.py` 新規13件）
- **倍掛け(double_up)のLLM対応**: `llm/llm_agent.py` に `choose_double_up()` を新規実装（`llm/prompt_builder.py` の `build_double_up_prompt()` でプロンプト生成→JSON抽出→DOUBLE/TAKE判定、パース失敗時はTAKEにフォールバック+警告ログ）。従来はBotのみ選択可能でLLMは常に既定値だった（テスト: `tests/test_double_up_llm.py` 新規13件）
- **LLM短時間試行の実施**: 6体（M6,L6,M5,L2,L1,M3）×6R×S2で実API試行（$0.59、1120秒）。カードトレード・倍掛けが実際に発火することを確認（`doc/llm_short_trial_plan.md`/`doc/llm_short_trial_report.md`）
- **倍掛けソロ市場除外バグ修正**: 試行結果を精査した結果（`doc/double_up_bug_investigation.md`）、仕様§6.2「単独参加市場での賞金獲得は倍掛けの成功判定に含めない」が未実装と判明。`engine/game.py` の `_process_double_up` を2パス方式に変更し、ソロ市場を除外した `non_solo_winners` で成功判定するよう修正。`tests/test_s2_rules.py` に `TestDoubleUpSoloExclusion` 5件を追加
- 全281件テストパス

### 検証
- 高騰境界値: シミュレーションで4人時30.3%への復元、5人以上は不変を確認
- 倍掛けバグ修正: 1,000試合Botシミュレーションでソロ市場成功0件・成功率45.2%（健全な水準）を確認
- 倍掛けバグ調査ではもう1件（「パース失敗」疑義）を検証した結果、実際はマークダウンコードブロックのパースが正常動作しており誤記と判明（バグではない）

## 2026-08-14: 立ち絵透明化スクリプトの閾値修正（108/45）

### 概要
`scripts/transparentize_emotions.py`（キャラ立ち絵42枚の白背景透明化）の閾値が指示値と異なっていたため修正・再変換。

### 変更
- `LUMA_THRESH`: 240→108、`SAT_THRESH`: 30→45（`BLUR_SIGMA=1.2`/`TRIM_PADDING=4`は変更なし）
- `viewer/static/emotions_alpha/`: 42枚を新閾値で再生成（コンタクトシート含む）
- 元画像 `viewer/static/emotions/` とバックアップ `viewer/static/emotions_backup_20260814/` は読み取り専用のまま変更なし

### 検証
- 42/42枚変換成功。透明比率の極端値: `xai_panic.png`=15.0%、`xai_sadness.png`=14.5%（<20%の「残りすぎ」フラグ、他は正常範囲）
- 下端20%領域の不透明画素比率を旧閾値(240/30)と新閾値(108/45)で比較。平均43.8%→35.6%（-8.3pp）に減少し、足元の影(グレー領域)が全体傾向として除去される方向に改善。個別には増加するケースもあり(暗い髪・服の再取り込み等)、Bot判定ではなく目視確認が必要

## 2026-08-14: S2 v0.7/v0.7.1 実装 — 強制返済・カードトレード・市場高騰の全員参加要件

### 概要
Season 2 仕様書 v0.7 の新規メカニクス（強制最低返済・カードトレード）と v0.7.1（ブロードキャスト提案・拒否アクション）を実装。あわせて正式契約が交渉プロンプトに表示されないバグを修正し、市場高騰の判定ロジックに少人数時の全員参加要件を追加した。

### 変更
- **正式契約バグ修正**: `engine/game.py` `_build_visible_state()` に `for_player_id` を追加し、PROPOSED状態の契約を当事者にのみ `contracts_pending` として公開。`llm/prompt_builder.py` に「提案中の正式契約」セクションを追加（原因調査: `doc/contract_investigation_report.md`）
- **強制返済（v0.7 §2）**: `engine/config.py` に `mandatory_repay_enabled`/`mandatory_repay_k`、`engine/player.py` に `compute_mandatory_repayment(debt, remaining, k)`（`ceil(debt/(remaining+k))`）、`engine/finance.py` の Finance フェイズに Step 2 として挿入
- **カードトレード（v0.7 §3 / v0.7.1）**: `engine/models.py` に `CardTradeProposal`/`CardTradeStatus`/`CardTradeProposeAction`（複数宛先 `with_players`）/`CardTradeAcceptAction`/`CardTradeRejectAction`、`engine/actions.py` にバリデーション、`engine/game.py` に `trade_proposals` 管理（ブロードキャスト展開・`offer_id`束ね・1件受諾で残り自動EXPIRED・ラウンド末失効）、`engine/player.py` に `swap_card()`。可視性: 受信者には `offer_id`/`all_targets`/`target_statuses` を非公開
- **fog_rounds 修正**: S2プリセット（`default_8_s2()`, `baseline_v1_s2()`）の `fog_rounds` を `[4,8]`→`[]`（v0.7 §10.2: 霧のラウンド削除）
- **市場高騰の全員参加要件**: `engine/config.py` に `surge_full_participation_max_alive: int = 0`（S2プリセットで4）、`engine/settlement.py` に `_should_surge()` を切り出し。生存者数が閾値以下なら「全員参加」判定、それ以外は従来の `len(mc) > alive_count/2`
- テスト: `tests/test_prompt_pending_contracts.py`(6件)、`tests/test_mandatory_repay.py`(8件)、`tests/test_card_trade.py`(16件)、`tests/test_s2_rules.py`(7件追加)を新規/更新。全247件通過
- 調査レポート: `doc/turn_order_investigation.md`（交渉フェイズは逐次処理・毎巡ランダムシャッフル・「早い者勝ち」は既存実装で担保）、`doc/surge_condition_investigation.md`（76%案は高騰を事実上ゼロ化する危険と判定し不採用）
- シミュレーション: `scripts/simulate_mandatory_repay.py`（4条件×1,000試合、k推奨=2）、`scripts/simulate_surge_change.py`（少人数全員参加あり/なし、各1,000試合）

### 検証
- v0.7: `doc/v0_7_verify_report.md`。強制返済の計算式・端数処理・破産判定、カードトレードの2アクション制・可視性・アトミック実行を仕様書と照合し一致確認
- v0.7.1: `doc/v0_7_1_verify_report.md`。ブロードキャスト可視性・god view記録・4ステータス(PROPOSED/ACCEPTED/REJECTED/EXPIRED)を確認
- 市場高騰: `doc/surge_condition_change_report.md`。5人以上は完全に不変、4人30.3%→1.3%、3人83.6%→9.8%。平均生還者数は4.92→4.91でほぼ不変

## 2026-08-13: 公開LP復旧＋観戦ビューア常駐化（user systemd）

### 概要
公開LP `https://dangou-card-viewer.devrelay.io/` が「Under Construction」フォールバック表示になっている報告を受け調査。原因は viewer サーバ（127.0.0.1:9023）の停止（nohup 常駐プロセスの自然死）で、Caddy が upstream 不達フォールバックを返していた（LP の HTML/画像/`feed.json` 自体は健全）。恒久対策として **user systemd で常駐化**し、クラッシュ・再起動後の自動復帰を実現した。

### 変更
- **新規**: `~/.config/systemd/user/dangou-viewer.service`（リポジトリ外）。`ExecStart=.venv/bin/python -c "from viewer.server import main; main()"`、`Environment=VIEWER_HOST=127.0.0.1 / VIEWER_PORT=9023`、`Restart=always` / `RestartSec=3`、`WantedBy=default.target`。linger（Linger=yes）済みでマシン再起動後も自動起動。
- `README.md`: 観戦ビューアの起動を systemd 常駐運用に更新（status/restart/journalctl の運用コマンドを追記）。
- `doc/devlog.md`: サイクル7.1 を追記。
- viewer のアプリコード（`viewer/server.py` / `viewer/static/lp.html`）は無変更。

### 検証
- `systemctl --user is-active`＝active / `is-enabled`＝enabled。
- ローカル `127.0.0.1:9023`＝200、公開LP＝200、LP本文に「観戦する／敗者の手記」表示（Under Construction でない）。
- クラッシュ耐性: `systemctl --user kill` → 数秒で MainPID が入れ替わり自動再起動・local=200。

## 2026-08-13: LP の実態修正（参加体数・OGP・ブログタイル動的化）

### 概要
公開LP `viewer/static/lp.html` の事実誤認と SNS 共有不備を修正。「AI 20体」→ 実参加 **8体** に統一し、OGP を SNS クローラが解決できる絶対URLに直し、ブログ記事タイルを PixBlog フィードから動的取得（失敗時は静的2件にフォールバック）するようにした。

### 変更
- `viewer/static/lp.html`:
  - 参加体数 20→8（title / meta description / og:description / kicker / hero sub / 概要カード の計6箇所）。
  - OGP `og:image` を絶対URL `https://dangou-card-viewer.devrelay.io/static/lp_hero.png` に修正、`og:url` を追加（SNSカード画像の表示不良を解消）。
  - ブログタイルをハードコード4件から **静的フォールバック2件（開幕・Grok脱落手記）** に置換し、`feed.json?limit=4` 取得成功時に動的タイルへ差し替え（`item.image` 欠落時は `lp_hero.png`）。取得失敗時は静的2件を維持。
  - `.grid` を `repeat(auto-fill, minmax(240px,1fr))` にして件数可変に対応。
- `.devrelay-output/lp.html`: 送付用コピー。

## 2026-08-13: Kimi K2.6 長考問題の根治（JSON有効率 23%→97%）

### 概要
trial_C で Kimi K2.6（M5/L5）が JSON有効率23%・平均レイテンシ99.5s・試合時間の72%を単独占有していた問題を根治。原因は reasoning(thinking) モードが `max_tokens=4000` を内部推論で食い尽くし、JSON回答(content)が空/切断されること。thinking を無効化して解消した。

### 変更
- `llm/models.py`: `ModelInfo` に per-model の `max_tokens` / `extra_params` を追加。M5/L5 に `extra_params={"thinking": {"type": "disabled"}}`、`timeout_seconds=90`。
- `llm/adapters.py`: OpenAI互換アダプタで `extra_params` を `extra_body` としてマージ。Anthropic/OpenAI 両方で `finish_reason`（stop_reason / choices.finish_reason）を usage に載せる。
- `llm/llm_agent.py`: per-model `max_tokens` を使用。`finish_reason=="length"` 検知時は是正リトライに切断ヒントを付与（全モデル共通の防御）。
- `llm/llm_logger.py`: `finish_reason` をログに記録。
- `llm/response_parser.py`: `LENGTH_TRUNCATION_HINT` を追加。
- `scripts/llm_trial.py`: Phase A のモデルを上書きする `--model` 引数を追加。
- `scripts/kimi_investigation.py`: **新規** 調査スクリプト（thinking有無×max_tokensの比較計測）。
- テスト: `tests/test_llm.py` に per-model max_tokens / extra_params / finish_reason のテストを追加、Kimi timeout 期待値を 90 に更新。

### 検証
- 調査（12コール, $0.084）: thinking=disabled で JSON率6/6=100%・平均8.9s。
- dev検証（6R, M5×1+Random×7, 35コール）: JSON率97%(34/35)・平均レイテンシ8.1s・コスト$0.15（12R換算$0.31）・AUTO COMMIT 0。目標（JSON率90%+/レイテンシ60s以下/コスト$1.5以下）を全達成。

## 2026-08-13: 観戦ビューアURLに LP（ランディングページ）を新設

### 概要
公開URL `https://dangou-card-viewer.devrelay.io/` のトップを観戦UI直表示から **LP** に差し替え、「観戦する」「ブログを読む」の2導線を設けた。

### 変更
- `viewer/static/lp.html`: **新規** LP（自己完結・インラインCSS・ダークトーン）。ヒーロー＋2大CTA（▶観戦する→`/watch`、📖敗者の手記を読む→PixBlog `pixblog.net/u/uso8m/`）＋概要3カード＋ブログ記事ピックアップ＋OGP。
- `viewer/static/lp_hero.png`: **新規** ヒーロー画像（既存タイトルバナーを流用）。
- `viewer/server.py`: `@app.get("/")` を LP 配信に変更、`@app.get("/watch")` を新設（観戦UI本体）。観戦UIは相対パス(`api/...`/`static/...`)依存のため **末尾スラッシュ無し** で配信（`/watch/` だとベースURL解決が崩れる）。
- `viewer/static/index.html` / `style.css`: ヘッダに `🏠 トップ`（`/`へ戻る）リンクと `.home-link` スタイルを追加。
- `viewer/README.md`: ルーティング表・画面節を追記。
- テスト: `tests/test_viewer_lp.py` **新規** 4件（`/`=LP・`/watch`=観戦UI・`/watch`非リダイレクト・`/api/games`=200）。全172件パス。

## 2026-08-12: 7感情目「奸」(ニヤリ/smirk)追加＋カード既定表情

### 概要
策略・裏切りを表す新表情 smirk（各vendorの `{vendor}_smirk.png`）を、ブログカードとゲーム内感情の両方で活用できるようにした。

### 変更（レベルA: カード）
- `scripts/blog.py`: `--card-emotion` 既定を `None` に変更。未指定時は lie(LIE DETECTED)/showdown(運命の一手)=smirk、その他=ease に自動分岐（明示指定は全カードで最優先）。
- `.claude/skills/blog-visual/SKILL.md` / `reference/brand.md`: emotion 一覧に `smirk` と既定分岐を追記。

### 変更（レベルB: ゲーム内感情）
- `llm/response_parser.py`: `VALID_EMOTIONS` に `"奸"` を追加（6→7感情）。
- `llm/prompt_builder.py`: emotion 選択肢に `"奸"(策略・ニヤリ・してやったり)` を追加。
- `viewer/static/index.html`: `EMOTION_EN` に `'奸':'smirk'`、`EMOTION_EMOJI` に `'奸':'😏'` を追加。
- `viewer/README.md`: 日英対応表・絵文字フォールバックに 奸/smirk/😏 を追記。
- テスト: `tests/test_viewer.py`(EMOTION_EN 検証を8感情へ)、`tests/test_llm.py`(プロンプトに"奸"を検証)を同期。

### 検証
- `--card lie` 無指定で smirk が出る（model は ease 維持・明示は最優先）。過去ログ・既存感情は不変。

## 2026-08-10: 感情表示機能

### 概要
各LLMエージェントが毎思考時に感情(喜怒哀楽+焦疑)を自己申告し、観戦ビューアに表示する機能。

### 変更
- `llm/prompt_builder.py`: strategyスキーマにemotionフィールド追加（6感情+平静）
- `llm/response_parser.py`: emotionバリデーション（列挙外→平静フォールバック、リトライ対象外）
- `llm/llm_logger.py`: emotionフィールド追加
- `llm/llm_agent.py`: strategy解析時にemotionをログ更新
- `viewer/log_parser.py`: emotion抽出（state/timeline両API）+ emotion_history（直近5件）
- `viewer/static/index.html`: パネルに大きな感情絵文字+直近5件推移、タイムラインに感情バッジ
- `viewer/static/style.css`: 感情表示スタイル
- `viewer/static/emotions/`: 空ディレクトリ（後日画像差し替え用）
- `viewer/README.md`: 画像差し替え方法（命名規則・推奨サイズ）
- テスト9件追加（全115件パス）

### 検証
- dev preset 1試合（6R, Haiku×1）: emotionが全コールで出力（主に"楽"、R1T3で"焦"に変化）
- ビューア state API: emotion="楽", emotion_history=["楽","楽","楽","楽","楽"] を確認

## 2026-08-10: 観戦ビューア公開起動（port 9023）

### 概要
`https://dangou-card-viewer.devrelay.io/` で観戦ビューアが公開稼働開始。

### 変更
- ビューアをport 9023 (127.0.0.1) で起動、Caddy逆プロキシで公開
- `viewer/README.md`: 「公開運用(port 9023)」節追記（起動/停止コマンド）
- `doc/issues.md`: ビューア恒久化課題を追記

### 確認
- ローカル: `curl http://127.0.0.1:9023/api/games` → 11試合列挙
- 公開: `curl https://dangou-card-viewer.devrelay.io/` → 200 OK
- PID: 50701

## 2026-08-10: 観戦ビューア公開対応（TestFlight連携）

### 概要
観戦ビューアをdevrelay.ioのTestFlight機能で公開するための改修。

### 調査結果
- **案A推奨**: uso8mで常駐(0.0.0.0:9025) + Caddyで逆プロキシ(`dangou-card-viewer.devrelay.io`)
- 案B(devrelayユーザー実行)は非推奨（ホーム700で権限問題）

### 変更
- `viewer/server.py`: 環境変数対応(HOST/PORT/ROOT_PATH/LOG_ROOT/TOKEN) + 簡易認証 + root_path
- `viewer/static/index.html`: 静的パス相対化（root_path対応）
- `viewer/README.md`: 常駐起動手順 + TestFlight設定ガイド
- `doc/testflight_integration.md`: 新規（アーキテクチャ判定+Caddy設定手順）
- テスト3件追加（全106件パス）

### けいすけ側の必要作業
1. `/etc/caddy/sites.d/dangou-card-viewer.devrelay.io` 作成（`reverse_proxy localhost:9025`）
2. `sudo systemctl reload caddy`

## 2026-08-10: 観戦ビューア v1

### 概要
進行中/完了済みの試合をブラウザでリアルタイム観戦できるWebビューア。
FastAPI + vanilla JS SPA。127.0.0.1:8787バインド（SSHトンネルでアクセス）。

### 構成
- `viewer/` パッケージ: server.py(FastAPI), log_parser.py(差分キャッシュ), static/(HTML+CSS)
- API: /api/games(一覧), /api/games/{trial}/{game}/state(8パネル用サマリ), /api/games/{trial}/{game}/players/{pid}/timeline(時系列詳細)
- 2秒ポーリング自動更新、ダーク基調8パネルグリッド、パネルクリックで時系列モーダル
- 依存追加: fastapi, uvicorn
- テスト10件追加（全103件パス）
- 11試合のログを正常に列挙・表示確認

## 2026-08-10: Step 3C-prep3 Kimi/Gemini JSON成立修正（最終仕上げ）

### 概要
**6社12モデル全て疎通OK + JSON成立達成（12/12）。**

### 原因と対策
1. **Kimi K2.6**: contentが空でreasoning_contentに本文 → DEFAULT_MAX_TOKENS=1000→4000引上げ + reasoning_contentフォールバック
2. **Gemini 2.5 Flash**: thinkingトークンがmax_tokensを消費しJSON切断 → 同上のmax_tokens引上げで解決
3. **Gemini Flash-Lite(L3)**: gemini-2.5-flash-lite 404 → gemini-3.5-flash-lite に更新（実在確認済み）
4. **Gemini Flash(M3)**: gemini-2.5-flash → gemini-3.5-flash に更新

### 変更
- `llm/constants.py`: DEFAULT_MAX_TOKENS 1000→4000
- `llm/adapters.py`: OpenAICompatAdapterにreasoning_contentフォールバック
- `llm/models.py`: M3→gemini-3.5-flash、L3→gemini-3.5-flash-lite
- `scripts/api_check.py`: max_tokens 1000→4000
- テスト2件追加（全89件パス）
- Kimi K2.6 JSONレイテンシ: 30〜43秒（reasoningに時間を要する）

### 3C推奨8体（6社カバー）
L3(Gemini 3.5 Flash-Lite), L6(DeepSeekV3), M6(DeepSeekV3), M2(GPT-4.1), L2(GPT-4.1-mini), L1(Haiku4.5), M1(Sonnet5), M3(Gemini 3.5 Flash)

## 2026-08-10: Step 3C-prep2 Gemini JSON修正/Kimiエンドポイント/モデルID確定

### 概要
前回疎通7/12→今回**疎通10/12、JSON成立8/12**。3社の問題を修正。

### 変更
- `llm/response_parser.py`: extract_jsonにtruncatedコードブロック対応（開始フェンスのみの場合のフォールバック）
- `llm/adapters.py`: OpenAICompatAdapterにtemperatureフォールバック（Kimi k2.6等の制限対応）
- `llm/models.py`: 12モデル全IDを確定（gpt-4.1/gpt-4.1-mini/grok-4.5/grok-4.3/kimi-k2.6/deepseek-chat）、Kimiのbase_urlをapi.moonshot.aiに修正
- `scripts/api_check.py`: max_tokens 500→1000
- テスト5件追加（全87件パス）

### 結果
- **新規成功**: DeepSeek(M6/L6)が疎通+JSON成立（入金完了）
- **改善**: Kimi(M5/L5)疎通成功（エンドポイント修正）。JSONは未成立（thinking/reasoning系の冗長出力で構造化JSONが不安定）
- **未解決**: Gemini M3はJSON抽出失敗（応答がtruncateされる）、L3は404（flash-liteがOpenAI互換で非公開の可能性）
- **3C推奨8体**: M1(Sonnet5), M2(GPT-4.1), M4(Grok4.5), M6(DeepSeekV3), L1(Haiku4.5), L2(GPT-4.1-mini), L4(Grok4.3), L6(DeepSeekV3) — **4社8モデル**

## 2026-08-09: Step 3C-prep 6社12モデル疎通確認+Geminiアダプタ

### 概要
Step 3C(全LLM戦)の前提として12モデルの疎通+JSON出力を確認。
Geminiアダプタ実装、モデルID確定、Sonnet 5のextended thinking対応。

### 結果
- **疎通OK: 7/12**, **JSON成立: 6/12**
- Anthropic(Haiku+Sonnet5)、OpenAI(GPT-4o+4o-mini)、xAI(Grok3+3-fast): 全てJSON成立
- Gemini: 疎通OKだがJSON抽出失敗（フリーテキスト応答。response_format指定が必要な可能性）
- Moonshot: 401認証エラー（APIキーの問題）
- DeepSeek: 402残高不足（支払い関連の問題）
- 総コスト: $0.009

### 変更
- `llm/adapters.py`: create_adapterでgemini→OpenAICompatAdapter、Sonnet 5のtemperature非対応+ThinkingBlock対応
- `llm/models.py`: モデルIDを実在IDに更新（claude-sonnet-5、gpt-4o、gemini-2.5-flash等）、Gemini base_url設定
- `scripts/api_check.py`: 新規（12モデル疎通チェックスクリプト）
- テスト2件追加（全82件パス）

## 2026-08-09: Step 3.3 プロンプトキャッシュ調査と詳細化

### 概要
キャッシュ未発動の原因調査。system promptが閾値(2048トークン)未満→仕様書v0.5準拠で詳細化(3972文字/推定3489トークン)。
ただし実API検証で**キャッシュはAPI側制約で依然ゼロ**（アカウント/モデル/リージョンの制約と推定）。

### 変更
- `llm/prompt_builder.py`: RULES_SUMMARYを大幅詳細化（脱落3種別、Settlement 8Step、キャリーオーバー、匿名通信、公開報奨、口約束vs契約、経済注意点など）
- テスト2件追加（全80件パス）
- `doc/investigation_cache.md`: 調査レポート

### 副次効果
RULES_SUMMARY詳細化はLLMのルール理解向上に有益。キャッシュとは独立した価値として維持。

## 2026-08-09: Step 3.2 契約アクション経路の修正とクラッシュ耐性

### 概要
調査レポート(doc/investigation_obligor.md)に基づくP0〜P3修正。seed 7のクラッシュ解消。

### 変更
- **P0**: LLMログ逐次書き込み化（クラッシュ時もログ保全）+ スタックトレース保全+ファイル保存
- **P1**: response_parserにcontract_propose terms検証（必須キー: obligor/counterparty/ob_type/round_num）。プロンプトにterms形式の具体例追加
- **P2**: game.py _execute_negotiation_action()をtry/exceptで防御（ACTION_ERRORイベントで記録、試合続行）
- **P3**: response_parserにbounty_post変換追加。プロンプトにbounty_post例追加
- テスト5件追加（全78件パス）

### 検証結果（seed 7、以前クラッシュした条件）
- **完走成功**: 12R走破、DM 19件+broadcast 17件の活発な交渉
- コスト: $0.38（P01: $0.11, P02: $0.27）
- 有効JSON: P01 20/21, P02 49/52
- キャッシュ読取: 0トークン（フル12Rでもキャッシュ未発動。要調査）
- ハイライト検出: 6件（H4破産、H8無駄撃ち、H9山分け×2、H13 AUTO COMMIT、H14キャリーオーバー）

## 2026-08-09: Step 3.1 LLM層の修正・堅牢化・ハイライトログ

### 概要
9点修正: コミット健忘バグ、交渉中commit除去、キャッシュ最適化、空回り削減、検証モード、ハイライトログ、API堅牢化、Phase Bレポート再生成、ルール要約監査。

### 変更
1. **コミット健忘修正**: commit時プロンプトに当該Rの交渉ログ+直前strategyメモを含有
2. **交渉中market_commit除去**: RULES_SUMMARYのアクション一覧を交渉/コミットで分離、パーサで拒否
3. **キャッシュ最適化**: Anthropic cache_control付与、LLMLoggerにcache_read_tokens記録
4. **空回り削減**: 新規メッセージなし+turn≥2で自動pass（AUTO_PASS_ON_NO_NEWS）
5. **検証モード**: `--preset dev`（6R/5巡/1試合）で高速開発テスト
6. **ハイライトログ**: 14ルール検出のscripts/highlights.py + バックフィル
7. **API堅牢化**: 60秒タイムアウト + 指数バックオフリトライ + error_type記録
8. **Phase Bレポート再生成**: 既存4試合(有効JSON率100%)からレポート生成
9. **ルール要約監査**: R12自動返済・生還判定タイミング追記、コスト上限$5
- テスト6件追加（全73件パス）

### 検証試合結果（Phase B dev preset: 6R/5巡/Haiku×2+Random×6）
- 実行時間: 118秒（目標5分以内 ✓）
- コスト: $0.08
- キャッシュヒット: 0（6R短縮版のため）
- broadcast: 10回（LLM同士が市場配分を提案する交渉行動を確認）
- ハイライト検出: 9件（H4破産×2、H8無駄撃ち、H9山分け×2、H12際どい生還、H14キャリーオーバー×3）

## 2026-08-09: Step 3 LLMアダプタ + LLM Agent + Step 3A/3B試験

### 概要
LLM APIへの接続アダプタ・エージェント・プロンプトシステムを実装。
Claude Haiku 4.5で接続試験(3A)と交渉試験(3B)を実施。

### 変更
- `engine/config.py`: `baseline_v1()` プリセット追加（flat賞金・prize_scale=2.0・survival=200万）
- `llm/` パッケージ新規: adapters(Anthropic+OpenAICompat+GeminiStub), models(12モデルレジストリ), prompt_builder, response_parser, llm_agent, llm_logger, constants
- `scripts/llm_trial.py`: Phase A/B試験スクリプト
- `tests/test_llm.py`: モックテスト16件（API非呼出）
- 依存追加: anthropic, openai, python-dotenv
- 全67テストパス

### Step 3A結果（接続試験: Haiku×1 + Random×7、1試合）
- 有効JSON率: **99%**（80/81コール）
- 12R走破: ✓ / AUTO COMMIT: 0回
- repay未発行（LLMが返済を選択せず条件未達で脱落）
- コスト: $0.35 / 入力128Kトークン / 出力44Kトークン

### Step 3B結果（交渉試験: Haiku×2 + Random×6、1試合完走）
- LLM同士の交渉を確認: P01「M02に行く」P02「M01を狙う」と市場分散の発言
- DM: 0件 / broadcast: 4件（全体発言で市場宣言）
- 有効JSON率: 2/35（コスト上限$2が早期に発動しpass化。次回は上限$5に緩和推奨）
- コスト: $0.02
- 嘘の兆候: strategyメモと発言が一致（この1試合では嘘なし）
- カードカウンティング言及: strategyメモ内で言及なし

## 2026-08-09: Step 2.3 SCSミラーマッチ耐性試験（最終Bot実験）

### 概要
SCSが「単独で強い戦略」か「支配戦略」かを判定するミラーマッチ実験。

### 結果（健全判定）
- Roster A (8種×1): SCS **95.9%**（従前94.1%から+1.8pp、tie-break導入の影響は許容範囲）
- Roster B (SCS×4+Rnd×4): SCS **0.6%**（**-95.3pp激落**）、Random **83.9%**
- Roster C (SCS×8): SCS **11.5%**、上位カード衝突率 **98.3%**、賞金分割率 **58.1%**
- **結論: 「単独では強いが普及すると自壊する戦略」= ゲーム構造は健全。Step 3 LLM投入へ進む**

### 変更
- `bots/base.py`: 4メソッドにtie-breakランダム化追加（同期防止）
- `scripts/mirror_match.py`: 新規（4構成ミラーマッチ実験）
- テスト1件追加（全51件パス）

## 2026-08-09: Step 2.2 賞金スケジュールA/B比較実験

### 概要
逓増傾斜がStrongCardSave支配の主因かを検証する対照実験。

### 結果
- **StrongCardSave生還率: tiered 98.4% → flat 94.1%（-4.3pp）**
- **判定: 賞金傾斜は主因ではない**。フラット化しても94%維持
- 次の仮説: (1) 高ランクカードの絶対的勝率、(2) 序盤低コスト戦略の資金効率
- カードランク別使用ラウンドは両条件でほぼ同一（温存パターン不変）
- フラット化でRandom(+9.9pp)、HoneyPot(+8.0pp)、HighPrizeHunter(+7.0pp)が改善

### 変更
- `scripts/simulate.py`: `--prize-schedule {tiered,flat}` オプション追加
- `scripts/compare_schedules.py`: 新規（A/B比較実験スクリプト）
- テスト1件追加（全50件パス）

## 2026-08-09: Step 2.1 Bot返済ポリシー + 経済パラメータ掃引

### 概要
全Botに早期返済ポリシーを追加。24セルのパラメータ掃引で適正値を特定し、1000試合で検証。

### 詳細
- Bot共通返済ポリシー: 毎R最初の手番で「残R数×EntryFee + 予備20万」を残して超過分を全額返済（RepayAction）
- simulate.pyに `--prize-scale`, `--survival-cash` オプション追加
- scripts/sweep.py: prize_scale×survival_cashの24セル掃引スクリプト
- テスト1件追加（全49件パス）
- 推奨パラメータ: prize_scale=2.0, survival_cash=200万 → **平均生還2.62人（目標帯1.6〜4.0に収まる）**
- Bot別生還率: StrongCardSave 98.4% / Collusion 92.3% / Random 40.8% / その他 0.4〜11.5%（Bot間格差は大きい）

## 2026-08-09: Step 2 ルールベースBot 8種 + 1000試合シミュレーション

### 概要
LLMなしルールベースBot 8種（Random/Conservative/StrongCardSave/HighPrizeHunter/EmptyMarketHunter/Collusion/Betrayal/HoneyPot）を実装し、1000試合シミュレーションでバランス計測。

### 詳細
- `bots/` パッケージ: 8種Bot + 基底クラス + 構造化インテントプロトコル + 定数管理
- `scripts/simulate.py`: マルチプロセス並列シミュレーションランナー（--roster, --no-logs, --workers対応）
- エンジン変更（最小限）: game.pyにメッセージ履歴追加、config.pyにdefault_8()追加+バリデータ柔軟化
- テスト12件追加（全48件パス）
- 1000試合: 3.0秒で完走、summary.csv + report.md生成
- バランス結果: 8人×768万賞金では全員condition_not_met（Entry Fee 960万が賞金を超過）。パラメータ調整はStep 3以降

## 2026-08-09: Step 1.1 レビュー指摘2件の修正

### 概要
`doc/review_step1.md` で検出した懸念事項2件を修正。

### 詳細
1. **報奨バリデーション**: 達成者型(achievement)×PLAYER_ELIMINATED の組合せを明示的に拒否するチェックを `engine/actions.py` に追加（§7.2: 因果帰属が機械判定不能）
2. **finance.py ハードコード除去**: `round_num < 12` → `round_num < config.num_rounds` に変更
3. テスト3件追加（全36件パス）

## 2026-08-09: Step 1 ルールエンジン実装

### 概要
仕様書v0.5に基づく決定論的ルールエンジンを `engine/` パッケージとして実装。

### 詳細
- `engine/` 16モジュール: config, models, cards, player, market, contracts, bounty, actions, autocommit, settlement, finance, elimination, negotiation, events, rng, game
- 5フェイズ進行（Market Open → Negotiation → Commit → Settlement → Finance）
- Settlement 8Step処理（§5.2準拠）
- 型A契約Atomic執行 / 型B契約自動監査
- FreeCash検証 / 自動代行Commit / 脱落時強制清算
- 20人版/12人版パラメータ切替対応
- 受け入れテスト16件 + 追加テスト17件（全33件パス）
- ドライランスクリプト（12人×12ラウンド、JSONLログ出力）
- README.md（実行方法・パラメータ設定・JSONL仕様）
## 2026-08-18: 本戦用player/gameコストcap（$5/$40）を導入

- 本戦runnerは `GameConfig.per_player_game_cost_cap_usd`（既定$5）と
  `game_cost_cap_usd`（既定$40）を正本として `GameCostBudget` を明示注入する。
- call前予約と成功usageの事後精算を導入。budget blockはAPIを呼ばず、parse retryを経由せず既存fallbackへ移行する。
- 旧 `COST_LIMIT_PER_GAME` / `COST_LIMIT_TOTAL` と本戦の$5/$12停止・警告を削除した。
- Phase 1/2/3 model_matrixは本戦budgetを注入せず、既存 `PHASE_DEFAULTS` / `BudgetedAdapter` は無変更。

## 2026-08-19: Phase 2比較の単発送信化と生レスポンス保存

- Phase 2だけは `create_adapter(..., max_retries=0, allow_temperature_fallback=False)` を使用し、
  adapter・SDK retryとtemperatureエラー時の追加送信を抑止する。
- Anthropic / OpenAI互換の両adapterが `max_retries` をSDKまで伝播できるようにした。既定引数は従来どおりで、
  Phase 1/3・本戦のretry挙動は変更しない。
- `phase2_calls.jsonl` に `response_text` 全文、requested/response model、match判定を記録する。
  API例外で本文がない場合は `response_text: null` とする。認証情報・request headerは記録しない。
- Phase 2のper-model capは `$0.03`、phase capは `$0.22` のまま。実API・dry-run・既存runのstate変更は未実施。

## 2026-08-20: Phase 3 runtime override監査とコスト検証記録

- Phase 3限定で `--effort` と `--timeout-seconds` を追加。H1のadaptive thinking effortとH4のtimeoutを、
  registryを変更しないruntime overrideとして明示指定できるようにした。
- 実際に適用されたmax tokens / effort / timeoutを、Phase 3 JSONL・state・レポートへ監査値として保存する。
  非対応モデルへeffortは送らず、未指定・非対応はnullとして記録する。
- `run_20260818_041810` ではH1を`8000/high/600`で完走、H4を`2000/null/180`で完走した。
  18モデルの最終Phase 3 state合計は`$0.927449`。再試験履歴を含むPhase 3合計とは区別する。
- `doc/cost/api_cost_estimate_2026-08-20.md`に、Provider Consoleのユーザー提供値、M3 R12実績、
  棄却した線形外挿、および段階的R12計測方針を固定した。

## 2026-08-22: Gemini 3.7 Flash 検証枠追加、L-only 12席 R12実戦、Viewer脱落表示バグ2件修正

- `llm/models.py` に検証専用 `M3_G37_EVAL`（`gemini-3.7-flash`）を追加。正式ロスターの
  `M3`（Gemini 3.5 Flash）は変更しない。Google公式価格（input $0.75 / output $3.75 per 1M、
  2026-12-31までの導入価格）、`supports_temperature=False`、`reasoning_effort="low"` を反映。
  `scripts/gemini_37_eval.py`（新設）でPhase 1〜3の段階的検証（実API疎通・談合カードJSON適合・
  3.5 vs 3.7比較）を実施し、3.7は3.5比で実測64.04%低コストと確認（詳細:
  `doc/devlog/2026-08-22_060137.md`〜`_064103.md`、`doc/cost/gemini_37_flash_pricing_2026-08-22.md`、
  `doc/cost/gemini_35_37_comparison_pricing_2026-08-22.md`、`doc/gemini_37_flash_eval_report.md`）。
- 固定ロスター `L1,L1,L2,L2,L3,L3,L4,L4,L5,L5,L6,L6`（L-only 12席・S2・R12）で実戦を完走。
  1,407 calls / $5.993606、budget block 0、5席生還（詳細: `doc/devlog/2026-08-22_093500.md`、
  `doc/trials/l12_r12_2026-08-22.md`、`doc/cost/l12_r12_pricing_2026-08-22.md`）。
  `scripts/llm_trial.py` / `scripts/l6_r12_report.py` / `scripts/model_matrix.py` を
  この実戦・レポート生成に必要な範囲で調整。
- 上記R12実戦runのViewer表示で2件の既存バグを発見・修正（Engine・trialログ・秘匿境界は無変更）。
  1. `viewer/log_parser.py` の `get_round_states()` が `AUTO_COMMIT_FAILURE`
     （合法Commit0件による強制脱落）を脱落イベントとして未ハンドルで、当該プレイヤー
     （run中のP09）が「生存」と誤表示されていた。ハンドリングを追加し修正
     （`doc/devlog/2026-08-22_094134.md`）。
  2. 同関数の脱落一覧に重複排除ガードが無く、`SURVIVAL_CHECK`+`FORCED_LIQUIDATION` の
     二重発火（R12生存条件未達の当該プレイヤーP12）で同一ラウンドに同一人物が2件表示されていた。
     `add_elimination()` ヘルパで同一round・同一playerを先勝ちで1件に正規化
     （`doc/devlog/2026-08-22_104500.md`）。生のtimeline/契約outcomesは間引かない。
  3. いずれも `dangou-viewer.service` を再起動して実API・実trialで反映確認済み
     （`doc/devlog/2026-08-22_102900.md`、`doc/devlog/2026-08-22_112000.md`）。
- `tests/test_viewer.py` に回帰14件（AUTO_COMMIT_FAILURE系6件 + 脱落重複排除系8件）を追加。
  `tests/test_model_matrix.py` / `tests/test_model_smoke.py` / `tests/test_registry_18.py` /
  `tests/test_l6_r12_trial.py` / `tests/test_gemini_37_eval.py`（新設）も本作業範囲で更新・追加。
  全テストスイート **780 passed**。

## 2026-08-22: FINAL_REFLECTION（脱落者の最終コメント）実装

- 脱落確定ラウンドの末尾（通常のReflectionフェイズ直後）で、脱落者本人に一度だけ最終コメント
  （敗因・他プレイヤー評価・最後の一言、3キーJSON: `emotion`/`defeat_cause`/`comment`）を
  書かせる演出/記録専用機能を追加した。ゲーム結果・生存判定・払い戻しロジックには一切影響しない。
- `GameConfig.final_reflection_enabled`（既定False）で制御し、`default_8_s2()` /
  `baseline_v1_s2()` のS2プリセットでのみ有効化した。既定挙動・非S2プリセットは無変更。
- `Game._phase_final_reflection()` は「`is_alive=False` かつ `elimination_round==round_num`」
  でstateから対象を導出し、`_final_reflection_done` 集合で1脱落=1回を保証する。engineが
  同一脱落に対し `ELIMINATION`+`FORCED_LIQUIDATION` 等の複数eventを出しても二重発火しない。
  `_build_elimination_context()` で契約違反/破産/R12生存条件未達の3種のreason_labelと
  清算内訳を合成し、`build_final_reflection_prompt()`（`llm/prompt_builder.py`）に渡す。
- `llm/response_parser.py` に `parse_final_reflection()` を新設。4段フォールバック
  （JSON正常→他キー組立→salvage復旧→プレーンテキスト受理）で、Handover Memoryとは逆に
  「失敗時は空にせずプレーンテキストで残す」安全側設計とした。`_recover_memory_field` を
  `_recover_string_field(stripped, key)` へ一般化し、`comment` キーにも対応させた。
- `LLMAgent.final_reflect()` は `self._memory` / `memory_history` を一切変更せず、
  結果を `self.final_reflection` にのみ保持する。API失敗・budget block・空応答は
  ステータス付きの安全な既定値を返しGameResultへ影響しない。`_call_llm()` に
  `max_tokens_override`（既定None、後方互換）を追加し、`final_reflection_max_tokens`
  （既定1000、通常の2000より低くしてbudget block発生率を抑制）を適用する。
  `final_reflect` は `PlayerAgent` 基底クラスに追加せず、`Game` 側の `getattr`+`callable`
  判定でduck-typing的にスキップするため、Bot/StubAgentは無変更。
- `tests/test_final_reflection.py` を新規41件で追加（発火条件・elimination_context導出・
  prompt描画・parser edge case・LLMAgent単体・実ログクロスチェック）。既存
  `tests/test_memory.py` 64件は無回帰。全体回帰 `uv run pytest tests/ -q` は
  **849 passed**（808既存＋41新規）。
- 過程で `tests/test_model_matrix.py` の4件が、Phase3統合テストの実ゲーム経路
  （`build_phase3_config()` が `baseline_v1_s2` を使用）でプレイヤーが意図通り脱落し
  FINAL_REFLECTIONが末尾に追加発火したことで、`logical_calls` 期待値と
  「最後のcallのmax_tokens」を検証する箇所が陳腐化して失敗した。これは仕様どおりの
  正しい挙動と判断し、テスト側（`logical_calls` を+1、`max_tokens` 捕捉を本編最初の
  callのみに変更）を修正して整合を取った。`scripts/model_matrix.py`・Engine本体は無変更。
- `uv run python scripts/dry_run.py`（12人版Bot専用）は例外なく完走。`git status --short logs/`
  は空で実trialログへの書き込みは無し。読み取り専用で `logs/llm/trial_C_l12_r12_20260822/`
  のGAME_END確定値に対し導出ロジックを手動再現し、既知の7脱落・5生存者が期待どおり
  1回/0回として導出できることを確認した（`.devrelay-output/final_reflection_targets_2026-08-22.md`）。
- Viewer側の表示対応は本サイクルのスコープ外（実データが無いため次サイクルへ）。

## 2026-08-22: FINAL_REFLECTION 実API小規模疎通試験

次のL12×R12本番トライアル前に、FINAL_REFLECTIONが実プロバイダ境界を越えて全モデルで
動くかを、フルゲームを回さず最小コストで確認する疎通試験を実施した。新規スクリプト
`scripts/final_reflection_smoke.py`を追加し、現行L12ロスターのユニーク6モデル
（`get_model("L1")`〜`get_model("L6")`、ハードコードなし）に各1回だけ実API呼び出しを行った。

- Bot 12体（`bots.BOT_REGISTRY`）だけの実`Game`インスタンスを対象ラウンドまで進めて
  本物の会話・市場結果をAPI課金ゼロで作った上で、`engine.elimination.forced_liquidation()`
  （本物の清算関数）で対象seatを合成脱落させ、実際に存在する5種のevent形状
  （`BANKRUPTCY`単独/`AUTO_COMMIT_FAILURE`単独/`ELIMINATION`+`FORCED_LIQUIDATION`/
  `FORCED_LIQUIDATION`単独/`SURVIVAL_CHECK`+`FORCED_LIQUIDATION`）をengine本体と同一の
  `data`構造で記録した。対象seatのみ`LLMAgent`へ差し替え、実際に
  `Game._phase_final_reflection()`を1回呼び出す（`_build_elimination_context()`→
  `build_final_reflection_prompt()`→`final_reflect()`→`parse_final_reflection()`→
  ログ後付けを全て実経路で通す）。`engine/`・`llm/`・`bots/`本体は無変更。
- API-freeゲート（`--parser-selftest`/`--dry-run`/`--mock`）を実装し、実API実行前に
  内部で強制的に自動実行して1つでも失敗したら実APIには一切進まない設計にした。実装中に
  2件の設計バグを発見・修正した:「`--dry-run`が実際に`_phase_final_reflection()`を呼んでおり
  0-call証明になっていなかった」問題と、「`sent_max_tokens`が常に`None`で検証項目
  `max_tokens=1000`override確認が未検証だった」問題（`RecordingAdapter`を新設して解消）。
- 安全策として`GameCostBudget`（本番の予約/精算経路）と`scripts.model_matrix.BudgetedAdapter`
  （インポートのみ、無変更で再利用）の二重ガード、`--run-id`ディレクトリの
  `mkdir(exist_ok=False)`による二重送信防止を実装した。
- `uv run pytest tests/test_final_reflection.py -q`は**41 passed**、
  `uv run pytest tests/ -q`は**849 passed**（無回帰）。API-freeゲート全PASS後、
  `--run-id fr_smoke_20260822 --max-calls 6 --max-cost 0.08`で実API実行し、
  6/6 API成功、実測コスト合計**$0.030300**（計画worst_case $0.03368、上限$0.05以内）。
  model_id/provider/base_urlは全件`get_model(key)`と一致、`sent_max_tokens=1000`を
  6/6実測確認（L6のレジストリ既定2000からのoverrideも確認）。Memory非破壊・state非破壊
  6/6、二重発火なし6/6、二重送信防止（同一`--run-id`再実行はAPI呼び出し0件で即中断）を
  確認した。`git status --short logs/llm/`は空（既存trialログへの書き込みなし）。
- 発見: C1（L1: Claude Haiku 4.5）は`finish_reason=max_tokens`でJSON応答が途中で
  物理的に打ち切られ、C6（L6: DeepSeek V4 Flash）は`comment`文字列内の未エスケープ
  改行でstrict JSON解析が失敗し、いずれも`ok_recovered`へフォールバックして
  `emotion`/`defeat_cause`を喪失した（生ログでは両方とも本来含まれていたことを確認済み）。
  ゲーム状態・Memory・生存判定への影響はゼロ（安全項目は全PASS）だが、Planに定めたGO基準
  「`parse_status ∈ {ok, ok_assembled}`が全件」を満たさないため、**判定はNO-GO（条件付き）**
  とした。具体的な修正案（`final_reflection_max_tokens`引き上げ、`ok_recovered`ティアでの
  `emotion`/`defeat_cause`正規表現救済追加）を`doc/trials/final_reflection_smoke_2026-08-22.md`
  へ記録した。いずれも`llm/response_parser.py`/`engine/config.py`側の改善であり、本サイクルの
  スコープ外（次サイクル候補）。
- 出力: `logs/smoke/final_reflection_fr_smoke_20260822/`（`calls.jsonl`, `manifest.json`,
  `real_llm_calls.jsonl`, `report.md`）、`doc/trials/final_reflection_smoke_2026-08-22.md`、
  `.devrelay-output/final_reflection_smoke_2026-08-22.md`。

## 2026-08-22: FINAL_REFLECTION 3000-token化 + parser救済強化 + L1/L6再試験（NO-GO解消・GO判定）

直前の疎通試験（上記）が出したNO-GO（条件付き）の原因2件を修正し、対象2モデルのみで
実API再試験しGOを得た。原因はC1（L1: `finish_reason=max_tokens`によるJSON物理切断）と
C6（L6: `comment`内未エスケープ改行によるstrict JSON失敗）が`ok_recovered`へフォールバックし
`emotion`/`defeat_cause`を喪失していたこと。

- **出力上限引き上げ**: `engine/config.py`の`final_reflection_max_tokens`を`1000→3000`
  （プロバイダ側物理切断を避けるハード上限であり狙う長さではない、と明記）。適用箇所は
  `llm/llm_agent.py:final_reflect()`の`max_tokens_override`経由のみで他フェイズは無影響
  （grep 2箇所のみで確認）。`llm/prompt_builder.py:build_final_reflection_prompt()`に
  「800〜1500字程度で簡潔に」誘導と`comment`内改行の`\n`エスケープ指示を追加し、
  `3000`という数値自体はプロンプトに一切出さない（`dry_run_prompts.md`目視で確認）。
- **parser救済ティア強化**: `llm/response_parser.py`に境界を尊重したスキャン
  `_recover_string_field_bounded()`（後続キーを飲み込まない、`emotion`/`defeat_cause`向け）と
  共有ヘルパ`_unescape_json_string()`、全損時も生JSONを漏らさずプレーンテキスト化する
  `_degrade_wrapper_to_text()`（新ステータス`ok_plaintext_wrapper`）を新設し、
  `parse_final_reflection()`の救済ティアを`emotion`/`defeat_cause`/`comment`独立救済へ
  全面書き換えた。全フェイズ共有の`extract_json()`とHandover Memory側が使う
  `_recover_string_field()`は無変更のまま温存。新規`salvaged`フィールドをFINAL_REFLECTION
  イベント（`engine/game.py`）とログ後付け（`llm/llm_agent.py`）へ追加伝播（既存キー無変更）。
- **テスト**: `tests/test_final_reflection.py`に実際のcycle 1生ログ形状を元にした新規9件
  （C1/C6形状の3キー救済、境界スキャンの後続キー非侵食、ラッパー漏洩ゼロ等）と
  config既定値確認1件を追加。`uv run pytest tests/test_final_reflection.py -q`は
  **51 passed**（既存41+新規10）、`tests/test_memory.py`は**64 passed**（無回帰）、
  `uv run pytest tests/ -q`は**859 passed**（既存849+新規10、失敗0）。
- **L1/L6再試験**: API-freeゲート（`--parser-selftest`拡張・`--dry-run --cases C1,C6`・
  `--mock --cases C1,C6`）全PASS後、`--run-id fr_smoke2_20260822 --cases C1,C6 --max-calls 2
  --max-cost 0.05 --max-cost-per-call 0.03`で実API実行。2/2 API成功、実測コスト合計
  **$0.009883**（計画worst_case $0.02025、上限$0.05以内）。C1は`finish_reason=end_turn`
  （旧`max_tokens`から改善）、C6は`comment`内改行が正しく`\n`エスケープされており、
  両方とも`parse_status="ok"`（`salvaged=[]`、救済不要）で3キーとも一発正常取得。
  `state_unchanged`/`memory_unchanged`は前後完全一致、`second_call_fired=False`、
  両commentの全文を人手確認し脱落理由と清算内訳の整合を確認。Planの「Change 5」11項目の
  GO基準をすべて満たし、**判定はGO**。二重送信防止・`git status --short logs/llm/`空も再確認。
- 詳細は `doc/devlog/2026-08-22_153938.md` および
  `doc/trials/final_reflection_smoke2_2026-08-22.md` を参照。
