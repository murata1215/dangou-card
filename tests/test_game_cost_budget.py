"""本戦専用GameCostBudgetのAPI不要テスト。"""

import pytest

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from llm.prompt_builder import build_loan_prompt, build_system_prompt
from scripts.model_matrix import build_phase3_config
from llm.costing import usage_cost, worst_case_cost
from llm.game_cost_budget import BudgetBlockedError, GameCostBudget
from llm.models import MODEL_REGISTRY


def test_game_config_cost_cap_defaults_and_override():
    config = GameConfig()
    assert config.per_player_game_cost_cap_usd == 5.0
    assert config.game_cost_cap_usd == 40.0
    changed = config.model_copy(update={
        "per_player_game_cost_cap_usd": 10.0,
        "game_cost_cap_usd": 60.0,
    })
    assert changed.model_dump()["per_player_game_cost_cap_usd"] == 10.0
    assert changed.model_dump()["game_cost_cap_usd"] == 60.0


def test_game_injects_budget_only_when_explicitly_supplied():
    class Agent:
        def __init__(self): self.budget = None
        def set_game_cost_budget(self, budget): self.budget = budget
    no_budget_agent = Agent()
    Game(GameConfig(), {"P01": no_budget_agent}, logger=EventLogger(fixed_timestamp="t"))
    assert no_budget_agent.budget is None
    budget_agent = Agent()
    budget = GameCostBudget(5.0, 40.0, EventLogger(fixed_timestamp="t"))
    Game(GameConfig(), {"P01": budget_agent}, logger=EventLogger(fixed_timestamp="t"), cost_budget=budget)
    assert budget_agent.budget is budget


def test_cost_cap_fields_do_not_change_phase_prompt_text():
    phase2 = GameConfig.baseline_v1_s2(num_players=18)
    phase2_changed = phase2.model_copy(update={
        "per_player_game_cost_cap_usd": 99.0, "game_cost_cap_usd": 199.0,
    })
    assert build_system_prompt("P01", phase2) == build_system_prompt("P01", phase2_changed)
    assert build_loan_prompt(phase2) == build_loan_prompt(phase2_changed)

    phase3 = build_phase3_config()
    phase3_changed = phase3.model_copy(update={
        "per_player_game_cost_cap_usd": 99.0, "game_cost_cap_usd": 199.0,
    })
    assert build_system_prompt("P01", phase3) == build_system_prompt("P01", phase3_changed)
    assert build_loan_prompt(phase3) == build_loan_prompt(phase3_changed)


def test_player_caps_are_independent_for_same_model_players():
    budget = GameCostBudget(5.0, 40.0, EventLogger(fixed_timestamp="t"))
    first = budget.reserve("P01", 4.0, round_num=1, phase="negotiation")
    budget.settle(first, 4.0)
    other = budget.reserve("P02", 4.0, round_num=1, phase="negotiation")
    budget.settle(other, 4.0)
    with pytest.raises(BudgetBlockedError, match="per_player_cap"):
        budget.reserve("P01", 1.1, round_num=1, phase="commit")
    assert budget.player_spent_usd["P02"] == 4.0


def test_game_cap_blocks_all_players_before_api_and_records_event():
    logger = EventLogger(fixed_timestamp="t")
    budget = GameCostBudget(10.0, 5.0, logger)
    reservation = budget.reserve("P01", 4.9, round_num=1, phase="loan_choice")
    budget.settle(reservation, 4.9)
    with pytest.raises(BudgetBlockedError, match="game_cap"):
        budget.reserve("P02", 0.2, round_num=1, phase="negotiation", turn=1)
    event = logger.events[-1]
    assert event.event_type == "LLM_BUDGET_BLOCKED"
    assert event.data["api_called"] is False
    assert event.data["reason"] == "game_cap"


def test_new_config_caps_do_not_have_legacy_five_or_twelve_limits():
    budget = GameCostBudget(10.0, 60.0, EventLogger(fixed_timestamp="t"))
    reservation = budget.reserve("P01", 6.0, round_num=1, phase="negotiation")
    budget.settle(reservation, 6.0)
    reservation = budget.reserve("P02", 7.0, round_num=1, phase="negotiation")
    budget.settle(reservation, 7.0)
    assert budget.game_spent_usd == 13.0
    assert not budget.blocks


def test_hidden_thinking_reserve_and_anthropic_normalization_are_preserved():
    gemini = MODEL_REGISTRY["M3"]
    base = worst_case_cost(gemini, "sys", "user", 100)
    without_reserve = worst_case_cost(
        type("M", (), {**gemini.__dict__, "hidden_thinking_reserve_tokens": 0})(), "sys", "user", 100,
    )
    assert base > without_reserve

    anthropic = MODEL_REGISTRY["L1"]
    usage = {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110,
             "cache_read_input_tokens": 50}
    # Anthropicのcache readは通常入力と別建てなので、合算後に割引される。
    assert usage_cost(anthropic, usage) > 0
