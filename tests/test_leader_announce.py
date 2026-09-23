"""
v0.10 サイクル10.2: 首位公示のテスト

- 各ラウンドのMarket Openで、資産（現金+倍掛け預託-借金残高）が最大の
  生存者のIDのみを公示する（config.leader_announce_enabled）
- 同額タイは該当者全員を公示する（内部の配列順・ID順で1名に絞らない）
- 脱落者はランキング対象外
- 公示は事実（ID）のみ。金額・順位・現金・借金は一切出さない
- leader_announce_enabled=False では既存挙動とバイト単位で完全一致する
"""
import json
import re
import tempfile
from pathlib import Path

import pytest

from bots import BOT_REGISTRY, DEFAULT_ROSTER
from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import DoubleUpDeposit, PlayerState
from engine.negotiation import StubAgent
from engine.player import total_assets
from llm.prompt_builder import (
    LEADER_ANNOUNCE_RULES_LINE,
    build_negotiation_prompt,
    build_system_prompt,
)
from scripts.highlights import detect_highlights
from tests.test_cycle5_prompt_salience import _base_visible_state, _make_player
from viewer.log_parser import get_round_states


FORBIDDEN_WORDS = [
    "狙", "避け", "警戒", "危険", "注意", "有利", "不利", "推奨", "べき", "標的", "脅威",
]


def _make_game(num_players: int = 4) -> Game:
    config = GameConfig.baseline_v1_s2(num_players)
    agents = {f"P{i+1:02d}": StubAgent() for i in range(num_players)}
    game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
    game._setup()
    return game


def _s1_config() -> GameConfig:
    return GameConfig(leader_announce_enabled=False)


def _s2_config() -> GameConfig:
    return GameConfig(leader_announce_enabled=True)


# ---------------------------------------------------------------------------
# total_assets() 資産計算
# ---------------------------------------------------------------------------

class TestTotalAssets:
    def test_deposit_amount_is_added(self):
        p = PlayerState(player_id="P01", cash=100_000, debt_balance=0, initial_loan=0, hand=[])
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=50_000,
            deposited_round=1, success_round=2, resolved=False,
        )
        assert total_assets(p, [dep]) == 150_000

    def test_resolved_deposit_is_not_added(self):
        p = PlayerState(player_id="P01", cash=100_000, debt_balance=0, initial_loan=0, hand=[])
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=50_000,
            deposited_round=1, success_round=2, resolved=True,
        )
        assert total_assets(p, [dep]) == 100_000

    def test_debt_excess_makes_assets_negative(self):
        """free_cash（max(0,...)）と異なり、借金超過分を負の資産として表現できる"""
        p = PlayerState(player_id="P01", cash=10_000, debt_balance=50_000, initial_loan=50_000, hand=[])
        assert total_assets(p, []) == -40_000
        # free_cashは0にクリップされ負値を表現できないことの回帰確認
        assert p.free_cash == 0

    def test_other_players_deposits_are_excluded(self):
        p = PlayerState(player_id="P01", cash=100_000, debt_balance=0, initial_loan=0, hand=[])
        dep = DoubleUpDeposit(
            player_id="P02", deposit_amount=50_000,
            deposited_round=1, success_round=2, resolved=False,
        )
        assert total_assets(p, [dep]) == 100_000

    def test_r1_equivalent_all_tied_zero(self):
        """R1相当: 現金=借入額=借金、預託なし → 資産0"""
        p = PlayerState(player_id="P01", cash=300_000, debt_balance=300_000, initial_loan=300_000, hand=[])
        assert total_assets(p, []) == 0


# ---------------------------------------------------------------------------
# Market Open 判定 / LEADER_ANNOUNCED イベント
# ---------------------------------------------------------------------------

class TestMarketOpenLeaderJudgement:
    def test_single_leader(self):
        game = _make_game(4)
        game.players["P01"].cash = 500_000
        game.players["P02"].cash = 100_000
        game.players["P03"].cash = 100_000
        game.players["P04"].cash = 100_000
        game._phase_market_open(1)
        assert game._current_leader_ids == ["P01"]

    def test_two_way_tie_both_announced(self):
        """同率2名→2名とも公示される"""
        game = _make_game(4)
        game.players["P01"].cash = 500_000
        game.players["P02"].cash = 500_000
        game.players["P03"].cash = 100_000
        game.players["P04"].cash = 100_000
        game._phase_market_open(1)
        assert game._current_leader_ids == ["P01", "P02"]

    def test_tie_result_independent_of_dict_order(self):
        """同率でも配列順先頭だけにならない（players辞書の順序を入れ替えても同じ集合）"""
        game_a = _make_game(4)
        game_a.players["P01"].cash = 500_000
        game_a.players["P04"].cash = 500_000
        game_a.players["P02"].cash = 100_000
        game_a.players["P03"].cash = 100_000
        game_a._phase_market_open(1)

        game_b = _make_game(4)
        # 代入順序を変えても（挿入順に依存する実装ならここで壊れる）
        game_b.players["P04"].cash = 500_000
        game_b.players["P01"].cash = 500_000
        game_b.players["P02"].cash = 100_000
        game_b.players["P03"].cash = 100_000
        game_b._phase_market_open(1)

        assert set(game_a._current_leader_ids) == set(game_b._current_leader_ids) == {"P01", "P04"}

    def test_eliminated_player_excluded_even_with_max_assets(self):
        """脱落者が最大資産でも対象外"""
        game = _make_game(4)
        game.players["P01"].cash = 900_000
        game.players["P01"].is_alive = False
        game.players["P01"].elimination_reason = "bankrupt"
        game.players["P01"].elimination_round = 1
        game.players["P02"].cash = 200_000
        game.players["P03"].cash = 100_000
        game.players["P04"].cash = 100_000
        game._phase_market_open(1)
        assert "P01" not in game._current_leader_ids
        assert game._current_leader_ids == ["P02"]

    def test_single_survivor(self):
        game = _make_game(4)
        for pid in ("P02", "P03", "P04"):
            game.players[pid].is_alive = False
            game.players[pid].elimination_reason = "bankrupt"
            game.players[pid].elimination_round = 1
        game._phase_market_open(1)
        assert game._current_leader_ids == ["P01"]

    def test_leader_announced_event_data_keys_only_round_and_player_ids(self):
        """公示内容の秘匿: dataキーが round/player_ids のみ（金額・順位キーが無い）"""
        game = _make_game(4)
        game._phase_market_open(1)
        events = [e for e in game.logger.events if e.event_type == "LEADER_ANNOUNCED"]
        assert len(events) == 1
        assert set(events[0].data.keys()) == {"round", "player_ids"}

    def test_leader_announced_not_recorded_when_disabled(self):
        config = GameConfig.baseline_v1(4)
        assert config.leader_announce_enabled is False
        agents = {f"P{i+1:02d}": StubAgent() for i in range(4)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        game._phase_market_open(1)
        events = [e for e in game.logger.events if e.event_type == "LEADER_ANNOUNCED"]
        assert events == []
        assert game._current_leader_ids == []


# ---------------------------------------------------------------------------
# visible_state への反映
# ---------------------------------------------------------------------------

class TestVisibleState:
    def test_leader_ids_present_when_enabled(self):
        game = _make_game(4)
        game._phase_market_open(1)
        state = game._build_visible_state(1)
        assert state["leader_ids"] == game._current_leader_ids

    def test_leader_ids_key_absent_when_disabled(self):
        config = GameConfig.baseline_v1(4)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(4)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        game._phase_market_open(1)
        state = game._build_visible_state(1)
        assert "leader_ids" not in state


# ---------------------------------------------------------------------------
# プロンプト
# ---------------------------------------------------------------------------

class TestSystemPromptGating:
    def test_disabled_prompt_has_no_leader_announce_text(self):
        prompt = build_system_prompt("P01", _s1_config())
        assert LEADER_ANNOUNCE_RULES_LINE not in prompt
        assert "資産首位" not in prompt

    def test_enabled_prompt_has_leader_announce_text(self):
        prompt = build_system_prompt("P01", _s2_config())
        assert LEADER_ANNOUNCE_RULES_LINE in prompt

    def test_disabled_prompt_byte_identical_to_baseline(self):
        baseline = build_system_prompt("P01", GameConfig(type_c_enabled=False, leader_announce_enabled=False))
        no_leader = build_system_prompt("P01", GameConfig(type_c_enabled=False))
        assert baseline == no_leader


class TestNegotiationPromptLeaderBlock:
    def test_leader_id_appears_in_public_block(self):
        player = _make_player("P01")
        state = _base_visible_state(
            alive_players=["P01", "P02"], leader_ids=["P02"],
        )
        prompt = build_negotiation_prompt(player, 1, 1, state, _s2_config())
        assert "## 今ラウンドの資産首位: P02" in prompt

    def test_multiple_leaders_comma_joined(self):
        player = _make_player("P01")
        state = _base_visible_state(
            alive_players=["P01", "P02", "P03"], leader_ids=["P02", "P03"],
        )
        prompt = build_negotiation_prompt(player, 1, 1, state, _s2_config())
        assert "## 今ラウンドの資産首位: P02, P03" in prompt

    def test_self_notice_for_leader(self):
        player = _make_player("P01")
        state = _base_visible_state(alive_players=["P01", "P02"], leader_ids=["P01"])
        prompt = build_negotiation_prompt(player, 1, 1, state, _s2_config())
        assert "あなたは今ラウンドの資産首位として公示されています" in prompt

    def test_self_notice_absent_for_non_leader(self):
        player = _make_player("P01")
        state = _base_visible_state(alive_players=["P01", "P02"], leader_ids=["P02"])
        prompt = build_negotiation_prompt(player, 1, 1, state, _s2_config())
        assert "あなたは今ラウンドの資産首位として公示されています" not in prompt

    def test_self_notice_for_all_tied_leaders(self):
        """同率者全員に本人通知が出る"""
        state = _base_visible_state(
            alive_players=["P01", "P02", "P03"], leader_ids=["P01", "P02"],
        )
        for pid in ("P01", "P02"):
            player = _make_player(pid)
            prompt = build_negotiation_prompt(player, 1, 1, state, _s2_config())
            assert "あなたは今ラウンドの資産首位として公示されています" in prompt
        player3 = _make_player("P03")
        prompt3 = build_negotiation_prompt(player3, 1, 1, state, _s2_config())
        assert "あなたは今ラウンドの資産首位として公示されています" not in prompt3

    def test_no_leader_block_when_disabled(self):
        player = _make_player("P01")
        state = _base_visible_state(alive_players=["P01", "P02"], leader_ids=["P01"])
        prompt = build_negotiation_prompt(player, 1, 1, state, _s1_config())
        assert "資産首位" not in prompt

    def test_byte_identical_when_disabled_even_with_leader_ids_in_state(self):
        """OFF時、leader_idsを含むvisible_stateを渡してもバイト単位で同一"""
        player = _make_player("P01")
        state_with = _base_visible_state(alive_players=["P01", "P02"], leader_ids=["P01"])
        state_without = _base_visible_state(alive_players=["P01", "P02"])
        prompt_with = build_negotiation_prompt(player, 1, 1, state_with, _s1_config())
        prompt_without = build_negotiation_prompt(player, 1, 1, state_without, _s1_config())
        assert prompt_with == prompt_without

    def test_no_amounts_in_leader_block_lines(self):
        """首位公示ブロック・本人通知の行自体には金額（数字）が一切現れない
        （個別財務通知の「あなたの状態」欄はP01自身の情報であり本テストの対象外。
        本テストは _render_leader_block / _render_leader_self_notice が生成する
        行そのものに数値が混入していないことを確認する）
        """
        state = _base_visible_state(
            alive_players=["P01", "P02"], leader_ids=["P01", "P02"],
        )
        player1 = _make_player("P01", cash=123_456, debt=0)
        prompt = build_negotiation_prompt(player1, 1, 1, state, _s2_config())
        leader_block_line = next(
            line for line in prompt.splitlines() if line.startswith("## 今ラウンドの資産首位")
        )
        self_notice_line = next(
            line for line in prompt.splitlines()
            if "資産首位として公示されています" in line
        )
        # プレイヤーID（P01等）自体の数字は除外し、金額らしき3桁以上の数字列
        # （P直後を除く）や「円」の不在を確認する
        amount_pattern = re.compile(r"(?<!P)\d{3,}")
        assert amount_pattern.search(leader_block_line) is None
        assert amount_pattern.search(self_notice_line) is None
        assert "円" not in leader_block_line
        assert "円" not in self_notice_line


class TestForbiddenWordsScan:
    def test_no_suggestive_words_in_rules_line(self):
        for w in FORBIDDEN_WORDS:
            assert w not in LEADER_ANNOUNCE_RULES_LINE

    def test_no_suggestive_words_in_negotiation_block(self):
        player = _make_player("P01")
        state = _base_visible_state(alive_players=["P01", "P02"], leader_ids=["P01", "P02"])
        prompt = build_negotiation_prompt(player, 1, 1, state, _s2_config())
        for w in FORBIDDEN_WORDS:
            assert w not in prompt, f"禁止語 '{w}' がプロンプトに含まれている"


# ---------------------------------------------------------------------------
# ビューワー
# ---------------------------------------------------------------------------

class TestViewerLogParser:
    def _write_events(self, tmp_path: Path, events: list[dict]) -> tuple[Path, str, str]:
        trial_dir_name = "trial_test"
        game_id = "G1"
        trial_dir = tmp_path / trial_dir_name
        trial_dir.mkdir(parents=True, exist_ok=True)
        events_file = trial_dir / f"{game_id}_events.jsonl"
        with events_file.open("w", encoding="utf-8") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        return tmp_path, trial_dir_name, game_id

    def test_leader_ids_parsed_from_events(self):
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 1, "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 1, "data": {"player_id": "P02"}},
            {"event_type": "MARKET_OPEN", "round_num": 1, "data": {"markets": []}},
            {"event_type": "LEADER_ANNOUNCED", "round_num": 1,
             "data": {"round": 1, "player_ids": ["P01", "P02"]}},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir, trial_dir_name, game_id = self._write_events(Path(tmpdir), events)
            data_public = get_round_states(logs_dir, trial_dir_name, game_id, view="public")
            data_god = get_round_states(logs_dir, trial_dir_name, game_id, view="god")
        assert data_public["rounds"]["1"]["leader_ids"] == ["P01", "P02"]
        assert data_god["rounds"]["1"]["leader_ids"] == ["P01", "P02"]


# ---------------------------------------------------------------------------
# ハイライト H17
# ---------------------------------------------------------------------------

class TestHighlightH17:
    def test_leader_change_detected(self):
        events = [
            {"event_type": "LEADER_ANNOUNCED", "round_num": 1,
             "data": {"round": 1, "player_ids": ["P01"]}},
            {"event_type": "LEADER_ANNOUNCED", "round_num": 2,
             "data": {"round": 2, "player_ids": ["P02"]}},
        ]
        highlights = detect_highlights(events, [], "G1")
        h17 = [h for h in highlights if h["type"] == "H17"]
        assert len(h17) == 1
        assert h17[0]["round"] == 2

    def test_no_change_no_highlight(self):
        events = [
            {"event_type": "LEADER_ANNOUNCED", "round_num": 1,
             "data": {"round": 1, "player_ids": ["P01"]}},
            {"event_type": "LEADER_ANNOUNCED", "round_num": 2,
             "data": {"round": 2, "player_ids": ["P01"]}},
        ]
        highlights = detect_highlights(events, [], "G1")
        assert [h for h in highlights if h["type"] == "H17"] == []

    def test_order_change_within_tie_not_detected_as_change(self):
        """集合が同じなら要素の並び順違いは交代として検出しない"""
        events = [
            {"event_type": "LEADER_ANNOUNCED", "round_num": 1,
             "data": {"round": 1, "player_ids": ["P01", "P02"]}},
            {"event_type": "LEADER_ANNOUNCED", "round_num": 2,
             "data": {"round": 2, "player_ids": ["P02", "P01"]}},
        ]
        highlights = detect_highlights(events, [], "G1")
        assert [h for h in highlights if h["type"] == "H17"] == []

    def test_r1_not_a_candidate_for_change(self):
        """R1は比較対象が無いため対象外"""
        events = [
            {"event_type": "LEADER_ANNOUNCED", "round_num": 1,
             "data": {"round": 1, "player_ids": ["P01", "P02", "P03", "P04"]}},
        ]
        highlights = detect_highlights(events, [], "G1")
        assert [h for h in highlights if h["type"] == "H17"] == []


# ---------------------------------------------------------------------------
# 統合テスト（S2 Bot 1試合）
# ---------------------------------------------------------------------------

class TestIntegrationS2BotGame:
    def test_leader_announced_every_round(self):
        config = GameConfig.baseline_v1_s2(len(DEFAULT_ROSTER))
        assert config.leader_announce_enabled is True
        agents = {}
        for i, bot_name in enumerate(DEFAULT_ROSTER):
            pid = f"P{i + 1:02d}"
            bot_class = BOT_REGISTRY[bot_name]
            agents[pid] = bot_class(seed=42 * 100 + i)
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game.run()

        leader_events = [e for e in logger.events if e.event_type == "LEADER_ANNOUNCED"]
        rounds_with_leader = {e.round_num for e in leader_events}
        market_open_events = [e for e in logger.events if e.event_type == "MARKET_OPEN"]
        rounds_with_market_open = {e.round_num for e in market_open_events}
        # Market Openが開かれたラウンドは必ずLEADER_ANNOUNCEDが1件ある
        assert rounds_with_market_open <= rounds_with_leader
        for rnd in rounds_with_market_open:
            events_this_round = [e for e in leader_events if e.round_num == rnd]
            assert len(events_this_round) == 1
            assert len(events_this_round[0].data["player_ids"]) >= 1
