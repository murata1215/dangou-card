"""
tools/fake_devrelay.py — DevRelay `POST /api/agent/raw-completion` のフェイクサーバー

サイクル10.7: llm.providers.devrelay_http.HttpAgentProvider の結合テスト用。
DevRelay側の契約どおりの固定JSONを返す（本物のDevRelayサーバーには接続しない）。

契約: doc/devlog（サイクル10.7）参照。
  POST {base_url}/api/agent/raw-completion
  Header: Authorization: Bearer {token}
  Body:   {targetProjectId, model, seatKey, system, prompt, timeoutS, ai}
  Resp:   {text, model, ai, usage, latencyMs, agentDurationMs, stopReason, sessionId, deniedTools}

異常系トリガ（`prompt` 文字列に以下が含まれると挙動を変える。テスト専用の合図であり、
本物のDevRelay契約には存在しない）:
  "__BUSY__"        : 同一seatKeyへの1回目は429 targetBusy、2回目以降は成功
  "__RATE_LIMITED__": 常に429 rateLimited
  "__AGENT_ERROR__" : 200 OK・stopReason="error"・error文言あり
  "__DENIED__"      : 200 OK・deniedTools=["Bash"]

単体テストからは `serve_in_thread()` で起動し、`python tools/fake_devrelay.py --port 8765`
でスタンドアロン起動もできる（curlでの手動疎通確認用）。
"""

from __future__ import annotations

import argparse
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

DEFAULT_TOKEN = "test-token"
RAW_COMPLETION_PATH = "/api/agent/raw-completion"


def _make_handler(token: str, busy_seen: dict[str, int], lock: threading.Lock) -> type[BaseHTTPRequestHandler]:
    """契約どおりのレスポンスを返すHTTPハンドラクラスを生成する（tokenと状態をクロージャで共有）。"""

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def _send_json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802 (BaseHTTPRequestHandlerの命名規約に合わせる)
            if self.path != RAW_COMPLETION_PATH:
                self._send_json(404, {"error": "not found", "code": "notFound"})
                return

            auth = self.headers.get("Authorization", "")
            if auth != f"Bearer {token}":
                self._send_json(403, {"error": "invalid token", "code": "notAllowed"})
                return

            length = int(self.headers.get("Content-Length", "0"))
            raw_body = self.rfile.read(length) if length else b"{}"
            try:
                req = json.loads(raw_body.decode("utf-8"))
            except Exception:
                self._send_json(400, {"error": "invalid json", "code": "aiUnavailable"})
                return

            prompt = req.get("prompt", "") or ""
            seat_key = req.get("seatKey", "") or ""
            model = req.get("model", "unknown-model")
            ai = req.get("ai", "claude")

            if "__RATE_LIMITED__" in prompt:
                self._send_json(429, {"error": "rate limited", "code": "rateLimited"})
                return

            if "__BUSY__" in prompt:
                with lock:
                    seen = busy_seen.get(seat_key, 0)
                    busy_seen[seat_key] = seen + 1
                if seen == 0:
                    self._send_json(429, {"error": "seat busy", "code": "targetBusy"})
                    return

            if "__AGENT_ERROR__" in prompt:
                self._send_json(200, {
                    "text": "", "model": model,
                    "usage": {"input": 10, "output": 0, "cacheRead": 0, "cacheWrite": 0},
                    "latencyMs": 100, "agentDurationMs": 50,
                    "stopReason": "error", "sessionId": "raw_fake_error",
                    "deniedTools": [], "error": "simulated agent error",
                })
                return

            denied_tools = ["Bash"] if "__DENIED__" in prompt else []
            self._send_json(200, {
                "text": '{"ok": true}',
                "model": model,
                "ai": ai,
                "usage": {"input": 42, "output": 8, "cacheRead": 0, "cacheWrite": 0},
                "latencyMs": 3500, "agentDurationMs": 3200,
                "stopReason": "success", "sessionId": "raw_fake_session",
                "deniedTools": denied_tools,
            })

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass  # テスト出力を汚さない（標準の access log を抑止）

    return Handler


def serve_in_thread(token: str = DEFAULT_TOKEN) -> tuple[ThreadingHTTPServer, str]:
    """フェイクサーバーを別スレッドで起動し、(server, base_url) を返す。

    呼び出し側は使い終わったら `server.shutdown()` すること。
    """
    busy_seen: dict[str, int] = {}
    lock = threading.Lock()
    handler_cls = _make_handler(token, busy_seen, lock)
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    return server, f"http://127.0.0.1:{port}"


def main() -> None:
    parser = argparse.ArgumentParser(description="DevRelay fake server (手動疎通確認用)")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--token", default=DEFAULT_TOKEN)
    args = parser.parse_args()

    busy_seen: dict[str, int] = {}
    lock = threading.Lock()
    handler_cls = _make_handler(args.token, busy_seen, lock)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler_cls)
    print(f"fake_devrelay listening on http://127.0.0.1:{args.port} (token={args.token})")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.shutdown()


if __name__ == "__main__":
    main()
