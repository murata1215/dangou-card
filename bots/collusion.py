"""
Bot 6: Collusion（談合・遵守）— 毎R談合を提案し、必ず遵守する

毎ラウンドの最初の手番でpropose_collusionをbroadcast。
acceptが1体以上で成立とみなし、指定市場にHIGH_CARDで参加。
不成立なら中間賞金市場に均等消化で参加。
"""

from engine.models import (
    Action, PassAction, BroadcastAction, MarketCommitAction,
    PlayerState, Market,
)
from engine.config import GameConfig
from bots.base import BotAgent
from bots.protocol import make_message, parse_message
from bots.constants import COLLUSION_LOAN, COLLUSION_CARD


class CollusionBot(BotAgent):
    """談合遵守Bot: 毎R談合提案、成立すれば必ず遵守"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("Collusion", seed)
        # 当該ラウンドで談合が成立したか
        self._collusion_market: str | None = None
        self._collusion_accepted: bool = False
        self._proposed_this_round: bool = False
        self._current_round: int = 0

    def choose_loan(self, config: GameConfig) -> int:
        """300万を借入"""
        self._config = config
        return COLLUSION_LOAN

    def negotiate(self, player_state: PlayerState, round_num: int,
                  turn: int, visible_state: dict) -> Action:
        """
        turn 1: 返済 → propose_collusion をbroadcast
        turn 2+: acceptを確認し、成立判定
        """
        pid = player_state.player_id

        # 返済判定（他のアクションより先に実行）
        if self._config:
            repay = self._try_repay(player_state, round_num, self._config)
            if repay:
                return repay

        # ラウンドが変わったらリセット
        if round_num != self._current_round:
            self._current_round = round_num
            self._proposed_this_round = False
            self._collusion_accepted = False
            self._collusion_market = None

        # turn 1: 談合提案
        if turn == 1 and not self._proposed_this_round:
            self._proposed_this_round = True
            # 中間賞金市場を提案先に選ぶ
            markets_info = visible_state.get("markets", [])
            if markets_info:
                sorted_m = sorted(markets_info, key=lambda m: m.get("prize_pool", 0))
                target = sorted_m[len(sorted_m) // 2]
                self._collusion_market = target.get("market_id", "M02")
            else:
                self._collusion_market = "M02"

            msg = make_message(
                "propose_collusion",
                market=self._collusion_market,
                card=COLLUSION_CARD,
                round=round_num,
                proposer=pid,
            )
            return BroadcastAction(
                player_id=pid,
                message=msg,
            )

        # turn 2+: 受信メッセージからacceptを確認
        if not self._collusion_accepted:
            intents = self._parse_messages(visible_state)
            for intent in intents:
                if (intent.get("intent") == "accept"
                        and intent.get("_sender") != pid):
                    self._collusion_accepted = True
                    break

        return PassAction(player_id=pid)

    def commit(self, player_state: PlayerState, markets: list[Market],
               round_num: int, visible_state: dict) -> MarketCommitAction:
        """
        談合成立: 提案市場にHIGH_CARDで参加
        不成立: 中間賞金市場にカード均等消化
        """
        pid = player_state.player_id

        if self._collusion_accepted and self._collusion_market:
            # 談合成立: 指定市場+指定カード
            card = self._find_card_by_rank_name(player_state.hand, COLLUSION_CARD)
            if card:
                return MarketCommitAction(
                    player_id=pid,
                    market_id=self._collusion_market,
                    card_rank=card.rank.name,
                )

        # 不成立 or 指定カードなし: 中間市場+最弱カード
        market = self._mid_market(markets)
        card = self._lowest_card(player_state.hand)
        if card is None:
            card = player_state.hand[0]

        return MarketCommitAction(
            player_id=pid,
            market_id=market.market_id,
            card_rank=card.rank.name,
        )
