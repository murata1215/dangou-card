# プロジェクト固有ルール

## devlog追記ルール

タスク(ビルド)完了時、`doc/devlog.md` の末尾に当該タスクのサイクルエントリを1節追記すること。形式: 日付見出し(同日なら既存日付の下に節追加)+「### サイクルX.X: タスク名」+ 要求/実行/検証/発見を平文3〜10行で。技術的明細はchangelogに書き、devlogには**何を狙い・何が起き・何を発見したか**の物語だけを書く。数値(テスト数・コスト・生還率等)は具体値で記す。

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
