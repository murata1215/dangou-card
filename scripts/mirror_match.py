"""
StrongCardSave ミラーマッチ耐性試験

4構成で比較し、SCSが「単独で強い」のか「支配戦略」なのかを判定する。
- Roster A: 8種×1（ベースライン）
- Roster B: SCS×4 + Random×4（最重要）
- Roster C: SCS×8（極端な均衡テスト）
- Roster D: Random×8（対照群）

使用方法:
    uv run python scripts/mirror_match.py
    uv run python scripts/mirror_match.py --games 100
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


# 4構成の定義
ROSTERS = {
    "A (8種×1)": list(DEFAULT_ROSTER),
    "B (SCS×4+Rnd×4)": ["StrongCardSave"] * 4 + ["Random"] * 4,
    "C (SCS×8)": ["StrongCardSave"] * 8,
    "D (Rnd×8)": ["Random"] * 8,
}


def run_game_with_detail(args: tuple) -> dict[str, Any]:
    """
    1試合を実行し、詳細な集計データを返す

    返却値に含まれるデータ:
    - 個体別(player_id)の生還・最終資産・bot_type
    - COMMITイベントから: カード使用(player_id, rank, round, market_id)
    - MARKET_RESULTイベントから: 勝者・賞金・参加者数
    """
    game_index, game_seed, roster, config_dict = args
    config = GameConfig(**config_dict)

    # 座席シャッフル（個体seedはゲームseed+席番号から導出）
    seat_rng = random.Random(game_seed)
    shuffled_roster = list(roster)
    seat_rng.shuffle(shuffled_roster)

    agents = {}
    bot_assignments = {}  # player_id → bot_type
    for i, bot_name in enumerate(shuffled_roster):
        pid = f"P{i + 1:02d}"
        bot_class = BOT_REGISTRY[bot_name]
        # 個体ごとに異なるseed（同一Bot種でもtie-breakが異なる）
        agents[pid] = bot_class(seed=game_seed * 100 + i)
        bot_assignments[pid] = bot_name

    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=game_seed, logger=logger)
    result = game.run()

    # --- イベントログから詳細データを抽出 ---

    # カード使用: (player_id, rank_value, round_num, market_id)
    commits_detail: list[tuple[str, int, int, str]] = []
    # 市場結果: (round_num, market_id, winners_list, prize_per_winner, num_participants)
    market_details: list[dict[str, Any]] = []

    for event in logger.events:
        if event.event_type == "COMMIT":
            pid = event.data.get("player_id", "")
            card_id = event.data.get("card", "")
            market_id = event.data.get("market_id", "")
            rank_name = re.sub(r'_\d+$', '', card_id)
            try:
                rank_val = CardRank[rank_name].value
            except KeyError:
                continue
            commits_detail.append((pid, rank_val, event.round_num, market_id))

        elif event.event_type == "MARKET_RESULT":
            market_details.append({
                "round_num": event.round_num,
                "market_id": event.data.get("market_id", ""),
                "winners": event.data.get("winners", []),
                "prize_per_winner": event.data.get("prize_per_winner", 0),
                "participants": event.data.get("participants", 0),
            })

    # 個体別結果
    player_results: list[dict[str, Any]] = []
    for pid, p in result.players.items():
        player_results.append({
            "player_id": pid,
            "bot_type": bot_assignments[pid],
            "survived": p.is_alive,
            "final_cash": p.cash,
        })

    return {
        "num_survivors": len(result.survivors),
        "player_results": player_results,
        "commits_detail": commits_detail,
        "market_details": market_details,
        "bot_assignments": bot_assignments,
    }


def run_roster(
    label: str, roster: list[str], config: GameConfig,
    num_games: int, base_seed: int, workers: int,
) -> dict[str, Any]:
    """1構成の全試合を実行して集計する"""
    config_dict = config.model_dump()
    task_args = [(i, base_seed + i, roster, config_dict) for i in range(num_games)]

    if workers > 1 and num_games > 1:
        with Pool(processes=workers) as pool:
            results = pool.map(run_game_with_detail, task_args)
    else:
        results = [run_game_with_detail(a) for a in task_args]

    # --- 集計 ---
    total_survivors = sum(r["num_survivors"] for r in results)
    avg_survivors = total_survivors / num_games

    # Bot種別別の生還率（同一種の複数個体を正しく集計）
    bot_surv: dict[str, int] = defaultdict(int)
    bot_total: dict[str, int] = defaultdict(int)
    bot_cash: dict[str, list[int]] = defaultdict(list)
    for r in results:
        for pr in r["player_results"]:
            bt = pr["bot_type"]
            bot_total[bt] += 1
            if pr["survived"]:
                bot_surv[bt] += 1
                bot_cash[bt].append(pr["final_cash"])

    bot_rates = {
        bt: bot_surv[bt] / bot_total[bt] * 100 if bot_total[bt] else 0
        for bt in sorted(bot_total.keys())
    }
    bot_avg_cash = {
        bt: sum(bot_cash[bt]) / len(bot_cash[bt]) if bot_cash[bt] else 0
        for bt in sorted(bot_total.keys())
    }

    # カードランク別平均使用ラウンド
    rank_rounds: dict[int, list[int]] = defaultdict(list)
    for r in results:
        for pid, rank_val, rnd, mid in r["commits_detail"]:
            rank_rounds[rank_val].append(rnd)
    avg_rank_round = {
        rv: sum(rs) / len(rs) if rs else 0
        for rv, rs in sorted(rank_rounds.items())
    }

    # 上位3カード(rank 8,9,10)の同市場衝突率
    top_card_rounds = 0  # rank8-10が使われたラウンド×市場の件数
    top_card_collisions = 0  # 同一round+marketに2+枚のrank8-10が出た件数
    round_market_top: dict[tuple[int, str], int] = defaultdict(int)
    for r in results:
        rm_counts: dict[tuple[int, str], int] = defaultdict(int)
        for pid, rank_val, rnd, mid in r["commits_detail"]:
            if rank_val >= 8:
                rm_counts[(rnd, mid)] += 1
        for key, count in rm_counts.items():
            top_card_rounds += 1
            if count >= 2:
                top_card_collisions += 1
    top_collision_rate = top_card_collisions / top_card_rounds * 100 if top_card_rounds else 0

    # 同ランク最高位による賞金分割の発生回数
    split_count = 0
    total_market_results = 0
    for r in results:
        for md in r["market_details"]:
            total_market_results += 1
            if len(md["winners"]) >= 2:
                split_count += 1
    split_rate = split_count / total_market_results * 100 if total_market_results else 0

    # 強カード(rank7+)使用時の平均競合人数
    strong_card_competitors: list[int] = []
    for r in results:
        # round+market → 参加者数のマップ
        rm_participants: dict[tuple[int, str], int] = {}
        for md in r["market_details"]:
            rm_participants[(md["round_num"], md["market_id"])] = md["participants"]
        for pid, rank_val, rnd, mid in r["commits_detail"]:
            if rank_val >= 7:
                n = rm_participants.get((rnd, mid), 0)
                strong_card_competitors.append(n)
    avg_strong_competitors = (
        sum(strong_card_competitors) / len(strong_card_competitors)
        if strong_card_competitors else 0
    )

    return {
        "label": label,
        "avg_survivors": avg_survivors,
        "bot_rates": bot_rates,
        "bot_avg_cash": bot_avg_cash,
        "avg_rank_round": avg_rank_round,
        "top_collision_rate": top_collision_rate,
        "split_count": split_count,
        "split_rate": split_rate,
        "avg_strong_competitors": avg_strong_competitors,
        "total_games": num_games,
    }


def generate_mirror_report(
    all_results: dict[str, dict[str, Any]],
    num_games: int, base_seed: int, elapsed: float,
    output_path: Path,
) -> None:
    """ミラーマッチレポートを生成する"""
    lines: list[str] = []
    lines.append("# StrongCardSave ミラーマッチ耐性試験レポート\n")
    lines.append(f"- 各構成: {num_games}試合")
    lines.append(f"- 賞金: フラット, prize_scale=2.0, survival_cash=200万")
    lines.append(f"- seed: {base_seed}")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append("")

    labels = list(all_results.keys())

    # 概要テーブル
    lines.append("## 概要\n")
    lines.append("| 指標 | " + " | ".join(labels) + " |")
    lines.append("|---|" + "|".join(["---"] * len(labels)) + "|")

    avg_survs = [f"{all_results[l]['avg_survivors']:.2f}" for l in labels]
    lines.append("| 平均生還者数 | " + " | ".join(avg_survs) + " |")

    # Bot別生還率テーブル
    lines.append("\n## Bot別生還率\n")
    all_bots = sorted(set(
        bt for r in all_results.values() for bt in r["bot_rates"].keys()
    ))
    lines.append("| Bot | " + " | ".join(labels) + " |")
    lines.append("|---|" + "|".join(["---"] * len(labels)) + "|")
    for bt in all_bots:
        vals = []
        for l in labels:
            rate = all_results[l]["bot_rates"].get(bt, None)
            vals.append(f"{rate:.1f}%" if rate is not None else "N/A")
        marker = " **←**" if bt == "StrongCardSave" else ""
        lines.append(f"| {bt}{marker} | " + " | ".join(vals) + " |")

    # 平均最終資産
    lines.append("\n## Bot別平均最終資産（生還者のみ）\n")
    lines.append("| Bot | " + " | ".join(labels) + " |")
    lines.append("|---|" + "|".join(["---"] * len(labels)) + "|")
    for bt in all_bots:
        vals = []
        for l in labels:
            cash = all_results[l]["bot_avg_cash"].get(bt, None)
            vals.append(f"{cash:,.0f}円" if cash is not None and cash > 0 else "N/A")
        lines.append(f"| {bt} | " + " | ".join(vals) + " |")

    # 衝突・分割指標
    lines.append("\n## 衝突・分割指標\n")
    lines.append("| 指標 | " + " | ".join(labels) + " |")
    lines.append("|---|" + "|".join(["---"] * len(labels)) + "|")

    vals = [f"{all_results[l]['top_collision_rate']:.1f}%" for l in labels]
    lines.append("| 上位3カード同市場衝突率 | " + " | ".join(vals) + " |")

    vals = [f"{all_results[l]['split_count']} ({all_results[l]['split_rate']:.1f}%)" for l in labels]
    lines.append("| 賞金分割発生回数(率) | " + " | ".join(vals) + " |")

    vals = [f"{all_results[l]['avg_strong_competitors']:.2f}人" for l in labels]
    lines.append("| 強カード使用時の平均競合人数 | " + " | ".join(vals) + " |")

    # カードランク別平均使用ラウンド
    lines.append("\n## カードランク別平均使用ラウンド\n")
    rank_names = {v.value: v.name for v in CardRank}
    lines.append("| ランク | カード名 | " + " | ".join(labels) + " |")
    lines.append("|---|---|" + "|".join(["---"] * len(labels)) + "|")
    for rv in sorted(rank_names.keys()):
        name = rank_names[rv]
        vals = [f"R{all_results[l]['avg_rank_round'].get(rv, 0):.1f}" for l in labels]
        lines.append(f"| {rv} | {name} | " + " | ".join(vals) + " |")

    # 判定
    lines.append("\n## 判定\n")

    scs_a = all_results[labels[0]]["bot_rates"].get("StrongCardSave", 0)
    scs_b = all_results[labels[1]]["bot_rates"].get("StrongCardSave", 0)
    scs_c = all_results[labels[2]]["bot_rates"].get("StrongCardSave", 0)
    rnd_d = all_results[labels[3]]["bot_rates"].get("Random", 0)
    drop = scs_a - scs_b

    lines.append(f"### StrongCardSave生還率の変化")
    lines.append(f"- Roster A (ベースライン): **{scs_a:.1f}%**")
    lines.append(f"- Roster B (SCS×4+Rnd×4): **{scs_b:.1f}%** (Aから **{-drop:+.1f}pp**)")
    lines.append(f"- Roster C (SCS×8): **{scs_c:.1f}%**")
    lines.append(f"- Roster D (Random×8): Random **{rnd_d:.1f}%**")
    lines.append("")

    # 回帰確認
    lines.append(f"### 回帰確認")
    lines.append(f"- Roster A の SCS生還率: {scs_a:.1f}% （従前94.1%からの差: {scs_a - 94.1:+.1f}pp）")
    if abs(scs_a - 94.1) <= 5:
        lines.append(f"- → tie-break導入による乖離は許容範囲（±5pp以内）")
    else:
        lines.append(f"- → ⚠️ 従前からの乖離が大きい。tie-breakがロジックに影響した可能性")
    lines.append("")

    if drop >= 30:
        lines.append("### 結論: **健全判定**\n")
        lines.append(f"SCS生還率がRoster Bで **{drop:.0f}pp低下**。上位カード衝突率も上昇。")
        lines.append("「単独では強いが普及すると自壊する戦略」= ゲーム構造は概ね健全。")
        lines.append("**→ カード構造は変更せずStep 3 LLM投入へ進む。**")
    elif scs_b >= 80:
        lines.append("### 結論: **危険判定**\n")
        lines.append(f"SCS生還率がRoster Bでも **{scs_b:.1f}%** を維持（低下幅わずか{drop:.0f}pp）。")
        lines.append("StrongCardSaveは支配戦略の可能性が高い。")
        lines.append("カード構造（ROYALを最後まで持てばほぼ勝てる1枚性）の再検討を推奨。")
    else:
        lines.append(f"### 結論: **中間判定**（低下幅 {drop:.0f}pp）\n")
        lines.append(f"SCS生還率: A={scs_a:.1f}% → B={scs_b:.1f}%。普及により弱体化するが完全な自壊ではない。")
        col_rate_b = all_results[labels[1]]["top_collision_rate"]
        col_rate_d = all_results[labels[3]]["top_collision_rate"]
        lines.append(f"上位カード衝突率: B={col_rate_b:.1f}% vs D={col_rate_d:.1f}%")
        if col_rate_b > col_rate_d * 1.5:
            lines.append("衝突起因の弱体化が確認された。ゲーム構造は概ね健全。")
        else:
            lines.append("衝突よりも賞金希釈（同一市場集中による分割）が主因の可能性。")
        lines.append("**→ Step 3 LLM投入へ進む。LLMがSCS同等の温存戦略を採用するか観察。**")

    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="SCS ミラーマッチ耐性試験")
    parser.add_argument("--games", type=int, default=1000, help="各構成の試合数")
    parser.add_argument("--seed", type=int, default=42, help="ベースseed")
    parser.add_argument("--workers", type=int, default=4, help="並列ワーカー数")
    args = parser.parse_args()

    try:
        os.nice(10)
    except (OSError, AttributeError):
        pass

    # 共通設定: flat賞金, prize_scale=2.0, survival_cash=200万
    base_config = GameConfig.default_8()
    scaled = [int(t * 2.0) for t in base_config.prize_tiers]
    total = sum(scaled)
    per_round = total // base_config.num_rounds
    flat_tiers = [per_round] * base_config.num_rounds
    flat_tiers[-1] += total - sum(flat_tiers)
    config = base_config.model_copy(update={
        "prize_tiers": flat_tiers,
        "total_prize": total,
        "survival_cash": 2_000_000,
    })

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(f"logs/mirror_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"=== SCS ミラーマッチ耐性試験 ===")
    print(f"各構成: {args.games}試合, 総賞金: {config.total_prize:,}円 (flat)")
    print(f"---")

    start_time = time.time()
    all_results: dict[str, dict[str, Any]] = {}

    for label, roster in ROSTERS.items():
        print(f"{label} 実行中...")
        r = run_roster(label, roster, config, args.games, args.seed, args.workers)
        all_results[label] = r
        scs = r["bot_rates"].get("StrongCardSave", None)
        rnd = r["bot_rates"].get("Random", None)
        scs_str = f"SCS={scs:.1f}%" if scs is not None else ""
        rnd_str = f"Rnd={rnd:.1f}%" if rnd is not None else ""
        print(f"  → 平均生還: {r['avg_survivors']:.2f}, {scs_str} {rnd_str}")

    elapsed = time.time() - start_time

    report_path = output_dir / "mirror_report.md"
    generate_mirror_report(all_results, args.games, args.seed, elapsed, report_path)

    print(f"\n=== 完了 ===")
    print(f"実行時間: {elapsed:.1f}秒")
    print(f"レポート: {report_path}")


if __name__ == "__main__":
    main()
