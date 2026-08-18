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
        input_tokens_total = 0
        output_tokens_total = 0
        cache_read_total = 0
        total_tokens_total = 0
        latest_round = 0
        latest_phase = ""
        latest_ts = ""
        strategy_summary = None
        last_message = None

        for e in entries:
            cost += e.get("cost_usd", 0)
            input_tokens_total += e.get("input_tokens", 0) or 0
            output_tokens_total += e.get("output_tokens", 0) or 0
            cache_read_total += e.get("cache_read_input_tokens", 0) or 0
            _tt = e.get("total_tokens", 0) or 0
            total_tokens_total += _tt if _tt > 0 else ((e.get("input_tokens", 0) or 0) + (e.get("output_tokens", 0) or 0))
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
                        msg = a.get("message", "")
                        last_message = msg  # 最後のbroadcastを最終発言として保持
                        # メッセージ収集
                        all_messages.append({
                            "sender": pid,
                            "type": "broadcast",
                            "message": msg[:100],
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
            "cash": None,  # 後段のeventsループでSNAPSHOTから埋める
            "debt": None,  # 後段のeventsループでSNAPSHOTから埋める
            "initial_loan": None,  # LOAN_CHOSENイベントから取得
            "last_message": (last_message[:120] if last_message else None),
            "stats": {
                "calls": calls,
                "valid_json": valid_json,
                "errors": errors,
                "cost": round(cost, 4),
                "dm_count": dm_count,
                "broadcast_count": bc_count,
                "last_active_sec_ago": last_active_sec,
                "input_tokens": input_tokens_total,
                "output_tokens": output_tokens_total,
                "cache_read_tokens": cache_read_total,
                "thinking_tokens": max(0, total_tokens_total - input_tokens_total - output_tokens_total),
            },
        })

    # イベントログから脱落情報を取得
    events_file = trial_dir / f"{game_id}_events.jsonl"
    eliminated: list[str] = []
    cash_by_pid: dict[str, int] = {}
    debt_by_pid: dict[str, int] = {}
    loan_by_pid: dict[str, int] = {}
    game_config: dict[str, Any] = {}
    if events_file.exists():
        events = _cache.get_entries(events_file)
        for e in events:
            et = e.get("event_type")
            if et == "GAME_START":
                game_config = e.get("data", {}).get("config", {})
            elif et == "LOAN_CHOSEN":
                data = e.get("data", {})
                pid = data.get("player_id", "")
                if pid:
                    loan_by_pid[pid] = data.get("loan_amount", 0)
            elif et in ("ELIMINATION", "BANKRUPTCY", "FORCED_LIQUIDATION"):
                pid = e.get("data", {}).get("player_id", "")
                if pid and pid not in eliminated:
                    eliminated.append(pid)
            elif et == "SURVIVAL_CHECK":
                data = e.get("data", {})
                if data.get("result") == "eliminated":
                    pid = data.get("player_id", "")
                    if pid and pid not in eliminated:
                        eliminated.append(pid)
            elif et == "SNAPSHOT":
                # SNAPSHOTから各プレイヤーの所持金・借金を取得
                # debt = cash - free_cash（free_cash > 0 のとき正確）
                for pid, sd in e.get("data", {}).get("snapshots", {}).items():
                    cash = sd.get("cash")
                    free_cash = sd.get("free_cash")
                    if cash is not None:
                        cash_by_pid[pid] = cash
                        if free_cash is not None:
                            debt_by_pid[pid] = cash - free_cash
            elif et == "INTEREST":
                # old_debt = SNAPSHOT時点の正確な借金残高
                # free_cash=0 の場合 cash-free_cash は過小評価するため、
                # INTEREST.old_debt で補正する
                data = e.get("data", {})
                pid = data.get("player_id", "")
                if pid and data.get("old_debt") is not None:
                    debt_by_pid[pid] = data["old_debt"]

        # プレイヤーの状態を更新
        for p in players:
            pid = p["player_id"]
            if pid in eliminated:
                p["status"] = "eliminated"
            if pid in cash_by_pid:
                p["cash"] = cash_by_pid[pid]
            if pid in debt_by_pid:
                p["debt"] = debt_by_pid[pid]
            if pid in loan_by_pid:
                p["initial_loan"] = loan_by_pid[pid]

    return {
        "current_round": max_round,
        "total_rounds": 12,
        "players": players,
        "recent_messages": all_messages[-5:],
        "total_cost": round(total_cost, 4),
        "eliminated": eliminated,
        "survivors": len(players) - len(eliminated),
        "config": game_config,
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


def get_commentary(
    logs_dir: Path, trial_dir_name: str, game_id: str,
) -> dict[str, Any] | None:
    """
    台本データを取得する。v2優先、v1フォールバック。

    Returns:
        {version, rounds: {"1": [{speaker, text}, ...], ...}} or None
    """
    trial_dir = logs_dir / trial_dir_name

    # v2優先 → v1フォールバック
    v2_path = trial_dir / "commentary" / "v2" / "script.jsonl"
    v1_path = trial_dir / "commentary" / "script.jsonl"

    if v2_path.exists():
        path = v2_path
        version = "v2"
    elif v1_path.exists():
        path = v1_path
        version = "v1"
    else:
        return None

    entries = _cache.get_entries(path)
    if not entries:
        return None

    # ラウンドごとにグループ化
    rounds: dict[str, list[dict[str, str]]] = {}
    for e in entries:
        rn = str(e.get("round", 0))
        speaker = e.get("speaker", "unknown")
        text = e.get("text", "")
        if rn not in rounds:
            rounds[rn] = []
        rounds[rn].append({"speaker": speaker, "text": text})

    return {"version": version, "rounds": rounds}


# §3.1 全員同一構成の固定12枚デッキ（card_id）
FULL_DECK = [
    "ROYAL_FLUSH_1", "STRAIGHT_FLUSH_1", "FOUR_OF_A_KIND_1", "FULL_HOUSE_1",
    "FLUSH_1", "STRAIGHT_1", "THREE_OF_A_KIND_1", "TWO_PAIR_1",
    "ONE_PAIR_1", "ONE_PAIR_2", "HIGH_CARD_1", "HIGH_CARD_2",
]


def _collect_round_messages(
    trial_dir: Path, game_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """
    LLMログから dm/broadcast をラウンド別に収集する。
    eventsのNEGOTIATION_ACTIONは本文/宛先を持たないため、LLMログを使う。

    Returns:
        {"1": [{sender, to?, type, message, turn, round}, ...], ...}
    """
    llm_logs_dir = trial_dir / "llm_logs"
    by_round: dict[str, list[dict[str, Any]]] = {}
    if not llm_logs_dir.exists():
        return by_round

    for lf in sorted(llm_logs_dir.glob(f"{game_id}_*_llm_calls.jsonl")):
        pid = lf.stem.split("_")[1] if "_" in lf.stem else "?"
        for e in _cache.get_entries(lf):
            a = _extract_action(e.get("response_text", ""))
            if not a or not isinstance(a, dict):
                continue
            at = a.get("type", "")
            if at not in ("dm", "broadcast"):
                continue
            rn = str(e.get("round_num", 0))
            rec: dict[str, Any] = {
                "sender": pid,
                "type": at,
                "message": str(a.get("message", ""))[:200],
                "turn": e.get("turn"),
                "round": e.get("round_num", 0),
            }
            if at == "dm":
                rec["to"] = a.get("to", "")
            by_round.setdefault(rn, []).append(rec)

    # ラウンド内は turn 順に並べる（turn 無しは末尾）
    for rn in by_round:
        by_round[rn].sort(key=lambda r: (r["turn"] is None, r["turn"] or 0))
    return by_round


def _collect_contract_terms(
    trial_dir: Path, game_id: str,
) -> dict[tuple[str, int], list[Any]]:
    """
    LLMログから contract_propose の terms を (proposer, round) 別に収集する。
    同一 (proposer, round) に複数提案があり得るためリストで保持し、順に消費する。
    """
    llm_logs_dir = trial_dir / "llm_logs"
    terms_by_key: dict[tuple[str, int], list[Any]] = {}
    if not llm_logs_dir.exists():
        return terms_by_key
    for lf in sorted(llm_logs_dir.glob(f"{game_id}_*_llm_calls.jsonl")):
        pid = lf.stem.split("_")[1] if "_" in lf.stem else "?"
        for e in _cache.get_entries(lf):
            a = _extract_action(e.get("response_text", ""))
            if a and isinstance(a, dict) and a.get("type") == "contract_propose":
                key = (pid, e.get("round_num", 0))
                terms_by_key.setdefault(key, []).append(a.get("terms", []))
    return terms_by_key


def get_round_states(
    logs_dir: Path, trial_dir_name: str, game_id: str,
) -> dict[str, Any]:
    """
    ラウンド別の盤面状況を再構成して返す。

    Returns:
        {
          total_rounds, players:[pid...], seat_map:{pid:label},
          rounds: { "R": {markets, commits, cash, holdings, contracts,
                          messages, eliminated} }
        }
    """
    trial_dir = logs_dir / trial_dir_name
    seat_map = _load_seat_map(trial_dir, game_id)
    events_file = trial_dir / f"{game_id}_events.jsonl"
    messages_by_round = _collect_round_messages(trial_dir, game_id)
    terms_by_key = _collect_contract_terms(trial_dir, game_id)

    result: dict[str, Any] = {
        "total_rounds": 12,
        "players": [],
        "seat_map": seat_map,
        "rounds": {},
    }

    if not events_file.exists():
        # eventsが無くてもメッセージだけは見せられる
        for rn, msgs in messages_by_round.items():
            result["rounds"][rn] = {
                "markets": [], "commits": [], "cash": {}, "holdings": {},
                "contracts": [], "messages": msgs, "eliminated": [],
            }
        return result

    events = _cache.get_entries(events_file)

    def rnd(r: Any) -> dict[str, Any]:
        rn = str(r)
        if rn not in result["rounds"]:
            result["rounds"][rn] = {
                "markets": [], "commits": [], "cash": {}, "holdings": {},
                "contracts": [], "messages": [], "eliminated": [],
            }
        return result["rounds"][rn]

    players: list[str] = []
    # 契約の累積状態 {contract_id: {...}}
    contracts: dict[str, dict[str, Any]] = {}
    # カード累積提出 {pid: set(card_id)}
    committed: dict[str, set[str]] = {}
    max_round = 0

    for e in events:
        et = e.get("event_type")
        data = e.get("data", {})
        r = e.get("round_num", 0)
        if isinstance(r, int) and r > max_round:
            max_round = r

        if et == "LOAN_CHOSEN":
            pid = data.get("player_id", "")
            if pid and pid not in players:
                players.append(pid)

        elif et == "MARKET_OPEN":
            rd = rnd(r)
            for m in data.get("markets", []):
                rd["markets"].append({
                    "market_id": m.get("market_id"),
                    "base_prize": m.get("base_prize"),
                    "carryover": m.get("carryover"),
                    "prize_pool": m.get("prize_pool"),
                    "participants": None,
                    "winners": [],
                    "prize_per_winner": None,
                })

        elif et == "MARKET_RESULT":
            rd = rnd(r)
            mid = data.get("market_id")
            target = None
            for m in rd["markets"]:
                if m["market_id"] == mid:
                    target = m
                    break
            if target is None:
                target = {"market_id": mid, "base_prize": None,
                          "carryover": None, "prize_pool": data.get("total_pool")}
                rd["markets"].append(target)
            target["participants"] = data.get("participants")
            target["winners"] = data.get("winners", [])
            target["prize_per_winner"] = data.get("prize_per_winner")
            target["total_pool"] = data.get("total_pool")
            target["surged"] = data.get("surged", False)

        elif et == "REVEAL":
            rd = rnd(r)
            rd["commits"] = []  # REVEALが正（rank付き）。COMMIT由来の重複を置換
            for c in data.get("commits", []):
                rd["commits"].append({
                    "player_id": c.get("player_id"),
                    "market_id": c.get("market_id"),
                    "card": c.get("card"),
                    "rank": c.get("rank"),
                })
                pid = c.get("player_id", "")
                card = c.get("card")
                if pid and card:
                    committed.setdefault(pid, set()).add(card)

        elif et == "COMMIT":
            # REVEALが無い場合のフォールバック用にcommittedへ反映
            pid = data.get("player_id", "")
            card = data.get("card")
            rd = rnd(r)
            if pid and card:
                committed.setdefault(pid, set()).add(card)
                if not any(x.get("player_id") == pid and x.get("card") == card
                           for x in rd["commits"]):
                    rd["commits"].append({
                        "player_id": pid, "market_id": data.get("market_id"),
                        "card": card, "rank": None,
                    })

        elif et == "SNAPSHOT":
            rd = rnd(r)
            for pid, sd in data.get("snapshots", {}).items():
                cash = sd.get("cash")
                free = sd.get("free_cash")
                # debt = cash - free_cash（free_cash > 0 のとき正確）
                debt = (cash - free) if (cash is not None and free is not None) else None
                rd["cash"][pid] = {"cash": cash, "free_cash": free, "debt": debt}

        elif et == "INTEREST":
            # old_debt = SNAPSHOT時点の正確な借金残高
            # free_cash=0 の場合 cash-free_cash は過小評価するため補正
            rd = rnd(r)
            pid = data.get("player_id", "")
            if pid and data.get("old_debt") is not None:
                entry = rd["cash"].setdefault(pid, {"cash": None, "free_cash": None, "debt": None})
                entry["debt"] = data["old_debt"]

        elif et == "NEGOTIATION_ACTION":
            act = data.get("action")
            if act == "contract_propose":
                cid = data.get("contract_id")
                proposer = data.get("player_id", "")
                parties = data.get("parties", [])
                key = (proposer, r)
                terms = None
                if terms_by_key.get(key):
                    terms = terms_by_key[key].pop(0)
                if cid:
                    contracts[cid] = {
                        "contract_id": cid,
                        "proposer": proposer,
                        "parties": parties,
                        "signed_by": [proposer],
                        "round_created": r,
                        "terms": terms,
                    }
            elif act == "contract_sign":
                cid = data.get("contract_id")
                signer = data.get("player_id", "")
                c = contracts.get(cid)
                if c and signer and signer not in c["signed_by"]:
                    c["signed_by"].append(signer)

        elif et in ("BANKRUPTCY", "FORCED_LIQUIDATION"):
            rd = rnd(r)
            rd["eliminated"].append({
                "player_id": data.get("player_id"),
                "reason": data.get("reason"),
            })
        elif et == "SURVIVAL_CHECK" and data.get("result") == "eliminated":
            rd = rnd(r)
            rd["eliminated"].append({
                "player_id": data.get("player_id"),
                "reason": "condition_not_met",
            })

    # プレイヤー順が取れなければ cash から補完
    if not players:
        seen: list[str] = []
        for rn in result["rounds"]:
            for pid in result["rounds"][rn]["cash"]:
                if pid not in seen:
                    seen.append(pid)
        players = sorted(seen)
    result["players"] = players

    # 各ラウンドに: メッセージ、契約の累積スナップショット、手札を付与
    # 手札はラウンド順に累積提出を再計算（committedは最終状態のため作り直す）
    cum_committed: dict[str, set[str]] = {pid: set() for pid in players}
    for rn in sorted(result["rounds"].keys(), key=lambda x: int(x)):
        rd = result["rounds"][rn]
        r_int = int(rn)
        # このラウンドの提出を累積へ
        for c in rd["commits"]:
            pid = c.get("player_id")
            card = c.get("card")
            if pid and card:
                cum_committed.setdefault(pid, set()).add(card)
        # 手札 = FULL_DECK − 累積提出（強い順）
        rd["holdings"] = {
            pid: [cid for cid in FULL_DECK if cid not in cum_committed.get(pid, set())]
            for pid in players
        }
        # メッセージ
        rd["messages"] = messages_by_round.get(rn, [])
        # 契約の「このラウンド時点」スナップショット（round_created ≤ R）
        rd["contracts"] = [
            {
                "contract_id": c["contract_id"],
                "proposer": c["proposer"],
                "parties": c["parties"],
                "signed_by": list(c["signed_by"]),
                "round_created": c["round_created"],
                "terms": c["terms"],
            }
            for c in contracts.values()
            if c["round_created"] <= r_int
        ]

    # eventsに無いがメッセージだけあるラウンドも拾う
    for rn, msgs in messages_by_round.items():
        if rn not in result["rounds"]:
            result["rounds"][rn] = {
                "markets": [], "commits": [], "cash": {},
                "holdings": {pid: list(FULL_DECK) for pid in players},
                "contracts": [], "messages": msgs, "eliminated": [],
            }

    return result


def get_cost_breakdown(
    logs_dir: Path, trial_dir_name: str, game_id: str,
) -> dict[str, Any]:
    """
    モデル別・プレイヤー別のコスト内訳を返す。

    Stage 2 の estimate_cost() を使い、キャッシュ割引・thinking内訳を正確に反映。
    reasoning テキストは一切含めない（秘匿維持）。
    """
    from llm.models import ModelInfo, estimate_cost, MODEL_REGISTRY

    # model_id → ModelInfo のルックアップ（レジストリから）
    # setdefault で先勝ち統一（llm.models.get_model() の逆引きと同じ意味論）。
    # M5/L5, M6/L6 のようにmodel_idが重複するエントリでは宣言順が早い方(M5/M6)が勝つ。
    _model_by_id: dict[str, ModelInfo] = {}
    for mi in MODEL_REGISTRY.values():
        _model_by_id.setdefault(mi.model_id, mi)

    trial_dir = logs_dir / trial_dir_name
    llm_logs_dir = trial_dir / "llm_logs"
    seat_map = _load_seat_map(trial_dir, game_id)

    by_model: dict[str, dict[str, Any]] = {}
    by_player: dict[str, dict[str, Any]] = {}
    total_cost = 0.0

    if not llm_logs_dir.exists():
        return {"by_model": {}, "by_player": {}, "total_cost": 0.0, "total_cost_jpy": 0}

    for lf in sorted(llm_logs_dir.glob(f"{game_id}_*_llm_calls.jsonl")):
        pid = lf.stem.split("_")[1] if "_" in lf.stem else "?"
        entries = _cache.get_entries(lf)
        if not entries:
            continue

        model_id = entries[0].get("model_id", "unknown")
        model_name = seat_map.get(pid, model_id)

        # プレイヤー集計の初期化
        p_data = by_player.setdefault(pid, {
            "model_id": model_id,
            "model_name": model_name,
            "calls": 0,
            "cost_total": 0.0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_read_tokens": 0,
            "thinking_tokens": 0,
        })

        for e in entries:
            inp = e.get("input_tokens", 0) or 0
            out = e.get("output_tokens", 0) or 0
            cached = e.get("cache_read_input_tokens", 0) or 0
            tt = e.get("total_tokens", 0) or 0
            thinking = max(0, (tt if tt > 0 else (inp + out)) - inp - out)

            # コスト再計算: ModelInfo があれば estimate_cost、なければログの cost_usd
            mi = _model_by_id.get(model_id)
            if mi:
                cost = estimate_cost(mi, inp, out, cache_read_input_tokens=cached, total_tokens=tt)
            else:
                cost = e.get("cost_usd", 0) or 0

            # モデル別集計
            m_data = by_model.setdefault(model_id, {
                "name": model_name,
                "calls": 0,
                "input_tokens": 0,
                "output_tokens": 0,
                "cache_read_tokens": 0,
                "thinking_tokens": 0,
                "cost_input": 0.0,
                "cost_output": 0.0,
                "cost_thinking": 0.0,
                "cost_total": 0.0,
            })
            m_data["calls"] += 1
            m_data["input_tokens"] += inp
            m_data["output_tokens"] += out
            m_data["cache_read_tokens"] += cached
            m_data["thinking_tokens"] += thinking
            m_data["cost_total"] += cost

            # 入力/出力/thinking の内訳コスト
            if mi:
                eff_cached_price = mi.cached_input_price if mi.cached_input_price is not None else mi.input_price
                eff_reasoning_price = mi.reasoning_price if mi.reasoning_price is not None else mi.output_price
                m_data["cost_input"] += ((inp - cached) * mi.input_price + cached * eff_cached_price) / 1_000_000
                m_data["cost_output"] += (out * mi.output_price) / 1_000_000
                m_data["cost_thinking"] += (thinking * eff_reasoning_price) / 1_000_000

            # プレイヤー集計
            p_data["calls"] += 1
            p_data["cost_total"] += cost
            p_data["input_tokens"] += inp
            p_data["output_tokens"] += out
            p_data["cache_read_tokens"] += cached
            p_data["thinking_tokens"] += thinking

            total_cost += cost

    # 丸め
    for m in by_model.values():
        m["cost_total"] = round(m["cost_total"], 6)
        m["cost_input"] = round(m["cost_input"], 6)
        m["cost_output"] = round(m["cost_output"], 6)
        m["cost_thinking"] = round(m["cost_thinking"], 6)
    for p in by_player.values():
        p["cost_total"] = round(p["cost_total"], 6)

    return {
        "by_model": by_model,
        "by_player": by_player,
        "total_cost": round(total_cost, 6),
        "total_cost_jpy": round(total_cost * 150, 0),
    }
