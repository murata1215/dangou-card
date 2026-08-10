"""
義務失効テスト

§6.3の義務単位での失効ルールを検証する。
"""

import pytest
from engine.models import Obligation, ObligationType, ContractStatus
from engine import elimination as elim_ops

from tests.conftest import make_obligation, make_contract


class TestObligationExpiry:
    """義務失効のテスト"""

    def test_obligor_eliminated_expires(self):
        """義務者が脱落 → その義務は失効"""
        ob = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 500_000},
        )
        contract = make_contract("C01", "P01", ["P01", "P02"], obligations=[ob])

        updated = elim_ops.expire_obligations_for_player("P01", [contract])
        assert updated[0].obligations[0].is_expired

    def test_counterparty_eliminated_expires(self):
        """相手方が脱落 → その義務は失効（貸し倒れ）"""
        ob = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 500_000},
        )
        contract = make_contract("C01", "P01", ["P01", "P02"], obligations=[ob])

        updated = elim_ops.expire_obligations_for_player("P02", [contract])
        assert updated[0].obligations[0].is_expired

    def test_unrelated_player_no_effect(self):
        """無関係なプレイヤーの脱落は義務に影響しない"""
        ob = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 500_000},
        )
        contract = make_contract("C01", "P01", ["P01", "P02"], obligations=[ob])

        updated = elim_ops.expire_obligations_for_player("P03", [contract])
        assert not updated[0].obligations[0].is_expired

    def test_fulfilled_obligation_not_expired(self):
        """履行済み義務は失効しない"""
        ob = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 500_000},
        )
        ob = ob.model_copy(update={"is_fulfilled": True})
        contract = make_contract("C01", "P01", ["P01", "P02"], obligations=[ob])

        updated = elim_ops.expire_obligations_for_player("P01", [contract])
        # 履行済みなので失効フラグは変更されない
        assert not updated[0].obligations[0].is_expired

    def test_three_party_partial_expiry(self):
        """3者契約: 脱落者に関わる義務のみ失効、他は継続"""
        ob1 = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 500_000},
        )
        ob2 = make_obligation(
            "OB02", "C01", obligor="P02", counterparty="P03",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 300_000},
        )
        ob3 = make_obligation(
            "OB03", "C01", obligor="P03", counterparty="P01",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 200_000},
        )
        contract = make_contract(
            "C01", "P01", ["P01", "P02", "P03"],
            obligations=[ob1, ob2, ob3],
        )

        # P03が脱落
        updated = elim_ops.expire_obligations_for_player("P03", [contract])
        c = updated[0]

        # OB01 (P01→P02): P03無関係 → 継続
        assert not c.obligations[0].is_expired
        # OB02 (P02→P03): P03が相手方 → 失効
        assert c.obligations[1].is_expired
        # OB03 (P03→P01): P03が義務者 → 失効
        assert c.obligations[2].is_expired

    def test_type_b_obligation_also_expires(self):
        """型B義務も脱落時に失効する"""
        ob = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_B_MARKET, round_num=5,
            details={"market_id": "M01"},
        )
        contract = make_contract("C01", "P02", ["P01", "P02"], obligations=[ob])

        updated = elim_ops.expire_obligations_for_player("P01", [contract])
        assert updated[0].obligations[0].is_expired
