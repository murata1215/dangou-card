"""
Bot 3: Strong-Card Save — 序盤弱カード温存、終盤強カード投入

R1-8は最低賞金市場に弱カードを降順投入。
R9-12は最高賞金市場に強カードを降順投入。
"""

from engine.models import Action, PassAction, MarketCommitAction, PlayerState, Market
from engine.config import GameConfig
from bots.base import BotAgent
from bots.constants import SCS_LOAN, SCS_EARLY_ROUNDS, SCS_LATE_START


class StrongCardSaveBot(BotAgent):
    """強カード温存Bot"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("StrongCardSave", seed)

    def choose_loan(self, config: GameConfig) -> int:
        """300万を借入"""
        self._config = config
        return SCS_LOAN

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
        序盤（R1-8）: 最低賞金市場 + 弱カード（昇順で最弱から消化）
        終盤（R9-12）: 最高賞金市場 + 強カード（降順で最強から投入）
        """
        if round_num <= SCS_EARLY_ROUNDS:
            # 序盤: 弱カードから消化、最低賞金市場
            market = self._cheapest_market(markets)
            card = self._lowest_card(player_state.hand)
        else:
            # 終盤: 強カードから投入、最高賞金市場
            market = self._most_expensive_market(markets)
            card = self._highest_card(player_state.hand)

        if card is None:
            card = player_state.hand[0]

        return MarketCommitAction(
            player_id=player_state.player_id,
            market_id=market.market_id,
            card_rank=card.rank.name,
        )

    def choose_double_up(self, player_state: PlayerState, prize_won: int,
                         round_num: int, visible_state: dict) -> bool:
        """序盤（R1-8）のみDOUBLE — 強カードが残っているので次Rも勝てる見込み"""
        return round_num <= SCS_EARLY_ROUNDS
