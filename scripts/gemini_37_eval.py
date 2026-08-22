"""Gemini 3.7 Flash の段階的・予算付き技術検証。

実APIを呼ぶサブコマンドは明示実行時のみ。M3 は変更せず、M3_G37_EVAL を
検証専用キーとして使う。全結果は logs/gemini_37_eval/<run_id>/ に残す。
"""

import argparse
import json
import math
import statistics
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bots.random_bot import RandomBot
from engine.cards import create_deck
from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import Market, PlayerState
from llm.adapters import AdapterError, create_adapter
from llm.costing import usage_cost, worst_case_cost
from llm.game_cost_budget import GameCostBudget
from llm.llm_agent import LLMAgent
from llm.llm_logger import LLMLogger
from llm.models import MODEL_REGISTRY, ModelInfo
from llm.prompt_builder import (
    build_commit_prompt, build_double_up_prompt, build_loan_prompt,
    build_negotiation_prompt, build_system_prompt,
)
from llm.response_parser import (
    VALID_EMOTIONS, ParseError, extract_json, make_correction_message, parse_response,
)
from scripts.model_matrix import BudgetedAdapter
from scripts.model_smoke import PING_SYSTEM, PING_USER


DEFAULT_OUT = Path("logs/gemini_37_eval")
CASE_NAMES = ("loan_choice", "negotiation", "commit", "double_up")
SUPPORTED_KEYS = ("M3", "M3_G37_EVAL")
PHASE1_INPUT_PRICE = 0.75
PHASE1_OUTPUT_PRICE = 3.75
PHASE3_PRICES = {
    "M3": ("gemini-3.5-flash", 1.50, 9.00),
    "M3_G37_EVAL": ("gemini-3.7-flash", 0.75, 3.75),
}


def _jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def _effective_model(key: str, max_tokens: int) -> ModelInfo:
    if key not in SUPPORTED_KEYS:
        raise ValueError(f"対象は {', '.join(SUPPORTED_KEYS)} のみです: {key}")
    # 3.7との比較では両モデルにsampling parameterを送らない。
    return replace(MODEL_REGISTRY[key], max_tokens=max_tokens, supports_temperature=False)


def _request_options(model: ModelInfo, thinking: str) -> dict[str, Any]:
    if thinking not in ("low", "medium", "high"):
        raise ValueError("thinking は low / medium / high のいずれかです")
    if model.adapter_type != "gemini":
        raise ValueError("Geminiモデルだけを対象にします")
    # Google OpenAI compatibility: reasoning_effort maps to Gemini thinking_level.
    # thinking_level/thinking_budget と同時には送らない。
    return {"reasoning_effort": thinking}


def _sample_context() -> tuple[GameConfig, PlayerState, list[Market], dict[str, Any], str]:
    config = GameConfig.baseline_v1_s2(num_players=12)
    player = PlayerState(
        player_id="P01", cash=3_000_000, debt_balance=2_400_000,
        initial_loan=3_000_000, hand=create_deck(),
    )
    markets = [
        Market(market_id="M01", base_prize=800_000),
        Market(market_id="M02", base_prize=800_000),
        Market(market_id="M03", base_prize=800_000),
    ]
    visible = {
        "alive_players": [f"P{i:02d}" for i in range(1, 13)],
        "markets": [
            {"market_id": "M01", "prize_pool": 800_000, "base_prize": 800_000, "carryover": 0},
            {"market_id": "M02", "prize_pool": 900_000, "base_prize": 800_000, "carryover": 100_000},
            {"market_id": "M03", "prize_pool": 800_000, "base_prize": 800_000, "carryover": 0},
        ],
        "used_cards": {"P02": ["ONE_PAIR"], "P03": ["FLUSH"]}, "double_ups": [],
        "my_obligations": [{
            "contract_id": "C01", "ob_type": "type_b_market", "round_num": 2,
            "details": {"market_id": "M01"}, "counterparty": "P02",
        }],
        "contracts_pending": [{
            "contract_id": "C02", "proposer": "P02", "round_created": 2,
            "parties": ["P01", "P02"], "signed_by": ["P02"],
            "obligations": [{
                "obligor": "P01", "counterparty": "P02", "ob_type": "type_b_card",
                "round_num": 3, "details": {"card_rank": "STRAIGHT"},
            }],
        }],
        "trades_pending": [{
            "trade_id": "T01", "proposer": "P03", "round_proposed": 2,
            "give_card_rank": "TWO_PAIR", "receive_card_rank": "FLUSH",
            "cash_amount": 0, "with_player": "P01",
        }],
        "messages": [
            {"sender": "P02", "type": "dm", "recipients": ["P01"], "message": "M01を分けませんか。"},
            {"sender": "P03", "type": "broadcast", "message": "私はM03を狙う。"},
        ],
        "last_round_results": {"round": 1, "markets": []},
    }
    memory = (
        "R1でP02とM01分配を口約束したが、C01ではR2のM01参加義務がある。"
        "P03のFLUSH交換案(T01)は、R3のC02カード義務と衝突し得る。"
        "借金返済を優先し、DOUBLEは空き巣でない市場に勝てる見込みがある場合だけ検討する。"
    )
    return config, player, markets, visible, memory


def build_case(case: str) -> tuple[str, str, str]:
    """Return (phase, system, realistic user prompt) for one production-format case."""
    config, player, markets, visible, memory = _sample_context()
    system = build_system_prompt("P01", config)
    if case == "loan_choice":
        return case, system, build_loan_prompt(config)
    if case == "negotiation":
        return case, system, build_negotiation_prompt(player, 2, 1, visible, config, memory=memory)
    if case == "commit":
        return case, system, build_commit_prompt(
            player, markets, 2, visible, config,
            negotiation_messages=visible["messages"],
            last_strategy={"reason": "M01をP02と分配", "emotion": "警戒"}, memory=memory,
        )
    if case == "double_up":
        return case, system, build_double_up_prompt(player, 800_000, 2, visible, config)
    raise ValueError(f"未知ケース: {case}")


def _validate(case: str, text: str, config: GameConfig) -> tuple[bool, str | None]:
    data = extract_json(text)
    if not data:
        return False, "JSON抽出失敗"
    try:
        strategy = data.get("strategy")
        if not isinstance(strategy, dict) or strategy.get("emotion") not in VALID_EMOTIONS:
            raise ParseError("strategy.emotion が不正", "strategyに有効なemotionを1つ指定してください")
        if case == "loan_choice":
            amount = data.get("action", data).get("amount")
            if not isinstance(amount, (int, float)) or not config.loan_min <= amount <= config.loan_max:
                raise ParseError("loan amount が範囲外", "action.amountに借入額を整数で指定してください")
        elif case in ("negotiation", "commit"):
            _, action = parse_response(text, "P01", case)
            if case == "commit" and action.type != "market_commit":
                raise ParseError("commit action が必要", "action.typeをmarket_commitにしてください")
        else:
            choice = str(data.get("choice", "")).upper()
            if choice not in ("TAKE", "DOUBLE"):
                raise ParseError("choice が不正", "choiceはTAKEまたはDOUBLEにしてください")
    except ParseError as e:
        return False, getattr(e, "correction_hint", str(e))
    return True, None


def _phase2_transport_error(usage: dict[str, Any], model: ModelInfo) -> str | None:
    """Detect conditions that must stop Phase 2 without a parser correction."""
    required = ("input_tokens", "output_tokens", "total_tokens", "finish_reason", "response_model")
    missing = [key for key in required if key not in usage or usage[key] is None]
    if missing:
        return f"usage unknown: missing {', '.join(missing)}"
    values = [usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]]
    if any(not isinstance(value, int) or value < 0 for value in values):
        return "usage unknown: token values are invalid"
    if usage["total_tokens"] < usage["input_tokens"] + usage["output_tokens"]:
        return "usage inconsistent: total_tokens is smaller than input + output"
    if usage["response_model"] != model.model_id:
        return f"returned model mismatch: {usage['response_model']}"
    finish_reason = str(usage["finish_reason"]).lower()
    if finish_reason == "length":
        return "length truncation"
    if finish_reason != "stop":
        return f"abnormal finish_reason: {usage['finish_reason']}"
    return None


def run_case(adapter: Any, model: ModelInfo, case: str, max_corrections: int) -> list[dict[str, Any]]:
    """Run one logical case; correction requests are separately auditable records."""
    phase, system, original = build_case(case)
    config, *_ = _sample_context()
    prompt = original
    records: list[dict[str, Any]] = []
    for retry in range(max_corrections + 1):
        started = time.time()
        try:
            text, usage = adapter.complete(system, [{"role": "user", "content": prompt}], max_tokens=model.max_tokens or 2000)
            error = _phase2_transport_error(usage, model)
            hard_stop = error is not None
        except Exception as e:
            text, usage = "", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            error = f"{type(e).__name__}: {str(e)[:300]}"
            hard_stop = True
        latency_ms = round((time.time() - started) * 1000)
        ok, correction = _validate(case, text, config) if error is None else (False, error)
        total = usage.get("total_tokens", 0) or 0
        input_t = usage.get("input_tokens", 0) or 0
        output_t = usage.get("output_tokens", 0) or 0
        records.append({
            "case": case, "retry_count": retry, "transport_retry_count": 0,
            "ok": ok, "error": error or correction, "hard_stop": hard_stop,
            "latency_ms": latency_ms, "finish_reason": usage.get("finish_reason"),
            "input_tokens": input_t, "output_tokens": output_t, "total_tokens": total,
            "reasoning_tokens": usage.get("reasoning_tokens", 0) or 0,
            "thinking_diff": max(0, total - input_t - output_t),
            "cost_usd": usage_cost(model, usage), "response_model": usage.get("response_model"),
            "response_text": text,
        })
        if ok or hard_stop or retry == max_corrections:
            break
        prompt = original + "\n\n" + make_correction_message(ParseError("JSON不適合", correction or "JSONだけを返してください"))
    return records


def _make_adapter(model: ModelInfo, max_calls: int, max_cost: float, thinking: str) -> BudgetedAdapter:
    raw = create_adapter(model, max_retries=0, allow_temperature_fallback=False)
    return BudgetedAdapter(raw, model, max_calls=max_calls, max_cost=max_cost,
                           max_tokens_cap=model.max_tokens or 2000,
                           request_options=_request_options(model, thinking))


def _phase1_preflight_error(model: ModelInfo) -> str | None:
    """Refuse to send when the evaluation registry drifts from its price snapshot."""
    if model.model_id != "gemini-3.7-flash":
        return f"requested model is not Gemini 3.7 Flash: {model.model_id}"
    if model.input_price != PHASE1_INPUT_PRICE or model.output_price != PHASE1_OUTPUT_PRICE:
        return "pricing snapshot mismatch"
    if model.supports_temperature:
        return "Gemini 3.7 Flash must not receive temperature"
    return None


def _phase3_preflight_error(key: str, model: ModelInfo, thinking: str) -> str | None:
    """Ensure both sides still match the recorded Standard-price comparison policy."""
    expected = PHASE3_PRICES.get(key)
    if expected is None:
        return f"unsupported comparison model: {key}"
    model_id, input_price, output_price = expected
    if (model.model_id, model.input_price, model.output_price) != expected:
        return f"pricing/model snapshot mismatch for {key}"
    if model.supports_temperature:
        return f"{key} must not receive sampling parameters"
    if _request_options(model, thinking) != {"reasoning_effort": "low"}:
        return "Phase 3 requires reasoning_effort=low for both models"
    return None


def _phase1_response_error(text: str, usage: dict[str, Any], model: ModelInfo) -> str | None:
    """Return an auditable hard-stop reason for a Phase-1 response."""
    required = ("input_tokens", "output_tokens", "total_tokens", "finish_reason", "response_model")
    missing = [key for key in required if key not in usage or usage[key] is None]
    if missing:
        return f"usage unknown: missing {', '.join(missing)}"
    values = [usage["input_tokens"], usage["output_tokens"], usage["total_tokens"]]
    if any(not isinstance(value, int) or value < 0 for value in values):
        return "usage unknown: token values are invalid"
    if usage["total_tokens"] < usage["input_tokens"] + usage["output_tokens"]:
        return "usage inconsistent: total_tokens is smaller than input + output"
    if usage["total_tokens"] == 0:
        return "usage unknown: total_tokens is zero"
    if usage["response_model"] != model.model_id:
        return f"returned model mismatch: {usage['response_model']}"
    if str(usage["finish_reason"]).lower() != "stop":
        return f"abnormal finish_reason: {usage['finish_reason']}"
    data = extract_json(text)
    if not isinstance(data, dict) or data.get("ok") is not True:
        return "invalid JSON ping response"
    return None


def run_smoke(args: argparse.Namespace, output_dir: Path) -> int:
    """Phase 1 only: strict JSON ping with one HTTP send per logical call."""
    model = _effective_model(args.model, args.max_tokens)
    preflight_error = _phase1_preflight_error(model)
    records_path = output_dir / "smoke_calls.jsonl"
    if preflight_error:
        summary = {"status": "stopped", "stop_reason": preflight_error, "calls": 0}
        (output_dir / "smoke_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 1

    adapter = _make_adapter(model, args.max_calls, args.max_cost, args.thinking)
    stop_reason: str | None = None
    records: list[dict[str, Any]] = []
    for index in range(1, args.calls + 1):
        reserved = worst_case_cost(model, PING_SYSTEM, PING_USER, args.max_tokens)
        started = time.time()
        try:
            text, usage = adapter.complete(
                PING_SYSTEM, [{"role": "user", "content": PING_USER}], max_tokens=args.max_tokens,
            )
            error = _phase1_response_error(text, usage, model)
        except Exception as exc:
            text, usage = "", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
            error = f"{type(exc).__name__}: {str(exc)[:300]}"
        latency_ms = round((time.time() - started) * 1000)
        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0
        total_tokens = usage.get("total_tokens", 0) or 0
        record = {
            "logical_call": index, "transport_retry_count": 0,
            "requested_model": model.model_id, "response_model": usage.get("response_model"),
            "thinking": args.thinking, "max_tokens": args.max_tokens,
            "reserved_cost_usd": reserved, "cost_usd": usage_cost(model, usage),
            "cumulative_cost_usd": adapter.spent_usd,
            "latency_ms": latency_ms, "finish_reason": usage.get("finish_reason"),
            "input_tokens": input_tokens, "output_tokens": output_tokens, "total_tokens": total_tokens,
            "reasoning_tokens": usage.get("reasoning_tokens", 0) or 0,
            "thinking_diff": max(0, total_tokens - input_tokens - output_tokens),
            "usage_raw": usage.get("usage_raw"), "json_extract_ok": extract_json(text) is not None,
            "response_text": text, "ok": error is None, "stop_reason": error,
        }
        _jsonl(records_path, record)
        records.append(record)
        if error:
            stop_reason = error
            break

    summary = {
        "status": "pass" if stop_reason is None and len(records) == args.calls else "stopped",
        "stop_reason": stop_reason, "requested_calls": args.calls, "calls": len(records),
        "model_key": args.model, "model_id": model.model_id, "thinking": args.thinking,
        "max_tokens": args.max_tokens, "max_cost_usd": args.max_cost,
        "cost_usd": round(adapter.spent_usd, 6), "records": records,
    }
    (output_dir / "smoke_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: value for key, value in summary.items() if key != "records"}, ensure_ascii=False))
    return 0 if summary["status"] == "pass" else 1


def run_json(args: argparse.Namespace, output_dir: Path) -> int:
    model = _effective_model(args.model, args.max_tokens)
    preflight_error = _phase1_preflight_error(model)
    if preflight_error:
        summary = {"status": "stopped", "stop_reason": preflight_error, "calls": 0}
        (output_dir / "json_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 1
    adapter = _make_adapter(model, args.max_calls, args.max_cost, args.thinking)
    calls_path = output_dir / "json_calls.jsonl"
    all_records: list[dict[str, Any]] = []
    stop_reason: str | None = None
    for case in args.cases.split(","):
        for record in run_case(adapter, model, case, args.max_parser_corrections):
            record.update({"model_key": args.model, "model_id": model.model_id, "thinking": args.thinking})
            _jsonl(calls_path, record)
            all_records.append(record)
        last = all_records[-1]
        if last["hard_stop"] or not last["ok"]:
            stop_reason = last["error"] or "parser correction did not recover"
            break
    first = [r for r in all_records if r["retry_count"] == 0]
    correction_records = [r for r in all_records if r["retry_count"] > 0]
    summary = {
        "status": "pass" if stop_reason is None and len({r["case"] for r in all_records}) == len(args.cases.split(",")) else "stopped",
        "stop_reason": stop_reason, "model_key": args.model, "model_id": model.model_id, "calls": len(all_records),
        "first_pass_rate": sum(r["ok"] for r in first) / len(first) if first else 0,
        "eventual_pass_rate": sum(any(x["ok"] for x in all_records if x["case"] == c) for c in args.cases.split(",")) / len(args.cases.split(",")),
        "cost_usd": round(sum(r["cost_usd"] for r in all_records), 6),
        "correction_count": len(correction_records),
        "correction_cost_usd": round(sum(r["cost_usd"] for r in correction_records), 6),
        "adapter_spent_usd": round(adapter.spent_usd, 6), "max_cost_usd": args.max_cost,
    }
    (output_dir / "json_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "pass" and summary["eventual_pass_rate"] == 1 else 1


def _latency_summary(rows: list[dict[str, Any]]) -> dict[str, float]:
    values = sorted(row["latency_ms"] for row in rows)
    if not values:
        return {"mean": 0, "median": 0, "max": 0, "p95": 0}
    return {
        "mean": round(statistics.mean(values), 2),
        "median": round(statistics.median(values), 2),
        "max": values[-1],
        "p95": values[math.ceil(len(values) * 0.95) - 1],
    }


def _comparison_model_summary(rows: list[dict[str, Any]], cases: list[str], repeats: int,
                              state: dict[str, Any]) -> dict[str, Any]:
    first = [row for row in rows if row["retry_count"] == 0]
    logical_ids = sorted({row["logical_case_id"] for row in rows})
    successful_ids = {
        row["logical_case_id"] for row in rows if row["ok"]
    }
    total_cost = sum(row["cost_usd"] for row in rows)
    by_case: dict[str, Any] = {}
    for case in cases:
        case_rows = [row for row in rows if row["case"] == case]
        case_first = [row for row in case_rows if row["retry_count"] == 0]
        case_ids = {row["logical_case_id"] for row in case_rows}
        case_successes = {row["logical_case_id"] for row in case_rows if row["ok"]}
        cost = sum(row["cost_usd"] for row in case_rows)
        by_case[case] = {
            "logical_cases": len(case_ids),
            "first_pass_rate": round(sum(row["ok"] for row in case_first) / len(case_first), 4) if case_first else 0,
            "eventual_pass_rate": round(len(case_successes) / len(case_ids), 4) if case_ids else 0,
            "correction_count": sum(row["retry_count"] > 0 for row in case_rows),
            "cost_usd": round(cost, 6),
            "cost_usd_per_successful_json": round(cost / len(case_successes), 6) if case_successes else None,
        }
    return {
        "status": state["status"], "stop_reason": state["stop_reason"],
        "expected_logical_cases": len(cases) * repeats,
        "logical_cases": len(logical_ids), "calls": len(rows),
        "first_pass_rate": round(sum(row["ok"] for row in first) / len(first), 4) if first else 0,
        "eventual_pass_rate": round(len(successful_ids) / len(logical_ids), 4) if logical_ids else 0,
        "successful_json_count": len(successful_ids),
        "correction_count": sum(row["retry_count"] > 0 for row in rows),
        "correction_cost_usd": round(sum(row["cost_usd"] for row in rows if row["retry_count"] > 0), 6),
        "length_rate": round(sum(str(row.get("finish_reason", "")).lower() == "length" for row in first) / len(first), 4) if first else 0,
        "transport_retry_count": sum(row["transport_retry_count"] for row in rows),
        "latency_ms": _latency_summary(rows),
        "tokens": {name: sum(row[name] for row in rows) for name in (
            "input_tokens", "output_tokens", "total_tokens", "thinking_diff", "reasoning_tokens",
        )},
        "cost_usd_total": round(total_cost, 6),
        "cost_usd_per_logical_call": round(total_cost / len(logical_ids), 6) if logical_ids else None,
        "cost_usd_per_successful_json": round(total_cost / len(successful_ids), 6) if successful_ids else None,
        "returned_models": sorted({row["response_model"] for row in rows if row["response_model"]}),
        "valid_action_emotion_count": len(successful_ids),
        "cases": by_case,
    }


def _load_compare_records(output_dir: Path) -> list[dict[str, Any]]:
    path = output_dir / "compare_calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def run_compare(args: argparse.Namespace, output_dir: Path) -> int:
    """Phase 3: interleaved, fixed-input comparison with per-model hard stops."""
    keys = args.models.split(",")
    cases = args.cases.split(",")
    models = {key: _effective_model(key, args.max_tokens) for key in keys}
    preflight_errors = {
        key: error for key, model in models.items()
        if (error := _phase3_preflight_error(key, model, args.thinking)) is not None
    }
    if preflight_errors:
        summary = {"status": "stopped", "global_stop_reason": "pricing/thinking preflight failed",
                   "preflight_errors": preflight_errors, "models": {}}
        (output_dir / "compare_summary.json").write_text(
            json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps(summary, ensure_ascii=False))
        return 1

    # A half-budget and half-call allocation enforces the shared hard cap even if one
    # model is stopped early.  48 global sends means at most 24 per model.
    per_model_budget = args.max_cost / len(keys)
    per_model_calls = args.max_calls // len(keys)
    resuming = getattr(args, "resume", False)
    records = _load_compare_records(output_dir) if resuming else []
    existing_cost = {key: sum(row["cost_usd"] for row in records if row["model_key"] == key) for key in keys}
    existing_calls = {key: sum(row["model_key"] == key for row in records) for key in keys}
    adapters = {
        key: _make_adapter(
            models[key], per_model_calls - existing_calls[key],
            max(0.0, per_model_budget - existing_cost[key]), args.thinking,
        )
        for key in keys
    }
    states = {key: {"status": "running", "stop_reason": None} for key in keys}
    complete_ids = set()
    for row in records:
        if row["ok"] or row["hard_stop"] or row["retry_count"] >= args.max_parser_corrections:
            complete_ids.add(row["logical_case_id"])
            if row["hard_stop"] or not row["ok"]:
                states[row["model_key"]] = {"status": "stopped", "stop_reason": row["error"] or "parser correction did not recover"}
    global_stop_reason: str | None = None
    for repeat in range(1, args.repeats + 1):
        # Alternate the first model every repeat to reduce cold-start/time bias.
        ordered_keys = keys if repeat % 2 else list(reversed(keys))
        for case in cases:
            for key in ordered_keys:
                if states[key]["status"] != "running":
                    continue
                logical_case_id = f"{key}:r{repeat}:{case}"
                if logical_case_id in complete_ids:
                    continue
                remaining = args.max_calls - len(records)
                if remaining < 1:
                    global_stop_reason = "shared max-calls budget exhausted"
                    break
                allowed_corrections = min(args.max_parser_corrections, remaining - 1)
                case_records = run_case(adapters[key], models[key], case, allowed_corrections)
                for record in case_records:
                    record.update({
                        "model_key": key, "model_id": models[key].model_id, "thinking": args.thinking,
                        "repeat": repeat, "logical_case_id": logical_case_id,
                    })
                    _jsonl(output_dir / "compare_calls.jsonl", record)
                    records.append(record)
                last = case_records[-1]
                if last["hard_stop"] or not last["ok"]:
                    states[key] = {"status": "stopped", "stop_reason": last["error"] or "parser correction did not recover"}
            if global_stop_reason:
                break
        if global_stop_reason:
            break

    for key, state in states.items():
        if state["status"] == "running":
            state["status"] = "completed"
    summary: dict[str, Any] = {
        "status": "pass" if global_stop_reason is None and all(
            state["status"] == "completed" for state in states.values()
        ) else "stopped",
        "global_stop_reason": global_stop_reason,
        "comparison_policy": {
            "thinking": args.thinking, "max_tokens": args.max_tokens,
            "transport_retry_count": 0, "sampling_parameters_sent": False,
            "fixed_input": True, "interleaved_models": True,
            "shared_max_calls": args.max_calls, "shared_max_cost_usd": args.max_cost,
            "per_model_max_calls": per_model_calls, "per_model_max_cost_usd": per_model_budget,
            "resumed": resuming, "calls_before_resume": sum(existing_calls.values()),
        },
        "models": {
            key: _comparison_model_summary(
                [row for row in records if row["model_key"] == key], cases, args.repeats, states[key],
            ) for key in keys
        },
    }
    old = summary["models"].get("M3", {}).get("cost_usd_per_logical_call")
    new = summary["models"].get("M3_G37_EVAL", {}).get("cost_usd_per_logical_call")
    summary["g37_cost_delta_vs_m3_usd_per_logical_call"] = round(new - old, 6) if old is not None and new is not None else None
    summary["g37_cost_reduction_pct_vs_m3"] = round((old - new) / old * 100, 2) if old and new is not None else None
    (output_dir / "compare_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if summary["status"] == "pass" else 1


def run_game(args: argparse.Namespace, output_dir: Path) -> int:
    model = _effective_model(args.model, args.max_tokens)
    config = GameConfig.baseline_v1_s2(num_players=args.random_bots + 1)
    # 12R設定は保持し、R3のReflection後にGameの既存stop_after_roundで安全停止する。
    event_logger = EventLogger(output_path=output_dir / "game01_events.jsonl")
    llm_logger = LLMLogger(output_dir / "llm_logs", game_id="game01_P01")
    raw = create_adapter(model, max_retries=0, allow_temperature_fallback=False)
    strict = BudgetedAdapter(raw, model, args.max_calls, args.per_player_cost_cap,
                             args.max_tokens, _request_options(model, args.thinking))
    llm_agent = LLMAgent("P01", model, strict, llm_logger, config)
    agents: dict[str, Any] = {"P01": llm_agent}
    for i in range(args.random_bots):
        agents[f"P{i + 2:02d}"] = RandomBot(seed=args.seed * 100 + i)
    budget = GameCostBudget(args.per_player_cost_cap, args.game_cost_cap, event_logger,
                            abort_on_block=args.abort_on_budget_block)
    error = None
    try:
        result = Game(config=config, agents=agents, seed=args.seed, logger=event_logger,
                      cost_budget=budget, stop_after_round=args.rounds).run()
    except Exception as e:
        result = None
        error = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        llm_logger.save()
        event_logger.save_jsonl(output_dir / "game01_events.jsonl")
    phases = [entry.get("phase") for entry in llm_logger.entries]
    summary = {
        "model_key": args.model, "model_id": model.model_id, "error": error,
        "stopped_after_round": args.rounds, "game_completed": result is not None,
        "calls": strict.calls_made, "cost_usd": round(strict.spent_usd, 6),
        "game_cost_usd": round(budget.game_spent_usd, 6), "auto_commit_count": llm_agent.auto_commit_count,
        "valid_json_count": llm_agent.valid_json_count,
        "parse_corrections": llm_agent.negotiation_correction_count + llm_agent.commit_correction_count,
        "phases_seen": phases, "memory_rounds": [m["round"] for m in llm_agent.memory_history],
    }
    (output_dir / "game_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if error is None and llm_agent.auto_commit_count == 0 else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Gemini 3.7 Flash evaluation (explicit API calls only)")
    parser.add_argument("command", choices=("smoke", "json", "compare", "game"))
    parser.add_argument("--out", default=str(DEFAULT_OUT))
    parser.add_argument("--run-id", default="")
    parser.add_argument("--resume", action="store_true", help="interrupted compare runの未完了ケースだけを続行")
    parser.add_argument("--max-tokens", type=int, default=2000)
    parser.add_argument("--thinking", choices=("low", "medium", "high"), default="low")
    parser.add_argument("--max-calls", type=int, default=8)
    parser.add_argument("--calls", type=int, default=3, help="smoke logical calls (3〜5)")
    parser.add_argument("--max-cost", type=float, default=0.30)
    parser.add_argument("--max-parser-corrections", type=int, default=1)
    parser.add_argument("--cases", default=",".join(CASE_NAMES))
    parser.add_argument("--model", default="M3_G37_EVAL")
    parser.add_argument("--models", default="M3,M3_G37_EVAL")
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--rounds", type=int, default=3)
    parser.add_argument("--random-bots", type=int, default=11)
    parser.add_argument("--seed", type=int, default=901)
    parser.add_argument("--per-player-cost-cap", type=float, default=0.30)
    parser.add_argument("--game-cost-cap", type=float, default=0.45)
    parser.add_argument("--abort-on-budget-block", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.max_tokens <= 0 or args.max_calls <= 0 or args.max_cost <= 0 or args.rounds < 1:
        raise SystemExit("token/call/cost/rounds は正数で指定してください")
    if args.command == "smoke" and (args.calls < 3 or args.calls > 5 or args.max_calls < args.calls):
        raise SystemExit("smoke の --calls は3〜5、--max-callsは--calls以上で指定してください")
    cases = args.cases.split(",")
    if any(case not in CASE_NAMES for case in cases):
        raise SystemExit(f"cases は {', '.join(CASE_NAMES)} のみです")
    if args.command == "compare":
        model_keys = args.models.split(",")
        if set(model_keys) != set(SUPPORTED_KEYS) or len(model_keys) != len(SUPPORTED_KEYS):
            raise SystemExit("compare の models は M3,M3_G37_EVAL を各1回指定してください")
        initial_calls = len(cases) * args.repeats * len(model_keys)
        if args.repeats < 1 or args.max_calls < initial_calls:
            raise SystemExit("compare は全初回送信数以上の --max-calls を指定してください")
    run_id = args.run_id or datetime.now().strftime("run_%Y%m%d_%H%M%S")
    output_dir = Path(args.out) / run_id
    if args.resume:
        if args.command != "compare" or not output_dir.is_dir():
            raise SystemExit("--resume は既存の compare run-id にのみ指定できます")
    else:
        output_dir.mkdir(parents=True, exist_ok=False)
    if not args.resume:
        manifest = vars(args) | {"run_id": run_id, "created_at": datetime.now(timezone.utc).isoformat()}
        (output_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.command == "smoke":
        return run_smoke(args, output_dir)
    if args.command == "json":
        return run_json(args, output_dir)
    if args.command == "compare":
        return run_compare(args, output_dir)
    return run_game(args, output_dir)


if __name__ == "__main__":
    raise SystemExit(main())
