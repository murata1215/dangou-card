"""
tools/devrelay_smoke.py — 実DevRelayサーバーへの1コールスモークテスト

サイクル10.7: HttpAgentProvider(llm/providers/devrelay_http.py)が実際のDevRelay
サーバーと疎通できるかを確認する。環境変数（DEVRELAY_URL / DEVRELAY_TOKEN）が
未設定の場合は実行をスキップし、「けいすけが設定後に実行」と明記して終了する
（CI・オフライン環境で失敗させないため）。

使い方:
    uv run python tools/devrelay_smoke.py
    uv run python tools/devrelay_smoke.py --model DR_OPUS48 --seat-key P02
"""

from __future__ import annotations

import argparse
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

DEFAULT_MODEL_KEY = "DR_FABLE"
DEFAULT_SEAT_KEY = "P05"
SYSTEM_PROMPT_TEMPLATE = "あなたは談合カードのプレイヤー {seat_key} である。JSON のみで応答せよ"
USER_PROMPT_TEMPLATE = '{{"question":"はい/いいえで答えよ: あなたは{seat_key}か"}}'


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="DevRelay 実サーバーへの1コールスモークテスト")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL_KEY,
        help="DevRelay モデルのレジストリキー（既定: DR_FABLE）",
    )
    parser.add_argument(
        "--seat-key",
        default=DEFAULT_SEAT_KEY,
        help="DevRelay seatKey（既定: P05）",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not os.environ.get("DEVRELAY_URL") or not os.environ.get("DEVRELAY_TOKEN"):
        print(
            "[SKIP] DEVRELAY_URL / DEVRELAY_TOKEN が未設定のためスモークを実行しません。"
            "けいすけが設定後に実行してください。"
        )
        return 0

    try:
        model_info = get_model(args.model)
    except ValueError as e:
        print(f"[FAIL] モデル指定が不正です: {e}")
        return 2
    if model_info.adapter_type != "devrelay_http":
        print(
            f"[FAIL] --model {args.model!r} は DevRelay 席ではありません "
            f"(adapter_type={model_info.adapter_type!r})"
        )
        return 2

    adapter = create_adapter(model_info)
    bind_seat = getattr(adapter, "bind_seat", None)
    if callable(bind_seat):
        bind_seat(args.seat_key)

    try:
        text, usage = adapter.complete(
            system=SYSTEM_PROMPT_TEMPLATE.format(seat_key=args.seat_key),
            messages=[{"role": "user", "content": USER_PROMPT_TEMPLATE.format(seat_key=args.seat_key)}],
            max_tokens=200,
            temperature=0.7,
        )
    except AdapterError as e:
        print(f"[FAIL] DevRelay呼び出しに失敗しました: {e}")
        return 1

    dr_meta = (usage.get("usage_raw") or {}).get("devrelay", {})
    print("[OK] DevRelay smoke call succeeded")
    print(f"  requested   : {args.model}")
    print(f"  seatKey     : {args.seat_key}")
    print(f"  text        : {text!r}")
    print(f"  model       : {dr_meta.get('model')}")
    print(f"  latencyMs   : {dr_meta.get('latencyMs')}")
    print(f"  sessionId   : {dr_meta.get('sessionId')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
