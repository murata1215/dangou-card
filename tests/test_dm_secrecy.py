"""
DM本文の秘匿化（§8.2是正）のテスト

背景: engine/game.py:990 の `"messages": list(self._round_messages)` が
for_player_id を無視し、DM本文が全プレイヤーのプロンプトに
`[P01→P02] 本文` として描画されていた（§8.2「DM内容」は秘匿情報のはずが漏洩）。

検証観点:
1. Game._visible_messages() / _build_visible_state() が非当事者のDM本文を落とすこと
2. 当事者（送信者・宛先）には本文が見えること
3. broadcastは全員に本文が見えること（リグレッション防止）
4. redactedレコードでもメタデータ（sender/to/turn）は保持されること
5. for_player_id=None は全DMが安全側（redacted）に倒れること
6. _render_message_list() がredactedを「（非公開のDM）」と描画すること
7. negotiation/commit/reflectionの各プロンプトに非当事者向けDM本文が出ないこと
8. 全プレイヤー分のvisible_stateを走査してもDM本文が非当事者側に一切出ないこと
9. 匿名通信（anonymous_broadcast）が全員に届き、掲載者が秘匿されること
10. commit/double_upのvisible_stateにmy_obligationsが入ること（実装漏れの是正）
11. builder許可リストが正確であること（build_post_game_reflection_promptのみが
    神視点情報の投入を許される唯一の例外であること）
12. build_post_game_reflection_promptが実際に神視点情報（DM本文・匿名通信の
    真の掲載者・非当事者向け契約条項）を含むこと（正のアサーション）
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import StubAgent
from engine.models import (
    MarketCommit, DmAction, BroadcastAction, AnonymousBroadcastAction,
    Contract, ContractStatus, Obligation, ObligationType,
)

import llm.prompt_builder as prompt_builder
from llm.prompt_builder import (
    _render_message_list, build_negotiation_prompt, build_commit_prompt,
    build_reflection_prompt, build_post_game_reflection_prompt,
)

from tests.conftest import make_player


def _commit_for(pid: str, market_id: str, hand):
    """テスト用: プレイヤーの手札からカードを1枚取ってMarketCommitを作る"""
    card = hand[0]
    return MarketCommit(player_id=pid, market_id=market_id, card=card)


def _make_game(num_players: int = 4) -> Game:
    config = GameConfig.baseline_v1_s2(num_players).model_copy(
        update={"num_rounds": 5},
    )
    agents = {f"P{i+1:02d}": StubAgent() for i in range(num_players)}
    game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
    game._setup()
    game._phase_market_open(1)
    return game


class TestVisibleMessagesFiltering:
    """Game._visible_messages() / _build_visible_state() のDMフィルタリング"""

    def test_non_party_dm_message_key_removed(self):
        """非当事者にはDMの'message'キー自体が存在しない（空文字ではなくキー欠落）"""
        game = _make_game()
        game._round_messages.append(
            {"sender": "P01", "to": "P02", "type": "dm", "message": "M01で山分けしよう"}
        )
        state = game._build_visible_state(1, for_player_id="P03")
        dm = state["messages"][0]
        assert "message" not in dm
        assert dm["redacted"] is True

    def test_sender_sees_dm_body(self):
        """送信者には本文が見える"""
        game = _make_game()
        game._round_messages.append(
            {"sender": "P01", "to": "P02", "type": "dm", "message": "M01で山分けしよう"}
        )
        state = game._build_visible_state(1, for_player_id="P01")
        assert state["messages"][0]["message"] == "M01で山分けしよう"
        assert "redacted" not in state["messages"][0]

    def test_recipient_sees_dm_body(self):
        """宛先には本文が見える"""
        game = _make_game()
        game._round_messages.append(
            {"sender": "P01", "to": "P02", "type": "dm", "message": "M01で山分けしよう"}
        )
        state = game._build_visible_state(1, for_player_id="P02")
        assert state["messages"][0]["message"] == "M01で山分けしよう"

    def test_broadcast_visible_to_everyone(self):
        """broadcastは全員に本文が見える（リグレッション防止）"""
        game = _make_game()
        game._round_messages.append(
            {"sender": "P03", "type": "broadcast", "message": "全員仲良く"}
        )
        for pid in ("P01", "P02", "P03", "P04"):
            state = game._build_visible_state(1, for_player_id=pid)
            assert state["messages"][0]["message"] == "全員仲良く"

    def test_redacted_metadata_preserved(self):
        """redactedでもsender/to/turnは保持される（密談の存在は観測可能）"""
        game = _make_game()
        game._round_messages.append(
            {"sender": "P01", "to": "P02", "type": "dm", "message": "secret", "turn": 3}
        )
        state = game._build_visible_state(1, for_player_id="P04")
        dm = state["messages"][0]
        assert dm["sender"] == "P01"
        assert dm["to"] == "P02"
        assert dm["turn"] == 3
        assert dm["type"] == "dm"

    def test_for_player_id_none_redacts_all_dms(self):
        """for_player_id=Noneは誰とも一致しないため全DMが安全側に倒れる"""
        game = _make_game()
        game._round_messages.append(
            {"sender": "P01", "to": "P02", "type": "dm", "message": "secret"}
        )
        state = game._build_visible_state(1, for_player_id=None)
        assert "message" not in state["messages"][0]
        assert state["messages"][0]["redacted"] is True


class TestRenderMessageListRedaction:
    """_render_message_list() のredacted描画"""

    def test_redacted_dm_renders_placeholder(self):
        messages = [
            {"sender": "P01", "to": "P02", "type": "dm", "redacted": True},
        ]
        lines = _render_message_list(messages, "テスト")
        joined = "\n".join(lines)
        assert "P01→P02" in joined
        assert "非公開のDM" in joined

    def test_visible_dm_renders_body(self):
        messages = [
            {"sender": "P01", "to": "P02", "type": "dm", "message": "秘密の話"},
        ]
        lines = _render_message_list(messages, "テスト")
        assert "秘密の話" in "\n".join(lines)

    def test_broadcast_still_renders_body(self):
        messages = [
            {"sender": "P03", "type": "broadcast", "message": "全員仲良く"},
        ]
        lines = _render_message_list(messages, "テスト")
        assert "全員仲良く" in "\n".join(lines)

    def test_anonymous_broadcast_renders_without_sender(self):
        """匿名通信は[匿名]として描画され、senderは出ない"""
        messages = [
            {"sender": None, "type": "anonymous_broadcast", "message": "内部告発だ"},
        ]
        lines = _render_message_list(messages, "テスト")
        joined = "\n".join(lines)
        assert "内部告発だ" in joined
        assert "匿名" in joined


class TestPromptsDoNotLeakDm:
    """negotiation/commit/reflectionプロンプトに非当事者向けDM本文が出ないこと"""

    def test_negotiation_prompt_hides_non_party_dm(self):
        game = _make_game()
        game._round_messages.append(
            {"sender": "P01", "to": "P02", "type": "dm", "message": "M01で組もう"}
        )
        visible_state = game._build_visible_state(1, for_player_id="P03")
        player = game.players["P03"]
        prompt = build_negotiation_prompt(
            player, 1, 1, visible_state, game.config,
        )
        assert "M01で組もう" not in prompt
        assert "非公開のDM" in prompt

    def test_negotiation_prompt_shows_party_dm(self):
        game = _make_game()
        game._round_messages.append(
            {"sender": "P01", "to": "P02", "type": "dm", "message": "M01で組もう"}
        )
        visible_state = game._build_visible_state(1, for_player_id="P02")
        player = game.players["P02"]
        prompt = build_negotiation_prompt(
            player, 1, 1, visible_state, game.config,
        )
        assert "M01で組もう" in prompt

    def test_commit_prompt_hides_non_party_dm(self):
        """commitはLLMAgent._current_round_messages経由だが、
        for_player_id付きnegotiate呼び出しで既にフィルタ済みのため安全"""
        player = make_player("P03", cash=3_000_000)
        config = GameConfig.baseline_v1_s2(4)
        visible_state = {"messages": [], "last_round_results": None, "my_obligations": []}
        negotiation_messages = [
            {"sender": "P01", "to": "P02", "type": "dm", "redacted": True},
        ]
        from engine.models import Market
        markets = [Market(market_id="M01", base_prize=1_000_000, carryover=0)]
        prompt = build_commit_prompt(
            player, markets, 1, visible_state, config,
            negotiation_messages=negotiation_messages,
        )
        assert "非公開のDM" in prompt

    def test_reflection_prompt_hides_non_party_dm(self):
        """reflectionプロンプト（非当事者視点）にDM本文が出ない＝memoryへの二次漏洩防止"""
        player = make_player("P03", cash=3_000_000)
        config = GameConfig.baseline_v1_s2(4)
        visible_state = {
            "messages": [
                {"sender": "P01", "to": "P02", "type": "dm", "redacted": True},
                {"sender": "P04", "type": "broadcast", "message": "全員仲良く"},
            ],
            "last_round_results": None,
            "my_obligations": [],
        }
        prompt = build_reflection_prompt(player, 1, visible_state, config, memory=None)
        assert "非公開のDM" in prompt
        assert "全員仲良く" in prompt


class TestFullScanNoLeak:
    """全プレイヤー分のvisible_stateを走査し、他人宛DM本文がどこにも現れないこと"""

    def test_no_dm_body_leaks_to_non_party_across_all_players(self):
        game = _make_game(num_players=6)
        game._round_messages.append(
            {"sender": "P01", "to": "P02", "type": "dm", "message": "ROYAL_FLUSH_SECRET_XYZ"}
        )
        for pid in game.players:
            state = game._build_visible_state(1, for_player_id=pid)
            state_str = str(state["messages"])
            if pid in ("P01", "P02"):
                assert "ROYAL_FLUSH_SECRET_XYZ" in state_str
            else:
                assert "ROYAL_FLUSH_SECRET_XYZ" not in state_str

    def test_no_failed_action_record_leaks_across_players(self):
        """D2: 不成立アクション記録（my_failed_actions）が他プレイヤーへ漏れないこと"""
        game = _make_game(num_players=6)
        game._action_failures = {pid: [] for pid in game.players}
        game._action_failures["P01"] = [
            {"turn": 1, "action": "dm", "target": "P02",
             "reason": "SENTINEL_FAILURE_LEAK_TEST", "consumed_slot": True},
        ]
        for pid in game.players:
            state = game._build_visible_state(1, for_player_id=pid)
            state_str = str(state.get("my_failed_actions", []))
            if pid == "P01":
                assert "SENTINEL_FAILURE_LEAK_TEST" in state_str
            else:
                assert "SENTINEL_FAILURE_LEAK_TEST" not in state_str


class TestAnonymousBroadcastDelivery:
    """匿名通信の配信修復（Part B）"""

    def test_anonymous_broadcast_reaches_round_messages(self):
        game = _make_game()
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="内部告発だ"),
            "P01", 1, 1,
        )
        assert len(game._round_messages) == 1
        msg = game._round_messages[0]
        assert msg["type"] == "anonymous_broadcast"
        assert msg["message"] == "内部告発だ"

    def test_anonymous_broadcast_poster_is_not_exposed(self):
        """§8.2: 匿名通信の掲載者は秘匿"""
        game = _make_game()
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="内部告発だ"),
            "P01", 1, 1,
        )
        state = game._build_visible_state(1, for_player_id="P03")
        msg = state["messages"][0]
        assert msg.get("sender") is None
        assert "P01" not in str(msg)

    def test_anonymous_broadcast_visible_to_all(self):
        game = _make_game()
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="内部告発だ"),
            "P01", 1, 1,
        )
        for pid in game.players:
            state = game._build_visible_state(1, for_player_id=pid)
            assert state["messages"][0]["message"] == "内部告発だ"

    def test_dm_and_broadcast_still_work_via_execute(self):
        """実行経路を通しても既存のdm/broadcast配信ロジックが壊れていない（回帰）"""
        game = _make_game()
        game._execute_negotiation_action(
            DmAction(player_id="P01", to="P02", message="hi"), "P01", 1, 1,
        )
        game._execute_negotiation_action(
            BroadcastAction(player_id="P03", message="yo"), "P03", 1, 1,
        )
        assert len(game._round_messages) == 2
        types = {m["type"] for m in game._round_messages}
        assert types == {"dm", "broadcast"}


class TestObligationsVisibleInCommitAndDoubleUp:
    """Part C: commit/double_upのvisible_stateにmy_obligationsが入ること"""

    def test_commit_visible_state_has_my_obligations_key(self):
        game = _make_game()
        state = game._build_visible_state(1, for_player_id="P01")
        assert "my_obligations" in state

    def test_phase_commit_passes_for_player_id(self):
        """_phase_commit内で構築されるvisible_stateにfor_player_idが効いている
        （契約義務ブロックが空になる実装漏れの是正、間接検証）"""
        game = _make_game()
        game._round_messages.append(
            {"sender": "P01", "to": "P02", "type": "dm", "message": "secret"}
        )

        captured: dict[str, dict] = {}

        class CapturingAgent(StubAgent):
            def commit(self, player_state, markets, round_num, visible_state):
                captured[player_state.player_id] = visible_state
                return super().commit(player_state, markets, round_num, visible_state)

        game.agents = {pid: CapturingAgent() for pid in game.players}
        game._phase_commit(1)

        # P03はDMの当事者ではないため本文が見えない
        assert "message" not in captured["P03"]["messages"][0]
        # my_obligationsキーが存在する（for_player_id付きで呼ばれている証拠）
        assert "my_obligations" in captured["P01"]


# builder許可リスト: 通常のプレイヤー向けプロンプト（神視点情報を一切含めてはいけない）
KNOWN_AGENT_FACING = {
    "build_system_prompt", "build_loan_prompt", "build_negotiation_prompt",
    "build_commit_prompt", "build_double_up_prompt", "build_reflection_prompt",
    "build_final_reflection_prompt", "build_completion_reflection_prompt",
}
# POST_GAME_REFLECTIONのみ、神視点情報の投入が許される唯一の例外
GOD_INFO_ALLOWED = {"build_post_game_reflection_prompt"}


class TestBuilderAllowList:
    """T-A: llm/prompt_builder.py のbuild_*関数の許可リストが厳密に一致すること"""

    def test_builder_allow_list_is_exact(self):
        actual = {
            name for name in dir(prompt_builder)
            if name.startswith("build_") and callable(getattr(prompt_builder, name))
        }
        assert actual == KNOWN_AGENT_FACING | GOD_INFO_ALLOWED


def _build_god_info_scenario_game() -> Game:
    """
    T-B用: 神視点情報（DM本文・匿名通信の真の掲載者・非当事者向け契約条項）を
    仕込んだ最小シナリオ。tests/test_post_game_reflection.py::_build_scenario_game()
    と同じ先例（直接state操作・_god_transcript/契約/eventの手動注入）を踏襲するが、
    本ファイルの独立性を保つためあえて別実装とする。

    視点プレイヤーはP02に固定する:
    - DM P01→P02（P02は受信者本人なので(1)開示対象）
    - 匿名通信の真の掲載者=P01（P02≠P01なので(2)開示対象）
    - 契約はP03↔P04（P02は非当事者なので(3)開示対象）
    """
    config = GameConfig.baseline_v1(4)
    agents = {f"P{i+1:02d}": StubAgent() for i in range(4)}
    game = Game(config=config, agents=agents, seed=7, logger=EventLogger())
    game._setup()
    game.current_round = 3

    game._god_transcript.append({
        "round": 1, "turn": 1, "type": "dm",
        "message": "DM_SECRECY_TEST_BODY_XYZ", "sender": "P01", "to": "P02",
    })
    game._god_transcript.append({
        "round": 2, "turn": 1, "type": "anonymous_broadcast",
        "message": "ANON_SECRECY_TEST_MSG", "actual_sender": "P01",
    })

    ob = Obligation(
        obligation_id="OB_SECRET", contract_id="C_SECRET",
        obligor="P03", counterparty="P04",
        ob_type=ObligationType.TYPE_A_PAYMENT, round_num=1,
        details={"amount": 3_000_000},
    )
    contract = Contract(
        contract_id="C_SECRET", proposer="P03", parties=["P03", "P04"],
        signed_by=["P03", "P04"], obligations=[ob], round_created=1,
        status=ContractStatus.ACTIVE,
    )
    game.contracts = [contract]

    _eliminate_for_secrecy_test(game, "P04", 3, reason="bankruptcy")
    return game


def _eliminate_for_secrecy_test(game: Game, pid: str, round_num: int, reason: str) -> None:
    p = game.players[pid]
    game.players[pid] = p.model_copy(update={
        "is_alive": False, "elimination_reason": reason, "elimination_round": round_num,
    })


class TestPostGamePromptContainsGodInfo:
    """T-B: build_post_game_reflection_prompt()が実際に神視点情報を含むこと（正のアサーション）"""

    def test_post_game_prompt_does_contain_god_info(self):
        game = _build_god_info_scenario_game()
        result = game._finalize()
        shared = game._build_god_shared_block(result)

        # P02（DMの受信当事者・匿名通信の非掲載者・契約C_SECRETの非当事者）
        # 視点で答え合わせプロンプトを組み立てる
        ctx = game._build_post_game_context("P02", result, shared)
        prompt = build_post_game_reflection_prompt(
            game.players["P02"], game.config, ctx,
        )

        # (i) DM本文
        assert "DM_SECRECY_TEST_BODY_XYZ" in prompt
        # (ii) 匿名通信の真の掲載者がP01であること（ゲーム中は秘匿されていた事実）
        assert "ANON_SECRECY_TEST_MSG" in prompt
        assert "真の掲載者は P01" in prompt
        # (iii) 非当事者向け秘密契約の存在（P02はC_SECRETの当事者ではない）
        assert "非公開の契約" in prompt
        assert "P03・P04" in prompt
