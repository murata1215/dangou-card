"""
v0.10 サイクル10.3: 自己順位通知のテスト

- 各ラウンドのMarket Openで、資産（現金+倍掛け預託-借金残高。首位公示10.2と
  同一定義・同一タイミング）から競技順位（同率は同順位、次は人数分スキップ）を
  計算し、本人にだけ「4位 / 6人」の形で通知する（config.rank_notice_enabled）
- 順位・資産額は本人以外には一切通知しない（公開されるのは首位のIDのみ＝10.2）
- 脱落者は順位・分母（生存者数）の両方から除外される
- 順位は交渉・コミット・倍掛け選択・振り返りの全4フェーズで再掲される
  （Market Open時点の値を使い回し、フェーズごとに再計算しない）
- rank_notice_enabled=False では既存挙動とバイト単位で完全一致する
"""
import json
import tempfile
from pathlib import Path

from bots import BOT_REGISTRY, DEFAULT_ROSTER
from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import DoubleUpDeposit, PlayerState
from engine.negotiation import StubAgent
from engine.player import AssetRank, assets_ranking, total_assets
from llm.prompt_builder import (
    build_commit_prompt,
    build_double_up_prompt,
    build_negotiation_prompt,
    build_reflection_prompt,
)
from tests.conftest import make_market
from tests.test_cycle5_prompt_salience import _base_visible_state, _make_player
from viewer.log_parser import get_round_states


def _make_game(num_players: int = 4) -> Game:
    config = GameConfig.baseline_v1_s2(num_players)
    agents = {f"P{i+1:02d}": StubAgent() for i in range(num_players)}
    game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
    game._setup()
    return game


def _s1_config() -> GameConfig:
    return GameConfig(rank_notice_enabled=False)


def _s2_config() -> GameConfig:
    return GameConfig(rank_notice_enabled=True)


def _p(pid: str, cash: int, debt: int = 0, alive: bool = True) -> PlayerState:
    p = PlayerState(player_id=pid, cash=cash, debt_balance=debt, initial_loan=debt, hand=[])
    if not alive:
        p = p.model_copy(update={"is_alive": False, "elimination_reason": "bankruptcy", "elimination_round": 1})
    return p


# ---------------------------------------------------------------------------
# assets_ranking() 純関数
# ---------------------------------------------------------------------------

class TestAssetsRanking:
    def test_normal_ranking(self):
        players = [_p("P01", 400_000), _p("P02", 300_000), _p("P03", 200_000), _p("P04", 100_000)]
        r = assets_ranking(players, [])
        assert r["P01"] == AssetRank(rank=1, tied=False, n_alive=4)
        assert r["P02"] == AssetRank(rank=2, tied=False, n_alive=4)
        assert r["P03"] == AssetRank(rank=3, tied=False, n_alive=4)
        assert r["P04"] == AssetRank(rank=4, tied=False, n_alive=4)

    def test_two_way_tie_skips_next_rank(self):
        """同額(500,300,300,100) → 1,2,2,4"""
        players = [_p("P01", 500_000), _p("P02", 300_000), _p("P03", 300_000), _p("P04", 100_000)]
        r = assets_ranking(players, [])
        assert r["P01"] == AssetRank(rank=1, tied=False, n_alive=4)
        assert r["P02"] == AssetRank(rank=2, tied=True, n_alive=4)
        assert r["P03"] == AssetRank(rank=2, tied=True, n_alive=4)
        assert r["P04"] == AssetRank(rank=4, tied=False, n_alive=4)

    def test_all_equal_everyone_rank1_tied(self):
        """R1形状: 全員同額 → 全員 rank=1, tied=True"""
        players = [_p(f"P0{i}", 300_000, 300_000) for i in range(1, 5)]
        r = assets_ranking(players, [])
        for pid in ("P01", "P02", "P03", "P04"):
            assert r[pid] == AssetRank(rank=1, tied=True, n_alive=4)

    def test_eliminated_excluded_from_rank_and_denominator(self):
        players = [_p("P01", 900_000, alive=False), _p("P02", 200_000), _p("P03", 100_000)]
        r = assets_ranking(players, [])
        assert "P01" not in r
        assert r["P02"] == AssetRank(rank=1, tied=False, n_alive=2)
        assert r["P03"] == AssetRank(rank=2, tied=False, n_alive=2)

    def test_single_survivor(self):
        players = [_p("P01", 900_000, alive=False), _p("P02", 200_000)]
        r = assets_ranking(players, [])
        assert r == {"P02": AssetRank(rank=1, tied=False, n_alive=1)}

    def test_no_alive_players_returns_empty(self):
        players = [_p("P01", 900_000, alive=False), _p("P02", 200_000, alive=False)]
        assert assets_ranking(players, []) == {}

    def test_uses_total_assets_definition_deposit_raises_rank(self):
        """未解決の倍掛け預託は資産に加算され、順位に反映される"""
        players = [_p("P01", 100_000), _p("P02", 150_000)]
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=100_000,
            deposited_round=1, success_round=2, resolved=False,
        )
        r = assets_ranking(players, [dep])
        # P01: 100,000+100,000=200,000 > P02: 150,000
        assert r["P01"].rank == 1
        assert r["P02"].rank == 2

    def test_debt_excess_pushes_to_bottom(self):
        players = [_p("P01", 10_000, debt=50_000), _p("P02", 100_000)]
        r = assets_ranking(players, [])
        assert r["P02"].rank == 1
        assert r["P01"].rank == 2

    def test_order_independent(self):
        a = [_p("P01", 500_000), _p("P04", 500_000), _p("P02", 100_000), _p("P03", 100_000)]
        b = [_p("P04", 500_000), _p("P01", 500_000), _p("P03", 100_000), _p("P02", 100_000)]
        ra = assets_ranking(a, [])
        rb = assets_ranking(b, [])
        assert ra == rb

    def test_asset_rank_has_no_amount_field(self):
        """金額漏洩の構造的ガード"""
        assert set(AssetRank._fields) == {"rank", "tied", "n_alive"}


# ---------------------------------------------------------------------------
# Market Open 判定 / RANK_NOTIFIED イベント
# ---------------------------------------------------------------------------

class TestMarketOpenRankEvents:
    def test_event_count_matches_survivor_count(self):
        game = _make_game(4)
        game.players["P04"].is_alive = False
        game.players["P04"].elimination_reason = "bankruptcy"
        game.players["P04"].elimination_round = 1
        game._phase_market_open(1)
        events = [e for e in game.logger.events if e.event_type == "RANK_NOTIFIED"]
        assert len(events) == 3

    def test_event_data_keys_exactly_five(self):
        game = _make_game(4)
        game._phase_market_open(1)
        events = [e for e in game.logger.events if e.event_type == "RANK_NOTIFIED"]
        for e in events:
            assert set(e.data.keys()) == {"round", "player_id", "rank", "tied", "n_alive"}

    def test_event_values_match_assets_ranking(self):
        game = _make_game(4)
        game.players["P01"].cash = 900_000
        game._phase_market_open(1)
        expected = assets_ranking(game.players.values(), game.double_up_deposits)
        events = {
            e.data["player_id"]: e.data
            for e in game.logger.events if e.event_type == "RANK_NOTIFIED"
        }
        for pid, r in expected.items():
            assert events[pid]["rank"] == r.rank
            assert events[pid]["tied"] == r.tied
            assert events[pid]["n_alive"] == r.n_alive

    def test_no_events_for_eliminated_player(self):
        game = _make_game(4)
        game.players["P04"].is_alive = False
        game.players["P04"].elimination_reason = "bankruptcy"
        game.players["P04"].elimination_round = 1
        game._phase_market_open(1)
        pids = {e.data["player_id"] for e in game.logger.events if e.event_type == "RANK_NOTIFIED"}
        assert "P04" not in pids

    def test_disabled_zero_events_and_empty_state(self):
        config = GameConfig.baseline_v1(4)
        assert config.rank_notice_enabled is False
        agents = {f"P{i+1:02d}": StubAgent() for i in range(4)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        game._phase_market_open(1)
        events = [e for e in game.logger.events if e.event_type == "RANK_NOTIFIED"]
        assert events == []
        assert game._current_rank_by_player == {}

    def test_rank1_set_matches_leader_ids(self):
        """rank==1の集合がleader_announceのcurrent_leader_idsと一致する"""
        game = _make_game(4)
        game.players["P01"].cash = 900_000
        game.players["P02"].cash = 900_000
        game._phase_market_open(1)
        rank1 = {pid for pid, r in game._current_rank_by_player.items() if r.rank == 1}
        assert rank1 == set(game._current_leader_ids)

    def test_leader_announced_before_rank_notified(self):
        game = _make_game(4)
        game._phase_market_open(1)
        types = [e.event_type for e in game.logger.events]
        leader_idx = types.index("LEADER_ANNOUNCED")
        rank_idx = types.index("RANK_NOTIFIED")
        assert leader_idx < rank_idx


# ---------------------------------------------------------------------------
# visible_state への反映
# ---------------------------------------------------------------------------

class TestVisibleStateMyRank:
    def test_my_rank_present_for_self(self):
        game = _make_game(4)
        game._phase_market_open(1)
        state = game._build_visible_state(1, for_player_id="P01")
        expected = game._current_rank_by_player["P01"]
        assert state["my_rank"] == {
            "rank": expected.rank, "tied": expected.tied, "n_alive": expected.n_alive,
        }

    def test_my_rank_absent_in_public_state(self):
        """for_player_id=Noneの公開stateには絶対に出ない（秘匿の中核）"""
        game = _make_game(4)
        game._phase_market_open(1)
        state = game._build_visible_state(1)
        assert "my_rank" not in state

    def test_my_rank_absent_when_disabled(self):
        config = GameConfig.baseline_v1(4)
        agents = {f"P{i+1:02d}": StubAgent() for i in range(4)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        game._phase_market_open(1)
        state = game._build_visible_state(1, for_player_id="P01")
        assert "my_rank" not in state

    def test_my_rank_absent_for_eliminated_player(self):
        game = _make_game(4)
        game.players["P04"].is_alive = False
        game.players["P04"].elimination_reason = "bankruptcy"
        game.players["P04"].elimination_round = 1
        game._phase_market_open(1)
        state = game._build_visible_state(1, for_player_id="P04")
        assert "my_rank" not in state

    def test_my_rank_has_exactly_three_keys(self):
        game = _make_game(4)
        game._phase_market_open(1)
        state = game._build_visible_state(1, for_player_id="P01")
        assert set(state["my_rank"].keys()) == {"rank", "tied", "n_alive"}


# ---------------------------------------------------------------------------
# プロンプト（4フェーズ共通）
# ---------------------------------------------------------------------------

REMINDER_LINE = "  目標: 生還者の中で最終資産1位（生還は最低条件）"


class TestNegotiationPromptRankNotice:
    def test_solo_rank_line(self):
        player = _make_player("P01")
        state = _base_visible_state(
            alive_players=["P01"], my_rank={"rank": 1, "tied": False, "n_alive": 1},
        )
        prompt = build_negotiation_prompt(player, 1, 1, state, _s2_config())
        assert "  あなたの現在順位: 1位 / 1人" in prompt

    def test_tied_rank_line(self):
        player = _make_player("P01")
        state = _base_visible_state(
            alive_players=["P01", "P02"], my_rank={"rank": 2, "tied": True, "n_alive": 6},
        )
        prompt = build_negotiation_prompt(player, 1, 1, state, _s2_config())
        assert "  あなたの現在順位: 同率2位 / 6人" in prompt

    def test_solo_of_six(self):
        player = _make_player("P01")
        state = _base_visible_state(
            alive_players=["P01"], my_rank={"rank": 4, "tied": False, "n_alive": 6},
        )
        prompt = build_negotiation_prompt(player, 1, 1, state, _s2_config())
        assert "  あなたの現在順位: 4位 / 6人" in prompt

    def test_reminder_line_present(self):
        player = _make_player("P01")
        state = _base_visible_state()
        prompt = build_negotiation_prompt(player, 1, 1, state, _s1_config())
        assert REMINDER_LINE in prompt

    def test_rank_absent_when_disabled(self):
        player = _make_player("P01")
        state = _base_visible_state(my_rank={"rank": 1, "tied": False, "n_alive": 1})
        prompt = build_negotiation_prompt(player, 1, 1, state, _s1_config())
        assert "現在順位" not in prompt
        # リマインドはフラグ非依存で出る
        assert REMINDER_LINE in prompt

    def test_only_one_rank_mention_and_no_other_player_rank(self):
        player = _make_player("P01")
        state = _base_visible_state(
            alive_players=["P01", "P02"], my_rank={"rank": 3, "tied": False, "n_alive": 6},
        )
        prompt = build_negotiation_prompt(player, 1, 1, state, _s2_config())
        assert prompt.count("現在順位") == 1
        assert "P02" not in [
            w for line in prompt.splitlines() if "現在順位" in line for w in line.split()
        ] or True  # 順位行自体にP02が現れないことは下記で厳密に確認
        rank_line = next(line for line in prompt.splitlines() if "現在順位" in line)
        assert "P02" not in rank_line

    def test_no_amount_in_rank_line(self):
        player = _make_player("P01", cash=123_456)
        state = _base_visible_state(my_rank={"rank": 2, "tied": False, "n_alive": 5})
        prompt = build_negotiation_prompt(player, 1, 1, state, _s2_config())
        rank_line = next(line for line in prompt.splitlines() if "現在順位" in line)
        assert "円" not in rank_line


class TestFourPhaseRankNotice:
    """4フェーズ（交渉/コミット/倍掛け/振り返り）すべてに順位+リマインドが出る"""

    def test_negotiation(self):
        player = _make_player("P01")
        state = _base_visible_state(my_rank={"rank": 2, "tied": False, "n_alive": 6})
        prompt = build_negotiation_prompt(player, 1, 1, state, _s2_config())
        assert "  あなたの現在順位: 2位 / 6人" in prompt
        assert REMINDER_LINE in prompt

    def test_commit(self):
        player = _make_player("P01")
        state = _base_visible_state(my_rank={"rank": 2, "tied": False, "n_alive": 6})
        prompt = build_commit_prompt(
            player, [make_market("M01", 500_000)], 1, state, _s2_config(),
        )
        assert "  あなたの現在順位: 2位 / 6人" in prompt
        assert REMINDER_LINE in prompt

    def test_double_up(self):
        player = _make_player("P01")
        state = _base_visible_state(my_rank={"rank": 2, "tied": False, "n_alive": 6})
        prompt = build_double_up_prompt(player, 200_000, 1, state, _s2_config())
        assert "  あなたの現在順位: 2位 / 6人" in prompt
        assert REMINDER_LINE in prompt

    def test_reflection(self):
        player = _make_player("P01")
        state = _base_visible_state(my_rank={"rank": 2, "tied": False, "n_alive": 6})
        prompt = build_reflection_prompt(player, 1, state, _s2_config())
        assert "  あなたの現在順位: 2位 / 6人" in prompt
        assert REMINDER_LINE in prompt

    def test_all_four_absent_rank_when_disabled(self):
        player = _make_player("P01")
        state = _base_visible_state(my_rank={"rank": 2, "tied": False, "n_alive": 6})
        config = _s1_config()
        prompts = {
            "negotiation": build_negotiation_prompt(player, 1, 1, state, config),
            "commit": build_commit_prompt(
                player, [make_market("M01", 500_000)], 1, state, config,
            ),
            "double_up": build_double_up_prompt(player, 200_000, 1, state, config),
            "reflection": build_reflection_prompt(player, 1, state, config),
        }
        for name, prompt in prompts.items():
            assert "現在順位" not in prompt, f"{name} に順位が漏洩している"
            assert REMINDER_LINE in prompt, f"{name} にリマインドが無い"


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

    def _events(self) -> list[dict]:
        return [
            {"event_type": "LOAN_CHOSEN", "round_num": 1, "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 1, "data": {"player_id": "P02"}},
            {"event_type": "MARKET_OPEN", "round_num": 1, "data": {"markets": []}},
            {"event_type": "RANK_NOTIFIED", "round_num": 1,
             "data": {"round": 1, "player_id": "P01", "rank": 1, "tied": False, "n_alive": 2}},
            {"event_type": "RANK_NOTIFIED", "round_num": 1,
             "data": {"round": 1, "player_id": "P02", "rank": 2, "tied": False, "n_alive": 2}},
        ]

    def test_public_ranks_empty(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir, trial_dir_name, game_id = self._write_events(Path(tmpdir), self._events())
            data = get_round_states(logs_dir, trial_dir_name, game_id, view="public")
        assert data["rounds"]["1"]["ranks"] == {}

    def test_god_ranks_populated(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            logs_dir, trial_dir_name, game_id = self._write_events(Path(tmpdir), self._events())
            data = get_round_states(logs_dir, trial_dir_name, game_id, view="god")
        assert data["rounds"]["1"]["ranks"]["P01"] == {"rank": 1, "tied": False, "n_alive": 2}
        assert data["rounds"]["1"]["ranks"]["P02"] == {"rank": 2, "tied": False, "n_alive": 2}


# ---------------------------------------------------------------------------
# 統合テスト（S2 Bot 1試合）
# ---------------------------------------------------------------------------

class TestIntegrationS2BotGame:
    def test_rank_notified_every_round(self):
        config = GameConfig.baseline_v1_s2(len(DEFAULT_ROSTER))
        assert config.rank_notice_enabled is True
        agents = {}
        for i, bot_name in enumerate(DEFAULT_ROSTER):
            pid = f"P{i + 1:02d}"
            bot_class = BOT_REGISTRY[bot_name]
            agents[pid] = bot_class(seed=42 * 100 + i)
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game.run()

        rank_events = [e for e in logger.events if e.event_type == "RANK_NOTIFIED"]
        market_open_events = [e for e in logger.events if e.event_type == "MARKET_OPEN"]
        rounds_with_market_open = {e.round_num for e in market_open_events}
        for rnd in rounds_with_market_open:
            events_this_round = [e for e in rank_events if e.round_num == rnd]
            ranks = [e.data["rank"] for e in events_this_round]
            assert len(ranks) >= 1
            assert 1 in ranks
            assert max(ranks) <= len(ranks)

    def test_presets(self):
        assert GameConfig.baseline_v1_s2(12).rank_notice_enabled is True
        assert GameConfig.default_8_s2().rank_notice_enabled is True
        assert GameConfig().rank_notice_enabled is False
        assert GameConfig.baseline_v1(12).rank_notice_enabled is False
