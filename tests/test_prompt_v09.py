"""
v0.9 サイクル9.2: Free Cash廃止の反映＋本戦分析5点（G1〜G5）の受け入れテスト

サイクル9.1でエンジン側の支払可能額ゲートが刷新され（GameConfig.free_cash_mode
∈ {"debt","cash","entry_fee"}、engine/player.py::spendable_cash()）、
baseline_v1_s2はfree_cash_mode="entry_fee"に切り替わった。本サイクルは
llm/prompt_builder.pyをこれに追随させ、あわせてv0.8本戦分析
（doc/analysis/trial_v08_l12_r12_20260830_analysis.md §10）の指摘5点
（G1〜G5）に対応する。engine/は一切変更しない。

対象:
  - G0: entry_fee/cashモードでFree Cash文言が一切出ないこと（debtモードは維持）
  - G0: 「あなたの状態」欄の支払可能額表示・transfer/bounty_postのgating
  - G1: 型A金銭義務の資金不足を執行前に警告（negotiation/commit）
  - G2: card_trade_proposeの雛形（本戦分析§10-6対応）
  - G3: 提案者本人へのcontract_sign非表示（回帰ガード）
  - G4: reflection末尾の出力指示強化
  - G5: 借入選択プロンプトの参考表（賞金ゼロなら何R目に破産するか）
"""

from engine.config import GameConfig
from engine.player import apply_interest, compute_mandatory_repayment

from llm.prompt_builder import (
    build_system_prompt,
    build_negotiation_prompt,
    build_commit_prompt,
    build_reflection_prompt,
    build_double_up_prompt,
    build_loan_prompt,
    _compute_finance_forecast,
)

from tests.conftest import make_player, make_market
from tests.test_cycle5_prompt_salience import (
    _make_player,
    _base_visible_state,
    _extract_available_unavailable,
)


ENTRY_FEE_CONFIG = GameConfig.baseline_v1_s2(12)
DEBT_CONFIG = GameConfig.baseline_v1()


def _commit_state() -> dict:
    return {"used_cards": {}}


# =============================================================================
# 1. Free Cash文言の全廃（entry_feeモード）／維持（debtモード）
# =============================================================================

class TestNoFreeCashInEntryFeeMode:
    def test_no_free_cash_anywhere_in_entry_fee_mode(self):
        config = ENTRY_FEE_CONFIG
        player = _make_player(cash=3_000_000, debt=1_000_000)
        vs = _base_visible_state()

        prompts = {
            "system": build_system_prompt("P01", config),
            "negotiation": build_negotiation_prompt(player, 1, 1, vs, config),
            "commit": build_commit_prompt(
                player, [make_market("M01", 500_000)], 1, _commit_state(), config,
            ),
            "reflection": build_reflection_prompt(player, 1, vs, config),
            "double_up": build_double_up_prompt(player, 200_000, 1, vs, config),
            "loan": build_loan_prompt(config),
        }
        for name, prompt in prompts.items():
            assert "Free Cash" not in prompt, f"{name} にFree Cashが残存: {prompt}"
            assert "free_cash" not in prompt, f"{name} にfree_cashが残存: {prompt}"


class TestDebtModeLegacyWording:
    def test_debt_mode_keeps_legacy_wording(self):
        config = DEBT_CONFIG
        prompt = build_system_prompt("P01", config)
        assert "Free Cash" in prompt
        assert "## Free Cash" in prompt


# =============================================================================
# 2. 「あなたの状態」の支払可能額表示・gating
# =============================================================================

class TestSpendableCashLineAndGating:
    def test_spendable_cash_line_present(self):
        config = ENTRY_FEE_CONFIG
        player = _make_player(cash=4_000_000, debt=4_000_000)
        vs = _base_visible_state()
        prompt = build_negotiation_prompt(player, 1, 1, vs, config)
        # entry_fee=10万円: 支払可能額 = 400万 - 10万 = 390万
        assert "支払可能額（現金 − 今RのEntry Fee）: 390万円" in prompt

    def test_gating_zero_marks_unavailable(self):
        config = ENTRY_FEE_CONFIG
        zero_player = _make_player(cash=100_000, debt=100_000)  # spendable=0
        vs = _base_visible_state()
        prompt = build_negotiation_prompt(zero_player, 1, 1, vs, config)
        _, unavailable = _extract_available_unavailable(prompt)
        assert "transfer・bounty_post（支払可能額 0）" in unavailable

    def test_gating_positive_marks_available(self):
        config = ENTRY_FEE_CONFIG
        player = _make_player(cash=500_000, debt=0)
        vs = _base_visible_state()
        prompt = build_negotiation_prompt(player, 1, 1, vs, config)
        available, _ = _extract_available_unavailable(prompt)
        assert "transfer" in available
        assert "bounty_post" in available


class TestReflectionAndDoubleUpNoStateFreeCashLine:
    def test_reflection_and_double_up_have_no_state_free_cash_line(self):
        config = ENTRY_FEE_CONFIG
        player = _make_player(cash=1_000_000, debt=500_000)
        vs = _base_visible_state()
        reflection = build_reflection_prompt(player, 1, vs, config)
        double_up = build_double_up_prompt(player, 100_000, 1, vs, config)
        assert "Free Cash" not in reflection
        assert "Free Cash" not in double_up


# =============================================================================
# 3. G5: 借入選択プロンプトの参考表
# =============================================================================

class TestLoanPromptReferenceTable:
    def test_loan_prompt_reference_table(self):
        config = ENTRY_FEE_CONFIG
        prompt = build_loan_prompt(config)
        assert "## 賞金を1度も取れなかった場合の目安" in prompt

        candidates = sorted({
            config.loan_min, 3_000_000, 5_000_000, 7_000_000, config.loan_max,
        })
        assert len(candidates) == 5
        for loan in candidates:
            player = make_player("X", cash=loan, debt=loan)
            after_interest = apply_interest(player, config.interest_rate)
            r1_repay = compute_mandatory_repayment(
                after_interest.debt_balance, config.num_rounds, config.mandatory_repay_k,
            )
            assert f"{loan // 10_000}万円 / {r1_repay:,}円" in prompt

        # 破産ラウンドには事由が併記される
        assert "Entry Fee不足" in prompt or "強制返済不能" in prompt

    def test_loan_prompt_no_table_when_repay_disabled(self):
        config = DEBT_CONFIG
        assert config.mandatory_repay_enabled is False
        prompt = build_loan_prompt(config)
        assert "賞金を1度も取れなかった場合の目安" not in prompt


# =============================================================================
# 4. G1: 型A金銭義務の資金不足警告
# =============================================================================

class TestG1TypeAShortfallWarning:
    def test_g1_warns_when_short_today_due(self):
        config = ENTRY_FEE_CONFIG
        player = _make_player(cash=500_000, debt=0)
        vs = _base_visible_state(my_obligations=[
            {"contract_id": "C1", "obligor": "P01", "counterparty": "P02",
             "ob_type": "type_a_payment", "round_num": 3,
             "details": {"amount": 450_000}},
        ])
        prompt = build_negotiation_prompt(player, 3, 1, vs, config)
        # available = 500,000 - entry_fee(100,000) = 400,000 < 450,000
        assert "⚠ 現在の現金500000円ではR3の型A 450000円を払えません（不足50000円）" in prompt

    def test_g1_warns_when_short_next_round_due(self):
        """次R期限: Entry Fee×2だけなら足りるが、今Rの強制最低返済見込みを
        引くと不足するケース（承認時ノートで指定された補強シナリオ）"""
        config = ENTRY_FEE_CONFIG
        player = _make_player(cash=700_000, debt=2_000_000)
        vs = _base_visible_state(my_obligations=[
            {"contract_id": "C2", "obligor": "P01", "counterparty": "P02",
             "ob_type": "type_a_payment", "round_num": 6,
             "details": {"amount": 300_000}},
        ])
        # Entry Fee×2だけなら 700,000 - 200,000 = 500,000 >= 300,000 で足りる
        assert player.cash - config.entry_fee * 2 >= 300_000
        forecast = _compute_finance_forecast(
            player.debt_balance, player.cash, 5, config,
            entry_fee_deduction=config.entry_fee,
        )
        available = player.cash - config.entry_fee * 2 - forecast["mandatory_repay"]
        assert available < 300_000
        shortfall = 300_000 - available

        prompt = build_negotiation_prompt(player, 5, 1, vs, config)
        assert (
            f"⚠ 現在の現金700000円ではR6の型A 300000円を払えません"
            f"（不足{shortfall}円）" in prompt
        )

    def test_g1_silent_when_sufficient(self):
        config = ENTRY_FEE_CONFIG
        player = _make_player(cash=1_000_000, debt=0)
        vs = _base_visible_state(my_obligations=[
            {"contract_id": "C3", "obligor": "P01", "counterparty": "P02",
             "ob_type": "type_a_payment", "round_num": 3,
             "details": {"amount": 200_000}},
        ])
        prompt = build_negotiation_prompt(player, 3, 1, vs, config)
        assert "⚠" not in prompt

    def test_g1_aggregates_same_round(self):
        config = ENTRY_FEE_CONFIG
        player = _make_player(cash=500_000, debt=0)
        vs = _base_visible_state(my_obligations=[
            {"contract_id": "C4", "obligor": "P01", "counterparty": "P02",
             "ob_type": "type_a_payment", "round_num": 3,
             "details": {"amount": 300_000}},
            {"contract_id": "C5", "obligor": "P01", "counterparty": "P03",
             "ob_type": "type_a_payment", "round_num": 3,
             "details": {"amount": 300_000}},
        ])
        prompt = build_negotiation_prompt(player, 3, 1, vs, config)
        # 合算600,000、原資400,000、不足200,000で1件のみの警告
        assert prompt.count("⚠") == 1
        assert "R3の型A 600000円を払えません（不足200000円）" in prompt

    def test_g1_silent_in_debt_mode(self):
        config = DEBT_CONFIG
        player = _make_player(cash=100_000, debt=0)
        vs = _base_visible_state(my_obligations=[
            {"contract_id": "C6", "obligor": "P01", "counterparty": "P02",
             "ob_type": "type_a_payment", "round_num": 3,
             "details": {"amount": 900_000}},
        ])
        prompt = build_negotiation_prompt(player, 3, 1, vs, config)
        assert "⚠" not in prompt

    def test_g1_present_in_commit_prompt(self):
        config = ENTRY_FEE_CONFIG
        player = _make_player(cash=500_000, debt=0)
        state = {
            "used_cards": {},
            "my_obligations": [
                {"contract_id": "C7", "obligor": "P01", "counterparty": "P02",
                 "ob_type": "type_a_payment", "round_num": 3,
                 "details": {"amount": 450_000}},
            ],
        }
        prompt = build_commit_prompt(
            player, [make_market("M01", 500_000)], 3, state, config,
        )
        assert "⚠ 現在の現金500000円ではR3の型A 450000円を払えません（不足50000円）" in prompt


# =============================================================================
# 5. G2: card_trade_propose の雛形
# =============================================================================

class TestG2CardTradeTemplate:
    def test_card_trade_template_content(self):
        config = ENTRY_FEE_CONFIG
        # フルデッキ = 全10ランクが手札にある → give_card=最低、receive_card=最高
        player = make_player("P01", cash=1_000_000, debt=0)
        vs = _base_visible_state(alive_players=["P01", "P02", "P03"])
        prompt = build_negotiation_prompt(player, 1, 1, vs, config)

        assert "## card_trade_propose の雛形" in prompt
        assert '"with_players": ["P02"]' in prompt
        assert '"give_card": "HIGH_CARD"' in prompt
        assert '"receive_card": "ROYAL_FLUSH"' in prompt
        # spendable = 1,000,000 - entry_fee(100,000) = 900,000 → min(300000, 900000)
        assert '"cash_amount": 300000' in prompt
        assert "支払可能額 900000円以内" in prompt

    def test_card_trade_template_absent_when_unavailable(self):
        config = ENTRY_FEE_CONFIG
        player = make_player("P01", cash=1_000_000, debt=0)
        vs = _base_visible_state(alive_players=["P01", "P02", "P03"])
        # R12はcard_trade_propose不可（config.card_trade_last_round=11）
        prompt = build_negotiation_prompt(player, 12, 1, vs, config)
        assert "## card_trade_propose の雛形" not in prompt


# =============================================================================
# 6. G3: 提案者本人へのcontract_sign非表示（回帰ガード）
# =============================================================================

def _pending_contract(contract_id: str, proposer: str) -> dict:
    return {
        "contract_id": contract_id, "proposer": proposer, "round_created": 1,
        "parties": ["P01", "P02"], "signed_by": [proposer],
        "obligations": [
            {"obligor": proposer, "counterparty": "P01" if proposer == "P02" else "P02",
             "ob_type": "type_a_payment", "round_num": 2, "details": {"amount": 10_000}},
        ],
    }


class TestG3ContractSignHiddenForOwnProposal:
    def test_contract_sign_hidden_for_own_proposal_only(self):
        config = ENTRY_FEE_CONFIG
        player = _make_player(cash=500_000, debt=0)
        vs = _base_visible_state(contracts_pending=[
            _pending_contract("C_own", "P01"),
        ])
        prompt = build_negotiation_prompt(player, 1, 1, vs, config)
        _, unavailable = _extract_available_unavailable(prompt)
        assert "contract_sign（署名できる契約がない）" in unavailable

    def test_contract_sign_available_with_others_proposal(self):
        config = ENTRY_FEE_CONFIG
        player = _make_player(cash=500_000, debt=0)
        vs = _base_visible_state(contracts_pending=[
            _pending_contract("C_own", "P01"),
            _pending_contract("C_other", "P02"),
        ])
        prompt = build_negotiation_prompt(player, 1, 1, vs, config)
        available, _ = _extract_available_unavailable(prompt)
        assert "contract_sign" in available


# =============================================================================
# 7. G4: reflection末尾の出力指示強化
# =============================================================================

class TestG4ReflectionOutputInstruction:
    def test_reflection_output_instruction(self):
        config = ENTRY_FEE_CONFIG
        player = _make_player(cash=500_000, debt=0)
        vs = _base_visible_state()
        prompt = build_reflection_prompt(player, 1, vs, config)
        assert (
            "このフェイズでは action や strategy を出力しないでください。"
            '出力は {"memory": "…"} だけです' in prompt
        )


# =============================================================================
# 8. system prompt文字数の回帰ガード
# =============================================================================

class TestSystemPromptWithinBudget:
    def test_system_prompt_within_budget(self):
        prompt = build_system_prompt("P01", GameConfig.baseline_v1_s2(12))
        assert len(prompt) <= 8_300
