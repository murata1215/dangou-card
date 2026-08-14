"""
共通のラウンド状況(全プレイヤー共通の事実)の構築

各記事が共有する「ラウンドごとの詳細状況」を、GameTrace の公開層とファクトシート
から生成する。ここで作った Markdown を各LLMの執筆プロンプトに同梱し、記事に埋め込む。

- build_round_situation_markdown(trace): 記事に埋め込む Markdown を返す
- extract_cash_series(trace): 状況画像(situation_image)用に、ラウンド別の現金推移を返す
"""

from __future__ import annotations

from engine.commentary.trace import GameTrace


def _yen(amount) -> str:
    try:
        return f"{int(amount):,}円"
    except (TypeError, ValueError):
        return str(amount)


def build_round_situation_markdown(trace: GameTrace) -> str:
    """全プレイヤー共通の「ラウンドごとの詳細状況」Markdown を構築する。"""
    seat_map = trace.seat_map
    num_rounds = int(trace.config.get("num_rounds", len(trace.packets)))
    survival_cash = int(trace.config.get("survival_cash", 2_000_000))

    lines: list[str] = []
    lines.append(f"全{num_rounds}ラウンド / 生還ライン: 現金≧{_yen(survival_cash)} かつ 借金0（最終ラウンド終了時に判定）")
    lines.append("")
    lines.append("### 出場者")
    for pid, name in sorted(seat_map.items()):
        lines.append(f"- {pid}: {name}")
    lines.append("")

    for packet in trace.packets:
        pub = packet.public
        rn = packet.round_num
        lines.append(f"#### ラウンド{rn}")

        # 市場
        if pub.markets:
            market_strs = []
            for m in pub.markets:
                market_strs.append(
                    f"{m['market_id']}(賞金プール{_yen(m.get('prize_pool', 0))})"
                )
            lines.append(f"- 開放市場: {', '.join(market_strs)}")

        # ファクトシート(確定情報 = 市場結果・残高・利息・破産・生還判定を含む)
        if pub.factsheet:
            lines.append("- 確定事実:")
            for fact in pub.factsheet:
                lines.append(f"    - {fact}")

        # 脱落
        if pub.eliminations:
            for el in pub.eliminations:
                nm = seat_map.get(el.get("player_id", ""), el.get("player_id", ""))
                lines.append(f"- 脱落: {el.get('player_id')}({nm}) — {el.get('reason', '不明')}")

        lines.append(f"- 生存者数: {pub.alive_count}人 / 契約提案数: {pub.contracts_proposed}件")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def extract_cash_series(trace: GameTrace) -> dict:
    """
    状況画像用に、ラウンド別の現金推移などを抽出する。

    現金は SNAPSHOT の free_cash(自由資金＝拘束されていない手元資金)を採用する。
    SNAPSHOT の cash は市場コミット等で拘束された分を含む総額で、生還判定
    (現金≧survival_cash)とは乖離するため、生還ラインと同じ土俵の free_cash を使う。

    Returns:
        {
          "rounds": [1..N],
          "series": {pid: [free_cash_or_None per round]},
          "names": {pid: display_name},
          "bankruptcies": {pid: round_num},   # 真の破産のみ
          "final": {pid: {"result","cash","debt"}},  # 最終ラウンドの生還判定(確定値)
          "survival_cash": int,
          "num_rounds": int,
        }
    """
    seat_map = trace.seat_map
    num_rounds = int(trace.config.get("num_rounds", len(trace.packets)))
    survival_cash = int(trace.config.get("survival_cash", 2_000_000))

    rounds = list(range(1, num_rounds + 1))
    series: dict[str, list] = {pid: [None] * num_rounds for pid in seat_map}
    bankruptcies: dict[str, int] = {}
    final: dict[str, dict] = {}

    for packet in trace.packets:
        rn = packet.round_num
        idx = rn - 1
        if 0 <= idx < num_rounds:
            for pid, snap in packet.public.snapshots.items():
                if pid in series and isinstance(snap, dict):
                    val = snap.get("free_cash", snap.get("cash"))
                    if val is not None:
                        series[pid][idx] = val
        for el in packet.public.eliminations:
            pid = el.get("player_id")
            # 真の破産(reason=="bankruptcy")のみ X マーカー対象。
            # FORCED_LIQUIDATION(condition_not_met)は最終生還ライン未達なので
            # survival_checks 経由の「脱落(v)」で扱う。
            if pid and el.get("reason") == "bankruptcy" and pid not in bankruptcies:
                bankruptcies[pid] = rn
        for sc in packet.public.survival_checks:
            pid = sc.get("player_id")
            if pid:
                final[pid] = {
                    "result": sc.get("result"),
                    "cash": sc.get("cash"),
                    "debt": sc.get("debt"),
                }

    return {
        "rounds": rounds,
        "series": series,
        "names": dict(seat_map),
        "bankruptcies": bankruptcies,
        "final": final,
        "survival_cash": survival_cash,
        "num_rounds": num_rounds,
    }
