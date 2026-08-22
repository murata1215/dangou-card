"""
Handover Memory 3バグ修正（WRAPPER_LEAK / WRONG_PAYLOAD / TRUNCATED）の
Before/After再評価スクリプト（2026-08-22）。

既存のR12 trialログ（reflection応答）を読み取り専用で走査し、修正前の
extract_memory()/normalize_memory()（現行コードの1000字ロジック）と、
修正後の extract_memory_with_status()/normalize_memory_with_truncation()
（3000字・境界縮約ロジック）を同じ入力に対して適用し、症状件数と
保存文字数分布をBefore/After比較する。

LLM APIは一切呼ばない。対象ディレクトリへの書き込みは一切行わない
（出力は --out で指定したファイルのみ）。

使い方:
    uv run python scripts/reeval_memory_fix.py \\
        --run-dir logs/llm/trial_C_l12_r12_20260822 \\
        --game-id game01 \\
        --out .devrelay-output/memory_fix_reeval_2026-08-22.md
"""

import argparse
import json
import statistics
import sys
from pathlib import Path
from typing import Any

# ``uv run python scripts/...`` で直接起動しても、repo rootのllm packageを
# 読めるようにする。他のscriptsと同じ明示的なimport path設定である。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from llm.response_parser import (  # noqa: E402
    extract_json, extract_memory_with_status, normalize_memory_with_truncation,
)

OLD_MAX_CHARS = 1000
NEW_MAX_CHARS = 3000


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    """空行を除いてJSONLを読む（読み取り専用）。"""
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _old_extract_memory(text: str) -> str:
    """
    修正前の extract_memory() ロジックの再現（WRAPPER_LEAK/WRONG_PAYLOAD再現用）。

    llm/response_parser.py 修正前の実装をそのまま複製している
    （現行コードは既に修正済みのため、Before側の比較には旧ロジックの
    再現が必要）。
    """
    if not text:
        return ""
    data = extract_json(text)
    if isinstance(data, dict):
        memory = data.get("memory")
        if isinstance(memory, str):
            return memory.strip()
    return text.strip()


def _old_normalize_memory(memory: str, max_chars: int) -> str:
    """修正前の normalize_memory()（ハードカットのみ）の再現。"""
    if not memory:
        return ""
    memory = memory.strip()
    if max_chars > 0 and len(memory) > max_chars:
        memory = memory[:max_chars]
    return memory


def _classify_old(raw_memory: str, saved_memory: str) -> str:
    """
    旧ロジックでの保存結果を症状分類する（WRAPPER_LEAK / WRONG_PAYLOAD / AT_CAP / OK）。

    WRONG_PAYLOAD（memoryキーが無くstrategyキーを持つJSONが丸ごと保存された）は
    fenceの有無に関係なく起こりうる。WRAPPER_LEAK（memoryキーが本来あるはずの
    ラッパー文字列がparse失敗でそのまま漏れた）と見分けるため、"strategy"を
    含みかつ"memory"キーを含まないものを先にWRONG_PAYLOADとして判定する
    （両者ともfenceで始まりうるため、fence prefixだけでは区別できない）。
    """
    stripped = raw_memory.lstrip()
    looks_like_json_wrapper = (
        stripped.startswith("```")
        or stripped.startswith("{")
        or stripped.startswith('{"memory"')
        or stripped.startswith("{'memory'")
    )
    if looks_like_json_wrapper and '"strategy"' in stripped[:200] and '"memory"' not in stripped[:200]:
        return "WRONG_PAYLOAD"
    if stripped.startswith("```") or stripped.startswith('{"memory"') or stripped.startswith("{'memory'"):
        return "WRAPPER_LEAK"
    if len(saved_memory) == OLD_MAX_CHARS:
        return "AT_CAP"
    return "OK"


def _percentile(values: list[int], pct: float) -> float:
    if not values:
        return 0.0
    values = sorted(values)
    idx = min(len(values) - 1, int(round((len(values) - 1) * pct)))
    return float(values[idx])


def _collect_reflection_entries(llm_logs_dir: Path, game_id: str) -> list[dict[str, Any]]:
    """全プレイヤーのllm_callsファイルからreflectionエントリだけを集める。"""
    entries: list[dict[str, Any]] = []
    for path in sorted(llm_logs_dir.glob(f"{game_id}_*_llm_calls.jsonl")):
        player_id = path.stem.replace(f"{game_id}_", "").replace("_llm_calls", "")
        for call in _read_jsonl(path):
            if call.get("phase") != "reflection":
                continue
            if not call.get("api_called", True):
                continue
            entries.append({
                "player_id": player_id,
                "round_num": call.get("round_num"),
                "model_id": call.get("model_id", "unknown"),
                "response_text": call.get("response_text", "") or "",
            })
    return entries


def run(run_dir: Path, game_id: str) -> dict[str, Any]:
    llm_logs_dir = run_dir / "llm_logs"
    entries = _collect_reflection_entries(llm_logs_dir, game_id)

    before_rows = []
    after_rows = []
    after_status_counts: dict[str, int] = {}

    for e in entries:
        text = e["response_text"]

        # --- Before（現行/1000字・ハードカット） ---
        old_raw = _old_extract_memory(text)
        old_saved = _old_normalize_memory(old_raw, OLD_MAX_CHARS)
        old_symptom = _classify_old(old_raw, old_saved)
        before_rows.append({**e, "raw": old_raw, "saved": old_saved, "symptom": old_symptom})

        # --- After（修正/3000字・境界縮約） ---
        new_memory, status = extract_memory_with_status(text)
        after_status_counts[status] = after_status_counts.get(status, 0) + 1
        if new_memory:
            new_saved, truncated = normalize_memory_with_truncation(new_memory, NEW_MAX_CHARS)
        else:
            new_saved, truncated = "", False
        after_rows.append({
            **e, "raw": new_memory, "saved": new_saved, "status": status, "truncated": truncated,
        })

    wrapper_leak_before = sum(1 for r in before_rows if r["symptom"] == "WRAPPER_LEAK")
    wrong_payload_before = sum(1 for r in before_rows if r["symptom"] == "WRONG_PAYLOAD")
    at_cap_before = sum(1 for r in before_rows if r["symptom"] == "AT_CAP")

    wrapper_leak_after = sum(
        1 for r in after_rows
        if r["raw"].lstrip().startswith("```") or r["raw"].lstrip().startswith('{"memory"')
    )
    wrong_payload_after = sum(
        1 for r in after_rows
        if r["raw"].lstrip().startswith("{") and '"strategy"' in r["raw"][:200]
    )
    cap_hit_after = sum(1 for r in after_rows if r["truncated"])
    fallback_after = sum(1 for r in after_rows if r["status"].startswith("rejected"))

    before_lens = [len(r["saved"]) for r in before_rows if r["saved"]]
    after_lens = [len(r["saved"]) for r in after_rows if r["saved"]]

    def _stats(vals: list[int]) -> dict[str, float]:
        if not vals:
            return {"n": 0, "mean": 0.0, "median": 0.0, "p95": 0.0, "max": 0.0}
        return {
            "n": len(vals),
            "mean": round(statistics.mean(vals), 1),
            "median": round(statistics.median(vals), 1),
            "p95": _percentile(vals, 0.95),
            "max": float(max(vals)),
        }

    # 手動突き合わせ候補: rejected（fallback）3件相当 + 縮約発生ケース + WRAPPER_LEAK代表1件
    fallback_cases = [r for r in after_rows if r["status"].startswith("rejected")]
    shrink_cases = [r for r in after_rows if r["truncated"]]
    wrapper_case = next(
        (r for r in before_rows if r["symptom"] == "WRAPPER_LEAK"), None,
    )

    return {
        "n_total": len(entries),
        "before": {
            "wrapper_leak": wrapper_leak_before,
            "wrong_payload": wrong_payload_before,
            "at_cap_1000": at_cap_before,
            "saved_len_stats": _stats(before_lens),
        },
        "after": {
            "wrapper_leak": wrapper_leak_after,
            "wrong_payload": wrong_payload_after,
            "cap_hit_3000": cap_hit_after,
            "fallback_count": fallback_after,
            "status_counts": after_status_counts,
            "saved_len_stats": _stats(after_lens),
        },
        "fallback_cases": fallback_cases,
        "shrink_cases": shrink_cases,
        "wrapper_leak_example": wrapper_case,
    }


def _fmt_stats(s: dict[str, float]) -> str:
    return f"n={int(s['n'])} mean={s['mean']} median={s['median']} p95={s['p95']} max={s['max']}"


def render_markdown(result: dict[str, Any], run_dir: Path, game_id: str) -> str:
    lines: list[str] = []
    lines.append("# Handover Memory 3バグ修正 Before/After再評価\n")
    lines.append(f"- 対象: `{run_dir}` / game_id=`{game_id}`（読み取り専用・LLM API不使用）")
    lines.append(f"- reflection応答総数: {result['n_total']}\n")

    b, a = result["before"], result["after"]
    lines.append("## 集計サマリ\n")
    lines.append("| 項目 | Before（現行/1000） | After（修正/3000） |")
    lines.append("|---|---|---|")
    lines.append(f"| WRAPPER_LEAK | {b['wrapper_leak']} | {a['wrapper_leak']} |")
    lines.append(f"| WRONG_PAYLOAD | {b['wrong_payload']} | {a['wrong_payload']} |")
    lines.append(f"| 上限ヒット | {b['at_cap_1000']}（1000字） | {a['cap_hit_3000']}（3000字） |")
    lines.append(f"| fallback（旧Memory保持） | 0 | {a['fallback_count']} |")
    lines.append(f"| 保存文字数分布 | {_fmt_stats(b['saved_len_stats'])} | {_fmt_stats(a['saved_len_stats'])} |")
    lines.append("")
    lines.append(f"parse_status内訳（After）: `{a['status_counts']}`\n")

    lines.append("## 手動突き合わせケース\n")
    for i, r in enumerate(result["fallback_cases"], start=1):
        lines.append(
            f"{i}. **{r['player_id']} R{r['round_num']}** "
            f"({r['model_id']}) status=`{r['status']}` → fallback（前ラウンドのMemoryを維持）"
        )
    for r in result["shrink_cases"]:
        lines.append(
            f"- **{r['player_id']} R{r['round_num']}** ({r['model_id']}) "
            f"境界縮約発生: raw={len(r['raw'])}字 → saved={len(r['saved'])}字 "
            f"末尾=`...{r['saved'][-30:]}`"
        )
    wl = result["wrapper_leak_example"]
    if wl:
        lines.append(
            f"- WRAPPER_LEAK代表（Before）: **{wl['player_id']} R{wl['round_num']}** "
            f"({wl['model_id']}) 先頭120字=`{wl['raw'][:120]!r}`"
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-dir", type=Path, required=True, help="trialディレクトリ（例: logs/llm/trial_C_l12_r12_20260822）")
    parser.add_argument("--game-id", type=str, default="game01")
    parser.add_argument("--out", type=Path, default=None, help="出力先Markdownパス（省略時は標準出力）")
    args = parser.parse_args()

    result = run(args.run_dir, args.game_id)
    md = render_markdown(result, args.run_dir, args.game_id)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(md, encoding="utf-8")
        print(f"wrote: {args.out}")
    else:
        print(md)


if __name__ == "__main__":
    main()
