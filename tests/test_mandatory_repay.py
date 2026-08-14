"""
強制返済（v0.7 §2）のテスト

- compute_mandatory_repayment() の計算検証
- Finance フェイズでの強制返済実行
- 返済不能時の破産脱落
- mandatory_repay_enabled=False でのS1互換性
"""

import math
import pytest

from engine.config import GameConfig
from engine.models import PlayerState
from engine.events import EventLogger
from engine import player as player_ops
from engine import finance as finance_ops
from engine.game import Game
from engine.negotiation import StubAgent

from tests.conftest import make_player


class TestComputeMandatoryRepayment:
    """compute_mandatory_repayment の計算検証"""

    def test_spec_example_r1(self):
        """仕様書§2.3の計算例: 借入300万, 利率1.5%, k=0, R1"""
        # R1終了時: debt = 3,000,000 * 1.015 = 3,045,000
        debt_after_interest = 3_045_000
        remaining = 12  # R1終了時
        k = 0
        result = player_ops.compute_mandatory_repayment(debt_after_interest, remaining, k)
        assert result == 253_750  # ceil(3,045,000 / 12)

    def test_spec_example_r2(self):
        """仕様書§2.3の計算例: R2"""
        # R1返済後: 3,045,000 - 253,750 = 2,791,250
        # R2利息: 2,791,250 * 1.015 = 2,833,118.75 → ceil = 2,833,119
        debt_after_interest = 2_833_119
        remaining = 11  # R2終了時
        result = player_ops.compute_mandatory_repayment(debt_after_interest, remaining, 0)
        assert result == 257_557  # ceil(2,833,119 / 11)

    def test_k0_uniform(self):
        """k=0: 完全均等返済"""
        result = player_ops.compute_mandatory_repayment(1_200_000, 12, 0)
        assert result == 100_000  # ceil(1,200,000 / 12) = 100,000

    def test_k1_lighter_early(self):
        """k=1: 序盤が軽くなる"""
        result_k0 = player_ops.compute_mandatory_repayment(1_200_000, 12, 0)
        result_k1 = player_ops.compute_mandatory_repayment(1_200_000, 12, 1)
        assert result_k1 < result_k0  # k=1 のほうが序盤は軽い
        assert result_k1 == math.ceil(1_200_000 / 13)

    def test_k2(self):
        """k=2: さらに軽い"""
        result = player_ops.compute_mandatory_repayment(1_200_000, 12, 2)
        assert result == math.ceil(1_200_000 / 14)

    def test_r12_full_repayment(self):
        """R12: remaining=1, k=0 → 全額返済"""
        result = player_ops.compute_mandatory_repayment(500_000, 1, 0)
        assert result == 500_000

    def test_zero_debt(self):
        """借金0 → 返済額0"""
        result = player_ops.compute_mandatory_repayment(0, 12, 0)
        assert result == 0

    def test_divisor_zero_or_negative(self):
        """divisor ≤ 0 → 全額返済"""
        result = player_ops.compute_mandatory_repayment(500_000, 0, 0)
        assert result == 500_000


class TestMandatoryRepayFinance:
    """Finance フェイズでの強制返済テスト"""

    def test_successful_repayment(self):
        """強制返済成功: 残高が減少する"""
        p = make_player("P01", cash=5_000_000, debt=3_000_000)
        players = {"P01": p}
        config = GameConfig.baseline_v1(8)
        config = config.model_copy(update={"mandatory_repay_enabled": True, "mandatory_repay_k": 0})
        logger = EventLogger()

        players, _ = finance_ops.execute_finance(
            players, round_num=1, config=config, contracts=[], logger=logger,
        )

        p_after = players["P01"]
        assert p_after.is_alive
        # 利息計上: 3,000,000 * 1.015 = 3,045,000
        # 返済額: ceil(3,045,000 / 12) = 253,750
        assert p_after.debt_balance == 3_045_000 - 253_750
        assert p_after.cash == 5_000_000 - 253_750

        # MANDATORY_REPAY イベントが発行されている
        repay_events = [e for e in logger.events if e.event_type == "MANDATORY_REPAY"]
        assert len(repay_events) == 1
        assert repay_events[0].data["amount"] == 253_750

    def test_bankruptcy_on_insufficient_cash(self):
        """返済不能 → 破産脱落"""
        # 現金100万で借金300万 → 利息後 3,045,000, 最低返済 253,750 → 100万 < 253,750... wait
        # Actually 100万 > 253,750. Let's use 200,000 cash.
        p = make_player("P01", cash=200_000, debt=3_000_000, loan=3_000_000)
        players = {"P01": p}
        config = GameConfig.baseline_v1(8)
        config = config.model_copy(update={"mandatory_repay_enabled": True, "mandatory_repay_k": 0})
        logger = EventLogger()

        players, _ = finance_ops.execute_finance(
            players, round_num=1, config=config, contracts=[], logger=logger,
        )

        p_after = players["P01"]
        assert not p_after.is_alive
        assert p_after.elimination_reason == "bankruptcy"

        # MANDATORY_REPAY_FAILED イベント
        fail_events = [e for e in logger.events if e.event_type == "MANDATORY_REPAY_FAILED"]
        assert len(fail_events) == 1

    def test_disabled_no_repayment(self):
        """mandatory_repay_enabled=False → 強制返済なし"""
        p = make_player("P01", cash=5_000_000, debt=3_000_000)
        players = {"P01": p}
        config = GameConfig.baseline_v1(8)  # enabled=False by default
        logger = EventLogger()

        players, _ = finance_ops.execute_finance(
            players, round_num=1, config=config, contracts=[], logger=logger,
        )

        p_after = players["P01"]
        # 利息のみ計上、強制返済なし
        assert p_after.debt_balance == math.ceil(3_000_000 * 1.015)
        assert p_after.cash == 5_000_000  # 現金変化なし

        repay_events = [e for e in logger.events if e.event_type == "MANDATORY_REPAY"]
        assert len(repay_events) == 0

    def test_s1_game_completes_unchanged(self):
        """S1設定でゲーム完走: 強制返済が発生しないこと"""
        config = GameConfig.baseline_v1(8)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        result = game.run()

        # 強制返済イベントがゼロ
        repay_events = [e for e in logger.events if e.event_type in ("MANDATORY_REPAY", "MANDATORY_REPAY_FAILED")]
        assert len(repay_events) == 0

        # ゲームは12ラウンド完走
        assert result.round_count == 12
