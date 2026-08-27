"""
Cycle 6.1: thinking有効時のtemperature衝突を解消する分岐の回帰テスト。

対象: llm/adapters.py の THINKING_TEMPERATURE_LOCKED_PROVIDERS /
      _thinking_enabled_payload() / AnthropicAdapter.complete() /
      OpenAICompatAdapter.complete()。

API呼び出しは一切行わない（すべて _client にフェイクを直接代入）。

最重要の非回帰点: 本走ロスター L1〜L6 は request_options を渡さないため、
thinking有効時の分岐には入らず、送信内容は Cycle 6 以前と1byteも変わらない。
これを test_core_roster_request_snapshot_is_unchanged_without_thinking で固定する。
"""

from llm.models import ModelInfo, MODEL_REGISTRY
from llm.adapters import (
    AnthropicAdapter,
    OpenAICompatAdapter,
    _thinking_enabled_payload,
)

SYS = "system prompt"
MESSAGES = [{"role": "user", "content": "user message"}]
DEFAULT_TEMPERATURE = 0.7  # llm/constants.py と同値（本走の呼出側固定値）


def _fake_anthropic_client(captured):
    class FakeBlock:
        text = "ok"

    class FakeUsage:
        input_tokens = 10
        output_tokens = 5
        cache_creation_input_tokens = 0
        cache_read_input_tokens = 0
        output_tokens_details = None

    class FakeResponse:
        content = [FakeBlock()]
        usage = FakeUsage()
        stop_reason = "end_turn"
        model = "claude-haiku-4-5-20251001"

    class FakeMessages:
        def create(self, **kwargs):
            captured.append(kwargs)
            return FakeResponse()

    class FakeClient:
        messages = FakeMessages()

    return FakeClient()


def _fake_openai_compat_client(captured):
    class FakeMessage:
        content = "ok"

    class FakeChoice:
        message = FakeMessage()
        finish_reason = "stop"

    class FakeUsage:
        prompt_tokens = 20
        completion_tokens = 10
        total_tokens = 30
        completion_tokens_details = None
        prompt_tokens_details = None

    class FakeResponse:
        choices = [FakeChoice()]
        usage = FakeUsage()
        model = "fake-model"

    class FakeCompletions:
        def create(self, **kwargs):
            captured.append(kwargs)
            return FakeResponse()

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        chat = FakeChat()

    return FakeClient()


class TestThinkingEnabledPayloadHelper:
    """_thinking_enabled_payload() の単体テスト"""

    def test_none_and_empty_are_false(self):
        assert _thinking_enabled_payload(None) is False
        assert _thinking_enabled_payload({}) is False

    def test_disabled_type_is_false(self):
        assert _thinking_enabled_payload({"thinking": {"type": "disabled"}}) is False

    def test_non_dict_thinking_key_is_false(self):
        assert _thinking_enabled_payload({"thinking": "adaptive"}) is False

    def test_enabled_and_adaptive_are_true(self):
        assert _thinking_enabled_payload({"thinking": {"type": "enabled"}}) is True
        assert _thinking_enabled_payload({"thinking": {"type": "adaptive"}}) is True


class TestAnthropicThinkingTemperatureLock:
    def _l1_info(self):
        return ModelInfo(
            model_id="claude-haiku-4-5-20251001", provider="Anthropic", name="Haiku",
            adapter_type="anthropic", input_price=1.0, output_price=5.0,
            env_key="ANTHROPIC_API_KEY", base_url=None,
        )

    def test_anthropic_thinking_forces_temperature_one(self):
        """thinking有効(adaptive)なら呼出側0.7ではなく1.0を送ること"""
        captured = []
        adapter = AnthropicAdapter(self._l1_info(), max_retries=0, allow_temperature_fallback=False)
        adapter._client = _fake_anthropic_client(captured)
        adapter.complete(
            SYS, MESSAGES, max_tokens=3000, temperature=0.7,
            request_options={"thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}},
        )
        assert len(captured) == 1
        assert captured[0]["temperature"] == 1.0

    def test_anthropic_without_thinking_keeps_caller_temperature(self):
        """request_optionsが無ければ従来どおり呼出側temperatureをそのまま送る（本走と同じ）"""
        captured = []
        adapter = AnthropicAdapter(self._l1_info(), max_retries=0, allow_temperature_fallback=False)
        adapter._client = _fake_anthropic_client(captured)
        adapter.complete(SYS, MESSAGES, max_tokens=500, temperature=0.7, request_options=None)
        assert len(captured) == 1
        assert captured[0]["temperature"] == 0.7


class TestMoonshotThinkingTemperatureLock:
    def _m5_info(self):
        return ModelInfo(
            model_id="kimi-k2.6", provider="Moonshot", name="Kimi K2.6",
            adapter_type="openai_compat", input_price=1.0, output_price=4.0,
            env_key="KIMI_API_KEY", base_url="https://api.moonshot.ai/v1",
            temperature_override=0.6,
            extra_params={"thinking": {"type": "disabled"}},
        )

    def test_moonshot_thinking_forces_temperature_one(self):
        """extra_bodyでthinking enabledを送ると、0.6ではなく1.0を送ること"""
        captured = []
        adapter = OpenAICompatAdapter(self._m5_info(), max_retries=0, allow_temperature_fallback=False)
        adapter._client = _fake_openai_compat_client(captured)
        adapter.complete(
            SYS, MESSAGES, max_tokens=3000, temperature=0.7,
            request_options={"extra_body": {"thinking": {"type": "enabled"}}},
        )
        assert len(captured) == 1
        assert captured[0]["temperature"] == 1.0
        assert captured[0]["extra_body"] == {"thinking": {"type": "enabled"}}

    def test_moonshot_without_thinking_keeps_override_and_disabled_payload(self):
        """request_optionsが無ければ従来どおりoverride 0.6 + disabled payloadのまま（本走と同じ）"""
        captured = []
        adapter = OpenAICompatAdapter(self._m5_info(), max_retries=0, allow_temperature_fallback=False)
        adapter._client = _fake_openai_compat_client(captured)
        adapter.complete(SYS, MESSAGES, max_tokens=500, temperature=0.7, request_options=None)
        assert len(captured) == 1
        assert captured[0]["temperature"] == 0.6
        assert captured[0]["extra_body"] == {"thinking": {"type": "disabled"}}


class TestUnlockedProvidersUnaffected:
    """xAI/DeepSeekはthinkingペイロードを送っても許可リスト外なのでtemperatureは変わらない"""

    def test_xai_models_are_not_temperature_locked(self):
        for key in ("L4", "M4", "H4"):
            info = MODEL_REGISTRY[key]
            captured = []
            adapter = OpenAICompatAdapter(info, max_retries=0, allow_temperature_fallback=False)
            adapter._client = _fake_openai_compat_client(captured)
            adapter.complete(
                SYS, MESSAGES, max_tokens=500, temperature=0.7,
                # xAIは実際にはthinkingペイロードを送らないが、
                # 「providerで限定している」ことを確認するため仮に送る。
                request_options={"extra_body": {"thinking": {"type": "enabled"}}},
            )
            assert captured[0]["temperature"] == 0.7, key

    def test_deepseek_h6_is_not_temperature_locked(self):
        """H6は5.5で thinking enabled + temperature 0.7 が実際に成功した実績があるため対象外のまま"""
        info = MODEL_REGISTRY["H6"]
        captured = []
        adapter = OpenAICompatAdapter(info, max_retries=0, allow_temperature_fallback=False)
        adapter._client = _fake_openai_compat_client(captured)
        adapter.complete(
            SYS, MESSAGES, max_tokens=500, temperature=0.7,
            request_options={"extra_body": {"thinking": {"type": "enabled"}}},
        )
        assert captured[0]["temperature"] == 0.7
        assert captured[0]["extra_body"] == {"thinking": {"type": "enabled"}}


class TestModelsWithoutTemperatureSupportStillOmitIt:
    """supports_temperature=Falseのモデルは thinking有効時でも temperature キー自体を送らない"""

    def test_anthropic_h1_m1_omit_temperature_even_with_thinking(self):
        for key in ("H1", "M1"):
            info = MODEL_REGISTRY[key]
            captured = []
            adapter = AnthropicAdapter(info, max_retries=0, allow_temperature_fallback=False)
            adapter._client = _fake_anthropic_client(captured)
            adapter.complete(
                SYS, MESSAGES, max_tokens=3000, temperature=0.7,
                request_options={"thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}},
            )
            assert "temperature" not in captured[0], key

    def test_openai_compat_h2_terra_m3g37_omit_temperature_even_with_thinking(self):
        for key in ("H2", "TERRA", "M3_G37_EVAL"):
            info = MODEL_REGISTRY[key]
            captured = []
            adapter = OpenAICompatAdapter(info, max_retries=0, allow_temperature_fallback=False)
            adapter._client = _fake_openai_compat_client(captured)
            adapter.complete(
                SYS, MESSAGES, max_tokens=3000, temperature=0.7,
                request_options={"reasoning_effort": "medium"},
            )
            assert "temperature" not in captured[0], key


class TestCoreRosterRequestSnapshotUnchanged:
    """
    本走ロスター L1〜L6 は request_options を渡さない(llm/llm_agent.py:227)ため、
    Cycle 6.1 の分岐に入らず、送信されるリクエスト辞書は Cycle 6 以前と完全に一致する。

    このテストは MODEL_REGISTRY の L1〜L6 定義そのものから期待値を組み立てるため、
    registryの値がこのテストと矛盾する形で変更されれば検知できる（0824 run 互換の固定）。
    """

    def _expected_full_messages(self):
        return [{"role": "system", "content": SYS}] + MESSAGES

    def test_l1_anthropic_snapshot(self):
        info = MODEL_REGISTRY["L1"]
        captured = []
        adapter = AnthropicAdapter(info, max_retries=0, allow_temperature_fallback=False)
        adapter._client = _fake_anthropic_client(captured)
        adapter.complete(SYS, MESSAGES, max_tokens=500, temperature=DEFAULT_TEMPERATURE)

        assert captured[0] == {
            "model": "claude-haiku-4-5-20251001",
            "system": [{"type": "text", "text": SYS, "cache_control": {"type": "ephemeral"}}],
            "messages": MESSAGES,
            "max_tokens": 500,
            "temperature": 0.7,
        }

    def test_l2_l3_l4_openai_compat_snapshot_no_extra_params(self):
        expected_ids = {"L2": "gpt-4.1-mini", "L3": "gemini-3.5-flash-lite", "L4": "grok-4.3"}
        full_messages = self._expected_full_messages()
        for key, model_id in expected_ids.items():
            info = MODEL_REGISTRY[key]
            captured = []
            adapter = OpenAICompatAdapter(info, max_retries=0, allow_temperature_fallback=False)
            adapter._client = _fake_openai_compat_client(captured)
            adapter.complete(SYS, MESSAGES, max_tokens=500, temperature=DEFAULT_TEMPERATURE)

            assert captured[0] == {
                "model": model_id,
                "messages": full_messages,
                "max_tokens": 500,
                "temperature": 0.7,
            }, key

    def test_l5_moonshot_snapshot_keeps_override_and_disabled_payload(self):
        info = MODEL_REGISTRY["L5"]
        full_messages = self._expected_full_messages()
        captured = []
        adapter = OpenAICompatAdapter(info, max_retries=0, allow_temperature_fallback=False)
        adapter._client = _fake_openai_compat_client(captured)
        adapter.complete(SYS, MESSAGES, max_tokens=500, temperature=DEFAULT_TEMPERATURE)

        assert captured[0] == {
            "model": "kimi-k2.6",
            "messages": full_messages,
            "max_tokens": 500,
            "extra_body": {"thinking": {"type": "disabled"}},
            "temperature": 0.6,
        }

    def test_l6_deepseek_snapshot_keeps_disabled_payload_and_caller_temperature(self):
        info = MODEL_REGISTRY["L6"]
        full_messages = self._expected_full_messages()
        captured = []
        adapter = OpenAICompatAdapter(info, max_retries=0, allow_temperature_fallback=False)
        adapter._client = _fake_openai_compat_client(captured)
        adapter.complete(SYS, MESSAGES, max_tokens=500, temperature=DEFAULT_TEMPERATURE)

        assert captured[0] == {
            "model": "deepseek-v4-flash",
            "messages": full_messages,
            "max_tokens": 500,
            "extra_body": {"thinking": {"type": "disabled"}},
            "temperature": 0.7,
        }
