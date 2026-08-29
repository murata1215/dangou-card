"""
Cycle 8.1 v0.8 E3: 倍掛けの即死ガード（D3・I4）のテスト

`_process_double_up()` が、預託すると強制最低返済を賄えなくなる/現金が
足りない選択をLLM（Agent）に一切提示しない（`choose_double_up` を呼ばない）
ことを検証する。式は `engine/finance.py` Step1(利息計上)/Step2(強制最低返済)
と同一（`player_ops.apply_interest` / `player_ops.compute_mandatory_repayment`）。
"""

from unittest.mock import MagicMock

from engine.config import GameConfig
from engine.models import Card, CardRank, MarketCommit, MarketResult
from engine.game import Game
from engine.events import EventLogger
from engine import player as player_ops
from tests.test_s2_rules import _make_bot_agents


def _make_game(config: GameConfig, cash: int, debt_balance: int) -> Game:
    agents = _make_bot_agents()
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=1, logger=logger)
    for pid in agents:
        game.players[pid] = player_ops.create_player(pid, 3_000_000)
    p = game.players["P01"]
    game.players["P01"] = p.model_copy(update={"cash": cash, "debt_balance": debt_balance})
    return game


def _market_result(prize: int, num_participants: int = 3) -> MarketResult:
    """非ソロ市場（participants>=2）でP01が勝者となる MarketResult"""
    participants = []
    for i in range(num_participants):
        pid = "P01" if i == 0 else f"P{i + 1:02d}"
        card = Card(rank=CardRank.HIGH_CARD, card_id=f"c_{pid}_M01")
        participants.append(MarketCommit(player_id=pid, market_id="M01", card=card))
    return MarketResult(
        market_id="M01",
        participants=participants,
        winners=["P01"],
        prize_per_winner=prize,
        total_pool=prize,
    )


def _find_blocked_log(logger: EventLogger, player_id: str):
    return [
        e for e in logger.events
        if e.event_type == "DOUBLE_UP_BLOCKED" and e.data.get("player_id") == player_id
    ]


class TestDoubleUpBlockedByCash:
    """現金不足（p.cash < eligible_prize）でブロック"""

    def test_blocked_when_cash_insufficient(self):
        config = GameConfig.default_8_s2()
        game = _make_game(config, cash=400_000, debt_balance=0)
        mock_agent = MagicMock()
        mock_agent.choose_double_up.return_value = True
        game.agents["P01"] = mock_agent

        results = [_market_result(500_000)]
        game._process_double_up(5, results)

        mock_agent.choose_double_up.assert_not_called()
        assert game.players["P01"].cash == 400_000  # 変化なし（TAKE扱い、預託は起きない）

        logs = _find_blocked_log(game.logger, "P01")
        assert len(logs) == 1
        assert logs[0].data["eligible_prize"] == 500_000
        assert logs[0].data["cash"] == 400_000

        notices = [
            n for n in game._contract_notices.get("P01", [])
            if n["kind"] == "double_up_blocked"
        ]
        assert len(notices) == 1
        assert notices[0]["eligible_prize"] == 500_000


class TestDoubleUpBlockedByMandatoryRepay:
    """預託後残高が強制最低返済額を割り込む場合にブロック"""

    def test_blocked_when_post_deposit_cash_below_min_repay(self):
        config = GameConfig.default_8_s2()  # mandatory_repay_enabled=True, k=0
        # debt=1,000,000 → 利息計上後 1,015,000。R5: remaining=12-5+1=8, divisor=8
        # min_repay = ceil(1,015,000/8) = 126,875
        game = _make_game(config, cash=600_000, debt_balance=1_000_000)
        mock_agent = MagicMock()
        mock_agent.choose_double_up.return_value = True
        game.agents["P01"] = mock_agent

        results = [_market_result(500_000)]
        game._process_double_up(5, results)

        mock_agent.choose_double_up.assert_not_called()
        logs = _find_blocked_log(game.logger, "P01")
        assert len(logs) == 1
        assert logs[0].data["min_repay"] == 126_875
        assert logs[0].data["eligible_prize"] == 500_000

    def test_boundary_post_deposit_cash_equals_min_repay_allowed(self):
        """預託後残高 == 強制最低返済額 ちょうどなら通る（境界値）"""
        config = GameConfig.default_8_s2()
        # 同じ min_repay=126,875。cash - eligible_prize == min_repay ちょうどにする
        game = _make_game(config, cash=626_875, debt_balance=1_000_000)
        mock_agent = MagicMock()
        mock_agent.choose_double_up.return_value = False
        game.agents["P01"] = mock_agent

        results = [_market_result(500_000)]
        game._process_double_up(5, results)

        mock_agent.choose_double_up.assert_called_once()
        assert not _find_blocked_log(game.logger, "P01")

    def test_r11_uses_divisor_two(self):
        """R11（remaining=2）でも同式でブロック判定される"""
        config = GameConfig.default_8_s2()
        # debt=1,000,000 → 利息計上後 1,015,000。R11: remaining=12-11+1=2, divisor=2
        # min_repay = ceil(1,015,000/2) = 507,500
        game = _make_game(config, cash=700_000, debt_balance=1_000_000)
        mock_agent = MagicMock()
        mock_agent.choose_double_up.return_value = True
        game.agents["P01"] = mock_agent

        results = [_market_result(400_000)]
        game._process_double_up(11, results)

        mock_agent.choose_double_up.assert_not_called()
        logs = _find_blocked_log(game.logger, "P01")
        assert len(logs) == 1
        assert logs[0].data["min_repay"] == 507_500


class TestDoubleUpGuardWithMandatoryRepayDisabled:
    """mandatory_repay_enabled=False なら現金チェックのみ行う"""

    def test_large_debt_does_not_block_when_disabled(self):
        config = GameConfig.default_8_s2().model_copy(update={
            "mandatory_repay_enabled": False,
        })
        # 巨額の借金があっても強制返済チェックは無効なのでブロックされない
        game = _make_game(config, cash=600_000, debt_balance=50_000_000)
        mock_agent = MagicMock()
        mock_agent.choose_double_up.return_value = False
        game.agents["P01"] = mock_agent

        results = [_market_result(500_000)]
        game._process_double_up(5, results)

        mock_agent.choose_double_up.assert_called_once()
        assert not _find_blocked_log(game.logger, "P01")

    def test_still_blocks_on_cash_shortfall_when_disabled(self):
        config = GameConfig.default_8_s2().model_copy(update={
            "mandatory_repay_enabled": False,
        })
        game = _make_game(config, cash=400_000, debt_balance=50_000_000)
        mock_agent = MagicMock()
        mock_agent.choose_double_up.return_value = True
        game.agents["P01"] = mock_agent

        results = [_market_result(500_000)]
        game._process_double_up(5, results)

        mock_agent.choose_double_up.assert_not_called()
        assert _find_blocked_log(game.logger, "P01")
