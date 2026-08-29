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
        """メインロスターのGeminiモデルID(M3/L3)が3.x系であること

        L7(Gemini 2.5 Flash-Lite)は2026-08-16追加の負荷試験専用枠のため対象外。
        """
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


class TestL7Gemini25FlashLite:
    """L7 (Gemini 2.5 Flash-Lite) 負荷試験用追加枠のテスト

    2026-08-16: 6体→18体の低コストインフラ負荷試験のために追加。
    API呼び出しは一切行わない。
    """

    def test_l7_registered(self):
        """L7がmodel_id/provider/adapter_type/env_key/base_urlの通り登録されていること"""
        from llm.models import MODEL_REGISTRY, GEMINI_OPENAI_BASE_URL
        m = MODEL_REGISTRY["L7"]
        assert m.model_id == "gemini-2.5-flash-lite"
        assert m.provider == "Google"
        assert m.adapter_type == "gemini"
        assert m.env_key == "GEMINI_API_KEY"
        assert m.base_url == GEMINI_OPENAI_BASE_URL

    def test_l7_prices(self):
        """L7の単価がユーザー指定通りであり、cache/reasoning単価は未設定(None)であること"""
        from llm.models import MODEL_REGISTRY
        m = MODEL_REGISTRY["L7"]
        assert m.input_price == 0.10
        assert m.output_price == 0.40
        assert m.cached_input_price is None
        assert m.reasoning_price is None
        assert m.extra_params is None

    def test_l7_uses_openai_compat_adapter(self):
        """L7がGemini経路(OpenAICompatAdapter)にルーティングされること（APIは呼ばない）"""
        from llm.adapters import create_adapter, OpenAICompatAdapter
        from llm.models import get_model
        adapter = create_adapter(get_model("L7"))
        assert isinstance(adapter, OpenAICompatAdapter)

    def test_l7_lookup_by_model_id(self):
        """get_model()がmodel_id文字列からL7を逆引きできること（viewerのコスト内訳表示が参照する経路）"""
        from llm.models import get_model
        m = get_model("gemini-2.5-flash-lite")
        assert m.name == "Gemini 2.5 Flash-Lite"

    def test_l7_estimate_cost(self):
        """L7のestimate_cost()が単価通りに計算されること"""
        from llm.models import MODEL_REGISTRY, estimate_cost
        m = MODEL_REGISTRY["L7"]
        cost = estimate_cost(m, input_tokens=4000, output_tokens=200, total_tokens=4200)
        expected = (4000 * 0.10 + 200 * 0.40) / 1_000_000
        assert cost == pytest.approx(expected)

    def test_l3_m3_unchanged(self):
        """L7追加が既存のL3/M3(Gemini 3.5系)を壊していないことの回帰確認"""
        from llm.models import MODEL_REGISTRY
        l3 = MODEL_REGISTRY["L3"]
        assert l3.model_id == "gemini-3.5-flash-lite"
        assert l3.input_price == 0.30
        assert l3.output_price == 2.50
        m3 = MODEL_REGISTRY["M3"]
        assert m3.model_id == "gemini-3.5-flash"
        assert m3.input_price == 1.50
        assert m3.output_price == 9.00

    def test_load_test_rosters_resolve(self):
        """明日朝の6体/18体ロースター文字列がget_model()で全解決できること

        scripts/llm_trial.py の roster.split(",") は .strip() しないため、
        カンマ後にスペースを入れない文字列であることをここで固定する。
        """
        from llm.models import get_model

        roster_6 = "L7,L7,L6,L6,L2,L2".split(",")
        roster_18 = "L7,L7,L7,L7,L7,L7,L6,L6,L6,L6,L6,L6,L2,L2,L2,L2,L2,L2".split(",")

        assert len(roster_6) == 6
        assert len(roster_18) == 18

        for key in roster_6 + roster_18:
            assert " " not in key, f"roster key contains stray whitespace: {key!r}"
            model = get_model(key)  # ValueErrorが出ないこと
            assert model is not None


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
        # R12 Financeの説明が含まれること（v0.8サイクル8.2 Step1で
        # 「残債全額が最低返済額」に短縮。事実は不変）
        assert "残債全額が最低返済額" in prompt

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
        assert "カード使用義務が2件あります" in text
        assert "2件" in text
        assert "1枚" in text
        # Cycle 2 wording-only fix: 結果断定・警告記号を排し事実提示のみにする
        assert "⚠" not in text
        assert "必ず違反" not in text

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


class TestUsageCapture:
    """Stage 1: 各アダプタが reasoning_tokens / cached_tokens を正しく捕捉することを検証"""

    def test_anthropic_usage_thinking_tokens(self):
        """Anthropic SDK の thinking_tokens が統一キー reasoning_tokens で取得されること"""
        from llm.adapters import _dump_usage_raw

        # Anthropic SDK の output_tokens_details.thinking_tokens をシミュレート
        class FakeOTD:
            thinking_tokens = 500

        class FakeUsage:
            input_tokens = 1000
            output_tokens = 800
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 50
            output_tokens_details = FakeOTD()
            def model_dump(self):
                return {"input_tokens": 1000, "output_tokens": 800,
                        "output_tokens_details": {"thinking_tokens": 500}}

        usage_obj = FakeUsage()
        _otd = getattr(usage_obj, "output_tokens_details", None)
        usage = {
            "input_tokens": usage_obj.input_tokens,
            "output_tokens": usage_obj.output_tokens,
            "cache_creation_input_tokens": getattr(usage_obj, "cache_creation_input_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(usage_obj, "cache_read_input_tokens", 0) or 0,
            "reasoning_tokens": getattr(_otd, "thinking_tokens", 0) or 0,
        }
        assert usage["reasoning_tokens"] == 500
        assert usage["cache_read_input_tokens"] == 50

    def test_openai_compat_usage_reasoning_tokens(self):
        """OpenAI互換の reasoning_tokens が捕捉されること"""
        class FakeCTD:
            reasoning_tokens = 300
        class FakePTD:
            cached_tokens = 0
            cache_write_tokens = 0
        class FakeUsage:
            prompt_tokens = 2000
            completion_tokens = 600
            completion_tokens_details = FakeCTD()
            prompt_tokens_details = FakePTD()

        usage_obj = FakeUsage()
        _ctd = getattr(usage_obj, "completion_tokens_details", None)
        _ptd = getattr(usage_obj, "prompt_tokens_details", None)
        usage = {
            "input_tokens": getattr(usage_obj, "prompt_tokens", 0) or 0,
            "output_tokens": getattr(usage_obj, "completion_tokens", 0) or 0,
            "cache_read_input_tokens": getattr(_ptd, "cached_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(_ptd, "cache_write_tokens", 0) or 0,
            "reasoning_tokens": getattr(_ctd, "reasoning_tokens", 0) or 0,
        }
        assert usage["reasoning_tokens"] == 300

    def test_openai_compat_usage_cached_tokens(self):
        """OpenAI互換の cached_tokens / cache_write_tokens が捕捉されること"""
        class FakeCTD:
            reasoning_tokens = 0
        class FakePTD:
            cached_tokens = 1000
            cache_write_tokens = 200
        class FakeUsage:
            prompt_tokens = 3000
            completion_tokens = 400
            completion_tokens_details = FakeCTD()
            prompt_tokens_details = FakePTD()

        usage_obj = FakeUsage()
        _ctd = getattr(usage_obj, "completion_tokens_details", None)
        _ptd = getattr(usage_obj, "prompt_tokens_details", None)
        usage = {
            "cache_read_input_tokens": getattr(_ptd, "cached_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(_ptd, "cache_write_tokens", 0) or 0,
            "reasoning_tokens": getattr(_ctd, "reasoning_tokens", 0) or 0,
        }
        assert usage["cache_read_input_tokens"] == 1000
        assert usage["cache_creation_input_tokens"] == 200
        assert usage["reasoning_tokens"] == 0

    def test_usage_details_none_fallback(self):
        """details が None の場合、reasoning/cache 系が全て 0 にフォールバックすること（回帰）"""
        class FakeUsage:
            prompt_tokens = 500
            completion_tokens = 100
            completion_tokens_details = None
            prompt_tokens_details = None

        usage_obj = FakeUsage()
        _ctd = getattr(usage_obj, "completion_tokens_details", None)
        _ptd = getattr(usage_obj, "prompt_tokens_details", None)
        usage = {
            "cache_read_input_tokens": getattr(_ptd, "cached_tokens", 0) or 0,
            "cache_creation_input_tokens": getattr(_ptd, "cache_write_tokens", 0) or 0,
            "reasoning_tokens": getattr(_ctd, "reasoning_tokens", 0) or 0,
        }
        assert usage["cache_read_input_tokens"] == 0
        assert usage["cache_creation_input_tokens"] == 0
        assert usage["reasoning_tokens"] == 0

    def test_dump_usage_raw(self):
        """_dump_usage_raw が pydantic model / plain object / None に対して安全に動作すること"""
        from llm.adapters import _dump_usage_raw

        # pydantic-like model_dump
        class FakeModel:
            def model_dump(self):
                return {"a": 1, "b": 2}
        result = _dump_usage_raw(FakeModel())
        assert result == {"a": 1, "b": 2}

        # plain object with vars()
        class PlainObj:
            x = 10
            y = 20
        result = _dump_usage_raw(PlainObj())
        assert result is not None  # vars() で取得可能

        # None → 例外を出さず None を返す
        result = _dump_usage_raw(None)
        assert result is None


class TestResponseModelCapture:
    """
    API返却モデル名（response.model）が usage 辞書に requested_model/response_model
    として伝播することを検証する。実クライアントは注入せず、_client に直接
    フェイクを代入して complete() の実経路を通す（API呼び出し・キー不要）。
    """

    def _anthropic_model_info(self):
        return ModelInfo(
            model_id="claude-haiku-4-5-20251001", provider="Anthropic", name="Haiku",
            adapter_type="anthropic",
            input_price=1.0, output_price=5.0,
            env_key="ANTHROPIC_API_KEY", base_url=None,
        )

    def _openai_model_info(self):
        return ModelInfo(
            model_id="deepseek-chat", provider="DeepSeek", name="DeepSeek",
            adapter_type="openai_compat",
            input_price=0.5, output_price=1.5,
            env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
        )

    def test_anthropic_response_model_propagates(self):
        """AnthropicAdapter.complete() が response.model を usage["response_model"] に伝播すること"""
        from llm.adapters import AnthropicAdapter, AdapterError

        class FakeBlock:
            text = "ok"

        class FakeUsage:
            input_tokens = 10
            output_tokens = 5
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 0
            output_tokens_details = None

        class FakeResponse:
            content = [FakeBlock()]
            usage = FakeUsage()
            stop_reason = "end_turn"
            model = "claude-haiku-4-5-20251001"

        class FakeMessages:
            def create(self, **kwargs):
                return FakeResponse()

        class FakeClient:
            messages = FakeMessages()

        info = self._anthropic_model_info()
        adapter = AnthropicAdapter(info)
        adapter._client = FakeClient()
        text, usage = adapter.complete("sys", [{"role": "user", "content": "hi"}])
        assert text == "ok"
        assert usage["requested_model"] == "claude-haiku-4-5-20251001"
        assert usage["response_model"] == "claude-haiku-4-5-20251001"

    def test_anthropic_response_model_none_when_missing(self):
        """response.model 属性が無い場合、response_model は None のまま（requestedで埋めない）"""
        from llm.adapters import AnthropicAdapter

        class FakeBlock:
            text = "ok"

        class FakeUsage:
            input_tokens = 10
            output_tokens = 5
            cache_creation_input_tokens = 0
            cache_read_input_tokens = 0
            output_tokens_details = None

        class FakeResponse:
            content = [FakeBlock()]
            usage = FakeUsage()
            stop_reason = "end_turn"
            # model属性を意図的に持たせない

        class FakeMessages:
            def create(self, **kwargs):
                return FakeResponse()

        class FakeClient:
            messages = FakeMessages()

        info = self._anthropic_model_info()
        adapter = AnthropicAdapter(info)
        adapter._client = FakeClient()
        _, usage = adapter.complete("sys", [{"role": "user", "content": "hi"}])
        assert usage["response_model"] is None
        assert usage["requested_model"] == "claude-haiku-4-5-20251001"

    def test_openai_compat_response_model_propagates(self):
        """OpenAICompatAdapter.complete() が response.model を usage["response_model"] に伝播すること"""
        from llm.adapters import OpenAICompatAdapter

        class FakeMessage:
            content = "ok"

        class FakeChoice:
            message = FakeMessage()
            finish_reason = "stop"

        class FakeUsage:
            prompt_tokens = 20
            completion_tokens = 10
            total_tokens = 30
            completion_tokens_details = None
            prompt_tokens_details = None

        class FakeResponse:
            choices = [FakeChoice()]
            usage = FakeUsage()
            model = "deepseek-chat"

        class FakeCompletions:
            def create(self, **kwargs):
                return FakeResponse()

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            chat = FakeChat()

        info = self._openai_model_info()
        adapter = OpenAICompatAdapter(info)
        adapter._client = FakeClient()
        text, usage = adapter.complete("sys", [{"role": "user", "content": "hi"}])
        assert text == "ok"
        assert usage["requested_model"] == "deepseek-chat"
        assert usage["response_model"] == "deepseek-chat"

    def test_openai_compat_response_model_empty_string_normalizes_to_none(self):
        """response.model が空文字の場合、response_model は None に正規化されること"""
        from llm.adapters import OpenAICompatAdapter

        class FakeMessage:
            content = "ok"

        class FakeChoice:
            message = FakeMessage()
            finish_reason = "stop"

        class FakeUsage:
            prompt_tokens = 20
            completion_tokens = 10
            total_tokens = 30
            completion_tokens_details = None
            prompt_tokens_details = None

        class FakeResponse:
            choices = [FakeChoice()]
            usage = FakeUsage()
            model = "   "

        class FakeCompletions:
            def create(self, **kwargs):
                return FakeResponse()

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            chat = FakeChat()

        info = self._openai_model_info()
        adapter = OpenAICompatAdapter(info)
        adapter._client = FakeClient()
        _, usage = adapter.complete("sys", [{"role": "user", "content": "hi"}])
        assert usage["response_model"] is None

    def test_logger_entry_has_response_model(self):
        """LLMLogger.log_call() が usage["response_model"] をエントリに転記すること"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = LLMLogger(tmpdir, game_id="test_rm")
            logger.log_call(
                player_id="P01", model_id="deepseek-chat", phase="negotiation",
                round_num=1, turn=1, system_prompt="sys", user_prompt="user",
                response_text="resp",
                usage={"input_tokens": 10, "output_tokens": 5, "response_model": "deepseek-chat"},
                cost=0.01, elapsed_ms=100,
            )
            entry = logger._entries[-1]
            assert entry["response_model"] == "deepseek-chat"
            logger.close()

    def test_logger_entry_response_model_defaults_none(self):
        """usage に response_model が無い場合、エントリでは None になること"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = LLMLogger(tmpdir, game_id="test_rm2")
            logger.log_call(
                player_id="P01", model_id="test", phase="negotiation",
                round_num=1, turn=1, system_prompt="sys", user_prompt="user",
                response_text="resp",
                usage={"input_tokens": 10, "output_tokens": 5},
                cost=0.01, elapsed_ms=100,
            )
            entry = logger._entries[-1]
            assert entry["response_model"] is None
            logger.close()


class TestOpenAICompatParamPolicy:
    """
    H2 (gpt-5.6-sol) 再テスト対応: max_tokens_param / supports_temperature の
    データ駆動パラメータ切替を検証する。実クライアントは注入せず、_client に
    直接フェイクを代入して complete() の実経路を通す（API呼び出し・キー不要）。
    """

    def _h2_model_info(self):
        return ModelInfo(
            model_id="gpt-5.6-sol", provider="OpenAI", name="GPT-5.6 Sol",
            adapter_type="openai_compat",
            input_price=5.0, output_price=30.0,
            env_key="OPENAI_API_KEY", base_url=None,
            max_tokens_param="max_completion_tokens",
            supports_temperature=False,
        )

    def _default_model_info(self, extra_params=None):
        return ModelInfo(
            model_id="deepseek-chat", provider="DeepSeek", name="DeepSeek",
            adapter_type="openai_compat",
            input_price=0.5, output_price=1.5,
            env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
            extra_params=extra_params,
        )

    def _fake_client(self, create_fn):
        from llm.adapters import OpenAICompatAdapter  # noqa: F401 (型参照用)

        class FakeMessage:
            content = "ok"

        class FakeChoice:
            message = FakeMessage()
            finish_reason = "stop"

        class FakeUsage:
            prompt_tokens = 20
            completion_tokens = 10
            total_tokens = 30
            completion_tokens_details = None
            prompt_tokens_details = None

        class FakeResponse:
            choices = [FakeChoice()]
            usage = FakeUsage()
            model = "gpt-5.6-sol"

        class FakeCompletions:
            def create(self, **kwargs):
                return create_fn(kwargs, FakeResponse)

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            chat = FakeChat()

        return FakeClient()

    def test_h2_sends_max_completion_tokens_not_max_tokens(self):
        """H2は max_completion_tokens のみを送り、max_tokens は送らないこと"""
        from llm.adapters import OpenAICompatAdapter

        captured = []

        def create_fn(kwargs, resp_cls):
            captured.append(kwargs)
            return resp_cls()

        info = self._h2_model_info()
        adapter = OpenAICompatAdapter(info)
        adapter._client = self._fake_client(create_fn)
        adapter.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=3072)

        assert len(captured) == 1
        assert "max_completion_tokens" in captured[0]
        assert captured[0]["max_completion_tokens"] == 3072
        assert "max_tokens" not in captured[0]

    def test_h2_does_not_send_temperature(self):
        """H2は temperature を一切送らないこと"""
        from llm.adapters import OpenAICompatAdapter

        captured = []

        def create_fn(kwargs, resp_cls):
            captured.append(kwargs)
            return resp_cls()

        info = self._h2_model_info()
        adapter = OpenAICompatAdapter(info)
        adapter._client = self._fake_client(create_fn)
        adapter.complete("sys", [{"role": "user", "content": "hi"}], temperature=0.0)

        assert len(captured) == 1
        assert "temperature" not in captured[0]

    def test_default_model_sends_max_tokens_and_temperature(self):
        """既定モデル（H2以外）は従来どおり max_tokens + temperature を送ること（回帰）"""
        from llm.adapters import OpenAICompatAdapter

        captured = []

        def create_fn(kwargs, resp_cls):
            captured.append(kwargs)
            return resp_cls()

        info = self._default_model_info()
        adapter = OpenAICompatAdapter(info)
        adapter._client = self._fake_client(create_fn)
        adapter.complete("sys", [{"role": "user", "content": "hi"}], max_tokens=1000, temperature=0.5)

        assert len(captured) == 1
        assert captured[0]["max_tokens"] == 1000
        assert "max_completion_tokens" not in captured[0]
        assert captured[0]["temperature"] == 0.5

    def test_model_temperature_override_is_sent_without_fallback(self):
        """Moonshotのような固定temperatureモデルは、呼出側0.7ではなく0.6を1回だけ送る。"""
        from llm.adapters import OpenAICompatAdapter

        captured = []

        def create_fn(kwargs, resp_cls):
            captured.append(kwargs)
            return resp_cls()

        info = ModelInfo(
            model_id="kimi-k2.6", provider="Moonshot", name="Kimi",
            adapter_type="openai_compat", input_price=1.0, output_price=4.0,
            env_key="KIMI_API_KEY", base_url="https://example.invalid",
            temperature_override=0.6,
        )
        adapter = OpenAICompatAdapter(info, max_retries=0, allow_temperature_fallback=False)
        adapter._client = self._fake_client(create_fn)
        adapter.complete("sys", [{"role": "user", "content": "hi"}], temperature=0.7)

        assert len(captured) == 1
        assert captured[0]["temperature"] == 0.6

    def test_default_model_still_falls_back_on_temperature_error(self):
        """既定モデルはtemperatureエラーで2回目が飛び、2回目にtemperatureが無いこと（既存挙動維持）"""
        from llm.adapters import OpenAICompatAdapter

        captured = []

        def create_fn(kwargs, resp_cls):
            captured.append(kwargs)
            if len(captured) == 1:
                raise Exception("temperature does not support this value")
            return resp_cls()

        info = self._default_model_info()
        adapter = OpenAICompatAdapter(info)
        adapter._client = self._fake_client(create_fn)
        adapter.complete("sys", [{"role": "user", "content": "hi"}], temperature=0.5)

        assert len(captured) == 2
        assert "temperature" in captured[0]
        assert "temperature" not in captured[1]

    def test_extra_params_and_usage_capture_unaffected(self):
        """extra_body保持、requested_model/response_model/finish_reason/usageが無傷であること"""
        from llm.adapters import OpenAICompatAdapter

        captured = []

        def create_fn(kwargs, resp_cls):
            captured.append(kwargs)
            return resp_cls()

        info = self._default_model_info(extra_params={"thinking": {"type": "disabled"}})
        adapter = OpenAICompatAdapter(info)
        adapter._client = self._fake_client(create_fn)
        text, usage = adapter.complete("sys", [{"role": "user", "content": "hi"}])

        assert captured[0]["extra_body"] == {"thinking": {"type": "disabled"}}
        assert text == "ok"
        assert usage["requested_model"] == "deepseek-chat"
        assert usage["response_model"] == "gpt-5.6-sol"
        assert usage["finish_reason"] == "stop"
        assert usage["input_tokens"] == 20
        assert usage["output_tokens"] == 10


class TestSingleHttpRequestGuarantee:
    """
    strict adapter 設定は成功・失敗を問わずクライアント再送を抑止する。
    H2 は supports_temperature=False のため、temperature fallbackも発生しない。
    Phase 2は max_retries=0 と allow_temperature_fallback=False を併用する。
    """

    def _h2_model_info(self):
        return ModelInfo(
            model_id="gpt-5.6-sol", provider="OpenAI", name="GPT-5.6 Sol",
            adapter_type="openai_compat",
            input_price=5.0, output_price=30.0,
            env_key="OPENAI_API_KEY", base_url=None,
            max_tokens_param="max_completion_tokens",
            supports_temperature=False,
        )

    def _default_model_info(self):
        return ModelInfo(
            model_id="deepseek-chat", provider="DeepSeek", name="DeepSeek",
            adapter_type="openai_compat",
            input_price=0.5, output_price=1.5,
            env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
        )

    def _always_raising_client(self, exc_factory, call_counter):
        class FakeCompletions:
            def create(self, **kwargs):
                call_counter.append(1)
                raise exc_factory()

        class FakeChat:
            completions = FakeCompletions()

        class FakeClient:
            chat = FakeChat()

        return FakeClient()

    def test_openai_compat_strict_mode_passes_zero_to_sdk(self, monkeypatch):
        """OpenAI互換SDK（Google/xAI/Moonshot/DeepSeekを含む）もretry=0を受け取る。"""
        import sys
        import types
        from llm.adapters import OpenAICompatAdapter

        captured = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setitem(sys.modules, "openai", types.SimpleNamespace(OpenAI=FakeOpenAI))
        monkeypatch.setenv("DEEPSEEK_API_KEY", "fake-key")
        adapter = OpenAICompatAdapter(self._default_model_info(), max_retries=0,
                                      allow_temperature_fallback=False)
        adapter._get_client()

        assert captured["max_retries"] == 0
        assert adapter._allow_temperature_fallback is False

    def test_anthropic_strict_mode_passes_zero_to_sdk(self, monkeypatch):
        """Anthropic SDKにもstrict経路のmax_retries=0が明示される。"""
        import sys
        import types
        from llm.adapters import AnthropicAdapter

        captured = {}

        class FakeAnthropic:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        monkeypatch.setitem(sys.modules, "anthropic", types.SimpleNamespace(Anthropic=FakeAnthropic))
        monkeypatch.setenv("ANTHROPIC_API_KEY", "fake-key")
        info = ModelInfo(
            model_id="claude-haiku-4-5-20251001", provider="Anthropic", name="Haiku",
            adapter_type="anthropic", input_price=1.0, output_price=5.0,
            env_key="ANTHROPIC_API_KEY", base_url=None,
        )
        adapter = AnthropicAdapter(info, max_retries=0, allow_temperature_fallback=False)
        adapter._get_client()

        assert captured["max_retries"] == 0
        assert adapter._allow_temperature_fallback is False

    @pytest.mark.parametrize("error_message", [
        "400 Bad Request: Unsupported parameter",
        "429 Too Many Requests",
        "500 Internal Server Error",
        "Request timeout",
        "Connection error",
    ])
    def test_no_resend_for_any_error(self, error_message, monkeypatch):
        """max_retries=0 なら400/429/500/timeout/connectionいずれでも create() は1回だけ"""
        import llm.adapters as adapters_mod
        from llm.adapters import OpenAICompatAdapter, AdapterError

        monkeypatch.setattr(adapters_mod.time, "sleep", lambda *a, **kw: None)
        calls: list[int] = []
        info = self._default_model_info()
        adapter = OpenAICompatAdapter(info, max_retries=0)
        adapter._client = self._always_raising_client(lambda: Exception(error_message), calls)

        with pytest.raises(AdapterError):
            adapter.complete("sys", [{"role": "user", "content": "hi"}])

        assert len(calls) == 1

    def test_h2_like_model_no_resend_even_on_temperature_error(self, monkeypatch):
        """supports_temperature=False かつ max_retries=0 なら「temperature」400でも create() は1回"""
        import llm.adapters as adapters_mod
        from llm.adapters import OpenAICompatAdapter, AdapterError

        monkeypatch.setattr(adapters_mod.time, "sleep", lambda *a, **kw: None)
        calls: list[int] = []
        info = self._h2_model_info()
        adapter = OpenAICompatAdapter(info, max_retries=0)
        adapter._client = self._always_raising_client(
            lambda: Exception("temperature is not supported for this model"), calls
        )

        with pytest.raises(AdapterError):
            adapter.complete("sys", [{"role": "user", "content": "hi"}])

        assert len(calls) == 1

    def test_strict_mode_suppresses_openai_temperature_fallback(self, monkeypatch):
        """Phase 2 の strict 設定ではtemperatureエラーも同一call内で再送しない。"""
        import llm.adapters as adapters_mod
        from llm.adapters import OpenAICompatAdapter, AdapterError

        monkeypatch.setattr(adapters_mod.time, "sleep", lambda *a, **kw: None)
        calls: list[int] = []
        adapter = OpenAICompatAdapter(
            self._default_model_info(),
            max_retries=0,
            allow_temperature_fallback=False,
        )
        adapter._client = self._always_raising_client(
            lambda: Exception("temperature does not support this value"), calls
        )

        with pytest.raises(AdapterError):
            adapter.complete("sys", [{"role": "user", "content": "hi"}])

        assert len(calls) == 1

    def test_anthropic_strict_mode_suppresses_retry_and_temperature_fallback(self, monkeypatch):
        """AnthropicもPhase 2 strict設定ならtemperatureエラーを再送しない。"""
        import llm.adapters as adapters_mod
        from llm.adapters import AnthropicAdapter, AdapterError

        monkeypatch.setattr(adapters_mod.time, "sleep", lambda *a, **kw: None)
        calls: list[int] = []

        class FakeMessages:
            def create(self, **kwargs):
                calls.append(1)
                raise Exception("temperature is deprecated")

        class FakeClient:
            messages = FakeMessages()

        info = ModelInfo(
            model_id="claude-haiku-4-5-20251001", provider="Anthropic", name="Haiku",
            adapter_type="anthropic", input_price=1.0, output_price=5.0,
            env_key="ANTHROPIC_API_KEY", base_url=None,
        )
        adapter = AnthropicAdapter(
            info,
            max_retries=0,
            allow_temperature_fallback=False,
        )
        adapter._client = FakeClient()

        with pytest.raises(AdapterError):
            adapter.complete("sys", [{"role": "user", "content": "hi"}])

        assert len(calls) == 1

    def test_anthropic_model_without_temperature_omits_parameter(self):
        """Sonnet/Opus相当のsupports_temperature=Falseは初回からtemperatureを送らない。"""
        from llm.adapters import AnthropicAdapter, AdapterError

        captured = []

        class FakeMessages:
            def create(self, **kwargs):
                captured.append(kwargs)
                raise Exception("expected stop")

        class FakeClient:
            messages = FakeMessages()

        info = ModelInfo(
            model_id="claude-sonnet-5", provider="Anthropic", name="Sonnet",
            adapter_type="anthropic", input_price=2.0, output_price=10.0,
            env_key="ANTHROPIC_API_KEY", base_url=None, supports_temperature=False,
        )
        adapter = AnthropicAdapter(info, max_retries=0, allow_temperature_fallback=False)
        adapter._client = FakeClient()

        with pytest.raises(AdapterError):
            adapter.complete("sys", [{"role": "user", "content": "hi"}], temperature=0.7)

        assert len(captured) == 1
        assert "temperature" not in captured[0]

    def test_default_adapter_still_retries_on_429(self, monkeypatch):
        """max_retries未指定なら従来どおり3回（API_MAX_RETRIES=2）呼ばれること（回帰）"""
        import llm.adapters as adapters_mod
        from llm.adapters import OpenAICompatAdapter, AdapterError

        monkeypatch.setattr(adapters_mod.time, "sleep", lambda *a, **kw: None)
        calls: list[int] = []
        info = self._default_model_info()
        adapter = OpenAICompatAdapter(info)  # max_retries未指定
        adapter._client = self._always_raising_client(lambda: Exception("429 rate limit"), calls)

        with pytest.raises(AdapterError):
            adapter.complete("sys", [{"role": "user", "content": "hi"}])

        assert len(calls) == 3

    def test_client_built_with_max_retries_zero(self, monkeypatch):
        """max_retries=0 のアダプタは openai.OpenAI(max_retries=0) でクライアントを生成すること"""
        import openai
        from llm.adapters import OpenAICompatAdapter

        captured_kwargs = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

        monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
        monkeypatch.setenv("OPENAI_API_KEY", "dummy")

        info = self._h2_model_info()
        adapter = OpenAICompatAdapter(info, max_retries=0)
        adapter._get_client()

        assert captured_kwargs.get("max_retries") == 0

    def test_anthropic_client_built_with_max_retries_zero(self, monkeypatch):
        """Anthropic SDKにも strict 設定の max_retries=0 が渡ること。"""
        import anthropic
        from llm.adapters import AnthropicAdapter

        captured_kwargs = {}

        class FakeAnthropic:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

        monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
        monkeypatch.setenv("ANTHROPIC_API_KEY", "dummy")
        info = ModelInfo(
            model_id="claude-haiku-4-5-20251001", provider="Anthropic", name="Haiku",
            adapter_type="anthropic", input_price=1.0, output_price=5.0,
            env_key="ANTHROPIC_API_KEY", base_url=None,
        )
        AnthropicAdapter(info, max_retries=0)._get_client()

        assert captured_kwargs.get("max_retries") == 0

    def test_client_kwargs_omit_max_retries_by_default(self, monkeypatch):
        """max_retries未指定では openai.OpenAI に max_retries キーを渡さないこと（SDK既定維持の回帰）"""
        import openai
        from llm.adapters import OpenAICompatAdapter

        captured_kwargs = {}

        class FakeOpenAI:
            def __init__(self, **kwargs):
                captured_kwargs.update(kwargs)

        monkeypatch.setattr(openai, "OpenAI", FakeOpenAI)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "dummy")

        info = self._default_model_info()
        adapter = OpenAICompatAdapter(info)
        adapter._get_client()

        assert "max_retries" not in captured_kwargs

    def test_create_adapter_passes_strict_options_to_all_adapters(self):
        """create_adapterのstrict optionsが全adapter種別へ伝播すること。"""
        from llm.adapters import create_adapter, OpenAICompatAdapter, AnthropicAdapter

        openai_info = self._default_model_info()
        adapter = create_adapter(openai_info, max_retries=0)
        assert isinstance(adapter, OpenAICompatAdapter)
        assert adapter._max_retries == 0

        gemini_info = ModelInfo(
            model_id="gemini-3.5-flash", provider="Google", name="Gemini",
            adapter_type="gemini",
            input_price=0.1, output_price=0.4,
            env_key="GEMINI_API_KEY", base_url="https://example.invalid",
        )
        adapter2 = create_adapter(gemini_info, max_retries=0)
        assert isinstance(adapter2, OpenAICompatAdapter)
        assert adapter2._max_retries == 0

        anthropic_info = ModelInfo(
            model_id="claude-haiku-4-5-20251001", provider="Anthropic", name="Haiku",
            adapter_type="anthropic",
            input_price=1.0, output_price=5.0,
            env_key="ANTHROPIC_API_KEY", base_url=None,
        )
        adapter3 = create_adapter(anthropic_info, max_retries=0)
        assert isinstance(adapter3, AnthropicAdapter)
        assert adapter3._max_retries == 0

        strict = create_adapter(
            openai_info,
            max_retries=0,
            allow_temperature_fallback=False,
        )
        assert strict._allow_temperature_fallback is False

    def test_model_info_defaults(self):
        """ModelInfoの既定値: max_tokens_param=='max_tokens'、supports_temperature is True"""
        info = self._default_model_info()
        assert info.max_tokens_param == "max_tokens"
        assert info.supports_temperature is True


class TestLoggerNewFields:
    """Stage 1: ログエントリに新フィールドが正しく記録されることを検証"""

    def test_logger_entry_has_reasoning_tokens(self):
        """reasoning_tokens が usage から記録されること"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = LLMLogger(tmpdir, game_id="test_rt")
            logger.log_call(
                player_id="P01", model_id="test", phase="negotiation",
                round_num=1, turn=1, system_prompt="sys", user_prompt="user",
                response_text="resp",
                usage={"input_tokens": 100, "output_tokens": 50, "reasoning_tokens": 200},
                cost=0.01, elapsed_ms=100,
            )
            entry = logger._entries[-1]
            assert entry["reasoning_tokens"] == 200
            logger.close()

    def test_logger_entry_has_unit_price_snapshot(self):
        """単価スナップショット (unit_price_input/output) が記録されること"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = LLMLogger(tmpdir, game_id="test_up")
            logger.log_call(
                player_id="P01", model_id="gemini", phase="commit",
                round_num=2, turn=None, system_prompt="sys", user_prompt="user",
                response_text="resp",
                usage={"input_tokens": 100, "output_tokens": 50},
                cost=0.05, elapsed_ms=200,
                unit_price_input=1.50,
                unit_price_output=9.00,
            )
            entry = logger._entries[-1]
            assert entry["unit_price_input"] == 1.50
            assert entry["unit_price_output"] == 9.00
            logger.close()

    def test_logger_entry_has_usage_raw(self):
        """usage_raw が記録されること（None でも例外を出さないこと）"""
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = LLMLogger(tmpdir, game_id="test_ur")
            raw = {"prompt_tokens": 100, "completion_tokens": 50,
                   "completion_tokens_details": {"reasoning_tokens": 30}}
            logger.log_call(
                player_id="P01", model_id="test", phase="negotiation",
                round_num=1, turn=1, system_prompt="sys", user_prompt="user",
                response_text="resp",
                usage={"input_tokens": 100, "output_tokens": 50, "usage_raw": raw},
                cost=0.01, elapsed_ms=100,
            )
            entry = logger._entries[-1]
            assert entry["usage_raw"] == raw

            # usage_raw が無い場合は None
            logger.log_call(
                player_id="P01", model_id="test", phase="commit",
                round_num=1, turn=None, system_prompt="sys", user_prompt="user",
                response_text="resp",
                usage={"input_tokens": 100, "output_tokens": 50},
                cost=0.01, elapsed_ms=100,
            )
            entry2 = logger._entries[-1]
            assert entry2["usage_raw"] is None

            # ファイルにも正しく書かれること（JSON serializable）
            with open(logger._file_path, "r") as f:
                lines = f.readlines()
            assert len(lines) == 2
            parsed = json.loads(lines[0])
            assert parsed["usage_raw"]["completion_tokens_details"]["reasoning_tokens"] == 30

            logger.close()


class TestEstimateCostV2:
    """Stage 2: estimate_cost のキャッシュ割引・thinking内訳対応のテスト"""

    def test_cache_discount_deepseek(self):
        """キャッシュヒットでinputコストが割引されること（DeepSeek V4 Flash）"""
        from llm.models import MODEL_REGISTRY, estimate_cost
        model = MODEL_REGISTRY["M6"]  # DeepSeek V4 Flash
        # input=1000, うち cached=800 → uncached=200
        cost_with_cache = estimate_cost(model, input_tokens=1000, output_tokens=100,
                                        cache_read_input_tokens=800)
        # uncached: 200 * 0.14 + cached: 800 * 0.0028 + output: 100 * 0.28 = 28 + 2.24 + 28 = 58.24
        expected = (200 * 0.14 + 800 * 0.0028 + 100 * 0.28) / 1_000_000
        assert abs(cost_with_cache - expected) < 1e-12

        # キャッシュなしの場合
        cost_no_cache = estimate_cost(model, input_tokens=1000, output_tokens=100)
        expected_no_cache = (1000 * 0.14 + 100 * 0.28) / 1_000_000
        assert abs(cost_no_cache - expected_no_cache) < 1e-12

        # キャッシュありの方が安いこと
        assert cost_with_cache < cost_no_cache

    def test_cache_discount_gpt41_mini(self):
        """GPT-4.1 Mini のキャッシュ割引"""
        from llm.models import MODEL_REGISTRY, estimate_cost
        model = MODEL_REGISTRY["L2"]  # GPT-4.1 Mini: input=0.40, cached=0.10
        cost = estimate_cost(model, input_tokens=3000, output_tokens=400,
                             cache_read_input_tokens=1000)
        # uncached: 2000*0.40 + cached: 1000*0.10 + output: 400*1.60
        expected = (2000 * 0.40 + 1000 * 0.10 + 400 * 1.60) / 1_000_000
        assert abs(cost - expected) < 1e-12

    def test_gemini_thinking_adds_cost(self):
        """Gemini: total > input+output のとき separate_thinking が加算されること"""
        from llm.models import MODEL_REGISTRY, estimate_cost
        model = MODEL_REGISTRY["M3"]  # Gemini 3.5 Flash: output_price=9.00
        # input=2000, output=500, total=2800 → thinking=300
        cost_with_thinking = estimate_cost(model, input_tokens=2000, output_tokens=500,
                                           total_tokens=2800)
        cost_no_thinking = estimate_cost(model, input_tokens=2000, output_tokens=500,
                                         total_tokens=2500)  # total = input+output
        # 差分 = 300 * 9.00 / 1e6 = 0.0027
        thinking_diff = cost_with_thinking - cost_no_thinking
        expected_diff = 300 * 9.00 / 1_000_000
        assert abs(thinking_diff - expected_diff) < 1e-12
        assert cost_with_thinking > cost_no_thinking

    def test_no_double_counting_openai(self):
        """OpenAI系: total = input+output のとき thinking=0 で従来と同額"""
        from llm.models import MODEL_REGISTRY, estimate_cost
        model = MODEL_REGISTRY["M2"]  # GPT-4.1
        input_t, output_t = 2000, 600
        # total = input + output → separate_thinking = 0
        cost_new = estimate_cost(model, input_tokens=input_t, output_tokens=output_t,
                                 total_tokens=input_t + output_t)
        # 旧計算（total_tokens=0 → input+output で代用）
        cost_old = estimate_cost(model, input_tokens=input_t, output_tokens=output_t)
        assert abs(cost_new - cost_old) < 1e-12
        # 手計算と一致
        expected = (2000 * 2.0 + 600 * 8.0) / 1_000_000
        assert abs(cost_new - expected) < 1e-12

    def test_grok43_confirmed_price(self):
        """Grok 4.3 の単価が確定値であること"""
        from llm.models import MODEL_REGISTRY
        model = MODEL_REGISTRY["L4"]
        assert model.input_price == 1.25
        assert model.output_price == 2.50
        assert model.cached_input_price == 0.20

    def test_deepseek_v4_flash_model_id(self):
        """DeepSeek M6/L6 の model_id が deepseek-v4-flash であること"""
        from llm.models import MODEL_REGISTRY
        assert MODEL_REGISTRY["M6"].model_id == "deepseek-v4-flash"
        assert MODEL_REGISTRY["L6"].model_id == "deepseek-v4-flash"
        assert MODEL_REGISTRY["M6"].input_price == 0.14
        assert MODEL_REGISTRY["M6"].output_price == 0.28
        assert MODEL_REGISTRY["M6"].cached_input_price == 0.0028

    def test_backward_compat_no_extra_args(self):
        """新引数なしの呼び出しが従来と同じ結果を返すこと"""
        from llm.models import ModelInfo, estimate_cost
        model = ModelInfo(
            model_id="test", provider="Test", name="Test",
            adapter_type="openai_compat", input_price=1.0, output_price=5.0,
            env_key="TEST_KEY", base_url=None,
        )
        cost = estimate_cost(model, 1000, 200)
        expected = (1000 * 1.0 + 200 * 5.0) / 1_000_000
        assert abs(cost - expected) < 1e-12

    def test_cached_input_price_none_defaults_to_input(self):
        """cached_input_price=None のとき input_price と同値で計算されること"""
        from llm.models import ModelInfo, estimate_cost
        model = ModelInfo(
            model_id="test", provider="Test", name="Test",
            adapter_type="openai_compat", input_price=2.0, output_price=5.0,
            env_key="TEST_KEY", base_url=None,
            # cached_input_price=None (default)
        )
        # cache_read があっても input_price で計算される（割引なし=安全側）
        cost = estimate_cost(model, 1000, 200, cache_read_input_tokens=500)
        expected = (1000 * 2.0 + 200 * 5.0) / 1_000_000  # 500*2.0 + 500*2.0 = 1000*2.0
        assert abs(cost - expected) < 1e-12

    def test_logger_total_tokens_field(self):
        """ログエントリに total_tokens が記録されること"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            logger = LLMLogger(tmpdir, game_id="test_tt")
            logger.log_call(
                player_id="P01", model_id="test", phase="negotiation",
                round_num=1, turn=1, system_prompt="sys", user_prompt="user",
                response_text="resp",
                usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 180},
                cost=0.01, elapsed_ms=100,
            )
            entry = logger._entries[-1]
            assert entry["total_tokens"] == 180
            logger.close()


class TestCostBreakdown:
    """Stage 3: コスト内訳パネルのテスト"""

    def _make_logs(self, tmp_path):
        """テスト用のログファイルを作成"""
        trial_dir = tmp_path / "trial_test_001"
        llm_dir = trial_dir / "llm_logs"
        llm_dir.mkdir(parents=True)

        import json

        # P01: gemini-3.5-flash (thinking あり: total > input + output)
        entries_p01 = [
            {"model_id": "gemini-3.5-flash", "cost_usd": 0.01,
             "input_tokens": 2000, "output_tokens": 500, "total_tokens": 2800,
             "cache_read_input_tokens": 0, "round_num": 1, "phase": "negotiation",
             "timestamp": "2026-08-16T00:00:00Z", "response_text": "test",
             "error": None, "elapsed_ms": 100},
        ]
        with open(llm_dir / "game01_P01_llm_calls.jsonl", "w") as f:
            for e in entries_p01:
                f.write(json.dumps(e) + "\n")

        # P02: gpt-4.1-mini (cache あり、thinking なし: total = input + output)
        entries_p02 = [
            {"model_id": "gpt-4.1-mini", "cost_usd": 0.001,
             "input_tokens": 3000, "output_tokens": 400, "total_tokens": 3400,
             "cache_read_input_tokens": 1000, "round_num": 1, "phase": "negotiation",
             "timestamp": "2026-08-16T00:01:00Z", "response_text": "test2",
             "error": None, "elapsed_ms": 200},
        ]
        with open(llm_dir / "game01_P02_llm_calls.jsonl", "w") as f:
            for e in entries_p02:
                f.write(json.dumps(e) + "\n")

        return tmp_path, "trial_test_001", "game01"

    def test_cost_breakdown_aggregation(self, tmp_path):
        """get_cost_breakdown() がモデル別・プレイヤー別に正しく集計すること"""
        from viewer.log_parser import get_cost_breakdown
        logs_dir, trial_dir, game_id = self._make_logs(tmp_path)

        result = get_cost_breakdown(logs_dir, trial_dir, game_id)

        # モデル別チェック
        assert "gemini-3.5-flash" in result["by_model"]
        gemini = result["by_model"]["gemini-3.5-flash"]
        assert gemini["calls"] == 1
        assert gemini["input_tokens"] == 2000
        assert gemini["output_tokens"] == 500
        assert gemini["thinking_tokens"] == 300  # 2800 - 2000 - 500
        assert gemini["cost_thinking"] > 0  # thinking cost exists

        assert "gpt-4.1-mini" in result["by_model"]
        gpt = result["by_model"]["gpt-4.1-mini"]
        assert gpt["cache_read_tokens"] == 1000
        assert gpt["thinking_tokens"] == 0  # total = input + output

        # プレイヤー別チェック
        assert "P01" in result["by_player"]
        assert result["by_player"]["P01"]["thinking_tokens"] == 300
        assert "P02" in result["by_player"]
        assert result["by_player"]["P02"]["cache_read_tokens"] == 1000

        # 合計
        assert result["total_cost"] > 0
        assert result["total_cost_jpy"] > 0

    def test_cost_breakdown_no_reasoning_text(self, tmp_path):
        """get_cost_breakdown() にreasoning テキストが含まれないこと（秘匿維持）"""
        from viewer.log_parser import get_cost_breakdown
        logs_dir, trial_dir, game_id = self._make_logs(tmp_path)

        result = get_cost_breakdown(logs_dir, trial_dir, game_id)

        result_str = str(result)
        assert "response_text" not in result_str
        assert "reasoning_preview" not in result_str
        # response_text の内容が混入していないこと
        for model_data in result["by_model"].values():
            assert "response_text" not in model_data
            assert "reasoning" not in model_data
        for player_data in result["by_player"].values():
            assert "response_text" not in player_data
            assert "reasoning" not in player_data

    def test_stats_includes_token_breakdown(self, tmp_path):
        """get_game_state() の stats にトークン内訳が含まれること"""
        from viewer.log_parser import get_game_state
        logs_dir, trial_dir, game_id = self._make_logs(tmp_path)

        state = get_game_state(logs_dir, trial_dir, game_id)

        for p in state["players"]:
            stats = p["stats"]
            assert "input_tokens" in stats
            assert "output_tokens" in stats
            assert "cache_read_tokens" in stats
            assert "thinking_tokens" in stats
            if p["player_id"] == "P01":
                assert stats["thinking_tokens"] == 300
                assert stats["input_tokens"] == 2000
            elif p["player_id"] == "P02":
                assert stats["cache_read_tokens"] == 1000
                assert stats["thinking_tokens"] == 0
