"""
Cycle 8 修正1: 倍掛け単独市場除外のログ記録テスト

`from_solo_market`（成功分岐の内側にしか記録されず代数的に到達不能だった
フィールド）を `forfeited_by_solo_only`（没収分岐で記録、到達可能）へ
改名し、`DOUBLE_UP_RESOLVED` の3分岐すべてに `outcome_reason` と
勝敗内訳（solo_wins/non_solo_wins）を付与したことを検証する。

判定ロジック（成功/没収の分岐そのもの、§6.2ゲート）は無変更であることを
「払戻額・没収額が従来どおり」というアサーションで担保する（賞金回帰ガード）。

v0.8 D2: 解決ロジックは game._process_double_up() から
engine.settlement.resolve_double_up_deposits() へ移設された
（execute_settlement()内部・Step2直後で呼ばれる）。このテストは
移設後の関数を直接呼んで検証する。
"""

from engine.config import GameConfig
from engine.models import Card, CardRank, DoubleUpDeposit, MarketCommit, MarketResult
from engine.game import Game
from engine.events import EventLogger
from engine import settlement as settlement_ops
from tests.test_s2_rules import _make_bot_agents


def _make_game_with_player(player_id: str = "P01") -> Game:
    config = GameConfig.default_8_s2()
    agents = _make_bot_agents()
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=1, logger=logger)
    from engine import player as player_ops
    for pid in agents:
        game.players[pid] = player_ops.create_player(pid, 3_000_000)
    return game


def _make_market_result(market_id: str, winner: str, prize: int,
                         num_participants: int) -> MarketResult:
    participants = []
    for i in range(num_participants):
        pid = winner if i == 0 else f"P{i + 1:02d}"
        card = Card(rank=CardRank.HIGH_CARD, card_id=f"c_{pid}_{market_id}")
        participants.append(
            MarketCommit(player_id=pid, market_id=market_id, card=card)
        )
    return MarketResult(
        market_id=market_id,
        participants=participants,
        winners=[winner],
        prize_per_winner=prize,
        total_pool=prize,
    )


def _last_double_up_resolved(logger: EventLogger, player_id: str) -> dict:
    events = [
        e for e in logger.events
        if e.event_type == "DOUBLE_UP_RESOLVED" and e.data.get("player_id") == player_id
    ]
    assert events, f"no DOUBLE_UP_RESOLVED for {player_id}"
    return events[-1].data


class TestDoubleUpOutcomeReasonNonSoloWin:
    """非単独市場での勝利 → success かつ outcome_reason=='non_solo_win'"""

    def test_non_solo_win_logs_outcome_reason(self):
        game = _make_game_with_player()
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=100_000,
            deposited_round=1, success_round=2,
        )
        game.double_up_deposits.append(dep)

        results = [_make_market_result("M01", "P01", 500_000, 3)]
        settlement_ops.resolve_double_up_deposits(game.players, results, game.double_up_deposits, 2, game.logger)

        assert dep.resolved is True
        assert dep.success is True
        assert dep.forfeited_by_solo_only is False
        # 賞金回帰ガード: 払戻額は従来どおり deposit_amount * 2
        # （market prize 自体は _process_double_up の対象外＝別ステップで加算されるため
        # ここでは倍掛け払戻分のみが player.cash に反映される）
        assert game.players["P01"].cash == 3_000_000 + 200_000

        data = _last_double_up_resolved(game.logger, "P01")
        assert data["result"] == "success"
        assert data["outcome_reason"] == "non_solo_win"
        assert data["payout"] == 200_000
        assert data["solo_wins"] == 0
        assert data["non_solo_wins"] == 1


class TestDoubleUpOutcomeReasonSoloOnlyWin:
    """単独市場でのみ勝利 → forfeit かつ outcome_reason=='solo_only_win'"""

    def test_solo_only_win_logs_outcome_reason(self):
        game = _make_game_with_player()
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=200_000,
            deposited_round=1, success_round=2,
        )
        game.double_up_deposits.append(dep)

        results = [
            _make_market_result("M01", "P01", 300_000, 1),  # ソロ
            _make_market_result("M02", "P02", 100_000, 1),  # 別プレイヤーのソロ
        ]
        cash_before = game.players["P01"].cash
        settlement_ops.resolve_double_up_deposits(game.players, results, game.double_up_deposits, 2, game.logger)

        assert dep.resolved is True
        assert dep.success is False
        assert dep.forfeited_by_solo_only is True
        # 賞金回帰ガード: 没収時は倍掛け由来の現金増減がない
        assert game.players["P01"].cash == cash_before

        data = _last_double_up_resolved(game.logger, "P01")
        assert data["result"] == "forfeit"
        assert data["outcome_reason"] == "solo_only_win"
        assert "payout" not in data
        assert data["solo_wins"] == 1
        assert data["non_solo_wins"] == 0


class TestDoubleUpOutcomeReasonNoWin:
    """当ラウンド無勝利 → forfeit かつ outcome_reason=='no_win'"""

    def test_no_win_logs_outcome_reason(self):
        game = _make_game_with_player()
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=100_000,
            deposited_round=1, success_round=2,
        )
        game.double_up_deposits.append(dep)

        results = [_make_market_result("M01", "P02", 500_000, 3)]
        cash_before = game.players["P01"].cash
        settlement_ops.resolve_double_up_deposits(game.players, results, game.double_up_deposits, 2, game.logger)

        assert dep.resolved is True
        assert dep.success is False
        assert dep.forfeited_by_solo_only is False
        assert game.players["P01"].cash == cash_before

        data = _last_double_up_resolved(game.logger, "P01")
        assert data["result"] == "forfeit"
        assert data["outcome_reason"] == "no_win"
        assert data["solo_wins"] == 0
        assert data["non_solo_wins"] == 0


class TestDoubleUpOutcomeReasonEliminated:
    """解決前に脱落 → forfeit_eliminated かつ outcome_reason=='eliminated'"""

    def test_eliminated_logs_outcome_reason(self):
        game = _make_game_with_player()
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=100_000,
            deposited_round=1, success_round=2,
        )
        game.double_up_deposits.append(dep)

        p = game.players["P01"]
        game.players["P01"] = p.model_copy(update={"is_alive": False})

        # 脱落済みなら勝敗にかかわらず没収（非単独市場で勝っていても没収）
        results = [_make_market_result("M01", "P01", 500_000, 3)]
        settlement_ops.resolve_double_up_deposits(game.players, results, game.double_up_deposits, 2, game.logger)

        assert dep.resolved is True
        assert dep.success is False
        assert dep.forfeited_by_solo_only is False

        data = _last_double_up_resolved(game.logger, "P01")
        assert data["result"] == "forfeit_eliminated"
        assert data["outcome_reason"] == "eliminated"
        # 勝敗集計は行うが判定には使わない
        assert data["non_solo_wins"] == 1
