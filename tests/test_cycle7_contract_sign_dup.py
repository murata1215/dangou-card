"""
Cycle 7: contract_sign 二重署名防止のプロンプト表示テスト

本走 trial_C_l12_r12_20260827 で ACTION_ERROR 12件（すべて contract_sign の
`ValueError: Pxx already signed contract C_xxxxxxxx`）が発生した。12件すべてが
「自分が提案した契約に、自分がもう一度署名しようとした」ケースであり、
engine/contracts.py の signed_by=[proposer]（提案者は自動署名）という事実が
プロンプト上に一切表示されていなかったことが原因だった。

本テストは実ログの契約 C_d680cdd2（提案者 P09 / 当事者 P09,P10 /
signed_by=["P09"]）を再現し、以下を検証する:
- 提案者視点では「署名済み」であることが明示される
- 相手方視点では「未署名」であることが明示される
- pending が自分の署名済みのみのとき contract_sign が選べるアクションに出ない
- 混在時（署名済み1件＋未署名1件）は contract_sign が選べる
- 既存の `未署名: Pxx` 表示は回帰しない
- 長さ予算（負の交渉プロンプト 6400字）を超えない
"""

from engine.models import Contract, ContractStatus, ObligationType
from engine.config import GameConfig
from llm.prompt_builder import build_negotiation_prompt, _available_negotiation_actions
from tests.conftest import make_player, make_obligation


def _make_c_d680cdd2() -> Contract:
    """実ログ再現: 提案者P09、当事者P09/P10、P09のみ署名済み"""
    ob1 = make_obligation(
        "OB01", "C_d680cdd2",
        obligor="P09", counterparty="P10",
        ob_type=ObligationType.TYPE_B_MARKET,
        round_num=6,
        details={"market_id": "M01"},
    )
    ob2 = make_obligation(
        "OB02", "C_d680cdd2",
        obligor="P10", counterparty="P09",
        ob_type=ObligationType.TYPE_B_MARKET,
        round_num=6,
        details={"market_id": "M01"},
    )
    return Contract(
        contract_id="C_d680cdd2",
        proposer="P09",
        parties=["P09", "P10"],
        signed_by=["P09"],
        obligations=[ob1, ob2],
        round_created=6,
        status=ContractStatus.PROPOSED,
    )


def _visible_state_for(for_player_id: str, contracts: list[Contract]) -> dict:
    return {
        "round_num": 7,
        "markets": [{"market_id": "M01", "prize_pool": 500000}],
        "alive_players": ["P09", "P10"],
        "contracts_public": [],
        "messages": [],
        "contracts_pending": [
            {
                "contract_id": c.contract_id,
                "proposer": c.proposer,
                "parties": c.parties,
                "signed_by": list(c.signed_by),
                "round_created": c.round_created,
                "obligations": [
                    {
                        "obligor": ob.obligor,
                        "counterparty": ob.counterparty,
                        "ob_type": ob.ob_type.value,
                        "round_num": ob.round_num,
                        "details": dict(ob.details),
                    }
                    for ob in c.obligations
                ],
            }
            for c in contracts
            if c.status == ContractStatus.PROPOSED and for_player_id in c.parties
        ],
    }


class TestSignatureStatusDisplay:
    """契約ごとの自分の署名状態表示（変更2・変更3）"""

    def test_proposer_sees_signed_status(self):
        """提案者(P09)視点: 署名済みと明示され、『署名した場合』というプレビュー文言は出ない"""
        contract = _make_c_d680cdd2()
        state = _visible_state_for("P09", [contract])
        player = make_player("P09", cash=3_000_000, debt=0)
        config = GameConfig.baseline_v1_s2(12)

        prompt = build_negotiation_prompt(player, 7, 1, state, config)

        assert "あなたの署名: 済（提案者は自動署名。この契約にこれ以上署名は不要）" in prompt
        assert "→ この契約が成立した場合、あなたの義務（既存分と合流後）は次のようになります:" in prompt
        assert "→ 署名した場合、あなたの義務" not in prompt

    def test_counterparty_sees_unsigned_status(self):
        """相手方(P10)視点: 未署名と明示され、従来通り『署名した場合』のプレビューが出る"""
        contract = _make_c_d680cdd2()
        state = _visible_state_for("P10", [contract])
        player = make_player("P10", cash=3_000_000, debt=0)
        config = GameConfig.baseline_v1_s2(12)

        prompt = build_negotiation_prompt(player, 7, 1, state, config)

        assert "あなたの署名: 未（あなたが署名すれば契約は成立する）" in prompt
        assert "→ 署名した場合、あなたの義務（既存分と合流後）は次のようになります:" in prompt

    def test_unsigned_field_unchanged(self):
        """既存の『未署名: Pxx』表示は回帰しない"""
        contract = _make_c_d680cdd2()
        state = _visible_state_for("P09", [contract])
        player = make_player("P09", cash=3_000_000, debt=0)
        config = GameConfig.baseline_v1_s2(12)

        prompt = build_negotiation_prompt(player, 7, 1, state, config)

        assert "未署名: P10" in prompt


class TestAutoSignFactStatement:
    """自動署名の事実文の出現条件（変更1）"""

    def test_fact_statement_present_when_pending_exists(self):
        contract = _make_c_d680cdd2()
        state = _visible_state_for("P09", [contract])
        player = make_player("P09", cash=3_000_000, debt=0)
        config = GameConfig.baseline_v1_s2(12)

        prompt = build_negotiation_prompt(player, 7, 1, state, config)

        assert "契約は提案者が自動で署名済みです。" in prompt
        assert "あなたが未署名の契約に署名するには" in prompt

    def test_fact_statement_absent_when_no_pending(self):
        state = _visible_state_for("P09", [])
        player = make_player("P09", cash=3_000_000, debt=0)
        config = GameConfig.baseline_v1_s2(12)

        prompt = build_negotiation_prompt(player, 7, 1, state, config)

        assert "契約は提案者が自動で署名済みです。" not in prompt
        assert "提案中の正式契約" not in prompt


class TestAvailableActionsGating:
    """『いま選べるアクション』の contract_sign 判定（変更4・回帰の本丸）"""

    def test_contract_sign_excluded_when_all_pending_self_signed(self):
        """pendingが自分の署名済みのみ → contract_signは選べない側に出る"""
        contract = _make_c_d680cdd2()
        state = _visible_state_for("P09", [contract])
        player = make_player("P09", cash=3_000_000, debt=0)
        config = GameConfig.baseline_v1_s2(12)

        available, unavailable = _available_negotiation_actions(player, 7, state, config)

        assert not any(a.startswith("contract_sign") for a in available)
        assert "contract_sign（署名できる契約がない）" in unavailable

    def test_contract_sign_included_for_counterparty(self):
        """pendingが自分の未署名のみ → contract_signは選べる側に出る"""
        contract = _make_c_d680cdd2()
        state = _visible_state_for("P10", [contract])
        player = make_player("P10", cash=3_000_000, debt=0)
        config = GameConfig.baseline_v1_s2(12)

        available, unavailable = _available_negotiation_actions(player, 7, state, config)

        assert any(a.startswith("contract_sign") for a in available)
        assert "contract_sign（署名できる契約がない）" not in unavailable

    def test_contract_sign_included_when_mixed(self):
        """署名済み契約1件＋未署名契約1件が混在 → contract_signは選べる側に出る"""
        signed_contract = _make_c_d680cdd2()
        ob = make_obligation(
            "OB03", "C_other",
            obligor="P10", counterparty="P09",
            ob_type=ObligationType.TYPE_A_PAYMENT,
            round_num=8,
            details={"amount": 100_000},
        )
        unsigned_for_me_contract = Contract(
            contract_id="C_other",
            proposer="P10",
            parties=["P09", "P10"],
            signed_by=["P10"],
            obligations=[ob],
            round_created=7,
            status=ContractStatus.PROPOSED,
        )
        state = _visible_state_for("P09", [signed_contract, unsigned_for_me_contract])
        player = make_player("P09", cash=3_000_000, debt=0)
        config = GameConfig.baseline_v1_s2(12)

        available, unavailable = _available_negotiation_actions(player, 7, state, config)

        assert any(a.startswith("contract_sign") for a in available)
        assert "contract_sign（署名できる契約がない）" not in unavailable


class TestPromptLengthBudgetRegression:
    """既存の長さ予算（tests/test_cycle5_prompt_salience.py）を壊さないことの確認"""

    def test_self_signed_pending_within_negotiation_budget(self):
        contract = _make_c_d680cdd2()
        state = _visible_state_for("P09", [contract])
        player = make_player("P09", cash=3_000_000, debt=0)
        config = GameConfig.baseline_v1_s2(12)

        prompt = build_negotiation_prompt(player, 7, 1, state, config)

        assert len(prompt) <= 6400, len(prompt)
