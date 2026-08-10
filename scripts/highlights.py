"""
ハイライトログ自動生成スクリプト

イベントJSONL + LLMログから名場面を検出しMDレポートを生成する。
14ルールのルールベース検出（LLM不使用）。

使用方法:
    uv run python scripts/highlights.py --input-dir logs/llm/trial_A_20260809_175006/
"""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _extract_market_mentions(text: str) -> list[str]:
    """テキストからM01〜M99の市場名を正規表現で抽出する"""
    return re.findall(r'M\d{2}', text)


def _extract_card_mentions(text: str) -> list[str]:
    """テキストからカード名を正規表現で抽出する"""
    cards = [
        "ROYAL_FLUSH", "STRAIGHT_FLUSH", "FOUR_OF_A_KIND", "FULL_HOUSE",
        "FLUSH", "STRAIGHT", "THREE_OF_A_KIND", "TWO_PAIR", "ONE_PAIR", "HIGH_CARD",
    ]
    found = []
    for c in cards:
        if c in text.upper().replace(" ", "_"):
            found.append(c)
    return found


def detect_highlights(
    events: list[dict],
    llm_entries: list[dict],
    game_id: str,
) -> list[dict[str, Any]]:
    """
    14ルールのハイライトを検出する

    Returns:
        ハイライトイベントのリスト
    """
    highlights: list[dict[str, Any]] = []

    # --- COMMITイベントの収集 ---
    commits_by_round: dict[int, dict[str, dict]] = defaultdict(dict)
    for e in events:
        if e.get("event_type") == "COMMIT":
            rnd = e.get("round_num", 0)
            pid = e.get("data", {}).get("player_id", "")
            commits_by_round[rnd][pid] = e.get("data", {})

    # --- broadcastメッセージの収集 ---
    broadcasts_by_round: dict[int, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("event_type") == "NEGOTIATION_ACTION" and e.get("data", {}).get("action") == "broadcast":
            rnd = e.get("round_num", 0)
            broadcasts_by_round[rnd].append(e.get("data", {}))

    # --- strategyメモの収集 ---
    strategies_by_round_pid: dict[tuple[int, str], dict] = {}
    for entry in llm_entries:
        resp = entry.get("response_text", "")
        if not resp:
            continue
        try:
            data = json.loads(resp) if resp.strip().startswith("{") else None
            if data is None:
                m = re.search(r'\{.*\}', resp, re.DOTALL)
                if m:
                    data = json.loads(m.group())
        except Exception:
            data = None
        if data and "strategy" in data:
            rnd = entry.get("round_num", 0)
            pid = entry.get("player_id", "")
            strategies_by_round_pid[(rnd, pid)] = data["strategy"]

    # --- MARKET_RESULTの収集 ---
    market_results: dict[int, list[dict]] = defaultdict(list)
    for e in events:
        if e.get("event_type") == "MARKET_RESULT":
            rnd = e.get("round_num", 0)
            market_results[rnd].append(e.get("data", {}))

    # === H1: 約束破り ===
    for rnd, bcs in broadcasts_by_round.items():
        for bc in bcs:
            pid = bc.get("player_id", "")
            # broadcastメッセージの内容はイベントログには含まれない場合がある
            # LLMログから探す
            pass  # H1はLLMログのbroadcast内容が必要（後述のLLMログ版で処理）

    # === H3: 契約違反脱落 ===
    for e in events:
        if e.get("event_type") == "ELIMINATION":
            data = e.get("data", {})
            if data.get("reason") == "contract_violation":
                highlights.append({
                    "type": "H3", "label": "契約違反脱落",
                    "round": e.get("round_num"), "player": data.get("player_id"),
                    "detail": f"{data.get('player_id')}が契約違反で脱落",
                })

    # === H4: 破産 ===
    for e in events:
        if e.get("event_type") == "BANKRUPTCY":
            data = e.get("data", {})
            highlights.append({
                "type": "H4", "label": "破産",
                "round": e.get("round_num"), "player": data.get("player_id"),
                "detail": f"{data.get('player_id')}がEntry Fee不足で破産脱落",
            })

    # === H5: 契約締結 ===
    for e in events:
        if e.get("event_type") == "NEGOTIATION_ACTION":
            data = e.get("data", {})
            if data.get("action") == "contract_propose" and data.get("success"):
                highlights.append({
                    "type": "H5", "label": "契約締結",
                    "round": e.get("round_num"), "player": data.get("player_id"),
                    "detail": f"契約提案: {data.get('contract_id', '?')}",
                })

    # === H8: 上位カード無駄撃ち（rank9-10で単独参加の市場） ===
    for rnd, results in market_results.items():
        for mr in results:
            if mr.get("participants", 0) == 1:
                # 単独参加者のカードを確認
                commits = commits_by_round.get(rnd, {})
                for pid, commit_data in commits.items():
                    card_id = commit_data.get("card", "")
                    if commit_data.get("market_id") == mr.get("market_id"):
                        rank_name = re.sub(r'_\d+$', '', card_id)
                        rank_val = {"ROYAL_FLUSH": 10, "STRAIGHT_FLUSH": 9}.get(rank_name, 0)
                        if rank_val >= 9:
                            highlights.append({
                                "type": "H8", "label": "上位カード無駄撃ち",
                                "round": rnd, "player": pid,
                                "detail": f"{pid}が{rank_name}を単独市場{mr.get('market_id')}に投入",
                            })

    # === H9: 同ランク山分け ===
    for rnd, results in market_results.items():
        for mr in results:
            winners = mr.get("winners", [])
            if len(winners) >= 2:
                highlights.append({
                    "type": "H9", "label": "同ランク山分け",
                    "round": rnd, "player": ", ".join(winners),
                    "detail": f"{mr.get('market_id')}で{len(winners)}人が山分け",
                })

    # === H10: 100万以上の送金 ===
    for e in events:
        if e.get("event_type") == "NEGOTIATION_ACTION":
            data = e.get("data", {})
            if data.get("action") == "transfer" and data.get("success"):
                amount = data.get("amount", 0)
                if amount >= 1_000_000:
                    highlights.append({
                        "type": "H10", "label": "大型送金",
                        "round": e.get("round_num"), "player": data.get("player_id"),
                        "detail": f"{data.get('player_id')}→{data.get('to', '?')}: {amount:,}円",
                    })

    # === H13: AUTO COMMIT ===
    for e in events:
        if e.get("event_type") == "AUTO_COMMIT":
            data = e.get("data", {})
            highlights.append({
                "type": "H13", "label": "AUTO COMMIT",
                "round": e.get("round_num"), "player": data.get("player_id"),
                "detail": data.get("message", "AUTO COMMIT"),
            })

    # === H14: キャリーオーバー ===
    for e in events:
        if e.get("event_type") == "MARKET_RESULT":
            data = e.get("data", {})
            if data.get("carryover", 0) > 0:
                highlights.append({
                    "type": "H14", "label": "キャリーオーバー",
                    "round": e.get("round_num"),
                    "detail": f"{data.get('market_id')}で{data.get('carryover', 0):,}円がキャリーオーバー",
                })

    # === H12: 際どい生還/落選 ===
    for e in events:
        if e.get("event_type") == "SURVIVAL_CHECK":
            data = e.get("data", {})
            cash = data.get("cash", 0)
            survived = data.get("result") == "survived"
            # 生還条件200万の±30万以内
            if abs(cash - 2_000_000) <= 300_000:
                label = "際どい生還" if survived else "際どい落選"
                highlights.append({
                    "type": "H12", "label": label,
                    "round": e.get("round_num"), "player": data.get("player_id"),
                    "detail": f"{data.get('player_id')}: Cash={cash:,}円 ({'生還' if survived else '脱落'})",
                })

    return highlights


def process_trial_dir(input_dir: Path, output_base: Path) -> None:
    """試験ディレクトリ内の全試合を処理する"""
    dir_name = input_dir.name
    output_dir = output_base / dir_name
    output_dir.mkdir(parents=True, exist_ok=True)

    # イベントJSONLとLLMログを探す
    event_files = sorted(input_dir.glob("*events.jsonl"))
    llm_logs_dir = input_dir / "llm_logs"
    llm_files = sorted(llm_logs_dir.glob("*.jsonl")) if llm_logs_dir.exists() else []

    # LLMログをgame_id別にグループ化
    llm_by_game: dict[str, list[dict]] = defaultdict(list)
    for f in llm_files:
        game_key = f.stem.split("_")[0]  # game01
        with open(f, encoding="utf-8") as fh:
            for line in fh:
                try:
                    entry = json.loads(line.strip())
                    llm_by_game[game_key].append(entry)
                except Exception:
                    pass

    all_highlights: dict[str, list[dict]] = {}

    # イベントログがあればそこから処理
    for ef in event_files:
        game_key = ef.stem.replace("_events", "")
        events = []
        with open(ef, encoding="utf-8") as fh:
            for line in fh:
                try:
                    events.append(json.loads(line.strip()))
                except Exception:
                    pass
        llm_entries = llm_by_game.get(game_key, [])
        hl = detect_highlights(events, llm_entries, game_key)
        all_highlights[game_key] = hl

    # イベントログがなくLLMログのみの場合
    if not event_files:
        for game_key, entries in llm_by_game.items():
            hl = detect_highlights([], entries, game_key)
            all_highlights[game_key] = hl

    # 各試合のMDを生成
    for game_key, hl_list in sorted(all_highlights.items()):
        md_path = output_dir / f"{game_key}_highlights.md"
        lines = [f"# {game_key} ハイライト\n"]
        if not hl_list:
            lines.append("_検出されたハイライトはありません。_\n")
        else:
            lines.append(f"検出数: {len(hl_list)}\n")
            for h in sorted(hl_list, key=lambda x: (x.get("round", 0), x.get("type", ""))):
                rnd = h.get("round", "?")
                lines.append(f"- **[R{rnd}] {h['type']}: {h['label']}** — {h['detail']}")
        lines.append("")
        md_path.write_text("\n".join(lines), encoding="utf-8")

    # highlights.jsonl
    jsonl_path = output_dir / "highlights.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for game_key, hl_list in sorted(all_highlights.items()):
            for h in hl_list:
                h["game_id"] = game_key
                f.write(json.dumps(h, ensure_ascii=False) + "\n")

    # index.md
    index_path = output_dir / "index.md"
    lines = [f"# ハイライトインデックス: {dir_name}\n"]
    total = sum(len(hl) for hl in all_highlights.values())
    lines.append(f"- 試合数: {len(all_highlights)}")
    lines.append(f"- 検出ハイライト総数: {total}\n")
    for game_key, hl_list in sorted(all_highlights.items()):
        types = defaultdict(int)
        for h in hl_list:
            types[h["type"]] += 1
        type_summary = ", ".join(f"{k}:{v}" for k, v in sorted(types.items()))
        lines.append(f"- [{game_key}]({game_key}_highlights.md): {len(hl_list)}件 ({type_summary})")
    lines.append("")
    index_path.write_text("\n".join(lines), encoding="utf-8")

    print(f"ハイライト生成完了: {output_dir}")
    print(f"  試合数: {len(all_highlights)}, 検出数: {total}")


def main():
    parser = argparse.ArgumentParser(description="ハイライトログ生成")
    parser.add_argument("--input-dir", type=str, required=True,
                        help="試験ログディレクトリ（例: logs/llm/trial_A_20260809_175006/）")
    parser.add_argument("--output-base", type=str, default="doc/highlights",
                        help="出力ベースディレクトリ（既定: doc/highlights）")
    args = parser.parse_args()

    process_trial_dir(Path(args.input_dir), Path(args.output_base))


if __name__ == "__main__":
    main()
