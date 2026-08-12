"""
Kimi K2.6 長考問題 調査スクリプト

trial_C の実ゲームプロンプトを使い、thinking無効化 / max_tokens増量の
効果を実測比較する。調査コスト上限$1, 30コール以内。
"""

import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from llm.models import ModelInfo, MOONSHOT_BASE_URL, estimate_cost
from llm.response_parser import extract_json


KIMI_LOG = Path("logs/llm/trial_C_20260810_053055/llm_logs/game01_P08_llm_calls.jsonl")


def load_prompts():
    """trial_C P08 ログから各フェイズの実プロンプトを1つずつ抽出"""
    with open(KIMI_LOG) as f:
        entries = [json.loads(line) for line in f]
    prompts = {}
    for phase in ("loan_choice", "negotiation", "commit"):
        for d in entries:
            if d["phase"] == phase:
                prompts[phase] = {
                    "system": d["system_prompt"],
                    "user": d["user_prompt"],
                }
                break
    return prompts


def call_kimi(system: str, user: str, model_info: ModelInfo, max_tokens: int,
              extra_params: dict | None = None):
    """Kimi K2.6 APIを直接呼び出し、詳細結果を返す"""
    import openai
    api_key = os.environ.get("KIMI_API_KEY")
    if not api_key:
        raise RuntimeError("KIMI_API_KEY not set")
    client = openai.OpenAI(
        api_key=api_key,
        base_url=MOONSHOT_BASE_URL,
        timeout=model_info.timeout_seconds,
    )
    messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
    create_kwargs = {
        "model": model_info.model_id,
        "messages": messages,
        "max_tokens": max_tokens,
    }
    if extra_params:
        create_kwargs["extra_body"] = extra_params

    start = time.time()
    try:
        # Kimi K2.6 thinking mode fixes temperature; non-thinking=0.6, thinking=1.0
        # Don't pass temperature to avoid rejection
        response = client.chat.completions.create(**create_kwargs)
    except Exception as e:
        elapsed = time.time() - start
        return {
            "text": "", "finish_reason": "error", "error": str(e)[:200],
            "input_tokens": 0, "output_tokens": 0,
            "elapsed_s": elapsed, "cost": 0.0, "json_ok": False,
        }

    elapsed = time.time() - start
    text = response.choices[0].message.content or "" if response.choices else ""
    # reasoning fallback
    if not text and response.choices:
        extras = getattr(response.choices[0].message, 'model_extra', None) or {}
        text = extras.get('reasoning_content', '') or ''
    finish_reason = response.choices[0].finish_reason if response.choices else None
    in_tok = getattr(response.usage, "prompt_tokens", 0) or 0
    out_tok = getattr(response.usage, "completion_tokens", 0) or 0
    cost = estimate_cost(model_info, in_tok, out_tok)

    # check if JSON is extractable
    json_data = extract_json(text)
    json_ok = json_data is not None

    return {
        "text": text, "finish_reason": finish_reason,
        "input_tokens": in_tok, "output_tokens": out_tok,
        "elapsed_s": elapsed, "cost": cost, "json_ok": json_ok,
        "text_len": len(text), "error": None,
    }


def main():
    prompts = load_prompts()
    print(f"Loaded prompts: {list(prompts.keys())}")

    base_model = ModelInfo(
        model_id="kimi-k2.6", provider="Moonshot", name="Kimi K2.6",
        adapter_type="openai_compat", input_price=0.95, output_price=4.0,
        env_key="KIMI_API_KEY", base_url=MOONSHOT_BASE_URL,
        timeout_seconds=120,
    )

    configs = {
        "A_baseline": {"max_tokens": 4000, "extra": None, "desc": "thinking=enabled, max_tokens=4000 (current)"},
        "B_no_think": {"max_tokens": 4000, "extra": {"thinking": {"type": "disabled"}}, "desc": "thinking=disabled, max_tokens=4000"},
        "C_big_budget": {"max_tokens": 8000, "extra": None, "desc": "thinking=enabled, max_tokens=8000"},
    }

    results = []
    total_cost = 0.0
    total_calls = 0
    cost_limit = 1.0
    call_limit = 30

    # Test each config with 2 phases (negotiation, commit) x 2 runs
    test_phases = ["negotiation", "commit"]
    runs_per = 2

    for cfg_name, cfg in configs.items():
        print(f"\n{'='*60}")
        print(f"Config: {cfg_name} -- {cfg['desc']}")
        print(f"{'='*60}")
        for phase in test_phases:
            if phase not in prompts:
                continue
            p = prompts[phase]
            for run_i in range(runs_per):
                if total_cost >= cost_limit or total_calls >= call_limit:
                    print(f"  SKIPPED (budget: ${total_cost:.4f}/{cost_limit}, calls: {total_calls}/{call_limit})")
                    break
                print(f"  {phase} run{run_i+1}...", end="", flush=True)
                r = call_kimi(p["system"], p["user"], base_model, cfg["max_tokens"], cfg["extra"])
                total_cost += r["cost"]
                total_calls += 1
                results.append({"config": cfg_name, "phase": phase, "run": run_i, **r})
                status = "OK" if r["json_ok"] else "FAIL"
                print(f" {status} finish={r['finish_reason']} json={r['json_ok']} "
                      f"len={r.get('text_len',0)} out_tok={r['output_tokens']} "
                      f"{r['elapsed_s']:.1f}s ${r['cost']:.4f}"
                      f"{' ERR:'+r['error'][:60] if r['error'] else ''}")

    # Summary
    print(f"\n{'='*60}")
    print(f"SUMMARY (total: {total_calls} calls, ${total_cost:.4f})")
    print(f"{'='*60}")
    for cfg_name in configs:
        cr = [r for r in results if r["config"] == cfg_name]
        if not cr:
            continue
        json_ok = sum(1 for r in cr if r["json_ok"])
        avg_lat = sum(r["elapsed_s"] for r in cr) / len(cr)
        avg_cost = sum(r["cost"] for r in cr) / len(cr)
        avg_out = sum(r["output_tokens"] for r in cr) / len(cr)
        finish_reasons = {}
        for r in cr:
            fr = r["finish_reason"] or "?"
            finish_reasons[fr] = finish_reasons.get(fr, 0) + 1
        print(f"\n{cfg_name}: {configs[cfg_name]['desc']}")
        print(f"  JSON rate: {json_ok}/{len(cr)} ({100*json_ok/len(cr):.0f}%)")
        print(f"  Avg latency: {avg_lat:.1f}s")
        print(f"  Avg cost/call: ${avg_cost:.4f}")
        print(f"  Avg output tokens: {avg_out:.0f}")
        print(f"  Finish reasons: {finish_reasons}")

    # Write report
    report_path = Path("logs/kimi_investigation_report.md")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"# Kimi K2.6 Investigation Report",
        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Total calls: {total_calls}, Total cost: ${total_cost:.4f}",
        "",
        "## Config Results",
        "",
        "| Config | JSON Rate | Avg Latency | Avg Cost/Call | Avg Out Tokens | Finish Reasons |",
        "|---|---|---|---|---|---|",
    ]
    for cfg_name in configs:
        cr = [r for r in results if r["config"] == cfg_name]
        if not cr:
            continue
        json_ok = sum(1 for r in cr if r["json_ok"])
        avg_lat = sum(r["elapsed_s"] for r in cr) / len(cr)
        avg_cost = sum(r["cost"] for r in cr) / len(cr)
        avg_out = sum(r["output_tokens"] for r in cr) / len(cr)
        finish_reasons = {}
        for r in cr:
            fr = r["finish_reason"] or "?"
            finish_reasons[fr] = finish_reasons.get(fr, 0) + 1
        lines.append(
            f"| {cfg_name} | {json_ok}/{len(cr)} ({100*json_ok/len(cr):.0f}%) | "
            f"{avg_lat:.1f}s | ${avg_cost:.4f} | {avg_out:.0f} | {finish_reasons} |"
        )
    lines += [
        "",
        "## Detail",
        "",
        "| Config | Phase | Run | JSON | Finish | Out Tokens | Latency | Cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['config']} | {r['phase']} | {r['run']} | "
            f"{'OK' if r['json_ok'] else 'FAIL'} | {r['finish_reason']} | "
            f"{r['output_tokens']} | {r['elapsed_s']:.1f}s | ${r['cost']:.4f} |"
        )
    lines.append("")
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport: {report_path}")


if __name__ == "__main__":
    main()
