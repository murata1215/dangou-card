"""
抽象プレイヤーインターフェース・スタブモジュール

Negotiationフェイズのプレイヤーインターフェースを定義する。
本ステップではLLMは使用せず、スタブ/手動注入で動作させる。
"""

from abc import ABC, abstractmethod

from engine.models import (
    Action, PassAction, MarketCommitAction, PlayerState, Market,
    CardRank,
)
from engine.config import GameConfig
from engine.cards import get_lowest_rank_card


class PlayerAgent(ABC):
    """
    プレイヤーエージェントの抽象基底クラス

    各AIエージェント（LLM, Bot, スタブ）はこのクラスを継承し、
    借入選択・交渉・コミットの3メソッドを実装する。
    """

    @abstractmethod
    def choose_loan(self, config: GameConfig) -> int:
        """
        ゲーム開始前の借入額を選択する（§2.1）

        Args:
            config: ゲーム設定（loan_min〜loan_maxの範囲内で選択）

        Returns:
            借入額（整数）
        """
        ...

    @abstractmethod
    def negotiate(
        self,
        player_state: PlayerState,
        round_num: int,
        turn: int,
        visible_state: dict,
    ) -> Action:
        """
        Negotiationフェイズで1アクションを選択する

        Args:
            player_state: 自分のプレイヤー状態
            round_num: ラウンド番号
            turn: 現在の巡数（1〜10）
            visible_state: 公開情報の辞書

        Returns:
            選択したAction
        """
        ...

    @abstractmethod
    def commit(
        self,
        player_state: PlayerState,
        markets: list[Market],
        round_num: int,
        visible_state: dict,
    ) -> MarketCommitAction:
        """
        Commitフェイズで市場+カードを選択する

        Args:
            player_state: 自分のプレイヤー状態
            markets: 当該ラウンドの市場リスト
            round_num: ラウンド番号
            visible_state: 公開情報の辞書

        Returns:
            MarketCommitAction
        """
        ...

    def choose_double_up(
        self,
        player_state: PlayerState,
        prize_won: int,
        round_num: int,
        visible_state: dict,
    ) -> bool:
        """
        S2: 倍掛け選択（§S2.4）

        市場賞金獲得後、TAKEかDOUBLEかを選択する。
        デフォルトはTAKE（False）。サブクラスでオーバーライド可能。

        Args:
            player_state: 自分のプレイヤー状態
            prize_won: 獲得した賞金額
            round_num: 現在のラウンド番号
            visible_state: 公開情報の辞書

        Returns:
            True=DOUBLE, False=TAKE
        """
        return False

    def reflect(
        self,
        player_state: PlayerState,
        round_num: int,
        visible_state: dict,
    ) -> None:
        """
        引き継ぎメモリ（Handover Memory）: ラウンド終了後の振り返り

        次ラウンドへ持ち越す自由記述メモを書く機会。デフォルトは何もしない
        （Bot/StubAgentはメモを使わないため、サブクラスでオーバーライド不要）。
        LLMAgentのみオーバーライドしてmemoryを更新する。

        Args:
            player_state: 自分のプレイヤー状態
            round_num: 終了したラウンドの番号
            visible_state: 公開情報の辞書（当ラウンドの結果を含む）
        """
        return None


class StubAgent(PlayerAgent):
    """
    固定行動スタブエージェント（ドライラン用）

    - 借入: 最低額(120万)
    - 交渉: 常にpass
    - コミット: 最低ランクのカード + 最低賞金の市場
    """

    def choose_loan(self, config: GameConfig) -> int:
        """最低借入額を選択"""
        return config.loan_min

    def negotiate(
        self,
        player_state: PlayerState,
        round_num: int,
        turn: int,
        visible_state: dict,
    ) -> Action:
        """常にpassを返す"""
        return PassAction(player_id=player_state.player_id)

    def commit(
        self,
        player_state: PlayerState,
        markets: list[Market],
        round_num: int,
        visible_state: dict,
    ) -> MarketCommitAction:
        """
        最低ランクカード + 最低賞金市場を選択

        自動代行Commitと同じ優先順位（§4.4）を使用。
        """
        # 最低賞金の市場を選択
        cheapest_market = min(markets, key=lambda m: (m.prize_pool, m.market_id))

        # 最低ランクのカードを選択
        lowest_card = get_lowest_rank_card(player_state.hand)
        if lowest_card is None:
            # 手札が空（通常ありえないが安全策）
            raise ValueError(f"{player_state.player_id} has no cards")

        return MarketCommitAction(
            player_id=player_state.player_id,
            market_id=cheapest_market.market_id,
            card_rank=lowest_card.rank.name,
        )
