"""
全当事者合意による契約解除（contract_cancel）のテスト

背景: 実runでP11/P06の間に同一R・同一市場に両立不能な条件（TWO_PAIR要求と
ONE_PAIR要求）の契約が二重に成立し、1ラウンドに出せるカードは1枚しかないため
どちらかが必ず型B違反で脱落した。旧契約を無効化する手段が一切なかったため、
「生存する全当事者の合意でACTIVE契約をCANCELLEDへ遷移させる」機能を追加した。

カテゴリ:
  A. can_cancel_contract() の可否述語
  B. request_cancel() の合意集約
  C. Settlement統合（解除の効果）
  D. 提案後・最終同意前に履行/監査が発生するケース
  E. validate_action() の検証
  F. _build_visible_state() の可視化
  G. パーサ・プロンプト
  H. post-game台帳
"""

import pytest

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import StubAgent
from engine import contracts as contract_ops
from engine import actions as action_ops
from engine.settlement import execute_settlement
from engine.models import (
    Contract, ContractStatus, Obligation, ObligationType,
    ContractCancelAction, MarketCommit, Card, CardRank, PassAction,
)

from llm.response_parser import parse_response, ParseError
from llm.prompt_builder import (
    _render_my_contracts_block,
    _render_contract_notice_block,
    build_negotiation_prompt,
    build_system_prompt,
)
from llm.llm_agent import LLMAgent
from llm.llm_logger import LLMLogger
from llm.models import ModelInfo

from tests.conftest import make_contract, make_obligation, make_player, make_market


def _make_game(num_players: int = 4) -> Game:
    config = GameConfig.baseline_v1(num_players)
    agents = {f"P{i+1:02d}": StubAgent() for i in range(num_players)}
    game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
    game._setup()
    return game


def _eliminate(game: Game, pid: str, round_num: int, reason: str = "bankruptcy") -> None:
    p = game.players[pid]
    game.players[pid] = p.model_copy(update={
        "is_alive": False, "elimination_reason": reason, "elimination_round": round_num,
    })


# =============================================================================
# A. can_cancel_contract() の可否述語
# =============================================================================

class TestCanCancelContract:
    def test_active_all_obligations_due_this_round(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        ok, reason = contract_ops.can_cancel_contract(c, round_num=5)
        assert ok is True
        assert reason is None

    def test_active_all_obligations_in_future(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=8)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        ok, reason = contract_ops.can_cancel_contract(c, round_num=5)
        assert ok is True

    def test_fulfilled_obligation_blocks(self):
        ob = Obligation(
            obligation_id="C1_OB01", contract_id="C1", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5, details={"amount": 1},
            is_fulfilled=True,
        )
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        ok, reason = contract_ops.can_cancel_contract(c, round_num=6)
        assert ok is False
        assert "fulfilled" in reason

    def test_expired_obligation_blocks(self):
        ob = Obligation(
            obligation_id="C1_OB01", contract_id="C1", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_B_MARKET, round_num=5, details={"market_id": "M01"},
            is_expired=True,
        )
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        ok, reason = contract_ops.can_cancel_contract(c, round_num=6)
        assert ok is False
        assert "expired" in reason

    def test_type_b_normal_fulfillment_blocks_via_round_num(self):
        """型B義務は正常履行でもis_fulfilledが立たない。round_num<現在Rで捕捉する"""
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD, round_num=5,
                              details={"card_rank": "ONE_PAIR"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        ok, reason = contract_ops.can_cancel_contract(c, round_num=6)
        assert ok is False
        assert "audited" in reason

    def test_type_b_violated_blocks(self):
        ob = Obligation(
            obligation_id="C1_OB01", contract_id="C1", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_B_MARKET, round_num=5, details={"market_id": "M01"},
            is_expired=True,
        )
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        ok, reason = contract_ops.can_cancel_contract(c, round_num=5)
        assert ok is False

    def test_proposed_blocks(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob], status=ContractStatus.PROPOSED)
        ok, reason = contract_ops.can_cancel_contract(c, round_num=5)
        assert ok is False
        assert "not active" in reason

    def test_cancelled_blocks(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob], status=ContractStatus.CANCELLED)
        ok, reason = contract_ops.can_cancel_contract(c, round_num=5)
        assert ok is False
        assert "not active" in reason

    def test_mixed_past_and_future_obligations_blocks_whole_contract(self):
        """R3済み義務+R5未到来義務の混在契約は、契約全体が解除不可(単位は契約)"""
        ob1 = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=3)
        ob2 = make_obligation("C1_OB02", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob1, ob2])
        ok, reason = contract_ops.can_cancel_contract(c, round_num=4)
        assert ok is False
        assert "audited" in reason


# =============================================================================
# B. request_cancel() の合意集約
# =============================================================================

class TestRequestCancel:
    def test_two_party_first_consent_stays_active(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        updated, cancelled = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=5, turn=1)
        assert cancelled is False
        assert updated.status == ContractStatus.ACTIVE
        assert updated.cancel_requested_by == ["P01"]

    def test_two_party_second_consent_cancels(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        c, _ = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=5, turn=1)
        updated, cancelled = contract_ops.request_cancel(c, "P02", {"P01", "P02"}, round_num=5, turn=2)
        assert cancelled is True
        assert updated.status == ContractStatus.CANCELLED
        assert updated.cancelled_round == 5
        assert updated.cancelled_turn == 2

    def test_three_party_two_of_three_not_cancelled(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02", "P03"], [ob])
        c, cancelled1 = contract_ops.request_cancel(c, "P01", {"P01", "P02", "P03"}, round_num=5, turn=1)
        c, cancelled2 = contract_ops.request_cancel(c, "P02", {"P01", "P02", "P03"}, round_num=5, turn=1)
        assert cancelled1 is False
        assert cancelled2 is False
        assert c.status == ContractStatus.ACTIVE

    def test_three_party_third_consent_cancels(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02", "P03"], [ob])
        c, _ = contract_ops.request_cancel(c, "P01", {"P01", "P02", "P03"}, round_num=5, turn=1)
        c, _ = contract_ops.request_cancel(c, "P02", {"P01", "P02", "P03"}, round_num=5, turn=1)
        c, cancelled = contract_ops.request_cancel(c, "P03", {"P01", "P02", "P03"}, round_num=5, turn=1)
        assert cancelled is True
        assert c.status == ContractStatus.CANCELLED

    def test_non_party_raises(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        try:
            contract_ops.request_cancel(c, "P99", {"P01", "P02"}, round_num=5, turn=1)
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_double_consent_raises(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        c, _ = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=5, turn=1)
        try:
            contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=5, turn=1)
            assert False, "expected ValueError"
        except ValueError:
            pass

    def test_eliminated_party_not_required(self):
        """脱落者を除く生存2人の合意で解除成立（脱落者の同意は不要）"""
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02", "P03"], [ob])
        c, _ = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=5, turn=1)
        updated, cancelled = contract_ops.request_cancel(c, "P02", {"P01", "P02"}, round_num=5, turn=1)
        assert cancelled is True
        assert updated.status == ContractStatus.CANCELLED

    def test_cancelled_obligations_expired(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        c, _ = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=5, turn=1)
        updated, cancelled = contract_ops.request_cancel(c, "P02", {"P01", "P02"}, round_num=5, turn=1)
        assert cancelled is True
        assert updated.obligations[0].is_expired is True
        assert updated.obligations[0].is_fulfilled is False


# =============================================================================
# C. Settlement統合（解除の効果）
# =============================================================================

def _commit(pid: str, market_id: str, rank: CardRank) -> MarketCommit:
    return MarketCommit(player_id=pid, market_id=market_id, card=Card(rank=rank, card_id=f"{rank.name}_1"))


class TestSettlementIntegration:
    def test_cancelled_type_b_violation_does_not_fire(self):
        """R3型B義務の契約をR3交渉中に解除→R3 SettlementでTYPE_B_VIOLATIONが発火しない"""
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD, round_num=3,
                              details={"card_rank": "TWO_PAIR"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob], round_created=1)
        c, _ = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=3, turn=1)
        c, cancelled = contract_ops.request_cancel(c, "P02", {"P01", "P02"}, round_num=3, turn=1)
        assert cancelled is True

        players = {"P01": make_player("P01", 3_000_000), "P02": make_player("P02", 3_000_000)}
        commits = [_commit("P01", "M01", CardRank.ONE_PAIR), _commit("P02", "M01", CardRank.HIGH_CARD)]
        market = make_market("M01", 1_000_000)
        logger = EventLogger()

        players, contracts, _, _, _ = execute_settlement(
            players, [market], commits, [c], [], round_num=3,
            config=GameConfig.baseline_v1(2), logger=logger,
        )
        assert players["P01"].is_alive is True
        type_b_events = [e for e in logger.events if e.event_type == "TYPE_B_VIOLATION"]
        assert type_b_events == []

    def test_uncancelled_type_b_still_violates(self):
        """1人だけ同意（未解除）なら従来どおり違反で脱落（回帰の要）"""
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD, round_num=3,
                              details={"card_rank": "TWO_PAIR"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob], round_created=1)
        c, cancelled = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=3, turn=1)
        assert cancelled is False  # 1人だけの同意

        players = {"P01": make_player("P01", 3_000_000), "P02": make_player("P02", 3_000_000)}
        commits = [_commit("P01", "M01", CardRank.ONE_PAIR), _commit("P02", "M01", CardRank.HIGH_CARD)]
        market = make_market("M01", 1_000_000)
        logger = EventLogger()

        players, contracts, _, _, _ = execute_settlement(
            players, [market], commits, [c], [], round_num=3,
            config=GameConfig.baseline_v1(2), logger=logger,
        )
        assert players["P01"].is_alive is False
        type_b_events = [e for e in logger.events if e.event_type == "TYPE_B_VIOLATION"]
        assert len(type_b_events) == 1

    def test_cancelled_type_a_does_not_execute(self):
        """R3型A義務を解除→TYPE_A_EXECUTIONが発火せず送金も起きない"""
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=3,
                              details={"amount": 500_000})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob], round_created=1)
        c, _ = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=3, turn=1)
        c, cancelled = contract_ops.request_cancel(c, "P02", {"P01", "P02"}, round_num=3, turn=1)
        assert cancelled is True

        players = {"P01": make_player("P01", 3_000_000), "P02": make_player("P02", 3_000_000)}
        commits = [_commit("P01", "M01", CardRank.ONE_PAIR), _commit("P02", "M01", CardRank.HIGH_CARD)]
        market = make_market("M01", 1_000_000)
        logger = EventLogger()
        p02_cash_before = players["P02"].cash

        players, contracts, _, _, _ = execute_settlement(
            players, [market], commits, [c], [], round_num=3,
            config=GameConfig.baseline_v1(2), logger=logger,
        )
        type_a_events = [e for e in logger.events if e.event_type == "TYPE_A_EXECUTION"]
        assert type_a_events == []
        # P02が勝者でなければ現金は変わらない（型A送金が起きていないこと）
        winners = [e.data.get("winners", []) for e in logger.events if e.event_type == "MARKET_RESULT"]
        if not any("P02" in w for w in winners):
            assert players["P02"].cash == p02_cash_before

    def test_cancelled_excluded_from_all_selection_functions(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD, round_num=3,
                              details={"card_rank": "ONE_PAIR"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob], status=ContractStatus.CANCELLED)
        assert contract_ops.get_active_type_b_obligations([c], 3) == []
        assert contract_ops.get_active_type_a_obligations([c], 3) == []
        assert contract_ops.get_all_type_b_for_player([c], "P01", 3) == []

    def test_p11_reproduction_scenario(self):
        """P11再現: 同一R・同一市場にTWO_PAIR/ONE_PAIRの2契約→旧契約を全員合意で解除→脱落しない"""
        ob_old = make_obligation("COLD_OB01", "C_old", "P01", "P02", ObligationType.TYPE_B_CARD, round_num=3,
                                  details={"card_rank": "TWO_PAIR"})
        c_old = make_contract("C_old", "P01", ["P01", "P02"], [ob_old], round_created=1)
        ob_new = make_obligation("CNEW_OB01", "C_new", "P01", "P02", ObligationType.TYPE_B_CARD, round_num=3,
                                  details={"card_rank": "ONE_PAIR"})
        c_new = make_contract("C_new", "P01", ["P01", "P02"], [ob_new], round_created=3)

        c_old, _ = contract_ops.request_cancel(c_old, "P01", {"P01", "P02"}, round_num=3, turn=1)
        c_old, cancelled = contract_ops.request_cancel(c_old, "P02", {"P01", "P02"}, round_num=3, turn=1)
        assert cancelled is True

        players = {"P01": make_player("P01", 3_000_000), "P02": make_player("P02", 3_000_000)}
        commits = [_commit("P01", "M01", CardRank.ONE_PAIR), _commit("P02", "M01", CardRank.HIGH_CARD)]
        market = make_market("M01", 1_000_000)
        logger = EventLogger()

        players, contracts, _, _, _ = execute_settlement(
            players, [market], commits, [c_old, c_new], [], round_num=3,
            config=GameConfig.baseline_v1(2), logger=logger,
        )
        assert players["P01"].is_alive is True
        assert [e for e in logger.events if e.event_type == "TYPE_B_VIOLATION"] == []


# =============================================================================
# D. 提案後・最終同意前に履行が発生（要件3）
# =============================================================================

class TestReCheckAtFinalConsent:
    def test_second_consent_after_settlement_passed_rejected(self):
        """R3にP1が同意→R3 Settlement通過→R4にP2が同意 → 不成立"""
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=3)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        c, cancelled = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=3, turn=1)
        assert cancelled is False

        # R4時点で最終同意前に再チェック（validate_actionが行う）
        ok, reason = contract_ops.can_cancel_contract(c, round_num=4)
        assert ok is False
        assert "audited" in reason

    def test_contract_stays_active_not_silently_removed(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=3)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        c, _ = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=3, turn=1)
        assert c.status == ContractStatus.ACTIVE


# =============================================================================
# E. validate_action() の検証
# =============================================================================

class TestValidateActionContractCancel:
    def _setup(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        contract = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {"P01": make_player("P01", 3_000_000), "P02": make_player("P02", 3_000_000)}
        return contract, players

    def test_not_found(self):
        contract, players = self._setup()
        action = ContractCancelAction(player_id="P01", contract_id="C_missing")
        result = action_ops.validate_action(
            action, players["P01"], GameConfig.baseline_v1(2), players, round_num=5, contracts=[contract],
        )
        assert result.success is False
        assert "not found" in result.reason

    def test_no_contracts_passed(self):
        contract, players = self._setup()
        action = ContractCancelAction(player_id="P01", contract_id="C1")
        result = action_ops.validate_action(
            action, players["P01"], GameConfig.baseline_v1(2), players, round_num=5,
        )
        assert result.success is False

    def test_non_party(self):
        contract, players = self._setup()
        players["P03"] = make_player("P03", 1_000_000)
        action = ContractCancelAction(player_id="P03", contract_id="C1")
        result = action_ops.validate_action(
            action, players["P03"], GameConfig.baseline_v1(2), players, round_num=5, contracts=[contract],
        )
        assert result.success is False
        assert "not a party" in result.reason

    def test_already_cancelled(self):
        contract, players = self._setup()
        cancelled_contract = contract.model_copy(update={"status": ContractStatus.CANCELLED})
        action = ContractCancelAction(player_id="P01", contract_id="C1")
        result = action_ops.validate_action(
            action, players["P01"], GameConfig.baseline_v1(2), players, round_num=5,
            contracts=[cancelled_contract],
        )
        assert result.success is False
        assert "already cancelled" in result.reason

    def test_proposed_not_active(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        contract = make_contract("C1", "P01", ["P01", "P02"], [ob], status=ContractStatus.PROPOSED)
        players = {"P01": make_player("P01", 3_000_000), "P02": make_player("P02", 3_000_000)}
        action = ContractCancelAction(player_id="P01", contract_id="C1")
        result = action_ops.validate_action(
            action, players["P01"], GameConfig.baseline_v1(2), players, round_num=5, contracts=[contract],
        )
        assert result.success is False
        assert "not active" in result.reason

    def test_fulfilled_obligation(self):
        ob = Obligation(
            obligation_id="C1_OB01", contract_id="C1", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5, details={"amount": 1},
            is_fulfilled=True,
        )
        contract = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {"P01": make_player("P01", 3_000_000), "P02": make_player("P02", 3_000_000)}
        action = ContractCancelAction(player_id="P01", contract_id="C1")
        result = action_ops.validate_action(
            action, players["P01"], GameConfig.baseline_v1(2), players, round_num=6, contracts=[contract],
        )
        assert result.success is False
        assert "fulfilled" in result.reason

    def test_expired_obligation(self):
        ob = Obligation(
            obligation_id="C1_OB01", contract_id="C1", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_B_MARKET, round_num=5, details={"market_id": "M01"},
            is_expired=True,
        )
        contract = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {"P01": make_player("P01", 3_000_000), "P02": make_player("P02", 3_000_000)}
        action = ContractCancelAction(player_id="P01", contract_id="C1")
        result = action_ops.validate_action(
            action, players["P01"], GameConfig.baseline_v1(2), players, round_num=6, contracts=[contract],
        )
        assert result.success is False
        assert "expired" in result.reason

    def test_double_consent(self):
        contract, players = self._setup()
        contract = contract.model_copy(update={"cancel_requested_by": ["P01"]})
        action = ContractCancelAction(player_id="P01", contract_id="C1")
        result = action_ops.validate_action(
            action, players["P01"], GameConfig.baseline_v1(2), players, round_num=5, contracts=[contract],
        )
        assert result.success is False
        assert "already requested" in result.reason

    def test_success(self):
        contract, players = self._setup()
        action = ContractCancelAction(player_id="P01", contract_id="C1")
        result = action_ops.validate_action(
            action, players["P01"], GameConfig.baseline_v1(2), players, round_num=5, contracts=[contract],
        )
        assert result.success is True
        assert result.consumes_action is True

    def test_backward_compat_without_contracts_kwarg_for_other_actions(self):
        """契約解除以外のアクションはcontracts引数省略でも従来どおり動く（既存呼び出し回帰）"""
        from engine.models import PassAction
        players = {"P01": make_player("P01", 3_000_000)}
        result = action_ops.validate_action(
            PassAction(player_id="P01"), players["P01"], GameConfig.baseline_v1(1), players, round_num=1,
        )
        assert result.success is True
        assert result.consumes_action is False


# =============================================================================
# F. _build_visible_state() の可視化
# =============================================================================

class TestVisibleStateCancellation:
    def _cancelled_contract(self) -> Contract:
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=3)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob], round_created=1)
        c, _ = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=3, turn=1)
        c, cancelled = contract_ops.request_cancel(c, "P02", {"P01", "P02"}, round_num=3, turn=2)
        assert cancelled is True
        return c

    def test_my_contracts_shows_cancelled_status(self):
        game = _make_game()
        game.contracts = [self._cancelled_contract()]
        state = game._build_visible_state(3, for_player_id="P01")
        entry = state["my_contracts"][0]
        assert entry["status"] == "cancelled"
        assert entry["cancelled_round"] == 3

    def test_my_obligations_empty_for_cancelled(self):
        game = _make_game()
        game.contracts = [self._cancelled_contract()]
        state = game._build_visible_state(3, for_player_id="P01")
        assert state["my_obligations"] == []

    def test_contracts_public_shows_cancelled(self):
        game = _make_game()
        game.contracts = [self._cancelled_contract()]
        state = game._build_visible_state(3, for_player_id="P03")
        pub = [c for c in state["contracts_public"] if c["contract_id"] == "C1"]
        assert len(pub) == 1
        assert pub[0]["status"] == "cancelled"

    def test_partial_consent_stays_active_with_requested_by(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=5)
        c = make_contract("C1", "P01", ["P01", "P02"], [ob], round_created=1)
        c, cancelled = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=3, turn=1)
        assert cancelled is False
        game = _make_game()
        game.contracts = [c]
        state = game._build_visible_state(3, for_player_id="P01")
        entry = state["my_contracts"][0]
        assert entry["status"] == "active"
        assert entry["cancel_requested_by"] == ["P01"]

    def test_spectator_state_has_no_my_contracts_key(self):
        game = _make_game()
        game.contracts = [self._cancelled_contract()]
        state = game._build_visible_state(3, for_player_id=None)
        assert "my_contracts" not in state


# =============================================================================
# G. パーサ・プロンプト
# =============================================================================

class TestParserAndPrompt:
    def test_parses_contract_cancel(self):
        text = '{"strategy": {"emotion": "平静"}, "action": {"type": "contract_cancel", "contract_id": "C_x"}}'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert isinstance(action, ContractCancelAction)
        assert action.contract_id == "C_x"
        assert action.player_id == "P01"

    def test_missing_contract_id_raises(self):
        text = '{"strategy": {"emotion": "平静"}, "action": {"type": "contract_cancel"}}'
        try:
            parse_response(text, "P01", "negotiation")
            assert False, "expected ParseError"
        except ParseError:
            pass

    def test_action_catalog_mentions_contract_cancel(self):
        config = GameConfig.baseline_v1(3)
        prompt = build_system_prompt("P01", config)
        assert "contract_cancel" in prompt

    def test_render_shows_cancelled_and_partial_consent(self):
        visible_state = {
            "my_contracts": [
                {
                    "contract_id": "C1", "parties": ["P01", "P02"],
                    "round_created": 1, "status": "cancelled",
                    "cancelled_round": 3, "cancel_requested_by": ["P01", "P02"],
                    "eliminated_parties": [],
                    "obligations": [{
                        "obligation_id": "C1_OB01", "obligor": "P01", "counterparty": "P02",
                        "ob_type": "type_a_payment", "round_num": 3,
                        "details": {"amount": 500_000}, "ob_status": "cancelled",
                    }],
                },
                {
                    "contract_id": "C2", "parties": ["P01", "P03"],
                    "round_created": 1, "status": "active",
                    "cancelled_round": None, "cancel_requested_by": ["P01"],
                    "eliminated_parties": [],
                    "obligations": [{
                        "obligation_id": "C2_OB01", "obligor": "P01", "counterparty": "P03",
                        "ob_type": "type_a_payment", "round_num": 9,
                        "details": {"amount": 300_000}, "ob_status": "upcoming",
                    }],
                },
            ],
        }
        lines = _render_my_contracts_block(visible_state, round_num=3)
        text = "\n".join(lines)
        assert "🚫 解除済み（R3）" in text
        assert "全当事者合意で解除されました" in text
        assert "⏳ 解除同意" in text


# =============================================================================
# H. post-game台帳
# =============================================================================

class TestPostGameLedger:
    def test_cancelled_shows_as_removed_not_fulfilled(self):
        game = _make_game(4)
        game.current_round = 3
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=2,
                              details={"amount": 500_000})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob], round_created=1)
        c, _ = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=2, turn=1)
        c, cancelled = contract_ops.request_cancel(c, "P02", {"P01", "P02"}, round_num=2, turn=1)
        assert cancelled is True
        game.contracts = [c]

        result = game._finalize()
        shared = game._build_god_shared_block(result)
        assert len(shared["contract_ledger"]) == 1
        line = shared["contract_ledger"][0]
        assert "－解除(R2)" in line
        assert "✓履行" not in line

    def test_cancelled_not_in_violation_ledger(self):
        game = _make_game(4)
        game.current_round = 3
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT, round_num=2,
                              details={"amount": 500_000})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob], round_created=1)
        c, _ = contract_ops.request_cancel(c, "P01", {"P01", "P02"}, round_num=2, turn=1)
        c, cancelled = contract_ops.request_cancel(c, "P02", {"P01", "P02"}, round_num=2, turn=1)
        assert cancelled is True
        game.contracts = [c]

        result = game._finalize()
        shared = game._build_god_shared_block(result)
        assert shared["violation_ledger"] == []


# =============================================================================
# I. AUTO_PASS修正: 契約解除通知（2026-08-23）
#
# 背景: contract_cancel は _round_messages を生成しないため、相手当事者の
# LLMAgent が AUTO_PASS_ON_NO_NEWS（新規メッセージ/新規失敗が無ければturn>=2で
# 自動pass）により一度もAPIを呼ばれないまま起床せず、全会一致に永久に到達しない
# 事故が実測された。本節は当事者限定の内部通知チャネル my_contract_notices が
# (1) 正しい相手だけに届き (2) 第三者・要求者本人・脱落者には届かず (3) 起床
# トリガとして機能し、かつ (4) 従来のAUTO_PASS省コスト挙動を壊していないことを
# 検証する。engine判定ロジック（can_cancel_contract/request_cancel）は無変更。
# =============================================================================

def _seed_contract(game: Game, contract_id: str = "C1",
                    parties: tuple[str, str] = ("P01", "P02"),
                    ob_round: int = 3) -> Contract:
    """型A義務(未到来R)を1件持つACTIVE契約をgameに直接セットするヘルパー"""
    ob = make_obligation(f"{contract_id}_OB01", contract_id, parties[0], parties[1],
                          ObligationType.TYPE_A_PAYMENT, round_num=ob_round,
                          details={"amount": 500_000})
    c = make_contract(contract_id, parties[0], list(parties), [ob], round_created=1)
    game.contracts.append(c)
    return c


class TestContractCancelNotices:
    """_contract_notices / my_contract_notices の配送規則"""

    def test_notice_delivered_to_counterparty_on_partial_request(self):
        game = _make_game(4)
        _seed_contract(game)
        game._execute_negotiation_action(
            ContractCancelAction(player_id="P02", contract_id="C1"), "P02", round_num=2, turn=2,
        )
        state = game._build_visible_state(2, for_player_id="P01")
        notices = state["my_contract_notices"]
        assert len(notices) == 1
        assert notices[0]["kind"] == "cancel_requested"
        assert notices[0]["by"] == "P02"
        assert notices[0]["contract_id"] == "C1"

    def test_no_notice_to_requester_self(self):
        game = _make_game(4)
        _seed_contract(game)
        game._execute_negotiation_action(
            ContractCancelAction(player_id="P02", contract_id="C1"), "P02", round_num=2, turn=2,
        )
        state = game._build_visible_state(2, for_player_id="P02")
        assert state["my_contract_notices"] == []

    def test_no_notice_to_third_party(self):
        """非当事者(P03)には通知が一切届かない（秘匿境界）"""
        game = _make_game(4)
        _seed_contract(game)
        game._execute_negotiation_action(
            ContractCancelAction(player_id="P02", contract_id="C1"), "P02", round_num=2, turn=2,
        )
        state = game._build_visible_state(2, for_player_id="P03")
        assert state["my_contract_notices"] == []

    def test_cancel_completed_notice_on_unanimous_consent(self):
        game = _make_game(4)
        _seed_contract(game)
        game._execute_negotiation_action(
            ContractCancelAction(player_id="P01", contract_id="C1"), "P01", round_num=2, turn=1,
        )
        game._execute_negotiation_action(
            ContractCancelAction(player_id="P02", contract_id="C1"), "P02", round_num=2, turn=2,
        )
        state_p01 = game._build_visible_state(2, for_player_id="P01")
        kinds = [n["kind"] for n in state_p01["my_contract_notices"]]
        assert "cancel_completed" in kinds

    def test_no_notice_to_eliminated_party(self):
        game = _make_game(4)
        _seed_contract(game)
        _eliminate(game, "P01", round_num=1)
        game._execute_negotiation_action(
            ContractCancelAction(player_id="P02", contract_id="C1"), "P02", round_num=2, turn=2,
        )
        state = game._build_visible_state(2, for_player_id="P01")
        assert state["my_contract_notices"] == []

    def test_notices_not_leaked_to_messages_or_public_or_god_transcript(self):
        game = _make_game(4)
        _seed_contract(game)
        game._execute_negotiation_action(
            ContractCancelAction(player_id="P02", contract_id="C1"), "P02", round_num=2, turn=2,
        )
        state = game._build_visible_state(2, for_player_id="P01")
        assert state.get("messages", []) == []
        for c in state["contracts_public"]:
            assert "my_contract_notices" not in c
            assert "cancel_requested_by" not in c  # 公開台帳には出さない（当事者限定情報）
        assert game._god_transcript == []

    def test_notice_buffer_capped_at_max(self):
        game = _make_game(4)
        for i in range(20):
            cid = f"C{i}"
            _seed_contract(game, contract_id=cid)
            game._execute_negotiation_action(
                ContractCancelAction(player_id="P02", contract_id=cid), "P02", round_num=2, turn=i + 1,
            )
        state = game._build_visible_state(2, for_player_id="P01")
        assert len(state["my_contract_notices"]) <= game._CONTRACT_NOTICE_MAX


class TestRenderContractNoticeBlock:
    """_render_contract_notice_block() の描画内容"""

    def test_empty_when_no_notices(self):
        assert _render_contract_notice_block({"my_contract_notices": []}) == []
        assert _render_contract_notice_block({}) == []

    def test_renders_cancel_requested(self):
        state = {"my_contract_notices": [{
            "turn": 2, "kind": "cancel_requested", "contract_id": "C_ab12",
            "by": "P02", "cancel_requested_by": ["P02"], "pending": ["P01"],
        }]}
        text = "\n".join(_render_contract_notice_block(state))
        assert "P02" in text
        assert "C_ab12" in text
        assert "contract_cancel" in text
        assert "解除に同意" in text

    def test_renders_cancel_completed(self):
        state = {"my_contract_notices": [{
            "turn": 3, "kind": "cancel_completed", "contract_id": "C_ab12",
            "by": "P02", "cancel_requested_by": ["P02", "P01"], "pending": [],
        }]}
        text = "\n".join(_render_contract_notice_block(state))
        assert "C_ab12" in text
        assert "解除されました" in text


class _CountingAdapter:
    """呼び出し回数だけを数える偽アダプタ（実API送信なし・test_llm.py のMockAdapterと同型）"""

    def __init__(self, responses: list[str] | None = None):
        self._responses = responses or ['{"strategy":{},"action":{"type":"pass"}}']
        self.call_count = 0

    def complete(self, system, messages, max_tokens, temperature):
        idx = min(self.call_count, len(self._responses) - 1)
        text = self._responses[idx]
        self.call_count += 1
        return text, {"input_tokens": 100, "output_tokens": 50}


class TestAutoPassWakeOnNotice:
    """AUTO_PASS_ON_NO_NEWS の第3起床トリガ（my_contract_notices）"""

    def _make_agent(self, responses=None) -> tuple[LLMAgent, _CountingAdapter]:
        model_info = ModelInfo(
            model_id="test-model", provider="Test", name="Test",
            adapter_type="anthropic",
            input_price=1.0, output_price=5.0,
            env_key="TEST_KEY", base_url=None,
        )
        adapter = _CountingAdapter(responses)
        logger = LLMLogger("/tmp/test_llm_notice_logs", game_id="test")
        agent = LLMAgent("P01", model_info, adapter, logger)
        config = GameConfig.baseline_v1()
        agent.choose_loan(config)  # このコールでcall_countが1進む
        adapter.call_count = 0  # 以降のnegotiate呼び出しだけを数えるためリセット
        return agent, adapter

    def test_wakes_on_new_notice_even_without_new_message(self):
        """新規メッセージ・新規失敗が無くても、新規契約通知があればAPIが呼ばれる"""
        agent, adapter = self._make_agent()
        base_state = {"markets": [], "messages": [], "alive_players": ["P01", "P02"],
                      "my_failed_actions": [], "my_contract_notices": []}
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        # turn1: 状態を確定させる
        agent.negotiate(p, 1, 1, dict(base_state))
        assert adapter.call_count == 1
        # turn2: 新規メッセージ・新規失敗なし → 通知ありなのでAUTO_PASSされない
        state_with_notice = dict(base_state)
        state_with_notice["my_contract_notices"] = [{
            "turn": 2, "kind": "cancel_requested", "contract_id": "C1",
            "by": "P02", "cancel_requested_by": ["P02"], "pending": ["P01"],
        }]
        action = agent.negotiate(p, 1, 2, state_with_notice)
        assert adapter.call_count == 2  # APIが実際に呼ばれた
        assert isinstance(action, PassAction)  # MockレスポンスはPass（内容自体は無関係）

    def test_still_auto_passes_without_new_notice(self):
        """通知が無いturnは従来どおりAUTO_PASSする（省コスト機構の回帰ガード）"""
        agent, adapter = self._make_agent()
        base_state = {"markets": [], "messages": [], "alive_players": ["P01", "P02"],
                      "my_failed_actions": [], "my_contract_notices": []}
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        agent.negotiate(p, 1, 1, dict(base_state))
        assert adapter.call_count == 1
        # turn2: 新規メッセージ・新規失敗・新規通知いずれも無し → AUTO_PASS
        action = agent.negotiate(p, 1, 2, dict(base_state))
        assert adapter.call_count == 1  # APIは呼ばれていない
        assert isinstance(action, PassAction)

    def test_no_repeat_wake_on_same_notice_count(self):
        """件数が変わらない同一通知が連続turnで再送されても2度目は起床しない"""
        agent, adapter = self._make_agent()
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        notice = [{"turn": 2, "kind": "cancel_requested", "contract_id": "C1",
                   "by": "P02", "cancel_requested_by": ["P02"], "pending": ["P01"]}]
        state = {"markets": [], "messages": [], "alive_players": ["P01", "P02"],
                 "my_failed_actions": [], "my_contract_notices": notice}
        agent.negotiate(p, 1, 1, dict(state))
        assert adapter.call_count == 1
        agent.negotiate(p, 1, 2, dict(state))  # 件数不変(1件のまま) → 起床トリガなし
        assert adapter.call_count == 1
        agent.negotiate(p, 1, 3, dict(state))
        assert adapter.call_count == 1
