"""Cycle 5.4: scripts/replay_probe.py の対象選択（既定27件 / --targets-file）の回帰テスト。

API呼び出しは一切行わない（LLMアダプタは`_load_targets`/`_prepare_targets`の
経路には現れない。run_executeのみがllm.adaptersを遅延importするが、本テスト
はrun_executeを呼ばない）。

`logs/`はgitignore対象のため、実trialログに依存するテストは
`pytest.mark.skipif(not rp.TRIAL_DIR.exists())`で退避する。
"""
from __future__ import annotations

import json

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
