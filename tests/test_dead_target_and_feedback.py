"""
脱落済みplayer宛てDM問題（D1〜D4）の回帰テスト

背景: Viewer God Viewで、P06(R9)の詳細にP04→P05等の無関係DMではなく、
脱落済みP09宛のDMが多数表示される事象を調査した結果、Engineは全件を正しく
拒否していたが（D1は既存実装で対称。dm/transferのみ）、以下4つの独立した
欠陥が判明した:

D1: engine/actions.py — contract_propose/card_trade_proposeの宛先生存チェックが
    dm/transferと非対称（脱落者を相手に含めても検証をすり抜けていた）
D2: engine/game.py — 不成立アクションがアクション枠を消費するにもかかわらず、
    理由がeventログにしか出ずLLMへ一切フィードバックされない
    （trial_C_l12_r12_20260822: P06がR7-R9で25回DM不成立を繰り返し、
    3ラウンド完全に無行動→R9破産脱落の実害）
D3: llm/prompt_builder.py — RULES_SUMMARYは脱落公示を約束しているが、
    実際に届けるプロンプトが1つも無かった
D4: engine/game.py — 脱落者が当事者の契約がstatus="active"のまま表示され、
    「死者との契約がまだ生きている」と誤読される

本ファイルはtests/test_dm_secrecy.pyの_make_game()/StubAgentパターンを踏襲する。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import StubAgent
from engine import actions as action_ops
from engine.models import (
    PlayerState, Action, PassAction, DmAction, TransferAction,
    ContractProposeAction, CardTradeProposeAction,
    Contract, ContractStatus, Obligation, ObligationType,
)

import llm.prompt_builder as prompt_builder
from llm.prompt_builder import (
    _render_eliminations_block, _render_action_feedback_block,
    build_negotiation_prompt, build_reflection_prompt, build_system_prompt,
)

from tests.conftest import make_player


def _make_game(num_players: int = 4, card_trade_enabled: bool = False) -> Game:
    if card_trade_enabled:
        config = GameConfig.baseline_v1_s2(num_players).model_copy(
            update={"num_rounds": 5},
        )
    else:
        config = GameConfig.baseline_v1(num_players).model_copy(
            update={"num_rounds": 5},
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


# =====================================================================
# D1: engine/actions.py — 宛先生存チェックの対称化
# =====================================================================

class TestDeadTargetRejection:
    """dm/transfer/contract_propose/card_trade_proposeの宛先生存チェック"""

    def test_dm_to_dead_target_rejected(self):
        game = _make_game()
        _eliminate(game, "P02", 1, "bankruptcy")
        action = DmAction(player_id="P01", to="P02", message="hi")
        result = action_ops.validate_action(
            action, game.players["P01"], game.config, game.players, round_num=2,
        )
        assert not result.success
        assert "not alive" in result.reason

    def test_dm_to_unknown_target_rejected(self):
        game = _make_game()
        action = DmAction(player_id="P01", to="P99", message="hi")
        result = action_ops.validate_action(
            action, game.players["P01"], game.config, game.players, round_num=2,
        )
        assert not result.success

    def test_dm_to_living_target_succeeds(self):
        """正の対照: 生存宛先へのDMは従来どおり成功"""
        game = _make_game()
        action = DmAction(player_id="P01", to="P02", message="hi")
        result = action_ops.validate_action(
            action, game.players["P01"], game.config, game.players, round_num=2,
        )
        assert result.success

    def test_transfer_to_dead_target_rejected(self):
        game = _make_game()
        _eliminate(game, "P02", 1, "bankruptcy")
        action = TransferAction(player_id="P01", to="P02", amount=100_000)
        result = action_ops.validate_action(
            action, game.players["P01"], game.config, game.players, round_num=2,
        )
        assert not result.success
        assert "not alive" in result.reason

    def test_contract_propose_dead_counterparty_rejected(self):
        game = _make_game()
        _eliminate(game, "P02", 1, "bankruptcy")
        action = ContractProposeAction(
            player_id="P01", with_players=["P02"],
            terms=[{
                "obligor": "P01", "counterparty": "P02",
                "ob_type": "type_a_payment", "round_num": 3,
                "details": {"amount": 100_000},
            }],
        )
        result = action_ops.validate_action(
            action, game.players["P01"], game.config, game.players, round_num=2,
        )
        assert not result.success
        assert "not alive" in result.reason

    def test_contract_propose_insufficient_cash_reason_wins_over_dead_target(self):
        """順序ガード: 資金不足の判定が生存チェックより先に効く
        （test_acceptance.py::test_15_contract_fee_insufficient と同じ順序を保証）"""
        config = GameConfig.default_20()
        p01 = make_player("P01", cash=50_000, debt=0)
        players = {"P01": p01}  # P02はplayers辞書に存在しない（未知宛先）
        action = ContractProposeAction(
            player_id="P01", with_players=["P02"],
            terms=[{
                "obligor": "P01", "counterparty": "P02",
                "ob_type": "type_a_payment", "round_num": 5,
                "details": {"amount": 100_000},
            }],
        )
        result = action_ops.validate_action(action, p01, config, players, round_num=1)
        assert not result.success
        assert "insufficient cash" in result.reason.lower()

    def test_contract_propose_all_alive_succeeds(self):
        game = _make_game()
        action = ContractProposeAction(
            player_id="P01", with_players=["P02"],
            terms=[{
                "obligor": "P01", "counterparty": "P02",
                "ob_type": "type_a_payment", "round_num": 3,
                "details": {"amount": 100_000},
            }],
        )
        result = action_ops.validate_action(
            action, game.players["P01"], game.config, game.players, round_num=2,
        )
        assert result.success

    def test_card_trade_propose_all_dead_targets_rejected(self):
        game = _make_game(num_players=4, card_trade_enabled=True)
        _eliminate(game, "P02", 1, "bankruptcy")
        hand = game.players["P01"].hand
        give_rank = hand[0].rank.name
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02"],
            give_card=give_rank, receive_card=give_rank, cash_amount=0,
        )
        result = action_ops.validate_action(
            action, game.players["P01"], game.config, game.players, round_num=2,
        )
        assert not result.success
        assert "No living target" in result.reason

    def test_card_trade_propose_one_living_target_succeeds(self):
        """ブロードキャスト意味論の保護: 1人でも生存していれば提案は成立"""
        game = _make_game(num_players=4, card_trade_enabled=True)
        _eliminate(game, "P02", 1, "bankruptcy")
        hand = game.players["P01"].hand
        give_rank = hand[0].rank.name
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02", "P03"],
            give_card=give_rank, receive_card=give_rank, cash_amount=0,
        )
        result = action_ops.validate_action(
            action, game.players["P01"], game.config, game.players, round_num=2,
        )
        assert result.success

    def test_card_trade_execute_skips_dead_target_creates_one_proposal(self):
        """execute時に死亡宛先をスキップし提案が1件だけ作られること（既存挙動の保護）"""
        game = _make_game(num_players=4, card_trade_enabled=True)
        _eliminate(game, "P02", 1, "bankruptcy")
        hand = game.players["P01"].hand
        give_rank = hand[0].rank.name
        action = CardTradeProposeAction(
            player_id="P01", with_players=["P02", "P03"],
            give_card=give_rank, receive_card=give_rank, cash_amount=0,
        )
        game._execute_negotiation_action(action, "P01", 2, 1)
        assert len(game.trade_proposals) == 1
        assert game.trade_proposals[0].with_player == "P03"


# =====================================================================
# D2 (engine側): 不成立アクション記録
# =====================================================================

class TestActionFailureRecord:
    """Game._record_action_failure() / self._action_failures"""

    def test_failure_is_recorded(self):
        game = _make_game()
        game._action_failures = {pid: [] for pid in game.players}
        action = DmAction(player_id="P01", to="P02", message="hi")
        game._record_action_failure("P01", action, "Target P02 is not alive", 1, True)
        assert len(game._action_failures["P01"]) == 1
        entry = game._action_failures["P01"][0]
        assert entry["action"] == "dm"
        assert entry["reason"] == "Target P02 is not alive"
        assert entry["target"] == "P02"
        assert entry["consumed_slot"] is True

    def test_message_body_never_recorded(self):
        """message本文が絶対に含まれないこと（sentinel文字列で検証）"""
        game = _make_game()
        game._action_failures = {pid: [] for pid in game.players}
        action = DmAction(player_id="P01", to="P02", message="SENTINEL_SECRET_BODY")
        game._record_action_failure("P01", action, "Target P02 is not alive", 1, True)
        assert "SENTINEL_SECRET_BODY" not in str(game._action_failures)

    def test_pass_action_never_recorded(self):
        """passは_phase_negotiationの分岐でcontinueするため記録対象にならない"""
        game = _make_game()
        game._action_failures = {pid: [] for pid in game.players}
        # passはそもそも_record_action_failure()の呼び出し経路に乗らないことを
        # _phase_negotiation実行で確認する（StubAgentは常にpass）
        game._phase_negotiation(1)
        for pid in game.players:
            assert game._action_failures.get(pid, []) == []

    def test_reset_on_round_start(self):
        game = _make_game()
        game._action_failures = {"P01": [{"turn": 1, "action": "dm", "reason": "x"}]}
        game._phase_negotiation(1)
        assert game._action_failures["P01"] == []

    def test_memo_max_keeps_last_n_entries(self):
        game = _make_game()
        game._action_failures = {pid: [] for pid in game.players}
        action = DmAction(player_id="P01", to="P02", message="hi")
        for i in range(20):
            game._record_action_failure("P01", action, f"reason{i}", i, True)
        bucket = game._action_failures["P01"]
        assert len(bucket) == game._ACTION_FAILURE_MEMO_MAX
        # 末尾12件が残る
        assert bucket[-1]["reason"] == "reason19"
        assert bucket[0]["reason"] == f"reason{20 - game._ACTION_FAILURE_MEMO_MAX}"

    def test_failure_still_consumes_action_slot(self):
        """§2.5無変更ガード: 不成立でも枠を消費すること"""
        game = _make_game()
        alive = list(game.players.keys())
        game.agents = {pid: _AlwaysDmDeadTarget(alive) for pid in alive}
        _eliminate(game, alive[-1], 0, "bankruptcy")
        game._phase_negotiation(1)
        living = [pid for pid in alive if pid != alive[-1]]
        for pid in living:
            assert game._action_counts[pid] == game.config.negotiation_max_actions

    def test_execute_exception_path_records_failure(self):
        game = _make_game()
        game._action_failures = {pid: [] for pid in game.players}
        bad_action = TransferAction(player_id="P01", to="P02", amount=100_000)
        # 実行時例外を強制するため存在しないプレイヤーへの参照を起こす
        del game.players["P02"]
        game.players["P02"] = None  # type: ignore[assignment]
        game._execute_negotiation_action(bad_action, "P01", 1, 1)
        assert len(game._action_failures["P01"]) == 1
        assert game._action_failures["P01"][0]["reason"] == "実行時エラーにより不成立"


class _AlwaysDmDeadTarget(StubAgent):
    """常に指定した最後のプレイヤーID宛にDMを試みるエージェント（テスト専用）"""

    def __init__(self, pids: list[str]):
        self._target = pids[-1]

    def negotiate(self, player_state, round_num, turn, visible_state) -> Action:
        if player_state.player_id == self._target:
            return PassAction(player_id=player_state.player_id)
        return DmAction(
            player_id=player_state.player_id, to=self._target, message="hi",
        )


class TestActionFailureVisibility:
    """_build_visible_state()のmy_failed_actions/my_action_budget公開範囲"""

    def test_visible_to_owner(self):
        game = _make_game()
        game._action_failures = {"P01": [{"turn": 1, "action": "dm",
                                          "reason": "not alive", "consumed_slot": True}]}
        state = game._build_visible_state(1, for_player_id="P01")
        assert state["my_failed_actions"][0]["reason"] == "not alive"

    def test_key_absent_when_for_player_id_none(self):
        game = _make_game()
        game._action_failures = {"P01": [{"turn": 1, "action": "dm", "reason": "x"}]}
        state = game._build_visible_state(1, for_player_id=None)
        assert "my_failed_actions" not in state
        assert "my_action_budget" not in state

    def test_no_leak_across_all_players(self):
        """全pidを走査して他人へ漏れないこと"""
        game = _make_game(num_players=6)
        game._action_failures = {pid: [] for pid in game.players}
        game._action_failures["P01"] = [
            {"turn": 1, "action": "dm", "reason": "SENTINEL_FAILURE_P01",
             "target": "P02", "consumed_slot": True},
        ]
        for pid in game.players:
            state = game._build_visible_state(1, for_player_id=pid)
            state_str = str(state.get("my_failed_actions", []))
            if pid == "P01":
                assert "SENTINEL_FAILURE_P01" in state_str
            else:
                assert "SENTINEL_FAILURE_P01" not in state_str

    def test_budget_returns_used_and_max(self):
        game = _make_game()
        game._action_counts = {"P01": 3}
        state = game._build_visible_state(1, for_player_id="P01")
        assert state["my_action_budget"] == {
            "used": 3, "max": game.config.negotiation_max_actions,
        }


# =====================================================================
# D2 (プロンプト描画側)
# =====================================================================

class TestActionFeedbackPrompt:
    """_render_action_feedback_block() の描画"""

    def test_reason_and_target_shown(self):
        visible_state = {
            "my_failed_actions": [
                {"turn": 1, "action": "dm", "target": "P09", "reason": "Target P09 is not alive"},
            ],
            "my_action_budget": {"used": 1, "max": 10},
        }
        lines = "\n".join(_render_action_feedback_block(visible_state))
        assert "P09" in lines
        assert "Target P09 is not alive" in lines

    def test_repeated_failure_triggers_warning(self):
        """同一失敗3回で反復警告が出る"""
        fail = {"turn": 1, "action": "dm", "target": "P09", "reason": "Target P09 is not alive"}
        visible_state = {
            "my_failed_actions": [dict(fail, turn=t) for t in range(1, 4)],
            "my_action_budget": {"used": 3, "max": 10},
        }
        lines = "\n".join(_render_action_feedback_block(visible_state))
        assert "3回繰り返しています" in lines

    def test_remaining_budget_shown(self):
        visible_state = {"my_failed_actions": [], "my_action_budget": {"used": 7, "max": 10}}
        lines = "\n".join(_render_action_feedback_block(visible_state))
        assert "残りアクション枠: 3/10" in lines

    def test_empty_when_no_failures_and_no_budget(self):
        """失敗0件・budget未設定ならセクションごと出ない（トークン回帰）"""
        visible_state = {}
        assert _render_action_feedback_block(visible_state) == []

    def test_negotiation_prompt_includes_feedback(self):
        game = _make_game()
        game._action_failures = {"P01": [
            {"turn": 1, "action": "dm", "target": "P09", "reason": "Target P09 is not alive"},
        ]}
        game._action_counts = {"P01": 1}
        state = game._build_visible_state(1, for_player_id="P01")
        prompt = build_negotiation_prompt(
            game.players["P01"], 1, 2, state, game.config,
        )
        assert "Target P09 is not alive" in prompt

    def test_reflection_prompt_includes_feedback(self):
        game = _make_game()
        game._action_failures = {"P01": [
            {"turn": 1, "action": "dm", "target": "P09", "reason": "Target P09 is not alive"},
        ]}
        game._action_counts = {"P01": 1}
        state = game._build_visible_state(1, for_player_id="P01")
        prompt = build_reflection_prompt(game.players["P01"], 1, state, game.config)
        assert "Target P09 is not alive" in prompt


# =====================================================================
# D3: 脱落公示
# =====================================================================

class TestEliminationAnnouncement:
    """_render_eliminations_block() と配線"""

    def test_eliminated_players_has_round_and_reason(self):
        game = _make_game()
        _eliminate(game, "P03", 3, "contract_violation")
        state = game._build_visible_state(4, for_player_id="P01")
        assert state["eliminated_players"] == [
            {"player_id": "P03", "round": 3, "reason": "contract_violation"},
        ]

    def test_eliminated_player_not_in_alive_players(self):
        """既存挙動の固定"""
        game = _make_game()
        _eliminate(game, "P03", 3, "contract_violation")
        state = game._build_visible_state(4, for_player_id="P01")
        assert "P03" not in state["alive_players"]

    def test_negotiation_prompt_shows_elimination(self):
        game = _make_game()
        _eliminate(game, "P03", 3, "contract_violation")
        state = game._build_visible_state(4, for_player_id="P01")
        prompt = build_negotiation_prompt(
            game.players["P01"], 4, 1, state, game.config,
        )
        assert "P03: R3脱落（契約違反）" in prompt
        assert "アクション枠だけを失います" in prompt

    def test_current_round_marker(self):
        game = _make_game()
        _eliminate(game, "P03", 4, "bankruptcy")
        state = game._build_visible_state(4, for_player_id="P01")
        lines = "\n".join(_render_eliminations_block(state, round_num=4))
        assert "⬅ 今ラウンド" in lines

    def test_reflection_prompt_shows_elimination(self):
        game = _make_game()
        _eliminate(game, "P03", 3, "contract_violation")
        state = game._build_visible_state(4, for_player_id="P01")
        prompt = build_reflection_prompt(game.players["P01"], 4, state, game.config)
        assert "P03: R3脱落" in prompt

    def test_no_eliminations_renders_nothing(self):
        game = _make_game()
        state = game._build_visible_state(1, for_player_id="P01")
        assert _render_eliminations_block(state) == []

    def test_memory_stale_warning_in_negotiation_and_reflection(self):
        game = _make_game()
        state = game._build_visible_state(1, for_player_id="P01")
        neg_prompt = build_negotiation_prompt(
            game.players["P01"], 1, 1, state, game.config, memory="past note",
        )
        refl_prompt = build_reflection_prompt(
            game.players["P01"], 1, state, game.config, memory="past note",
        )
        assert "このメモは過去の自分の記述です" in neg_prompt
        assert "このメモは過去の自分の記述です" in refl_prompt

    def test_final_reflection_prompt_unaffected_by_stale_warning(self):
        """バイト安定性ガード: final_reflectionにはstale注記が無い（デフォルトFalse）"""
        from llm.prompt_builder import build_final_reflection_prompt
        game = _make_game()
        _eliminate(game, "P01", 1, "bankruptcy")
        state = game._build_visible_state(1, for_player_id="P01")
        ctx = {
            "elimination_round": 1, "elimination_reason": "bankruptcy",
            "final_rank": None, "survivors": [], "other_eliminated": [],
        }
        prompt = build_final_reflection_prompt(
            game.players["P01"], 1, state, game.config, ctx, memory="past note",
        )
        assert "このメモは過去の自分の記述です" not in prompt


class TestRulesSummaryPromises:
    """RULES_SUMMARY文言と.format()のプリセット互換性"""

    def test_dead_target_prohibition_mentioned(self):
        config = GameConfig.baseline_v1(4)
        prompt = build_system_prompt("P01", config)
        # v0.8サイクル8.2 Step1で1文に統合された（事実は不変）
        assert "脱落者指定は不成立でアクション枠を失うが" in prompt

    def test_failed_action_consumes_slot_mentioned(self):
        config = GameConfig.baseline_v1(4)
        prompt = build_system_prompt("P01", config)
        assert "**不成立アクションは枠を消費する**" in prompt

    def test_max_actions_number_present(self):
        config = GameConfig.baseline_v1(4)
        prompt = build_system_prompt("P01", config)
        # v0.8サイクル8.2 Step1の圧縮で「最大Nアクション」→「Nアクション/R上限」に短縮
        assert f"{config.negotiation_max_actions}アクション/R上限" in prompt

    def test_all_presets_format_without_keyerror(self):
        presets = [
            GameConfig.default_8_s2(),
            GameConfig.baseline_v1(),
            GameConfig.baseline_v1_s2(),
            GameConfig.default_12(),
            GameConfig.default_20(),
        ]
        for config in presets:
            prompt = build_system_prompt("P01", config)
            assert "{negotiation_max_actions}" not in prompt
            assert "{" not in prompt.split("## アクション形式")[0].replace(
                "{{", "").replace("}}", "")


# =====================================================================
# D4: 脱落当事者を含む契約の表示
# =====================================================================

class TestContractsPublicEliminatedParties:
    def _add_contract(self, game: Game, parties: list[str]) -> None:
        ob = Obligation(
            obligation_id="OB_1", contract_id="C_1",
            obligor=parties[0], counterparty=parties[1],
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 100_000},
        )
        contract = Contract(
            contract_id="C_1", proposer=parties[0], parties=parties,
            signed_by=parties, obligations=[ob], round_created=1,
            status=ContractStatus.ACTIVE,
        )
        game.contracts = [contract]

    def test_eliminated_parties_listed(self):
        game = _make_game()
        self._add_contract(game, ["P01", "P02"])
        _eliminate(game, "P02", 3, "bankruptcy")
        state = game._build_visible_state(4, for_player_id="P01")
        assert state["contracts_public"][0]["eliminated_parties"] == ["P02"]

    def test_all_alive_empty_list(self):
        game = _make_game()
        self._add_contract(game, ["P01", "P02"])
        state = game._build_visible_state(4, for_player_id="P01")
        assert state["contracts_public"][0]["eliminated_parties"] == []

    def test_existing_keys_preserved(self):
        """形状ガード: contract_id/parties/status が残る"""
        game = _make_game()
        self._add_contract(game, ["P01", "P02"])
        state = game._build_visible_state(4, for_player_id="P01")
        entry = state["contracts_public"][0]
        assert entry["contract_id"] == "C_1"
        assert entry["parties"] == ["P01", "P02"]
        assert entry["status"] == "active"

    def test_contract_status_stays_active(self):
        """engine無変更の証明: Contract.statusはACTIVEのまま変わらない"""
        game = _make_game()
        self._add_contract(game, ["P01", "P02"])
        _eliminate(game, "P02", 3, "bankruptcy")
        game._build_visible_state(4, for_player_id="P01")
        assert game.contracts[0].status == ContractStatus.ACTIVE

    def test_reflection_prompt_shows_stale_contract_note(self):
        game = _make_game()
        self._add_contract(game, ["P01", "P02"])
        _eliminate(game, "P02", 3, "bankruptcy")
        state = game._build_visible_state(4, for_player_id="P01")
        prompt = build_reflection_prompt(game.players["P01"], 4, state, game.config)
        assert "脱落済み" in prompt
        assert "解除交渉は不要" in prompt

    def test_reflection_prompt_no_note_when_all_alive(self):
        game = _make_game()
        self._add_contract(game, ["P01", "P02"])
        state = game._build_visible_state(4, for_player_id="P01")
        prompt = build_reflection_prompt(game.players["P01"], 4, state, game.config)
        assert "脱落済み" not in prompt

    def test_reflection_prompt_survives_missing_eliminated_parties_key(self):
        """eliminated_parties欠落の手組みdictでも落ちない（後方互換）"""
        player = make_player("P01", cash=3_000_000)
        config = GameConfig.baseline_v1(4)
        visible_state = {
            "messages": [], "last_round_results": None, "my_obligations": [],
            "contracts_public": [
                {"contract_id": "C_1", "parties": ["P01", "P02"], "status": "active"},
            ],
        }
        prompt = build_reflection_prompt(player, 1, visible_state, config)
        assert "C_1" in prompt


# =====================================================================
# End-to-end: 実バグの再現シナリオ
# =====================================================================

class TestDmLoopScenario:
    """4人でP03を脱落させ、常にP03へDMするエージェントで_phase_negotiation(1)を実行"""

    def test_full_loop_scenario(self):
        game = _make_game(num_players=4)
        _eliminate(game, "P03", 0, "bankruptcy")
        alive = [pid for pid in game.players if pid != "P03"]

        captured_prompts: list[str] = []

        class LoggingDeadTargetAgent(StubAgent):
            def negotiate(self, player_state, round_num, turn, visible_state):
                prompt = build_negotiation_prompt(
                    player_state, round_num, turn, visible_state, game.config,
                )
                captured_prompts.append(prompt)
                return DmAction(player_id=player_state.player_id, to="P03", message="hi")

        game.agents = {pid: LoggingDeadTargetAgent() for pid in alive}
        game.agents["P03"] = StubAgent()  # 脱落済み・呼ばれない想定

        game._phase_negotiation(1)

        # (i) §2.5不変: 枠は消費される
        for pid in alive:
            assert game._action_counts[pid] == game.config.negotiation_max_actions

        # (ii) 2回目以降のプロンプトすべてに"P03"+"不成立"
        # 各プレイヤーごとに複数回呼ばれるため、2回目以降のみを対象化する
        per_player_count: dict[str, int] = {}
        for i, prompt in enumerate(captured_prompts):
            pid = None
            for cand in alive:
                if f"あなたの状態（{cand}）" in prompt:
                    pid = cand
                    break
            if pid is None:
                continue
            per_player_count[pid] = per_player_count.get(pid, 0) + 1
            if per_player_count[pid] >= 2:
                assert "P03" in prompt
                assert "不成立" in prompt

        # (iii) 3回目で反復警告
        third_or_later = [
            p for p in captured_prompts if "同じ不成立を" in p and "回繰り返しています" in p
        ]
        assert len(third_or_later) > 0

        # (iv) _round_messagesにDMが1件も入らない
        dm_messages = [m for m in game._round_messages if m.get("type") == "dm"]
        assert dm_messages == []

    def test_token_length_bound_with_many_eliminations(self):
        """12人・11脱落・失敗12件でプロンプト長 < 12000のトークン上限テスト"""
        game = _make_game(num_players=12)
        for i, pid in enumerate(list(game.players.keys())[1:], start=1):
            _eliminate(game, pid, i % 10 + 1, "bankruptcy")
        game._action_failures["P01"] = [
            {"turn": t, "action": "dm", "target": "P02",
             "reason": "Target P02 is not alive", "consumed_slot": True}
            for t in range(1, 13)
        ]
        game._action_counts["P01"] = 10
        state = game._build_visible_state(11, for_player_id="P01")
        prompt = build_negotiation_prompt(
            game.players["P01"], 11, 11, state, game.config,
        )
        assert len(prompt) < 12000
