"""
賞金スケジュールA/B比較実験スクリプト

条件A: 逓増傾斜(tiered) vs 条件B: フラット(flat)
同一総賞金・同一seedで1000試合ずつ実行し、StrongCardSave支配の原因を分析する。

使用方法:
    uv run python scripts/compare_schedules.py
    uv run python scripts/compare_schedules.py --games 100
"""

import argparse
import os
import random
import re
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
from engine.models import CardRank
from bots import BOT_REGISTRY, DEFAULT_ROSTER


def run_game_with_stats(args: tuple) -> dict[str, Any]:
    """
    1試合を実行し、集計用データを含めて返す

    Args:
        args: (game_index, game_seed, roster, config_dict)

    Returns:
        試合結果 + カード使用データ + 賞金獲得データ
    """
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

    # ロガー（イベント集計用に常に有効）
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=game_seed, logger=logger)
    result = game.run()

    # --- イベントログから集計データを抽出 ---

    # カード使用データ: (bot_type, card_rank_value, round_num)
    card_usage: list[tuple[str, int, int]] = []
    # 賞金獲得データ: (bot_type, round_num, amount)
    prize_won: list[tuple[str, int, int]] = []

    for event in logger.events:
        if event.event_type == "COMMIT":
            pid = event.data.get("player_id", "")
            card_id = event.data.get("card", "")
            # card_idからランクを抽出（例: "ROYAL_FLUSH_1" → "ROYAL_FLUSH"）
            rank_name = re.sub(r'_\d+$', '', card_id)
            try:
                rank_val = CardRank[rank_name].value
            except KeyError:
                continue
            bt = bot_assignments.get(pid, "")
            card_usage.append((bt, rank_val, event.round_num))

        elif event.event_type == "MARKET_RESULT":
            winners = event.data.get("winners", [])
            prize = event.data.get("prize_per_winner", 0)
            for winner_pid in winners:
                bt = bot_assignments.get(winner_pid, "")
                prize_won.append((bt, event.round_num, prize))

    # 生還データ
    bot_survived: dict[str, bool] = {}
    for pid, p in result.players.items():
        bot_survived[bot_assignments[pid]] = p.is_alive

    return {
        "num_survivors": len(result.survivors),
        "bot_survived": bot_survived,
        "card_usage": card_usage,
        "prize_won": prize_won,
    }


def run_condition(
    label: str,
    config: GameConfig,
    roster: list[str],
    num_games: int,
    base_seed: int,
    workers: int,
) -> dict[str, Any]:
    """1条件の全試合を実行して集計する"""
    config_dict = config.model_dump()
    task_args = [
        (i, base_seed + i, roster, config_dict)
        for i in range(num_games)
    ]

    if workers > 1 and num_games > 1:
        with Pool(processes=workers) as pool:
            results = pool.map(run_game_with_stats, task_args)
    else:
        results = [run_game_with_stats(a) for a in task_args]

    # --- 集計 ---
    total_survivors = sum(r["num_survivors"] for r in results)
    avg_survivors = total_survivors / num_games

    # Bot別生還率
    bot_surv_count: dict[str, int] = defaultdict(int)
    bot_game_count: dict[str, int] = defaultdict(int)
    for r in results:
        for bt, survived in r["bot_survived"].items():
            bot_game_count[bt] += 1
            if survived:
                bot_surv_count[bt] += 1
    bot_rates = {
        bt: bot_surv_count[bt] / bot_game_count[bt] * 100
        if bot_game_count[bt] > 0 else 0
        for bt in sorted(bot_game_count.keys())
    }

    # カードランク別平均使用ラウンド
    rank_rounds: dict[int, list[int]] = defaultdict(list)
    for r in results:
        for bt, rank_val, rnd in r["card_usage"]:
            rank_rounds[rank_val].append(rnd)
    avg_rank_round = {
        rank_val: sum(rounds) / len(rounds) if rounds else 0
        for rank_val, rounds in sorted(rank_rounds.items())
    }

    # R期間別獲得賞金
    period_prize: dict[str, int] = {"R1-4": 0, "R5-8": 0, "R9-12": 0}
    total_prize_won = 0
    for r in results:
        for bt, rnd, amount in r["prize_won"]:
            total_prize_won += amount
            if rnd <= 4:
                period_prize["R1-4"] += amount
            elif rnd <= 8:
                period_prize["R5-8"] += amount
            else:
                period_prize["R9-12"] += amount

    period_pct = {}
    for period, amount in period_prize.items():
        period_pct[period] = amount / total_prize_won * 100 if total_prize_won else 0

    return {
        "label": label,
        "avg_survivors": avg_survivors,
        "bot_rates": bot_rates,
        "avg_rank_round": avg_rank_round,
        "period_prize": period_prize,
        "period_pct": period_pct,
        "total_prize_won": total_prize_won,
    }


def generate_compare_report(
    result_a: dict, result_b: dict,
    num_games: int, base_seed: int, elapsed: float,
    config_a: GameConfig, config_b: GameConfig,
    output_path: Path,
) -> None:
    """比較レポートを生成する"""
    lines: list[str] = []
    lines.append("# 賞金スケジュール A/B 比較レポート\n")
    lines.append(f"- 条件A: **逓増傾斜 (tiered)** — R1-4:低 / R5-8:中 / R9-12:高")
    lines.append(f"- 条件B: **フラット (flat)** — 全ラウンド均等配分")
    lines.append(f"- 共通: prize_scale=2.0, survival_cash=200万, 8人ロスター, seed={base_seed}")
    lines.append(f"- 各条件: {num_games}試合")
    lines.append(f"- 総賞金: A={config_a.total_prize:,}円 / B={config_b.total_prize:,}円")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append("")

    # Bot別生還率比較
    lines.append("## Bot別生還率\n")
    lines.append("| Bot | A (tiered) | B (flat) | 差分 (B-A) |")
    lines.append("|---|---|---|---|")

    all_bots = sorted(set(list(result_a["bot_rates"].keys()) + list(result_b["bot_rates"].keys())))
    random_a = result_a["bot_rates"].get("Random", 0)
    random_b = result_b["bot_rates"].get("Random", 0)

    for bt in all_bots:
        ra = result_a["bot_rates"].get(bt, 0)
        rb = result_b["bot_rates"].get(bt, 0)
        diff = rb - ra
        marker = " **←**" if bt == "StrongCardSave" else ""
        lines.append(f"| {bt}{marker} | {ra:.1f}% | {rb:.1f}% | {diff:+.1f}pp |")

    lines.append(f"\n**平均生還者数**: A={result_a['avg_survivors']:.2f} / B={result_b['avg_survivors']:.2f}")

    # StrongCardSave注目
    scs_a = result_a["bot_rates"].get("StrongCardSave", 0)
    scs_b = result_b["bot_rates"].get("StrongCardSave", 0)
    lines.append(f"\n### StrongCardSave生還率: A={scs_a:.1f}% → B={scs_b:.1f}%\n")

    # Random比の優位度
    lines.append("## Random比の各Bot優位度\n")
    lines.append("| Bot | A (vs Random) | B (vs Random) |")
    lines.append("|---|---|---|")
    for bt in all_bots:
        if bt == "Random":
            continue
        adv_a = result_a["bot_rates"].get(bt, 0) - random_a
        adv_b = result_b["bot_rates"].get(bt, 0) - random_b
        lines.append(f"| {bt} | {adv_a:+.1f}pp | {adv_b:+.1f}pp |")

    # カードランク別平均使用ラウンド
    lines.append("\n## カードランク別平均使用ラウンド\n")
    lines.append("| ランク | カード名 | A (tiered) | B (flat) |")
    lines.append("|---|---|---|---|")
    rank_names = {v.value: v.name for v in CardRank}
    for rank_val in sorted(rank_names.keys()):
        name = rank_names[rank_val]
        avg_a = result_a["avg_rank_round"].get(rank_val, 0)
        avg_b = result_b["avg_rank_round"].get(rank_val, 0)
        lines.append(f"| {rank_val} | {name} | R{avg_a:.1f} | R{avg_b:.1f} |")

    # R期間別獲得賞金比率
    lines.append("\n## R期間別獲得賞金比率\n")
    lines.append("| 期間 | A (tiered) | B (flat) |")
    lines.append("|---|---|---|")
    for period in ["R1-4", "R5-8", "R9-12"]:
        pct_a = result_a["period_pct"].get(period, 0)
        pct_b = result_b["period_pct"].get(period, 0)
        lines.append(f"| {period} | {pct_a:.1f}% | {pct_b:.1f}% |")

    # 判定
    lines.append("\n## 判定\n")
    if scs_b <= 70:
        lines.append(f"StrongCardSaveがフラットで **{scs_b:.1f}%** まで低下（tiered: {scs_a:.1f}%）。")
        lines.append("逓増傾斜が温存戦略の主因であることが確認された。")
        lines.append("**→ フラット採用候補**")
    elif scs_b > 90:
        lines.append(f"StrongCardSaveがフラットでも **{scs_b:.1f}%** を維持（tiered: {scs_a:.1f}%）。")
        lines.append("**→ 賞金傾斜は主因ではない。**")
        lines.append("\n次に疑うべき仮説:")
        lines.append("1. **カード構造**: 高ランクカードの勝率が終盤に限らず圧倒的")
        lines.append("2. **StrongCardSaveの序盤低コスト戦略**: 序盤最低市場×弱カードでEntry Feeの回収率が高く、資金効率で他Botに勝る")
    else:
        lines.append(f"StrongCardSaveがフラットで **{scs_b:.1f}%**（tiered: {scs_a:.1f}%）。中間的な結果。")
        lines.append(f"逓増傾斜は一因だが、他の要素も寄与している。")
        lines.append(f"差分: {scs_a - scs_b:.1f}pp の低下。")
        if scs_a - scs_b > 10:
            lines.append("傾斜の影響は有意。フラット化で格差は縮小するが、追加の調整が必要。")
        else:
            lines.append("傾斜の影響は限定的。カード構造またはBot戦略の問題が大きい。")

    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="賞金スケジュール A/B 比較実験")
    parser.add_argument("--games", type=int, default=1000, help="各条件の試合数（デフォルト: 1000）")
    parser.add_argument("--seed", type=int, default=42, help="ベースseed")
    parser.add_argument("--workers", type=int, default=4, help="並列ワーカー数")
    args = parser.parse_args()

    try:
        os.nice(10)
    except (OSError, AttributeError):
        pass

    roster = list(DEFAULT_ROSTER)

    # ベース設定: 8人版, prize_scale=2.0, survival_cash=200万
    base_config = GameConfig.default_8()
    scaled = [int(t * 2.0) for t in base_config.prize_tiers]
    base_config = base_config.model_copy(update={
        "prize_tiers": scaled,
        "total_prize": sum(scaled),
        "survival_cash": 2_000_000,
    })

    # 条件A: 逓増傾斜（そのまま）
    config_a = base_config

    # 条件B: フラット（総賞金を均等配分）
    total = base_config.total_prize
    per_round = total // base_config.num_rounds
    flat_tiers = [per_round] * base_config.num_rounds
    flat_tiers[-1] += total - sum(flat_tiers)
    config_b = base_config.model_copy(update={"prize_tiers": flat_tiers})

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(f"logs/compare_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== 賞金スケジュール A/B 比較実験 ===")
    print(f"各条件: {args.games}試合")
    print(f"条件A (tiered): {config_a.prize_tiers[:4]}...{config_a.prize_tiers[-4:]}")
    print(f"条件B (flat):   {config_b.prize_tiers[:4]}...{config_b.prize_tiers[-4:]}")
    print(f"総賞金: A={config_a.total_prize:,} / B={config_b.total_prize:,}")
    print(f"出力: {output_dir}")
    print("---")

    start_time = time.time()

    print("条件A (tiered) 実行中...")
    result_a = run_condition("tiered", config_a, roster, args.games, args.seed, args.workers)
    print(f"  → 平均生還: {result_a['avg_survivors']:.2f}, SCS: {result_a['bot_rates'].get('StrongCardSave', 0):.1f}%")

    print("条件B (flat) 実行中...")
    result_b = run_condition("flat", config_b, roster, args.games, args.seed, args.workers)
    print(f"  → 平均生還: {result_b['avg_survivors']:.2f}, SCS: {result_b['bot_rates'].get('StrongCardSave', 0):.1f}%")

    elapsed = time.time() - start_time

    # レポート生成
    report_path = output_dir / "compare_report.md"
    generate_compare_report(result_a, result_b, args.games, args.seed, elapsed,
                            config_a, config_b, report_path)

    print(f"\n=== 完了 ===")
    print(f"実行時間: {elapsed:.1f}秒")
    print(f"レポート: {report_path}")


if __name__ == "__main__":
    main()
