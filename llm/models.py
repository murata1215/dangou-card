"""
モデルレジストリ

ロスター仕様書v1.0の12モデルを定義。
モデルID・アダプタ種別・単価・API設定を管理する。
L7は2026-08-16追加の負荷試験専用枠（12モデル仕様書には含まれない）。

Step 3C-prep2: モデルIDを各社APIで確認した実在IDに確定。
- OpenAI: gpt-4.1 / gpt-4.1-mini（最新世代）
- xAI: grok-4.5 / grok-4.3（実在確認）
- Gemini: gemini-3.5-flash / gemini-3.5-flash-lite（OpenAI互換エンドポイント）
- Moonshot: kimi-k2.6（api.moonshot.aiが正しいエンドポイント）
- DeepSeek: deepseek-chat（402入金待ち）
- Anthropic: claude-sonnet-5 / claude-haiku-4-5（確認済み）
"""

from dataclasses import dataclass


# --- provider 表示名 → emotion画像/資産のベンダーキー正規化 ---
# engine/blog/roster.py の _vendor() と同じ対応表（将来的にあちらから参照する）
_PROVIDER_TO_VENDOR: dict[str, str] = {
    "Anthropic": "anthropic",
    "OpenAI": "openai",
    "Google": "google",
    "xAI": "xai",
    "Moonshot": "moonshot",
    "DeepSeek": "deepseek",
}

# --- 階級順・ベンダー順（表示・実行順のソートキーに使用） ---
TIER_ORDER: tuple[str, ...] = ("H", "M", "L")
VENDOR_ORDER: tuple[str, ...] = ("Anthropic", "OpenAI", "Google", "xAI", "Moonshot", "DeepSeek")


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
    max_tokens_param: str = "max_tokens"  # OpenAI互換APIへ送るトークン上限のキー名。GPT-5系reasoningは "max_completion_tokens"
    supports_temperature: bool = True     # False なら temperature を一切送らない（reasoning系で拒否されるモデル用）
    extra_params: dict | None = None    # API固有パラメータ（例: {"thinking": {"type": "disabled"}}）
    cached_input_price: float | None = None  # キャッシュヒット入力単価（$/1Mトークン）。None → input_price と同値
    reasoning_price: float | None = None     # thinkingトークン単価（$/1Mトークン）。None → output_price と同値
    tier: str = ""           # "H"(強)/"M"(中)/"L"(軽)。"" = 未分類（テスト用アドホック生成のデフォルト）

    @property
    def vendor(self) -> str:
        """provider 表示名を emotion画像/資産のベンダーキーへ正規化する（engine/blog/roster.py._vendor と同義）"""
        return _PROVIDER_TO_VENDOR.get(self.provider, "anthropic")


# --- Gemini OpenAI互換エンドポイント ---
GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"

# --- Moonshot国際版エンドポイント ---
MOONSHOT_BASE_URL = "https://api.moonshot.ai/v1"

# --- ロスター仕様書v1.0の12モデル（疎通確認済みID） + L7（負荷試験用追加枠） ---
MODEL_REGISTRY: dict[str, ModelInfo] = {
    # Tier M（中量級）
    "M1": ModelInfo(
        model_id="claude-sonnet-5",
        provider="Anthropic", name="Claude Sonnet 5",
        adapter_type="anthropic",
        input_price=2.0, output_price=10.0,  # Anthropic料金表 2026-08-16
        env_key="CLAUDE_API_KEY", base_url=None,
        # cached_input_price: 記載なし→input同値（安全側）
        tier="M",
    ),
    "M2": ModelInfo(
        model_id="gpt-4.1",
        provider="OpenAI", name="GPT-4.1",
        adapter_type="openai_compat",
        input_price=2.0, output_price=8.0,  # OpenAI料金表 2026-08-16
        env_key="OPENAI_API_KEY", base_url=None,
        cached_input_price=0.50,  # OpenAI料金表 2026-08-16
        tier="M",
    ),
    "M3": ModelInfo(
        model_id="gemini-3.5-flash",
        provider="Google", name="Gemini 3.5 Flash",
        adapter_type="gemini",
        input_price=1.50, output_price=9.00,  # Google公式 2026-08 ai.google.dev/gemini-api/docs/pricing
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        # TODO: Geminiキャッシュ単価はContext Caching API別体系（$/1M tokens/hour）。ここでは未設定=input同値
        tier="M",
    ),
    "M4": ModelInfo(
        model_id="grok-4.5",
        provider="xAI", name="Grok 4.5",
        adapter_type="openai_compat",
        input_price=2.0, output_price=6.0,  # xAI料金表 2026-08-16
        env_key="GROK_API_KEY", base_url="https://api.x.ai/v1",
        cached_input_price=0.30,  # xAI料金表 2026-08-16
        tier="M",
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
        tier="M",
    ),
    "M6": ModelInfo(
        model_id="deepseek-v4-flash",  # 実測: deepseek-chatはV4 Flashにリダイレクト確認 2026-08-16
        provider="DeepSeek", name="DeepSeek V4 Flash",
        adapter_type="openai_compat",
        input_price=0.14, output_price=0.28,  # DeepSeek料金表 2026-08-16（V4 Flash単価）
        env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
        cached_input_price=0.0028,  # DeepSeek料金表 2026-08-16
        # 2026-08-17: thinking既定ON(effort=high)がmax_tokens=4000を食い潰しfinish_reason=length
        # →content空→reasoning_contentフォールバックでJSON崩壊（seed=701実測 P04 25.2%/P05 13.7%）。
        # 8コール診断実験でthinking無効化によりreasoning_tokens=0・JSON成功率100%・latency大幅改善を確認。
        extra_params={"thinking": {"type": "disabled"}},
        max_tokens=2000,
        tier="M",
    ),
    # Tier L（軽量級）
    "L1": ModelInfo(
        model_id="claude-haiku-4-5-20251001",
        provider="Anthropic", name="Claude Haiku 4.5",
        adapter_type="anthropic",
        input_price=1.0, output_price=5.0,  # Anthropic料金表 2026-08-16
        env_key="CLAUDE_API_KEY", base_url=None,
        cached_input_price=0.10,  # Anthropic料金表 2026-08-16
        tier="L",
    ),
    "L2": ModelInfo(
        model_id="gpt-4.1-mini",
        provider="OpenAI", name="GPT-4.1 Mini",
        adapter_type="openai_compat",
        input_price=0.40, output_price=1.60,  # OpenAI料金表 2026-08-16
        env_key="OPENAI_API_KEY", base_url=None,
        cached_input_price=0.10,  # OpenAI料金表 2026-08-16
        tier="L",
    ),
    "L3": ModelInfo(
        model_id="gemini-3.5-flash-lite",
        provider="Google", name="Gemini 3.5 Flash-Lite",
        adapter_type="gemini",
        input_price=0.30, output_price=2.50,  # Google公式 2026-08 ai.google.dev/gemini-api/docs/pricing
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        # TODO: Geminiキャッシュ単価はContext Caching API別体系。ここでは未設定=input同値
        tier="L",
    ),
    "L4": ModelInfo(
        model_id="grok-4.3",
        provider="xAI", name="Grok 4.3",
        adapter_type="openai_compat",
        input_price=1.25, output_price=2.50,  # xAI料金表 2026-08-16（旧: 0.60/3.00 は過小/過大）
        env_key="GROK_API_KEY", base_url="https://api.x.ai/v1",
        cached_input_price=0.20,  # xAI料金表 2026-08-16
        tier="L",
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
        tier="L",
    ),
    "L6": ModelInfo(
        model_id="deepseek-v4-flash",  # 実測: deepseek-chatはV4 Flashにリダイレクト確認 2026-08-16
        provider="DeepSeek", name="DeepSeek V4 Flash (L)",
        adapter_type="openai_compat",
        input_price=0.14, output_price=0.28,  # DeepSeek料金表 2026-08-16（V4 Flash単価）
        env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
        cached_input_price=0.0028,  # DeepSeek料金表 2026-08-16
        # 2026-08-17: M6と同一model_idにつき同一の是正が必要（片方だけ直すと片肺暴走する）
        extra_params={"thinking": {"type": "disabled"}},
        max_tokens=2000,
        tier="L",
    ),
    # Tier L+（負荷試験用の追加安価枠。2026-08-16追加、ロスター仕様書v1.0の12モデルとは別枠）
    "L7": ModelInfo(
        model_id="gemini-2.5-flash-lite",
        provider="Google", name="Gemini 2.5 Flash-Lite",
        adapter_type="gemini",
        input_price=0.10, output_price=0.40,  # ユーザー指定単価 2026-08-16（18体負荷試験用）
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        # cached_input_price / reasoning_price: 未設定（L3/M3と同じ扱い、input/output単価を代用）
        tier="L",
    ),
    # Tier H（強量級・フラッグシップ）
    # model_idは scripts/discover_models.py（2026-08-17実行）の一覧APIで実在確認済み。
    # 単価は一覧APIからは取得不可のため、doc/uso8000000_model_roster_v1_0.md §3
    # （決勝候補、2026-08-09時点情報）を出典とする。実戦投入前に各社公式料金ページで
    # 必ず再確認すること（M2=GPT-5.6 Terra→実装gpt-4.1のような乖離が過去に発生済み）。
    "H1": ModelInfo(
        model_id="claude-opus-5",
        provider="Anthropic", name="Claude Opus 5",
        adapter_type="anthropic",
        input_price=5.0, output_price=25.0,  # roster_v1_0.md §3 (2026-08-09時点) 要再確認
        env_key="CLAUDE_API_KEY", base_url=None,
        tier="H",
    ),
    "H2": ModelInfo(
        model_id="gpt-5.6-sol",
        provider="OpenAI", name="GPT-5.6 Sol",
        adapter_type="openai_compat",
        input_price=5.0, output_price=30.0,  # roster_v1_0.md §3 (2026-08-09時点) 要再確認
        env_key="OPENAI_API_KEY", base_url=None,
        tier="H",
        # 2026-08-18: Phase 1実測で400 "max_tokens is not supported" を確認。
        # reasoning系のため max_completion_tokens を使用し、temperatureは送らない。
        max_tokens_param="max_completion_tokens",
        supports_temperature=False,
    ),
    "H3": ModelInfo(
        model_id="gemini-3.1-pro-preview",
        provider="Google", name="Gemini 3.1 Pro",
        adapter_type="gemini",
        input_price=2.0, output_price=12.0,  # roster_v1_0.md §3 (2026-08-09時点) 要再確認。2M文脈
        env_key="GEMINI_API_KEY", base_url=GEMINI_OPENAI_BASE_URL,
        tier="H",
    ),
    "H4": ModelInfo(
        model_id="grok-4.6",
        provider="xAI", name="Grok 4.6",
        adapter_type="openai_compat",
        # roster_v1_0.md §3 にxAI強ティア記載なし（欠落）。grok-4.5(M4: $2/$6)からの
        # 推定値。実戦投入前に xAI 公式料金ページで確定させること（TODO）。
        input_price=5.0, output_price=15.0,
        env_key="GROK_API_KEY", base_url="https://api.x.ai/v1",
        tier="H",
    ),
    "H5": ModelInfo(
        model_id="kimi-k3",
        provider="Moonshot", name="Kimi K3",
        adapter_type="openai_compat",
        input_price=3.0, output_price=15.0,  # roster_v1_0.md §3 (2026-08-09時点) 要再確認
        env_key="KIMI_API_KEY", base_url=MOONSHOT_BASE_URL,
        timeout_seconds=90,
        extra_params={"thinking": {"type": "disabled"}},  # M5/L5と同じ理由でthinking無効化
        tier="H",
    ),
    "H6": ModelInfo(
        model_id="deepseek-v4-pro",
        provider="DeepSeek", name="DeepSeek V4 Pro",
        adapter_type="openai_compat",
        # roster_v1_0.md §2表(M6の当初案) 由来。DeepSeekは旗艦級でも低価格帯を維持する
        # 傾向があるため妥当性はあるが、v4-flash(M6/L6)と同じくthinking暴走リスクがあり
        # 実戦投入前に公式料金ページとthinking挙動を要再確認。
        input_price=0.435, output_price=0.87,
        env_key="DEEPSEEK_API_KEY", base_url="https://api.deepseek.com",
        extra_params={"thinking": {"type": "disabled"}},  # M6/L6と同じ理由でthinking無効化
        max_tokens=2000,
        tier="H",
    ),
}


def get_model(key: str) -> ModelInfo:
    """モデルキー(H1〜H6, M1〜M6, L1〜L6, L7)またはmodel_idからModelInfoを取得

    model_idでの逆引きは「先勝ち」（MODEL_REGISTRY定義順で最初に一致した1件）。
    M5/L5, M6/L6 のようにmodel_idが重複するエントリでは、宣言順が早い方
    （辞書順=このファイルでの記述順）が常に返る。viewer/log_parser.py の
    ローカル逆引きも同じ先勝ちルールに統一されている（setdefault使用）。
    """
    if key in MODEL_REGISTRY:
        return MODEL_REGISTRY[key]
    for info in MODEL_REGISTRY.values():
        if info.model_id == key:
            return info
    raise ValueError(f"Unknown model: {key}")


def get_models_by_tier(tier: str) -> dict[str, ModelInfo]:
    """指定した階級("H"/"M"/"L")のエントリのみを宣言順で返す（L7のtierは"L"なので含まれる）"""
    return {k: v for k, v in MODEL_REGISTRY.items() if v.tier == tier}


def get_model_keys_by_id(model_id: str) -> list[str]:
    """指定model_idを持つ全レジストリキーを宣言順で返す（get_modelの先勝ちで潰れる重複を保持する正直版）"""
    return [k for k, v in MODEL_REGISTRY.items() if v.model_id == model_id]


def list_vendor_endpoints() -> list[tuple[str, str, str | None, str]]:
    """(provider, env_key, base_url, adapter_type) のベンダー別一覧をprovider重複を除いて返す

    scripts/discover_models.py がモデルID発見のためのAPIエンドポイント一覧として使用する。
    """
    seen: set[str] = set()
    result: list[tuple[str, str, str | None, str]] = []
    for info in MODEL_REGISTRY.values():
        if info.provider in seen:
            continue
        seen.add(info.provider)
        result.append((info.provider, info.env_key, info.base_url, info.adapter_type))
    return result


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
