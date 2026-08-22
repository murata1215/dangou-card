"""
ゲーム完全終了後の全員答え合わせ（POST_GAME_REFLECTION）機能のテスト

背景: FINAL_REFLECTION（脱落者/完走者の最終コメント）はゲーム内で正当に
知り得た情報のみを扱う。本機能は、それとは別に「結果が完全に確定した後」
にのみ発火する専用フェイズで、生還者・脱落者の全12名（本テストでは
小規模ロスターで代表）に1回ずつ「神視点の答え合わせ」を書かせる。

このcallだけが、匿名通信の真の掲載者・DM本文・秘密契約の条項・破られた約束
といった「本人がゲーム中には正当に知り得なかった情報」をpromptへ投入してよい
唯一の経路である（`build_post_game_reflection_prompt()`）。

検証観点:
1. 発火条件（既定Falseで発火しない・全員1回ずつ・二重発火しない・
   予算abort/stop_after_roundでは発火しない・失敗しても他を止めない）
2. GameResult/game state/agentのmemoryを一切変更しないこと
3. `Game._god_transcript` がラウンド跨ぎで消えず、`_build_visible_state()`
   からは絶対に参照されないこと（最重要ガード）
4. `_build_god_shared_block()` / `_build_post_game_context()` が
   最終順位・ラウンドダイジェスト・契約台帳・違反台帳・本人固有の開示
   （revelations）を正しく組み立てること
5. `build_post_game_reflection_prompt()` の出力schema・サイズ上限
6. `parse_post_game_reflection()` のsalvage ladder・roster照合
"""

import json

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import StubAgent
from engine.models import (
    DmAction, AnonymousBroadcastAction, PassAction,
    Contract, ContractStatus, Obligation, ObligationType,
)

from llm.prompt_builder import build_post_game_reflection_prompt
from llm.response_parser import parse_post_game_reflection


# =============================================================================
# ローカルヘルパ（tests/test_final_reflection.py の様式をこのモジュール内に
# 独立してコピーする。テストモジュール間でprivateヘルパをimportしない）
# =============================================================================

class RecordingPostGameReflectAgent(StubAgent):
    """post_game_reflect() 呼び出しを記録するテスト用エージェント"""

    def __init__(self, result: dict | None = None):
        self.post_game_reflect_calls: list[tuple[str, int, dict]] = []
        self._result = result or {
            "status": "ok", "comment": "答え合わせ完了", "emotion": "平静",
            "self_assessment": "まずまずだった", "biggest_revelation": "裏切りがあった",
            "changed_opinion": "見方が変わった", "best_player": None,
            "most_deceptive_player": None, "chars": 6, "truncated": False,
            "salvaged": [],
        }

    def post_game_reflect(self, player_state, round_num, post_game_context):
        self.post_game_reflect_calls.append(
            (player_state.player_id, round_num, post_game_context)
        )
        return self._result


class ExplodingPostGameReflectAgent(StubAgent):
    """post_game_reflect()が必ず例外を投げるテスト用エージェント"""

    def __init__(self):
        self.called = False

    def post_game_reflect(self, player_state, round_num, post_game_context):
        self.called = True
        raise RuntimeError("boom")


def _make_config(num_players: int = 8, **overrides):
    updates = {"post_game_reflection_enabled": True}
    updates.update(overrides)
    return GameConfig.baseline_v1(num_players).model_copy(update=updates)


def _make_game(config, agent_factory=RecordingPostGameReflectAgent, num_players: int = 8):
    agents = {f"P{i+1:02d}": agent_factory() for i in range(num_players)}
    game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
    game._setup()
    return game, agents


def _eliminate(game: Game, pid: str, round_num: int, reason: str = "bankruptcy") -> None:
    """テスト用: state直接操作で脱落を確定させる（elimination.pyは経由しない）"""
    p = game.players[pid]
    game.players[pid] = p.model_copy(update={
        "is_alive": False,
        "elimination_reason": reason,
        "elimination_round": round_num,
    })


class DmSendingAgent(StubAgent):
    """交渉フェイズの初手で1通だけDMを送る（以降はpass）"""

    def __init__(self, to: str, message: str):
        self.to = to
        self.message = message
        self.sent = False

    def negotiate(self, player_state, round_num, turn, visible_state):
        if not self.sent:
            self.sent = True
            return DmAction(player_id=player_state.player_id, to=self.to, message=self.message)
        return PassAction(player_id=player_state.player_id)


class AnonSendingAgent(StubAgent):
    """交渉フェイズの初手で1通だけ匿名通信を送る（以降はpass）"""

    def __init__(self, message: str):
        self.message = message
        self.sent = False

    def negotiate(self, player_state, round_num, turn, visible_state):
        if not self.sent:
            self.sent = True
            return AnonymousBroadcastAction(player_id=player_state.player_id, message=self.message)
        return PassAction(player_id=player_state.player_id)


def _build_scenario_game():
    """
    TestBuildPostGameContext / TestPostGameReflectionPrompt 用の共有シナリオ。

    4人ロスター、current_round=3、以下を仕込む:
    - P01→P02 DM（R1）
    - 匿名通信の真の掲載者=P03（R2）
    - 契約C1: P01↔P02（非当事者=P03,P04）型A義務（R2, TYPE_A_FAILURE発生 → 不履行）
    - 契約C2: P03↔P04（非当事者=P01,P02）型B_MARKET義務（R1, TYPE_B_VIOLATION発生 → 違反）
    - R1のMARKET_RESULT（M01, 勝者P02）
    - P04がR3に脱落（contract_violation）
    """
    config = GameConfig.baseline_v1(4)
    agents = {f"P{i+1:02d}": StubAgent() for i in range(4)}
    game = Game(config=config, agents=agents, seed=7, logger=EventLogger())
    game._setup()
    game.current_round = 3

    game._god_transcript.append({
        "round": 1, "turn": 1, "type": "dm",
        "message": "M01を独占しよう", "sender": "P01", "to": "P02",
    })
    game._god_transcript.append({
        "round": 2, "turn": 1, "type": "anonymous_broadcast",
        "message": "P04を信用するな", "actual_sender": "P03",
    })

    ob1 = Obligation(
        obligation_id="OB1", contract_id="C1", obligor="P01", counterparty="P02",
        ob_type=ObligationType.TYPE_A_PAYMENT, round_num=2, details={"amount": 3_000_000},
    )
    contract1 = Contract(
        contract_id="C1", proposer="P01", parties=["P01", "P02"],
        signed_by=["P01", "P02"], obligations=[ob1], round_created=1,
        status=ContractStatus.ACTIVE,
    )
    ob2 = Obligation(
        obligation_id="OB2", contract_id="C2", obligor="P03", counterparty="P04",
        ob_type=ObligationType.TYPE_B_MARKET, round_num=1, details={"market_id": "M01"},
    )
    contract2 = Contract(
        contract_id="C2", proposer="P03", parties=["P03", "P04"],
        signed_by=["P03", "P04"], obligations=[ob2], round_created=1,
        status=ContractStatus.ACTIVE,
    )
    game.contracts = [contract1, contract2]

    game.logger.log("MARKET_RESULT", 1, "settlement", step=2, data={
        "market_id": "M01", "participants": 2, "winners": ["P02"],
        "prize_per_winner": 800_000, "total_pool": 800_000,
        "carryover": 0, "surged": False,
    })
    game.logger.log("TYPE_B_VIOLATION", 1, "settlement", step=3, data={
        "player_id": "P03", "obligation_id": "OB2", "ob_type": "type_b_market",
        "details": {"market_id": "M01"},
    })
    game.logger.log("TYPE_A_FAILURE", 2, "settlement", step=5, data={
        "player_id": "P01", "reason": "Atomic execution failed - insufficient free cash",
    })

    _eliminate(game, "P04", 3, reason="contract_violation")
    return game


def _synthetic_12x12_scenario():
    """test_prompt_size_ceiling用: 12人・12ラウンド想定の合成ゲーム"""
    config = GameConfig.baseline_v1(12)
    agents = {f"P{i+1:02d}": StubAgent() for i in range(12)}
    game = Game(config=config, agents=agents, seed=99, logger=EventLogger())
    game._setup()
    game.current_round = 12

    pids = [f"P{i+1:02d}" for i in range(12)]
    for r in range(1, 13):
        for m in range(1, 4):
            game.logger.log("MARKET_RESULT", r, "settlement", step=2, data={
                "market_id": f"M{m:02d}", "participants": 4,
                "winners": [pids[(r + m) % 12], pids[(r + m + 1) % 12]],
                "prize_per_winner": 500_000, "total_pool": 1_000_000,
                "carryover": 0, "surged": False,
            })

    for i in range(20):
        game._god_transcript.append({
            "round": (i % 12) + 1, "turn": 1, "type": "dm",
            "message": f"密談その{i}についての長めの本文です" * 2,
            "sender": pids[i % 12], "to": pids[(i + 1) % 12],
        })
    for i in range(10):
        game._god_transcript.append({
            "round": (i % 12) + 1, "turn": 2, "type": "anonymous_broadcast",
            "message": f"匿名メッセージその{i}",
            "actual_sender": pids[(i * 3) % 12],
        })

    contracts = []
    for i in range(15):
        a, b = pids[i % 12], pids[(i + 5) % 12]
        ob = Obligation(
            obligation_id=f"OB{i}", contract_id=f"C{i}", obligor=a, counterparty=b,
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=(i % 12) + 1,
            details={"amount": 1_000_000 + i * 10_000},
        )
        contracts.append(Contract(
            contract_id=f"C{i}", proposer=a, parties=[a, b], signed_by=[a, b],
            obligations=[ob], round_created=1, status=ContractStatus.ACTIVE,
        ))
    game.contracts = contracts
    for i in range(0, 15, 3):
        ob = contracts[i].obligations[0]
        game.logger.log("TYPE_A_FAILURE", ob.round_num, "settlement", step=5, data={
            "player_id": ob.obligor, "reason": "insufficient free cash",
        })

    for i, pid in enumerate(pids):
        if i % 2 == 0:
            game.players[pid] = game.players[pid].model_copy(update={
                "is_alive": False, "elimination_reason": "bankruptcy",
                "elimination_round": (i % 11) + 1,
            })
    return game, pids


# =============================================================================
# TestPostGameGating
# =============================================================================

class TestPostGameGating:
    """Game._phase_post_game_reflection() の発火条件"""

    def test_disabled_by_default_zero_calls(self):
        config = GameConfig.baseline_v1(4)
        assert config.post_game_reflection_enabled is False
        game, agents = _make_game(config, num_players=4)
        result = game._finalize()
        game._phase_post_game_reflection(result)
        for agent in agents.values():
            assert agent.post_game_reflect_calls == []
        assert [e for e in game.logger.events if e.event_type == "POST_GAME_REFLECTION"] == []

    def test_all_players_get_exactly_one(self):
        config = _make_config(8)
        game, agents = _make_game(config, num_players=8)
        _eliminate(game, "P02", 1)
        _eliminate(game, "P05", 2)
        result = game._finalize()
        game._phase_post_game_reflection(result)
        for pid, agent in agents.items():
            assert len(agent.post_game_reflect_calls) == 1
            assert agent.post_game_reflect_calls[0][0] == pid
        pg_events = [e for e in game.logger.events if e.event_type == "POST_GAME_REFLECTION"]
        assert len(pg_events) == 8
        assert {e.data["player_id"] for e in pg_events} == set(agents)

    def test_no_double_call_when_phase_invoked_twice(self):
        config = _make_config(4)
        game, agents = _make_game(config, num_players=4)
        result = game._finalize()
        game._phase_post_game_reflection(result)
        game._phase_post_game_reflection(result)
        for agent in agents.values():
            assert len(agent.post_game_reflect_calls) == 1

    def test_result_identical_before_and_after(self):
        config = _make_config(4)
        game, agents = _make_game(config, num_players=4)
        _eliminate(game, "P03", 2)
        result = game._finalize()
        survivors_before = [p.player_id for p in result.survivors]
        eliminated_before = [p.player_id for p in result.eliminated]
        players_before = {pid: p.model_copy() for pid, p in result.players.items()}

        game._phase_post_game_reflection(result)

        assert [p.player_id for p in result.survivors] == survivors_before
        assert [p.player_id for p in result.eliminated] == eliminated_before
        for pid, p in result.players.items():
            assert p == players_before[pid]

    def test_one_agent_failure_does_not_stop_others(self):
        config = _make_config(4)
        agents = {f"P{i+1:02d}": RecordingPostGameReflectAgent() for i in range(4)}
        agents["P02"] = ExplodingPostGameReflectAgent()
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        result = game._finalize()
        game._phase_post_game_reflection(result)

        assert agents["P02"].called is True
        pg_events = {
            e.data["player_id"]: e
            for e in game.logger.events if e.event_type == "POST_GAME_REFLECTION"
        }
        assert len(pg_events) == 4
        assert pg_events["P02"].data["status"] == "error"
        for pid in ("P01", "P03", "P04"):
            assert pg_events[pid].data["status"] == "ok"

    def test_not_fired_on_budget_abort(self):
        from llm.game_cost_budget import GameCostBudget

        class LoanOnlyAgent(RecordingPostGameReflectAgent):
            def choose_loan(self, config):
                return config.loan_min

        logger = EventLogger(fixed_timestamp="t")
        config = _make_config(1)
        budget = GameCostBudget(5.0, 40.0, logger, abort_on_block=True)
        budget.blocks.append({"reason": "per_player_cap", "player_id": "P01"})
        agent = LoanOnlyAgent()
        game = Game(config, {"P01": agent}, logger=logger, cost_budget=budget)

        result = game.run()

        assert result.round_count == 0
        assert agent.post_game_reflect_calls == []
        assert [e for e in logger.events if e.event_type == "POST_GAME_REFLECTION"] == []

    def test_budget_block_does_not_prevent_completion(self):
        from llm.game_cost_budget import GameCostBudget

        class LoanOnlyAgent(RecordingPostGameReflectAgent):
            def choose_loan(self, config):
                return config.loan_min

        logger = EventLogger(fixed_timestamp="t")
        config = _make_config(1)
        budget = GameCostBudget(5.0, 40.0, logger, abort_on_block=True)
        budget.blocks.append({"reason": "per_player_cap", "player_id": "P01"})
        game = Game(config, {"P01": LoanOnlyAgent()}, logger=logger, cost_budget=budget)

        result = game.run()

        assert result is not None
        assert logger.events[-1].event_type == "GAME_END"
        assert logger.events[-1].data["completed"] is False
        assert logger.events[-1].data["abort_reason"] == "llm_budget_blocked"

    def test_not_fired_on_stop_after_round(self):
        config = _make_config(1)
        agent = RecordingPostGameReflectAgent()
        logger = EventLogger(fixed_timestamp="t")

        result = Game(config, {"P01": agent}, logger=logger, stop_after_round=3).run()

        assert result.round_count == 3
        assert agent.post_game_reflect_calls == []
        event_types = [e.event_type for e in logger.events]
        assert "POST_GAME_REFLECTION" not in event_types
        assert event_types[-1] == "GAME_END"
        assert logger.events[-1].data["stop_reason"] == "trial_stop_after_round"

    def test_stub_agent_without_method_emits_no_event(self):
        config = _make_config(4)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(4)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        result = game._finalize()
        game._phase_post_game_reflection(result)
        assert [e for e in game.logger.events if e.event_type == "POST_GAME_REFLECTION"] == []

    def test_state_and_memory_unmutated(self):
        config = _make_config(4)
        game, agents = _make_game(config, num_players=4)
        _eliminate(game, "P02", 1)
        contracts_before = list(game.contracts)
        memories_before = {pid: getattr(a, "_memory", None) for pid, a in agents.items()}
        players_before = {pid: p.model_copy() for pid, p in game.players.items()}

        result = game._finalize()
        game._phase_post_game_reflection(result)

        assert game.contracts == contracts_before
        for pid, a in agents.items():
            assert getattr(a, "_memory", None) == memories_before[pid]
        for pid, p in game.players.items():
            assert p == players_before[pid]


# =============================================================================
# TestGodTranscript
# =============================================================================

class TestGodTranscript:
    """Game._god_transcript の隔離性・永続性"""

    def test_transcript_survives_round_reset(self):
        config = GameConfig.baseline_v1(4)
        game, _agents = _make_game(config, agent_factory=StubAgent, num_players=4)
        game._god_transcript.append({
            "round": 1, "turn": 1, "type": "dm",
            "message": "seed", "sender": "P01", "to": "P02",
        })
        game._phase_negotiation(2)  # _round_messagesはここでクリアされる
        assert game._god_transcript[0]["message"] == "seed"

    def test_transcript_never_reaches_visible_state(self):
        config = GameConfig.baseline_v1(4)
        game, _agents = _make_game(config, agent_factory=StubAgent, num_players=4)
        game._god_transcript.append({
            "round": 1, "turn": 1, "type": "dm",
            "message": "秘密の合言葉XYZZY", "sender": "P01", "to": "P02",
        })
        game._god_transcript.append({
            "round": 1, "turn": 2, "type": "anonymous_broadcast",
            "message": "裏で糸を引いている", "actual_sender": "P03",
        })
        for round_num in (1, 2):
            for pid in list(game.players) + [None]:
                dump = json.dumps(
                    game._build_visible_state(round_num, for_player_id=pid),
                    ensure_ascii=False,
                )
                assert "秘密の合言葉XYZZY" not in dump
                assert "actual_sender" not in dump

    def test_transcript_records_dm_to_field(self):
        config = GameConfig.baseline_v1(4)
        agents = {
            "P01": DmSendingAgent("P02", "国家機密メッセージ"),
            "P02": StubAgent(), "P03": StubAgent(), "P04": StubAgent(),
        }
        game = Game(config=config, agents=agents, seed=1, logger=EventLogger())
        game._setup()
        game._phase_negotiation(1)

        dm_entries = [e for e in game._god_transcript if e["type"] == "dm"]
        assert len(dm_entries) == 1
        assert dm_entries[0]["sender"] == "P01"
        assert dm_entries[0]["to"] == "P02"
        assert dm_entries[0]["message"] == "国家機密メッセージ"

    def test_transcript_records_anon_actual_sender(self):
        config = GameConfig.baseline_v1(4)
        agents = {
            "P01": AnonSendingAgent("裏でP01が糸を引く"),
            "P02": StubAgent(), "P03": StubAgent(), "P04": StubAgent(),
        }
        game = Game(config=config, agents=agents, seed=1, logger=EventLogger())
        game._setup()
        game._phase_negotiation(1)

        anon_entries = [e for e in game._god_transcript if e["type"] == "anonymous_broadcast"]
        assert len(anon_entries) == 1
        assert anon_entries[0]["actual_sender"] == "P01"
        assert anon_entries[0]["message"] == "裏でP01が糸を引く"


# =============================================================================
# TestBuildPostGameContext
# =============================================================================

class TestBuildPostGameContext:
    """Game._build_god_shared_block() / _build_post_game_context()"""

    def test_shared_block_contains_standings_digest_ledgers(self):
        game = _build_scenario_game()
        result = game._finalize()
        shared = game._build_god_shared_block(result)

        assert shared["roster"] == ["P01", "P02", "P03", "P04"]
        assert len(shared["final_standings"]) == 4
        assert any("P04" in line and "R3脱落" in line for line in shared["final_standings"])
        assert len(shared["round_digest"]) == 3  # current_round=3
        assert "M01" in shared["round_digest"][0]
        assert "P02" in shared["round_digest"][0]
        assert len(shared["contract_ledger"]) == 2
        assert any("✗不履行" in line for line in shared["contract_ledger"])
        assert any("✗違反" in line for line in shared["contract_ledger"])
        assert len(shared["violation_ledger"]) == 2

    def test_shared_block_byte_identical_across_all_players(self):
        game = _build_scenario_game()
        result = game._finalize()
        shared1 = game._build_god_shared_block(result)
        shared2 = game._build_god_shared_block(result)
        assert (
            json.dumps(shared1, ensure_ascii=False, sort_keys=True)
            == json.dumps(shared2, ensure_ascii=False, sort_keys=True)
        )

    def test_revelations_block_is_player_specific(self):
        game = _build_scenario_game()
        result = game._finalize()
        shared = game._build_god_shared_block(result)

        revelations_by_pid = {
            pid: tuple(game._build_post_game_context(pid, result, shared)["revelations"])
            for pid in game.players
        }
        # 4人とも異なる開示内容を受け取ること
        assert len(set(revelations_by_pid.values())) == 4

    def test_dm_revelation_visible_to_participants_only(self):
        game = _build_scenario_game()
        result = game._finalize()
        shared = game._build_god_shared_block(result)

        ctx_p01 = game._build_post_game_context("P01", result, shared)
        ctx_p02 = game._build_post_game_context("P02", result, shared)
        ctx_p03 = game._build_post_game_context("P03", result, shared)

        assert any("M01を独占しよう" in r for r in ctx_p01["revelations"])
        assert any("M01を独占しよう" in r for r in ctx_p02["revelations"])
        assert not any("M01を独占しよう" in r for r in ctx_p03["revelations"])

    def test_anon_true_sender_revealed_to_non_senders(self):
        game = _build_scenario_game()
        result = game._finalize()
        shared = game._build_god_shared_block(result)

        ctx_p01 = game._build_post_game_context("P01", result, shared)
        ctx_p03 = game._build_post_game_context("P03", result, shared)

        assert any("P03" in r and "匿名通信" in r for r in ctx_p01["revelations"])
        # 自分自身が真の掲載者だった通信は「開示」ではないので含まれない
        assert not any("匿名通信" in r for r in ctx_p03["revelations"])

    def test_non_party_contract_terms_revealed(self):
        game = _build_scenario_game()
        result = game._finalize()
        shared = game._build_god_shared_block(result)

        ctx_p03 = game._build_post_game_context("P03", result, shared)
        ctx_p04 = game._build_post_game_context("P04", result, shared)

        # P03/P04はC1(P01↔P02)の非当事者 → 開示対象
        assert any("P01" in r and "P02" in r and "契約" in r for r in ctx_p03["revelations"])
        assert any("P01" in r and "P02" in r and "契約" in r for r in ctx_p04["revelations"])

    def test_broken_promise_against_victim_revealed(self):
        game = _build_scenario_game()
        result = game._finalize()
        shared = game._build_god_shared_block(result)

        # C2(P03→P04, 型B義務)がTYPE_B_VIOLATION → 被害者P04にのみ開示
        ctx_p04 = game._build_post_game_context("P04", result, shared)
        ctx_p03 = game._build_post_game_context("P03", result, shared)
        assert any("P03" in r and "破りました" in r for r in ctx_p04["revelations"])
        assert not any("破りました" in r for r in ctx_p03["revelations"])

        # C1(P01→P02, 型A義務)がTYPE_A_FAILURE → 被害者P02にのみ開示
        ctx_p02 = game._build_post_game_context("P02", result, shared)
        ctx_p01 = game._build_post_game_context("P01", result, shared)
        assert any("P01" in r and "履行されません" in r for r in ctx_p02["revelations"])
        assert not any("履行されません" in r for r in ctx_p01["revelations"])

    def test_own_rank_present_and_consistent(self):
        game = _build_scenario_game()
        result = game._finalize()
        shared = game._build_god_shared_block(result)
        for pid in game.players:
            ctx = game._build_post_game_context(pid, result, shared)
            assert ctx["own_rank"] == shared["rank_by_player"][pid]

    def test_survived_and_elimination_fields_accurate(self):
        game = _build_scenario_game()
        result = game._finalize()
        shared = game._build_god_shared_block(result)

        ctx_p01 = game._build_post_game_context("P01", result, shared)
        ctx_p04 = game._build_post_game_context("P04", result, shared)
        assert ctx_p01["survived"] is True
        assert ctx_p01["elimination_reason"] is None
        assert ctx_p01["elimination_round"] is None
        assert ctx_p04["survived"] is False
        assert ctx_p04["elimination_reason"] == "contract_violation"
        assert ctx_p04["elimination_round"] == 3

    def test_fog_round_cards_degrade_gracefully(self):
        config = GameConfig.baseline_v1(4).model_copy(update={"fog_rounds": [2]})
        agents = {f"P{i+1:02d}": StubAgent() for i in range(4)}
        game = Game(config=config, agents=agents, seed=3, logger=EventLogger())
        game._setup()
        game.current_round = 2
        game.logger.log("REVEAL", 2, "settlement", step=1, data={
            "commits": [{"player_id": "P01", "market_id": "M01", "card": "FOG", "rank": "FOG"}],
            "fog": True,
        })
        result = game._finalize()
        shared = game._build_god_shared_block(result)  # KeyErrorが起きないこと
        assert shared["round_digest"][1] == "R2: （市場結果なし）"

    def test_other_agents_memory_and_reasoning_absent(self):
        game = _build_scenario_game()
        result = game._finalize()
        shared = game._build_god_shared_block(result)
        ctx = game._build_post_game_context("P01", result, shared)
        dump = json.dumps(ctx, ensure_ascii=False, default=str)
        assert "_memory" not in dump
        assert "reasoning" not in dump


# =============================================================================
# TestPostGameReflectionPrompt
# =============================================================================

class TestPostGameReflectionPrompt:
    """build_post_game_reflection_prompt() の描画内容・サイズ上限"""

    def test_output_schema_seven_keys_present(self):
        game = _build_scenario_game()
        result = game._finalize()
        shared = game._build_god_shared_block(result)
        ctx = game._build_post_game_context("P01", result, shared)
        prompt = build_post_game_reflection_prompt(game.players["P01"], game.config, ctx)

        for key in (
            "emotion", "self_assessment", "biggest_revelation", "changed_opinion",
            "best_player", "most_deceptive_player", "comment",
        ):
            assert f'"{key}"' in prompt
        assert "ゲームは終わりました" in prompt
        assert "この結果は変わりません" in prompt

    def test_survived_and_eliminated_status_lines_are_accurate(self):
        game = _build_scenario_game()
        result = game._finalize()
        shared = game._build_god_shared_block(result)

        ctx_p01 = game._build_post_game_context("P01", result, shared)
        ctx_p04 = game._build_post_game_context("P04", result, shared)
        prompt_p01 = build_post_game_reflection_prompt(game.players["P01"], game.config, ctx_p01)
        prompt_p04 = build_post_game_reflection_prompt(game.players["P04"], game.config, ctx_p04)

        assert "生還しました" in prompt_p01
        assert "脱落しました" in prompt_p04
        assert "contract_violation" in prompt_p04

    def test_prompt_size_ceiling(self):
        game, pids = _synthetic_12x12_scenario()
        result = game._finalize()
        shared = game._build_god_shared_block(result)
        for pid in pids:
            ctx = game._build_post_game_context(pid, result, shared)
            prompt = build_post_game_reflection_prompt(game.players[pid], game.config, ctx)
            assert len(prompt) < 12000, f"{pid}: {len(prompt)} chars"


# =============================================================================
# TestParsePostGameReflection
# =============================================================================

class TestParsePostGameReflection:
    """parse_post_game_reflection() のフォールバック挙動"""

    def test_full_json_ok(self):
        text = json.dumps({
            "emotion": "楽", "self_assessment": "よくやった", "biggest_revelation": "P07が黒幕",
            "changed_opinion": "P03を見直した", "best_player": "P07",
            "most_deceptive_player": "p03", "comment": "満足した",
        }, ensure_ascii=False)
        result = parse_post_game_reflection(text, roster={"P01", "P03", "P07"})
        assert result["status"] == "ok"
        assert result["comment"] == "満足した"
        assert result["emotion"] == "楽"
        assert result["best_player"] == "P07"
        assert result["most_deceptive_player"] == "P03"
        assert result["best_player_raw"] is None
        assert result["salvaged"] == []

    def test_fenced_json_ok(self):
        text = '```json\n{"comment": "コードフェンス経由"}\n```'
        result = parse_post_game_reflection(text)
        assert result["status"] == "ok"
        assert result["comment"] == "コードフェンス経由"

    def test_raw_newline_salvage_ok_recovered(self):
        text = '```json\n{"comment": "本当に悔しい\n最後まで頑張った", "emotion": "哀"}\n```'
        result = parse_post_game_reflection(text)
        assert result["status"] == "ok_recovered"
        assert result["comment"] == "本当に悔しい\n最後まで頑張った"
        assert result["emotion"] == "哀"

    def test_truncated_before_comment_key_ok_assembled(self):
        text = '```json\n{"emotion": "焦", "biggest_revelation": "P07が黒幕だった"'
        result = parse_post_game_reflection(text)
        assert result["status"] == "ok_assembled"
        assert "P07が黒幕だった" in result["comment"]
        assert result["emotion"] == "焦"

    def test_comment_only(self):
        text = json.dumps({"comment": "これだけ"})
        result = parse_post_game_reflection(text)
        assert result["status"] == "ok"
        assert result["comment"] == "これだけ"
        assert result["emotion"] == "平静"

    def test_comment_missing_rejected(self):
        text = json.dumps({"emotion": "楽"})
        result = parse_post_game_reflection(text)
        assert result["status"] == "rejected_no_comment"
        assert result["comment"] == ""

    def test_empty_text(self):
        result = parse_post_game_reflection("")
        assert result["status"] == "empty"
        assert result["comment"] == ""

    def test_unsalvageable_wrapper_degrades_to_plaintext_wrapper(self):
        text = '{"emo'
        result = parse_post_game_reflection(text)
        assert result["status"] == "ok_plaintext_wrapper"
        assert "{" not in result["comment"]
        assert "comment" not in result["comment"]

    def test_non_enum_emotion_defaults_to_heisei(self):
        text = json.dumps({"comment": "普通の感想", "emotion": "困惑"})
        result = parse_post_game_reflection(text)
        assert result["emotion"] == "平静"

    def test_unknown_player_id_raw_and_salvage_flag(self):
        text = json.dumps({"comment": "OK", "best_player": "P13（幻覚モデル名かも）"})
        result = parse_post_game_reflection(text, roster={"P01", "P02"})
        assert result["best_player"] is None
        assert result["best_player_raw"] == "P13（幻覚モデル名かも）"
        assert "best_player_unresolved" in result["salvaged"]

    def test_lowercase_player_id_normalized(self):
        text = json.dumps({"comment": "OK", "best_player": "p07"})
        result = parse_post_game_reflection(text, roster={"P07"})
        assert result["best_player"] == "P07"
        assert result["best_player_raw"] is None

    def test_self_reference_allowed(self):
        text = json.dumps({"comment": "自分が一番だった", "best_player": "P05"})
        result = parse_post_game_reflection(text, roster={"P05"})
        assert result["best_player"] == "P05"

    def test_nashi_does_not_set_salvage_flag(self):
        text = json.dumps({"comment": "特にいない", "most_deceptive_player": "なし"})
        result = parse_post_game_reflection(text, roster={"P01"})
        assert result["most_deceptive_player"] is None
        assert result["most_deceptive_player_raw"] is None
        assert "most_deceptive_player_unresolved" not in result["salvaged"]

    def test_char_cap_truncates_with_flag(self):
        text = json.dumps({"comment": "あ" * 1500})
        result = parse_post_game_reflection(text, max_chars=1000)
        assert result["chars"] == 1000
        assert result["truncated"] is True

    def test_aux_field_truncation_recorded_in_salvaged(self):
        text = json.dumps({"comment": "OK", "self_assessment": "あ" * 400})
        result = parse_post_game_reflection(text)
        assert len(result["self_assessment"]) <= 250
        assert "self_assessment_truncated" in result["salvaged"]

    def test_roster_none_skips_membership_check(self):
        text = json.dumps({"comment": "OK", "best_player": "P99"})
        result = parse_post_game_reflection(text, roster=None)
        assert result["best_player"] == "P99"
        assert "best_player_unresolved" not in result["salvaged"]
