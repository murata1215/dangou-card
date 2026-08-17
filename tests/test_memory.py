"""
引き継ぎメモリ（Handover Memory）機能のテスト

「R5のP01はR1でP04に裏切られた記憶を持っているか？」の調査（LLMコールは1-shot、
チャット/戦略メモは毎ラウンド消去、過去の契約違反もvisible_stateに出ない）を受けて
実装した「引き継ぎメモリ」機能を検証する。

設計:
- ラウンド終了後（Settlement/Finance後）に専用のReflectionフェイズを設け、
  各生存AIに次ラウンドへ持ち越す自由記述メモを1枚だけ書かせる
- 材料は「前ラウンドのmemory＋当ラウンドの会話・契約・結果」。渡すのは常に
  最新の1枚のみ（累積しない）
- config.memory_enabled=False（既定）では一切発火せず旧挙動を保持する
- フォーマットは強制しない（自由記述。モデルの性能差が出る場所）

検証観点:
1. Reflectionフェイズの発火条件（有効時のみ・最終R除く・生存者のみ・例外を握りつぶす）
2. reflectionプロンプトに当ラウンドの会話・契約・結果が実際に入ること
3. memoryの注入（negotiation/commitプロンプトへの反映、最新1枚のみ）
4. 失敗時の安全側動作（API失敗・空応答時は前ラウンドのメモリを維持）
5. 秘匿性（memoryは他プレイヤーのvisible_stateに絶対混入しない）
6. reasoningキーがmemoryに混入しないこと
7. viewerが未知のphase（reflection応答）を誤検出しないこと
"""

import json
from unittest.mock import MagicMock, patch

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import StubAgent
from engine.models import MarketCommit

from llm.prompt_builder import (
    build_negotiation_prompt, build_commit_prompt, build_reflection_prompt,
)
from llm.response_parser import extract_memory, normalize_memory

from tests.conftest import make_player, make_market


def _commit_for(pid: str, market_id: str, hand):
    """テスト用: プレイヤーの手札からカードを1枚取ってMarketCommitを作る"""
    card = hand[0]
    return MarketCommit(player_id=pid, market_id=market_id, card=card)


class RecordingAgent(StubAgent):
    """reflect() 呼び出しを記録するテスト用エージェント"""

    def __init__(self):
        self.reflect_calls: list[tuple[str, int]] = []

    def reflect(self, player_state, round_num, visible_state):
        self.reflect_calls.append((player_state.player_id, round_num))


class TestReflectDefaultNoop:
    """PlayerAgent.reflect() は非abstractのデフォルトno-op"""

    def test_reflect_default_is_noop(self):
        """デフォルト実装はNoneを返し例外を投げない（Bot/StubAgentは無変更でよい）"""
        agent = StubAgent()
        player = make_player("P01", cash=1_000_000)
        result = agent.reflect(player, 1, {"messages": []})
        assert result is None


class TestReflectionPhase:
    """Game._phase_reflection() の発火条件"""

    def _settle_round(self, game: Game, round_num: int) -> None:
        game._phase_market_open(round_num)
        pids = list(game.players)
        hand = game.players[pids[0]].hand
        game._current_commits = [_commit_for(pids[0], "M01", hand)]
        game._phase_settlement(round_num)
        game._phase_finance(round_num)

    def _make_game(self, config, agent_factory=RecordingAgent, num_players=8):
        agents = {f"P{i+1:02d}": agent_factory() for i in range(num_players)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        return game, agents

    def test_reflection_phase_runs_after_finance(self):
        """memory_enabled=Trueなら生存プレイヤー全員でreflect()が呼ばれる"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        game, agents = self._make_game(config)
        self._settle_round(game, 1)
        game._phase_reflection(1)

        for pid, agent in agents.items():
            assert agent.reflect_calls == [(pid, 1)]

    def test_reflection_skipped_on_final_round(self):
        """最終ラウンドはreflect()を呼ばない（次ラウンドが無く使われないため）"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 3})
        game, agents = self._make_game(config)
        self._settle_round(game, 3)
        game._phase_reflection(3)

        for agent in agents.values():
            assert agent.reflect_calls == []

    def test_reflection_skipped_when_disabled(self):
        """config.memory_enabled=False（既定）では一切reflect()を呼ばない"""
        config = GameConfig.baseline_v1(8)
        assert config.memory_enabled is False
        game, agents = self._make_game(config)
        self._settle_round(game, 1)
        game._phase_reflection(1)

        for agent in agents.values():
            assert agent.reflect_calls == []

    def test_reflection_skips_eliminated_players(self):
        """脱落済み（is_alive=False）のプレイヤーはreflect()を呼ばれない"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        game, agents = self._make_game(config)
        self._settle_round(game, 1)
        game.players["P02"].is_alive = False
        game._phase_reflection(1)

        assert agents["P02"].reflect_calls == []
        assert agents["P01"].reflect_calls == [("P01", 1)]

    def test_reflection_agent_exception_does_not_stop_others(self):
        """1体のreflect()が例外を投げても他プレイヤーの処理は継続する"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})

        class ExplodingAgent(RecordingAgent):
            def reflect(self, player_state, round_num, visible_state):
                raise RuntimeError("boom")

        agents = {f"P{i+1:02d}": RecordingAgent() for i in range(8)}
        agents["P01"] = ExplodingAgent()
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        self._settle_round(game, 1)

        game._phase_reflection(1)  # 例外を投げずに完走すること

        assert agents["P02"].reflect_calls == [("P02", 1)]

    def test_reflection_visible_state_includes_current_round_results(self):
        """
        _phase_reflection() が渡すvisible_stateには当ラウンドのsettlement結果が
        含まれる（commitフェイズ時点では存在しない「今ラウンドの結果」が
        reflection時点では揃っているというプラン前提の検証）
        """
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        captured: dict[str, dict] = {}

        class CapturingAgent(StubAgent):
            def reflect(self, player_state, round_num, visible_state):
                captured[player_state.player_id] = visible_state

        agents = {f"P{i+1:02d}": CapturingAgent() for i in range(8)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        self._settle_round(game, 1)
        game._phase_reflection(1)

        state = captured["P01"]
        assert state["last_round_results"] is not None
        assert state["last_round_results"]["round"] == 1

    def test_reflection_visible_state_includes_current_round_messages(self):
        """
        当ラウンドの会話（_round_messages）はnegotiationフェイズ冒頭でしか
        クリアされないため、settlement/finance通過後のreflectionでもまだ参照できる
        """
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        captured: dict[str, dict] = {}

        class CapturingAgent(StubAgent):
            def reflect(self, player_state, round_num, visible_state):
                captured[player_state.player_id] = visible_state

        agents = {f"P{i+1:02d}": CapturingAgent() for i in range(8)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        game._phase_market_open(1)
        game._round_messages.append(
            {"sender": "P01", "to": "P02", "type": "dm", "message": "M01で山分けしよう"}
        )
        pids = list(game.players)
        hand = game.players[pids[0]].hand
        game._current_commits = [_commit_for(pids[0], "M01", hand)]
        game._phase_settlement(1)
        game._phase_finance(1)
        game._phase_reflection(1)

        state = captured["P01"]
        assert any("山分けしよう" in m.get("message", "") for m in state["messages"])


class TestReflectionPrompt:
    """build_reflection_prompt() の描画内容"""

    def test_reflection_prompt_contains_round_messages_and_results(self):
        """今ラウンドの会話・契約・市場結果がすべてプロンプトに入る（ユーザー要件そのもの）"""
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        visible_state = {
            "messages": [
                {"sender": "P01", "to": "P02", "type": "dm", "message": "M01で組もう"},
                {"sender": "P03", "type": "broadcast", "message": "全員仲良く"},
            ],
            "last_round_results": {
                "round": 3,
                "markets": [{
                    "market_id": "M01",
                    "participants": ["P01", "P04"],
                    "commits": [
                        {"player_id": "P01", "card_rank": "FLUSH"},
                        {"player_id": "P04", "card_rank": "TWO_PAIR"},
                    ],
                    "winners": ["P01"], "prize_per_winner": 480_000,
                    "total_pool": 480_000, "surged": False, "carryover_to_next": 0,
                }],
            },
            "my_obligations": [],
            "contracts_public": [
                {"contract_id": "C01", "parties": ["P01", "P02"], "status": "active"},
            ],
        }
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_reflection_prompt(player, 3, visible_state, config, memory=None)

        assert "M01で組もう" in prompt
        assert "全員仲良く" in prompt
        assert "P01[FLUSH]★" in prompt
        assert "P04[TWO_PAIR]" in prompt
        assert "C01" in prompt
        assert '"memory"' in prompt

    def test_reflection_prompt_includes_previous_memory(self):
        """前ラウンドから引き継いだmemoryがあればプロンプトに含まれる"""
        player = make_player("P01", cash=1_000_000)
        visible_state = {"messages": [], "last_round_results": None, "my_obligations": []}
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_reflection_prompt(
            player, 2, visible_state, config, memory="R1でP04に裏切られた",
        )
        assert "R1でP04に裏切られた" in prompt
        assert "あなたの記憶" in prompt

    def test_reflection_prompt_respects_memory_max_chars_in_instruction(self):
        """文字数上限の指示にconfig.memory_max_charsが反映される"""
        player = make_player("P01", cash=1_000_000)
        visible_state = {"messages": [], "last_round_results": None, "my_obligations": []}
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"memory_max_chars": 500})
        prompt = build_reflection_prompt(player, 2, visible_state, config, memory=None)
        assert "500字" in prompt


class TestMemoryInjection:
    """negotiation/commitプロンプトへのmemory注入"""

    def _base_state(self):
        return {
            "round_num": 2,
            "markets": [{"market_id": "M01", "prize_pool": 480_000,
                         "base_prize": 480_000, "carryover": 0}],
            "last_round_results": None,
            "alive_players": ["P01"],
            "messages": [],
            "contracts_pending": [],
            "trades_pending": [],
        }

    def test_memory_injected_into_negotiation_prompt(self):
        player = make_player("P01", cash=1_000_000, debt=500_000)
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_negotiation_prompt(
            player, 2, 1, self._base_state(), config, memory="R1でP04に裏切られた",
        )
        assert "R1でP04に裏切られた" in prompt
        assert "あなたの記憶" in prompt

    def test_memory_injected_into_commit_prompt(self):
        player = make_player("P01", cash=1_000_000, debt=500_000)
        config = GameConfig.baseline_v1_s2(8)
        markets = [make_market("M01", base_prize=480_000)]
        prompt = build_commit_prompt(
            player, markets, 2, self._base_state(), config,
            memory="R1でP04に裏切られた",
        )
        assert "R1でP04に裏切られた" in prompt
        assert "あなたの記憶" in prompt

    def test_memory_none_omits_section_in_negotiation(self):
        """memory=None（R1やmemory_enabled=False）ではセクション自体が出ない"""
        player = make_player("P01", cash=1_000_000)
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_negotiation_prompt(player, 2, 1, self._base_state(), config, memory=None)
        assert "あなたの記憶" not in prompt

    def test_memory_none_omits_section_in_commit(self):
        player = make_player("P01", cash=1_000_000)
        config = GameConfig.baseline_v1_s2(8)
        markets = [make_market("M01", base_prize=480_000)]
        prompt = build_commit_prompt(
            player, markets, 2, self._base_state(), config, memory=None,
        )
        assert "あなたの記憶" not in prompt


class TestNormalizeMemory:
    """normalize_memory() — 最大文字数への切り詰め"""

    def test_memory_truncated_to_max_chars(self):
        long_memory = "あ" * 2000
        result = normalize_memory(long_memory, 1000)
        assert len(result) == 1000

    def test_memory_under_limit_unchanged(self):
        memory = "短いメモ"
        assert normalize_memory(memory, 1000) == memory

    def test_empty_memory_stays_empty(self):
        assert normalize_memory("", 1000) == ""


class TestExtractMemory:
    """extract_memory() — 自由記述レスポンスからのmemory抽出（ParseErrorを投げない）"""

    def test_memory_accepts_plain_text_response(self):
        """JSONでなくても応答テキストそのものが採用される（自由記述ゆえに破綻しない）"""
        text = "P04を信用しない。R4はP02と組む。"
        assert extract_memory(text) == text

    def test_memory_extracts_from_json(self):
        text = '{"memory": "R1でP04に裏切られた"}'
        assert extract_memory(text) == "R1でP04に裏切られた"

    def test_memory_extracts_from_fenced_json(self):
        text = '```json\n{"memory": "テストメモ"}\n```'
        assert extract_memory(text) == "テストメモ"

    def test_empty_text_returns_empty(self):
        assert extract_memory("") == ""

    def test_reasoning_never_reaches_memory(self):
        """reasoningキーが混入していても抽出されるのはmemoryキーのみ"""
        text = '{"reasoning": "内心はP04を潰したい", "memory": "表向きは友好的に"}'
        result = extract_memory(text)
        assert result == "表向きは友好的に"
        assert "内心" not in result


class TestLLMAgentReflect:
    """LLMAgent.reflect() の動作（モック使用）"""

    def _make_agent(self, memory_max_chars: int = 1000):
        from llm.llm_agent import LLMAgent
        from llm.models import ModelInfo

        model_info = ModelInfo(
            model_id="test-model", provider="Test", name="Test Model",
            adapter_type="openai_compat", input_price=0.0, output_price=0.0,
            env_key="TEST_API_KEY", base_url="http://localhost",
            timeout_seconds=10, max_tokens=100, extra_params=None,
        )
        logger = MagicMock()
        logger.total_cost = 0.0
        config = GameConfig.baseline_v1_s2(8).model_copy(
            update={"memory_max_chars": memory_max_chars}
        )
        agent = LLMAgent("P01", model_info, MagicMock(), logger, config)
        agent._config = config
        agent._system_prompt = "test"
        return agent

    def _make_player(self):
        return make_player("P01", cash=1_000_000, debt=500_000)

    def _visible_state(self):
        return {"messages": [], "last_round_results": None, "my_obligations": []}

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_reflect_updates_memory_from_json(self, mock_call):
        mock_call.return_value = ('{"memory": "P04を信用しない"}', {})
        agent = self._make_agent()
        agent.reflect(self._make_player(), 1, self._visible_state())
        assert agent._memory == "P04を信用しない"
        assert agent.memory_history == [{"round": 1, "memory": "P04を信用しない"}]

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_reflect_accepts_plain_text(self, mock_call):
        mock_call.return_value = ("P04を信用しない。次はP02と組む。", {})
        agent = self._make_agent()
        agent.reflect(self._make_player(), 1, self._visible_state())
        assert agent._memory == "P04を信用しない。次はP02と組む。"

    def test_memory_preserved_on_cost_exceeded(self):
        """コスト超過時はreflect()自体が何もしない（前ラウンドのメモリを維持）"""
        agent = self._make_agent()
        agent._memory = "既存メモ"
        agent._cost_exceeded = True
        agent.reflect(self._make_player(), 1, self._visible_state())
        assert agent._memory == "既存メモ"

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_memory_preserved_on_api_failure(self, mock_call):
        """API失敗（空応答）時は前ラウンドのメモリがそのまま維持される（空上書き禁止）"""
        agent = self._make_agent()
        agent._memory = "R1でP04に裏切られた"
        mock_call.return_value = ("", {})
        agent.reflect(self._make_player(), 2, self._visible_state())
        assert agent._memory == "R1でP04に裏切られた"

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_memory_preserved_on_empty_extracted_memory(self, mock_call):
        """memoryキーが空文字の場合も前ラウンドのメモリを維持する"""
        agent = self._make_agent()
        agent._memory = "既存メモ"
        mock_call.return_value = ('{"memory": ""}', {})
        agent.reflect(self._make_player(), 2, self._visible_state())
        assert agent._memory == "既存メモ"

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_memory_truncated_by_config_max_chars(self, mock_call):
        agent = self._make_agent(memory_max_chars=10)
        mock_call.return_value = ('{"memory": "' + "あ" * 50 + '"}', {})
        agent.reflect(self._make_player(), 1, self._visible_state())
        assert len(agent._memory) == 10

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_only_latest_memory_is_carried(self, mock_call):
        """2回reflect()すると2回目のメモが1回目を上書きする（累積しない）"""
        agent = self._make_agent()
        mock_call.return_value = ('{"memory": "R1: P04注意"}', {})
        agent.reflect(self._make_player(), 1, self._visible_state())
        mock_call.return_value = ('{"memory": "R2: P02と同盟"}', {})
        agent.reflect(self._make_player(), 2, self._visible_state())

        assert agent._memory == "R2: P02と同盟"
        assert "R1: P04注意" not in agent._memory
        # 履歴（観戦・分析用、visible_stateには出さない）には両方残る
        assert len(agent.memory_history) == 2

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_memory_passed_to_negotiation_prompt(self, mock_call):
        """reflect()で書いたmemoryが次のnegotiate()呼び出しのプロンプトに注入される"""
        mock_call.return_value = ('{"memory": "R1でP04に裏切られた"}', {})
        agent = self._make_agent()
        agent.reflect(self._make_player(), 1, self._visible_state())

        mock_call.reset_mock()
        mock_call.return_value = ('{"strategy": {}, "action": {"type": "pass"}}', {})
        visible_state = {
            "markets": [], "last_round_results": None, "alive_players": ["P01"],
            "messages": [], "contracts_pending": [], "trades_pending": [],
        }
        agent.negotiate(self._make_player(), 2, 1, visible_state)

        sent_prompt = mock_call.call_args.args[2]
        assert "R1でP04に裏切られた" in sent_prompt

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_memory_passed_to_commit_prompt(self, mock_call):
        """reflect()で書いたmemoryが次のcommit()呼び出しのプロンプトに注入される"""
        mock_call.return_value = ('{"memory": "R1でP04に裏切られた"}', {})
        agent = self._make_agent()
        agent.reflect(self._make_player(), 1, self._visible_state())

        mock_call.reset_mock()
        mock_call.return_value = (
            '{"strategy": {}, "action": {"type": "market_commit", '
            '"market_id": "M01", "card": "ONE_PAIR"}}', {}
        )
        visible_state = {
            "markets": [], "last_round_results": None, "alive_players": ["P01"],
            "messages": [], "contracts_pending": [], "trades_pending": [],
        }
        markets = [make_market("M01", base_prize=480_000)]
        agent.commit(self._make_player(), markets, 2, visible_state)

        sent_prompt = mock_call.call_args.args[2]
        assert "R1でP04に裏切られた" in sent_prompt


class TestMemorySecrecy:
    """memoryが他プレイヤー・visible_stateに絶対漏れないことの構造的検証"""

    def test_memory_not_in_visible_state(self):
        """
        _build_visible_state() の出力にmemoryキーが一切含まれない
        （memoryはLLMAgentインスタンス内に閉じ、エンジン経由で他人に渡らない）
        """
        config = GameConfig.baseline_v1_s2(8)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        game._phase_market_open(1)
        state = game._build_visible_state(1, for_player_id="P01")
        assert "memory" not in state
        assert "memory" not in str(state.get("my_obligations", []))

    def test_other_players_prompt_lacks_my_memory(self):
        """
        build_negotiation_prompt/build_commit_promptはmemory引数として
        明示的に渡された文字列のみを描画する。エンジンのvisible_stateには
        memoryが存在しないため、他プレイヤー用プロンプトを組み立てる際に
        自分のmemoryを渡さない限り絶対に混入しない（構造的な保証）
        """
        player = make_player("P02", cash=1_000_000)
        state = {
            "round_num": 2, "markets": [], "last_round_results": None,
            "alive_players": ["P01", "P02"], "messages": [],
            "contracts_pending": [], "trades_pending": [],
        }
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_negotiation_prompt(player, 2, 1, state, config, memory=None)
        assert "あなたの記憶" not in prompt


class TestViewerIgnoresReflection:
    """reflection応答がviewerの会話収集ロジックに誤検出されないこと"""

    def test_viewer_ignores_reflection_entries(self, tmp_path):
        """
        reflection応答（{"memory": "..."}のみでactionキーが無い）は
        _collect_round_messages() が拾わない（action抽出失敗時にスキップする
        既存ロジックがそのまま機能する。viewer側の変更は不要）
        """
        from viewer.log_parser import _collect_round_messages

        trial_dir = tmp_path / "trial_test"
        llm_logs_dir = trial_dir / "llm_logs"
        llm_logs_dir.mkdir(parents=True)

        entries = [
            {
                "player_id": "P01", "phase": "reflection", "round_num": 1,
                "response_text": '{"memory": "R1でP04に裏切られた。次は油断しない。"}',
            },
            {
                "player_id": "P01", "phase": "negotiation", "round_num": 1, "turn": 1,
                "response_text": '{"strategy": {}, "action": {"type": "broadcast", "message": "よろしく"}}',
            },
        ]
        log_file = llm_logs_dir / "game1_P01_llm_calls.jsonl"
        with open(log_file, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")

        by_round = _collect_round_messages(trial_dir, "game1")
        msgs = by_round.get("1", [])
        assert len(msgs) == 1
        assert msgs[0]["type"] == "broadcast"
        assert all("裏切られた" not in m.get("message", "") for m in msgs)
