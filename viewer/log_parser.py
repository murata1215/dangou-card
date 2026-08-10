"""
ログパーサ — JSONL→構造化データ、差分キャッシュ付き

既存のLLMコールログ・エンジンイベントログ・座席マップを読み取り、
ビューア用の構造化データに変換する。読み取り専用。

差分キャッシュ: mtime+sizeで変更検知、変更なしなら即返し。
進行中ファイルの書きかけ行はスキップ。
"""

import json
import re
import time
from pathlib import Path
from typing import Any


class LogCache:
    """ファイル差分キャッシュ（mtime+sizeベース）"""

    def __init__(self) -> None:
        # path文字列 → (mtime, size, parsed_entries)
        self._cache: dict[str, tuple[float, int, list[dict]]] = {}

    def get_entries(self, path: Path) -> list[dict[str, Any]]:
        """
        JSONLファイルをパースしてエントリリストを返す。
        ファイルが変更されていなければキャッシュを返す。
        書きかけ行はスキップ。
        """
        key = str(path)
        try:
            stat = path.stat()
        except (FileNotFoundError, OSError):
            return []

        mtime, size = stat.st_mtime, stat.st_size
        if key in self._cache:
            cached_mtime, cached_size, cached_entries = self._cache[key]
            if mtime == cached_mtime and size == cached_size:
                return cached_entries

        # 全行パース（追記対応は将来の最適化）
        entries: list[dict[str, Any]] = []
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass  # 書きかけ行をスキップ
        except (FileNotFoundError, OSError):
            pass

        self._cache[key] = (mtime, size, entries)
        return entries


# グローバルキャッシュインスタンス
_cache = LogCache()


def list_games(logs_dir: Path) -> list[dict[str, Any]]:
    """
    logs/llm/ 配下のtrial_*ディレクトリからゲーム一覧を返す。
    新しい順にソート。
    """
    games: list[dict[str, Any]] = []
    if not logs_dir.exists():
        return games

    for trial_dir in sorted(logs_dir.iterdir(), reverse=True):
        if not trial_dir.is_dir() or not trial_dir.name.startswith("trial_"):
            continue

        llm_logs_dir = trial_dir / "llm_logs"
        if not llm_logs_dir.exists():
            continue

        # game単位でグループ化（game01_P01_llm_calls.jsonl → game01）
        game_ids: set[str] = set()
        for f in llm_logs_dir.glob("*_llm_calls.jsonl"):
            parts = f.stem.split("_")
            if parts:
                game_ids.add(parts[0])

        for game_id in sorted(game_ids):
            # LLMログファイル数
            llm_files = list(llm_logs_dir.glob(f"{game_id}_*_llm_calls.jsonl"))
            total_entries = 0
            latest_ts = ""
            for lf in llm_files:
                entries = _cache.get_entries(lf)
                total_entries += len(entries)
                for e in entries[-1:]:
                    ts = e.get("timestamp", "")
                    if ts > latest_ts:
                        latest_ts = ts

            # イベントログ・座席マップの存在チェック
            has_events = (trial_dir / f"{game_id}_events.jsonl").exists()
            has_seat_map = (trial_dir / f"{game_id}_seat_map.json").exists()

            games.append({
                "trial_dir": trial_dir.name,
                "game_id": game_id,
                "players": len(llm_files),
                "has_events": has_events,
                "has_seat_map": has_seat_map,
                "llm_log_count": len(llm_files),
                "total_entries": total_entries,
                "latest_timestamp": latest_ts,
            })

    return games


def _load_seat_map(trial_dir: Path, game_id: str) -> dict[str, str]:
    """座席⇔モデル対応表を読む"""
    seat_map_path = trial_dir / f"{game_id}_seat_map.json"
    if seat_map_path.exists():
        try:
            return json.loads(seat_map_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _extract_strategy(response_text: str) -> dict[str, Any] | None:
    """response_textからstrategy辞書を抽出する"""
    if not response_text:
        return None
    try:
        # JSONを抽出（コードブロック対応）
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response_text, re.DOTALL)
        if m:
            data = json.loads(m.group(1).strip())
        else:
            # 生JSON
            start = response_text.find("{")
            if start >= 0:
                depth = 0
                for i in range(start, len(response_text)):
                    if response_text[i] == "{":
                        depth += 1
                    elif response_text[i] == "}":
                        depth -= 1
                        if depth == 0:
                            data = json.loads(response_text[start:i + 1])
                            break
                else:
                    return None
            else:
                return None
        return data.get("strategy") if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def _extract_action(response_text: str) -> dict[str, Any] | None:
    """response_textからaction辞書を抽出する"""
    if not response_text:
        return None
    try:
        m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', response_text, re.DOTALL)
        if m:
            data = json.loads(m.group(1).strip())
        else:
            start = response_text.find("{")
            if start >= 0:
                depth = 0
                for i in range(start, len(response_text)):
                    if response_text[i] == "{":
                        depth += 1
                    elif response_text[i] == "}":
                        depth -= 1
                        if depth == 0:
                            data = json.loads(response_text[start:i + 1])
                            break
                else:
                    return None
            else:
                return None
        return data.get("action") if isinstance(data, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


def get_game_state(logs_dir: Path, trial_dir_name: str, game_id: str) -> dict[str, Any]:
    """
    パネル表示用の全体状態サマリを返す。
    """
    trial_dir = logs_dir / trial_dir_name
    llm_logs_dir = trial_dir / "llm_logs"
    seat_map = _load_seat_map(trial_dir, game_id)

    players: list[dict[str, Any]] = []
    total_cost = 0.0
    max_round = 0
    all_messages: list[dict] = []
    now = time.time()

    for lf in sorted(llm_logs_dir.glob(f"{game_id}_*_llm_calls.jsonl")):
        pid = lf.stem.split("_")[1] if "_" in lf.stem else "?"
        entries = _cache.get_entries(lf)

        calls = len(entries)
        valid_json = 0
        errors = 0
        cost = 0.0
        dm_count = 0
        bc_count = 0
        latest_round = 0
        latest_phase = ""
        latest_ts = ""
        strategy_summary = None

        for e in entries:
            cost += e.get("cost_usd", 0)
            rnd = e.get("round_num", 0)
            if rnd > latest_round:
                latest_round = rnd
            latest_phase = e.get("phase", "")
            latest_ts = e.get("timestamp", "")

            if e.get("error"):
                errors += 1

            resp = e.get("response_text", "")
            if resp:
                s = _extract_strategy(resp)
                if s is not None:
                    valid_json += 1
                    strategy_summary = s
                a = _extract_action(resp)
                if a:
                    at = a.get("type", "")
                    if at == "dm":
                        dm_count += 1
                    elif at == "broadcast":
                        bc_count += 1
                        # メッセージ収集
                        all_messages.append({
                            "sender": pid,
                            "type": "broadcast",
                            "message": a.get("message", "")[:100],
                            "round": rnd,
                        })

        if latest_round > max_round:
            max_round = latest_round

        total_cost += cost
        model_id = entries[0].get("model_id", "") if entries else ""
        model_label = seat_map.get(pid, model_id)

        # last_active計算
        last_active_sec = None
        if latest_ts:
            try:
                from datetime import datetime, timezone
                ts = datetime.fromisoformat(latest_ts.replace("Z", "+00:00"))
                last_active_sec = int(now - ts.timestamp())
            except Exception:
                pass

        # emotion抽出: 最新のemotionと直近5件の履歴
        latest_emotion = "平静"
        emotion_history: list[str] = []
        for e in entries:
            em = _extract_emotion(e)
            emotion_history.append(em)
            latest_emotion = em
        emotion_history = emotion_history[-5:]  # 直近5件

        players.append({
            "player_id": pid,
            "model_id": model_id,
            "model_name": model_label,
            "status": "alive",
            "latest_phase": latest_phase,
            "latest_round": latest_round,
            "strategy_summary": _truncate_strategy(strategy_summary),
            "emotion": latest_emotion,
            "emotion_history": emotion_history,
            "stats": {
                "calls": calls,
                "valid_json": valid_json,
                "errors": errors,
                "cost": round(cost, 4),
                "dm_count": dm_count,
                "broadcast_count": bc_count,
                "last_active_sec_ago": last_active_sec,
            },
        })

    # イベントログから脱落情報を取得
    events_file = trial_dir / f"{game_id}_events.jsonl"
    eliminated: list[str] = []
    if events_file.exists():
        events = _cache.get_entries(events_file)
        for e in events:
            if e.get("event_type") in ("ELIMINATION", "BANKRUPTCY", "FORCED_LIQUIDATION"):
                pid = e.get("data", {}).get("player_id", "")
                if pid and pid not in eliminated:
                    eliminated.append(pid)
            if e.get("event_type") == "SURVIVAL_CHECK":
                data = e.get("data", {})
                if data.get("result") == "eliminated":
                    pid = data.get("player_id", "")
                    if pid and pid not in eliminated:
                        eliminated.append(pid)

        # プレイヤーの状態を更新
        for p in players:
            if p["player_id"] in eliminated:
                p["status"] = "eliminated"

    return {
        "current_round": max_round,
        "total_rounds": 12,
        "players": players,
        "recent_messages": all_messages[-5:],
        "total_cost": round(total_cost, 4),
        "eliminated": eliminated,
        "survivors": len(players) - len(eliminated),
    }


def _truncate_strategy(s: dict | None) -> dict | None:
    """strategy辞書を表示用に短縮する"""
    if not s or not isinstance(s, dict):
        return None
    result = {}
    for k in ("target_market", "current_goal", "card_plan", "reason", "emotion"):
        if k in s:
            val = str(s[k])[:80]
            result[k] = val
    return result if result else None


def _extract_emotion(entry: dict[str, Any]) -> str:
    """ログエントリからemotionを抽出する。なければ平静を返す"""
    # まずログエントリのemotionフィールドを確認
    em = entry.get("emotion")
    if em and em in {"喜", "怒", "哀", "楽", "焦", "疑"}:
        return em
    # strategyから抽出
    resp = entry.get("response_text", "")
    s = _extract_strategy(resp)
    if s and isinstance(s, dict):
        em = s.get("emotion", "")
        if em in {"喜", "怒", "哀", "楽", "焦", "疑"}:
            return em
    return "平静"


def get_player_timeline(
    logs_dir: Path, trial_dir_name: str, game_id: str, pid: str
) -> list[dict[str, Any]]:
    """プレイヤーの時系列を返す"""
    trial_dir = logs_dir / trial_dir_name
    llm_logs_dir = trial_dir / "llm_logs"
    lf = llm_logs_dir / f"{game_id}_{pid}_llm_calls.jsonl"

    entries = _cache.get_entries(lf)
    timeline: list[dict[str, Any]] = []

    for e in entries:
        resp = e.get("response_text", "")
        strategy = _extract_strategy(resp)
        action = _extract_action(resp)

        item: dict[str, Any] = {
            "timestamp": e.get("timestamp", ""),
            "round": e.get("round_num", 0),
            "turn": e.get("turn"),
            "phase": e.get("phase", ""),
            "cost": e.get("cost_usd", 0),
            "latency_ms": e.get("elapsed_ms", 0),
            "error": e.get("error"),
            "error_type": e.get("error_type"),
            "input_tokens": e.get("input_tokens", 0),
            "output_tokens": e.get("output_tokens", 0),
            "emotion": _extract_emotion(e),
        }

        if action:
            item["type"] = "action"
            item["action_type"] = action.get("type", "?")
            if action.get("type") in ("dm", "broadcast"):
                item["content"] = action.get("message", "")[:200]
                if action.get("type") == "dm":
                    item["to"] = action.get("to", "")
            elif action.get("type") == "market_commit":
                item["content"] = f"{action.get('market_id','?')} / {action.get('card','?')}"
        elif strategy:
            item["type"] = "strategy"
        else:
            item["type"] = "call"

        if strategy:
            item["strategy"] = _truncate_strategy(strategy)

        # reasoning（折りたたみ用）：response_textの先頭200文字
        if resp and len(resp) > 50:
            item["reasoning_preview"] = resp[:200]

        timeline.append(item)

    return timeline
