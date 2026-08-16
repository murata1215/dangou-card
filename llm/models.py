"""
モデルレジストリ

ロスター仕様書v1.0の12モデルを定義。
モデルID・アダプタ種別・単価・API設定を管理する。

Step 3C-prep2: モデルIDを各社APIで確認した実在IDに確定。
- OpenAI: gpt-4.1 / gpt-4.1-mini（最新世代）
- xAI: grok-4.5 / grok-4.3（実在確認）
- Gemini: gemini-3.5-flash / gemini-3.5-flash-lite（OpenAI互換エンドポイント）
- Moonshot: kimi-k2.6（api.moonshot.aiが正しいエンドポイント）
- DeepSeek: deepseek-chat（402入金待ち）
- Anthropic: claude-sonnet-5 / claude-haiku-4-5（確認済み）
"""

from dataclasses import dataclass


@dataclass
class ModelInfo:
    """1モデルの定義情報"""
    model_id: str           # API呼出し用のモデルID
    provider: str           # プロバイダ名
    name: str               # 表示名
    adapter_type: str       # "anthropic" / "openai_compat" / "gemini"
    input_price: float      # 入力単価（$/1Mトークン）
    output_price: float     # 出力単価（$/1Mトークン）
    env_key: str            # 環境変数名（APIキー）
    base_url: str | None    # OpenAI互換のbase_url（Noneなら公式）
    timeout_seconds: int = 60  # APIコールのタイムアウト（秒）。reasoning系は長めに設定
    max_tokens: int | None = None       # per-model出力トークン上限（None → DEFAULT_MAX_TOKENS）
    extra_params: dict | None = None    # API固有パラメータ（例: {"thinking": {"type": "disabled"}}）
    cached_input_price: float | None = None  # キャッシュヒット入力単価（$/1Mトークン）。None → input_price と同値
    reasoning_price: float | None = None     # thinkingトークン単価（$/1Mトークン）。None → output_price と同値


# --- Gemini OpenAI互換エンドポイント ---
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# --- Moonshot国際版エンドポイント ---
MOONSHOT_BASE_URL = "https://api.moonshot.ai/v1"

# --- ロスター仕様書v1.0の12モデル（疎通確認済みID） ---
MODEL_REGISTRY: dict[str, ModelInfo] = {
    # Tier M（中量級）
    "M1": ModelInfo(
        model_id="claude-sonnet-5",
        provider="Anthropic", name="Claude Sonnet 5",
        adapter_type="anthropic",
        input_price=2.0, output_price=10.0,  # Anthropic料金表 2026-08-16
        env_key="CLAUDE_API_KEY", base_url=None,
        # cached_input_price: 記載なし→input同値（安全側）
    ),
    "M2": ModelInfo(
        model_id="gpt-4.1",
        provider="OpenAI", name="GPT-4.1",
        adapter_type="openai_compat",
        input_price=2.0, output_price=8.0,  # OpenAI料金表 2026-08-16
        env_key="OPENAI_API_KEY", base_url=None,
        cached_input_price=0.50,  # OpenAI料金表 2026-08-16
    ),
    "M3": ModelInfo(
        model_id="gemini-3.5-flash",
        provider="Google", name="Gemini 3.5 Flash",
        adapter_type="gemini",
        input_price=1.50, output_price=9.00,  # Google公式 2026-08 ai.google.dev/gemini-api/docs/pricing
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        # TODO: Geminiキャッシュ単価はContext Caching API別体系（$/1M tokens/hour）。ここでは未設定=input同値
    ),
    "M4": ModelInfo(
        model_id="grok-4.5",
        provider="xAI", name="Grok 4.5",
        adapter_type="openai_compat",
        input_price=2.0, output_price=6.0,  # xAI料金表 2026-08-16
        env_key="GROK_API_KEY", base_url="https://api.x.ai/v1",
        cached_input_price=0.30,  # xAI料金表 2026-08-16
    ),
    "M5": ModelInfo(
        model_id="kimi-k2.6",
        provider="Moonshot", name="Kimi K2.6",
        adapter_type="openai_compat",
        input_price=0.95, output_price=4.0,  # Moonshot料金表 2026-08-16
        env_key="KIMI_API_KEY", base_url=MOONSHOT_BASE_URL,
        timeout_seconds=90,
        extra_params={"thinking": {"type": "disabled"}},
        # cached_input_price: 記載なし→input同値
    ),
    "M6": ModelInfo(
        model_id="deepseek-v4-flash",  # 実測: deepseek-chatはV4 Flashにリダイレクト確認 2026-08-16
        provider="DeepSeek", name="DeepSeek V4 Flash",
        adapter_type="openai_compat",
        input_price=0.14, output_price=0.28,  # DeepSeek料金表 2026-08-16（V4 Flash単価）
        env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
        cached_input_price=0.0028,  # DeepSeek料金表 2026-08-16
    ),
    # Tier L（軽量級）
    "L1": ModelInfo(
        model_id="claude-haiku-4-5-20251001",
        provider="Anthropic", name="Claude Haiku 4.5",
        adapter_type="anthropic",
        input_price=1.0, output_price=5.0,  # Anthropic料金表 2026-08-16
        env_key="CLAUDE_API_KEY", base_url=None,
        cached_input_price=0.10,  # Anthropic料金表 2026-08-16
    ),
    "L2": ModelInfo(
        model_id="gpt-4.1-mini",
        provider="OpenAI", name="GPT-4.1 Mini",
        adapter_type="openai_compat",
        input_price=0.40, output_price=1.60,  # OpenAI料金表 2026-08-16
        env_key="OPENAI_API_KEY", base_url=None,
        cached_input_price=0.10,  # OpenAI料金表 2026-08-16
    ),
    "L3": ModelInfo(
        model_id="gemini-3.5-flash-lite",
        provider="Google", name="Gemini 3.5 Flash-Lite",
        adapter_type="gemini",
        input_price=0.30, output_price=2.50,  # Google公式 2026-08 ai.google.dev/gemini-api/docs/pricing
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        # TODO: Geminiキャッシュ単価はContext Caching API別体系。ここでは未設定=input同値
    ),
    "L4": ModelInfo(
        model_id="grok-4.3",
        provider="xAI", name="Grok 4.3",
        adapter_type="openai_compat",
        input_price=1.25, output_price=2.50,  # xAI料金表 2026-08-16（旧: 0.60/3.00 は過小/過大）
        env_key="GROK_API_KEY", base_url="https://api.x.ai/v1",
        cached_input_price=0.20,  # xAI料金表 2026-08-16
    ),
    "L5": ModelInfo(
        model_id="kimi-k2.6",
        provider="Moonshot", name="Kimi K2.6 (L)",
        adapter_type="openai_compat",
        input_price=0.95, output_price=4.0,  # Moonshot料金表 2026-08-16
        env_key="KIMI_API_KEY", base_url=MOONSHOT_BASE_URL,
        timeout_seconds=90,
        extra_params={"thinking": {"type": "disabled"}},
        # cached_input_price: 記載なし→input同値
    ),
    "L6": ModelInfo(
        model_id="deepseek-v4-flash",  # 実測: deepseek-chatはV4 Flashにリダイレクト確認 2026-08-16
        provider="DeepSeek", name="DeepSeek V4 Flash (L)",
        adapter_type="openai_compat",
        input_price=0.14, output_price=0.28,  # DeepSeek料金表 2026-08-16（V4 Flash単価）
        env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
        cached_input_price=0.0028,  # DeepSeek料金表 2026-08-16
    ),
}


def get_model(key: str) -> ModelInfo:
    """モデルキー(M1〜M6, L1〜L6)またはmodel_idからModelInfoを取得"""
    if key in MODEL_REGISTRY:
        return MODEL_REGISTRY[key]
    for info in MODEL_REGISTRY.values():
        if info.model_id == key:
            return info
    raise ValueError(f"Unknown model: {key}")


def estimate_cost(
    model: ModelInfo,
    input_tokens: int,
    output_tokens: int,
    cache_read_input_tokens: int = 0,
    total_tokens: int = 0,
) -> float:
    """
    トークン数からコスト（USD）を概算する

    Stage 2: キャッシュ割引・thinking トークン内訳に対応。
    - cache_read_input_tokens: キャッシュヒット入力トークン数（割引単価適用）
    - total_tokens: 全トークン合計（0の場合 input+output で代用）

    二重計上防止:
      separate_thinking = max(0, total_tokens - input_tokens - output_tokens)
      OpenAI系: reasoning は completion_tokens に含まれる → separate_thinking ≈ 0（自動的に二重計上なし）
      Gemini系: completion に thinking が含まれない → 差分 = thinking が加算される
      Anthropic: thinking は output_tokens に含まれる → total = input + output → separate_thinking = 0
    """
    effective_cached_price = model.cached_input_price if model.cached_input_price is not None else model.input_price
    effective_reasoning_price = model.reasoning_price if model.reasoning_price is not None else model.output_price

    # 入力コスト: 未キャッシュ分はフル単価、キャッシュ分は割引単価
    uncached_input = input_tokens - cache_read_input_tokens
    input_cost = uncached_input * model.input_price + cache_read_input_tokens * effective_cached_price

    # 出力コスト
    output_cost = output_tokens * model.output_price

    # thinking コスト（二重計上防止: total - input - output の差分のみ）
    effective_total = total_tokens if total_tokens > 0 else (input_tokens + output_tokens)
    separate_thinking = max(0, effective_total - input_tokens - output_tokens)
    thinking_cost = separate_thinking * effective_reasoning_price

    return (input_cost + output_cost + thinking_cost) / 1_000_000
