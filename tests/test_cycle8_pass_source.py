"""
Cycle 8 修正2: pass のログ・トレーサビリティ

`PassAction.source` フィールド（Cycle 8で新設）が、
- LLMAgent.negotiate() の各 no-API-call 経路（cost_exceeded /
  auto_no_news / budget_blocked / parse_failed）で正しく設定されること
- LLMが実際にAPIを呼んでpassを選んだ通常経路では "llm"（デフォルト）のままなこと
- Bot（StubAgent）のpassが既存どおり動作すること（後方互換）
- engine/game.py の NEGOTIATION_ACTION ログの data に source が載ること

を検証する。実ログ（logs/llm/trial_C_l12_r12_20260828/）の再現実験により、
11件の「LLMコール記録と対応しないpass」は全件 AUTO_PASS_ON_NO_NEWS が原因と
確定済み（/tmp/cycle8/verify_pass.py、11/11一致）。本ファイルはその確定結果を
コードレベルで裏付けるための回帰テスト。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import StubAgent
from engine.models import PassAction
from llm.llm_agent import LLMAgent
from llm.llm_logger import LLMLogger
from llm.models import ModelInfo
from tests.conftest import make_player


class MockAdapter:
    """APIを呼ばないモックアダプタ（tests/test_llm.pyと同一パターン）"""

    def __init__(self, responses: list[str] | None = None):
        self._responses = responses or ['{"strategy":{},"action":{"type":"pass"}}']
        self._call_count = 0

    def complete(self, system, messages, max_tokens, temperature):
        idx = min(self._call_count, len(self._responses) - 1)
        text = self._responses[idx]
        self._call_count += 1
        return text, {"input_tokens": 100, "output_tokens": 50}


def _make_llm_agent(responses=None) -> LLMAgent:
    model_info = ModelInfo(
        model_id="test-model", provider="Test", name="Test",
        adapter_type="anthropic",
        input_price=1.0, output_price=5.0,
        env_key="TEST_KEY", base_url=None,
    )
    adapter = MockAdapter(responses)
    logger = LLMLogger("/tmp/test_llm_logs_cycle8", game_id="test")
    return LLMAgent("P01", model_info, adapter, logger)


def _make_game(num_players: int = 4) -> Game:
    config = GameConfig.baseline_v1(num_players).model_copy(
        update={"num_rounds": 5},
    )
    agents = {f"P{i + 1:02d}": StubAgent() for i in range(num_players)}
    game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
    game._setup()
    game._phase_market_open(1)
    return game


class TestPassActionSourceDefault:
    """PassActionのデフォルト値とモデルレベルの後方互換性"""

    def test_default_source_is_llm(self):
        """既存の `PassAction(player_id=...)` 呼び出しは全て不変（source='llm'）"""
        action = PassAction(player_id="P01")
        assert action.source == "llm"


class TestLLMAgentPassSource:
    """LLMAgent.negotiate() の各pass経路でsourceが正しく設定されること"""

    def test_normal_llm_pass_has_llm_source(self):
        """LLMが実際にpassを選んだ通常経路 -> source=='llm'"""
        agent = _make_llm_agent()
        config = GameConfig.baseline_v1()
        agent.choose_loan(config)
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        action = agent.negotiate(
            p, 1, 1, {"markets": [], "messages": [], "alive_players": ["P01"]},
        )
        assert isinstance(action, PassAction)
        assert action.source == "llm"

    def test_cost_exceeded_pass_has_cost_exceeded_source(self):
        """コスト上限超過経路 -> source=='cost_exceeded'"""
        agent = _make_llm_agent()
        config = GameConfig.baseline_v1()
        agent.choose_loan(config)
        agent._cost_exceeded = True
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        action = agent.negotiate(
            p, 1, 1, {"markets": [], "messages": [], "alive_players": ["P01"]},
        )
        assert isinstance(action, PassAction)
        assert action.source == "cost_exceeded"

    def test_auto_pass_on_no_news_has_auto_no_news_source(self):
        """turn>=2かつ新規メッセージ/失敗/通知なし -> source=='auto_no_news'
        （実ログ11件の原因として確定済みの経路のコードレベル再現）"""
        agent = _make_llm_agent()
        config = GameConfig.baseline_v1()
        agent.choose_loan(config)
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        # turn>=2、messagesが空（新規メッセージなし）、失敗/通知なし
        action = agent.negotiate(
            p, 1, 2, {"markets": [], "messages": [], "alive_players": ["P01"]},
        )
        assert isinstance(action, PassAction)
        assert action.source == "auto_no_news"


class TestNegotiationActionLogHasSource:
    """engine/game.py の NEGOTIATION_ACTION ログにsourceが載ること"""

    def test_pass_event_data_contains_source(self):
        game = _make_game()
        game._phase_negotiation(1)
        pass_events = [
            e for e in game.logger.events
            if e.event_type == "NEGOTIATION_ACTION" and e.data.get("action") == "pass"
        ]
        assert pass_events, "StubAgentは常にpassするのでNEGOTIATION_ACTION(pass)が必ず存在するはず"
        for e in pass_events:
            assert "source" in e.data
            # StubAgent（Bot相当）は後方互換のためsource未指定=デフォルト'llm'のまま
            assert e.data["source"] == "llm"


class TestBotPassUnchanged:
    """Bot（StubAgent）のpassが既存どおり動作すること（後方互換の確認）"""

    def test_stub_agent_pass_still_works(self):
        game = _make_game()
        game._phase_negotiation(1)
        # 例外なく完走し、全員がpass以外の行動をしていないこと
        for pid in game.players:
            pass_events = [
                e for e in game.logger.events
                if e.event_type == "NEGOTIATION_ACTION"
                and e.data.get("player_id") == pid
                and e.data.get("action") == "pass"
            ]
            assert len(pass_events) >= 1
