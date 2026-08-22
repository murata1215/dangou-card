"""Gemini 3.7 evaluation harness tests.  No test calls an external API."""

from types import SimpleNamespace

from llm.models import MODEL_REGISTRY
from scripts import gemini_37_eval as eval37


class _FakeAdapter:
    def __init__(self, responses):
        self.responses = iter(responses)

    def complete(self, system, messages, max_tokens=2000):
        return next(self.responses), {
            "input_tokens": 100,
            "output_tokens": 20,
            "total_tokens": 130,
            "finish_reason": "stop",
            "response_model": "gemini-3.7-flash",
        }


def test_eval_key_is_separate_from_official_m3_and_disables_sampling():
    assert MODEL_REGISTRY["M3"].model_id == "gemini-3.5-flash"
    model = eval37._effective_model("M3_G37_EVAL", 2000)
    assert model.model_id == "gemini-3.7-flash"
    assert model.max_tokens == 2000
    assert model.supports_temperature is False
    assert MODEL_REGISTRY["M3_G37_EVAL"].supports_temperature is False


def test_gemini_low_thinking_uses_only_openai_compat_reasoning_effort():
    model = eval37._effective_model("M3_G37_EVAL", 2000)
    assert eval37._request_options(model, "low") == {"reasoning_effort": "low"}


def test_phase3_uses_the_same_low_thinking_policy_and_recorded_standard_prices():
    for key in ("M3", "M3_G37_EVAL"):
        model = eval37._effective_model(key, 2000)
        assert eval37._request_options(model, "low") == {"reasoning_effort": "low"}
        assert eval37._phase3_preflight_error(key, model, "low") is None


def test_each_case_contains_a_production_prompt_with_japanese_dm_or_memory():
    for case in eval37.CASE_NAMES:
        phase, system, prompt = eval37.build_case(case)
        assert phase == case
        assert system
        assert prompt
    assert "P02" in eval37.build_case("negotiation")[2]
    assert "借金返済を優先" in eval37.build_case("commit")[2]
    assert "契約 C02" in eval37.build_case("negotiation")[2]
    assert "トレード T01" in eval37.build_case("negotiation")[2]


def test_loan_case_records_hidden_thinking_difference():
    model = eval37._effective_model("M3_G37_EVAL", 2000)
    records = eval37.run_case(_FakeAdapter([
        '{"strategy":{"emotion":"焦"},"action":{"amount":3000000}}',
    ]), model, "loan_choice", 1)
    assert len(records) == 1
    assert records[0]["ok"] is True
    assert records[0]["thinking_diff"] == 10
    assert records[0]["cost_usd"] > 0


def test_invalid_json_is_retried_once_with_a_separate_auditable_record():
    model = eval37._effective_model("M3_G37_EVAL", 2000)
    records = eval37.run_case(
        _FakeAdapter(["not json", '{"strategy":{"emotion":"焦"},"action":{"amount":3000000}}']), model, "loan_choice", 1,
    )
    assert [r["retry_count"] for r in records] == [0, 1]
    assert records[0]["ok"] is False
    assert records[1]["ok"] is True


def _smoke_args(**overrides):
    values = {
        "model": "M3_G37_EVAL", "max_tokens": 2000, "max_calls": 5,
        "max_cost": 0.10, "thinking": "low", "calls": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _SmokeAdapter:
    def __init__(self, usages):
        self.usages = iter(usages)
        self.spent_usd = 0.0

    def complete(self, system, messages, max_tokens=2000):
        usage = next(self.usages)
        self.spent_usd += 0.001
        return '{"ok": true}', usage


def _good_usage(**overrides):
    usage = {
        "input_tokens": 100, "output_tokens": 20, "total_tokens": 130,
        "finish_reason": "stop", "response_model": "gemini-3.7-flash",
        "reasoning_tokens": 10,
    }
    usage.update(overrides)
    return usage


def test_smoke_runs_three_clean_calls_with_retry_zero(monkeypatch, tmp_path):
    monkeypatch.setattr(eval37, "_make_adapter", lambda *args: _SmokeAdapter([_good_usage()] * 3))
    assert eval37.run_smoke(_smoke_args(), tmp_path) == 0
    summary = (tmp_path / "smoke_summary.json").read_text(encoding="utf-8")
    assert '"status": "pass"' in summary
    records = (tmp_path / "smoke_calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(records) == 3
    assert all('"transport_retry_count": 0' in record for record in records)


def test_smoke_stops_immediately_on_returned_model_mismatch(monkeypatch, tmp_path):
    monkeypatch.setattr(eval37, "_make_adapter", lambda *args: _SmokeAdapter([
        _good_usage(response_model="unexpected-model"), _good_usage(), _good_usage(),
    ]))
    assert eval37.run_smoke(_smoke_args(), tmp_path) == 1
    records = (tmp_path / "smoke_calls.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(records) == 1
    assert "returned model mismatch" in records[0]


def test_smoke_stops_on_unknown_usage(monkeypatch, tmp_path):
    monkeypatch.setattr(eval37, "_make_adapter", lambda *args: _SmokeAdapter([
        _good_usage(total_tokens=None), _good_usage(), _good_usage(),
    ]))
    assert eval37.run_smoke(_smoke_args(), tmp_path) == 1
    assert "usage unknown" in (tmp_path / "smoke_summary.json").read_text(encoding="utf-8")


def test_phase2_requires_an_explicit_valid_emotion():
    model = eval37._effective_model("M3_G37_EVAL", 2000)
    records = eval37.run_case(_FakeAdapter(['{"action":{"amount":3000000}}']), model, "loan_choice", 0)
    assert records[0]["ok"] is False
    assert "emotion" in records[0]["error"]


def test_phase2_stops_remaining_cases_on_model_mismatch(monkeypatch, tmp_path):
    class Adapter(_SmokeAdapter):
        def complete(self, system, messages, max_tokens=2000):
            usage = next(self.usages)
            self.spent_usd += 0.001
            return '{"strategy":{"emotion":"楽"},"action":{"amount":3000000}}', usage

    monkeypatch.setattr(eval37, "_make_adapter", lambda *args: Adapter([
        _good_usage(response_model="wrong-model"), _good_usage(), _good_usage(), _good_usage(),
    ]))
    args = SimpleNamespace(
        model="M3_G37_EVAL", max_tokens=2000, max_calls=8, max_cost=0.12,
        thinking="low", max_parser_corrections=1, cases="loan_choice,negotiation,commit,double_up",
    )
    assert eval37.run_json(args, tmp_path) == 1
    assert len((tmp_path / "json_calls.jsonl").read_text(encoding="utf-8").splitlines()) == 1


class _CompareAdapter:
    def __init__(self, model_id, responses):
        self.model_id = model_id
        self.responses = iter(responses)
        self.spent_usd = 0.0

    def complete(self, system, messages, max_tokens=2000):
        text, overrides = next(self.responses)
        self.spent_usd += 0.001
        usage = _good_usage(response_model=self.model_id)
        usage.update(overrides)
        return text, usage


def _compare_args(**overrides):
    values = {
        "models": "M3,M3_G37_EVAL", "cases": "loan_choice", "repeats": 2,
        "max_tokens": 2000, "max_calls": 8, "max_cost": 0.48,
        "thinking": "low", "max_parser_corrections": 1,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_compare_interleaves_models_and_records_cost_statistics(monkeypatch, tmp_path):
    adapters = {}

    def make_adapter(model, *args):
        adapter = _CompareAdapter(model.model_id, [
            ('{"strategy":{"emotion":"楽"},"action":{"amount":3000000}}', {}),
            ('{"strategy":{"emotion":"楽"},"action":{"amount":3000000}}', {}),
        ])
        adapters[model.model_id] = adapter
        return adapter

    monkeypatch.setattr(eval37, "_make_adapter", make_adapter)
    assert eval37.run_compare(_compare_args(), tmp_path) == 0
    rows = [__import__("json").loads(line) for line in (tmp_path / "compare_calls.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [row["model_key"] for row in rows] == ["M3", "M3_G37_EVAL", "M3_G37_EVAL", "M3"]
    summary = __import__("json").loads((tmp_path / "compare_summary.json").read_text(encoding="utf-8"))
    assert summary["status"] == "pass"
    assert summary["models"]["M3"]["latency_ms"]["p95"] >= 0
    assert summary["models"]["M3_G37_EVAL"]["cost_usd_per_successful_json"] is not None


def test_compare_stops_only_the_failed_model_and_keeps_other_model_running(monkeypatch, tmp_path):
    def make_adapter(model, *args):
        if model.model_id == "gemini-3.5-flash":
            responses = [
                ('{"strategy":{"emotion":"楽"},"action":{"amount":3000000}}', {"response_model": "wrong"}),
            ]
        else:
            responses = [
                ('{"strategy":{"emotion":"楽"},"action":{"amount":3000000}}', {}),
                ('{"strategy":{"emotion":"楽"},"action":{"amount":3000000}}', {}),
            ]
        return _CompareAdapter(model.model_id, responses)

    monkeypatch.setattr(eval37, "_make_adapter", make_adapter)
    assert eval37.run_compare(_compare_args(), tmp_path) == 1
    summary = __import__("json").loads((tmp_path / "compare_summary.json").read_text(encoding="utf-8"))
    assert summary["models"]["M3"]["status"] == "stopped"
    assert summary["models"]["M3_G37_EVAL"]["status"] == "completed"
