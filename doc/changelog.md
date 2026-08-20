# Changelog

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
