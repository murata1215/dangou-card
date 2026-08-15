# プロジェクト固有ルール

## devlog追記ルール

タスク(ビルド)完了時、`doc/devlog.md` の末尾に当該タスクのサイクルエントリを1節追記すること。形式: 日付見出し(同日なら既存日付の下に節追加)+「### サイクルX.X: タスク名」+ 要求/実行/検証/発見を平文3〜10行で。技術的明細はchangelogに書き、devlogには**何を狙い・何が起き・何を発見したか**の物語だけを書く。数値(テスト数・コスト・生還率等)は具体値で記す。

## S2設定の単一ソース化ルール

Season 2 (S2) のゲーム設定は `engine/config.py` の `GameConfig.default_8_s2()` / `baseline_v1_s2()` プリセットを**唯一の正**とする。スクリプト側（`scripts/*.py`）で個別に `GameConfig(...)` を組み立てて S2 パラメータを再現するのは禁止。過去にプリセット更新が各スクリプトへ伝播せず、シミュレーション結果が古い設定のまま出続けるバグを起こした（`doc/simulate_s2_stale_investigation.md`）。新規スクリプトで S2 設定が必要な場合は必ずプリセット関数を呼び出すこと。

## 契約detailsキーの正規化ルール

型B義務（特にtype_b_card）のdetailsキーは `create_contract()` の入口で必ず正規化する。LLMが `"card"`/`"rank"` 等の同義語で送っても `"card_rank"` に統一し、無効なrank名は `ValueError` で契約提案自体を拒否する。判定ロジック（`audit_type_b`）・自動代行Commit・ビューワー表示・契約義務可視化ブロックの4消費者全てが `card_rank` キーを参照する設計のため、入口1箇所での正規化が全消費者に効く。過去にキー不一致（`"card"` vs `"card_rank"`）により、正しくカードを提出したプレイヤーが常に契約違反判定される重大バグが発生した（R7全滅の主因、`doc/changelog.md` 2026-08-15参照）。

## EventLogger逐次追記パターン

`engine/events.py` の `EventLogger` は `output_path` 指定時のみ `log()` 呼び出しごとに即時追記+flushする（`llm/llm_logger.py` の `LLMLogger` と同パターン）。`output_path` 未指定時は従来通りメモリ保持のみでディスクI/Oは発生しない（`simulate.py` 等の高速シミュレーション経路に副作用なし）。進行中ゲームをビューワーで観戦させたい場合は `EventLogger(output_path=...)` を明示すること。
