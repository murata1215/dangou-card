"""
LLMコールロガー

全プロンプト・全生レスポンスをJSONLで保存する。
トークン数・所要時間・コスト概算・キャッシュ情報も記録。

P0修正: 逐次書き込み化。各コール完了時点でJSONLに追記し、
クラッシュしても直前までのログが必ず残る。
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


class LLMLogger:
    """
    LLMコールのJSONLロガー（逐次書き込み方式）

    各log_call()でファイルに即時追記+flushする。
    試合終了前にクラッシュしてもログが残る。
    """

    def __init__(self, output_dir: str | Path, game_id: str = "game") -> None:
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.game_id = game_id
        self._entries: list[dict[str, Any]] = []
        self._total_cost: float = 0.0
        # 逐次書き込み用ファイルハンドル
        self._file_path = self.output_dir / f"{self.game_id}_llm_calls.jsonl"
        self._file = open(self._file_path, "a", encoding="utf-8")

    def log_call(
        self,
        player_id: str,
        model_id: str,
        phase: str,
        round_num: int,
        turn: int | None,
        system_prompt: str,
        user_prompt: str,
        response_text: str,
        usage: dict[str, int],
        cost: float,
        elapsed_ms: float,
        retry_count: int = 0,
        error: str | None = None,
        error_type: str | None = None,
        emotion: str | None = None,
        finish_reason: str | None = None,
    ) -> None:
        """
        1回のLLMコールを記録し、即時にファイルへ追記する
        """
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "game_id": self.game_id,
            "player_id": player_id,
            "model_id": model_id,
            "phase": phase,
            "round_num": round_num,
            "turn": turn,
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response_text": response_text,
            "input_tokens": usage.get("input_tokens", 0),
            "output_tokens": usage.get("output_tokens", 0),
            "cache_creation_input_tokens": usage.get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": usage.get("cache_read_input_tokens", 0),
            "cost_usd": cost,
            "elapsed_ms": elapsed_ms,
            "retry_count": retry_count,
            "error": error,
            "error_type": error_type,
            "emotion": emotion,
            "reasoning": None,  # CoT: _update_last_log_emotion で後付け
            "finish_reason": finish_reason,
        }
        self._entries.append(entry)
        self._total_cost += cost

        # 逐次書き込み: 即時にファイルへ追記+flush
        try:
            self._file.write(json.dumps(entry, ensure_ascii=False) + "\n")
            self._file.flush()
        except Exception:
            pass  # ファイル書き込みエラーでも試合は続行

    @property
    def total_cost(self) -> float:
        return self._total_cost

    @property
    def total_calls(self) -> int:
        return len(self._entries)

    @property
    def total_input_tokens(self) -> int:
        return sum(e.get("input_tokens", 0) for e in self._entries)

    @property
    def total_output_tokens(self) -> int:
        return sum(e.get("output_tokens", 0) for e in self._entries)

    @property
    def total_cache_read_tokens(self) -> int:
        return sum(e.get("cache_read_input_tokens", 0) for e in self._entries)

    @property
    def entries(self) -> list[dict[str, Any]]:
        return list(self._entries)

    def save(self) -> Path:
        """
        in-memoryエントリでファイルを全書き直しする。

        逐次書き込み中は emotion/reasoning が後付け更新されるため、
        試合完了後にこのメソッドを呼ぶことでファイルに最終値が反映される。
        """
        try:
            self._file.close()
            with open(self._file_path, "w", encoding="utf-8") as f:
                for entry in self._entries:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            # 再オープン（以降の追記に備える）
            self._file = open(self._file_path, "a", encoding="utf-8")
        except Exception:
            pass
        return self._file_path

    def close(self) -> None:
        """ファイルハンドルを閉じる"""
        try:
            self._file.close()
        except Exception:
            pass
