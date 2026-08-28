"""
Cycle 8 修正3: strategyへのカテゴリenum追加テスト

`normalize_reason_category`（Cycle 5対称性原則を厳守した8種の中立な
候補列挙。優劣を示唆する評価語彙は含めない）が
- 正常値はそのまま通す
- 列挙外の値は None にする
- 欠落時も None にする（ParseErrorにしない＝リトライを誘発しない）
- 交渉プロンプトの user prompt 側に全8候補が出現する（system prompt側は
  7100字上限の残余21字しかないため対象外。plan判断のとおり）
- 既存のシステムプロンプト/交渉プロンプトの文字数予算（7100字 / 6400字 / 3550字）
  を超えない
- 追加ブロックに評価語彙（既存test_prompt_finance.pyのADVICE_WORDS/
  JUDGMENT_WORDSと同一手法）が含まれない
- llm_logger.log_call() の初期エントリに reason_category キーがあり、
  llm_agent._update_last_log_emotion() で後付けされる
を検証する。
"""

from engine.config import GameConfig
from llm.prompt_builder import build_system_prompt, build_negotiation_prompt
from llm.response_parser import (
    normalize_reason_category, VALID_REASON_CATEGORIES, parse_response,
)
from llm.llm_logger import LLMLogger
from tests.test_cycle5_prompt_salience import _make_player, _base_visible_state


# tests/test_prompt_finance.py と同一の評価語彙リスト（対称性原則の機械保証）
ADVICE_WORDS = ["すべき", "推奨します", "危険", "安全な", "おすすめ", "注意しましょう"]
JUDGMENT_WORDS = [
    "おすすめ", "推奨", "安全", "危険", "履行不能", "署名すべき", "署名しない",
    "返済すべき", "避けるべき", "した方が", "必ず違反で脱落", "矛盾", "すべき",
]


class TestNormalizeReasonCategory:
    """normalize_reason_categoryの正規化ロジック"""

    def test_valid_category_passes_through(self):
        strategy = {"reason_category": "行動枠温存"}
        result = normalize_reason_category(strategy)
        assert result["reason_category"] == "行動枠温存"

    def test_invalid_category_becomes_none(self):
        strategy = {"reason_category": "有利な選択"}
        result = normalize_reason_category(strategy)
        assert result["reason_category"] is None

    def test_missing_category_becomes_none(self):
        strategy = {}
        result = normalize_reason_category(strategy)
        assert result["reason_category"] is None

    def test_all_candidates_are_valid(self):
        for cat in VALID_REASON_CATEGORIES:
            result = normalize_reason_category({"reason_category": cat})
            assert result["reason_category"] == cat

    def test_parse_response_applies_normalization(self):
        """parse_responseの戻り値strategyにreason_categoryが正規化されて載る（列挙外→None）"""
        text = '{"strategy": {"reason_category": "存在しない値"}, "action": {"type": "pass"}}'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert strategy["reason_category"] is None

    def test_parse_response_preserves_valid_category(self):
        text = '{"strategy": {"reason_category": "戦略的沈黙"}, "action": {"type": "pass"}}'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert strategy["reason_category"] == "戦略的沈黙"


class TestNegotiationPromptCategoryEnumeration:
    """交渉プロンプトに全8候補が事実の列挙として出現すること"""

    def test_all_eight_candidates_present(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=800_000, debt=300_000)
        vs = _base_visible_state()
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        for cat in VALID_REASON_CATEGORIES:
            assert cat in prompt, f"候補 '{cat}' が交渉プロンプトに出現しません"

    def test_reason_category_not_in_system_prompt(self):
        """system_promptの7100字上限（残余21字）には入らない配置判断の裏付け"""
        config = GameConfig.baseline_v1_s2(12)
        prompt = build_system_prompt("P01", config)
        assert "reason_category" not in prompt


class TestPromptLengthBudgetUnaffected:
    """既存の文字数予算（Cycle 5機械保証）を超えないこと"""

    def test_system_prompt_length_budget_unaffected(self):
        config = GameConfig.baseline_v1_s2(12)
        prompt = build_system_prompt("P01", config)
        assert len(prompt) <= 7100, len(prompt)

    def test_negotiation_prompt_length_budget_unaffected(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=800_000, debt=300_000)

        pending_contract = {
            "proposer": "P02", "contract_id": "C_PEND1", "round_created": 5,
            "parties": ["P01", "P02"], "signed_by": ["P02"],
            "obligations": [
                {"obligor": "P02", "counterparty": "P01", "ob_type": "type_a_payment",
                 "round_num": 6, "details": {"amount": 300_000}},
            ],
        }
        trade_pending = {
            "trade_id": "T_1", "proposer": "P02", "round_proposed": 6, "cash_amount": 50_000,
            "give_card_rank": "ONE_PAIR", "with_player": "P01", "receive_card_rank": "FLUSH",
        }
        messages = [
            {"sender": f"P{(i % 5) + 2:02d}", "type": "broadcast", "message": "x" * 60}
            for i in range(10)
        ]
        memory = "あ" * 3000
        worst_vs = _base_visible_state(
            markets=[
                {"market_id": "M01", "prize_pool": 900_000, "base_prize": 900_000, "carryover": 0},
                {"market_id": "M02", "prize_pool": 1_200_000, "base_prize": 600_000, "carryover": 600_000},
                {"market_id": "M03", "prize_pool": 500_000, "base_prize": 500_000, "carryover": 0},
            ],
            alive_players=[f"P{i:02d}" for i in range(1, 13)],
            double_ups=[{"player_id": "P01", "deposit": 400_000, "success_round": 7}],
            messages=messages,
            contracts_pending=[pending_contract],
            trades_pending=[trade_pending],
        )
        worst_prompt = build_negotiation_prompt(player, 6, 5, worst_vs, config, memory=memory)
        assert len(worst_prompt) <= 6400, len(worst_prompt)

        std_vs = _base_visible_state(
            markets=[{"market_id": "M01", "prize_pool": 900_000, "base_prize": 900_000, "carryover": 0}],
            alive_players=[f"P{i:02d}" for i in range(1, 13)],
            messages=[{"sender": "P02", "type": "broadcast", "message": "hello there"}],
        )
        std_memory = "い" * 1264
        std_prompt = build_negotiation_prompt(player, 6, 3, std_vs, config, memory=std_memory)
        assert len(std_prompt) <= 3550, len(std_prompt)


class TestSymmetryVocabularyScan:
    """Cycle 8で追加したブロックに評価語彙が含まれないこと（対称性原則の機械保証）"""

    def _category_block(self) -> str:
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=800_000, debt=300_000)
        vs = _base_visible_state()
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        lines = prompt.split("\n")
        idx = next(i for i, l in enumerate(lines) if l.startswith("strategyには任意で"))
        return lines[idx]

    def test_no_advice_words_in_category_block(self):
        block = self._category_block()
        for word in ADVICE_WORDS:
            assert word not in block, f"助言語彙 '{word}' が含まれています"

    def test_no_judgment_words_in_category_block(self):
        block = self._category_block()
        for word in JUDGMENT_WORDS:
            assert word not in block, f"助言/断定語彙 '{word}' が含まれています"


class TestLLMLoggerReasonCategoryField:
    """llm_loggerのlog_call初期キー + post-hoc反映"""

    def test_log_call_initializes_reason_category_key(self, tmp_path):
        logger = LLMLogger(str(tmp_path), game_id="test")
        logger.log_call(
            player_id="P01", model_id="test-model", phase="negotiation",
            round_num=1, turn=1, system_prompt="s", user_prompt="u",
            response_text='{"strategy":{},"action":{"type":"pass"}}',
            usage={"input_tokens": 10, "output_tokens": 5}, cost=0.001, elapsed_ms=10.0,
        )
        assert logger.entries[-1]["reason_category"] is None

    def test_update_last_log_emotion_sets_reason_category(self, tmp_path):
        from llm.llm_agent import LLMAgent
        from llm.models import ModelInfo

        model_info = ModelInfo(
            model_id="test-model", provider="Test", name="Test",
            adapter_type="anthropic", input_price=1.0, output_price=5.0,
            env_key="TEST_KEY", base_url=None,
        )
        logger = LLMLogger(str(tmp_path), game_id="test")
        logger.log_call(
            player_id="P01", model_id="test-model", phase="negotiation",
            round_num=1, turn=1, system_prompt="s", user_prompt="u",
            response_text='{"strategy":{},"action":{"type":"pass"}}',
            usage={"input_tokens": 10, "output_tokens": 5}, cost=0.001, elapsed_ms=10.0,
        )
        agent = LLMAgent("P01", model_info, adapter=None, llm_logger=logger)
        agent._update_last_log_emotion({"reason_category": "行動枠温存"})
        assert logger.entries[-1]["reason_category"] == "行動枠温存"

    def test_update_last_log_emotion_missing_category_stays_none(self, tmp_path):
        from llm.llm_agent import LLMAgent
        from llm.models import ModelInfo

        model_info = ModelInfo(
            model_id="test-model", provider="Test", name="Test",
            adapter_type="anthropic", input_price=1.0, output_price=5.0,
            env_key="TEST_KEY", base_url=None,
        )
        logger = LLMLogger(str(tmp_path), game_id="test")
        logger.log_call(
            player_id="P01", model_id="test-model", phase="negotiation",
            round_num=1, turn=1, system_prompt="s", user_prompt="u",
            response_text='{"strategy":{},"action":{"type":"pass"}}',
            usage={"input_tokens": 10, "output_tokens": 5}, cost=0.001, elapsed_ms=10.0,
        )
        agent = LLMAgent("P01", model_info, adapter=None, llm_logger=logger)
        agent._update_last_log_emotion({})
        assert logger.entries[-1]["reason_category"] is None
