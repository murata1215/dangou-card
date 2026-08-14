"""
レスポンスパーサーのカードトレード関連テスト + 未知アクション警告テスト
"""

import logging

import pytest

from engine.models import (
    CardTradeProposeAction,
    CardTradeAcceptAction,
    CardTradeRejectAction,
    PassAction,
)
from llm.response_parser import _convert_action, ParseError


class TestCardTradeProposeParser:
    """card_trade_propose のパーステスト"""

    def test_basic_propose(self):
        """基本的なカードトレード提案をパースできる"""
        data = {
            "type": "card_trade_propose",
            "with_players": ["P07"],
            "give_card": "ONE_PAIR",
            "receive_card": "FLUSH",
            "cash_amount": 0,
        }
        action = _convert_action(data, "P01", "negotiation")
        assert isinstance(action, CardTradeProposeAction)
        assert action.with_players == ["P07"]
        assert action.give_card == "ONE_PAIR"
        assert action.receive_card == "FLUSH"
        assert action.cash_amount == 0

    def test_broadcast_propose(self):
        """複数宛先のブロードキャスト提案をパースできる"""
        data = {
            "type": "card_trade_propose",
            "with_players": ["P02", "P03", "P04"],
            "give_card": "high_card",
            "receive_card": "straight",
            "cash_amount": 500000,
        }
        action = _convert_action(data, "P01", "negotiation")
        assert isinstance(action, CardTradeProposeAction)
        assert action.with_players == ["P02", "P03", "P04"]
        assert action.give_card == "HIGH_CARD"  # 正規化される
        assert action.receive_card == "STRAIGHT"
        assert action.cash_amount == 500000

    def test_with_key_alias(self):
        """with_players の代わりに with キーでも指定できる"""
        data = {
            "type": "card_trade_propose",
            "with": ["P05"],
            "give_card": "TWO_PAIR",
            "receive_card": "THREE_OF_A_KIND",
        }
        action = _convert_action(data, "P01", "negotiation")
        assert isinstance(action, CardTradeProposeAction)
        assert action.with_players == ["P05"]

    def test_cash_key_alias(self):
        """cash_amount の代わりに cash キーでも指定できる"""
        data = {
            "type": "card_trade_propose",
            "with_players": ["P07"],
            "give_card": "ONE_PAIR",
            "receive_card": "FLUSH",
            "cash": 100000,
        }
        action = _convert_action(data, "P01", "negotiation")
        assert isinstance(action, CardTradeProposeAction)
        assert action.cash_amount == 100000

    def test_missing_with_players_raises(self):
        """with_players が空の場合 ParseError"""
        data = {
            "type": "card_trade_propose",
            "with_players": [],
            "give_card": "ONE_PAIR",
            "receive_card": "FLUSH",
        }
        with pytest.raises(ParseError):
            _convert_action(data, "P01", "negotiation")

    def test_invalid_give_card_raises(self):
        """無効な差出カード名で ParseError"""
        data = {
            "type": "card_trade_propose",
            "with_players": ["P07"],
            "give_card": "INVALID_CARD",
            "receive_card": "FLUSH",
        }
        with pytest.raises(ParseError, match="差出カード名"):
            _convert_action(data, "P01", "negotiation")

    def test_invalid_receive_card_raises(self):
        """無効な受取カード名で ParseError"""
        data = {
            "type": "card_trade_propose",
            "with_players": ["P07"],
            "give_card": "ONE_PAIR",
            "receive_card": "JOKER",
        }
        with pytest.raises(ParseError, match="受取カード名"):
            _convert_action(data, "P01", "negotiation")


class TestCardTradeAcceptParser:
    """card_trade_accept のパーステスト"""

    def test_basic_accept(self):
        """基本的なカードトレード受諾をパースできる"""
        data = {
            "type": "card_trade_accept",
            "trade_id": "T_abc123",
        }
        action = _convert_action(data, "P02", "negotiation")
        assert isinstance(action, CardTradeAcceptAction)
        assert action.trade_id == "T_abc123"
        assert action.player_id == "P02"

    def test_missing_trade_id_raises(self):
        """trade_id が空の場合 ParseError"""
        data = {
            "type": "card_trade_accept",
            "trade_id": "",
        }
        with pytest.raises(ParseError, match="trade_id"):
            _convert_action(data, "P02", "negotiation")


class TestCardTradeRejectParser:
    """card_trade_reject のパーステスト"""

    def test_basic_reject(self):
        """基本的なカードトレード拒否をパースできる"""
        data = {
            "type": "card_trade_reject",
            "trade_id": "T_def456",
        }
        action = _convert_action(data, "P03", "negotiation")
        assert isinstance(action, CardTradeRejectAction)
        assert action.trade_id == "T_def456"
        assert action.player_id == "P03"

    def test_missing_trade_id_raises(self):
        """trade_id が空の場合 ParseError"""
        data = {
            "type": "card_trade_reject",
        }
        with pytest.raises(ParseError, match="trade_id"):
            _convert_action(data, "P03", "negotiation")


class TestUnknownActionWarning:
    """未知アクションの警告テスト"""

    def test_unknown_action_returns_pass(self):
        """未知のアクションは PassAction にフォールバック"""
        data = {"type": "some_unknown_action"}
        action = _convert_action(data, "P01", "negotiation")
        assert isinstance(action, PassAction)
        assert action.player_id == "P01"

    def test_unknown_action_logs_warning(self, caplog):
        """未知のアクションで警告ログが出力される"""
        data = {"type": "totally_invented"}
        with caplog.at_level(logging.WARNING, logger="llm.response_parser"):
            _convert_action(data, "P05", "negotiation")
        assert "Unknown action type 'totally_invented' from P05" in caplog.text
