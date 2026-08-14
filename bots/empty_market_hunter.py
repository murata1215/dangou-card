"""
Bot 5: Empty-Market Hunter（空き巣）— 前Rの参加者最少市場を狙う

最低ランクカードで空き巣を狙い、効率的に賞金を稼ぐ。
"""

from engine.models import Action, PassAction, MarketCommitAction, PlayerState, Market
from engine.config import GameConfig
from bots.base import BotAgent
from bots.constants import EMH_LOAN


class EmptyMarketHunterBot(BotAgent):
    """空き巣Bot: 前ラウンドの参加者最少市場を追跡"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("EmptyMarketHunter", seed)
        # 前ラウンドで最も参加者が少なかった市場ラベル（初回はNone）
        self._target_market_id: str | None = None

    def choose_loan(self, config: GameConfig) -> int:
        """300万を借入"""
        self._config = config
        return EMH_LOAN

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
        前ラウンドの参加者が最少だった市場を狙う。
        初回（R1）はランダム選択。最低ランクカードを使う。
        """
        # 前ラウンドの市場結果から参加者数を推定
        # used_cardsの変化から各市場の参加者数を推測するのは困難なので、
        # visible_stateの市場情報を使用
        market_ids = [m.market_id for m in markets]

        if self._target_market_id and self._target_market_id in market_ids:
            target_id = self._target_market_id
        else:
            # 初回またはターゲット市場がない場合はランダム
            target_id = self.rng.choice(market_ids)

        # 次ラウンド用: 今回選んだ市場の反対側（最も人が少なそうな市場）を推定
        # 簡易ヒューリスティック: 賞金が最も低い市場は参加者が少ない傾向
        cheapest = self._cheapest_market(markets)
        self._target_market_id = cheapest.market_id

        card = self._lowest_card(player_state.hand)
        if card is None:
            card = player_state.hand[0]

        return MarketCommitAction(
            player_id=player_state.player_id,
            market_id=target_id,
            card_rank=card.rank.name,
        )

    def choose_double_up(self, player_state: PlayerState, prize_won: int,
                         round_num: int, visible_state: dict) -> bool:
        """常にDOUBLE — 空き巣成功で次Rも勝てる自信"""
        return True
