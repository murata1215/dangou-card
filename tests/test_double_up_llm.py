"""
倍掛け（double_up）の LLM 対応テスト

- build_double_up_prompt() の出力検証
- LLMAgent.choose_double_up() のパース動作検証（モック使用）
"""

import logging
from unittest.mock import MagicMock, patch

import pytest

from engine.config import GameConfig
from engine.models import PlayerState, Card, CardRank


class TestBuildDoubleUpPrompt:
    """build_double_up_prompt のプロンプト生成テスト"""

    def _make_player(self) -> PlayerState:
        return PlayerState(
            player_id="P01",
            cash=5_000_000,
            debt_balance=3_000_000,
            initial_loan=3_000_000,
            hand=[
                Card(rank=CardRank.FLUSH, card_id="c1"),
                Card(rank=CardRank.FULL_HOUSE, card_id="c2"),
                Card(rank=CardRank.ROYAL_FLUSH, card_id="c3"),
            ],
        )

    def test_contains_prize_amount(self):
        """獲得賞金額がプロンプトに含まれる"""
        from llm.prompt_builder import build_double_up_prompt

        config = GameConfig.baseline_v1_s2(8)
        prompt = build_double_up_prompt(
            self._make_player(), 800_000, 5,
            {"alive_players": ["P01", "P02", "P03"], "double_ups": []},
            config,
        )
        assert "80万円" in prompt

    def test_contains_success_condition(self):
        """成功条件の説明が含まれる"""
        from llm.prompt_builder import build_double_up_prompt

        config = GameConfig.baseline_v1_s2(8)
        prompt = build_double_up_prompt(
            self._make_player(), 800_000, 5,
            {"alive_players": ["P01", "P02"], "double_ups": []},
            config,
        )
        assert "R6" in prompt  # 次ラウンド
        assert "空き巣" in prompt or "1人だけの市場" in prompt
        assert "全額没収" in prompt

    def test_contains_double_payout(self):
        """2倍の受領額が表示される"""
        from llm.prompt_builder import build_double_up_prompt

        config = GameConfig.baseline_v1_s2(8)
        prompt = build_double_up_prompt(
            self._make_player(), 800_000, 5,
            {"alive_players": ["P01"], "double_ups": []},
            config,
        )
        assert "160万円" in prompt  # 800_000 * 2

    def test_contains_hand_info(self):
        """残りカード情報が含まれる"""
        from llm.prompt_builder import build_double_up_prompt

        config = GameConfig.baseline_v1_s2(8)
        prompt = build_double_up_prompt(
            self._make_player(), 500_000, 3,
            {"alive_players": ["P01"], "double_ups": []},
            config,
        )
        assert "FLUSH" in prompt
        assert "FULL_HOUSE" in prompt
        assert "ROYAL_FLUSH" in prompt

    def test_contains_choice_format(self):
        """回答形式の指示が含まれる"""
        from llm.prompt_builder import build_double_up_prompt

        config = GameConfig.baseline_v1_s2(8)
        prompt = build_double_up_prompt(
            self._make_player(), 500_000, 3,
            {"alive_players": ["P01"], "double_ups": []},
            config,
        )
        assert '"DOUBLE"' in prompt
        assert '"TAKE"' in prompt
        assert '"choice"' in prompt

    def test_shows_other_double_ups(self):
        """他プレイヤーの倍掛け状況が表示される"""
        from llm.prompt_builder import build_double_up_prompt

        config = GameConfig.baseline_v1_s2(8)
        prompt = build_double_up_prompt(
            self._make_player(), 500_000, 3,
            {
                "alive_players": ["P01", "P02"],
                "double_ups": [
                    {"player_id": "P02", "deposit": 600_000, "success_round": 4},
                ],
            },
            config,
        )
        assert "P02" in prompt
        assert "60万円" in prompt
        assert "R4" in prompt


class TestLLMAgentChooseDoubleUp:
    """LLMAgent.choose_double_up のパース動作テスト（モック使用）"""

    def _make_agent(self):
        from llm.llm_agent import LLMAgent
        from llm.models import ModelInfo

        model_info = ModelInfo(
            model_id="test-model",
            provider="Test",
            name="Test Model",
            adapter_type="openai_compat",
            input_price=0.0,
            output_price=0.0,
            env_key="TEST_API_KEY",
            base_url="http://localhost",
            timeout_seconds=10,
            max_tokens=100,
            extra_params=None,
        )
        logger = MagicMock()
        logger.total_cost = 0.0
        agent = LLMAgent("P01", model_info, MagicMock(), logger, GameConfig.baseline_v1_s2(8))
        agent._config = GameConfig.baseline_v1_s2(8)
        agent._system_prompt = "test"
        return agent

    def _make_player(self) -> PlayerState:
        return PlayerState(
            player_id="P01",
            cash=5_000_000,
            debt_balance=3_000_000,
            initial_loan=3_000_000,
        )

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_double_choice(self, mock_call):
        """DOUBLE を選択した場合 True が返る"""
        mock_call.return_value = ('{"strategy": {"reason": "test"}, "choice": "DOUBLE"}', {})
        agent = self._make_agent()
        result = agent.choose_double_up(
            self._make_player(), 500_000, 3,
            {"alive_players": ["P01"], "double_ups": []},
        )
        assert result is True

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_take_choice(self, mock_call):
        """TAKE を選択した場合 False が返る"""
        mock_call.return_value = ('{"strategy": {"reason": "safe"}, "choice": "TAKE"}', {})
        agent = self._make_agent()
        result = agent.choose_double_up(
            self._make_player(), 500_000, 3,
            {"alive_players": ["P01"], "double_ups": []},
        )
        assert result is False

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_invalid_choice_defaults_to_take(self, mock_call):
        """無効な選択は TAKE にフォールバック"""
        mock_call.return_value = ('{"strategy": {}, "choice": "MAYBE"}', {})
        agent = self._make_agent()
        result = agent.choose_double_up(
            self._make_player(), 500_000, 3,
            {"alive_players": ["P01"], "double_ups": []},
        )
        assert result is False

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_empty_response_defaults_to_take(self, mock_call):
        """空レスポンスは TAKE にフォールバック"""
        mock_call.return_value = ("", {})
        agent = self._make_agent()
        result = agent.choose_double_up(
            self._make_player(), 500_000, 3,
            {"alive_players": ["P01"], "double_ups": []},
        )
        assert result is False

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_parse_failure_defaults_to_take(self, mock_call):
        """JSON パース失敗時は TAKE にフォールバック"""
        mock_call.return_value = ("this is not json at all", {})
        agent = self._make_agent()
        result = agent.choose_double_up(
            self._make_player(), 500_000, 3,
            {"alive_players": ["P01"], "double_ups": []},
        )
        assert result is False

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_parse_failure_logs_warning(self, mock_call, caplog):
        """パース失敗時に警告ログが出力される"""
        mock_call.return_value = ("broken json {{{", {})
        agent = self._make_agent()
        with caplog.at_level(logging.WARNING, logger="llm.llm_agent"):
            agent.choose_double_up(
                self._make_player(), 500_000, 3,
                {"alive_players": ["P01"], "double_ups": []},
            )
        assert "defaulting to TAKE" in caplog.text

    def test_cost_exceeded_returns_take(self):
        """コスト超過時は TAKE"""
        agent = self._make_agent()
        agent._cost_exceeded = True
        result = agent.choose_double_up(
            self._make_player(), 500_000, 3,
            {"alive_players": ["P01"], "double_ups": []},
        )
        assert result is False
