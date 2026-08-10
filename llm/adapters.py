"""
LLM APIアダプタ

各社APIへの接続を共通インターフェースで抽象化する。
.envの読み取りはこのモジュール内に限定。
キー・生レスポンス内のキー情報をログ・例外メッセージに絶対に出さない。

修正3: プロンプトキャッシュ最適化（Anthropic cache_control）
修正7: タイムアウト60秒 + 指数バックオフリトライ
"""

import os
import time
from typing import Any

from llm.models import ModelInfo
from llm.constants import API_TIMEOUT_SECONDS, API_MAX_RETRIES


class AdapterError(Exception):
    """APIアダプタのエラー（キー情報を含まない安全なメッセージのみ）"""
    pass


def _should_retry(error: Exception) -> bool:
    """リトライすべきエラーか判定する（429/5xx/接続エラー/タイムアウト）"""
    error_str = str(error).lower()
    error_type = type(error).__name__.lower()
    # タイムアウト
    if "timeout" in error_str or "timeout" in error_type:
        return True
    # レート制限 (429)
    if "429" in error_str or "rate" in error_str:
        return True
    # サーバーエラー (5xx)
    if any(f"{code}" in error_str for code in [500, 502, 503, 504]):
        return True
    # 接続エラー
    if "connection" in error_str or "connect" in error_type:
        return True
    return False


def _classify_error(error: Exception) -> str:
    """エラー種別を分類する（ログ用）"""
    error_str = str(error).lower()
    if "timeout" in error_str:
        return "timeout"
    if "429" in error_str or "rate" in error_str:
        return "rate_limit"
    if any(f"{code}" in error_str for code in [500, 502, 503, 504]):
        return "server_error"
    if "connection" in error_str:
        return "connection_error"
    return "other"


class AnthropicAdapter:
    """
    Anthropic API アダプタ

    修正3: system部にcache_controlを付与
    修正7: タイムアウト + リトライ
    """

    def __init__(self, model_info: ModelInfo) -> None:
        self.model_info = model_info
        self._client = None

    def _get_client(self) -> Any:
        """遅延初期化でクライアントを取得（キーをログに出さない）"""
        if self._client is None:
            import anthropic
            import httpx
            api_key = os.environ.get(self.model_info.env_key)
            if not api_key:
                raise AdapterError(
                    f"Environment variable {self.model_info.env_key} is not set"
                )
            # モデル別タイムアウト設定
            self._client = anthropic.Anthropic(
                api_key=api_key,
                timeout=httpx.Timeout(self.model_info.timeout_seconds),
            )
        return self._client

    def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> tuple[str, dict[str, int]]:
        """
        APIコールを実行する（リトライ付き）

        Returns:
            (レスポンステキスト, usage辞書)
            usage辞書にはcache_read_input_tokensも含む
        """
        last_error = None
        for attempt in range(API_MAX_RETRIES + 1):
            try:
                client = self._get_client()
                # 修正3: system部にcache_controlを付与
                # extended thinking対応: temperatureが使えないモデルもある
                kwargs: dict[str, Any] = {
                    "model": self.model_info.model_id,
                    "system": [{
                        "type": "text",
                        "text": system,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    "messages": messages,
                    "max_tokens": max_tokens,
                }
                # temperature非対応モデル（Sonnet 5等のextended thinking）の判定:
                # まずtemperature付きで試し、400エラーなら省略してリトライ
                try:
                    response = client.messages.create(temperature=temperature, **kwargs)
                except Exception as temp_err:
                    if "temperature" in str(temp_err).lower() and "deprecated" in str(temp_err).lower():
                        response = client.messages.create(**kwargs)
                    else:
                        raise
                # Sonnet 5等はThinkingBlockを返す場合がある。TextBlockのみ抽出
                text = ""
                for block in (response.content or []):
                    if hasattr(block, "text"):
                        text = block.text
                        break
                usage = {
                    "input_tokens": response.usage.input_tokens,
                    "output_tokens": response.usage.output_tokens,
                    "cache_creation_input_tokens": getattr(response.usage, "cache_creation_input_tokens", 0) or 0,
                    "cache_read_input_tokens": getattr(response.usage, "cache_read_input_tokens", 0) or 0,
                }
                return text, usage

            except Exception as e:
                last_error = e
                if attempt < API_MAX_RETRIES and _should_retry(e):
                    # 指数バックオフ: 1秒, 2秒
                    wait = 2 ** attempt
                    time.sleep(wait)
                    continue
                break

        # 全リトライ失敗
        error_type = type(last_error).__name__ if last_error else "Unknown"
        error_msg = str(last_error)[:200] if last_error else ""
        raise AdapterError(
            f"Anthropic API error ({error_type}): {error_msg}"
        ) from None


class OpenAICompatAdapter:
    """
    OpenAI互換API アダプタ

    openai SDKのbase_url切替でDeepSeek/Kimi/Grok/OpenAIに対応。
    修正7: タイムアウト + リトライ
    """

    def __init__(self, model_info: ModelInfo) -> None:
        self.model_info = model_info
        self._client = None

    def _get_client(self) -> Any:
        """遅延初期化でクライアントを取得"""
        if self._client is None:
            import openai
            api_key = os.environ.get(self.model_info.env_key)
            if not api_key:
                raise AdapterError(
                    f"Environment variable {self.model_info.env_key} is not set"
                )
            kwargs: dict[str, Any] = {
                "api_key": api_key,
                "timeout": self.model_info.timeout_seconds,  # モデル別タイムアウト
            }
            if self.model_info.base_url:
                kwargs["base_url"] = self.model_info.base_url
            self._client = openai.OpenAI(**kwargs)
        return self._client

    def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1000,
        temperature: float = 0.7,
    ) -> tuple[str, dict[str, int]]:
        """APIコールを実行する（リトライ付き）"""
        last_error = None
        for attempt in range(API_MAX_RETRIES + 1):
            try:
                client = self._get_client()
                full_messages = [{"role": "system", "content": system}] + messages
                create_kwargs = {
                    "model": self.model_info.model_id,
                    "messages": full_messages,
                    "max_tokens": max_tokens,
                }
                # temperatureフォールバック: 一部モデル(Kimi k2.6等)は特定値のみ許可
                try:
                    response = client.chat.completions.create(
                        temperature=temperature, **create_kwargs,
                    )
                except Exception as temp_err:
                    if "temperature" in str(temp_err).lower():
                        # temperature制限のあるモデル: temperature省略で再試行
                        response = client.chat.completions.create(**create_kwargs)
                    else:
                        raise
                text = response.choices[0].message.content or "" if response.choices else ""
                # reasoning系モデル対応: contentが空でreasoning_contentに本文がある場合
                if not text and response.choices:
                    extras = getattr(response.choices[0].message, 'model_extra', None) or {}
                    text = extras.get('reasoning_content', '') or ''
                usage = {
                    "input_tokens": getattr(response.usage, "prompt_tokens", 0) or 0,
                    "output_tokens": getattr(response.usage, "completion_tokens", 0) or 0,
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                }
                return text, usage

            except Exception as e:
                last_error = e
                if attempt < API_MAX_RETRIES and _should_retry(e):
                    wait = 2 ** attempt
                    time.sleep(wait)
                    continue
                break

        error_type = type(last_error).__name__ if last_error else "Unknown"
        error_msg = str(last_error)[:200] if last_error else ""
        raise AdapterError(
            f"OpenAI-compat API error ({error_type}): {error_msg}"
        ) from None


class GeminiStub:
    """Gemini APIスタブ（本ステップでは未実装）"""

    def __init__(self, model_info: ModelInfo) -> None:
        self.model_info = model_info

    def complete(self, system, messages, max_tokens=1000, temperature=0.7):
        raise NotImplementedError(
            f"Gemini adapter is not yet implemented (model: {self.model_info.model_id})"
        )


def create_adapter(model_info: ModelInfo) -> AnthropicAdapter | OpenAICompatAdapter | GeminiStub:
    """ModelInfoからアダプタを自動選択して生成する"""
    if model_info.adapter_type == "anthropic":
        return AnthropicAdapter(model_info)
    elif model_info.adapter_type == "openai_compat":
        return OpenAICompatAdapter(model_info)
    elif model_info.adapter_type == "gemini":
        # GeminiはOpenAI互換エンドポイントを使用（google-genai SDK不要）
        return OpenAICompatAdapter(model_info)
    else:
        raise ValueError(f"Unknown adapter type: {model_info.adapter_type}")
