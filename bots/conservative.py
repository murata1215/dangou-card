"""
Bot 2: Conservative — 最低借入・最低市場・均等消化

金銭リスク行為ゼロ。カードは手札を均等に消化する。
"""

from engine.models import Action, PassAction, MarketCommitAction, PlayerState, Market
from engine.config import GameConfig
from bots.base import BotAgent
from bots.constants import CONSERVATIVE_LOAN


class ConservativeBot(BotAgent):
    """保守的Bot: 最低借入・最低賞金市場・カード均等消化"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("Conservative", seed)

    def choose_loan(self, config: GameConfig) -> int:
        """最低借入額を選択"""
        self._config = config
        return CONSERVATIVE_LOAN

    def negotiate(self, player_state: PlayerState, round_num: int,
                  turn: int, visible_state: dict) -> Action:
        """返済のみ"""
        if self._config:
            repay = self._try_repay(player_state, round_num, self._config)
            if repay:
                return repay
        return PassAction(player_id=player_state.player_id)

    def commit(self, player_state: PlayerState, markets: list[Market],
               round_num: int, visible_state: dict) -> MarketCommitAction:
        """
        最低賞金市場 + カード均等消化

        手札をランク順にソートし、ラウンド番号に対応するカードを使用。
        残り手札数とラウンドの対応で均等消化を実現。
        """
        market = self._cheapest_market(markets)
        # 手札を弱い順にソートし、先頭（最弱）から順に使う
        sorted_hand = self._get_cards_sorted(player_state.hand, ascending=True)
        card = sorted_hand[0] if sorted_hand else player_state.hand[0]

        return MarketCommitAction(
            player_id=player_state.player_id,
            market_id=market.market_id,
            card_rank=card.rank.name,
        )
