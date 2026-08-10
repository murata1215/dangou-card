"""
BotAgent基底クラス

全ルールベースBotの共通基底。
PlayerAgentを継承し、Bot用RNG・手札ソート・メッセージ解析・返済ポリシーを提供。
"""

import random
from typing import Any

from engine.negotiation import PlayerAgent
from engine.models import PlayerState, Market, Card, CardRank, RepayAction, Action
from engine.config import GameConfig
from bots.protocol import parse_message
from bots.constants import REPAY_RESERVE


class BotAgent(PlayerAgent):
    """
    全Botの共通基底クラス

    Args:
        bot_type: Bot種別名（例: "Random", "Conservative"）
        seed: Bot固有の乱数シード
        enable_repay: 返済ポリシーを有効にするか（デフォルトTrue）
    """

    def __init__(self, bot_type: str, seed: int = 0, enable_repay: bool = True) -> None:
        self.bot_type = bot_type
        self.rng = random.Random(seed)
        self.enable_repay = enable_repay
        self._repaid_this_round: int = 0  # 当該ラウンドの返済済み額
        self._last_repay_round: int = 0   # 最後に返済したラウンド
        self._config: GameConfig | None = None  # choose_loan時に保持

    def _compute_repay_amount(
        self, player_state: PlayerState, round_num: int, config: GameConfig
    ) -> int:
        """
        返済可能額を計算する

        残ラウンド数×Entry Fee + 予備を手元に残し、超過分を返済に充てる。
        借金残高がゼロなら返済不要。FreeCash非適用（§2.4: システム向け支払い）。

        Args:
            player_state: 自分のプレイヤー状態
            round_num: 現在のラウンド番号
            config: ゲーム設定

        Returns:
            返済すべき額（0なら返済不要）
        """
        if player_state.debt_balance <= 0:
            return 0
        # 残りラウンドで必要なEntry Fee + 予備資金
        remaining_rounds = config.num_rounds - round_num
        reserve = remaining_rounds * config.entry_fee + REPAY_RESERVE
        # 返済に回せる額 = Cash - 予備
        available = player_state.cash - reserve
        if available <= 0:
            return 0
        # Debt残高以上は返済しない
        return min(available, player_state.debt_balance)

    def _try_repay(
        self, player_state: PlayerState, round_num: int, config: GameConfig
    ) -> Action | None:
        """
        返済アクションを生成する（返済不要ならNoneを返す）

        各ラウンドで1回だけ返済する。
        """
        if not self.enable_repay:
            return None
        if round_num == self._last_repay_round:
            return None  # このラウンドは返済済み
        amount = self._compute_repay_amount(player_state, round_num, config)
        if amount <= 0:
            return None
        self._last_repay_round = round_num
        self._repaid_this_round = amount
        return RepayAction(player_id=player_state.player_id, amount=amount)

    def _parse_messages(self, visible_state: dict) -> list[dict[str, Any]]:
        """
        visible_stateからbroadcast/dmメッセージを取得し、インテントをパースする

        Returns:
            パース成功したインテントのリスト
        """
        parsed: list[dict[str, Any]] = []
        for msg in visible_state.get("messages", []):
            intent = parse_message(msg.get("message", ""))
            if intent:
                intent["_sender"] = msg.get("sender", "")
                intent["_type"] = msg.get("type", "")
                parsed.append(intent)
        return parsed

    def _get_cards_sorted(
        self, hand: list[Card], ascending: bool = True
    ) -> list[Card]:
        """
        手札をランク順にソートする

        Args:
            hand: 手札
            ascending: Trueなら弱い順（デフォルト）

        Returns:
            ソートされたカードリスト
        """
        return sorted(hand, key=lambda c: (c.rank.value, c.card_id),
                       reverse=not ascending)

    def _find_card_by_rank_name(
        self, hand: list[Card], rank_name: str
    ) -> Card | None:
        """手札から指定ランク名のカードを探す"""
        try:
            rank = CardRank[rank_name]
        except KeyError:
            return None
        for c in hand:
            if c.rank == rank:
                return c
        return None

    def _find_card_at_least(
        self, hand: list[Card], min_rank: int
    ) -> Card | None:
        """手札からmin_rank以上の最低ランクカードを探す"""
        candidates = [c for c in hand if c.rank.value >= min_rank]
        if not candidates:
            return None
        return min(candidates, key=lambda c: (c.rank.value, c.card_id))

    def _lowest_card(self, hand: list[Card]) -> Card | None:
        """手札から最低ランクカードを返す。同ランク複数枚はseedでtie-break"""
        if not hand:
            return None
        min_rank = min(c.rank.value for c in hand)
        candidates = [c for c in hand if c.rank.value == min_rank]
        if len(candidates) == 1:
            return candidates[0]
        return self.rng.choice(candidates)

    def _highest_card(self, hand: list[Card]) -> Card | None:
        """手札から最高ランクカードを返す。同ランク複数枚はseedでtie-break"""
        if not hand:
            return None
        max_rank = max(c.rank.value for c in hand)
        candidates = [c for c in hand if c.rank.value == max_rank]
        if len(candidates) == 1:
            return candidates[0]
        return self.rng.choice(candidates)

    def _cheapest_market(self, markets: list[Market]) -> Market:
        """最低賞金の市場を返す。同額はseedでtie-break（同期防止）"""
        min_prize = min(m.prize_pool for m in markets)
        candidates = [m for m in markets if m.prize_pool == min_prize]
        if len(candidates) == 1:
            return candidates[0]
        return self.rng.choice(candidates)

    def _most_expensive_market(self, markets: list[Market]) -> Market:
        """最高賞金の市場を返す。同額はseedでtie-break（同期防止）"""
        max_prize = max(m.prize_pool for m in markets)
        candidates = [m for m in markets if m.prize_pool == max_prize]
        if len(candidates) == 1:
            return candidates[0]
        return self.rng.choice(candidates)

    def _mid_market(self, markets: list[Market]) -> Market:
        """中間賞金の市場を返す"""
        sorted_markets = sorted(markets, key=lambda m: m.prize_pool)
        return sorted_markets[len(sorted_markets) // 2]
