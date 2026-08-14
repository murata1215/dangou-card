"""
一人称記憶パケット構築

build_trace の GameTrace から、特定プレイヤー(pid)の視点に絞った
「一人称記憶パケット」を構築する。

- 公開層(PublicLayer)は全員に見える情報なのでそのまま通す
- 秘匿層(SecretLayer)は当該pidのstrategy/dmのみにフィルタ
  - strategy: player_id == pid
  - dm: sender == pid(送信) または recipient == pid(受信)

engine/commentary/trace.py の GameTrace を入力に取り、追加のログ読み込みは行わない。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from engine.commentary.trace import GameTrace


@dataclass
class RoundMemory:
    """1ラウンド分の一人称記憶"""
    round_num: int
    factsheet: list[str] = field(default_factory=list)      # 確定事実(公開)
    markets: list[dict] = field(default_factory=list)       # 公開
    broadcasts: list[dict] = field(default_factory=list)    # 公開(全体チャット)
    commits: list[dict] = field(default_factory=list)       # 公開(Reveal)
    market_results: list[dict] = field(default_factory=list)  # 公開
    snapshots: dict = field(default_factory=dict)           # 公開(残高)
    eliminations: list[dict] = field(default_factory=list)  # 公開
    survival_checks: list[dict] = field(default_factory=list)  # 公開
    my_strategies: list[dict] = field(default_factory=list)  # 秘匿(本人): {round,text,emotion}
    my_dms_sent: list[dict] = field(default_factory=list)   # 秘匿(本人送信): {recipient,message,turn}
    my_dms_recv: list[dict] = field(default_factory=list)   # 秘匿(本人受信): {sender,message,turn}


@dataclass
class PlayerMemory:
    """特定プレイヤーの一人称記憶パケット(全ラウンド)"""
    pid: str
    name: str
    model_key: str            # seat_map の "M5:Kimi K2.6" → "M5"
    num_rounds: int
    survival_cash: int
    rounds: list[RoundMemory] = field(default_factory=list)
    final_result: str | None = None    # "survived" / "eliminated" / "bankruptcy"
    final_round: int | None = None
    final_cash: int | None = None
    final_debt: int | None = None
    final_reason: str | None = None
    initial_loan: int | None = None    # ゲーム開始時に選んだ借入額(利息前)


def _model_key(name: str) -> str:
    """座席表の表示名 'M5:Kimi K2.6' からモデルキー 'M5' を取り出す。"""
    if ":" in name:
        return name.split(":", 1)[0].strip()
    return name.strip()


def _parse_initial_loan(factsheet: list[str], pid: str) -> int | None:
    """R1ファクトシートの利息行から pid の初期借入額(利息前)を抽出する。

    利息行の例: "... P08(M5:Kimi K2.6) 借金3,000,000円→3,045,000円(+45,000円); ..."
    → "借金" 直後・"→" 直前の値(利息が付く前＝開始時に選んだ借入額)を返す。
    """
    pat = re.compile(re.escape(pid) + r"\([^)]*\)\s*借金([\d,]+)円→")
    for line in factsheet:
        m = pat.search(line)
        if m:
            try:
                return int(m.group(1).replace(",", ""))
            except ValueError:
                return None
    return None


def build_player_memory(trace: GameTrace, pid: str) -> PlayerMemory:
    """GameTrace から pid 視点の一人称記憶パケットを構築する。"""
    if pid not in trace.seat_map:
        raise ValueError(f"unknown player id: {pid} (available: {sorted(trace.seat_map)})")

    name = trace.seat_map[pid]
    survival_cash = int(trace.config.get("survival_cash", 2_000_000))
    num_rounds = int(trace.config.get("num_rounds", len(trace.packets)))

    mem = PlayerMemory(
        pid=pid,
        name=name,
        model_key=_model_key(name),
        num_rounds=num_rounds,
        survival_cash=survival_cash,
    )

    for packet in trace.packets:
        pub = packet.public
        sec = packet.secret

        rm = RoundMemory(
            round_num=packet.round_num,
            factsheet=list(pub.factsheet),
            markets=list(pub.markets),
            broadcasts=list(pub.broadcasts),
            commits=list(pub.commits),
            market_results=list(pub.market_results),
            snapshots=dict(pub.snapshots),
            eliminations=list(pub.eliminations),
            survival_checks=list(pub.survival_checks),
        )

        # --- 秘匿層フィルタ: 本人の内心メモ ---
        for st in sec.strategies:
            if st.get("player_id") == pid:
                rm.my_strategies.append({
                    "round": st.get("round"),
                    "text": st.get("text", ""),
                    "emotion": st.get("emotion"),
                })

        # --- 秘匿層フィルタ: 本人が送受信したDM ---
        for dm in sec.dms:
            if dm.get("sender") == pid:
                rm.my_dms_sent.append({
                    "recipient": dm.get("recipient", ""),
                    "message": dm.get("message", ""),
                    "turn": dm.get("turn"),
                })
            elif dm.get("recipient") == pid:
                rm.my_dms_recv.append({
                    "sender": dm.get("sender", ""),
                    "message": dm.get("message", ""),
                    "turn": dm.get("turn"),
                })

        # --- 本人の最終結果を拾う ---
        for sc in pub.survival_checks:
            if sc.get("player_id") == pid:
                mem.final_result = sc.get("result")
                mem.final_round = packet.round_num
                mem.final_cash = sc.get("cash")
                mem.final_debt = sc.get("debt")

        for el in pub.eliminations:
            if el.get("player_id") == pid:
                # 破産・強制清算(ゲーム途中脱落)。survival_check より優先度は状況次第だが、
                # 途中破産はそれ自体が最終結果なので上書きする。
                mem.final_result = "bankruptcy" if el.get("reason") == "bankruptcy" else "eliminated"
                mem.final_round = packet.round_num
                mem.final_reason = el.get("reason")

        mem.rounds.append(rm)

    # 初期借入額(利息前)を R1 ファクトシートから抽出
    if mem.rounds:
        mem.initial_loan = _parse_initial_loan(mem.rounds[0].factsheet, pid)

    return mem
