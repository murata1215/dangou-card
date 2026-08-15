"""
LLMモジュールのモックテスト

API呼出しは行わない。プロンプトビルダー・JSONパーサ・
リトライ・匿名化・コスト上限をテストする。
"""

import pytest

from engine.config import GameConfig
from engine.models import (
    PassAction, MarketCommitAction, DmAction, TransferAction,
    ContractProposeAction, BountyPostAction,
    CardRank, PlayerState, Card, Market,
)
from engine.cards import create_deck
from llm.prompt_builder import build_system_prompt, build_loan_prompt, build_negotiation_prompt, build_commit_prompt
from llm.response_parser import extract_json, parse_response, ParseError
from llm.llm_logger import LLMLogger
from llm.llm_agent import LLMAgent
from llm.models import ModelInfo, estimate_cost
from tests.conftest import make_player, make_market


class MockAdapter:
    """APIを呼ばないモックアダプタ"""

    def __init__(self, responses: list[str] | None = None):
        self._responses = responses or ['{"strategy":{},"action":{"type":"pass"}}']
        self._call_count = 0

    def complete(self, system, messages, max_tokens, temperature):
        idx = min(self._call_count, len(self._responses) - 1)
        text = self._responses[idx]
        self._call_count += 1
        return text, {"input_tokens": 100, "output_tokens": 50}


class TestPromptBuilder:
    """プロンプトビルダーのテスト"""

    def test_system_prompt_no_model_name(self):
        """システムプロンプトにモデル名が含まれないこと（匿名化）"""
        config = GameConfig.baseline_v1()
        prompt = build_system_prompt("P03", config)
        # モデル名が含まれないこと
        for name in ["Claude", "GPT", "Gemini", "Grok", "Kimi", "DeepSeek",
                      "Haiku", "Sonnet", "Opus", "anthropic", "openai"]:
            assert name.lower() not in prompt.lower(), f"Model name '{name}' found in prompt"
        # プレイヤーIDが含まれること
        assert "P03" in prompt

    def test_system_prompt_contains_rules(self):
        """システムプロンプトにルール要約が含まれること"""
        config = GameConfig.baseline_v1()
        prompt = build_system_prompt("P01", config)
        assert "カード" in prompt
        assert "市場" in prompt
        assert "借金" in prompt or "借入" in prompt
        assert "Free Cash" in prompt

    def test_loan_prompt_generated(self):
        """借入額選択プロンプトが生成されること"""
        config = GameConfig.baseline_v1()
        prompt = build_loan_prompt(config)
        assert "借入額" in prompt
        assert "120" in prompt  # 120万
        assert "1000" in prompt  # 1000万


class TestResponseParser:
    """レスポンスパーサーのテスト"""

    def test_valid_json_pass(self):
        """正常JSON → PassAction変換成功"""
        text = '{"strategy": {"plan": "test"}, "action": {"type": "pass"}}'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert isinstance(action, PassAction)
        assert strategy is not None

    def test_valid_json_market_commit(self):
        """正常JSON → MarketCommitAction変換成功"""
        text = '{"strategy": {}, "action": {"type": "market_commit", "market_id": "M01", "card": "ONE_PAIR"}}'
        strategy, action = parse_response(text, "P01", "commit")
        assert isinstance(action, MarketCommitAction)
        assert action.card_rank == "ONE_PAIR"
        assert action.market_id == "M01"

    def test_card_field_to_card_rank(self):
        """仕様§9.4の"card"キー → engineのcard_rankに変換"""
        text = '{"action": {"type": "market_commit", "market_id": "M02", "card": "FULL_HOUSE"}}'
        _, action = parse_response(text, "P01", "commit")
        assert isinstance(action, MarketCommitAction)
        assert action.card_rank == "FULL_HOUSE"

    def test_invalid_json_raises(self):
        """不正JSON → ParseError"""
        text = "これはJSONではありません"
        with pytest.raises(ParseError) as exc_info:
            parse_response(text, "P01", "negotiation")
        assert exc_info.value.correction_hint

    def test_json_in_code_block(self):
        """コードブロック内のJSON抽出"""
        text = '説明文\n```json\n{"strategy": {}, "action": {"type": "pass"}}\n```'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert isinstance(action, PassAction)

    def test_dm_action(self):
        """DM アクションの変換"""
        text = '{"strategy": {}, "action": {"type": "dm", "to": "P07", "message": "hello"}}'
        _, action = parse_response(text, "P01", "negotiation")
        assert isinstance(action, DmAction)
        assert action.to == "P07"

    def test_transfer_action(self):
        """送金アクションの変換"""
        text = '{"strategy": {}, "action": {"type": "transfer", "to": "P02", "amount": 500000}}'
        _, action = parse_response(text, "P01", "negotiation")
        assert isinstance(action, TransferAction)
        assert action.amount == 500000


class TestLLMAgent:
    """LLMAgentのモックテスト"""

    def _make_agent(self, responses=None):
        """テスト用LLMAgentを生成"""
        model_info = ModelInfo(
            model_id="test-model", provider="Test", name="Test",
            adapter_type="anthropic",
            input_price=1.0, output_price=5.0,
            env_key="TEST_KEY", base_url=None,
        )
        adapter = MockAdapter(responses)
        logger = LLMLogger("/tmp/test_llm_logs", game_id="test")
        agent = LLMAgent("P01", model_info, adapter, logger)
        return agent

    def test_negotiate_pass(self):
        """MockAdapterでnegotiateがPassActionを返すこと"""
        agent = self._make_agent()
        config = GameConfig.baseline_v1()
        agent.choose_loan(config)  # config保持
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        action = agent.negotiate(p, 1, 1, {"markets": [], "messages": [], "alive_players": ["P01"]})
        assert isinstance(action, PassAction)

    def test_commit_market_commit(self):
        """MockAdapterでcommitがMarketCommitActionを返すこと"""
        responses = ['{"strategy":{},"action":{"type":"market_commit","market_id":"M01","card":"ONE_PAIR"}}']
        agent = self._make_agent(responses)
        config = GameConfig.baseline_v1()
        agent.choose_loan(config)
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        markets = [make_market("M01", 500_000), make_market("M02", 300_000)]
        commit = agent.commit(p, markets, 1, {"markets": [], "messages": [], "used_cards": {}})
        assert isinstance(commit, MarketCommitAction)
        assert commit.market_id == "M01"

    def test_cost_limit_triggers_pass(self):
        """コスト上限超過でpassに切り替わること"""
        agent = self._make_agent()
        config = GameConfig.baseline_v1()
        agent.choose_loan(config)
        # コスト超過をシミュレート
        agent._cost_exceeded = True
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        action = agent.negotiate(p, 1, 1, {"markets": [], "messages": [], "alive_players": ["P01"]})
        assert isinstance(action, PassAction)


class TestBaselineV1:
    """baseline_v1プリセットのテスト"""

    def test_baseline_v1_flat_total(self):
        """baseline_v1のflat賞金の合計が正しいこと"""
        config = GameConfig.baseline_v1(num_players=8)
        expected_total = int(19_200_000 * 8 / 20 * 2.0)
        assert config.total_prize == expected_total
        assert sum(config.prize_tiers) == config.total_prize

    def test_baseline_v1_survival_cash(self):
        """baseline_v1の生還条件が200万円であること"""
        config = GameConfig.baseline_v1()
        assert config.survival_cash == 2_000_000

    def test_baseline_v1_flat_schedule(self):
        """baseline_v1がフラットスケジュールであること"""
        config = GameConfig.baseline_v1()
        base = config.prize_tiers[0]
        for t in config.prize_tiers[:-1]:
            assert t == base


class TestStep3CPrep:
    """Step 3C-prep Geminiアダプタのテスト"""

    def test_gemini_adapter_returns_openai_compat(self):
        """adapter_type="gemini"でOpenAICompatAdapterが返ること"""
        from llm.adapters import create_adapter, OpenAICompatAdapter
        from llm.models import ModelInfo
        model = ModelInfo(
            model_id="gemini-2.5-flash", provider="Google", name="Test",
            adapter_type="gemini",
            input_price=1.50, output_price=9.00,
            env_key="GEMINI_API_KEY",
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        )
        adapter = create_adapter(model)
        assert isinstance(adapter, OpenAICompatAdapter)

    def test_model_registry_has_gemini_base_url(self):
        """GeminiモデルにOpenAI互換base_urlが設定されていること"""
        from llm.models import MODEL_REGISTRY
        for key in ("M3", "L3"):
            model = MODEL_REGISTRY[key]
            assert model.adapter_type == "gemini"


class TestStep3CPrep2:
    """Step 3C-prep2 修正のテスト"""

    def test_extract_json_truncated_code_block(self):
        """truncatedコードブロック（閉じフェンスなし）からJSON抽出できること"""
        # Geminiが返すtruncated response形式
        text = '```json\n{"strategy": {"reason": "test"}, "action": {"type": "pass"}}\n余分なテキスト'
        data = extract_json(text)
        assert data is not None
        assert data["action"]["type"] == "pass"

    def test_extract_json_truncated_incomplete(self):
        """truncatedで不完全なJSON（閉じ括弧なし）はNoneを返すこと"""
        text = '```json\n{"strategy": {"reason": "test'
        data = extract_json(text)
        assert data is None

    def test_extract_json_normal_code_block_still_works(self):
        """通常のコードブロック（閉じフェンスあり）が引き続き動くこと"""
        text = '```json\n{"strategy": {}, "action": {"type": "pass"}}\n```'
        data = extract_json(text)
        assert data is not None
        assert data["action"]["type"] == "pass"

    def test_moonshot_base_url_is_ai(self):
        """Moonshotのbase_urlが api.moonshot.ai であること（.cnではない）"""
        from llm.models import MODEL_REGISTRY
        for key in ("M5", "L5"):
            model = MODEL_REGISTRY[key]
            assert "moonshot.ai" in (model.base_url or ""), f"{key}: {model.base_url}"

    def test_model_ids_are_current_gen(self):
        """モデルIDが最新世代であること"""
        from llm.models import MODEL_REGISTRY
        assert MODEL_REGISTRY["M2"].model_id == "gpt-4.1"
        assert MODEL_REGISTRY["L2"].model_id == "gpt-4.1-mini"
        assert MODEL_REGISTRY["M4"].model_id == "grok-4.5"
        assert MODEL_REGISTRY["L4"].model_id == "grok-4.3"
        assert MODEL_REGISTRY["M5"].model_id == "kimi-k2.6"

    def test_gemini_model_ids_are_3x(self):
        """GeminiモデルIDが3.x系であること"""
        from llm.models import MODEL_REGISTRY
        assert "3.5" in MODEL_REGISTRY["M3"].model_id
        assert "3.5" in MODEL_REGISTRY["L3"].model_id

    def test_default_max_tokens_is_4000(self):
        """DEFAULT_MAX_TOKENSが4000であること（reasoning系モデル対応）"""
        from llm.constants import DEFAULT_MAX_TOKENS
        assert DEFAULT_MAX_TOKENS == 4000


class TestEmotionFeature:
    """感情表示機能のテスト"""

    def test_emotion_parse_valid(self):
        """有効なemotion値が正しく抽出されること"""
        text = '{"strategy": {"reason": "test", "emotion": "焦"}, "action": {"type": "pass"}}'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert strategy["emotion"] == "焦"

    def test_emotion_parse_invalid_fallback(self):
        """列挙外のemotionが"平静"にフォールバックすること"""
        text = '{"strategy": {"reason": "test", "emotion": "怯"}, "action": {"type": "pass"}}'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert strategy["emotion"] == "平静"

    def test_emotion_missing_fallback(self):
        """emotionが欠落した場合に"平静"が設定されること"""
        text = '{"strategy": {"reason": "test"}, "action": {"type": "pass"}}'
        strategy, action = parse_response(text, "P01", "negotiation")
        assert strategy["emotion"] == "平静"

    def test_emotion_does_not_cause_retry(self):
        """emotion欠落でParseErrorにならないこと（リトライ対象外）"""
        text = '{"strategy": {"reason": "test"}, "action": {"type": "pass"}}'
        # ParseErrorが発生しなければOK
        strategy, action = parse_response(text, "P01", "negotiation")
        assert isinstance(action, PassAction)

    def test_prompt_contains_emotion_instruction(self):
        """プロンプトにemotion指示が含まれること"""
        config = GameConfig.baseline_v1()
        prompt = build_system_prompt("P01", config)
        assert "emotion" in prompt
        assert "喜" in prompt
        assert "怒" in prompt
        assert "哀" in prompt
        assert "楽" in prompt
        assert "焦" in prompt
        assert "疑" in prompt
        assert "奸" in prompt


class TestStep3C:
    """Step 3C 全LLM戦のテスト"""

    def test_phase_c_roster_is_8_models(self):
        """Phase Cロスターが8モデルであること"""
        from scripts.llm_trial import PHASE_C_ROSTER
        from llm.models import MODEL_REGISTRY
        assert len(PHASE_C_ROSTER) == 8
        # 全モデルキーがレジストリに存在すること
        for key in PHASE_C_ROSTER:
            assert key in MODEL_REGISTRY, f"{key} not in registry"

    def test_phase_c_covers_6_providers(self):
        """Phase Cロスターが6社をカバーすること"""
        from scripts.llm_trial import PHASE_C_ROSTER
        from llm.models import MODEL_REGISTRY
        providers = set()
        for key in PHASE_C_ROSTER:
            providers.add(MODEL_REGISTRY[key].provider)
        assert len(providers) >= 6, f"Only {len(providers)} providers: {providers}"

    def test_phase_c_anonymization(self):
        """Phase Cのsystem_promptにモデル名が含まれないこと"""
        config = GameConfig.baseline_v1()
        prompt = build_system_prompt("P03", config)
        # モデル名/ベンダー名が含まれないこと
        for name in ["Claude", "GPT", "Gemini", "Grok", "Kimi", "DeepSeek",
                      "Haiku", "Sonnet", "Opus", "anthropic", "openai", "google",
                      "moonshot", "xai"]:
            assert name.lower() not in prompt.lower(), f"'{name}' found in system prompt"

    def test_kimi_has_extended_timeout(self):
        """Kimi系モデルのタイムアウトが90秒であること（thinking無効化で短縮）"""
        from llm.models import MODEL_REGISTRY
        for key in ("M5", "L5"):
            assert MODEL_REGISTRY[key].timeout_seconds == 90

    def test_kimi_has_thinking_disabled(self):
        """Kimi系モデルにthinking無効化のextra_paramsが設定されていること"""
        from llm.models import MODEL_REGISTRY
        for key in ("M5", "L5"):
            ep = MODEL_REGISTRY[key].extra_params
            assert ep is not None
            assert ep.get("thinking", {}).get("type") == "disabled"

    def test_model_info_max_tokens_default_none(self):
        """ModelInfoのmax_tokensデフォルトがNoneであること"""
        from llm.models import ModelInfo
        m = ModelInfo(
            model_id="test", provider="Test", name="Test",
            adapter_type="openai_compat", input_price=1.0, output_price=1.0,
            env_key="TEST_KEY", base_url=None,
        )
        assert m.max_tokens is None
        assert m.extra_params is None

    def test_model_info_per_model_max_tokens(self):
        """ModelInfoのper-model max_tokensが設定可能であること"""
        from llm.models import ModelInfo
        m = ModelInfo(
            model_id="test", provider="Test", name="Test",
            adapter_type="openai_compat", input_price=1.0, output_price=1.0,
            env_key="TEST_KEY", base_url=None,
            max_tokens=8000,
            extra_params={"thinking": {"type": "disabled"}},
        )
        assert m.max_tokens == 8000
        assert m.extra_params["thinking"]["type"] == "disabled"

    def test_finish_reason_in_usage(self):
        """finish_reasonがusage dictに含まれること（adapters.pyの変更確認）"""
        # finish_reasonはアダプタがusage dictに含めて返す設計
        # ここではパーサのLENGTH_TRUNCATION_HINTが定義されていることを確認
        from llm.response_parser import LENGTH_TRUNCATION_HINT
        assert "出力トークン上限" in LENGTH_TRUNCATION_HINT
        assert "JSON" in LENGTH_TRUNCATION_HINT


class TestStep33CacheFix:
    """Step 3.3 キャッシュ修正のテスト"""

    def test_system_prompt_exceeds_cache_threshold(self):
        """system promptがAnthropicキャッシュ最小閾値(2048トークン)を超えること"""
        config = GameConfig.baseline_v1()
        prompt = build_system_prompt("P01", config)
        # 日本語混在テキストのトークン推定: JP×1.5 + EN/3.5
        jp_chars = sum(1 for c in prompt if ord(c) > 127)
        en_chars = len(prompt) - jp_chars
        est_tokens = int(jp_chars * 1.5 + en_chars / 3.5)
        # 2048トークンの閾値を超えていること
        assert est_tokens >= 2048, (
            f"System prompt estimated at {est_tokens} tokens, "
            f"need ≥2048 for Anthropic cache. Chars: {len(prompt)}"
        )

    def test_system_prompt_contains_detailed_rules(self):
        """詳細化されたルール要約が仕様書の重要セクションを含むこと"""
        config = GameConfig.baseline_v1()
        prompt = build_system_prompt("P01", config)
        # 追加された仕様書セクションが含まれること
        assert "脱落" in prompt and "3種" in prompt or "契約違反" in prompt
        assert "キャリーオーバー" in prompt
        assert "匿名通信" in prompt
        assert "口約束" in prompt
        assert "全員生還は算術的に不可能" in prompt
        assert "Atomic" in prompt


class TestStep31Fixes:
    """Step 3.1 修正のテスト"""

    def test_commit_prompt_contains_negotiation_log(self):
        """修正1: コミットプロンプトに交渉ログが含まれること"""
        from llm.prompt_builder import build_commit_prompt
        config = GameConfig.baseline_v1()
        p = make_player("P01", cash=3_000_000, debt=3_000_000)
        markets = [make_market("M01", 500_000), make_market("M02", 300_000)]
        negotiation_msgs = [
            {"sender": "P01", "type": "broadcast", "message": "M01に行くぞ", "turn": 1},
            {"sender": "P02", "type": "broadcast", "message": "M02を狙う", "turn": 1},
        ]
        last_strategy = {"target_market": "M01", "card_plan": "ROYAL_FLUSH"}

        prompt = build_commit_prompt(
            p, markets, 1, {"used_cards": {}}, config,
            negotiation_messages=negotiation_msgs,
            last_strategy=last_strategy,
        )
        # 交渉ログが含まれること
        assert "M01に行くぞ" in prompt
        assert "M02を狙う" in prompt
        # strategyメモが含まれること
        assert "target_market" in prompt
        assert "ROYAL_FLUSH" in prompt
        # 整合指示が含まれること
        assert "整合" in prompt

    def test_negotiation_rejects_market_commit(self):
        """修正2: 交渉フェイズでmarket_commitがParseErrorになること"""
        from llm.response_parser import parse_response, ParseError
        text = '{"strategy": {}, "action": {"type": "market_commit", "market_id": "M01", "card": "ONE_PAIR"}}'
        with pytest.raises(ParseError) as exc_info:
            parse_response(text, "P01", "negotiation")
        assert "交渉フェイズ" in str(exc_info.value)

    def test_market_commit_ok_in_commit_phase(self):
        """修正2: コミットフェイズではmarket_commitが正常に処理されること"""
        from llm.response_parser import parse_response
        text = '{"strategy": {}, "action": {"type": "market_commit", "market_id": "M01", "card": "ONE_PAIR"}}'
        strategy, action = parse_response(text, "P01", "commit")
        assert isinstance(action, MarketCommitAction)

    def test_rules_summary_contains_r12_auto_repay(self):
        """修正9: ルール要約にR12自動返済が記載されていること"""
        from llm.prompt_builder import build_system_prompt
        config = GameConfig.baseline_v1()
        prompt = build_system_prompt("P01", config)
        assert "自動" in prompt and "返済" in prompt
        # R12 Financeの説明が含まれること
        assert "返済可能額" in prompt or "全額充当" in prompt

    def test_rules_summary_contains_final_market_s2(self):
        """S2設定でRULES_SUMMARYに最終市場ルールが含まれること"""
        from llm.prompt_builder import build_system_prompt
        config = GameConfig.baseline_v1_s2()
        prompt = build_system_prompt("P01", config)
        assert "最終市場" in prompt
        assert "3倍" in prompt

    def test_rules_summary_no_final_market_s1(self):
        """S1設定（multiplier=1）ではRULES_SUMMARYに最終市場が含まれないこと"""
        from llm.prompt_builder import build_system_prompt
        config = GameConfig.baseline_v1()
        prompt = build_system_prompt("P01", config)
        assert "最終市場" not in prompt

    def test_obligations_block_in_negotiation_prompt(self):
        """署名済み義務がnegotiationプロンプトに含まれること"""
        from llm.prompt_builder import build_negotiation_prompt
        from engine.models import PlayerState, Card, CardRank
        config = GameConfig.baseline_v1()
        player = PlayerState(
            player_id="P01", cash=3_000_000, debt_balance=1_200_000,
            initial_loan=1_200_000,
            hand=[Card(card_id="c1", rank=CardRank.HIGH_CARD)],
        )
        state = {
            "markets": [{"market_id": "M01", "prize_pool": 500_000}],
            "alive_players": ["P01", "P02"],
            "messages": [],
            "contracts_pending": [],
            "trades_pending": [],
            "my_obligations": [
                {"contract_id": "C_abc", "obligor": "P01", "counterparty": "P02",
                 "ob_type": "type_b_market", "round_num": 5,
                 "details": {"market_id": "M02"}},
            ],
        }
        prompt = build_negotiation_prompt(player, 5, 1, state, config)
        assert "契約義務" in prompt
        assert "C_abc" in prompt
        assert "型B市場指定" in prompt
        assert "M02" in prompt
        assert "⬅ 今ラウンド" in prompt

    def test_obligations_block_in_commit_prompt(self):
        """署名済み義務がcommitプロンプトに含まれること"""
        from llm.prompt_builder import build_commit_prompt
        from engine.models import PlayerState, Card, CardRank, Market
        config = GameConfig.baseline_v1()
        player = PlayerState(
            player_id="P01", cash=3_000_000, debt_balance=1_200_000,
            initial_loan=1_200_000,
            hand=[Card(card_id="c1", rank=CardRank.HIGH_CARD)],
        )
        markets = [Market(market_id="M01", base_prize=500_000, carryover=0)]
        state = {
            "used_cards": {},
            "my_obligations": [
                {"contract_id": "C_xyz", "obligor": "P01", "counterparty": "P03",
                 "ob_type": "type_b_card", "round_num": 3,
                 "details": {"card_rank": "FLUSH"}},
            ],
        }
        prompt = build_commit_prompt(player, markets, 3, state, config)
        assert "契約義務" in prompt
        assert "C_xyz" in prompt
        assert "型Bカード指定" in prompt
        assert "FLUSH" in prompt

    def test_obligations_block_in_double_up_prompt(self):
        """署名済み義務がdouble_upプロンプトに含まれること"""
        from llm.prompt_builder import build_double_up_prompt
        from engine.models import PlayerState, Card, CardRank
        config = GameConfig.baseline_v1_s2()
        player = PlayerState(
            player_id="P01", cash=3_000_000, debt_balance=1_200_000,
            initial_loan=1_200_000,
            hand=[Card(card_id="c1", rank=CardRank.HIGH_CARD)],
        )
        state = {
            "alive_players": ["P01", "P02"],
            "double_ups": [],
            "my_obligations": [
                {"contract_id": "C_du", "obligor": "P01", "counterparty": "P02",
                 "ob_type": "type_a_payment", "round_num": 7,
                 "details": {"amount": 500_000}},
            ],
        }
        prompt = build_double_up_prompt(player, 300_000, 5, state, config)
        assert "契約義務" in prompt
        assert "型A金銭支払い" in prompt
        assert "50万円" in prompt

    def test_obligations_block_empty_when_none(self):
        """義務ゼロのときは契約義務セクションが出ないこと"""
        from llm.prompt_builder import build_negotiation_prompt
        from engine.models import PlayerState, Card, CardRank
        config = GameConfig.baseline_v1()
        player = PlayerState(
            player_id="P01", cash=3_000_000, debt_balance=1_200_000,
            initial_loan=1_200_000,
            hand=[Card(card_id="c1", rank=CardRank.HIGH_CARD)],
        )
        state = {
            "markets": [{"market_id": "M01", "prize_pool": 500_000}],
            "alive_players": ["P01"],
            "messages": [],
            "contracts_pending": [],
            "trades_pending": [],
            "my_obligations": [],
        }
        prompt = build_negotiation_prompt(player, 1, 1, state, config)
        assert "契約義務" not in prompt

    def test_obligations_conflict_warning(self):
        """同一ラウンドにtype_b_card義務2件でコンフリクト警告が出ること"""
        from llm.prompt_builder import _render_obligations_block
        visible = {"my_obligations": [
            {"contract_id": "C_a", "obligor": "P01", "counterparty": "P02",
             "ob_type": "type_b_card", "round_num": 5,
             "details": {"card_rank": "ONE_PAIR"}},
            {"contract_id": "C_b", "obligor": "P01", "counterparty": "P03",
             "ob_type": "type_b_card", "round_num": 5,
             "details": {"card_rank": "FLUSH"}},
        ]}
        lines = _render_obligations_block("P01", visible, 5)
        text = "\n".join(lines)
        assert "⚠" in text
        assert "2件" in text
        assert "1枚" in text

    def test_obligations_no_conflict_for_single_card(self):
        """同一ラウンドにtype_b_card義務1件ならコンフリクト警告が出ないこと"""
        from llm.prompt_builder import _render_obligations_block
        visible = {"my_obligations": [
            {"contract_id": "C_a", "obligor": "P01", "counterparty": "P02",
             "ob_type": "type_b_card", "round_num": 5,
             "details": {"card_rank": "ONE_PAIR"}},
        ]}
        lines = _render_obligations_block("P01", visible, 5)
        text = "\n".join(lines)
        assert "⚠" not in text

    def test_obligations_round_grouping(self):
        """義務がラウンドでグルーピングされること"""
        from llm.prompt_builder import _render_obligations_block
        visible = {"my_obligations": [
            {"contract_id": "C_a", "obligor": "P01", "counterparty": "P02",
             "ob_type": "type_b_market", "round_num": 5,
             "details": {"market_id": "M01"}},
            {"contract_id": "C_b", "obligor": "P01", "counterparty": "P03",
             "ob_type": "type_a_payment", "round_num": 8,
             "details": {"amount": 300_000}},
        ]}
        lines = _render_obligations_block("P01", visible, 5)
        text = "\n".join(lines)
        # R5が先、R8が後に出ること
        r5_pos = text.index("R5")
        r8_pos = text.index("R8")
        assert r5_pos < r8_pos
        # R5は「今ラウンド」強調、R8はなし
        assert "R5** ⬅ 今ラウンド" in text
        assert "R8**:" in text
        assert "R8** ⬅" not in text

    def test_type_b_card_key_normalization_card(self):
        """type_b_card義務のdetailsキー 'card' が 'card_rank' に正規化されること"""
        from engine.contracts import create_contract
        terms = [
            {"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_card",
             "round_num": 5, "details": {"card": "ONE_PAIR"}},
        ]
        contract = create_contract("P01", ["P01", "P02"], terms, 5)
        ob = contract.obligations[0]
        # 正規化後: "card_rank" キーに統一
        assert ob.details.get("card_rank") == "ONE_PAIR"
        # 元の "card" キーは除去
        assert "card" not in ob.details

    def test_type_b_card_key_normalization_rank(self):
        """type_b_card義務のdetailsキー 'rank' が 'card_rank' に正規化されること"""
        from engine.contracts import create_contract
        terms = [
            {"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_card",
             "round_num": 5, "details": {"rank": "FLUSH"}},
        ]
        contract = create_contract("P01", ["P01", "P02"], terms, 5)
        ob = contract.obligations[0]
        assert ob.details.get("card_rank") == "FLUSH"
        assert "rank" not in ob.details

    def test_type_b_card_key_backward_compat(self):
        """type_b_card義務のdetailsキーが既に 'card_rank' なら変更なし"""
        from engine.contracts import create_contract
        terms = [
            {"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_card",
             "round_num": 5, "details": {"card_rank": "ONE_PAIR"}},
        ]
        contract = create_contract("P01", ["P01", "P02"], terms, 5)
        ob = contract.obligations[0]
        assert ob.details.get("card_rank") == "ONE_PAIR"

    def test_type_b_card_regression_audit_no_violation(self):
        """回帰テスト: 'card'キーで作ったtype_b_card義務 → 正しいカードを出せば違反にならない"""
        from engine.contracts import create_contract, audit_type_b, get_active_type_b_obligations
        from engine.models import MarketCommit, Card, CardRank, ContractStatus
        # "card" キーで契約作成（LLMが送信する形式）
        terms = [
            {"obligor": "P02", "counterparty": "P09", "ob_type": "type_b_card",
             "round_num": 3, "details": {"card": "ONE_PAIR"}},
        ]
        contract = create_contract("P09", ["P09", "P02"], terms, 3)
        # 契約を ACTIVE にする
        contract = contract.model_copy(update={"status": ContractStatus.ACTIVE,
                                                "signed_by": ["P09", "P02"]})
        # P02が ONE_PAIR をコミット
        commit = MarketCommit(
            player_id="P02", market_id="M03",
            card=Card(card_id="ONE_PAIR_1", rank=CardRank.ONE_PAIR),
        )
        # 監査: 違反なし
        type_b_obs = get_active_type_b_obligations([contract], 3)
        violations = audit_type_b(type_b_obs, [commit])
        assert len(violations) == 0, f"Expected no violations, got {violations}"

    def test_type_b_card_regression_audit_violation(self):
        """回帰テスト: 'card'キーで作ったtype_b_card義務 → 別カードを出せば違反になる"""
        from engine.contracts import create_contract, audit_type_b, get_active_type_b_obligations
        from engine.models import MarketCommit, Card, CardRank, ContractStatus
        terms = [
            {"obligor": "P02", "counterparty": "P09", "ob_type": "type_b_card",
             "round_num": 3, "details": {"card": "ONE_PAIR"}},
        ]
        contract = create_contract("P09", ["P09", "P02"], terms, 3)
        contract = contract.model_copy(update={"status": ContractStatus.ACTIVE,
                                                "signed_by": ["P09", "P02"]})
        # P02が FLUSH をコミット（義務違反）
        commit = MarketCommit(
            player_id="P02", market_id="M03",
            card=Card(card_id="FLUSH_1", rank=CardRank.FLUSH),
        )
        type_b_obs = get_active_type_b_obligations([contract], 3)
        violations = audit_type_b(type_b_obs, [commit])
        assert len(violations) == 1

    def test_type_b_card_invalid_rank_rejected(self):
        """無効なrank名のtype_b_card義務を含む契約提案がValueErrorで弾かれること"""
        from engine.contracts import create_contract
        terms = [
            {"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_card",
             "round_num": 5, "details": {"card_rank": "INVALID_RANK"}},
        ]
        import pytest
        with pytest.raises(ValueError, match="Invalid type_b_card"):
            create_contract("P01", ["P01", "P02"], terms, 5)

    def test_type_b_card_no_key_rejected(self):
        """card_rankキーが全くないtype_b_card義務がValueErrorで弾かれること"""
        from engine.contracts import create_contract
        terms = [
            {"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_card",
             "round_num": 5, "details": {}},
        ]
        import pytest
        with pytest.raises(ValueError, match="Invalid type_b_card"):
            create_contract("P01", ["P01", "P02"], terms, 5)

    def test_event_logger_streaming_writes_immediately(self, tmp_path):
        """逐次追記モード: log()呼び出し後にファイルにイベントが即時書き込まれること"""
        from engine.events import EventLogger
        out = tmp_path / "events.jsonl"
        logger = EventLogger(output_path=out)
        logger.log("TEST_EVENT", 1, "test", data={"key": "value"})
        # ファイルが即時に存在し、1行書き込まれている
        assert out.exists()
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 1
        import json
        event = json.loads(lines[0])
        assert event["event_type"] == "TEST_EVENT"
        assert event["data"]["key"] == "value"
        # 2つ目のイベント
        logger.log("TEST_EVENT_2", 2, "test")
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 2
        logger.close()

    def test_event_logger_no_file_without_path(self, tmp_path):
        """後方互換: output_pathなしではファイルが生成されないこと"""
        from engine.events import EventLogger
        import os
        before = set(os.listdir(tmp_path))
        logger = EventLogger()
        logger.log("TEST", 1, "test")
        after = set(os.listdir(tmp_path))
        # tmp_path に新規ファイルが生成されていない
        assert before == after
        # メモリには保持
        assert len(logger.events) == 1
        # save_jsonl で従来通り一括書き出しできる
        out = tmp_path / "manual.jsonl"
        logger.save_jsonl(out)
        assert out.exists()
        lines = out.read_text().strip().split("\n")
        assert len(lines) == 1

    def test_event_logger_no_duplicate_on_save(self, tmp_path):
        """二重書き込み回避: 逐次書き込み済みパスにsave_jsonl()しても重複しないこと"""
        from engine.events import EventLogger
        out = tmp_path / "events.jsonl"
        logger = EventLogger(output_path=out)
        logger.log("E1", 1, "test")
        logger.log("E2", 2, "test")
        # 逐次書き込みで2行
        assert len(out.read_text().strip().split("\n")) == 2
        # save_jsonl を同一パスで呼んでも増えない
        logger.save_jsonl(out)
        assert len(out.read_text().strip().split("\n")) == 2
        logger.close()

    def test_event_logger_save_to_different_path(self, tmp_path):
        """逐次書き込み中でも別パスへのsave_jsonlは従来通り全書き出しすること"""
        from engine.events import EventLogger
        out1 = tmp_path / "streaming.jsonl"
        out2 = tmp_path / "manual.jsonl"
        logger = EventLogger(output_path=out1)
        logger.log("E1", 1, "test")
        logger.log("E2", 2, "test")
        # 別パスに全書き出し
        logger.save_jsonl(out2)
        assert out2.exists()
        lines = out2.read_text().strip().split("\n")
        assert len(lines) == 2
        logger.close()

    def test_highlight_detection_h9(self):
        """修正6: H9同ランク山分けが検出されること"""
        from scripts.highlights import detect_highlights
        events = [{
            "event_type": "MARKET_RESULT",
            "round_num": 5,
            "data": {
                "market_id": "M01",
                "winners": ["P01", "P02"],
                "prize_per_winner": 500000,
                "participants": 3,
            },
        }]
        hl = detect_highlights(events, [], "test")
        h9s = [h for h in hl if h["type"] == "H9"]
        assert len(h9s) >= 1
        assert "山分け" in h9s[0]["detail"]

    def test_highlight_detection_h13(self):
        """修正6: H13 AUTO COMMITが検出されること"""
        from scripts.highlights import detect_highlights
        events = [{
            "event_type": "AUTO_COMMIT",
            "round_num": 3,
            "data": {"player_id": "P05", "message": "P05: AUTO COMMIT"},
        }]
        hl = detect_highlights(events, [], "test")
        h13s = [h for h in hl if h["type"] == "H13"]
        assert len(h13s) >= 1


class TestStep32Fixes:
    """Step 3.2 修正のテスト"""

    def test_contract_propose_invalid_terms_raises_parse_error(self):
        """P1: obligor欠落のtermsがParseErrorになること"""
        from llm.response_parser import parse_response, ParseError
        # obligorキーが欠落した不正なterms
        text = '{"strategy": {}, "action": {"type": "contract_propose", "with": ["P07"], "terms": [{"type": "payment", "player": "P07", "amount": 500000}]}}'
        with pytest.raises(ParseError) as exc_info:
            parse_response(text, "P01", "negotiation")
        assert "obligor" in str(exc_info.value).lower() or "必須キー" in str(exc_info.value)

    def test_contract_propose_valid_terms_accepted(self):
        """P1: 正しい形式のtermsが受理されること"""
        from llm.response_parser import parse_response
        text = ('{"strategy": {}, "action": {"type": "contract_propose", "with": ["P07"], '
                '"terms": [{"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment", '
                '"round_num": 5, "details": {"amount": 500000}}]}}')
        strategy, action = parse_response(text, "P01", "negotiation")
        assert isinstance(action, ContractProposeAction)
        assert len(action.terms) == 1
        assert action.terms[0]["obligor"] == "P01"

    def test_p2_action_error_does_not_crash_game(self):
        """P2: 不正アクション適用で試合が続行しエラーイベントが記録されること"""
        from engine.game import Game
        from engine.config import GameConfig
        from engine.events import EventLogger
        from bots.random_bot import RandomBot

        config = GameConfig.baseline_v1(num_players=2)
        agents = {
            "P01": RandomBot(seed=42),
            "P02": RandomBot(seed=99),
        }
        game = Game(config=config, agents=agents, seed=42)
        # 不正なアクション（ContractProposeActionに不正termsを持たせる）
        bad_action = ContractProposeAction(
            player_id="P01",
            with_players=["P02"],
            terms=[{"bad_key": "no_obligor"}],  # obligor欠落
        )
        event_logger = EventLogger()
        game.logger = event_logger
        game._setup()
        # P2防御: 例外がキャッチされ試合は続行する
        game._execute_negotiation_action(bad_action, "P01", 1, 1)
        # ACTION_ERRORイベントが記録されていること
        error_events = [e for e in event_logger.events if e.event_type == "ACTION_ERROR"]
        assert len(error_events) >= 1
        assert "obligor" in error_events[0].data.get("error", "").lower()

    def test_bounty_post_conversion(self):
        """P3: bounty_postが正しく変換されること"""
        from llm.response_parser import parse_response
        text = ('{"strategy": {}, "action": {"type": "bounty_post", "amount": 500000, '
                '"bounty_type": "achievement", "condition_type": "market_win_against", '
                '"condition": {"target_player": "P07"}, "round_num": 5}}')
        strategy, action = parse_response(text, "P01", "negotiation")
        assert isinstance(action, BountyPostAction)
        assert action.amount == 500000
        assert action.bounty_type == "achievement"
        assert action.round_num == 5

    def test_llm_logger_incremental_write(self):
        """P0: 逐次書き込みでファイルに即座に反映されること"""
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = LLMLogger(tmpdir, game_id="test_incr")
            # 1回目のlog_call
            logger.log_call(
                player_id="P01", model_id="test", phase="negotiation",
                round_num=1, turn=1, system_prompt="sys", user_prompt="user",
                response_text="resp", usage={"input_tokens": 10, "output_tokens": 5},
                cost=0.01, elapsed_ms=100,
            )
            # ファイルに即座に書き込まれていること
            log_path = logger._file_path
            with open(log_path, "r") as f:
                lines = f.readlines()
            assert len(lines) == 1
            entry = json.loads(lines[0])
            assert entry["player_id"] == "P01"
            assert entry["round_num"] == 1

            # 2回目のlog_call
            logger.log_call(
                player_id="P01", model_id="test", phase="commit",
                round_num=1, turn=None, system_prompt="sys", user_prompt="user2",
                response_text="resp2", usage={"input_tokens": 20, "output_tokens": 10},
                cost=0.02, elapsed_ms=200,
            )
            with open(log_path, "r") as f:
                lines = f.readlines()
            assert len(lines) == 2  # 2行に増えている

            logger.close()
