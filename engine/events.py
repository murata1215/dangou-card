"""
イベントログモジュール

全ゲームイベントを時系列で記録し、JSONL形式で出力する。
リプレイ・観戦UIの将来入力となるため、
イベント種別・タイムスタンプ・ラウンド・フェーズ・全状態変化を含む。

逐次追記モード: output_path 指定時は log() 呼び出しごとに即時追記+flush。
進行中ゲームでもビューワーがラウンド状況を表示できるようになる。
output_path 未指定時は従来通りメモリ保持のみ（シミュレーション経路に副作用なし）。
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
    output_path 指定時は LLMLogger と同パターンで逐次追記+flush も行う。
    タイムスタンプは自動付与されるが、テスト時は固定値に上書き可能。
    """

    def __init__(
        self,
        fixed_timestamp: str | None = None,
        output_path: str | Path | None = None,
    ) -> None:
        """
        Args:
            fixed_timestamp: テスト用の固定タイムスタンプ。
                             Noneなら実時刻を使用。
            output_path: 逐次追記先のファイルパス。
                         指定時は log() ごとに即時追記+flush。
                         Noneなら従来通りメモリ保持のみ（ディスク書き込みしない）。
        """
        self._events: list[GameEvent] = []
        self._fixed_timestamp = fixed_timestamp
        # 逐次書き込み: output_path 指定時のみ有効
        # シミュレーション経路（simulate.py等）では output_path=None で
        # 生成されるため、余分なファイルI/Oは発生しない。
        self._output_path = Path(output_path) if output_path else None
        self._file = None
        if self._output_path:
            self._output_path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self._output_path, "a", encoding="utf-8")

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

        メモリに保持し、逐次追記モード時はファイルにも即時書き込む。

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

        # 逐次書き込み: ファイルハンドルが開いていれば即時追記+flush
        if self._file:
            try:
                self._file.write(event.model_dump_json() + "\n")
                self._file.flush()
            except Exception:
                pass  # 書き込みエラーでもゲーム続行

        return event

    @property
    def events(self) -> list[GameEvent]:
        """記録された全イベントを返す"""
        return list(self._events)

    def save_jsonl(self, path: str | Path) -> None:
        """
        全イベントをJSONLファイルに書き出す

        逐次追記済みで同じパスなら flush のみ（二重書き込み回避）。
        別パスまたは逐次追記未使用なら従来通り全書き出し。

        Args:
            path: 出力先ファイルパス
        """
        path = Path(path)
        # 逐次書き込み済みで同じパスなら二重書き込みを回避
        if self._output_path and path.resolve() == self._output_path.resolve():
            # 既に逐次書き込み済み — flush のみ
            if self._file:
                try:
                    self._file.flush()
                except Exception:
                    pass
            return
        # 別パスまたは逐次書き込み未使用: 従来通り全書き出し
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            for event in self._events:
                f.write(event.model_dump_json() + "\n")

    def close(self) -> None:
        """ファイルハンドルを閉じる（LLMLogger.close() と同パターン）"""
        if self._file:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None

    def clear(self) -> None:
        """イベントログをクリアする"""
        self._events.clear()
