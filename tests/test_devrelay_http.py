"""
サイクル10.7: HttpAgentProvider(DevRelay経由のサブスク実験席)のテスト

単体テストは httpx.MockTransport でHTTPをモックする（実ネットワークへ接続しない）。
結合テストは tools/fake_devrelay.py の固定サーバーへ実際にHTTPで疎通する。
"""

import json
import logging

import httpx
import pytest

from llm.adapters import AdapterError, _classify_error, create_adapter
from llm.costing import usage_cost, worst_case_cost
from llm.game_cost_budget import GameCostBudget
from llm.llm_agent import LLMAgent
from llm.llm_logger import LLMLogger
from llm.models import MODEL_REGISTRY, ModelInfo, get_model
from llm.providers.devrelay_http import (
    ANONYMIZATION_LINE,
    DEFAULT_TARGET_PROJECT_ID,
    HttpAgentProvider,
)

import tools.fake_devrelay as fake_devrelay


def make_model(**overrides) -> ModelInfo:
    """テスト専用のdevrelay_http ModelInfoを作る（レジストリのDR_FABLEと同形）。"""
    base = dict(
        model_id="devrelay/claude-fable-5-1",
        provider="Anthropic", name="Test DevRelay Seat",
        adapter_type="devrelay_http",
        input_price=0.0, output_price=0.0,
        env_key="DEVRELAY_TOKEN", base_url=None,
        timeout_seconds=120,
        supports_temperature=False,
        billing="subscription",
        tier="",
    )
    base.update(overrides)
    return ModelInfo(**base)


def success_payload(**overrides) -> dict:
    payload = {
        "text": '{"ok": true}',
        "model": "claude-fable-5-1",
        "usage": {"input": 42, "output": 8, "cacheRead": 1, "cacheWrite": 2},
        "latencyMs": 3500, "agentDurationMs": 3200,
        "stopReason": "success", "sessionId": "raw_test_session",
        "deniedTools": [],
    }
    payload.update(overrides)
    return payload


@pytest.fixture(autouse=True)
def _env(monkeypatch):
    """DevRelay接続先の環境変数を各テストへ共通設定する（明示上書きも可）。"""
    monkeypatch.setenv("DEVRELAY_URL", "https://devrelay.example")
    monkeypatch.setenv("DEVRELAY_TOKEN", "tok-secret-123")
    monkeypatch.delenv("DEVRELAY_TARGET_PROJECT_ID", raising=False)
    monkeypatch.delenv("DEVRELAY_SEAT_KEY", raising=False)


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """リトライの指数バックオフを待たない（テスト高速化）。"""
    monkeypatch.setattr("llm.providers.devrelay_http.time.sleep", lambda s: None)


# --- 1. リクエスト組み立て ---

def test_request_assembly():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["method"] = request.method
        captured["url"] = str(request.url)
        captured["headers"] = {k.lower(): v for k, v in request.headers.items()}
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=success_payload())

    provider = HttpAgentProvider(make_model(), transport=httpx.MockTransport(handler))
    provider.bind_seat("P05")
    provider.complete(system="SYSTEM_TEXT", messages=[{"role": "user", "content": "USER_TEXT"}])

    assert captured["method"] == "POST"
    assert captured["url"] == "https://devrelay.example/api/agent/raw-completion"
    assert captured["headers"]["authorization"] == "Bearer tok-secret-123"
    assert captured["headers"]["content-type"] == "application/json"

    body = captured["body"]
    assert body["targetProjectId"] == DEFAULT_TARGET_PROJECT_ID
    assert body["model"] == "claude-fable-5-1"  # "devrelay/" 接頭辞が剥がれている
    assert body["seatKey"] == "P05"
    assert body["prompt"] == "USER_TEXT"
    assert body["timeoutS"] == 120
    assert body["system"] == f"SYSTEM_TEXT\n\n{ANONYMIZATION_LINE}"


def test_target_project_id_env_override(monkeypatch):
    monkeypatch.setenv("DEVRELAY_TARGET_PROJECT_ID", "custom-project-id")
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=success_payload())

    provider = HttpAgentProvider(make_model(), transport=httpx.MockTransport(handler))
    provider.complete(system="s", messages=[{"role": "user", "content": "u"}])
    assert captured["body"]["targetProjectId"] == "custom-project-id"


# --- 2. model 必須 ---

def test_model_prefix_required():
    with pytest.raises(ValueError):
        HttpAgentProvider(make_model(model_id="claude-fable-5-1"))  # 接頭辞なし


def test_model_name_required_after_prefix():
    with pytest.raises(ValueError):
        HttpAgentProvider(make_model(model_id="devrelay/"))  # 未指定


def test_timeout_seconds_limit():
    with pytest.raises(ValueError):
        HttpAgentProvider(make_model(timeout_seconds=200))  # DevRelay契約の180秒超過


# --- 3. トークン未設定 ---

def test_missing_token_raises_adapter_error(monkeypatch):
    monkeypatch.delenv("DEVRELAY_TOKEN", raising=False)
    provider = HttpAgentProvider(
        make_model(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=success_payload()))
    )
    with pytest.raises(AdapterError) as exc:
        provider.complete(system="s", messages=[{"role": "user", "content": "u"}])
    assert "DEVRELAY_TOKEN" in str(exc.value)


def test_missing_url_raises_adapter_error(monkeypatch):
    monkeypatch.delenv("DEVRELAY_URL", raising=False)
    provider = HttpAgentProvider(
        make_model(), transport=httpx.MockTransport(lambda r: httpx.Response(200, json=success_payload()))
    )
    with pytest.raises(AdapterError) as exc:
        provider.complete(system="s", messages=[{"role": "user", "content": "u"}])
    assert "DEVRELAY_URL" in str(exc.value)


# --- 4. text抽出・usageマッピング・billing ---

def test_text_and_usage_mapping():
    provider = HttpAgentProvider(
        make_model(),
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=success_payload())),
    )
    text, usage = provider.complete(system="s", messages=[{"role": "user", "content": "u"}])

    assert text == '{"ok": true}'
    assert usage["input_tokens"] == 42
    assert usage["output_tokens"] == 8
    assert usage["total_tokens"] == 50
    assert usage["cache_read_input_tokens"] == 1
    assert usage["cache_creation_input_tokens"] == 2
    assert usage["finish_reason"] == "success"
    assert usage["response_model"] == "claude-fable-5-1"
    assert usage["usage_raw"]["billing"] == "subscription"
    dr = usage["usage_raw"]["devrelay"]
    assert dr["sessionId"] == "raw_test_session"
    assert dr["latencyMs"] == 3500
    assert dr["seatKey"]  # bind_seat していなくても既定値が入る
    assert dr["anonymization_appended"] is True


# --- 5/6. 429リトライ ---

def test_429_target_busy_then_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={"error": "seat busy", "code": "targetBusy"})
        return httpx.Response(200, json=success_payload())

    provider = HttpAgentProvider(make_model(), transport=httpx.MockTransport(handler))
    text, usage = provider.complete(system="s", messages=[{"role": "user", "content": "u"}])
    assert calls["n"] == 2
    assert text == '{"ok": true}'


def test_429_exhausted_raises_and_classifies_rate_limit():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited", "code": "rateLimited"})

    provider = HttpAgentProvider(
        make_model(), max_retries=1, transport=httpx.MockTransport(handler)
    )
    with pytest.raises(AdapterError) as exc:
        provider.complete(system="s", messages=[{"role": "user", "content": "u"}])
    assert "429" in str(exc.value)
    assert _classify_error(exc.value) == "rate_limit"


# --- 7. stopReason=error ---

def test_stop_reason_error_raises_without_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=success_payload(
            text="", stopReason="error", error="simulated agent error", usage={
                "input": 5, "output": 0, "cacheRead": 0, "cacheWrite": 0,
            },
        ))

    provider = HttpAgentProvider(make_model(), transport=httpx.MockTransport(handler))
    with pytest.raises(AdapterError) as exc:
        provider.complete(system="s", messages=[{"role": "user", "content": "u"}])
    assert calls["n"] == 1  # リトライしない
    assert "simulated agent error" in str(exc.value)


# --- 8. 403 notAllowed ---

def test_403_not_allowed_raises_without_retry():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(403, json={"error": "denied", "code": "notAllowed"})

    provider = HttpAgentProvider(make_model(), transport=httpx.MockTransport(handler))
    with pytest.raises(AdapterError) as exc:
        provider.complete(system="s", messages=[{"role": "user", "content": "u"}])
    assert calls["n"] == 1
    assert "403" in str(exc.value)


# --- 9. タイムアウト ---

def test_read_timeout_raises_without_retry_and_sets_http_timeout():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("timed out", request=request)

    provider = HttpAgentProvider(make_model(), transport=httpx.MockTransport(handler))
    with pytest.raises(AdapterError) as exc:
        provider.complete(system="s", messages=[{"role": "user", "content": "u"}])
    assert calls["n"] == 1  # リトライしない
    assert "timeout" in str(exc.value).lower()

    client = provider._get_client(provider.model_info.timeout_seconds + 30)
    assert client.timeout.read == 150.0  # timeout_seconds(120) + HTTP_TIMEOUT_MARGIN_S(30)


# --- 10. 0円計上 ---

def test_zero_cost_billing():
    model = make_model()
    usage = {"input_tokens": 100_000, "output_tokens": 100_000, "total_tokens": 200_000}
    assert usage_cost(model, usage) == 0.0
    assert worst_case_cost(model, "system" * 1000, "user" * 1000, 4000) == 0.0


def test_zero_cost_never_blocks_tiny_budget():
    budget = GameCostBudget(per_player_cap_usd=0.0, game_cap_usd=0.0, event_logger=None)
    reservation = budget.reserve("P01", 0.0, round_num=1, phase="test")
    budget.settle(reservation, 0.0)
    assert budget.player_spent_usd["P01"] == 0.0
    assert budget.game_spent_usd == 0.0


# --- 11. deniedTools 警告 ---

def test_denied_tools_logs_warning(caplog):
    provider = HttpAgentProvider(
        make_model(),
        transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json=success_payload(deniedTools=["Bash"]))
        ),
    )
    with caplog.at_level(logging.WARNING):
        provider.complete(system="s", messages=[{"role": "user", "content": "u"}])
    assert any("deniedTools" in r.message and "Bash" in r.message for r in caplog.records)


# --- 12. レジストリ登録 ---

def test_registry_entries_are_subscription_and_unregistered_from_default_roster():
    for key in ("DR_FABLE", "DR_OPUS"):
        info = MODEL_REGISTRY[key]
        assert info.adapter_type == "devrelay_http"
        assert info.billing == "subscription"
        assert info.input_price == 0.0
        assert info.output_price == 0.0
        assert info.tier == ""  # get_models_by_tier() では拾われない＝既定ロスター外

    assert get_model("devrelay/claude-fable-5-1") is MODEL_REGISTRY["DR_FABLE"]
    assert get_model("devrelay/claude-opus-5") is MODEL_REGISTRY["DR_OPUS"]

    adapter = create_adapter(MODEL_REGISTRY["DR_FABLE"])
    assert isinstance(adapter, HttpAgentProvider)


# --- 13. 匿名化行 ---

def test_anonymization_line_appended_only_in_transport_payload():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["system"] = json.loads(request.content)["system"]
        return httpx.Response(200, json=success_payload())

    provider = HttpAgentProvider(make_model(), transport=httpx.MockTransport(handler))
    original_system = "あなたはプレイヤーP05である。"
    provider.complete(system=original_system, messages=[{"role": "user", "content": "u"}])

    assert captured["system"] == f"{original_system}\n\n{ANONYMIZATION_LINE}"
    # build_system_prompt()相当の元文字列自体は変更されない（呼び出し側の変数は不変）
    assert original_system == "あなたはプレイヤーP05である。"


# --- 14. LLMAgent 統合: bind_seat と 0円ログ ---

def test_llm_agent_binds_seat_and_logs_zero_cost(tmp_path):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=success_payload())

    provider = HttpAgentProvider(make_model(), transport=httpx.MockTransport(handler))
    model_info = make_model()
    llm_logger = LLMLogger(tmp_path, game_id="test_devrelay")
    agent = LLMAgent("P07", model_info, provider, llm_logger)

    assert provider._seat_key == "P07"  # LLMAgent.__init__のbind_seatフック

    text, usage = agent._call_llm("test_phase", 1, "user prompt text")
    assert text == '{"ok": true}'
    assert llm_logger._entries[-1]["cost_usd"] == 0.0
    assert llm_logger._entries[-1]["usage_raw"]["billing"] == "subscription"


# --- 15. 結合テスト: フェイクサーバー ---

@pytest.fixture()
def fake_server():
    server, base_url = fake_devrelay.serve_in_thread(token="fake-token")
    yield base_url
    server.shutdown()


def test_fake_server_round_trip(fake_server, monkeypatch):
    monkeypatch.setenv("DEVRELAY_URL", fake_server)
    monkeypatch.setenv("DEVRELAY_TOKEN", "fake-token")
    provider = HttpAgentProvider(make_model())
    provider.bind_seat("P09")
    text, usage = provider.complete(system="s", messages=[{"role": "user", "content": "u"}])
    assert text == '{"ok": true}'
    assert usage["usage_raw"]["devrelay"]["sessionId"] == "raw_fake_session"


def test_fake_server_busy_then_success(fake_server, monkeypatch):
    monkeypatch.setenv("DEVRELAY_URL", fake_server)
    monkeypatch.setenv("DEVRELAY_TOKEN", "fake-token")
    provider = HttpAgentProvider(make_model())
    provider.bind_seat("P10")
    text, usage = provider.complete(
        system="s", messages=[{"role": "user", "content": "__BUSY__ 質問文"}]
    )
    assert text == '{"ok": true}'


def test_fake_server_agent_error(fake_server, monkeypatch):
    monkeypatch.setenv("DEVRELAY_URL", fake_server)
    monkeypatch.setenv("DEVRELAY_TOKEN", "fake-token")
    provider = HttpAgentProvider(make_model())
    provider.bind_seat("P11")
    with pytest.raises(AdapterError):
        provider.complete(system="s", messages=[{"role": "user", "content": "__AGENT_ERROR__"}])
