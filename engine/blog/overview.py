"""
大会概要ファクトの構築(ネタバレ無し)

オープニング記事のために、この試合の「確定ルール・経済パラメータ」を Markdown 化する。
数値は trace.config(= engine/config.py の GameConfig)から引く。試合結果(生死・順位)
は一切含めない。

- build_overview_markdown(trace, roster): ナレーターに渡す大会ファクトを返す
"""

from __future__ import annotations

from engine.commentary.trace import GameTrace


def _yen(amount) -> str:
    try:
        return f"{int(amount):,}円"
    except (TypeError, ValueError):
        return str(amount)


def _man(amount) -> str:
    """円 → 万円表記(例: 1_200_000 -> '120万円')。"""
    try:
        return f"{int(amount) // 10000:,}万円"
    except (TypeError, ValueError):
        return str(amount)


# カードランク(弱→強)。GAME_KNOWLEDGE_BLOCK と一致。
_CARD_RANKS = (
    "HIGH_CARD(1) < ONE_PAIR(2) < TWO_PAIR(3) < THREE_OF_A_KIND(4) < "
    "STRAIGHT(5) < FLUSH(6) < FULL_HOUSE(7) < FOUR_OF_A_KIND(8) < "
    "STRAIGHT_FLUSH(9) < ROYAL_FLUSH(10)"
)


def build_overview_markdown(trace: GameTrace, roster: list[dict]) -> str:
    """大会概要の確定ファクト Markdown(ネタバレ無し)を構築する。"""
    cfg = trace.config
    num_players = int(cfg.get("num_players", len(roster)))
    num_rounds = int(cfg.get("num_rounds", len(trace.packets)))
    num_markets = int(cfg.get("num_markets", 3))
    survival_cash = int(cfg.get("survival_cash", 2_000_000))
    loan_min = int(cfg.get("loan_min", 1_200_000))
    loan_max = int(cfg.get("loan_max", 10_000_000))
    interest = float(cfg.get("interest_rate", 0.015))
    entry_fee = int(cfg.get("entry_fee", 100_000))
    contract_fee = int(cfg.get("contract_fee", 100_000))
    anon_fee = int(cfg.get("anon_broadcast_fee", 100_000))
    anon_limit = int(cfg.get("anon_broadcast_limit", 2))
    tiers = list(cfg.get("prize_tiers") or [])
    total_prize = cfg.get("total_prize")

    # 賞金スケジュールの要約。prize_tiers があれば連続同額をまとめ、
    # 無ければ total_prize から総額＋1ラウンド平均で表す。
    def _prize_line() -> str:
        if tiers:
            segs: list[str] = []
            start = 0
            for i in range(1, len(tiers) + 1):
                if i == len(tiers) or tiers[i] != tiers[start]:
                    r0, r1 = start + 1, i
                    label = f"R{r0}" if r0 == r1 else f"R{r0}-{r1}"
                    segs.append(f"{label}: 計{_man(tiers[start])}/R")
                    start = i
            return " / ".join(segs)
        if total_prize:
            per = int(total_prize) // max(num_rounds, 1)
            return f"総額 {_man(total_prize)}を全{num_rounds}ラウンドで配分（1ラウンド平均 約{_man(per)}）"
        return "非公開"

    lines: list[str] = []
    lines.append(f"# 大会概要ファクト: 嘘八百万 ―談合カード―（このデータのみ使用可・結果は伏せる）")
    lines.append("")
    lines.append("## 基本ルール")
    lines.append(f"- 参加者: AIエージェント {num_players}体（本戦の顔ぶれは下記ロスター）")
    lines.append(f"- 期間: 全{num_rounds}ラウンド")
    lines.append(f"- 毎ラウンド{num_markets}つの市場（M01・M02・M03…）が開放。市場参加は必須（パス不可）")
    lines.append(f"- 生還条件（最終ラウンド終了時）: 借金残高0 かつ 現金≧生還ライン {_yen(survival_cash)}")
    lines.append(f"- 敗北: 正式契約の違反 / Entry Fee等が払えず破産 / 終了時に生還条件未達")
    lines.append("")
    lines.append("## 資金と利息")
    lines.append(f"- 初期借入: {_yen(loan_min)}〜{_yen(loan_max)} の範囲で各自が選択")
    lines.append(f"- 利息: 毎ラウンド 借金残高に {interest * 100:.1f}%（複利）を計上")
    lines.append(f"- Free Cash（自由資金）= max(0, 現金 − 借金[利息込み])。送金・金銭契約はこの範囲でのみ可能")
    lines.append("")
    lines.append("## 市場と勝敗")
    lines.append(f"- 賞金規模: {_prize_line()}")
    lines.append(f"- Entry Fee: 1市場参加につき {_yen(entry_fee)}（その市場の賞金プールに加算）")
    lines.append(f"- 各自は毎ラウンド、参加市場と伏せたカード1枚を秘密裏にコミット")
    lines.append(f"- カードランク(弱→強): {_CARD_RANKS}")
    lines.append(f"- Reveal後、その市場で最高ランクのAIが賞金プール全額を獲得。同ランクは山分け。使用カードは消滅")
    lines.append(f"- 手札は全{num_rounds}枚（12ランク相当）で{num_rounds}ラウンドに1枚ずつ使い切る設計")
    lines.append("")
    lines.append("## 交渉・談合・裏切り")
    lines.append(f"- 各ラウンドに交渉フェーズ: 全体チャット(broadcast) / 個別DM / 契約提案・締結 / 即時送金")
    lines.append(f"- 口約束は無料・拘束力なし（破っても信用を失うだけ）")
    lines.append(f"- 匿名通信: {_yen(anon_fee)}で発信者を伏せた全体メッセージを1ラウンド{anon_limit}通まで")
    lines.append("")
    lines.append("## 公正証書（正式契約）")
    lines.append(f"- 発行料 {_yen(contract_fee)}（提案者が全額負担）。2人以上の連名・署名で成立")
    lines.append(f"- 型A（金銭契約）: 指定時点の金銭移動をシステムが自動執行。履行不能＝契約違反＝即時脱落")
    lines.append(f"- 型B（行動契約）: 市場参加/カード使用/特定市場不参加の指定。違反をSettlementで照合し即時脱落")
    lines.append(f"- 口約束と違い、公正証書は自動執行・自動監査。存在は公示され内容は秘匿")
    lines.append(f"- 「将来払いを結ばせ相手の資金源を断つ」経済暗殺が機械判定可能な形で成立する（意図的設計）")
    lines.append("")
    lines.append("## 出場ロスター（本戦の顔ぶれ・生死は伏せる）")
    for r in roster:
        lines.append(f"- {r['pid']}: {r['name']}（{r['provider']}）")
    lines.append("")
    lines.append("### 勝てない構造（ゲーム理論）")
    lines.append("- 全員生還には賞金総額を大きく上回る現金が必要で、算術的に全員は生き残れない。")
    lines.append("- ゆえに温い協調は必ず崩れる。談合し、裏切り、誰かを蹴落とすことが合理になる。")
    lines.append("")
    return "\n".join(lines).rstrip() + "\n"
