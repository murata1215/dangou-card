"""
Cycle 2 Phase D 受け入れテスト（API-free）

対応プラン: doc計画の Phase C（P0-1〜P0-5, P1-1, P1-3）が実際に
「材料は全部あった上でAIが選んだ結果」と言い切れる状態を作れているかを、
実APIを呼ばずにプロンプト本文の文字列検査だけで検証する。

D-1: 矛盾する型B義務3型すべてに注記が出ること
D-2: 手札に無いランクを要求するtype_b_card義務に不一致注記が出ること
D-3: 強制最低返済の実額がengineの実測値（P06 R9 / P01 R11相当）と完全一致すること
D-4: build_double_up_promptに処理順・実額・TAKE/DOUBLE両方の差引後現金が出ること
D-5: Entry Feeの実額と徴収タイミング・充足/不足が境界値で正しく出ること
D-6: stale Memoryと現在値が食い違っても、財務・義務ブロックはengine真値を表示すること
D-7: 助言語彙が新規追加テキストに含まれないこと
"""

import math

import pytest

from engine.config import GameConfig
from engine.models import Card, CardRank, Market, PlayerState
from engine.player import apply_interest, compute_mandatory_repayment
from llm.prompt_builder import (
    _render_finance_block,
    _render_obligations_block,
    build_commit_prompt,
    build_double_up_prompt,
    build_negotiation_prompt,
    build_reflection_prompt,
)
from tests.conftest import make_market, make_player


def _hand(*ranks: CardRank) -> list[Card]:
    return [Card(card_id=f"c{i}", rank=r) for i, r in enumerate(ranks)]


# ---------------------------------------------------------------------------
# D-1: 矛盾する型B義務3型すべてに注記が出ること
# ---------------------------------------------------------------------------

class TestD1ConflictDetection:
    def test_two_type_b_card_obligations_conflict(self):
        """同一Rの type_b_card 義務2件（TWO_PAIR × ONE_PAIR）→ カード矛盾警告"""
        visible = {"my_obligations": [
            {"contract_id": "C_f12e03a9", "obligor": "P11", "counterparty": "P06",
             "ob_type": "type_b_card", "round_num": 3,
             "details": {"card_rank": "TWO_PAIR"}},
            {"contract_id": "C_37a0020b", "obligor": "P11", "counterparty": "P04",
             "ob_type": "type_b_card", "round_num": 3,
             "details": {"card_rank": "ONE_PAIR"}},
        ]}
        lines = _render_obligations_block("P11", visible, 3)
        text = "\n".join(lines)
        assert "C_f12e03a9" in text and "C_37a0020b" in text
        assert "カード使用義務が2件あります" in text
        assert "1ラウンドに提出できるカードは1枚です" in text
        # Cycle 2 wording-only fix: 結果断定・警告記号を排し事実提示のみにする
        assert "⚠" not in text
        assert "必ず違反" not in text and "脱落" not in text

    def test_two_different_type_b_market_obligations_conflict(self):
        """同一Rの type_b_market 義務2件（異なる市場）→ 市場矛盾警告"""
        visible = {"my_obligations": [
            {"contract_id": "C_m1", "obligor": "P01", "counterparty": "P02",
             "ob_type": "type_b_market", "round_num": 5,
             "details": {"market_id": "M01"}},
            {"contract_id": "C_m2", "obligor": "P01", "counterparty": "P03",
             "ob_type": "type_b_market", "round_num": 5,
             "details": {"market_id": "M02"}},
        ]}
        lines = _render_obligations_block("P01", visible, 5)
        text = "\n".join(lines)
        assert "市場指定義務が異なる市場で2件" in text
        assert "M01" in text and "M02" in text
        assert "1ラウンドに参加できる市場は1つです" in text
        # Cycle 2.2 wording-only fix: 結果断定・警告記号を排し事実提示のみにする
        assert "⚠" not in text
        assert "必ず違反" not in text and "脱落" not in text

    def test_type_b_market_and_type_b_no_market_same_market_conflict(self):
        """同一Rの type_b_market M01 と type_b_no_market M01 → 参加/不参加矛盾警告"""
        visible = {"my_obligations": [
            {"contract_id": "C_p", "obligor": "P01", "counterparty": "P02",
             "ob_type": "type_b_market", "round_num": 7,
             "details": {"market_id": "M01"}},
            {"contract_id": "C_np", "obligor": "P01", "counterparty": "P03",
             "ob_type": "type_b_no_market", "round_num": 7,
             "details": {"market_id": "M01"}},
        ]}
        lines = _render_obligations_block("P01", visible, 7)
        text = "\n".join(lines)
        assert "M01" in text
        assert "参加を指定する義務と不参加を指定する義務が両方あります" in text
        # Cycle 2.2 wording-only fix: 結果断定・警告記号を排し事実提示のみにする
        assert "⚠" not in text
        assert "矛盾" not in text
        assert "必ず違反" not in text and "脱落" not in text

    def test_no_conflict_warning_for_unrelated_obligations(self):
        """矛盾しない義務の組では警告が出ないこと（過検出の否定）"""
        visible = {"my_obligations": [
            {"contract_id": "C_a", "obligor": "P01", "counterparty": "P02",
             "ob_type": "type_a_payment", "round_num": 5,
             "details": {"amount": 100_000}},
            {"contract_id": "C_b", "obligor": "P01", "counterparty": "P03",
             "ob_type": "type_b_market", "round_num": 5,
             "details": {"market_id": "M01"}},
        ]}
        lines = _render_obligations_block("P01", visible, 5)
        text = "\n".join(lines)
        assert "⚠" not in text


# ---------------------------------------------------------------------------
# D-2: 手札に無いランクを要求するtype_b_card義務に不一致注記が出ること
# ---------------------------------------------------------------------------

class TestD2HandCrossCheck:
    def test_obligation_requiring_rank_not_in_hand_is_flagged(self):
        """P09 R6相当: 義務がFULL_HOUSEを要求するが手札に無い"""
        visible = {"my_obligations": [
            {"contract_id": "C_fa84b438", "obligor": "P09", "counterparty": "P05",
             "ob_type": "type_b_card", "round_num": 6,
             "details": {"card_rank": "FULL_HOUSE"}},
        ]}
        hand_rank_names = {
            "HIGH_CARD", "THREE_OF_A_KIND", "STRAIGHT", "FLUSH",
            "FOUR_OF_A_KIND", "STRAIGHT_FLUSH", "ROYAL_FLUSH",
        }
        lines = _render_obligations_block("P09", visible, 6, hand_rank_names)
        text = "\n".join(lines)
        assert "FULL_HOUSE" in text
        assert "現在の手札にありません" in text
        assert "現在の手札にあります" not in text

    def test_obligation_requiring_rank_in_hand_is_confirmed(self):
        """要求ランクが手札にある場合は「あります」の事実注記が出る"""
        visible = {"my_obligations": [
            {"contract_id": "C_ok", "obligor": "P09", "counterparty": "P05",
             "ob_type": "type_b_card", "round_num": 6,
             "details": {"card_rank": "FLUSH"}},
        ]}
        hand_rank_names = {"FLUSH", "HIGH_CARD"}
        lines = _render_obligations_block("P09", visible, 6, hand_rank_names)
        text = "\n".join(lines)
        assert "現在の手札にあります" in text
        assert "現在の手札にありません" not in text

    def test_no_hand_note_when_hand_rank_names_not_provided(self):
        """hand_rank_names未指定（後方互換）では突合注記が出ないこと"""
        visible = {"my_obligations": [
            {"contract_id": "C_ok", "obligor": "P09", "counterparty": "P05",
             "ob_type": "type_b_card", "round_num": 6,
             "details": {"card_rank": "FULL_HOUSE"}},
        ]}
        lines = _render_obligations_block("P09", visible, 6)
        text = "\n".join(lines)
        assert "現在の手札に" not in text


# ---------------------------------------------------------------------------
# D-3: 強制最低返済の実額がengine実測値と完全一致すること
# ---------------------------------------------------------------------------

class TestD3MandatoryRepayForecastMatchesEngine:
    def test_p06_r9_case_matches_engine_exactly(self):
        """P06 R9相当: debt 450,596 / cash 84,063 / round 9 of 12"""
        config = GameConfig.baseline_v1_s2(num_players=12)
        debt_balance = 450_596
        cash = 84_063
        round_num = 9

        # engineの実装そのものを直接計算（二重実装チェック用の参照値）
        dummy = make_player("P06", cash=cash, debt=debt_balance)
        after_interest = apply_interest(dummy, config.interest_rate)
        expected_interest = after_interest.debt_balance - debt_balance
        expected_divisor = config.num_rounds - round_num + 1
        expected_repay = compute_mandatory_repayment(
            after_interest.debt_balance, expected_divisor, config.mandatory_repay_k,
        )

        assert expected_interest == 6_759
        assert after_interest.debt_balance == 457_355
        assert expected_divisor == 4
        assert expected_repay == 114_339

        player = make_player("P06", cash=cash, debt=debt_balance)
        prompt = build_commit_prompt(
            player, [make_market("M01", 500_000)], round_num, {"used_cards": {}}, config,
        )
        assert f"+{expected_interest}円" in prompt
        assert f"{after_interest.debt_balance}円" in prompt
        assert f"除数（このラウンドを含む残りラウンド数{expected_divisor}" in prompt
        assert f"= {expected_repay}円" in prompt
        assert f"強制最低返済額との差額: {cash - expected_repay}円" in prompt
        # Cycle 2.2 wording-only fix: 結果断定・警告記号を排し数値事実のみにする
        assert "⚠" not in prompt
        assert "不足します" not in prompt
        assert "MANDATORY_REPAY_FAILED" not in prompt

    def test_p01_r11_case_matches_engine_exactly(self):
        """P01 R11相当: debt 1,279,974 / round 11 of 12"""
        config = GameConfig.baseline_v1_s2(num_players=12)
        debt_balance = 1_279_974
        round_num = 11

        dummy = make_player("P01", cash=0, debt=debt_balance)
        after_interest = apply_interest(dummy, config.interest_rate)
        expected_interest = after_interest.debt_balance - debt_balance
        expected_divisor = config.num_rounds - round_num + 1
        expected_repay = compute_mandatory_repayment(
            after_interest.debt_balance, expected_divisor, config.mandatory_repay_k,
        )

        assert expected_interest == 19_200
        assert after_interest.debt_balance == 1_299_174
        assert expected_divisor == 2
        assert expected_repay == 649_587

        player = make_player("P01", cash=350_854, debt=debt_balance)
        prompt = build_negotiation_prompt(player, round_num, 1, {"markets": []}, config)
        assert f"+{expected_interest}円" in prompt
        assert f"= {expected_repay}円" in prompt

    def test_finance_block_deficit_line_is_numeric_fact_only(self):
        """
        Cycle 2.2 wording-only fix: _render_finance_block の不足行が、
        結果断定・警告記号を含まない数値事実のみになっていること
        （P06 R9相当: debt 450,596 / cash 84,063 / round 9 of 12）
        """
        config = GameConfig.baseline_v1_s2(num_players=12)
        lines = _render_finance_block(
            cash=84_063, debt_balance=450_596, forecast_round=9, config=config,
        )
        text = "\n".join(lines)
        assert "差引後の現金見込み: 84063円 − 114339円 = -30276円" in text
        assert "強制最低返済額との差額: -30276円" in text
        assert "⚠" not in text
        assert "不足します" not in text and "MANDATORY_REPAY_FAILED" not in text


# ---------------------------------------------------------------------------
# D-4: build_double_up_prompt に処理順・実額・TAKE/DOUBLE両方の差引後現金
# ---------------------------------------------------------------------------

class TestD4DoubleUpFinanceForecast:
    def test_p01_r11_double_up_case(self):
        """
        P01 R11相当: cash(賞金反映後)=1,290,854 / debt=1,279,974 / prize_won=940,000
        DOUBLEを選ぶと差引後現金がマイナス（不足298,733円）になることが
        プロンプト本文の数値だけから読み取れること
        """
        config = GameConfig.baseline_v1_s2(num_players=12)
        round_num = 11
        prize_won = 940_000
        player = make_player("P01", cash=1_290_854, debt=1_279_974)

        prompt = build_double_up_prompt(player, prize_won, round_num, {}, config)

        # 処理順の事実
        assert "この選択の直後、同じR内でFinance" in prompt

        # TAKE側の見込み
        assert "TAKEを選んだ場合の現金 1290854円" in prompt
        assert "Finance後の現金見込み: 641267円" in prompt

        # DOUBLE側の見込み（不足になること）
        assert "DOUBLEを選んだ場合の現金 1290854円 − 預託940000円 = 350854円" in prompt
        assert "Finance後の現金見込み: -298733円" in prompt
        assert "DOUBLE選択時のFinance後現金見込み: -298733円" in prompt
        assert "強制最低返済額649587円に対する差額: -298733円" in prompt
        # Cycle 2 wording-only fix: 結果断定・警告記号を排し数値事実のみにする
        assert "⚠" not in prompt
        assert "不足します" not in prompt
        assert "MANDATORY_REPAY_FAILED" not in prompt

    def test_take_side_has_no_deficit_when_sufficient(self):
        """TAKE/DOUBLEともに不足しない盤面では警告が出ないこと"""
        config = GameConfig.baseline_v1_s2(num_players=12)
        player = make_player("P02", cash=5_000_000, debt=100_000)
        prompt = build_double_up_prompt(player, 200_000, 3, {}, config)
        assert "不足します" not in prompt


# ---------------------------------------------------------------------------
# D-5: Entry Feeの実額と徴収タイミング・充足/不足の境界
# ---------------------------------------------------------------------------

class TestD5EntryFeeBoundary:
    def test_commit_prompt_shows_entry_fee_amount_and_timing(self):
        config = GameConfig.baseline_v1(num_players=12)
        player = make_player("P07", cash=config.entry_fee - 1, debt=0)
        prompt = build_commit_prompt(
            player, [make_market("M01", 500_000)], 10, {"used_cards": {}}, config,
        )
        assert f"Entry Fee {config.entry_fee}円" in prompt
        assert "自動徴収" in prompt

    def test_reflection_prompt_reports_shortfall_at_boundary(self):
        """現金がEntry Feeちょうど1円不足 → 次Rで「不足」と表示"""
        config = GameConfig.baseline_v1(num_players=12)
        player = make_player("P07", cash=config.entry_fee - 1, debt=380_000)
        prompt = build_reflection_prompt(player, 10, {}, config)
        assert "不足" in prompt

    def test_reflection_prompt_reports_sufficiency_at_boundary(self):
        """現金がEntry Feeちょうど → 次Rで「充足」と表示"""
        config = GameConfig.baseline_v1(num_players=12)
        player = make_player("P07", cash=config.entry_fee, debt=380_000)
        prompt = build_reflection_prompt(player, 10, {}, config)
        assert "充足" in prompt


# ---------------------------------------------------------------------------
# D-6: stale Memoryと現在値が食い違っても、財務・義務ブロックはengine真値
# ---------------------------------------------------------------------------

class TestD6CurrentValuesOverrideStaleMemory:
    def test_finance_block_uses_current_state_not_stale_memory_numbers(self):
        """
        古いMemoryに全く異なる（間違った）財務数値が書かれていても、
        プロンプトの財務ブロックはengine真値（現在のcash/debt）から計算される
        """
        config = GameConfig.baseline_v1_s2(num_players=12)
        stale_memory = (
            "【R8時点】現金は500万円、借金はゼロ。返済の心配は一切ない。"
        )
        player = make_player("P06", cash=84_063, debt=450_596)
        prompt = build_negotiation_prompt(
            player, 9, 1, {"markets": []}, config, memory=stale_memory,
        )
        # stale memoryの数字も出てはいるが（引き継ぎメモリとして提示はする）、
        # 現在値優先の警告と、実際の財務ブロックの数値は現在値そのものである
        assert stale_memory in prompt
        assert "常に正しい現状" in prompt
        assert "114339円" in prompt  # engine真値の強制最低返済額
        assert "84063円" not in prompt.split("あなたの状態")[0]  # memory部分に紛れ込んでいない


# ---------------------------------------------------------------------------
# D-7: 助言語彙が新規追加テキストに含まれないこと
# ---------------------------------------------------------------------------

class TestP05PreSignPreview:
    """P0-5: 署名した場合の合流後義務プレビューが矛盾を検出できること"""

    def test_pending_contract_merge_preview_detects_conflict(self):
        config = GameConfig.baseline_v1_s2(num_players=12)
        player = PlayerState(
            player_id="P01", cash=350_854, debt_balance=1_279_974,
            initial_loan=1_279_974,
            hand=_hand(CardRank.HIGH_CARD, CardRank.FLUSH),
        )
        visible = {
            "markets": [{"market_id": "M01", "prize_pool": 500_000}],
            "alive_players": ["P01", "P02"],
            "messages": [],
            "contracts_pending": [
                {
                    "contract_id": "C_new", "proposer": "P02", "parties": ["P01", "P02"],
                    "signed_by": ["P02"], "round_created": 11,
                    "obligations": [
                        {"obligor": "P01", "counterparty": "P02",
                         "ob_type": "type_b_card", "round_num": 11,
                         "details": {"card_rank": "FULL_HOUSE"}},
                    ],
                },
            ],
            "trades_pending": [],
            "my_obligations": [
                {"contract_id": "C_a", "obligor": "P01", "counterparty": "P03",
                 "ob_type": "type_b_card", "round_num": 11,
                 "details": {"card_rank": "ONE_PAIR"}},
            ],
            "double_ups": [],
        }
        prompt = build_negotiation_prompt(player, 11, 1, visible, config)
        assert "署名した場合、あなたの義務（既存分と合流後）は次のようになります" in prompt
        assert "C_a" in prompt and "C_new" in prompt
        assert "カード使用義務が2件" in prompt


class TestD7NoAdviceVocabulary:
    ADVICE_WORDS = ["すべき", "推奨します", "危険", "安全な", "おすすめ", "注意しましょう"]

    def _all_prompts(self) -> list[str]:
        config = GameConfig.baseline_v1_s2(num_players=12)
        player_with_conflict = PlayerState(
            player_id="P01", cash=350_854, debt_balance=1_279_974,
            initial_loan=1_279_974, hand=_hand(CardRank.HIGH_CARD, CardRank.FLUSH),
        )
        visible = {
            "markets": [{"market_id": "M01", "prize_pool": 500_000}],
            "alive_players": ["P01", "P02"],
            "messages": [],
            "contracts_pending": [
                {
                    "contract_id": "C_new", "proposer": "P02", "parties": ["P01", "P02"],
                    "signed_by": ["P02"], "round_created": 11,
                    "obligations": [
                        {"obligor": "P01", "counterparty": "P02",
                         "ob_type": "type_b_card", "round_num": 11,
                         "details": {"card_rank": "FULL_HOUSE"}},
                    ],
                },
            ],
            "trades_pending": [],
            "my_obligations": [
                {"contract_id": "C_a", "obligor": "P01", "counterparty": "P03",
                 "ob_type": "type_b_card", "round_num": 11,
                 "details": {"card_rank": "ONE_PAIR"}},
            ],
            "double_ups": [],
        }
        markets = [Market(market_id="M01", base_prize=500_000, carryover=0)]

        prompts = [
            build_negotiation_prompt(player_with_conflict, 11, 1, visible, config),
            build_commit_prompt(player_with_conflict, markets, 11, visible, config),
            build_reflection_prompt(player_with_conflict, 11, visible, config),
            build_double_up_prompt(player_with_conflict, 300_000, 11, visible, config),
        ]
        return prompts

    def test_no_advice_vocabulary_in_any_finance_or_obligation_prompt(self):
        for prompt in self._all_prompts():
            for word in self.ADVICE_WORDS:
                assert word not in prompt, f"助言語彙 '{word}' が含まれています"

    JUDGMENT_WORDS = [
        "おすすめ", "推奨", "安全", "危険", "履行不能", "署名すべき", "署名しない",
        "返済すべき", "避けるべき", "した方が", "必ず違反で脱落", "矛盾", "すべき",
    ]

    def test_no_judgment_vocabulary_in_cycle2_blocks(self):
        """
        Cycle 2.2 wording-only fix: Cycle 2で追加した各ブロック（Finance不足行・
        義務矛盾3型・倍掛け処理順ブロック）単体に、結果断定/助言語彙が
        含まれていないことを機械確認する（RULES_SUMMARY等の既存文言による
        偽陽性を避けるため、対象をCycle 2追加ブロックのテキストに限定する）
        """
        config = GameConfig.baseline_v1_s2(num_players=12)

        # 対象1: Finance不足あり/なし両ケース
        finance_deficit_text = "\n".join(_render_finance_block(
            cash=84_063, debt_balance=450_596, forecast_round=9, config=config,
        ))
        finance_ok_text = "\n".join(_render_finance_block(
            cash=5_000_000, debt_balance=100_000, forecast_round=3, config=config,
        ))

        # 対象2: 義務矛盾3型（card / market / market×no_market）
        card_conflict_text = "\n".join(_render_obligations_block("P11", {
            "my_obligations": [
                {"contract_id": "C_a", "obligor": "P11", "counterparty": "P06",
                 "ob_type": "type_b_card", "round_num": 3,
                 "details": {"card_rank": "TWO_PAIR"}},
                {"contract_id": "C_b", "obligor": "P11", "counterparty": "P04",
                 "ob_type": "type_b_card", "round_num": 3,
                 "details": {"card_rank": "ONE_PAIR"}},
            ],
        }, 3))
        market_conflict_text = "\n".join(_render_obligations_block("P01", {
            "my_obligations": [
                {"contract_id": "C_m1", "obligor": "P01", "counterparty": "P02",
                 "ob_type": "type_b_market", "round_num": 5,
                 "details": {"market_id": "M01"}},
                {"contract_id": "C_m2", "obligor": "P01", "counterparty": "P03",
                 "ob_type": "type_b_market", "round_num": 5,
                 "details": {"market_id": "M02"}},
            ],
        }, 5))
        no_market_conflict_text = "\n".join(_render_obligations_block("P01", {
            "my_obligations": [
                {"contract_id": "C_p", "obligor": "P01", "counterparty": "P02",
                 "ob_type": "type_b_market", "round_num": 7,
                 "details": {"market_id": "M01"}},
                {"contract_id": "C_np", "obligor": "P01", "counterparty": "P03",
                 "ob_type": "type_b_no_market", "round_num": 7,
                 "details": {"market_id": "M01"}},
            ],
        }, 7))

        # 対象3: 倍掛け処理順ブロック（見出しから次の "##" 見出し直前まで）
        player = make_player("P01", cash=1_290_854, debt=1_279_974)
        double_up_prompt = build_double_up_prompt(player, 940_000, 11, {}, config)
        start = double_up_prompt.index("## この選択の直後に実行される処理")
        rest = double_up_prompt[start:]
        end_rel = rest.find("\n##", 1)
        double_up_block = rest if end_rel == -1 else rest[:end_rel]

        targets = {
            "finance_deficit": finance_deficit_text,
            "finance_ok": finance_ok_text,
            "card_conflict": card_conflict_text,
            "market_conflict": market_conflict_text,
            "no_market_conflict": no_market_conflict_text,
            "double_up_block": double_up_block,
        }
        for name, text in targets.items():
            for word in self.JUDGMENT_WORDS:
                assert word not in text, f"{name} に助言/断定語彙 '{word}' が含まれています"
