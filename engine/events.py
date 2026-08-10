"""
イベントログモジュール

全ゲームイベントを時系列で記録し、JSONL形式で出力する。
リプレイ・観戦UIの将来入力となるため、
イベント種別・タイムスタンプ・ラウンド・フェーズ・全状態変化を含む。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from engine.models import GameEvent


class EventLogger:
    """
    ゲームイベントロガー

    全イベントをメモリに保持し、ゲーム終了時にJSONLファイルへ出力する。
    タイムスタンプは自動付与されるが、テスト時は固定値に上書き可能。
    """

    def __init__(self, fixed_timestamp: str | None = None) -> None:
        """
        Args:
            fixed_timestamp: テスト用の固定タイムスタンプ。
                             Noneなら実時刻を使用。
        """
        self._events: list[GameEvent] = []
        self._fixed_timestamp = fixed_timestamp

    def _now(self) -> str:
        """現在時刻をISO8601で返す（テスト時は固定値）"""
        if self._fixed_timestamp:
            return self._fixed_timestamp
        return datetime.now(timezone.utc).isoformat()

    def log(
        self,
        event_type: str,
        round_num: int,
        phase: str,
        data: dict[str, Any] | None = None,
        step: int | None = None,
    ) -> GameEvent:
        """
        イベントを記録する

        Args:
            event_type: イベント種別（例: "GAME_START", "MARKET_RESULT"等）
            round_num: ラウンド番号（0=ゲーム開始前）
            phase: フェイズ名
            data: イベント固有データ
            step: Settlement内のStep番号（1-8）

        Returns:
            記録されたGameEvent
        """
        event = GameEvent(
            event_type=event_type,
            timestamp=self._now(),
            round_num=round_num,
            phase=phase,
            step=step,
            data=data or {},
        )
        self._events.append(event)
        return event

    @property
    def events(self) -> list[GameEvent]:
        """記録された全イベントを返す"""
        return list(self._events)

    def save_jsonl(self, path: str | Path) -> None:
        """
        全イベントをJSONLファイルに書き出す

        各行が1つのGameEventのJSON表現。
        ディレクトリが存在しない場合は自動作成する。

        Args:
            path: 出力先ファイルパス
        """
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for event in self._events:
                f.write(event.model_dump_json() + "\n")

    def clear(self) -> None:
        """イベントログをクリアする"""
        self._events.clear()
