"""
llm/models.py の18モデルレジストリ（H1〜H6 + M1〜M6 + L1〜L7）の不変条件テスト。

実APIは一切呼ばない（オフライン専用）。既存13エントリのスナップショットで
H追加作業による既存エントリへの誤爆を即検知する。
"""

from dataclasses import fields

import pytest

from llm.models import (
    MODEL_REGISTRY,
    ModelInfo,
    TIER_ORDER,
    VENDOR_ORDER,
    get_model,
    get_models_by_tier,
    get_model_keys_by_id,
    list_vendor_endpoints,
    estimate_cost,
)
from llm.adapters import create_adapter


CORE_18_KEYS = [f"{t}{i}" for t in ("H", "M", "L") for i in range(1, 7)]


def test_18_core_keys_present():
    for key in CORE_18_KEYS:
        assert key in MODEL_REGISTRY, f"{key} が MODEL_REGISTRY に存在しない"


def test_legacy_entries_frozen():
    """H追加作業でM1〜M6/L1〜L7の既存フィールドが変更されていないことをスナップショット比較で保証する"""
    expected = {
        "M1": dict(model_id="claude-sonnet-5", provider="Anthropic", name="Claude Sonnet 5",
                    adapter_type="anthropic", input_price=2.0, output_price=10.0,
                    env_key="CLAUDE_API_KEY", base_url=None, tier="M"),
        "M2": dict(model_id="gpt-4.1", provider="OpenAI", name="GPT-4.1",
                    adapter_type="openai_compat", input_price=2.0, output_price=8.0,
                    env_key="OPENAI_API_KEY", base_url=None, cached_input_price=0.50, tier="M"),
        "M3": dict(model_id="gemini-3.5-flash", provider="Google", name="Gemini 3.5 Flash",
                    adapter_type="gemini", input_price=1.50, output_price=9.00,
                    env_key="GEMINI_API_KEY", tier="M"),
        "M4": dict(model_id="grok-4.5", provider="xAI", name="Grok 4.5",
                    adapter_type="openai_compat", input_price=2.0, output_price=6.0,
                    env_key="GROK_API_KEY", base_url="https://api.x.ai/v1",
                    cached_input_price=0.30, tier="M"),
        "M5": dict(model_id="kimi-k2.6", provider="Moonshot", name="Kimi K2.6",
                    adapter_type="openai_compat", input_price=0.95, output_price=4.0,
                    env_key="KIMI_API_KEY", timeout_seconds=90,
                    extra_params={"thinking": {"type": "disabled"}}, tier="M"),
        "M6": dict(model_id="deepseek-v4-flash", provider="DeepSeek", name="DeepSeek V4 Flash",
                    adapter_type="openai_compat", input_price=0.14, output_price=0.28,
                    env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
                    cached_input_price=0.0028, extra_params={"thinking": {"type": "disabled"}},
                    max_tokens=2000, tier="M"),
        "L1": dict(model_id="claude-haiku-4-5-20251001", provider="Anthropic", name="Claude Haiku 4.5",
                    adapter_type="anthropic", input_price=1.0, output_price=5.0,
                    env_key="CLAUDE_API_KEY", cached_input_price=0.10, tier="L"),
        "L2": dict(model_id="gpt-4.1-mini", provider="OpenAI", name="GPT-4.1 Mini",
                    adapter_type="openai_compat", input_price=0.40, output_price=1.60,
                    env_key="OPENAI_API_KEY", cached_input_price=0.10, tier="L"),
        "L3": dict(model_id="gemini-3.5-flash-lite", provider="Google", name="Gemini 3.5 Flash-Lite",
                    adapter_type="gemini", input_price=0.30, output_price=2.50,
                    env_key="GEMINI_API_KEY", tier="L"),
        "L4": dict(model_id="grok-4.3", provider="xAI", name="Grok 4.3",
                    adapter_type="openai_compat", input_price=1.25, output_price=2.50,
                    env_key="GROK_API_KEY", cached_input_price=0.20, tier="L"),
        "L5": dict(model_id="kimi-k2.6", provider="Moonshot", name="Kimi K2.6 (L)",
                    adapter_type="openai_compat", input_price=0.95, output_price=4.0,
                    env_key="KIMI_API_KEY", timeout_seconds=90,
                    extra_params={"thinking": {"type": "disabled"}}, tier="L"),
        "L6": dict(model_id="deepseek-v4-flash", provider="DeepSeek", name="DeepSeek V4 Flash (L)",
                    adapter_type="openai_compat", input_price=0.14, output_price=0.28,
                    env_key="DEEPSEEK_API_KEY", cached_input_price=0.0028,
                    extra_params={"thinking": {"type": "disabled"}}, max_tokens=2000, tier="L"),
        "L7": dict(model_id="gemini-2.5-flash-lite", provider="Google", name="Gemini 2.5 Flash-Lite",
                    adapter_type="gemini", input_price=0.10, output_price=0.40, tier="L"),
    }
    for key, exp in expected.items():
        info = MODEL_REGISTRY[key]
        for attr, val in exp.items():
            assert getattr(info, attr) == val, f"{key}.{attr} changed: {getattr(info, attr)!r} != {val!r}"


def test_tier_field_consistent():
    for key in CORE_18_KEYS:
        assert MODEL_REGISTRY[key].tier == key[0], f"{key}.tier should be {key[0]!r}"
    assert MODEL_REGISTRY["L7"].tier == "L"


def test_tier_grouping():
    weak = get_models_by_tier("L")
    mid = get_models_by_tier("M")
    strong = get_models_by_tier("H")
    assert set(weak.keys()) == {"L1", "L2", "L3", "L4", "L5", "L6", "L7"}
    assert set(mid.keys()) == {"M1", "M2", "M3", "M4", "M5", "M6"}
    assert set(strong.keys()) == {"H1", "H2", "H3", "H4", "H5", "H6"}


def test_duplicate_model_ids_agree_on_economics():
    """同一model_idを持つ複数エントリは経済パラメータが一致していなければならない（M5/L5, M6/L6）"""
    by_id: dict[str, list[str]] = {}
    for key, info in MODEL_REGISTRY.items():
        by_id.setdefault(info.model_id, []).append(key)
    econ_fields = ["input_price", "output_price", "cached_input_price", "reasoning_price",
                   "adapter_type", "base_url", "env_key"]
    for model_id, keys in by_id.items():
        if len(keys) < 2:
            continue
        first = MODEL_REGISTRY[keys[0]]
        for other_key in keys[1:]:
            other = MODEL_REGISTRY[other_key]
            for f in econ_fields:
                assert getattr(first, f) == getattr(other, f), (
                    f"model_id={model_id!r} 重複キー {keys[0]}/{other_key} で {f} が不一致"
                )


def test_get_model_reverse_lookup_is_first_match():
    assert get_model("kimi-k2.6") is MODEL_REGISTRY["M5"]
    assert get_model("deepseek-v4-flash") is MODEL_REGISTRY["M6"]


def test_get_model_key_lookup():
    for key in CORE_18_KEYS + ["L7"]:
        assert get_model(key) is MODEL_REGISTRY[key]


def test_get_model_unknown_raises():
    with pytest.raises(ValueError):
        get_model("no-such-model")


def test_h_tier_reuses_existing_env_keys():
    expected_env = {
        "H1": "CLAUDE_API_KEY", "H2": "OPENAI_API_KEY", "H3": "GEMINI_API_KEY",
        "H4": "GROK_API_KEY", "H5": "KIMI_API_KEY", "H6": "DEEPSEEK_API_KEY",
    }
    for key, env in expected_env.items():
        assert MODEL_REGISTRY[key].env_key == env


def test_no_placeholder_model_ids():
    for key, info in MODEL_REGISTRY.items():
        assert "TODO" not in info.model_id
        assert "XXX" not in info.model_id
        assert "(" not in info.model_id  # 仕様書の括弧書き未確定IDが紛れ込んでいないか
        assert info.model_id.strip() != ""


def test_all_prices_positive():
    for key, info in MODEL_REGISTRY.items():
        assert info.input_price > 0, key
        assert info.output_price > 0, key


def test_get_model_keys_by_id_is_honest():
    keys = get_model_keys_by_id("kimi-k2.6")
    assert keys == ["M5", "L5"]
    keys6 = get_model_keys_by_id("deepseek-v4-flash")
    assert keys6 == ["M6", "L6"]
    assert get_model_keys_by_id("no-such-id") == []


def test_all_entries_route_to_an_adapter():
    """全キーでcreate_adapter()が例外なく完了する（env読込は_get_client内で遅延なのでオフライン安全）"""
    for key, info in MODEL_REGISTRY.items():
        adapter = create_adapter(info)
        assert adapter is not None, key


def test_estimate_cost_for_every_entry():
    for key, info in MODEL_REGISTRY.items():
        cost = estimate_cost(info, 1000, 200)
        assert cost >= 0, key


def test_vendor_property():
    assert MODEL_REGISTRY["M1"].vendor == "anthropic"
    assert MODEL_REGISTRY["M2"].vendor == "openai"
    assert MODEL_REGISTRY["M3"].vendor == "google"
    assert MODEL_REGISTRY["M4"].vendor == "xai"
    assert MODEL_REGISTRY["M5"].vendor == "moonshot"
    assert MODEL_REGISTRY["M6"].vendor == "deepseek"
    expected_h_vendor = {
        "H1": "anthropic", "H2": "openai", "H3": "google",
        "H4": "xai", "H5": "moonshot", "H6": "deepseek",
    }
    for key, vendor in expected_h_vendor.items():
        assert MODEL_REGISTRY[key].vendor == vendor


def test_vendor_endpoints():
    endpoints = list_vendor_endpoints()
    providers = [p for p, _, _, _ in endpoints]
    assert providers == list(VENDOR_ORDER)
    for provider, env_key, base_url, adapter_type in endpoints:
        assert env_key
        assert adapter_type in ("anthropic", "openai_compat", "gemini")


def test_tier_order_and_vendor_order_constants():
    assert TIER_ORDER == ("H", "M", "L")
    assert VENDOR_ORDER == ("Anthropic", "OpenAI", "Google", "xAI", "Moonshot", "DeepSeek")


def test_model_info_field_count_unchanged_except_tier():
    """ModelInfoのフィールド数が既知の値であること（想定外フィールド追加の検知）

    2026-08-18: hidden_thinking_reserve_tokens を追加（worst_case_costのhidden thinking
    予約対応。既定0で全モデルの計算を変えない。scripts/model_smoke.py:worst_case_cost参照）。"""
    names = [f.name for f in fields(ModelInfo)]
    assert names == [
        "model_id", "provider", "name", "adapter_type", "input_price", "output_price",
        "env_key", "base_url", "timeout_seconds", "max_tokens",
        "max_tokens_param", "supports_temperature", "extra_params",
        "cached_input_price", "reasoning_price", "tier",
        "hidden_thinking_reserve_tokens",
    ]


def test_only_h2_overrides_openai_param_policy():
    """H2 (gpt-5.6-sol) のみが max_tokens_param / supports_temperature を上書きしていること。
    他の openai_compat / gemini モデルは全て既定値（max_tokens / temperature送信あり）のまま。"""
    from llm.models import MODEL_REGISTRY as REG
    for key, info in REG.items():
        if info.adapter_type not in ("openai_compat", "gemini"):
            continue
        if key == "H2":
            assert info.max_tokens_param == "max_completion_tokens"
            assert info.supports_temperature is False
        else:
            assert info.max_tokens_param == "max_tokens", key
            assert info.supports_temperature is True, key


def test_log_parser_lookup_matches_get_model():
    """viewer/log_parser.py の model_id 逆引きが get_model() と同じ先勝ちルールになっている回帰テスト"""
    from llm.models import MODEL_REGISTRY as REG
    _model_by_id: dict[str, ModelInfo] = {}
    for mi in REG.values():
        _model_by_id.setdefault(mi.model_id, mi)
    assert _model_by_id["kimi-k2.6"] is get_model("kimi-k2.6")
    assert _model_by_id["deepseek-v4-flash"] is get_model("deepseek-v4-flash")


def test_tier_label_handles_h():
    from engine.blog.cards_svg import _tier_label, _price_pitch
    assert "強量級" in _tier_label("H1")
    assert "軽量級" not in _tier_label("H1")
    assert "中量級" in _tier_label("M1")
    assert "軽量級" in _tier_label("L1")
    # 未登録キーは従来ヒューリスティックにフォールバック（クラッシュしない）
    assert _tier_label("Z9") == "軽量級 ・ ライトウェイト"
    assert _price_pitch("H1", 25.0)
