"""
PixBlog REST API クライアント

投稿・更新(修正投稿)・公開・画像アップロードを提供する薄いラッパ。
標準ライブラリ(urllib)のみで実装し、追加依存を持たない。

API 仕様(pixwriter 経由で確認):
- POST   /api/v1/posts            記事作成(status 既定 draft)
- PATCH  /api/v1/posts/{id}       記事更新 / status を published に変更して公開
- POST   /api/v1/images           画像アップロード(multipart) → URL 取得
- 認証: Authorization: Bearer <token>
- 本文: title + body(+ content_format="markdown") / status / tags / category / featured_media_url / memo

APIキー(PIXBLOG_API_TOKEN)は環境変数から読む。キー文字列はログ・例外に出さない。
"""

from __future__ import annotations

import json
import mimetypes
import os
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

DEFAULT_BASE_URL = "https://pixblog.net/api/v1"
ENV_TOKEN = "PIXBLOG_API_TOKEN"


class PixBlogError(Exception):
    """PixBlog API エラー(キー情報を含まない安全なメッセージのみ)。"""


class PixBlogClient:
    def __init__(
        self,
        token: str | None = None,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 60,
    ) -> None:
        self._token = token or os.environ.get(ENV_TOKEN)
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    # ------------------------------------------------------------------
    # 内部ユーティリティ
    # ------------------------------------------------------------------
    def _require_token(self) -> str:
        if not self._token:
            raise PixBlogError(
                f"環境変数 {ENV_TOKEN} が未設定です(PixBlog APIキーを設定してください)"
            )
        return self._token

    def _request(self, method: str, path: str, payload: dict | None = None) -> dict:
        token = self._require_token()
        url = f"{self.base_url}{path}"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", "application/json")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body) if body else {}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise PixBlogError(f"HTTP {e.code} {method} {path}: {detail}") from None
        except urllib.error.URLError as e:
            raise PixBlogError(f"接続エラー {method} {path}: {e.reason}") from None

    @staticmethod
    def _extract_post_id(res: dict) -> str | None:
        for key in ("id", "post_id", "postId", "slug"):
            if key in res and res[key] is not None:
                return str(res[key])
        data = res.get("data")
        if isinstance(data, dict):
            for key in ("id", "post_id", "postId", "slug"):
                if key in data and data[key] is not None:
                    return str(data[key])
        return None

    # ------------------------------------------------------------------
    # 記事 API
    # ------------------------------------------------------------------
    def create_post(
        self,
        title: str,
        body: str,
        *,
        status: str = "draft",
        tags: list[str] | None = None,
        category: str | None = None,
        featured_media_url: str | None = None,
        memo: str | None = None,
        content_format: str = "markdown",
    ) -> dict:
        """記事を作成する。戻り値に post_id を含む(_extract_post_id で取得可)。"""
        payload: dict[str, Any] = {
            "title": title,
            "body": body,
            "content_format": content_format,
            "status": status,
        }
        if tags:
            payload["tags"] = tags
        if category:
            payload["category"] = category
        if featured_media_url:
            payload["featured_media_url"] = featured_media_url
        if memo:
            payload["memo"] = memo
        res = self._request("POST", "/posts", payload)
        res["_post_id"] = self._extract_post_id(res)
        return res

    def update_post(
        self,
        post_id: str,
        *,
        title: str | None = None,
        body: str | None = None,
        status: str | None = None,
        tags: list[str] | None = None,
        category: str | None = None,
        featured_media_url: str | None = None,
        memo: str | None = None,
        content_format: str = "markdown",
    ) -> dict:
        """既存記事を更新する(修正投稿)。指定したフィールドのみ送る。"""
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
            payload["content_format"] = content_format
        if status is not None:
            payload["status"] = status
        if tags is not None:
            payload["tags"] = tags
        if category is not None:
            payload["category"] = category
        if featured_media_url is not None:
            payload["featured_media_url"] = featured_media_url
        if memo is not None:
            payload["memo"] = memo
        if not payload:
            raise PixBlogError("update_post: 更新するフィールドがありません")
        return self._request("PATCH", f"/posts/{post_id}", payload)

    def publish(self, post_id: str) -> dict:
        """記事を公開(status=published)にする。"""
        return self._request("PATCH", f"/posts/{post_id}", {"status": "published"})

    # ------------------------------------------------------------------
    # 画像 API (multipart/form-data)
    # ------------------------------------------------------------------
    def upload_image(self, image_path: str | Path, field_name: str = "file") -> dict:
        """画像をアップロードし、レスポンス(URLを含む想定)を返す。"""
        token = self._require_token()
        image_path = Path(image_path)
        if not image_path.exists():
            raise PixBlogError(f"画像が見つかりません: {image_path}")

        boundary = f"----pixblog{uuid.uuid4().hex}"
        mime, _ = mimetypes.guess_type(str(image_path))
        mime = mime or "application/octet-stream"

        body = bytearray()
        body += f"--{boundary}\r\n".encode()
        body += (
            f'Content-Disposition: form-data; name="{field_name}"; '
            f'filename="{image_path.name}"\r\n'
        ).encode()
        body += f"Content-Type: {mime}\r\n\r\n".encode()
        body += image_path.read_bytes()
        body += f"\r\n--{boundary}--\r\n".encode()

        url = f"{self.base_url}/images"
        req = urllib.request.Request(url, data=bytes(body), method="POST")
        req.add_header("Authorization", f"Bearer {token}")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                text = resp.read().decode("utf-8")
                return json.loads(text) if text else {}
        except urllib.error.HTTPError as e:
            detail = ""
            try:
                detail = e.read().decode("utf-8", errors="replace")[:300]
            except Exception:
                pass
            raise PixBlogError(f"HTTP {e.code} POST /images: {detail}") from None
        except urllib.error.URLError as e:
            raise PixBlogError(f"接続エラー POST /images: {e.reason}") from None

    @staticmethod
    def extract_image_url(res: dict) -> str | None:
        for key in ("url", "media_url", "featured_media_url", "image_url", "src"):
            if key in res and res[key]:
                return str(res[key])
        data = res.get("data")
        if isinstance(data, dict):
            for key in ("url", "media_url", "image_url", "src"):
                if key in data and data[key]:
                    return str(data[key])
        return None
