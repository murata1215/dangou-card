"""
モデルID発見スクリプト（Step 0・トークン課金ゼロ）

各社の「モデル一覧」メタデータエンドポイントを叩くだけで、completion（応答生成）を
一切発生させない。呼び出すのは以下の一覧APIのみ:

- Anthropic : GET /v1/models        (anthropic.Anthropic().models.list)
- OpenAI互換: GET {base_url}/models (openai.OpenAI().models.list) ※xAI/Moonshot/DeepSeek/OpenAIで共用
- Google    : GET https://generativelanguage.googleapis.com/v1beta/models?key=... (ネイティブREST)
              ＋ 上記OpenAI互換パスのフォールバック

トークン課金は一切発生しない（レート制限枠のみ消費）。APIキーの値は絶対に出力しない。
キー未設定・認証エラーはクラッシュさせず SKIP/AUTH_FAIL として表示する。

使用方法:
    uv run python scripts/discover_models.py
    uv run python scripts/discover_models.py --vendor Anthropic,Google
    uv run python scripts/discover_models.py --grep opus
    uv run python scripts/discover_models.py --all   # ノイズ除去フィルタを無効化
"""

import argparse
import os
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from llm.models import MODEL_REGISTRY, list_vendor_endpoints, get_model_keys_by_id

# 既定で除外するノイズ（埋め込み・音声・画像系など、対話用ではないモデル）
DEFAULT_NOISE_PATTERN = re.compile(
    r"embed|whisper|tts|dall-e|moderation|image|audio|realtime|search|transcribe",
    re.IGNORECASE,
)


def _current_keys_for_id(model_id: str) -> str:
    keys = get_model_keys_by_id(model_id)
    return ",".join(keys) if keys else ""


def discover_anthropic(env_key: str) -> tuple[str, list[dict[str, Any]]]:
    """GET /v1/models（新しい順で返る＝フラッグシップが先頭）"""
    api_key = os.environ.get(env_key, "")
    if not api_key:
        return "SKIP", []
    try:
        import anthropic
        client = anthropic.Anthropic(api_key=api_key)
        page = client.models.list(limit=1000)
        rows = []
        for m in page.data:
            rows.append({
                "id": m.id,
                "display_name": getattr(m, "display_name", "") or "",
                "created_at": str(getattr(m, "created_at", "") or ""),
            })
        return "OK", rows
    except Exception as e:
        msg = str(e)
        if "401" in msg or "authentic" in msg.lower():
            return "AUTH_FAIL", []
        return f"ERROR:{type(e).__name__}", []


def discover_openai_compat(env_key: str, base_url: str | None) -> tuple[str, list[dict[str, Any]]]:
    """GET {base_url}/models"""
    api_key = os.environ.get(env_key, "")
    if not api_key:
        return "SKIP", []
    try:
        import openai
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = openai.OpenAI(**kwargs)
        page = client.models.list()
        rows = []
        for m in page.data:
            rows.append({
                "id": m.id,
                "created": str(getattr(m, "created", "") or ""),
                "owned_by": getattr(m, "owned_by", "") or "",
            })
        return "OK", rows
    except Exception as e:
        msg = str(e)
        if "401" in msg or "403" in msg or "authentic" in msg.lower():
            return "AUTH_FAIL", []
        return f"ERROR:{type(e).__name__}", []


def discover_google_native(env_key: str) -> tuple[str, list[dict[str, Any]]]:
    """GET https://generativelanguage.googleapis.com/v1beta/models?key=...（ネイティブREST、best-effort）"""
    api_key = os.environ.get(env_key, "")
    if not api_key:
        return "SKIP", []
    try:
        import httpx
        resp = httpx.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key, "pageSize": 200},
            timeout=20.0,
        )
        if resp.status_code in (401, 403):
            return "AUTH_FAIL", []
        resp.raise_for_status()
        data = resp.json()
        rows = []
        for m in data.get("models", []):
            methods = m.get("supportedGenerationMethods", []) or []
            if "generateContent" not in methods:
                continue  # generateContent非対応（embedding等）は除外
            rows.append({
                "id": (m.get("name") or "").removeprefix("models/"),
                "display_name": m.get("displayName", "") or "",
                "input_token_limit": str(m.get("inputTokenLimit", "") or ""),
            })
        return "OK", rows
    except Exception as e:
        return f"ERROR:{type(e).__name__}", []


def filter_noise(rows: list[dict[str, Any]], enabled: bool, grep: str | None) -> list[dict[str, Any]]:
    out = rows
    if enabled:
        out = [r for r in out if not DEFAULT_NOISE_PATTERN.search(r.get("id", ""))]
    if grep:
        pat = re.compile(grep, re.IGNORECASE)
        out = [r for r in out if pat.search(r.get("id", ""))]
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="6社モデルID発見（$0・completion呼び出しなし）")
    parser.add_argument("--vendor", default="", help="カンマ区切りでベンダーを絞る（例: Anthropic,Google）")
    parser.add_argument("--grep", default="", help="model_idの正規表現フィルタ")
    parser.add_argument("--all", action="store_true", help="ノイズ除去フィルタを無効化")
    args = parser.parse_args()

    vendor_filter = {v.strip() for v in args.vendor.split(",") if v.strip()} or None
    endpoints = list_vendor_endpoints()  # [(provider, env_key, base_url, adapter_type), ...]

    print("=== モデルID発見（$0・一覧APIのみ） ===\n")

    for provider, env_key, base_url, adapter_type in endpoints:
        if vendor_filter and provider not in vendor_filter:
            continue

        print(f"## {provider}  (env: {env_key})")
        if provider == "Anthropic":
            status, rows = discover_anthropic(env_key)
        elif provider == "Google":
            status, rows = discover_google_native(env_key)
            if status != "OK":
                # フォールバック: OpenAI互換パス
                status, rows = discover_openai_compat(env_key, base_url)
        else:
            status, rows = discover_openai_compat(env_key, base_url)

        if status == "SKIP":
            print(f"  → SKIP（キー未設定: {env_key}）\n")
            continue
        if status != "OK":
            print(f"  → {status}\n")
            continue

        rows = filter_noise(rows, enabled=not args.all, grep=args.grep or None)
        if not rows:
            print("  （該当モデルなし。--all または --grep で絞り込みを緩めてください）\n")
            continue

        print(f"  | model_id | 現行レジストリキー | 詳細 |")
        print(f"  |---|---|---|")
        for r in rows:
            mid = r.get("id", "")
            current = _current_keys_for_id(mid)
            detail_parts = [f"{k}={v}" for k, v in r.items() if k != "id" and v]
            detail = " / ".join(detail_parts)
            print(f"  | `{mid}` | {current} | {detail} |")
        print()

    print("=== 完了 ===")
    print("H1〜H6のmodel_idと単価は、上記表と各社料金ページを見て人間が確定してください。")
    print("（単価はどのモデル一覧APIからも取得できません。xAIのみ GET /v1/language-models が別途best-effortで試せます）")


if __name__ == "__main__":
    main()
