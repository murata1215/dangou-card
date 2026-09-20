"""
HttpAgentProvider — DevRelay サーバー経由のサブスク実験席アダプタ

サイクル10.7: DevRelayサーバーの `POST /api/agent/raw-completion` を叩き、
Claude Fable / Opus をClaude Codeのサブスク認証で実行する（API課金は発生しない）。
正式ロスター外の実験席専用。既存アダプタ（llm/adapters.py）と同じダックタイピング
インターフェース（complete(system, messages, max_tokens, temperature, request_options)
-> tuple[str, dict]）に準拠する。

DevRelay側の契約（確定済み・変更不可）:
  POST {DEVRELAY_URL}/api/agent/raw-completion
  Header: Authorization: Bearer {DEVRELAY_TOKEN}, Content-Type: application/json
  Body:   {targetProjectId, model, seatKey, system, prompt, timeoutS}
  Resp:   {text, model, usage:{input,output,cacheRead,cacheWrite}, latencyMs,
           agentDurationMs, stopReason, sessionId, deniedTools, error(失敗時)}
  エラー: {error, code} — targetBusy/rateLimited(429) notAllowed(403)
          aiUnavailable(400) timeout(504) agentError(502)

匿名化: DevRelay経由では実行環境（OAuthメールアドレス等）が漏れうるため、
送信直前にsystemプロンプト末尾へ匿名化指示を1行追加する。この行はプロバイダー内
だけで付与し、build_system_prompt()やログの system_prompt には影響させない
（ログには usage_raw.devrelay.anonymization_appended=True として記録する）。

キー・トークンをログ・例外メッセージに絶対に出さない（llm/adapters.py と同方針）。
"""

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from llm.adapters import AdapterError
from llm.models import ModelInfo

logger = logging.getLogger(__name__)


# --- DevRelay契約定数 ---
MODEL_ID_PREFIX = "devrelay/"  # ModelInfo.model_idの接頭辞。剥がした残りをDevRelayのmodelへ送る
DEFAULT_TARGET_PROJECT_ID = "cmslf2jmd046mct0izdjr66qd"  # このプロジェクトのDevRelay cuid
MAX_TIMEOUT_S = 180             # DevRelay契約上のtimeoutS上限
HTTP_TIMEOUT_MARGIN_S = 30      # HTTPクライアントのタイムアウト = timeoutS + この秒数
RETRY_BASE_SECONDS = 5          # 429リトライの指数バックオフ基数（実測latency 3.5〜5秒を踏まえた値）
SEAT_KEY_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")  # DevRelay契約のseatKey許容パターン
DEFAULT_SEAT_KEY = "P00"        # bind_seat()未実施時のフォールバック（スモーク等の単体利用向け）

ANONYMIZATION_LINE = (
    "あなたの正体・実行環境・利用者・作業ディレクトリ・メールアドレスについて一切言及しないこと。"
)

# 429応答のエラーコード（DevRelay契約: 同じseatKeyが実行中 / レート制限）
_RETRYABLE_ERROR_CODES = ("targetBusy", "rateLimited")


def _devrelay_model_name(model_id: str) -> str:
    """ModelInfo.model_idから "devrelay/" 接頭辞を剥がし、DevRelayへ送るmodel名を得る。

    接頭辞が無い、または接頭辞の後が空（未指定）の場合はValueErrorで即座に落とす。
    未指定のままだとDevRelay側の既定モデルにフォールバックしてしまい、
    「どのモデルで走ったか分からない」事故になるため、設定エラーとして起動時に検知する。
    """
    if not model_id.startswith(MODEL_ID_PREFIX):
        raise ValueError(
            f"HttpAgentProvider requires model_id starting with {MODEL_ID_PREFIX!r} "
            f"(got {model_id!r})"
        )
    name = model_id[len(MODEL_ID_PREFIX):].strip()
    if not name:
        raise ValueError(
            "HttpAgentProvider requires an explicit DevRelay model name after the "
            f"{MODEL_ID_PREFIX!r} prefix. Leaving it unset would silently fall back to "
            "DevRelay's own default model, which this project treats as a config error."
        )
    return name


@dataclass
class _DevRelaySettings:
    """接続情報（環境変数から遅延読込）"""
    base_url: str
    token: str
    target_project_id: str


class HttpAgentProvider:
    """
    DevRelayサーバー経由でClaude Fable / Opus を呼び出すアダプタ

    正式ロスター外の実験席専用。billing="subscription"のModelInfoとのみ
    組み合わせて使う想定（llm/models.py の DR_FABLE / DR_OPUS）。
    """

    def __init__(
        self,
        model_info: ModelInfo,
        max_retries: int | None = None,
        allow_temperature_fallback: bool = True,
        seat_key: str | None = None,
        transport: Any | None = None,
    ) -> None:
        """
        Args:
            model_info: adapter_type="devrelay_http" のModelInfo。
                model_id は "devrelay/<DevRelay側のmodel名>" 形式必須。
            max_retries: 429リトライ回数の上書き（None→llm.constants.API_MAX_RETRIES）。
            allow_temperature_fallback: 未使用（他アダプタとのコンストラクタ互換のため保持）。
                DevRelay契約にtemperatureパラメータは無いため常に無視する。
            seat_key: 既定のseatKey（未指定ならbind_seat()または環境変数で後から確定させる）。
            transport: httpx.Client へ渡すtransport（テスト用のhttpx.MockTransport注入）。
        """
        self.model_info = model_info
        self._max_retries = max_retries
        self._allow_temperature_fallback = allow_temperature_fallback
        self._transport = transport
        self._client: Any = None
        self._seat_key: str | None = seat_key
        # timeout_seconds が180秒を超えるとDevRelay契約のtimeoutS上限に違反するため、
        # 実行時ではなく構築時に落とす（本番運用でも起動直後に気付けるようにする）。
        if self.model_info.timeout_seconds > MAX_TIMEOUT_S:
            raise ValueError(
                f"HttpAgentProvider: timeout_seconds={self.model_info.timeout_seconds} "
                f"exceeds DevRelay's timeoutS limit ({MAX_TIMEOUT_S})"
            )
        # model_id検証も構築時に前倒しする（未指定モデルでの本番投入を未然に防ぐ）。
        _devrelay_model_name(self.model_info.model_id)

    def bind_seat(self, seat_key: str) -> None:
        """このプロバイダーインスタンスが使うDevRelay seatKeyを確定する。

        LLMAgent側からプレイヤーID（例: "P05"）を渡す想定
        （llm_agent.LLMAgent.__init__ の bind_seat フック経由）。
        """
        if not SEAT_KEY_RE.match(seat_key):
            raise AdapterError(f"Invalid DevRelay seatKey: {seat_key!r}")
        self._seat_key = seat_key

    def _resolve_seat_key(self) -> str:
        """有効なseatKeyを決定する（bind済み優先→環境変数→既定値）。"""
        seat_key = self._seat_key or os.environ.get("DEVRELAY_SEAT_KEY") or DEFAULT_SEAT_KEY
        if not SEAT_KEY_RE.match(seat_key):
            raise AdapterError(f"Invalid DevRelay seatKey: {seat_key!r}")
        return seat_key

    def _get_settings(self) -> _DevRelaySettings:
        """接続情報を環境変数から遅延読込する（キーをログに出さない）。"""
        base_url = os.environ.get("DEVRELAY_URL")
        token = os.environ.get(self.model_info.env_key)  # 既定 "DEVRELAY_TOKEN"
        target_project_id = os.environ.get(
            "DEVRELAY_TARGET_PROJECT_ID", DEFAULT_TARGET_PROJECT_ID
        )
        if not base_url:
            raise AdapterError("Environment variable DEVRELAY_URL is not set")
        if not token:
            raise AdapterError(
                f"Environment variable {self.model_info.env_key} is not set"
            )
        return _DevRelaySettings(
            base_url=base_url, token=token, target_project_id=target_project_id
        )

    def _get_client(self, http_timeout: float) -> Any:
        """遅延初期化でhttpxクライアントを取得する（テストではtransport注入で差し替え）。"""
        if self._client is None:
            import httpx
            kwargs: dict[str, Any] = {"timeout": http_timeout}
            if self._transport is not None:
                kwargs["transport"] = self._transport
            self._client = httpx.Client(**kwargs)
        return self._client

    def complete(
        self,
        system: str,
        messages: list[dict[str, str]],
        max_tokens: int = 1000,
        temperature: float = 0.7,
        request_options: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """
        DevRelay経由でAPIコールを実行する（429のみリトライ、他は即例外）。

        max_tokens/temperature/request_options はDevRelay契約に存在しないため
        送信しない（引数は他アダプタとのシグネチャ互換のためだけに保持）。

        Returns:
            (レスポンステキスト, usage辞書)
        """
        import httpx

        settings = self._get_settings()
        seat_key = self._resolve_seat_key()
        model_name = _devrelay_model_name(self.model_info.model_id)
        user_prompt = messages[-1]["content"] if messages else ""
        # 匿名化: DevRelay経由でOAuthメールアドレス等の実行環境情報が漏れうるため、
        # 送信直前にsystem末尾へ1行追加する（build_system_prompt()自体は変更しない）。
        system_with_notice = f"{system}\n\n{ANONYMIZATION_LINE}"

        timeout_s = self.model_info.timeout_seconds
        http_timeout = timeout_s + HTTP_TIMEOUT_MARGIN_S
        payload = {
            "targetProjectId": settings.target_project_id,
            "model": model_name,
            "seatKey": seat_key,
            "system": system_with_notice,
            "prompt": user_prompt,
            "timeoutS": timeout_s,
        }
        headers = {
            "Authorization": f"Bearer {settings.token}",
            "Content-Type": "application/json",
        }
        url = f"{settings.base_url.rstrip('/')}/api/agent/raw-completion"

        client = self._get_client(http_timeout)
        from llm.constants import API_MAX_RETRIES
        attempts = API_MAX_RETRIES if self._max_retries is None else self._max_retries

        last_error: Exception | None = None
        for attempt in range(attempts + 1):
            try:
                response = client.post(url, json=payload, headers=headers)
            except httpx.ConnectError as e:
                # 接続エラーのみリトライ対象（read timeoutは既にtimeoutS+30秒待った後なので
                # リトライしても無駄が大きく、契約どおり即座にエラー扱いにする）。
                last_error = e
                if attempt < attempts:
                    time.sleep(RETRY_BASE_SECONDS * (2 ** attempt))
                    continue
                raise AdapterError(
                    f"DevRelay HTTP error ({type(e).__name__}): {str(e)[:200]}"
                ) from None
            except Exception as e:
                raise AdapterError(
                    f"DevRelay HTTP error ({type(e).__name__}): {str(e)[:200]}"
                ) from None

            if response.status_code == 429:
                body = _safe_error_body(response)
                last_error = AdapterError(f"DevRelay rate limited (429): {body}")
                if attempt < attempts:
                    time.sleep(RETRY_BASE_SECONDS * (2 ** attempt))
                    continue
                raise last_error from None

            if response.status_code != 200:
                body = _safe_error_body(response)
                raise AdapterError(
                    f"DevRelay HTTP {response.status_code}: {body}"
                ) from None

            data = response.json()
            stop_reason = data.get("stopReason")
            if stop_reason != "success":
                raise AdapterError(
                    f"DevRelay agent error (stopReason={stop_reason}): "
                    f"{str(data.get('error'))[:200]}"
                ) from None

            return self._build_result(data, seat_key)

        # ここには到達しない想定（各分岐で例外送出/return済み）。安全側フォールバック。
        raise AdapterError(f"DevRelay API error: {str(last_error)[:200]}") from None

    def _build_result(self, data: dict[str, Any], seat_key: str) -> tuple[str, dict[str, Any]]:
        """成功レスポンスをllm_agent互換の (text, usage) へ変換する。"""
        text = data.get("text", "") or ""
        dr_usage = data.get("usage") or {}
        denied_tools = data.get("deniedTools") or []
        response_model = data.get("model")

        if denied_tools:
            logger.warning(
                "DevRelay deniedTools non-empty: seatKey=%s model=%s deniedTools=%s",
                seat_key, response_model, denied_tools,
            )
        logger.info(
            "DevRelay call ok: seatKey=%s model=%s latencyMs=%s sessionId=%s",
            seat_key, response_model, data.get("latencyMs"), data.get("sessionId"),
        )

        input_tokens = dr_usage.get("input", 0) or 0
        output_tokens = dr_usage.get("output", 0) or 0
        usage: dict[str, Any] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cache_read_input_tokens": dr_usage.get("cacheRead", 0) or 0,
            "cache_creation_input_tokens": dr_usage.get("cacheWrite", 0) or 0,
            "reasoning_tokens": 0,
            "finish_reason": data.get("stopReason"),
            "usage_raw": {
                "billing": "subscription",
                "devrelay": {
                    "model": response_model,
                    "usage": dr_usage,
                    "latencyMs": data.get("latencyMs"),
                    "agentDurationMs": data.get("agentDurationMs"),
                    "sessionId": data.get("sessionId"),
                    "stopReason": data.get("stopReason"),
                    "deniedTools": denied_tools,
                    "seatKey": seat_key,
                    "anonymization_appended": True,
                },
            },
            "requested_model": self.model_info.model_id,
            "response_model": response_model if isinstance(response_model, str) and response_model.strip() else None,
        }
        return text, usage


def _safe_error_body(response: Any) -> str:
    """エラー応答本文を安全に短く抽出する（キー情報を含まない前提のDevRelayエラーJSONのみ）。"""
    try:
        data = response.json()
        if isinstance(data, dict):
            return f"{data.get('code')}: {data.get('error')}"[:200]
        return str(data)[:200]
    except Exception:
        return str(getattr(response, "text", ""))[:200]
