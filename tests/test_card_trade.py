"""
カードトレード（v0.7 §3 / v0.7.1 ブロードキャスト・拒否）のテスト

- swap_card() の正常動作
- CardTradeProposeAction / CardTradeAcceptAction / CardTradeRejectAction のバリデーション
- 提案→受諾のアトミック実行
- ブロードキャスト提案（複数宛先）
- 1件受諾で同一 offer_id の残りが EXPIRED
- 拒否アクション
- ラウンド末失効
- 可視性（受信者に他宛先非公開）
- プロンプト表示
"""

import pytest

from engine.config import GameConfig
from engine.models import (
    PlayerState, Card, CardRank,
    CardTradeProposal, CardTradeStatus,
    CardTradeProposeAction, CardTradeAcceptAction, CardTradeRejectAction,
)
from engine import player as player_ops
from engine import actions as action_ops
from engine.game import Game
from engine.events import EventLogger
from engine.negotiation import StubAgent
from llm.prompt_builder import build_negotiation_prompt

from tests.conftest import make_player


class TestSwapCard:
    """swap_card() のテスト"""

    def test_basic_swap(self):
        """カード交換が正常に行われる"""
        p = make_player("P01", cash=3_000_000, debt=2_000_000)
        give = next(c for c in p.hand if c.rank == CardRank.HIGH_CARD)
        receive = Card(card_id="FLUSH_99", rank=CardRank.FLUSH)

        p2 = player_ops.swap_card(p, give, receive)

        assert len(p2.hand) == len(p.hand)  # 枚数不変
        assert not any(c.card_id == give.card_id for c in p2.hand)
        assert any(c.card_id == "FLUSH_99" for c in p2.hand)

    def test_swap_duplicate_card_id_renamed(self):
        """同一card_idのカードを受け取るとcard_idがリネームされる"""
        p = make_player("P01", cash=3_000_000, debt=2_000_000)
        # P01 は HIGH_CARD_1 を持っている。同じ card_id のカードを受け取る
        give = next(c for c in p.hand if c.rank == CardRank.FLUSH)
        receive = Card(card_id="HIGH_CARD_1", rank=CardRank.HIGH_CARD)  # 重複 card_id

        p2 = player_ops.swap_card(p, give, receive)

        assert len(p2.hand) == len(p.hand)  # 枚数不変
        # HIGH_CARD が 3 枚（元の _1, _2 + リネームされた受け取り分）
        hc_cards = [c for c in p2.hand if c.rank == CardRank.HIGH_CARD]
        assert len(hc_cards) == 3
        # card_id はすべてユニーク
        card_ids = [c.card_id for c in p2.hand]
        assert len(card_ids) == len(set(card_ids))

    def test_use_card_removes_only_one(self):
        """同一card_idが2枚ある場合、use_cardは1枚だけ除去する"""
        p = make_player("P01", cash=3_000_000, debt=2_000_000)
        # 手動で重複カードを追加（バグ再現用）
        dup_card = Card(card_id="HIGH_CARD_1", rank=CardRank.HIGH_CARD)
        hand_with_dup = list(p.hand) + [dup_card]
        p = p.model_copy(update={"hand": hand_with_dup})
        original_count = len(p.hand)  # 13枚

        target = next(c for c in p.hand if c.card_id == "HIGH_CARD_1")
        p2 = player_ops.use_card(p, target)

        assert len(p2.hand) == original_count - 1  # 1枚だけ減少（2枚ではない）
        # まだ HIGH_CARD_1 が1枚残っている
        assert any(c.card_id == "HIGH_CARD_1" for c in p2.hand)

    def test_trade_then_use_no_drain(self):
        """トレードで同rank受取→使用しても手札が1枚だけ減る（ドレインしない）"""
        p1 = make_player("P01", cash=3_000_000, debt=2_000_000)
        p2_state = make_player("P02", cash=3_000_000, debt=2_000_000)

        # P01 が FLUSH_1 を渡し、P02 の HIGH_CARD_1 を受け取る（P01は既にHIGH_CARD_1を所持）
        give1 = next(c for c in p1.hand if c.card_id == "FLUSH_1")
        recv1 = next(c for c in p2_state.hand if c.card_id == "HIGH_CARD_1")

        p1_after = player_ops.swap_card(p1, give1, recv1)
        assert len(p1_after.hand) == 12  # 枚数不変

        # HIGH_CARD が3枚（元の _1, _2 + リネームされた受け取り分）
        hc_cards = [c for c in p1_after.hand if c.rank == CardRank.HIGH_CARD]
        assert len(hc_cards) == 3

        # 1枚使用して11枚になる（2枚消えない）
        p1_used = player_ops.use_card(p1_after, hc_cards[0])
        assert len(p1_used.hand) == 11  # 1枚だけ減少

    def test_swap_card_not_in_hand_raises(self):
        """手札にないカードを差し出そうとするとエラー"""
        p = make_player("P01", cash=3_000_000, debt=2_000_000)
        fake = Card(card_id="FAKE_99", rank=CardRank.ROYAL_FLUSH)
        receive = Card(card_id="X_99", rank=CardRank.FLUSH)

        with pytest.raises(ValueError, match="does not have card"):
            player_ops.swap_card(p, fake, receive)


class TestCardTradeProposeValidation:
    """CardTradeProposeAction のバリデーション"""

    def _make_config(self) -> GameConfig:
        return GameConfig.baseline_v1(8).model_copy(update={
            "card_trade_enabled": True,
        })

    def _make_players(self) -> dict[str, PlayerState]:
        return {
            "P01": make_player("P01", cash=5_000_000, debt=3_000_000),
            "P02": make_player("P02", cash=5_000_000, debt=3_000_000),
            "P03": make_player("P03", cash=5_000_000, debt=3_000_000),
        }

    def test_valid_proposal(self):
        """正常な提案（with_players リスト）"""
        players = self._make_players()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        result = action_ops.validate_action(
            action, players["P01"], self._make_config(), players, round_num=1,
        )
        assert result.success

    def test_valid_broadcast_proposal(self):
        """正常なブロードキャスト提案（複数宛先）"""
        players = self._make_players()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02", "P03"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        result = action_ops.validate_action(
            action, players["P01"], self._make_config(), players, round_num=1,
        )
        assert result.success

    def test_disabled(self):
        """card_trade_enabled=False → 不可"""
        players = self._make_players()
        config = GameConfig.baseline_v1(8)  # enabled=False
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success
        assert "disabled" in result.reason

    def test_empty_targets(self):
        """宛先が空 → 不可"""
        players = self._make_players()
        action = CardTradeProposeAction(
            player_id="P01", with_players=[],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        result = action_ops.validate_action(
            action, players["P01"], self._make_config(), players, round_num=1,
        )
        assert not result.success

    def test_too_many_targets(self):
        """宛先数が上限超過 → 不可"""
        players = self._make_players()
        config = self._make_config().model_copy(update={"card_trade_broadcast_max": 2})
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02", "P03", "P04"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        result = action_ops.validate_action(
            action, players["P01"], config, players, round_num=1,
        )
        assert not result.success
        assert "Too many" in result.reason

    def test_card_not_in_hand(self):
        """自分のカードを持っていない"""
        players = self._make_players()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="INVALID_RANK", receive_card="HIGH_CARD",
        )
        result = action_ops.validate_action(
            action, players["P01"], self._make_config(), players, round_num=1,
        )
        assert not result.success

    def test_insufficient_free_cash(self):
        """FreeCash不足の現金付きトレード"""
        players = self._make_players()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="FLUSH",
            cash_amount=3_000_000,  # 300万 > free_cash 200万
        )
        result = action_ops.validate_action(
            action, players["P01"], self._make_config(), players, round_num=1,
        )
        assert not result.success
        assert "free cash" in result.reason.lower()

    def test_valid_with_cash(self):
        """現金付きトレードが正常"""
        players = self._make_players()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="FLUSH",
            cash_amount=1_000_000,
        )
        result = action_ops.validate_action(
            action, players["P01"], self._make_config(), players, round_num=1,
        )
        assert result.success


class TestCardTradeAcceptValidation:
    """CardTradeAcceptAction のバリデーション"""

    def test_accept_always_passes_validation(self):
        """受諾アクションのバリデーションは常にOK"""
        p = make_player("P02", cash=5_000_000, debt=3_000_000)
        action = CardTradeAcceptAction(player_id="P02", trade_id="T_12345678")
        config = GameConfig.baseline_v1(8).model_copy(update={"card_trade_enabled": True})
        result = action_ops.validate_action(
            action, p, config, {"P02": p}, round_num=1,
        )
        assert result.success


class TestCardTradeRejectValidation:
    """CardTradeRejectAction のバリデーション"""

    def test_reject_does_not_consume_action(self):
        """拒否は回数枠を消費しない"""
        p = make_player("P02", cash=5_000_000, debt=3_000_000)
        action = CardTradeRejectAction(player_id="P02", trade_id="T_12345678")
        config = GameConfig.baseline_v1(8).model_copy(update={"card_trade_enabled": True})
        result = action_ops.validate_action(
            action, p, config, {"P02": p}, round_num=1,
        )
        assert result.success
        assert result.consumes_action is False


class TestCardTradeExecution:
    """カードトレードの提案→受諾実行テスト"""

    def test_s2_game_completes_with_trade(self):
        """S2設定でゲームが完走する"""
        config = GameConfig.baseline_v1_s2(8)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        result = game.run()
        assert result.round_count == 12

    def test_s1_game_unaffected(self):
        """S1設定でゲーム完走: カードトレード無効"""
        config = GameConfig.baseline_v1(8)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        result = game.run()
        assert result.round_count == 12

        trade_events = [
            e for e in logger.events
            if e.data.get("action") in ("card_trade_propose", "card_trade_accept", "card_trade_reject")
        ]
        assert len(trade_events) == 0


class TestCardTradeProposalModel:
    """CardTradeProposal モデルのテスト"""

    def test_proposal_creation(self):
        """提案モデルの生成"""
        tp = CardTradeProposal(
            trade_id="T_abcd1234",
            proposer="P01",
            with_player="P02",
            give_card_rank="HIGH_CARD",
            receive_card_rank="FLUSH",
            cash_amount=500_000,
            round_proposed=3,
        )
        assert tp.status == CardTradeStatus.PROPOSED
        assert tp.cash_amount == 500_000
        assert tp.offer_id == ""

    def test_proposal_with_offer_id(self):
        """offer_id 付きの提案（ブロードキャスト）"""
        tp = CardTradeProposal(
            trade_id="T_abcd1234",
            offer_id="O_12345678",
            proposer="P01",
            with_player="P02",
            give_card_rank="HIGH_CARD",
            receive_card_rank="FLUSH",
            round_proposed=3,
        )
        assert tp.offer_id == "O_12345678"


class TestBroadcastProposal:
    """ブロードキャスト提案のテスト"""

    def _make_game(self):
        config = GameConfig.baseline_v1_s2(8)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game._setup()
        game._phase_market_open(1)
        game._trade_counts = {pid: 0 for pid in game.players}
        return game

    def test_broadcast_creates_multiple_proposals(self):
        """ブロードキャスト提案で宛先ごとに個別の提案が生成される"""
        game = self._make_game()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02", "P03", "P04"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)

        proposals = [tp for tp in game.trade_proposals if tp.proposer == "P01"]
        assert len(proposals) == 3
        targets = {tp.with_player for tp in proposals}
        assert targets == {"P02", "P03", "P04"}

    def test_broadcast_same_offer_id(self):
        """全提案が同一 offer_id を持つ"""
        game = self._make_game()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02", "P03"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)

        proposals = [tp for tp in game.trade_proposals if tp.proposer == "P01"]
        offer_ids = {tp.offer_id for tp in proposals}
        assert len(offer_ids) == 1
        assert offer_ids.pop().startswith("O_")

    def test_accept_expires_siblings(self):
        """1件受諾で同一 offer_id の残りが EXPIRED になる"""
        game = self._make_game()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02", "P03"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)

        proposals = game.trade_proposals
        assert len(proposals) == 2

        # P02 が受諾
        p02_proposal = next(tp for tp in proposals if tp.with_player == "P02")
        accept = CardTradeAcceptAction(player_id="P02", trade_id=p02_proposal.trade_id)
        game._execute_negotiation_action_inner(accept, "P02", 1, 1)

        assert p02_proposal.status == CardTradeStatus.ACCEPTED
        p03_proposal = next(tp for tp in proposals if tp.with_player == "P03")
        assert p03_proposal.status == CardTradeStatus.EXPIRED

    def test_propose_does_not_consume_trade_slot(self):
        """v0.8 E7: 提案だけでは枠を消費しない（成立ベースに変更）"""
        game = self._make_game()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02", "P03", "P04"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)
        assert game._trade_counts["P01"] == 0

    def test_accept_consumes_both_slots(self):
        """v0.8 E7: 受諾成立で提案者・受諾者の双方が枠を消費する"""
        game = self._make_game()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02", "P03", "P04"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)
        assert game._trade_counts["P01"] == 0

        p02_proposal = next(tp for tp in game.trade_proposals if tp.with_player == "P02")
        accept = CardTradeAcceptAction(player_id="P02", trade_id=p02_proposal.trade_id)
        game._execute_negotiation_action_inner(accept, "P02", 1, 1)

        assert game._trade_counts["P01"] == 1
        assert game._trade_counts["P02"] == 1


class TestRejectAction:
    """拒否アクションのテスト"""

    def _make_game_with_proposal(self):
        config = GameConfig.baseline_v1_s2(8)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game._setup()
        game._phase_market_open(1)
        game._trade_counts = {pid: 0 for pid in game.players}

        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)
        return game

    def test_reject_sets_status(self):
        """拒否で REJECTED に遷移する"""
        game = self._make_game_with_proposal()
        tp = game.trade_proposals[0]
        reject = CardTradeRejectAction(player_id="P02", trade_id=tp.trade_id)
        game._execute_negotiation_action_inner(reject, "P02", 1, 2)
        assert tp.status == CardTradeStatus.REJECTED

    def test_reject_does_not_count_trade(self):
        """拒否は trade_counts を増やさない"""
        game = self._make_game_with_proposal()
        tp = game.trade_proposals[0]
        reject = CardTradeRejectAction(player_id="P02", trade_id=tp.trade_id)
        game._execute_negotiation_action_inner(reject, "P02", 1, 2)
        assert game._trade_counts["P02"] == 0

    def test_rejected_cannot_be_accepted(self):
        """REJECTED の提案は受諾不可"""
        game = self._make_game_with_proposal()
        tp = game.trade_proposals[0]
        # まず拒否
        reject = CardTradeRejectAction(player_id="P02", trade_id=tp.trade_id)
        game._execute_negotiation_action_inner(reject, "P02", 1, 2)
        # 受諾を試みる
        accept = CardTradeAcceptAction(player_id="P02", trade_id=tp.trade_id)
        game._execute_negotiation_action_inner(accept, "P02", 1, 3)
        # status は REJECTED のまま（ACCEPTED にならない）
        assert tp.status == CardTradeStatus.REJECTED

    def test_reject_notifies_proposer(self):
        """v0.8 E10: 拒否は提案者へ trade_rejected 通知が届く"""
        game = self._make_game_with_proposal()
        tp = game.trade_proposals[0]
        reject = CardTradeRejectAction(player_id="P02", trade_id=tp.trade_id)
        game._execute_negotiation_action_inner(reject, "P02", 1, 2)

        notices = game._contract_notices.get("P01", [])
        matches = [n for n in notices if n["kind"] == "trade_rejected"]
        assert len(matches) == 1
        assert matches[0]["trade_id"] == tp.trade_id
        assert matches[0]["with_player"] == "P02"


class TestSelfTradeProhibition:
    """v0.8 G1: 自己トレード禁止（E7で提案が枠を消費しなくなり自己受諾が
    到達可能になったための必須の防御）"""

    def _make_players(self):
        return {
            "P01": make_player("P01", cash=5_000_000, debt=3_000_000),
            "P02": make_player("P02", cash=5_000_000, debt=3_000_000),
        }

    def _make_config(self):
        return GameConfig.baseline_v1(8).model_copy(update={"card_trade_enabled": True})

    def _make_game(self):
        config = GameConfig.baseline_v1_s2(8)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game._setup()
        game._phase_market_open(1)
        game._trade_counts = {pid: 0 for pid in game.players}
        return game

    def test_self_only_target_rejected_by_validation(self):
        """自分1人だけが宛先 → validate_action で不成立"""
        players = self._make_players()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P01"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        result = action_ops.validate_action(
            action, players["P01"], self._make_config(), players, round_num=1,
        )
        assert not result.success
        assert "yourself" in result.reason.lower()

    def test_broadcast_with_self_mixed_still_valid(self):
        """自分を含むブロードキャストでも、自分以外の宛先がいれば成立する"""
        players = self._make_players()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P01", "P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        result = action_ops.validate_action(
            action, players["P01"], self._make_config(), players, round_num=1,
        )
        assert result.success

    def test_self_target_skipped_at_apply(self):
        """apply層: 自分宛のみのブロードキャストは提案が1件も生成されない"""
        game = self._make_game()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P01"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)
        assert game.trade_proposals == []

    def test_broadcast_self_mixed_skips_only_self_at_apply(self):
        """apply層: 自分を含むブロードキャストは自分宛だけがスキップされる"""
        game = self._make_game()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P01", "P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)
        targets = {tp.with_player for tp in game.trade_proposals}
        assert targets == {"P02"}

    def test_cash_conservation_regression(self):
        """複製バグ回帰: トレード成立後も全プレイヤーの現金合計が保存される"""
        game = self._make_game()
        cash_before = sum(p.cash for p in game.players.values())

        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="FLUSH",
            cash_amount=100_000,
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)
        tp = game.trade_proposals[0]
        accept = CardTradeAcceptAction(player_id="P02", trade_id=tp.trade_id)
        game._execute_negotiation_action_inner(accept, "P02", 1, 1)

        cash_after = sum(p.cash for p in game.players.values())
        assert cash_after == cash_before


class TestTradeSlotBasedOnCompletion:
    """v0.8 E7/E7b: トレード枠は「成立」ベース。提案の失効で枠は消費されず、
    上限到達で他の未成立提案が掃き寄せ失効する"""

    def _make_game(self):
        config = GameConfig.baseline_v1_s2(8)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game._setup()
        game._phase_market_open(1)
        game._trade_counts = {pid: 0 for pid in game.players}
        return game

    def test_expired_proposal_does_not_consume_slot_then_accept_still_works(self):
        """提案が失効しても枠は消費されず、後続の提案が受諾で成立する"""
        game = self._make_game()
        action1 = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        game._execute_negotiation_action_inner(action1, "P01", 1, 1)
        tp1 = game.trade_proposals[0]
        reject = CardTradeRejectAction(player_id="P02", trade_id=tp1.trade_id)
        game._execute_negotiation_action_inner(reject, "P02", 1, 2)
        assert game._trade_counts["P01"] == 0

        action2 = CardTradeProposeAction(
            player_id="P01", with_players=["P03"],
            give_card="ONE_PAIR", receive_card="ONE_PAIR",
        )
        game._execute_negotiation_action_inner(action2, "P01", 1, 3)
        tp2 = next(tp for tp in game.trade_proposals if tp.with_player == "P03")
        accept = CardTradeAcceptAction(player_id="P03", trade_id=tp2.trade_id)
        game._execute_negotiation_action_inner(accept, "P03", 1, 4)

        assert tp2.status == CardTradeStatus.ACCEPTED
        assert game._trade_counts["P01"] == 1
        assert game._trade_counts["P03"] == 1

    def test_success_blocks_further_propose_for_both_parties(self):
        """成立後は提案者・受諾者ともに新規提案が上限で不成立になる"""
        game = self._make_game()
        action1 = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        game._execute_negotiation_action_inner(action1, "P01", 1, 1)
        tp1 = game.trade_proposals[0]
        accept = CardTradeAcceptAction(player_id="P02", trade_id=tp1.trade_id)
        game._execute_negotiation_action_inner(accept, "P02", 1, 2)
        assert game._trade_counts["P01"] == 1
        assert game._trade_counts["P02"] == 1

        # 提案者P01は新規提案しても apply 側の防御チェックで即 return（提案が作られない）
        action2 = CardTradeProposeAction(
            player_id="P01", with_players=["P03"],
            give_card="ONE_PAIR", receive_card="ONE_PAIR",
        )
        game._execute_negotiation_action_inner(action2, "P01", 1, 3)
        assert not any(tp.with_player == "P03" for tp in game.trade_proposals)

    def test_sibling_supersede_across_offer_ids_when_capped(self):
        """v0.8 E7b: 同一offer_idでない別提案も、当事者が上限に達したら掃き寄せ失効する"""
        game = self._make_game()
        action1 = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        game._execute_negotiation_action_inner(action1, "P01", 1, 1)
        action2 = CardTradeProposeAction(
            player_id="P01", with_players=["P03"],
            give_card="ONE_PAIR", receive_card="ONE_PAIR",
        )
        game._execute_negotiation_action_inner(action2, "P01", 1, 2)

        tp1 = next(tp for tp in game.trade_proposals if tp.with_player == "P02")
        tp2 = next(tp for tp in game.trade_proposals if tp.with_player == "P03")
        assert tp1.offer_id != tp2.offer_id

        accept = CardTradeAcceptAction(player_id="P02", trade_id=tp1.trade_id)
        game._execute_negotiation_action_inner(accept, "P02", 1, 3)

        assert tp1.status == CardTradeStatus.ACCEPTED
        assert tp2.status == CardTradeStatus.EXPIRED

        notices = game._contract_notices.get("P01", [])
        matches = [n for n in notices if n["kind"] == "trade_superseded"]
        assert len(matches) == 1
        assert matches[0]["trade_id"] == tp2.trade_id
        assert matches[0]["accepted_trade_id"] == tp1.trade_id

    def test_failed_funds_notifies_both_parties(self):
        """v0.8 E10: 受諾時の資金不足EXPIREDは提案者・受諾者の双方に通知される"""
        game = self._make_game()
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
            cash_amount=100_000,
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)
        tp = game.trade_proposals[0]

        # 提案後に提案者の現金が尽きた状態を模擬（再検証で不成立になる）
        p01 = game.players["P01"]
        game.players["P01"] = p01.model_copy(update={"cash": 0})

        accept = CardTradeAcceptAction(player_id="P02", trade_id=tp.trade_id)
        game._execute_negotiation_action_inner(accept, "P02", 1, 2)

        assert tp.status == CardTradeStatus.EXPIRED
        p01_notices = [n for n in game._contract_notices.get("P01", []) if n["kind"] == "trade_failed_funds"]
        p02_notices = [n for n in game._contract_notices.get("P02", []) if n["kind"] == "trade_failed_funds"]
        assert len(p01_notices) == 1
        assert len(p02_notices) == 1
        assert p01_notices[0]["trade_id"] == tp.trade_id
        assert p02_notices[0]["trade_id"] == tp.trade_id


class TestContractCommittedCardNotTradable:
    """v0.8 E8: 契約で使用予定のカードはトレードできない"""

    def _make_players(self):
        return {
            "P01": make_player("P01", cash=5_000_000, debt=3_000_000),
            "P02": make_player("P02", cash=5_000_000, debt=3_000_000),
        }

    def _make_config(self):
        return GameConfig.baseline_v1(8).model_copy(update={"card_trade_enabled": True})

    def _make_contract_with_card_obligation(self, obligor: str, card_rank: str, round_num: int):
        from engine.models import ObligationType
        from tests.conftest import make_contract, make_obligation
        ob = make_obligation("C1_OB01", "C1", obligor, "PXX", ObligationType.TYPE_B_CARD,
                              round_num=round_num, details={"card_rank": card_rank})
        return make_contract("C1", obligor, [obligor, "PXX"], [ob])

    def test_propose_blocked_when_only_copy_committed(self):
        """1枚しか無いランクが契約で拘束されていれば提案不可"""
        players = self._make_players()
        p01 = players["P01"].model_copy(update={
            "hand": [c for c in players["P01"].hand if c.card_id == "HIGH_CARD_1"],
        })
        players["P01"] = p01
        contract = self._make_contract_with_card_obligation("P01", "HIGH_CARD", round_num=5)
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        result = action_ops.validate_action(
            action, players["P01"], self._make_config(), players, round_num=3,
            contracts=[contract],
        )
        assert not result.success
        assert "契約" in result.reason

    def test_propose_allowed_when_extra_copy_available(self):
        """2枚持ちで義務1件なら1枚は提案可能"""
        players = self._make_players()
        contract = self._make_contract_with_card_obligation("P01", "HIGH_CARD", round_num=5)
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        result = action_ops.validate_action(
            action, players["P01"], self._make_config(), players, round_num=3,
            contracts=[contract],
        )
        assert result.success

    def test_propose_allowed_when_obligation_in_past_round(self):
        """過去Rの義務はブロックしない"""
        players = self._make_players()
        p01 = players["P01"].model_copy(update={
            "hand": [c for c in players["P01"].hand if c.card_id == "HIGH_CARD_1"],
        })
        players["P01"] = p01
        contract = self._make_contract_with_card_obligation("P01", "HIGH_CARD", round_num=2)
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        result = action_ops.validate_action(
            action, players["P01"], self._make_config(), players, round_num=3,
            contracts=[contract],
        )
        assert result.success

    def test_propose_without_contracts_kwarg_skips_check(self):
        """contracts 未指定なら E8 検証はスキップされる（後方互換）"""
        players = self._make_players()
        p01 = players["P01"].model_copy(update={
            "hand": [c for c in players["P01"].hand if c.card_id == "HIGH_CARD_1"],
        })
        players["P01"] = p01
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        result = action_ops.validate_action(
            action, players["P01"], self._make_config(), players, round_num=3,
        )
        assert result.success

    def test_accept_blocked_when_accepter_card_committed(self):
        """受諾者の差し出すカードが契約で拘束されていればブロック"""
        from engine.models import CardTradeProposal
        players = self._make_players()
        p02 = players["P02"].model_copy(update={
            "hand": [c for c in players["P02"].hand if c.card_id == "HIGH_CARD_1"],
        })
        players["P02"] = p02
        contract = self._make_contract_with_card_obligation("P02", "HIGH_CARD", round_num=5)
        proposal = CardTradeProposal(
            trade_id="T_e8accept1", offer_id="", proposer="P01", with_player="P02",
            give_card_rank="FLUSH", receive_card_rank="HIGH_CARD", round_proposed=3,
        )
        action = CardTradeAcceptAction(player_id="P02", trade_id=proposal.trade_id)
        result = action_ops.validate_action(
            action, players["P02"], self._make_config(), players, round_num=3,
            contracts=[contract], trade_proposals=[proposal],
        )
        assert not result.success
        assert "契約" in result.reason

    def test_accept_blocked_when_proposer_card_committed(self):
        """提案者側の渡すカードが契約で拘束されていればブロック（提案者側）"""
        from engine.models import CardTradeProposal
        players = self._make_players()
        p01 = players["P01"].model_copy(update={
            "hand": [c for c in players["P01"].hand if c.card_id == "HIGH_CARD_1"],
        })
        players["P01"] = p01
        contract = self._make_contract_with_card_obligation("P01", "HIGH_CARD", round_num=5)
        proposal = CardTradeProposal(
            trade_id="T_e8accept2", offer_id="", proposer="P01", with_player="P02",
            give_card_rank="HIGH_CARD", receive_card_rank="FLUSH", round_proposed=3,
        )
        action = CardTradeAcceptAction(player_id="P02", trade_id=proposal.trade_id)
        result = action_ops.validate_action(
            action, players["P02"], self._make_config(), players, round_num=3,
            contracts=[contract], trade_proposals=[proposal],
        )
        assert not result.success
        assert "契約" in result.reason
        assert "提案者側" in result.reason


class TestRoundEndExpiration:
    """ラウンド末失効のテスト"""

    def test_pending_expires_at_round_end(self):
        """ラウンド末で PROPOSED → EXPIRED"""
        config = GameConfig.baseline_v1_s2(8)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game._setup()
        game._phase_market_open(1)
        game._trade_counts = {pid: 0 for pid in game.players}

        # 提案を手動で追加
        tp = CardTradeProposal(
            trade_id="T_manual",
            offer_id="O_manual",
            proposer="P01",
            with_player="P02",
            give_card_rank="HIGH_CARD",
            receive_card_rank="HIGH_CARD",
            round_proposed=1,
        )
        game.trade_proposals.append(tp)

        # 交渉フェイズを実行（StubAgent は全員 pass なのですぐ終了）
        game._phase_negotiation(1)

        # 提案が EXPIRED になっている
        assert tp.status == CardTradeStatus.EXPIRED


class TestTradesPendingVisibility:
    """trades_pending の可視性テスト"""

    def _make_pending_state(self, for_player_id: str) -> dict:
        tp = CardTradeProposal(
            trade_id="T_test01",
            proposer="P01",
            with_player="P02",
            give_card_rank="HIGH_CARD",
            receive_card_rank="FLUSH",
            round_proposed=2,
        )
        state: dict = {
            "round_num": 3,
            "markets": [],
            "alive_players": ["P01", "P02", "P03"],
            "contracts_public": [],
            "messages": [],
            "contracts_pending": [],
        }
        state["trades_pending"] = [
            {
                "trade_id": tp.trade_id,
                "proposer": tp.proposer,
                "with_player": tp.with_player,
                "give_card_rank": tp.give_card_rank,
                "receive_card_rank": tp.receive_card_rank,
                "cash_amount": tp.cash_amount,
                "round_proposed": tp.round_proposed,
            }
        ] if for_player_id in (tp.proposer, tp.with_player) and tp.status == CardTradeStatus.PROPOSED else []
        return state

    def test_party_sees_trade(self):
        """当事者にはトレード提案が見える"""
        state = self._make_pending_state("P02")
        assert len(state["trades_pending"]) == 1
        assert state["trades_pending"][0]["trade_id"] == "T_test01"

    def test_non_party_does_not_see_trade(self):
        """第三者にはトレード提案が見えない"""
        state = self._make_pending_state("P03")
        assert len(state["trades_pending"]) == 0

    def test_receiver_has_no_offer_id(self):
        """受信者の visible_state に offer_id が含まれない"""
        config = GameConfig.baseline_v1_s2(8)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        game._phase_market_open(1)
        game._trade_counts = {pid: 0 for pid in game.players}

        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02", "P03"],
            give_card="HIGH_CARD", receive_card="HIGH_CARD",
        )
        game._execute_negotiation_action_inner(action, "P01", 1, 1)

        # P02（受信者）の visible_state
        state_p02 = game._build_visible_state(1, for_player_id="P02")
        assert len(state_p02["trades_pending"]) == 1
        trade = state_p02["trades_pending"][0]
        assert "offer_id" not in trade
        assert "all_targets" not in trade
        assert "target_statuses" not in trade

        # P01（提案者）の visible_state には offer_id がある
        state_p01 = game._build_visible_state(1, for_player_id="P01")
        assert len(state_p01["trades_pending"]) == 2
        for t in state_p01["trades_pending"]:
            assert "offer_id" in t
            assert "all_targets" in t
            assert set(t["all_targets"]) == {"P02", "P03"}

        # P04（第三者）には何も見えない
        state_p04 = game._build_visible_state(1, for_player_id="P04")
        assert len(state_p04["trades_pending"]) == 0


class TestTradePromptDisplay:
    """プロンプト表示テスト"""

    def test_trade_section_shown(self):
        """トレード提案がプロンプトに表示される"""
        state = {
            "round_num": 3,
            "markets": [{"market_id": "M01", "prize_pool": 500000}],
            "alive_players": ["P01", "P02"],
            "messages": [],
            "contracts_pending": [],
            "trades_pending": [
                {
                    "trade_id": "T_abc12345",
                    "proposer": "P01",
                    "with_player": "P02",
                    "give_card_rank": "HIGH_CARD",
                    "receive_card_rank": "FLUSH",
                    "cash_amount": 500_000,
                    "round_proposed": 2,
                }
            ],
        }
        player = make_player("P02", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1(8)
        prompt = build_negotiation_prompt(player, 3, 1, state, config)

        assert "提案中のカードトレード" in prompt
        assert "T_abc12345" in prompt
        assert "card_trade_accept" in prompt
        assert "card_trade_reject" in prompt
        assert "HIGH_CARD" in prompt
        assert "50万円" in prompt

    def test_no_trade_section_when_empty(self):
        """トレード提案がないときセクションが出ない"""
        state = {
            "round_num": 3,
            "markets": [{"market_id": "M01", "prize_pool": 500000}],
            "alive_players": ["P01", "P02"],
            "messages": [],
            "contracts_pending": [],
            "trades_pending": [],
        }
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1(8)
        prompt = build_negotiation_prompt(player, 3, 1, state, config)

        assert "提案中のカードトレード" not in prompt

    def test_proposer_sees_targets_in_prompt(self):
        """提案者にはプロンプトに宛先リストが表示される"""
        state = {
            "round_num": 3,
            "markets": [{"market_id": "M01", "prize_pool": 500000}],
            "alive_players": ["P01", "P02", "P03"],
            "messages": [],
            "contracts_pending": [],
            "trades_pending": [
                {
                    "trade_id": "T_abc12345",
                    "proposer": "P01",
                    "with_player": "P02",
                    "give_card_rank": "HIGH_CARD",
                    "receive_card_rank": "FLUSH",
                    "cash_amount": 0,
                    "round_proposed": 2,
                    "offer_id": "O_test",
                    "all_targets": ["P02", "P03"],
                    "target_statuses": {"P02": "proposed", "P03": "proposed"},
                }
            ],
        }
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1(8)
        prompt = build_negotiation_prompt(player, 3, 1, state, config)

        assert "宛先:" in prompt
        assert "P02" in prompt
        assert "P03" in prompt
