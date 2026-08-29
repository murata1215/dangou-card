"""
自分が当事者の契約を毎ラウンド再提示する機能（my_contracts）の回帰テスト

背景: 実run trial_C_l12_r12_20260822 で、P03 は R6 に P09 との契約 C_90159021
（義務: P09は R6 に M02へ参加してはいけない / obligor=P09, counterparty=P03）を
結んだが、P09 は R6 commit で契約違反により脱落した。その後 P03 の negotiation /
commit プロンプトには契約の中身を再提示する経路が一切無く（自分は counterparty
であり obligor ではないため my_obligations に載らない／round_num=6 < round_num
のため二重に除外される）、唯一の記憶手段である Handover Memory だけが劣化しながら
持ち越された結果、R12 のメモに「契約C_90159021…内容は忘れたが、違反しないよう
注意。R12で何か義務があるかもしれない」という誤った警戒が書かれた。

本ファイルは tests/test_dead_target_and_feedback.py の _make_game()/_eliminate()
パターンを踏襲する。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game, _obligation_status
from engine.negotiation import StubAgent
from engine import elimination as elim_ops
from engine.models import (
    PlayerState, Contract, ContractStatus, Obligation, ObligationType,
)

from llm.prompt_builder import (
    _format_obligation_detail,
    _render_my_contracts_block,
    _render_memory_block,
    build_negotiation_prompt,
    build_commit_prompt,
)

from tests.conftest import make_player, make_market


def _make_game(num_players: int = 9) -> Game:
    config = GameConfig.baseline_v1(num_players).model_copy(
        update={"num_rounds": 12},
    )
    agents = {f"P{i+1:02d}": StubAgent() for i in range(num_players)}
    game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
    game._setup()
    game._phase_market_open(1)
    return game


def _eliminate(game: Game, pid: str, round_num: int, reason: str) -> None:
    p = game.players[pid]
    game.players[pid] = p.model_copy(update={
        "is_alive": False, "elimination_reason": reason, "elimination_round": round_num,
    })


def _c90159021_contract() -> Contract:
    """実run再現: P09がR6にM02へ参加してはいけない、義務者はP09のみ"""
    ob = Obligation(
        obligation_id="C_90159021_OB01", contract_id="C_90159021",
        obligor="P09", counterparty="P03",
        ob_type=ObligationType.TYPE_B_NO_MARKET, round_num=6,
        details={"market_id": "M02"},
    )
    return Contract(
        contract_id="C_90159021", proposer="P03", parties=["P03", "P09"],
        signed_by=["P03", "P09"], obligations=[ob], round_created=6,
        status=ContractStatus.ACTIVE,
    )


# =====================================================================
# A. engine/game.py: _build_visible_state() の my_contracts
# =====================================================================

class TestMyContractsVisibleState:
    def test_visible_to_beneficiary_only_party(self):
        """本件の直接回帰: obligor ではない当事者(P03)にも契約が見える"""
        game = _make_game()
        game.contracts = [_c90159021_contract()]
        state = game._build_visible_state(12, for_player_id="P03")

        assert len(state["my_contracts"]) == 1
        entry = state["my_contracts"][0]
        assert entry["contract_id"] == "C_90159021"
        assert entry["parties"] == ["P03", "P09"]
        assert len(entry["obligations"]) == 1
        ob = entry["obligations"][0]
        assert ob["obligor"] == "P09"
        assert ob["counterparty"] == "P03"
        assert ob["ob_type"] == "type_b_no_market"
        assert ob["details"] == {"market_id": "M02"}

        # my_obligations は既存どおり空のまま（P03はobligorではないため）
        assert state["my_obligations"] == []

    def test_hidden_from_non_party(self):
        game = _make_game()
        game.contracts = [_c90159021_contract()]
        state = game._build_visible_state(12, for_player_id="P05")
        assert state["my_contracts"] == []

    def test_absent_for_spectator_state(self):
        game = _make_game()
        game.contracts = [_c90159021_contract()]
        state = game._build_visible_state(12, for_player_id=None)
        assert "my_contracts" not in state

    def test_includes_past_round_obligations_expired(self):
        """R6義務がP09脱落によりexpiredでも、R12視点で消えずob_status=expired"""
        game = _make_game()
        game.contracts = [_c90159021_contract()]
        _eliminate(game, "P09", 6, "contract_violation")
        game.contracts = elim_ops.expire_obligations_for_player("P09", game.contracts)

        state = game._build_visible_state(12, for_player_id="P03")
        entry = state["my_contracts"][0]
        assert entry["eliminated_parties"] == ["P09"]
        ob = entry["obligations"][0]
        assert ob["ob_status"] == "expired"

    def test_includes_past_round_obligations_past_when_not_expired(self):
        """脱落なしなら同じ義務はob_status=past（期限経過・未失効）"""
        game = _make_game()
        game.contracts = [_c90159021_contract()]
        state = game._build_visible_state(12, for_player_id="P03")
        ob = state["my_contracts"][0]["obligations"][0]
        assert ob["ob_status"] == "past"

    def test_obligation_status_derivation(self):
        """_obligation_status() の5分岐を直接検証"""
        base = dict(
            obligation_id="C_x_OB01", contract_id="C_x",
            obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 1},
        )
        fulfilled = Obligation(**base, is_fulfilled=True)
        expired = Obligation(**base, is_expired=True)
        past = Obligation(**base)
        due = Obligation(**{**base, "round_num": 7})
        upcoming = Obligation(**{**base, "round_num": 9})

        assert _obligation_status(fulfilled, round_num=7) == "fulfilled"
        assert _obligation_status(expired, round_num=7) == "expired"
        assert _obligation_status(past, round_num=7) == "past"        # round_num=5 < 7
        assert _obligation_status(due, round_num=7) == "due"          # round_num=7 == 7
        assert _obligation_status(upcoming, round_num=7) == "upcoming"  # round_num=9 > 7

        # fulfilled は expired より優先される
        both = Obligation(**base, is_fulfilled=True, is_expired=True)
        assert _obligation_status(both, round_num=7) == "fulfilled"

    def test_excludes_proposed_contracts(self):
        """PROPOSEDはcontracts_pendingのみ。my_contractsには出ない"""
        ob = Obligation(
            obligation_id="C_p_OB01", contract_id="C_p",
            obligor="P09", counterparty="P03",
            ob_type=ObligationType.TYPE_B_NO_MARKET, round_num=6,
            details={"market_id": "M02"},
        )
        contract = Contract(
            contract_id="C_p", proposer="P03", parties=["P03", "P09"],
            signed_by=["P03"], obligations=[ob], round_created=6,
            status=ContractStatus.PROPOSED,
        )
        game = _make_game()
        game.contracts = [contract]
        state = game._build_visible_state(6, for_player_id="P03")
        assert state["my_contracts"] == []
        assert len(state["contracts_pending"]) == 1

    def test_my_obligations_unchanged_shape(self):
        """既存my_obligationsの出力形状・中身が本変更で変わっていないことの固定"""
        ob = Obligation(
            obligation_id="C_2_OB01", contract_id="C_2",
            obligor="P03", counterparty="P09",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=12,
            details={"amount": 100_000},
        )
        contract = Contract(
            contract_id="C_2", proposer="P03", parties=["P03", "P09"],
            signed_by=["P03", "P09"], obligations=[ob], round_created=6,
            status=ContractStatus.ACTIVE,
        )
        game = _make_game()
        game.contracts = [contract]
        state = game._build_visible_state(12, for_player_id="P03")
        assert state["my_obligations"] == [{
            "contract_id": "C_2",
            "obligor": "P03",
            "counterparty": "P09",
            "ob_type": "type_a_payment",
            "round_num": 12,
            "details": {"amount": 100_000},
        }]


# =====================================================================
# B. llm/prompt_builder.py: レンダリング
# =====================================================================

class TestRenderMyContractsBlock:
    def test_negotiation_and_commit_prompts_contain_my_contracts(self):
        player = make_player("P03", cash=3_000_000)
        config = GameConfig.baseline_v1(5)
        visible_state = {
            "markets": [], "last_round_results": None, "my_obligations": [],
            "alive_players": ["P03", "P09"], "eliminated_players": [],
            "messages": [], "contracts_pending": [],
            "my_contracts": [{
                "contract_id": "C_90159021", "parties": ["P03", "P09"],
                "round_created": 6, "status": "active",
                "eliminated_parties": ["P09"],
                "obligations": [{
                    "obligation_id": "C_90159021_OB01",
                    "obligor": "P09", "counterparty": "P03",
                    "ob_type": "type_b_no_market", "round_num": 6,
                    "details": {"market_id": "M02"}, "ob_status": "expired",
                }],
            }],
        }
        nprompt = build_negotiation_prompt(player, 12, 1, visible_state, config)
        assert "## あなたが当事者の正式契約" in nprompt
        assert "C_90159021" in nprompt
        assert "M02" in nprompt

        cprompt = build_commit_prompt(
            player, [make_market("M01", 1_000_000)], 12, visible_state, config,
        )
        assert "## あなたが当事者の正式契約" in cprompt
        assert "C_90159021" in cprompt

    def test_prompt_omits_block_when_no_contracts(self):
        player = make_player("P03", cash=3_000_000)
        config = GameConfig.baseline_v1(5)
        visible_state = {
            "markets": [], "last_round_results": None, "my_obligations": [],
            "alive_players": ["P03"], "eliminated_players": [], "messages": [],
            "contracts_pending": [], "my_contracts": [],
        }
        prompt = build_negotiation_prompt(player, 1, 1, visible_state, config)
        assert "## あなたが当事者の正式契約" not in prompt

    def test_prompt_omits_block_when_key_absent(self):
        """my_contractsキー自体が無い旧形式dictでも落ちない（後方互換）"""
        player = make_player("P03", cash=3_000_000)
        config = GameConfig.baseline_v1(5)
        visible_state = {
            "markets": [], "last_round_results": None, "my_obligations": [],
            "alive_players": ["P03"], "eliminated_players": [], "messages": [],
            "contracts_pending": [],
        }
        prompt = build_negotiation_prompt(player, 1, 1, visible_state, config)
        assert "## あなたが当事者の正式契約" not in prompt

    def test_settled_contract_shows_no_remaining_obligation_note(self):
        visible_state = {
            "my_contracts": [{
                "contract_id": "C_1", "parties": ["P01", "P02"],
                "round_created": 3, "status": "active",
                "eliminated_parties": [],
                "obligations": [{
                    "obligation_id": "C_1_OB01",
                    "obligor": "P01", "counterparty": "P02",
                    "ob_type": "type_b_card", "round_num": 3,
                    "details": {"card_rank": "ONE_PAIR"}, "ob_status": "past",
                }],
            }],
        }
        lines = _render_my_contracts_block(visible_state, round_num=10)
        text = "\n".join(lines)
        assert "残っている義務はありません" in text

    def test_due_obligation_is_marked(self):
        visible_state = {
            "my_contracts": [{
                "contract_id": "C_1", "parties": ["P01", "P02"],
                "round_created": 3, "status": "active",
                "eliminated_parties": [],
                "obligations": [{
                    "obligation_id": "C_1_OB01",
                    "obligor": "P01", "counterparty": "P02",
                    "ob_type": "type_a_payment", "round_num": 3,
                    "details": {"amount": 500_000}, "ob_status": "due",
                }],
            }],
        }
        lines = _render_my_contracts_block(visible_state, round_num=3)
        text = "\n".join(lines)
        assert "今ラウンドが期限" in text
        assert "残っている義務はありません" not in text

    def test_eliminated_party_marker_shown(self):
        visible_state = {
            "my_contracts": [{
                "contract_id": "C_90159021", "parties": ["P03", "P09"],
                "round_created": 6, "status": "active",
                "eliminated_parties": ["P09"],
                "obligations": [{
                    "obligation_id": "C_90159021_OB01",
                    "obligor": "P09", "counterparty": "P03",
                    "ob_type": "type_b_no_market", "round_num": 6,
                    "details": {"market_id": "M02"}, "ob_status": "expired",
                }],
            }],
        }
        lines = _render_my_contracts_block(visible_state, round_num=12)
        text = "\n".join(lines)
        assert "P09が脱落済み" in text
        assert "失効" in text

    def test_memory_stale_warning_mentions_contracts(self):
        lines = _render_memory_block("何かメモ", stale_warning=True)
        text = "\n".join(lines)
        assert "あなたが当事者の正式契約" in text

    def test_memory_no_stale_warning_by_default(self):
        """stale_warning=Falseの3プロンプト（final/completion/post_game）は出力不変"""
        lines = _render_memory_block("何かメモ")
        text = "\n".join(lines)
        assert "あなたが当事者の正式契約" not in text
        assert "このメモは過去の自分の記述です" not in text

    def test_format_obligation_detail_card_rank_and_fallback(self):
        label_a, detail_a = _format_obligation_detail({
            "ob_type": "type_a_payment", "details": {"amount": 500_000},
        })
        assert label_a == "型A金銭支払い"
        assert detail_a == "50万円"

        label_b, detail_b = _format_obligation_detail({
            "ob_type": "type_b_market", "details": {"market_id": "M02"},
        })
        assert detail_b == "M02"

        _, detail_card_rank = _format_obligation_detail({
            "ob_type": "type_b_card", "details": {"card_rank": "ONE_PAIR"},
        })
        assert detail_card_rank == "ONE_PAIR"

        # 正規化前の古いデータ（"card"キー）との互換フォールバック
        _, detail_card_fallback = _format_obligation_detail({
            "ob_type": "type_b_card", "details": {"card": "TWO_PAIR"},
        })
        assert detail_card_fallback == "TWO_PAIR"

        _, detail_empty = _format_obligation_detail({
            "ob_type": "type_b_no_market", "details": {},
        })
        assert detail_empty == ""


# =====================================================================
# C. シナリオ再現: C_90159021 が R12 プロンプトに現れるようになること
# =====================================================================

class TestC90159021ScenarioReproduction:
    def test_r12_negotiation_prompt_shows_expired_obligation(self):
        """
        Q1の再検証: 本修正後は P03 の R12 negotiation プロンプトに
        C_90159021・M02・R6期限・失効・「残っている義務はありません」が出る。
        （修正前は Handover Memory 内にしか契約IDが出現しなかった＝本テストの前提）
        """
        game = _make_game()
        game.contracts = [_c90159021_contract()]
        _eliminate(game, "P09", 6, "contract_violation")
        game.contracts = elim_ops.expire_obligations_for_player("P09", game.contracts)

        state = game._build_visible_state(12, for_player_id="P03")
        prompt = build_negotiation_prompt(
            game.players["P03"], 12, 1, state, game.config,
            memory="契約C_90159021（P09との契約）がactive。内容は忘れたが、"
                   "違反しないよう注意。R12で何か義務があるかもしれない。",
        )

        assert "## あなたが当事者の正式契約" in prompt
        assert "C_90159021" in prompt
        assert "M02" in prompt
        assert "R6期限" in prompt
        assert "失効" in prompt
        assert "残っている義務はありません" in prompt
        # 記憶ブロックにも契約優先の注記が出ている
        assert "あなたが当事者の正式契約" in prompt.split("## あなたの記憶")[1].split("##")[0]


# =====================================================================
# D. v0.8 I1: ゾンビ契約のCLOSED化
# =====================================================================

class TestZombieContractClosed:
    def test_counterparty_eliminated_no_remaining_obligations_closes_contract(self):
        """相手が脱落し残存義務ゼロになったACTIVE契約は、
        close_contracts_without_remaining_obligations() 後にCLOSEDへ遷移し、
        my_contracts から消える（v0.8 I1）"""
        from engine import contracts as contract_ops

        game = _make_game()
        game.contracts = [_c90159021_contract()]
        _eliminate(game, "P09", 6, "contract_violation")
        game.contracts = elim_ops.expire_obligations_for_player("P09", game.contracts)
        # ここまでは既存挙動（ACTIVEのまま・my_contractsに残る）と同じ
        assert game.contracts[0].status == ContractStatus.ACTIVE

        # v0.8 I1: Settlement/Finance末に呼ばれるゾンビ契約整理を明示的に実行
        game.contracts = contract_ops.close_contracts_without_remaining_obligations(
            game.contracts, round_num=12,
        )
        assert game.contracts[0].status == ContractStatus.CLOSED

        state = game._build_visible_state(12, for_player_id="P03")
        assert state["my_contracts"] == []

    def test_closed_shown_in_contracts_public_raw_status(self):
        """CLOSEDはcontracts_publicには生ステータス文字列のまま残る（v0.8 I1）"""
        from engine import contracts as contract_ops

        game = _make_game()
        game.contracts = [_c90159021_contract()]
        _eliminate(game, "P09", 6, "contract_violation")
        game.contracts = elim_ops.expire_obligations_for_player("P09", game.contracts)
        game.contracts = contract_ops.close_contracts_without_remaining_obligations(
            game.contracts, round_num=12,
        )

        state = game._build_visible_state(12, for_player_id="P03")
        public = [c for c in state["contracts_public"] if c["contract_id"] == "C_90159021"]
        assert len(public) == 1
        assert public[0]["status"] == "closed"

    def test_active_contract_with_future_obligation_not_closed(self):
        """未到来義務が残るACTIVE契約はCLOSEDにならない（回帰防止）"""
        from engine import contracts as contract_ops

        game = _make_game()
        game.contracts = [_c90159021_contract()]
        game.contracts = contract_ops.close_contracts_without_remaining_obligations(
            game.contracts, round_num=1,
        )
        assert game.contracts[0].status == ContractStatus.ACTIVE
