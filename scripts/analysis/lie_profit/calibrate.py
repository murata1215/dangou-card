#!/usr/bin/env python3
"""Step 2 (variant A): 定義キャリブレーション。

各(round,player)についてターンを走査し、「市場言及(M0[1-3])を含む最後のメッセージ」を
その回の宣言市場とみなして COMMIT の実際の市場と突き合わせる。
114/39 の再現を試みるが、無理に数字を合わせにいかない（実測をそのまま報告する）。
"""
import argparse
import json
import os
import re
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parents[3]
OUT_DIR = Path(os.environ.get("LIE_PROFIT_OUT", "/tmp/lie_probe"))

LOOSE_RE = re.compile(r"M0[1-3]")


def load_events(events_path):
    events = []
    with open(events_path) as f:
        for line in f:
            events.append(json.loads(line))
    return events


def load_deduped():
    with open(OUT_DIR / "deduped_negotiation.json") as f:
        data = json.load(f)
    out = {}
    for k, v in data["deduped"].items():
        r, p, t = k.split("|")
        out[(int(r), p, int(t))] = v
    return out


def last_market_loose(text):
    matches = LOOSE_RE.findall(text or "")
    return matches[-1] if matches else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", default="logs/llm/trial_C_l12_r12_v08_20260830")
    args = ap.parse_args()

    trial = REPO / args.trial
    events_path = trial / "game01_events.jsonl"

    events = load_events(events_path)
    commits = [e for e in events if e["event_type"] == "COMMIT"]
    commit_map = {}
    for e in commits:
        d = e["data"]
        commit_map[(e["round_num"], d["player_id"])] = d["market_id"]

    deduped = load_deduped()

    # group by (round, player), collect turns in order, find last message with a market ref
    by_rp = defaultdict(list)
    for (r, p, t), rec in deduped.items():
        by_rp[(r, p)].append((t, rec))

    for variant_name, include_anon in [("broadcast+dm", False), ("broadcast+dm+anon", True)]:
        n_considered = 0
        n_mismatch = 0
        mismatches = []
        for (r, p), turns in by_rp.items():
            turns.sort(key=lambda x: x[0])
            last_market = None
            for t, rec in turns:
                action = rec.get("action")
                if not action:
                    continue
                at = action.get("type")
                if at not in ("broadcast", "dm") and not (include_anon and at == "anonymous_broadcast"):
                    continue
                msg = action.get("message", "")
                m = last_market_loose(msg)
                if m:
                    last_market = (t, at, m, msg, action.get("to"))
            if last_market is None:
                continue
            n_considered += 1
            actual = commit_map.get((r, p))
            if actual is None:
                continue
            if last_market[2] != actual:
                n_mismatch += 1
                mismatches.append((r, p, last_market, actual))
        print(f"[{variant_name}] considered={n_considered} mismatch={n_mismatch} rate={n_mismatch/n_considered*100:.2f}%")

    # print the broadcast+dm (no anon) mismatch list for inspection
    print("\n--- detail for broadcast+dm (no anon) ---")
    n_considered = 0
    n_mismatch = 0
    mismatches = []
    for (r, p), turns in by_rp.items():
        turns.sort(key=lambda x: x[0])
        last_market = None
        for t, rec in turns:
            action = rec.get("action")
            if not action:
                continue
            at = action.get("type")
            if at not in ("broadcast", "dm"):
                continue
            msg = action.get("message", "")
            m = last_market_loose(msg)
            if m:
                last_market = (t, at, m, msg, action.get("to"))
        if last_market is None:
            continue
        n_considered += 1
        actual = commit_map.get((r, p))
        if actual is None:
            continue
        if last_market[2] != actual:
            n_mismatch += 1
            mismatches.append((r, p, last_market, actual))
    print(f"considered={n_considered} mismatch={n_mismatch}")
    for r, p, lm, actual in sorted(mismatches):
        print(f"R{r} {p}: declared={lm[2]} actual={actual} turn={lm[0]} type={lm[1]}")

    out_path = OUT_DIR / "mismatches_broadcast_dm.json"
    with open(out_path, "w") as f:
        json.dump([
            {"round": r, "player": p, "turn": lm[0], "action_type": lm[1], "declared": lm[2], "to": lm[4], "message": lm[3], "actual": actual}
            for r, p, lm, actual in mismatches
        ], f, ensure_ascii=False, indent=2)
    print(f"\nsaved {out_path}")


if __name__ == "__main__":
    main()
