"""
Cycle 3: anonymous_broadcast / bounty / card_trade 利用経路監査 — 修正検証テスト

R12実測（logs/llm/trial_C_l12_r12_20260822/）から、3機能とも
「存在は知れる/存在は知っている」段階までは到達していたが、
- anonymous_broadcast: JSON形式がaction一覧に無い（PROMPT_DISCOVERY_GAP）＋
  Free Cash非適用の再掲が無く誤読された（MODEL_ERROR）＋
  費用表示が誤った設定値変数を参照していた（表示参照バグ）
- bounty: 有効な報奨一覧・bounty_cancel形式がPromptに存在せず、
  IDを取得できない（MISSING_ACTIONABLE_STATE / PROMPT_DISCOVERY_GAP）
- card_trade: 経路は健全。P07 R7の失敗はAI側のACTION_CONSTRUCTION_ERROR
  （上限5人指定に対し7人指定）で、当時のFEEDBACK_GAPはcommit 49071e4で
  試合翌日に解消済み（RESOLVED_POST_TRIAL）

という原因が判明した（Cycle 3 Plan Phase A〜C）。
本ファイルは Phase E の確定修正（E1/E2/E3/E4-a/E4-b）と、
D1/D3で要検証とされた項目のAPI-free回帰を検証する。

engine/ は一切変更していない。llm/prompt_builder.py のみが変更対象。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.models import AnonymousBroadcastAction, CardTradeProposeAction
from engine.negotiation import StubAgent

from llm.prompt_builder import build_system_prompt, build_negotiation_prompt

from tests.conftest import make_player


def _make_game(num_players: int = 3, seed: int = 42, config: GameConfig | None = None) -> Game:
    cfg = config or GameConfig.baseline_v1_s2(num_players)
    agents = {f"P{i+1:02d}": StubAgent() for i in range(num_players)}
    return Game(config=cfg, agents=agents, seed=seed, logger=EventLogger())


def _base_negotiation_state(**overrides) -> dict:
    state = {
        "round_num": 4,
        "markets": [{"market_id": "M01", "prize_pool": 480_000,
                     "base_prize": 480_000, "carryover": 0}],
        "last_round_results": None,
        "alive_players": ["P01", "P02", "P03"],
        "messages": [],
        "contracts_pending": [],
        "trades_pending": [],
        "bounties_public": [],
    }
    state.update(overrides)
    return state


# =============================================================================
# E1: anonymous_broadcast のJSON形式がaction一覧に含まれる
# =============================================================================

class TestE1AnonymousBroadcastFormat:
    def test_prompt_anonymous_broadcast_format(self):
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_system_prompt("P01", config)
        assert '"type": "anonymous_broadcast"' in prompt


# =============================================================================
# E2: 有効な bounties_public が negotiation prompt に描画される
# =============================================================================

class TestE2BountiesPublicRendered:
    def test_prompt_bounties_public_rendered(self):
        state = _base_negotiation_state(bounties_public=[
            {
                "bounty_id": "B_abc12345",
                "amount": 500_000,
                "condition_type": "market_win_against",
                "condition": {"target_player": "P07"},
                "poster": "P01",
                "is_active": True,
            },
        ])
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_negotiation_prompt(player, 4, 1, state, config)

        assert "## 有効な公開報奨" in prompt
        assert "B_abc12345" in prompt
        assert "50万円" in prompt
        assert "market_win_against" in prompt
        assert "P07" in prompt

    def test_prompt_bounties_public_hides_inactive(self):
        """is_active=Falseの報奨（取り下げ済み）は一覧に出さない"""
        state = _base_negotiation_state(bounties_public=[
            {
                "bounty_id": "B_cancelled1",
                "amount": 500_000,
                "condition_type": "market_win_against",
                "condition": {"target_player": "P07"},
                "poster": "P01",
                "is_active": False,
            },
        ])
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_negotiation_prompt(player, 4, 1, state, config)

        assert "B_cancelled1" not in prompt
        assert "## 有効な公開報奨" not in prompt

    def test_prompt_bounties_public_anonymizes_poster(self):
        """poster=None（匿名掲載）の場合、掲載者名を出さず「匿名」と表示する"""
        state = _base_negotiation_state(bounties_public=[
            {
                "bounty_id": "B_anon00001",
                "amount": 300_000,
                "condition_type": "same_market",
                "condition": {"target_player": "P05"},
                "poster": None,
                "is_active": True,
            },
        ])
        player = make_player("P02", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_negotiation_prompt(player, 4, 1, state, config)

        assert "掲載者: 匿名" in prompt


# =============================================================================
# E3: bounty_cancel のJSON形式がaction一覧に含まれる
# =============================================================================

class TestE3BountyCancelFormat:
    def test_prompt_bounty_cancel_format(self):
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_system_prompt("P01", config)
        assert '"type": "bounty_cancel"' in prompt


# =============================================================================
# E4-a: 匿名通信費の事実（cash支払・Free Cash制限外）が機能説明直下に再掲される
# =============================================================================

class TestE4aAnonFeeFreeCashExempt:
    def test_prompt_anon_fee_free_cash_exempt(self):
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_system_prompt("P01", config)
        assert "匿名通信" in prompt
        # 機能説明の近傍に「現金払い」「Free Cash制限外」の事実が明示されている
        # （"匿名通信:" は機能説明行。"匿名通信費"はL719の非適用列挙にも出るため
        #  コロン付きで機能説明行のみを狙う。budget制約により文言は最小限に圧縮
        #  済みだが、事実2点（現金払い／Free Cash制限外）は保持している）
        idx = prompt.index("匿名通信:")
        nearby = prompt[idx:idx + 200]
        assert "現金" in nearby and "cash" in nearby.lower()
        assert "Free Cash制限外" in nearby


# =============================================================================
# E4-b: 匿名通信費の表示金額が anon_broadcast_fee 設定値と一致する
# =============================================================================

class TestE4bAnonFeeAmountReference:
    def test_prompt_anon_fee_amount_matches_config(self):
        """デフォルト設定（entry_fee == anon_broadcast_fee）で表示金額が一致する"""
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_system_prompt("P01", config)
        expected_man = config.anon_broadcast_fee // 10_000
        idx = prompt.index("匿名通信:")
        line = prompt[idx:prompt.index("\n", idx)]
        assert f"{expected_man}万円" in line

    def test_prompt_anon_fee_tracks_anon_config_when_diverged(self):
        """
        entry_fee と anon_broadcast_fee を異なる値にした回帰テスト。

        E4-bの核心: 匿名通信費の表示が entry_fee_man ではなく
        anon_fee_man（= config.anon_broadcast_fee 由来）を参照していることを
        値を分岐させて確認する。両者が偶然一致している間は検出できない
        参照ミスを検出するための必須テスト。
        """
        base = GameConfig.baseline_v1_s2(8)
        config = base.model_copy(update={
            "entry_fee": 100_000,
            "anon_broadcast_fee": 300_000,
        })
        prompt = build_system_prompt("P01", config)

        # 匿名通信費の表示は anon_broadcast_fee（30万円）に追随する
        anon_idx = prompt.index("匿名通信:")
        anon_line = prompt[anon_idx:prompt.index("\n", anon_idx)]
        assert "30万円" in anon_line
        assert "10万円" not in anon_line

        # Entry Fee の表示（市場節）は entry_fee（10万円）のまま変わらない
        entry_idx = prompt.index("市場参加につきEntry Fee")
        entry_line = prompt[entry_idx:prompt.index("\n", entry_idx)]
        assert "10万円" in entry_line


# =============================================================================
# 語彙スキャン: E1〜E4の新規追加ブロックに戦略助言が含まれない
# =============================================================================

class TestNoStrategyAdviceInNewBlocks:
    ADVICE_WORDS = ["推奨", "有利", "おすすめ", "使うべき", "積極的", "活用"]

    def test_prompt_no_strategy_advice_in_new_blocks(self):
        config = GameConfig.baseline_v1_s2(8)
        system_prompt = build_system_prompt("P01", config)

        state = _base_negotiation_state(bounties_public=[
            {
                "bounty_id": "B_abc12345",
                "amount": 500_000,
                "condition_type": "market_win_against",
                "condition": {"target_player": "P07"},
                "poster": "P01",
                "is_active": True,
            },
        ])
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        neg_prompt = build_negotiation_prompt(player, 4, 1, state, config)

        for word in self.ADVICE_WORDS:
            assert word not in system_prompt, f"戦略助言語彙 '{word}' がsystem_promptに混入"
            assert word not in neg_prompt, f"戦略助言語彙 '{word}' がnegotiation_promptに混入"

        # 「得」は「取得」「所得」等の非助言語にも一致するため、
        # 新規追加ブロック（匿名通信・報奨・bounty_cancel関連行）近傍のみを狭く検査する
        anon_idx = system_prompt.index("匿名通信")
        anon_block = system_prompt[anon_idx:anon_idx + 400]
        assert "安全" not in anon_block
        assert "使うべき" not in anon_block


# =============================================================================
# D1: anonymous_broadcast 本人/他人/God View の可視性（API-free, engineフル実行）
# =============================================================================

class TestAnonBroadcastVisibility:
    def test_anon_broadcast_self_visibility(self):
        """
        送信者本人の visible_state に自分が送った匿名メッセージの本文が見える
        （sender:Noneのため「自分が送った」という属性は付かないが、本文自体は
        本人にも他人にも同一に公開される＝隠蔽・欠落はない）。
        """
        game = _make_game(num_players=3)
        game._setup()
        game._phase_market_open(1)
        game._round_messages = []
        game._anon_broadcast_counts = {pid: 0 for pid in game.players}
        game._action_failures = {pid: [] for pid in game.players}

        action = AnonymousBroadcastAction(player_id="P01", message="M01へ誘導すべき")
        game._execute_negotiation_action(action, "P01", 1, turn=1)

        # 本人視点
        state_self = game._build_visible_state(1, for_player_id="P01")
        msgs_self = state_self["messages"]
        assert len(msgs_self) == 1
        assert msgs_self[0]["message"] == "M01へ誘導すべき"
        assert msgs_self[0]["sender"] is None

    def test_anon_broadcast_others_do_not_see_actual_sender(self):
        """他プレイヤー視点でも actual_sender は一切含まれない（sender:Noneのまま）"""
        game = _make_game(num_players=3)
        game._setup()
        game._phase_market_open(1)
        game._round_messages = []
        game._anon_broadcast_counts = {pid: 0 for pid in game.players}
        game._action_failures = {pid: [] for pid in game.players}

        action = AnonymousBroadcastAction(player_id="P01", message="M01へ誘導すべき")
        game._execute_negotiation_action(action, "P01", 1, turn=1)

        state_other = game._build_visible_state(1, for_player_id="P02")
        msgs_other = state_other["messages"]
        assert len(msgs_other) == 1
        assert msgs_other[0]["sender"] is None
        assert msgs_other[0]["message"] == "M01へ誘導すべき"
        assert "actual_sender" not in msgs_other[0]
        assert "P01" not in str(msgs_other[0])

    def test_anon_broadcast_god_view_reveals_actual_sender(self):
        """_god_transcript にのみ actual_sender=P01 が記録される（post-game開示専用）"""
        game = _make_game(num_players=3)
        game._setup()
        game._phase_market_open(1)
        game._round_messages = []
        game._anon_broadcast_counts = {pid: 0 for pid in game.players}
        game._action_failures = {pid: [] for pid in game.players}

        action = AnonymousBroadcastAction(player_id="P01", message="M01へ誘導すべき")
        game._execute_negotiation_action(action, "P01", 1, turn=1)

        god_entries = [e for e in game._god_transcript if e.get("type") == "anonymous_broadcast"]
        assert len(god_entries) == 1
        assert god_entries[0]["actual_sender"] == "P01"
        assert god_entries[0]["message"] == "M01へ誘導すべき"

    def test_anon_broadcast_insufficient_cash_rejected(self):
        """現金不足時は不成立として記録され、支払いは発生しない（cash判定・Free Cash非依存を実証）"""
        game = _make_game(num_players=3)
        game._setup()
        game._phase_market_open(1)
        game._round_messages = []
        game._anon_broadcast_counts = {pid: 0 for pid in game.players}
        game._action_failures = {pid: [] for pid in game.players}

        # cashを匿名通信費未満に落とす
        p = game.players["P01"]
        game.players["P01"] = p.model_copy(update={"cash": 50_000})

        from engine import actions as action_ops
        action = AnonymousBroadcastAction(player_id="P01", message="test")
        result = action_ops.validate_action(
            action, game.players["P01"], game.config, game.players,
            1, game._anon_broadcast_counts.get("P01", 0),
        )
        assert result.success is False


# =============================================================================
# D3: card_trade 失敗フィードバックの描画（E5検証のみ・commit 49071e4のend-to-end実証）
# =============================================================================

class TestCardTradeFailureFeedback:
    def test_card_trade_failure_feedback_rendered(self):
        """
        上限5人超過（P07 R7の実失敗パターン）の理由がmy_failed_actions経由で
        次のNegotiation promptに描画されることを確認する（現行HEADで解消済みの実証）。
        """
        state = _base_negotiation_state(
            my_failed_actions=[
                {
                    "turn": 1,
                    "action": "card_trade_propose",
                    "target": None,
                    "reason": "Too many targets: 7 > 5",
                },
            ],
            my_action_budget={"used": 1, "max": 10},
        )
        player = make_player("P07", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1_s2(8)
        prompt = build_negotiation_prompt(player, 7, 2, state, config)

        assert "不成立になったあなたのアクション" in prompt
        assert "Too many targets: 7 > 5" in prompt
        assert "card_trade_propose" in prompt
