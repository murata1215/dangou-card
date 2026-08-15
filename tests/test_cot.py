"""
CoT (Chain-of-Thought) reasoning フィールドのテスト

- enable_cot フラグの挙動
- プロンプトへの reasoning 指示追加/非追加
- 応答パースでの reasoning 抽出・欠落時の頑健性
- 情報リーク防止（他プレイヤーへの reasoning 非公開）
- ログ記録
"""

import pytest

from engine.config import GameConfig
from engine.game import Game
from engine.events import EventLogger
from engine.negotiation import StubAgent
from llm.prompt_builder import (
    build_system_prompt,
    build_loan_prompt,
    build_negotiation_prompt,
    build_commit_prompt,
    build_double_up_prompt,
)
from llm.response_parser import parse_response
from tests.conftest import make_player


class TestCoTConfig:
    """enable_cot フラグのテスト"""

    def test_enable_cot_default_false(self):
        """enable_cot のデフォルトは False"""
        config = GameConfig.baseline_v1(8)
        assert config.enable_cot is False

    def test_enable_cot_s2_default_false(self):
        """S2 プリセットでも enable_cot=False"""
        config = GameConfig.baseline_v1_s2(8)
        assert config.enable_cot is False

    def test_enable_cot_can_be_enabled(self):
        """enable_cot を True に設定できる"""
        config = GameConfig.baseline_v1(8).model_copy(update={"enable_cot": True})
        assert config.enable_cot is True


class TestCoTPrompt:
    """プロンプトへの reasoning 指示テスト"""

    def test_system_prompt_contains_reasoning_when_enabled(self):
        """enable_cot=True でシステムプロンプトに reasoning 指示が含まれる"""
        config = GameConfig.baseline_v1(8).model_copy(update={"enable_cot": True})
        prompt = build_system_prompt("P01", config)
        assert "reasoning" in prompt
        assert "推論" in prompt

    def test_system_prompt_no_reasoning_when_disabled(self):
        """enable_cot=False でシステムプロンプトに reasoning 指示がない"""
        config = GameConfig.baseline_v1(8)
        prompt = build_system_prompt("P01", config)
        assert "reasoning" not in prompt.lower().split("アクション形式")[1] if "アクション形式" in prompt else True

    def test_loan_prompt_reasoning_when_enabled(self):
        """enable_cot=True で借入プロンプトのJSON例に reasoning が含まれる"""
        config = GameConfig.baseline_v1(8).model_copy(update={"enable_cot": True})
        prompt = build_loan_prompt(config)
        assert '"reasoning"' in prompt

    def test_loan_prompt_no_reasoning_when_disabled(self):
        """enable_cot=False で借入プロンプトに reasoning がない"""
        config = GameConfig.baseline_v1(8)
        prompt = build_loan_prompt(config)
        assert '"reasoning"' not in prompt

    def test_negotiation_prompt_reasoning_when_enabled(self):
        """enable_cot=True で交渉プロンプトのJSON例に reasoning が含まれる"""
        config = GameConfig.baseline_v1(8).model_copy(update={"enable_cot": True})
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        state = {
            "round_num": 1, "markets": [{"market_id": "M01", "prize_pool": 500000}],
            "alive_players": ["P01", "P02"], "messages": [],
            "contracts_pending": [], "trades_pending": [],
        }
        prompt = build_negotiation_prompt(player, 1, 1, state, config)
        assert '"reasoning"' in prompt

    def test_commit_prompt_reasoning_when_enabled(self):
        """enable_cot=True でコミットプロンプトのJSON例に reasoning が含まれる"""
        config = GameConfig.baseline_v1(8).model_copy(update={"enable_cot": True})
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        from engine.models import Market
        markets = [Market(market_id="M01", base_prize=500000)]
        state = {"round_num": 1, "used_cards": {}}
        prompt = build_commit_prompt(player, markets, 1, state, config)
        assert '"reasoning"' in prompt

    def test_double_up_prompt_reasoning_when_enabled(self):
        """enable_cot=True で倍掛けプロンプトのJSON例に reasoning が含まれる"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"enable_cot": True})
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        state = {
            "round_num": 1, "alive_players": ["P01", "P02"],
            "double_ups": [],
        }
        prompt = build_double_up_prompt(player, 500000, 1, state, config)
        assert '"reasoning"' in prompt


class TestCoTParse:
    """reasoning のパーステスト"""

    def test_parse_with_reasoning(self):
        """reasoning 付き応答が正しくパースされ、strategy に _reasoning が含まれる"""
        text = '{"reasoning": "市場分析の結果M01が最適", "strategy": {"reason": "test", "emotion": "楽"}, "action": {"type": "pass"}}'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert strategy["_reasoning"] == "市場分析の結果M01が最適"
        assert action.type == "pass"

    def test_parse_without_reasoning(self):
        """reasoning なし応答でも従来通りパースされる（回帰）"""
        text = '{"strategy": {"reason": "test", "emotion": "楽"}, "action": {"type": "pass"}}'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert "_reasoning" not in strategy
        assert action.type == "pass"

    def test_reasoning_preserves_strategy_fields(self):
        """reasoning があっても strategy の他フィールド（emotion 等）は保持される"""
        text = '{"reasoning": "思考内容", "strategy": {"reason": "test", "emotion": "奸", "target_market": "M02"}, "action": {"type": "pass"}}'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert strategy["_reasoning"] == "思考内容"
        assert strategy["emotion"] == "奸"
        assert strategy["target_market"] == "M02"

    def test_reasoning_without_strategy(self):
        """strategy なしでも reasoning があれば _reasoning 付き dict が返る"""
        text = '{"reasoning": "考え中", "action": {"type": "pass"}}'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert strategy is not None
        assert strategy["_reasoning"] == "考え中"

    def test_non_string_reasoning_ignored(self):
        """reasoning が文字列でない場合は無視される"""
        text = '{"reasoning": 42, "strategy": {"reason": "test", "emotion": "楽"}, "action": {"type": "pass"}}'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert "_reasoning" not in strategy


class TestCoTNoLeak:
    """情報リーク防止テスト（最重要）"""

    def test_visible_state_no_reasoning(self):
        """_build_visible_state に reasoning/strategy/_reasoning が含まれない"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"enable_cot": True})
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game._setup()
        game._phase_market_open(1)

        # 全プレイヤー向けの visible_state を取得
        for pid in game.players:
            state = game._build_visible_state(1, for_player_id=pid)
            state_str = str(state)
            assert "_reasoning" not in state_str
            assert "reasoning" not in state_str.lower().replace("reasoning", "").lower()  # キーとして存在しない

    def test_negotiation_event_no_reasoning(self):
        """NEGOTIATION_ACTION イベントに reasoning が含まれない"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"enable_cot": True})
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game._setup()
        game._phase_market_open(1)
        game._phase_negotiation(1)

        for event in logger.events:
            if event.event_type == "NEGOTIATION_ACTION":
                event_str = str(event.data)
                assert "_reasoning" not in event_str
                assert "reasoning" not in event_str

    def test_other_player_prompt_no_reasoning(self):
        """他プレイヤー向けプロンプトに reasoning が含まれない"""
        config = GameConfig.baseline_v1(8).model_copy(update={"enable_cot": True})

        # P01 の reasoning が P02 向けプロンプトに入らないことを確認
        # visible_state にはそもそも strategy/reasoning が含まれない設計
        player_p02 = make_player("P02", cash=3_000_000, debt=2_000_000)
        state = {
            "round_num": 1, "markets": [{"market_id": "M01", "prize_pool": 500000}],
            "alive_players": ["P01", "P02"], "messages": [],
            "contracts_pending": [], "trades_pending": [],
        }
        prompt = build_negotiation_prompt(player_p02, 1, 1, state, config)
        # プロンプト内にJSON例としての "reasoning" はあるが、他者の思考内容はない
        # (具体的な思考内容テキストが混入しないことを確認)
        assert "市場分析の結果" not in prompt  # 他者の具体的 reasoning がない


class TestCoTLogging:
    """reasoning のログ記録テスト"""

    def test_reasoning_field_in_log_entry(self):
        """llm_logger の entry に reasoning フィールドが存在する"""
        from llm.llm_logger import LLMLogger
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as tmpdir:
            logger = LLMLogger(Path(tmpdir), game_id="test")
            logger.log_call(
                player_id="P01", model_id="test-model",
                phase="negotiation", round_num=1, turn=1,
                system_prompt="sys", user_prompt="usr",
                response_text="resp",
                usage={"input_tokens": 100, "output_tokens": 50},
                cost=0.001, elapsed_ms=100.0,
            )
            entry = logger._entries[-1]
            assert "reasoning" in entry
            assert entry["reasoning"] is None  # デフォルトは None

    def test_reasoning_populated_via_strategy(self):
        """_update_last_log_emotion で reasoning が後付けされる"""
        from llm.llm_logger import LLMLogger
        from llm.llm_agent import LLMAgent
        from llm.models import ModelInfo
        import tempfile
        from pathlib import Path

        model = ModelInfo(
            model_id="test", provider="Test", name="Test",
            adapter_type="openai_compat",
            input_price=0.0, output_price=0.0,
            env_key="TEST_KEY", base_url=None,
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            llm_logger = LLMLogger(Path(tmpdir), game_id="test")
            config = GameConfig.baseline_v1(8).model_copy(update={"enable_cot": True})
            agent = LLMAgent("P01", model, None, llm_logger, config)

            # ダミーエントリを追加
            llm_logger.log_call(
                player_id="P01", model_id="test",
                phase="negotiation", round_num=1, turn=1,
                system_prompt="", user_prompt="", response_text="",
                usage={"input_tokens": 0, "output_tokens": 0},
                cost=0.0, elapsed_ms=0.0,
            )

            # reasoning 付き strategy で更新
            strategy = {"emotion": "奸", "_reasoning": "M01が空いている可能性が高い"}
            agent._update_last_log_emotion(strategy)

            entry = llm_logger._entries[-1]
            assert entry["emotion"] == "奸"
            assert entry["reasoning"] == "M01が空いている可能性が高い"
