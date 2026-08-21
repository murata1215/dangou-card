"""L1〜L6 R12試験ログから、AFTER固定レポートを生成する（API送信なし）。"""

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# ``uv run python scripts/...`` で直接起動しても、repo rootのllm packageを
# 読めるようにする。他のscriptsと同じ明示的なimport path設定である。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.response_parser import extract_json


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """空行を除いてJSONLを読む。

    Args:
        path: 読み込むJSONLのパス。

    Returns:
        読み込めたJSON objectの順序付きリスト。
    """
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _call_summary(calls: list[dict[str, Any]]) -> dict[str, int | float]:
    """LLMLoggerの行を、token・JSON・障害の比較可能な集計へ正規化する。"""
    api_calls = [call for call in calls if call.get("api_called", True)]
    input_tokens = sum(int(call.get("input_tokens", 0) or 0) for call in api_calls)
    cached_tokens = sum(int(call.get("cache_read_input_tokens", 0) or 0) for call in api_calls)
    output_tokens = sum(int(call.get("output_tokens", 0) or 0) for call in api_calls)
    total_tokens = sum(
        int(call.get("total_tokens", 0) or 0) or
        int(call.get("input_tokens", 0) or 0) + int(call.get("output_tokens", 0) or 0)
        for call in api_calls
    )
    valid_json = 0
    for call in api_calls:
        if call.get("error") is None and call.get("response_text"):
            try:
                valid_json += int(extract_json(call["response_text"]) is not None)
            except Exception:
                # 壊れた生応答も試験の一次記録として残し、集計だけ0件扱いにする。
                continue
    return {
        "calls": len(api_calls),
        "input": input_tokens,
        "cached": cached_tokens,
        "output": output_tokens,
        "thinking": max(0, total_tokens - input_tokens - output_tokens),
        "cost": sum(float(call.get("cost_usd", 0) or 0) for call in api_calls),
        "valid_json": valid_json,
        "errors": sum(int(call.get("error") is not None) for call in api_calls),
        "timeouts": sum(int(call.get("error_type") == "timeout") for call in api_calls),
        "retries": sum(int(call.get("retry_count", 0) or 0) for call in api_calls),
        "budget_blocks": sum(int(call.get("budget_blocked", False)) for call in calls),
    }


def generate_report(log_dir: Path, output_path: Path) -> None:
    """試験ログをAFTER比較用Markdownへ固定する。

    Args:
        log_dir: ``scripts/llm_trial.py`` が生成したtrial_Cディレクトリ。
        output_path: 保存するMarkdownパス。親ディレクトリは作成する。
    """
    manifest = json.loads((log_dir / "trial_manifest.json").read_text(encoding="utf-8"))
    seat_map = json.loads((log_dir / "game01_seat_map.json").read_text(encoding="utf-8"))
    events = _read_jsonl(log_dir / "game01_events.jsonl")
    game_end = next((event for event in reversed(events) if event["event_type"] == "GAME_END"), None)
    aborted = next((event for event in events if event["event_type"] == "GAME_ABORTED"), None)

    lines = ["# L1〜L6 Season 2 R12 実戦試験結果\n"]
    lines.extend([
        f"- 試験ログ: `{log_dir}`",
        f"- 完走: **{'いいえ（中断）' if aborted else 'はい'}**",
        f"- seed: `{manifest['seed']}` / ruleset: `{manifest['ruleset']}` / roster: `{','.join(manifest['roster_keys'])}`",
        f"- 実効 output 上限: **{manifest['models'][0]['effective_max_output_tokens']} tokens（全席統一）**",
        f"- CoT: `{manifest['enable_cot']}` / cost cap: `${manifest['per_player_game_cost_cap_usd']}/player`, `${manifest['game_cost_cap_usd']}/game`",
        "",
        "## ラウンド別生存者\n",
        "| Round | 生存者数 | 生存者 |",
        "|---:|---:|---|",
    ])
    for event in events:
        if event["event_type"] == "ROUND_COMPLETE":
            data = event["data"]
            lines.append(f"| R{event['round_num']} | {data['alive_count']} | {', '.join(data['alive_players'])} |")
    if aborted:
        lines.append(f"\n- 中断: R{aborted['round_num']} / `{aborted['data']['reason']}`")

    lines.extend(["\n## プレイヤー別 API・結果\n",
                  "| Seat | Model | Calls | Input | Cached | Output | Thinking | Cost | JSON | AUTO | Error / Timeout / Retry | Budget block |",
                  "|---|---|---:|---:|---:|---:|---:|---:|---|---:|---|---:|"])
    for pid in sorted(seat_map):
        calls = _read_jsonl(log_dir / "llm_logs" / f"game01_{pid}_llm_calls.jsonl")
        summary = _call_summary(calls)
        auto = sum(
            int(event["event_type"] == "AUTO_COMMIT" and event["data"].get("player_id") == pid)
            for event in events
        )
        lines.append(
            f"| {pid} | {seat_map[pid]} | {summary['calls']} | {summary['input']:,} | "
            f"{summary['cached']:,} | {summary['output']:,} | {summary['thinking']:,} | "
            f"${summary['cost']:.4f} | {summary['valid_json']}/{summary['calls']} | {auto} | "
            f"{summary['errors']} / {summary['timeouts']} / {summary['retries']} | {summary['budget_blocks']} |"
        )

    end_data = game_end["data"] if game_end else {}
    survivors = end_data.get("survivors", [])
    eliminated = end_data.get("eliminated", [])
    lines.extend(["\n## 最終結果\n", "| Rank | Seat | Model | 最終Cash | 結果 |", "|---:|---|---|---:|---|"])
    for rank, player in enumerate(survivors, start=1):
        pid = player["player_id"]
        lines.append(f"| {rank} | {pid} | {seat_map.get(pid, '?')} | {player['cash']:,}円 | survived |")
    for player in sorted(eliminated, key=lambda item: (item.get("round") or 99, item["player_id"])):
        pid = player["player_id"]
        lines.append(f"| - | {pid} | {seat_map.get(pid, '?')} | 0円 | R{player.get('round')} {player.get('reason')} |")

    market_open = [event for event in events if event["event_type"] == "MARKET_OPEN"]
    for expected_round, label in ((1, "通常R"), (manifest["num_rounds"], "R12")):
        event = next((item for item in market_open if item["round_num"] == expected_round), None)
        if event:
            total = sum(market["base_prize"] for market in event["data"]["markets"])
            lines.append(f"\n- {label}基本賞金（3市場合計）: `{total:,}円`")

    lines.extend([
        "\n## Console AFTER 比較\n",
        "AFTER値はProvider Consoleから手入力する。BEFOREと集計単位が異なるGoogle、Moonshot、xAI、DeepSeekは、"
        "内部推定コストとの一対一比較を行わない。",
        "",
        "| Provider | BEFORE | AFTER | 差分 | 比較上の注意 |",
        "|---|---:|---:|---:|---|",
        "| Anthropic | $9.37 | 未採取 | 未算出 | API key画面の期間を確認 |",
        "| OpenAI | $3.16 | 未採取 | 未算出 | project月次 |",
        "| Google | ¥8,706 / ¥10,000 | 未採取 | 未算出 | project・28日集計 |",
        "| Moonshot | balance $18.61851 | 未採取 | 未算出 | account集計 |",
        "| xAI | usage $2.51 | 未採取 | 未算出 | team・30日集計 |",
        "| DeepSeek | total cost $0.74 | 未採取 | 未算出 | account累計・peak/off-peak注意 |",
        "",
    ])
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    """CLI入口。ログディレクトリからのみ結果を再構成し、APIを呼ばない。"""
    parser = argparse.ArgumentParser(description="L1〜L6 R12 AFTERレポート生成（API送信なし）")
    parser.add_argument("--log-dir", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    generate_report(args.log_dir, args.output)
    print(f"AFTERレポート生成: {args.output}")


if __name__ == "__main__":
    main()
