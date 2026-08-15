"""
出力トークン削減の効果検証バッチ

before（修正前プロンプト）と after（修正後プロンプト）で、
Haiku / GPT-4.1-mini の出力トークンを比較する。
"""

# ============================================================================
# WARNING: extract_json_content() は本番パーサ llm/response_parser.py とは
# 別実装であり、JSON有効率を誤って低く算出する。
#
# 具体的には find("{") ~ rfind("}") の1回勝負のため、Haiku が散文中に { を
# 含む場合に破綻する。complex_negotiation で 6-7/10 と報告されたが、本番
# パーサ（4段階フォールバック）では 27/27 = 100% で問題なかった。
#
# 再利用時は llm.response_parser.parse_response() を import して使うこと。
# ============================================================================

import json
import os
import sys
import time
import statistics
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

from llm.adapters import create_adapter
from llm.models import get_model, estimate_cost
from llm.constants import DEFAULT_MAX_TOKENS, DEFAULT_TEMPERATURE

# --- 設定 ---
RUNS_PER_INPUT = 10
LOG_DIR = Path(__file__).resolve().parent.parent / "logs/llm/trial_C_20260814_210120/llm_logs"

# テスト入力のソース（P04 Haiku のログから抽出）
TEST_INPUTS = [
    {"label": "early_negotiation", "call_idx": 4},   # R1 nego turn4
    {"label": "complex_negotiation", "call_idx": 23}, # R4 nego with contracts
    {"label": "mid_commit", "call_idx": 13},          # R2 commit
]

# 修正前のプロンプト（変更された行のみ差し替え）
BEFORE_PATCH = (
    "出力はJSONオブジェクトのみを返してください。前置き・後置きの説明文やマークダウンのコードフェンス（```json等）は不要です。",
    '出力は必ず以下の形式:\n```json',
)
# AFTER は現在のプロンプトそのまま

AFTER_PATCH_CLOSE = (
    "",  # after にはコードフェンス閉じがない
    "\n```",  # before にはコードフェンス閉じがある
)


def load_test_inputs():
    """P04 のログからテスト入力を抽出する"""
    p04_file = LOG_DIR / "game01_P04_llm_calls.jsonl"
    with open(p04_file) as f:
        entries = [json.loads(line) for line in f]

    inputs = []
    for ti in TEST_INPUTS:
        e = entries[ti["call_idx"]]
        inputs.append({
            "label": ti["label"],
            "system_prompt": e["system_prompt"],
            "user_prompt": e["user_prompt"],
            "round_num": e["round_num"],
            "phase": e["phase"],
        })
    return inputs


def make_before_prompt(system_prompt: str) -> str:
    """修正後 → 修正前に戻す"""
    sp = system_prompt
    # 1行目の差し替え
    sp = sp.replace(BEFORE_PATCH[0], BEFORE_PATCH[1])
    # コードフェンス閉じの復元: JSON例示行の直後に ``` を挿入
    # 例示行を特定
    marker = '"emotion": "楽"}}, "action": {{"type": "アクション種別", ...}}}}'
    actual_marker = marker.replace("{{", "{").replace("}}", "}")
    idx = sp.find(actual_marker)
    if idx >= 0:
        end = idx + len(actual_marker)
        sp = sp[:end] + "\n```" + sp[end:]
    return sp


def extract_json_content(text: str) -> tuple[dict | None, str, str]:
    """レスポンスからJSON部分と前後テキストを分離"""
    stripped = text.strip()
    # コードフェンス除去
    if stripped.startswith("```"):
        lines = stripped.split("\n", 1)
        stripped = lines[1] if len(lines) > 1 else ""
    if stripped.endswith("```"):
        stripped = stripped[:-3]
    stripped = stripped.strip()

    js = stripped.find("{")
    je = stripped.rfind("}")
    if js < 0 or je < 0:
        return None, text, ""

    prefix = text[:text.find("{")]
    suffix = text[text.rfind("}") + 1:]
    json_str = stripped[js : je + 1]

    try:
        data = json.loads(json_str)
        return data, prefix, suffix
    except json.JSONDecodeError:
        return None, prefix, suffix


def run_benchmark():
    """ベンチマーク実行"""
    inputs = load_test_inputs()
    models = [
        ("claude-haiku-4-5-20251001", "L1"),
        ("gpt-4.1-mini", "L2"),
    ]

    results = {}  # {model_key: {condition: {input_label: [result_dicts]}}}

    total_cost = 0.0
    total_calls = 0

    for model_id, model_key in models:
        model_info = get_model(model_key)
        adapter = create_adapter(model_info)
        results[model_id] = {"before": {}, "after": {}}

        for condition in ["before", "after"]:
            for inp in inputs:
                label = inp["label"]
                results[model_id][condition][label] = []

                if condition == "before":
                    system = make_before_prompt(inp["system_prompt"])
                else:
                    system = inp["system_prompt"]

                user = inp["user_prompt"]

                for run in range(RUNS_PER_INPUT):
                    effective_max_tokens = model_info.max_tokens or DEFAULT_MAX_TOKENS
                    try:
                        text, usage = adapter.complete(
                            system=system,
                            messages=[{"role": "user", "content": user}],
                            max_tokens=effective_max_tokens,
                            temperature=DEFAULT_TEMPERATURE,
                        )
                    except Exception as e:
                        print(f"  ERROR: {model_id} {condition} {label} run {run}: {e}")
                        results[model_id][condition][label].append({
                            "error": str(e)[:200],
                        })
                        continue

                    cost = estimate_cost(model_info, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
                    total_cost += cost
                    total_calls += 1

                    data, prefix, suffix = extract_json_content(text)

                    # 非JSONテキスト分析
                    clean_prefix = prefix.replace("```json", "").replace("```", "").strip()
                    clean_suffix = suffix.replace("```", "").strip()
                    has_codefence = "```" in text

                    # JSON内フィールド分析
                    strategy_chars = 0
                    action_chars = 0
                    reason_chars = 0
                    action_type = ""
                    if data:
                        s = data.get("strategy", {})
                        a = data.get("action", {})
                        strategy_chars = len(json.dumps(s, ensure_ascii=False))
                        action_chars = len(json.dumps(a, ensure_ascii=False))
                        reason_chars = len(str(s.get("reason", "")))
                        action_type = a.get("type", "")

                    result = {
                        "output_tokens": usage.get("output_tokens", 0),
                        "input_tokens": usage.get("input_tokens", 0),
                        "response_chars": len(text),
                        "non_json_chars": len(clean_prefix) + len(clean_suffix),
                        "has_codefence": has_codefence,
                        "json_valid": data is not None,
                        "strategy_chars": strategy_chars,
                        "action_chars": action_chars,
                        "reason_chars": reason_chars,
                        "action_type": action_type,
                        "cost": cost,
                        "response_text": text,
                    }
                    results[model_id][condition][label].append(result)
                    print(f"  {model_id} {condition} {label} run {run}: out={usage.get('output_tokens', 0)} tok, cost=${cost:.4f}")

                    # Rate limit対策
                    time.sleep(0.5)

    print(f"\n=== Total: {total_calls} calls, ${total_cost:.4f} ===")
    return results, total_cost


def generate_report(results: dict, total_cost: float) -> str:
    """数値レポートとレスポンス抜粋を生成"""
    lines = []
    lines.append("# 出力トークン削減 効果検証レポート\n")
    lines.append(f"実行日時: {time.strftime('%Y-%m-%d %H:%M JST', time.localtime())}")
    lines.append(f"総コール数: {sum(len(rs) for m in results.values() for c in m.values() for rs in c.values())}")
    lines.append(f"総コスト: ${total_cost:.4f}\n")

    # --- 主指標テーブル ---
    lines.append("## 1. 主指標（出力トークン）\n")
    lines.append("| モデル | 条件 | 入力 | 平均out_tok | std | min | max | 非JSON chars avg | コードフェンス率 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")

    for model_id in results:
        for condition in ["before", "after"]:
            for label in results[model_id][condition]:
                runs = [r for r in results[model_id][condition][label] if "error" not in r]
                if not runs:
                    lines.append(f"| {model_id[:20]} | {condition} | {label} | ERROR | - | - | - | - | - |")
                    continue
                ot = [r["output_tokens"] for r in runs]
                nj = [r["non_json_chars"] for r in runs]
                cf = sum(1 for r in runs if r["has_codefence"])
                avg_ot = statistics.mean(ot)
                std_ot = statistics.stdev(ot) if len(ot) > 1 else 0
                lines.append(
                    f"| {model_id[:20]} | {condition} | {label} | {avg_ot:.0f} | {std_ot:.0f} | {min(ot)} | {max(ot)} | {statistics.mean(nj):.0f} | {cf}/{len(runs)} |"
                )

    # --- 削減率サマリ ---
    lines.append("\n## 2. 削減率サマリ\n")
    lines.append("| モデル | 入力 | before avg | after avg | 削減率 |")
    lines.append("|---|---|---|---|---|")
    for model_id in results:
        for label in TEST_INPUTS:
            lbl = label["label"]
            before_runs = [r for r in results[model_id]["before"].get(lbl, []) if "error" not in r]
            after_runs = [r for r in results[model_id]["after"].get(lbl, []) if "error" not in r]
            if before_runs and after_runs:
                b_avg = statistics.mean([r["output_tokens"] for r in before_runs])
                a_avg = statistics.mean([r["output_tokens"] for r in after_runs])
                reduction = (1 - a_avg / b_avg) * 100 if b_avg > 0 else 0
                lines.append(f"| {model_id[:20]} | {lbl} | {b_avg:.0f} | {a_avg:.0f} | {reduction:+.1f}% |")

    # --- 副作用検出 ---
    lines.append("\n## 3. 副作用検出指標\n")
    lines.append("| モデル | 条件 | 入力 | JSON有効率 | reason avg chars | action_type分布 |")
    lines.append("|---|---|---|---|---|---|")
    for model_id in results:
        for condition in ["before", "after"]:
            for label in results[model_id][condition]:
                runs = [r for r in results[model_id][condition][label] if "error" not in r]
                if not runs:
                    continue
                valid = sum(1 for r in runs if r["json_valid"])
                reason_avg = statistics.mean([r["reason_chars"] for r in runs]) if runs else 0
                # action type distribution
                types = {}
                for r in runs:
                    t = r["action_type"] or "?"
                    types[t] = types.get(t, 0) + 1
                type_str = ", ".join(f"{k}:{v}" for k, v in sorted(types.items()))
                lines.append(
                    f"| {model_id[:20]} | {condition} | {label} | {valid}/{len(runs)} | {reason_avg:.0f} | {type_str} |"
                )

    # --- レスポンス抜粋 ---
    lines.append("\n## 4. レスポンス抜粋（before/after 比較）\n")
    for model_id in results:
        lines.append(f"### {model_id}\n")
        for label_info in TEST_INPUTS:
            label = label_info["label"]
            lines.append(f"#### {label}\n")
            for condition in ["before", "after"]:
                runs = [r for r in results[model_id][condition].get(label, []) if "error" not in r]
                lines.append(f"**{condition}** (2件抜粋):\n")
                for r in runs[:2]:
                    text = r["response_text"]
                    # 長すぎる場合は先頭500文字 + 末尾200文字
                    if len(text) > 800:
                        display = text[:500] + "\n...(省略)...\n" + text[-200:]
                    else:
                        display = text
                    lines.append(f"```\nout_tok={r['output_tokens']}, non_json={r['non_json_chars']} chars\n{display}\n```\n")

    return "\n".join(lines)


if __name__ == "__main__":
    print("=== 出力トークン削減 効果検証バッチ ===")
    print(f"テスト入力: {len(TEST_INPUTS)} 局面")
    print(f"実行回数: {RUNS_PER_INPUT} 回/入力")
    print(f"推定コスト: $0.61")
    print()

    results, total_cost = run_benchmark()

    report = generate_report(results, total_cost)

    # レポート出力
    output_dir = Path(__file__).resolve().parent.parent / ".devrelay-output"
    output_dir.mkdir(exist_ok=True)
    report_path = output_dir / "bench_report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReport saved to: {report_path}")

    # コンソールにもサマリ出力
    for line in report.split("\n"):
        if line.startswith("|") or line.startswith("#"):
            print(line)
