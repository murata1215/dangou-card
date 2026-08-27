"""Cycle 5.4: scripts/replay_probe.py の対象選択（既定27件 / --targets-file）の回帰テスト。

API呼び出しは一切行わない（LLMアダプタは`_load_targets`/`_prepare_targets`の
経路には現れない。run_executeのみがllm.adaptersを遅延importするが、本テスト
はrun_executeを呼ばない）。

`logs/`はgitignore対象のため、実trialログに依存するテストは
`pytest.mark.skipif(not rp.TRIAL_DIR.exists())`で退避する。
"""
from __future__ import annotations

import json
import sys

import pytest

from scripts import replay_probe as rp

TRIAL_AVAILABLE = rp.TRIAL_DIR.exists()
requires_trial = pytest.mark.skipif(
    not TRIAL_AVAILABLE,
    reason=f"trialログが見つかりません: {rp.TRIAL_DIR}（logs/はgitignore対象）",
)

# 既定TARGET_TURNSテーブルから実測済みの27件・出現順（P03 R6 t8のみ2レコード）。
EXPECTED_DEFAULT_TARGETS = [
    ("P02", 1, 1), ("P02", 2, 2), ("P02", 3, 1), ("P02", 3, 2),
    ("P02", 3, 5), ("P02", 3, 7), ("P02", 6, 2), ("P02", 7, 8),
    ("P03", 1, 2), ("P03", 6, 7), ("P03", 6, 8), ("P03", 6, 8),
    ("P03", 6, 9), ("P03", 6, 10),
    ("P04", 6, 2),
    ("P06", 3, 2), ("P06", 6, 2), ("P06", 6, 6), ("P06", 6, 8),
    ("P09", 4, 4), ("P09", 5, 6), ("P09", 5, 7), ("P09", 5, 8),
    ("P09", 5, 10), ("P09", 6, 5), ("P09", 6, 9),
    ("P11", 6, 4),
]


def _write_targets_file(tmp_path, targets, **doc_overrides):
    doc = {
        "schema_version": rp.TARGETS_FILE_SCHEMA_VERSION,
        "generated_by": {"cycle": "5.4-test", "tool": "pytest"},
        "definition": {"rule_id": "unit_test", "description": "test only"},
        "config_preset": rp.EXPECTED_CONFIG_PRESET,
        "targets": targets,
    }
    doc.update(doc_overrides)
    path = tmp_path / "targets.json"
    path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# 既定モード（TARGET_TURNS）が今回の改修で1ビットも変わっていないことの回帰
# ---------------------------------------------------------------------------
@requires_trial
class TestDefaultModeUnchanged:
    def test_load_targets_yields_27_records_in_known_order(self):
        targets = rp._load_targets()
        got = [(t.player_id, t.round_num, t.turn) for t in targets]
        assert got == EXPECTED_DEFAULT_TARGETS
        assert len(targets) == 27

    def test_default_mode_meta_is_empty(self):
        targets = rp._load_targets()
        assert targets, "既定モードで対象が0件になっている"
        assert all(t.meta == {} for t in targets)

    def test_explicit_none_is_same_as_omitted(self):
        assert rp._load_targets() == rp._load_targets(None)

    def test_prepared_targets_indices_are_dense_and_ordered(self):
        config = rp.GameConfig.baseline_v1_s2(12)
        prepared = rp._prepare_targets(config)
        assert [p.index for p in prepared] == list(range(len(prepared)))
        assert all(p.meta == {} for p in prepared)

    def test_duplicate_coordinate_p03_r6_t8_yields_two_records(self):
        targets = rp._load_targets()
        matches = [
            t for t in targets
            if (t.player_id, t.round_num, t.turn) == ("P03", 6, 8)
        ]
        assert len(matches) == 2


# ---------------------------------------------------------------------------
# --targets-file モード（Cycle 5.4新設）
# ---------------------------------------------------------------------------
@requires_trial
class TestTargetsFileMode:
    def test_loads_in_file_order_with_meta(self, tmp_path):
        targets = [
            {
                "player_id": "P09", "round_num": 6, "turn": 9, "occurrence": 0,
                "meta": {"role": "proposer", "peer": "P07"},
            },
            {
                "player_id": "P02", "round_num": 1, "turn": 1, "occurrence": 0,
                "meta": {"role": "replier", "peer": "P06"},
            },
        ]
        path = _write_targets_file(tmp_path, targets)
        loaded = rp._load_targets(path)
        assert [(t.player_id, t.round_num, t.turn) for t in loaded] == [
            ("P09", 6, 9), ("P02", 1, 1),
        ]
        assert loaded[0].meta == {"role": "proposer", "peer": "P07"}
        assert loaded[1].meta == {"role": "replier", "peer": "P06"}

    def test_prepare_targets_carries_meta_and_role_counts(self, tmp_path):
        targets = [
            {"player_id": "P09", "round_num": 6, "turn": 9,
             "meta": {"role": "proposer", "peer": "P07"}},
            {"player_id": "P02", "round_num": 1, "turn": 1,
             "meta": {"role": "replier", "peer": "P06"}},
        ]
        path = _write_targets_file(tmp_path, targets)
        config = rp.GameConfig.baseline_v1_s2(12)
        prepared = rp._prepare_targets(config, path)
        assert len(prepared) == 2
        assert [p.index for p in prepared] == [0, 1]
        assert prepared[0].meta["role"] == "proposer"
        assert prepared[1].meta["role"] == "replier"
        assert rp._role_counts(prepared) == {"proposer": 1, "replier": 1}

    def test_all_closing_replaced_for_real_coordinates(self, tmp_path):
        targets = [{"player_id": "P02", "round_num": 1, "turn": 1}]
        path = _write_targets_file(tmp_path, targets)
        config = rp.GameConfig.baseline_v1_s2(12)
        prepared = rp._prepare_targets(config, path)
        assert prepared[0].closing_replaced is True

    def test_idempotency_guard_no_double_success_condition(self, tmp_path):
        targets = [{"player_id": "P02", "round_num": 1, "turn": 1}]
        path = _write_targets_file(tmp_path, targets)
        config = rp.GameConfig.baseline_v1_s2(12)
        prepared = rp._prepare_targets(config, path)
        assert prepared[0].patched_user.count("成功条件:") <= 1

    def test_meta_defaults_to_empty_dict_when_absent(self, tmp_path):
        targets = [{"player_id": "P02", "round_num": 1, "turn": 1}]
        path = _write_targets_file(tmp_path, targets)
        loaded = rp._load_targets(path)
        assert loaded[0].meta == {}

    def test_unknown_schema_version_raises(self, tmp_path):
        path = _write_targets_file(
            tmp_path, [{"player_id": "P02", "round_num": 1, "turn": 1}],
            schema_version=99,
        )
        with pytest.raises(rp.TargetsFileError):
            rp._load_targets(path)

    def test_unknown_role_raises(self, tmp_path):
        targets = [{
            "player_id": "P02", "round_num": 1, "turn": 1,
            "meta": {"role": "villain"},
        }]
        path = _write_targets_file(tmp_path, targets)
        with pytest.raises(rp.TargetsFileError):
            rp._load_targets(path)

    def test_nonexistent_coordinate_raises(self, tmp_path):
        targets = [{"player_id": "P02", "round_num": 999, "turn": 999}]
        path = _write_targets_file(tmp_path, targets)
        with pytest.raises(rp.TargetsFileError):
            rp._load_targets(path)

    def test_wrong_config_preset_raises(self, tmp_path):
        path = _write_targets_file(
            tmp_path, [{"player_id": "P02", "round_num": 1, "turn": 1}],
            config_preset="something_else",
        )
        with pytest.raises(rp.TargetsFileError):
            rp._load_targets(path)

    def test_bad_player_id_raises(self, tmp_path):
        path = _write_targets_file(tmp_path, [{"player_id": "P2", "round_num": 1, "turn": 1}])
        with pytest.raises(rp.TargetsFileError):
            rp._load_targets(path)

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(rp.TargetsFileError):
            rp._load_targets(tmp_path / "does_not_exist.json")

    def test_empty_targets_list_raises(self, tmp_path):
        path = _write_targets_file(tmp_path, [])
        with pytest.raises(rp.TargetsFileError):
            rp._load_targets(path)

    def test_out_of_range_occurrence_raises(self, tmp_path):
        # (P03, 6, 8)は2レコードしか無いのでoccurrence=2は範囲外
        path = _write_targets_file(
            tmp_path,
            [{"player_id": "P03", "round_num": 6, "turn": 8, "occurrence": 2}],
        )
        with pytest.raises(rp.TargetsFileError):
            rp._load_targets(path)


# ---------------------------------------------------------------------------
# role分割バケット・paired比較（合成データのみ・API/trial不要）
# ---------------------------------------------------------------------------
class TestRoleSplitBuckets:
    def _records(self):
        def rec(sample, idx, variant, role, is_contract, api_ok=True):
            return {
                "sample_index": sample, "target_index": idx, "variant": variant,
                "role": role, "is_contract_action": is_contract, "api_ok": api_ok,
            }

        return [
            rec(1, 1, "baseline", "proposer", False),
            rec(1, 1, "patched", "proposer", True),
            rec(1, 2, "baseline", "replier", False),
            rec(1, 2, "patched", "replier", False),
            rec(1, 3, "baseline", "proposer", True),
            rec(1, 3, "patched", "proposer", False),
            rec(1, 4, "baseline", "replier", False),
            rec(1, 4, "patched", "replier", True),
        ]

    def test_paired_counts_overall(self):
        pc = rp._paired_counts(self._records())
        assert pc["n_pairs"] == 4
        assert pc["b_baseline0_patched1"] == 2
        assert pc["c_baseline1_patched0"] == 1
        assert pc["both_contract"] == 0
        assert pc["neither_contract"] == 1

    def test_paired_counts_by_role(self):
        pc_p = rp._paired_counts(self._records(), role="proposer")
        assert pc_p["n_pairs"] == 2
        assert pc_p["b_baseline0_patched1"] == 1
        assert pc_p["c_baseline1_patched0"] == 1

        pc_r = rp._paired_counts(self._records(), role="replier")
        assert pc_r["n_pairs"] == 2
        assert pc_r["b_baseline0_patched1"] == 1
        assert pc_r["c_baseline1_patched0"] == 0

    def test_api_error_pairs_are_excluded(self):
        records = self._records()
        records.append({
            "sample_index": 1, "target_index": 5, "variant": "baseline",
            "role": "proposer", "is_contract_action": False, "api_ok": False,
        })
        records.append({
            "sample_index": 1, "target_index": 5, "variant": "patched",
            "role": "proposer", "is_contract_action": True, "api_ok": True,
        })
        pc = rp._paired_counts(records)
        assert pc["n_pairs"] == 4  # api_ok=False側のあるペアは対から除外される

    def test_single_variant_run_yields_zero_pairs(self):
        records = [r for r in self._records() if r["variant"] == "baseline"]
        pc = rp._paired_counts(records)
        assert pc["n_pairs"] == 0
        mc = rp._mcnemar_exact(pc["b_baseline0_patched1"], pc["c_baseline1_patched0"])
        assert mc["p_value"] == 1.0
        assert mc["n_discordant"] == 0


# ---------------------------------------------------------------------------
# McNemar正確検定（既発表値によるgolden vector）
# ---------------------------------------------------------------------------
class TestMcNemarExact:
    def test_cycle_5_1_published_value(self):
        # doc/devlog/2026-08-26_130007.md 記載: Cycle5.1 b=2,c=0 -> p=0.50
        result = rp._mcnemar_exact(2, 0)
        assert result["p_value"] == pytest.approx(0.50)

    def test_cycle_5_3_published_value(self):
        # 同上: Cycle5.3 b=2,c=1 -> p=1.0
        result = rp._mcnemar_exact(2, 1)
        assert result["p_value"] == pytest.approx(1.00)

    def test_no_discordant_pairs(self):
        result = rp._mcnemar_exact(0, 0)
        assert result["p_value"] == 1.0
        assert result["n_discordant"] == 0

    @pytest.mark.parametrize("b,c,expected", [
        (0, 0, 1.0),
        (1, 0, 1.0),
        (2, 0, 0.5),
        (2, 1, 1.0),
        (5, 0, 2 * (1 / 32)),
        (6, 0, 2 * (1 / 64)),
    ])
    def test_exact_binomial_values(self, b, c, expected):
        result = rp._mcnemar_exact(b, c)
        assert result["p_value"] == pytest.approx(expected, abs=1e-9)

    def test_symmetry(self):
        assert rp._mcnemar_exact(3, 5)["p_value"] == pytest.approx(
            rp._mcnemar_exact(5, 3)["p_value"]
        )

    def test_p_value_never_exceeds_one(self):
        for b in range(0, 10):
            for c in range(0, 10):
                assert rp._mcnemar_exact(b, c)["p_value"] <= 1.0

    def test_binom_cdf_half_edges(self):
        assert rp._binom_cdf_half(0, 0) == 1.0
        assert rp._binom_cdf_half(-5, 4) == rp._binom_cdf_half(0, 4)
        assert rp._binom_cdf_half(100, 4) == rp._binom_cdf_half(4, 4)

    def test_large_n_does_not_overflow(self):
        result = rp._mcnemar_exact(40, 2)
        assert 0.0 <= result["p_value"] <= 1.0
        assert result["n_discordant"] == 42


# ---------------------------------------------------------------------------
# 実際のtargets_cycle54.json（生成後のみ有効。無ければskip）
# ---------------------------------------------------------------------------
_REAL_TARGETS_FILE = rp.TRIAL_DIR.parent.parent.parent / "replay_probe" / "targets_cycle54.json"


@pytest.mark.skipif(
    not (TRIAL_AVAILABLE and _REAL_TARGETS_FILE.exists()),
    reason=f"targets_cycle54.jsonが未生成、またはtrialログ不在: {_REAL_TARGETS_FILE}",
)
class TestRealTargetsFile:
    def test_loads_and_all_have_role(self):
        loaded = rp._load_targets(_REAL_TARGETS_FILE)
        assert len(loaded) >= 20
        assert all(t.meta.get("role") in rp.KNOWN_ROLES for t in loaded)

    def test_both_roles_present(self):
        loaded = rp._load_targets(_REAL_TARGETS_FILE)
        roles = {t.meta.get("role") for t in loaded}
        assert roles == {"proposer", "replier"}

    def test_all_closing_replaced(self):
        config = rp.GameConfig.baseline_v1_s2(12)
        prepared = rp._prepare_targets(config, _REAL_TARGETS_FILE)
        assert all(p.closing_replaced for p in prepared)

    def test_declared_counts_match(self):
        doc = json.loads(_REAL_TARGETS_FILE.read_text(encoding="utf-8"))
        counts = doc.get("definition", {}).get("counts", {})
        loaded = rp._load_targets(_REAL_TARGETS_FILE)
        if "total" in counts:
            assert len(loaded) == counts["total"]


# ---------------------------------------------------------------------------
# Cycle 5.5: targets_cycle55.json（19件・チェーン一意・★手番必須）
# ---------------------------------------------------------------------------
_REAL_TARGETS_FILE_55 = rp.TRIAL_DIR.parent.parent.parent / "replay_probe" / "targets_cycle55.json"


@pytest.mark.skipif(
    not (TRIAL_AVAILABLE and _REAL_TARGETS_FILE_55.exists()),
    reason=f"targets_cycle55.jsonが未生成、またはtrialログ不在: {_REAL_TARGETS_FILE_55}",
)
class TestRealTargetsFileCycle55:
    def test_loads_19_with_role_balance(self):
        loaded = rp._load_targets(_REAL_TARGETS_FILE_55)
        assert len(loaded) == 19
        roles = [t.meta.get("role") for t in loaded]
        assert roles.count("proposer") == 10
        assert roles.count("replier") == 9

    def test_chains_are_unique(self):
        loaded = rp._load_targets(_REAL_TARGETS_FILE_55)
        chain_keys = set()
        for t in loaded:
            dm = t.meta["agreement_dm"]
            key = (dm["round_num"], dm["turn"], dm["sender"], dm["recipient"])
            assert key not in chain_keys, f"チェーン重複: {key}"
            chain_keys.add(key)
        assert len(chain_keys) == 19

    def test_includes_star_target(self):
        # Cycle 5.4で唯一 contract_propose に転換した手番（必須採用）
        loaded = rp._load_targets(_REAL_TARGETS_FILE_55)
        assert any(
            t.player_id == "P03" and t.round_num == 6 and t.turn == 10
            and t.meta.get("role") == "proposer"
            for t in loaded
        )

    def test_all_closing_replaced(self):
        config = rp.GameConfig.baseline_v1_s2(12)
        prepared = rp._prepare_targets(config, _REAL_TARGETS_FILE_55)
        assert len(prepared) == 19
        assert all(p.closing_replaced for p in prepared)


# ---------------------------------------------------------------------------
# Cycle 5.5: --thinking の request_options テーブル（API呼び出しなし）
# ---------------------------------------------------------------------------
class TestThinkingRequestOptions:
    def test_off_is_always_none_regardless_of_provider(self):
        for key in ("M1", "TERRA", "H4", "H6", "M5"):
            model = rp.get_model(key)
            assert rp._thinking_request_options(model, "off") is None

    def test_anthropic_medium_uses_proven_adaptive_shape(self):
        # scripts/model_matrix.py _phase3_request_options() で実証済みの形。
        # タスク指定の{"type":"enabled","budget_tokens":N}ではない。
        model = rp.get_model("M1")
        opts = rp._thinking_request_options(model, "medium")
        assert opts == {"thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}}

    def test_openai_medium_uses_reasoning_effort(self):
        model = rp.get_model("TERRA")
        opts = rp._thinking_request_options(model, "medium")
        assert opts == {"reasoning_effort": "medium"}

    def test_xai_medium_sends_nothing_extra(self):
        # grok-4.6は既定で常時思考するため、追加のrequest_optionsは送らない
        # （hidden_thinking_reserve_tokensで別途会計する）。
        model = rp.get_model("H4")
        assert rp._thinking_request_options(model, "medium") is None

    def test_deepseek_medium_overrides_registry_disable(self):
        model = rp.get_model("H6")
        opts = rp._thinking_request_options(model, "medium")
        assert opts == {"extra_body": {"thinking": {"type": "enabled"}}}
        # registry自体のextra_paramsは無効化のまま（上書きはrequest_options側の責務）
        assert model.extra_params == {"thinking": {"type": "disabled"}}


# ---------------------------------------------------------------------------
# Cycle 5.5: _hardened_cost の include_hidden_reserve（既定Falseは現行と完全同値）
# ---------------------------------------------------------------------------
class TestHardenedCostHiddenReserve:
    def test_default_is_false_and_matches_explicit_false(self):
        model = rp.get_model("H4")
        system, user = "a" * 100, "b" * 100
        assert rp._hardened_cost(model, system, user, 6000) == rp._hardened_cost(
            model, system, user, 6000, include_hidden_reserve=False,
        )

    def test_true_adds_hidden_reserve_at_reasoning_price(self):
        model = rp.get_model("H4")  # hidden_thinking_reserve_tokens=1536
        system, user = "a" * 100, "b" * 100
        without = rp._hardened_cost(model, system, user, 6000, include_hidden_reserve=False)
        with_reserve = rp._hardened_cost(model, system, user, 6000, include_hidden_reserve=True)
        effective_reasoning_price = model.reasoning_price if model.reasoning_price is not None else model.output_price
        expected_diff = model.hidden_thinking_reserve_tokens * effective_reasoning_price / 1_000_000
        assert with_reserve - without == pytest.approx(expected_diff)

    def test_zero_reserve_model_is_unaffected(self):
        model = rp.get_model("M1")  # hidden_thinking_reserve_tokens=0（既定）
        system, user = "a" * 100, "b" * 100
        assert rp._hardened_cost(model, system, user, 2000, include_hidden_reserve=False) == \
            rp._hardened_cost(model, system, user, 2000, include_hidden_reserve=True)

    def test_estimate_call_cost_default_matches_hardened_false(self):
        model = rp.get_model("H4")
        system, user = "a" * 50, "b" * 50
        via_estimate = rp._estimate_call_cost("hardened", model, system, user, 6000, 0.5, 400)
        via_direct = rp._hardened_cost(model, system, user, 6000, include_hidden_reserve=False)
        assert via_estimate == via_direct


# ---------------------------------------------------------------------------
# Cycle 5.5: --model-override（_apply_model_override）。trial log不要（合成データ）
# ---------------------------------------------------------------------------
def _dummy_prepared_target(**overrides) -> "rp.PreparedTarget":
    base = dict(
        index=0, player_id="P99", round_num=1, turn=1,
        model_id="deepseek-v4-flash", model_key="M6",
        baseline_system="sys_b", baseline_user="user_b",
        patched_system="sys_p", patched_user="user_p",
        closing_replaced=True, deposit_replaced=False,
        logged_input_tokens=100, logged_output_tokens=50, logged_cost_usd=0.001,
        input_token_ratio=0.5, meta={"role": "proposer", "peer": "P98"},
    )
    base.update(overrides)
    return rp.PreparedTarget(**base)


class TestModelOverride:
    def test_replaces_only_model_key_and_model_id(self):
        t = _dummy_prepared_target()
        overridden = rp._apply_model_override([t], "H4")[0]
        assert overridden.model_key == "H4"
        assert overridden.model_id == rp.get_model("H4").model_id
        assert overridden.baseline_system == t.baseline_system
        assert overridden.baseline_user == t.baseline_user
        assert overridden.patched_system == t.patched_system
        assert overridden.patched_user == t.patched_user
        assert overridden.meta == t.meta
        assert overridden.input_token_ratio == t.input_token_ratio
        assert overridden.index == t.index
        assert overridden.player_id == t.player_id

    def test_original_target_is_not_mutated(self):
        t = _dummy_prepared_target()
        rp._apply_model_override([t], "H4")
        assert t.model_key == "M6"
        assert t.model_id == "deepseek-v4-flash"

    def test_unknown_model_key_raises(self):
        t = _dummy_prepared_target()
        with pytest.raises(ValueError):
            rp._apply_model_override([t], "NOT_A_REAL_KEY")


# ---------------------------------------------------------------------------
# Cycle 5.5: --thinking の事前バリデーション（0 API call）
# ---------------------------------------------------------------------------
class TestThinkingPreflightValidation:
    def test_thinking_medium_below_min_output_tokens_exits(self):
        t = _dummy_prepared_target()
        with pytest.raises(SystemExit):
            rp._preflight(
                [t], samples=1, variants=["patched"], budget_mode="hardened",
                max_output_tokens=2000, max_cost_usd=100, check_keys=False,
                thinking_mode="medium",
            )

    def test_thinking_medium_at_min_output_tokens_ok(self):
        t = _dummy_prepared_target()
        result = rp._preflight(
            [t], samples=1, variants=["patched"], budget_mode="hardened",
            max_output_tokens=rp.THINKING_MIN_OUTPUT_TOKENS, max_cost_usd=100,
            check_keys=False, thinking_mode="medium",
        )
        assert result["target_count"] == 1

    def test_thinking_off_ignores_threshold(self):
        t = _dummy_prepared_target()
        result = rp._preflight(
            [t], samples=1, variants=["patched"], budget_mode="hardened",
            max_output_tokens=100, max_cost_usd=100,
            check_keys=False, thinking_mode="off",
        )
        assert result["target_count"] == 1


# ---------------------------------------------------------------------------
# Cycle 5.5: CLI検証（--model-override は --dry-run と併用不可）
# ---------------------------------------------------------------------------
class TestCLIModelOverrideValidation:
    def test_model_override_with_implicit_dry_run_errors(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["replay_probe.py", "--model-override", "M1"])
        with pytest.raises(SystemExit) as exc:
            rp.main()
        assert exc.value.code not in (0, None)

    def test_model_override_with_explicit_dry_run_errors(self, monkeypatch):
        monkeypatch.setattr(
            sys, "argv", ["replay_probe.py", "--dry-run", "--model-override", "M1"],
        )
        with pytest.raises(SystemExit) as exc:
            rp.main()
        assert exc.value.code not in (0, None)


# ---------------------------------------------------------------------------
# Cycle 5.5: summary.json の by_model セクション（合成レコードのみ・API不要）
# ---------------------------------------------------------------------------
class TestByModelSummary:
    def _budget(self) -> rp.GameCostBudget:
        return rp.GameCostBudget(
            per_player_cap_usd=100, game_cap_usd=100,
            event_logger=rp._NullEventLogger(), abort_on_block=False,
        )

    def test_by_model_present_and_correct(self, tmp_path):
        records = [
            {
                "variant": "patched", "model_key": "H4", "model_id": "grok-4.6",
                "is_contract_action": True, "parse_ok": True, "json_extracted": True,
                "truncated": False, "role": "proposer", "action_type": "contract_propose",
                "finish_reason": "stop", "reasoning_tokens": 500, "cost_usd": 0.01,
            },
            {
                "variant": "patched", "model_key": "H4", "model_id": "grok-4.6",
                "is_contract_action": False, "parse_ok": True, "json_extracted": True,
                "truncated": True, "role": "replier", "action_type": "pass",
                "finish_reason": "length", "reasoning_tokens": 700, "cost_usd": 0.02,
            },
        ]
        rp._write_summary_and_csv(tmp_path, records, self._budget(), None, ["patched"])
        summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
        bm = summary["by_model"]["H4"]
        assert bm["n"] == 2
        assert bm["conversion_rate"]["num"] == 1
        assert bm["truncated_count"] == 1
        assert bm["reasoning_tokens_mean"] == pytest.approx(600.0)
        assert bm["reasoning_tokens_max"] == 700
        assert bm["role_distribution"] == {"proposer": 1, "replier": 1}
        assert bm["finish_reason_distribution"] == {"length": 1, "stop": 1}

    def test_legacy_summary_omits_by_model(self, tmp_path):
        records = [{
            "variant": "patched", "model_key": "H4", "model_id": "grok-4.6",
            "is_contract_action": False, "parse_ok": True, "json_extracted": True,
            "truncated": False, "cost_usd": 0.0,
        }]
        rp._write_summary_and_csv(
            tmp_path, records, self._budget(), None, ["patched"], legacy_summary=True,
        )
        summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
        assert "by_model" not in summary

    def test_no_model_key_omits_by_model(self, tmp_path):
        records = [{
            "variant": "patched", "is_contract_action": False, "parse_ok": True,
            "json_extracted": True, "truncated": False, "cost_usd": 0.0,
        }]
        rp._write_summary_and_csv(tmp_path, records, self._budget(), None, ["patched"])
        summary = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))
        assert "by_model" not in summary
