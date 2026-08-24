"""
Cycle 1 Phase 1: 手札の権威的提示・固定JSON例の除去・Memory優先順位・card_plan照合の回帰テスト

背景（§1.5監査 trial_C_l12_r12_20260822）:
AUTO COMMIT 4件全て（P09 R3/R4/R6, P06 R7）が「本人が指定しようとしたカードが、
その時点の手札に存在しなかった」ことに起因していた。うち2件（P09 R3/R4）は
commit プロンプト末尾のJSON例が `{"market_id": "M01", "card": "ONE_PAIR"}` に
固定ハードコードされており、応答がこの例と完全一致していた。

本ファイルは llm/prompt_builder.py の P-1〜P-4 修正を検証する。
"""

from engine.config import GameConfig
from engine.models import Card, CardRank

from llm.prompt_builder import (
    build_commit_prompt,
    build_negotiation_prompt,
    _render_memory_block,
)

from tests.conftest import make_player, make_market


def _hand_without(*excluded_ranks: CardRank) -> list[Card]:
    """create_deck()相当だが指定ランクを除いた手札を作る"""
    cards: list[Card] = []
    for rank in CardRank:
        if rank in excluded_ranks:
            continue
        n = 2 if rank in (CardRank.HIGH_CARD, CardRank.ONE_PAIR) else 1
        for i in range(1, n + 1):
            cards.append(Card(rank=rank, card_id=f"{rank.name}_{i}"))
    return cards


def _base_visible_state() -> dict:
    return {
        "markets": [], "last_round_results": None, "my_obligations": [],
        "alive_players": ["P09"], "eliminated_players": [], "messages": [],
        "contracts_pending": [], "my_contracts": [],
    }


# =====================================================================
# T-P1/T-P2/T-P3: JSON例が実手札・実市場から動的生成される
# =====================================================================

class TestDynamicJsonExample:
    def test_example_card_is_in_hand_enable_cot_false(self):
        """T-P1: enable_cot=False でも例のcardが必ず手札に存在する"""
        hand = _hand_without(CardRank.ONE_PAIR)
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        markets = [make_market("M01", 1_000_000)]
        prompt = build_commit_prompt(
            player, markets, 3, _base_visible_state(), config,
        )
        hand_names = {c.rank.name for c in hand}
        import re
        m = re.search(r'"card":\s*"([A-Z_]+)"', prompt)
        assert m is not None
        assert m.group(1) in hand_names

    def test_example_card_is_in_hand_enable_cot_true(self):
        """T-P1: enable_cot=True でも同様"""
        hand = _hand_without(CardRank.ONE_PAIR)
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9).model_copy(update={"enable_cot": True})
        markets = [make_market("M01", 1_000_000)]
        prompt = build_commit_prompt(
            player, markets, 3, _base_visible_state(), config,
        )
        assert '"reasoning"' in prompt
        hand_names = {c.rank.name for c in hand}
        import re
        m = re.search(r'"card":\s*"([A-Z_]+)"', prompt)
        assert m is not None
        assert m.group(1) in hand_names

    def test_one_pair_never_appears_when_not_in_hand(self):
        """T-P2: P09 R3/R4の直接再現。ONE_PAIRが手札に無ければ出力に一切現れない"""
        hand = _hand_without(CardRank.ONE_PAIR)
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        markets = [make_market("M01", 1_000_000)]
        prompt = build_commit_prompt(
            player, markets, 3, _base_visible_state(), config,
        )
        assert '"card": "ONE_PAIR"' not in prompt
        assert "ONE_PAIR" not in prompt

    def test_example_market_matches_available_markets(self):
        """T-P3: 市場がM02/M03のみのときM01を例に出さない"""
        hand = _hand_without(CardRank.ONE_PAIR)
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        markets = [make_market("M02", 1_000_000), make_market("M03", 1_000_000)]
        prompt = build_commit_prompt(
            player, markets, 3, _base_visible_state(), config,
        )
        import re
        m = re.search(r'"market_id":\s*"(M\d+)"', prompt)
        assert m is not None
        assert m.group(1) in ("M02", "M03")
        assert '"market_id": "M01"' not in prompt


# =====================================================================
# T-P4: 手札ブロックの選択可能性・AUTO COMMIT明示
# =====================================================================

class TestHandBlockExplicitness:
    def test_hand_block_states_selectable_set_and_auto_commit(self):
        hand = _hand_without(CardRank.ONE_PAIR)
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        markets = [make_market("M01", 1_000_000)]
        prompt = build_commit_prompt(
            player, markets, 3, _base_visible_state(), config,
        )
        hand_names = {c.rank.name for c in hand}
        assert f"この{len(hand)}枚からのみ選べます" in prompt
        assert "AUTO COMMIT" in prompt
        for name in hand_names:
            assert name in prompt


# =====================================================================
# T-P5: Memory優先順位に「手札」「使用済みカード」が含まれる
# =====================================================================

class TestMemoryStaleWarningPriority:
    def test_stale_warning_mentions_hand_and_used_cards(self):
        lines = _render_memory_block("何かメモ", stale_warning=True)
        text = "\n".join(lines)
        assert "手札" in text
        assert "使用済みカード" in text

    def test_no_stale_warning_by_default_unchanged(self):
        lines = _render_memory_block("何かメモ")
        text = "\n".join(lines)
        assert "このメモは過去の自分の記述です" not in text


# =====================================================================
# T-P6/T-P7/T-P8: card_plan の手札照合
# =====================================================================

class TestCardPlanCrossCheck:
    def test_card_plan_mismatch_gets_note(self):
        """T-P6: 手札にONE_PAIRが無いのにcard_planがONE_PAIRを指す→注記が付く"""
        hand = _hand_without(CardRank.ONE_PAIR)
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        markets = [make_market("M01", 1_000_000)]
        prompt = build_commit_prompt(
            player, markets, 4, _base_visible_state(), config,
            last_strategy={"card_plan": "M01でONE_PAIRを使用予定"},
        )
        assert "ONE_PAIR は現在の手札にありません" in prompt

    def test_card_plan_match_gets_no_note(self):
        """T-P7: 手札にあるカードを指すcard_planには注記が付かない（誤検知回帰）"""
        hand = _hand_without(CardRank.ONE_PAIR)  # TWO_PAIR は残っている
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        markets = [make_market("M01", 1_000_000)]
        prompt = build_commit_prompt(
            player, markets, 4, _base_visible_state(), config,
            last_strategy={"card_plan": "M01でTWO_PAIRを使用予定"},
        )
        assert "は現在の手札にありません" not in prompt

    def test_card_plan_without_card_name_does_not_crash(self):
        """T-P8: card_plan にカード名が含まれない場合も無加工でクラッシュしない"""
        hand = _hand_without(CardRank.ONE_PAIR)
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        markets = [make_market("M01", 1_000_000)]
        prompt = build_commit_prompt(
            player, markets, 4, _base_visible_state(), config,
            last_strategy={"card_plan": "M03に集中"},
        )
        assert "M03に集中" in prompt
        assert "は現在の手札にありません" not in prompt


# =====================================================================
# T-P9: 実ログ再現（trial_C_l12_r12_20260822）
# =====================================================================

class TestRealLogReplay:
    """
    P09 R3/R4/R6, P06 R7 の実際の hand / last_strategy を fixture化し、
    修正後プロンプトが「指定しようとしたカードが手札に無い」ことを機械的に
    明示することを検証する。
    """

    def test_p09_r3_one_pair_missing(self):
        hand = _hand_without(CardRank.ONE_PAIR)
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        markets = [make_market("M01", 1_000_000)]
        prompt = build_commit_prompt(
            player, markets, 3, _base_visible_state(), config,
            last_strategy={
                "reason": "交渉での合意通り、M01でONE_PAIRを使って協調し利益確保と過密回避を図る",
                "card_plan": "次ラウンドはM01でONE_PAIR使用予定",
            },
        )
        assert "ONE_PAIR は現在の手札にありません" in prompt
        assert '"card": "ONE_PAIR"' not in prompt

    def test_p09_r4_one_pair_missing_repeat(self):
        """R3で同じ間違いをした直後のR4でも同様（反復再現）"""
        hand = _hand_without(CardRank.ONE_PAIR, CardRank.TWO_PAIR)
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        markets = [make_market("M01", 1_000_000)]
        prompt = build_commit_prompt(
            player, markets, 4, _base_visible_state(), config,
            last_strategy={
                "reason": "P06とカード交換提案済みで、M01でONE_PAIR協調を継続し堅実に獲得を狙う",
                "card_plan": "次ラウンドはM01でONE_PAIR使用予定",
            },
        )
        assert "ONE_PAIR は現在の手札にありません" in prompt

    def test_p09_r6_full_house_missing(self):
        hand = _hand_without(CardRank.FULL_HOUSE)
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        markets = [make_market("M03", 1_000_000)]
        prompt = build_commit_prompt(
            player, markets, 6, _base_visible_state(), config,
            last_strategy={
                "reason": "P06とFLUSH⇔FULL_HOUSEのカード交換を確定し、型B契約違反リスク回避の"
                          "ためFULL_HOUSEでM03参加",
                "card_plan": "M03でFULL_HOUSEを使用",
            },
        )
        assert "FULL_HOUSE は現在の手札にありません" in prompt
        assert '"card": "FULL_HOUSE"' not in prompt

    def test_p06_r7_flush_missing(self):
        hand = _hand_without(CardRank.FLUSH)
        player = make_player("P06", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        markets = [make_market("M03", 1_000_000)]
        prompt = build_commit_prompt(
            player, markets, 7, _base_visible_state(), config,
            last_strategy={
                "reason": "自分はR7以降M03でFLUSHを使い、安定勝利を目指す",
                "card_plan": "M03でFLUSHを使用",
            },
        )
        assert "FLUSH は現在の手札にありません" in prompt
        assert '"card": "FLUSH"' not in prompt


# =====================================================================
# T-P10: 助言表現の不在（事実通知のみ）
# =====================================================================

class TestNoAdviceLanguage:
    # 「推奨ではありません」という事実の注記（否定形）は助言ではないため対象外とする。
    # ここでは「〜すべき」「〜を推奨します」等の肯定形の指示・評価表現のみを検出する。
    ADVICE_WORDS = ["すべきです", "推奨します", "推奨されます", "注意しましょう", "気をつけましょう"]

    def test_commit_prompt_has_no_advice_language(self):
        hand = _hand_without(CardRank.ONE_PAIR)
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        markets = [make_market("M01", 1_000_000)]
        visible_state = _base_visible_state()
        visible_state["my_auto_commits"] = [{
            "round": 3, "requested_market_id": "M01", "requested_card_rank": "ONE_PAIR",
            "reason": "Card ONE_PAIR not in hand",
            "actual_market_id": "M01", "actual_card": "HIGH_CARD_1",
        }]
        prompt = build_commit_prompt(
            player, markets, 4, visible_state, config,
        )
        for w in self.ADVICE_WORDS:
            assert w not in prompt

    def test_negotiation_prompt_has_no_advice_language(self):
        hand = _hand_without(CardRank.ONE_PAIR)
        player = make_player("P09", cash=1_000_000, hand=hand)
        config = GameConfig.baseline_v1(9)
        visible_state = _base_visible_state()
        visible_state["my_auto_commits"] = [{
            "round": 3, "requested_market_id": "M01", "requested_card_rank": "ONE_PAIR",
            "reason": "Card ONE_PAIR not in hand",
            "actual_market_id": "M01", "actual_card": "HIGH_CARD_1",
        }]
        prompt = build_negotiation_prompt(player, 4, 1, visible_state, config)
        for w in self.ADVICE_WORDS:
            assert w not in prompt
        assert "AUTO COMMIT" in prompt
        assert "ONE_PAIR" in prompt
