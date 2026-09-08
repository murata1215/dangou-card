#!/usr/bin/env python3
"""Step 6: 作戦メモ（strategy）全文抽出。

`deduped_negotiation.json`（negotiation フェイズ、inventory.py が生成済み）の
strategy に加え、`llm_logs/*.jsonl` の commit フェイズ（最終市場決定の理由）を
本番 `llm.response_parser.parse_response()` で再パースして (round, player) 単位で
dedup し、対象の (round, player) ごとに全ターンの
`巡 | action種別 | 宛先 | strategy(全フィールド原文JSON) | 対外発言の要旨` を出力する。

strategy は要約・改変せず `json.dumps(..., ensure_ascii=False)` の原文をそのまま出す。
既存の `inventory.py`/`calibrate.py`/`calibrate2.py`/`build_table.py`/`full_text.py` は無変更。
"""
import argparse
import json
import os
import sys
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from llm.response_parser import parse_response  # 本番関数をそのまま使う

OUT_DIR = Path(os.environ.get("LIE_PROFIT_OUT", "/tmp/lie_probe"))


def load_deduped_negotiation():
    """inventory.py が保存した negotiation フェイズの dedup 済みデータを読む。"""
    with open(OUT_DIR / "deduped_negotiation.json") as f:
        data = json.load(f)
    out = {}
    for k, v in data["deduped"].items():
        r, p, t = k.split("|")
        out[(int(r), p, int(t))] = v
    return out


def load_commit_phase(llm_logs_dir):
    """commit フェイズの行を (round_num, player_id) × retry_count 最大で dedup し再パース。"""
    records = []
    for lf in sorted(llm_logs_dir.glob("game01_P*_llm_calls.jsonl")):
        pid_from_name = lf.stem.split("_")[1]
        with open(lf) as f:
            for line in f:
                entry = json.loads(line)
                if entry.get("phase") != "commit":
                    continue
                resp_text = entry.get("response_text", "") or ""
                strategy, action = None, None
                try:
                    strategy, action = parse_response(resp_text, entry.get("player_id", pid_from_name), "commit")
                except Exception:
                    pass
                records.append({
                    "player_id": entry.get("player_id", pid_from_name),
                    "round_num": entry.get("round_num"),
                    "retry_count": entry.get("retry_count", 0) or 0,
                    "strategy": strategy,
                    "action": action.model_dump() if action is not None and hasattr(action, "model_dump") else action,
                })
    groups = defaultdict(list)
    for r in records:
        groups[(r["round_num"], r["player_id"])].append(r)
    deduped = {}
    for key, group in groups.items():
        deduped[key] = max(group, key=lambda r: r["retry_count"])
    return deduped


def load_events(events_path):
    events = []
    with open(events_path) as f:
        for line in f:
            events.append(json.loads(line))
    return events


def summarize(action):
    """対外発言の要旨（20字以内、原文の先頭を切り詰めるのみで意味の改変はしない）"""
    if not action:
        return "(該当なし)"
    at = action.get("type")
    if at in ("broadcast", "dm", "anonymous_broadcast"):
        msg = (action.get("message") or "").replace("\n", " ")
        return msg[:20] + ("…" if len(msg) > 20 else "")
    if at == "market_commit":
        return f"{action.get('market_id')}に{action.get('card_rank')}でコミット"
    return f"({at})"


def dump_target(round_num, player_id, neg, commit_map):
    print("=" * 20, f"R{round_num} {player_id} 全ターン（negotiation → commit）", "=" * 20)
    turns = [(t, rec) for (r, p, t), rec in neg.items() if r == round_num and p == player_id]
    turns.sort(key=lambda x: x[0])
    for t, rec in turns:
        a = rec.get("action") or {}
        strategy = rec.get("strategy")
        at = a.get("type")
        to = a.get("to") if at == "dm" else ("全体" if at in ("broadcast", "anonymous_broadcast") else "-")
        strategy_json = json.dumps(strategy, ensure_ascii=False) if strategy is not None else "null"
        print(f"| 巡{t} | {at} | {to} | {strategy_json} | {summarize(a)} |")
    commit_rec = commit_map.get((round_num, player_id))
    if commit_rec:
        a = commit_rec.get("action") or {}
        strategy = commit_rec.get("strategy")
        strategy_json = json.dumps(strategy, ensure_ascii=False) if strategy is not None else "null"
        print(f"| 巡=commit | market_commit | - | {strategy_json} | {summarize(a)} |")
    else:
        print("| 巡=commit | (commitフェイズのlog無し) | - | null | - |")
    print()


def dump_commit_ledger(events, round_num):
    print("=" * 20, f"R{round_num} 実測台帳（COMMIT / MARKET_RESULT / BANKRUPTCY）", "=" * 20)
    for e in events:
        if e.get("round_num") != round_num:
            continue
        if e["event_type"] in ("COMMIT", "MARKET_RESULT", "BANKRUPTCY"):
            print(json.dumps(e, ensure_ascii=False))
    print()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", default="logs/llm/trial_C_l12_r12_v08_20260830")
    ap.add_argument(
        "--targets", nargs="+",
        default=["12:P11", "12:P10", "11:P10", "11:P04", "12:P03", "4:P05", "11:P07", "12:P07"],
        help="round:player の形式",
    )
    ap.add_argument("--commit-ledger", nargs="*", type=int, default=[11, 12],
                     help="COMMIT/MARKET_RESULT/BANKRUPTCYの実測を出すラウンド")
    args = ap.parse_args()

    trial = REPO / args.trial
    events_path = trial / "game01_events.jsonl"
    llm_logs_dir = trial / "llm_logs"

    neg = load_deduped_negotiation()
    commit_map = load_commit_phase(llm_logs_dir)
    events = load_events(events_path)

    for r in args.commit_ledger:
        dump_commit_ledger(events, r)

    for spec in args.targets:
        r_str, p = spec.split(":")
        dump_target(int(r_str), p, neg, commit_map)


if __name__ == "__main__":
    main()
