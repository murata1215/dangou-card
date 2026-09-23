"""
v0.10 型C（条件付き金銭契約）のプロンプトテスト

- config.type_c_enabled で型C文面が出し分けられること
- 無効時は既存プロンプトとバイト単位で完全一致すること
- 新規文面（TYPE_C_CONTRACT_RULES/TYPE_C_ACTION_TEMPLATE/通知3種）に
  禁止語（保険/賭け/成功報酬/キックバック/予測/利益分配/ヘッジ）が
  含まれないこと
- 既存の「イベント型（保険型）」の「（保険型）」が無条件に消えていること
- 型Cは破産余裕額（G1警告）の計算対象に含まれないこと（回帰）
- _format_type_c_condition() が3種の条件を正しく要約すること
"""

from engine.config import GameConfig
from engine.models import PlayerState
from llm.prompt_builder import (
    build_system_prompt, build_negotiation_prompt,
    TYPE_C_CONTRACT_RULES, TYPE_C_ACTION_TEMPLATE,
    _format_type_c_condition, _format_obligation_detail,
    _render_type_a_shortfall_warning, _render_contract_notice_block,
)
from tests.test_cycle5_prompt_salience import _make_player, _base_visible_state


FORBIDDEN_WORDS = ["保険", "賭け", "成功報酬", "キックバック", "予測", "利益分配", "ヘッジ"]


def _s1_config() -> GameConfig:
    return GameConfig(type_c_enabled=False)


def _s2_config() -> GameConfig:
    return GameConfig(type_c_enabled=True)


class TestSystemPromptGating:
    """config.type_c_enabled による出し分け"""

    def test_disabled_prompt_has_no_type_c_text(self):
        prompt = build_system_prompt("P01", _s1_config())
        assert "type_c_conditional" not in prompt
        assert "型C" not in prompt

    def test_enabled_prompt_has_type_c_text(self):
        prompt = build_system_prompt("P01", _s2_config())
        assert "type_c_conditional" in prompt
        assert "型C" in prompt

    def test_disabled_prompt_byte_identical_to_no_type_c_baseline(self):
        """無効時は既存挙動（型C導入前）と完全に同一バイト列になる。
        比較対象は「型Cブロックの挿入行」から{type_c_*_block}を除いたものが
        空文字列に展開されるため、TYPE_C_CONTRACT_RULES/TYPE_C_ACTION_TEMPLATE
        の文字列そのものが一切出現しないことで検証する"""
        prompt = build_system_prompt("P01", _s1_config())
        assert TYPE_C_CONTRACT_RULES not in prompt
        assert TYPE_C_ACTION_TEMPLATE not in prompt

    def test_default_gameconfig_is_type_c_disabled(self):
        """S1既定（GameConfig()）はtype_c_enabled=False"""
        assert GameConfig().type_c_enabled is False

    def test_baseline_v1_s2_has_type_c_enabled(self):
        assert GameConfig.baseline_v1_s2(8).type_c_enabled is True

    def test_default_8_s2_has_type_c_enabled(self):
        assert GameConfig.default_8_s2().type_c_enabled is True


class TestInsuranceLabelRemovedUnconditionally(object):
    """既存の「イベント型（保険型）」から「（保険型）」が無条件に消えている"""

    def test_removed_when_type_c_disabled(self):
        prompt = build_system_prompt("P01", _s1_config())
        assert "（保険型）" not in prompt
        assert "イベント型:" in prompt  # 例文自体は残る

    def test_removed_when_type_c_enabled(self):
        prompt = build_system_prompt("P01", _s2_config())
        assert "（保険型）" not in prompt
        assert "イベント型:" in prompt


class TestForbiddenVocabularyScan:
    """新規追加ブロックに禁止語が含まれないこと（対称性/中立性の機械保証）"""

    def test_type_c_contract_rules_has_no_forbidden_words(self):
        for word in FORBIDDEN_WORDS:
            assert word not in TYPE_C_CONTRACT_RULES, f"禁止語 '{word}' が含まれています"

    def test_type_c_action_template_has_no_forbidden_words(self):
        for word in FORBIDDEN_WORDS:
            assert word not in TYPE_C_ACTION_TEMPLATE, f"禁止語 '{word}' が含まれています"

    def test_type_c_no_usage_example_playstyle_advice(self):
        """使用例（具体的なプレイ指南）を書かない — 助言語彙が入っていないことで代替確認"""
        advice_words = ["すべき", "推奨します", "おすすめ", "した方が"]
        for word in advice_words:
            assert word not in TYPE_C_CONTRACT_RULES
            assert word not in TYPE_C_ACTION_TEMPLATE

    def test_notice_blocks_have_no_forbidden_words(self):
        visible_state = _base_visible_state(my_contract_notices=[
            {"contract_id": "C1", "kind": "type_c_fired", "obligation_id": "C1_OB01",
             "condition_type": "market_winner", "amount": 300_000},
            {"contract_id": "C2", "kind": "type_c_not_met", "obligation_id": "C2_OB01",
             "condition_type": "eliminated"},
            {"contract_id": "C3", "kind": "type_c_expired", "obligation_id": "C3_OB01"},
        ])
        lines = _render_contract_notice_block(visible_state, round_num=5, config=_s2_config())
        text = "\n".join(lines)
        assert text  # 3種とも描画されていること
        for word in FORBIDDEN_WORDS:
            assert word not in text


class TestFormatTypeCCondition:
    """_format_type_c_condition() の要旨組み立て"""

    def test_market_winner(self):
        d = {"condition_type": "market_winner",
             "condition": {"market_id": "M02", "target_player": "P05"}}
        result = _format_type_c_condition(d)
        assert "M02" in result and "P05" in result

    def test_eliminated(self):
        d = {"condition_type": "eliminated", "condition": {"target_player": "P09"}}
        result = _format_type_c_condition(d)
        assert "P09" in result and "脱落" in result

    def test_market_surge(self):
        d = {"condition_type": "market_surge", "condition": {"market_id": "M01"}}
        result = _format_type_c_condition(d)
        assert "M01" in result and "高騰" in result

    def test_unknown_condition_type_falls_back_to_raw(self):
        d = {"condition_type": "something_else", "condition": {}}
        result = _format_type_c_condition(d)
        assert result == "something_else"


class TestFormatObligationDetailTypeC:
    """_format_obligation_detail() の型C分岐（amountキー分岐より前段で処理）"""

    def test_type_c_shows_amount_and_condition(self):
        ob = {
            "ob_type": "type_c_conditional",
            "details": {
                "amount": 1_000_000, "condition_type": "market_winner",
                "condition": {"market_id": "M02", "target_player": "P05"},
            },
        }
        label, detail = _format_obligation_detail(ob)
        assert label == "型C条件付き金銭"
        assert "100万円" in detail
        assert "M02" in detail and "P05" in detail

    def test_type_a_unaffected_by_type_c_branch(self):
        """型Aの既存分岐は型C分岐追加の影響を受けない（回帰）"""
        ob = {"ob_type": "type_a_payment", "details": {"amount": 500_000}}
        label, detail = _format_obligation_detail(ob)
        assert label == "型A金銭支払い"
        assert detail == "50万円"


class TestNegotiationPromptTypeCContent:
    """交渉プロンプトへの型C情報の反映"""

    def test_my_obligation_type_c_rendered_in_negotiation_prompt(self):
        config = GameConfig.baseline_v1_s2(2)
        player = _make_player("P01", cash=1_000_000)
        vs = _base_visible_state(my_obligations=[
            {"contract_id": "C1", "obligor": "P01", "counterparty": "P02",
             "ob_type": "type_c_conditional", "round_num": 6,
             "details": {"amount": 500_000, "condition_type": "market_surge",
                         "condition": {"market_id": "M01"}}},
        ])
        prompt = build_negotiation_prompt(player, 5, 1, vs, config)
        assert "型C条件付き金銭" in prompt
        assert "50万円" in prompt


class TestTypeCExcludedFromShortfallWarning:
    """型Cは破産余裕額（G1警告）の計算に含まれない（回帰。既存フィルタの確認のみ）"""

    def test_type_c_obligation_not_counted_in_shortfall(self):
        config = GameConfig(free_cash_mode="entry_fee", type_c_enabled=True)
        player = PlayerState(
            player_id="P01", cash=100_000, debt_balance=0, initial_loan=0, hand=[],
        )
        # 型A義務ゼロ・型Cのみ100万円 → 警告は出ない（型Cは対象外のため）
        vs = _base_visible_state(my_obligations=[
            {"obligor": "P01", "counterparty": "P02", "ob_type": "type_c_conditional",
             "round_num": 3,
             "details": {"amount": 1_000_000, "condition_type": "market_surge",
                         "condition": {"market_id": "M01"}}},
        ])
        lines = _render_type_a_shortfall_warning(player, vs, round_num=3, config=config)
        assert lines == []

    def test_type_a_obligation_still_counted(self):
        """比較対照: 同額の型Aなら警告が出る（フィルタが機能していることの確認）"""
        config = GameConfig(free_cash_mode="entry_fee", type_c_enabled=True)
        player = PlayerState(
            player_id="P01", cash=100_000, debt_balance=0, initial_loan=0, hand=[],
        )
        vs = _base_visible_state(my_obligations=[
            {"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment",
             "round_num": 3, "details": {"amount": 1_000_000}},
        ])
        lines = _render_type_a_shortfall_warning(player, vs, round_num=3, config=config)
        assert lines != []
