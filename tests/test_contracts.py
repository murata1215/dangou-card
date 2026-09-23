"""
v0.8 E8: 契約中カードのトレード禁止 — ヘルパー関数の単体テスト

`engine.contracts.count_committed_type_b_cards` / `is_card_tradable` を検証する。
"""

from engine.models import Card, CardRank, ObligationType, MarketResult
from engine import contracts as contract_ops
from tests.conftest import make_contract, make_obligation, make_player


class TestCountCommittedTypeBCards:
    """count_committed_type_b_cards のテスト"""

    def test_no_contracts_zero(self):
        assert contract_ops.count_committed_type_b_cards([], "P01", "HIGH_CARD", 3) == 0

    def test_active_type_b_card_counted(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                              round_num=5, details={"card_rank": "HIGH_CARD"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        assert contract_ops.count_committed_type_b_cards([c], "P01", "HIGH_CARD", 3) == 1

    def test_different_rank_not_counted(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                              round_num=5, details={"card_rank": "ONE_PAIR"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        assert contract_ops.count_committed_type_b_cards([c], "P01", "HIGH_CARD", 3) == 0

    def test_different_obligor_not_counted(self):
        ob = make_obligation("C1_OB01", "C1", "P02", "P01", ObligationType.TYPE_B_CARD,
                              round_num=5, details={"card_rank": "HIGH_CARD"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        assert contract_ops.count_committed_type_b_cards([c], "P01", "HIGH_CARD", 3) == 0

    def test_past_round_not_counted(self):
        """過去Rの義務は未到来ではないため数えない"""
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                              round_num=2, details={"card_rank": "HIGH_CARD"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        assert contract_ops.count_committed_type_b_cards([c], "P01", "HIGH_CARD", 3) == 0

    def test_future_round_counted(self):
        """将来Rの義務は拘束する"""
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                              round_num=8, details={"card_rank": "HIGH_CARD"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        assert contract_ops.count_committed_type_b_cards([c], "P01", "HIGH_CARD", 3) == 1

    def test_proposed_contract_not_counted(self):
        """未署名（PROPOSED）契約の義務は数えない"""
        from engine.models import ContractStatus
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                              round_num=5, details={"card_rank": "HIGH_CARD"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob], status=ContractStatus.PROPOSED)
        assert contract_ops.count_committed_type_b_cards([c], "P01", "HIGH_CARD", 3) == 0

    def test_fulfilled_not_counted(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                              round_num=5, details={"card_rank": "HIGH_CARD"})
        ob = ob.model_copy(update={"is_fulfilled": True})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        assert contract_ops.count_committed_type_b_cards([c], "P01", "HIGH_CARD", 3) == 0

    def test_expired_not_counted(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                              round_num=5, details={"card_rank": "HIGH_CARD"})
        ob = ob.model_copy(update={"is_expired": True})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        assert contract_ops.count_committed_type_b_cards([c], "P01", "HIGH_CARD", 3) == 0

    def test_multiple_obligations_counted(self):
        ob1 = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                               round_num=5, details={"card_rank": "HIGH_CARD"})
        ob2 = make_obligation("C1_OB02", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                               round_num=7, details={"card_rank": "HIGH_CARD"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob1, ob2])
        assert contract_ops.count_committed_type_b_cards([c], "P01", "HIGH_CARD", 3) == 2


class TestIsCardTradable:
    """is_card_tradable のテスト"""

    def test_no_contracts_tradable(self):
        p = make_player("P01", cash=1_000_000)
        assert contract_ops.is_card_tradable(None, p, "HIGH_CARD", 3) is True
        assert contract_ops.is_card_tradable([], p, "HIGH_CARD", 3) is True

    def test_one_card_one_obligation_blocked(self):
        """1枚持ちで義務1件 → 手放すと違反確定なのでブロック"""
        p = make_player("P01", cash=1_000_000, hand=[
            Card(card_id="HIGH_CARD_1", rank=CardRank.HIGH_CARD),
        ])
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                              round_num=5, details={"card_rank": "HIGH_CARD"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        assert contract_ops.is_card_tradable([c], p, "HIGH_CARD", 3) is False

    def test_two_cards_one_obligation_tradable(self):
        """2枚持ち・義務1件 → 1枚は手放せる"""
        p = make_player("P01", cash=1_000_000)  # フルデッキ = HIGH_CARDを2枚持つ
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                              round_num=5, details={"card_rank": "HIGH_CARD"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        assert contract_ops.is_card_tradable([c], p, "HIGH_CARD", 3) is True

    def test_two_cards_two_obligations_blocked(self):
        """2枚持ち・義務2件 → 手放すと不足するのでブロック"""
        p = make_player("P01", cash=1_000_000)
        ob1 = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                               round_num=5, details={"card_rank": "HIGH_CARD"})
        ob2 = make_obligation("C1_OB02", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                               round_num=7, details={"card_rank": "HIGH_CARD"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob1, ob2])
        assert contract_ops.is_card_tradable([c], p, "HIGH_CARD", 3) is False

    def test_different_rank_unaffected(self):
        p = make_player("P01", cash=1_000_000)
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_B_CARD,
                              round_num=5, details={"card_rank": "ONE_PAIR"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        assert contract_ops.is_card_tradable([c], p, "HIGH_CARD", 3) is True

    def test_invalid_rank_name_defaults_true(self):
        p = make_player("P01", cash=1_000_000)
        assert contract_ops.is_card_tradable([], p, "NOT_A_RANK", 3) is True


class TestValidateTypeCDetails:
    """validate_type_c_details のテスト（v0.10 §1.2, §2）"""

    VALID_MARKETS = {"M01", "M02"}
    VALID_PLAYERS = {"P01", "P02", "P03"}

    def test_valid_market_winner(self):
        details = {
            "amount": 500_000, "condition_type": "market_winner",
            "condition": {"market_id": "M01", "target_player": "P02"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is None

    def test_valid_eliminated(self):
        details = {
            "amount": 100_000, "condition_type": "eliminated",
            "condition": {"target_player": "P03"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is None

    def test_valid_market_surge(self):
        details = {
            "amount": 200_000, "condition_type": "market_surge",
            "condition": {"market_id": "M02"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is None

    def test_amount_zero_rejected(self):
        details = {
            "amount": 0, "condition_type": "market_surge",
            "condition": {"market_id": "M01"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_amount_negative_rejected(self):
        details = {
            "amount": -1, "condition_type": "market_surge",
            "condition": {"market_id": "M01"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_amount_bool_rejected(self):
        """boolはintのサブクラスのため明示的に弾く"""
        details = {
            "amount": True, "condition_type": "market_surge",
            "condition": {"market_id": "M01"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_invalid_condition_type_rejected(self):
        details = {
            "amount": 100_000, "condition_type": "no_such_condition",
            "condition": {},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_condition_not_dict_rejected(self):
        details = {"amount": 100_000, "condition_type": "market_surge", "condition": "M01"}
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_market_winner_missing_market_id_rejected(self):
        details = {
            "amount": 100_000, "condition_type": "market_winner",
            "condition": {"target_player": "P02"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_market_winner_missing_target_player_rejected(self):
        details = {
            "amount": 100_000, "condition_type": "market_winner",
            "condition": {"market_id": "M01"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_market_winner_unknown_market_id_rejected(self):
        details = {
            "amount": 100_000, "condition_type": "market_winner",
            "condition": {"market_id": "M99", "target_player": "P02"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_market_winner_unknown_target_player_rejected(self):
        details = {
            "amount": 100_000, "condition_type": "market_winner",
            "condition": {"market_id": "M01", "target_player": "P99"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_eliminated_missing_target_player_rejected(self):
        details = {"amount": 100_000, "condition_type": "eliminated", "condition": {}}
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_eliminated_unknown_target_player_rejected(self):
        details = {
            "amount": 100_000, "condition_type": "eliminated",
            "condition": {"target_player": "P99"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_eliminated_target_player_need_not_be_a_party(self):
        """条件対象プレイヤーは契約当事者である必要はない（§2）"""
        details = {
            "amount": 100_000, "condition_type": "eliminated",
            "condition": {"target_player": "P03"},
        }
        # P03は当事者集合に含まれなくても valid_player_ids に含まれれば許可
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is None

    def test_market_surge_missing_market_id_rejected(self):
        details = {"amount": 100_000, "condition_type": "market_surge", "condition": {}}
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_market_surge_unknown_market_id_rejected(self):
        details = {
            "amount": 100_000, "condition_type": "market_surge",
            "condition": {"market_id": "M99"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is not None

    def test_no_solvency_check_at_proposal_time(self):
        """組成時の支払能力チェックは行わない（§1.2）—amount任意の大きさでも通る"""
        details = {
            "amount": 999_999_999, "condition_type": "market_surge",
            "condition": {"market_id": "M01"},
        }
        assert contract_ops.validate_type_c_details(
            details, self.VALID_MARKETS, self.VALID_PLAYERS) is None


class TestGetActiveTypeCObligations:
    """get_active_type_c_obligations のテスト（v0.10）"""

    def test_active_type_c_returned(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_C_CONDITIONAL,
                              round_num=5, details={"amount": 100_000})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        result = contract_ops.get_active_type_c_obligations([c], 5)
        assert len(result) == 1
        assert result[0].obligation_id == "C1_OB01"

    def test_excluded_obligor_filtered(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_C_CONDITIONAL,
                              round_num=5, details={"amount": 100_000})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        result = contract_ops.get_active_type_c_obligations([c], 5, excluded_players={"P01"})
        assert result == []

    def test_excluded_counterparty_filtered(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_C_CONDITIONAL,
                              round_num=5, details={"amount": 100_000})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        result = contract_ops.get_active_type_c_obligations([c], 5, excluded_players={"P02"})
        assert result == []

    def test_expired_not_returned(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_C_CONDITIONAL,
                              round_num=5, details={"amount": 100_000})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        c = contract_ops.expire_obligations([c], {"C1_OB01"})[0]
        assert contract_ops.get_active_type_c_obligations([c], 5) == []

    def test_type_a_not_returned(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT,
                              round_num=5, details={"amount": 100_000})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        assert contract_ops.get_active_type_c_obligations([c], 5) == []


class TestEvaluateTypeCCondition:
    """evaluate_type_c_condition のテスト（v0.10 §2）— 純関数の単体検証"""

    def _make_ob(self, condition_type: str, condition: dict, amount: int = 100_000):
        return make_obligation(
            "C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_C_CONDITIONAL,
            round_num=5,
            details={"amount": amount, "condition_type": condition_type, "condition": condition},
        )

    def test_market_winner_fires_when_target_is_winner(self):
        from engine.models import MarketResult
        ob = self._make_ob("market_winner", {"market_id": "M01", "target_player": "P02"})
        mr = MarketResult(market_id="M01", participants=[], winners=["P02"],
                           prize_per_winner=100_000, total_pool=100_000, surged=False)
        fired, reason = contract_ops.evaluate_type_c_condition(
            ob, market_results={"M01": mr}, surged_by_market={}, eliminated_player_ids=set(),
            players={},
        )
        assert fired is True

    def test_market_winner_not_fired_when_target_not_winner(self):
        from engine.models import MarketResult
        ob = self._make_ob("market_winner", {"market_id": "M01", "target_player": "P02"})
        mr = MarketResult(market_id="M01", participants=[], winners=["P03"],
                           prize_per_winner=100_000, total_pool=100_000, surged=False)
        fired, reason = contract_ops.evaluate_type_c_condition(
            ob, market_results={"M01": mr}, surged_by_market={}, eliminated_player_ids=set(),
            players={},
        )
        assert fired is False

    def test_market_winner_not_fired_when_market_not_resolved(self):
        """参加者0で市場自体が解決されていない（決済ラウンド不一致等）扱いは不成立"""
        ob = self._make_ob("market_winner", {"market_id": "M01", "target_player": "P02"})
        fired, reason = contract_ops.evaluate_type_c_condition(
            ob, market_results={}, surged_by_market={}, eliminated_player_ids=set(), players={},
        )
        assert fired is False

    def test_market_winner_shared_win_fires(self):
        """同ランク山分けの勝者も勝者に含む（§2.1）"""
        from engine.models import MarketResult
        ob = self._make_ob("market_winner", {"market_id": "M01", "target_player": "P02"})
        mr = MarketResult(market_id="M01", participants=[], winners=["P02", "P03"],
                           prize_per_winner=50_000, total_pool=100_000, surged=False)
        fired, reason = contract_ops.evaluate_type_c_condition(
            ob, market_results={"M01": mr}, surged_by_market={}, eliminated_player_ids=set(),
            players={},
        )
        assert fired is True

    def test_eliminated_fires_via_this_settlement_set(self):
        """同一Settlementの型B監査脱落（players.is_aliveはまだTrue）でもORで成立"""
        ob = self._make_ob("eliminated", {"target_player": "P02"})
        p02 = make_player("P02", cash=0)  # is_alive デフォルトTrue
        fired, reason = contract_ops.evaluate_type_c_condition(
            ob, market_results={}, surged_by_market={}, eliminated_player_ids={"P02"},
            players={"P02": p02},
        )
        assert fired is True

    def test_eliminated_fires_via_past_round_elimination(self):
        """過去ラウンドに既に脱落済み（is_alive=False）でも成立"""
        ob = self._make_ob("eliminated", {"target_player": "P02"})
        p02 = make_player("P02", cash=0).model_copy(update={"is_alive": False})
        fired, reason = contract_ops.evaluate_type_c_condition(
            ob, market_results={}, surged_by_market={}, eliminated_player_ids=set(),
            players={"P02": p02},
        )
        assert fired is True

    def test_eliminated_not_fired_when_alive_and_not_in_set(self):
        ob = self._make_ob("eliminated", {"target_player": "P02"})
        p02 = make_player("P02", cash=0)
        fired, reason = contract_ops.evaluate_type_c_condition(
            ob, market_results={}, surged_by_market={}, eliminated_player_ids=set(),
            players={"P02": p02},
        )
        assert fired is False

    def test_eliminated_unknown_player_treated_as_eliminated(self):
        """playersに存在しない対象は is_alive=False 相当として扱われる"""
        ob = self._make_ob("eliminated", {"target_player": "P99"})
        fired, reason = contract_ops.evaluate_type_c_condition(
            ob, market_results={}, surged_by_market={}, eliminated_player_ids=set(), players={},
        )
        assert fired is True

    def test_market_surge_fires(self):
        ob = self._make_ob("market_surge", {"market_id": "M01"})
        fired, reason = contract_ops.evaluate_type_c_condition(
            ob, market_results={}, surged_by_market={"M01": True}, eliminated_player_ids=set(),
            players={},
        )
        assert fired is True

    def test_market_surge_not_fired(self):
        ob = self._make_ob("market_surge", {"market_id": "M01"})
        fired, reason = contract_ops.evaluate_type_c_condition(
            ob, market_results={}, surged_by_market={"M01": False}, eliminated_player_ids=set(),
            players={},
        )
        assert fired is False

    def test_market_surge_missing_defaults_not_fired(self):
        ob = self._make_ob("market_surge", {"market_id": "M01"})
        fired, reason = contract_ops.evaluate_type_c_condition(
            ob, market_results={}, surged_by_market={}, eliminated_player_ids=set(), players={},
        )
        assert fired is False

    def test_no_side_effects_pure_function(self):
        """同一入力を2回渡しても結果が変わらない（純関数であることの簡易確認）"""
        ob = self._make_ob("market_surge", {"market_id": "M01"})
        surged = {"M01": True}
        r1 = contract_ops.evaluate_type_c_condition(
            ob, market_results={}, surged_by_market=surged, eliminated_player_ids=set(), players={},
        )
        r2 = contract_ops.evaluate_type_c_condition(
            ob, market_results={}, surged_by_market=surged, eliminated_player_ids=set(), players={},
        )
        assert r1 == r2


class TestExpireObligations:
    """expire_obligations のテスト（v0.10）"""

    def test_marks_specified_obligation_expired(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_C_CONDITIONAL,
                              round_num=5, details={"amount": 100_000})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        updated = contract_ops.expire_obligations([c], {"C1_OB01"})
        assert updated[0].obligations[0].is_expired is True

    def test_empty_ids_no_op(self):
        ob = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_C_CONDITIONAL,
                              round_num=5, details={"amount": 100_000})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        updated = contract_ops.expire_obligations([c], set())
        assert updated == [c]

    def test_does_not_affect_other_obligations(self):
        ob1 = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_C_CONDITIONAL,
                               round_num=5, details={"amount": 100_000})
        ob2 = make_obligation("C1_OB02", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT,
                               round_num=5, details={"amount": 50_000})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob1, ob2])
        updated = contract_ops.expire_obligations([c], {"C1_OB01"})
        obs = {o.obligation_id: o for o in updated[0].obligations}
        assert obs["C1_OB01"].is_expired is True
        assert obs["C1_OB02"].is_expired is False

    def test_does_not_call_elimination_expire_for_player(self):
        """expire_obligations_for_playerとは独立の実装（既存関数を呼び出していない）"""
        import inspect
        src = inspect.getsource(contract_ops.expire_obligations)
        assert "elim_ops.expire_obligations_for_player(" not in src
        assert ".expire_obligations_for_player(" not in src
