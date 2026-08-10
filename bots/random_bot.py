"""
Bot 1: Random — 全ランダム行動

借入額・市場・カードをすべてランダムに選択。交渉しない。
ベースラインとして使用。
"""

from engine.models import Action, PassAction, MarketCommitAction, PlayerState, Market
from engine.config import GameConfig
from bots.base import BotAgent


class RandomBot(BotAgent):
    """全ランダム行動Bot"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("Random", seed)

    def choose_loan(self, config: GameConfig) -> int:
        """借入額をmin〜maxの一様乱数で選択"""
        self._config = config
        return self.rng.randint(config.loan_min, config.loan_max)

    def negotiate(self, player_state: PlayerState, round_num: int,
                  turn: int, visible_state: dict) -> Action:
        """返済のみ。それ以外はpass"""
        if self._config:
            repay = self._try_repay(player_state, round_num, self._config)
            if repay:
                return repay
        return PassAction(player_id=player_state.player_id)

    def commit(self, player_state: PlayerState, markets: list[Market],
               round_num: int, visible_state: dict) -> MarketCommitAction:
        """ランダムな市場+ランダムなカードを選択"""
        market = self.rng.choice(markets)
        card = self.rng.choice(player_state.hand)
        return MarketCommitAction(
            player_id=player_state.player_id,
            market_id=market.market_id,
            card_rank=card.rank.name,
        )
