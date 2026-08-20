"""
18モデル (H1〜H6・M1〜M6・L1〜L6) 段階的スモークテスト基盤

「疎通 → 談合カードJSON互換 → ゲーム経路統合」を4フェーズに分割し、
各フェーズの間で人間が結果を確認して止められるようにする。
18モデル全体で総額$1未満を目標とする（コストガード4層）。

フェーズ:
    0: 棚卸し（APIコールなし・$0）
    1: 超軽量疎通（1モデル1コール）
    2: 談合カードJSON互換（1モデル1コール、Phase1通過分のみ）
    3: ミニゲーム統合テスト（1ラウンドのみ、Phase1+2通過分のみ）

実行順: 弱(L)6 → 中(M)6 → 強(H)6。各ティア内は input+output 単価昇順
（＝最高額モデルが常に最後）。

使用方法:
    uv run python scripts/model_matrix.py --phase 0
    uv run python scripts/model_matrix.py --phase 1 --dry-run
    uv run python scripts/model_matrix.py --phase 1 --tier weak,mid,strong --max-cost 0.08
    uv run python scripts/model_matrix.py --phase 2 --max-cost 0.22
    uv run python scripts/model_matrix.py --phase 3 --tier weak,mid --max-cost 0.20
    uv run python scripts/model_matrix.py --phase 3 --tier strong --max-cost 0.45 --max-cost-per-model 0.10
    uv run python scripts/model_matrix.py --report

state.json のみが正。各モデル処理後に os.replace() で原子的に書き換える
（kill -9 されても最大1件しか失わない）。

MODEL_REGISTRYはモデル別の送信パラメータとPhase 2 output上限を持つ。Phase 3の
max_tokensクランプは BudgetedAdapter 側でコール単位に行う。llm/adapters.pyの
complete()戻り値シグネチャ (text, usage) は変更しない。
"""

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from llm.models import MODEL_REGISTRY, ModelInfo, estimate_cost, get_model
from llm.costing import usage_cost as _usage_cost
from llm.adapters import create_adapter, AdapterError
from llm.response_parser import extract_json, parse_response, ParseError
from llm.prompt_builder import build_system_prompt
from llm.llm_agent import LLMAgent
from llm.llm_logger import LLMLogger
from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from bots.random_bot import RandomBot

# model_smoke.py は無改変で import 再利用する（プロンプト・ヒューリスティックの単一の真実）
from scripts.model_smoke import (
    PING_SYSTEM as SMOKE_PING_SYSTEM,
    PING_USER as SMOKE_PING_USER,
    GAME_USER,
    _looks_like_pure_json,
)
from llm.costing import worst_case_cost as _worst_case_cost
from llm.phase2_schema import (build_phase2_response_schema,
                               build_h1_phase2_light_schema,
                               normalize_h1_phase2_transport,
                               structured_schema_input_reserve_tokens,
                               validate_h1_phase2_light_schema,
                               validate_phase2_schema_complexity)


# --- 対象キー: H1〜H6 / M1〜M6 / L1〜L6 の18コアキー（L7は負荷試験枠のため対象外） ---
CORE_18_KEYS = [f"{t}{i}" for t in ("H", "M", "L") for i in range(1, 7)]

TIER_ALIASES = {
    "weak": "L", "mid": "M", "strong": "H",
    "l": "L", "m": "M", "h": "H",
    "L": "L", "M": "M", "H": "H",
}
EXEC_TIER_ORDER = ("L", "M", "H")  # 弱→中→強（各ティア内は単価昇順）

DEFAULT_OUT_DIR = Path("logs/model_matrix")
DEFAULT_MAX_COST_TOTAL = 1.00
DEFAULT_SEED = 901

PHASE_DEFAULTS: dict[int, dict[str, Any]] = {
    1: {"max_cost": 0.08, "max_cost_per_model": 0.02, "max_calls": 40, "max_tokens": 64, "retries": 1},
    2: {"max_cost": 0.22, "max_cost_per_model": 0.03, "max_calls": 24, "max_tokens": 400, "retries": 0},
    3: {"max_cost": 0.20, "max_cost_per_model": 0.03, "max_calls": 96, "max_tokens": 500, "retries": 0},
}

# --- Phase 1 疎通テスト用プロンプト（model_smoke.py と共用） ---
PING_SYSTEM = SMOKE_PING_SYSTEM
PING_USER = SMOKE_PING_USER

# --- Phase 2 談合カードJSON互換テスト用プロンプト（model_smoke.py の GAME_USER を共用） ---
GAME2_USER = GAME_USER
H1_PHASE2_TRANSPORT_USER = """ラウンド3 / 交渉フェイズ（巡2）

市場: M01=84万円, M02=84万円, M03=84万円
あなたの状態: 現金300万円。手札: HIGH_CARD, ONE_PAIR, TWO_PAIR, THREE_OF_A_KIND, STRAIGHT, FLUSH
他プレイヤー: P02「M01は均等分配にしませんか」; P05「私はM03を狙います」

次の19 fieldだけを持つJSON objectを返してください。action_typeは交渉actionを1つ選び、emotionは感情を1つ選びます。
action_type, emotion, to, message, amount, with_players_json, terms_json, contract_id, bounty_type, condition_type, condition_target_player, round_num, anonymous, beneficiary, bounty_id, give_card, receive_card, cash_amount, trade_id。
未使用stringは""、integerは0、anonymousはfalse、with_players_jsonとterms_jsonは空配列を表す"[]"にしてください。
bounty_type/condition_typeの未使用値は""です。with_players_jsonは文字列配列をJSON文字列化し、terms_jsonはcanonical term dict配列をJSON文字列化してください。"""


class BudgetError(Exception):
    """予算超過で処理を中断すべきときに投げる（プロセス終了はmain側で行う）"""


# ============================================================
# state.json 管理
# ============================================================

def _new_run_id() -> str:
    return "run_" + datetime.now().strftime("%Y%m%d_%H%M%S")


def _run_dir(out_dir: Path, run_id: str) -> Path:
    return out_dir / run_id


def _latest_run_path(out_dir: Path) -> Path:
    return out_dir / "LATEST_RUN"


def _write_latest_run(out_dir: Path, run_id: str) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    _latest_run_path(out_dir).write_text(run_id + "\n", encoding="utf-8")


def _read_latest_run(out_dir: Path) -> str | None:
    p = _latest_run_path(out_dir)
    if not p.exists():
        return None
    return p.read_text(encoding="utf-8").strip() or None


def _init_state(run_id: str, max_cost_total: float, keys: list[str]) -> dict[str, Any]:
    models: dict[str, Any] = {}
    for order, key in enumerate(keys):
        info = MODEL_REGISTRY[key]
        models[key] = {
            "tier": info.tier, "order": order,
            "provider": info.provider, "model_id": info.model_id,
            "phase1": {"status": "pending"},
            "phase2": {"status": "pending"},
            "phase3": {"status": "pending"},
        }
    return {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "budget": {"max_cost_total": max_cost_total, "spent_usd": 0.0, "calls_made": 0},
        "phases": {"0": {"status": "pending"}, "1": {"status": "pending"},
                   "2": {"status": "pending"}, "3": {"status": "pending"}},
        "models": models,
    }


def _load_state(run_dir: Path) -> dict[str, Any]:
    path = run_dir / "state.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _save_state(run_dir: Path, state: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / "state.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _ensure_models_in_state(state: dict[str, Any], keys: list[str]) -> None:
    """state.json に未登録のキーがあれば末尾に追加する（--models で新キーを後から指定した場合）"""
    existing = state["models"]
    next_order = max((m.get("order", -1) for m in existing.values()), default=-1) + 1
    for key in keys:
        if key in existing:
            continue
        info = MODEL_REGISTRY[key]
        existing[key] = {
            "tier": info.tier, "order": next_order,
            "provider": info.provider, "model_id": info.model_id,
            "phase1": {"status": "pending"},
            "phase2": {"status": "pending"},
            "phase3": {"status": "pending"},
        }
        next_order += 1


def _append_jsonl(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")
        f.flush()


# ============================================================
# 対象モデル選定（弱→中→強、各ティア内は単価昇順）
# ============================================================

def ordered_keys(tiers: list[str] | None, explicit: list[str] | None) -> list[str]:
    if explicit:
        unknown = [k for k in explicit if k not in MODEL_REGISTRY]
        if unknown:
            raise ValueError(f"不明なキー: {unknown}")
        return explicit
    wanted_tiers = set(tiers) if tiers else set(EXEC_TIER_ORDER)
    result: list[str] = []
    for t in EXEC_TIER_ORDER:
        if t not in wanted_tiers:
            continue
        entries = [(k, MODEL_REGISTRY[k]) for k in CORE_18_KEYS if MODEL_REGISTRY[k].tier == t]
        entries.sort(key=lambda kv: kv[1].input_price + kv[1].output_price)
        result.extend(k for k, _ in entries)
    return result


def _parse_tiers(raw: str) -> list[str]:
    if not raw or raw == "all":
        return list(EXEC_TIER_ORDER)
    out = []
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if part not in TIER_ALIASES:
            raise ValueError(f"不明なティア指定: {part!r}（weak/mid/strong または H/M/L）")
        out.append(TIER_ALIASES[part])
    return out


# ============================================================
# Phase 0: 棚卸し（APIコールなし）
# ============================================================

def run_phase0(run_dir: Path, state: dict[str, Any]) -> None:
    lines = ["# Phase 0 棚卸し（APIコールなし）\n"]
    lines.append(f"- 実行日時: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    lines.append(f"- 登録数: {len(MODEL_REGISTRY)}（コア18 + L7負荷試験枠等）\n")
    lines.append("| Vendor | Tier | key | model_id | adapter | endpoint | env_key | thinking | timeout | max_tokens | in$ | out$ | cached$ | env設定 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for key, info in MODEL_REGISTRY.items():
        thinking = ""
        if info.extra_params and "thinking" in info.extra_params:
            thinking = str(info.extra_params["thinking"])
        endpoint = info.base_url or "(default)"
        env_set = "✓" if os.environ.get(info.env_key) else "—"
        lines.append(
            f"| {info.provider} | {info.tier or '?'} | {key} | `{info.model_id}` | {info.adapter_type} | "
            f"{endpoint} | {info.env_key} | {thinking} | {info.timeout_seconds}s | {info.max_tokens or '-'} | "
            f"{info.input_price} | {info.output_price} | {info.cached_input_price or '-'} | {env_set} |"
        )
    lines.append("")
    unregistered = [k for k in CORE_18_KEYS if k not in MODEL_REGISTRY]
    if unregistered:
        lines.append(f"## 未登録キー（Phase1対象外）\n\n{', '.join(unregistered)}\n")
    else:
        lines.append("## 未登録キー\n\nなし（コア18キーは全て登録済み）\n")

    run_dir.mkdir(parents=True, exist_ok=True)
    out_path = run_dir / "phase0_inventory.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    state["phases"]["0"] = {"status": "done", "finished_at": datetime.now(timezone.utc).isoformat()}
    _save_state(run_dir, state)
    print(f"Phase 0 完了。棚卸し表: {out_path}")


# ============================================================
# 予算ガード（層2: コール毎事前チェック）
# ============================================================

def _check_budget_before_call(
    state: dict[str, Any], phase_spent: float, phase_max_cost: float,
    max_cost_total: float, per_model_spent: float, per_model_cap: float,
    calls_so_far: int, max_calls: int, worst: float,
) -> None:
    if calls_so_far >= max_calls:
        raise BudgetError(f"max_calls({max_calls})に到達")
    if phase_spent + worst > phase_max_cost:
        raise BudgetError(f"このフェーズの上限(${phase_max_cost:.2f})超過見込み")
    if state["budget"]["spent_usd"] + worst > max_cost_total:
        raise BudgetError(f"通算上限(${max_cost_total:.2f})超過見込み")
    if per_model_spent + worst > per_model_cap:
        raise BudgetError(f"1モデル上限(${per_model_cap:.2f})超過見込み")


# ============================================================
# requested/returned モデル名の一致判定
# ============================================================

def _norm_model_name(name: str) -> str:
    """比較用にモデル名を正規化する（小文字化・前後空白除去・先頭 models/ 除去）"""
    n = name.strip().lower()
    if n.startswith("models/"):
        n = n[len("models/"):]
    return n


def _model_match(requested: str | None, returned: str | None) -> str:
    """
    requested（MODEL_REGISTRYの model_id）と returned（API応答の model）を比較する。

    Returns:
        "unknown"  : returned が取得不能（None/空）
        "match"    : 正規化後に完全一致
        "alias"    : 一方が他方のprefix（例: gpt-4.1-mini → gpt-4.1-mini-2025-04-14
                     のような、エイリアス→日付スナップショット解決）
        "mismatch" : 上記以外（プロバイダ側の別モデルへのリダイレクト等、要注意）
    """
    if not returned:
        return "unknown"
    if not requested:
        return "unknown"
    r = _norm_model_name(requested)
    a = _norm_model_name(returned)
    if r == a:
        return "match"
    if r.startswith(a) or a.startswith(r):
        return "alias"
    return "mismatch"


_MODEL_MATCH_MARK = {"match": "✓", "alias": "≈", "mismatch": "⚠", "unknown": "?"}


# ============================================================
# usage → コスト計算（cache割引・thinking差分課金を反映）
# ============================================================

def _legacy_usage_cost(model: ModelInfo, usage: dict[str, Any]) -> float:
    """
    usage 辞書からコストを算出する。

    estimate_cost() は OpenAI 系の慣習（input_tokens が cache_read を内包）を前提とするが、
    Anthropic は input_tokens と cache_read_input_tokens を別建てで返すため、
    ここで OpenAI 慣習へ正規化してから渡す（estimate_cost 自体は変更しない）。
    reasoning_tokens は estimate_cost 側が total-input-output の差分で扱うため、
    ここでは渡さない（二重計上防止）。
    """
    input_tokens = usage.get("input_tokens", 0) or 0
    output_tokens = usage.get("output_tokens", 0) or 0
    cache_read = usage.get("cache_read_input_tokens", 0) or 0
    total_tokens = usage.get("total_tokens", 0) or 0
    if model.adapter_type == "anthropic":
        input_tokens += cache_read
        if total_tokens:
            total_tokens += cache_read
    return estimate_cost(
        model, input_tokens, output_tokens,
        cache_read_input_tokens=cache_read,
        total_tokens=total_tokens,
    )


# ============================================================
# Phase 1: 超軽量疎通
# ============================================================

def _call_once_phase1(model: ModelInfo, max_tokens: int, retries: int) -> dict[str, Any]:
    # Phase 1 はリトライ責務をこの関数の外側ループ（retries）に一本化する。
    # アダプタ内部リトライと OpenAI SDK 内部リトライは max_retries=0 で常に無効化し、
    # 1試行 = HTTP 1回 を保証する（--retries N → HTTP最大 N+1 回。三重リトライにしない）。
    adapter = create_adapter(model, max_retries=0)
    last_err: str | None = None
    for attempt in range(retries + 1):
        try:
            start = time.time()
            text, usage = adapter.complete(
                system=PING_SYSTEM,
                messages=[{"role": "user", "content": PING_USER}],
                max_tokens=max_tokens,
                temperature=0.0,
            )
            latency_ms = round((time.time() - start) * 1000)
            ok = len(text.strip()) > 0
            data = extract_json(text) if ok else None
            return {
                "ok": ok, "error": None, "latency_ms": latency_ms,
                "usage": usage, "text_sample": text.strip()[:200],
                "json_extract_ok": data is not None,
                "response_model": usage.get("response_model"),
            }
        except (AdapterError, Exception) as e:
            last_err = f"{type(e).__name__}: {str(e)[:300]}"
            if attempt < retries:
                continue
    return {
        "ok": False, "error": last_err, "latency_ms": 0,
        "usage": {"input_tokens": 0, "output_tokens": 0},
        "text_sample": "", "json_extract_ok": False,
        "response_model": None,
    }


def run_phase1(run_dir: Path, state: dict[str, Any], keys: list[str],
                max_cost: float, max_cost_total: float, max_cost_per_model: float,
                max_calls: int, max_tokens: int, retries: int,
                resume: bool, force: bool, dry_run: bool) -> str:
    _ensure_models_in_state(state, keys)
    calls_path = run_dir / "phase1_calls.jsonl"
    phase_spent = 0.0
    calls_so_far = 0
    aborted = False

    targets = []
    for key in keys:
        m = state["models"][key]
        if resume and not force and m["phase1"].get("status") in ("pass", "fail"):
            continue
        targets.append(key)

    total_worst = 0.0
    for key in targets:
        info = MODEL_REGISTRY[key]
        total_worst += _worst_case_cost(info, PING_SYSTEM, PING_USER, max_tokens) * (1 + retries)

    print(f"Phase 1 対象: {len(targets)}モデル、最悪見積コスト ${total_worst:.4f}")
    if dry_run:
        for key in targets:
            print(f"  [dry-run] {key} {MODEL_REGISTRY[key].provider} {MODEL_REGISTRY[key].name}")
        return "dry_run"

    for key in targets:
        info = MODEL_REGISTRY[key]
        worst = _worst_case_cost(info, PING_SYSTEM, PING_USER, max_tokens) * (1 + retries)
        try:
            _check_budget_before_call(
                state, phase_spent, max_cost, max_cost_total, 0.0, max_cost_per_model,
                calls_so_far, max_calls, worst,
            )
        except BudgetError as e:
            print(f"{key}: 予算中断 ({e})")
            state["models"][key]["phase1"] = {"status": "skipped_budget", "reason": str(e)}
            aborted = True
            _save_state(run_dir, state)
            continue

        if not os.environ.get(info.env_key):
            print(f"{key} ({info.provider} {info.name})... SKIP（キー未設定: {info.env_key}）")
            state["models"][key]["phase1"] = {"status": "skip", "reason": f"キー未設定: {info.env_key}"}
            _save_state(run_dir, state)
            continue

        print(f"{key} ({info.provider} {info.name})...", end=" ", flush=True)
        r = _call_once_phase1(info, max_tokens, retries)
        usage = r["usage"]
        cost = _usage_cost(info, usage)
        phase_spent += cost
        state["budget"]["spent_usd"] += cost
        state["budget"]["calls_made"] += 1
        calls_so_far += 1

        response_model = r.get("response_model")
        match = _model_match(info.model_id, response_model)
        status = "pass" if r["ok"] else "fail"
        record = {"key": key, "model_id": info.model_id, "requested_model": info.model_id,
                   "timestamp": datetime.now(timezone.utc).isoformat(),
                   "cost_usd": round(cost, 6), "model_match": match, **r}
        _append_jsonl(calls_path, record)
        state["models"][key]["phase1"] = {
            "status": status, "latency_ms": r["latency_ms"], "cost_usd": round(cost, 6),
            "error": r["error"], "json_extract_ok": r["json_extract_ok"],
            "response_model": response_model, "model_match": match,
        }
        _save_state(run_dir, state)
        match_note = "" if match in ("match", "unknown") else f" → {response_model}({match})"
        print(f"{status} ({r['latency_ms']}ms) ${cost:.4f}{match_note} | "
              f"累計${state['budget']['spent_usd']:.4f}/${max_cost_total:.2f}")

    _generate_phase1_report(run_dir, state)
    state["phases"]["1"] = {
        "status": "aborted_budget" if aborted else "done",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(run_dir, state)
    return "aborted_budget" if aborted else "done"


def _generate_phase1_report(run_dir: Path, state: dict[str, Any]) -> None:
    lines = ["# Phase 1 疎通レポート\n"]
    lines.append("| key | provider | requested | returned | 一致 | 状態 | latency | cost | 備考 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for key, m in sorted(state["models"].items(), key=lambda kv: kv[1]["order"]):
        p1 = m.get("phase1", {})
        status = p1.get("status", "pending")
        latency = f"{p1.get('latency_ms', 0)}ms" if p1.get("latency_ms") else "—"
        cost = f"${p1.get('cost_usd', 0):.4f}" if p1.get("cost_usd") else "—"
        note = (p1.get("error") or p1.get("reason") or "")[:80]
        returned = p1.get("response_model") or "—"
        match_mark = _MODEL_MATCH_MARK.get(p1.get("model_match", "unknown"), "—") if "model_match" in p1 else "—"
        lines.append(
            f"| {key} | {m['provider']} | `{m['model_id']}` | `{returned}` | {match_mark} | "
            f"{status} | {latency} | {cost} | {note} |"
        )
    (run_dir / "phase1_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================
# Phase 2: 談合カードJSON互換
# ============================================================

def _is_h1_phase2_model(model: ModelInfo) -> bool:
    return model.model_id == MODEL_REGISTRY["H1"].model_id


def _phase2_user_content(model: ModelInfo) -> str:
    """Return the exact model-specific Phase 2 user content used for a call."""
    return H1_PHASE2_TRANSPORT_USER if _is_h1_phase2_model(model) else GAME2_USER


def _phase2_schema_for_model(model: ModelInfo) -> dict[str, Any] | None:
    if _is_h1_phase2_model(model):
        return build_h1_phase2_light_schema()
    if model.model_id in {MODEL_REGISTRY["M3"].model_id, MODEL_REGISTRY["H3"].model_id}:
        return build_phase2_response_schema()
    return None


def _phase2_request_options(model: ModelInfo) -> dict[str, Any]:
    """Return Phase-2-only options; never used by Phase 1/3 or games."""
    schema = _phase2_schema_for_model(model)
    if _is_h1_phase2_model(model):
        assert schema is not None
        validate_h1_phase2_light_schema(schema)
        return {"thinking": {"type": "disabled"}, "output_config": {"format": {"type": "json_schema", "schema": schema}}}
    if model.model_id in {MODEL_REGISTRY["M3"].model_id, MODEL_REGISTRY["H3"].model_id}:
        assert schema is not None
        validate_phase2_schema_complexity(schema)
        # Gemini 3.5 Flash supports minimal; Gemini 3.1 Pro Preview's lowest
        # supported setting is low.  Do not combine this OpenAI-compatible
        # control with Gemini thinking_level/thinking_budget controls.
        reasoning_effort = "minimal" if model.model_id == MODEL_REGISTRY["M3"].model_id else "low"
        return {"reasoning_effort": reasoning_effort, "response_format": {"type": "json_schema", "json_schema": {"name": "phase2_negotiation", "strict": True, "schema": schema}}}
    return {}


def _phase2_worst_case_cost(model: ModelInfo, system: str, max_tokens: int) -> float:
    options = _phase2_request_options(model)
    schema = _phase2_schema_for_model(model)
    reserve = structured_schema_input_reserve_tokens(schema) if options and schema else 0
    return _worst_case_cost(model, system, _phase2_user_content(model), max_tokens, input_reserve_tokens=reserve)


def _call_once_phase2(model: ModelInfo, max_tokens: int) -> dict[str, Any]:
    # Phase 2 の比較実験だけは、adapter/SDK retry と temperature fallback
    # による追加送信を抑止する。他Phaseおよび本戦の既定挙動は変更しない。
    adapter = create_adapter(
        _phase2_effective_model(model),
        max_retries=0,
        allow_temperature_fallback=False,
    )
    game_system = build_system_prompt("P01", GameConfig.baseline_v1_s2(num_players=18))
    try:
        start = time.time()
        complete_kwargs: dict[str, Any] = dict(
            system=game_system,
            messages=[{"role": "user", "content": _phase2_user_content(model)}],
            max_tokens=max_tokens,
            temperature=0.7,
        )
        options = _phase2_request_options(model)
        if options:
            complete_kwargs["request_options"] = options
        text, usage = adapter.complete(**complete_kwargs)
        latency_ms = round((time.time() - start) * 1000)
    except (AdapterError, Exception) as e:
        return {
            "ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}", "latency_ms": 0,
            "usage": {"input_tokens": 0, "output_tokens": 0}, "parse_ok": False,
            "pure_json": False, "emotion": None, "response_text": None,
            "response_model": None,
        }

    ok = len(text.strip()) > 0
    pure_json = _looks_like_pure_json(text) if ok else False
    parse_ok = False
    emotion = None
    parse_error = None
    if ok:
        data = extract_json(text)
        if data:
            parse_text = text
            try:
                if _is_h1_phase2_model(model):
                    canonical = normalize_h1_phase2_transport(data)
                    parse_text = json.dumps(canonical, ensure_ascii=False)
                strategy, action = parse_response(parse_text, "P01", "negotiation")
                parse_ok = True
                emotion = (strategy or {}).get("emotion") if isinstance(strategy, dict) else None
            except (ParseError, ValueError) as e:
                parse_error = e.correction_hint if hasattr(e, "correction_hint") else str(e)[:200]
        else:
            parse_error = "JSON抽出失敗"
    return {
        "ok": ok, "error": None, "latency_ms": latency_ms, "usage": usage,
        "parse_ok": parse_ok, "pure_json": pure_json, "emotion": emotion,
        "parse_error": parse_error, "text_sample": text.strip()[:300],
        # 有料の Phase 2 実験は parse 成否にかかわらず本文を完全保存する。
        # request header / secret はこの record に含めない。
        "response_text": text,
        "response_model": usage.get("response_model"),
    }


def _phase2_effective_max_tokens(model: ModelInfo, configured_max_tokens: int) -> int:
    """Phase 2だけで必要なモデル別出力上限を解決する。"""
    return model.phase2_max_tokens if model.phase2_max_tokens is not None else configured_max_tokens


def _phase2_effective_model(model: ModelInfo) -> ModelInfo:
    """Return a Phase-2-only ModelInfo copy when a timeout override is set."""
    if model.phase2_timeout_seconds is None:
        return model
    return replace(model, timeout_seconds=model.phase2_timeout_seconds)


def run_phase2(run_dir: Path, state: dict[str, Any], keys: list[str],
                max_cost: float, max_cost_total: float, max_cost_per_model: float,
                max_calls: int, max_tokens: int,
                resume: bool, force: bool, dry_run: bool, retry_failed: bool = False) -> str:
    if state["phases"]["1"]["status"] not in ("done", "aborted_budget"):
        print("Phase 1 が未完了です。先に --phase 1 を実行してください。")
        return "gate_error"

    passers = [k for k in keys if state["models"].get(k, {}).get("phase1", {}).get("status") == "pass"]
    targets = []
    for key in passers:
        m = state["models"][key]
        status = m["phase2"].get("status")
        if retry_failed and status != "fail":
            continue
        if not retry_failed and resume and not force and status in ("pass", "fail"):
            continue
        targets.append(key)

    calls_path = run_dir / "phase2_calls.jsonl"
    phase_spent = 0.0
    calls_so_far = 0
    aborted = False

    total_worst = sum(
        _phase2_worst_case_cost(MODEL_REGISTRY[k], build_system_prompt("P01", GameConfig.baseline_v1_s2(num_players=18)),
                          _phase2_effective_max_tokens(MODEL_REGISTRY[k], max_tokens))
        for k in targets
    )
    print(f"Phase 2 対象: {len(targets)}モデル（Phase1通過分）、最悪見積コスト ${total_worst:.4f}")
    if dry_run:
        for key in targets:
            print(f"  [dry-run] {key} {MODEL_REGISTRY[key].provider} {MODEL_REGISTRY[key].name}")
        return "dry_run"

    existing_attempts: dict[str, int] = {}
    if calls_path.exists():
        for line in calls_path.read_text(encoding="utf-8").splitlines():
            try:
                prior = json.loads(line)
            except json.JSONDecodeError:
                continue
            if key := prior.get("key"):
                existing_attempts[key] = existing_attempts.get(key, 0) + 1
    attempt_kind = "retry_failed" if retry_failed else ("force" if force else "initial")

    for key in targets:
        info = MODEL_REGISTRY[key]
        effective_max_tokens = _phase2_effective_max_tokens(info, max_tokens)
        game_system = build_system_prompt("P01", GameConfig.baseline_v1_s2(num_players=18))
        worst = _phase2_worst_case_cost(info, game_system, effective_max_tokens)
        try:
            _check_budget_before_call(
                state, phase_spent, max_cost, max_cost_total, 0.0, max_cost_per_model,
                calls_so_far, max_calls, worst,
            )
        except BudgetError as e:
            print(f"{key}: 予算中断 ({e})")
            state["models"][key]["phase2"] = {"status": "skipped_budget", "reason": str(e)}
            aborted = True
            _save_state(run_dir, state)
            continue

        print(f"{key} ({info.provider} {info.name})...", end=" ", flush=True)
        r = _call_once_phase2(info, effective_max_tokens)
        usage = r["usage"]
        # Phase 1 と同一の実測usageベース計算に統一（hidden thinking / cache割引 / Anthropic正規化）
        cost = _usage_cost(info, usage)
        phase_spent += cost
        state["budget"]["spent_usd"] += cost
        state["budget"]["calls_made"] += 1
        calls_so_far += 1

        status = "pass" if r["parse_ok"] else "fail"
        response_model = r.get("response_model")
        model_match = _model_match(info.model_id, response_model)
        record = {
            "key": key,
            "model_id": info.model_id,
            "requested_model": info.model_id,
            "model_match": model_match,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "attempt": existing_attempts.get(key, 0) + 1,
            "attempt_kind": attempt_kind,
            "max_tokens": effective_max_tokens,
            "cost_usd": round(cost, 6),
            **r,
        }
        _append_jsonl(calls_path, record)
        existing_attempts[key] = record["attempt"]
        state["models"][key]["phase2"] = {
            "status": status, "latency_ms": r["latency_ms"], "cost_usd": round(cost, 6),
            "error": r["error"] or r.get("parse_error"), "pure_json": r["pure_json"], "emotion": r["emotion"],
            "response_model": response_model, "model_match": model_match,
        }
        _save_state(run_dir, state)
        print(f"{status} ({r['latency_ms']}ms) ${cost:.4f} | 累計${state['budget']['spent_usd']:.4f}/"
              f"${max_cost_total:.2f}")

    _generate_phase2_report(run_dir, state)
    state["phases"]["2"] = {
        "status": "aborted_budget" if aborted else "done",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(run_dir, state)
    return "aborted_budget" if aborted else "done"


def _generate_phase2_report(run_dir: Path, state: dict[str, Any]) -> None:
    lines = ["# Phase 2 談合カードJSON互換レポート\n"]
    lines.append("| key | provider | model | 状態 | latency | cost | emotion | pure_json | 備考 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for key, m in sorted(state["models"].items(), key=lambda kv: kv[1]["order"]):
        p2 = m.get("phase2", {})
        status = p2.get("status", "pending")
        latency = f"{p2.get('latency_ms', 0)}ms" if p2.get("latency_ms") else "—"
        cost = f"${p2.get('cost_usd', 0):.4f}" if p2.get("cost_usd") else "—"
        note = (p2.get("error") or "")[:80] if p2.get("error") else ""
        lines.append(f"| {key} | {m['provider']} | {m['model_id']} | {status} | {latency} | {cost} | "
                     f"{p2.get('emotion', '') or ''} | {p2.get('pure_json', '')} | {note} |")
    (run_dir / "phase2_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================
# Phase 3: ミニゲーム統合テスト
# ============================================================

_PHASE3_EFFORT_CHOICES = ("low", "medium", "high", "xhigh", "max")


@dataclass(frozen=True)
class Phase3RuntimeOverrides:
    """Phase 3だけで使う非永続のrequest/runtime上書き。"""

    effort: str | None = None
    timeout_seconds: int | None = None


def _is_h1_phase3_effort_model(model: ModelInfo) -> bool:
    """Adaptive thinkingのeffortを送れるモデルを意図的にH1だけへ絞る。"""
    return (
        model.adapter_type == "anthropic"
        and model.model_id == MODEL_REGISTRY["H1"].model_id
    )


def _phase3_effective_model(model: ModelInfo, max_tokens: int,
                            timeout_seconds: int | None) -> ModelInfo:
    """Return a Phase-3-only copy carrying the actual request limits.

    LLMAgent resolves its output limit from ModelInfo, while BudgetedAdapter
    clamps that same call.  Keeping both on this copy makes an audit value such
    as 8000 describe the request that was actually sent, without mutating the
    registry used by other phases or normal games.
    """
    updates: dict[str, Any] = {"max_tokens": max_tokens}
    if timeout_seconds is not None:
        updates["timeout_seconds"] = timeout_seconds
    return replace(model, **updates)


def _phase3_request_options(model: ModelInfo, effort: str | None) -> dict[str, Any] | None:
    """Return the provider payload for an actually supported Phase-3 effort."""
    if effort is None or not _is_h1_phase3_effort_model(model):
        return None
    return {
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
    }

class BudgetedAdapter:
    """
    LLMAgentへ渡すアダプタラッパ。MODEL_REGISTRY（ModelInfo）は一切書き換えず、
    コール単位でmax_tokensをクランプし、コール数/コストの上限に達したら
    AdapterErrorを投げる（LLMAgent._call_llmはこれを捕捉して空応答へ退避する）。
    """

    def __init__(self, inner: Any, model_info: ModelInfo, max_calls: int, max_cost: float,
                 max_tokens_cap: int, request_options: dict[str, Any] | None = None) -> None:
        self._inner = inner
        self._model_info = model_info
        self.max_calls = max_calls
        self.max_cost = max_cost
        self.max_tokens_cap = max_tokens_cap
        self._request_options = request_options
        self.calls_made = 0
        self.spent_usd = 0.0

    def complete(self, system: str, messages: list[dict[str, str]],
                 max_tokens: int = 1000, temperature: float = 0.7) -> tuple[str, dict[str, int]]:
        if self.calls_made >= self.max_calls:
            raise AdapterError(f"BudgetedAdapter: max_calls({self.max_calls})到達")
        clamped = min(max_tokens, self.max_tokens_cap)
        worst = _worst_case_cost(self._model_info, system,
                                  " ".join(m.get("content", "") for m in messages), clamped)
        if self.spent_usd + worst > self.max_cost:
            raise AdapterError(f"BudgetedAdapter: 予算上限(${self.max_cost:.2f})超過見込み")
        complete_kwargs: dict[str, Any] = {
            "max_tokens": clamped,
            "temperature": temperature,
        }
        # None の場合は従来と完全に同じinner呼出しを維持する。
        if self._request_options is not None:
            complete_kwargs["request_options"] = self._request_options
        text, usage = self._inner.complete(system, messages, **complete_kwargs)
        # Phase 1/2 と同一の実測usageベース計算に統一
        # （hidden thinking / cache割引 / Anthropic正規化を予算消費へ正しく反映）
        cost = _usage_cost(self._model_info, usage)
        self.calls_made += 1
        self.spent_usd += cost
        return text, usage


def build_phase3_config() -> GameConfig:
    base = GameConfig.baseline_v1_s2(num_players=4)
    return base.model_copy(update={
        "num_rounds": 1,
        "prize_tiers": base.prize_tiers[:1],
        "total_prize": sum(base.prize_tiers[:1]),
        "negotiation_max_turns": 1,
        "enable_cot": False,
        "memory_enabled": False,
        "double_up_enabled": False,
    })


def _judge_phase3(events: list[Any], agent: LLMAgent) -> dict[str, Any]:
    game_end = any(e.event_type == "GAME_END" for e in events)
    commit_ok = any(
        e.event_type == "COMMIT" and e.data.get("player_id") == "P01" and e.data.get("auto") is False
        for e in events
    )
    passed = bool(game_end and commit_ok and agent.auto_commit_count == 0 and agent.valid_json_count >= 2)
    return {
        "game_end": game_end, "commit_ok": commit_ok,
        "auto_commit_count": agent.auto_commit_count, "valid_json_count": agent.valid_json_count,
        "total_calls": agent.total_calls, "passed": passed,
    }


def _phase3_model_audit(info: ModelInfo, agent: LLMAgent, llm_logger: LLMLogger) -> dict[str, Any]:
    """Summarize per-call model identity and parse-correction audit data for Phase 3."""
    response_models: list[str] = []
    call_matches: list[str] = []
    for entry in llm_logger.entries:
        returned = entry.get("response_model")
        if returned and returned not in response_models:
            response_models.append(returned)
        call_matches.append(_model_match(info.model_id, returned))

    if not call_matches or "unknown" in call_matches:
        model_match = "unknown"
    elif "mismatch" in call_matches:
        model_match = "mismatch"
    elif "alias" in call_matches:
        model_match = "alias"
    else:
        model_match = "match"

    negotiation_corrections = agent.negotiation_correction_count
    commit_corrections = agent.commit_correction_count
    return {
        "requested_model": info.model_id,
        # A scalar is unambiguous only when every returned model name agrees.
        "response_model": response_models[0] if len(response_models) == 1 else None,
        "response_models": response_models,
        "model_match": model_match,
        "logical_calls": agent.total_calls,
        "negotiation_corrections": negotiation_corrections,
        "commit_corrections": commit_corrections,
        "parse_corrections_total": negotiation_corrections + commit_corrections,
    }


def _run_one_phase3_game(key: str, info: ModelInfo, run_dir: Path, seed: int,
                          max_calls: int, max_cost_per_model: float, max_tokens: int,
                          runtime_overrides: Phase3RuntimeOverrides | None = None) -> dict[str, Any]:
    overrides = runtime_overrides or Phase3RuntimeOverrides()
    effective_info = _phase3_effective_model(info, max_tokens, overrides.timeout_seconds)
    request_options = _phase3_request_options(effective_info, overrides.effort)
    runtime_effort = overrides.effort if request_options is not None else None
    model_dir = run_dir / "phase3" / key
    model_dir.mkdir(parents=True, exist_ok=True)
    llm_logger = LLMLogger(model_dir / "llm_logs", game_id=f"{key}_P01")
    # Phase 3 matrix runs are strict experiments: a logical call is allowed one
    # transport send only.  Keep this isolated from Phase 1/2 and normal games.
    raw_adapter = create_adapter(effective_info, max_retries=0, allow_temperature_fallback=False)
    budgeted = BudgetedAdapter(raw_adapter, effective_info, max_calls=max_calls,
                                max_cost=max_cost_per_model, max_tokens_cap=max_tokens,
                                request_options=request_options)
    config = build_phase3_config()
    agent = LLMAgent("P01", effective_info, budgeted, llm_logger, config)
    agents: dict[str, Any] = {"P01": agent}
    for i in range(1, config.num_players):
        pid = f"P{i + 1:02d}"
        agents[pid] = RandomBot(seed=seed * 100 + i)

    event_path = model_dir / "game01_events.jsonl"
    event_logger = EventLogger(output_path=event_path)
    error: str | None = None
    start = time.time()
    try:
        Game(config=config, agents=agents, seed=seed, logger=event_logger).run()
    except Exception as e:
        error = f"{type(e).__name__}: {str(e)[:300]}"
    finally:
        llm_logger.save()
        event_logger.save_jsonl(event_path)
    elapsed_ms = round((time.time() - start) * 1000)

    verdict = _judge_phase3(event_logger.events, agent)
    return {
        "error": error, "elapsed_ms": elapsed_ms,
        "calls_made": budgeted.calls_made, "cost_usd": round(budgeted.spent_usd, 6),
        **verdict, **_phase3_model_audit(effective_info, agent, llm_logger),
        "effective_max_tokens": effective_info.max_tokens,
        "runtime_effort": runtime_effort,
        "runtime_timeout_seconds": overrides.timeout_seconds,
    }


def run_phase3(run_dir: Path, state: dict[str, Any], keys: list[str],
                max_cost: float, max_cost_total: float, max_cost_per_model: float,
                max_calls: int, max_tokens: int, seed: int,
                resume: bool, force: bool, dry_run: bool,
                runtime_overrides: Phase3RuntimeOverrides | None = None) -> str:
    if state["phases"]["1"]["status"] not in ("done", "aborted_budget") or \
       state["phases"]["2"]["status"] not in ("done", "aborted_budget"):
        print("Phase 1/2 が未完了です。先に --phase 1, --phase 2 を実行してください。")
        return "gate_error"

    passers = [
        k for k in keys
        if state["models"].get(k, {}).get("phase1", {}).get("status") == "pass"
        and state["models"].get(k, {}).get("phase2", {}).get("status") == "pass"
    ]
    targets = []
    for key in passers:
        m = state["models"][key]
        if resume and not force and m["phase3"].get("status") in ("pass", "fail"):
            continue
        targets.append(key)

    phase_spent = 0.0
    aborted = False
    print(f"Phase 3 対象: {len(targets)}モデル（Phase1+2通過分）、1モデル上限 ${max_cost_per_model:.2f}")
    if dry_run:
        for key in targets:
            print(f"  [dry-run] {key} {MODEL_REGISTRY[key].provider} {MODEL_REGISTRY[key].name}")
        return "dry_run"

    for key in targets:
        info = MODEL_REGISTRY[key]
        worst = max_cost_per_model
        try:
            _check_budget_before_call(
                state, phase_spent, max_cost, max_cost_total, 0.0, max_cost_per_model,
                0, 10**9, worst,
            )
        except BudgetError as e:
            print(f"{key}: 予算中断 ({e})")
            overrides = runtime_overrides or Phase3RuntimeOverrides()
            effective_info = _phase3_effective_model(info, max_tokens, overrides.timeout_seconds)
            state["models"][key]["phase3"] = {
                "status": "skipped_budget", "reason": str(e),
                "effective_max_tokens": effective_info.max_tokens,
                "runtime_effort": (
                    overrides.effort
                    if _phase3_request_options(effective_info, overrides.effort) is not None
                    else None
                ),
                "runtime_timeout_seconds": overrides.timeout_seconds,
            }
            aborted = True
            _save_state(run_dir, state)
            continue

        print(f"{key} ({info.provider} {info.name})...", end=" ", flush=True)
        r = _run_one_phase3_game(
            key, info, run_dir, seed, max_calls, max_cost_per_model, max_tokens,
            runtime_overrides=runtime_overrides,
        )
        phase_spent += r["cost_usd"]
        state["budget"]["spent_usd"] += r["cost_usd"]
        state["budget"]["calls_made"] += r["calls_made"]

        status = "pass" if r["passed"] else "fail"
        _append_jsonl(run_dir / "phase3_calls.jsonl",
                       {"key": key, "model_id": info.model_id,
                        "timestamp": datetime.now(timezone.utc).isoformat(), **r})
        state["models"][key]["phase3"] = {
            "status": status, "elapsed_ms": r["elapsed_ms"], "cost_usd": r["cost_usd"],
            "auto_commit_count": r["auto_commit_count"], "valid_json_count": r["valid_json_count"],
            "error": r["error"],
            "requested_model": r["requested_model"], "response_model": r["response_model"],
            "response_models": r["response_models"], "model_match": r["model_match"],
            "logical_calls": r["logical_calls"],
            "negotiation_corrections": r["negotiation_corrections"],
            "commit_corrections": r["commit_corrections"],
            "parse_corrections_total": r["parse_corrections_total"],
            "effective_max_tokens": r["effective_max_tokens"],
            "runtime_effort": r["runtime_effort"],
            "runtime_timeout_seconds": r["runtime_timeout_seconds"],
        }
        _save_state(run_dir, state)
        print(f"{status} ({r['elapsed_ms']}ms) ${r['cost_usd']:.4f} | "
              f"累計${state['budget']['spent_usd']:.4f}/${max_cost_total:.2f}")

    _generate_phase3_report(run_dir, state)
    state["phases"]["3"] = {
        "status": "aborted_budget" if aborted else "done",
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }
    _save_state(run_dir, state)
    return "aborted_budget" if aborted else "done"


def _generate_phase3_report(run_dir: Path, state: dict[str, Any]) -> None:
    lines = ["# Phase 3 ミニゲーム統合レポート\n"]
    lines.append("| key | provider | model | returned | match | 状態 | elapsed | cost | calls | corrections | max tokens | effort | timeout | auto_commit | valid_json | 備考 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    for key, m in sorted(state["models"].items(), key=lambda kv: kv[1]["order"]):
        p3 = m.get("phase3", {})
        status = p3.get("status", "pending")
        elapsed = f"{p3.get('elapsed_ms', 0)}ms" if p3.get("elapsed_ms") else "—"
        cost = f"${p3.get('cost_usd', 0):.4f}" if p3.get("cost_usd") else "—"
        note = (p3.get("error") or "")[:80] if p3.get("error") else ""
        returned = p3.get("response_model") or ", ".join(p3.get("response_models", [])) or "—"
        corrections = p3.get("parse_corrections_total", "")
        effective_max_tokens = p3.get("effective_max_tokens", "—")
        runtime_effort = p3.get("runtime_effort") or "—"
        runtime_timeout = p3.get("runtime_timeout_seconds")
        runtime_timeout_s = runtime_timeout if runtime_timeout is not None else "—"
        lines.append(f"| {key} | {m['provider']} | {m['model_id']} | {returned} | {p3.get('model_match', '—')} | "
                     f"{status} | {elapsed} | {cost} | {p3.get('logical_calls', '')} | {corrections} | "
                     f"{effective_max_tokens} | {runtime_effort} | {runtime_timeout_s} | "
                     f"{p3.get('auto_commit_count', '')} | {p3.get('valid_json_count', '')} | {note} |")
    (run_dir / "phase3_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


# ============================================================
# 最終レポート
# ============================================================

def generate_final_report(run_dir: Path, state: dict[str, Any], out_paths: list[Path]) -> None:
    lines = ["# 18モデル マトリクス最終レポート\n"]
    lines.append(f"- run_id: {state['run_id']}")
    lines.append(f"- 通算コスト: ${state['budget']['spent_usd']:.4f} / ${state['budget']['max_cost_total']:.2f}")
    lines.append(f"- 通算コール数: {state['budget']['calls_made']}\n")
    lines.append("| Vendor | Tier | Model | API | JSON | Game | Returned | Latency | Cost | 備考 |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")

    def mark(status: str | None) -> str:
        if status == "pass":
            return "✓"
        if status in ("fail", "error"):
            return "✗"
        return "—"

    total_cost = 0.0
    for key, m in sorted(state["models"].items(), key=lambda kv: kv[1]["order"]):
        p1, p2, p3 = m.get("phase1", {}), m.get("phase2", {}), m.get("phase3", {})
        cost = (p1.get("cost_usd") or 0) + (p2.get("cost_usd") or 0) + (p3.get("cost_usd") or 0)
        total_cost += cost
        latency = p1.get("latency_ms") or p2.get("latency_ms") or "—"
        latency_s = f"{latency}ms" if isinstance(latency, int) else "—"
        note_parts = [p1.get("error") or "", p2.get("error") or "", p3.get("error") or ""]
        if any(field in p3 for field in ("effective_max_tokens", "runtime_effort", "runtime_timeout_seconds")):
            note_parts.append(
                "P3 runtime: "
                f"max_tokens={p3.get('effective_max_tokens', '—')}, "
                f"effort={p3.get('runtime_effort') or '—'}, "
                f"timeout={p3.get('runtime_timeout_seconds') or '—'}"
            )
        note = "; ".join(n for n in note_parts if n)[:100]
        returned = p1.get("response_model")
        match = p1.get("model_match")
        if returned:
            returned_s = f"{returned}" + (f" {_MODEL_MATCH_MARK.get(match, '')}" if match == "mismatch" else "")
        else:
            returned_s = "—"
        lines.append(
            f"| {m['provider']} | {m['tier']} | {key} ({m['model_id']}) | "
            f"{mark(p1.get('status'))} | {mark(p2.get('status'))} | {mark(p3.get('status'))} | "
            f"{returned_s} | {latency_s} | ${cost:.4f} | {note} |"
        )

    lines.append("")
    lines.append("`✓`=通過 / `✗`=失敗 / `—`=未実行（ゲート除外・予算スキップ・キー未設定）。")
    lines.append(
        "Returned列: Phase 1のAPI実返却モデル名。`⚠`=requestedと不一致（要確認）。"
        "`—`=取得不能または未実行。"
    )
    lines.append(
        "注記: ベンダー間で output_tokens を直接比較しないこと。Anthropicはthinkingを"
        "output_tokensに合算、OpenAIはreasoningをcompletionに内包、Geminiのみ差分課金。"
    )
    lines.append("")
    for phase, label in (("1", "Phase 1"), ("2", "Phase 2"), ("3", "Phase 3")):
        pstate = state["phases"].get(phase, {})
        if pstate.get("status") not in ("done",):
            key_arg = ",".join(CORE_18_KEYS)
            lines.append(
                f"- {label}: {pstate.get('status', 'pending')} → 再開: "
                f"`uv run python scripts/model_matrix.py --phase {phase} --run-id {state['run_id']} "
                f"--resume`"
            )

    text = "\n".join(lines) + "\n"
    for p in out_paths:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")


# ============================================================
# main / CLI
# ============================================================

def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("正の整数を指定してください") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("正の整数を指定してください")
    return parsed


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="18モデル段階的スモークテスト（Phase 0〜3）")
    parser.add_argument("--phase", type=int, choices=[0, 1, 2, 3], help="実行フェーズ")
    parser.add_argument("--tier", default="all", help="weak,mid,strong（既定: all）")
    parser.add_argument("--models", default="", help="対象キーを明示指定（カンマ区切り、--tierを上書き）")
    parser.add_argument("--run-id", default="", help="ラン用ディレクトリを明示指定")
    parser.add_argument("--max-cost", type=float, default=None, help="このフェーズ起動の累計USD上限")
    parser.add_argument("--max-cost-total", type=float, default=DEFAULT_MAX_COST_TOTAL, help="全フェーズ通算のハード上限")
    parser.add_argument("--max-cost-per-model", type=float, default=None, help="1モデル上限USD")
    parser.add_argument("--max-calls", type=int, default=None, help="絶対コール数天井")
    parser.add_argument("--max-tokens", type=int, default=None, help="出力上限")
    parser.add_argument("--effort", choices=_PHASE3_EFFORT_CHOICES, default=None,
                        help="Phase 3のH1 adaptive thinking effort")
    parser.add_argument("--timeout-seconds", type=_positive_int, default=None,
                        help="Phase 3だけのAPI timeout override（秒）")
    parser.add_argument(
        "--retries", type=int, default=None,
        help="Phase 1の再試行回数（スクリプト側のみ。アダプタ内部・OpenAI SDK内部のリトライは常に0＝1試行あたりHTTP1回）",
    )
    parser.add_argument("--resume", action="store_true", help="既済モデルをスキップ")
    retry_group = parser.add_mutually_exclusive_group()
    retry_group.add_argument("--force", action="store_true", help="既済モデルも再実行")
    retry_group.add_argument("--retry-failed", action="store_true", help="直近Phase 2でfailのモデルだけを再実行")
    parser.add_argument("--dry-run", action="store_true", help="計画と最悪コストのみ、APIコールゼロ")
    parser.add_argument("--report", action="store_true", help="state.jsonから最終表を再生成、APIコールゼロ")
    parser.add_argument("--out", default=str(DEFAULT_OUT_DIR), help="出力先ディレクトリ")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Phase3のゲームseed")
    return parser


def _validate_phase3_runtime_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    if args.phase != 3 and (args.effort is not None or args.timeout_seconds is not None):
        parser.error("--effort と --timeout-seconds は --phase 3 でのみ指定できます")


def _resolve_run_dir(args: argparse.Namespace, out_dir: Path) -> tuple[Path, str, bool]:
    """(run_dir, run_id, is_new) を返す"""
    if args.run_id:
        return _run_dir(out_dir, args.run_id), args.run_id, not (_run_dir(out_dir, args.run_id) / "state.json").exists()
    if args.phase in (0, 1) and not args.report:
        latest = _read_latest_run(out_dir)
        if latest and (_run_dir(out_dir, latest) / "state.json").exists() and args.resume:
            return _run_dir(out_dir, latest), latest, False
        run_id = _new_run_id()
        return _run_dir(out_dir, run_id), run_id, True
    latest = _read_latest_run(out_dir)
    if not latest:
        print("LATEST_RUN が見つかりません。--run-id を指定するか、先に --phase 0/1 を実行してください。")
        sys.exit(3)
    return _run_dir(out_dir, latest), latest, False


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    out_dir = Path(args.out)

    if args.report:
        run_dir, run_id, _ = _resolve_run_dir(args, out_dir)
        state = _load_state(run_dir)
        generate_final_report(run_dir, state, [run_dir / "matrix_report.md", Path("doc/model_matrix_report.md")])
        print(f"レポート再生成完了: {run_dir / 'matrix_report.md'}")
        sys.exit(0)

    if args.phase is None:
        print("--phase {0,1,2,3} または --report を指定してください。")
        sys.exit(3)

    _validate_phase3_runtime_args(args, parser)

    try:
        tiers = _parse_tiers(args.tier)
        explicit = [k.strip() for k in args.models.split(",") if k.strip()] or None
        keys = ordered_keys(tiers, explicit)
    except ValueError as e:
        print(str(e))
        sys.exit(1)

    run_dir, run_id, is_new = _resolve_run_dir(args, out_dir)
    if is_new:
        state = _init_state(run_id, args.max_cost_total, keys)
        _save_state(run_dir, state)
        _write_latest_run(out_dir, run_id)
    else:
        state = _load_state(run_dir)
        state["budget"]["max_cost_total"] = max(state["budget"]["max_cost_total"], args.max_cost_total)
        _ensure_models_in_state(state, keys)

    print(f"=== run_id: {run_id} / phase {args.phase} / 対象{len(keys)}モデル ===")

    defaults = PHASE_DEFAULTS.get(args.phase, {})
    max_cost = args.max_cost if args.max_cost is not None else defaults.get("max_cost", 0.5)
    max_cost_per_model = args.max_cost_per_model if args.max_cost_per_model is not None else defaults.get("max_cost_per_model", 0.1)
    max_calls = args.max_calls if args.max_calls is not None else defaults.get("max_calls", 40)
    max_tokens = args.max_tokens if args.max_tokens is not None else defaults.get("max_tokens", 400)
    retries = args.retries if args.retries is not None else defaults.get("retries", 0)

    if args.phase == 0:
        run_phase0(run_dir, state)
        result = "done"
    elif args.phase == 1:
        result = run_phase1(run_dir, state, keys, max_cost, args.max_cost_total, max_cost_per_model,
                             max_calls, max_tokens, retries, args.resume, args.force, args.dry_run)
    elif args.phase == 2:
        result = run_phase2(run_dir, state, keys, max_cost, args.max_cost_total, max_cost_per_model,
                            max_calls, max_tokens, args.resume, args.force, args.dry_run, args.retry_failed)
    else:
        runtime_overrides = Phase3RuntimeOverrides(
            effort=args.effort,
            timeout_seconds=args.timeout_seconds,
        )
        result = run_phase3(run_dir, state, keys, max_cost, args.max_cost_total, max_cost_per_model,
                             max_calls, max_tokens, args.seed, args.resume, args.force, args.dry_run,
                             runtime_overrides=runtime_overrides)

    generate_final_report(run_dir, state, [run_dir / "matrix_report.md", Path("doc/model_matrix_report.md")])

    print(f"=== 完了: {result} ===")
    print(f"通算コスト: ${state['budget']['spent_usd']:.4f} / ${args.max_cost_total:.2f}")

    if result == "gate_error":
        sys.exit(3)
    if result == "aborted_budget":
        sys.exit(2)
    if result == "dry_run":
        sys.exit(0)
    sys.exit(0)


if __name__ == "__main__":
    main()
