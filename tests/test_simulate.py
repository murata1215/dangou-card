"""
シミュレーション再現性テスト

同一seedで2回実行した結果が完全一致することを検証する。
"""

import pytest

from engine.config import GameConfig
from engine.game import Game
from bots import BOT_REGISTRY, DEFAULT_ROSTER


def _run_game(seed: int) -> dict:
    """1試合実行して結果をdictで返すヘルパー"""
    config = GameConfig.default_8()
    agents = {}
    for i, bot_name in enumerate(DEFAULT_ROSTER):
        pid = f"P{i + 1:02d}"
        bot_class = BOT_REGISTRY[bot_name]
        agents[pid] = bot_class(seed=seed * 100 + i)
    game = Game(config=config, agents=agents, seed=seed)
    result = game.run()
    # 再現性比較用のデータを抽出
    return {
        pid: {
            "cash": p.cash,
            "debt_balance": p.debt_balance,
            "is_alive": p.is_alive,
            "elimination_reason": p.elimination_reason,
            "elimination_round": p.elimination_round,
            "used_cards": [c.card_id for c in p.used_cards],
        }
        for pid, p in result.players.items()
    }


class TestSimulationReproducibility:
    """シミュレーション再現性テスト"""

    def test_same_seed_same_result(self):
        """同一seedで2回実行した結果が完全一致すること"""
        result1 = _run_game(seed=42)
        result2 = _run_game(seed=42)
        assert result1 == result2

    def test_different_seed_different_result(self):
        """異なるseedでは結果が異なること（確率的に）"""
        result1 = _run_game(seed=42)
        result2 = _run_game(seed=99)
        all_same = True
        for pid in result1:
            if result1[pid] != result2.get(pid):
                all_same = False
                break

    def test_mirror_match_seed_reproducibility(self):
        """
        SCS×4+Random×4のRoster Bで同一seedの2回実行が同一結果になること。
        個体seed導入後のミラーマッチ再現性を確認。
        """
        roster_b = ["StrongCardSave"] * 4 + ["Random"] * 4

        def _run_roster_b(seed: int) -> dict:
            import random as stdlib_random
            config = GameConfig.default_8()
            # flat賞金, prize_scale=2.0
            scaled = [int(t * 2.0) for t in config.prize_tiers]
            total = sum(scaled)
            per_round = total // config.num_rounds
            flat_tiers = [per_round] * config.num_rounds
            flat_tiers[-1] += total - sum(flat_tiers)
            config2 = config.model_copy(update={
                "prize_tiers": flat_tiers, "total_prize": total,
                "survival_cash": 2_000_000,
            })
            seat_rng = stdlib_random.Random(seed)
            shuffled = list(roster_b)
            seat_rng.shuffle(shuffled)
            agents = {}
            for i, bot_name in enumerate(shuffled):
                pid = f"P{i + 1:02d}"
                bot_class = BOT_REGISTRY[bot_name]
                agents[pid] = bot_class(seed=seed * 100 + i)
            game = Game(config=config2, agents=agents, seed=seed)
            result = game.run()
            return {
                pid: {
                    "cash": p.cash, "is_alive": p.is_alive,
                    "used_cards": [c.card_id for c in p.used_cards],
                }
                for pid, p in result.players.items()
            }

        r1 = _run_roster_b(42)
        r2 = _run_roster_b(42)
        assert r1 == r2
