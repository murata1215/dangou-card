"""
6社(最大18モデル) API疎通チェックスクリプト

各モデルに2コール（疎通+ゲームJSON）を実行し、
結果をdoc/api_check_report.mdに出力する。

MODEL_REGISTRYにH1〜H6（強量級・フラッグシップ）が追加されたため、
デフォルトの全走査には高額モデルが含まれる。全走査は明示的な
--all指定なしでは実行しない（誤って$10級モデルをまとめて叩かないための安全策）。

使用方法:
    uv run python scripts/api_check.py --keys M1,L1,M2      # 対象を絞って実行（推奨）
    uv run python scripts/api_check.py --all                # 全モデル走査（要注意・高額）
"""

import argparse
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from llm.models import MODEL_REGISTRY, ModelInfo, estimate_cost
from llm.adapters import create_adapter, AdapterError
from llm.response_parser import extract_json, parse_response, ParseError


# --- 疎通テスト用プロンプト ---
PING_SYSTEM = "You are a test assistant."
PING_USER = "Reply with exactly: OK"

# --- ゲームJSONテスト用プロンプト（縮小版） ---
GAME_SYSTEM = """あなたはP01です。市場争奪ゲームに参加中。
3つの市場にカードを出して賞金を競います。
回答は必ずJSON形式で返してください。"""

GAME_USER = """ラウンド1 / 交渉フェイズ（巡1）

市場:
  M01: 賞金 42万円
  M02: 賞金 42万円
  M03: 賞金 42万円

あなたの状態:
  現金: 300万円
  手札: HIGH_CARD, ONE_PAIR, TWO_PAIR, THREE_OF_A_KIND

アクションをJSON形式で1つ選んでください。
例: {"strategy": {"target_market": "M01", "reason": "テスト"}, "action": {"type": "pass"}}"""


def check_model(
    key: str,
    model: ModelInfo,
    cost_budget: list[float],
    max_cost: float = 0.5,
) -> dict[str, Any]:
    """1モデルの疎通チェックを実行する"""
    result: dict[str, Any] = {
        "key": key,
        "model_id": model.model_id,
        "provider": model.provider,
        "name": model.name,
        "adapter_type": model.adapter_type,
    }

    # キー確認
    api_key = os.environ.get(model.env_key, "")
    if not api_key:
        result["status"] = "SKIP"
        result["error"] = f"キー未設定: {model.env_key}"
        result["ping_ok"] = False
        result["json_ok"] = False
        return result

    try:
        adapter = create_adapter(model)
    except Exception as e:
        result["status"] = "ERROR"
        result["error"] = f"アダプタ作成失敗: {type(e).__name__}"
        result["ping_ok"] = False
        result["json_ok"] = False
        return result

    # --- 疎通テスト ---
    ping_ok = False
    ping_latency = 0.0
    ping_tokens = {"input_tokens": 0, "output_tokens": 0}
    ping_error = None

    try:
        start = time.time()
        text, usage = adapter.complete(
            system=PING_SYSTEM,
            messages=[{"role": "user", "content": PING_USER}],
            max_tokens=50,
            temperature=0.0,
        )
        ping_latency = (time.time() - start) * 1000
        ping_ok = len(text.strip()) > 0
        ping_tokens = usage
    except (AdapterError, Exception) as e:
        ping_error = f"{type(e).__name__}: {str(e)[:100]}"

    ping_cost = estimate_cost(model, ping_tokens.get("input_tokens", 0), ping_tokens.get("output_tokens", 0))
    cost_budget[0] += ping_cost

    result["ping_ok"] = ping_ok
    result["ping_latency_ms"] = round(ping_latency)
    result["ping_error"] = ping_error
    result["ping_tokens"] = ping_tokens

    if cost_budget[0] > max_cost:
        result["status"] = "BUDGET"
        result["json_ok"] = False
        return result

    # --- ゲームJSONテスト ---
    json_ok = False
    json_latency = 0.0
    json_tokens = {"input_tokens": 0, "output_tokens": 0}
    json_error = None
    json_parse_ok = False

    if ping_ok:
        try:
            start = time.time()
            text, usage = adapter.complete(
                system=GAME_SYSTEM,
                messages=[{"role": "user", "content": GAME_USER}],
                max_tokens=4000,
                temperature=0.7,
            )
            json_latency = (time.time() - start) * 1000
            json_tokens = usage
            json_ok = len(text.strip()) > 0

            # JSONパース検証
            if json_ok:
                data = extract_json(text)
                if data:
                    try:
                        parse_response(text, "P01", "negotiation")
                        json_parse_ok = True
                    except ParseError:
                        json_parse_ok = False
                        json_error = "ParseError（JSON形式不正）"
                else:
                    json_error = "JSON抽出失敗"

        except (AdapterError, Exception) as e:
            json_error = f"{type(e).__name__}: {str(e)[:100]}"

    json_cost = estimate_cost(model, json_tokens.get("input_tokens", 0), json_tokens.get("output_tokens", 0))
    cost_budget[0] += json_cost

    result["json_ok"] = json_parse_ok
    result["json_latency_ms"] = round(json_latency)
    result["json_error"] = json_error
    result["json_tokens"] = json_tokens
    result["total_cost"] = round(ping_cost + json_cost, 6)
    result["status"] = "OK" if ping_ok and json_parse_ok else "PARTIAL" if ping_ok else "FAIL"

    return result


def generate_report(results: list[dict], total_cost: float, elapsed: float, output_path: Path) -> None:
    """疎通結果レポートを生成する"""
    n = len(results)
    lines: list[str] = []
    lines.append(f"# {n}モデル API疎通チェックレポート\n")
    lines.append(f"- 実行日: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- 実行時間: {elapsed:.1f}秒")
    lines.append(f"- 総コスト: ${total_cost:.4f}")
    lines.append("")

    # 結果表
    ok_count = sum(1 for r in results if r["status"] == "OK")
    partial = sum(1 for r in results if r["status"] == "PARTIAL")
    skip = sum(1 for r in results if r["status"] == "SKIP")
    fail = sum(1 for r in results if r["status"] in ("FAIL", "ERROR"))

    lines.append(f"## サマリ: 疎通OK {ok_count}/{n}, JSON成立 {sum(1 for r in results if r.get('json_ok'))}/{n}\n")

    lines.append("## 結果表\n")
    lines.append("| # | ベンダー | モデル | 疎通 | レイテンシ | JSON | JSON ms | コスト | 備考 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    for r in results:
        ping = "✓" if r["ping_ok"] else "✗" if r["status"] != "SKIP" else "—"
        json_s = "✓" if r.get("json_ok") else "✗" if r["status"] not in ("SKIP", "BUDGET") else "—"
        latency = f"{r.get('ping_latency_ms', 0)}ms" if r["ping_ok"] else "—"
        json_lat = f"{r.get('json_latency_ms', 0)}ms" if r.get("json_ok") else "—"
        cost = f"${r.get('total_cost', 0):.4f}" if r.get("total_cost") else "—"
        note = r.get("ping_error") or r.get("json_error") or r.get("error", "")
        note = note[:60] if note else ""
        lines.append(f"| {r['key']} | {r['provider']} | {r['name']} | {ping} | {latency} | {json_s} | {json_lat} | {cost} | {note} |")

    # ベンダー別注意点
    lines.append("\n## ベンダー別注意点\n")
    vendors: dict[str, list[dict]] = {}
    for r in results:
        vendors.setdefault(r["provider"], []).append(r)

    for vendor, vr in sorted(vendors.items()):
        notes = []
        if all(r["status"] == "SKIP" for r in vr):
            notes.append("キー未設定のためスキップ")
        elif any(r["status"] in ("FAIL", "ERROR") for r in vr):
            errors = [r.get("ping_error") or r.get("json_error") or r.get("error", "") for r in vr if r["status"] in ("FAIL", "ERROR")]
            notes.append(f"エラー: {'; '.join(e[:80] for e in errors if e)}")
        if any(r.get("json_ok") and not r.get("json_error") for r in vr):
            notes.append("JSON出力成功")
        lines.append(f"- **{vendor}**: {', '.join(notes) if notes else '正常'}")

    # Step 3C推奨8体
    lines.append("\n## Step 3C推奨8体の提案\n")
    ok_models = [r for r in results if r.get("json_ok")]
    if len(ok_models) >= 8:
        # 6社カバー + JSON成功 + レイテンシ順
        selected = sorted(ok_models, key=lambda r: r.get("json_latency_ms", 99999))[:8]
        lines.append("JSON成立+レイテンシ上位8体:\n")
        for r in selected:
            lines.append(f"- **{r['key']}** {r['name']} ({r['provider']}, {r.get('json_latency_ms', '?')}ms)")
    elif ok_models:
        lines.append(f"JSON成立モデルが{len(ok_models)}体のみ。全て推奨:\n")
        for r in ok_models:
            lines.append(f"- **{r['key']}** {r['name']} ({r['provider']})")
    else:
        lines.append("JSON成立モデルがありません。モデルIDの確認が必要です。")

    lines.append("")
    output_path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="モデルAPI疎通チェック（既定は全走査せず--keys必須）")
    parser.add_argument("--keys", default="", help="カンマ区切りの対象キー（例: M1,L1,M2）")
    parser.add_argument("--all", action="store_true",
                         help="全モデルを走査する（H1〜H6の高額モデルを含むため要注意・明示指定必須）")
    parser.add_argument("--max-cost", type=float, default=0.5, help="総コスト上限USD（既定0.5）")
    args = parser.parse_args()

    if args.keys:
        keys = [k.strip() for k in args.keys.split(",") if k.strip()]
        unknown = [k for k in keys if k not in MODEL_REGISTRY]
        if unknown:
            print(f"不明なキー: {unknown}")
            sys.exit(1)
    elif args.all:
        keys = sorted(MODEL_REGISTRY.keys())
    else:
        print("--keys で対象を指定するか、--all で全モデル走査を明示してください"
              "（H1〜H6は高額モデルのため誤爆防止で既定は何もしません）")
        sys.exit(1)

    print(f"=== {len(keys)}モデル API疎通チェック ===")
    print(f"コスト上限: ${args.max_cost:.2f}")
    print("---")

    start = time.time()
    cost_budget = [0.0]  # mutableで渡すためリスト
    results: list[dict] = []

    for key in keys:
        model = MODEL_REGISTRY[key]
        print(f"{key} ({model.provider} {model.name})...", end=" ", flush=True)

        if cost_budget[0] > args.max_cost:
            print("BUDGET超過")
            results.append({
                "key": key, "model_id": model.model_id, "provider": model.provider,
                "name": model.name, "adapter_type": model.adapter_type,
                "status": "BUDGET", "ping_ok": False, "json_ok": False,
            })
            continue

        r = check_model(key, model, cost_budget, max_cost=args.max_cost)
        results.append(r)
        status = r["status"]
        detail = ""
        if r["ping_ok"]:
            detail += f"疎通OK({r.get('ping_latency_ms', '?')}ms)"
        if r.get("json_ok"):
            detail += f" JSON✓({r.get('json_latency_ms', '?')}ms)"
        elif r.get("json_error"):
            detail += f" JSON✗"
        if r.get("error"):
            detail = r["error"][:60]
        print(f"{status} {detail} ${r.get('total_cost', 0):.4f}")

    elapsed = time.time() - start

    # レポート生成
    report_path = Path("doc/api_check_report.md")
    generate_report(results, cost_budget[0], elapsed, report_path)
    print(f"\n=== 完了 ===")
    print(f"実行時間: {elapsed:.1f}秒, 総コスト: ${cost_budget[0]:.4f}")
    print(f"レポート: {report_path}")


if __name__ == "__main__":
    main()
