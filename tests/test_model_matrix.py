"""
scripts/model_matrix.py のユニットテスト

API を一切呼ばない。create_adapter / Game をフェイクに差し替えて検証する
（tests/test_model_smoke.py の作法を踏襲）。
"""

import json

import pytest

import scripts.model_matrix as mm
from llm.adapters import AdapterError
from llm.models import MODEL_REGISTRY, ModelInfo, estimate_cost, get_model


# ============================================================
# フェイクアダプタ
# ============================================================

class _FakeOkAdapter:
    """成功応答のみを返すフェイクアダプタ（Phase1/2共用の妥当なJSONを返す）"""

    def __init__(self, model_info):
        self.model_info = model_info
        self.calls = 0

    def complete(self, system, messages, max_tokens=1000, temperature=0.7):
        self.calls += 1
        text = '{"strategy": {"reason": "test", "emotion": "楽"}, "action": {"type": "pass"}}'
        usage = {"input_tokens": 100, "output_tokens": 20}
        return text, usage


class _FakeErrorAdapter:
    """常にAdapterErrorを投げるフェイクアダプタ"""

    def __init__(self, model_info):
        self.model_info = model_info

    def complete(self, system, messages, max_tokens=1000, temperature=0.7):
        raise AdapterError("OpenAI-compat API error (NotFoundError): fake 404 for test")


class _FakeMarkdownAdapter:
    """Markdownコードフェンス混入JSONを返すフェイクアダプタ"""

    def __init__(self, model_info):
        self.model_info = model_info

    def complete(self, system, messages, max_tokens=1000, temperature=0.7):
        text = '```json\n{"strategy": {"reason": "test", "emotion": "楽"}, "action": {"type": "pass"}}\n```'
        usage = {"input_tokens": 100, "output_tokens": 20}
        return text, usage


class _RefuseToCallAdapter:
    """呼ばれたらテスト失敗にする（Phase0/dry-run/予算超過で呼ばれないことの検証用）"""

    def __init__(self, model_info):
        self.model_info = model_info

    def complete(self, *a, **kw):
        raise AssertionError("API は呼ばれないはずでした（Phase0/dry-run/予算超過ガード）")


class _FakeOkAdapterWithResponseModel:
    """成功応答＋response_model付きusageを返すフェイクアダプタ（requested/returned検証用）"""

    def __init__(self, model_info, response_model=None):
        self.model_info = model_info
        self._response_model = response_model if response_model is not None else model_info.model_id

    def complete(self, system, messages, max_tokens=1000, temperature=0.7):
        text = '{"ok": true}'
        usage = {
            "input_tokens": 10, "output_tokens": 5,
            "requested_model": self.model_info.model_id,
            "response_model": self._response_model,
        }
        return text, usage


class _FakeUsageRichAdapter:
    """cache_read / total_tokens を含む usage を返すフェイク（コスト計算検証用）"""

    def __init__(self, model_info, usage):
        self.model_info = model_info
        self._usage = usage

    def complete(self, system, messages, max_tokens=1000, temperature=0.7):
        return '{"ok": true}', dict(self._usage)


class _FakeGameJsonUsageAdapter:
    """Phase2互換の妥当な交渉JSONを返しつつ、任意のusageを注入できるフェイク
    （Phase2コスト計算バグ修正の検証用: _FakeOkAdapterのJSON + _FakeUsageRichAdapterのusage）"""

    def __init__(self, model_info, usage):
        self.model_info = model_info
        self._usage = usage
        self.calls = 0

    def complete(self, system, messages, max_tokens=1000, temperature=0.7):
        self.calls += 1
        text = '{"strategy": {"reason": "test", "emotion": "楽"}, "action": {"type": "pass"}}'
        return text, dict(self._usage)


def _fake_create_adapter_factory(cls):
    def _factory(model_info, max_retries=None):
        return cls(model_info)
    return _factory


# ============================================================
# 対象選定・順序
# ============================================================

def test_core_18_keys_defined():
    assert len(mm.CORE_18_KEYS) == 18
    for key in mm.CORE_18_KEYS:
        assert key in MODEL_REGISTRY


def test_ordered_keys_execution_order_weak_mid_strong_cheapest_first():
    keys = mm.ordered_keys(None, None)
    tiers = [MODEL_REGISTRY[k].tier for k in keys]
    # 弱(L)が全て中(M)より前、中が全て強(H)より前
    assert tiers == sorted(tiers, key=lambda t: {"L": 0, "M": 1, "H": 2}[t])
    # 各ティア内は単価(input+output)昇順
    for tier in ("L", "M", "H"):
        tier_keys = [k for k in keys if MODEL_REGISTRY[k].tier == tier]
        prices = [MODEL_REGISTRY[k].input_price + MODEL_REGISTRY[k].output_price for k in tier_keys]
        assert prices == sorted(prices)


def test_ordered_keys_explicit_overrides_tier():
    keys = mm.ordered_keys(["H"], ["L1", "H1"])
    assert keys == ["L1", "H1"]


def test_ordered_keys_unknown_explicit_key_raises():
    with pytest.raises(ValueError):
        mm.ordered_keys(None, ["NOPE"])


def test_parse_tiers_aliases():
    assert mm._parse_tiers("weak,mid") == ["L", "M"]
    assert mm._parse_tiers("strong") == ["H"]
    assert mm._parse_tiers("all") == list(mm.EXEC_TIER_ORDER)
    assert mm._parse_tiers("") == list(mm.EXEC_TIER_ORDER)
    with pytest.raises(ValueError):
        mm._parse_tiers("nonsense")


# ============================================================
# Phase 0
# ============================================================

def test_phase0_makes_no_api_calls(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_RefuseToCallAdapter))
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, mm.CORE_18_KEYS)
    mm.run_phase0(run_dir, state)
    assert (run_dir / "phase0_inventory.md").exists()
    assert state["phases"]["0"]["status"] == "done"


def test_phase0_table_lists_every_registry_key_with_tier(tmp_path):
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, mm.CORE_18_KEYS)
    mm.run_phase0(run_dir, state)
    text = (run_dir / "phase0_inventory.md").read_text(encoding="utf-8")
    for key in MODEL_REGISTRY:
        assert key in text


# ============================================================
# Phase 1
# ============================================================

def test_dry_run_never_calls_api(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_RefuseToCallAdapter))
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1", "L2"])
    result = mm.run_phase1(run_dir, state, ["L1", "L2"], max_cost=1.0, max_cost_total=1.0,
                            max_cost_per_model=1.0, max_calls=10, max_tokens=64, retries=0,
                            resume=False, force=False, dry_run=True)
    assert result == "dry_run"


def test_phase1_pass_and_budget_tracking(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_FakeOkAdapter))
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    result = mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0,
                            max_cost_per_model=1.0, max_calls=10, max_tokens=64, retries=0,
                            resume=False, force=False, dry_run=False)
    assert result == "done"
    assert state["models"]["L1"]["phase1"]["status"] == "pass"
    assert state["budget"]["spent_usd"] > 0
    assert state["budget"]["calls_made"] == 1
    assert (run_dir / "phase1_calls.jsonl").exists()
    assert (run_dir / "phase1_report.md").exists()


def test_phase1_error_adapter_marks_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_FakeErrorAdapter))
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=64, retries=1,
                   resume=False, force=False, dry_run=False)
    assert state["models"]["L1"]["phase1"]["status"] == "fail"


def test_phase1_missing_env_key_is_skip_not_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_RefuseToCallAdapter))
    monkeypatch.delenv(MODEL_REGISTRY["L1"].env_key, raising=False)
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=64, retries=0,
                   resume=False, force=False, dry_run=False)
    assert state["models"]["L1"]["phase1"]["status"] == "skip"


# ============================================================
# Phase 1: リトライ責務の一本化（アダプタ内部retryは常に0）
# ============================================================

def test_phase1_always_creates_adapter_with_zero_lower_retries(monkeypatch):
    """_call_once_phase1 は retries の値によらず、常に max_retries=0 でアダプタを生成すること
    （アダプタ内部リトライ・OpenAI SDK内部リトライを常に無効化する）"""
    captured = []

    def _factory(model_info, max_retries=None):
        captured.append(max_retries)
        return _FakeOkAdapter(model_info)

    monkeypatch.setattr(mm, "create_adapter", _factory)
    info = MODEL_REGISTRY["L1"]
    mm._call_once_phase1(info, max_tokens=64, retries=0)
    mm._call_once_phase1(info, max_tokens=64, retries=2)

    assert captured == [0, 0]


def test_phase1_retries_are_outer_loop_only(monkeypatch):
    """--retries N はスクリプト外側ループの試行回数だけを増やし、アダプタは
    毎回 max_retries=0 で単一生成されること（三重リトライにならない証明）"""
    create_calls = []
    complete_calls = {"n": 0}

    class _AlwaysFailAdapter:
        def __init__(self, model_info):
            self.model_info = model_info

        def complete(self, *a, **kw):
            complete_calls["n"] += 1
            raise AdapterError("fake 429 for test")

    def _factory(model_info, max_retries=None):
        create_calls.append(max_retries)
        return _AlwaysFailAdapter(model_info)

    monkeypatch.setattr(mm, "create_adapter", _factory)
    info = MODEL_REGISTRY["L1"]
    result = mm._call_once_phase1(info, max_tokens=64, retries=2)

    assert result["ok"] is False
    assert complete_calls["n"] == 3          # 外側ループが retries+1 = 3回試行
    assert create_calls == [0]               # create_adapterは1回だけ、常にmax_retries=0


def test_phase1_zero_retries_calls_complete_once(monkeypatch):
    """retries=0 なら complete() は1回だけ呼ばれること（今回のH2実行条件そのもの）"""
    complete_calls = {"n": 0}

    class _AlwaysFailAdapter:
        def __init__(self, model_info):
            self.model_info = model_info

        def complete(self, *a, **kw):
            complete_calls["n"] += 1
            raise AdapterError("fake error for test")

    def _factory(model_info, max_retries=None):
        return _AlwaysFailAdapter(model_info)

    monkeypatch.setattr(mm, "create_adapter", _factory)
    info = MODEL_REGISTRY["L1"]
    result = mm._call_once_phase1(info, max_tokens=64, retries=0)

    assert result["ok"] is False
    assert complete_calls["n"] == 1


def test_cost_guard_aborts_and_marks_remaining_skipped_budget(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_FakeOkAdapter))
    for key in ("L1", "L2"):
        monkeypatch.setenv(MODEL_REGISTRY[key].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 0.0, ["L1", "L2"])
    result = mm.run_phase1(run_dir, state, ["L1", "L2"], max_cost=0.0, max_cost_total=0.0,
                            max_cost_per_model=1.0, max_calls=10, max_tokens=64, retries=0,
                            resume=False, force=False, dry_run=False)
    assert result == "aborted_budget"
    assert state["models"]["L1"]["phase1"]["status"] == "skipped_budget"
    assert state["models"]["L2"]["phase1"]["status"] == "skipped_budget"


def test_resume_skips_already_passed_models(tmp_path, monkeypatch):
    calls = {"n": 0}

    class _CountingOkAdapter(_FakeOkAdapter):
        def complete(self, *a, **kw):
            calls["n"] += 1
            return super().complete(*a, **kw)

    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_CountingOkAdapter))
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0, max_cost_per_model=1.0,
                   max_calls=10, max_tokens=64, retries=0, resume=False, force=False, dry_run=False)
    assert calls["n"] == 1
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0, max_cost_per_model=1.0,
                   max_calls=10, max_tokens=64, retries=0, resume=True, force=False, dry_run=False)
    assert calls["n"] == 1  # resumeで再実行されない


def test_force_reruns_passed_models(tmp_path, monkeypatch):
    calls = {"n": 0}

    class _CountingOkAdapter(_FakeOkAdapter):
        def complete(self, *a, **kw):
            calls["n"] += 1
            return super().complete(*a, **kw)

    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_CountingOkAdapter))
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0, max_cost_per_model=1.0,
                   max_calls=10, max_tokens=64, retries=0, resume=False, force=False, dry_run=False)
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0, max_cost_per_model=1.0,
                   max_calls=10, max_tokens=64, retries=0, resume=True, force=True, dry_run=False)
    assert calls["n"] == 2  # forceで再実行される


def test_state_survives_process_restart(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_FakeOkAdapter))
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    mm._save_state(run_dir, state)
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0, max_cost_per_model=1.0,
                   max_calls=10, max_tokens=64, retries=0, resume=False, force=False, dry_run=False)

    reloaded = mm._load_state(run_dir)
    assert reloaded["models"]["L1"]["phase1"]["status"] == "pass"
    assert reloaded["phases"]["1"]["status"] == "done"


# ============================================================
# requested/returned モデル名の一致判定
# ============================================================

def test_model_match_exact_match():
    assert mm._model_match("gpt-4.1-mini", "gpt-4.1-mini") == "match"


def test_model_match_case_and_whitespace_insensitive():
    assert mm._model_match("gpt-4.1-mini", " GPT-4.1-Mini ") == "match"


def test_model_match_alias_date_snapshot():
    # requested がエイリアス、returned が日付スナップショット解決版（prefix一致）
    assert mm._model_match("gpt-4.1-mini", "gpt-4.1-mini-2025-04-14") == "alias"


def test_model_match_mismatch_unrelated_model():
    assert mm._model_match("deepseek-chat", "deepseek-reasoner") == "mismatch"


def test_model_match_unknown_when_returned_missing():
    assert mm._model_match("gpt-4.1-mini", None) == "unknown"
    assert mm._model_match("gpt-4.1-mini", "") == "unknown"


def test_model_match_normalizes_models_prefix():
    # Gemini の OpenAI互換経路が "models/gemini-3.5-flash-lite" を返す場合を想定
    assert mm._model_match("gemini-3.5-flash-lite", "models/gemini-3.5-flash-lite") == "match"


# ============================================================
# Phase 1: requested/returned モデル名の記録
# ============================================================

def test_phase1_records_requested_and_matching_response_model(tmp_path, monkeypatch):
    def _factory(model_info, max_retries=None):
        return _FakeOkAdapterWithResponseModel(model_info, response_model=model_info.model_id)
    monkeypatch.setattr(mm, "create_adapter", _factory)
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=64, retries=0,
                   resume=False, force=False, dry_run=False)
    p1 = state["models"]["L1"]["phase1"]
    assert p1["response_model"] == MODEL_REGISTRY["L1"].model_id
    assert p1["model_match"] == "match"
    # JSONL側にも requested_model / model_match が記録されること
    line = json.loads((run_dir / "phase1_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()[0])
    assert line["requested_model"] == MODEL_REGISTRY["L1"].model_id
    assert line["model_match"] == "match"


def test_phase1_records_mismatch_response_model(tmp_path, monkeypatch):
    def _factory(model_info, max_retries=None):
        return _FakeOkAdapterWithResponseModel(model_info, response_model="some-other-model")
    monkeypatch.setattr(mm, "create_adapter", _factory)
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=64, retries=0,
                   resume=False, force=False, dry_run=False)
    p1 = state["models"]["L1"]["phase1"]
    assert p1["response_model"] == "some-other-model"
    assert p1["model_match"] == "mismatch"


def test_phase1_fail_records_response_model_none(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_FakeErrorAdapter))
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=64, retries=0,
                   resume=False, force=False, dry_run=False)
    p1 = state["models"]["L1"]["phase1"]
    assert p1["response_model"] is None
    assert p1["model_match"] == "unknown"


def test_generate_phase1_report_shows_requested_returned_and_match(tmp_path, monkeypatch):
    def _factory(model_info, max_retries=None):
        return _FakeOkAdapterWithResponseModel(model_info, response_model=model_info.model_id)
    monkeypatch.setattr(mm, "create_adapter", _factory)
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=64, retries=0,
                   resume=False, force=False, dry_run=False)
    text = (run_dir / "phase1_report.md").read_text(encoding="utf-8")
    assert MODEL_REGISTRY["L1"].model_id in text
    assert "✓" in text  # 一致マーク


def test_generate_final_report_shows_returned_column_and_mismatch_mark(tmp_path, monkeypatch):
    def _factory(model_info, max_retries=None):
        return _FakeOkAdapterWithResponseModel(model_info, response_model="unexpected-model-xyz")
    monkeypatch.setattr(mm, "create_adapter", _factory)
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=64, retries=0,
                   resume=False, force=False, dry_run=False)
    out_path = run_dir / "matrix_report.md"
    mm.generate_final_report(run_dir, state, [out_path])
    text = out_path.read_text(encoding="utf-8")
    assert "unexpected-model-xyz" in text
    assert "⚠" in text
    assert "Returned" in text


# ============================================================
# Phase 2
# ============================================================

def test_phase2_without_phase1_state_returns_gate_error(tmp_path):
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    result = mm.run_phase2(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0,
                            max_cost_per_model=1.0, max_calls=10, max_tokens=400,
                            resume=False, force=False, dry_run=False)
    assert result == "gate_error"


def test_phase2_only_runs_phase1_passers(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_FakeOkAdapter))
    for key in ("L1", "L2"):
        monkeypatch.setenv(MODEL_REGISTRY[key].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1", "L2"])
    state["phases"]["1"] = {"status": "done"}
    state["models"]["L1"]["phase1"] = {"status": "pass"}
    state["models"]["L2"]["phase1"] = {"status": "fail"}
    mm.run_phase2(run_dir, state, ["L1", "L2"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=400,
                   resume=False, force=False, dry_run=False)
    assert state["models"]["L1"]["phase2"]["status"] == "pass"
    # phase1 fail のL2はphase2が実行されずpendingのまま
    assert state["models"]["L2"]["phase2"]["status"] == "pending"


def test_phase2_handles_markdown_fenced_json(tmp_path, monkeypatch):
    """Markdownフェンス付きJSONでもextract_json/parse_responseがフェンスを剥がして解釈できること。
    pure_json はフェンス除去後の中身が'{'始まりかどうかを判定するため、フェンス付きでもTrueになる
    （散文混入の有無を測る指標であり、フェンス有無そのものではない）"""
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_FakeMarkdownAdapter))
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    state["phases"]["1"] = {"status": "done"}
    state["models"]["L1"]["phase1"] = {"status": "pass"}
    mm.run_phase2(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=400,
                   resume=False, force=False, dry_run=False)
    assert state["models"]["L1"]["phase2"]["status"] == "pass"
    assert state["models"]["L1"]["phase2"]["pure_json"] is True


def test_phase2_records_emotion_and_parse_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_FakeOkAdapter))
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    state["phases"]["1"] = {"status": "done"}
    state["models"]["L1"]["phase1"] = {"status": "pass"}
    mm.run_phase2(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=400,
                   resume=False, force=False, dry_run=False)
    assert state["models"]["L1"]["phase2"]["emotion"] == "楽"
    assert state["models"]["L1"]["phase2"]["pure_json"] is True


# ============================================================
# Phase 2 コスト計算修正: _usage_cost() へ統一（旧estimate_cost直呼び出しのバグ修正）
# ============================================================

def _run_phase2_single(tmp_path, key, usage, monkeypatch):
    """1モデルだけPhase2を実行し、(state, run_dir, adapter)を返す共通ヘルパー"""
    model = MODEL_REGISTRY[key]

    def _factory(model_info, max_retries=None):
        return _FakeGameJsonUsageAdapter(model_info, usage)
    monkeypatch.setattr(mm, "create_adapter", _factory)
    monkeypatch.setenv(model.env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, [key])
    state["phases"]["1"] = {"status": "done"}
    state["models"][key]["phase1"] = {"status": "pass"}
    mm.run_phase2(run_dir, state, [key], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=400,
                   resume=False, force=False, dry_run=False)
    return state, run_dir


def test_phase2_cost_includes_gemini_hidden_thinking(tmp_path, monkeypatch):
    """M3 gemini-3.5-flash: total_tokensに含まれるhidden thinkingが記録コストに反映されること
    （旧estimate_cost直呼びだとinput/outputしか渡さずthinking分が消えていた）"""
    m3 = MODEL_REGISTRY["M3"]
    usage = {"input_tokens": 32, "output_tokens": 5, "total_tokens": 222,
              "cache_read_input_tokens": 0}
    state, run_dir = _run_phase2_single(tmp_path, "M3", usage, monkeypatch)
    expected = round(mm._usage_cost(m3, usage), 6)
    naive = round(estimate_cost(m3, usage["input_tokens"], usage["output_tokens"]), 6)
    assert state["models"]["M3"]["phase2"]["cost_usd"] == pytest.approx(expected, abs=1e-9)
    assert expected > naive  # hidden thinking分が正しく加算されている


def test_phase2_cost_applies_cache_discount_for_xai(tmp_path, monkeypatch):
    """M4 grok-4.5: cache_read_input_tokens分が割引単価で計算されること"""
    m4 = MODEL_REGISTRY["M4"]
    usage = {"input_tokens": 526, "output_tokens": 5, "total_tokens": 573,
              "cache_read_input_tokens": 384}
    state, run_dir = _run_phase2_single(tmp_path, "M4", usage, monkeypatch)
    expected = round(mm._usage_cost(m4, usage), 6)
    naive = round(estimate_cost(m4, usage["input_tokens"], usage["output_tokens"]), 6)
    assert state["models"]["M4"]["phase2"]["cost_usd"] == pytest.approx(expected, abs=1e-9)
    assert expected < naive  # cache割引分が正しく減算されている


def test_phase2_cost_handles_cache_and_reasoning_together(tmp_path, monkeypatch):
    """H4 grok-4.6: cache割引とreasoning(thinking)差分課金が同時に発生しても二重加算されないこと"""
    h4 = MODEL_REGISTRY["H4"]
    usage = {"input_tokens": 300, "output_tokens": 40, "total_tokens": 464,
              "cache_read_input_tokens": 128}
    state, run_dir = _run_phase2_single(tmp_path, "H4", usage, monkeypatch)
    expected = round(mm._usage_cost(h4, usage), 6)
    assert state["models"]["H4"]["phase2"]["cost_usd"] == pytest.approx(expected, abs=1e-9)


def test_phase2_cost_normalizes_anthropic_cache_usage(tmp_path, monkeypatch):
    """L1 claude-haiku: Anthropic慣習（input_tokensにcache_readを含まない）が正規化されること"""
    l1 = MODEL_REGISTRY["L1"]
    usage = {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60,
              "cache_read_input_tokens": 200}
    state, run_dir = _run_phase2_single(tmp_path, "L1", usage, monkeypatch)
    expected = round(mm._usage_cost(l1, usage), 6)
    assert state["models"]["L1"]["phase2"]["cost_usd"] == pytest.approx(expected, abs=1e-9)
    assert state["models"]["L1"]["phase2"]["cost_usd"] > 0


@pytest.mark.parametrize("key,input_tokens,output_tokens", [
    ("M2", 42, 5),
    ("M5", 42, 8),
    ("M6", 35, 5),
    ("L2", 30, 6),
])
def test_phase2_cost_unchanged_for_plain_usage(tmp_path, monkeypatch, key, input_tokens, output_tokens):
    """cacheもthinkingも無い通常usageでは、旧estimate_cost直呼び出しと完全に同値であること（非回帰）"""
    model = MODEL_REGISTRY[key]
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens,
              "total_tokens": input_tokens + output_tokens, "cache_read_input_tokens": 0}
    state, run_dir = _run_phase2_single(tmp_path, key, usage, monkeypatch)
    naive = round(estimate_cost(model, input_tokens, output_tokens), 6)
    assert state["models"][key]["phase2"]["cost_usd"] == pytest.approx(naive, abs=1e-9)


def test_phase2_cost_tolerates_missing_usage_fields(tmp_path, monkeypatch):
    """total_tokens/cache_read_input_tokensが無いusageでも例外を出さず算出できること"""
    l2 = MODEL_REGISTRY["L2"]
    usage = {"input_tokens": 20, "output_tokens": 4}
    state, run_dir = _run_phase2_single(tmp_path, "L2", usage, monkeypatch)
    expected = round(estimate_cost(l2, 20, 4), 6)
    assert state["models"]["L2"]["phase2"]["cost_usd"] == pytest.approx(expected, abs=1e-9)


def test_phase2_cost_zero_on_adapter_error(tmp_path, monkeypatch):
    """アダプタ例外時はusage={input:0,output:0}となり、記録コストが0.0であること"""
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_FakeErrorAdapter))
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    state["phases"]["1"] = {"status": "done"}
    state["models"]["L1"]["phase1"] = {"status": "pass"}
    mm.run_phase2(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=400,
                   resume=False, force=False, dry_run=False)
    assert state["models"]["L1"]["phase2"]["cost_usd"] == 0.0
    assert state["budget"]["spent_usd"] == 0.0


def test_phase2_records_cost_to_state_jsonl_and_budget(tmp_path, monkeypatch):
    """state / phase2_calls.jsonl / budget.spent_usd の3箇所が同一の正しいコストになること"""
    m3 = MODEL_REGISTRY["M3"]
    usage = {"input_tokens": 32, "output_tokens": 5, "total_tokens": 222,
              "cache_read_input_tokens": 0}
    state, run_dir = _run_phase2_single(tmp_path, "M3", usage, monkeypatch)
    expected = round(mm._usage_cost(m3, usage), 6)
    assert state["models"]["M3"]["phase2"]["cost_usd"] == pytest.approx(expected, abs=1e-9)
    assert state["budget"]["spent_usd"] == pytest.approx(expected, abs=1e-9)
    line = json.loads((run_dir / "phase2_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()[0])
    assert line["cost_usd"] == pytest.approx(expected, abs=1e-9)


def test_phase2_report_reflects_corrected_cost(tmp_path, monkeypatch):
    """_generate_phase2_report()が生成するphase2_report.mdに修正後コストが反映されること"""
    m3 = MODEL_REGISTRY["M3"]
    usage = {"input_tokens": 32, "output_tokens": 5, "total_tokens": 222,
              "cache_read_input_tokens": 0}
    state, run_dir = _run_phase2_single(tmp_path, "M3", usage, monkeypatch)
    expected = round(mm._usage_cost(m3, usage), 6)
    report_text = (run_dir / "phase2_report.md").read_text(encoding="utf-8")
    assert f"${expected:.4f}" in report_text


def test_phase2_makes_exactly_one_api_call(tmp_path, monkeypatch):
    """Phase2は1モデル1試行=API1コールのみで、usage合算処理が不要であることの構造的証明"""
    m3 = MODEL_REGISTRY["M3"]
    usage = {"input_tokens": 32, "output_tokens": 5, "total_tokens": 222,
              "cache_read_input_tokens": 0}
    captured_adapter = {}

    def _factory(model_info, max_retries=None):
        adapter = _FakeGameJsonUsageAdapter(model_info, usage)
        captured_adapter["adapter"] = adapter
        return adapter
    monkeypatch.setattr(mm, "create_adapter", _factory)
    monkeypatch.setenv(m3.env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["M3"])
    state["phases"]["1"] = {"status": "done"}
    state["models"]["M3"]["phase1"] = {"status": "pass"}
    mm.run_phase2(run_dir, state, ["M3"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=400,
                   resume=False, force=False, dry_run=False)
    assert captured_adapter["adapter"].calls == 1


# ============================================================
# Phase 3: BudgetedAdapter / 判定ロジック
# ============================================================

def test_budgeted_adapter_clamps_max_tokens_without_mutating_registry():
    info = get_model("L1")
    original_max_tokens = info.max_tokens
    inner = _FakeOkAdapter(info)
    wrapped = mm.BudgetedAdapter(inner, info, max_calls=5, max_cost=1.0, max_tokens_cap=50)
    wrapped.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=500, temperature=0.5)
    assert info.max_tokens == original_max_tokens  # レジストリ非破壊
    assert wrapped.calls_made == 1


def test_budgeted_adapter_stops_after_max_calls():
    info = get_model("L1")
    inner = _FakeOkAdapter(info)
    wrapped = mm.BudgetedAdapter(inner, info, max_calls=1, max_cost=1.0, max_tokens_cap=50)
    wrapped.complete("sys", [{"role": "user", "content": "hi"}])
    with pytest.raises(AdapterError):
        wrapped.complete("sys", [{"role": "user", "content": "hi"}])


def test_budgeted_adapter_stops_when_cost_cap_too_small():
    info = get_model("H1")  # 高額モデルで最悪見積が閾値を超えることを確認
    inner = _FakeOkAdapter(info)
    wrapped = mm.BudgetedAdapter(inner, info, max_calls=5, max_cost=0.0000001, max_tokens_cap=500)
    with pytest.raises(AdapterError):
        wrapped.complete("sys", [{"role": "user", "content": "hi" * 500}], max_tokens=500)


# ------------------------------------------------------------
# Phase 3: BudgetedAdapter コスト計算修正（_usage_cost()統一）
# ------------------------------------------------------------

def test_budgeted_adapter_cost_includes_gemini_hidden_thinking():
    """M3 gemini-3.5-flash: total_tokensに含まれるhidden thinkingがspent_usdに反映されること
    （旧estimate_cost直呼びだとinput/outputしか渡さずthinking分が消えていた）"""
    info = MODEL_REGISTRY["M3"]
    usage = {"input_tokens": 32, "output_tokens": 5, "total_tokens": 222,
             "cache_read_input_tokens": 0}
    inner = _FakeUsageRichAdapter(info, usage)
    wrapped = mm.BudgetedAdapter(inner, info, max_calls=5, max_cost=1.0, max_tokens_cap=500)
    wrapped.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=500)
    expected = round(mm._usage_cost(info, usage), 6)
    naive = round(estimate_cost(info, usage["input_tokens"], usage["output_tokens"]), 6)
    assert round(wrapped.spent_usd, 6) == pytest.approx(expected, abs=1e-9)
    assert wrapped.spent_usd > naive  # hidden thinking分が正しく加算されている


def test_budgeted_adapter_cost_applies_cache_discount_for_xai():
    """M4 grok-4.5: cache_read_input_tokens分が割引単価で計算されること"""
    info = MODEL_REGISTRY["M4"]
    usage = {"input_tokens": 526, "output_tokens": 5, "total_tokens": 573,
             "cache_read_input_tokens": 384}
    inner = _FakeUsageRichAdapter(info, usage)
    wrapped = mm.BudgetedAdapter(inner, info, max_calls=5, max_cost=1.0, max_tokens_cap=500)
    wrapped.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=500)
    expected = round(mm._usage_cost(info, usage), 6)
    naive = round(estimate_cost(info, usage["input_tokens"], usage["output_tokens"]), 6)
    assert round(wrapped.spent_usd, 6) == pytest.approx(expected, abs=1e-9)
    assert wrapped.spent_usd < naive  # cache割引分が正しく減算されている


def test_budgeted_adapter_cost_handles_cache_and_reasoning_together():
    """H4 grok-4.6: cache割引とreasoning(thinking)差分課金が同時に発生しても二重加算されないこと"""
    info = MODEL_REGISTRY["H4"]
    usage = {"input_tokens": 300, "output_tokens": 40, "total_tokens": 464,
             "cache_read_input_tokens": 128}
    inner = _FakeUsageRichAdapter(info, usage)
    wrapped = mm.BudgetedAdapter(inner, info, max_calls=5, max_cost=1.0, max_tokens_cap=500)
    wrapped.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=500)
    expected = round(mm._usage_cost(info, usage), 6)
    assert round(wrapped.spent_usd, 6) == pytest.approx(expected, abs=1e-9)


def test_budgeted_adapter_normalizes_anthropic_cache_usage():
    """L1 claude-haiku: Anthropic慣習（input_tokensにcache_readを含まない）が正規化されること"""
    info = MODEL_REGISTRY["L1"]
    usage = {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60,
             "cache_read_input_tokens": 200}
    inner = _FakeUsageRichAdapter(info, usage)
    wrapped = mm.BudgetedAdapter(inner, info, max_calls=5, max_cost=1.0, max_tokens_cap=500)
    wrapped.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=500)
    expected = round(mm._usage_cost(info, usage), 6)
    assert round(wrapped.spent_usd, 6) == pytest.approx(expected, abs=1e-9)
    assert wrapped.spent_usd > 0


@pytest.mark.parametrize("key,input_tokens,output_tokens", [
    ("M2", 42, 5),
    ("M5", 42, 8),
    ("M6", 35, 5),
    ("L2", 30, 6),
])
def test_budgeted_adapter_cost_unchanged_for_plain_usage(key, input_tokens, output_tokens):
    """cacheもthinkingも無い通常usageでは、旧estimate_cost直呼び出しと完全に同値であること（非回帰）"""
    info = MODEL_REGISTRY[key]
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens,
             "total_tokens": input_tokens + output_tokens, "cache_read_input_tokens": 0}
    inner = _FakeUsageRichAdapter(info, usage)
    wrapped = mm.BudgetedAdapter(inner, info, max_calls=5, max_cost=1.0, max_tokens_cap=500)
    wrapped.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=500)
    naive = round(estimate_cost(info, input_tokens, output_tokens), 6)
    assert round(wrapped.spent_usd, 6) == pytest.approx(naive, abs=1e-9)


def test_budgeted_adapter_tolerates_missing_usage_fields():
    """total_tokens/cache_read_input_tokensが無いusageでも例外を出さず算出できること"""
    info = MODEL_REGISTRY["L2"]
    usage = {"input_tokens": 20, "output_tokens": 4}
    inner = _FakeUsageRichAdapter(info, usage)
    wrapped = mm.BudgetedAdapter(inner, info, max_calls=5, max_cost=1.0, max_tokens_cap=500)
    wrapped.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=500)
    expected = round(estimate_cost(info, 20, 4), 6)
    assert round(wrapped.spent_usd, 6) == pytest.approx(expected, abs=1e-9)


def test_budgeted_adapter_accumulates_cost_over_multiple_calls():
    """3回complete()を呼ぶと、spent_usdが3×_usage_costの合計になること
    （Phase3は1モデルあたり複数コールが発生するため、コール単位の加算が正しいことの確認）"""
    info = MODEL_REGISTRY["M2"]
    usage = {"input_tokens": 42, "output_tokens": 5, "total_tokens": 47,
             "cache_read_input_tokens": 0}
    inner = _FakeUsageRichAdapter(info, usage)
    wrapped = mm.BudgetedAdapter(inner, info, max_calls=5, max_cost=1.0, max_tokens_cap=500)
    for _ in range(3):
        wrapped.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=500)
    expected = round(3 * mm._usage_cost(info, usage), 6)
    assert round(wrapped.spent_usd, 6) == pytest.approx(expected, abs=1e-9)
    assert wrapped.calls_made == 3


def test_budgeted_adapter_blocks_next_call_when_budget_reached():
    """修正後の実コストに基づき、予算到達後は次のcomplete()がAdapterErrorになること
    （既存の事前ガード＝worst-case予約方式の維持確認。Geminiのhidden thinkingを含む
    実コストで2コール分ちょうどの予算を与え、3コール目がブロックされることを確認）"""
    info = MODEL_REGISTRY["M3"]
    usage = {"input_tokens": 32, "output_tokens": 5, "total_tokens": 222,
             "cache_read_input_tokens": 0}
    per_call = mm._usage_cost(info, usage)
    inner = _FakeUsageRichAdapter(info, usage)
    wrapped = mm.BudgetedAdapter(inner, info, max_calls=10, max_cost=per_call * 2 + 1e-9,
                                  max_tokens_cap=50)
    wrapped.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=50)
    wrapped.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=50)
    with pytest.raises(AdapterError):
        wrapped.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=50)
    assert wrapped.calls_made == 2


def test_budgeted_adapter_does_not_charge_on_inner_error():
    """innerがAdapterErrorを投げた場合、calls_made/spent_usdとも加算されないこと（現行仕様の明文化）"""
    info = MODEL_REGISTRY["L1"]
    inner = _FakeErrorAdapter(info)
    wrapped = mm.BudgetedAdapter(inner, info, max_calls=5, max_cost=1.0, max_tokens_cap=50)
    with pytest.raises(AdapterError):
        wrapped.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=50)
    assert wrapped.calls_made == 0
    assert wrapped.spent_usd == 0.0


class _FakeEvent:
    def __init__(self, event_type, data):
        self.event_type = event_type
        self.data = data


class _FakeAgent:
    def __init__(self, auto_commit_count, valid_json_count, total_calls):
        self.auto_commit_count = auto_commit_count
        self.valid_json_count = valid_json_count
        self.total_calls = total_calls


def test_judge_phase3_pass_requires_game_end_and_non_auto_commit():
    events = [
        _FakeEvent("COMMIT", {"player_id": "P01", "auto": False}),
        _FakeEvent("GAME_END", {}),
    ]
    agent = _FakeAgent(auto_commit_count=0, valid_json_count=2, total_calls=3)
    verdict = mm._judge_phase3(events, agent)
    assert verdict["passed"] is True


def test_judge_phase3_fails_on_auto_commit():
    events = [
        _FakeEvent("AUTO_COMMIT", {"player_id": "P01"}),
        _FakeEvent("GAME_END", {}),
    ]
    agent = _FakeAgent(auto_commit_count=1, valid_json_count=0, total_calls=3)
    verdict = mm._judge_phase3(events, agent)
    assert verdict["passed"] is False


def test_judge_phase3_fails_without_game_end():
    events = [_FakeEvent("COMMIT", {"player_id": "P01", "auto": False})]
    agent = _FakeAgent(auto_commit_count=0, valid_json_count=2, total_calls=3)
    verdict = mm._judge_phase3(events, agent)
    assert verdict["passed"] is False


def test_phase3_config_comes_from_baseline_v1_s2_factory():
    config = mm.build_phase3_config()
    assert config.num_rounds == 1
    assert len(config.prize_tiers) == 1
    assert config.negotiation_max_turns == 1
    assert config.num_players == 4
    assert config.enable_cot is False
    assert config.memory_enabled is False
    assert config.double_up_enabled is False
    assert config.surge_enabled is True  # baseline_v1_s2由来のフィールドは維持される


def test_phase3_runs_end_to_end_with_fake_adapter_and_saves_logs(tmp_path, monkeypatch):
    """フェイクアダプタで実ゲームを1本走らせ、ログ保存とクラッシュ耐性を確認する（勝敗結果は問わない）"""
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_FakeOkAdapter))
    info = get_model("L1")
    result = mm._run_one_phase3_game("L1", info, tmp_path, seed=1, max_calls=20,
                                       max_cost_per_model=1.0, max_tokens=500)
    assert result["error"] is None
    assert (tmp_path / "phase3" / "L1" / "game01_events.jsonl").exists()
    llm_log_files = list((tmp_path / "phase3" / "L1" / "llm_logs").glob("*.jsonl"))
    assert len(llm_log_files) >= 1
    assert result["calls_made"] <= 20


def test_phase3_saves_logs_even_when_game_raises(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_FakeOkAdapter))

    class _ExplodingGame:
        def __init__(self, *a, **kw):
            pass

        def run(self):
            raise RuntimeError("boom")

    monkeypatch.setattr(mm, "Game", _ExplodingGame)
    info = get_model("L1")
    result = mm._run_one_phase3_game("L1", info, tmp_path, seed=1, max_calls=20,
                                       max_cost_per_model=1.0, max_tokens=500)
    assert result["error"] is not None
    assert "RuntimeError" in result["error"]
    # finallyでllm_logger.save()/event_logger.save_jsonl()が呼ばれ、ファイルが残ること
    assert (tmp_path / "phase3" / "L1" / "game01_events.jsonl").exists()
    llm_log_files = list((tmp_path / "phase3" / "L1" / "llm_logs").glob("*.jsonl"))
    assert len(llm_log_files) >= 1


def test_phase3_game_records_corrected_cost(tmp_path, monkeypatch):
    """_run_one_phase3_gameが、hidden thinkingを含む実コストをcost_usdに正しく記録すること
    （旧estimate_cost直呼びの値とは一致しないこと）"""
    m3 = MODEL_REGISTRY["M3"]
    usage = {"input_tokens": 32, "output_tokens": 5, "total_tokens": 222,
             "cache_read_input_tokens": 0}

    def _factory(model_info, max_retries=None):
        return _FakeGameJsonUsageAdapter(model_info, usage)
    monkeypatch.setattr(mm, "create_adapter", _factory)
    result = mm._run_one_phase3_game("M3", m3, tmp_path, seed=1, max_calls=20,
                                       max_cost_per_model=1.0, max_tokens=500)
    per_call = mm._usage_cost(m3, usage)
    expected = round(result["calls_made"] * per_call, 6)
    naive = round(
        result["calls_made"] * estimate_cost(m3, usage["input_tokens"], usage["output_tokens"]), 6)
    assert result["calls_made"] >= 1
    assert result["cost_usd"] == pytest.approx(expected, abs=1e-6)
    assert result["cost_usd"] != pytest.approx(naive, abs=1e-9)


def test_phase3_records_cost_to_state_jsonl_and_report(tmp_path, monkeypatch):
    """state / phase3_calls.jsonl / phase3_report.md / budget.spent_usd が同一の修正後コストを示すこと"""
    m3 = MODEL_REGISTRY["M3"]
    usage = {"input_tokens": 32, "output_tokens": 5, "total_tokens": 222,
             "cache_read_input_tokens": 0}

    def _factory(model_info, max_retries=None):
        return _FakeGameJsonUsageAdapter(model_info, usage)
    monkeypatch.setattr(mm, "create_adapter", _factory)
    monkeypatch.setenv(m3.env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["M3"])
    state["phases"]["1"] = {"status": "done"}
    state["phases"]["2"] = {"status": "done"}
    state["models"]["M3"]["phase1"] = {"status": "pass"}
    state["models"]["M3"]["phase2"] = {"status": "pass"}
    mm.run_phase3(run_dir, state, ["M3"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=20, max_tokens=500,
                   seed=1, resume=False, force=False, dry_run=False)
    cost = state["models"]["M3"]["phase3"]["cost_usd"]
    assert cost > 0
    assert state["budget"]["spent_usd"] == pytest.approx(cost, abs=1e-9)
    line = json.loads((run_dir / "phase3_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()[0])
    assert line["cost_usd"] == pytest.approx(cost, abs=1e-9)
    report_text = (run_dir / "phase3_report.md").read_text(encoding="utf-8")
    assert f"${cost:.4f}" in report_text


# ============================================================
# 安全性・レポート
# ============================================================

def test_model_registry_not_mutated_by_phase1_run(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_FakeOkAdapter))
    monkeypatch.setenv(MODEL_REGISTRY["L1"].env_key, "fake-key")
    snapshot = dict(MODEL_REGISTRY["L1"].__dict__)
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    mm.run_phase1(run_dir, state, ["L1"], max_cost=1.0, max_cost_total=1.0, max_cost_per_model=1.0,
                   max_calls=10, max_tokens=64, retries=0, resume=False, force=False, dry_run=False)
    assert dict(MODEL_REGISTRY["L1"].__dict__) == snapshot


def test_final_report_has_all_columns_and_one_row_per_model(tmp_path):
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1", "L2"])
    out_path = run_dir / "matrix_report.md"
    mm.generate_final_report(run_dir, state, [out_path])
    text = out_path.read_text(encoding="utf-8")
    for col in ("Vendor", "Tier", "Model", "API", "JSON", "Game", "Latency", "Cost", "備考"):
        assert col in text
    assert "L1" in text
    assert "L2" in text


def test_report_flag_regenerates_table_without_api(tmp_path, monkeypatch):
    monkeypatch.setattr(mm, "create_adapter", _fake_create_adapter_factory(_RefuseToCallAdapter))
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["L1"])
    mm._save_state(run_dir, state)
    reloaded = mm._load_state(run_dir)
    mm.generate_final_report(run_dir, reloaded, [run_dir / "matrix_report.md"])
    assert (run_dir / "matrix_report.md").exists()


def test_estimate_cost_consistent_with_worst_case_cost():
    """worst_case_costがestimate_costと同じ単価テーブルを使っていることの回帰確認"""
    info = get_model("L1")
    worst = mm._worst_case_cost(info, "system", "user", 100)
    assert worst >= 0
    assert worst == estimate_cost(info, (len("system") + len("user")) // 2 + 50, 100)


# ============================================================
# _usage_cost: cache割引・thinking差分課金の正しい反映（コスト計算バグ修正）
# ============================================================

def test_usage_cost_applies_cache_discount_for_xai():
    """M4 grok-4.5 実測値: cache_read分は割引単価になり、素朴計算より安くなる"""
    m4 = MODEL_REGISTRY["M4"]
    usage = {"input_tokens": 526, "output_tokens": 5, "total_tokens": 573,
              "cache_read_input_tokens": 384}
    cost = mm._usage_cost(m4, usage)
    naive = estimate_cost(m4, 526, 5)
    assert cost == pytest.approx(0.000681, abs=1e-6)
    assert cost < naive


def test_usage_cost_adds_separate_thinking_for_gemini():
    """M3 gemini-3.5-flash 実測値: total>input+outputの差分がthinkingとして加算される"""
    m3 = MODEL_REGISTRY["M3"]
    usage = {"input_tokens": 32, "output_tokens": 5, "total_tokens": 222,
              "cache_read_input_tokens": 0}
    cost = mm._usage_cost(m3, usage)
    naive = estimate_cost(m3, 32, 5)
    assert cost == pytest.approx(0.001758, abs=1e-6)
    assert cost > naive


@pytest.mark.parametrize("key,input_tokens,output_tokens", [
    ("M1", 53, 9),
    ("M2", 42, 5),
    ("M5", 42, 8),
    ("M6", 35, 5),
])
def test_usage_cost_no_double_count_when_total_equals_in_plus_out(key, input_tokens, output_tokens):
    """total==input+outputかつcache_read=0のときは素朴計算と完全一致（二重計上なし）"""
    model = MODEL_REGISTRY[key]
    usage = {"input_tokens": input_tokens, "output_tokens": output_tokens,
              "total_tokens": input_tokens + output_tokens, "cache_read_input_tokens": 0}
    cost = mm._usage_cost(model, usage)
    naive = estimate_cost(model, input_tokens, output_tokens)
    assert cost == pytest.approx(naive, abs=1e-9)


def test_usage_cost_backward_compatible_when_fields_missing():
    """cache_read_input_tokens/total_tokensが無いusageでも従来通り動く"""
    model = MODEL_REGISTRY["L1"]
    usage = {"input_tokens": 100, "output_tokens": 20}
    cost = mm._usage_cost(model, usage)
    assert cost == pytest.approx(estimate_cost(model, 100, 20), abs=1e-12)


def test_usage_cost_anthropic_cache_read_not_subtracted_from_input():
    """
    Anthropicはinput_tokensとcache_read_input_tokensを別建てで返す（OpenAI慣習と逆）。
    素朴にcache_readをestimate_costへ渡すと uncached_input が負になってしまうため、
    _usage_costがAnthropicのみinput_tokensへcache_readを合算して正規化することを確認する。
    """
    anthropic_model = ModelInfo(
        model_id="test-anthropic", provider="Anthropic", name="Test Anthropic",
        adapter_type="anthropic", input_price=2.0, output_price=10.0,
        env_key="TEST_KEY", base_url=None, cached_input_price=0.2,
    )
    usage = {"input_tokens": 50, "output_tokens": 10, "total_tokens": 60,
              "cache_read_input_tokens": 200}
    cost = mm._usage_cost(anthropic_model, usage)
    # 正規化後: uncached_input=50, cache_read=200 → input_cost = 50*2.0 + 200*0.2 = 140
    # output_cost = 10*10.0 = 100 → thinking差分は total(260)-input(250)-output(10)=0
    expected = (140 + 100) / 1_000_000
    assert cost == pytest.approx(expected, abs=1e-12)
    assert cost > 0  # 負のコストにならないこと


def test_phase1_records_corrected_cost_in_state_and_jsonl(tmp_path, monkeypatch):
    """run_phase1が_usage_costで算出した修正値をstate/JSONLに記録すること"""
    m4 = MODEL_REGISTRY["M4"]
    usage = {"input_tokens": 526, "output_tokens": 5, "total_tokens": 573,
              "cache_read_input_tokens": 384,
              "requested_model": m4.model_id, "response_model": m4.model_id}

    def _factory(model_info, max_retries=None):
        return _FakeUsageRichAdapter(model_info, usage)
    monkeypatch.setattr(mm, "create_adapter", _factory)
    monkeypatch.setenv(m4.env_key, "fake-key")
    run_dir = tmp_path / "run1"
    state = mm._init_state("run1", 1.0, ["M4"])
    mm.run_phase1(run_dir, state, ["M4"], max_cost=1.0, max_cost_total=1.0,
                   max_cost_per_model=1.0, max_calls=10, max_tokens=64, retries=0,
                   resume=False, force=False, dry_run=False)
    expected_cost = round(mm._usage_cost(m4, usage), 6)
    assert state["models"]["M4"]["phase1"]["cost_usd"] == pytest.approx(expected_cost, abs=1e-9)
    naive = round(estimate_cost(m4, 526, 5), 6)
    assert expected_cost != naive
    line = json.loads((run_dir / "phase1_calls.jsonl").read_text(encoding="utf-8").strip().splitlines()[0])
    assert line["cost_usd"] == pytest.approx(expected_cost, abs=1e-9)
