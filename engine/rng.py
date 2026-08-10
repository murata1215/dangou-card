"""
乱数管理モジュール

全ゲームの乱数を単一seedから導出し、同一seedで完全再現可能にする。
用途別にサブRNGを生成して独立性を保つ。
"""

import random


class GameRng:
    """
    ゲーム用乱数ジェネレータ

    単一のseedから全ゲーム内乱数を再現可能に生成する。
    用途ごとに独立したRNGインスタンスを返す。

    Args:
        seed: ゲームのマスターシード
    """

    def __init__(self, seed: int) -> None:
        self.seed = seed
        self._master = random.Random(seed)
        # 用途別にサブシードを生成して独立性を確保
        self._negotiation_rng = random.Random(self._master.randint(0, 2**63))
        self._market_rng = random.Random(self._master.randint(0, 2**63))
        self._general_rng = random.Random(self._master.randint(0, 2**63))

    def shuffle_turn_order(self, player_ids: list[str]) -> list[str]:
        """
        Negotiationの手番順をランダムシャッフルする（§5.1）

        毎巡ランダム手番。同一seedなら同じ順序が再現される。

        Args:
            player_ids: シャッフル対象のプレイヤーIDリスト

        Returns:
            シャッフルされたプレイヤーIDリスト（元リストは変更しない）
        """
        shuffled = list(player_ids)
        self._negotiation_rng.shuffle(shuffled)
        return shuffled

    def distribute_prize_random(self, total: int, num_markets: int) -> list[int]:
        """
        ラウンド賞金をランダムに3市場へ配分する

        market_distribution="random" 時に使用。
        ランダムに分割しつつ、合計が一致するよう端数調整。

        Args:
            total: ラウンド総賞金
            num_markets: 市場数

        Returns:
            各市場への配分額リスト
        """
        # ランダムな重みを生成して配分
        weights = [self._market_rng.random() for _ in range(num_markets)]
        weight_sum = sum(weights)
        raw = [int(total * w / weight_sum) for w in weights]
        # 端数を最後の市場に付与
        remainder = total - sum(raw)
        raw[-1] += remainder
        return raw
