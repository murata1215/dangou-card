"""
汎用モデルスモークテストスクリプト

MODEL_REGISTRY 内の1モデルを対象に、少数回のAPIコールで
疎通・応答品質・コスト特性を確認する。既存の6社12モデル一括
疎通スクリプト（scripts/api_check.py）とは異なり、以下を目的とする:

- 1モデルだけを指定して呼べる（--model）
- コール数・コスト上限をCLIで明示的に指定できる（--calls / --max-cost）
- エラーメッセージを切り捨てず全文記録する（AdapterErrorで失われた原因例外を
  1回だけ生SDKで再取得する）
- finish_reason / cache / reasoning / total tokens を1コールごとにJSONLへ記録する
- --dry-run でAPIを一切呼ばずに実行計画とコスト見積のみ表示できる

--suite thinking_matrix（DeepSeek L6診断用）:
- 同一の実戦相当プロンプトを固定し、thinking有効/無効・max_tokensだけを変える8条件を実行する
- llm/adapters.py の「content空ならreasoning_contentを本文採用」フォールバックの手前で
  content と reasoning_content を分離記録する（診断専用の生SDK直呼び、1条件1回）
- 送信パラメータ（sent_max_tokens / sent_temperature / sent_extra_body）を全レコードに記録する

llm/models.py・llm/adapters.py・engine/ は変更しない。
既存のMODEL_REGISTRYをそのまま参照するのみ。extra_paramsの実験はスクリプト側から
call単位で渡すだけで、MODEL_REGISTRYの内容は一切書き換えない。

使用例:
    uv run python scripts/model_smoke.py --model L3 --dry-run
    uv run python scripts/model_smoke.py --model L3 --calls 4 --max-cost 0.10
    uv run python scripts/model_smoke.py --model L6 --suite thinking_matrix --dry-run
    uv run python scripts/model_smoke.py --model L6 --suite thinking_matrix --calls 8 --max-cost 0.10
"""

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from llm.models import get_model, estimate_cost, ModelInfo
from llm.adapters import create_adapter, AdapterError, OpenAICompatAdapter, _dump_usage_raw
from llm.response_parser import extract_json, parse_response, ParseError
from llm.prompt_builder import build_system_prompt
from llm.constants import DEFAULT_TEMPERATURE
from engine.config import GameConfig

HARD_MAX_CALLS = 8  # このスクリプトが1回の実行で許容する絶対上限（安全弁）

PING_SYSTEM = "You are a test assistant. Always reply with strict JSON only."
PING_USER = 'JSON形式で厳密に1つだけ返してください: {"ok": true}'

GAME_USER = """ラウンド3 / 交渉フェイズ（巡2）

市場:
  M01: 賞金 84万円
  M02: 賞金 84万円
  M03: 賞金 84万円

あなたの状態:
  現金: 300万円
  手札: HIGH_CARD, ONE_PAIR, TWO_PAIR, THREE_OF_A_KIND, STRAIGHT, FLUSH

他プレイヤーからの発言:
  P02: 「M01は均等分配にしませんか」
  P05: 「私はM03を狙います」

アクションをJSON形式で1つ選んでください（交渉フェイズなのでmarket_commitは使えません）。
例: {"strategy": {"target_market": "M01", "reason": "..."}, "action": {"type": "pass"}}"""


def build_cases(model: ModelInfo, max_tokens: int) -> list[dict[str, Any]]:
    """テストケース定義を返す（合計4ケース: ping 1回 + 実戦相当プロンプト3回）"""
    # 実戦相当のシステムプロンプト（18体S2想定）。build_system_prompt は
    # engine/llm 双方の既存実装をそのまま再利用する（新規ロジックなし）。
    game_system = build_system_prompt("P01", GameConfig.baseline_v1_s2(num_players=18))

    cases = [
        {
            "case": "ping_json",
            "system": PING_SYSTEM,
            "user": PING_USER,
            "max_tokens": 200,
            "phase": None,  # parse_response を使わず extract_json のみで判定
        },
    ]
    for i in range(1, 4):
        cases.append({
            "case": f"game_json_{i}",
            "system": game_system,
            "user": GAME_USER,
            "max_tokens": max_tokens,
            "phase": "negotiation",
        })
    return cases


# --- thinking_matrix suite（DeepSeek L6診断用） ---

THINKING_DISABLED: dict[str, Any] = {"thinking": {"type": "disabled"}}

# 8コール構成: baseline(既定=thinking有効/high) ×2, nothink×4000 ×1, nothink×2000 ×3, nothink×1000 ×2
# reasoning_effort は今回試さない（DeepSeek公式仕様上 low/medium が実質 high に潰れ、
# Flashでは有効な中間段が存在しないため比較条件として意味を成さない）。
THINKING_MATRIX_SPEC: list[tuple[str, dict[str, Any] | None, int]] = [
    ("baseline_4000", None, 4000),
    ("baseline_4000", None, 4000),
    ("nothink_4000", THINKING_DISABLED, 4000),
    ("nothink_2000", THINKING_DISABLED, 2000),
    ("nothink_2000", THINKING_DISABLED, 2000),
    ("nothink_2000", THINKING_DISABLED, 2000),
    ("nothink_1000", THINKING_DISABLED, 1000),
    ("nothink_1000", THINKING_DISABLED, 1000),
]


def build_thinking_matrix_cases(model: ModelInfo) -> list[dict[str, Any]]:
    """
    DeepSeek L6 (deepseek-v4-flash) のthinking暴走診断用8条件を返す。
    同一の実戦相当プロンプト（build_cases()と同じ18体S2交渉フェイズ）を固定し、
    thinking有効/無効・max_tokensだけを変える。
    """
    game_system = build_system_prompt("P01", GameConfig.baseline_v1_s2(num_players=18))
    cases: list[dict[str, Any]] = []
    condition_counts: dict[str, int] = {}
    for condition, extra_params, max_tokens in THINKING_MATRIX_SPEC:
        condition_counts[condition] = condition_counts.get(condition, 0) + 1
        n = condition_counts[condition]
        cases.append({
            "case": f"{condition}_{n}",
            "condition": condition,
            "system": game_system,
            "user": GAME_USER,
            "max_tokens": max_tokens,
            "phase": "negotiation",
            "extra_params": extra_params,
            "temperature": DEFAULT_TEMPERATURE,
        })
    return cases


def _looks_like_pure_json(text: str) -> bool:
    """先頭が（コードフェンスを除去した上で）'{'で始まるか。余計な散文の有無の粗い判定に使う。"""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.endswith("```"):
            t = t[:-3]
        t = t.strip()
    return t.startswith("{")


def complete_with_fields(
    adapter: OpenAICompatAdapter,
    system: str,
    user: str,
    max_tokens: int,
    temperature: float,
    extra_params: dict[str, Any] | None,
) -> dict[str, Any]:
    """
    llm/adapters.py の OpenAICompatAdapter.complete()（:213-282）と同一の
    create_kwargs 構成・temperatureフォールバック規則で、1回だけ直接APIを呼ぶ。

    llm/adapters.py はレスポンスの message.content が空のとき reasoning_content を
    text にフォールバックしてしまい、どちらに何が入っていたか観測できなくなる
    （:246-250）。本関数はそのフォールバックの手前で止めて content と
    reasoning_content を分離したまま返す。診断専用（リトライなし・1条件1回）。
    llm/adapters.py 自体は変更しない。
    """
    client = adapter._get_client()
    full_messages = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    create_kwargs: dict[str, Any] = {
        "model": adapter.model_info.model_id,
        "messages": full_messages,
        "max_tokens": max_tokens,
    }
    if extra_params:
        create_kwargs["extra_body"] = extra_params
    try:
        response = client.chat.completions.create(temperature=temperature, **create_kwargs)
    except Exception as temp_err:
        if "temperature" in str(temp_err).lower():
            response = client.chat.completions.create(**create_kwargs)
        else:
            raise

    content = (response.choices[0].message.content or "") if response.choices else ""
    reasoning_content = ""
    if response.choices:
        extras = getattr(response.choices[0].message, "model_extra", None) or {}
        reasoning_content = extras.get("reasoning_content", "") or ""

    # llm/adapters.py:246-250 と同一の規則を再現（実戦でゲームに渡る text と一致させる）
    if content:
        text, text_source = content, "content"
    elif reasoning_content:
        text, text_source = reasoning_content, "reasoning_content"
    else:
        text, text_source = "", "empty"

    finish_reason = response.choices[0].finish_reason if response.choices else None
    _ctd = getattr(response.usage, "completion_tokens_details", None)
    _ptd = getattr(response.usage, "prompt_tokens_details", None)
    usage = {
        "input_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
        "output_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
        "total_tokens": getattr(response.usage, "total_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(_ptd, "cached_tokens", 0) or 0,
        "cache_creation_input_tokens": getattr(_ptd, "cache_write_tokens", 0) or 0,
        "reasoning_tokens": getattr(_ctd, "reasoning_tokens", 0) or 0,
        "finish_reason": finish_reason,
        "usage_raw": _dump_usage_raw(response.usage),
    }
    return {
        "text": text,
        "text_source": text_source,
        "content": content,
        "reasoning_content": reasoning_content,
        "usage": usage,
    }


def run_thinking_case(
    model_key: str,
    model: ModelInfo,
    adapter: OpenAICompatAdapter,
    case: dict[str, Any],
    attempt_index: int,
) -> dict[str, Any]:
    """thinking_matrix 用の1コール実行（complete_with_fields を使い content/reasoning_content を分離記録）"""
    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model_key": model_key,
        "model_id": model.model_id,
        "provider": model.provider,
        "base_url": model.base_url,
        "adapter_class": type(adapter).__name__,
        "case": case["case"],
        "condition": case["condition"],
        "attempt_index": attempt_index,
        "sent_max_tokens": case["max_tokens"],
        "sent_temperature": case.get("temperature", DEFAULT_TEMPERATURE),
        "sent_extra_body": case.get("extra_params"),
    }

    start = time.time()
    try:
        result = complete_with_fields(
            adapter,
            system=case["system"],
            user=case["user"],
            max_tokens=case["max_tokens"],
            temperature=case.get("temperature", DEFAULT_TEMPERATURE),
            extra_params=case.get("extra_params"),
        )
        latency_ms = round((time.time() - start) * 1000, 1)
        text = result["text"]
        usage = result["usage"]

        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0
        total_tokens = usage.get("total_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_write = usage.get("cache_creation_input_tokens", 0) or 0
        reasoning_tokens = usage.get("reasoning_tokens", 0) or 0
        cost = estimate_cost(model, input_tokens, output_tokens, cache_read, total_tokens)

        json_extract_ok = False
        parse_ok = False
        parse_error = None
        data = extract_json(text)
        if data is not None:
            json_extract_ok = True
            try:
                parse_response(text, "P01", case["phase"])
                parse_ok = True
            except ParseError as pe:
                parse_error = str(pe)

        record.update({
            "ok": True,
            "error_type": None,
            "error_full": None,
            "latency_ms": latency_ms,
            "finish_reason": usage.get("finish_reason"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
            "reasoning_tokens": reasoning_tokens,
            "thinking_diff": max(0, total_tokens - input_tokens - output_tokens),
            "usage_raw": usage.get("usage_raw"),
            "content_len": len(result["content"]),
            "reasoning_content_len": len(result["reasoning_content"]),
            "used_reasoning_fallback": result["text_source"] == "reasoning_content",
            "text_source": result["text_source"],
            "json_extract_ok": json_extract_ok,
            "parse_ok": parse_ok,
            "parse_error": parse_error,
            "has_extraneous_prose": (not _looks_like_pure_json(text)) if text else True,
            "response_len": len(text),
            "response_head": text[:300],
            "response_tail": text[-300:] if len(text) > 300 else "",
            "cost_usd": cost,
        })
        return record

    except Exception as e:
        latency_ms = round((time.time() - start) * 1000, 1)
        record.update({
            "ok": False,
            "error_type": type(e).__name__,
            "error_full": str(e),  # 生SDK直呼びのため切り捨てなし全文
            "latency_ms": latency_ms,
            "finish_reason": None,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "reasoning_tokens": 0, "thinking_diff": 0, "usage_raw": None,
            "content_len": 0, "reasoning_content_len": 0,
            "used_reasoning_fallback": False, "text_source": None,
            "json_extract_ok": False, "parse_ok": False, "parse_error": None,
            "has_extraneous_prose": None,
            "response_len": 0, "response_head": "", "response_tail": "",
            "cost_usd": 0.0,
        })
        return record


def worst_case_cost(model: ModelInfo, system: str, user: str, max_tokens: int) -> float:
    """次コールの最悪コスト（入力全額課金 + 出力max_tokens全消費 + hidden thinking予約）を事前試算する

    2026-08-18: model.hidden_thinking_reserve_tokens を加算するよう拡張。
    Gemini/xAI系（M3/L3/H3/M4/L4/H4）は max_tokens の外側で thinking トークンが課金される
    ことが実測で確認されており（Phase 1実測: max_tokens=64でも185〜343token発生）、
    従来の見積は常にこの分を過小評価していた。

    保証レベルの注意（重要）:
    - Anthropic/OpenAI/Kimi/DeepSeek（hidden_thinking_reserve_tokens=0）:
      thinking が output/completion に内包されるか無効化済みであることを実装・実測で確認済みのため、
      従来どおり「入力全額 + max_tokens全消費」が数学的worst-caseとして妥当。
    - Gemini/xAI（hidden_thinking_reserve_tokens=512）:
      512 は provider が保証する上限ではなく、実測最大343（xAI L4）に対する約1.5倍の
      **経験的安全マージン**である。関数名が worst_case_cost であっても、これらのモデルについては
      数学的な完全保証ではなく観測ベースの保守的な予約見積であることに留意すること。
      thinking budget / reasoning effort の上限をリクエストで指定していない限り、
      512を超える可能性は理論上残る（超過分は次コールの予算ガードで検知・停止される）。
    """
    # 簡易トークン推定: 日本語混在テキストは概ね2〜3文字/token。安全側に2文字/tokenで見積もる。
    approx_input_tokens = (len(system) + len(user)) // 2 + 50  # +50はメッセージ構造のオーバーヘッド分
    reserve = model.hidden_thinking_reserve_tokens  # 0なら現行式と完全同値
    total_tokens = approx_input_tokens + max_tokens + reserve
    return estimate_cost(model, approx_input_tokens, max_tokens, total_tokens=total_tokens)


def fetch_raw_error_detail(adapter: OpenAICompatAdapter, system: str, user: str, max_tokens: int) -> dict[str, Any]:
    """
    AdapterErrorで切り捨てられた原因例外を、同一クライアントで1回だけ再取得し、
    全文（切り詰めなし）を記録する。このコールも呼び出し元でコール予算に加算すること。
    --suite default 専用（thinking_matrix は complete_with_fields が生SDK直呼びのため
    AdapterErrorの200字切り捨てが発生せず、run_thinking_case が既に全文を記録する）。
    """
    detail: dict[str, Any] = {"raw_error_str": None, "status_code": None, "body": None, "response_text": None}
    try:
        client = adapter._get_client()
        client.chat.completions.create(
            model=adapter.model_info.model_id,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=max_tokens,
            temperature=DEFAULT_TEMPERATURE,
        )
    except Exception as e:
        detail["raw_error_str"] = str(e)  # 切り詰めなし
        detail["status_code"] = getattr(e, "status_code", None)
        body = getattr(e, "body", None)
        detail["body"] = str(body) if body is not None else None
        resp = getattr(e, "response", None)
        if resp is not None:
            try:
                detail["response_text"] = resp.text
            except Exception:
                detail["response_text"] = None
    return detail


def run_case(
    model_key: str,
    model: ModelInfo,
    adapter: Any,
    case: dict[str, Any],
    attempt_index: int,
) -> dict[str, Any]:
    """1コールを実行し、結果レコードを返す"""
    record: dict[str, Any] = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "model_key": model_key,
        "model_id": model.model_id,
        "provider": model.provider,
        "base_url": model.base_url,
        "adapter_class": type(adapter).__name__,
        "case": case["case"],
        "attempt_index": attempt_index,
    }

    start = time.time()
    try:
        text, usage = adapter.complete(
            system=case["system"],
            messages=[{"role": "user", "content": case["user"]}],
            max_tokens=case["max_tokens"],
            temperature=DEFAULT_TEMPERATURE,
        )
        latency_ms = round((time.time() - start) * 1000, 1)

        input_tokens = usage.get("input_tokens", 0) or 0
        output_tokens = usage.get("output_tokens", 0) or 0
        total_tokens = usage.get("total_tokens", 0) or 0
        cache_read = usage.get("cache_read_input_tokens", 0) or 0
        cache_write = usage.get("cache_creation_input_tokens", 0) or 0
        reasoning_tokens = usage.get("reasoning_tokens", 0) or 0
        cost = estimate_cost(model, input_tokens, output_tokens, cache_read, total_tokens)

        json_extract_ok = False
        parse_ok = False
        parse_error = None
        data = extract_json(text)
        if data is not None:
            json_extract_ok = True
            if case["phase"] is not None:
                try:
                    parse_response(text, "P01", case["phase"])
                    parse_ok = True
                except ParseError as pe:
                    parse_error = str(pe)
            else:
                # ping_json はaction形式ではないので、JSON抽出成功のみで成立とみなす
                parse_ok = True

        record.update({
            "ok": True,
            "error_type": None,
            "error_full": None,
            "latency_ms": latency_ms,
            "finish_reason": usage.get("finish_reason"),
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": total_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
            "reasoning_tokens": reasoning_tokens,
            "thinking_diff": max(0, total_tokens - input_tokens - output_tokens),
            "usage_raw": usage.get("usage_raw"),
            "json_extract_ok": json_extract_ok,
            "parse_ok": parse_ok,
            "parse_error": parse_error,
            "response_len": len(text),
            "response_head": text[:300],
            "cost_usd": cost,
        })
        return record

    except AdapterError as e:
        latency_ms = round((time.time() - start) * 1000, 1)
        record.update({
            "ok": False,
            "error_type": type(e).__name__,
            "error_full": str(e),  # AdapterError自体は200字切り捨て済みメッセージを保持
            "latency_ms": latency_ms,
            "finish_reason": None,
            "input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "reasoning_tokens": 0, "thinking_diff": 0, "usage_raw": None,
            "json_extract_ok": False, "parse_ok": False, "parse_error": None,
            "response_len": 0, "response_head": "",
            "cost_usd": 0.0,
        })
        return record


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="1モデル専用の少数回スモークテスト")
    parser.add_argument("--model", required=True, help="MODEL_REGISTRY のキー（例: L3）")
    parser.add_argument("--calls", type=int, default=4, help=f"総APIコール数上限（デフォルト4、絶対上限{HARD_MAX_CALLS}）")
    parser.add_argument("--max-cost", type=float, default=0.10, help="累計推定コスト上限USD（デフォルト0.10）")
    parser.add_argument("--max-tokens", type=int, default=2000, help="game_jsonケースの出力上限トークン（デフォルト2000、--suite default のみ使用）")
    parser.add_argument("--suite", default="default", choices=["default", "thinking_matrix"],
                         help="default: 疎通確認4ケース / thinking_matrix: L6 thinking診断8条件（--model L6専用想定）")
    parser.add_argument("--dry-run", action="store_true", help="APIを呼ばず実行計画のみ表示")
    parser.add_argument("--out", default="logs/smoke", help="出力ディレクトリ（デフォルト logs/smoke）")
    args = parser.parse_args(argv)

    if args.calls > HARD_MAX_CALLS:
        print(f"エラー: --calls={args.calls} はハード上限 {HARD_MAX_CALLS} を超えています。中断します。")
        return 1
    if args.calls < 1:
        print("エラー: --calls は1以上を指定してください。")
        return 1

    try:
        model = get_model(args.model)
    except ValueError as e:
        print(f"エラー: {e}")
        return 1

    if args.suite == "thinking_matrix":
        cases = build_thinking_matrix_cases(model)
    else:
        cases = build_cases(model, args.max_tokens)
    # --calls がケース数未満なら先頭から間引く
    cases = cases[: args.calls]

    print(f"=== モデルスモークテスト: {args.model} ({model.provider} {model.name}) suite={args.suite} ===")
    print(f"model_id={model.model_id} adapter_type={model.adapter_type} base_url={model.base_url}")
    print(f"env_key={model.env_key} 単価: input=${model.input_price}/1M output=${model.output_price}/1M")
    print(f"コール上限={args.calls} (絶対上限{HARD_MAX_CALLS}) コスト上限=${args.max_cost}")
    print("---")

    plan_cost = 0.0
    for c in cases:
        wc = worst_case_cost(model, c["system"], c["user"], c["max_tokens"])
        plan_cost += wc
        label = f" condition={c['condition']} extra_params={c.get('extra_params')}" if args.suite == "thinking_matrix" else ""
        print(f"  [計画] {c['case']}: max_tokens={c['max_tokens']} 最悪コスト見積=${wc:.4f}{label}")
    print(f"  [計画] 合計最悪コスト見積 = ${plan_cost:.4f}（上限 ${args.max_cost}）")

    if args.dry_run:
        print("\n--dry-run のためAPIは呼びません。")
        return 0

    if plan_cost > args.max_cost:
        print(f"\n中断: 計画上の最悪コスト ${plan_cost:.4f} が上限 ${args.max_cost} を超えるため、APIを呼ばずに終了します。")
        return 1

    try:
        adapter = create_adapter(model)
    except Exception as e:
        print(f"エラー: アダプタ作成失敗: {type(e).__name__}: {e}")
        return 1

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    jsonl_path = out_dir / f"smoke_{args.model}_{args.suite}_{ts}.jsonl"
    md_path = out_dir / f"smoke_{args.model}_{args.suite}_{ts}.md"

    records: list[dict[str, Any]] = []
    cumulative_cost = 0.0
    calls_made = 0

    print(f"\n出力: {jsonl_path}")
    print("---")

    with jsonl_path.open("w", encoding="utf-8") as f:
        for i, c in enumerate(cases, start=1):
            next_worst = worst_case_cost(model, c["system"], c["user"], c["max_tokens"])
            if cumulative_cost + next_worst > args.max_cost:
                print(f"中断: [{c['case']}] 実行前の累計見積 ${cumulative_cost + next_worst:.4f} が上限 ${args.max_cost} を超えるため、これ以降は呼びません。")
                break

            print(f"[{i}/{len(cases)}] {c['case']} 実行中...", end=" ", flush=True)
            if args.suite == "thinking_matrix":
                rec = run_thinking_case(args.model, model, adapter, c, i)
            else:
                rec = run_case(args.model, model, adapter, c, i)
            calls_made += 1
            cumulative_cost += rec["cost_usd"]

            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            f.flush()
            records.append(rec)

            if rec["ok"]:
                extra_info = ""
                if args.suite == "thinking_matrix":
                    extra_info = (
                        f" reasoning_tok={rec.get('reasoning_tokens')} "
                        f"text_source={rec.get('text_source')} "
                        f"fallback={'Y' if rec.get('used_reasoning_fallback') else 'N'}"
                    )
                print(f"OK latency={rec['latency_ms']}ms finish={rec['finish_reason']} "
                      f"in={rec['input_tokens']} out={rec['output_tokens']} "
                      f"json={'✓' if rec['json_extract_ok'] else '✗'} "
                      f"parse={'✓' if rec['parse_ok'] else '✗'} cost=${rec['cost_usd']:.5f}{extra_info}")
            else:
                print(f"NG error_type={rec['error_type']} error={rec['error_full']}")

                # エラー全文の再取得は --suite default のみ（thinking_matrix は
                # complete_with_fields の生SDK直呼びで既に全文を記録済み）
                if args.suite == "default" and calls_made < args.calls and isinstance(adapter, OpenAICompatAdapter):
                    print("  → エラー全文を再取得します（コール予算+1）...", end=" ", flush=True)
                    detail = fetch_raw_error_detail(adapter, c["system"], c["user"], c["max_tokens"])
                    calls_made += 1
                    detail_rec = {
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                        "model_key": args.model, "model_id": model.model_id,
                        "case": f"{c['case']}_raw_error_detail", "attempt_index": i,
                        **detail,
                    }
                    f.write(json.dumps(detail_rec, ensure_ascii=False) + "\n")
                    f.flush()
                    records.append(detail_rec)
                    print("記録しました")

            if calls_made >= args.calls:
                break

    # --- サマリ集計 ---
    normal_records = [r for r in records if "ok" in r]
    ok_count = sum(1 for r in normal_records if r.get("ok"))
    json_ok_count = sum(1 for r in normal_records if r.get("json_extract_ok"))
    parse_ok_count = sum(1 for r in normal_records if r.get("parse_ok"))
    latencies = [r["latency_ms"] for r in normal_records if r.get("ok")]
    total_cost = sum(r.get("cost_usd", 0.0) for r in normal_records)
    total_in = sum(r.get("input_tokens", 0) for r in normal_records)
    total_out = sum(r.get("output_tokens", 0) for r in normal_records)
    total_thinking = sum(r.get("thinking_diff", 0) for r in normal_records)

    summary_lines = [
        f"# モデルスモークテスト結果: {args.model} ({model.provider} {model.name}) suite={args.suite}",
        "",
        f"- model_id: `{model.model_id}`",
        f"- base_url: `{model.base_url}`",
        f"- 実行コール数: {len(normal_records)} (計画{len(cases)}, 上限{args.calls})",
        f"- 成功: {ok_count}/{len(normal_records)}",
        f"- JSON抽出成功: {json_ok_count}/{len(normal_records)}",
        f"- パース成功（action変換まで）: {parse_ok_count}/{len(normal_records)}",
        f"- latency: min={min(latencies):.0f}ms avg={sum(latencies)/len(latencies):.0f}ms max={max(latencies):.0f}ms" if latencies else "- latency: N/A（成功コールなし）",
        f"- token合計: input={total_in} output={total_out} thinking差分={total_thinking}",
        f"- 実測コスト合計: ${total_cost:.6f} (¥{total_cost*150:,.2f})",
        "",
        "## コール別詳細",
        "",
    ]
    for r in normal_records:
        if r.get("ok"):
            extra = ""
            if args.suite == "thinking_matrix":
                extra = (
                    f" reasoning_tok={r.get('reasoning_tokens')} text_source={r.get('text_source')} "
                    f"fallback={'Y' if r.get('used_reasoning_fallback') else 'N'} "
                    f"prose={'Y' if r.get('has_extraneous_prose') else 'N'}"
                )
            cond_label = f" [{r.get('condition')}]" if args.suite == "thinking_matrix" else ""
            summary_lines.append(
                f"- {r['case']}{cond_label}: OK latency={r['latency_ms']}ms finish_reason={r['finish_reason']} "
                f"in={r['input_tokens']} out={r['output_tokens']} think_diff={r['thinking_diff']} "
                f"json={'✓' if r['json_extract_ok'] else '✗'} parse={'✓' if r['parse_ok'] else '✗'} "
                f"cost=${r['cost_usd']:.6f}{extra}"
            )
        else:
            summary_lines.append(f"- {r['case']}: **NG** error_type={r['error_type']} error_full={r['error_full']}")

    detail_records = [r for r in records if "raw_error_str" in r]
    if detail_records:
        summary_lines.append("\n## エラー全文（再取得分）\n")
        for d in detail_records:
            summary_lines.append(f"- {d['case']}: status_code={d.get('status_code')}")
            summary_lines.append(f"  - raw_error_str: {d.get('raw_error_str')}")
            if d.get("body"):
                summary_lines.append(f"  - body: {d.get('body')}")
            if d.get("response_text"):
                summary_lines.append(f"  - response_text: {d.get('response_text')}")

    # thinking_matrix suite: 条件別の集計表を追加
    if args.suite == "thinking_matrix" and normal_records:
        summary_lines.append("\n## 条件別集計（thinking_matrix）\n")
        conditions_order: list[str] = []
        by_condition: dict[str, list[dict[str, Any]]] = {}
        for r in normal_records:
            cond = r.get("condition", "unknown")
            if cond not in by_condition:
                by_condition[cond] = []
                conditions_order.append(cond)
            by_condition[cond].append(r)
        summary_lines.append(
            "| condition | n | ok | length率 | JSON率 | fallback件数 | avg_output_tok | avg_reasoning_tok | latency(個別ms) | cost合計 |"
        )
        summary_lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for cond in conditions_order:
            rs = by_condition[cond]
            n = len(rs)
            ok_n = sum(1 for r in rs if r.get("ok"))
            length_n = sum(1 for r in rs if r.get("finish_reason") == "length")
            json_n = sum(1 for r in rs if r.get("json_extract_ok"))
            fb_n = sum(1 for r in rs if r.get("used_reasoning_fallback"))
            out_toks = [r.get("output_tokens", 0) for r in rs if r.get("ok")]
            reasoning_toks = [r.get("reasoning_tokens", 0) for r in rs if r.get("ok")]
            lat = [r.get("latency_ms", 0) for r in rs if r.get("ok")]
            cost_sum = sum(r.get("cost_usd", 0.0) for r in rs)
            avg_out = sum(out_toks) / len(out_toks) if out_toks else 0
            avg_reasoning = sum(reasoning_toks) / len(reasoning_toks) if reasoning_toks else 0
            lat_str = ", ".join(f"{v:.0f}" for v in lat) if lat else "-"
            summary_lines.append(
                f"| {cond} | {n} | {ok_n} | {length_n}/{n} | {json_n}/{n} | {fb_n} | "
                f"{avg_out:.0f} | {avg_reasoning:.0f} | {lat_str} | ${cost_sum:.6f} |"
            )

    md_path.write_text("\n".join(summary_lines), encoding="utf-8")

    print("\n=== サマリ ===")
    print("\n".join(summary_lines[:11]))
    print(f"\n出力: {jsonl_path}")
    print(f"出力: {md_path}")

    # 1件でも失敗（AdapterError）があれば非0で終了する（呼び出し元がCIで検知できるように）
    return 1 if any(not r.get("ok") for r in normal_records) else 0


if __name__ == "__main__":
    sys.exit(main())
