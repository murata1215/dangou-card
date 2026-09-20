"""
tools/devrelay_smoke.py — 実DevRelayサーバーへのスモークテスト（単発 / 複数席同時）

サイクル10.7: HttpAgentProvider(llm/providers/devrelay_http.py)が実際のDevRelay
サーバーと疎通できるかを確認する。環境変数（DEVRELAY_URL / DEVRELAY_TOKEN）が
未設定の場合は実行をスキップし、「けいすけが設定後に実行」と明記して終了する
（CI・オフライン環境で失敗させないため）。

サイクル10.9: DevRelay raw-completionが `ai: "codex"` に対応したため、送信した
`ai`（ModelInfo.devrelay_ai）とレスポンスの `ai` を表示する。また `--models`/
`--seat-keys` で複数席を別seatKeyで同時に1コールずつ叩く結合確認を追加した。

使い方:
    uv run python tools/devrelay_smoke.py
    uv run python tools/devrelay_smoke.py --model DR_OPUS48 --seat-key P02
    uv run python tools/devrelay_smoke.py --model DR_TERRA --seat-key P04
    uv run python tools/devrelay_smoke.py \
        --models DR_FABLE,DR_OPUS48,DR_SONNET5,DR_TERRA,DR_SOL \
        --seat-keys P01,P02,P03,P04,P05
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor
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
    parser = argparse.ArgumentParser(description="DevRelay 実サーバーへのスモークテスト")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL_KEY,
        help="DevRelay モデルのレジストリキー（既定: DR_FABLE）。--models指定時は無視される",
    )
    parser.add_argument(
        "--seat-key",
        default=DEFAULT_SEAT_KEY,
        help="DevRelay seatKey（既定: P05）。--seat-keys指定時は無視される",
    )
    parser.add_argument(
        "--models",
        default=None,
        help="カンマ区切りのモデルキー列。指定時は--seat-keysと同数の席を同時に1コールずつ叩く",
    )
    parser.add_argument(
        "--seat-keys",
        default=None,
        help="カンマ区切りのseatKey列。--modelsと同数である必要がある",
    )
    return parser.parse_args()


def _resolve_devrelay_model(model_key: str) -> tuple:
    """モデルキーを検証し (ModelInfo, エラーメッセージ or None) を返す。"""
    try:
        model_info = get_model(model_key)
    except ValueError as e:
        return None, f"モデル指定が不正です: {e}"
    if model_info.adapter_type != "devrelay_http":
        return None, (
            f"--model {model_key!r} は DevRelay 席ではありません "
            f"(adapter_type={model_info.adapter_type!r})"
        )
    return model_info, None


def _call_seat(model_key: str, seat_key: str) -> dict:
    """1席分のスモーク呼び出しを実行し、結果を辞書で返す（例外は握って呼び出し元へ返す）。"""
    result: dict = {"model_key": model_key, "seat_key": seat_key}

    model_info, err = _resolve_devrelay_model(model_key)
    if err is not None:
        result["ok"] = False
        result["error"] = err
        result["error_kind"] = "config"
        return result

    result["ai_sent"] = model_info.devrelay_ai
    adapter = create_adapter(model_info)
    bind_seat = getattr(adapter, "bind_seat", None)
    if callable(bind_seat):
        bind_seat(seat_key)

    try:
        text, usage = adapter.complete(
            system=SYSTEM_PROMPT_TEMPLATE.format(seat_key=seat_key),
            messages=[{"role": "user", "content": USER_PROMPT_TEMPLATE.format(seat_key=seat_key)}],
            max_tokens=200,
            temperature=0.7,
        )
    except AdapterError as e:
        result["ok"] = False
        result["error"] = f"DevRelay呼び出しに失敗しました: {e}"
        result["error_kind"] = "adapter"
        return result

    dr_meta = (usage.get("usage_raw") or {}).get("devrelay", {})
    result.update(
        ok=True,
        text=text,
        model=dr_meta.get("model"),
        ai_resp=dr_meta.get("ai"),
        latencyMs=dr_meta.get("latencyMs"),
        sessionId=dr_meta.get("sessionId"),
    )
    return result


def _print_single(result: dict) -> int:
    if not result.get("ok"):
        print(f"[FAIL] {result['error']}")
        return 2 if result.get("error_kind") == "config" else 1

    print("[OK] DevRelay smoke call succeeded")
    print(f"  requested   : {result['model_key']}")
    print(f"  seatKey     : {result['seat_key']}")
    print(f"  text        : {result['text']!r}")
    print(f"  model       : {result['model']}")
    print(f"  ai (sent)   : {result['ai_sent']}")
    print(f"  ai (resp)   : {result['ai_resp']}")
    print(f"  latencyMs   : {result['latencyMs']}")
    print(f"  sessionId   : {result['sessionId']}")
    return 0


def _run_concurrent(model_keys: list[str], seat_keys: list[str]) -> int:
    if len(model_keys) != len(seat_keys):
        print(
            f"[FAIL] --models({len(model_keys)}件) と --seat-keys({len(seat_keys)}件) の"
            "個数が一致しません"
        )
        return 2

    with ThreadPoolExecutor(max_workers=len(model_keys)) as executor:
        results = list(executor.map(lambda pair: _call_seat(*pair), zip(model_keys, seat_keys)))

    header = f"| {'key':<12} | {'seatKey':<8} | {'ai(sent)':<8} | {'ai(resp)':<8} | {'model':<28} | {'latencyMs':<9} | status |"
    print(header)
    print("|" + "-" * (len(header) - 2) + "|")
    all_ok = True
    for r in results:
        if r.get("ok"):
            print(
                f"| {r['model_key']:<12} | {r['seat_key']:<8} | {str(r.get('ai_sent')):<8} | "
                f"{str(r.get('ai_resp')):<8} | {str(r.get('model')):<28} | "
                f"{str(r.get('latencyMs')):<9} | 200 OK |"
            )
        else:
            all_ok = False
            print(
                f"| {r['model_key']:<12} | {r['seat_key']:<8} | {'-':<8} | {'-':<8} | "
                f"{'-':<28} | {'-':<9} | FAIL: {r.get('error')} |"
            )

    return 0 if all_ok else 1


def main() -> int:
    args = parse_args()
    if not os.environ.get("DEVRELAY_URL") or not os.environ.get("DEVRELAY_TOKEN"):
        print(
            "[SKIP] DEVRELAY_URL / DEVRELAY_TOKEN が未設定のためスモークを実行しません。"
            "けいすけが設定後に実行してください。"
        )
        return 0

    if args.models is not None or args.seat_keys is not None:
        if args.models is None or args.seat_keys is None:
            print("[FAIL] --models と --seat-keys は両方セットで指定してください")
            return 2
        model_keys = [k.strip() for k in args.models.split(",") if k.strip()]
        seat_keys = [k.strip() for k in args.seat_keys.split(",") if k.strip()]
        return _run_concurrent(model_keys, seat_keys)

    result = _call_seat(args.model, args.seat_key)
    return _print_single(result)


if __name__ == "__main__":
    sys.exit(main())
