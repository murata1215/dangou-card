#!/usr/bin/env python3
"""Step 1: 抽出基盤の構築。

`llm_logs/*.jsonl` を本番 `llm.response_parser.parse_response()` で再パースし、
(round_num, player_id, turn) × retry_count 最大で dedup した上で
NEGOTIATION_ACTION イベントと突き合わせる。

中間出力は環境変数 LIE_PROFIT_OUT（既定 /tmp/lie_probe）に書く。
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


def load_events(events_path):
    events = []
    with open(events_path) as f:
        for line in f:
            events.append(json.loads(line))
    return events


def index_events(events):
    idx = defaultdict(list)
    for e in events:
        idx[e["event_type"]].append(e)
    return idx


def load_llm_calls(llm_logs_dir):
    """全プレイヤー分の negotiation phase 行を読み、本番parse_responseで再パース。"""
    records = []
    for lf in sorted(llm_logs_dir.glob("game01_P*_llm_calls.jsonl")):
        pid_from_name = lf.stem.split("_")[1]
        with open(lf) as f:
            for line in f:
                entry = json.loads(line)
                if entry.get("phase") != "negotiation":
                    continue
                resp_text = entry.get("response_text", "") or ""
                strategy, action = None, None
                try:
                    strategy, action = parse_response(resp_text, entry.get("player_id", pid_from_name), "negotiation")
                except Exception:
                    pass
                records.append({
                    "player_id": entry.get("player_id", pid_from_name),
                    "round_num": entry.get("round_num"),
                    "turn": entry.get("turn"),
                    "retry_count": entry.get("retry_count", 0) or 0,
                    "timestamp": entry.get("timestamp"),
                    "top_emotion": entry.get("emotion"),
                    "strategy": strategy,
                    "action": action.model_dump() if action is not None and hasattr(action, "model_dump") else action,
                    "response_text": resp_text,
                    "error": entry.get("error"),
                })
    return records


def dedup_by_retry_max(records):
    """(round_num, player_id, turn) でグループ化し retry_count 最大を採用"""
    groups = defaultdict(list)
    for r in records:
        key = (r["round_num"], r["player_id"], r["turn"])
        groups[key].append(r)
    deduped = {}
    for key, group in groups.items():
        best = max(group, key=lambda r: r["retry_count"])
        deduped[key] = best
    return deduped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", default="logs/llm/trial_C_l12_r12_v08_20260830",
                     help="REPO からの相対パス（既定: trial_C_l12_r12_v08_20260830）")
    args = ap.parse_args()

    trial = REPO / args.trial
    events_path = trial / "game01_events.jsonl"
    llm_logs_dir = trial / "llm_logs"

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    events = load_events(events_path)
    idx = index_events(events)
    print("=== event_type counts ===")
    for k in sorted(idx, key=lambda k: -len(idx[k])):
        print(f"{len(idx[k]):5d} {k}")

    records = load_llm_calls(llm_logs_dir)
    print(f"\n=== negotiation phase raw llm_calls records: {len(records)} ===")
    deduped = dedup_by_retry_max(records)
    print(f"=== after dedup (round,player,turn) unique keys: {len(deduped)} ===")

    # action type breakdown
    from collections import Counter
    action_types = Counter()
    parse_fail = 0
    for r in deduped.values():
        a = r["action"]
        if a is None:
            parse_fail += 1
            continue
        at = a.get("type") if isinstance(a, dict) else None
        action_types[at] += 1
    print("\n=== action type breakdown (deduped) ===")
    for k, v in action_types.most_common():
        print(f"{v:5d} {k}")
    print(f"parse_fail (action=None): {parse_fail}")

    # join with NEGOTIATION_ACTION events
    neg_events = idx.get("NEGOTIATION_ACTION", [])
    print(f"\n=== NEGOTIATION_ACTION events: {len(neg_events)} ===")
    ne_keys = defaultdict(list)
    for e in neg_events:
        d = e["data"]
        key = (e["round_num"], d["player_id"], d["turn"])
        ne_keys[key].append(e)

    matched = 0
    events_only = 0
    llm_only = 0
    for key in set(list(deduped.keys()) + list(ne_keys.keys())):
        in_llm = key in deduped
        in_ev = key in ne_keys
        if in_llm and in_ev:
            matched += 1
        elif in_ev and not in_llm:
            events_only += 1
        elif in_llm and not in_ev:
            llm_only += 1
    print(f"matched={matched} events_only={events_only} llm_only={llm_only}")

    # save intermediate for later steps
    out = {
        "deduped": {f"{k[0]}|{k[1]}|{k[2]}": v for k, v in deduped.items()},
    }
    out_path = OUT_DIR / "deduped_negotiation.json"
    with open(out_path, "w") as f:
        json.dump(out, f, ensure_ascii=False, default=str)
    print(f"\nsaved {out_path}")


if __name__ == "__main__":
    main()
