"""
tools/replay_llm_call.py — 過去のLLM呼び出しログを指定行だけ再送する（DevRelay 504検証用）

サイクル10.14: サイクル10.12でP11(DR_OPUS48)の504はelapsed≈108秒の打ち切りと判定し、
サイクル10.13でDR席のtimeout_secondsを120→180秒にした。実際に504になったコールを
同じsystem/user promptで再送し、180秒で通るか・何秒かかるかを実測する。

既存の adapter / provider（llm/adapters.py の create_adapter, llm/providers/devrelay_http.py）
をそのまま使う。独自のHTTP実装はしない。tools/devrelay_smoke.py と同じ骨格（sys.path挿入→
load_dotenv→get_model→create_adapter→bind_seat→complete）。

ログの1メッセージ形式（llm/llm_agent.py の呼び出し方と同一）:
    adapter.complete(system=rec["system_prompt"],
                      messages=[{"role": "user", "content": rec["user_prompt"]}])
匿名化行（ANONYMIZATION_LINE）はHttpAgentProvider側で送信直前に付与されるため、
ログのsystem_promptをそのまま渡せば本戦と同一の送信内容になる。

安全弁: --model で指定したModelInfo.model_id が、再送対象行の記録済み model_id と
一致しない場合は送信前に停止する（DR_OPUS48以外に誤って送らないため）。

使い方:
    uv run python tools/replay_llm_call.py --dry-run --lines 106,118,120,141,108
    uv run python tools/replay_llm_call.py --lines 106,118,120,141,108 --model DR_OPUS48
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

# tools/devrelay_smoke.py と同じパターン: `python tools/replay_llm_call.py` 直接実行時に
# リポジトリルートをsys.pathへ通す（`llm` パッケージをimportできるようにする）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from llm.adapters import AdapterError, create_adapter  # noqa: E402
from llm.models import get_model  # noqa: E402
from llm.response_parser import extract_json  # noqa: E402

JST = timezone(timedelta(hours=9))
DEFAULT_MODEL_KEY = "DR_OPUS48"
DEFAULT_SEAT = "P11"
DEFAULT_GAME_LOG_DIR = "logs/llm/trial_C_l12r12_dr_1201/llm_logs"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="過去のLLM呼び出しログの指定行を再送し、結果（所要秒・トークン・成否）を記録する"
    )
    parser.add_argument("--seat", default=DEFAULT_SEAT, help=f"対象プレイヤーID（既定: {DEFAULT_SEAT}）")
    parser.add_argument(
        "--lines", required=True,
        help="再送するログ行番号（1始まり）のカンマ区切り。例: 106,118,120,141,108",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL_KEY, help=f"DevRelayモデルのレジストリキー（既定: {DEFAULT_MODEL_KEY}）")
    parser.add_argument("--repeat", type=int, default=1, help="各行の送信回数（既定: 1）")
    parser.add_argument(
        "--log-path", default=None,
        help=f"再送元のllm_calls.jsonlパス（既定: {DEFAULT_GAME_LOG_DIR}/game01_{{seat}}_llm_calls.jsonl）",
    )
    parser.add_argument(
        "--out-dir", default=None,
        help="結果jsonlの出力先ディレクトリ（既定: logs/replay/<JST日時>/）",
    )
    parser.add_argument("--dry-run", action="store_true", help="送信せず対象行の内容を表示するだけ")
    return parser.parse_args()


def _resolve_log_path(args: argparse.Namespace) -> Path:
    if args.log_path:
        return Path(args.log_path)
    repo_root = Path(__file__).resolve().parent.parent
    return repo_root / DEFAULT_GAME_LOG_DIR / f"game01_{args.seat}_llm_calls.jsonl"


def _load_lines(log_path: Path, line_numbers: list[int]) -> dict[int, dict]:
    """1始まりの行番号 → パース済みレコード の辞書を返す。"""
    records: dict[int, dict] = {}
    wanted = set(line_numbers)
    with open(log_path, encoding="utf-8") as f:
        for n, raw in enumerate(f, 1):
            if n in wanted:
                records[n] = json.loads(raw)
    missing = wanted - records.keys()
    if missing:
        raise SystemExit(f"[FAIL] ログに存在しない行番号: {sorted(missing)}（ファイル: {log_path}）")
    return records


def _prev_result_label(rec: dict) -> str:
    if rec.get("error"):
        err = rec["error"] or ""
        return "504" if "504" in err else f"ERR({rec.get('error_type')})"
    return "ok"


def _print_dry_run_row(line_no: int, rec: dict) -> None:
    prev = _prev_result_label(rec)
    sys_len = len(rec.get("system_prompt") or "")
    usr_len = len(rec.get("user_prompt") or "")
    prev_elapsed = rec.get("elapsed_ms", 0) / 1000
    print(f"--- line {line_no} ---")
    print(f"  player_id     : {rec.get('player_id')}")
    print(f"  round/phase   : R{rec.get('round_num')} / {rec.get('phase')} (turn={rec.get('turn')})")
    print(f"  model_id(記録) : {rec.get('model_id')}")
    print(f"  前回結果       : {prev} (elapsed={prev_elapsed:.1f}s, output_tokens={rec.get('output_tokens')})")
    print(f"  入力字数       : system={sys_len} user={usr_len}")
    print(f"  user_prompt末尾300字:")
    print("    " + (rec.get("user_prompt") or "")[-300:].replace("\n", "\n    "))
    print()


def _run_dry_run(records: dict[int, dict], line_numbers: list[int]) -> int:
    print(f"[DRY-RUN] 対象 {len(line_numbers)} 件（送信しません）")
    for n in line_numbers:
        _print_dry_run_row(n, records[n])
    return 0


def _send_one(adapter, rec: dict) -> dict:
    """1件送信し、結果レコード（result/elapsed_s/usage等）を返す。例外は握って結果化する。"""
    system = rec["system_prompt"]
    user_prompt = rec["user_prompt"]
    t0 = time.perf_counter()
    try:
        text, usage = adapter.complete(
            system=system,
            messages=[{"role": "user", "content": user_prompt}],
        )
    except AdapterError as e:
        elapsed_s = time.perf_counter() - t0
        return {
            "result": "error",
            "error": str(e),
            "elapsed_s": round(elapsed_s, 1),
        }
    elapsed_s = time.perf_counter() - t0
    dr = (usage.get("usage_raw") or {}).get("devrelay", {})
    dr_usage = dr.get("usage") or {}
    parsed = extract_json(text) or {}
    action = parsed.get("action") if isinstance(parsed, dict) else None
    return {
        "result": "ok",
        "elapsed_s": round(elapsed_s, 1),
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "latencyMs": dr.get("latencyMs"),
        "agentDurationMs": dr.get("agentDurationMs"),
        "sessionId": dr.get("sessionId"),
        "action": action,
        "response_head_300": text[:300],
        "response_text": text,
    }


def _run_send(
    records: dict[int, dict],
    line_numbers: list[int],
    model_key: str,
    seat: str,
    repeat: int,
    out_path: Path,
) -> list[dict]:
    model_info = get_model(model_key)
    if model_info.adapter_type != "devrelay_http":
        raise SystemExit(f"[FAIL] --model {model_key!r} は DevRelay 席ではありません (adapter_type={model_info.adapter_type!r})")

    results = []
    out_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"[SEND] {len(line_numbers)}行 x repeat={repeat} を model={model_key} seat={seat} で順番に送信します")
    print(f"[SEND] 出力先: {out_path}")

    with open(out_path, "a", encoding="utf-8") as out_f:
        for n in line_numbers:
            rec = records[n]
            if rec.get("player_id") != seat:
                raise SystemExit(
                    f"[FAIL] line {n} の player_id={rec.get('player_id')!r} が --seat {seat!r} と不一致"
                )
            if rec.get("model_id") != model_info.model_id:
                raise SystemExit(
                    f"[FAIL] line {n} の記録済み model_id={rec.get('model_id')!r} が "
                    f"--model {model_key!r}（model_id={model_info.model_id!r}）と不一致。"
                    "安全のため送信を中止します。"
                )

            prev = _prev_result_label(rec)
            for rep in range(repeat):
                # adapterは1回きりの単発コールごとに作り直す（bind_seat状態を毎回明示するため）。
                adapter = create_adapter(model_info, max_retries=0)
                bind_seat = getattr(adapter, "bind_seat", None)
                if callable(bind_seat):
                    bind_seat(seat)

                send_result = _send_one(adapter, rec)
                record = {
                    "timestamp": datetime.now(JST).isoformat(),
                    "seat": seat,
                    "line": n,
                    "repeat_idx": rep,
                    "round_num": rec.get("round_num"),
                    "phase": rec.get("phase"),
                    "turn": rec.get("turn"),
                    "model_key": model_key,
                    "model_id": model_info.model_id,
                    "prev_result": prev,
                    "prev_elapsed_s": round(rec.get("elapsed_ms", 0) / 1000, 1),
                    "prev_output_tokens": rec.get("output_tokens"),
                    **send_result,
                }
                results.append(record)
                out_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                out_f.flush()

                if send_result["result"] == "ok":
                    print(
                        f"[OK]  line={n} R{rec.get('round_num')}/{rec.get('phase')} "
                        f"prev={prev} -> ok elapsed={send_result['elapsed_s']}s "
                        f"out_tok={send_result.get('output_tokens')} "
                        f"resp_head={send_result['response_head_300'][:80]!r}"
                    )
                else:
                    print(
                        f"[ERR] line={n} R{rec.get('round_num')}/{rec.get('phase')} "
                        f"prev={prev} -> error elapsed={send_result['elapsed_s']}s "
                        f"error={send_result['error'][:100]!r}"
                    )
    return results


def _print_summary_table(results: list[dict]) -> None:
    print()
    print("=== 結果表 ===")
    print("| 行 | R | phase | 前回 | 今回 | 所要秒 | out_tok | 前回比 |")
    print("|---|---|---|---|---|---|---|---|")
    for r in results:
        今回 = "ok" if r["result"] == "ok" else "error"
        out_tok = r.get("output_tokens", "-")
        prev_elapsed = r["prev_elapsed_s"]
        cur_elapsed = r["elapsed_s"]
        if r["prev_result"] != "ok" and r["result"] == "ok":
            比 = f"504→ok ({prev_elapsed}s→{cur_elapsed}s)"
        elif r["prev_result"] == "ok" and r["result"] == "ok":
            比 = f"ok→ok ({prev_elapsed}s→{cur_elapsed}s)"
        else:
            比 = f"{r['prev_result']}→{今回} ({prev_elapsed}s→{cur_elapsed}s)"
        print(
            f"| {r['line']} | {r['round_num']} | {r['phase']} | {r['prev_result']} | "
            f"{今回} | {cur_elapsed} | {out_tok} | {比} |"
        )


def main() -> int:
    args = parse_args()
    line_numbers = [int(x.strip()) for x in args.lines.split(",") if x.strip()]
    log_path = _resolve_log_path(args)
    records = _load_lines(log_path, line_numbers)

    if args.dry_run:
        return _run_dry_run(records, line_numbers)

    if not __import__("os").environ.get("DEVRELAY_URL") or not __import__("os").environ.get("DEVRELAY_TOKEN"):
        print("[SKIP] DEVRELAY_URL / DEVRELAY_TOKEN が未設定のため実送信をスキップします。")
        return 0

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        repo_root = Path(__file__).resolve().parent.parent
        out_dir = repo_root / "logs" / "replay" / datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"replay_{args.seat}.jsonl"

    results = _run_send(records, line_numbers, args.model, args.seat, args.repeat, out_path)
    _print_summary_table(results)
    return 0


if __name__ == "__main__":
    sys.exit(main())
