#!/usr/bin/env python3
"""
高騰条件変更の前後比較シミュレーション

条件A: surge_full_participation_max_alive=0（従来挙動）
条件B: surge_full_participation_max_alive=4（新挙動: 4人以下は全員参加必須）

各1,000試合で生存者数別の高騰発生率を比較する。
"""

import argparse
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from bots import BOT_REGISTRY, DEFAULT_ROSTER


CONDITIONS = [
    {"label": "A_legacy", "surge_full_participation_max_alive": 0},
    {"label": "B_full4", "surge_full_participation_max_alive": 4},
]


def run_single_game(args: tuple) -> dict[str, Any]:
    game_index, base_seed, roster, config_dict, label = args
    game_seed = base_seed + game_index
    config = GameConfig(**config_dict)

    seat_rng = random.Random(game_seed)
    shuffled = list(roster)
    seat_rng.shuffle(shuffled)

    agents = {}
    for i, bot_name in enumerate(shuffled):
        pid = f"P{i+1:02d}"
        agents[pid] = BOT_REGISTRY[bot_name](seed=game_seed * 100 + i)

    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=game_seed, logger=logger)
    result = game.run()

    # 生存者数別の高騰発生を集計
    surge_data: dict[int, dict[str, int]] = {}  # alive_count → {surges, rounds}
    for snap in result.round_snapshots:
        ac = snap.get("alive_count", 8)
        sc = snap.get("surge_count", 0)
        if ac not in surge_data:
            surge_data[ac] = {"surges": 0, "rounds": 0}
        surge_data[ac]["surges"] += sc
        surge_data[ac]["rounds"] += 1

    return {
        "label": label,
        "num_survivors": len(result.survivors),
        "surge_data": surge_data,
        "round_snapshots": result.round_snapshots,
    }


def main():
    parser = argparse.ArgumentParser(description="高騰条件変更シミュレーション")
    parser.add_argument("--games", type=int, default=1000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    try:
        os.nice(10)
    except (OSError, AttributeError):
        pass

    roster = list(DEFAULT_ROSTER)
    base_config = GameConfig.baseline_v1_s2(len(roster))

    print(f"=== 高騰条件変更シミュレーション ===")
    print(f"条件数: {len(CONDITIONS)}")
    print(f"各条件: {args.games} 試合")
    print("---")

    start = time.time()
    all_results: dict[str, list] = {}

    for cond in CONDITIONS:
        label = cond["label"]
        print(f"\n[{label}] surge_full_participation_max_alive={cond['surge_full_participation_max_alive']} ...")

        config = base_config.model_copy(update={
            "surge_full_participation_max_alive": cond["surge_full_participation_max_alive"],
        })
        config_dict = config.model_dump()

        task_args = [(i, args.seed, roster, config_dict, label) for i in range(args.games)]

        if args.workers > 1:
            with Pool(processes=args.workers) as pool:
                results = pool.map(run_single_game, task_args)
        else:
            results = [run_single_game(a) for a in task_args]

        all_results[label] = results
        avg_surv = sum(r["num_survivors"] for r in results) / args.games
        print(f"  平均生還: {avg_surv:.2f}")

    elapsed = time.time() - start

    # レポート生成
    lines = []
    lines.append("# 高騰条件変更シミュレーションレポート\n")
    lines.append("> Bot シミュレーションの限界: ルールベース Bot の保守的な行動パターンに基づく。\n")
    lines.append(f"- 各条件: {args.games} 試合")
    lines.append(f"- ベース seed: {args.seed}")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append(f"- 条件A: surge_full_participation_max_alive=0（従来挙動）")
    lines.append(f"- 条件B: surge_full_participation_max_alive=4（4人以下は全員参加必須）")
    lines.append("")

    # 生存者数別の高騰発生率
    lines.append("## 生存者数別の高騰発生率\n")
    lines.append("| 生存者数 | A_legacy | B_full4 | 変化 |")
    lines.append("|---|---|---|---|")

    for alive_n in range(8, 0, -1):
        row = f"| {alive_n}人 |"
        vals = {}
        for cond in CONDITIONS:
            label = cond["label"]
            total_surges = 0
            total_rounds = 0
            for r in all_results[label]:
                sd = r["surge_data"].get(alive_n, {})
                total_surges += sd.get("surges", 0)
                total_rounds += sd.get("rounds", 0)
            rate = (total_surges / total_rounds * 100) if total_rounds > 0 else 0
            vals[label] = rate
            row += f" {rate:.1f}% ({total_surges}/{total_rounds}) |"

        # 変化
        a = vals.get("A_legacy", 0)
        b = vals.get("B_full4", 0)
        diff = b - a
        row += f" {diff:+.1f}pp |"
        lines.append(row)

    lines.append("")

    # 平均生還者数
    lines.append("## 平均生還者数\n")
    lines.append("| 条件 | 平均生還者数 |")
    lines.append("|---|---|")
    for cond in CONDITIONS:
        label = cond["label"]
        avg = sum(r["num_survivors"] for r in all_results[label]) / args.games
        lines.append(f"| {label} | {avg:.2f} |")
    lines.append("")

    # 評価
    lines.append("## 評価\n")

    # 4人以下の高騰率が0%かチェック
    for alive_n in [3, 4]:
        for cond in CONDITIONS:
            if cond["label"] != "B_full4":
                continue
            total_s = sum(r["surge_data"].get(alive_n, {}).get("surges", 0) for r in all_results[cond["label"]])
            total_r = sum(r["surge_data"].get(alive_n, {}).get("rounds", 0) for r in all_results[cond["label"]])
            rate = (total_s / total_r * 100) if total_r > 0 else 0
            if rate < 1.0:
                lines.append(f"- **{alive_n}人時の高騰率が {rate:.1f}%** — 3市場制で全員が同一市場に集中する確率が極めて低いため、事実上発生しない")
                lines.append(f"  - 緩和案: 境界人数を `surge_full_participation_max_alive=3` に下げる（4人時は通常判定に戻す）")
                lines.append(f"  - 別案: 4人以下では `alive_count - 1` 人以上で高騰（1人の棄権を許容）")

    lines.append("")

    report_path = Path("doc/surge_condition_change_report.md")
    report_path.write_text("\n".join(lines), encoding="utf-8")

    devrelay = Path(".devrelay-output")
    devrelay.mkdir(exist_ok=True)
    import shutil
    shutil.copy(report_path, devrelay / "surge_condition_change_report.md")

    print(f"\n=== 完了 ({elapsed:.1f}秒) ===")
    print(f"レポート: {report_path}")


if __name__ == "__main__":
    main()
