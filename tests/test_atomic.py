"""
Atomic判定テスト

§6.6の型A契約Atomic執行の詳細仕様を検証する。
"""

import pytest
from engine.models import Obligation, ObligationType
from engine import contracts as contract_ops

from tests.conftest import make_obligation, make_contract


class TestAtomicExecution:
    """型A Atomic執行のテスト"""

    def test_single_obligation_success(self):
        """単一義務、FreeCash以内 → 成功"""
        ob = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 500_000},
        )
        snapshots = {"P01": {"cash": 1_000_000, "free_cash": 800_000}}

        _, failed, payments = contract_ops.execute_type_a_atomic(
            [ob], snapshots,
        )

        assert len(failed) == 0
        assert payments["P01"] == -500_000
        assert payments["P02"] == 500_000

    def test_single_obligation_failure(self):
        """単一義務、FreeCash不足 → 脱落"""
        ob = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 1_000_000},
        )
        snapshots = {"P01": {"cash": 800_000, "free_cash": 500_000}}

        _, failed, payments = contract_ops.execute_type_a_atomic(
            [ob], snapshots,
        )

        assert "P01" in failed
        # 支払いは行われない
        assert payments.get("P02", 0) == 0

    def test_multiple_obligors_independent(self):
        """複数義務者は独立して判定される"""
        ob1 = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P03",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 300_000},
        )
        ob2 = make_obligation(
            "OB02", "C02", obligor="P02", counterparty="P03",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 500_000},
        )

        snapshots = {
            "P01": {"cash": 1_000_000, "free_cash": 500_000},  # 300万 <= 500万 OK
            "P02": {"cash": 500_000, "free_cash": 300_000},   # 500万 > 300万 NG
        }

        _, failed, payments = contract_ops.execute_type_a_atomic(
            [ob1, ob2], snapshots,
        )

        # P01は成功、P02は失敗
        assert "P01" not in failed
        assert "P02" in failed
        assert payments["P01"] == -300_000
        assert payments["P03"] == 300_000  # P01からの分のみ

    def test_atomic_all_or_nothing(self):
        """同一義務者の複数義務は全額合算してAtomic判定"""
        ob1 = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 400_000},
        )
        ob2 = make_obligation(
            "OB02", "C02", obligor="P01", counterparty="P03",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 400_000},
        )

        # FC=700_000 で 合計800_000 → 不足
        snapshots = {"P01": {"cash": 1_000_000, "free_cash": 700_000}}

        _, failed, _ = contract_ops.execute_type_a_atomic(
            [ob1, ob2], snapshots,
        )

        assert "P01" in failed

    def test_excluded_players_not_executed(self):
        """除外プレイヤー（Step 3で脱落確定）の義務は取得されない"""
        ob1 = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 100_000},
        )
        ob2 = make_obligation(
            "OB02", "C02", obligor="P03", counterparty="P01",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 200_000},
        )
        c1 = make_contract("C01", "P01", ["P01", "P02"], obligations=[ob1])
        c2 = make_contract("C02", "P03", ["P03", "P01"], obligations=[ob2])

        # P01がStep 3で脱落確定
        excluded = {"P01"}
        result = contract_ops.get_active_type_a_obligations(
            [c1, c2], round_num=5, excluded_players=excluded,
        )

        # P01が義務者/相手方の義務はすべて除外
        assert len(result) == 0
