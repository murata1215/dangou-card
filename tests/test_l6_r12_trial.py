"""L1〜L6 R12実戦試験のAPI不要な安全設定テスト。"""

import json

import pytest

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.market import generate_markets
from engine.negotiation import StubAgent
from llm.game_cost_budget import GameCostBudget
from llm.costing import usage_cost, worst_case_cost
from llm.models import MODEL_REGISTRY
from llm.adapters import AdapterError
from scripts.model_matrix import BudgetedAdapter
from scripts.l6_r12_report import generate_report
from scripts.llm_trial import build_trial_manifest, effective_trial_model


def test_l6_runtime_output_override_is_non_destructive_and_auditable():
    """2000-token統一がregistryを変えず、manifestへ残ることを確認する。"""
    roster = [f"L{i}" for i in range(1, 7)]
    original_max_tokens = {key: MODEL_REGISTRY[key].max_tokens for key in roster}
    config = GameConfig.baseline_v1_s2(num_players=6)

    manifest = build_trial_manifest(
        roster, config, seed=1201, max_output_tokens=2000,
        abort_on_budget_block=True,
    )

    assert all(model["effective_max_output_tokens"] == 2000 for model in manifest["models"])
    assert {key: MODEL_REGISTRY[key].max_tokens for key in roster} == original_max_tokens
    assert effective_trial_model("L1", 2000) is not MODEL_REGISTRY["L1"]
    assert effective_trial_model("L1", 2000).max_tokens == 2000
    assert manifest["enable_cot"] is False
    assert manifest["per_player_game_cost_cap_usd"] == 5.0
    assert manifest["game_cost_cap_usd"] == 40.0


def test_manifest_records_stop_after_round_without_changing_s2_total_rounds():
    config = GameConfig.baseline_v1_s2(num_players=6)
    manifest = build_trial_manifest(
        [f"L{i}" for i in range(1, 7)], config, 1201, 2000, True,
        stop_after_round=3,
    )

    assert manifest["num_rounds"] == 12
    assert manifest["stop_after_round"] == 3


def test_s2_six_player_prizes_record_normal_and_final_market_multiplier():
    """6席baseline_v1_s2の通常RとR12の基本賞金倍率を固定する。"""
    config = GameConfig.baseline_v1_s2(num_players=6)

    normal_markets = generate_markets(1, config, {})
    final_markets = generate_markets(12, config, {})

    assert sum(m.base_prize for m in normal_markets) == 960_000
    assert [m.base_prize for m in normal_markets] == [320_000, 320_000, 320_000]
    assert sum(m.base_prize for m in final_markets) == 2_880_000
    assert [m.base_prize for m in final_markets] == [960_000, 960_000, 960_000]


def test_strict_budget_block_stops_game_and_preserves_game_end_event():
    """厳格試験は最初の予約block後に後続フェイズを実行しない。"""
    class LoanOnlyAgent:
        """APIを使わず、Gameのsetup境界だけを通すテスト用agent。"""

        def set_game_cost_budget(self, budget):
            self.budget = budget

        def choose_loan(self, config):
            return config.loan_min

    logger = EventLogger(fixed_timestamp="t")
    budget = GameCostBudget(5.0, 40.0, logger, abort_on_block=True)
    # Game.run開始前に発生済みのblockを模擬し、APIを追加送信せず終了する境界を検証する。
    budget.blocks.append({"reason": "per_player_cap", "player_id": "P01"})
    game = Game(GameConfig.baseline_v1_s2(num_players=1), {"P01": LoanOnlyAgent()},
                logger=logger, cost_budget=budget)

    result = game.run()

    assert result.round_count == 0
    event_types = [event.event_type for event in logger.events]
    assert event_types == ["GAME_START", "LOAN_CHOSEN", "GAME_ABORTED", "GAME_END"]
    game_end = logger.events[-1]
    assert game_end.data["completed"] is False
    assert game_end.data["abort_reason"] == "llm_budget_blocked"


def test_stop_after_round_keeps_s2_r12_semantics_and_reflects_before_stopping():
    """R3は通常RのままReflection後に止まり、R4を開始しない。"""
    class RecordingAgent(StubAgent):
        def __init__(self):
            self.reflections: list[int] = []

        def reflect(self, player_state, round_num, visible_state):
            self.reflections.append(round_num)

    config = GameConfig.baseline_v1_s2(num_players=1)
    agent = RecordingAgent()
    logger = EventLogger(fixed_timestamp="t")
    result = Game(config, {"P01": agent}, logger=logger, stop_after_round=3).run()

    assert config.num_rounds == 12
    assert result.round_count == 3
    assert agent.reflections == [1, 2, 3]
    events = logger.events
    assert [e.round_num for e in events if e.event_type == "MARKET_OPEN"] == [1, 2, 3]
    assert [e.round_num for e in events if e.event_type == "ROUND_COMPLETE"] == [1, 2, 3]
    stopped = next(e for e in events if e.event_type == "GAME_STOPPED")
    assert stopped.data == {
        "reason": "trial_stop_after_round",
        "stop_after_round": 3,
        "completed_round": 3,
    }
    assert events[-1].event_type == "GAME_END"
    assert events[-1].data["completed"] is False
    assert events[-1].data["stop_reason"] == "trial_stop_after_round"


def test_deepseek_v4_flash_pricing_is_consistent_for_reservation_and_usage():
    """L6はcache missで予約し、cache hitを実績時だけ割り引く。"""
    model = MODEL_REGISTRY["L6"]
    assert (model.cached_input_price, model.input_price, model.output_price) == (0.0028, 0.14, 0.28)
    assert (
        MODEL_REGISTRY["M6"].cached_input_price,
        MODEL_REGISTRY["M6"].input_price,
        MODEL_REGISTRY["M6"].output_price,
    ) == (0.0028, 0.14, 0.28)

    reservation = worst_case_cost(model, "system", "user", 2000)
    expected_reservation = ((len("system") + len("user")) // 2 + 50) * 0.14 / 1_000_000 + 2000 * 0.28 / 1_000_000
    assert reservation == pytest.approx(expected_reservation)

    actual = usage_cost(model, {
        "input_tokens": 1000,
        "cache_read_input_tokens": 800,
        "output_tokens": 100,
        "total_tokens": 1100,
    })
    assert actual == pytest.approx((200 * 0.14 + 800 * 0.0028 + 100 * 0.28) / 1_000_000)

    class MustNotCall:
        def complete(self, *args, **kwargs):
            raise AssertionError("予約超過ならadapterへ送信してはいけない")

    adapter = BudgetedAdapter(
        MustNotCall(), model, max_calls=1, max_cost=reservation - 0.000000001,
        max_tokens_cap=2000,
    )
    with pytest.raises(AdapterError, match="予算上限"):
        adapter.complete("system", [{"role": "user", "content": "user"}], max_tokens=2000)


def test_after_report_reads_manifest_events_and_budget_abort(tmp_path):
    """AFTERレポートが逐次保存済みログだけで中断を再構成できることを確認する。"""
    log_dir = tmp_path / "trial_C"
    llm_dir = log_dir / "llm_logs"
    llm_dir.mkdir(parents=True)
    config = GameConfig.baseline_v1_s2(num_players=6)
    manifest = build_trial_manifest(
        [f"L{i}" for i in range(1, 7)], config, 1201, 2000, True,
    )
    (log_dir / "trial_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (log_dir / "game01_seat_map.json").write_text(
        json.dumps({"P01": "L1:Claude Haiku 4.5"}), encoding="utf-8",
    )
    events = [
        {"event_type": "GAME_ABORTED", "round_num": 2, "data": {"reason": "llm_budget_blocked"}},
        {"event_type": "GAME_END", "round_num": 2, "data": {"survivors": [], "eliminated": []}},
    ]
    (log_dir / "game01_events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n", encoding="utf-8",
    )
    (llm_dir / "game01_P01_llm_calls.jsonl").write_text(
        json.dumps({
            "api_called": True, "input_tokens": 10, "output_tokens": 5,
            "total_tokens": 18, "cost_usd": 0.01, "response_text": "",
            "error": "timeout", "error_type": "timeout", "retry_count": 1,
        }) + "\n", encoding="utf-8",
    )

    output = tmp_path / "after.md"
    generate_report(log_dir, output)
    report = output.read_text(encoding="utf-8")

    assert "いいえ（中断）" in report
    assert "10 | 0 | 5 | 3" in report
    assert "1 / 1 / 1" in report
