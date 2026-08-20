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
