"""
scripts/model_smoke.py のユニットテスト

API を一切呼ばない。create_adapter をフェイクアダプタに差し替えて検証する。
"""

import json

import pytest

from scripts.model_smoke import (
    main,
    build_cases,
    build_thinking_matrix_cases,
    complete_with_fields,
    run_thinking_case,
    worst_case_cost,
    HARD_MAX_CALLS,
    THINKING_DISABLED,
)
from llm.models import get_model, estimate_cost, MODEL_REGISTRY


class _FakeOkAdapter:
    """成功応答のみを返すフェイクアダプタ"""

    def __init__(self, model_info):
        self.model_info = model_info
        self.calls = 0

    def complete(self, system, messages, max_tokens=1000, temperature=0.7):
        self.calls += 1
        text = '{"strategy": {"reason": "test"}, "action": {"type": "pass"}}'
        usage = {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 120,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
            "reasoning_tokens": 0,
            "finish_reason": "stop",
            "usage_raw": {"prompt_tokens": 100, "completion_tokens": 20},
        }
        return text, usage


class _FakeErrorAdapter:
    """常にAdapterErrorを投げるフェイクアダプタ"""

    def __init__(self, model_info):
        self.model_info = model_info

    def complete(self, system, messages, max_tokens=1000, temperature=0.7):
        from llm.adapters import AdapterError
        raise AdapterError("OpenAI-compat API error (NotFoundError): fake 404 for test")


class _RefuseToCallAdapter:
    """呼ばれたらテスト失敗にする（--dry-run や予算超過で呼ばれないことの検証用）"""

    def __init__(self, model_info):
        self.model_info = model_info

    def complete(self, *a, **kw):
        raise AssertionError("API は呼ばれないはずでした（--dry-run または予算超過ガード）")


def test_l3_resolves_to_gemini_route():
    """L3 がGeminiのOpenAI互換経路として解決できること（回帰確認、L7調査を踏まえた最終確認）"""
    model = get_model("L3")
    assert model.model_id == "gemini-3.5-flash-lite"
    assert model.provider == "Google"
    assert model.adapter_type == "gemini"


def test_build_cases_count_and_content():
    """build_cases が ping1 + game_json3 = 4ケースを返し、game_jsonはnegotiation phaseであること"""
    model = get_model("L3")
    cases = build_cases(model, max_tokens=2000)
    assert len(cases) == 4
    assert cases[0]["case"] == "ping_json"
    assert cases[0]["phase"] is None
    for c in cases[1:]:
        assert c["case"].startswith("game_json_")
        assert c["phase"] == "negotiation"
        assert c["max_tokens"] == 2000


def test_worst_case_cost_matches_estimate_cost_ordering():
    """worst_case_costがestimate_cost()の単価を使っており、max_tokensが大きいほど高くなること"""
    model = get_model("L3")
    small = worst_case_cost(model, "sys", "user", max_tokens=100)
    large = worst_case_cost(model, "sys", "user", max_tokens=2000)
    assert small < large
    assert small > 0


# --- hidden_thinking_reserve_tokens 対応（2026-08-18） ---

_RESERVE_TARGET_KEYS = ["M3", "M3_G37_EVAL", "L3", "H3", "M4", "L4", "H4"]
_ZERO_RESERVE_KEYS = [
    k for k in MODEL_REGISTRY if k not in _RESERVE_TARGET_KEYS
]


@pytest.mark.parametrize("key", _ZERO_RESERVE_KEYS)
@pytest.mark.parametrize("max_tokens", [16, 64, 200, 400, 500, 2000])
def test_worst_case_cost_zero_reserve_models_unchanged(key, max_tokens):
    """reserve=0のモデル（Anthropic/OpenAI/Kimi/DeepSeek + L7）は、
    複数のmax_tokensにわたって旧式(estimate_cost直呼び)と数値が完全一致すること（非回帰）"""
    model = get_model(key)
    assert model.hidden_thinking_reserve_tokens == 0
    system, user = "system prompt text", "user prompt text"
    approx_input_tokens = (len(system) + len(user)) // 2 + 50
    expected = estimate_cost(model, approx_input_tokens, max_tokens)
    actual = worst_case_cost(model, system, user, max_tokens=max_tokens)
    assert actual == pytest.approx(expected)


@pytest.mark.parametrize("key", ["M3", "M3_G37_EVAL", "L3", "H3"])
def test_worst_case_cost_includes_gemini_hidden_thinking(key):
    """Gemini対象モデルは、予約分だけ旧式(estimate_cost直呼び)より高くなること。
    差分は 512 * output_price / 1_000_000 に厳密一致する"""
    model = get_model(key)
    assert model.hidden_thinking_reserve_tokens == 512
    system, user = "system prompt text", "user prompt text"
    approx_input_tokens = (len(system) + len(user)) // 2 + 50
    old_style = estimate_cost(model, approx_input_tokens, 64)
    new_style = worst_case_cost(model, system, user, max_tokens=64)
    expected_delta = 512 * model.output_price / 1_000_000
    assert new_style > old_style
    assert (new_style - old_style) == pytest.approx(expected_delta)


@pytest.mark.parametrize("key", ["M4", "L4", "H4"])
def test_worst_case_cost_includes_xai_reasoning(key):
    """xAI対象3モデルは、予約分だけ旧式(estimate_cost直呼び)より高くなること。
    差分は各モデルの実測由来reserve * output_priceに厳密一致する。
    Cycle 6.1(2026-08-27): M4/H4はthinking medium実測を反映して引き上げ済み
    （M4: 1024→2560, H4: 1536→9728）。L4はthinking無効化のため不変。"""
    model = get_model(key)
    assert model.hidden_thinking_reserve_tokens == {"L4": 768, "M4": 2560, "H4": 9728}[key]
    system, user = "system prompt text", "user prompt text"
    approx_input_tokens = (len(system) + len(user)) // 2 + 50
    old_style = estimate_cost(model, approx_input_tokens, 64)
    new_style = worst_case_cost(model, system, user, max_tokens=64)
    expected_delta = model.hidden_thinking_reserve_tokens * model.output_price / 1_000_000
    assert new_style > old_style
    assert (new_style - old_style) == pytest.approx(expected_delta)


def test_worst_case_cost_h2_not_double_counted():
    """H2(gpt-5.6-sol, max_completion_tokens系)はreserve対象外(=0)であり、
    hidden thinkingをmax_tokensと別枠で二重計上しないこと（旧式と完全一致）"""
    model = get_model("H2")
    assert model.hidden_thinking_reserve_tokens == 0
    system, user = "system prompt text", "user prompt text"
    approx_input_tokens = (len(system) + len(user)) // 2 + 50
    expected = estimate_cost(model, approx_input_tokens, 400)
    actual = worst_case_cost(model, system, user, max_tokens=400)
    assert actual == pytest.approx(expected)


def test_hidden_thinking_reserve_defaults_to_zero():
    """ModelInfoをアドホック生成した場合、hidden_thinking_reserve_tokensの既定値は0であること"""
    from llm.models import ModelInfo

    model = ModelInfo(
        model_id="test-model", provider="Test", name="Test Model",
        adapter_type="openai_compat", input_price=1.0, output_price=1.0,
        env_key="TEST_API_KEY", base_url=None,
    )
    assert model.hidden_thinking_reserve_tokens == 0


def test_observed_max_thinking_fits_in_reserve():
    """実測された最大hidden thinking量(343token, xAI L4)が予約512token以内に収まることの固定値確認。
    注意: これは実測データの範囲内であることの確認であり、512がprovider保証の絶対上限で
    あることを示すものではない（経験的安全マージン。scripts/model_smoke.py:worst_case_cost参照）"""
    observed_max_thinking_tokens = 343  # Phase 1実測 (logs/model_matrix/run_20260818_041810/phase1_calls.jsonl) grok-4.3(L4)
    reserve = get_model("L4").hidden_thinking_reserve_tokens
    assert observed_max_thinking_tokens < reserve


def test_worst_case_cost_reserve_targets_are_exactly_expected_set():
    """予約>0は既存6件と検証専用M3_G37_EVALだけであること。"""
    reserved = {k for k, v in MODEL_REGISTRY.items() if v.hidden_thinking_reserve_tokens > 0}
    assert reserved == {"M3", "M3_G37_EVAL", "L3", "H3", "M4", "L4", "H4"}


def test_dry_run_does_not_call_api(monkeypatch):
    """--dry-run のときAPIが一切呼ばれないこと"""
    monkeypatch.setattr("scripts.model_smoke.create_adapter", lambda m: _RefuseToCallAdapter(m))
    rc = main(["--model", "L3", "--dry-run"])
    assert rc == 0


def test_calls_over_hard_limit_is_rejected(monkeypatch, tmp_path):
    """--calls がハード上限(5)を超える指定は、APIを呼ばずにエラー終了すること"""
    monkeypatch.setattr("scripts.model_smoke.create_adapter", lambda m: _RefuseToCallAdapter(m))
    rc = main(["--model", "L3", "--calls", str(HARD_MAX_CALLS + 1), "--out", str(tmp_path)])
    assert rc == 1


def test_zero_calls_is_rejected(monkeypatch, tmp_path):
    """--calls 0 はエラー終了すること"""
    monkeypatch.setattr("scripts.model_smoke.create_adapter", lambda m: _RefuseToCallAdapter(m))
    rc = main(["--model", "L3", "--calls", "0", "--out", str(tmp_path)])
    assert rc == 1


def test_budget_guard_prevents_call_when_max_cost_too_small(monkeypatch, tmp_path):
    """--max-cost が極小のとき、コール前に中断しAPIを呼ばないこと"""
    monkeypatch.setattr("scripts.model_smoke.create_adapter", lambda m: _RefuseToCallAdapter(m))
    rc = main(["--model", "L3", "--calls", "1", "--max-cost", "0.0000001", "--out", str(tmp_path)])
    assert rc == 1


def test_success_run_records_jsonl_and_cost(monkeypatch, tmp_path):
    """フェイク成功アダプタでJSONLが書き出され、cost_usdがestimate_cost()と一致すること"""
    monkeypatch.setattr("scripts.model_smoke.create_adapter", lambda m: _FakeOkAdapter(m))
    rc = main(["--model", "L3", "--calls", "1", "--max-cost", "1.0", "--out", str(tmp_path)])
    assert rc == 0

    jsonl_files = list(tmp_path.glob("smoke_L3_*.jsonl"))
    assert len(jsonl_files) == 1
    lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])

    required_keys = {
        "model_key", "model_id", "case", "ok", "error_type", "error_full",
        "latency_ms", "finish_reason", "input_tokens", "output_tokens",
        "total_tokens", "cache_read_input_tokens", "cache_creation_input_tokens",
        "reasoning_tokens", "thinking_diff", "usage_raw",
        "json_extract_ok", "parse_ok", "response_len", "response_head", "cost_usd",
    }
    assert required_keys.issubset(rec.keys())
    assert rec["ok"] is True
    assert rec["json_extract_ok"] is True
    assert rec["parse_ok"] is True

    model = get_model("L3")
    expected_cost = estimate_cost(model, 100, 20, 0, 120)
    assert rec["cost_usd"] == pytest.approx(expected_cost)

    md_files = list(tmp_path.glob("smoke_L3_*.md"))
    assert len(md_files) == 1


def test_failure_run_records_error_and_continues(monkeypatch, tmp_path):
    """AdapterErrorが発生してもok=Falseで記録し、非0で終了すること（残りケースの記録も試みる）"""
    monkeypatch.setattr("scripts.model_smoke.create_adapter", lambda m: _FakeErrorAdapter(m))
    rc = main(["--model", "L3", "--calls", "2", "--max-cost", "1.0", "--out", str(tmp_path)])
    assert rc == 1

    jsonl_files = list(tmp_path.glob("smoke_L3_*.jsonl"))
    assert len(jsonl_files) == 1
    lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
    # ping_json + game_json_1 の2件が両方とも失敗として記録されていること
    assert len(lines) == 2
    for line in lines:
        rec = json.loads(line)
        assert rec["ok"] is False
        assert rec["error_type"] == "AdapterError"
        assert "fake 404 for test" in rec["error_full"]


def test_unknown_model_key_errors_without_calling_api(monkeypatch, tmp_path):
    """存在しないモデルキーはAPIを呼ばずにエラー終了すること"""
    called = []
    monkeypatch.setattr("scripts.model_smoke.create_adapter", lambda m: called.append(1) or _RefuseToCallAdapter(m))
    rc = main(["--model", "NOT_A_REAL_KEY", "--out", str(tmp_path)])
    assert rc == 1
    assert called == []


# --- thinking_matrix suite（DeepSeek L6診断用）のテスト ---
# ここから先はAPIを呼ばない。complete_with_fields()が呼ぶ client.chat.completions.create()
# をフェイククライアントで差し替えて検証する。


class _FakeMessage:
    def __init__(self, content, reasoning_content=None):
        self.content = content
        self.model_extra = {"reasoning_content": reasoning_content} if reasoning_content is not None else {}


class _FakeChoice:
    def __init__(self, message, finish_reason="stop"):
        self.message = message
        self.finish_reason = finish_reason


class _FakeCTD:
    def __init__(self, reasoning_tokens=0):
        self.reasoning_tokens = reasoning_tokens


class _FakePTD:
    def __init__(self, cached_tokens=0, cache_write_tokens=0):
        self.cached_tokens = cached_tokens
        self.cache_write_tokens = cache_write_tokens


class _FakeUsage:
    def __init__(self, prompt_tokens=100, completion_tokens=20, total_tokens=120,
                 reasoning_tokens=0, cached_tokens=0):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.total_tokens = total_tokens
        self.completion_tokens_details = _FakeCTD(reasoning_tokens)
        self.prompt_tokens_details = _FakePTD(cached_tokens)

    def model_dump(self):
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


class _FakeApiResponse:
    def __init__(self, choices, usage):
        self.choices = choices
        self.usage = usage


class _FakeCompletionsNamespace:
    def __init__(self, responder):
        self.calls: list[dict] = []
        self._responder = responder

    def create(self, **kwargs):
        self.calls.append(kwargs)
        return self._responder(kwargs)


class _FakeChatNamespace:
    def __init__(self, completions: _FakeCompletionsNamespace):
        self.completions = completions


class _FakeSdkClient:
    def __init__(self, responder):
        self.completions = _FakeCompletionsNamespace(responder)
        self.chat = _FakeChatNamespace(self.completions)


class _FakeThinkingAdapter:
    """complete_with_fields()専用のフェイクアダプタ。_get_client()が固定のfakeクライアントを返す"""

    def __init__(self, model_info, responder):
        self.model_info = model_info
        self._client = _FakeSdkClient(responder)

    def _get_client(self):
        return self._client


class _FakeThinkingOkAdapter:
    """main()のthinking_matrixスイート end-to-end テスト用。常に正常JSONを返す"""

    def __init__(self, model_info):
        self.model_info = model_info
        self._client = _FakeSdkClient(lambda kwargs: _FakeApiResponse(
            [_FakeChoice(_FakeMessage(content='{"strategy": {"reason": "t"}, "action": {"type": "pass"}}'), "stop")],
            _FakeUsage(reasoning_tokens=50),
        ))

    def _get_client(self):
        return self._client


def test_thinking_matrix_suite_case_breakdown():
    """--suite thinking_matrix が指定通りの8条件（条件名×回数、max_tokens）を返すこと"""
    model = get_model("L6")
    cases = build_thinking_matrix_cases(model)
    assert len(cases) == 8
    conditions = [c["condition"] for c in cases]
    assert conditions == [
        "baseline_4000", "baseline_4000",
        "nothink_4000",
        "nothink_2000", "nothink_2000", "nothink_2000",
        "nothink_1000", "nothink_1000",
    ]
    max_tokens_list = [c["max_tokens"] for c in cases]
    assert max_tokens_list == [4000, 4000, 4000, 2000, 2000, 2000, 1000, 1000]
    # 全ケースがnegotiationフェイズ・同一プロンプトであること
    for c in cases:
        assert c["phase"] == "negotiation"
        assert c["user"] == cases[0]["user"]
        assert c["system"] == cases[0]["system"]


def test_thinking_matrix_extra_params_no_reasoning_effort():
    """baselineはextra_params=None、nothink系はthinking disabled。reasoning_effortはどのケースにも無いこと"""
    model = get_model("L6")
    cases = build_thinking_matrix_cases(model)
    for c in cases:
        extra = c.get("extra_params") or {}
        assert "reasoning_effort" not in extra
        if c["condition"] == "baseline_4000":
            assert c["extra_params"] is None
        else:
            assert c["extra_params"] == THINKING_DISABLED == {"thinking": {"type": "disabled"}}


def test_thinking_matrix_does_not_mutate_model_registry():
    """build_thinking_matrix_cases() 実行前後でMODEL_REGISTRY['L6']が無改変であること

    2026-08-17: 8コール診断実験の結果、L6は恒久的にthinking無効化+max_tokens=2000を
    登録済み（llm/models.py）。ここではその恒久値自体を固定せず、
    「実験ケース構築がレジストリを書き換えないこと」だけを検証する。
    """
    before_extra = MODEL_REGISTRY["L6"].extra_params
    before_max_tokens = MODEL_REGISTRY["L6"].max_tokens
    model = get_model("L6")
    build_thinking_matrix_cases(model)
    assert MODEL_REGISTRY["L6"].extra_params == before_extra
    assert MODEL_REGISTRY["L6"].max_tokens == before_max_tokens
    # 恒久値そのものの確認（2026-08-17是正後の期待値）
    assert MODEL_REGISTRY["L6"].extra_params == {"thinking": {"type": "disabled"}}
    assert MODEL_REGISTRY["L6"].max_tokens == 2000


def test_complete_with_fields_sends_extra_body_per_call():
    """extra_paramsがcall単位でextra_bodyとして送信され、Noneのときはextra_bodyキー自体が付かないこと"""
    model = get_model("L6")
    responder = lambda kwargs: _FakeApiResponse(
        [_FakeChoice(_FakeMessage(content='{"ok": true}'), "stop")], _FakeUsage()
    )
    adapter = _FakeThinkingAdapter(model, responder)

    complete_with_fields(adapter, "sys", "user", max_tokens=2000, temperature=0.7, extra_params=None)
    complete_with_fields(
        adapter, "sys", "user", max_tokens=2000, temperature=0.7,
        extra_params={"thinking": {"type": "disabled"}},
    )

    calls = adapter._client.completions.calls
    assert len(calls) == 2
    assert "extra_body" not in calls[0]
    assert calls[1]["extra_body"] == {"thinking": {"type": "disabled"}}
    assert calls[0]["max_tokens"] == 2000
    assert calls[0]["model"] == model.model_id


def test_complete_with_fields_separates_content_and_reasoning_content():
    """content空・reasoning_content非空のときtext_source='reasoning_content'として分離記録されること"""
    model = get_model("L6")
    responder = lambda kwargs: _FakeApiResponse(
        [_FakeChoice(_FakeMessage(content="", reasoning_content="thinking out loud, no json here"), "length")],
        _FakeUsage(reasoning_tokens=4000, completion_tokens=4000, total_tokens=4483),
    )
    adapter = _FakeThinkingAdapter(model, responder)

    result = complete_with_fields(adapter, "sys", "user", max_tokens=4000, temperature=0.7, extra_params=None)

    assert result["text_source"] == "reasoning_content"
    assert result["content"] == ""
    assert result["reasoning_content"] == "thinking out loud, no json here"
    assert result["text"] == "thinking out loud, no json here"
    assert result["usage"]["finish_reason"] == "length"
    assert result["usage"]["reasoning_tokens"] == 4000


def test_run_thinking_case_records_fallback_and_json_failure():
    """length切断＋reasoning_contentフォールバック時、used_reasoning_fallback/JSON失敗が正しく記録されること"""
    model = get_model("L6")
    responder = lambda kwargs: _FakeApiResponse(
        [_FakeChoice(_FakeMessage(content="", reasoning_content="we need think... " * 5), "length")],
        _FakeUsage(reasoning_tokens=4000, completion_tokens=4000, total_tokens=4483),
    )
    adapter = _FakeThinkingAdapter(model, responder)
    case = {
        "case": "nothink_test_1", "condition": "nothink_test",
        "system": "sys", "user": "user", "max_tokens": 4000, "phase": "negotiation",
        "extra_params": {"thinking": {"type": "disabled"}}, "temperature": 0.7,
    }

    rec = run_thinking_case("L6", model, adapter, case, 1)

    assert rec["ok"] is True
    assert rec["used_reasoning_fallback"] is True
    assert rec["text_source"] == "reasoning_content"
    assert rec["finish_reason"] == "length"
    assert rec["json_extract_ok"] is False
    assert rec["parse_ok"] is False
    assert rec["sent_extra_body"] == {"thinking": {"type": "disabled"}}
    assert rec["sent_max_tokens"] == 4000
    assert rec["condition"] == "nothink_test"


def test_run_thinking_case_records_clean_json_success():
    """正常stop・contentにJSONが入っている場合、fallbackなし・JSON成功として記録されること"""
    model = get_model("L6")
    responder = lambda kwargs: _FakeApiResponse(
        [_FakeChoice(_FakeMessage(
            content='{"strategy": {"reason": "ok"}, "action": {"type": "pass"}}'), "stop")],
        _FakeUsage(reasoning_tokens=120, completion_tokens=140, total_tokens=240),
    )
    adapter = _FakeThinkingAdapter(model, responder)
    case = {
        "case": "nothink_2000_1", "condition": "nothink_2000",
        "system": "sys", "user": "user", "max_tokens": 2000, "phase": "negotiation",
        "extra_params": {"thinking": {"type": "disabled"}}, "temperature": 0.7,
    }

    rec = run_thinking_case("L6", model, adapter, case, 1)

    assert rec["ok"] is True
    assert rec["used_reasoning_fallback"] is False
    assert rec["text_source"] == "content"
    assert rec["finish_reason"] == "stop"
    assert rec["json_extract_ok"] is True
    assert rec["parse_ok"] is True
    assert rec["has_extraneous_prose"] is False


def test_hard_max_calls_is_eight():
    """thinking_matrix(8条件)を1回の実行で回せるようHARD_MAX_CALLSが8であること"""
    assert HARD_MAX_CALLS == 8


def test_thinking_matrix_dry_run_does_not_call_api(monkeypatch):
    """--suite thinking_matrix --dry-run のときAPIが一切呼ばれないこと"""
    monkeypatch.setattr("scripts.model_smoke.create_adapter", lambda m: _RefuseToCallAdapter(m))
    rc = main(["--model", "L6", "--suite", "thinking_matrix", "--dry-run"])
    assert rc == 0


def test_thinking_matrix_main_run_writes_condition_summary(monkeypatch, tmp_path):
    """--suite thinking_matrix のmain()実行がcondition付きJSONLと条件別集計表入りMDを書き出すこと"""
    monkeypatch.setattr("scripts.model_smoke.create_adapter", lambda m: _FakeThinkingOkAdapter(m))
    rc = main([
        "--model", "L6", "--suite", "thinking_matrix",
        "--calls", "3", "--max-cost", "1.0", "--out", str(tmp_path),
    ])
    assert rc == 0

    jsonl_files = list(tmp_path.glob("smoke_L6_thinking_matrix_*.jsonl"))
    assert len(jsonl_files) == 1
    lines = jsonl_files[0].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 3
    recs = [json.loads(line) for line in lines]
    assert [r["condition"] for r in recs] == ["baseline_4000", "baseline_4000", "nothink_4000"]
    assert all(r["ok"] for r in recs)

    md_files = list(tmp_path.glob("smoke_L6_thinking_matrix_*.md"))
    assert len(md_files) == 1
    md_text = md_files[0].read_text(encoding="utf-8")
    assert "条件別集計" in md_text

    # main()実行後もMODEL_REGISTRYが無改変であること（2026-08-17是正後の恒久値のまま）
    assert MODEL_REGISTRY["L6"].extra_params == {"thinking": {"type": "disabled"}}
    assert MODEL_REGISTRY["L6"].max_tokens == 2000
