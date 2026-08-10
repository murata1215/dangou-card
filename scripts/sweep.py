"""
パラメータ掃引スクリプト

prize_scale × survival_cash のグリッド掃引を実行し、
目標帯（8人中1.6〜4人生還）に入るパラメータセルを特定する。

使用方法:
    uv run python scripts/sweep.py
    uv run python scripts/sweep.py --games-per-cell 50
"""

import argparse
import os
import sys
import time
from collections import defaultdict
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import GameConfig
from engine.game import Game
from bots import BOT_REGISTRY, DEFAULT_ROSTER

import random


# 掃引グリッド
PRIZE_SCALES = [0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
SURVIVAL_CASH_VALUES = [2_000_000, 3_000_000, 4_000_000, 5_000_000]


def run_cell_game(args: tuple) -> dict[str, Any]:
    """1試合を実行する（掃引用）"""
    game_index, game_seed, roster, config_dict = args

    config = GameConfig(**config_dict)

    # 座席シャッフル
    seat_rng = random.Random(game_seed)
    shuffled_roster = list(roster)
    seat_rng.shuffle(shuffled_roster)

    # エージェント生成
    agents = {}
    bot_assignments = {}
    for i, bot_name in enumerate(shuffled_roster):
        pid = f"P{i + 1:02d}"
        bot_class = BOT_REGISTRY[bot_name]
        agents[pid] = bot_class(seed=game_seed * 100 + i)
        bot_assignments[pid] = bot_name

    # ゲーム実行（ログなし）
    game = Game(config=config, agents=agents, seed=game_seed)
    result = game.run()

    # Bot別の生還情報を返す
    bot_survived: dict[str, bool] = {}
    for pid, p in result.players.items():
        bot_survived[bot_assignments[pid]] = p.is_alive

    return {
        "num_survivors": len(result.survivors),
        "bot_survived": bot_survived,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="談合カード パラメータ掃引")
    parser.add_argument("--games-per-cell", type=int, default=100,
                        help="各セルの試合数（デフォルト: 100）")
    parser.add_argument("--seed", type=int, default=42, help="ベースseed")
    parser.add_argument("--workers", type=int, default=4, help="並列ワーカー数")
    args = parser.parse_args()

    try:
        os.nice(10)
    except (OSError, AttributeError):
        pass

    roster = list(DEFAULT_ROSTER)
    base_config = GameConfig.default_8()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(f"logs/sweep_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== パラメータ掃引 ===")
    print(f"グリッド: {len(PRIZE_SCALES)} prize_scale × {len(SURVIVAL_CASH_VALUES)} survival_cash = {len(PRIZE_SCALES) * len(SURVIVAL_CASH_VALUES)} セル")
    print(f"各セル: {args.games_per_cell}試合")
    print(f"合計: {len(PRIZE_SCALES) * len(SURVIVAL_CASH_VALUES) * args.games_per_cell}試合")
    print(f"出力: {output_dir}")
    print("---")

    start_time = time.time()

    # 結果格納
    results: list[dict[str, Any]] = []

    cell_index = 0
    for ps in PRIZE_SCALES:
        for sc in SURVIVAL_CASH_VALUES:
            cell_index += 1
            # 設定を生成
            scaled_tiers = [int(t * ps) for t in base_config.prize_tiers]
            config = base_config.model_copy(update={
                "prize_tiers": scaled_tiers,
                "total_prize": sum(scaled_tiers),
                "survival_cash": sc,
            })
            config_dict = config.model_dump()

            # seedオフセット: セルごとに異なる基点
            seed_offset = args.seed + cell_index * 10000

            task_args = [
                (i, seed_offset + i, roster, config_dict)
                for i in range(args.games_per_cell)
            ]

            # 並列実行
            if args.workers > 1:
                with Pool(processes=args.workers) as pool:
                    cell_results = pool.map(run_cell_game, task_args)
            else:
                cell_results = [run_cell_game(a) for a in task_args]

            # 集計
            total_surv = sum(r["num_survivors"] for r in cell_results)
            avg_surv = total_surv / args.games_per_cell

            # Bot別生還率
            bot_surv_count: dict[str, int] = defaultdict(int)
            bot_game_count: dict[str, int] = defaultdict(int)
            for r in cell_results:
                for bt, survived in r["bot_survived"].items():
                    bot_game_count[bt] += 1
                    if survived:
                        bot_surv_count[bt] += 1

            bot_rates = {
                bt: bot_surv_count[bt] / bot_game_count[bt] * 100
                if bot_game_count[bt] > 0 else 0
                for bt in sorted(bot_game_count.keys())
            }

            # 生還率の分散（Bot間の公平性指標）
            rates_list = list(bot_rates.values())
            rate_variance = (
                sum((r - sum(rates_list) / len(rates_list)) ** 2 for r in rates_list) / len(rates_list)
                if rates_list else 0
            )

            results.append({
                "prize_scale": ps,
                "survival_cash": sc,
                "avg_survivors": avg_surv,
                "bot_rates": bot_rates,
                "rate_variance": rate_variance,
                "total_prize": config.total_prize,
            })

            print(f"  [{cell_index:2d}/{len(PRIZE_SCALES) * len(SURVIVAL_CASH_VALUES)}] "
                  f"scale={ps:.2f} surv_cash={sc // 10000}万: "
                  f"avg_surv={avg_surv:.2f}")

    elapsed = time.time() - start_time

    # レポート生成
    report_path = output_dir / "sweep_report.md"
    _generate_sweep_report(results, args, elapsed, base_config, report_path)

    print(f"\n=== 完了 ===")
    print(f"実行時間: {elapsed:.1f}秒")
    print(f"レポート: {report_path}")


def _generate_sweep_report(
    results: list[dict[str, Any]],
    args: Any,
    elapsed: float,
    base_config: GameConfig,
    output_path: Path,
) -> None:
    """掃引レポートを生成する"""
    lines: list[str] = []
    lines.append("# パラメータ掃引レポート\n")
    lines.append(f"- 各セル試合数: {args.games_per_cell}")
    lines.append(f"- ベースseed: {args.seed}")
    lines.append(f"- プレイヤー数: {base_config.num_players}")
    lines.append(f"- ベース総賞金: {base_config.total_prize:,}円")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append("")

    # 目標帯
    target_low = base_config.num_players * 0.2   # 1.6
    target_high = base_config.num_players * 0.5  # 4.0

    # 結果テーブル
    lines.append("## 掃引結果\n")
    lines.append("| prize_scale | survival_cash | 総賞金 | 平均生還者数 | 目標帯 | Bot間分散 |")
    lines.append("|---|---|---|---|---|---|")

    best_cell = None
    best_distance = float("inf")

    for r in results:
        in_target = target_low <= r["avg_survivors"] <= target_high
        marker = "**✓**" if in_target else ""
        lines.append(
            f"| {r['prize_scale']:.2f} | {r['survival_cash'] // 10000}万 | "
            f"{r['total_prize']:,}円 | {r['avg_survivors']:.2f} | {marker} | "
            f"{r['rate_variance']:.1f} |"
        )

        # 目標帯中央(2.8)に最も近いセルを推奨候補に
        target_center = (target_low + target_high) / 2
        distance = abs(r["avg_survivors"] - target_center)
        if in_target and distance < best_distance:
            best_distance = distance
            best_cell = r

    # 目標帯にセルがなければ最も近いものを選ぶ
    if best_cell is None:
        target_center = (target_low + target_high) / 2
        best_cell = min(results, key=lambda r: abs(r["avg_survivors"] - target_center))

    # Bot別生還率（推奨セル）
    lines.append(f"\n## 推奨パラメータ\n")
    lines.append(f"- **prize_scale: {best_cell['prize_scale']:.2f}**")
    lines.append(f"- **survival_cash: {best_cell['survival_cash'] // 10000}万円**")
    lines.append(f"- 総賞金: {best_cell['total_prize']:,}円")
    lines.append(f"- 平均生還者数: {best_cell['avg_survivors']:.2f}")
    lines.append("")

    lines.append("### 推奨セルのBot別生還率\n")
    lines.append("| Bot | 生還率 |")
    lines.append("|---|---|")
    for bt, rate in sorted(best_cell["bot_rates"].items()):
        lines.append(f"| {bt} | {rate:.1f}% |")

    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    main()
