"""
Cycle 4: anonymous_broadcast 送信者本人 self-marker のテスト

背景: engine/game.py の _visible_messages() は匿名通信を全プレイヤーへ
sender:None で届けるが（§7.1/§8.2）、送信者本人もメタデータ上は自分の発言と
識別できなかった。本ファイルは、送信者本人にだけ private per-player 加工で
"is_mine": True を付与する Cycle 4 変更の秘匿境界・ラウンド境界の同時clearを検証する。

匿名性は一切緩和しない: 他プレイヤーの visible_state / Prompt には
is_mine キー自体が存在せず、God View（_god_transcript）の actual_sender 開示も
従来どおりで変化しない。
"""

import inspect

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import AnonymousBroadcastAction
from engine.negotiation import StubAgent
from engine import actions as action_ops

from llm.prompt_builder import (
    build_negotiation_prompt, build_commit_prompt, build_reflection_prompt,
)
from viewer.log_parser import _project_message

from tests.conftest import make_player


def _make_game(num_players: int = 3, seed: int = 42) -> Game:
    config = GameConfig.baseline_v1_s2(num_players)
    agents = {f"P{i+1:02d}": StubAgent() for i in range(num_players)}
    game = Game(config=config, agents=agents, seed=seed, logger=EventLogger())
    game._setup()
    game._phase_market_open(1)
    return game


def _reset_negotiation_state(game: Game) -> None:
    """_phase_negotiation() を経由せず _execute_negotiation_action() を直接叩く
    テスト向けに、必要なラウンド内stateだけを最小構成する（既存D1テストと同じ先例）。
    Cycle 4: _round_messages と _anon_broadcast_owners は必ず _reset_round_message_state()
    経由でクリアする（片方だけ再代入しない、というPlanの必須要件をテスト側でも徹底する）。
    """
    game._reset_round_message_state()
    game._anon_broadcast_counts = {pid: 0 for pid in game.players}
    game._action_failures = {pid: [] for pid in game.players}


# =============================================================================
# 必須1/2: 成功時のみ owner 登録、reject時は非登録
# =============================================================================

class TestOwnerRegistration:
    def test_only_successful_anon_broadcast_registers_owner(self):
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)

        action = AnonymousBroadcastAction(player_id="P01", message="M01へ誘導すべき")
        game._execute_negotiation_action(action, "P01", 1, turn=1)

        assert len(game._round_messages) == 1
        assert game._anon_broadcast_owners == {0: "P01"}

    def test_rejected_anon_broadcast_due_to_limit_registers_no_owner(self):
        """1R2通上限を超えた3通目はvalidate_actionでrejectされ、game側の実行分岐に
        到達しないため owner も message も増えない（上限超過はexecute前のガード）。"""
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)
        game._anon_broadcast_counts["P01"] = 2  # 既に2通送信済み

        action = AnonymousBroadcastAction(player_id="P01", message="3通目")
        result = action_ops.validate_action(
            action, game.players["P01"], game.config, game.players,
            1, game._anon_broadcast_counts.get("P01", 0),
        )
        assert result.success is False
        assert "Anonymous broadcast limit reached" in result.reason

        # rejectされたのでgameの実行分岐(_execute_negotiation_action)には到達させない
        assert len(game._round_messages) == 0
        assert game._anon_broadcast_owners == {}

    def test_rejected_anon_broadcast_due_to_cash_registers_no_owner(self):
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)
        p = game.players["P01"]
        game.players["P01"] = p.model_copy(update={"cash": 50_000})

        action = AnonymousBroadcastAction(player_id="P01", message="現金不足")
        result = action_ops.validate_action(
            action, game.players["P01"], game.config, game.players,
            1, game._anon_broadcast_counts.get("P01", 0),
        )
        assert result.success is False
        assert "Insufficient cash for anonymous broadcast" in result.reason
        assert len(game._round_messages) == 0
        assert game._anon_broadcast_owners == {}


# =============================================================================
# 必須3: 同一本文でも index で正しく所有者判定できる
# =============================================================================

class TestSameTextNotConfused:
    def test_two_senders_same_text_are_not_confused(self):
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)

        text = "M01が有利"
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message=text), "P01", 1, turn=1,
        )
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P02", message=text), "P02", 1, turn=2,
        )

        assert game._anon_broadcast_owners == {0: "P01", 1: "P02"}

        state_p01 = game._build_visible_state(1, for_player_id="P01")
        state_p02 = game._build_visible_state(1, for_player_id="P02")

        msgs_p01 = state_p01["messages"]
        msgs_p02 = state_p02["messages"]
        assert len(msgs_p01) == 2 and len(msgs_p02) == 2

        # P01視点: index0（自分の発言）にだけis_mine、index1（P02の発言）には無い
        assert msgs_p01[0].get("is_mine") is True
        assert "is_mine" not in msgs_p01[1]

        # P02視点: index1（自分の発言）にだけis_mine、index0（P01の発言）には無い
        assert "is_mine" not in msgs_p02[0]
        assert msgs_p02[1].get("is_mine") is True


# =============================================================================
# 必須4/5: ラウンド境界での同時clear・stale owner非漏洩
# =============================================================================

class TestRoundBoundaryClear:
    def test_round_boundary_clears_messages_and_owners_together(self):
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="R1発言"), "P01", 1, turn=1,
        )
        assert len(game._round_messages) == 1
        assert game._anon_broadcast_owners == {0: "P01"}

        # 次ラウンドの開始と同じ操作
        game._reset_round_message_state()

        assert game._round_messages == []
        assert game._anon_broadcast_owners == {}

    def test_stale_owner_does_not_leak_across_rounds(self):
        """R1でP01がindex=0に匿名発言→R2でP02がindex=0に匿名発言した場合、
        P02にmarkerが付き、P01には付かない（前ラウンドownerの誤適用が無いこと）。"""
        game = _make_game(num_players=3)

        # --- Round 1 ---
        _reset_negotiation_state(game)
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="R1発言"), "P01", 1, turn=1,
        )
        assert game._anon_broadcast_owners == {0: "P01"}

        # --- Round 2（本番同様、_reset_round_message_state()経由でクリア）---
        game._reset_round_message_state()
        game._anon_broadcast_counts = {pid: 0 for pid in game.players}
        game._action_failures = {pid: [] for pid in game.players}
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P02", message="R2発言"), "P02", 2, turn=1,
        )

        assert game._anon_broadcast_owners == {0: "P02"}  # indexが再利用されている

        state_p02 = game._build_visible_state(2, for_player_id="P02")
        state_p01 = game._build_visible_state(2, for_player_id="P01")
        assert state_p02["messages"][0].get("is_mine") is True
        assert "is_mine" not in state_p01["messages"][0]

    def test_round_message_state_reset_is_single_sourced(self):
        """engine/game.py内で _round_messages / _anon_broadcast_owners への再代入が
        __init__ と _reset_round_message_state() 以外に存在しないことをソーススキャンで固定する。
        （C2: 片方だけ書き換える余地を構造的に消す、というPlanの必須要件）"""
        import engine.game as game_module

        source = inspect.getsource(game_module)
        lines = source.splitlines()

        reassign_lines = [
            (i + 1, line) for i, line in enumerate(lines)
            if ("self._round_messages = []" in line or "self._round_messages: list[dict] = []" in line
                or "self._anon_broadcast_owners = {}" in line
                or "self._anon_broadcast_owners: dict[int, str] = {}" in line)
        ]
        # 期待: __init__（型注釈付き宣言）2箇所 + _reset_round_message_state()内 2箇所 = 4箇所
        assert len(reassign_lines) == 4, f"unexpected reassignment sites: {reassign_lines}"

        # _reset_round_message_state 内の2行が隣接していること（同一関数内で同時に行われる証拠）
        reset_source = inspect.getsource(game_module.Game._reset_round_message_state)
        assert "self._round_messages = []" in reset_source
        assert "self._anon_broadcast_owners = {}" in reset_source


# =============================================================================
# 必須6/7: 本人のみ marker、他人はフィールド自体が無い
# =============================================================================

class TestSelfMarkerVisibility:
    def test_sender_sees_own_anon_broadcast_marker(self):
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="M01へ誘導すべき"), "P01", 1, turn=1,
        )

        state_self = game._build_visible_state(1, for_player_id="P01")
        msg = state_self["messages"][0]
        assert msg["is_mine"] is True
        assert msg["sender"] is None  # 匿名性そのものは維持

        prompt = build_negotiation_prompt(
            game.players["P01"], 1, 2, state_self, game.config,
        )
        assert "[匿名/あなたが送信] M01へ誘導すべき" in prompt

    def test_other_player_has_no_self_marker_field_at_all(self):
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="M01へ誘導すべき"), "P01", 1, turn=1,
        )

        state_other = game._build_visible_state(1, for_player_id="P02")
        msg = state_other["messages"][0]
        assert "is_mine" not in msg  # Falseですら出さない
        assert msg["sender"] is None
        assert "actual_sender" not in msg
        assert "P01" not in str(msg)

        prompt = build_negotiation_prompt(
            game.players["P02"], 1, 2, state_other, game.config,
        )
        assert "[匿名]" in prompt
        assert "あなたが送信" not in prompt
        # 匿名メッセージ行そのものにP01（実送信者）が出ないこと
        anon_line = next(line for line in prompt.splitlines() if "[匿名]" in line)
        assert "P01" not in anon_line


# =============================================================================
# 必須8: God View の actual_sender は従来どおり
# =============================================================================

class TestGodViewUnchanged:
    def test_god_view_actual_sender_preserved(self):
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="M01へ誘導すべき"), "P01", 1, turn=1,
        )

        god_entries = [e for e in game._god_transcript if e.get("type") == "anonymous_broadcast"]
        assert len(god_entries) == 1
        assert god_entries[0]["actual_sender"] == "P01"
        assert god_entries[0]["message"] == "M01へ誘導すべき"
        assert "is_mine" not in god_entries[0]  # is_mineはgod_transcriptに一切流用されない


# =============================================================================
# 必須9: reflection prompt（Memory生成入力）/ commit prompt での伝播
# =============================================================================

class TestCrossPhasePropagation:
    def test_reflection_prompt_carries_self_marker_for_sender_only(self):
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="M01へ誘導すべき"), "P01", 1, turn=1,
        )

        state_p01 = game._build_visible_state(1, for_player_id="P01")
        state_p02 = game._build_visible_state(1, for_player_id="P02")

        prompt_p01 = build_reflection_prompt(game.players["P01"], 1, state_p01, game.config)
        prompt_p02 = build_reflection_prompt(game.players["P02"], 1, state_p02, game.config)

        assert "[匿名/あなたが送信] M01へ誘導すべき" in prompt_p01
        assert "あなたが送信" not in prompt_p02
        assert "[匿名] M01へ誘導すべき" in prompt_p02

    def test_commit_prompt_carries_self_marker(self):
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="M01へ誘導すべき"), "P01", 1, turn=1,
        )

        state_p01 = game._build_visible_state(1, for_player_id="P01")
        state_p02 = game._build_visible_state(1, for_player_id="P02")

        prompt_p01 = build_commit_prompt(
            game.players["P01"], game._current_markets, 1, state_p01, game.config,
            negotiation_messages=state_p01["messages"],
        )
        prompt_p02 = build_commit_prompt(
            game.players["P02"], game._current_markets, 1, state_p02, game.config,
            negotiation_messages=state_p02["messages"],
        )

        assert "[匿名/あなたが送信] M01へ誘導すべき" in prompt_p01
        assert "あなたが送信" not in prompt_p02


# =============================================================================
# 必須10: Viewer既存表示に変更なし
# =============================================================================

class TestViewerUnaffected:
    def test_viewer_projection_unchanged_by_is_mine_field(self):
        """_project_message は is_mine キーの有無に関わらず同一出力を返す
        （Viewerはengine._round_messagesではなく_god_transcript由来のログを読むため、
        is_mineフィールドの存在自体を参照しない設計上の帰結を確認する）。"""
        base = {
            "sender": None, "type": "anonymous_broadcast", "message": "M01有利",
            "turn": 1, "round": 1, "actual_sender": "P01",
        }
        with_marker = dict(base, is_mine=True)

        for view in ("public", "god"):
            out_base = _project_message(dict(base), view)
            out_marker = _project_message(dict(with_marker), view)
            assert out_base == out_marker
            assert "is_mine" not in out_base
            assert "is_mine" not in out_marker


# =============================================================================
# 追加: 他プレイヤーprompt全経路でのmarker文字列非混入 / Bot経路 / 戦略助言非追加
# =============================================================================

class TestAdditionalGuards:
    def test_other_player_prompts_never_contain_marker_string(self):
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="M01へ誘導すべき"), "P01", 1, turn=1,
        )
        state_p02 = game._build_visible_state(1, for_player_id="P02")

        nego_prompt = build_negotiation_prompt(game.players["P02"], 1, 2, state_p02, game.config)
        commit_prompt = build_commit_prompt(
            game.players["P02"], game._current_markets, 1, state_p02, game.config,
            negotiation_messages=state_p02["messages"],
        )
        reflection_prompt = build_reflection_prompt(game.players["P02"], 1, state_p02, game.config)

        for prompt in (nego_prompt, commit_prompt, reflection_prompt):
            assert "あなたが送信" not in prompt

    def test_bot_path_visible_state_has_no_marker(self):
        """for_player_id=None（Bot/内部呼び出し）にはis_mineが一切付かない"""
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="M01へ誘導すべき"), "P01", 1, turn=1,
        )
        state_none = game._build_visible_state(1, for_player_id=None)
        assert "is_mine" not in state_none["messages"][0]

    def test_no_strategy_advice_in_marker(self):
        game = _make_game(num_players=3)
        _reset_negotiation_state(game)
        game._execute_negotiation_action(
            AnonymousBroadcastAction(player_id="P01", message="M01へ誘導すべき"), "P01", 1, turn=1,
        )
        state_p01 = game._build_visible_state(1, for_player_id="P01")
        prompt = build_negotiation_prompt(game.players["P01"], 1, 2, state_p01, game.config)

        marker_idx = prompt.index("[匿名/あなたが送信]")
        marker_line = prompt[marker_idx:marker_idx + 200].splitlines()[0]
        for advice_word in ("推奨", "有利", "おすすめ", "安全", "使うべき", "積極的", "活用"):
            assert advice_word not in marker_line
