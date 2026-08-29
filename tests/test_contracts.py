"""
v0.8 E8: 契約中カードのトレード禁止 — ヘルパー関数の単体テスト

`engine.contracts.count_committed_type_b_cards` / `is_card_tradable` を検証する。
"""

from engine.models import Card, CardRank, ObligationType
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
