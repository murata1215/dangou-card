"""
提案中契約（PROPOSED）のプロンプト表示テスト

contract_investigation_report.md で判明したインフラ欠陥の修正を検証する:
- 提案中の契約が当事者のプロンプトに表示されること
- 第三者には表示されないこと
- active/completed 契約は pending に含まれないこと
- 提案中契約が 0 件のときセクションが出力されないこと
"""

import pytest

from engine.models import (
    Contract, ContractStatus, Obligation, ObligationType, PlayerState,
)
from engine.config import GameConfig
from llm.prompt_builder import build_negotiation_prompt
from tests.conftest import (
    make_player, make_contract, make_obligation, make_market,
)


def _make_proposed_contract() -> Contract:
    """テスト用の PROPOSED 契約を生成する"""
    ob1 = make_obligation(
        "OB01", "C_test01",
        obligor="P01", counterparty="P02",
        ob_type=ObligationType.TYPE_A_PAYMENT,
        round_num=3,
        details={"amount": 210000},
    )
    ob2 = make_obligation(
        "OB02", "C_test01",
        obligor="P02", counterparty="P01",
        ob_type=ObligationType.TYPE_B_MARKET,
        round_num=3,
        details={"market_id": "M02"},
    )
    return Contract(
        contract_id="C_test01",
        proposer="P01",
        parties=["P01", "P02"],
        signed_by=["P01"],  # 提案者のみ署名済み
        obligations=[ob1, ob2],
        round_created=2,
        status=ContractStatus.PROPOSED,
    )


def _make_visible_state_with_pending(for_player_id: str, contracts: list[Contract]) -> dict:
    """テスト用の visible_state を構築する（game.py の _build_visible_state 相当）"""
    state: dict = {
        "round_num": 3,
        "markets": [
            {"market_id": "M01", "prize_pool": 500000},
            {"market_id": "M02", "prize_pool": 300000},
            {"market_id": "M03", "prize_pool": 400000},
        ],
        "alive_players": ["P01", "P02", "P03"],
        "contracts_public": [
            {"contract_id": c.contract_id, "parties": c.parties,
             "status": c.status.value}
            for c in contracts
            if c.status.value in ("active", "completed")
        ],
        "messages": [],
    }
    # 当事者向けの提案中契約（game.py と同じロジック）
    state["contracts_pending"] = [
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
        if c.status == ContractStatus.PROPOSED
        and for_player_id in c.parties
    ]
    return state


class TestPendingContractsVisibility:
    """提案中契約の可視性テスト"""

    def test_party_sees_pending_contract(self):
        """当事者には提案中契約が contracts_pending に含まれる"""
        contract = _make_proposed_contract()
        state = _make_visible_state_with_pending("P02", [contract])

        assert len(state["contracts_pending"]) == 1
        assert state["contracts_pending"][0]["contract_id"] == "C_test01"
        assert state["contracts_pending"][0]["proposer"] == "P01"
        assert state["contracts_pending"][0]["round_created"] == 2

    def test_non_party_does_not_see_pending_contract(self):
        """第三者には提案中契約が contracts_pending に含まれない"""
        contract = _make_proposed_contract()
        state = _make_visible_state_with_pending("P03", [contract])

        assert len(state["contracts_pending"]) == 0

    def test_active_contract_not_in_pending(self):
        """active 契約は contracts_pending に含まれない"""
        active_contract = make_contract(
            "C_active",
            proposer="P01",
            parties=["P01", "P02"],
            obligations=[],
            status=ContractStatus.ACTIVE,
        )
        state = _make_visible_state_with_pending("P01", [active_contract])

        assert len(state["contracts_pending"]) == 0
        # active は contracts_public に含まれる
        assert len(state["contracts_public"]) == 1

    def test_proposer_also_sees_pending(self):
        """提案者自身にも提案中契約が表示される"""
        contract = _make_proposed_contract()
        state = _make_visible_state_with_pending("P01", [contract])

        assert len(state["contracts_pending"]) == 1


class TestNegotiationPromptPendingContracts:
    """交渉プロンプトへの提案中契約セクション表示テスト"""

    def test_prompt_contains_pending_contract_section(self):
        """提案中契約がある場合、プロンプトに契約セクションが含まれる"""
        contract = _make_proposed_contract()
        state = _make_visible_state_with_pending("P02", [contract])
        player = make_player("P02", cash=3000000, debt=2500000)
        config = GameConfig.default_20()

        prompt = build_negotiation_prompt(player, 3, 1, state, config)

        assert "提案中の正式契約（署名待ち）" in prompt
        assert "C_test01" in prompt
        assert "contract_sign" in prompt
        assert "P01" in prompt  # proposer
        assert "型A金銭支払い" in prompt
        assert "21万円" in prompt
        assert "型B市場指定" in prompt
        assert "M02" in prompt
        assert "未署名: P02" in prompt

    def test_prompt_omits_section_when_no_pending(self):
        """提案中契約が 0 件のとき、セクションが出力されない"""
        state = _make_visible_state_with_pending("P01", [])
        player = make_player("P01", cash=3000000, debt=2500000)
        config = GameConfig.default_20()

        prompt = build_negotiation_prompt(player, 3, 1, state, config)

        assert "提案中の正式契約" not in prompt
        assert "署名待ち" not in prompt


# =========================================================================
# v0.8 D5: 署名待ち(PROPOSED)契約の失効
# =========================================================================

def _make_game_for_expiry(num_players: int = 4):
    """E5テスト用の実Gameインスタンス（StubAgentは常にpassするため
    ラウンド末の自動失効ロジックだけを純粋に検証できる）"""
    from engine.config import GameConfig as _GameConfig
    from engine.events import EventLogger
    from engine.game import Game
    from engine.negotiation import StubAgent

    config = _GameConfig.baseline_v1(num_players).model_copy(
        update={"num_rounds": 12, "negotiation_max_turns": 1},
    )
    agents = {f"P{i+1:02d}": StubAgent() for i in range(num_players)}
    game = Game(config=config, agents=agents, seed=1, logger=EventLogger())
    game._setup()
    game._phase_market_open(1)
    return game


def _proposed_contract_awaiting_signature(round_created: int) -> Contract:
    ob = make_obligation(
        "OB_E5_01", "C_e5_test",
        obligor="P01", counterparty="P02",
        ob_type=ObligationType.TYPE_A_PAYMENT,
        round_num=round_created + 2,
        details={"amount": 100000},
    )
    return Contract(
        contract_id="C_e5_test",
        proposer="P01",
        parties=["P01", "P02"],
        signed_by=["P01"],  # P02が未署名のまま
        obligations=[ob],
        round_created=round_created,
        status=ContractStatus.PROPOSED,
    )


class TestProposedContractExpiresAtRoundEnd:
    """v0.8 D5: 当該ラウンドで提案され署名が揃わなかったPROPOSED契約は
    ラウンド末にEXPIREDへ遷移する"""

    def test_unsigned_proposed_contract_expires_same_round(self):
        game = _make_game_for_expiry()
        game.contracts = [_proposed_contract_awaiting_signature(round_created=1)]

        game._phase_negotiation(1)

        assert len(game.contracts) == 1
        assert game.contracts[0].status == ContractStatus.EXPIRED

    def test_proposer_receives_contract_expired_notice(self):
        game = _make_game_for_expiry()
        game.contracts = [_proposed_contract_awaiting_signature(round_created=1)]

        game._phase_negotiation(1)

        notices = game._contract_notices.get("P01", [])
        kinds = [n["kind"] for n in notices]
        assert "contract_expired" in kinds
        notice = next(n for n in notices if n["kind"] == "contract_expired")
        assert notice["contract_id"] == "C_e5_test"
        assert notice["unsigned_by"] == ["P02"]

    def test_expired_contract_excluded_from_pending_and_my_contracts(self):
        game = _make_game_for_expiry()
        game.contracts = [_proposed_contract_awaiting_signature(round_created=1)]

        game._phase_negotiation(1)

        state = game._build_visible_state(2, for_player_id="P02")
        assert state["contracts_pending"] == []
        assert state["my_contracts"] == []

    def test_expired_contract_shown_in_contracts_public_raw_status(self):
        game = _make_game_for_expiry()
        game.contracts = [_proposed_contract_awaiting_signature(round_created=1)]

        game._phase_negotiation(1)

        state = game._build_visible_state(2, for_player_id="P03")
        public = [c for c in state["contracts_public"] if c["contract_id"] == "C_e5_test"]
        assert len(public) == 1
        assert public[0]["status"] == "expired"

    def test_proposed_contract_from_earlier_round_not_expired_by_later_round_end(self):
        """round_created != round_num（前ラウンド以前の提案）はこの巡の
        ラウンド末失効の対象にならない（同ラウンド提案分のみが対象）"""
        game = _make_game_for_expiry()
        game.contracts = [_proposed_contract_awaiting_signature(round_created=1)]

        game._phase_negotiation(2)

        assert game.contracts[0].status == ContractStatus.PROPOSED


class TestProposedContractExpiresOnPartyElimination:
    """v0.8 D5: PROPOSED契約の当事者が署名前に脱落した場合も
    その時点でEXPIREDへ遷移し、生存する提案者に通知される"""

    def test_party_elimination_expires_proposed_contract(self):
        from engine import elimination as elim_ops

        game = _make_game_for_expiry()
        contract = _proposed_contract_awaiting_signature(round_created=1)
        game.contracts = [contract]

        before = list(game.contracts)
        p02 = game.players["P02"]
        game.players["P02"] = p02.model_copy(update={
            "is_alive": False, "elimination_reason": "bankruptcy", "elimination_round": 1,
        })
        game.contracts = elim_ops.expire_obligations_for_player("P02", game.contracts)
        game._notify_contracts_expired_by_elimination(before, "P02")

        assert game.contracts[0].status == ContractStatus.EXPIRED
        notices = game._contract_notices.get("P01", [])
        kinds = [n["kind"] for n in notices]
        assert "contract_expired" in kinds
