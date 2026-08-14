"""
参加者ロスターの構築

GameTrace の seat_map から、オープニング記事・ロスター画像で使う参加者一覧を作る。
各エントリはネタバレ(生死・順位)を含まない静的な紹介情報のみ。

- build_roster(trace): [{pid, model_key, name, provider, vendor}] を席順で返す
"""

from __future__ import annotations

from engine.commentary.trace import GameTrace
from llm.models import get_model

# provider(表示名) → emotion画像のベンダーキー
_VENDOR_KEYS = {"anthropic", "openai", "google", "xai", "moonshot", "deepseek"}


def _model_key(name: str) -> str:
    """座席表の表示名 'M1:Claude Sonnet 5' からモデルキー 'M1' を取り出す。"""
    if ":" in name:
        return name.split(":", 1)[0].strip()
    return name.strip()


def _display_name(name: str) -> str:
    """'M1:Claude Sonnet 5' から表示名 'Claude Sonnet 5' を取り出す。"""
    if ":" in name:
        return name.split(":", 1)[1].strip()
    return name.strip()


def _vendor(provider: str) -> str:
    """provider 表示名を emotion 画像のベンダーキーへ正規化する。"""
    v = (provider or "").strip().lower()
    return v if v in _VENDOR_KEYS else "anthropic"


def build_roster(trace: GameTrace) -> list[dict]:
    """seat_map から参加者一覧(席順)を構築する。"""
    roster: list[dict] = []
    for pid, raw in sorted(trace.seat_map.items()):
        model_key = _model_key(raw)
        name = _display_name(raw)
        provider = ""
        try:
            info = get_model(model_key)
            provider = info.provider
            if info.name:
                name = info.name
        except (KeyError, ValueError):
            provider = ""
        roster.append({
            "pid": pid,
            "model_key": model_key,
            "name": name,
            "provider": provider,
            "vendor": _vendor(provider),
        })
    return roster
