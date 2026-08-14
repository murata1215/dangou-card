#!/usr/bin/env python3
"""
強制返済 k パラメータ感度シミュレーション

v0.7 の強制返済（mandatory_repay）の k=0/1/2 と無効の4条件を
各1,000試合で計測し、序盤破産・生存者推移・借入額相関を分析する。

使い方:
    uv run python scripts/simulate_mandatory_repay.py
    uv run python scripts/simulate_mandatory_repay.py --games 500 --workers 8
"""

import argparse
import csv
import math
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
from engine.game import Game, GameResult
from bots import BOT_REGISTRY, DEFAULT_ROSTER


# ---------------------------------------------------------------------------
# 条件定義
# ---------------------------------------------------------------------------

CONDITIONS = [
    {"label": "A_no_repay", "mandatory_repay_enabled": False, "mandatory_repay_k": 0},
    {"label": "B_k0", "mandatory_repay_enabled": True, "mandatory_repay_k": 0},
    {"label": "C_k1", "mandatory_repay_enabled": True, "mandatory_repay_k": 1},
    {"label": "D_k2", "mandatory_repay_enabled": True, "mandatory_repay_k": 2},
]


# ---------------------------------------------------------------------------
# 1試合実行
# ---------------------------------------------------------------------------

def run_single_game(args: tuple) -> dict[str, Any]:
    game_index, base_seed, roster, config_dict, condition_label = args
    game_seed = base_seed + game_index

    config = GameConfig(**config_dict)

    seat_rng = random.Random(game_seed)
    shuffled_roster = list(roster)
    seat_rng.shuffle(shuffled_roster)

    agents = {}
    bot_assignments = {}
    for i, bot_name in enumerate(shuffled_roster):
        pid = f"P{i + 1:02d}"
        bot_class = BOT_REGISTRY[bot_name]
        bot_seed = game_seed * 100 + i
        agents[pid] = bot_class(seed=bot_seed)
        bot_assignments[pid] = bot_name

    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=game_seed, logger=logger)
    result = game.run()

    # 破産イベント抽出
    bankruptcies = []
    for ev in logger.events:
        if ev.event_type == "MANDATORY_REPAY_FAILED":
            bankruptcies.append({
                "player_id": ev.data["player_id"],
                "round": ev.round_num,
                "required": ev.data.get("required", 0),
                "cash": ev.data.get("cash", 0),
            })
        elif ev.event_type == "BANKRUPTCY":
            bankruptcies.append({
                "player_id": ev.data["player_id"],
                "round": ev.round_num,
                "required": 0,
                "cash": 0,
            })

    # 高騰イベント
    surge_by_round: dict[int, int] = {}
    for ev in logger.events:
        if ev.event_type == "MARKET_RESULT" and ev.data.get("surged"):
            surge_by_round[ev.round_num] = surge_by_round.get(ev.round_num, 0) + 1

    # 生還者順位
    survivor_ranks = {p.player_id: i + 1 for i, p in enumerate(result.survivors)}

    rows = []
    for pid, p in result.players.items():
        rows.append({
            "condition": condition_label,
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
        })

    return {
        "condition": condition_label,
        "game_id": game_index + 1,
        "rows": rows,
        "num_survivors": len(result.survivors),
        "round_snapshots": result.round_snapshots,
        "bankruptcies": bankruptcies,
        "surge_by_round": surge_by_round,
    }


# ---------------------------------------------------------------------------
# 集計＆レポート
# ---------------------------------------------------------------------------

def generate_report(
    all_results: dict[str, list],
    all_rows: dict[str, list],
    config_base: GameConfig,
    num_games: int,
    base_seed: int,
    elapsed: float,
    output_path: Path,
) -> None:
    lines: list[str] = []
    lines.append("# v0.7 強制返済シミュレーションレポート\n")
    lines.append("> **Bot シミュレーションの限界**: ルールベース Bot は戦略的に振る舞わないため、")
    lines.append("> LLM 戦の挙動を予測するものではない。Bot は借入額を固定的に選び、返済も")
    lines.append("> 機械的なポリシーに従う。LLM は状況に応じて借入額・返済・交渉を変えるため、")
    lines.append("> 実際の破産率・生存者数はこのデータとは異なる可能性が高い。\n")
    lines.append(f"- 条件数: {len(CONDITIONS)}")
    lines.append(f"- 各条件の試合数: {num_games}")
    lines.append(f"- 総試合数: {num_games * len(CONDITIONS)}")
    lines.append(f"- ベース seed: {base_seed}")
    lines.append(f"- プレイヤー数: {config_base.num_players}")
    lines.append(f"- 生還条件: 借金0 + 現金{config_base.survival_cash:,}円以上")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append(f"- ロスター: {', '.join(sorted(set(DEFAULT_ROSTER)))}")
    lines.append("")

    # ---- 1. 序盤破産 ----
    lines.append("## 1. 序盤破産の頻度\n")

    # 破産ラウンド分布
    lines.append("### 破産ラウンド分布\n")
    header = "| ラウンド |"
    sep = "|---|"
    for cond in CONDITIONS:
        header += f" {cond['label']} |"
        sep += "---|"
    lines.append(header)
    lines.append(sep)

    bankruptcy_by_round: dict[str, dict[int, int]] = {}
    for cond in CONDITIONS:
        label = cond["label"]
        bankruptcy_by_round[label] = defaultdict(int)
        for r in all_results[label]:
            for b in r["bankruptcies"]:
                bankruptcy_by_round[label][b["round"]] += 1

    for rn in range(1, 13):
        row = f"| R{rn} |"
        for cond in CONDITIONS:
            count = bankruptcy_by_round[cond["label"]].get(rn, 0)
            row += f" {count} |"
        lines.append(row)

    # 合計行
    row = "| **合計** |"
    for cond in CONDITIONS:
        total = sum(bankruptcy_by_round[cond["label"]].values())
        row += f" **{total}** |"
    lines.append(row)
    lines.append("")

    # 破産統計
    lines.append("### 破産統計\n")
    lines.append("| 指標 |" + "".join(f" {c['label']} |" for c in CONDITIONS))
    lines.append("|---|" + "---|" * len(CONDITIONS))

    for metric_name, metric_fn in [
        ("1試合あたり平均破産人数", lambda label: sum(len(r["bankruptcies"]) for r in all_results[label]) / num_games),
        ("破産1人以上の試合割合", lambda label: sum(1 for r in all_results[label] if r["bankruptcies"]) / num_games * 100),
        ("全員R12到達の試合割合", lambda label: sum(1 for r in all_results[label] if not r["bankruptcies"]) / num_games * 100),
    ]:
        row = f"| {metric_name} |"
        for cond in CONDITIONS:
            val = metric_fn(cond["label"])
            if isinstance(val, float):
                row += f" {val:.2f}{'%' if '割合' in metric_name else ''} |"
            else:
                row += f" {val} |"
        lines.append(row)
    lines.append("")

    # ---- 2. 生存者推移 ----
    lines.append("## 2. 生存者数の推移\n")
    lines.append("### 各ラウンド終了時の平均生存者数\n")
    header = "| ラウンド |"
    sep = "|---|"
    for cond in CONDITIONS:
        header += f" {cond['label']} |"
        sep += "---|"
    lines.append(header)
    lines.append(sep)

    for rn in range(1, 13):
        row = f"| R{rn} |"
        for cond in CONDITIONS:
            label = cond["label"]
            alive_counts = []
            for r in all_results[label]:
                snaps = r["round_snapshots"]
                if rn - 1 < len(snaps):
                    alive_counts.append(snaps[rn - 1].get("alive_count", 8))
                else:
                    alive_counts.append(0)
            avg = sum(alive_counts) / len(alive_counts) if alive_counts else 0
            row += f" {avg:.2f} |"
        lines.append(row)
    lines.append("")

    # 生存者が N 人以下になった試合の割合
    lines.append("### 生存者が N 人以下になった試合の割合\n")
    for threshold in [7, 6, 5, 4]:
        header2 = f"| ラウンド |"
        sep2 = "|---|"
        for cond in CONDITIONS:
            header2 += f" {cond['label']} |"
            sep2 += "---|"
        lines.append(f"**≤{threshold}人:**\n")
        lines.append(header2)
        lines.append(sep2)
        for rn in range(1, 13):
            row = f"| R{rn} |"
            for cond in CONDITIONS:
                label = cond["label"]
                count = 0
                for r in all_results[label]:
                    snaps = r["round_snapshots"]
                    if rn - 1 < len(snaps) and snaps[rn - 1].get("alive_count", 8) <= threshold:
                        count += 1
                pct = count / num_games * 100
                row += f" {pct:.1f}% |"
            lines.append(row)
        lines.append("")

    # 高騰発生の生存者数別集計
    lines.append("### 市場高騰の発生状況（生存者数別）\n")
    lines.append("| 生存者数 |" + "".join(f" {c['label']} |" for c in CONDITIONS))
    lines.append("|---|" + "---|" * len(CONDITIONS))
    for alive_n in range(8, 0, -1):
        row = f"| {alive_n}人 |"
        for cond in CONDITIONS:
            label = cond["label"]
            surge_count = 0
            round_count = 0
            for r in all_results[label]:
                for rn_idx, snap in enumerate(r["round_snapshots"]):
                    if snap.get("alive_count", 8) == alive_n:
                        surge_count += snap.get("surge_count", 0)
                        round_count += 1
            rate = (surge_count / round_count * 100) if round_count > 0 else 0
            row += f" {rate:.1f}% ({surge_count}/{round_count}) |"
        lines.append(row)
    lines.append("")

    # ---- 3. 借入額と最終順位の相関 ----
    lines.append("## 3. 借入額と最終順位の相関\n")

    for cond in CONDITIONS:
        label = cond["label"]
        rows_c = all_rows[label]
        loans = [r["initial_loan"] for r in rows_c]
        # final_rank: 0=脱落 → 脱落者は num_players+1 として相関計算
        ranks = [r["final_rank"] if r["final_rank"] > 0 else config_base.num_players + 1 for r in rows_c]

        # 相関係数
        n = len(loans)
        if n > 1:
            mean_l = sum(loans) / n
            mean_r = sum(ranks) / n
            cov = sum((l - mean_l) * (r - mean_r) for l, r in zip(loans, ranks)) / n
            std_l = math.sqrt(sum((l - mean_l) ** 2 for l in loans) / n)
            std_r = math.sqrt(sum((r - mean_r) ** 2 for r in ranks) / n)
            corr = cov / (std_l * std_r) if std_l > 0 and std_r > 0 else 0
        else:
            corr = 0

        lines.append(f"### {label}: 相関係数 = {corr:.4f}\n")

        # 四分位分析
        sorted_loans = sorted(set(loans))
        if len(sorted_loans) >= 4:
            q1 = sorted_loans[len(sorted_loans) // 4]
            q2 = sorted_loans[len(sorted_loans) // 2]
            q3 = sorted_loans[3 * len(sorted_loans) // 4]
            quartiles = [
                ("Q1 (最低〜25%)", lambda l: l <= q1),
                ("Q2 (25%〜50%)", lambda l: q1 < l <= q2),
                ("Q3 (50%〜75%)", lambda l: q2 < l <= q3),
                ("Q4 (75%〜最高)", lambda l: l > q3),
            ]
        else:
            quartiles = [("全体", lambda l: True)]

        lines.append("| 借入額区分 | 人数 | 生還率 | 平均最終資産(生還者) | 平均借入額 |")
        lines.append("|---|---|---|---|---|")
        for qname, qfilter in quartiles:
            subset = [r for r in rows_c if qfilter(r["initial_loan"])]
            cnt = len(subset)
            survived = [r for r in subset if r["survived"]]
            surv_rate = len(survived) / cnt * 100 if cnt else 0
            avg_cash = sum(r["final_cash"] for r in survived) / len(survived) if survived else 0
            avg_loan = sum(r["initial_loan"] for r in subset) / cnt if cnt else 0
            lines.append(f"| {qname} | {cnt} | {surv_rate:.1f}% | {avg_cash:,.0f}円 | {avg_loan:,.0f}円 |")

        # 最低借入(120万)の成績
        min_loan = config_base.loan_min
        min_loan_rows = [r for r in rows_c if r["initial_loan"] == min_loan]
        if min_loan_rows:
            cnt_ml = len(min_loan_rows)
            surv_ml = [r for r in min_loan_rows if r["survived"]]
            rate_ml = len(surv_ml) / cnt_ml * 100
            avg_cash_ml = sum(r["final_cash"] for r in surv_ml) / len(surv_ml) if surv_ml else 0
            lines.append(f"\n**最低借入({min_loan//10000}万円)**: {cnt_ml}人, 生還率{rate_ml:.1f}%, 平均最終資産{avg_cash_ml:,.0f}円\n")
        lines.append("")

    # ---- 4. k 感度分析 ----
    lines.append("## 4. k 感度分析\n")
    lines.append("| 指標 |" + "".join(f" {c['label']} |" for c in CONDITIONS))
    lines.append("|---|" + "---|" * len(CONDITIONS))

    metrics = [
        ("平均生還者数", lambda label: sum(r["num_survivors"] for r in all_results[label]) / num_games),
        ("平均破産人数/試合", lambda label: sum(len(r["bankruptcies"]) for r in all_results[label]) / num_games),
        ("R1-R3破産件数", lambda label: sum(1 for r in all_results[label] for b in r["bankruptcies"] if b["round"] <= 3)),
        ("R4-R8破産件数", lambda label: sum(1 for r in all_results[label] for b in r["bankruptcies"] if 4 <= b["round"] <= 8)),
        ("R9-R11破産件数", lambda label: sum(1 for r in all_results[label] for b in r["bankruptcies"] if 9 <= b["round"] <= 11)),
        ("R12脱落件数(条件未達)", lambda label: sum(1 for r in all_rows[label] if r["elimination_reason"] == "condition_not_met")),
    ]
    for mname, mfn in metrics:
        row = f"| {mname} |"
        for cond in CONDITIONS:
            val = mfn(cond["label"])
            row += f" {val:.2f} |" if isinstance(val, float) else f" {val} |"
        lines.append(row)
    lines.append("")

    # k 推奨
    lines.append("### k 推奨値\n")

    # 自動判定ロジック
    avg_survivors = {
        c["label"]: sum(r["num_survivors"] for r in all_results[c["label"]]) / num_games
        for c in CONDITIONS
    }
    early_bankruptcy = {
        c["label"]: sum(1 for r in all_results[c["label"]] for b in r["bankruptcies"] if b["round"] <= 3)
        for c in CONDITIONS
    }

    lines.append("| 条件 | 平均生還者数 | R1-R3破産 | 評価 |")
    lines.append("|---|---|---|---|")
    for cond in CONDITIONS:
        label = cond["label"]
        avg_s = avg_survivors[label]
        early_b = early_bankruptcy[label]
        if not cond["mandatory_repay_enabled"]:
            eval_text = "baseline"
        elif avg_s < 1.0:
            eval_text = "破産多すぎ（k 引き上げ推奨）"
        elif avg_s > 4.0:
            eval_text = "緩すぎ（脱落圧力不足）"
        elif early_b > num_games * 0.3:
            eval_text = "序盤破産多すぎ（k 引き上げ推奨）"
        else:
            eval_text = "適正帯"
        lines.append(f"| {label} | {avg_s:.2f} | {early_b} | {eval_text} |")

    # 推奨値の決定
    best_k = None
    for cond in CONDITIONS[1:]:  # k=0,1,2
        label = cond["label"]
        k_val = cond["mandatory_repay_k"]
        avg_s = avg_survivors[label]
        early_b = early_bankruptcy[label]
        if 1.0 <= avg_s <= 4.0 and early_b <= num_games * 0.3:
            if best_k is None:
                best_k = k_val
    if best_k is None:
        best_k = 2  # 全条件で破産多すぎなら最大緩和
        lines.append(f"\n**推奨: k = {best_k}**（全条件で破産が多いため最大緩和を推奨。ただし k=2 でも過剰な場合はルール見直しが必要）\n")
    else:
        lines.append(f"\n**推奨: k = {best_k}**（適正帯に入る最小の k 値）\n")

    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# メイン
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="v0.7 強制返済シミュレーション")
    parser.add_argument("--games", type=int, default=1000, help="各条件の試合数")
    parser.add_argument("--seed", type=int, default=42, help="ベースseed")
    parser.add_argument("--workers", type=int, default=4, help="並列ワーカー数")
    args = parser.parse_args()

    try:
        os.nice(10)
    except (OSError, AttributeError):
        pass

    roster = list(DEFAULT_ROSTER)
    base_config = GameConfig.baseline_v1_s2(len(roster))

    print(f"=== v0.7 強制返済シミュレーション ===")
    print(f"条件数: {len(CONDITIONS)}")
    print(f"各条件: {args.games} 試合")
    print(f"総試合数: {args.games * len(CONDITIONS)}")
    print(f"ベースseed: {args.seed}")
    print(f"ワーカー数: {args.workers}")
    print("---")

    start_time = time.time()

    all_results: dict[str, list] = {}
    all_rows: dict[str, list] = {}

    for cond in CONDITIONS:
        label = cond["label"]
        print(f"\n[{label}] mandatory_repay_enabled={cond['mandatory_repay_enabled']}, k={cond['mandatory_repay_k']} ...")

        config = base_config.model_copy(update={
            "mandatory_repay_enabled": cond["mandatory_repay_enabled"],
            "mandatory_repay_k": cond["mandatory_repay_k"],
        })
        config_dict = config.model_dump()

        task_args = [
            (i, args.seed, roster, config_dict, label)
            for i in range(args.games)
        ]

        if args.workers > 1 and args.games > 1:
            with Pool(processes=args.workers) as pool:
                results = pool.map(run_single_game, task_args)
        else:
            results = [run_single_game(a) for a in task_args]

        all_results[label] = results
        rows = []
        for r in results:
            rows.extend(r["rows"])
        all_rows[label] = rows

        total_surv = sum(r["num_survivors"] for r in results)
        total_bankrupt = sum(len(r["bankruptcies"]) for r in results)
        print(f"  生還者合計: {total_surv}, 破産合計: {total_bankrupt}, "
              f"平均生還: {total_surv/args.games:.2f}")

    elapsed = time.time() - start_time

    # CSV 出力
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(f"logs/sim_mandatory_repay_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    for label, rows in all_rows.items():
        csv_path = output_dir / f"{label}_summary.csv"
        fieldnames = list(rows[0].keys()) if rows else []
        with open(csv_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    # レポート
    report_path = Path("doc/v0_7_simulation_report.md")
    generate_report(all_results, all_rows, base_config, args.games, args.seed, elapsed, report_path)

    # .devrelay-output にもコピー
    devrelay_dir = Path(".devrelay-output")
    devrelay_dir.mkdir(exist_ok=True)
    import shutil
    shutil.copy(report_path, devrelay_dir / "v0_7_simulation_report.md")

    print(f"\n=== 完了 ({elapsed:.1f}秒) ===")
    print(f"CSV: {output_dir}/")
    print(f"レポート: {report_path}")
    print(f"コピー: {devrelay_dir / 'v0_7_simulation_report.md'}")


if __name__ == "__main__":
    main()
