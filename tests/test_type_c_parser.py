"""
v0.10 型C（条件付き金銭契約）のパーサーテスト

`llm/response_parser.py`: `_convert_action()` の contract_propose 経路が
ob_type="type_c_conditional" を受理し、details/condition の形（shape）を
検証することを確認する。round_num範囲・market_id/target_playerの実在性
チェックはこのparser層には現在ラウンド・config・プレイヤー一覧の文脈が
無いため行わない（engine/actions.py の validate_type_c_details() が担当）。
"""

import pytest

from engine.models import ContractProposeAction
from llm.response_parser import _convert_action, parse_response, ParseError


def _term(**overrides) -> dict:
    base = {
        "obligor": "P01", "counterparty": "P07", "ob_type": "type_c_conditional",
        "round_num": 10,
        "details": {
            "amount": 1_000_000, "condition_type": "market_winner",
            "condition": {"market_id": "M02", "target_player": "P05"},
        },
    }
    base.update(overrides)
    return base


class TestTypeCTermShapeAccepted:
    """正常形は受理される"""

    def test_market_winner_accepted(self):
        data = {"type": "contract_propose", "with": ["P07"], "terms": [_term()]}
        action = _convert_action(data, "P01", "negotiation")
        assert isinstance(action, ContractProposeAction)
        assert action.terms[0]["ob_type"] == "type_c_conditional"

    def test_eliminated_accepted(self):
        term = _term(details={
            "amount": 500_000, "condition_type": "eliminated",
            "condition": {"target_player": "P09"},
        })
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        action = _convert_action(data, "P01", "negotiation")
        assert isinstance(action, ContractProposeAction)

    def test_market_surge_accepted(self):
        term = _term(details={
            "amount": 300_000, "condition_type": "market_surge",
            "condition": {"market_id": "M01"},
        })
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        action = _convert_action(data, "P01", "negotiation")
        assert isinstance(action, ContractProposeAction)

    def test_multiple_terms_mixed_types_accepted(self):
        """1契約に型A・型Cを混在できる（§: 1つの契約に複数義務を含められる）"""
        ob_a = {
            "obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
            "round_num": 10, "details": {"amount": 200_000},
        }
        data = {"type": "contract_propose", "with": ["P07"], "terms": [ob_a, _term()]}
        action = _convert_action(data, "P01", "negotiation")
        assert len(action.terms) == 2


class TestTypeCTermShapeRejected:
    """不正な形は ParseError（correction_hint付き）になる"""

    def test_details_not_dict_rejected(self):
        term = _term(details="not-a-dict")
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        with pytest.raises(ParseError) as exc_info:
            _convert_action(data, "P01", "negotiation")
        assert exc_info.value.correction_hint

    def test_amount_missing_rejected(self):
        term = _term(details={
            "condition_type": "market_winner",
            "condition": {"market_id": "M02", "target_player": "P05"},
        })
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        with pytest.raises(ParseError):
            _convert_action(data, "P01", "negotiation")

    def test_amount_zero_rejected(self):
        term = _term(details={
            "amount": 0, "condition_type": "market_winner",
            "condition": {"market_id": "M02", "target_player": "P05"},
        })
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        with pytest.raises(ParseError):
            _convert_action(data, "P01", "negotiation")

    def test_amount_negative_rejected(self):
        term = _term(details={
            "amount": -500, "condition_type": "market_winner",
            "condition": {"market_id": "M02", "target_player": "P05"},
        })
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        with pytest.raises(ParseError):
            _convert_action(data, "P01", "negotiation")

    def test_amount_not_int_rejected(self):
        term = _term(details={
            "amount": "1000000", "condition_type": "market_winner",
            "condition": {"market_id": "M02", "target_player": "P05"},
        })
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        with pytest.raises(ParseError):
            _convert_action(data, "P01", "negotiation")

    def test_condition_type_missing_rejected(self):
        term = _term(details={
            "amount": 1_000_000,
            "condition": {"market_id": "M02", "target_player": "P05"},
        })
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        with pytest.raises(ParseError):
            _convert_action(data, "P01", "negotiation")

    def test_condition_type_unknown_rejected(self):
        term = _term(details={
            "amount": 1_000_000, "condition_type": "not_a_real_condition",
            "condition": {"market_id": "M02", "target_player": "P05"},
        })
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        with pytest.raises(ParseError) as exc_info:
            _convert_action(data, "P01", "negotiation")
        assert "market_winner" in exc_info.value.correction_hint

    def test_condition_not_dict_rejected(self):
        term = _term(details={
            "amount": 1_000_000, "condition_type": "market_winner",
            "condition": "M02",
        })
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        with pytest.raises(ParseError):
            _convert_action(data, "P01", "negotiation")

    def test_market_winner_missing_market_id_rejected(self):
        term = _term(details={
            "amount": 1_000_000, "condition_type": "market_winner",
            "condition": {"target_player": "P05"},
        })
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        with pytest.raises(ParseError):
            _convert_action(data, "P01", "negotiation")

    def test_market_winner_missing_target_player_rejected(self):
        term = _term(details={
            "amount": 1_000_000, "condition_type": "market_winner",
            "condition": {"market_id": "M02"},
        })
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        with pytest.raises(ParseError):
            _convert_action(data, "P01", "negotiation")

    def test_eliminated_missing_target_player_rejected(self):
        term = _term(details={"amount": 1_000_000, "condition_type": "eliminated", "condition": {}})
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        with pytest.raises(ParseError):
            _convert_action(data, "P01", "negotiation")

    def test_market_surge_missing_market_id_rejected(self):
        term = _term(details={"amount": 1_000_000, "condition_type": "market_surge", "condition": {}})
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        with pytest.raises(ParseError):
            _convert_action(data, "P01", "negotiation")

    def test_whole_proposal_rejected_on_single_bad_term(self):
        """1条項でも不正なら提案全体を却下する（複数termsのうち1つが不正）"""
        ob_a = {
            "obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment",
            "round_num": 10, "details": {"amount": 200_000},
        }
        bad_c = _term(details={"amount": -1, "condition_type": "market_surge",
                                "condition": {"market_id": "M02"}})
        data = {"type": "contract_propose", "with": ["P07"], "terms": [ob_a, bad_c]}
        with pytest.raises(ParseError):
            _convert_action(data, "P01", "negotiation")


class TestParseResponseIntegration:
    """parse_response() 経由でも同じ検証が働くことの確認"""

    def test_parse_response_accepts_valid_type_c_term(self):
        text = (
            '{"strategy": {}, "action": {"type": "contract_propose", "with": ["P07"], '
            '"terms": [' + str(_term()).replace("'", '"') + ']}}'
        )
        strategy, action = parse_response(text, "P01", "negotiation")
        assert isinstance(action, ContractProposeAction)

    def test_parse_response_rejects_invalid_round_num_range_is_not_this_layer(self):
        """round_num範囲チェックはparser層では行わない（engine/actions.py側の責務）。
        parser層はshapeのみ見るため、範囲外round_num自体ではParseErrorにならない
        （intでありさえすれば通る）ことを確認する回帰ガード。"""
        term = _term(round_num=999)
        data = {"type": "contract_propose", "with": ["P07"], "terms": [term]}
        action = _convert_action(data, "P01", "negotiation")
        assert action.terms[0]["round_num"] == 999
