"""
Cycle 1で修正されたPhase Cレポート生成ロジック（scripts/llm_trial.py）の
未了受け入れテスト（Cycle 2実装時に併せて消化）。

対象の実装済み修正（コード中のコメントラベル）:
- C-1: `_roster_summary()` — 座席マップの実データから「N社Mモデル・LLM×P」を
  動的に導出する（旧: "6社8モデル・LLM×8"固定文字列で12体戦を8体戦と誤認）。
- C-2: `regenerate_phase_c_report()` が `trial_manifest.json` から
  run_id/seed/予定ラウンド数を読む（無ければ従来通りNone/推定にフォールバック）。
- C-3: AUTO_COMMIT_FAILURE（合法commitゼロで脱落）をAUTO列と別に
  FAILURE列として集計する。

実APIは一切呼ばない。ファイルI/O（JSONL/JSON）のみで完結する。
"""

import json

import pytest

from engine.config import GameConfig
from scripts.llm_trial import (
    _roster_summary,
    generate_phase_c_report,
    regenerate_phase_c_report,
)


# ---------------------------------------------------------------------------
# T-R1〜T-R3: _roster_summary（純粋関数の単体テスト）
# ---------------------------------------------------------------------------

class TestRosterSummary:
    def test_t_r1_multi_provider_seat_map(self):
        """T-R1: 2社4モデル・4座席のseat_mapから正しい社数・モデル数・人数を導出する"""
        seat_map = {
            "P01": "M1:Claude Sonnet 5",
            "P02": "M2:GPT-4.1",
            "P03": "L1:Claude Haiku 4.5",
            "P04": "L2:GPT-4.1 Mini",
        }
        summary = _roster_summary(seat_map)
        assert "2社" in summary
        assert "4モデル" in summary
        assert "LLM×4" in summary
        assert "Random 0体" in summary

    def test_t_r2_twelve_seats_not_mislabeled_as_eight(self):
        """
        T-R2: 12座席のseat_mapが「LLM×8」に誤表示されないこと
        （実運用で発生したC-1バグの直接再現。旧実装は固定文字列だったため
        12体戦でも常に「LLM×8」と表示されていた）
        """
        roster_keys = ["M1", "M2", "L1", "L2", "M3", "M4"]
        seat_map = {
            f"P{i+1:02d}": f"{roster_keys[i % len(roster_keys)]}:dummy"
            for i in range(12)
        }
        summary = _roster_summary(seat_map)
        assert "LLM×12" in summary
        assert "LLM×8" not in summary

    def test_t_r3_unknown_model_key_does_not_crash_or_count_as_provider(self):
        """T-R3: MODEL_REGISTRYに無いmodel_keyでも例外にならず、社数に加算されない"""
        seat_map = {
            "P01": "M1:Claude Sonnet 5",
            "P02": "ZZZ_UNKNOWN:謎のモデル",
        }
        summary = _roster_summary(seat_map)
        assert "2モデル" in summary
        assert "LLM×2" in summary
        # 未知キーは provider を持たないため 1社のみ
        assert "1社" in summary


# ---------------------------------------------------------------------------
# T-R4: generate_phase_c_report の空結果フォールバック
# ---------------------------------------------------------------------------

class TestGeneratePhaseCReportEmptyResults:
    def test_t_r4_empty_results_falls_back_without_crash(self, tmp_path):
        """T-R4: results=[] でも例外にならず「(データなし)」で構成行が出る"""
        config = GameConfig.baseline_v1(num_players=8)
        output_path = tmp_path / "trial_C_report.md"
        generate_phase_c_report([], config, elapsed=12.3, output_path=output_path)
        text = output_path.read_text(encoding="utf-8")
        assert "(データなし)" in text
        assert "試合数: 0" in text


# ---------------------------------------------------------------------------
# T-R5〜T-R8: regenerate_phase_c_report（ファイルベースの統合テスト）
# ---------------------------------------------------------------------------

def _write_common_log_files(
    log_dir,
    *,
    seat_map: dict[str, str],
    events: list[dict],
    manifest: dict | None = None,
):
    """テスト用のログディレクトリ構成を書き出す共通ヘルパー"""
    llm_logs_dir = log_dir / "llm_logs"
    llm_logs_dir.mkdir(parents=True, exist_ok=True)

    (log_dir / "game01_seat_map.json").write_text(
        json.dumps(seat_map, ensure_ascii=False), encoding="utf-8",
    )
    with open(log_dir / "game01_events.jsonl", "w", encoding="utf-8") as f:
        for e in events:
            f.write(json.dumps(e, ensure_ascii=False) + "\n")
    if manifest is not None:
        (log_dir / "trial_manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8",
        )
    # 各プレイヤーのLLMコールログ（空でも良いが、ファイルは存在させる）
    for pid in seat_map:
        call_path = llm_logs_dir / f"game01_{pid}_llm_calls.jsonl"
        with open(call_path, "w", encoding="utf-8") as f:
            f.write(json.dumps({
                "round_num": 1, "turn": 1, "cost_usd": 0.001,
                "elapsed_ms": 500, "response_text": '{"strategy": {}}',
            }, ensure_ascii=False) + "\n")


class TestRegeneratePhaseCReportManifest:
    def test_t_r5_manifest_present_shows_run_id_and_seed(self, tmp_path):
        """T-R5: trial_manifest.jsonが存在する場合、run_id/seedがレポートに出る"""
        seat_map = {"P01": "M1:Claude Sonnet 5", "P02": "L1:Claude Haiku 4.5"}
        events = [
            {"event_type": "GAME_END", "round_num": 12,
             "data": {"survivors": [], "eliminated": []}},
        ]
        manifest = {"run_id": "trial_C_test_20260824", "seed": 301, "num_rounds": 12}
        _write_common_log_files(tmp_path, seat_map=seat_map, events=events, manifest=manifest)

        regenerate_phase_c_report(tmp_path)
        text = (tmp_path / "trial_C_report.md").read_text(encoding="utf-8")
        assert "trial_C_test_20260824" in text
        assert "seed: 301" in text

    def test_t_r6_manifest_absent_omits_run_id_line(self, tmp_path):
        """T-R6: trial_manifest.jsonが無い場合、run_id行は出ずクラッシュもしない"""
        seat_map = {"P01": "M1:Claude Sonnet 5"}
        events = [
            {"event_type": "GAME_END", "round_num": 12,
             "data": {"survivors": [], "eliminated": []}},
        ]
        _write_common_log_files(tmp_path, seat_map=seat_map, events=events, manifest=None)

        regenerate_phase_c_report(tmp_path)
        text = (tmp_path / "trial_C_report.md").read_text(encoding="utf-8")
        assert "run_id:" not in text


class TestRegeneratePhaseCReportRoundsProgress:
    def test_t_r7_incomplete_run_shows_stopped_suffix(self, tmp_path):
        """T-R7: 予定12R・完走6R → 「（途中停止）」が付くこと"""
        seat_map = {"P01": "M1:Claude Sonnet 5"}
        events = [
            {"event_type": "ROUND_COMPLETE", "round_num": r, "data": {}}
            for r in range(1, 7)
        ] + [
            {"event_type": "GAME_END", "round_num": 6,
             "data": {"survivors": [], "eliminated": []}},
        ]
        manifest = {"run_id": "trial_partial", "seed": 1, "num_rounds": 12}
        _write_common_log_files(tmp_path, seat_map=seat_map, events=events, manifest=manifest)

        regenerate_phase_c_report(tmp_path)
        text = (tmp_path / "trial_C_report.md").read_text(encoding="utf-8")
        assert "予定12R / 完走6R（途中停止）" in text

    def test_t_r7b_complete_run_shows_no_stopped_suffix(self, tmp_path):
        """完走12R/予定12Rでは「（途中停止）」が付かないこと（対比）"""
        seat_map = {"P01": "M1:Claude Sonnet 5"}
        events = [
            {"event_type": "ROUND_COMPLETE", "round_num": r, "data": {}}
            for r in range(1, 13)
        ] + [
            {"event_type": "GAME_END", "round_num": 12,
             "data": {"survivors": [], "eliminated": []}},
        ]
        manifest = {"run_id": "trial_full", "seed": 1, "num_rounds": 12}
        _write_common_log_files(tmp_path, seat_map=seat_map, events=events, manifest=manifest)

        regenerate_phase_c_report(tmp_path)
        text = (tmp_path / "trial_C_report.md").read_text(encoding="utf-8")
        assert "予定12R / 完走12R（途中停止）" not in text
        assert "予定12R / 完走12R" in text


class TestRegeneratePhaseCReportAutoFailureColumn:
    def test_t_r8_auto_commit_failure_counted_separately_from_auto_commit(self, tmp_path):
        """
        T-R8: AUTO_COMMIT_FAILURE（合法commitゼロで脱落）がAUTO列ではなく
        FAILURE列に集計されること（AUTO列に混入しないこと）
        """
        seat_map = {"P01": "M1:Claude Sonnet 5", "P02": "L1:Claude Haiku 4.5"}
        events = [
            {"event_type": "AUTO_COMMIT", "round_num": 3,
             "data": {"player_id": "P02"}},
            {"event_type": "AUTO_COMMIT_FAILURE", "round_num": 6,
             "data": {"player_id": "P01", "reason": "contract_violation"}},
            {"event_type": "GAME_END", "round_num": 12,
             "data": {"survivors": [], "eliminated": []}},
        ]
        manifest = {"run_id": "trial_af", "seed": 1, "num_rounds": 12}
        _write_common_log_files(tmp_path, seat_map=seat_map, events=events, manifest=manifest)

        regenerate_phase_c_report(tmp_path)
        text = (tmp_path / "trial_C_report.md").read_text(encoding="utf-8")

        # モデル別集計テーブルのヘッダにFAILURE列があること
        assert "| 座席 | モデル | JSON率 | AUTO | FAILURE | エラー |" in text

        # 「座席⇔モデル対応表」にも "| P01 |" 始まりの行があるため、
        # モデル別集計セクション以降だけを対象に検索する
        model_section = text.split("## モデル別集計")[1]
        p01_line = next(l for l in model_section.splitlines() if l.startswith("| P01 |"))
        p02_line = next(l for l in model_section.splitlines() if l.startswith("| P02 |"))
        p01_cols = [c.strip() for c in p01_line.strip("|").split("|")]
        p02_cols = [c.strip() for c in p02_line.strip("|").split("|")]
        # 列順: 座席,モデル,JSON率,AUTO,FAILURE,エラー,...
        assert p01_cols[3] == "0"  # AUTO
        assert p01_cols[4] == "1"  # FAILURE
        assert p02_cols[3] == "1"  # AUTO
        assert p02_cols[4] == "0"  # FAILURE
