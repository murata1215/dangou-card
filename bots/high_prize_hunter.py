"""
Bot 4: High-Prize Hunter — 常に最高賞金市場を狙う

最高賞金市場に、賞金額に応じたランクのカードを投入。
"""

from engine.models import Action, PassAction, MarketCommitAction, PlayerState, Market
from engine.config import GameConfig
from bots.base import BotAgent
from bots.constants import HPH_LOAN, HPH_HIGH_PRIZE_RANK, HPH_MID_PRIZE_RANK


class HighPrizeHunterBot(BotAgent):
    """最高賞金市場ハンターBot"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("HighPrizeHunter", seed)

    def choose_loan(self, config: GameConfig) -> int:
        """500万を借入"""
        self._config = config
        return HPH_LOAN

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
        常に最高賞金市場を選択。
        賞金が高いほど強いカードを使う簡易ヒューリスティック。
        """
        market = self._most_expensive_market(markets)

        # 賞金額に応じてカードランク閾値を決定
        # 最高賞金市場の賞金がラウンド賞金の50%以上なら強カード
        sorted_markets = sorted(markets, key=lambda m: m.prize_pool)
        if market.prize_pool >= sorted_markets[-1].prize_pool * 0.8:
            # 高賞金: rank 7+ を狙う
            card = self._find_card_at_least(player_state.hand, HPH_HIGH_PRIZE_RANK)
        else:
            # 中賞金: rank 4+ を狙う
            card = self._find_card_at_least(player_state.hand, HPH_MID_PRIZE_RANK)

        # 該当ランクがなければ手札最強カードを使用
        if card is None:
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
        """高額賞金（50万以上）のみDOUBLE"""
        return prize_won >= 500_000
