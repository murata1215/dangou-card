"""
v0.10 サイクル10.3: 目的文の強化のテスト

- system prompt の先頭（RULES_SUMMARY冒頭）に `## 目的` 節を新設し、
  「生存は最低条件、総合1位（最終資産）が最も高く評価される」の3行を
  フラグに依存せず（S1 rulesetを含め）常時表示する
- 圧縮後も既存の生還条件・敗北条件の文言は意味を保って残る
- system prompt の文字数上限（9,100字 / type_c OFF時8,300字）を、
  100字以上のマージンを残して満たす
- 交渉・コミット・倍掛け選択・振り返りの全4フェーズで、目的のリマインド行が
  毎ラウンド再掲される（フラグ非依存）

注意（本サイクルの独自判断）: 本ファイルは build_system_prompt() の出力に対して
FORBIDDEN_WORDS のような示唆語スキャンを一切行わない。新2行目に含まれる
「脱落を避けながら」の「避け」がテスト対象を交渉プロンプトの示唆語スキャン
（tests/test_leader_announce.py）まで広げると誤って引っかかるため。
"""
from engine.config import GameConfig
from llm.prompt_builder import (
    OBJECTIVE_LINES,
    OBJECTIVE_REMINDER_LINE,
    build_commit_prompt,
    build_double_up_prompt,
    build_negotiation_prompt,
    build_reflection_prompt,
    build_system_prompt,
)
from tests.conftest import make_market
from tests.test_cycle5_prompt_salience import _base_visible_state, _make_player


class TestSystemPromptObjectiveLines:
    def _configs(self):
        return {
            "GameConfig()": GameConfig(),
            "default_8()": GameConfig.default_8(),
            "baseline_v1(12)": GameConfig.baseline_v1(12),
            "baseline_v1_s2(12)": GameConfig.baseline_v1_s2(12),
            "default_8_s2()": GameConfig.default_8_s2(),
        }

    def test_objective_lines_present_in_all_presets(self):
        for name, config in self._configs().items():
            prompt = build_system_prompt("P01", config)
            assert OBJECTIVE_LINES in prompt, f"{name} に目的3行が無い"

    def test_objective_section_at_very_top(self):
        prompt = build_system_prompt("P01", GameConfig())
        assert prompt.startswith("# 談合カード ルール\n\n## 目的\n" + OBJECTIVE_LINES)

    def test_compressed_bullets_retain_meaning(self):
        prompt = build_system_prompt("P01", GameConfig())
        assert "生存者中の最終現金が高いほど上位" in prompt
        assert "条件を失えば明確な敗北" in prompt

    def test_system_prompt_within_budget_with_margin(self):
        prompt = build_system_prompt("P01", GameConfig.baseline_v1_s2(12))
        assert len(prompt) <= 9_100, len(prompt)
        assert len(prompt) <= 9_000, (
            f"実測{len(prompt)}字。9,100字上限との差を100字以上確保する要件を満たさない"
        )

    def test_system_prompt_type_c_off_within_budget_with_margin(self):
        config = GameConfig.baseline_v1_s2(12).model_copy(update={"type_c_enabled": False})
        prompt = build_system_prompt("P01", config)
        assert len(prompt) <= 8_300, len(prompt)
        assert len(prompt) <= 8_200, (
            f"実測{len(prompt)}字。8,300字上限との差を100字以上確保する要件を満たさない"
        )


class TestNegotiationObjectiveReminder:
    def test_reminder_present_every_round(self):
        player = _make_player("P01")
        state = _base_visible_state()
        for round_num in range(1, 13):
            prompt = build_negotiation_prompt(player, round_num, 1, state, GameConfig())
            assert OBJECTIVE_REMINDER_LINE in prompt

    def test_reminder_present_s1_and_s2(self):
        player = _make_player("P01")
        state = _base_visible_state()
        for config in (GameConfig(), GameConfig.baseline_v1_s2(12)):
            prompt = build_negotiation_prompt(player, 1, 1, state, config)
            assert OBJECTIVE_REMINDER_LINE in prompt

    def test_reminder_appears_exactly_once(self):
        player = _make_player("P01")
        state = _base_visible_state()
        prompt = build_negotiation_prompt(player, 1, 1, state, GameConfig())
        assert prompt.count(OBJECTIVE_REMINDER_LINE) == 1


class TestFourPhaseObjectiveReminder:
    def test_all_four_phases_have_reminder(self):
        player = _make_player("P01")
        state = _base_visible_state()
        config = GameConfig()
        prompts = {
            "negotiation": build_negotiation_prompt(player, 1, 1, state, config),
            "commit": build_commit_prompt(
                player, [make_market("M01", 500_000)], 1, state, config,
            ),
            "double_up": build_double_up_prompt(player, 200_000, 1, state, config),
            "reflection": build_reflection_prompt(player, 1, state, config),
        }
        for name, prompt in prompts.items():
            assert OBJECTIVE_REMINDER_LINE in prompt, f"{name} にリマインドが無い"
