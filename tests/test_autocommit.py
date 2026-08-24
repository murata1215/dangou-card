"""
Cycle 1 Phase 2/3: AUTO COMMITの理由記録・本人通知・秘匿境界の回帰テスト (T-A1〜T-A12)

背景（§1.5監査 trial_C_l12_r12_20260822）:
AUTO COMMIT 4件全てが「本人が指定しようとしたカードが手札に無かった」ことに起因し、
かつ「AUTOだった事実」が本人にも他プレイヤーにも一切通知されていなかった
（P09 R3→R4で同じ間違いを反復、P06 R7は「約束を破った」と誤認された）。

本ファイルは以下の実装（engine/game.py A-1〜A-3,A-6 / A-4 visible_state /
llm/prompt_builder.py A-5 / llm/llm_agent.py A-8）を検証する。
アルゴリズム本体（engine/autocommit.py の compute_legal_commits/select_auto_commit）は
一切変更されていないことを前提とし、そのことも回帰確認する（T-A11）。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import StubAgent
from engine.models import (
    Card, CardRank, Market, MarketCommit,
    Contract, ContractStatus, Obligation, ObligationType,
)
from engine import autocommit as autocommit_ops

from llm.prompt_builder import build_negotiation_prompt

from tests.conftest import make_market


def _make_game(num_players: int = 4) -> Game:
    config = GameConfig.baseline_v1(num_players).model_copy(
        update={"num_rounds": 5},
    )
    agents = {f"P{i+1:02d}": StubAgent() for i in range(num_players)}
    game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
    game._setup()
    game._phase_market_open(1)
    return game


def _hand_without(*excluded_ranks: CardRank) -> list[Card]:
    """create_deck()相当だが指定ランクを除いた手札を作る（未保有カード再現用）"""
    cards: list[Card] = []
    for rank in CardRank:
        if rank in excluded_ranks:
            continue
        n = 2 if rank in (CardRank.HIGH_CARD, CardRank.ONE_PAIR) else 1
        for i in range(1, n + 1):
            cards.append(Card(rank=rank, card_id=f"{rank.name}_{i}"))
    return cards


class ScriptedAgent(StubAgent):
    """commit()の返り値/例外を差し替えられるテスト用エージェント

    LLMAgent同様に auto_commit_count / note_auto_commit() を実装し、
    validation失敗経路での加算(A-8)を検証できるようにする。
    """

    def __init__(self, commit_fn=None):
        self._commit_fn = commit_fn
        self.auto_commit_count = 0
        self.noted_reasons: list[str | None] = []

    def commit(self, player_state, markets, round_num, visible_state):
        if self._commit_fn is not None:
            return self._commit_fn(player_state, markets, round_num, visible_state)
        return super().commit(player_state, markets, round_num, visible_state)

    def note_auto_commit(self, reason: str | None) -> None:
        self.auto_commit_count += 1
        self.noted_reasons.append(reason)


def _events(game: Game, event_type: str, player_id: str | None = None) -> list:
    out = []
    for e in game.logger.events:
        if e.event_type != event_type:
            continue
        if player_id is not None and e.data.get("player_id") != player_id:
            continue
        out.append(e)
    return out


# =====================================================================
# T-A1: 正常commit
# =====================================================================

class TestNormalCommit:
    def test_no_auto_event_and_auto_false(self):
        game = _make_game()
        # StubAgent: 最低ランクカード+最低賞金市場を正常に選ぶ
        game._phase_commit(1)

        commit_events = _events(game, "COMMIT", "P01")
        assert len(commit_events) == 1
        assert commit_events[0].data["auto"] is False
        assert _events(game, "AUTO_COMMIT", "P01") == []
        assert _events(game, "AUTO_COMMIT_FAILURE", "P01") == []
        assert "P01" not in game._current_auto_pids

    def test_no_auto_block_in_next_round_prompt(self):
        game = _make_game()
        game._phase_commit(1)
        state = game._build_visible_state(2, for_player_id="P01")
        assert state["my_auto_commits"] == []


# =====================================================================
# T-A2: 未保有カード指定 → AUTO_COMMIT
# =====================================================================

class TestAutoCommitOnMissingCard:
    def test_auto_commit_recorded_with_requested_and_reason(self):
        game = _make_game()
        game.players["P01"] = game.players["P01"].model_copy(
            update={"hand": _hand_without(CardRank.ONE_PAIR)},
        )
        market_id = game._current_markets[0].market_id

        def commit_fn(player_state, markets, round_num, visible_state):
            from engine.models import MarketCommitAction
            return MarketCommitAction(
                player_id=player_state.player_id,
                market_id=market_id, card_rank="ONE_PAIR",
            )

        game.agents["P01"] = ScriptedAgent(commit_fn)
        game._phase_commit(1)

        auto_events = _events(game, "AUTO_COMMIT", "P01")
        assert len(auto_events) == 1
        d = auto_events[0].data
        assert d["requested_market_id"] == market_id
        assert d["requested_card_rank"] == "ONE_PAIR"
        assert d["reason"] == "Card ONE_PAIR not in hand"
        assert d["actual_market_id"] is not None
        assert d["actual_card"] is not None

        commit_events = _events(game, "COMMIT", "P01")
        assert commit_events[0].data["auto"] is True
        assert "P01" in game._current_auto_pids


# =====================================================================
# T-A3: agentが例外 → no_valid_response
# =====================================================================

class TestAutoCommitOnAgentException:
    def test_reason_is_no_valid_response_and_requested_is_none(self):
        game = _make_game()

        def commit_fn(player_state, markets, round_num, visible_state):
            raise RuntimeError("boom")

        agent = ScriptedAgent(commit_fn)
        game.agents["P01"] = agent
        game._phase_commit(1)

        auto_events = _events(game, "AUTO_COMMIT", "P01")
        assert len(auto_events) == 1
        d = auto_events[0].data
        assert d["reason"] == "no_valid_response"
        assert d["requested_market_id"] is None
        assert d["requested_card_rank"] is None

        # commit_action が None（例外経路）のため、engine側からの
        # note_auto_commit() 二重加算は起きない（A-8: agent.commit()内部で
        # 既に加算済みという想定のLLMAgentと同じ契約）
        assert agent.auto_commit_count == 0


# =====================================================================
# T-A4: market_idの妥当性検証は現状スコープ外（validate_actionはcard_rankのみ検証）
# =====================================================================

class TestMarketIdNotValidated:
    """
    engine/actions.py の MarketCommitAction 検証は card_rank の手札所持のみを
    見ており、market_id が当該ラウンドの実在市場かどうかは検証しない
    （Cycle 1では engine/actions.py を変更対象としていないため、この既知の
    仕様境界を現状のまま固定するテスト）。
    """

    def test_invalid_market_id_with_valid_card_is_accepted_as_normal_commit(self):
        game = _make_game()
        from engine.models import MarketCommitAction

        def commit_fn(player_state, markets, round_num, visible_state):
            lowest = min(player_state.hand, key=lambda c: c.rank.value)
            return MarketCommitAction(
                player_id=player_state.player_id,
                market_id="M99_NONEXISTENT", card_rank=lowest.rank.name,
            )

        game.agents["P01"] = ScriptedAgent(commit_fn)
        game._phase_commit(1)

        commit_events = _events(game, "COMMIT", "P01")
        assert len(commit_events) == 1
        assert commit_events[0].data["auto"] is False
        assert commit_events[0].data["market_id"] == "M99_NONEXISTENT"
        assert _events(game, "AUTO_COMMIT", "P01") == []


# =====================================================================
# T-A5: visible_state["my_auto_commits"]
# =====================================================================

class TestMyAutoCommitsVisibleState:
    def test_contains_requested_actual_reason(self):
        game = _make_game()
        game.players["P01"] = game.players["P01"].model_copy(
            update={"hand": _hand_without(CardRank.ONE_PAIR)},
        )
        market_id = game._current_markets[0].market_id

        def commit_fn(player_state, markets, round_num, visible_state):
            from engine.models import MarketCommitAction
            return MarketCommitAction(
                player_id=player_state.player_id,
                market_id=market_id, card_rank="ONE_PAIR",
            )

        game.agents["P01"] = ScriptedAgent(commit_fn)
        game._phase_commit(1)

        state = game._build_visible_state(2, for_player_id="P01")
        history = state["my_auto_commits"]
        assert len(history) == 1
        h = history[0]
        assert h["round"] == 1
        assert h["requested_market_id"] == market_id
        assert h["requested_card_rank"] == "ONE_PAIR"
        assert h["reason"] == "Card ONE_PAIR not in hand"
        assert h["actual_market_id"] is not None
        assert h["actual_card"] is not None
        assert h["failure"] is False


# =====================================================================
# T-A6: 次ラウンドprompt に事実として反復防止情報が出る
# =====================================================================

class TestAutoCommitFactNotificationInPrompt:
    def test_negotiation_prompt_shows_requested_and_actual(self):
        game = _make_game()
        game.players["P01"] = game.players["P01"].model_copy(
            update={"hand": _hand_without(CardRank.ONE_PAIR)},
        )
        market_id = game._current_markets[0].market_id

        def commit_fn(player_state, markets, round_num, visible_state):
            from engine.models import MarketCommitAction
            return MarketCommitAction(
                player_id=player_state.player_id,
                market_id=market_id, card_rank="ONE_PAIR",
            )

        game.agents["P01"] = ScriptedAgent(commit_fn)
        game._phase_commit(1)

        state = game._build_visible_state(2, for_player_id="P01")
        player_state = game.players["P01"]
        prompt = build_negotiation_prompt(player_state, 2, 1, state, game.config)

        assert "自動代行コミット" in prompt
        assert f"R1: あなたが指定したのは「{market_id} / ONE_PAIR」でした。" in prompt
        assert "却下理由: Card ONE_PAIR not in hand" in prompt
        assert "システムが提出したのは" in prompt


# =====================================================================
# T-A7: 公開情報は【AUTO COMMIT】タグのみ（理由・requestedは非公開）
# =====================================================================

class TestPublicAutoTagOnly:
    def test_other_player_sees_tag_but_not_reason_or_requested(self):
        game = _make_game()
        game.players["P01"] = game.players["P01"].model_copy(
            update={"hand": _hand_without(CardRank.ONE_PAIR)},
        )
        market_id = game._current_markets[0].market_id

        def commit_fn(player_state, markets, round_num, visible_state):
            from engine.models import MarketCommitAction
            return MarketCommitAction(
                player_id=player_state.player_id,
                market_id=market_id, card_rank="ONE_PAIR",
            )

        game.agents["P01"] = ScriptedAgent(commit_fn)
        game._phase_commit(1)
        # 他プレイヤーもcommitさせ、市場決着まで通す（last_round_resultsの生成に必要）
        for pid in ("P02", "P03", "P04"):
            pass  # StubAgentは既に_phase_commit(1)内で処理済み

        # last_round_results を持つのは settlement 後。ここでは直接
        # last_round_auto_players / commits の auto フラグ経路（A-6）を検証する。
        state_p02 = game._build_visible_state(1, for_player_id="P02")
        # my_auto_commits は本人のみ（他人には常に空）
        assert state_p02["my_auto_commits"] == []

        # last_round_auto_players（公開）: 直接 _last_round_results を差し込んで検証
        game._last_round_results = {
            "round": 1,
            "markets": [
                {
                    "market_id": market_id,
                    "participants": ["P01"],
                    "commits": [
                        {"player_id": "P01", "card_rank": "HIGH_CARD", "auto": True},
                    ],
                    "winners": ["P01"],
                    "prize_per_winner": 100,
                    "total_pool": 100,
                    "surged": False,
                    "carryover_to_next": 0,
                }
            ],
        }
        state_p02 = game._build_visible_state(2, for_player_id="P02")
        assert state_p02["last_round_auto_players"] == ["P01"]

        prompt = build_negotiation_prompt(
            game.players["P02"], 2, 1, state_p02, game.config,
        )
        assert "【AUTO COMMIT】" in prompt
        # 理由・requestedは他プレイヤーのプロンプトに一切出ない
        # （ONE_PAIRはカード構成の一般名としてRULES_SUMMARYに常に出現するため、
        # 「本人が指定しようとした」旨のブロック自体が無いことで漏洩なしを確認する）
        assert "却下理由" not in prompt
        assert "あなたが指定したのは" not in prompt
        assert "自動代行コミット（AUTO COMMIT）の記録" not in prompt


# =====================================================================
# T-A8/T-A9: 型b義務の矛盾で合法commit0 → AUTO_COMMIT_FAILURE
# =====================================================================

class TestAutoCommitFailure:
    def test_legal_commit_zero_triggers_failure_with_diagnostics(self):
        game = _make_game()
        game.players["P01"] = game.players["P01"].model_copy(
            update={"hand": _hand_without(CardRank.FULL_HOUSE)},
        )
        ob = Obligation(
            obligation_id="C_test_OB01", contract_id="C_test",
            obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_B_CARD, round_num=1,
            details={"card_rank": "FULL_HOUSE"},
        )
        game.contracts = [Contract(
            contract_id="C_test", proposer="P02", parties=["P01", "P02"],
            signed_by=["P01", "P02"], obligations=[ob], round_created=1,
            status=ContractStatus.ACTIVE,
        )]
        market_id = game._current_markets[0].market_id

        def commit_fn(player_state, markets, round_num, visible_state):
            from engine.models import MarketCommitAction
            # 手札に無いFULL_HOUSEを指定 → validate_action失敗 → AUTO経路へ
            return MarketCommitAction(
                player_id=player_state.player_id,
                market_id=market_id, card_rank="FULL_HOUSE",
            )

        game.agents["P01"] = ScriptedAgent(commit_fn)
        game._phase_commit(1)

        failure_events = _events(game, "AUTO_COMMIT_FAILURE", "P01")
        assert len(failure_events) == 1
        d = failure_events[0].data
        assert d["legal_commit_count"] == 0
        assert d["failure_detail"] == (
            "No legal commits available (contradictory contracts)"
        )
        assert d["blocking_obligations"]
        assert d["requested_market_id"] == market_id
        assert d["requested_card_rank"] == "FULL_HOUSE"
        # A-2回帰: reason は既存互換で contract_violation のまま（失敗理由に
        # 上書きされて握り潰されない）
        assert d["reason"] == "contract_violation"

        # 脱落している
        assert game.players["P01"].is_alive is False
        assert game.players["P01"].elimination_reason == "contract_violation"

        # 秘匿履歴にも failure=True で残る
        state = game._build_visible_state(2, for_player_id="P01")
        h = state["my_auto_commits"][0]
        assert h["failure"] is True
        assert h["actual_market_id"] is None
        assert h["actual_card"] is None


# =====================================================================
# T-A10: 秘匿境界の機械検証
# =====================================================================

class TestSecrecyNoLeak:
    def test_other_players_prompt_never_leaks_requested_or_reason(self):
        game = _make_game()
        game.players["P01"] = game.players["P01"].model_copy(
            update={"hand": _hand_without(CardRank.ONE_PAIR)},
        )
        market_id = game._current_markets[0].market_id

        def commit_fn(player_state, markets, round_num, visible_state):
            from engine.models import MarketCommitAction
            return MarketCommitAction(
                player_id=player_state.player_id,
                market_id=market_id, card_rank="ONE_PAIR",
            )

        game.agents["P01"] = ScriptedAgent(commit_fn)
        game._phase_commit(1)

        for pid in game.players:
            if pid == "P01":
                continue
            state = game._build_visible_state(2, for_player_id=pid)
            assert state["my_auto_commits"] == []
            prompt = build_negotiation_prompt(
                game.players[pid], 2, 1, state, game.config,
            )
            assert "自動代行コミット" not in prompt
            assert "却下理由" not in prompt


# =====================================================================
# T-A11: select_auto_commit()の選択結果スナップショット（アルゴリズム非変更）
# =====================================================================

class TestSelectAutoCommitAlgorithmUnchanged:
    def test_selection_priority_rank_then_prize_then_market_id(self):
        cards = [
            Card(rank=CardRank.TWO_PAIR, card_id="TWO_PAIR_1"),
            Card(rank=CardRank.HIGH_CARD, card_id="HIGH_CARD_1"),
            Card(rank=CardRank.HIGH_CARD, card_id="HIGH_CARD_2"),
        ]
        markets = [
            make_market("M02", base_prize=500_000),
            make_market("M01", base_prize=500_000),
        ]
        legal = [
            MarketCommit(player_id="P01", market_id=m.market_id, card=c)
            for m in markets for c in cards
        ]
        selected = autocommit_ops.select_auto_commit(legal, markets)
        assert selected is not None
        # 最低ランク(HIGH_CARD)・同ランク内は最低賞金市場が同額のためmarket_id昇順
        assert selected.card.rank == CardRank.HIGH_CARD
        assert selected.market_id == "M01"

    def test_empty_legal_returns_none(self):
        assert autocommit_ops.select_auto_commit([], []) is None


# =====================================================================
# T-A12: validation失敗経路でも auto_commit_count が加算される（A-8）
# =====================================================================

class TestNoteAutoCommitCounter:
    def test_validation_failure_increments_counter(self):
        game = _make_game()
        game.players["P01"] = game.players["P01"].model_copy(
            update={"hand": _hand_without(CardRank.ONE_PAIR)},
        )
        market_id = game._current_markets[0].market_id

        def commit_fn(player_state, markets, round_num, visible_state):
            from engine.models import MarketCommitAction
            return MarketCommitAction(
                player_id=player_state.player_id,
                market_id=market_id, card_rank="ONE_PAIR",
            )

        agent = ScriptedAgent(commit_fn)
        game.agents["P01"] = agent
        game._phase_commit(1)

        assert agent.auto_commit_count == 1
        assert agent.noted_reasons == ["Card ONE_PAIR not in hand"]

    def test_agent_exception_path_does_not_double_count(self):
        """commit_action が None（agent例外）の場合、
        engine側の note_auto_commit() は呼ばれない
        （LLMAgent側は自身の例外ハンドラで既にカウント済みという契約のため）"""
        game = _make_game()

        def commit_fn(player_state, markets, round_num, visible_state):
            raise RuntimeError("boom")

        agent = ScriptedAgent(commit_fn)
        game.agents["P01"] = agent
        game._phase_commit(1)

        assert agent.auto_commit_count == 0
