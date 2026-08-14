"""
Bot 8: Honey-Pot — 市場宣伝で集客し、強カードで刈り取る

claimでbroadcast（「M02が熱い」等）し、自分もその市場へ行く。
2ラウンドに1回は強カードを投入して勝利を狙う。
"""

from engine.models import (
    Action, PassAction, BroadcastAction, MarketCommitAction,
    PlayerState, Market,
)
from engine.config import GameConfig
from bots.base import BotAgent
from bots.protocol import make_message
from bots.constants import (
    HONEYPOT_LOAN, HONEYPOT_STRONG_INTERVAL, HONEYPOT_STRONG_RANK,
)


class HoneyPotBot(BotAgent):
    """ハニーポットBot: 市場宣伝+強カード刈り取り"""

    def __init__(self, seed: int = 0) -> None:
        super().__init__("HoneyPot", seed)
        self._target_market: str | None = None
        self._claimed_this_round: bool = False
        self._current_round: int = 0

    def choose_loan(self, config: GameConfig) -> int:
        """700万を借入"""
        self._config = config
        return HONEYPOT_LOAN

    def negotiate(self, player_state: PlayerState, round_num: int,
                  turn: int, visible_state: dict) -> Action:
        """
        返済 → turn 1: claimをbroadcast
        """
        pid = player_state.player_id

        # 返済判定
        if self._config:
            repay = self._try_repay(player_state, round_num, self._config)
            if repay:
                return repay

        # ラウンドリセット
        if round_num != self._current_round:
            self._current_round = round_num
            self._claimed_this_round = False
            self._target_market = None

        if turn == 1 and not self._claimed_this_round:
            self._claimed_this_round = True
            # ターゲット: ランダムに市場を選ぶ（他Bot を引き寄せるため）
            markets_info = visible_state.get("markets", [])
            if markets_info:
                target = self.rng.choice(markets_info)
                self._target_market = target.get("market_id", "M01")
            else:
                self._target_market = "M01"

            msg = make_message(
                "claim",
                text=f"{self._target_market}が熱い！強カードが集まっている",
            )
            return BroadcastAction(player_id=pid, message=msg)

        return PassAction(player_id=pid)

    def commit(self, player_state: PlayerState, markets: list[Market],
               round_num: int, visible_state: dict) -> MarketCommitAction:
        """
        claim先の市場に参加。
        強カードインターバル（2Rに1回）では強カード（rank 7+）を投入。
        それ以外は最弱カード。
        """
        pid = player_state.player_id

        # ターゲット市場決定
        market_ids = [m.market_id for m in markets]
        if self._target_market and self._target_market in market_ids:
            target_id = self._target_market
        else:
            target_id = self.rng.choice(market_ids)

        # 強カード投入判定（2Rに1回）
        use_strong = (round_num % HONEYPOT_STRONG_INTERVAL == 0)

        if use_strong:
            card = self._find_card_at_least(
                player_state.hand, HONEYPOT_STRONG_RANK
            )
            if card is None:
                # 強カードがなければ最強カードを使用
                card = self._highest_card(player_state.hand)
        else:
            card = self._lowest_card(player_state.hand)

        if card is None:
            card = player_state.hand[0]

        return MarketCommitAction(
            player_id=pid,
            market_id=target_id,
            card_rank=card.rank.name,
        )

    def choose_double_up(self, player_state: PlayerState, prize_won: int,
                         round_num: int, visible_state: dict) -> bool:
        """偶数ラウンドのみDOUBLE"""
        return round_num % 2 == 0
