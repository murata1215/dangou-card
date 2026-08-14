#!/usr/bin/env python3
"""
各キャラクター（AI）の時系列「思考・会話」を脚色なしで1つのMarkdownに書き出す。

実況（ずんだもん/めたん）の演出は一切挟まず、LLMログの生データのみを使う:
- 考え = response_text 内 strategy.reason（＋current_goal / target_market / card_plan / emotion）
- 会話 = action の dm / broadcast の message（全文・truncateなし）
- 行動 = market_commit / contract_propose / contract_sign / repay / transfer / choose_loan / pass

使い方:
    uv run python scripts/export_character_timeline.py
    uv run python scripts/export_character_timeline.py --trial trial_C_20260810_053055 --game game01
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# 既存パーサを再利用（JSON抽出はコードブロック/生JSON両対応済）
from viewer.log_parser import (  # noqa: E402
    _extract_action,
    _extract_strategy,
    _load_seat_map,
)


# --- ヘルパ -----------------------------------------------------------------

OB_TYPE_JA = {
    "type_a_payment": "型A(金銭)",
    "type_b_market": "型B(市場参加)",
    "type_b_no_market": "型B(市場不参加)",
    "type_b_card": "型B(カード使用)",
}


def _read_entries(path: Path) -> list[dict[str, Any]]:
    """JSONLを読み、timestamp昇順で返す。"""
    import json

    entries: list[dict[str, Any]] = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    entries.sort(key=lambda e: e.get("timestamp", ""))
    return entries


def _fmt_time(ts: str) -> str:
    if not ts:
        return "--:--:--"
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        return dt.strftime("%H:%M:%S")
    except ValueError:
        return ts


def _yen(v: Any) -> str:
    try:
        return "¥" + f"{int(v):,}"
    except (TypeError, ValueError):
        return str(v)


def _summarize_terms(terms: list[dict[str, Any]]) -> str:
    """契約義務リストを人間可読な締結条件に要約する。"""
    parts: list[str] = []
    for t in terms or []:
        if not isinstance(t, dict):
            continue
        ob = t.get("obligor", "?")
        cp = t.get("counterparty", "?")
        ty = t.get("ob_type", "?")
        d = t.get("details", {}) or {}
        rn = t.get("round_num")
        r = f" R{rn}履行" if rn is not None else ""
        if ty == "type_a_payment":
            parts.append(f"{ob}→{cp} {_yen(d.get('amount'))}支払{r}")
        elif ty == "type_b_market":
            parts.append(f"{ob}は{d.get('market_id', '?')}に参加{r}")
        elif ty == "type_b_no_market":
            parts.append(f"{ob}は{d.get('market_id', '?')}に不参加{r}")
        elif ty == "type_b_card":
            parts.append(f"{ob}は{d.get('card', d.get('card_rank', '?'))}を使用{r}")
        else:
            parts.append(f"{OB_TYPE_JA.get(ty, ty)}({ob}→{cp}){r}")
    return " / ".join(parts) if parts else "（義務なし）"


def _name(seat_map: dict[str, str], pid: str) -> str:
    return seat_map.get(pid, pid)


# --- 本体 -------------------------------------------------------------------

def _render_entry(e: dict[str, Any], seat_map: dict[str, str]) -> list[str]:
    """1レコードをmd行のリストに変換する。"""
    resp = e.get("response_text", "") or ""
    strategy = _extract_strategy(resp)
    action = _extract_action(resp)

    rnd = e.get("round_num", 0)
    phase = e.get("phase", "")
    turn = e.get("turn")
    tstr = _fmt_time(e.get("timestamp", ""))
    turn_s = f" · T{turn}" if turn is not None else ""

    lines: list[str] = [f"#### R{rnd} · {phase}{turn_s} · {tstr}"]

    if e.get("error"):
        lines.append(f"- ⚠️ エラー: {e.get('error_type') or ''} {e.get('error')}")

    if isinstance(strategy, dict):
        reason = strategy.get("reason")
        if reason:
            lines.append(f"- 💭 考え: {reason}")
        goal = strategy.get("current_goal")
        if goal:
            lines.append(f"- 🎯 目標: {goal}")
        tm = strategy.get("target_market")
        cp = strategy.get("card_plan")
        if tm or cp:
            lines.append(f"- 🗺 市場計画: {tm or '—'} / {cp or '—'}")
        emo = strategy.get("emotion")
        if emo:
            lines.append(f"- 🎭 感情: {emo}")

    if isinstance(action, dict):
        at = action.get("type", "?")
        if at == "broadcast":
            lines.append(f"- 🗣 全体発言: 「{action.get('message', '')}」")
        elif at == "dm":
            to = action.get("to", "?")
            lines.append(f"- ✉️ DM→{to}({_name(seat_map, to)}): 「{action.get('message', '')}」")
        elif at == "market_commit":
            lines.append(f"- ⚙️ コミット: {action.get('market_id', '?')} / {action.get('card', '?')}")
        elif at == "contract_propose":
            wth = ", ".join(action.get("with", []) or [])
            lines.append(f"- 📜 契約提案→[{wth}]: {_summarize_terms(action.get('terms', []))}")
        elif at == "contract_sign":
            lines.append(f"- ✍️ 契約署名: {action.get('contract_id', '?')}")
        elif at == "repay":
            lines.append(f"- 💵 返済: {_yen(action.get('amount'))}")
        elif at == "transfer":
            lines.append(f"- 💸 送金→{action.get('to', '?')}: {_yen(action.get('amount'))}")
        elif at == "choose_loan":
            lines.append(f"- 🏦 借入額決定: {_yen(action.get('amount'))}")
        elif at == "pass":
            lines.append("- ⏭ パス")
        else:
            lines.append(f"- ⚙️ {at}")

    return lines


def build_markdown(trial_dir: Path, game_id: str) -> str:
    seat_map = _load_seat_map(trial_dir, game_id)
    llm_logs_dir = trial_dir / "llm_logs"
    files = sorted(llm_logs_dir.glob(f"{game_id}_P*_llm_calls.jsonl"))

    out: list[str] = []
    out.append(f"# {game_id} 各キャラクター時系列ログ（思考・会話 / 脚色なし）")
    out.append("")
    out.append(f"- 対象: `{trial_dir.name}/{game_id}`")
    out.append(f"- 生成日時: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    out.append("- 出典: LLM生ログ（`llm_logs/*_llm_calls.jsonl`）。実況の演出は含みません。")
    out.append("")
    out.append("**凡例**: 💭考え(reason) / 🎯目標 / 🗺市場計画 / 🎭感情 / "
               "🗣全体発言 / ✉️DM / ⚙️コミット / 📜契約提案 / ✍️署名 / "
               "💵返済 / 💸送金 / 🏦借入 / ⏭パス")
    out.append("")

    # キャラ一覧
    out.append("## 登場キャラクター")
    out.append("")
    out.append("| 座席 | モデル |")
    out.append("|---|---|")
    for f in files:
        pid = f.stem.split("_")[1]
        out.append(f"| {pid} | {_name(seat_map, pid)} |")
    out.append("")

    for f in files:
        pid = f.stem.split("_")[1]
        entries = _read_entries(f)
        out.append("---")
        out.append("")
        out.append(f"## {pid} — {_name(seat_map, pid)}")
        out.append("")
        out.append(f"*記録数: {len(entries)}件*")
        out.append("")
        for e in entries:
            out.extend(_render_entry(e, seat_map))
            out.append("")

    return "\n".join(out) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="各キャラの時系列思考・会話をmd出力")
    ap.add_argument("--logs-root", default="logs/llm")
    ap.add_argument("--trial", default="trial_C_20260810_053055")
    ap.add_argument("--game", default="game01")
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    trial_dir = REPO_ROOT / args.logs_root / args.trial
    if not trial_dir.exists():
        sys.exit(f"trial dir not found: {trial_dir}")

    md = build_markdown(trial_dir, args.game)

    out_path = (
        Path(args.out)
        if args.out
        else REPO_ROOT / ".devrelay-output" / f"{args.game}_characters_timeline.md"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(md, encoding="utf-8")
    print(f"wrote {out_path} ({len(md):,} bytes, {md.count(chr(10)):,} lines)")


if __name__ == "__main__":
    main()
