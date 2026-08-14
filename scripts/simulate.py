"""
シミュレーションランナー

ルールベースBot 8種による大量試行シミュレーションを実行し、
ゲームバランスを計測する。

使用方法:
    uv run python scripts/simulate.py --games 1000
    uv run python scripts/simulate.py --games 100 --roster "Random:8"
    uv run python scripts/simulate.py --games 1000 --no-logs
"""

import argparse
import csv
import os
import random
import sys
import time
from collections import defaultdict
from datetime import datetime
from multiprocessing import Pool
from pathlib import Path
from typing import Any

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game, GameResult
from bots import BOT_REGISTRY, DEFAULT_ROSTER


def parse_roster(roster_str: str) -> list[str]:
    """
    ロスター文字列をBot名リストに変換する

    形式: "Random:3,Collusion:2,Betrayal:3"
    → ["Random", "Random", "Random", "Collusion", "Collusion", ...]
    """
    result: list[str] = []
    for part in roster_str.split(","):
        part = part.strip()
        if ":" in part:
            name, count = part.rsplit(":", 1)
            name = name.strip()
            count_int = int(count.strip())
        else:
            name = part
            count_int = 1
        if name not in BOT_REGISTRY:
            raise ValueError(f"Unknown bot: {name}. Available: {list(BOT_REGISTRY.keys())}")
        result.extend([name] * count_int)
    return result


def create_config_for_roster(roster: list[str]) -> GameConfig:
    """
    ロスターのプレイヤー数に応じたGameConfigを生成する

    総賞金を人数比(N/20)で縮小。
    """
    num = len(roster)
    if num == 20:
        return GameConfig.default_20()
    elif num == 12:
        return GameConfig.default_12()
    elif num == 8:
        return GameConfig.default_8()
    else:
        # 任意人数: 比例縮小
        ratio = num / 20
        base_tiers = [1_200_000] * 4 + [1_600_000] * 4 + [2_000_000] * 4
        scaled_tiers = [int(t * ratio) for t in base_tiers]
        total = sum(scaled_tiers)
        return GameConfig(
            num_players=num,
            total_prize=total,
            prize_tiers=scaled_tiers,
        )


def run_single_game(args: tuple) -> dict[str, Any]:
    """
    1試合を実行する（multiprocessing用）

    Args:
        args: (game_index, base_seed, roster, config_dict, save_logs, output_dir)

    Returns:
        試合結果の辞書
    """
    game_index, base_seed, roster, config_dict, save_logs, output_dir = args
    game_seed = base_seed + game_index

    # 設定を復元
    config = GameConfig(**config_dict)

    # 座席シャッフル: seedから導出
    seat_rng = random.Random(game_seed)
    shuffled_roster = list(roster)
    seat_rng.shuffle(shuffled_roster)

    # エージェント生成（各BotにゲームseedからBot固有seedを導出）
    agents = {}
    bot_assignments = {}  # player_id → bot_type
    for i, bot_name in enumerate(shuffled_roster):
        pid = f"P{i + 1:02d}"
        bot_class = BOT_REGISTRY[bot_name]
        bot_seed = game_seed * 100 + i  # Bot固有seed
        agents[pid] = bot_class(seed=bot_seed)
        bot_assignments[pid] = bot_name

    # ロガー
    logger = EventLogger() if save_logs else None

    # ゲーム実行
    game = Game(config=config, agents=agents, seed=game_seed, logger=logger)
    result = game.run()

    # JSONLログ保存
    if save_logs and logger and output_dir:
        games_dir = Path(output_dir) / "games"
        games_dir.mkdir(parents=True, exist_ok=True)
        logger.save_jsonl(games_dir / f"game_{game_index + 1:04d}.jsonl")

    # S2: プレイヤー別倍掛け統計
    player_du_stats: dict[str, dict[str, int]] = {}
    for dep in result.double_up_deposits:
        pid_du = dep.player_id
        if pid_du not in player_du_stats:
            player_du_stats[pid_du] = {"count": 0, "success": 0, "forfeited": 0, "solo_success": 0}
        player_du_stats[pid_du]["count"] += 1
        if dep.resolved and dep.success:
            player_du_stats[pid_du]["success"] += 1
            if dep.from_solo_market:
                player_du_stats[pid_du]["solo_success"] += 1
        elif dep.resolved and not dep.success:
            player_du_stats[pid_du]["forfeited"] += dep.deposit_amount

    # 生還者順位
    survivor_ranks = {p.player_id: i + 1 for i, p in enumerate(result.survivors)}

    # 結果を行データに変換
    rows: list[dict[str, Any]] = []
    for pid, p in result.players.items():
        du = player_du_stats.get(pid, {})
        rows.append({
            "game_id": game_index + 1,
            "seed": game_seed,
            "player_id": pid,
            "bot_type": bot_assignments[pid],
            "initial_loan": p.initial_loan,
            "final_cash": p.cash,
            "final_debt": p.debt_balance,
            "survived": p.is_alive,
            "elimination_reason": p.elimination_reason or "",
            "elimination_round": p.elimination_round or "",
            "final_rank": survivor_ranks.get(pid, 0),
            "double_up_count": du.get("count", 0),
            "double_up_success": du.get("success", 0),
            "double_up_forfeited": du.get("forfeited", 0),
            "double_up_solo_success": du.get("solo_success", 0),
        })

    return {
        "game_id": game_index + 1,
        "rows": rows,
        "num_survivors": len(result.survivors),
        "round_snapshots": result.round_snapshots,
        "double_up_deposits": result.double_up_deposits,
    }


def generate_report(
    summary_rows: list[dict[str, Any]],
    roster: list[str],
    config: GameConfig,
    num_games: int,
    base_seed: int,
    elapsed: float,
    output_path: Path,
) -> None:
    """集計レポートをMarkdownで生成する"""

    # --- Bot別統計 ---
    bot_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {
        "games": 0, "survived": 0, "total_cash": 0,
        "total_loan": 0, "reasons": defaultdict(int),
        "elim_rounds": [],
    })

    survivor_counts: list[int] = []
    game_survivors: dict[int, int] = {}

    for row in summary_rows:
        bt = row["bot_type"]
        stats = bot_stats[bt]
        stats["games"] += 1
        stats["total_loan"] += row["initial_loan"]
        if row["survived"]:
            stats["survived"] += 1
            stats["total_cash"] += row["final_cash"]
        else:
            reason = row["elimination_reason"] or "unknown"
            stats["reasons"][reason] += 1
            elim_r = row["elimination_round"]
            if elim_r:
                stats["elim_rounds"].append(int(elim_r))

        gid = row["game_id"]
        if gid not in game_survivors:
            game_survivors[gid] = 0
        if row["survived"]:
            game_survivors[gid] += 1

    survivor_counts = list(game_survivors.values())

    # --- レポート生成 ---
    lines: list[str] = []
    lines.append("# シミュレーションレポート\n")
    lines.append(f"- 試合数: {num_games}")
    lines.append(f"- ベースseed: {base_seed}")
    lines.append(f"- プレイヤー数: {config.num_players}")
    lines.append(f"- 総賞金: {config.total_prize:,}円")
    lines.append(f"- 生還条件: 借金0 + 現金{config.survival_cash:,}円以上")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append(f"- ロスター: {', '.join(sorted(set(roster)))}")
    lines.append("")

    # Bot別統計テーブル
    lines.append("## Bot別統計\n")
    lines.append("| Bot | 試合数 | 生還率 | 平均最終資産 | 借入効率 | 破産 | 契約違反 | 条件未達 | 平均脱落R |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    for bt in sorted(bot_stats.keys()):
        s = bot_stats[bt]
        games = s["games"]
        rate = s["survived"] / games * 100 if games else 0
        avg_cash = s["total_cash"] / s["survived"] if s["survived"] else 0
        avg_loan = s["total_loan"] / games if games else 0
        efficiency = avg_cash / avg_loan if avg_loan else 0
        bankruptcy = s["reasons"].get("bankruptcy", 0)
        violation = s["reasons"].get("contract_violation", 0)
        not_met = s["reasons"].get("condition_not_met", 0)
        avg_elim_r = (sum(s["elim_rounds"]) / len(s["elim_rounds"])
                      if s["elim_rounds"] else 0)

        lines.append(
            f"| {bt} | {games} | {rate:.1f}% | {avg_cash:,.0f}円 | "
            f"{efficiency:.2f} | {bankruptcy} | {violation} | {not_met} | "
            f"{avg_elim_r:.1f} |"
        )

    # 経済指標
    lines.append("\n## 経済指標（全体）\n")
    total_loans = sum(r["initial_loan"] for r in summary_rows)
    total_final_cash_survivors = sum(
        r["final_cash"] for r in summary_rows if r["survived"]
    )
    total_survivors = sum(1 for r in summary_rows if r["survived"])
    total_eliminated = sum(1 for r in summary_rows if not r["survived"])

    lines.append(f"- 総借入額: {total_loans:,}円")
    lines.append(f"- 総賞金注入: {config.total_prize * num_games:,}円")
    lines.append(f"- 総Entry Fee: {config.entry_fee * config.num_players * config.num_rounds * num_games:,}円")
    lines.append(f"- 生還者数合計: {total_survivors}")
    lines.append(f"- 脱落者数合計: {total_eliminated}")
    lines.append(f"- 生還者平均最終資産: {total_final_cash_survivors / total_survivors:,.0f}円" if total_survivors else "- 生還者: 0")

    # 生還者数分布
    lines.append("\n### 生還者数分布\n")
    lines.append("| 生還者数 | 試合数 | 割合 |")
    lines.append("|---|---|---|")
    surv_dist: dict[int, int] = defaultdict(int)
    for c in survivor_counts:
        surv_dist[c] += 1
    for k in sorted(surv_dist.keys()):
        pct = surv_dist[k] / num_games * 100
        lines.append(f"| {k} | {surv_dist[k]} | {pct:.1f}% |")

    avg_survivors = sum(survivor_counts) / len(survivor_counts) if survivor_counts else 0
    lines.append(f"\n平均生還者数: {avg_survivors:.2f}")

    # バランス確認チェックリスト
    lines.append("\n## バランス確認チェックリスト\n")

    # 1. 特定Bot支配
    survival_rates = {
        bt: bot_stats[bt]["survived"] / bot_stats[bt]["games"] * 100
        if bot_stats[bt]["games"] else 0
        for bt in bot_stats
    }
    max_rate = max(survival_rates.values()) if survival_rates else 0
    min_rate = min(survival_rates.values()) if survival_rates else 0
    dominant = max_rate - min_rate > 50

    lines.append(f"- [{'x' if not dominant else ' '}] 特定Botが一方的に強くない "
                 f"（最高{max_rate:.1f}% vs 最低{min_rate:.1f}%、差{max_rate - min_rate:.1f}pp）")

    # 2. Random比で戦略Bot優位
    random_rate = survival_rates.get("Random", 0)
    strategic_better = any(
        r > random_rate for bt, r in survival_rates.items() if bt != "Random"
    )
    lines.append(f"- [{'x' if strategic_better else ' '}] Random比で戦略Botが有意に優位 "
                 f"（Random: {random_rate:.1f}%）")

    # 3. 生還率目標帯（8人中1.6〜4相当 = 20〜50%）
    target_low = config.num_players * 0.2
    target_high = config.num_players * 0.5
    in_target = target_low <= avg_survivors <= target_high
    lines.append(f"- [{'x' if in_target else ' '}] 生還率が目標帯 "
                 f"（平均{avg_survivors:.2f}人、目標{target_low:.1f}〜{target_high:.1f}）")

    # 4. StrongCardSave支配的でないか
    scs_rate = survival_rates.get("StrongCardSave", 0)
    scs_dominant = scs_rate > max_rate * 0.9 and scs_rate > 60
    lines.append(f"- [{'x' if not scs_dominant else ' '}] 強カード温存が支配的でない "
                 f"（StrongCardSave: {scs_rate:.1f}%）")

    # 5. Betrayal vs Collusion
    betrayal_rate = survival_rates.get("Betrayal", 0)
    collusion_rate = survival_rates.get("Collusion", 0)
    exploitation = betrayal_rate > collusion_rate * 2 and betrayal_rate > 50
    lines.append(f"- [{'x' if not exploitation else ' '}] BetrayalがCollusionを一方的に搾取していない "
                 f"（Betrayal: {betrayal_rate:.1f}% vs Collusion: {collusion_rate:.1f}%）")

    # 6. 途中脱落発生
    early_elim = any(
        r.get("elimination_round", 12) and r.get("elimination_round", 12) != "" and int(r.get("elimination_round", 12)) < 12
        for r in summary_rows if not r["survived"]
    )
    lines.append(f"- [{'x' if early_elim else ' '}] 12ラウンド前に脱落が発生している")

    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """メインエントリーポイント"""
    parser = argparse.ArgumentParser(description="談合カード シミュレーション")
    parser.add_argument("--games", type=int, default=1000, help="試合数（デフォルト: 1000）")
    parser.add_argument("--seed", type=int, default=42, help="ベースseed（デフォルト: 42）")
    parser.add_argument("--roster", type=str, default=None,
                        help='ロスター指定（例: "Random:8", "Collusion:4,Betrayal:4"）')
    parser.add_argument("--no-logs", action="store_true", help="JSONLログ出力をスキップ")
    parser.add_argument("--workers", type=int, default=4, help="並列ワーカー数（デフォルト: 4）")
    parser.add_argument("--prize-scale", type=float, default=1.0,
                        help="賞金倍率（デフォルト: 1.0）")
    parser.add_argument("--survival-cash", type=int, default=None,
                        help="生還条件の現金額を上書き（デフォルト: 設定値）")
    parser.add_argument("--prize-schedule", type=str, default="tiered",
                        choices=["tiered", "flat"],
                        help="賞金スケジュール: tiered(逓増, 既定) / flat(均等)")
    parser.add_argument("--ruleset", type=str, default="S1",
                        choices=["S1", "S2"],
                        help="ルールセット: S1(Season 1, 既定) / S2(Season 2)")
    args = parser.parse_args()

    # nice 10で実行（本番サーバー同居対策）
    try:
        os.nice(10)
    except (OSError, AttributeError):
        pass

    # ロスター決定
    if args.roster:
        roster = parse_roster(args.roster)
    else:
        roster = list(DEFAULT_ROSTER)

    # 設定生成
    if args.ruleset == "S2":
        config = GameConfig.baseline_v1_s2(len(roster))
    else:
        config = create_config_for_roster(roster)

    # 賞金倍率の適用
    if args.prize_scale != 1.0:
        scaled = [int(t * args.prize_scale) for t in config.prize_tiers]
        config = config.model_copy(update={
            "prize_tiers": scaled,
            "total_prize": sum(scaled),
        })

    # 生還条件の上書き
    if args.survival_cash is not None:
        config = config.model_copy(update={"survival_cash": args.survival_cash})

    # 賞金スケジュールの変換（flat: 総賞金を全ラウンド均等配分）
    if args.prize_schedule == "flat":
        total = config.total_prize
        per_round = total // config.num_rounds
        flat_tiers = [per_round] * config.num_rounds
        flat_tiers[-1] += total - sum(flat_tiers)  # 端数を最終ラウンドで調整
        config = config.model_copy(update={"prize_tiers": flat_tiers})

    # 出力ディレクトリ
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(f"logs/sim_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== 談合カード シミュレーション ===")
    print(f"ルールセット: {args.ruleset}")
    print(f"試合数: {args.games}")
    print(f"ベースseed: {args.seed}")
    print(f"プレイヤー数: {config.num_players}")
    print(f"ロスター: {', '.join(roster)}")
    print(f"総賞金: {config.total_prize:,}円")
    print(f"ワーカー数: {args.workers}")
    print(f"出力: {output_dir}")
    print("---")

    start_time = time.time()

    # 試合パラメータリスト作成
    config_dict = config.model_dump()
    task_args = [
        (i, args.seed, roster, config_dict, not args.no_logs, str(output_dir))
        for i in range(args.games)
    ]

    # 並列実行
    all_rows: list[dict[str, Any]] = []
    total_survivors = 0

    if args.workers > 1 and args.games > 1:
        with Pool(processes=args.workers) as pool:
            results = pool.map(run_single_game, task_args)
    else:
        results = [run_single_game(a) for a in task_args]

    for r in results:
        all_rows.extend(r["rows"])
        total_survivors += r["num_survivors"]

    elapsed = time.time() - start_time

    # summary.csv 出力
    csv_path = output_dir / "summary.csv"
    fieldnames = [
        "game_id", "seed", "player_id", "bot_type", "initial_loan",
        "final_cash", "final_debt", "survived", "elimination_reason",
        "elimination_round", "final_rank",
        "double_up_count", "double_up_success", "double_up_forfeited",
        "double_up_solo_success",
    ]
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_rows)

    # レポート生成
    report_path = output_dir / "report.md"
    generate_report(all_rows, roster, config, args.games, args.seed, elapsed, report_path)

    # サマリ表示
    avg_surv = total_survivors / args.games if args.games else 0
    print(f"\n=== 完了 ===")
    print(f"実行時間: {elapsed:.1f}秒（{elapsed / args.games * 1000:.1f}ms/試合）")
    print(f"平均生還者数: {avg_surv:.2f} / {config.num_players}")
    print(f"summary.csv: {csv_path}")
    print(f"report.md: {report_path}")
    if not args.no_logs:
        print(f"JSONLログ: {output_dir / 'games/'}")


if __name__ == "__main__":
    main()
