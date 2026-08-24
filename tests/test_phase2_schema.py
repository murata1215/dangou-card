"""Phase 2 Structured Outputs are locally validated without provider calls."""

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

import scripts.model_matrix as mm
from llm.adapters import AnthropicAdapter, OpenAICompatAdapter
from llm.models import MODEL_REGISTRY
from llm.phase2_schema import (
    build_h1_phase2_light_schema,
    build_phase2_response_schema,
    normalize_h1_phase2_transport,
    schema_complexity,
    structured_schema_input_reserve_tokens,
    validate_h1_phase2_light_schema,
    validate_phase2_schema_complexity,
)
from llm.response_parser import parse_response


def _transport(action_type: str = "pass", **values):
    data = {
        "action_type": action_type, "emotion": "楽", "to": "", "message": "", "amount": 0,
        "with_players_json": "[]", "terms_json": "[]", "contract_id": "",
        "bounty_type": "", "condition_type": "", "condition_target_player": "",
        "round_num": 0, "anonymous": False, "beneficiary": "", "bounty_id": "",
        "give_card": "", "receive_card": "", "cash_amount": 0, "trade_id": "",
    }
    data.update(values)
    return data


def test_phase2_schema_is_canonical_and_within_anthropic_complexity_limits():
    schema = build_phase2_response_schema()
    counts = validate_phase2_schema_complexity(schema)

    assert counts == {
        "optional_parameters": 24,
        "union_parameters": 0,
        "objects": 6,
        "objects_with_additional_properties_false": 6,
    }
    assert schema["required"] == ["strategy", "action"]
    assert schema["properties"]["strategy"]["required"] == ["emotion"]

    action = schema["properties"]["action"]
    assert action["required"] == ["type", "beneficiary"]
    assert set(action["properties"]) == {
        "type", "beneficiary", "to", "message", "amount", "with", "terms",
        "contract_id", "bounty_type", "condition_type", "condition", "round_num",
        "anonymous", "bounty_id", "with_players", "give_card", "receive_card",
        "cash_amount", "trade_id",
    }
    for alias in ("with_terms", "round", "cash", "card", "rank"):
        assert alias not in action["properties"]


def test_phase2_schema_validator_rejects_an_optional_parameter_overflow():
    schema = deepcopy(build_phase2_response_schema())
    schema["properties"]["action"]["required"].remove("beneficiary")

    assert schema_complexity(schema)["optional_parameters"] == 25
    with pytest.raises(ValueError, match="optional-parameter"):
        validate_phase2_schema_complexity(schema)


def test_required_beneficiary_sentinel_preserves_non_bounty_semantics():
    strategy, action = parse_response(
        '{"strategy":{"emotion":"楽"},"action":{"type":"pass","beneficiary":""}}',
        "P01", "negotiation",
    )

    assert strategy == {"emotion": "楽"}
    assert action.__class__.__name__ == "PassAction"


def test_phase2_options_are_limited_to_h1_m3_h3_and_keep_h1_light_schema_scoped():
    h1 = mm._phase2_request_options(MODEL_REGISTRY["H1"])
    m3 = mm._phase2_request_options(MODEL_REGISTRY["M3"])
    h3 = mm._phase2_request_options(MODEL_REGISTRY["H3"])

    assert mm._phase2_request_options(MODEL_REGISTRY["L1"]) == {}
    assert h1["thinking"] == {"type": "disabled"}
    assert "effort" not in h1
    assert "effort" not in h1["output_config"]
    assert m3["reasoning_effort"] == "minimal"
    assert h3["reasoning_effort"] == "low"
    for options in (m3, h3):
        assert "thinking_level" not in options
        assert "thinking_budget" not in options

    h1_schema = h1["output_config"]["format"]["schema"]
    canonical_schema = m3["response_format"]["json_schema"]["schema"]
    assert h1_schema == build_h1_phase2_light_schema()
    assert canonical_schema == build_phase2_response_schema()
    assert canonical_schema == h3["response_format"]["json_schema"]["schema"]
    assert h1_schema != canonical_schema


def test_h1_light_schema_has_fixed_flat_complexity_and_no_prompt_alias():
    schema = build_h1_phase2_light_schema()
    assert validate_h1_phase2_light_schema(schema) == {
        "optional_parameters": 0,
        "union_parameters": 0,
        "objects": 1,
        "objects_with_additional_properties_false": 1,
    }
    assert len(schema["properties"]) == 19
    enum_fields = [value for value in schema["properties"].values() if "enum" in value]
    assert len(enum_fields) == 4
    assert sum(len(value["enum"]) for value in enum_fields) == 28
    assert len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode()) <= 1320
    assert mm._phase2_user_content(MODEL_REGISTRY["H1"]) != mm.GAME2_USER
    assert '"strategy"' not in mm._phase2_user_content(MODEL_REGISTRY["H1"])
    assert '"action"' not in mm._phase2_user_content(MODEL_REGISTRY["H1"])


def test_h1_adapter_sends_disabled_thinking_structured_output_without_temperature_or_effort():
    captured: dict[str, object] = {}

    class Messages:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                content=[SimpleNamespace(text=json.dumps(_transport(), ensure_ascii=False))],
                usage=SimpleNamespace(input_tokens=1, output_tokens=1,
                                      cache_creation_input_tokens=0, cache_read_input_tokens=0,
                                      output_tokens_details=None),
                stop_reason="end_turn", model=MODEL_REGISTRY["H1"].model_id,
            )

    adapter = AnthropicAdapter(MODEL_REGISTRY["H1"], max_retries=0, allow_temperature_fallback=False)
    adapter._client = SimpleNamespace(messages=Messages())
    text, _ = adapter.complete(
        "sys", [{"role": "user", "content": "hi"}], max_tokens=400,
        request_options=mm._phase2_request_options(MODEL_REGISTRY["H1"]),
    )

    assert text
    assert captured["max_tokens"] == 400
    assert captured["thinking"] == {"type": "disabled"}
    assert "temperature" not in captured
    assert "effort" not in captured
    assert "effort" not in captured["output_config"]
    assert captured["output_config"]["format"]["type"] == "json_schema"


@pytest.mark.parametrize(("key", "reasoning_effort"), [("M3", "minimal"), ("H3", "low")])
def test_gemini_adapter_sends_model_specific_reasoning_and_structured_output_only(key, reasoning_effort):
    captured: dict[str, object] = {}

    class Completions:
        def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content="{}", model_extra={}), finish_reason="stop")],
                usage=SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2,
                                      completion_tokens_details=None, prompt_tokens_details=None),
                model=MODEL_REGISTRY[key].model_id,
            )

    adapter = OpenAICompatAdapter(MODEL_REGISTRY[key], max_retries=0, allow_temperature_fallback=False)
    adapter._client = SimpleNamespace(chat=SimpleNamespace(completions=Completions()))
    adapter.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=512,
                     request_options=mm._phase2_request_options(MODEL_REGISTRY[key]))

    assert captured["reasoning_effort"] == reasoning_effort
    assert "thinking_level" not in captured
    assert "thinking_budget" not in captured
    assert captured["response_format"]["type"] == "json_schema"


def test_phase2_structured_schema_reserve_is_budgeted_only_for_target_models():
    system = mm.build_system_prompt("P01", mm.GameConfig.baseline_v1_s2(num_players=18))
    reserve = structured_schema_input_reserve_tokens(build_h1_phase2_light_schema())

    assert reserve > 0
    h1_user = mm._phase2_user_content(MODEL_REGISTRY["H1"])
    h1 = mm._phase2_worst_case_cost(MODEL_REGISTRY["H1"], system, 400)
    h1_without_schema = mm._worst_case_cost(MODEL_REGISTRY["H1"], system, h1_user, 400)
    assert h1 > h1_without_schema
    assert h1 < 0.03
    assert mm._phase2_worst_case_cost(MODEL_REGISTRY["L1"], system, 400) == pytest.approx(
        mm._worst_case_cost(MODEL_REGISTRY["L1"], system, mm.GAME2_USER, 400)
    )


def test_gemini_phase2_overrides_and_reserves_are_model_specific():
    system = mm.build_system_prompt("P01", mm.GameConfig.baseline_v1_s2(num_players=18))

    assert mm._phase2_effective_max_tokens(MODEL_REGISTRY["M3"], 400) == 512
    assert mm._phase2_effective_max_tokens(MODEL_REGISTRY["H3"], 400) == 912
    # 2026-08-23サイクル「全当事者合意による契約解除（contract_cancel）」でアクションカタログに
    # contract_cancel の説明文を追加し、system prompt長が変化したため期待値を更新（意図的な
    # 一度きりの変更。このコミットを跨ぐキャッシュ率・コスト予約値は比較不可）。
    # Cycle 2（2026-08-24）: RULES_SUMMARYの強制最低返済の説明文を実装（除数の定義）に
    # 合わせて訂正したことでsystem prompt長が変化したため、再度期待値を更新。
    # Cycle 3（2026-08-24）: anonymous_broadcast/bounty_cancel のJSON action形式を
    # アクションカタログへ追加（Plan E1/E3、PROMPT_DISCOVERY_GAPの是正）したことで
    # system prompt長が変化したため、三度目の期待値更新。
    assert mm._phase2_worst_case_cost(MODEL_REGISTRY["M3"], system, 512) == pytest.approx(0.014994)
    assert mm._phase2_worst_case_cost(MODEL_REGISTRY["H3"], system, 912) == pytest.approx(0.024792)
    assert mm._phase2_worst_case_cost(MODEL_REGISTRY["M3"], system, 512) < 0.03
    assert mm._phase2_worst_case_cost(MODEL_REGISTRY["H3"], system, 912) < 0.03


def test_h1_phase2_timeout_override_is_scoped_to_the_phase2_adapter_copy(monkeypatch):
    captured: dict[str, object] = {}

    class Adapter:
        def complete(self, **kwargs):
            captured["complete_kwargs"] = kwargs
            return (json.dumps(_transport(), ensure_ascii=False),
                    {"input_tokens": 1, "output_tokens": 1, "response_model": MODEL_REGISTRY["H1"].model_id})

    def factory(info, **kwargs):
        captured["model"] = info
        captured["factory_kwargs"] = kwargs
        return Adapter()

    monkeypatch.setattr(mm, "create_adapter", factory)
    result = mm._call_once_phase2(MODEL_REGISTRY["H1"], 400)

    effective = captured["model"]
    assert effective is not MODEL_REGISTRY["H1"]
    assert effective.timeout_seconds == 300
    assert MODEL_REGISTRY["H1"].timeout_seconds == 60
    assert captured["factory_kwargs"] == {"max_retries": 0, "allow_temperature_fallback": False}
    assert captured["complete_kwargs"]["messages"] == [{
        "role": "user", "content": mm._phase2_user_content(MODEL_REGISTRY["H1"]),
    }]
    assert result["parse_ok"] is True


@pytest.mark.parametrize("key", ["M1", "L1", "M3", "H3"])
def test_phase2_timeout_override_does_not_leak_to_other_models(key):
    model = MODEL_REGISTRY[key]
    assert mm._phase2_effective_model(model) is model
    assert mm._phase2_effective_model(model).timeout_seconds == model.timeout_seconds


def test_h1_phase2_client_uses_300_seconds_and_timeout_does_not_retry(monkeypatch):
    import anthropic

    captured: dict[str, object] = {}

    class TimeoutMessages:
        calls = 0

        def create(self, **kwargs):
            self.calls += 1
            raise TimeoutError("fake timeout")

    messages = TimeoutMessages()

    class FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)
            self.messages = messages

    monkeypatch.setattr(anthropic, "Anthropic", FakeClient)
    monkeypatch.setenv(MODEL_REGISTRY["H1"].env_key, "fake-key")
    adapter = AnthropicAdapter(mm._phase2_effective_model(MODEL_REGISTRY["H1"]), max_retries=0,
                               allow_temperature_fallback=False)

    with pytest.raises(Exception, match="Anthropic API error"):
        adapter.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=400,
                         request_options=mm._phase2_request_options(MODEL_REGISTRY["H1"]))

    assert captured["max_retries"] == 0
    assert captured["timeout"].read == 300
    assert messages.calls == 1


@pytest.mark.parametrize(
    ("action_type", "values", "expected_keys", "action_class"),
    [
        ("pass", {}, {"type"}, "PassAction"),
        ("dm", {"to": "P02", "message": "相談"}, {"type", "to", "message"}, "DmAction"),
        ("broadcast", {"message": "公開提案"}, {"type", "message"}, "BroadcastAction"),
        ("transfer", {"to": "P02", "amount": 100}, {"type", "to", "amount"}, "TransferAction"),
        ("repay", {"amount": 100}, {"type", "amount"}, "RepayAction"),
        ("contract_propose", {
            "with_players_json": json.dumps(["P02", "P03"]),
            "terms_json": json.dumps([
                {"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment", "round_num": 4, "details": {"amount": 100}},
                {"obligor": "P02", "counterparty": "P01", "ob_type": "type_b_market", "round_num": 4, "details": {"market_id": "M01"}},
                {"obligor": "P03", "counterparty": "P01", "ob_type": "type_b_card", "round_num": 4, "details": {"card_rank": "FLUSH"}},
            ]),
        }, {"type", "with", "terms"}, "ContractProposeAction"),
        ("contract_sign", {"contract_id": "C_abc"}, {"type", "contract_id"}, "ContractSignAction"),
        ("contract_cancel", {"contract_id": "C_abc"}, {"type", "contract_id"}, "ContractCancelAction"),
        ("anonymous_broadcast", {"message": "匿名"}, {"type", "message"}, "AnonymousBroadcastAction"),
        ("bounty_post", {
            "amount": 100, "bounty_type": "achievement", "condition_type": "same_market",
            "condition_target_player": "P02", "round_num": 4, "anonymous": True,
        }, {"type", "amount", "bounty_type", "condition_type", "condition", "beneficiary", "round_num", "anonymous"}, "BountyPostAction"),
        ("bounty_cancel", {"bounty_id": "B_abc"}, {"type", "bounty_id"}, "BountyCancelAction"),
        ("card_trade_propose", {
            "with_players_json": json.dumps(["P02", "P03"]), "give_card": "ONE_PAIR",
            "receive_card": "FLUSH", "cash_amount": -50,
        }, {"type", "with_players", "give_card", "receive_card", "cash_amount"}, "CardTradeProposeAction"),
        ("card_trade_accept", {"trade_id": "T_abc"}, {"type", "trade_id"}, "CardTradeAcceptAction"),
        ("card_trade_reject", {"trade_id": "T_abc"}, {"type", "trade_id"}, "CardTradeRejectAction"),
    ],
)
def test_h1_transport_round_trips_each_action_through_the_existing_parser(
    action_type, values, expected_keys, action_class,
):
    canonical = normalize_h1_phase2_transport(_transport(action_type, **values))
    assert set(canonical["action"]) == expected_keys
    strategy, action = parse_response(json.dumps(canonical, ensure_ascii=False), "P01", "negotiation")
    assert strategy == {"emotion": "楽"}
    assert action.__class__.__name__ == action_class


@pytest.mark.parametrize(
    "action_type,field,value",
    [
        ("pass", "message", "unexpected"),
        ("dm", "amount", 1),
        ("contract_propose", "bounty_type", "achievement"),
        ("bounty_post", "trade_id", "T_abc"),
        ("card_trade_propose", "amount", 1),
    ],
)
def test_h1_transport_rejects_non_sentinel_unused_fields(action_type, field, value):
    with pytest.raises(ValueError, match="sentinel"):
        normalize_h1_phase2_transport(_transport(action_type, **{field: value}))


@pytest.mark.parametrize(
    "field,value",
    [
        ("with_players_json", '"[\\\"P02\\\"]"'),
        ("with_players_json", "null"),
        ("with_players_json", '{"player":"P02"}'),
        ("terms_json", '"[]"'),
        ("terms_json", "null"),
        ("terms_json", '{"term": 1}'),
        ("terms_json", '[{"obligor":"P01"}]'),
        ("terms_json", '[{"obligor":"P01","counterparty":"P02","ob_type":"type_a_payment","round_num":1,"details":{},"extra":true}]'),
    ],
)
def test_h1_transport_rejects_malformed_json_string_fields(field, value):
    with pytest.raises(ValueError):
        normalize_h1_phase2_transport(_transport("pass", **{field: value}))
