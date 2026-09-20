"""
tools/devrelay_smoke.py — 実DevRelayサーバーへの1コールスモークテスト

サイクル10.7: HttpAgentProvider(llm/providers/devrelay_http.py)が実際のDevRelay
サーバーと疎通できるかを確認する。環境変数（DEVRELAY_URL / DEVRELAY_TOKEN）が
未設定の場合は実行をスキップし、「けいすけが設定後に実行」と明記して終了する
（CI・オフライン環境で失敗させないため）。

使い方:
    uv run python tools/devrelay_smoke.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# scripts/model_smoke.py 等と同じパターン: `python tools/devrelay_smoke.py` 直接実行時に
# リポジトリルートをsys.pathへ通す（`llm` パッケージをimportできるようにする）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

import os  # noqa: E402 (load_dotenv後にos.environを読むため意図的に後置)

from llm.adapters import AdapterError, create_adapter  # noqa: E402
from llm.models import get_model  # noqa: E402

SEAT_KEY = "P05"
SYSTEM_PROMPT = "あなたは談合カードのプレイヤー P05 である。JSON のみで応答せよ"
USER_PROMPT = '{"question":"はい/いいえで答えよ: あなたはP05か"}'


def main() -> int:
    if not os.environ.get("DEVRELAY_URL") or not os.environ.get("DEVRELAY_TOKEN"):
        print(
            "[SKIP] DEVRELAY_URL / DEVRELAY_TOKEN が未設定のためスモークを実行しません。"
            "けいすけが設定後に実行してください。"
        )
        return 0

    model_info = get_model("DR_FABLE")
    adapter = create_adapter(model_info)
    bind_seat = getattr(adapter, "bind_seat", None)
    if callable(bind_seat):
        bind_seat(SEAT_KEY)

    try:
        text, usage = adapter.complete(
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": USER_PROMPT}],
            max_tokens=200,
            temperature=0.7,
        )
    except AdapterError as e:
        print(f"[FAIL] DevRelay呼び出しに失敗しました: {e}")
        return 1

    dr_meta = (usage.get("usage_raw") or {}).get("devrelay", {})
    print("[OK] DevRelay smoke call succeeded")
    print(f"  text        : {text!r}")
    print(f"  model       : {dr_meta.get('model')}")
    print(f"  latencyMs   : {dr_meta.get('latencyMs')}")
    print(f"  sessionId   : {dr_meta.get('sessionId')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
