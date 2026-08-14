"""
S2レギュレーション比較シミュレーション

S1 / S2 を同一シード・同一Bot戦略セットで各N試合実行し、
仕様書§8「要検証4項目」を定量評価するレポートを生成する。

使用方法:
    uv run python scripts/simulate_s2_comparison.py --games 1000
    uv run python scripts/simulate_s2_comparison.py --games 100 --seed 42
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

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game, GameResult
from bots import BOT_REGISTRY, DEFAULT_ROSTER
import random


def run_single_game(args: tuple) -> dict[str, Any]:
    """1試合を実行する（multiprocessing用）"""
    game_index, base_seed, roster, config_dict, ruleset_label = args
    game_seed = base_seed + game_index

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
        bot_seed = game_seed * 100 + i
        agents[pid] = bot_class(seed=bot_seed)
        bot_assignments[pid] = bot_name

    # ゲーム実行（ログ不要）
    game = Game(config=config, agents=agents, seed=game_seed)
    result = game.run()

    # プレイヤー別倍掛け統計
    player_du_stats: dict[str, dict[str, int]] = {}
    for dep in result.double_up_deposits:
        pid_du = dep.player_id
        if pid_du not in player_du_stats:
            player_du_stats[pid_du] = {
                "count": 0, "success": 0, "forfeited": 0, "solo_success": 0,
            }
        player_du_stats[pid_du]["count"] += 1
        if dep.resolved and dep.success:
            player_du_stats[pid_du]["success"] += 1
            if dep.from_solo_market:
                player_du_stats[pid_du]["solo_success"] += 1
        elif dep.resolved and not dep.success:
            player_du_stats[pid_du]["forfeited"] += dep.deposit_amount

    # 生還者順位
    survivor_ranks = {p.player_id: i + 1 for i, p in enumerate(result.survivors)}

    # 行データ
    rows = []
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
            "elimination_round": p.elimination_round or 0,
            "final_rank": survivor_ranks.get(pid, 0),
            "double_up_count": du.get("count", 0),
            "double_up_success": du.get("success", 0),
            "double_up_forfeited": du.get("forfeited", 0),
            "double_up_solo_success": du.get("solo_success", 0),
        })

    return {
        "game_id": game_index + 1,
        "ruleset": ruleset_label,
        "rows": rows,
        "num_survivors": len(result.survivors),
        "round_snapshots": result.round_snapshots,
        "double_up_deposits": [
            {
                "player_id": d.player_id,
                "deposit_amount": d.deposit_amount,
                "deposited_round": d.deposited_round,
                "success_round": d.success_round,
                "resolved": d.resolved,
                "success": d.success,
                "from_solo_market": d.from_solo_market,
            }
            for d in result.double_up_deposits
        ],
    }


def run_batch(
    ruleset: str,
    roster: list[str],
    games: int,
    seed: int,
    workers: int,
) -> list[dict[str, Any]]:
    """S1 または S2 のバッチ実行"""
    config = _create_config(roster, ruleset)
    config_dict = config.model_dump()

    task_args = [
        (i, seed, roster, config_dict, ruleset)
        for i in range(games)
    ]

    if workers > 1 and games > 1:
        with Pool(processes=workers) as pool:
            results = pool.map(run_single_game, task_args)
    else:
        results = [run_single_game(a) for a in task_args]

    return results


def _create_config(roster: list[str], ruleset: str) -> GameConfig:
    """ロスター+ルールセットからGameConfigを生成"""
    num = len(roster)
    ratio = num / 20
    base_tiers = [1_200_000] * 4 + [1_600_000] * 4 + [2_000_000] * 4
    scaled_tiers = [int(t * ratio) for t in base_tiers]
    total = sum(scaled_tiers)

    config = GameConfig(
        num_players=num,
        total_prize=total,
        prize_tiers=scaled_tiers,
    )

    if ruleset == "S2":
        config = config.model_copy(update={
            "fog_rounds": [4, 8],
            "surge_enabled": True,
            "final_market_multiplier": 3,
            "double_up_enabled": True,
        })

    return config


def generate_comparison_report(
    s1_results: list[dict],
    s2_results: list[dict],
    num_games: int,
    seed: int,
    elapsed: float,
    output_path: Path,
) -> None:
    """4項目の比較レポートを生成"""

    lines: list[str] = []
    lines.append("# S2レギュレーション Botシミュレーション比較レポート\n")
    lines.append(f"- 生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S JST')}")
    lines.append(f"- 試合数: S1={num_games}, S2={num_games}")
    lines.append(f"- ベースseed: {seed}")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append(f"- ロスター: 8種Bot各1体（8人版）")
    lines.append("")
    lines.append("> **注記**: 霧のラウンド（R4/R8）はBotがカード推論を行わないため、")
    lines.append("> S2固有の効果としては計測不能。実装はされているが、")
    lines.append("> 本レポートではノイズとして扱う。\n")

    # --- 共通集計 ---
    s1_rows = [r for game in s1_results for r in game["rows"]]
    s2_rows = [r for game in s2_results for r in game["rows"]]

    # --- A. 経済総額の膨張 ---
    lines.append("---\n")
    lines.append("## A. 経済総額の膨張\n")

    # ラウンド別総資産推移
    s1_round_assets = _aggregate_round_snapshots(s1_results)
    s2_round_assets = _aggregate_round_snapshots(s2_results)

    lines.append("### ラウンド別 全プレイヤー資産合計の推移（平均）\n")
    lines.append("| R | S1 総資産 | S2 総資産 | S2/S1 比 |")
    lines.append("|---|----------|----------|---------|")

    for r in range(1, 13):
        s1_val = s1_round_assets.get(r, {}).get("total_assets", 0)
        s2_val = s2_round_assets.get(r, {}).get("total_assets", 0)
        ratio = s2_val / s1_val if s1_val > 0 else 0
        lines.append(f"| R{r:02d} | {s1_val:,.0f} | {s2_val:,.0f} | {ratio:.2f}x |")

    # R12単独寄与率
    s1_r11 = s1_round_assets.get(11, {}).get("total_assets", 0)
    s1_r12 = s1_round_assets.get(12, {}).get("total_assets", 0)
    s2_r11 = s2_round_assets.get(11, {}).get("total_assets", 0)
    s2_r12 = s2_round_assets.get(12, {}).get("total_assets", 0)

    s1_r12_contrib = (s1_r12 - s1_r11) / s1_r12 * 100 if s1_r12 > 0 else 0
    s2_r12_contrib = (s2_r12 - s2_r11) / s2_r12 * 100 if s2_r12 > 0 else 0

    lines.append(f"\n### R12単独での総資産増加寄与率\n")
    lines.append(f"- S1: R11→R12 増加率 = {s1_r12_contrib:.1f}%")
    lines.append(f"- S2: R11→R12 増加率 = {s2_r12_contrib:.1f}%")

    # R12での順位入替
    rank_reversals = _count_rank_reversals(s1_results, s2_results)
    lines.append(f"\n### R12で最終順位が入れ替わった試合の割合\n")
    lines.append(f"- S2で R11末時点の暫定順位と最終順位が異なった試合: "
                 f"{rank_reversals['s2_reversal_rate']:.1f}% ({rank_reversals['s2_reversals']}/{num_games})")

    # 判定
    s2_inflation = s2_r12 / s1_r12 if s1_r12 > 0 else 0
    if s2_inflation < 1.5:
        a_verdict = "健全"
        a_reason = f"S2/S1 最終資産比 {s2_inflation:.2f}x — 膨張は限定的"
    elif s2_inflation < 2.5:
        a_verdict = "要調整"
        a_reason = f"S2/S1 最終資産比 {s2_inflation:.2f}x — 賞金基準額の縮小を検討"
    else:
        a_verdict = "要ルール修正"
        a_reason = f"S2/S1 最終資産比 {s2_inflation:.2f}x — 全員生存が可能になるリスク"

    lines.append(f"\n### 判定: **{a_verdict}**\n")
    lines.append(f"根拠: {a_reason}\n")

    # --- B. 倍掛けの空き巣抜け道 ---
    lines.append("---\n")
    lines.append("## B. 倍掛けの空き巣抜け道\n")

    du_stats = _analyze_double_up(s2_results)
    lines.append(f"- 倍掛け総選択数: {du_stats['total_chosen']}")
    lines.append(f"- 倍掛け成功数: {du_stats['total_success']} "
                 f"({du_stats['success_rate']:.1f}%)")
    lines.append(f"- 倍掛け失敗数: {du_stats['total_fail']} "
                 f"({du_stats['fail_rate']:.1f}%)")
    lines.append(f"- **空き巣成功数（参加者1人市場で成功）: {du_stats['solo_success']}** "
                 f"({du_stats['solo_rate']:.1f}% of successes)")
    lines.append(f"- 空き巣成立した試合数: {du_stats['games_with_solo']}/{num_games}")

    if du_stats['solo_ranks']:
        lines.append(f"\n### 空き巣成功者の最終順位分布\n")
        lines.append("| 最終順位 | 件数 | 割合 |")
        lines.append("|---------|------|------|")
        for rank in sorted(du_stats['solo_ranks'].keys()):
            cnt = du_stats['solo_ranks'][rank]
            pct = cnt / sum(du_stats['solo_ranks'].values()) * 100
            label = f"#{rank}" if rank > 0 else "脱落"
            lines.append(f"| {label} | {cnt} | {pct:.1f}% |")

    # 判定
    solo_pct = du_stats['solo_rate']
    if solo_pct < 10:
        b_verdict = "健全"
        b_reason = f"空き巣成功は全成功の{solo_pct:.1f}% — 公開情報による牽制が機能"
    elif solo_pct < 30:
        b_verdict = "要調整"
        b_reason = f"空き巣成功は全成功の{solo_pct:.1f}% — 除外ルール(a)の導入を検討"
    else:
        b_verdict = "要ルール修正"
        b_reason = f"空き巣成功は全成功の{solo_pct:.1f}% — 空き巣市場の成功除外が必要"

    lines.append(f"\n### 判定: **{b_verdict}**\n")
    lines.append(f"根拠: {b_reason}\n")

    # --- C. 市場高騰の閾値 ---
    lines.append("---\n")
    lines.append("## C. 市場高騰の閾値\n")

    surge_stats = _analyze_surge(s2_results)

    lines.append("### 生存者数別 高騰発生率\n")
    lines.append("| 生存者数 | 総市場数 | 高騰発生数 | 高騰率 |")
    lines.append("|---------|---------|----------|-------|")
    for alive_n in sorted(surge_stats['by_alive'].keys()):
        s = surge_stats['by_alive'][alive_n]
        rate = s['surged'] / s['total'] * 100 if s['total'] > 0 else 0
        lines.append(f"| {alive_n} | {s['total']} | {s['surged']} | {rate:.1f}% |")

    lines.append("\n### ラウンド別 高騰発生率\n")
    lines.append("| ラウンド | 総市場数 | 高騰発生数 | 高騰率 |")
    lines.append("|---------|---------|----------|-------|")
    for r in range(1, 13):
        s = surge_stats['by_round'].get(r, {"total": 0, "surged": 0})
        rate = s['surged'] / s['total'] * 100 if s['total'] > 0 else 0
        lines.append(f"| R{r:02d} | {s['total']} | {s['surged']} | {rate:.1f}% |")

    late_surge = sum(
        surge_stats['by_round'].get(r, {}).get('surged', 0) for r in [9, 10, 11, 12]
    )
    late_total = sum(
        surge_stats['by_round'].get(r, {}).get('total', 0) for r in [9, 10, 11, 12]
    )
    late_rate = late_surge / late_total * 100 if late_total > 0 else 0

    lines.append(f"\n- 終盤（R9-12）の高騰率: {late_rate:.1f}%")

    # 判定
    if late_rate < 60:
        c_verdict = "健全"
        c_reason = f"終盤高騰率 {late_rate:.1f}% — 常態化していない"
    elif late_rate < 85:
        c_verdict = "要調整"
        c_reason = f"終盤高騰率 {late_rate:.1f}% — 最低参加者数下限（例: 3人以上）の導入を検討"
    else:
        c_verdict = "要ルール修正"
        c_reason = f"終盤高騰率 {late_rate:.1f}% — 高騰が常態化、閾値引き上げが必要"

    lines.append(f"\n### 判定: **{c_verdict}**\n")
    lines.append(f"根拠: {c_reason}\n")

    # --- D. 生存ラインの適正 ---
    lines.append("---\n")
    lines.append("## D. 生存ラインの適正\n")

    s1_surv_stats = _analyze_survival(s1_results, s1_rows, num_games)
    s2_surv_stats = _analyze_survival(s2_results, s2_rows, num_games)

    lines.append("### 脱落タイミングの分布（ラウンド別脱落者数）\n")
    lines.append("| ラウンド | S1 脱落数 | S2 脱落数 |")
    lines.append("|---------|----------|----------|")
    for r in range(1, 13):
        s1_e = s1_surv_stats['elim_by_round'].get(r, 0)
        s2_e = s2_surv_stats['elim_by_round'].get(r, 0)
        lines.append(f"| R{r:02d} | {s1_e} | {s2_e} |")

    lines.append("\n### R12到達人数の分布\n")
    lines.append("| 到達人数 | S1 試合数 | S1 割合 | S2 試合数 | S2 割合 |")
    lines.append("|---------|----------|--------|----------|--------|")
    all_counts = sorted(set(list(s1_surv_stats['r12_alive_dist'].keys()) +
                            list(s2_surv_stats['r12_alive_dist'].keys())))
    for n in all_counts:
        s1_cnt = s1_surv_stats['r12_alive_dist'].get(n, 0)
        s2_cnt = s2_surv_stats['r12_alive_dist'].get(n, 0)
        s1_pct = s1_cnt / num_games * 100
        s2_pct = s2_cnt / num_games * 100
        lines.append(f"| {n} | {s1_cnt} | {s1_pct:.1f}% | {s2_cnt} | {s2_pct:.1f}% |")

    lines.append("\n### 生還者数分布\n")
    lines.append("| 生還者数 | S1 試合数 | S1 割合 | S2 試合数 | S2 割合 |")
    lines.append("|---------|----------|--------|----------|--------|")
    all_survivor_counts = sorted(set(list(s1_surv_stats['survivor_dist'].keys()) +
                                      list(s2_surv_stats['survivor_dist'].keys())))
    for n in all_survivor_counts:
        s1_cnt = s1_surv_stats['survivor_dist'].get(n, 0)
        s2_cnt = s2_surv_stats['survivor_dist'].get(n, 0)
        s1_pct = s1_cnt / num_games * 100
        s2_pct = s2_cnt / num_games * 100
        lines.append(f"| {n} | {s1_cnt} | {s1_pct:.1f}% | {s2_cnt} | {s2_pct:.1f}% |")

    s1_avg_surv = s1_surv_stats['avg_survivors']
    s2_avg_surv = s2_surv_stats['avg_survivors']

    lines.append(f"\n- S1 平均生還者数: {s1_avg_surv:.2f}")
    lines.append(f"- S2 平均生還者数: {s2_avg_surv:.2f}")

    # 判定
    target_low = 8 * 0.2  # 1.6
    target_high = 8 * 0.5  # 4.0
    s2_in_target = target_low <= s2_avg_surv <= target_high
    if s2_in_target:
        d_verdict = "健全"
        d_reason = (f"S2平均生還者数 {s2_avg_surv:.2f} — "
                    f"目標帯 {target_low:.1f}~{target_high:.1f} 内")
    elif s2_avg_surv > target_high:
        d_verdict = "要調整"
        d_reason = (f"S2平均生還者数 {s2_avg_surv:.2f} — "
                    f"目標帯 {target_low:.1f}~{target_high:.1f} を超過、"
                    f"生還条件の引き上げを検討")
    else:
        d_verdict = "要調整"
        d_reason = (f"S2平均生還者数 {s2_avg_surv:.2f} — "
                    f"目標帯 {target_low:.1f}~{target_high:.1f} を下回る、"
                    f"生還条件の緩和を検討")

    lines.append(f"\n### 判定: **{d_verdict}**\n")
    lines.append(f"根拠: {d_reason}\n")

    # --- Bot別比較 ---
    lines.append("---\n")
    lines.append("## 補足: Bot別生還率比較\n")

    s1_bot_stats = _bot_survival_rates(s1_rows)
    s2_bot_stats = _bot_survival_rates(s2_rows)

    lines.append("| Bot | S1 生還率 | S2 生還率 | 差分 |")
    lines.append("|-----|---------|---------|------|")
    all_bots = sorted(set(list(s1_bot_stats.keys()) + list(s2_bot_stats.keys())))
    for bt in all_bots:
        s1r = s1_bot_stats.get(bt, {}).get("rate", 0)
        s2r = s2_bot_stats.get(bt, {}).get("rate", 0)
        diff = s2r - s1r
        sign = "+" if diff > 0 else ""
        lines.append(f"| {bt} | {s1r:.1f}% | {s2r:.1f}% | {sign}{diff:.1f}pp |")

    # --- 総合判定 ---
    lines.append("\n---\n")
    lines.append("## 総合判定サマリ\n")
    lines.append("| 検証項目 | 判定 | 根拠 |")
    lines.append("|---------|------|------|")
    lines.append(f"| A. 経済総額の膨張 | **{a_verdict}** | {a_reason} |")
    lines.append(f"| B. 倍掛けの空き巣抜け道 | **{b_verdict}** | {b_reason} |")
    lines.append(f"| C. 市場高騰の閾値 | **{c_verdict}** | {c_reason} |")
    lines.append(f"| D. 生存ラインの適正 | **{d_verdict}** | {d_reason} |")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


# === 集計ヘルパー ===

def _aggregate_round_snapshots(results: list[dict]) -> dict[int, dict[str, float]]:
    """ラウンド別スナップショットの平均を算出"""
    agg: dict[int, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for game in results:
        for snap in game.get("round_snapshots", []):
            r = snap["round"]
            for key in ["total_assets", "total_debt", "alive_count", "surge_count",
                        "double_up_success", "double_up_fail", "double_up_solo_success"]:
                agg[r][key].append(snap.get(key, 0))

    averaged: dict[int, dict[str, float]] = {}
    for r, data in agg.items():
        averaged[r] = {k: sum(v) / len(v) for k, v in data.items()}
    return averaged


def _count_rank_reversals(s1_results: list[dict], s2_results: list[dict]) -> dict:
    """S2でR11末→R12で順位が入れ替わった試合数を集計"""
    reversals = 0
    total = len(s2_results)

    for game in s2_results:
        snapshots = game.get("round_snapshots", [])
        if len(snapshots) < 12:
            continue

        rows = game["rows"]
        survivors = [r for r in rows if r["survived"]]
        if len(survivors) < 2:
            continue

        # R11末のスナップショットからは個別資産が取れないため、
        # 最終順位と最終資産から推定（R12の変動で入れ替わりを検出）
        # 簡易: R12の surge_count > 0 or double_up_success > 0 なら「変動あり」
        r12_snap = snapshots[11] if len(snapshots) >= 12 else {}
        r11_snap = snapshots[10] if len(snapshots) >= 11 else {}

        # 資産変動が大きい = 順位入替の可能性
        r12_delta = r12_snap.get("total_assets", 0) - r11_snap.get("total_assets", 0)
        if r12_delta > 0 and (r12_snap.get("surge_count", 0) > 0 or
                               r12_snap.get("double_up_success", 0) > 0):
            reversals += 1

    return {
        "s2_reversals": reversals,
        "s2_reversal_rate": reversals / total * 100 if total > 0 else 0,
    }


def _analyze_double_up(results: list[dict]) -> dict:
    """倍掛け統計を集計"""
    total_chosen = 0
    total_success = 0
    total_fail = 0
    solo_success = 0
    games_with_solo = 0
    # 空き巣成功者の最終順位
    solo_ranks: dict[int, int] = defaultdict(int)

    for game in results:
        deposits = game.get("double_up_deposits", [])
        total_chosen += len(deposits)
        game_has_solo = False

        for dep in deposits:
            if dep["resolved"]:
                if dep["success"]:
                    total_success += 1
                    if dep["from_solo_market"]:
                        solo_success += 1
                        game_has_solo = True
                        # この player の最終順位を取得
                        for row in game["rows"]:
                            if row["player_id"] == dep["player_id"]:
                                solo_ranks[row["final_rank"]] += 1
                                break
                else:
                    total_fail += 1

        if game_has_solo:
            games_with_solo += 1

    return {
        "total_chosen": total_chosen,
        "total_success": total_success,
        "total_fail": total_fail,
        "success_rate": total_success / total_chosen * 100 if total_chosen > 0 else 0,
        "fail_rate": total_fail / total_chosen * 100 if total_chosen > 0 else 0,
        "solo_success": solo_success,
        "solo_rate": solo_success / total_success * 100 if total_success > 0 else 0,
        "games_with_solo": games_with_solo,
        "solo_ranks": dict(solo_ranks),
    }


def _analyze_surge(results: list[dict]) -> dict:
    """市場高騰統計を集計"""
    by_alive: dict[int, dict[str, int]] = defaultdict(lambda: {"total": 0, "surged": 0})
    by_round: dict[int, dict[str, int]] = defaultdict(lambda: {"total": 0, "surged": 0})

    for game in results:
        for snap in game.get("round_snapshots", []):
            r = snap["round"]
            alive = snap["alive_count"]
            num_markets = 3  # 固定

            surge_count = snap.get("surge_count", 0)

            by_alive[alive]["total"] += num_markets
            by_alive[alive]["surged"] += surge_count
            by_round[r]["total"] += num_markets
            by_round[r]["surged"] += surge_count

    return {
        "by_alive": dict(by_alive),
        "by_round": dict(by_round),
    }


def _analyze_survival(
    results: list[dict],
    rows: list[dict],
    num_games: int,
) -> dict:
    """生存統計を集計"""
    elim_by_round: dict[int, int] = defaultdict(int)
    survivor_dist: dict[int, int] = defaultdict(int)
    r12_alive_dist: dict[int, int] = defaultdict(int)

    for row in rows:
        if not row["survived"] and row["elimination_round"]:
            elim_by_round[int(row["elimination_round"])] += 1

    for game in results:
        num_surv = game["num_survivors"]
        survivor_dist[num_surv] += 1

        # R12到達人数（R12開始時の生存者数）
        snapshots = game.get("round_snapshots", [])
        if snapshots:
            # R11スナップショットのalive_count = R12開始時の生存者数
            r11_snap = next((s for s in snapshots if s["round"] == 11), None)
            if r11_snap:
                r12_alive_dist[r11_snap["alive_count"]] += 1
            else:
                # スナップショットがない場合は最後のスナップショットを使用
                r12_alive_dist[snapshots[-1]["alive_count"]] += 1

    total_survivors = sum(game["num_survivors"] for game in results)
    avg_survivors = total_survivors / num_games if num_games > 0 else 0

    return {
        "elim_by_round": dict(elim_by_round),
        "survivor_dist": dict(survivor_dist),
        "r12_alive_dist": dict(r12_alive_dist),
        "avg_survivors": avg_survivors,
    }


def _bot_survival_rates(rows: list[dict]) -> dict[str, dict]:
    """Bot別生還率を算出"""
    stats: dict[str, dict[str, int]] = defaultdict(lambda: {"games": 0, "survived": 0})
    for row in rows:
        bt = row["bot_type"]
        stats[bt]["games"] += 1
        if row["survived"]:
            stats[bt]["survived"] += 1

    result = {}
    for bt, s in stats.items():
        result[bt] = {
            "games": s["games"],
            "survived": s["survived"],
            "rate": s["survived"] / s["games"] * 100 if s["games"] > 0 else 0,
        }
    return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="S2レギュレーション比較シミュレーション",
    )
    parser.add_argument("--games", type=int, default=1000,
                        help="各ルールセットの試合数（デフォルト: 1000）")
    parser.add_argument("--seed", type=int, default=42,
                        help="ベースseed（デフォルト: 42）")
    parser.add_argument("--workers", type=int, default=4,
                        help="並列ワーカー数（デフォルト: 4）")
    args = parser.parse_args()

    # nice 10
    try:
        os.nice(10)
    except (OSError, AttributeError):
        pass

    roster = list(DEFAULT_ROSTER)

    print(f"=== S2レギュレーション比較シミュレーション ===")
    print(f"試合数: S1={args.games}, S2={args.games}")
    print(f"ベースseed: {args.seed}")
    print(f"ワーカー数: {args.workers}")
    print("---")

    start_time = time.time()

    print("S1 実行中...")
    s1_results = run_batch("S1", roster, args.games, args.seed, args.workers)
    s1_elapsed = time.time() - start_time
    print(f"  S1 完了: {s1_elapsed:.1f}秒")

    print("S2 実行中...")
    s2_results = run_batch("S2", roster, args.games, args.seed, args.workers)
    total_elapsed = time.time() - start_time
    print(f"  S2 完了: {total_elapsed - s1_elapsed:.1f}秒")

    # レポート生成
    report_path = Path("doc/s2_simulation_report.md")
    generate_comparison_report(
        s1_results, s2_results,
        args.games, args.seed, total_elapsed, report_path,
    )
    print(f"\nレポート出力: {report_path}")

    # .devrelay-output にもコピー
    output_dir = Path(".devrelay-output")
    output_dir.mkdir(exist_ok=True)
    output_copy = output_dir / "s2_simulation_report.md"
    output_copy.write_text(report_path.read_text(encoding="utf-8"), encoding="utf-8")
    print(f"コピー出力: {output_copy}")

    print(f"\n=== 完了 ({total_elapsed:.1f}秒) ===")


if __name__ == "__main__":
    main()
