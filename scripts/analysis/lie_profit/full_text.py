#!/usr/bin/env python3
"""Step 5: 全文引用ブロック抽出。

対象は既定で R12 P10/P11 の全発言・全DM(送受信) と、R11 P07 が受信した全DM +
P10/P04 の R11 全発言（前タスクの調査対象と同一）。
"""
import argparse
import json
import os
from pathlib import Path

OUT_DIR = Path(os.environ.get("LIE_PROFIT_OUT", "/tmp/lie_probe"))


def load_deduped():
    with open(OUT_DIR / "deduped_negotiation.json") as f:
        data = json.load(f)
    out = {}
    for k, v in data["deduped"].items():
        r, p, t = k.split("|")
        out[(int(r), p, int(t))] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--round-a", type=int, default=12)
    ap.add_argument("--players-a", nargs="+", default=["P10", "P11"])
    ap.add_argument("--round-b", type=int, default=11)
    ap.add_argument("--dm-target-b", default="P07")
    ap.add_argument("--players-b", nargs="+", default=["P10", "P04"])
    args = ap.parse_args()

    deduped = load_deduped()

    def get_round_player_all_turns(r, p):
        items = [(t, rec) for (rr, pp, t), rec in deduped.items() if rr == r and pp == p]
        items.sort(key=lambda x: x[0])
        return items

    # 1. 指定ラウンド・指定プレイヤーの 全発言・全DM(送受信)
    for pid in args.players_a:
        print("=" * 20, f"R{args.round_a} {pid} 全ターン", "=" * 20)
        for t, rec in get_round_player_all_turns(args.round_a, pid):
            a = rec.get("action") or {}
            print(f"--- turn {t} type={a.get('type')} to={a.get('to')} emotion={((rec.get('strategy') or {}).get('emotion'))}")
            if a.get("type") in ("broadcast", "dm", "anonymous_broadcast"):
                print(a.get("message", ""))
        print()

    print("=" * 20, f"R{args.round_a} 中 {'/'.join(args.players_a)} 宛の受信DM(他席から)", "=" * 20)
    for (r, p, t), rec in sorted(deduped.items()):
        if r != args.round_a:
            continue
        a = rec.get("action") or {}
        if a.get("type") == "dm" and a.get("to") in args.players_a:
            print(f"--- from={p} turn={t} to={a.get('to')}")
            print(a.get("message", ""))

    # 2. 別ラウンドの DM受信 + 指定プレイヤー全発言
    print("\n\n" + "=" * 20, f"R{args.round_b} 中 {args.dm_target_b} 宛の受信DM(他席から)", "=" * 20)
    for (r, p, t), rec in sorted(deduped.items()):
        if r != args.round_b:
            continue
        a = rec.get("action") or {}
        if a.get("type") == "dm" and a.get("to") == args.dm_target_b:
            print(f"--- from={p} turn={t}")
            print(a.get("message", ""))

    for pid in args.players_b:
        print("\n" + "=" * 20, f"R{args.round_b} {pid} 全発言(broadcast+dm送信)", "=" * 20)
        for t, rec in get_round_player_all_turns(args.round_b, pid):
            a = rec.get("action") or {}
            if a.get("type") in ("broadcast", "dm", "anonymous_broadcast"):
                print(f"--- turn {t} type={a.get('type')} to={a.get('to')}")
                print(a.get("message", ""))


if __name__ == "__main__":
    main()
