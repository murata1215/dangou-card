"""
Step 3A/3B/3C LLM試験スクリプト

Phase A: 接続試験（Haiku×1 + Random×7）
Phase B: 交渉試験（Haiku×2 + Random×6）
Phase C: 全LLM戦（6社8モデル、Random 0体）

使用方法:
    uv run python scripts/llm_trial.py --phase A
    uv run python scripts/llm_trial.py --phase B --preset dev
    uv run python scripts/llm_trial.py --phase C --games 1 --seed 301
    uv run python scripts/llm_trial.py --regenerate-report logs/llm/trial_B_20260809_175839/
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game, GameResult
from bots.random_bot import RandomBot
from llm.models import get_model, ModelInfo, MODEL_REGISTRY
from llm.adapters import create_adapter
from llm.llm_logger import LLMLogger
from llm.llm_agent import LLMAgent
from llm.constants import COST_LIMIT_TOTAL, COST_LIMIT_PER_GAME
import random as stdlib_random


HAIKU_MODEL_KEY = "L1"  # claude-haiku-4-5-20251001

# Phase Cロスター: 6社8モデル
PHASE_C_ROSTER = ["M1", "L1", "M2", "L2", "M3", "M4", "M5", "M6"]


def run_trial_game(
    llm_count: int,
    game_index: int,
    config: GameConfig,
    output_dir: Path,
    seed: int = 42,
    model_key: str | None = None,
) -> tuple[GameResult, list[LLMAgent], EventLogger]:
    """1試合を実行する"""
    game_seed = seed + game_index
    model_info = get_model(model_key or HAIKU_MODEL_KEY)
    agents: dict[str, Any] = {}
    llm_agents: list[LLMAgent] = []

    # LLMエージェント
    for i in range(llm_count):
        pid = f"P{i + 1:02d}"
        adapter = create_adapter(model_info)
        llm_logger = LLMLogger(
            output_dir / "llm_logs",
            game_id=f"game{game_index + 1:02d}_{pid}",
        )
        agent = LLMAgent(pid, model_info, adapter, llm_logger, config)
        agents[pid] = agent
        llm_agents.append(agent)

    # 残りをRandomBot
    for i in range(llm_count, config.num_players):
        pid = f"P{i + 1:02d}"
        agents[pid] = RandomBot(seed=game_seed * 100 + i)

    event_logger = EventLogger()
    game = Game(config=config, agents=agents, seed=game_seed, logger=event_logger)
    result = game.run()

    # LLMログ保存
    for agent in llm_agents:
        agent.llm_logger.save()

    # イベントログ保存
    event_logger.save_jsonl(output_dir / f"game{game_index + 1:02d}_events.jsonl")

    return result, llm_agents, event_logger


def generate_phase_a_report(
    results: list[tuple[GameResult, list[LLMAgent], EventLogger]],
    config: GameConfig,
    elapsed: float,
    output_path: Path,
) -> None:
    """Phase Aレポートを生成する"""
    lines: list[str] = []
    lines.append("# Step 3A: 接続試験レポート\n")
    lines.append(f"- モデル: Claude Haiku 4.5 (`claude-haiku-4-5-20251001`)")
    lines.append(f"- 構成: LLM×1 + Random×7")
    lines.append(f"- 試合数: {len(results)}")
    lines.append(f"- 設定: baseline_v1 (flat, survival={config.survival_cash // 10_000}万)")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append("")

    total_cost = 0.0
    total_calls = 0
    total_valid = 0
    total_auto_commits = 0
    total_cache_read = 0

    for game_idx, (result, llm_agents, event_logger) in enumerate(results):
        agent = llm_agents[0]
        pid = agent.player_id
        player = result.players.get(pid)

        total_cost += agent.llm_logger.total_cost
        total_calls += agent.total_calls
        total_valid += agent.valid_json_count
        total_auto_commits += agent.auto_commit_count
        total_cache_read += agent.llm_logger.total_cache_read_tokens

        lines.append(f"## 試合 {game_idx + 1}\n")
        if player:
            status = "生還" if player.is_alive else f"脱落(R{player.elimination_round}, {player.elimination_reason})"
            lines.append(f"- {pid} 結果: **{status}**")
            lines.append(f"- 最終Cash: {player.cash:,}円 / Debt: {player.debt_balance:,}円")
        lines.append(f"- APIコール数: {agent.total_calls}")
        lines.append(f"- 有効JSON率: {agent.valid_json_count}/{agent.total_calls} ({agent.valid_json_count / max(agent.total_calls, 1) * 100:.0f}%)")
        lines.append(f"- AUTO COMMIT: {agent.auto_commit_count}回")
        lines.append(f"- コスト: ${agent.llm_logger.total_cost:.4f}")
        lines.append(f"- キャッシュ読取: {agent.llm_logger.total_cache_read_tokens:,}トークン")

        # repay確認
        repay_events = [
            e for e in event_logger.events
            if e.event_type == "NEGOTIATION_ACTION"
            and e.data.get("player_id") == pid
            and e.data.get("action") == "repay"
        ]
        lines.append(f"- repay発行回数: {len(repay_events)}")

        # strategyメモ抜粋
        if agent.strategy_history:
            lines.append("\n### strategyメモ抜粋")
            first = agent.strategy_history[0]
            lines.append(f"- R{first['round']} (最初): `{str(first['strategy'])[:200]}`")
            if len(agent.strategy_history) > 1:
                last = agent.strategy_history[-1]
                lines.append(f"- R{last['round']} (最後): `{str(last['strategy'])[:200]}`")

        lines.append("")

    # チェックリスト
    valid_rate = total_valid / max(total_calls, 1) * 100
    completed = all(r[0].round_count >= config.num_rounds for r in results)
    repay_issued = any(
        any(e.event_type == "NEGOTIATION_ACTION"
            and e.data.get("action") == "repay"
            and e.data.get("player_id") == r[1][0].player_id
            for e in r[2].events)
        for r in results
    )

    lines.append("## チェックリスト\n")
    lines.append(f"- [{'x' if valid_rate >= 80 else ' '}] 有効JSON率 80%以上（実績: {valid_rate:.0f}%）")
    lines.append(f"- [{'x' if completed else ' '}] {config.num_rounds}R最後まで走破")
    lines.append(f"- [{'x' if total_auto_commits == 0 else ' '}] AUTO COMMIT未発生（実績: {total_auto_commits}回）")
    lines.append(f"- [{'x' if repay_issued else ' '}] repayを発行した")
    lines.append(f"- 実コスト合計: **${total_cost:.4f}**")
    lines.append(f"- キャッシュ読取合計: {total_cache_read:,}トークン")
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def generate_phase_b_report(
    results: list[tuple[GameResult, list[LLMAgent], EventLogger]],
    config: GameConfig,
    elapsed: float,
    output_path: Path,
) -> None:
    """Phase Bレポートを生成する"""
    lines: list[str] = []
    lines.append("# Step 3B: 交渉試験レポート\n")
    lines.append(f"- モデル: Claude Haiku 4.5")
    lines.append(f"- 構成: LLM×2 + Random×{config.num_players - 2}")
    lines.append(f"- 試合数: {len(results)}")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append("")

    total_cost = 0.0
    total_dm = 0
    total_bc = 0

    for game_idx, (result, llm_agents, event_logger) in enumerate(results):
        llm_pids = {a.player_id for a in llm_agents}

        dm_events = [e for e in event_logger.events
                     if e.event_type == "NEGOTIATION_ACTION"
                     and e.data.get("player_id") in llm_pids
                     and e.data.get("action") == "dm"]
        bc_events = [e for e in event_logger.events
                     if e.event_type == "NEGOTIATION_ACTION"
                     and e.data.get("player_id") in llm_pids
                     and e.data.get("action") == "broadcast"]

        total_dm += len(dm_events)
        total_bc += len(bc_events)

        lines.append(f"## 試合 {game_idx + 1}\n")
        for agent in llm_agents:
            pid = agent.player_id
            player = result.players.get(pid)
            status = "生還" if player and player.is_alive else "脱落"
            total_cost += agent.llm_logger.total_cost
            lines.append(f"### {pid} ({status})")
            lines.append(f"- コスト: ${agent.llm_logger.total_cost:.4f}")
            lines.append(f"- 有効JSON: {agent.valid_json_count}/{agent.total_calls}")
            lines.append(f"- キャッシュ読取: {agent.llm_logger.total_cache_read_tokens:,}トークン")

        lines.append(f"\n- DM送信数: {len(dm_events)}")
        lines.append(f"- broadcast数: {len(bc_events)}")

        # strategyメモ
        for agent in llm_agents:
            if agent.strategy_history:
                lines.append(f"\n#### {agent.player_id} strategyメモ")
                for sh in agent.strategy_history[:5]:
                    lines.append(f"- R{sh['round']}T{sh.get('turn','?')}: `{str(sh['strategy'])[:150]}`")
                if len(agent.strategy_history) > 5:
                    lines.append(f"  ...他 {len(agent.strategy_history) - 5} 件")
        lines.append("")

    lines.append("## 行動観察サマリ\n")
    lines.append(f"- DM送信総数: {total_dm}")
    lines.append(f"- broadcast総数: {total_bc}")
    lines.append(f"- 実コスト合計: **${total_cost:.4f}**")
    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def run_trial_game_c(
    roster_keys: list[str],
    game_index: int,
    config: GameConfig,
    output_dir: Path,
    seed: int = 42,
) -> tuple[GameResult, list[LLMAgent], EventLogger, dict[str, str]]:
    """
    Phase C: 全LLM戦の1試合を実行する

    Returns:
        (GameResult, LLMAgentリスト, EventLogger, 座席⇔モデル対応表{pid: model_name})
    """
    game_seed = seed + game_index

    # 座席シャッフル（seedからランダム化）
    seat_rng = stdlib_random.Random(game_seed)
    shuffled_keys = list(roster_keys)
    seat_rng.shuffle(shuffled_keys)

    agents: dict[str, Any] = {}
    llm_agents: list[LLMAgent] = []
    seat_map: dict[str, str] = {}  # pid → model_name

    for i, model_key in enumerate(shuffled_keys):
        pid = f"P{i + 1:02d}"
        model_info = get_model(model_key)
        adapter = create_adapter(model_info)
        llm_logger = LLMLogger(
            output_dir / "llm_logs",
            game_id=f"game{game_index + 1:02d}_{pid}",
        )
        # 匿名化: player_idのみ。model_infoのname/providerはプロンプトに含めない
        agent = LLMAgent(pid, model_info, adapter, llm_logger, config)
        agents[pid] = agent
        llm_agents.append(agent)
        seat_map[pid] = f"{model_key}:{model_info.name}"

    event_logger = EventLogger()
    game = Game(config=config, agents=agents, seed=game_seed, logger=event_logger)

    # コスト上限付き実行: ラウンドごとにチェック
    # Game.run()を使い、コスト超過はエージェントレベルで既に制御されている
    # 全体上限はここでチェック
    result = game.run()

    # 全体コスト上限チェック（事後）
    total_cost = sum(a.llm_logger.total_cost for a in llm_agents)
    if total_cost > COST_LIMIT_TOTAL:
        print(f"  ⚠ 全体コスト上限${COST_LIMIT_TOTAL}超過: ${total_cost:.4f}")

    # イベントログ保存
    event_logger.save_jsonl(output_dir / f"game{game_index + 1:02d}_events.jsonl")

    # 座席⇔モデル対応表をファイルに保存
    seat_map_path = output_dir / f"game{game_index + 1:02d}_seat_map.json"
    seat_map_path.write_text(json.dumps(seat_map, indent=2, ensure_ascii=False), encoding="utf-8")

    return result, llm_agents, event_logger, seat_map


def generate_phase_c_report(
    results: list[tuple[GameResult, list[LLMAgent], EventLogger, dict[str, str]]],
    config: GameConfig,
    elapsed: float,
    output_path: Path,
) -> None:
    """Phase Cレポートを生成する"""
    lines: list[str] = []
    lines.append("# Step 3C: 全LLM戦レポート\n")
    lines.append(f"- 構成: **6社8モデル・LLM×8・Random 0体**")
    lines.append(f"- 設定: RULESET_BASELINE_V1 ({config.num_rounds}R, survival={config.survival_cash // 10_000}万)")
    lines.append(f"- 試合数: {len(results)}")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append("")

    total_cost = 0.0

    for game_idx, (result, llm_agents, event_logger, seat_map) in enumerate(results):
        lines.append(f"## 試合 {game_idx + 1}\n")

        # 座席⇔モデル対応表
        lines.append("### 座席⇔モデル対応表\n")
        lines.append("| 座席 | モデル | 借入額 | 最終Cash | 結果 |")
        lines.append("|---|---|---|---|---|")

        for agent in llm_agents:
            pid = agent.player_id
            model_name = seat_map.get(pid, "?")
            player = result.players.get(pid)
            if player:
                loan = f"{player.initial_loan // 10_000}万"
                cash = f"{player.cash:,}円"
                status = "**生還**" if player.is_alive else f"脱落(R{player.elimination_round}, {player.elimination_reason})"
            else:
                loan = cash = status = "?"
            lines.append(f"| {pid} | {model_name} | {loan} | {cash} | {status} |")

        # 生還者
        survivors = [p for p in result.survivors]
        lines.append(f"\n**生還者: {len(survivors)}人**")
        if survivors:
            for p in survivors:
                model = seat_map.get(p.player_id, "?")
                lines.append(f"- {p.player_id} ({model}): Cash={p.cash:,}円")

        # モデル別集計
        lines.append("\n### モデル別集計\n")
        lines.append("| 座席 | モデル | JSON率 | AUTO | エラー | コール数 | コスト |")
        lines.append("|---|---|---|---|---|---|---|")

        for agent in llm_agents:
            pid = agent.player_id
            model_name = seat_map.get(pid, "?")
            json_rate = f"{agent.valid_json_count}/{agent.total_calls}"
            pct = agent.valid_json_count / max(agent.total_calls, 1) * 100
            cost = agent.llm_logger.total_cost
            total_cost += cost
            # エラー数を集計
            errors = sum(1 for e in agent.llm_logger.entries if e.get("error"))
            lines.append(f"| {pid} | {model_name} | {json_rate} ({pct:.0f}%) | {agent.auto_commit_count} | {errors} | {agent.total_calls} | ${cost:.4f} |")

        # 行動観察
        lines.append("\n### 行動観察\n")
        llm_pids = {a.player_id for a in llm_agents}
        dm_count = 0
        bc_count = 0
        contract_count = 0
        repay_count = 0
        for e in event_logger.events:
            if e.event_type != "NEGOTIATION_ACTION":
                continue
            pid = e.data.get("player_id", "")
            if pid not in llm_pids:
                continue
            action = e.data.get("action", "")
            if action == "dm":
                dm_count += 1
            elif action == "broadcast":
                bc_count += 1
            elif action == "contract_propose":
                contract_count += 1
            elif action == "repay":
                repay_count += 1

        lines.append(f"- DM送信: {dm_count}件")
        lines.append(f"- 全体発言: {bc_count}件")
        lines.append(f"- 契約提案: {contract_count}件")
        lines.append(f"- 返済: {repay_count}件")

        # strategyメモからtrusted/distrustedを集計
        lines.append("\n### 信頼・警戒関係（strategyメモから）\n")
        for agent in llm_agents:
            pid = agent.player_id
            model = seat_map.get(pid, "?")
            trusted_set: set[str] = set()
            distrusted_set: set[str] = set()
            for sh in agent.strategy_history:
                s = sh.get("strategy", {})
                if isinstance(s, dict):
                    for t in s.get("trusted_players", []):
                        if isinstance(t, str):
                            trusted_set.add(t)
                    for d in s.get("distrusted_players", []):
                        if isinstance(d, str):
                            distrusted_set.add(d)
            if trusted_set or distrusted_set:
                trust_str = ", ".join(sorted(trusted_set)) if trusted_set else "なし"
                distrust_str = ", ".join(sorted(distrusted_set)) if distrusted_set else "なし"
                lines.append(f"- {pid} ({model}): 信頼→{trust_str} / 警戒→{distrust_str}")

        # strategyメモ抜粋（最初と最後）
        lines.append("\n### strategyメモ抜粋\n")
        for agent in llm_agents:
            pid = agent.player_id
            model = seat_map.get(pid, "?")
            if agent.strategy_history:
                first = agent.strategy_history[0]
                lines.append(f"**{pid} ({model})**")
                lines.append(f"- R{first['round']}(最初): `{str(first['strategy'])[:200]}`")
                if len(agent.strategy_history) > 1:
                    last = agent.strategy_history[-1]
                    lines.append(f"- R{last['round']}(最後): `{str(last['strategy'])[:200]}`")
                lines.append("")

        lines.append("")

    # 全体サマリ
    lines.append("## 全体サマリ\n")
    lines.append(f"- 実コスト合計: **${total_cost:.4f}**")
    total_calls = sum(a.total_calls for _, agents, _, _ in results for a in agents)
    total_valid = sum(a.valid_json_count for _, agents, _, _ in results for a in agents)
    lines.append(f"- 総コール数: {total_calls}")
    lines.append(f"- 有効JSON率: {total_valid}/{total_calls} ({total_valid / max(total_calls, 1) * 100:.0f}%)")
    lines.append("")

    output_path.write_text("\n".join(lines), encoding="utf-8")


def regenerate_report_from_logs(log_dir: Path) -> None:
    """
    修正8: 既存ログからPhase Bレポートを再生成（API呼出しなし）
    """
    llm_logs_dir = log_dir / "llm_logs"
    if not llm_logs_dir.exists():
        print(f"ログディレクトリが見つかりません: {llm_logs_dir}")
        return

    log_files = sorted(llm_logs_dir.glob("*.jsonl"))
    if not log_files:
        print("ログファイルが見つかりません")
        return

    # ゲームごとにグループ化
    games: dict[str, list[dict]] = {}
    for f in log_files:
        # game01_P01_llm_calls.jsonl → game01
        game_key = f.stem.split("_")[0]
        if game_key not in games:
            games[game_key] = []
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                entry = json.loads(line.strip())
                games[game_key].append(entry)

    lines: list[str] = []
    lines.append("# Phase B レポート（ログから再生成）\n")
    lines.append(f"- ソース: {log_dir}")
    lines.append(f"- 試合数: {len(games)}")
    lines.append("")

    total_cost = 0.0
    total_dm = 0
    total_bc = 0

    for game_key in sorted(games.keys()):
        entries = games[game_key]
        lines.append(f"## {game_key}\n")

        # プレイヤー別集計
        players: dict[str, dict] = {}
        for e in entries:
            pid = e.get("player_id", "")
            if pid not in players:
                players[pid] = {"calls": 0, "valid": 0, "cost": 0.0,
                                "strategies": [], "cache_read": 0}
            p = players[pid]
            p["calls"] += 1
            p["cost"] += e.get("cost_usd", 0)
            p["cache_read"] += e.get("cache_read_input_tokens", 0)
            total_cost += e.get("cost_usd", 0)

            # 有効JSONカウント
            resp = e.get("response_text", "")
            if resp and not e.get("error"):
                try:
                    from llm.response_parser import extract_json
                    if extract_json(resp):
                        p["valid"] += 1
                except Exception:
                    pass

            # strategy抽出
            if resp:
                try:
                    from llm.response_parser import extract_json
                    data = extract_json(resp)
                    if data and "strategy" in data:
                        p["strategies"].append({
                            "round": e.get("round_num"),
                            "turn": e.get("turn"),
                            "strategy": data["strategy"],
                        })
                except Exception:
                    pass

        for pid, p in sorted(players.items()):
            lines.append(f"### {pid}")
            lines.append(f"- コール: {p['calls']}, 有効JSON: {p['valid']}/{p['calls']}")
            lines.append(f"- コスト: ${p['cost']:.4f}, キャッシュ読取: {p['cache_read']:,}")
            if p["strategies"]:
                lines.append("- strategyメモ:")
                for s in p["strategies"][:3]:
                    lines.append(f"  R{s['round']}T{s.get('turn','?')}: `{str(s['strategy'])[:150]}`")
            lines.append("")

    lines.append(f"## サマリ\n")
    lines.append(f"- 実コスト合計: **${total_cost:.4f}**")
    lines.append("")

    report_path = log_dir / "trial_B_report_regenerated.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"レポート生成: {report_path}")


def regenerate_phase_c_report(log_dir: Path) -> None:
    """既存ログからPhase Cレポートを完全再生成（レイテンシ分析・行動観察・信頼関係含む）"""
    from llm.response_parser import extract_json

    llm_logs_dir = log_dir / "llm_logs"
    seat_map_path = log_dir / "game01_seat_map.json"
    events_path = log_dir / "game01_events.jsonl"

    if not llm_logs_dir.exists():
        print(f"ログディレクトリが見つかりません: {llm_logs_dir}")
        return

    # 座席マップ読み込み
    seat_map: dict[str, str] = {}
    if seat_map_path.exists():
        seat_map = json.loads(seat_map_path.read_text(encoding="utf-8"))

    # イベントログ読み込み
    events: list[dict] = []
    if events_path.exists():
        with open(events_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    events.append(json.loads(line.strip()))

    # LLMコールログをプレイヤー別に読み込み
    player_calls: dict[str, list[dict]] = {}
    for pid in sorted(seat_map.keys()):
        fn = llm_logs_dir / f"game01_{pid}_llm_calls.jsonl"
        if fn.exists():
            with open(fn, encoding="utf-8") as f:
                player_calls[pid] = [json.loads(l.strip()) for l in f if l.strip()]
        else:
            player_calls[pid] = []

    # --- イベントログからゲーム結果を抽出 ---
    game_end = next((e for e in events if e.get("event_type") == "GAME_END"), None)
    survivors_data = game_end["data"]["survivors"] if game_end else []
    eliminated_data = game_end["data"].get("eliminated", []) if game_end else []

    # 借入額（LOAN_CHOSEN / LOAN_DECISION の両方に対応）
    loan_events: dict[str, int] = {}
    for e in events:
        if e.get("event_type") in ("LOAN_CHOSEN", "LOAN_DECISION"):
            pid = e["data"]["player_id"]
            amt = e["data"].get("loan_amount") or e["data"].get("amount", 0)
            loan_events[pid] = amt

    # 脱落情報（SURVIVAL_CHECK + BANKRUPTCY + FORCED_LIQUIDATION）
    elim_info: dict[str, dict] = {}
    for e in events:
        etype = e.get("event_type", "")
        if etype == "SURVIVAL_CHECK" and e["data"].get("result") == "eliminated":
            pid = e["data"]["player_id"]
            elim_info[pid] = {"round": e.get("round_num", "?"), "reason": e["data"].get("reason", "?")}
        elif etype == "BANKRUPTCY":
            pid = e["data"]["player_id"]
            elim_info.setdefault(pid, {"round": e.get("round_num", "?"), "reason": "bankruptcy"})
        elif etype == "FORCED_LIQUIDATION":
            pid = e["data"]["player_id"]
            elim_info.setdefault(pid, {"round": e.get("round_num", "?"), "reason": e["data"].get("reason", "?")})

    # 実行時間（最初と最後のイベントのtimestamp差）
    if events:
        from datetime import datetime as dt
        ts_first = events[0].get("timestamp", "")
        ts_last = events[-1].get("timestamp", "")
        try:
            t0 = dt.fromisoformat(ts_first)
            t1 = dt.fromisoformat(ts_last)
            elapsed = (t1 - t0).total_seconds()
        except Exception:
            elapsed = 0.0
    else:
        elapsed = 0.0

    # --- プレイヤー別統計 ---
    stats: dict[str, dict[str, Any]] = {}
    for pid, calls in player_calls.items():
        valid = 0
        auto_commit = 0
        errors = 0
        latencies: list[float] = []
        cost = 0.0
        strategies: list[dict] = []

        for c in calls:
            cost += c.get("cost_usd", 0) or 0
            elapsed_ms = c.get("elapsed_ms", 0) or 0
            latencies.append(elapsed_ms / 1000.0)

            if c.get("error"):
                errors += 1
                continue

            resp = c.get("response_text", "")
            if resp:
                try:
                    d = extract_json(resp)
                    if d:
                        valid += 1
                        if "strategy" in d:
                            strategies.append({
                                "round": c.get("round_num"),
                                "turn": c.get("turn"),
                                "strategy": d["strategy"],
                            })
                except Exception:
                    pass

        # AUTO COMMITカウント（イベントログから）
        for e in events:
            if (e.get("event_type") == "AUTO_COMMIT"
                    and e.get("data", {}).get("player_id") == pid):
                auto_commit += 1

        total_lat = sum(latencies)
        avg_lat = total_lat / len(latencies) if latencies else 0

        stats[pid] = {
            "calls": len(calls),
            "valid": valid,
            "auto_commit": auto_commit,
            "errors": errors,
            "avg_lat": avg_lat,
            "total_lat": total_lat,
            "cost": cost,
            "strategies": strategies,
            "latencies": latencies,
        }

    # --- レポート生成 ---
    lines: list[str] = []
    lines.append("# Step 3C: 全LLM戦レポート\n")
    lines.append("- 構成: **6社8モデル・LLM×8・Random 0体**")
    lines.append("- 設定: RULESET_BASELINE_V1 (12R, survival=200万)")
    lines.append(f"- 実行時間: {elapsed:.0f}秒 ({elapsed / 3600:.1f}時間)")
    lines.append("")

    # === 座席⇔モデル対応表 ===
    lines.append("## 座席⇔モデル対応表\n")
    lines.append("| 座席 | モデル | 借入額 | 最終Cash | 結果 |")
    lines.append("|---|---|---|---|---|")
    survivor_pids = {s["player_id"] for s in survivors_data}
    for pid in sorted(seat_map.keys()):
        model = seat_map.get(pid, "?")
        loan = loan_events.get(pid, 0)
        loan_str = f"{loan // 10_000}万" if loan else "?"

        if pid in survivor_pids:
            cash = next(s["cash"] for s in survivors_data if s["player_id"] == pid)
            status = f"**生還** (Cash={cash:,}円)"
        elif pid in elim_info:
            ei = elim_info[pid]
            status = f"脱落(R{ei['round']}, {ei['reason']})"
            cash = 0
        else:
            status = "?"
            cash = 0
        lines.append(f"| {pid} | {model} | {loan_str} | {cash:,}円 | {status} |")

    lines.append(f"\n**生還者: {len(survivors_data)}人**")
    for s in survivors_data:
        model = seat_map.get(s["player_id"], "?")
        lines.append(f"- {s['player_id']} ({model}): Cash={s['cash']:,}円")

    # === モデル別集計 ===
    lines.append("\n## モデル別集計\n")
    lines.append("| 座席 | モデル | JSON率 | AUTO | エラー | コール数 | 平均レイテンシ | 総レイテンシ | コスト |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    total_cost = 0.0
    total_calls = 0
    total_valid = 0
    for pid in sorted(seat_map.keys()):
        s = stats.get(pid, {})
        model = seat_map.get(pid, "?")
        calls_n = s.get("calls", 0)
        valid_n = s.get("valid", 0)
        pct = valid_n / max(calls_n, 1) * 100
        cost = s.get("cost", 0)
        total_cost += cost
        total_calls += calls_n
        total_valid += valid_n
        avg_lat = s.get("avg_lat", 0)
        total_lat = s.get("total_lat", 0)
        lines.append(
            f"| {pid} | {model} | {valid_n}/{calls_n} ({pct:.0f}%) "
            f"| {s.get('auto_commit', 0)} | {s.get('errors', 0)} | {calls_n} "
            f"| {avg_lat:.1f}s | {total_lat:.0f}s | ${cost:.4f} |"
        )

    # === 所要時間分析 ===
    lines.append("\n## 所要時間分析\n")
    lines.append(f"- 全体: {elapsed:.0f}秒 ({elapsed / 3600:.1f}時間)")
    lines.append("")
    lines.append("| 座席 | モデル | 総レイテンシ | 全体比 | コール数 | 平均/コール |")
    lines.append("|---|---|---|---|---|---|")
    # ソート: 総レイテンシ降順
    sorted_pids = sorted(stats.keys(), key=lambda p: stats[p].get("total_lat", 0), reverse=True)
    total_lat_all = sum(s.get("total_lat", 0) for s in stats.values())
    for pid in sorted_pids:
        s = stats[pid]
        model = seat_map.get(pid, "?")
        tl = s.get("total_lat", 0)
        pct = tl / max(total_lat_all, 1) * 100
        lines.append(
            f"| {pid} | {model} | {tl:.0f}s ({tl / 3600:.1f}h) "
            f"| {pct:.1f}% | {s.get('calls', 0)} | {s.get('avg_lat', 0):.1f}s |"
        )
    lines.append("")
    # ボトルネック分析
    top = sorted_pids[0] if sorted_pids else None
    if top:
        ts = stats[top]
        lines.append(
            f"**ボトルネック**: {top} ({seat_map.get(top, '?')}) が"
            f"総レイテンシの{ts.get('total_lat', 0) / max(total_lat_all, 1) * 100:.1f}%を占める。"
        )
        if ts.get("calls", 0) > 150:
            lines.append(
                f"JSON率{ts.get('valid', 0)}/{ts.get('calls', 0)}"
                f" ({ts.get('valid', 0) / max(ts.get('calls', 0), 1) * 100:.0f}%)"
                f"が低く、大量のリトライが発生。"
                f"平均レイテンシ{ts.get('avg_lat', 0):.1f}sも長い。"
            )

    # === 行動観察 ===
    lines.append("\n## 行動観察\n")

    # 交渉アクション集計
    dm_count = 0
    bc_count = 0
    contract_propose = 0
    contract_accept = 0
    repay_count = 0
    transfer_count = 0
    transfer_total = 0
    dm_pairs: dict[str, int] = {}  # "P01→P02": count

    for e in events:
        if e.get("event_type") != "NEGOTIATION_ACTION":
            continue
        action = e.get("data", {}).get("action", "")
        pid = e.get("data", {}).get("player_id", "")
        if action == "dm":
            dm_count += 1
            target = e.get("data", {}).get("target", "")
            if pid and target:
                key = f"{pid}→{target}"
                dm_pairs[key] = dm_pairs.get(key, 0) + 1
        elif action == "broadcast":
            bc_count += 1
        elif action == "contract_propose":
            contract_propose += 1
        elif action == "contract_accept":
            contract_accept += 1
        elif action == "repay":
            repay_count += 1
        elif action == "transfer":
            transfer_count += 1
            transfer_total += e.get("data", {}).get("amount", 0)

    lines.append(f"- DM送信: {dm_count}件")
    lines.append(f"- 全体発言(broadcast): {bc_count}件")
    lines.append(f"- 契約提案: {contract_propose}件 / 契約承諾: {contract_accept}件")
    lines.append(f"- 返済: {repay_count}件")
    lines.append(f"- 送金: {transfer_count}件 (総額{transfer_total:,}円)")

    # DM頻度上位
    if dm_pairs:
        lines.append("\n**DM頻度上位:**")
        for pair, cnt in sorted(dm_pairs.items(), key=lambda x: -x[1])[:10]:
            lines.append(f"- {pair}: {cnt}回")

    # カード使用パターン（市場結果から）
    lines.append("\n**市場結果:**")
    market_results = [e for e in events if e.get("event_type") == "MARKET_RESULT"]
    for mr in market_results:
        rd = mr.get("round_num", "?")
        data = mr.get("data", {})
        mid = data.get("market_id", "?")
        winners = data.get("winners", [])
        prize = data.get("prize_per_winner", 0)
        n_part = data.get("participants", 0)
        pool = data.get("total_pool", 0)
        winners_str = ", ".join(winners) if isinstance(winners, list) else str(winners)
        if winners:
            lines.append(f"- R{rd} {mid}: 勝者={winners_str}, 賞金/人={prize:,}円, 参加={n_part}人, プール={pool:,}円")

    # 温存戦略・嘘の検出（strategyメモのテキストマイニング）
    lines.append("\n**戦略的観察（strategyメモから）:**")
    for pid in sorted(seat_map.keys()):
        s = stats.get(pid, {})
        model = seat_map.get(pid, "?")
        observations: list[str] = []

        for st in s.get("strategies", []):
            strategy = st.get("strategy", {})
            if isinstance(strategy, dict):
                reason = str(strategy.get("reason", ""))
                card_plan = str(strategy.get("card_plan", ""))
                goal = str(strategy.get("current_goal", ""))
                all_text = reason + card_plan + goal

                # 温存戦略の検出
                if any(kw in all_text for kw in ["温存", "残す", "取っておく", "save", "reserve"]):
                    if f"温存戦略(R{st['round']})" not in observations:
                        observations.append(f"温存戦略(R{st['round']})")
                # 嘘・ブラフ検出
                if any(kw in all_text for kw in ["嘘", "ブラフ", "bluff", "偽", "装う", "騙"]):
                    if f"ブラフ言及(R{st['round']})" not in observations:
                        observations.append(f"ブラフ言及(R{st['round']})")
                # カウンティング言及
                if any(kw in all_text for kw in ["カウント", "counting", "残りカード", "使用済み"]):
                    if f"カウンティング(R{st['round']})" not in observations:
                        observations.append(f"カウンティング(R{st['round']})")
                # 空き巣狙い
                if any(kw in all_text for kw in ["空き巣", "単独", "独占"]):
                    if f"空き巣狙い(R{st['round']})" not in observations:
                        observations.append(f"空き巣狙い(R{st['round']})")

        if observations:
            lines.append(f"- {pid} ({model}): {', '.join(observations[:8])}")

    # === 信頼・警戒関係（strategyのreason文テキストマイニング） ===
    lines.append("\n## 信頼・警戒関係\n")
    all_pids = sorted(seat_map.keys())
    for pid in all_pids:
        s = stats.get(pid, {})
        model = seat_map.get(pid, "?")
        trust_mentions: dict[str, int] = {}
        distrust_mentions: dict[str, int] = {}

        for st in s.get("strategies", []):
            strategy = st.get("strategy", {})
            if isinstance(strategy, dict):
                text = json.dumps(strategy, ensure_ascii=False)
                for other_pid in all_pids:
                    if other_pid == pid:
                        continue
                    if other_pid not in text:
                        continue
                    # 信頼の文脈
                    for kw in ["協力", "信頼", "味方", "友好", "同盟", "提携", "連携"]:
                        if kw in text and other_pid in text:
                            trust_mentions[other_pid] = trust_mentions.get(other_pid, 0) + 1
                            break
                    # 警戒の文脈
                    for kw in ["警戒", "敵", "妨害", "脅威", "危険", "潰す", "道連れ", "裏切"]:
                        if kw in text and other_pid in text:
                            distrust_mentions[other_pid] = distrust_mentions.get(other_pid, 0) + 1
                            break

        if trust_mentions or distrust_mentions:
            trust_str = ", ".join(
                f"{p}({n}回)" for p, n in sorted(trust_mentions.items(), key=lambda x: -x[1])
            ) if trust_mentions else "なし"
            distrust_str = ", ".join(
                f"{p}({n}回)" for p, n in sorted(distrust_mentions.items(), key=lambda x: -x[1])
            ) if distrust_mentions else "なし"
            lines.append(f"- {pid} ({model}): 協力的言及→{trust_str} / 警戒的言及→{distrust_str}")

    # === strategyメモ抜粋 ===
    lines.append("\n## strategyメモ抜粋\n")
    for pid in sorted(seat_map.keys()):
        s = stats.get(pid, {})
        model = seat_map.get(pid, "?")
        strats = s.get("strategies", [])
        if strats:
            first = strats[0]
            lines.append(f"**{pid} ({model})**")
            lines.append(f"- R{first['round']}(最初): `{str(first['strategy'])[:200]}`")
            if len(strats) > 1:
                last = strats[-1]
                lines.append(f"- R{last['round']}(最後): `{str(last['strategy'])[:200]}`")
            lines.append("")

    # === 全体サマリ ===
    lines.append("\n## 全体サマリ\n")
    lines.append(f"- 実コスト合計: **${total_cost:.4f}**")
    lines.append(f"- 総コール数: {total_calls}")
    lines.append(f"- 有効JSON率: {total_valid}/{total_calls} ({total_valid / max(total_calls, 1) * 100:.0f}%)")

    winner = survivors_data[0] if survivors_data else None
    if winner:
        winner_model = seat_map.get(winner["player_id"], "?")
        lines.append(f"- **勝者: {winner['player_id']} ({winner_model}), Cash={winner['cash']:,}円**")
    lines.append("")

    report_path = log_dir / "trial_C_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"Phase Cレポート生成: {report_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="LLM試験")
    parser.add_argument("--phase", type=str, choices=["A", "B", "C"],
                        help="Phase A(接続) / B(交渉) / C(全LLM戦)")
    parser.add_argument("--games", type=int, default=None,
                        help="試合数（A既定:1, B既定:5）")
    parser.add_argument("--seed", type=int, default=42, help="ベースseed")
    # 修正5: 検証モード
    parser.add_argument("--preset", type=str, choices=["dev"], default=None,
                        help="プリセット: dev(6R/5巡/1試合)")
    parser.add_argument("--rounds", type=int, default=None, help="ラウンド数（上書き）")
    parser.add_argument("--max-turns", type=int, default=None, help="交渉最大巡数（上書き）")
    # 修正8: レポート再生成
    parser.add_argument("--regenerate-report", type=str, default=None,
                        help="既存ログディレクトリからPhase Bレポートを再生成")
    parser.add_argument("--regenerate-c-report", type=str, default=None,
                        help="既存ログディレクトリからPhase Cレポートを再生成")
    parser.add_argument("--model", type=str, default=None,
                        help="Phase Aのモデルキーを上書き（例: M5）。デフォルト: L1(Haiku)")
    parser.add_argument("--ruleset", type=str, default="S1",
                        choices=["S1", "S2"],
                        help="ルールセット: S1(Season 1, 既定) / S2(Season 2)")
    parser.add_argument("--roster", type=str, default=None,
                        help='Phase C ロスター（カンマ区切り、例: "M6,L6,M5,L2,L1,M3"）')
    args = parser.parse_args()

    # 修正8: レポート再生成モード
    if args.regenerate_report:
        regenerate_report_from_logs(Path(args.regenerate_report))
        return

    if args.regenerate_c_report:
        regenerate_phase_c_report(Path(args.regenerate_c_report))
        return

    if not args.phase:
        parser.error("--phase A, B or C is required (unless --regenerate-report)")

    # Phase C ロスター決定（--roster指定 or デフォルト）
    phase_c_roster = args.roster.split(",") if args.roster else PHASE_C_ROSTER
    # Phase C はロスター長、A/B は8人固定
    num_players = len(phase_c_roster) if args.phase == "C" else 8

    if args.ruleset == "S2":
        config = GameConfig.baseline_v1_s2(num_players=num_players)
    else:
        config = GameConfig.baseline_v1(num_players=num_players)

    # 修正5: presetの適用
    if args.preset == "dev":
        num_rounds = args.rounds or 6
        max_turns = args.max_turns or 5
        games_override = args.games or 1
        # prize_tiersを切り詰め
        config = config.model_copy(update={
            "num_rounds": num_rounds,
            "prize_tiers": config.prize_tiers[:num_rounds],
            "total_prize": sum(config.prize_tiers[:num_rounds]),
            "negotiation_max_turns": max_turns,
        })
        print(f"[dev preset] {num_rounds}R, {max_turns}巡, {games_override}試合")
    else:
        games_override = args.games
        if args.rounds:
            num_rounds = args.rounds
            config = config.model_copy(update={
                "num_rounds": num_rounds,
                "prize_tiers": config.prize_tiers[:num_rounds],
                "total_prize": sum(config.prize_tiers[:num_rounds]),
            })
        if args.max_turns:
            config = config.model_copy(update={"negotiation_max_turns": args.max_turns})

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = Path(f"logs/llm/trial_{args.phase}_{timestamp}")
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.phase == "A":
        num_games = games_override or 1
        llm_count = 1
        print(f"=== Step 3A: 接続試験 ===")
        print(f"構成: Haiku×{llm_count} + Random×{config.num_players - llm_count}")
    elif args.phase == "B":
        num_games = games_override or 5
        llm_count = 2
        print(f"=== Step 3B: 交渉試験 ===")
        print(f"構成: Haiku×{llm_count} + Random×{config.num_players - llm_count}")
    else:  # Phase C
        num_games = games_override or 1
        print(f"=== Step 3C: 全LLM戦（{len(phase_c_roster)}体） ===")
        print(f"ロスター: {', '.join(phase_c_roster)}")

    print(f"試合数: {num_games}, {config.num_rounds}R, 交渉{config.negotiation_max_turns}巡")
    print(f"コスト上限: エージェント${COST_LIMIT_PER_GAME}, 全体${COST_LIMIT_TOTAL}")
    print(f"出力: {output_dir}")
    print("---")

    start = time.time()

    if args.phase == "C":
        # Phase C: 全LLM戦
        all_results_c: list[tuple[GameResult, list[LLMAgent], EventLogger, dict[str, str]]] = []
        for i in range(num_games):
            print(f"試合 {i + 1}/{num_games} 実行中...")
            try:
                result, llm_agents, event_logger, seat_map = run_trial_game_c(
                    phase_c_roster, i, config, output_dir, args.seed,
                )
                all_results_c.append((result, llm_agents, event_logger, seat_map))
                # 進捗表示
                print(f"  座席⇔モデル対応:")
                for pid, model_name in sorted(seat_map.items()):
                    agent = next((a for a in llm_agents if a.player_id == pid), None)
                    p = result.players.get(pid)
                    if agent and p:
                        status = "生還" if p.is_alive else "脱落"
                        print(f"    {pid} ({model_name}): {status}, "
                              f"${agent.llm_logger.total_cost:.4f}, "
                              f"JSON:{agent.valid_json_count}/{agent.total_calls}")
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"  エラー: {e}\n{tb}")
                try:
                    (output_dir / f"error_game{i + 1:02d}.txt").write_text(tb, encoding="utf-8")
                except Exception:
                    pass

        elapsed = time.time() - start
        report_path = output_dir / "trial_C_report.md"
        generate_phase_c_report(all_results_c, config, elapsed, report_path)
    else:
        # Phase A/B
        all_results: list[tuple[GameResult, list[LLMAgent], EventLogger]] = []
        for i in range(num_games):
            print(f"試合 {i + 1}/{num_games} 実行中...")
            try:
                result, llm_agents, event_logger = run_trial_game(
                    llm_count, i, config, output_dir, args.seed,
                    model_key=args.model,
                )
                all_results.append((result, llm_agents, event_logger))
                for agent in llm_agents:
                    pid = agent.player_id
                    p = result.players.get(pid)
                    status = "生還" if p and p.is_alive else "脱落"
                    print(f"  {pid}: {status}, ${agent.llm_logger.total_cost:.4f}, "
                          f"JSON:{agent.valid_json_count}/{agent.total_calls}, "
                          f"cache:{agent.llm_logger.total_cache_read_tokens:,}")
            except Exception as e:
                import traceback
                tb = traceback.format_exc()
                print(f"  エラー: {e}\n{tb}")
                try:
                    (output_dir / f"error_game{i + 1:02d}.txt").write_text(tb, encoding="utf-8")
                except Exception:
                    pass

        elapsed = time.time() - start

        if args.phase == "A":
            report_path = output_dir / "trial_A_report.md"
            generate_phase_a_report(all_results, config, elapsed, report_path)
        else:
            report_path = output_dir / "trial_B_report.md"
            generate_phase_b_report(all_results, config, elapsed, report_path)

    print(f"\n=== 完了 ===")
    print(f"実行時間: {elapsed:.1f}秒")
    if args.phase == "C":
        tc = sum(a.llm_logger.total_cost for _, agents, _, _ in all_results_c for a in agents)
    else:
        tc = sum(a.llm_logger.total_cost for _, agents, _ in all_results for a in agents)
    print(f"総コスト: ${tc:.4f}")
    print(f"レポート: {report_path}")


if __name__ == "__main__":
    main()
