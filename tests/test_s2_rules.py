"""
Season 2 ルールメカニクスのテスト

S2固有の4メカニクス（霧のラウンド・市場高騰・最終市場・倍掛け）と、
S2ルールでのゲーム完走・再現性を検証する。
"""

import pytest
from engine.config import GameConfig
from engine.models import (
    Market, MarketCommit, MarketResult, Card, CardRank, PlayerState, DoubleUpDeposit,
)
from engine import market as market_ops
from engine.game import Game, GameResult
from engine.events import EventLogger
from engine.negotiation import StubAgent
from bots import BOT_REGISTRY, DEFAULT_ROSTER


# === 霧のラウンド ===

class TestFogRounds:
    """霧のラウンド（R4/R8）のテスト"""

    def test_fog_round_card_masked_in_reveal(self):
        """霧ラウンドのREVEALイベントではカード情報がFOGになる"""
        config = GameConfig.default_8().model_copy(update={"fog_rounds": [4, 8]})
        agents = _make_stub_agents(config.num_players)
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game.run()

        # R4のREVEALイベントを検索
        r4_reveals = [
            e for e in logger.events
            if e.event_type == "REVEAL" and e.round_num == 4
        ]
        assert len(r4_reveals) == 1
        reveal = r4_reveals[0]
        assert reveal.data["fog"] is True
        for commit in reveal.data["commits"]:
            assert commit["card"] == "FOG"
            assert commit["rank"] == "FOG"

    def test_non_fog_round_card_visible(self):
        """非霧ラウンドのREVEALイベントではカード情報が公開される"""
        config = GameConfig.default_8().model_copy(update={"fog_rounds": [4, 8]})
        agents = _make_stub_agents(config.num_players)
        logger = EventLogger()
        game = Game(config=config, agents=agents, seed=42, logger=logger)
        game.run()

        # R1のREVEALイベントを検索
        r1_reveals = [
            e for e in logger.events
            if e.event_type == "REVEAL" and e.round_num == 1
        ]
        assert len(r1_reveals) == 1
        reveal = r1_reveals[0]
        assert reveal.data["fog"] is False
        for commit in reveal.data["commits"]:
            assert commit["card"] != "FOG"
            assert commit["rank"] != "FOG"


# === 市場高騰 ===

class TestMarketSurge:
    """市場高騰メカニクスのテスト"""

    def test_surge_activation(self):
        """参加者 > 生存者/2 で高騰発動 → プール2倍"""
        market = Market(market_id="M01", base_prize=100_000)
        commits = [
            MarketCommit(
                player_id=f"P{i+1:02d}",
                market_id="M01",
                card=Card(rank=CardRank.HIGH_CARD, card_id=f"HC_{i}"),
            )
            for i in range(3)  # 3人参加
        ]
        # surge=True → プール2倍
        result = market_ops.resolve_market(market, commits, entry_fee=100_000, surge=True)
        # 基本100k + Entry Fee 3*100k = 400k → 2倍 = 800k
        assert result.total_pool == 800_000
        assert result.surged is True

    def test_surge_not_triggered(self):
        """surge=False → 通常プール"""
        market = Market(market_id="M01", base_prize=100_000)
        commits = [
            MarketCommit(
                player_id="P01",
                market_id="M01",
                card=Card(rank=CardRank.HIGH_CARD, card_id="HC_0"),
            ),
        ]
        result = market_ops.resolve_market(market, commits, entry_fee=100_000, surge=False)
        assert result.total_pool == 200_000  # 100k + 100k
        assert result.surged is False

    def test_surge_zero_participants(self):
        """参加者0でsurge=True → キャリーオーバー（参加者0は高騰判定に至らない）"""
        market = Market(market_id="M01", base_prize=100_000)
        result = market_ops.resolve_market(market, [], entry_fee=100_000, surge=True)
        # 参加者0のときも2倍されるが、勝者がいないので影響なし
        assert result.total_pool == 200_000
        assert len(result.winners) == 0


# === 市場高騰: 少人数全員参加要件 ===

class TestSurgeFullParticipation:
    """少人数時の全員参加高騰要件（surge_full_participation_max_alive）のテスト"""

    def test_4_alive_all_participate_surge_with_max4(self):
        """生存4人・同一市場4人 → 高騰する（max_alive=4明示）"""
        from engine.settlement import _should_surge
        config = GameConfig.default_8_s2().model_copy(update={
            "surge_full_participation_max_alive": 4,
        })
        assert _should_surge(4, 4, config) is True

    def test_4_alive_3_participate_no_surge_with_max4(self):
        """生存4人・同一市場3人 → 高騰しない（max_alive=4明示）"""
        from engine.settlement import _should_surge
        config = GameConfig.default_8_s2().model_copy(update={
            "surge_full_participation_max_alive": 4,
        })
        assert _should_surge(3, 4, config) is False

    # --- 境界人数3（S2プリセットのデフォルト値）---

    def test_4_alive_3_participate_surge_with_max3(self):
        """生存4人・同一市場3人 → 高騰する（max_alive=3: 通常判定 3>2）"""
        from engine.settlement import _should_surge
        config = GameConfig.default_8_s2()  # surge_full_participation_max_alive=3
        assert _should_surge(3, 4, config) is True

    def test_3_alive_3_participate_surge_with_max3(self):
        """生存3人・同一市場3人 → 高騰する（max_alive=3: 全員参加 3==3）"""
        from engine.settlement import _should_surge
        config = GameConfig.default_8_s2()
        assert _should_surge(3, 3, config) is True

    def test_3_alive_2_participate_no_surge_with_max3(self):
        """生存3人・同一市場2人 → 高騰しない（max_alive=3: 全員参加 2!=3）"""
        from engine.settlement import _should_surge
        config = GameConfig.default_8_s2()
        assert _should_surge(2, 3, config) is False

    def test_3_alive_all_participate_surge(self):
        """生存3人・同一市場3人 → 高騰する"""
        from engine.settlement import _should_surge
        config = GameConfig.default_8_s2()
        assert _should_surge(3, 3, config) is True

    def test_3_alive_2_participate_no_surge(self):
        """生存3人・同一市場2人 → 高騰しない（現行なら高騰していた）"""
        from engine.settlement import _should_surge
        config = GameConfig.default_8_s2()
        assert _should_surge(2, 3, config) is False

    def test_5_alive_3_participate_surge(self):
        """生存5人・同一市場3人 → 高騰する（現行どおり、変更なし）"""
        from engine.settlement import _should_surge
        config = GameConfig.default_8_s2()
        # 5人の場合は通常判定: 3 > 5/2=2.5 → True
        assert _should_surge(3, 5, config) is True

    def test_8_alive_5_participate_surge(self):
        """生存8人・同一市場5人 → 高騰する（現行どおり、変更なし）"""
        from engine.settlement import _should_surge
        config = GameConfig.default_8_s2()
        # 8人の場合は通常判定: 5 > 8/2=4.0 → True
        assert _should_surge(5, 8, config) is True

    def test_param_zero_reverts_to_legacy(self):
        """パラメータ0で従来挙動に戻る"""
        from engine.settlement import _should_surge
        config = GameConfig.default_8_s2().model_copy(update={
            "surge_full_participation_max_alive": 0,
        })
        # 4人でも通常判定: 3 > 4/2=2.0 → True（従来挙動）
        assert _should_surge(3, 4, config) is True
        # 3人でも通常判定: 2 > 3/2=1.5 → True（従来挙動）
        assert _should_surge(2, 3, config) is True


# === 最終市場 ===

class TestFinalMarket:
    """最終市場（R12 基本賞金3倍）のテスト"""

    def test_final_market_triple(self):
        """R12のbase_prizeが3倍になる"""
        config = GameConfig(
            num_players=8,
            prize_tiers=[100_000] * 12,
            final_market_multiplier=3,
        )
        markets = market_ops.generate_markets(12, config, {})
        total_prize = sum(m.base_prize for m in markets)
        assert total_prize == 300_000  # 100k * 3 = 300k

    def test_non_final_round_normal(self):
        """R1-R11ではbase_prizeは通常通り"""
        config = GameConfig(
            num_players=8,
            prize_tiers=[100_000] * 12,
            final_market_multiplier=3,
        )
        markets = market_ops.generate_markets(1, config, {})
        total_prize = sum(m.base_prize for m in markets)
        assert total_prize == 100_000  # 通常

    def test_final_market_with_surge(self):
        """R12 + 高騰 = (base*3 + entries) * 2"""
        config = GameConfig(
            num_players=8,
            prize_tiers=[100_000] * 12,
            final_market_multiplier=3,
        )
        markets = market_ops.generate_markets(12, config, {})
        # M01のbase_prize = 300k / 3 = 100k
        market = markets[0]

        commits = [
            MarketCommit(
                player_id=f"P{i+1:02d}",
                market_id=market.market_id,
                card=Card(rank=CardRank.HIGH_CARD, card_id=f"HC_{i}"),
            )
            for i in range(5)
        ]
        result = market_ops.resolve_market(market, commits, entry_fee=100_000, surge=True)
        # base_prize(100k) + entry(5*100k) = 600k → surge 2x = 1200k
        expected = (market.base_prize + 5 * 100_000) * 2
        assert result.total_pool == expected
        assert result.surged is True


# === 倍掛け ===

class TestDoubleUp:
    """倍掛けメカニクスのテスト"""

    def test_double_up_model(self):
        """DoubleUpDepositモデルの基本動作"""
        dep = DoubleUpDeposit(
            player_id="P01",
            deposit_amount=500_000,
            deposited_round=3,
            success_round=4,
        )
        assert dep.resolved is False
        assert dep.success is False
        assert dep.from_solo_market is False

    def test_s2_game_has_double_up_deposits(self):
        """S2ルールのゲームで倍掛け預託が発生する"""
        config = GameConfig.default_8_s2()
        agents = _make_bot_agents()
        game = Game(config=config, agents=agents, seed=42)
        result = game.run()
        # Bot戦略上、いくつかのBotはDOUBLEを選択するはず
        assert len(result.double_up_deposits) > 0

    def test_double_up_all_resolved(self):
        """ゲーム終了時に全倍掛け預託が解決済み"""
        config = GameConfig.default_8_s2()
        agents = _make_bot_agents()
        game = Game(config=config, agents=agents, seed=42)
        result = game.run()
        for dep in result.double_up_deposits:
            assert dep.resolved is True, (
                f"Unresolved deposit: {dep.player_id} R{dep.deposited_round}"
            )


class TestDoubleUpSoloExclusion:
    """倍掛け成功判定からのソロ市場除外テスト（§6.2）"""

    def _make_game_with_player(self, player_id: str = "P01") -> Game:
        """プレイヤーが初期化済みの Game を作成"""
        config = GameConfig.default_8_s2()
        agents = _make_bot_agents()
        game = Game(config=config, agents=agents, seed=1)
        # プレイヤーを手動初期化（run() を呼ばずにテストするため）
        from engine import player as player_ops
        for pid in agents:
            game.players[pid] = player_ops.create_player(pid, 3_000_000)
        return game

    def _make_market_result(self, market_id: str, winner: str, prize: int,
                            num_participants: int) -> MarketResult:
        """テスト用 MarketResult を生成"""
        participants = []
        for i in range(num_participants):
            pid = winner if i == 0 else f"P{i+1:02d}"
            card = Card(rank=CardRank.HIGH_CARD, card_id=f"c_{pid}_{market_id}")
            participants.append(
                MarketCommit(player_id=pid, market_id=market_id, card=card)
            )
        return MarketResult(
            market_id=market_id,
            participants=participants,
            winners=[winner],
            prize_per_winner=prize,
            total_pool=prize,
        )

    def test_solo_market_only_fails(self):
        """ソロ市場のみで賞金獲得 → 倍掛け失敗"""
        game = self._make_game_with_player()
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=100_000,
            deposited_round=1, success_round=2,
        )
        game.double_up_deposits.append(dep)

        results = [self._make_market_result("M01", "P01", 500_000, 1)]
        game._process_double_up(2, results)

        assert dep.resolved is True
        assert dep.success is False, "ソロ市場のみでの賞金獲得は倍掛け失敗すべき"

    def test_non_solo_market_succeeds(self):
        """複数人市場で賞金獲得 → 倍掛け成功"""
        game = self._make_game_with_player()
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=100_000,
            deposited_round=1, success_round=2,
        )
        game.double_up_deposits.append(dep)

        results = [self._make_market_result("M01", "P01", 500_000, 3)]
        game._process_double_up(2, results)

        assert dep.resolved is True
        assert dep.success is True, "複数人市場での賞金獲得は倍掛け成功すべき"

    def test_mixed_markets_succeeds(self):
        """ソロ市場+複数人市場の両方で獲得 → 倍掛け成功"""
        game = self._make_game_with_player()
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=100_000,
            deposited_round=1, success_round=2,
        )
        game.double_up_deposits.append(dep)

        results = [
            self._make_market_result("M01", "P01", 300_000, 1),  # ソロ
            self._make_market_result("M02", "P01", 200_000, 2),  # 複数人
        ]
        game._process_double_up(2, results)

        assert dep.resolved is True
        assert dep.success is True, "複数人市場で獲得があれば成功すべき"
        assert dep.from_solo_market is False

    def test_all_solo_markets_fails(self):
        """全市場がソロ市場 → 倍掛け失敗"""
        game = self._make_game_with_player()
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=200_000,
            deposited_round=1, success_round=2,
        )
        game.double_up_deposits.append(dep)

        results = [
            self._make_market_result("M01", "P01", 300_000, 1),  # ソロ
            self._make_market_result("M02", "P01", 200_000, 1),  # ソロ
            self._make_market_result("M03", "P02", 100_000, 1),  # 別プレイヤー
        ]
        game._process_double_up(2, results)

        assert dep.resolved is True
        assert dep.success is False, "全市場ソロでは倍掛け失敗すべき"

    def test_no_prize_fails(self):
        """賞金獲得なし → 倍掛け失敗"""
        game = self._make_game_with_player()
        dep = DoubleUpDeposit(
            player_id="P01", deposit_amount=100_000,
            deposited_round=1, success_round=2,
        )
        game.double_up_deposits.append(dep)

        results = [self._make_market_result("M01", "P02", 500_000, 3)]
        game._process_double_up(2, results)

        assert dep.resolved is True
        assert dep.success is False


# === S2ゲーム完走・再現性 ===

class TestS2Integration:
    """S2ルールでのゲーム統合テスト"""

    def test_s2_game_completes(self):
        """S2ルールで12ラウンド完走する"""
        config = GameConfig.default_8_s2()
        agents = _make_bot_agents()
        game = Game(config=config, agents=agents, seed=42)
        result = game.run()
        assert result.round_count == 12

    def test_s2_reproducibility(self):
        """同一seedで同一結果が再現される"""
        config = GameConfig.default_8_s2()

        agents1 = _make_bot_agents(seed_base=100)
        game1 = Game(config=config, agents=agents1, seed=99)
        result1 = game1.run()

        agents2 = _make_bot_agents(seed_base=100)
        game2 = Game(config=config, agents=agents2, seed=99)
        result2 = game2.run()

        assert len(result1.survivors) == len(result2.survivors)
        for p1, p2 in zip(result1.survivors, result2.survivors):
            assert p1.player_id == p2.player_id
            assert p1.cash == p2.cash

    def test_s2_has_round_snapshots(self):
        """S2ゲームでラウンドスナップショットが12個生成される"""
        config = GameConfig.default_8_s2()
        agents = _make_bot_agents()
        game = Game(config=config, agents=agents, seed=42)
        result = game.run()
        assert len(result.round_snapshots) == 12
        for snap in result.round_snapshots:
            assert "total_assets" in snap
            assert "alive_count" in snap
            assert "surge_count" in snap

    def test_s1_unchanged(self):
        """S1ルールのゲームがS2変更の影響を受けない"""
        config = GameConfig.default_8()
        agents = _make_bot_agents()
        game = Game(config=config, agents=agents, seed=42)
        result = game.run()
        assert result.round_count == 12
        # S1ではdouble_up_depositsは空
        assert len(result.double_up_deposits) == 0
        # S1ではsurge_countは全0
        for snap in result.round_snapshots:
            assert snap["surge_count"] == 0

    def test_s2_config_presets(self):
        """S2設定プリセットが正しく構成される（v0.7: fog_rounds=[]）"""
        c = GameConfig.default_8_s2()
        assert c.fog_rounds == []  # v0.7 §10.2 で削除
        assert c.surge_enabled is True
        assert c.final_market_multiplier == 3
        assert c.double_up_enabled is True
        assert c.mandatory_repay_enabled is True
        assert c.mandatory_repay_k == 0
        assert c.card_trade_enabled is True
        assert c.num_players == 8

        c2 = GameConfig.baseline_v1_s2()
        assert c2.fog_rounds == []  # v0.7 §10.2 で削除
        assert c2.surge_enabled is True
        assert c2.survival_cash == 2_000_000
        assert c2.mandatory_repay_enabled is True
        assert c2.card_trade_enabled is True


# === ヘルパー ===

def _make_stub_agents(num_players: int) -> dict[str, StubAgent]:
    """StubAgentの辞書を生成"""
    return {f"P{i+1:02d}": StubAgent() for i in range(num_players)}


def _make_bot_agents(seed_base: int = 42) -> dict[str, "BotAgent"]:
    """8種Bot各1体の辞書を生成"""
    roster = list(DEFAULT_ROSTER)
    agents = {}
    for i, bot_name in enumerate(roster):
        pid = f"P{i+1:02d}"
        bot_class = BOT_REGISTRY[bot_name]
        agents[pid] = bot_class(seed=seed_base * 100 + i)
    return agents
