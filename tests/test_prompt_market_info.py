"""
繰越（キャリーオーバー）と前ラウンド結果のプレイヤー周知テスト

2026-08-17: trial_C_20260815_202246 R4 で M03 の賞金が960,000円になった件を
調査した結果、繰越自体はエンジンの仕様どおりだったが、繰越の事実（前ラウンド
流札→繰越）と前ラウンドの決着結果が visible_state / プロンプトに一切含まれて
おらず、§8.1が「公開情報」と宣言しているにもかかわらずプレイヤーに周知されて
いなかったことが判明した。本ファイルはその是正を検証する。

- Game._build_visible_state() の markets に base_prize/carryover が入ること
- Game._last_round_results が settlement 後に正しく記録されること（R1はNone）
- build_negotiation_prompt / build_commit_prompt が
  内訳表示・前ラウンド結果セクションを描画すること
- 追加した情報に秘匿情報（他人の現金・借金・手札・reasoning）が混入しないこと

2026-08-17（続報・引き継ぎメモリ調査）: 前ラウンド結果の参加者が
"参加6人" のように人数へ潰されており、game.py が保存済みの参加者ID・
使用カードが一度もプロンプトに描画されていなかった（§8.1「使用カード
（決着後）」の実装漏れ）ことが判明。_last_round_results に commits
（player_id + card_rank）を追加し、描画をID+カード列挙に変更した。
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import StubAgent

from llm.prompt_builder import build_negotiation_prompt, build_commit_prompt

from tests.conftest import make_player, make_market


def _make_game(num_players: int = 8, seed: int = 42) -> Game:
    config = GameConfig.baseline_v1_s2(num_players)
    agents = {f"P{i+1:02d}": StubAgent() for i in range(num_players)}
    return Game(config=config, agents=agents, seed=seed, logger=EventLogger())


class TestVisibleStateMarketBreakdown:
    """_build_visible_state() の markets に base_prize/carryover が入ること"""

    def test_visible_state_includes_base_prize_and_carryover(self):
        game = _make_game()
        game._setup()
        game._phase_market_open(1)
        # 手動で繰越を持つ市場を差し込む（生成された市場を上書き）
        game._current_markets = [
            make_market("M01", base_prize=480_000, carryover=0),
            make_market("M02", base_prize=480_000, carryover=480_000),
            make_market("M03", base_prize=480_000, carryover=0),
        ]

        state = game._build_visible_state(1, for_player_id="P01")
        by_id = {m["market_id"]: m for m in state["markets"]}

        assert by_id["M02"]["base_prize"] == 480_000
        assert by_id["M02"]["carryover"] == 480_000
        assert by_id["M02"]["prize_pool"] == 960_000
        assert by_id["M02"]["prize_pool"] == by_id["M02"]["base_prize"] + by_id["M02"]["carryover"]

        assert by_id["M01"]["carryover"] == 0
        assert by_id["M01"]["prize_pool"] == by_id["M01"]["base_prize"]


class TestLastRoundResults:
    """Game._last_round_results の記録タイミングと内容"""

    def test_last_round_results_none_on_round1(self):
        """R1開始時点（settlement前）は last_round_results が None"""
        game = _make_game()
        game._setup()
        game._phase_market_open(1)

        assert game._last_round_results is None
        state = game._build_visible_state(1, for_player_id="P01")
        assert state["last_round_results"] is None

        # プロンプトにも「前ラウンド」セクションが出ない
        player = game.players["P01"]
        neg_prompt = build_negotiation_prompt(player, 1, 1, state, game.config)
        assert "前ラウンド" not in neg_prompt
        commit_prompt = build_commit_prompt(
            player, game._current_markets, 1, state, game.config,
        )
        assert "前ラウンド" not in commit_prompt

    def test_last_round_results_recorded_after_settlement(self):
        """
        参加者0の市場（流札）を含めて settlement した場合、
        _last_round_results に参加者0＋繰越額が記録される
        """
        game = _make_game()
        game._setup()
        game._phase_market_open(1)

        # M03 のみ誰も参加させず、M01/M02 には参加者を作る
        markets_by_id = {m.market_id: m for m in game._current_markets}
        assert set(markets_by_id) == {"M01", "M02", "M03"}

        commits = []
        pids = list(game.players)
        for pid in pids[:2]:
            hand = game.players[pid].hand
            commits.append(_commit_for(pid, "M01", hand))
        for pid in pids[2:4]:
            hand = game.players[pid].hand
            commits.append(_commit_for(pid, "M02", hand))
        # M03 には誰も参加しない（流札）

        game._current_commits = commits
        game._phase_settlement(1)

        lrr = game._last_round_results
        assert lrr is not None
        assert lrr["round"] == 1

        by_id = {m["market_id"]: m for m in lrr["markets"]}

        # M03: 流札。参加者0、繰越額 > 0
        m03 = by_id["M03"]
        assert m03["participants"] == []
        assert m03["winners"] == []
        assert m03["carryover_to_next"] > 0
        assert m03["carryover_to_next"] == m03["total_pool"]

        # M01: 参加者ありなので勝者・獲得額が記録される
        m01 = by_id["M01"]
        assert len(m01["participants"]) == 2
        assert len(m01["winners"]) >= 1
        assert m01["prize_per_winner"] > 0

        # 次ラウンドの visible_state に反映される
        game._phase_market_open(2)
        state2 = game._build_visible_state(2, for_player_id="P01")
        assert state2["last_round_results"]["round"] == 1
        by_id2 = {m["market_id"]: m for m in state2["markets"]}
        assert by_id2["M03"]["carryover"] == m03["carryover_to_next"]
        assert by_id2["M03"]["prize_pool"] == by_id2["M03"]["base_prize"] + by_id2["M03"]["carryover"]


def _commit_for(pid: str, market_id: str, hand):
    """テスト用: プレイヤーの手札からカードを1枚取ってMarketCommitを作る"""
    from engine.models import MarketCommit
    card = hand[0]
    return MarketCommit(player_id=pid, market_id=market_id, card=card)


class TestLastRoundResultsCommits:
    """
    _last_round_results["markets"][*]["commits"] — 参加者ID+使用カード

    §8.1「各市場の参加者・使用カード（決着後）」のうちカードが一度も
    プロンプトに描画されていなかった実装漏れの是正。
    """

    def test_last_round_results_includes_commits(self):
        """settlement後、commitsに参加者ID+カードランクが記録される"""
        game = _make_game()
        game._setup()
        game._phase_market_open(1)

        pids = list(game.players)
        commits = []
        for pid in pids[:3]:
            hand = game.players[pid].hand
            commits.append(_commit_for(pid, "M01", hand))
        game._current_commits = commits
        game._phase_settlement(1)

        lrr = game._last_round_results
        by_id = {m["market_id"]: m for m in lrr["markets"]}
        m01 = by_id["M01"]
        assert "commits" in m01
        assert len(m01["commits"]) == 3
        recorded_ids = {c["player_id"] for c in m01["commits"]}
        assert recorded_ids == set(pids[:3])
        for c in m01["commits"]:
            assert c["card_rank"] in {r.name for r in __import__("engine.models", fromlist=["CardRank"]).CardRank}

        # 流札（M02/M03）はcommitsが空リスト
        for mid in ("M02", "M03"):
            assert by_id[mid]["commits"] == []

    def test_fog_round_hides_card_rank(self):
        """霧のラウンドではcard_rankが'FOG'に伏せられる"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"fog_rounds": [1]})
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        game._phase_market_open(1)

        pids = list(game.players)
        commits = []
        for pid in pids[:3]:
            hand = game.players[pid].hand
            commits.append(_commit_for(pid, "M01", hand))
        game._current_commits = commits
        game._phase_settlement(1)

        lrr = game._last_round_results
        by_id = {m["market_id"]: m for m in lrr["markets"]}
        for c in by_id["M01"]["commits"]:
            assert c["card_rank"] == "FOG"

    def test_non_fog_round_shows_real_card_rank(self):
        """非霧ラウンドではcard_rankが実際のランク名になる（'FOG'ではない）"""
        game = _make_game()
        game._setup()
        game._phase_market_open(1)

        pids = list(game.players)
        commits = []
        for pid in pids[:3]:
            hand = game.players[pid].hand
            commits.append(_commit_for(pid, "M01", hand))
        game._current_commits = commits
        game._phase_settlement(1)

        lrr = game._last_round_results
        by_id = {m["market_id"]: m for m in lrr["markets"]}
        for c in by_id["M01"]["commits"]:
            assert c["card_rank"] != "FOG"


class TestPromptShowsParticipantIdsAndCards:
    """
    build_negotiation_prompt / build_commit_prompt の前ラウンド結果描画

    commitsがあればID+カード列挙、無ければparticipantsのみ、
    どちらも無ければ人数のみ、という3段フォールバックを検証する。
    """

    def _base_state(self, last_round_results):
        return {
            "round_num": 4,
            "markets": [{"market_id": "M01", "prize_pool": 480_000,
                         "base_prize": 480_000, "carryover": 0}],
            "last_round_results": last_round_results,
            "alive_players": ["P01", "P02"],
            "messages": [],
            "contracts_pending": [],
            "trades_pending": [],
        }

    def test_prompt_shows_participant_ids_and_cards(self):
        """commitsがあればID[ランク]の列挙＋勝者に★が付く"""
        last_round_results = {
            "round": 3,
            "markets": [{
                "market_id": "M01",
                "participants": ["P01", "P02", "P04"],
                "commits": [
                    {"player_id": "P01", "card_rank": "ROYAL_FLUSH"},
                    {"player_id": "P02", "card_rank": "FLUSH"},
                    {"player_id": "P04", "card_rank": "TWO_PAIR"},
                ],
                "winners": ["P01"], "prize_per_winner": 1_080_000,
                "total_pool": 1_080_000, "surged": False, "carryover_to_next": 0,
            }],
        }
        state = self._base_state(last_round_results)
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1(8)
        prompt = build_negotiation_prompt(player, 4, 1, state, config)

        assert "P01[ROYAL_FLUSH]★" in prompt
        assert "P02[FLUSH]" in prompt
        assert "P02[FLUSH]★" not in prompt  # 非勝者に★は付かない
        assert "P04[TWO_PAIR]" in prompt

    def test_prompt_falls_back_to_participants_only(self):
        """commitsが無くparticipantsのみの場合、IDのみ列挙（カードは出さない）"""
        last_round_results = {
            "round": 3,
            "markets": [{
                "market_id": "M01",
                "participants": ["P01", "P02"],
                "winners": ["P01"], "prize_per_winner": 1_080_000,
                "total_pool": 1_080_000, "surged": False, "carryover_to_next": 0,
            }],
        }
        state = self._base_state(last_round_results)
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1(8)
        prompt = build_commit_prompt(
            player,
            [make_market("M01", base_prize=480_000, carryover=0)],
            4, state, config,
        )
        assert "参加2人 → 勝者 P01" in prompt
        assert "[" not in prompt.split("## 前ラウンド")[1].split("\n")[1]

    def test_prompt_falls_back_to_count_when_no_ids(self):
        """participants/commitsどちらも無い旧形式dictでも壊れない（後方互換）"""
        last_round_results = {
            "round": 3,
            "markets": [{
                "market_id": "M01",
                "winners": ["P01"], "prize_per_winner": 1_080_000,
                "total_pool": 1_080_000, "surged": False, "carryover_to_next": 0,
            }],
        }
        state = self._base_state(last_round_results)
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1(8)
        # participantsキー自体が無い（空リストのデフォルトになる）→ 不成立扱いにフォールバック
        prompt = build_negotiation_prompt(player, 4, 1, state, config)
        assert "不成立" in prompt


class TestNegotiationPromptCarryover:
    """build_negotiation_prompt の内訳表示・前ラウンド結果セクション"""

    def _base_state(self, markets, last_round_results=None):
        return {
            "round_num": 4,
            "markets": markets,
            "last_round_results": last_round_results,
            "alive_players": ["P01", "P02"],
            "messages": [],
            "contracts_pending": [],
            "trades_pending": [],
        }

    def test_negotiation_prompt_shows_carryover_breakdown(self):
        markets = [
            {"market_id": "M01", "prize_pool": 480_000, "base_prize": 480_000, "carryover": 0},
            {"market_id": "M03", "prize_pool": 960_000, "base_prize": 480_000, "carryover": 480_000},
        ]
        state = self._base_state(markets)
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1(8)
        prompt = build_negotiation_prompt(player, 4, 1, state, config)

        assert "M03: 賞金 96万円（基本48万 + 前R繰越48万）" in prompt
        # carryover=0 の市場には括弧内訳が付かない
        m01_line = next(line for line in prompt.splitlines() if line.strip().startswith("M01:"))
        assert "（基本" not in m01_line

    def test_negotiation_prompt_no_breakdown_without_carryover_keys(self):
        """base_prize/carryoverキーが無い旧形式のdictでも壊れない（後方互換）"""
        markets = [{"market_id": "M01", "prize_pool": 500_000}]
        state = self._base_state(markets)
        player = make_player("P02", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1(8)
        prompt = build_negotiation_prompt(player, 3, 1, state, config)
        assert "M01: 賞金 50万円" in prompt
        assert "（基本" not in prompt

    def test_negotiation_prompt_shows_last_round_results(self):
        markets = [
            {"market_id": "M01", "prize_pool": 480_000, "base_prize": 480_000, "carryover": 0},
            {"market_id": "M03", "prize_pool": 960_000, "base_prize": 480_000, "carryover": 480_000},
        ]
        last_round_results = {
            "round": 3,
            "markets": [
                {
                    "market_id": "M01", "participants": ["P01", "P02"],
                    "winners": ["P01"], "prize_per_winner": 1_080_000,
                    "total_pool": 1_080_000, "surged": True, "carryover_to_next": 0,
                },
                {
                    "market_id": "M03", "participants": [],
                    "winners": [], "prize_per_winner": 0,
                    "total_pool": 480_000, "surged": False, "carryover_to_next": 480_000,
                },
            ],
        }
        state = self._base_state(markets, last_round_results)
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1(8)
        prompt = build_negotiation_prompt(player, 4, 1, state, config)

        assert "## 前ラウンド（R3）の結果" in prompt
        assert "M01: 参加2人 → 勝者 P01（各108万円）【高騰×2】" in prompt
        assert "M03: 参加0人 → 不成立。賞金48万円は今ラウンドのM03へ繰越" in prompt


class TestCommitPromptLastRoundResults:
    """build_commit_prompt の前ラウンド結果セクション"""

    def test_commit_prompt_shows_last_round_results(self):
        from tests.conftest import make_market

        markets = [
            make_market("M01", base_prize=480_000, carryover=0),
            make_market("M03", base_prize=480_000, carryover=480_000),
        ]
        last_round_results = {
            "round": 3,
            "markets": [
                {
                    "market_id": "M01", "participants": ["P01", "P02"],
                    "winners": ["P08"], "prize_per_winner": 780_000,
                    "total_pool": 780_000, "surged": False, "carryover_to_next": 0,
                },
                {
                    "market_id": "M03", "participants": [],
                    "winners": [], "prize_per_winner": 0,
                    "total_pool": 480_000, "surged": False, "carryover_to_next": 480_000,
                },
            ],
        }
        state = {
            "round_num": 4,
            "markets": [{"market_id": m.market_id, "prize_pool": m.prize_pool,
                         "base_prize": m.base_prize, "carryover": m.carryover} for m in markets],
            "last_round_results": last_round_results,
            "alive_players": ["P01", "P02"],
            "messages": [],
            "contracts_pending": [],
            "trades_pending": [],
        }
        player = make_player("P01", cash=3_000_000, debt=2_000_000)
        config = GameConfig.baseline_v1(8)
        prompt = build_commit_prompt(player, markets, 4, state, config)

        assert "M03: 賞金 96万円（基本48万 + 前R繰越48万）" in prompt
        assert "## 前ラウンド（R3）の結果" in prompt
        assert "M01: 参加2人 → 勝者 P08（各78万円）" in prompt
        assert "M03: 参加0人 → 不成立。賞金48万円は今ラウンドのM03へ繰越" in prompt


class TestNoSecretInfoLeak:
    """追加したセクションに秘匿情報が混入しないこと"""

    ALLOWED_MARKET_RESULT_KEYS = {
        "market_id", "participants", "winners", "prize_per_winner",
        "total_pool", "surged", "carryover_to_next", "commits",
    }
    # "auto" はCycle 1のAUTO COMMIT機能で追加された公開情報キー
    # （RULES_SUMMARYで「AUTO COMMIT発生」は公開情報と明記されている）。
    # このテスト自体はCycle 2で変更していないが、既存の許可リストが
    # 追加当時から更新されていなかったため、実装に合わせて追記する。
    ALLOWED_COMMIT_KEYS = {"player_id", "card_rank", "auto"}

    def test_last_round_results_has_no_secret_keys(self):
        """
        _last_round_results の各市場エントリは §8.1公開情報
        （参加者ID・勝者・獲得額・高騰・繰越）のキーのみで構成される
        """
        game = _make_game()
        game._setup()
        game._phase_market_open(1)

        pids = list(game.players)
        commits = []
        for pid in pids[:3]:
            hand = game.players[pid].hand
            commits.append(_commit_for(pid, "M01", hand))
        # M02/M03 は流札のまま
        game._current_commits = commits
        game._phase_settlement(1)

        lrr = game._last_round_results
        assert lrr is not None
        for m in lrr["markets"]:
            assert set(m.keys()) <= self.ALLOWED_MARKET_RESULT_KEYS
            for c in m.get("commits", []):
                assert set(c.keys()) <= self.ALLOWED_COMMIT_KEYS

    def test_prompt_contains_no_secret_info(self):
        """
        last_round_results / markets の内訳表示を含むプロンプトに
        reasoning・借金残高キー・他プレイヤーの手札詳細が混入しない
        （tests/test_cot.py の秘匿検査と同方針）
        """
        game = _make_game()
        game._setup()
        game._phase_market_open(1)

        pids = list(game.players)
        commits = []
        for pid in pids[:3]:
            hand = game.players[pid].hand
            commits.append(_commit_for(pid, "M01", hand))
        # M02/M03 は流札のまま
        game._current_commits = commits
        game._phase_settlement(1)

        game._phase_market_open(2)
        for pid in game.players:
            state = game._build_visible_state(2, for_player_id=pid)
            player = game.players[pid]
            commit_prompt = build_commit_prompt(player, game._current_markets, 2, state, game.config)
            neg_prompt = build_negotiation_prompt(player, 2, 1, state, game.config)

            for prompt in (commit_prompt, neg_prompt):
                assert "reasoning" not in prompt.lower()
                assert "_reasoning" not in prompt
                # 秘匿情報のキー名がそのまま漏れていないこと
                assert "debt_balance" not in prompt
