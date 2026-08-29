"""
v0.8 D6: contract_propose の terms 検証テスト

engine/actions.py の ContractProposeAction 検証ブロックが、実行時例外
（P2防御層のsilent脱落トラップ）を待たずに ActionResult(False, reason) を
明示的に返すことを検証する。
"""

from engine.config import GameConfig
from engine.models import ContractProposeAction
from engine import actions as action_ops
from tests.conftest import make_player


def _config(num_players: int = 4) -> GameConfig:
    return GameConfig.baseline_v1_s2(num_players)


def _players(num: int = 4) -> dict:
    return {
        f"P{i+1:02d}": make_player(f"P{i+1:02d}", cash=1_000_000, debt=0)
        for i in range(num)
    }


def _propose(**kwargs) -> ContractProposeAction:
    base = {
        "player_id": "P01",
        "with_players": ["P02"],
        "terms": [{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_a_payment", "round_num": 5,
            "details": {"amount": 100_000},
        }],
    }
    base.update(kwargs)
    return ContractProposeAction(**base)


class TestWithPlayersValidation:
    def test_empty_with_players_fails(self):
        config = _config()
        players = _players()
        action = _propose(with_players=[])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success

    def test_self_in_with_players_fails(self):
        config = _config()
        players = _players()
        action = _propose(with_players=["P01", "P02"])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success
        assert "proposer" in result.reason

    def test_duplicate_with_players_fails(self):
        config = _config()
        players = _players()
        action = _propose(with_players=["P02", "P02"])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success
        assert "duplicate" in result.reason


class TestTermPartyValidation:
    def test_obligor_not_a_party_fails(self):
        """契約の当事者(P01/P02)ではないobligorは拒否される"""
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P03", "counterparty": "P02",
            "ob_type": "type_a_payment", "round_num": 5,
            "details": {"amount": 100_000},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success
        assert "obligor" in result.reason

    def test_counterparty_not_a_party_fails(self):
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P03",
            "ob_type": "type_a_payment", "round_num": 5,
            "details": {"amount": 100_000},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success
        assert "counterparty" in result.reason

    def test_obligor_equals_counterparty_fails(self):
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P01",
            "ob_type": "type_a_payment", "round_num": 5,
            "details": {"amount": 100_000},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success


class TestRoundNumValidation:
    def test_past_round_num_fails(self):
        """現在Rより過去のround_numは拒否される（過去に解除不能な死に契約を作れない）"""
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_a_payment", "round_num": 3,
            "details": {"amount": 100_000},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=5,
        )
        assert not result.success
        assert "round_num" in result.reason

    def test_round_num_beyond_num_rounds_fails(self):
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_a_payment", "round_num": config.num_rounds + 1,
            "details": {"amount": 100_000},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success

    def test_round_num_equal_current_round_ok(self):
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_a_payment", "round_num": 5,
            "details": {"amount": 100_000},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=5,
        )
        assert result.success

    def test_non_int_round_num_fails(self):
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_a_payment", "round_num": "5",
            "details": {"amount": 100_000},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success


class TestMarketIdValidation:
    def test_nonexistent_market_id_fails(self):
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_b_market", "round_num": 5,
            "details": {"market_id": "M99"},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success
        assert "market_id" in result.reason

    def test_valid_market_id_ok(self):
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_b_market", "round_num": 5,
            "details": {"market_id": "M01"},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert result.success

    def test_type_b_no_market_invalid_market_id_fails(self):
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_b_no_market", "round_num": 5,
            "details": {"market_id": "ZZZ"},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success


class TestTypeAAmountValidation:
    def test_negative_amount_fails(self):
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_a_payment", "round_num": 5,
            "details": {"amount": -100},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success
        assert "amount" in result.reason

    def test_zero_amount_fails(self):
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_a_payment", "round_num": 5,
            "details": {"amount": 0},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success

    def test_non_int_amount_fails(self):
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_a_payment", "round_num": 5,
            "details": {"amount": "100000"},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success

    def test_positive_amount_ok(self):
        config = _config()
        players = _players()
        action = _propose()
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert result.success


class TestTypeBCardValidation:
    def test_invalid_card_rank_fails(self):
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_b_card", "round_num": 5,
            "details": {"card_rank": "NOT_A_RANK"},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success

    def test_card_alias_normalized_and_valid(self):
        """'card'/'rank' エイリアスはvalidate側でも正規化してから判定する"""
        config = _config()
        players = _players()
        action = _propose(terms=[{
            "obligor": "P01", "counterparty": "P02",
            "ob_type": "type_b_card", "round_num": 5,
            "details": {"card": "ROYAL_FLUSH"},
        }])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert result.success


class TestEmptyTermsValidation:
    def test_empty_terms_fails(self):
        config = _config()
        players = _players()
        action = _propose(terms=[])
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success
