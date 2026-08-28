# プロジェクト固有ルール

## devlog追記ルール

タスク(ビルド)完了時、**1サイクル＝1ファイル**で開発記録を新規作成すること。
既存 devlog への追記・全文読み込みは禁止（コンテキスト肥大の原因になるため）。

- 保存先: `doc/devlog/` 配下
- ファイル名: `doc/devlog/YYYY-MM-DD_HHMMSS.md`
  （例: `doc/devlog/2026-08-16_085830.md`。`TZ=Asia/Tokyo date '+%Y-%m-%d_%H%M%S'` を使う）
  ※ コロン `:` はファイル名に使わない（git/URL 互換のため `_` 区切り）
- 中身の形式: 冒頭に `# YYYY-MM-DD HH:MM JST ｜ サイクルX.X: タスク名`、
  本文に 要求 / 実行 / 検証 / 発見 を平文3〜10行。
  技術的明細は changelog、devlog には「何を狙い・何が起き・何を発見したか」の物語だけ。
  数値(テスト数・コスト・生還率等)は具体値で記す。
- 索引の更新: 作成のたび `doc/devlog/INDEX.md` の末尾に1行だけ追記する
  （`- 2026-08-16_085830 | サイクルX.X | タスク名 | 1行要約`）。INDEX.md は常に軽量に保つ。
- 過去ログの参照: 経緯が必要なときのみ、まず `doc/devlog/INDEX.md` を読み、
  該当する個別ファイルだけを読む。`doc/devlog/` を一括 cat / 全読みすることは禁止。

## S2設定の単一ソース化ルール

Season 2 (S2) のゲーム設定は `engine/config.py` の `GameConfig.default_8_s2()` / `baseline_v1_s2()` プリセットを**唯一の正**とする。スクリプト側（`scripts/*.py`）で個別に `GameConfig(...)` を組み立てて S2 パラメータを再現するのは禁止。過去にプリセット更新が各スクリプトへ伝播せず、シミュレーション結果が古い設定のまま出続けるバグを起こした（`doc/simulate_s2_stale_investigation.md`）。新規スクリプトで S2 設定が必要な場合は必ずプリセット関数を呼び出すこと。

## 契約detailsキーの正規化ルール

型B義務（特にtype_b_card）のdetailsキーは `create_contract()` の入口で必ず正規化する。LLMが `"card"`/`"rank"` 等の同義語で送っても `"card_rank"` に統一し、無効なrank名は `ValueError` で契約提案自体を拒否する。判定ロジック（`audit_type_b`）・自動代行Commit・ビューワー表示・契約義務可視化ブロックの4消費者全てが `card_rank` キーを参照する設計のため、入口1箇所での正規化が全消費者に効く。過去にキー不一致（`"card"` vs `"card_rank"`）により、正しくカードを提出したプレイヤーが常に契約違反判定される重大バグが発生した（R7全滅の主因、`doc/changelog.md` 2026-08-15参照）。

## EventLogger逐次追記パターン

`engine/events.py` の `EventLogger` は `output_path` 指定時のみ `log()` 呼び出しごとに即時追記+flushする（`llm/llm_logger.py` の `LLMLogger` と同パターン）。`output_path` 未指定時は従来通りメモリ保持のみでディスクI/Oは発生しない（`simulate.py` 等の高速シミュレーション経路に副作用なし）。進行中ゲームをビューワーで観戦させたい場合は `EventLogger(output_path=...)` を明示すること。

## card_idの一意性は手札内で保証されない

全プレイヤーが同一card_id体系のデッキ（例: 全員が`"TWO_PAIR_1"`）を持つゲーム設計のため、カードトレードで相手のカードを受け取ると**同一card_idが手札内に2枚存在しうる**。`engine/player.py` の `use_card()` はcard_id一致のカードを**1枚だけ**除去する実装（全除去すると2枚同時消滅し手札ドレインを起こす、過去のR12 AUTO_COMMIT_FAILUREの真因）。`swap_card()` は受け取るカードのcard_idが手札と衝突する場合、`_t`/`_t2`...サフィックスでリネームしてから手札に加える（rankは不変）。手札を操作する新規コードを書く場合、card_idでの一致判定は必ず「1件のみ」を意図しているか確認すること。

## LLMLoggerのpost-hoc更新とsave()の役割

`llm/llm_logger.py` の `LLMLogger.log_call()` は逐次書き込み（クラッシュ耐性のため即時ファイル追記+flush）だが、emotion/reasoningは `llm/llm_agent.py` の `_update_last_log_emotion()` で**メモリ上のエントリのみ**後付け更新される。ファイルに最終値を反映するには試合終了後に必ず `LLMLogger.save()`（in-memory entriesで全書き直し）を呼ぶこと。Phase A/Bの `scripts/llm_trial.py` は元々呼んでいたが、Phase C (`run_trial_game_c()`) では欠落しており、emotion/reasoningがファイルに反映されないバグがあった（2026-08-16修正）。新しい実行フェーズ関数を追加する際は、試合終了時に全エージェントの `agent.llm_logger.save()` を呼ぶことを忘れないこと。

## 長時間トライアルはデタッチ起動する

Claude/DevRelayセッションはSIGALRMタイムアウトを持ち、フォアグラウンド/子プロセスとして起動した長時間の `llm_trial.py` はセッションタイムアウトに巻き込まれてkillされる（seed=504のR10全体停止の原因）。12ラウンドフル試合やコスト$1を超えるような長時間トライアルは、必ず `bash scripts/run_trial.sh --phase C ...` （`setsid nohup` で親プロセスから完全に切り離す）で起動し、`bash scripts/check_trial.sh` で進行確認すること。短時間（R1打ち切り等）のスモークテストでも念のため同様に起動するのが安全。

## CoT reasoningフィールドは秘匿情報（情報リーク厳禁）

`engine/config.py` の `enable_cot=True` 時、LLM応答JSONの `reasoning` フィールドは `llm/response_parser.py` で `strategy["_reasoning"]` に格納され、`llm/llm_agent.py` 経由で `llm/llm_logger.py`（神視点のみ閲覧可能なJSONLログ）にのみ記録される。**`engine/game.py` の `_build_visible_state()`・`NEGOTIATION_ACTION` イベント・他プレイヤー向けプロンプトのいずれにも `reasoning`/`_reasoning` を含めてはならない**。新しい可視化経路（ビューワーAPI・イベント種別・プロンプトテンプレート等）を追加する際は、`strategy` 辞書をそのまま他プレイヤーに公開しないこと（`_reasoning` キーの混入に注意）。担保テスト: `tests/test_cot.py::TestCoTNoLeak`。

## 引き継ぎメモリ（memory）は絶対にvisible_stateへ入れない・空文字で上書きしない

`config.memory_enabled=True` 時、`LLMAgent._memory` は次ラウンドへ持ち越す自由記述メモ（`llm/llm_agent.py`）。
このメモは**エージェントインスタンス内（`self._memory`）に閉じ、`engine/game.py` の `_build_visible_state()` を
一切経由しない**ことで秘匿性を構造的に担保している。新しい可視化経路（ビューワーAPI・イベント種別・観戦パネル等）
を追加する際は `memory`/`memory_history` を他プレイヤー向けの出力に混入させないこと。
また `LLMAgent.reflect()` はAPI失敗・空応答・`{"memory": ""}` のいずれの場合も**前ラウンドの `self._memory` を
変更しない**（空文字での上書き禁止）。`negotiate()`/`commit()` へ渡す `memory` は毎回 `self._memory or None` を
参照するだけなので、reflect側で誤って空文字を書き込むと次ラウンド以降ずっと記憶が消えたまま気づかれない
危険がある。担保テスト: `tests/test_memory.py::TestLLMAgentReflect`（`test_memory_preserved_on_api_failure` 等）・
`TestMemorySecrecy`。

## DM本文は当事者以外に対しキーごと削除する（`_visible_messages()`）

`engine/game.py` の `_build_visible_state()` が返す `"messages"` は `_visible_messages(for_player_id)` を経由する。
DM（`type == "dm"`）は送信者・宛先以外に対し **`message` キー自体を辞書から削除**して返す（`redacted: True`を付与）。
空文字での上書きではなくキー欠落にしているのは、reasoning・memoryと同じ「構造的秘匿」パターンを踏襲するため
（`str(state)` に本文が一切現れないことをテストで機械的に検証できる）。sender/to/turn等のメタデータは残すため、
「密談が行われている事実」自体は非当事者にも見える。`for_player_id=None` で呼ばれた場合は誰とも一致しないため
全DMが安全側（redacted）に倒れる。新しい可視化経路（ビューワーAPI・イベント種別・新プロンプトテンプレート等）
を追加する際は `_round_messages` を生で（`_visible_messages()` を経由せず）他プレイヤー向け出力に混入させない
こと。匿名通信（`anonymous_broadcast`）は本文は全員に公開するが `sender: None` で掲載者のみ秘匿する（§8.2）。
担保テスト: `tests/test_dm_secrecy.py`（`TestFullScanNoLeak` が全プレイヤー横断の漏洩スキャンを行う）。

## Viewerの公開表示と神視点を分離する

Viewer APIは既定で`view=public`とし、DM本文・宛先、匿名発言の実発信者、契約条項・義務、sensitive action由来のreasoning previewを返してはならない。`get_player_timeline()`、`get_round_states()`、`get_player_round_detail()`のいずれも同じ公開境界を使うこと。公開表示の構造化itemには`visibility: "public"`と表示ラベルを付け、UI側の推測だけで秘匿性を判断しない。

ゲーム内の秘匿情報を確認する必要がある場合だけ`view=god`を使用できる。God viewは`VIEWER_GOD_TOKEN`と`X-Viewer-God-Token`の一致を必須とし、未設定・不一致は403にする。tokenは`localStorage`等の永続領域・URL・git・ログ・devlogへ残さない。タブ終了で自動破棄される`sessionStorage`に限り、同一タブ内でGod認証を1回だけ行い以後の全God対応API呼び出しで使い回す（プレイヤー詳細を開き直すたびの再入力を避ける）目的での一時保持を許可する。サーバー側token（`VIEWER_GOD_TOKEN`）はrepo外の権限制限されたEnvironmentFileからuser systemdへ注入する運用を変えない。God viewで返せるのは構造化済みのゲーム情報（DM、匿名発言の実発信者、契約と履行記録）だけであり、system prompt、user prompt、`response_text`、APIキー、認証情報、ゲーム外の運用情報は返さない。

神視点UIでは`visibility: "secret"`を受けたカードに、色だけでなく`🔒`等のアイコンと文字ラベルを常に表示する。公開カードも`🌐`等のラベルを持たせる。自由記述のreasoning／Handover Memoryがモデル自身によって秘匿内容を引用する意味論的漏洩は別リスクなので、公開viewでsensitive actionに紐付くpreviewを返さず、新たな可視化経路を追加するときは同じ境界を回帰テストで確認する。

## LLMコストは常に `_usage_cost(model, usage)` 経由で算出する（`estimate_cost()` を直接input/outputだけで呼ばない）

`llm/models.py` の `estimate_cost(model, input_tokens, output_tokens, cache_read_input_tokens=0,
total_tokens=0)` はキャッシュ割引・thinking差分課金に対応しているが、`cache_read_input_tokens`/
`total_tokens` を渡さずに呼ぶと **Geminiのhidden thinkingが無料扱い・xAI等のキャッシュ割引が
未反映・Anthropicのusage慣習差（`input_tokens`にcache_readが含まれない）が正規化されない**まま
過小/過大請求になる（`scripts/model_matrix.py` Phase 1で実測発覚、2026-08-18）。usageから課金額を
求める箇所は必ず `scripts/model_matrix.py` の `_usage_cost(model, usage)` を経由すること（Anthropic
のみ`input_tokens`へ`cache_read_input_tokens`を合算してから`estimate_cost`へ渡す正規化を行う）。
新しいコスト記録経路（新フェーズ・新スクリプト・ビューワー集計等）を追加する際は `estimate_cost()`
を素朴にinput/outputだけで呼ばないこと。担保テスト: `tests/test_model_matrix.py`
（`test_usage_cost_*`・`test_phase1_records_corrected_cost_*`・`test_phase2_cost_*`・
`test_budgeted_adapter_cost_*`・`test_phase3_game_records_corrected_cost`・
`test_phase3_records_cost_to_state_jsonl_and_report`）。Phase 1/2/3 とも `_usage_cost()` へ統一済み
（Phase 3 `BudgetedAdapter.complete` は2026-08-18に修正、予算消費判定 `spent_usd` にも正しく反映される）。
**既知の残余課題**: (1) 2026-08-18、`_worst_case_cost`（`scripts/model_smoke.py:worst_case_cost`）に
`ModelInfo.hidden_thinking_reserve_tokens`（既定0）を追加し、`estimate_cost(..., total_tokens=
approx_input+max_tokens+reserve)` で hidden thinking 分を事前予約するよう是正した（**予算上限
`PHASE_DEFAULTS`/`DEFAULT_MAX_COST_TOTAL` は変更していない**。見積是正の結果ブロックされる
モデルが増えるのは意図した結果）。対象は実測でmax_tokens外の課金が確認されたGemini/xAIの
CORE_18内6モデルのみ: `M3`/`L3`/`H3`（Gemini）・`M4`/`L4`/`H4`（xAI）。512は実測最大343
（xAI L4, max_tokens=64時点）に対する**約1.5倍の経験的安全マージン**であり、provider側で
thinking budget/reasoning effortの上限を送っていない以上、**provider保証の数学的worst-case
上限ではない**（関数名が`worst_case_cost`でも、この6モデルについては観測ベースの保守的
予約見積であることに留意）。Anthropic/OpenAI/Kimi/DeepSeekの12モデルとcore外の`L7`は
`hidden_thinking_reserve_tokens=0`のまま（`L7`は既知の残余ギャップ、CORE_18対象外のため
本サイクルでは未対応）。担保テスト: `tests/test_model_smoke.py`
（`test_worst_case_cost_zero_reserve_models_unchanged`・`test_worst_case_cost_includes_
gemini_hidden_thinking`・`test_worst_case_cost_includes_xai_reasoning`・
`test_worst_case_cost_h2_not_double_counted`・`test_worst_case_cost_reserve_targets_are_
exactly_expected_set`）、`tests/test_model_matrix.py`（`test_phase_defaults_unchanged`・
`test_worst_case_cost_reserve_targets_are_core18_members`・`test_phase3_guard_blocks_
earlier_for_thinking_models`・`test_budgeted_adapter_plain_models_unaffected`）。
(2) 本戦の実績計算も `llm/costing.py:usage_cost()` に統一し、Anthropicの
`cache_read_input_tokens`合算を適用する。GameCostBudgetが明示注入された本戦だけは、同モジュールの
`worst_case_cost()`で事前予約し、player/gameの実績を同じ式で精算する。Phase 1/2/3は
`scripts/model_matrix.py`の既存BudgetedAdapterだけを使い、本戦budgetは注入しない。
(3) `scripts/model_smoke.py --suite thinking_matrix` はケース単位で`extra_params`を上書きし
DeepSeek等でもthinkingを強制有効化するため、モデル単位の`hidden_thinking_reserve_tokens=0`
前提が崩れる（手動診断専用・`--max-cost`明示前提のため据置、既知の残余リスクとして明記のみ）。
(4) `PHASE_DEFAULTS[2]["max_cost"]`自体（見積計算式ではなく上限値そのもの）は、system prompt長の
増加でCORE_18予約合計が上限へ接近・超過した際に**人間が個別サイクルで判断して調整する値**であり、
見積ロジックが自動追随することはない。Cycle 3でaction一覧2行追加（129文字増）により0.54%超過し
`xfail(strict=True)`で可視化、Cycle 4で人間判断により$0.22→$0.23へ引き上げてheadroom 3.83%を確保した
（`scripts/model_matrix.py`側の予約計算ロジック・他Phaseの`max_cost`・per-model cap・モデル単価は
無変更）。system prompt長を変えない変更であれば`reservations["H1"] == 0.0289`（`tests/test_model_matrix.py`）
が不変であることが、予約計算ロジック側を触っていないことの検知テストとして機能する。

## matrixのstrict retryは下位再送・temperature fallbackも明示的に止める

`llm/adapters.py` の `create_adapter(model_info, max_retries=None,
allow_temperature_fallback=True)` は、スクリプト外側・adapter内部・SDK内部のretryに加え、temperature
エラー時の「temperatureを外して再送」互換経路も持つ。`--retries N` を各層へ素朴に伝播すると多重送信と
なり、コスト上限ガードがHTTP回数を正しく見積れない。

スクリプト層が試行回数を管理するPhase 1は `max_retries=0` で下位retryを止め、外側ループだけで再試行する。
Phase 2の有料比較は外側retryも持たないため、必ず
`create_adapter(model, max_retries=0, allow_temperature_fallback=False)` を使い、adapter/SDK retryと
temperature fallbackをともに止める。これによりクライアント実装からのAPI送信は1 logical callにつき最大1回となる。
送信後のネットワーク断でproviderが受信・課金済みかどうかは確定できないが、クライアントが再送することはない。

Phase 3のmatrixミニゲームも外側retryを持たないため、Phase 3経路に限り同じstrict引数を渡す。loan・
negotiation・commitの各logical callは最大1 transport sendとし、parse correctionだけはゲーム仕様上の別logical
callとして許容する（通常3、最大7 call/model）。Phase 1/2/3以外の本戦・model_smokeなどは既定挙動を維持する。
Phase 2の監査では `phase2_calls.jsonl` の `response_text`（API本文全文）、`requested_model`、`response_model`、
`model_match` を使用する。Phase 3の集計にはrequested/response model、model match、logical calls、
negotiation/commit correction数を追加し、全文・usage・finish reason・latency・costは個別LLMログの既存保存を維持する。
認証情報やrequest headerをJSONLへ記録してはならない。担保テスト:
`tests/test_llm.py::TestSingleHttpRequestGuarantee`、
`tests/test_model_matrix.py::test_phase2_uses_strict_single_request_adapter_and_records_full_response`、
`tests/test_model_matrix.py::test_phase3_uses_strict_adapter_and_records_model_audit_and_corrections`。

## Phase 2 Structured Outputs はモデル別request specを単一経路で解決する

`scripts/model_matrix.py` の `_phase2_user_content()`、`_phase2_schema_for_model()`、
`_phase2_request_options()`、`_phase2_worst_case_cost()` は、Phase 2に限るモデル別request specの単一経路である。
実送信と予約計算は必ず同じprompt、schema、実効max tokensを使うこと。予約専用のprompt/schemaを別定義したり、
Phase 1/3・本戦へPhase 2用optionを漏らしてはならない。

H1（Claude Opus 5）はcanonical schemaを直接送らず、flat transport schemaと専用promptを使い、raw responseを保存後に
`normalize_h1_phase2_transport()`でcanonical dictへ戻す。M3/H3（Gemini）はcanonical schemaを維持する。M3だけ
`reasoning_effort="minimal"`、H3は`"low"`を送り、`thinking_level`/`thinking_budget`と併送しない。H3の912-token
overrideはPhase 2だけであり、487 hidden-thinking実測 + 400 visible JSON allowance + 25 safety marginから導出したもの。
M3/H3の両方を再試行する際は、total capを引き上げず、M3の実績コストを確認してからH3の予約ゲートを評価する。

## Phase 3 runtime overrideは非破壊・監査可能・対応モデル限定にする

Phase 3に限り、`scripts/model_matrix.py` の `--effort` と `--timeout-seconds` を明示的な再試験条件として使える。
`_phase3_effective_model()` はregistryを変更せず、実効`max_tokens`とtimeoutを持つ`ModelInfo` copyを作る。
`--effort`は現在H1 (`claude-opus-5`) のAnthropic adaptive-thinking経路だけへ
`thinking={"type": "adaptive"}` と `output_config.effort` として送る。非対応モデルには送信せず、
実効effortもnullとして扱う。

結果は、CLI入力をそのまま記録するのではなく、実際に適用した
`effective_max_tokens`、`runtime_effort`、`runtime_timeout_seconds`を`phase3_calls.jsonl`、state、
Phase 3 report、最終matrix reportへ追加フィールドとして保存する。未指定値はnull（レポートでは`—`）とし、
過去runの欠損フィールドを壊さない。overrideはretry、temperature fallback、コスト計算、hidden reserve、
parse correction仕様を変更してはならない。担保テスト: `tests/test_model_matrix.py` の
`test_phase3_h1_effort_reaches_anthropic_request_and_is_audited`、
`test_phase3_unsupported_effort_is_not_sent_and_audits_null`、
`test_phase3_timeout_override_reaches_openai_sdk_without_registry_mutation`、
`test_phase3_runtime_cli_parse_and_rejects_non_phase3_overrides`。

## 新モデル検証は `_EVAL` 専用キーで正式ロスターと分離する

新モデル（例: Gemini 3.7 Flash）を評価するときは、正式ロスターのキー（`M3`等）を書き換えず、
`M3_G37_EVAL` のような検証専用キーを `MODEL_REGISTRY` に追加する。正式キーは常に既存モデルを指し続け、
本戦・過去runの再現性を壊さない。検証専用キーは `tier=""` とし、Phase 1〜3の段階的評価
（実API疎通 → 談合カードJSON適合 → 正式モデルとのコスト比較）を経てから、採用するかどうかを
別途判断する。価格・sampling parameter対応・thinking挙動は provider公式一次情報を確認日付付きで
コード内コメントに固定する。

## Viewerの脱落判定はEngineが出す全イベント種別を尊重しつつ表示側で正規化する

`viewer/log_parser.py` はEngineが出すJSONLイベントを解釈するだけで、Engineの脱落判定
（`engine/elimination.py forced_liquidation()` が唯一の脱落確定経路）には一切介入しない。
ただしEngineは1回の脱落に対し複数種類のイベント（`AUTO_COMMIT_FAILURE`単体、
`ELIMINATION`+`FORCED_LIQUIDATION`、`MANDATORY_REPAY_FAILED`+`FORCED_LIQUIDATION`、
`SURVIVAL_CHECK`+`FORCED_LIQUIDATION`など）を出すことがあるため、Viewer側の脱落系イベント
ハンドリングは次の2点を常に両立させる。

1. **新しい脱落イベント種別を追加するときは、`get_game_state()`（生存数・バッジ）と
   `get_round_states()`（ラウンド別脱落者一覧）の両方に反映する。** 片方だけに追加すると
   （実例: `AUTO_COMMIT_FAILURE`が`get_round_states()`のみ未対応だった）、生存数と
   ラウンド詳細の表示が食い違う。
2. **`rd["eliminated"]`のような表示用の脱落者一覧は、同一round・同一playerにつき1件に
   正規化する。** 正規化は表示用データ構造だけに適用し、生のtimeline
   （`get_player_timeline()`）や契約 `outcomes`（god限定）には適用しない。複数の脱落系
   イベントが実際に発生した事実は、生ログ側でそのまま確認できる状態を維持する。

代表1件を残す規則は「先勝ち」（最初に検出したイベントのreason/detailsを採用）で統一する。
`get_game_state()`は元々 `if pid not in eliminated:` ガードで先勝ちだったため、
`get_round_states()`側も同じ規則の `add_elimination()` ヘルパに揃える。

`get_game_state()`の`players[].elimination_round`（player一覧カードの「R5脱落」表示用）も
同じ先勝ち規則に従う（`mark_eliminated()`ヘルパ）。ただしラウンド番号だけは「最初に判明した
値」を採用し、先着イベントの`round_num`が欠落/不正だった場合に後続イベントで補完できるように
する。`round_num`が欠落・0以下・非int（旧runログ等）の場合は推測せず`None`のまま表示側で
非表示にフォールバックする。

## Viewerの秘密契約terms/outcomesは「engine正規化前のLLM生JSON」

`get_round_states()`が返す契約`terms`は、engineが正規化した`Contract`/`Obligation`
オブジェクトではなく、`_collect_contract_terms()`が`llm_logs/*_llm_calls.jsonl`の
`contract_propose`アクションから直接拾った**LLM生出力**である。
`engine/contracts.py`の`create_contract()`が行う`card`/`rank`→`card_rank`正規化や
不正ランクの拒否は、Viewerのtermsには一切かかっていない。そのため`details`の
キー形状はLLM次第で揺れる（実データでは`type_b_card`の77%が`details.card_rank`
ではなく`details.card`）。表示側（`fmtObligation()`, `viewer/static/index.html`）は
`ob_type`・`details`のキーを一切信用せず、既知4種（`type_a_payment`/
`type_b_market`/`type_b_no_market`/`type_b_card`）以外は「未対応の契約条項:
<type>」に、キー欠落は`?`にフォールバックする。**推測して意味を捏造しない。**

契約`outcomes`（履行/違反イベント）は、engineイベント自体が`contract_id`を
持たないことが多い（`TYPE_A_EXECUTION`/`TYPE_A_FAILURE`/`TYPE_B_VIOLATION`/
`AUTO_COMMIT`/`AUTO_COMMIT_FAILURE`）。`_contract_id_of_outcome()`が
`obligation_id`（`{contract_id}_OB{nn}`; `engine/models.py`の採番規則）から
contract_idを復元する。`obligation_id`を持たない`TYPE_A_FAILURE`/
`AUTO_COMMIT`/`AUTO_COMMIT_FAILURE`は紐付けない（推測しない）。
`view=public`では従来どおり`terms: None`, `outcomes: []`でマスクされる
（`_contract_involves_player()`によるgod側の本人契約スコープも含め、
このマスクロジック自体は変更していない）。

## 契約の中身はHandover Memoryではなく毎ラウンドstateから再提示する（my_contracts）

LLMは1-shot呼出しで会話履歴を持たず、契約内容を保持する手段が自由記述の
Handover Memoryしか無かった。実run `trial_C_l12_r12_20260822` では、P03/P09間の
契約`C_90159021`（義務: P09がR6にM02へ参加してはいけない、義務者=P09のみ）が
R6で成立→P09がR6 commitで契約違反脱落したにもかかわらず、P03のnegotiation/commit
プロンプトには契約の中身を再提示する経路が一切なかった。原因は`_build_visible_state()`
の`my_obligations`が (a) `ob.obligor == for_player_id`のみを対象とする（受益者=
counterpartyには何も出ない）(b) `ob.round_num >= round_num`のみを対象とする（過去
ラウンドの義務は消える）という二重フィルタを持つ「今後のTODO」専用リストだった
ため。一方reflectionの`contracts_public`は毎R「[C_90159021] 状態: active」を
条項なしで出し続け、P03のメモは「契約はactiveだが内容は忘れた」へと劣化した
（R6→R11で6回反復注入された結果、R12メモで「R12で何か義務があるかもしれない」
という誤った警戒に増幅）。

修正: `_build_visible_state()`に`state["my_contracts"]`を追加（`for_player_id`が
当事者である`ACTIVE`契約の全容。義務ごとに`ob_status`
= fulfilled/expired/past/due/upcoming を付与）。`llm/prompt_builder.py`の
`_render_my_contracts_block()`がnegotiation/commit/reflection/double_upの
4プロンプトに「## あなたが当事者の正式契約（現在の状態・これが正本です）」を
毎ラウンド描画する。`my_obligations`（今R以降のTODO）とは役割を分離し、無変更で
残す。`contracts_public`（全員向け・条項なし）も無変更。`my_contracts`は
`contracts_pending`と同じ`for_player_id in c.parties`条件で当事者限定、
`for_player_id=None`（観戦用呼び出し）では`my_contracts`キー自体が生えない。

なお`engine/contracts.py`の`ContractStatus.COMPLETED`/`EXPIRED`はenumとして
定義されているが、代入箇所がコードベース全体でゼロ（`sign_contract()`が
`PROPOSED→ACTIVE`にするのみ）。`contracts_public`/`my_contracts`が「義務が全部消化済みでも
契約自体はactiveと表示される」のはこの仕様どおりであり、`ob_status`（義務単位）
を見て判断する必要がある。義務レベルの状態を持たないのは意図的な設計（契約自体の
COMPLETED/EXPIRED遷移を実装するとエンジン判定ロジック側の`status != ACTIVE`
continueに影響するため見送った）。

### `ACTIVE`からの唯一の実遷移: 全当事者合意による`CANCELLED`（§6・contract_cancel）

上記の「ACTIVEは変化しない」という前提には、**全当事者合意による解除**という
唯一の例外がある。同一R・同一市場に両立不能な条件（例: TWO_PAIR要求とONE_PAIR要求）の
契約が二重に成立すると、1ラウンドに出せるカードは1枚しかないためどちらかが必ず
型B違反で脱落する。これを「旧契約を全員合意で解除→新条件で`contract_propose`」の
2ステップで解消できるようにしたのが`contract_cancel`アクション（単一アクション、
提案/署名の2段構成にはしない。合意対象が`contract_id`だけで完全に決まるため）。

**可否判定は永続フラグを持たず、既存の義務フラグから毎回導出する**
（`engine/contracts.py:can_cancel_contract()`）:

```
解除可能 ⟺ contract.status == ACTIVE かつ、全 obligation について
    not ob.is_fulfilled          # 型A履行済みでない
  かつ not ob.is_expired          # 脱落による失効でない
  かつ ob.round_num >= 現在R      # 未来Rが期限（今Rが期限までは解除可）
```

型B義務は正常に守っても`is_fulfilled`が立たない（`audit_type_b()`は違反者だけを
返す仕様）ため、`ob.round_num < round_num`を「既に監査済み＝実績あり」の代理指標として
使う。これにより「一部当事者だけが履行/違反した後にこっそり解除して証拠を消す」ことを防ぐ。

生存する全当事者が同じ`contract_cancel`を出した時点で`CANCELLED`に遷移する
（`request_cancel()`）。1人でも出していなければ元の契約は`ACTIVE`のまま
（`cancel_requested_by`にPIDが積まれるだけ）。脱落者の同意は不要（行動できないため）。

`CANCELLED`は既存の全ての`contract.status != ContractStatus.ACTIVE`継続チェック
（`get_active_type_b_obligations`/`get_active_type_a_obligations`/
`get_all_type_b_for_player`/`my_contracts`/`my_obligations`）に自動的に乗るため、
Settlement/監査/自動代行Commitのロジックは一切変更していない。解除の事実（`cancelled_round`）は
契約ID・当事者・statusと同じ公開粒度で`contracts_public`/Viewerに残り続ける
（条項`terms`は従来どおりgod限定のまま）。

### `contract_cancel`はAUTO_PASSの起床条件から独立している（第3の起床トリガ）

`llm/llm_agent.py`の`negotiate()`は空回り削減のため`turn>=2`かつ新規`messages`が
無ければ自動pass（`AUTO_PASS_ON_NO_NEWS`）する設計だが、`contract_cancel`アクションは
`_round_messages`を一切生成しない（会話ではなく状態遷移のため）。そのため片側が解除を
打診しても、相手のプロンプトに変化が起きず`messages`件数の起床条件に一度も引っかからず、
永久にAUTO_PASSし続けて全会一致に到達できないという実装ミスがあった
（`scripts/contract_cancel_smoke.py --repro`で再現・回帰防止）。

`my_failed_actions`（不成立アクションのフィードバック）が既に同じ形の第2の起床トリガ
として存在していたのに倣い、`my_contract_notices`（自分が当事者の契約への解除打診・
解除成立の通知）の**件数増分**を第3の起床トリガとして追加した。新しい起床経路を
追加するたびに、それが`_round_messages`を経由しない状態変化である場合はAUTO_PASSの
対象外に落ちていないかをまず疑うこと（`messages`件数だけを信用しない）。

### 実APIスモークは allowlist + run-id + 多層capで守る

実LLM APIを叩くスモークテスト（`scripts/contract_cancel_smoke.py --real`ほか同系統の
ハーネス）は、コマンドライン誤操作1つで想定外の課金・想定外モデルへの送信が起き得る。
これを防ぐため以下を標準パターンとする:

- **`REAL_MODEL_ALLOWLIST`**（モジュール定数のset）: allowlist外の`--model`指定は
  実APIアダプタを作る**前**にexit 1で拒否する。allowlistへの追記自体をコードレビュー
  対象にすることで、「うっかり別モデルに実行が進む」をコード変更なしには不可能にする。
- **`--run-id`必須 + 出力先ディレクトリを`exist_ok=False`で作成**: 既存run-idの
  再実行（＝二重送信）を`FileExistsError`で機械的に弾く。
- **`HARD_MAX_CALLS`**: `--max-calls`自体の上限をコード側に固定し、引数だけでは
  超えられないようにする。
- **プリフライト必須通過**: 実APIへ進む前に既存のAPI-freeモード（`--repro`/
  `--dry-run`/`--mock`/`--scripted`等）を内部で自動実行し、1つでも失敗したら実APIを
  一切呼ばない。
- **実API前の`worst_case_cost`事前突合**: 実際に送信するプロンプトを本物のbuilder関数
  で構築した上で`worst_case_cost`を計算し、`--max-cost`/`--max-cost-per-call`と比較して
  超過なら送信前に中断する（見積だけでなく実プロンプトを使うことで、システムプロンプト
  肥大化等の実害を反映できる）。
- 実行後は`BudgetedAdapter`（アダプタ単位のcap）と`GameCostBudget(abort_on_block=True)`
  （ゲーム単位のcap、`llm/game_cost_budget.py`）の二重のランタイムcapが効く。
  `BudgetBlockedError`は`llm/llm_agent.py`の8箇所で捕捉済みのため、cap到達時も
  クラッシュせず`PassAction`等へ安全に退避する。

（Stage D-0/D-1の実測: L6での実行は実call4・$0.00137で完走し、上記いずれのcapにも
一度も抵触しなかった。詳細は`doc/devlog/2026-08-23_212143.md`・`2026-08-23_213502.md`）

## FINAL_REFLECTION（脱落者の一人称振り返り）は自由記述部分をgod限定にする

「本人の一人称コメントだから公開して良い」という直感は、FINAL_REFLECTIONには適用できない。
CoT reasoningやHandover Memoryが公開経路から締め出されているのは主に**構造的隔離**
（`_build_visible_state()`を一切経由しない）によるものだが、FINAL_REFLECTIONの`comment`/
`defeat_cause`はプロンプト上「敗因・信頼・裏切りを振り返って語ってほしい」と明示的に
一人称の物語として求めており、実測（`doc/trials/final_reflection_smoke2_2026-08-22.md`の
C1/C6、2/2件）で§8.2秘匿情報（契約条項の逐語的復唱、交渉相手の特定）を実際に含んでいた。
よってViewer側は`emotion`/`round`/`has_comment`/`truncated`/`availability`のみpublicに返し、
自由記述本体（`comment`/`defeat_cause`）と`status`/`salvaged`/`comment_chars`は`view=god`
限定とする二層設計を採用した（`viewer/log_parser.py`の`_safe_round_result()`）。
将来モデル自身に「公開してよい一言」を別フィールド（例: `last_word`）として書かせる案は
安全だが、これはEngine/promptスキーマの変更でありViewer側だけでは実現できない。
新しい自由記述系の可視化経路を追加する際は、「一人称だから安全」と決めつけず、実データで
§8.2該当語（契約ID、相手プレイヤーID、市場ID等）の混入有無を確認してから公開範囲を決めること。
担保テスト: `tests/test_viewer.py::TestFinalReflectionViewer::test_public_view_hides_secret_content`。

## POST_GAME_REFLECTION（ゲーム完全終了後の全員答え合わせ）はgod情報を積極的に含む唯一の例外

`_finalize()`後に全生存者・全脱落者へ一律発火する`POST_GAME_REFLECTION`は、FINAL_REFLECTIONとは
逆に**「他プレイヤーの内心・被匿名性・非当事者契約の種明かし」を意図的に読ませる**設計であり、
上記のgod隔離原則そのものの唯一の例外として設けている。次の7点を必ず守ること。

1. **god情報をプロンプトに積む権限を持つbuilderは`llm/prompt_builder.py`の
   `build_post_game_reflection_prompt()`一つだけ**である。他8つの`build_*`は従来どおり
   agent向け（god情報禁止）のままで、これを崩してはならない。担保テスト:
   `tests/test_dm_secrecy.py::TestBuilderAllowList::test_builder_allow_list_is_exact`。
2. POST_GAME_REFLECTIONの出力（`status`/`emotion`/`key_insight`/`self_assessment`/
   `biggest_revelation`/`best_player`/`most_deceptive_player`/`changed_opinion`/`comment`等）は
   **既存のpublic向けキーであっても全面god限定**とする。FINAL_REFLECTIONのような
   「一部フィールドだけpublic」の二層設計は適用しない。理由は本イベントの自由記述が
   構造的に他プレイヤーの秘密（DM本文・匿名の実発信者・非当事者向け契約条項）へ
   直接言及しうるため。
3. `POST_GAME_REFLECTION`は`_god_transcript`と同様の構造的隔離下で生成され、
   `_build_visible_state()`/`_visible_messages()`を一切経由しない。
4. POST_GAME_REFLECTIONの発火は**読み取り専用**であり、ゲーム状態・memory・契約・資産・
   勝敗結果を一切変更しない（`_finalize()`後に実行されるため、変更しても既に確定した
   結果へは反映されない設計）。
5. POST_GAME_REFLECTIONは正常完走パス（`_finalize()`成功後）でのみ発火し、
   JSONLログ上では**`GAME_END`より後**に出現する。ログ読み取り側（`viewer/log_parser.py`他）は
   `GAME_END`検出で読み取りを打ち切ってはならない（現状`break`は存在しない。
   `tests/test_l6_r12_trial.py`の`events[-1]=="GAME_END"`は"GAME_END以降にイベントが
   存在しない"ことの担保ではなく、単に`_finalize()`直前ログの末尾検証であることに注意）。
6. Viewer側は現サイクルでは`POST_GAME_REFLECTION`イベントを一切UIへ表示しない
   （`viewer/log_parser.py`のif/elifディスパッチが未対応の`event_type`を黙ってスキップする
   既存挙動に委ねている）。表示対応を追加する場合も、god限定の原則（上記2）を維持すること。
   担保テスト: `tests/test_viewer.py::TestPostGameReflectionViewer`。
7. モデル自身に「公開してよい一言」を書かせる`public_last_word`のような別フィールドは
   **本サイクルでは実装していない**。将来追加する場合もFINAL_REFLECTIONの節と同様、
   実データで§8.2該当語の混入有無を確認してから公開範囲を決めること。

## 不成立アクションのフィードバックは本人限定・engine判定不介入

`engine/actions.py`の`validate_action()`が不成立と判定したアクション（脱落者宛DM・送金・契約提案・
カードトレード等、資金不足、その他の全拒否理由）は、行動枠を消費する（§2.5、既存仕様のまま変更しない）
一方で、従来は失敗理由がイベントログにしか記録されず、行動主体のLLM自身へは一切フィードバックされて
いなかった。これにより脱落済みプレイヤー宛のDMを繰り返し送り続け、ラウンド内の行動枠を丸ごと浪費して
自滅する実害（`trial_C_l12_r12_20260822` R7-R9のP06、有効枠30中25枠を脱落済みP09宛DMで浪費し
R9破産）が実際に発生した。

修正として`engine/game.py`は`_action_failures: dict[player_id, list[dict]]`にラウンド単位で
不成立記録（`turn`/`action`/`reason`/`target`/`consumed_slot`、直近`_ACTION_FAILURE_MEMO_MAX=12`件）を
保持し、`_build_visible_state(for_player_id=...)`経由で**本人にのみ**`my_failed_actions`/
`my_action_budget`として公開する。この記録・公開経路には次の不変条件がある。

1. **engineの合否判定ロジック自体（`validate_action()`）は一切変更しない。** フィードバックは
   判定結果を「本人に伝える」だけであり、判定基準（生存チェック等）を緩めたり厳しくしたりしない。
   D1（`ContractProposeAction`/`CardTradeProposeAction`の脱落者宛先チェックの非対称性）は
   フィードバックとは独立した別修正である。
2. **`my_failed_actions`は`for_player_id`と完全一致するプレイヤーにのみ返す。** 他プレイヤーへの
   `_build_visible_state()`呼び出しでは絶対に混入しないこと（DM本文と同じ「本人限定」原則）。
   担保テスト: `tests/test_dm_secrecy.py::TestFullScanNoLeak::test_no_failed_action_record_leaks_across_players`。
3. **`llm/llm_agent.py`の`AUTO_PASS_ON_NO_NEWS`最適化は新規失敗があるときバイパスする。**
   （`has_new_failure = failure_count > self._last_failure_count`）。そうしないと「メッセージ増加なし」
   の条件だけで自動パスされ、失敗フィードバックがプロンプトに積まれてもLLMに気づかせる機会がないまま
   ラウンドが終わってしまう。
4. §2.5（不成立アクションも行動枠を消費する）自体は意図的なゲームルールであり、本修正でも変更しない。
   本修正はあくまで「なぜ失敗したか」を本人に見えるようにすることで、無意味な浪費を自己回避
   できるようにする情報提供に留まる。

担保テスト: `tests/test_dead_target_and_feedback.py`（`TestActionFailureRecord`・
`TestActionFailureVisibility`・`TestActionFeedbackPrompt`・`TestDmLoopScenario`）。

## Viewer のメッセージ表示は LLMログ（本文）と Engineイベント（成否）の join を必ず経由する

`viewer/log_parser.py`の`_collect_round_messages()`はLLMログ（`{game_id}_{pid}_llm_calls.jsonl`）
から交渉メッセージの本文・宛先を復元するが、**そのメッセージが実際にengineへ受理されたか
（不成立で行動枠だけ消費されたか）は、LLMログ単体には含まれない**。LLMが送信を試みた
という事実だけを表示し、成否を確認せずに通常メッセージと同列表示すると、脱落済みプレイヤー宛の
不成立DMが「送れた」ように見えるビューワー側の誤表示を生む（今回の元バグの表示面。
`trial_C_l12_r12_20260822`のP06→P09 R7-R9、実測25件全て不成立だが修正前は通常DMと同じ見た目だった）。

修正として、Engineイベント（`{game_id}_events.jsonl`の`NEGOTIATION_ACTION`、`data.success`/
`data.reason`を持つ）を`(player_id, round_num, turn)`で索引化する`_index_negotiation_outcomes()`と、
LLMログ1件をこの索引と突き合わせる`_match_delivery()`を経由し、各メッセージへ`delivery`
（`"delivered"`/`"rejected"`/`"unverified"`の3値）と、rejected時のみ`reject_reason`を付与する。

1. **`"unverified"`（対応イベントが見つからない＝未検証）を`"rejected"`（明示的に不成立と判定
   された）と絶対に混同表示しないこと。** これを混同すると「不明」を「不成立」と決めつける、
   今回の元バグと同種の誤情報を新たに作り出すことになる。
2. **`reject_reason`は宛先PIDを直接含みうる**（例: `"Target P09 is not alive"`）ため、
   `view=="god"`のときのみ付与する。`view=="public"`のDM表示（本文・宛先が非公開のプレースホルダ）
   には`delivery`/`reject_reason`のいずれも含めない（§8.2のDM秘匿境界の一部として扱う）。
3. **後方互換は「`delivery`フィールドを一切付与しない」ことで担保する。** `NEGOTIATION_ACTION`に
   `turn`を含まない旧形式のログ・イベント（`_collect_round_messages(trial_dir, game_id)`の2引数
   呼び出しを含む）では`events`が渡されない、または索引が空になるため、機械的に`"unverified"`を
   埋めたりしない。「わからない」を明示的な値で埋めることは、それ自体が誤情報の温床になる。
4. `get_player_round_detail()`の`secret_events.round_totals`には`messages_rejected`
   （そのラウンドの秘匿DM全体の不成立件数）と`messages_rejected_shown`（本人が関与し表示された
   分のうちの不成立件数）を追加する。この2値も同じjoin結果に依存するため、`delivery`を持たない
   旧ログでは両方0になる（誤って全件成立扱いにはしない）。

担保テスト: `tests/test_viewer.py::TestNegotiationDeliveryJoin`（純関数の単体テスト、
`_collect_round_messages`の後方互換テスト、`get_round_states`/`get_player_round_detail`経由の
end-to-endテスト、public view非漏洩テストを含む）。実データ照合:
`trial_C_l12_r12_20260822`でdelivered:653/rejected:25/unverified:4、P06→P09のR7-R9全25件が
`rejected`かつ`reject_reason`一致を確認済み。

## 匿名性を破らずに送信者本人だけへ事実を返す: index-keyed private map パターン

`anonymous_broadcast`は他プレイヤーに対しては`sender: None`で正しく匿名だが、送信者本人にとっても
同じ形で返ってしまうため、本人が次ラウンドのreflection prompt（＝Handover Memory生成入力）で自分の
匿名発言を他人のものと誤認する余地があった（Cycle 4）。`_god_transcript`（`actual_sender`保持）は
`_visible_messages()`/`_build_visible_state()`から一切参照しない絶対条件があるため、`engine/game.py`に
`_god_transcript`とは独立した「本人限定の私的情報」を追加する必要があった。

採用したのは`_round_messages`のindexをキーにした専用dict（`_anon_broadcast_owners: dict[int, str]`）
を持ち、`_visible_messages()`が本人向けコピーにのみ真偽フラグ（`is_mine`）を付与するパターン。
本文一致ではなくindexで判定するため、2人が同一本文の匿名発言をしても取り違えない。フラグは
`redacted`と同じ「真のときだけキーを付ける」慣習に従い、他プレイヤー・`for_player_id=None`
（Bot/内部）向けコピーにはキー自体が存在しない（`False`すら出さない）。

**このパターンを別の私的情報に転用する場合、必ず守ること**:

1. index-keyed private mapは、キーの元になるリスト（本件は`_round_messages`）と**必ず同一ライフサイクル**
   で管理する。リストがラウンド境界で`[]`に戻りindexが0から再利用される設計である以上、mapを一緒に
   クリアし忘れると、新ラウンドの別プレイヤーの発言へ前ラウンドの所有者情報が誤適用される
   （匿名性破壊＋AI自己認識の誤認識という複合的なstate corruption）。
2. クリアは**単一の関数に集約**する（本件は`_reset_round_message_state()`）。リストとmapを別々の箇所で
   個別にクリアするコードを書かないこと。ソーススキャンテスト（`_round_messages`/対応するmapへの
   再代入が`__init__`とそのリセット関数以外に存在しないことを`inspect.getsource`等で機械確認）で
   固定するのが望ましい（`tests/test_anon_self_marker.py::test_round_message_state_reset_is_single_sourced`）。
3. private mapは`_god_transcript`にもリスト本体（本件`_round_messages`）にも積まない。Prompt層
   （`llm/prompt_builder.py`）に渡す直前のvisible_state加工でのみ参照する。

## thinking有効時はAnthropic/Moonshotのtemperatureを1.0に強制する（provider allowlist）

Cycle 6の監査スモークで、thinkingを有効化した状態でAnthropic/Moonshot系モデルへ
`temperature`を1以外の値で送るとHTTP 400になることを実測確認した
（Anthropic: "temperature may only be set to 1 when thinking is enabled or in adaptive ..."、
Moonshot: "invalid temperature: only 1 is allowed for this model"）。Cycle 6.1で
`llm/adapters.py`に対処を実装した。

- `THINKING_TEMPERATURE_LOCKED_PROVIDERS = ("Anthropic", "Moonshot")`と
  `THINKING_LOCKED_TEMPERATURE = 1.0`は、**HTTP 400本文で実測確認したproviderだけ**を
  列挙する。xAI/DeepSeek/Google/OpenAIは対象外（未実測のまま挙動を変えない）。
- `_thinking_enabled_payload(payload)`は、リクエストに実際に「thinkingを有効化する」指示が
  入っているか（`{"thinking": {"type": "disabled"}}`ではないか）を判定する。registry既定の
  extra_paramsはdisabledのため、`request_options`（実験・検証専用経路）でthinkingを明示的に
  有効化したときだけ`temperature=1.0`強制が発動する。
- **本走（`llm/llm_agent.py`）は`request_options`を一切渡さない**ため、この分岐には入らず
  L1〜L6を含む全登録モデルの送信内容は不変。この非回帰は`tests/test_thinking_temperature.py`
  の`TestCoreRosterRequestSnapshotUnchanged`で、実際に送信されるkwargs dictの完全一致として
  固定している。
- 新しいproviderでも同種の400を実測したら、`THINKING_TEMPERATURE_LOCKED_PROVIDERS`へ追記する
  （未実測のまま推測で追加しないこと）。

## `hidden_thinking_reserve_tokens`は複数回実測の最大値へ安全マージンを掛けて設定する

上記「LLMコストは常に`_usage_cost(model, usage)`経由で算出する」節で導入した
`hidden_thinking_reserve_tokens`（xAI/Gemini 6モデル対象）は、Cycle 6.1でH4
（`grok-4.6`: 1536→9728）・M4（`grok-4.5`: 1024→2560）を実測ベースで引き上げた。

- Cycle 6の単発実測（H4 reasoning_tokens=6756）だけでなく、Cycle 6.1の検証スモークで
  再実測した値（H4=7746、M4=2035）も含め、**複数回の実測のうち最大値**を基準にする。
  単発実測はconditionのばらつき（`thinking`パラメータ、`max_output_tokens`）で再現しない
  ことがあるため（`hidden_thinking_reserve_tokens=1536`は元々thinkingが浅い条件での観測値で、
  `thinking medium`・`max_output_tokens=3000`条件では大きく下回っていた）。
- 安全マージンは`ceil(max_measured_reasoning_tokens * 1.2)`を512単位へ切り上げる方式に統一した
  （H4: ceil(7746*1.2)=9296→9728、M4: ceil(2035*1.2)=2442→2560）。1.2倍は経験的マージンであり
  provider保証の数学的worst-case上限ではない点は既存節の注意書きのまま変わらない。
- reserve値を引き上げると`scripts/model_matrix.py`の`_phase2_worst_case_cost()`が計算する
  CORE_18予約合計が増えるため、`PHASE_DEFAULTS[2]["max_cost"]`（Phase 2の全体上限）が不足する
  場合がある。Cycle 6.1では0.25→0.40へ人間判断で引き上げた（headroom 6.8%）。reserve値と
  Phase 2上限は連動して見直す必要があることに留意する。担保テスト:
  `tests/test_registry_18.py`（`test_h4_hidden_reserve_covers_measured_reasoning`・
  `test_m4_hidden_reserve_covers_measured_reasoning`）、`tests/test_model_matrix.py`
  （`test_xai_reserves_cover_phase2_observed_reasoning_without_claiming_a_hard_cap`）。

## 倍掛け単独市場除外は判定ゲートとログ記録を分離する（`outcome_reason`/`forfeited_by_solo_only`）

`engine/game.py`の倍掛け（double-up）判定ゲート（`non_solo_winners[pid] > 0`で単独市場のみの
勝利を除外する）自体は当初から正しく機能していたが、旧`from_solo_market`フィールドは
**成功分岐の内側にのみ**記録される設計だったため、その分岐に入る時点で既に「非単独市場での
勝利が確定」しており、同フィールドがTrueになることは代数的に起こり得ない到達不能コードだった
（forfeit分岐にはフィールド自体が存在しなかった）。

Cycle 8で`forfeited_by_solo_only`（forfeit分岐で設定、到達可能）へ改称し、成功・forfeit・
未勝利の3分岐すべてに`outcome_reason`（`non_solo_win`/`solo_only_win`/`no_win`/`eliminated`）と
勝敗内訳（solo_wins/non_solo_wins）を返す純関数`_summarize_market_wins()`を新設した。
**判定ゲート自体（賞金・没収の実額）は一切変更していない。** ログ・観測経路を新設・変更する際は、
「判定ロジックが正しく機能している」ことと「その判定結果がログから観測可能である」ことを
別々に検証すること（本件は前者は最初から正しく、後者だけが欠陥だった）。担保テスト:
`tests/test_cycle8_double_up_logging.py`。実データ検証: `trial_C_l12_r12_20260828`の
P03 R9・P11 R12が`solo_only_win`として識別可能になったことを確認済み（`doc/devlog/2026-08-28_213206.md`）。

## API非呼び出しpassは`PassAction.source`でトレーサビリティを持たせる

`llm/llm_agent.py`の`negotiate()`にはAPIを呼ばずに`PassAction`を返す経路が複数ある
（`cost_exceeded`/`auto_no_news`（`AUTO_PASS_ON_NO_NEWS`）/`budget_blocked`/`parse_failed`等）。
これらはLLMコールログ（`llm_calls.jsonl`）に対応するレコードを残さないため、pass件数と
LLMコール件数の突合だけでは原因が判別できない（`trial_C_l12_r12_20260828`のpass 439件中11件が
該当し、分析2/2の時点では「判別不能」としていた）。

Cycle 8で`PassAction.source`（既定`"llm"`、後方互換）を追加し、`engine/game.py`の
`NEGOTIATION_ACTION`ログへ`getattr(action, "source", "llm")`を含めるようにした。新しい
API非呼び出し経路を追加する場合は、必ず対応する`source`値を設定すること。
`llm_calls.jsonl`（総コール数・有効JSON率の集計元）へ合成レコードを追加する対処は
`scripts/llm_trial.py`のレポート集計を歪めるため採用しない。担保テスト:
`tests/test_cycle8_pass_source.py`。

## `strategy.reason_category`は自由記述`reason`を置き換えず併記する

pass等の`strategy.reason`が自由記述のみだと、分析のたびにキーワードマッチに頼らざるを
得ずコストが増大する（分析2/2実測: pass理由の71.8%がキーワード分類不能）。Cycle 8で
`llm/response_parser.py`に`normalize_reason_category()`を追加し、既存`normalize_emotion()`と
同型（列挙外・欠落は`None`、`ParseError`にしない＝リトライを誘発しない）で
`strategy.reason_category`を導入した。選択肢は対称8種（情報収集・様子見／戦略的沈黙／
交渉継続待ち／資金・カード制約による断念／その他 等）で、Cycle 5の対称性原則
（特定の選択肢が有利/不利に見える書き方をしない）に従う。

配置場所は`system_prompt`ではなく交渉プロンプト（`llm/prompt_builder.py`の`build_negotiation_prompt`）
側。理由は`system_prompt`が7079/7100字で残余21字しかなく（`tests/test_cycle5_prompt_salience.py`が
機械保証する上限）、reason_categoryの列挙提示（+112字）を追加する余地がないため。新しい
strategy系フィールドをsystem_prompt側に追加したくなった場合は、まず既存の文字数上限テストで
残余を確認すること。担保テスト: `tests/test_cycle8_reason_category.py`。
