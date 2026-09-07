#!/usr/bin/env python3
"""Step 3/4: 不一致件（実測40件）の全件表と利得降順ランキングを構築。

gain_raw   = 獲得賞金 + 宣言市場に来て負けた相手の損失合計（重複計上あり: 同一ラウンドで
             同じ市場を複数人が宣言した場合、被害額が両者に二重計上される）
gain_conservative = 獲得賞金 + 宣言市場に来て負けた相手の損失合計 / 同一(round,declared)を
             宣言した発言者数（重複計上を按分した控えめなスコア）
"""
import argparse
import json
import os
from pathlib import Path
from collections import defaultdict

REPO = Path(__file__).resolve().parents[3]
OUT_DIR = Path(os.environ.get("LIE_PROFIT_OUT", "/tmp/lie_probe"))
ENTRY_FEE = 100000


def load_events(events_path):
    with open(events_path) as f:
        return [json.loads(l) for l in f]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trial", default="logs/llm/trial_C_l12_r12_v08_20260830")
    args = ap.parse_args()

    trial = REPO / args.trial
    events_path = trial / "game01_events.jsonl"
    seat_map_path = trial / "game01_seat_map.json"

    events = load_events(events_path)
    seat_map = json.loads(seat_map_path.read_text())

    reveal_by_round = {}
    market_result_by_round = defaultdict(dict)
    double_up_resolved_by_round_player = {}
    double_up_chosen_by_round_player = {}

    for e in events:
        et = e["event_type"]
        r = e["round_num"]
        d = e["data"]
        if et == "REVEAL":
            reveal_by_round[r] = {c["player_id"]: c for c in d["commits"]}
        elif et == "MARKET_RESULT":
            market_result_by_round[r][d["market_id"]] = d
        elif et == "DOUBLE_UP_RESOLVED":
            double_up_resolved_by_round_player[(r, d["player_id"])] = d
        elif et == "DOUBLE_UP_CHOSEN":
            double_up_chosen_by_round_player[(r, d["player_id"])] = d

    mismatches = json.load(open(OUT_DIR / "mismatches_variantB.json"))

    # load deduped negotiation records for emotion lookup
    dedup_raw = json.load(open(OUT_DIR / "deduped_negotiation.json"))["deduped"]
    emotions_by_round_player = defaultdict(list)
    for k, rec in dedup_raw.items():
        r, p, t = k.split("|")
        r = int(r); t = int(t)
        strategy = rec.get("strategy") or {}
        emo = strategy.get("emotion") or rec.get("top_emotion")
        emotions_by_round_player[(r, p)].append((t, emo))

    rows = []
    for m in mismatches:
        r = m["round"]; p = m["player"]
        declared = m["declared"]; actual = m["actual"]
        reveal = reveal_by_round.get(r, {})
        my_commit = reveal.get(p, {})
        my_card = my_commit.get("card")
        my_rank = my_commit.get("rank")
        mr_actual = market_result_by_round[r].get(actual, {})
        won = p in mr_actual.get("winners", [])
        prize = mr_actual.get("prize_per_winner", 0) if won else 0

        # who actually came to the declared market
        arrivals = [pid for pid, c in reveal.items() if c["market_id"] == declared]
        mr_declared = market_result_by_round[r].get(declared, {})
        winners_declared = set(mr_declared.get("winners", []))
        arrival_pl = []
        total_loss = 0
        for a in arrivals:
            if a == p:
                continue
            a_won = a in winners_declared
            loss = 0
            if not a_won:
                loss = ENTRY_FEE
                du = double_up_resolved_by_round_player.get((r, a))
                if du and du.get("result") == "forfeit":
                    loss += du.get("deposit", 0)
            arrival_pl.append({"player": a, "won": a_won, "loss": loss})
            total_loss += loss

        gain_raw = prize + total_loss  # 重複計上ありの生スコア

        emos = sorted(emotions_by_round_player.get((r, p), []))

        rows.append({
            "round": r, "player": p, "model": seat_map.get(p, "?"),
            "declared": declared, "actual": actual,
            "action_type": m["action_type"], "to": m.get("to"),
            "message": m["message"],
            "my_card": my_card, "my_rank": my_rank,
            "won": won, "prize": prize,
            "arrivals_to_declared": arrival_pl,
            "total_loss_at_declared": total_loss,
            "gain_raw": gain_raw,
            "emotions": emos,
        })

    # gain_conservative: 同一(round, declared)を宣言した発言者数で被害額を按分
    groups = defaultdict(list)
    for row in rows:
        groups[(row["round"], row["declared"])].append(row)
    for row in rows:
        n = len(groups[(row["round"], row["declared"])])
        row["gain_conservative"] = row["prize"] + (row["total_loss_at_declared"] / n if n else 0)

    rows.sort(key=lambda x: -x["gain_raw"])
    out_path = OUT_DIR / "rows_full.json"
    with open(out_path, "w") as f:
        json.dump(rows, f, ensure_ascii=False, indent=2, default=str)

    print(f"total rows: {len(rows)}")
    print("\n=== top 15 by gain_raw ===")
    for row in rows[:15]:
        weak = row["my_rank"] in ("HIGH_CARD", "ONE_PAIR", "TWO_PAIR") and row["won"]
        mark = " ★" if weak else ""
        print(f"R{row['round']:2d} {row['player']}({row['model']}) declared={row['declared']} actual={row['actual']} "
              f"my_card={row['my_rank']} won={row['won']} prize={row['prize']} "
              f"loss_at_declared={row['total_loss_at_declared']} gain_raw={row['gain_raw']} "
              f"gain_conservative={row['gain_conservative']:.0f}{mark}")
    print(f"\nsaved {out_path}")


if __name__ == "__main__":
    main()
