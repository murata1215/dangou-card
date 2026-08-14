"""
Bot 7: Betrayal（談合・裏切り）— 談合に乗るが裏切る

Collusionの提案にacceptし、指定市場へ行くが、
提案カードより1ランク上のカードで総取りを狙う。
"""

from engine.models import (
    Action, PassAction, BroadcastAction, MarketCommitAction,
    PlayerState, Market, CardRank,
)
from engine.config import GameConfig
from bots.base import BotAgent
from bots.protocol import make_message
from bots.constants import BETRAYAL_LOAN, BETRAYAL_RANK_BOOST


class BetrayalBot(BotAgent):
    """裏切りBot: 談合acceptして1ランク上で総取り"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("Betrayal", seed)
        self._collusion_market: str | None = None
        self._collusion_card_rank: str | None = None
        self._accepted: bool = False
        self._current_round: int = 0

    def choose_loan(self, config: GameConfig) -> int:
        """500万を借入"""
        self._config = config
        return BETRAYAL_LOAN

    def negotiate(self, player_state: PlayerState, round_num: int,
                  turn: int, visible_state: dict) -> Action:
        """
        返済 → propose_collusionを検知したらacceptをbroadcast。
        """
        pid = player_state.player_id

        # 返済判定
        if self._config:
            repay = self._try_repay(player_state, round_num, self._config)
            if repay:
                return repay

        # ラウンドが変わったらリセット
        if round_num != self._current_round:
            self._current_round = round_num
            self._accepted = False
            self._collusion_market = None
            self._collusion_card_rank = None

        # 既にacceptした場合はpass
        if self._accepted:
            return PassAction(player_id=pid)

        # propose_collusionを探す
        intents = self._parse_messages(visible_state)
        for intent in intents:
            if intent.get("intent") == "propose_collusion":
                proposed_market = intent.get("market")
                proposed_card = intent.get("card", "HIGH_CARD")
                proposer = intent.get("proposer", intent.get("_sender", ""))

                # 自分自身の提案は無視
                if proposer == pid:
                    continue

                # 裏切りカード（提案より1ランク上）が手札にあるか確認
                try:
                    base_rank = CardRank[proposed_card]
                    betrayal_rank_val = base_rank.value + BETRAYAL_RANK_BOOST
                    # 手札にある最低限の裏切りカード
                    betrayal_card = self._find_card_at_least(
                        player_state.hand, betrayal_rank_val
                    )
                except (KeyError, ValueError):
                    betrayal_card = None

                if betrayal_card and proposed_market:
                    self._collusion_market = proposed_market
                    self._collusion_card_rank = betrayal_card.rank.name
                    self._accepted = True

                    msg = make_message("accept", ref=f"{proposer}_R{round_num}")
                    return BroadcastAction(player_id=pid, message=msg)

        return PassAction(player_id=pid)

    def commit(self, player_state: PlayerState, markets: list[Market],
               round_num: int, visible_state: dict) -> MarketCommitAction:
        """
        談合accept済み: 指定市場に裏切りカードで参加
        未accept: 最高賞金市場に手札最強カード
        """
        pid = player_state.player_id

        if self._accepted and self._collusion_market and self._collusion_card_rank:
            # 裏切り: 指定市場に1ランク上で参加
            card = self._find_card_by_rank_name(
                player_state.hand, self._collusion_card_rank
            )
            if card:
                return MarketCommitAction(
                    player_id=pid,
                    market_id=self._collusion_market,
                    card_rank=card.rank.name,
                )

        # 未accept or カードなし: 最高賞金市場+最強カード
        market = self._most_expensive_market(markets)
        card = self._highest_card(player_state.hand)
        if card is None:
            card = player_state.hand[0]

        return MarketCommitAction(
            player_id=pid,
            market_id=market.market_id,
            card_rank=card.rank.name,
        )

    def choose_double_up(self, player_state: PlayerState, prize_won: int,
                         round_num: int, visible_state: dict) -> bool:
        """常にDOUBLE — 攻撃的戦略"""
        return True
