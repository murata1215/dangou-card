"""
支払可能額（spendable cash）と free_cash_mode のテスト（v0.9 サイクル9.1）

Free Cash（max(0, 現金 − 借金残高)）ゲートの廃止に伴い導入した
GameConfig.free_cash_mode / engine.player.spendable_cash() /
engine.player.insufficient_funds_reason() を検証する。

対象: doc/analysis/free_cash_inventory_20260830.md の6ゲート
①送金 ②報奨預託 ③トレード提案 ④トレード成立(提案者) ⑤トレード成立(受諾者)
⑥型A Atomic執行。
"""

import pytest

from engine.config import GameConfig
from engine.models import (
    TransferAction, BountyPostAction, CardTradeProposeAction, CardTradeAcceptAction,
    CardTradeStatus, ObligationType, Card, CardRank, MarketCommit, DoubleUpDeposit,
)
from engine import actions as action_ops
from engine import player as player_ops
from engine import contracts as contract_ops
from engine import settlement as settlement_ops
from engine import finance as finance_ops
from engine.game import Game
from engine.events import EventLogger
from engine.negotiation import StubAgent

from tests.conftest import make_player, make_contract, make_obligation, make_market

ENTRY_FEE = 100_000  # baseline_v1_s2() の entry_fee（GameConfig既定値を継承）


@pytest.fixture
def s2_config() -> GameConfig:
    """entry_fee モードの標準設定（8人版）"""
    return GameConfig.baseline_v1_s2(8)


# ---------------------------------------------------------------------------
# 単体: spendable_cash() / プリセット
# ---------------------------------------------------------------------------
class TestSpendableCashUnit:
    """spendable_cash() 単体: 3モード × at_settlement"""

    def test_debt_mode_matches_free_cash(self):
        cfg = GameConfig.baseline_v1(8)
        assert cfg.free_cash_mode == "debt"
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        assert player_ops.spendable_cash(p, cfg, at_settlement=False) == 0
        assert player_ops.spendable_cash(p, cfg, at_settlement=True) == 0
        assert p.free_cash == 0  # PlayerState.free_cash は不変

        p2 = make_player("P02", cash=5_000_000, debt=2_000_000)
        assert player_ops.spendable_cash(p2, cfg, at_settlement=False) == 3_000_000
        assert player_ops.spendable_cash(p2, cfg, at_settlement=False) == p2.free_cash

    def test_cash_mode_returns_full_cash(self):
        cfg = GameConfig.baseline_v1(8).model_copy(update={"free_cash_mode": "cash"})
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        assert player_ops.spendable_cash(p, cfg, at_settlement=False) == 3_000_000
        assert player_ops.spendable_cash(p, cfg, at_settlement=True) == 3_000_000

    def test_entry_fee_mode_reserves_fee_in_negotiation_only(self, s2_config):
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        assert (
            player_ops.spendable_cash(p, s2_config, at_settlement=False)
            == 3_000_000 - ENTRY_FEE
        )
        # Settlement時はEntry Fee二重控除を避けるため現金全額
        assert player_ops.spendable_cash(p, s2_config, at_settlement=True) == 3_000_000

    def test_entry_fee_mode_never_negative(self, s2_config):
        p = make_player("P01", cash=50_000, debt=0)
        assert player_ops.spendable_cash(p, s2_config, at_settlement=False) == 0

    def test_presets(self, s2_config):
        assert s2_config.free_cash_mode == "entry_fee"
        assert GameConfig.baseline_v1(8).free_cash_mode == "debt"
        assert GameConfig().free_cash_mode == "debt"
        assert GameConfig.default_20().free_cash_mode == "debt"
        assert GameConfig.default_12().free_cash_mode == "debt"
        assert GameConfig.default_8().free_cash_mode == "debt"

    def test_insufficient_funds_reason_debt_mode_purposes(self):
        cfg = GameConfig.baseline_v1(8)
        assert player_ops.insufficient_funds_reason(cfg, "transfer") == \
            "Insufficient free cash for transfer"
        assert player_ops.insufficient_funds_reason(cfg, "bounty") == \
            "Insufficient free cash for bounty deposit"
        assert player_ops.insufficient_funds_reason(cfg, "trade") == \
            "Insufficient free cash for trade payment"
        assert player_ops.insufficient_funds_reason(cfg, "type_a") == \
            "Atomic execution failed - insufficient free cash"

    def test_insufficient_funds_reason_cash_mode(self):
        cfg = GameConfig.baseline_v1(8).model_copy(update={"free_cash_mode": "cash"})
        assert player_ops.insufficient_funds_reason(cfg, "transfer") == "現金が不足しています"

    def test_insufficient_funds_reason_entry_fee_mode(self, s2_config):
        reason = player_ops.insufficient_funds_reason(s2_config, "transfer")
        assert "支払可能額" in reason
        assert "Entry Fee" in reason
        assert str(ENTRY_FEE) in reason


# ---------------------------------------------------------------------------
# 要件1: 送金境界（R1借入300万 → 290万OK / 290万1円NG）
# ---------------------------------------------------------------------------
class TestTransferBoundary:
    def test_transfer_at_boundary_ok(self, s2_config):
        p01 = make_player("P01", cash=3_000_000, debt=3_000_000)
        p02 = make_player("P02", cash=0, debt=0)
        players = {"P01": p01, "P02": p02}
        action = TransferAction(player_id="P01", to="P02", amount=2_900_000)
        result = action_ops.validate_action(action, p01, s2_config, players, round_num=1)
        assert result.success

    def test_transfer_one_yen_over_rejected_with_jp_reason(self, s2_config):
        p01 = make_player("P01", cash=3_000_000, debt=3_000_000)
        p02 = make_player("P02", cash=0, debt=0)
        players = {"P01": p01, "P02": p02}
        action = TransferAction(player_id="P01", to="P02", amount=2_900_001)
        result = action_ops.validate_action(action, p01, s2_config, players, round_num=1)
        assert not result.success
        assert "支払可能額" in result.reason
        assert "Entry Fee" in result.reason
        assert str(ENTRY_FEE) in result.reason

    def test_debt_mode_reason_unchanged(self):
        """既定(debt)モードでは既存の英語文言のまま（回帰確認）"""
        cfg = GameConfig.baseline_v1(8)
        p01 = make_player("P01", cash=3_000_000, debt=3_000_000)  # free_cash=0
        p02 = make_player("P02", cash=0, debt=0)
        players = {"P01": p01, "P02": p02}
        action = TransferAction(player_id="P01", to="P02", amount=1)
        result = action_ops.validate_action(action, p01, cfg, players, round_num=1)
        assert not result.success
        assert "free cash" in result.reason.lower()

    def test_cash_mode_reason(self):
        cfg = GameConfig.baseline_v1(8).model_copy(update={"free_cash_mode": "cash"})
        p01 = make_player("P01", cash=1_000, debt=0)
        p02 = make_player("P02", cash=0, debt=0)
        players = {"P01": p01, "P02": p02}
        action = TransferAction(player_id="P01", to="P02", amount=2_000)
        result = action_ops.validate_action(action, p01, cfg, players, round_num=1)
        assert not result.success
        assert result.reason == "現金が不足しています"


# ---------------------------------------------------------------------------
# 要件2: 送金後もCommitでEntry Feeが引ける（破産しない）→ Finance強制返済で破産
# ---------------------------------------------------------------------------
class TestEntryFeeReserveSurvivesCommitThenFinanceBankrupts:
    def _make_game(self, config: GameConfig) -> Game:
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game._setup()
        # R1借入300万・送金290万済み（cash=100,000）を模擬。
        # StubAgentのchoose_loanはconfig.loan_min固定のため、_setup()後に上書きする。
        p01 = game.players["P01"]
        game.players["P01"] = p01.model_copy(update={
            "cash": ENTRY_FEE,  # 300万 - 290万送金 = 10万（今RのEntry Feeちょうど）
            "debt_balance": 3_000_000,
            "initial_loan": 3_000_000,
        })
        game._phase_market_open(1)
        return game

    def test_commit_survives_with_zero_cash_after_entry_fee(self, s2_config):
        game = self._make_game(s2_config)
        game._phase_commit(1)

        p01_after_commit = game.players["P01"]
        assert p01_after_commit.is_alive
        assert p01_after_commit.cash == 0  # Entry Fee 10万がちょうど引かれた

        bankruptcy_events = [
            e for e in game.logger.events
            if e.event_type == "BANKRUPTCY" and e.data.get("player_id") == "P01"
        ]
        assert bankruptcy_events == []

    def test_finance_mandatory_repay_then_bankrupts(self, s2_config):
        """Entry Fee以外は守らない仕様の確認: Commit後の強制最低返済で破産する"""
        game = self._make_game(s2_config)
        game._phase_commit(1)
        assert game.players["P01"].is_alive
        assert game.players["P01"].cash == 0

        game._phase_finance(1)

        p01_after_finance = game.players["P01"]
        assert not p01_after_finance.is_alive
        assert p01_after_finance.elimination_reason == "bankruptcy"

        fail_events = [
            e for e in game.logger.events
            if e.event_type == "MANDATORY_REPAY_FAILED" and e.data.get("player_id") == "P01"
        ]
        assert len(fail_events) == 1


# ---------------------------------------------------------------------------
# 要件3・4: 型A Atomic — 二重控除なし・スナップショットに賞金/倍掛け払出反映
# ---------------------------------------------------------------------------
class TestTypeANoDoubleDeduction:
    def _commit(self, pid: str, market_id: str, rank: CardRank) -> MarketCommit:
        return MarketCommit(player_id=pid, market_id=market_id, card=Card(rank=rank, card_id=f"{rank.name}_1"))

    def test_exact_cash_equals_obligation_is_fulfilled(self, s2_config):
        """Commit後の現金==義務額ちょうどで型A成立（Entry Feeは既に引かれた後の
        現金を基準にするため、entry_feeモードでも二重控除は起きない）"""
        ob = make_obligation(
            "C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT,
            round_num=1, details={"amount": 1_000_000},
        )
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])

        players = {
            "P01": make_player("P01", cash=1_000_000, debt=3_000_000),
            "P02": make_player("P02", cash=1_000_000, debt=1_000_000),
        }
        commits = [
            self._commit("P01", "M01", CardRank.FULL_HOUSE),
            self._commit("P02", "M01", CardRank.HIGH_CARD),
        ]
        market = make_market("M01", 0)
        logger = EventLogger()
        config = GameConfig.baseline_v1(2).model_copy(update={
            "entry_fee": 0, "free_cash_mode": "entry_fee",
        })

        players, contracts, *_ = settlement_ops.execute_settlement(
            players, [market], commits, [c], [], round_num=1,
            config=config, logger=logger,
        )

        assert players["P01"].is_alive is True
        type_a_events = [e for e in logger.events if e.event_type == "TYPE_A_EXECUTION"]
        assert len(type_a_events) == 1

    def test_type_a_failure_reason_localized(self, s2_config):
        ob = make_obligation(
            "C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT,
            round_num=1, details={"amount": 1_000_001},
        )
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {
            "P01": make_player("P01", cash=1_000_000, debt=3_000_000),
            "P02": make_player("P02", cash=1_000_000, debt=1_000_000),
        }
        commits = [
            self._commit("P01", "M01", CardRank.FULL_HOUSE),
            self._commit("P02", "M01", CardRank.HIGH_CARD),
        ]
        market = make_market("M01", 0)
        logger = EventLogger()
        config = GameConfig.baseline_v1(2).model_copy(update={
            "entry_fee": 0, "free_cash_mode": "entry_fee",
        })

        players, contracts, *_ = settlement_ops.execute_settlement(
            players, [market], commits, [c], [], round_num=1,
            config=config, logger=logger,
        )

        assert players["P01"].is_alive is False
        fail_events = [e for e in logger.events if e.event_type == "TYPE_A_FAILURE"]
        assert len(fail_events) == 1
        assert "支払可能額" in fail_events[0].data["reason"]

    def test_snapshot_has_four_keys_and_spendable_equals_cash_in_entry_fee_mode(self):
        ob = make_obligation(
            "C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT,
            round_num=1, details={"amount": 500_000},
        )
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {
            "P01": make_player("P01", cash=2_000_000, debt=3_000_000),
            "P02": make_player("P02", cash=1_000_000, debt=1_000_000),
        }
        commits = [
            self._commit("P01", "M01", CardRank.FULL_HOUSE),
            self._commit("P02", "M01", CardRank.HIGH_CARD),
        ]
        market = make_market("M01", 0)
        logger = EventLogger()
        config = GameConfig.baseline_v1(2).model_copy(update={
            "entry_fee": 0, "free_cash_mode": "entry_fee",
        })

        settlement_ops.execute_settlement(
            players, [market], commits, [c], [], round_num=1,
            config=config, logger=logger,
        )

        snap_events = [e for e in logger.events if e.event_type == "SNAPSHOT"]
        assert len(snap_events) == 1
        snap = snap_events[0].data["snapshots"]["P01"]
        assert set(["cash", "free_cash", "spendable", "debt_balance"]).issubset(snap.keys())
        # entry_feeモードでは at_settlement=True → spendable == cash（二重控除なし）
        assert snap["spendable"] == snap["cash"]
        assert snap["debt_balance"] == 3_000_000


class TestTypeASnapshotIncludesPayouts:
    """スナップショットに市場賞金・前R倍掛け払出が反映される（entry_feeモード版）"""

    def _commit(self, pid: str, market_id: str, rank: CardRank) -> MarketCommit:
        return MarketCommit(player_id=pid, market_id=market_id, card=Card(rank=rank, card_id=f"{rank.name}_1"))

    def _make_config(self) -> GameConfig:
        return GameConfig.baseline_v1(2).model_copy(update={
            "entry_fee": 0, "free_cash_mode": "entry_fee",
        })

    def test_prize_and_double_up_payout_fund_type_a(self):
        ob = make_obligation(
            "C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT,
            round_num=2, details={"amount": 200_000},
        )
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])

        players = {"P01": make_player("P01", cash=0, debt=0), "P02": make_player("P02", cash=0, debt=0)}
        commits = [
            self._commit("P01", "M01", CardRank.FULL_HOUSE),
            self._commit("P02", "M01", CardRank.HIGH_CARD),
        ]
        market = make_market("M01", 100_000)
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=100_000,
            deposited_round=1, success_round=2,
        )
        logger = EventLogger()

        players, contracts, _, _, _, du_summary = settlement_ops.execute_settlement(
            players, [market], commits, [c], [], round_num=2,
            config=self._make_config(), logger=logger,
            double_up_deposits=[dep],
        )

        assert dep.resolved is True
        assert dep.success is True
        resolved = du_summary["resolved"][0]
        assert resolved["payout"] == 200_000

        # 市場賞金10万 + 倍掛け払出20万 = cash 30万。entry_feeモードでも
        # Settlement時は at_settlement=True → spendable=cash=30万で型A(20万)成立。
        assert players["P01"].is_alive is True
        type_a_events = [e for e in logger.events if e.event_type == "TYPE_A_EXECUTION"]
        assert len(type_a_events) == 1

    def test_without_payout_type_a_obligation_fails(self):
        ob = make_obligation(
            "C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT,
            round_num=2, details={"amount": 200_000},
        )
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])

        players = {"P01": make_player("P01", cash=0, debt=0), "P02": make_player("P02", cash=0, debt=0)}
        commits = [
            self._commit("P01", "M01", CardRank.FULL_HOUSE),
            self._commit("P02", "M01", CardRank.HIGH_CARD),
        ]
        market = make_market("M01", 100_000)
        logger = EventLogger()

        players, contracts, _, _, _, du_summary = settlement_ops.execute_settlement(
            players, [market], commits, [c], [], round_num=2,
            config=self._make_config(), logger=logger,
            double_up_deposits=[],
        )

        assert players["P01"].is_alive is False
        type_a_events = [e for e in logger.events if e.event_type == "TYPE_A_EXECUTION"]
        assert type_a_events == []


# ---------------------------------------------------------------------------
# 要件5: トレード現金 — 提案者払い・受諾者払いの両方
# ---------------------------------------------------------------------------
class TestCardTradeSpendable:
    def _make_game(self, config: GameConfig) -> Game:
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game._setup()
        game._phase_market_open(1)
        game._trade_counts = {pid: 0 for pid in game.players}
        return game

    def test_proposer_pays_gate3_validate_action_boundary(self, s2_config):
        """ゲート③（提案時）: 提案者払い（cash_amount>0）はspendable基準"""
        p01 = make_player("P01", cash=3_000_000, debt=3_000_000)
        p02 = make_player("P02", cash=0, debt=0)
        players = {"P01": p01, "P02": p02}

        ok_action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
            cash_amount=2_900_000,
        )
        result = action_ops.validate_action(ok_action, p01, s2_config, players, round_num=1)
        assert result.success

        ng_action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
            cash_amount=2_900_001,
        )
        result = action_ops.validate_action(ng_action, p01, s2_config, players, round_num=1)
        assert not result.success
        assert "支払可能額" in result.reason

    def test_proposer_pays_gate4_trade_settlement_reverify(self, s2_config):
        """ゲート④（受諾時の提案者側再検証）: 資金不足でEXPIRED、双方に通知"""
        game = self._make_game(s2_config)
        # P01: 300万借入・debt=300万 → entry_feeモードのspendable=290万
        game.players["P01"] = game.players["P01"].model_copy(update={
            "cash": 3_000_000, "debt_balance": 3_000_000,
        })

        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
            cash_amount=2_900_001,  # 提案時点ではvalidate_actionを経由しない内部実行なので通る
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)
        tp = game.trade_proposals[0]

        accept = CardTradeAcceptAction(player_id="P02", trade_id=tp.trade_id)
        game._execute_negotiation_action_inner(accept, "P02", 1, 2)

        assert tp.status == CardTradeStatus.EXPIRED
        p01_notices = [n for n in game._contract_notices.get("P01", []) if n["kind"] == "trade_failed_funds"]
        p02_notices = [n for n in game._contract_notices.get("P02", []) if n["kind"] == "trade_failed_funds"]
        assert len(p01_notices) == 1
        assert len(p02_notices) == 1
        assert p01_notices[0]["short_side"] == "proposer"

    def test_accepter_pays_gate5_trade_settlement_reverify(self, s2_config):
        """ゲート⑤（受諾時の受諾者側再検証）: cash_amount<0（受諾者払い）"""
        game = self._make_game(s2_config)
        # P02（受諾者）が支払う側。spendable(entry_fee)=290万に対し290万1円は不足。
        game.players["P02"] = game.players["P02"].model_copy(update={
            "cash": 3_000_000, "debt_balance": 3_000_000,
        })

        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
            cash_amount=-2_900_001,  # 負値 = 受諾者(P02)が払う
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)
        tp = game.trade_proposals[0]

        accept = CardTradeAcceptAction(player_id="P02", trade_id=tp.trade_id)
        game._execute_negotiation_action_inner(accept, "P02", 1, 2)

        assert tp.status == CardTradeStatus.EXPIRED
        p01_notices = [n for n in game._contract_notices.get("P01", []) if n["kind"] == "trade_failed_funds"]
        p02_notices = [n for n in game._contract_notices.get("P02", []) if n["kind"] == "trade_failed_funds"]
        assert len(p01_notices) == 1
        assert len(p02_notices) == 1
        assert p01_notices[0]["short_side"] == "accepter"

    def test_accepter_pays_succeeds_at_boundary(self, s2_config):
        """受諾者払いがspendableちょうどなら成立する"""
        game = self._make_game(s2_config)
        game.players["P02"] = game.players["P02"].model_copy(update={
            "cash": 3_000_000, "debt_balance": 3_000_000,
        })

        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
            cash_amount=-2_900_000,
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)
        tp = game.trade_proposals[0]

        accept = CardTradeAcceptAction(player_id="P02", trade_id=tp.trade_id)
        game._execute_negotiation_action_inner(accept, "P02", 1, 2)

        assert tp.status == CardTradeStatus.ACCEPTED


# ---------------------------------------------------------------------------
# 要件6: 報奨 — 匿名手数料込みで判定
# ---------------------------------------------------------------------------
class TestBountySurcharge:
    def _make_action(self, amount: int, anonymous: bool) -> BountyPostAction:
        return BountyPostAction(
            player_id="P01", amount=amount,
            bounty_type="achievement",
            condition_type="market_win_against",
            condition={"target_player": "P02"},
            round_num=1,
            anonymous=anonymous,
        )

    def test_anonymous_surcharge_pushes_over_boundary(self, s2_config):
        # spendable(entry_fee) = 3,000,000 - 100,000 = 2,900,000
        # amount=2,636,364 anonymous → deposited = 2,636,364 + ceil(263,636.4) = 2,900,001 → NG
        p01 = make_player("P01", cash=3_000_000, debt=3_000_000)
        players = {"P01": p01, "P02": make_player("P02", cash=0, debt=0)}
        action = self._make_action(2_636_364, anonymous=True)
        result = action_ops.validate_action(action, p01, s2_config, players, round_num=1)
        assert not result.success
        assert "支払可能額" in result.reason

    def test_anonymous_surcharge_at_boundary_ok(self, s2_config):
        # amount=2,636,363 anonymous → deposited = 2,636,363 + ceil(263,636.3) = 2,900,000 → OK
        p01 = make_player("P01", cash=3_000_000, debt=3_000_000)
        players = {"P01": p01, "P02": make_player("P02", cash=0, debt=0)}
        action = self._make_action(2_636_363, anonymous=True)
        result = action_ops.validate_action(action, p01, s2_config, players, round_num=1)
        assert result.success

    def test_non_anonymous_no_surcharge_at_boundary_ok(self, s2_config):
        p01 = make_player("P01", cash=3_000_000, debt=3_000_000)
        players = {"P01": p01, "P02": make_player("P02", cash=0, debt=0)}
        action = self._make_action(2_900_000, anonymous=False)
        result = action_ops.validate_action(action, p01, s2_config, players, round_num=1)
        assert result.success


# ---------------------------------------------------------------------------
# 要件7: cash モード最小テスト（Entry Feeを確保しない）
# ---------------------------------------------------------------------------
class TestCashModeMinimal:
    def test_full_cash_transferable_without_reserving_entry_fee(self):
        cfg = GameConfig.baseline_v1(8).model_copy(update={"free_cash_mode": "cash"})
        p01 = make_player("P01", cash=3_000_000, debt=3_000_000)
        p02 = make_player("P02", cash=0, debt=0)
        players = {"P01": p01, "P02": p02}
        # entry_feeモードなら290万が上限だが、cashモードは全額(300万)送金可能
        action = TransferAction(player_id="P01", to="P02", amount=3_000_000)
        result = action_ops.validate_action(action, p01, cfg, players, round_num=1)
        assert result.success


# ---------------------------------------------------------------------------
# 後方互換: execute_type_a_atomic の旧形状スナップショット・フォールバック
# ---------------------------------------------------------------------------
class TestBackwardCompatibility:
    def test_execute_type_a_atomic_falls_back_to_free_cash_without_spendable_key(self):
        ob = make_obligation(
            "OB1", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT,
            round_num=1, details={"amount": 300_000},
        )
        # "spendable" キーの無い旧形状スナップショット
        snapshots = {"P01": {"cash": 500_000, "free_cash": 300_000}}
        updated, failed, payments = contract_ops.execute_type_a_atomic([ob], snapshots)
        assert failed == []
        assert updated[0].is_fulfilled is True
        assert payments["P01"] == -300_000
        assert payments["P02"] == 300_000

    def test_execute_type_a_atomic_prefers_spendable_over_free_cash(self):
        ob = make_obligation(
            "OB1", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT,
            round_num=1, details={"amount": 300_000},
        )
        # free_cash=0（従来なら不履行）だが spendable=300,000（entry_feeモード想定）なら成立
        snapshots = {"P01": {"cash": 300_000, "free_cash": 0, "spendable": 300_000}}
        updated, failed, payments = contract_ops.execute_type_a_atomic([ob], snapshots)
        assert failed == []
        assert updated[0].is_fulfilled is True
