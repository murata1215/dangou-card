"""
FreeCash境界値テスト

§2.4のFreeCash計算と、適用/非適用の区分を検証する。
"""

import pytest
from engine.models import TransferAction, RepayAction, BountyPostAction
from engine.config import GameConfig
from engine import actions as action_ops

from tests.conftest import make_player


class TestFreeCash:
    """FreeCashの計算と制限のテスト"""

    def test_free_cash_calculation(self):
        """FreeCash = max(0, Cash - Debt)"""
        # Cash > Debt → FreeCash = Cash - Debt
        p = make_player("P01", cash=5_000_000, debt=2_000_000)
        assert p.free_cash == 3_000_000

        # Cash = Debt → FreeCash = 0
        p = make_player("P01", cash=1_200_000, debt=1_200_000)
        assert p.free_cash == 0

        # Cash < Debt → FreeCash = 0 (max(0, ...)で0にクランプ)
        p = make_player("P01", cash=500_000, debt=1_200_000)
        assert p.free_cash == 0

    def test_free_cash_zero_at_start(self):
        """ゲーム開始時は全員FreeCash=0（Cash=Debt=借入額）"""
        for loan in [1_200_000, 5_000_000, 10_000_000]:
            p = make_player("P01", cash=loan, debt=loan)
            assert p.free_cash == 0

    def test_transfer_exactly_free_cash(self):
        """FreeCashぴったりの送金は成功する"""
        config = GameConfig.default_20()
        p01 = make_player("P01", cash=5_000_000, debt=2_000_000)  # FC=300万
        p02 = make_player("P02", cash=1_000_000, debt=0)
        players = {"P01": p01, "P02": p02}

        action = TransferAction(player_id="P01", to="P02", amount=3_000_000)
        result = action_ops.validate_action(action, p01, config, players, round_num=1)
        assert result.success

    def test_transfer_one_over_free_cash(self):
        """FreeCash+1円の送金は失敗する"""
        config = GameConfig.default_20()
        p01 = make_player("P01", cash=5_000_000, debt=2_000_000)  # FC=300万
        p02 = make_player("P02", cash=1_000_000, debt=0)
        players = {"P01": p01, "P02": p02}

        action = TransferAction(player_id="P01", to="P02", amount=3_000_001)
        result = action_ops.validate_action(action, p01, config, players, round_num=1)
        assert not result.success

    def test_repay_not_free_cash_limited(self):
        """返済はFreeCash制限の対象外（§2.4）"""
        config = GameConfig.default_20()
        # Cash=200万, Debt=150万 → FC=50万
        # 150万の返済はFC超えだが、Cash以内なのでOK
        p01 = make_player("P01", cash=2_000_000, debt=1_500_000)
        players = {"P01": p01}

        action = RepayAction(player_id="P01", amount=1_500_000)
        result = action_ops.validate_action(action, p01, config, players, round_num=1)
        assert result.success

    def test_bounty_free_cash_limited(self):
        """報奨預託はFreeCash制限の対象（§7.2）"""
        config = GameConfig.default_20()
        # FC=10万、報奨額20万 → 不成立
        p01 = make_player("P01", cash=1_300_000, debt=1_200_000)  # FC=10万
        players = {"P01": p01}

        action = BountyPostAction(
            player_id="P01", amount=200_000,
            bounty_type="achievement",
            condition_type="market_win_against",
            condition={"target_player": "P02"},
            round_num=1,
        )
        result = action_ops.validate_action(action, p01, config, players, round_num=1)
        assert not result.success
