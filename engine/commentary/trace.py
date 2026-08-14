"""
トレース構築モジュール

完了済み試合ログからラウンド単位のパケット(公開層/秘匿層)を構築する。
ゲームの再実行は行わず、JSONL読み取りのみで完了する。
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# データ構造
# ---------------------------------------------------------------------------

@dataclass
class PublicLayer:
    """公開情報 — ずんだもん(実況)に渡して良い情報"""
    markets: list[dict] = field(default_factory=list)
    broadcasts: list[dict] = field(default_factory=list)       # {player_id, message, turn}
    commits: list[dict] = field(default_factory=list)          # REVEAL
    market_results: list[dict] = field(default_factory=list)   # MARKET_RESULT
    eliminations: list[dict] = field(default_factory=list)     # BANKRUPTCY / FORCED_LIQUIDATION
    survival_checks: list[dict] = field(default_factory=list)  # SURVIVAL_CHECK (R12)
    snapshots: dict = field(default_factory=dict)              # {player_id: {cash, free_cash}}
    alive_count: int = 0
    contracts_proposed: int = 0
    highlights: list[dict] = field(default_factory=list)
    factsheet: list[str] = field(default_factory=list)   # v2: 事前計算済みファクト

    def to_dict(self) -> dict:
        return {
            "markets": self.markets,
            "broadcasts": self.broadcasts,
            "commits": self.commits,
            "market_results": self.market_results,
            "eliminations": self.eliminations,
            "survival_checks": self.survival_checks,
            "snapshots": self.snapshots,
            "alive_count": self.alive_count,
            "contracts_proposed": self.contracts_proposed,
            "highlights": self.highlights,
            "factsheet": self.factsheet,
        }


@dataclass
class SecretLayer:
    """秘匿情報 — めたん(解説)のみ使用"""
    strategies: list[dict] = field(default_factory=list)  # {player_id, round, text, emotion}
    dms: list[dict] = field(default_factory=list)         # {sender, recipient, message, turn}

    def to_dict(self) -> dict:
        return {
            "strategies": self.strategies,
            "dms": self.dms,
        }


@dataclass
class RoundPacket:
    """1ラウンド分の実況データパケット"""
    round_num: int
    public: PublicLayer
    secret: SecretLayer

    def to_dict(self) -> dict:
        return {
            "round_num": self.round_num,
            "public": self.public.to_dict(),
            "secret": self.secret.to_dict(),
        }


@dataclass
class GameTrace:
    """試合全体のトレース"""
    seat_map: dict[str, str]   # player_id → display_name
    config: dict               # GAME_START.data.config
    packets: list[RoundPacket] = field(default_factory=list)


# ---------------------------------------------------------------------------
# JSONL読み込みユーティリティ
# ---------------------------------------------------------------------------

def _read_jsonl(path: Path) -> list[dict]:
    """JSONLファイルを行ごとにパースして返す"""
    entries: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                entries.append(json.loads(line))
    return entries


# ---------------------------------------------------------------------------
# LLM応答からアクション情報を抽出
# ---------------------------------------------------------------------------

def _extract_action_json(response_text: str) -> dict | None:
    """LLM response_textからJSONオブジェクトを抽出する"""
    if not response_text:
        return None
    # コードフェンス内のJSON
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", response_text, re.DOTALL)
    text = m.group(1).strip() if m else response_text.strip()
    # 先頭のJSON以外のテキストをスキップ
    idx = text.find("{")
    if idx < 0:
        return None
    text = text[idx:]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # 切り詰められたJSONの簡易修復: 閉じ括弧を追加
        for suffix in ["}", "}}", "}}}", "}}}}",  '"}', '"}}', '"}}}']:
            try:
                return json.loads(text + suffix)
            except json.JSONDecodeError:
                continue
        return None


def _extract_strategy_text(parsed: dict) -> str:
    """戦略メモを読みやすいテキストに変換"""
    strategy = parsed.get("strategy")
    if not strategy or not isinstance(strategy, dict):
        return ""
    parts: list[str] = []
    for key in ["reason", "current_goal", "card_plan", "target_market", "emotion"]:
        val = strategy.get(key)
        if val:
            parts.append(f"{key}: {val}")
    return " / ".join(parts) if parts else json.dumps(strategy, ensure_ascii=False)[:300]


# ---------------------------------------------------------------------------
# ファクトシート構築 (v2)
# ---------------------------------------------------------------------------

def _format_yen(amount: int | float) -> str:
    """数値を円表示にフォーマット"""
    return f"{int(amount):,}円"


def _build_factsheet(
    pub: "PublicLayer",
    round_events: list[dict],
    game_config: dict,
    seat_map: dict[str, str],
) -> list[str]:
    """
    ラウンドのイベントから事前計算済みファクトシートを構築する。
    AIはこのテキストをそのまま引用するだけでよい。
    """
    facts: list[str] = []

    # 1. 市場結果サマリ (REVEAL + MARKET_RESULT)
    # コミット情報をmarket_idごとにグループ化
    commits_by_market: dict[str, list[dict]] = {}
    for c in pub.commits:
        mid = c.get("market_id", "")
        commits_by_market.setdefault(mid, []).append(c)

    for mr in pub.market_results:
        mid = mr.get("market_id", "")
        winners = mr.get("winners", [])
        prize = mr.get("prize_per_winner", 0)
        pool = mr.get("total_pool", 0)
        participants = mr.get("participants", 0)

        # コミットからカード情報を取得
        market_commits = commits_by_market.get(mid, [])
        commit_strs = []
        for c in market_commits:
            pid = c.get("player_id", "")
            name = seat_map.get(pid, pid)
            rank = c.get("rank", "?")
            is_winner = pid in winners
            status = "勝利" if is_winner else "敗北"
            commit_strs.append(f"{pid}({name})={rank}[{status}]")

        winner_names = [f"{w}({seat_map.get(w, w)})" for w in winners]
        fact = (
            f"市場{mid}: 参加{participants}人({', '.join(commit_strs)})。"
            f"勝者: {', '.join(winner_names)}、1人あたり{_format_yen(prize)}、プール合計{_format_yen(pool)}"
        )
        facts.append(fact)

    # 2. 決算後の残高 (SNAPSHOT)
    if pub.snapshots:
        balance_parts = []
        for pid in sorted(pub.snapshots.keys()):
            snap = pub.snapshots[pid]
            name = seat_map.get(pid, pid)
            balance_parts.append(
                f"{pid}({name})=現金{_format_yen(snap['cash'])}, FreeCash{_format_yen(snap['free_cash'])}"
            )
        facts.append(f"決算後残高: {'; '.join(balance_parts)}")

    # 3. 利息情報 (INTEREST)
    interest_parts = []
    for ev in round_events:
        if ev.get("event_type") == "INTEREST":
            d = ev.get("data", {})
            pid = d.get("player_id", "")
            name = seat_map.get(pid, pid)
            interest_parts.append(
                f"{pid}({name}) 借金{_format_yen(d.get('old_debt', 0))}"
                f"→{_format_yen(d.get('new_debt', 0))}"
                f"(+{_format_yen(d.get('interest', 0))})"
            )
    if interest_parts:
        facts.append(f"利息: {'; '.join(interest_parts)}")

    # 4. 破産イベント (BANKRUPTCY)
    for ev in round_events:
        if ev.get("event_type") == "BANKRUPTCY":
            d = ev.get("data", {})
            pid = d.get("player_id", "")
            name = seat_map.get(pid, pid)
            reason = d.get("reason", "不明")
            facts.append(f"破産: {pid}({name})が{reason}で破産脱落(R{d.get('round', '?')})")

    # 5. 生還判定 (SURVIVAL_CHECK — 主にR12)
    survival_cash = game_config.get("survival_cash", 2000000)
    for sc in pub.survival_checks:
        pid = sc.get("player_id", "")
        name = seat_map.get(pid, pid)
        result = sc.get("result", "")
        cash = sc.get("cash", 0)
        debt = sc.get("debt", 0)

        if result == "survived":
            facts.append(
                f"生還判定: {pid}({name}) survived"
                f"(現金{_format_yen(cash)}, 借金{_format_yen(debt)}"
                f" → 条件: 現金≧{_format_yen(survival_cash)}かつ借金0 → 達成)"
            )
        else:
            reason_parts = []
            if debt > 0:
                reason_parts.append(f"借金{_format_yen(debt)}が残存")
            if cash < survival_cash:
                shortfall = survival_cash - cash
                reason_parts.append(
                    f"現金{_format_yen(cash)} < {_format_yen(survival_cash)}({_format_yen(shortfall)}不足)"
                )
            reason_str = "、".join(reason_parts) if reason_parts else "条件未達"
            facts.append(
                f"生還判定: {pid}({name}) eliminated"
                f"(現金{_format_yen(cash)}, 借金{_format_yen(debt)}"
                f" → 条件: 現金≧{_format_yen(survival_cash)}かつ借金0 → 未達: {reason_str})"
            )

    return facts


# ---------------------------------------------------------------------------
# メイン: トレース構築
# ---------------------------------------------------------------------------

def build_trace(game_dir: str | Path) -> GameTrace:
    """
    試合ディレクトリからGameTraceを構築する。

    Args:
        game_dir: 試合ディレクトリ (例: logs/llm/trial_C_20260810_053055/)
    """
    game_dir = Path(game_dir)

    # --- 座席表 ---
    seat_map_path = list(game_dir.glob("*_seat_map.json"))
    if not seat_map_path:
        raise FileNotFoundError(f"seat_map.json not found in {game_dir}")
    with open(seat_map_path[0], "r", encoding="utf-8") as f:
        seat_map: dict[str, str] = json.load(f)

    # --- イベントログ ---
    events_path = list(game_dir.glob("*_events.jsonl"))
    if not events_path:
        raise FileNotFoundError(f"events.jsonl not found in {game_dir}")
    all_events = _read_jsonl(events_path[0])

    # --- LLMコールログ ---
    llm_dir = game_dir / "llm_logs"
    llm_calls: list[dict] = []
    if llm_dir.exists():
        for p in sorted(llm_dir.glob("*_llm_calls.jsonl")):
            llm_calls.extend(_read_jsonl(p))

    # --- ハイライト ---
    highlights_all: list[dict] = []
    # doc/highlights/<dir_name>/highlights.jsonl
    project_root = game_dir.parent.parent  # logs/llm/ → project root
    hl_path = project_root / "doc" / "highlights" / game_dir.name / "highlights.jsonl"
    if hl_path.exists():
        highlights_all = _read_jsonl(hl_path)

    # --- GAME_START configの取得 ---
    game_config: dict = {}
    for ev in all_events:
        if ev.get("event_type") == "GAME_START":
            game_config = ev.get("data", {}).get("config", {})
            break

    # --- ラウンド番号の範囲を特定 ---
    num_rounds = game_config.get("num_rounds", 12)

    # --- イベントをラウンドごとにグループ化 ---
    events_by_round: dict[int, list[dict]] = {}
    for ev in all_events:
        rn = ev.get("round_num", 0)
        events_by_round.setdefault(rn, []).append(ev)

    # --- LLMコールをラウンドごとにグループ化 ---
    llm_by_round: dict[int, list[dict]] = {}
    for call in llm_calls:
        rn = call.get("round_num", 0)
        llm_by_round.setdefault(rn, []).append(call)

    # --- ハイライトをラウンドごとにグループ化 ---
    hl_by_round: dict[int, list[dict]] = {}
    for hl in highlights_all:
        rn = hl.get("round", 0)
        hl_by_round.setdefault(rn, []).append(hl)

    # --- 生存者トラッキング ---
    num_players = game_config.get("num_players", 8)
    eliminated_players: set[str] = set()

    # --- パケット構築 ---
    packets: list[RoundPacket] = []

    for rn in range(1, num_rounds + 1):
        pub = PublicLayer()
        sec = SecretLayer()

        round_events = events_by_round.get(rn, [])

        for ev in round_events:
            et = ev["event_type"]
            data = ev.get("data", {})

            if et == "MARKET_OPEN":
                pub.markets = data.get("markets", [])

            elif et == "REVEAL":
                pub.commits = data.get("commits", [])

            elif et == "MARKET_RESULT":
                pub.market_results.append(data)

            elif et == "SNAPSHOT":
                pub.snapshots = data.get("snapshots", {})

            elif et == "BANKRUPTCY":
                pub.eliminations.append(data)
                pid = data.get("player_id")
                if pid:
                    eliminated_players.add(pid)

            elif et == "FORCED_LIQUIDATION":
                pub.eliminations.append(data)
                pid = data.get("player_id")
                if pid:
                    eliminated_players.add(pid)

            elif et == "SURVIVAL_CHECK":
                pub.survival_checks.append(data)
                if data.get("result") == "eliminated":
                    pid = data.get("player_id")
                    if pid:
                        eliminated_players.add(pid)

            elif et == "NEGOTIATION_ACTION":
                action_type = data.get("action")
                if action_type == "contract_propose":
                    pub.contracts_proposed += 1

        pub.alive_count = num_players - len(eliminated_players)

        # --- LLMコールからメッセージ本文・戦略メモを復元 ---
        round_llm = llm_by_round.get(rn, [])
        for call in round_llm:
            player_id = call.get("player_id", "")
            phase = call.get("phase", "")
            response_text = call.get("response_text", "")
            turn = call.get("turn")

            parsed = _extract_action_json(response_text)
            if not parsed:
                continue

            # 戦略メモの抽出
            strategy_text = _extract_strategy_text(parsed)
            if strategy_text:
                emotion = None
                strat = parsed.get("strategy", {})
                if isinstance(strat, dict):
                    emotion = strat.get("emotion")
                sec.strategies.append({
                    "player_id": player_id,
                    "round": rn,
                    "text": strategy_text,
                    "emotion": emotion,
                })

            # アクション(broadcast/dm)の抽出
            action = parsed.get("action")
            if not action or not isinstance(action, dict):
                continue
            atype = action.get("type", "")

            if atype == "broadcast":
                msg = action.get("message", "")
                if msg:
                    pub.broadcasts.append({
                        "player_id": player_id,
                        "message": msg,
                        "turn": turn,
                    })

            elif atype == "dm":
                msg = action.get("message", "")
                target = action.get("target", "")
                if msg:
                    sec.dms.append({
                        "sender": player_id,
                        "recipient": target,
                        "message": msg,
                        "turn": turn,
                    })

        # --- ハイライト ---
        pub.highlights = hl_by_round.get(rn, [])

        # --- ファクトシート (v2) ---
        pub.factsheet = _build_factsheet(pub, round_events, game_config, seat_map)

        packets.append(RoundPacket(round_num=rn, public=pub, secret=sec))

    return GameTrace(
        seat_map=seat_map,
        config=game_config,
        packets=packets,
    )


# ---------------------------------------------------------------------------
# 秘匿情報混入チェック
# ---------------------------------------------------------------------------

def extract_secret_keywords(secret: SecretLayer) -> set[str]:
    """
    秘匿層から特徴的なキーワードを抽出する。
    ずんだもんのセリフに混入していないかチェックするために使う。
    """
    keywords: set[str] = set()
    for dm in secret.dms:
        msg = dm.get("message", "")
        # DMメッセージから特徴的な短いフレーズを抽出(10文字以上の部分)
        for segment in re.split(r"[。、！？\n]", msg):
            segment = segment.strip()
            if len(segment) >= 10:
                keywords.add(segment)
    # 「内心メモ」「DM」といった構造的キーワードは除外(一般用語なので)
    return keywords


def check_leakage(
    zundamon_lines: list[str],
    secret: SecretLayer,
) -> list[str]:
    """
    ずんだもんのセリフに秘匿情報が混入していないかチェックする。
    混入があれば警告メッセージのリストを返す。
    """
    keywords = extract_secret_keywords(secret)
    warnings: list[str] = []
    full_text = " ".join(zundamon_lines)
    for kw in keywords:
        if kw in full_text:
            warnings.append(f"秘匿情報の可能性: 「{kw[:30]}...」がずんだもんのセリフに出現")
    return warnings


# ---------------------------------------------------------------------------
# 禁止語チェック (v2)
# ---------------------------------------------------------------------------

ZUNDAMON_BANNED_WORDS = [
    "内心メモ", "内心", "メモによると", "戦略メモ", "strategy",
    "DM", "ダイレクトメッセージ", "秘密のメッセージ",
    "本心", "心の中", "密約", "裏の",
    "秘匿情報", "秘匿層", "神視点",
]


def check_banned_words(zundamon_lines: list[str]) -> list[str]:
    """
    ずんだもんのセリフに禁止語が含まれていないかチェックする。
    含まれていれば違反語のリストを返す。
    """
    full_text = " ".join(zundamon_lines)
    violations: list[str] = []
    for word in ZUNDAMON_BANNED_WORDS:
        if word in full_text:
            violations.append(word)
    return violations
