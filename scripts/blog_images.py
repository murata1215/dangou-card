#!/usr/bin/env python3
"""サイクル9.7: 連載16本用の図版28枚を生成し、記事へ画像参照を埋め込む。

再実行可能。engine/ 配下は import のみで変更しない。LLM API は呼ばない。

使い方:
    uv run python scripts/blog_images.py --date 20260824
    uv run python scripts/blog_images.py --date 20260830
    uv run python scripts/blog_images.py --date 20260921
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import matplotlib.font_manager as fm  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.blog.situation_image import _resolve_jp_font  # noqa: E402

# ---------------------------------------------------------------------------
# 静的データ（各記事の本文テーブル・転機節から採録。cash 単位は円）
# ---------------------------------------------------------------------------

ROSTER_20260824 = [
    ("P01", "Claude Haiku 4.5", 4_000_000),
    ("P02", "Kimi K2.6", 1_200_000),
    ("P03", "DeepSeek V4 Flash", 7_000_000),
    ("P04", "DeepSeek V4 Flash", 6_000_000),
    ("P05", "Gemini 3.5 Flash-Lite", 5_000_000),
    ("P06", "GPT-4.1 Mini", 1_200_000),
    ("P07", "Kimi K2.6", 1_200_000),
    ("P08", "Claude Haiku 4.5", 6_000_000),
    ("P09", "GPT-4.1 Mini", 1_200_000),
    ("P10", "Grok 4.3", 3_000_000),
    ("P11", "Grok 4.3", 5_000_000),
    ("P12", "Gemini 3.5 Flash-Lite", 10_000_000),
]

ROSTER_20260830 = [
    ("P01", "Claude Haiku 4.5", 7_000_000),
    ("P02", "Kimi K2.6", 4_000_000),
    ("P03", "DeepSeek V4 Flash", 4_000_000),
    ("P04", "DeepSeek V4 Flash", 6_000_000),
    ("P05", "Gemini 3.5 Flash-Lite", 1_200_000),
    ("P06", "GPT-4.1 Mini", 3_000_000),
    ("P07", "Kimi K2.6", 4_000_000),
    ("P08", "Claude Haiku 4.5", 4_000_000),
    ("P09", "GPT-4.1 Mini", 4_000_000),
    ("P10", "Grok 4.3", 5_000_000),
    ("P11", "Grok 4.3", 5_000_000),
    ("P12", "Gemini 3.5 Flash-Lite", 1_200_000),
]

# サイクル10.16の総括記事(17_summary.md)で使われた対戦表と同一(/tmp/c1016/gen_images.py と一致)。
# ※=DevRelay実験席（サブスク枠で動く実験席。仕組みの解説はしない）
ROSTER_20260921 = [
    ("P01", "Gemini 3.5 Flash-Lite", 5_000_000),
    ("P02", "Claude Opus 5※", 3_000_000),
    ("P03", "GPT-5.6 Terra※", 5_000_000),
    ("P04", "Grok 4.3", 5_000_000),
    ("P05", "GPT-4.1 Mini", 7_000_000),
    ("P06", "Claude Fable 5.1※", 5_000_000),
    ("P07", "DeepSeek V4 Flash", 5_000_000),
    ("P08", "Claude Haiku 4.5", 5_000_000),
    ("P09", "Kimi K2.6", 7_000_000),
    ("P10", "GPT-5.6 Sol※", 5_000_000),
    ("P11", "Claude Opus 4.8※", 3_000_000),
    ("P12", "Claude Sonnet 5※", 5_000_000),
]

# status: "survive" -> final_cash 有効値。"eliminated" -> elim_round + note（記事本文の注記）
RESULTS_20260824 = [
    dict(seat="P02", final_cash=5_036_313, status="survive"),
    dict(seat="P05", final_cash=5_004_647, status="survive"),
    dict(seat="P12", final_cash=4_969_302, status="survive"),
    dict(seat="P11", final_cash=4_464_647, status="survive"),
    dict(seat="P10", final_cash=2_850_787, status="survive"),
    dict(seat="P03", status="eliminated", elim_round=12, note="条件未達（136万円没収）"),
    dict(seat="P08", status="eliminated", elim_round=12, note="条件未達（33万円没収）"),
    dict(seat="P04", status="eliminated", elim_round=12, note="破産"),
    dict(seat="P01", status="eliminated", elim_round=10, note="破産"),
    dict(seat="P06", status="eliminated", elim_round=6, note="破産"),
    dict(seat="P07", status="eliminated", elim_round=6, note="破産"),
    dict(seat="P09", status="eliminated", elim_round=6, note="破産"),
]

RESULTS_20260830 = [
    dict(seat="P10", final_cash=7_656_647, status="survive"),
    dict(seat="P03", final_cash=6_407_719, status="survive"),
    dict(seat="P11", final_cash=6_324_647, status="survive"),
    dict(seat="P04", final_cash=5_993_580, status="survive"),
    dict(seat="P01", final_cash=5_458_511, status="survive"),
    dict(seat="P07", final_cash=4_247_719, status="survive"),
    dict(seat="P05", status="eliminated", elim_round=12, note="破産"),
    dict(seat="P02", status="eliminated", elim_round=9, note="破産"),
    dict(seat="P08", status="eliminated", elim_round=9, note="破産"),
    dict(seat="P09", status="eliminated", elim_round=9, note="破産"),
    dict(seat="P12", status="eliminated", elim_round=6, note="破産"),
    dict(seat="P06", status="eliminated", elim_round=2, note="契約違反"),
]

# サイクル10.16の総括記事(17_summary.md)で使われた最終結果表と同一(/tmp/c1016/gen_images.py と一致)。
# facts.md §2 の最終結果表と一致。
RESULTS_20260921 = [
    dict(seat="P06", final_cash=7_367_218, status="survive"),
    dict(seat="P10", final_cash=5_961_313, status="survive"),
    dict(seat="P02", final_cash=5_877_364, status="survive"),
    dict(seat="P11", final_cash=4_130_787, status="survive"),
    dict(seat="P07", final_cash=3_164_647, status="survive"),
    dict(seat="P01", status="eliminated", elim_round=10, note="破産"),
    dict(seat="P03", status="eliminated", elim_round=9, note="破産"),
    dict(seat="P08", status="eliminated", elim_round=9, note="破産"),
    dict(seat="P04", status="eliminated", elim_round=12, note="条件未達"),
    dict(seat="P09", status="eliminated", elim_round=12, note="条件未達"),
    dict(seat="P12", status="eliminated", elim_round=12, note="条件未達"),
    dict(seat="P05", status="eliminated", elim_round=3, note="契約違反"),
]

# 決定的瞬間カード。type="lie" -> LIE DETECTED / type="number" -> 決定的数値
# 出典: 各記事「## 転機」節本文（LIE は §5 と突合済みの宣言/実際の行動、number は本文中の数値）
MOMENT_CARDS = {
    "20260824": {
        "02": dict(seat="P06", type="number", heading="決定的数値",
                    lines=["現金 99,768円", "未回収債務 574,203円"], sub="第6ラウンド破産時"),
        "03": dict(seat="P09", type="number", heading="決定的数値",
                    lines=["M02高騰プール", "2,680,000円"], sub="第1ラウンド最大の当たり／第6ラウンド破産"),
        "04": dict(seat="P07", type="lie", round_label="R5",
                    statement="M03でSTRAIGHT_FLUSH、お互い勝ちましょう",
                    actual="M02でONE_PAIRを使用"),
        "05": dict(seat="P01", type="number", heading="決定的数値",
                    lines=["倍掛け預託 940,000円", "没収 全額"], sub="回収ならず"),
        "06": dict(seat="P04", type="lie", round_label="R10",
                    statement="M02でROYAL_FLUSH勝利、こちらはHIGH_CARD参加で30万円送金",
                    actual="送金なし・無視"),
        "07": dict(seat="P02", type="lie", round_label="R10",
                    statement="M02でROYAL_FLUSH勝利、こちらはHIGH_CARD参加で30万円送金",
                    actual="送金なし・無視"),
    },
    "20260830": {
        "10": dict(seat="P06", type="number", heading="決定的数値",
                    lines=["現金 2,546,250円", "果たせなかった義務 200,000円"], sub="契約違反による即時脱落"),
        "11": dict(seat="P12", type="number", heading="決定的数値",
                    lines=["必要賞金 32,302円", "結果 破産"], sub="第6ラウンドの崖っぷち"),
        "12": dict(seat="P09", type="number", heading="決定的数値",
                    lines=["必要額 480,000円", "手元 340,000円"], sub="第9ラウンド、届かなかった差額"),
        "13": dict(seat="P02", type="number", heading="決定的数値",
                    lines=["倍掛け預託 520,000円", "没収 全額"], sub="R7、FULL_HOUSEが砕けた日"),
        "14": dict(seat="P05", type="number", heading="決定的数値",
                    lines=["M01獲得 1,140,000円"], sub="崖っぷちからの生還"),
        "15": dict(seat="P10", type="lie", round_label="R11",
                    statement="M01でHIGH_CARD確定。M02誘導は無視してM01固めましょう",
                    actual="M02へ転向、STRAIGHT_FLUSHで参加"),
    },
    "20260921": {
        "18": dict(seat="P05", type="number", heading="決定的数値",
                    lines=["義務: M01・STRAIGHT_FLUSH", "実際: M02・FULL_HOUSE"],
                    sub="R3、警告を受けてなお契約と異なる市場へ"),
        "19": dict(seat="P08", type="number", heading="決定的数値",
                    lines=["必要 476,413円", "手元 133,607円"], sub="R9、強制返済に届かず破産"),
        "20": dict(seat="P03", type="number", heading="決定的数値",
                    lines=["必要 476,413円", "手元 233,607円"], sub="R9、強制返済に届かず破産"),
        "21": dict(seat="P01", type="number", heading="決定的数値",
                    lines=["必要 483,559円", "手元 357,194円"], sub="R10、強制返済に届かず破産"),
        "22": dict(seat="P12", type="number", heading="決定的数値",
                    lines=["最終現金 559,353円", "生還ライン 2,000,000円"], sub="R12、生還条件に届かず"),
        "23": dict(seat="P09", type="number", heading="決定的数値",
                    lines=["最終現金 1,178,511円", "生還ライン 2,000,000円"], sub="R12、生還条件に届かず"),
        "24": dict(seat="P04", type="number", heading="決定的数値",
                    lines=["最終現金 1,934,647円", "不足額 65,353円"], sub="R12、生還ラインまであと一歩"),
        "25": dict(seat="P07", type="number", heading="決定的数値",
                    lines=["R10単独勝利 2,480,000円", "R12空き巣 不発"],
                    sub="生還を決めたのはR10、届かなかったのはR12"),
        "26": dict(seat="P11", type="number", heading="決定的数値",
                    lines=["自動代行 2回(R9・R10)", "R12 ROYAL_FLUSH 2,220,000円"],
                    sub="故障が守った切り札"),
        "27": dict(seat="P02", type="number", heading="決定的数値",
                    lines=["M02 STRAIGHT_FLUSH", "賞金 2,220,000円"], sub="R12、最終ラウンドの一手"),
        "28": dict(seat="P10", type="number", heading="決定的数値",
                    lines=["契約提案 59件", "署名成立 6件"], sub="最多提案、それでも2位で着地"),
        "29": dict(seat="P06", type="number", heading="決定的数値",
                    lines=["R1単独勝利 2,680,000円", "R2倍掛け成功 5,360,000円"],
                    sub="開始2ラウンドで築いた首位の土台"),
    },
}

# order -> (seat, memoir_filename)
MEMOIRS_20260824 = {
    "02": ("P06", "02_memoir_P06.md"),
    "03": ("P09", "03_memoir_P09.md"),
    "04": ("P07", "04_memoir_P07.md"),
    "05": ("P01", "05_memoir_P01.md"),
    "06": ("P04", "06_memoir_P04.md"),
    "07": ("P02", "07_memoir_P02.md"),
}
MEMOIRS_20260830 = {
    "10": ("P06", "10_memoir_P06.md"),
    "11": ("P12", "11_memoir_P12.md"),
    "12": ("P09", "12_memoir_P09.md"),
    "13": ("P02", "13_memoir_P02.md"),
    "14": ("P05", "14_memoir_P05.md"),
    "15": ("P10", "15_memoir_P10.md"),
}

# サイクル10.17: l12r12_dr_1201(12体・実験席混合)の手記12本。
# 並び順=脱落順(早い順)→生還者を最終資産の低い順→首位P06を最後
MEMOIRS_20260921 = {
    "18": ("P05", "18_memoir_P05.md"),
    "19": ("P08", "19_memoir_P08.md"),
    "20": ("P03", "20_memoir_P03.md"),
    "21": ("P01", "21_memoir_P01.md"),
    "22": ("P12", "22_memoir_P12.md"),
    "23": ("P09", "23_memoir_P09.md"),
    "24": ("P04", "24_memoir_P04.md"),
    "25": ("P07", "25_memoir_P07.md"),
    "26": ("P11", "26_memoir_P11.md"),
    "27": ("P02", "27_memoir_P02.md"),
    "28": ("P10", "28_memoir_P10.md"),
    "29": ("P06", "29_memoir_P06.md"),
}

DATE_CONFIG = {
    "20260824": dict(
        log_path=ROOT / "logs/llm/trial_C_l12_r12_20260824/game01_events.jsonl",
        posts_dir=ROOT / "doc/blog/20260824/posts",
        images_dir=ROOT / "doc/blog/20260824/images",
        roster=ROSTER_20260824,
        results=RESULTS_20260824,
        memoirs=MEMOIRS_20260824,
        roster_post="01_system.md",
        results_post="08_summary.md",
        roster_image="01_roster.png",
        results_image="08_results.png",
    ),
    "20260830": dict(
        log_path=ROOT / "logs/llm/trial_C_l12_r12_v08_20260830/game01_events.jsonl",
        posts_dir=ROOT / "doc/blog/20260830/posts",
        images_dir=ROOT / "doc/blog/20260830/images",
        roster=ROSTER_20260830,
        results=RESULTS_20260830,
        memoirs=MEMOIRS_20260830,
        roster_post="09_system.md",
        results_post="16_summary.md",
        roster_image="09_roster.png",
        results_image="16_results.png",
    ),
    "20260921": dict(
        log_path=ROOT / "logs/llm/trial_C_l12r12_dr_1201/game01_events.jsonl",
        posts_dir=ROOT / "doc/blog/20260921/posts",
        images_dir=ROOT / "doc/blog/20260921/images",
        roster=ROSTER_20260921,
        results=RESULTS_20260921,
        memoirs=MEMOIRS_20260921,
        # roster/results 画像は総括記事(17_summary.md)1本に同居(サイクル10.16で既に埋め込み済み・
        # insert_after_lead は冪等なのでここで再実行しても no-op)
        roster_post="17_summary.md",
        results_post="17_summary.md",
        roster_image="17_roster.png",
        results_image="17_results.png",
        # サイクル10.16の /tmp/c1016/gen_images.py と同一のタイトル文言(既存出力を変えないため)
        results_title="9月21日戦（12体・実験席混合） 最終結果",
        roster_title="9月21日戦 対戦表（※=DevRelay実験席）",
    ),
}


_JP_FONT_FILE_HINTS = ("noto", "ipa", "japan", "gothic", "cjk")


def _register_system_jp_fonts() -> None:
    """matplotlib のフォントキャッシュに未登録のCJKフォントファイルを明示的に追加する。

    (fc-list で存在を確認済みの Noto Sans CJK JP / IPAGothic が
     fontManager.ttflist に載っていなかったための補助。engine/ は変更しない。)
    """
    for font_dir in ("/usr/share/fonts", "/usr/local/share/fonts"):
        p = Path(font_dir)
        if not p.exists():
            continue
        for ext in ("*.ttc", "*.ttf", "*.otf"):
            for f in p.rglob(ext):
                if any(h in f.name.lower() for h in _JP_FONT_FILE_HINTS):
                    try:
                        fm.fontManager.addfont(str(f))
                    except Exception:
                        continue


def resolve_font() -> str | None:
    """engine/blog/situation_image._resolve_jp_font() を再利用（import のみ、engine 無変更）。"""
    name = _resolve_jp_font()
    if not name:
        _register_system_jp_fonts()
        name = _resolve_jp_font()
    if name:
        try:
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
        except Exception:
            pass
    return name


# ---------------------------------------------------------------------------
# A: ログ再生によるラウンド別 現金・借金 復元
# ---------------------------------------------------------------------------

def load_events(log_path: Path) -> list[dict]:
    events = []
    with open(log_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def reconstruct_series(events: list[dict], seats: list[str]) -> dict[str, dict]:
    """各席のラウンド別 (cash, debt) 系列と脱落ラウンドを復元する。"""
    state = {pid: {"cash": None, "debt": None} for pid in seats}
    series = {pid: {"rounds": [], "cash": [], "debt": []} for pid in seats}
    elim_round: dict[str, int] = {}

    events_by_round: dict[int, list[dict]] = {}
    for ev in events:
        r = ev.get("round_num")
        if r is None:
            continue
        events_by_round.setdefault(r, []).append(ev)

    for r in sorted(events_by_round):
        round_events = events_by_round[r]
        elim_this_round: dict[str, tuple[int, int]] = {}

        for ev in round_events:
            et = ev["event_type"]
            data = ev.get("data", {})
            if et == "SNAPSHOT" and ev.get("phase") == "settlement":
                for pid, snap in data.get("snapshots", {}).items():
                    if pid in state and pid not in elim_round:
                        state[pid]["cash"] = snap.get("cash")
                        # サイクル10.17: SNAPSHOTのdebt_balanceを基準値として採用する。
                        # negotiationフェーズ中の任意返済(action=repay)はINTEREST/MANDATORY_REPAY
                        # イベントを発生させないため、従来はdebtが最後の値に固定されたまま更新されず
                        # 残ってしまう不具合があった(例: l12r12_dr_1201 P02がR6のrepay 3回で残債務を
                        # 完済したが、以後の借金系列がR5時点の1,885,247円のまま動かなかった)。
                        # 同一ラウンド内で後続のINTEREST/MANDATORY_REPAYが発生すればそちらが上書きする
                        # ため、既存の「debt有プレイヤーの年間推移」には影響しない。
                        if "debt_balance" in snap:
                            state[pid]["debt"] = snap.get("debt_balance")
            elif et == "INTEREST":
                pid = data.get("player_id")
                if pid in state and pid not in elim_round:
                    state[pid]["debt"] = data.get("new_debt")
            elif et in ("MANDATORY_REPAY", "REPAYMENT", "AUTO_REPAYMENT"):
                pid = data.get("player_id")
                if pid in state and pid not in elim_round:
                    if "new_cash" in data:
                        state[pid]["cash"] = data["new_cash"]
                    if "new_debt" in data:
                        state[pid]["debt"] = data["new_debt"]
            elif et == "TYPE_A_EXECUTION":
                # サイクル10.17: 型A決済（純粋な型A支払＋型C発火分の送金）はSNAPSHOT後・
                # MANDATORY_REPAY前に記録される（全ラウンドで共通の順序）。借金が無く
                # MANDATORY_REPAYで上書きされない席（例: l12r12_dr_1201 R12のP02）は
                # このイベントを反映しないとSNAPSHOTの決済前金額のまま最終値がずれるため、
                # obligor(支払)/counterparty(受取)の両方にamountを反映する。
                # MANDATORY_REPAY等の直後の上書きは従来通り優先される（イベント順で後勝ち）。
                obligor = data.get("obligor")
                counterparty = data.get("counterparty")
                amount = data.get("amount", 0)
                if obligor in state and obligor not in elim_round and state[obligor]["cash"] is not None:
                    state[obligor]["cash"] -= amount
                if counterparty in state and counterparty not in elim_round and state[counterparty]["cash"] is not None:
                    state[counterparty]["cash"] += amount
            elif et in ("BANKRUPTCY", "FORCED_LIQUIDATION"):
                pid = data.get("player_id")
                if pid in state and pid not in elim_round:
                    cash_before = data.get("cash_before", state[pid]["cash"] or 0)
                    debt_before = data.get("debt_before", state[pid]["debt"] or 0)
                    elim_this_round[pid] = (cash_before, debt_before)

        for pid in seats:
            if pid in elim_round:
                continue
            if pid in elim_this_round:
                cash_before, debt_before = elim_this_round[pid]
                series[pid]["rounds"].append(r)
                series[pid]["cash"].append(cash_before)
                series[pid]["debt"].append(debt_before)
                elim_round[pid] = r
            elif state[pid]["cash"] is not None:
                series[pid]["rounds"].append(r)
                series[pid]["cash"].append(state[pid]["cash"])
                series[pid]["debt"].append(state[pid]["debt"] or 0)

    for pid in seats:
        series[pid]["elim_round"] = elim_round.get(pid)
    return series


# ---------------------------------------------------------------------------
# 画像生成
# ---------------------------------------------------------------------------

def report(path: Path) -> None:
    size = path.stat().st_size
    print(f"{path.relative_to(ROOT)} ({size:,} bytes)")


def gen_asset_chart(pid: str, order: str, series: dict, out_path: Path) -> None:
    rounds = series["rounds"]
    cash_m = [c / 10_000 for c in series["cash"]]
    debt_m = [d / 10_000 for d in series["debt"]]
    elim_round = series.get("elim_round")

    fig, ax = plt.subplots(figsize=(9, 5), dpi=120)
    ax.plot(rounds, cash_m, marker="o", color="#1f77b4", label="現金")
    ax.plot(rounds, debt_m, marker="o", color="#d62728", linestyle="--", label="借金")

    if elim_round is not None:
        ax.axvline(elim_round, color="#888888", linestyle=":", linewidth=1)
        ax.annotate(f"R{elim_round} 脱落", xy=(elim_round, cash_m[-1]),
                    xytext=(5, 10), textcoords="offset points", color="#d62728",
                    fontsize=11, fontweight="bold")
    else:
        final_cash = series["cash"][-1]
        final_debt = series["debt"][-1]
        ax.annotate(f"{final_cash:,.0f}円 / 借金{final_debt:,.0f}円",
                    xy=(rounds[-1], cash_m[-1]), xytext=(-90, 12),
                    textcoords="offset points", color="#1f77b4", fontsize=10,
                    fontweight="bold")

    ax.set_title(f"{pid} ─ 現金と借金の推移")
    ax.set_xlabel("Round")
    ax.set_ylabel("万円")
    ax.set_xlim(0.5, 12.5)
    ax.set_xticks(range(1, 13))
    ax.legend(loc="upper left")
    ax.grid(alpha=0.3)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    report(out_path)


def gen_moment_card(order: str, card: dict, out_path: Path) -> None:
    fig, ax = plt.subplots(figsize=(12, 6.75), dpi=100)
    ax.axis("off")

    if card["type"] == "lie":
        bg = "#2b0f10"
        fg = "#ff4d4d"
        fig.patch.set_facecolor(bg)
        ax.set_facecolor(bg)
        for spine_pos in [(0.02, 0.02, 0.96, 0.96)]:
            pass
        rect = plt.Rectangle((0.03, 0.03), 0.94, 0.94, fill=False,
                              edgecolor=fg, linewidth=4, transform=ax.transAxes)
        ax.add_patch(rect)
        ax.text(0.5, 0.82, "LIE DETECTED", ha="center", va="center",
                fontsize=40, fontweight="bold", color=fg, transform=ax.transAxes)
        ax.text(0.5, 0.65, card["round_label"], ha="center", va="center",
                fontsize=26, color="#ffffff", transform=ax.transAxes)
        ax.text(0.5, 0.47, f"宣言: {card['statement']}", ha="center", va="center",
                fontsize=16, color="#ffd6d6", transform=ax.transAxes, wrap=True)
        ax.text(0.5, 0.28, f"実際の行動: {card['actual']}", ha="center", va="center",
                fontsize=18, fontweight="bold", color="#ffffff", transform=ax.transAxes,
                wrap=True)
        seat = card["seat"]
        ax.text(0.5, 0.10, seat, ha="center", va="center", fontsize=16,
                color="#ff9999", transform=ax.transAxes)
    else:
        bg = "#0f1f2b"
        fg = "#4dc3ff"
        fig.patch.set_facecolor(bg)
        ax.set_facecolor(bg)
        rect = plt.Rectangle((0.03, 0.03), 0.94, 0.94, fill=False,
                              edgecolor=fg, linewidth=4, transform=ax.transAxes)
        ax.add_patch(rect)
        ax.text(0.5, 0.82, card["heading"], ha="center", va="center",
                fontsize=32, fontweight="bold", color=fg, transform=ax.transAxes)
        n_lines = len(card["lines"])
        y_positions = [0.58, 0.38] if n_lines == 2 else [0.5]
        for line, y in zip(card["lines"], y_positions):
            ax.text(0.5, y, line, ha="center", va="center", fontsize=30,
                    fontweight="bold", color="#ffffff", transform=ax.transAxes)
        if card.get("sub"):
            ax.text(0.5, 0.18, card["sub"], ha="center", va="center", fontsize=16,
                    color="#9fd8ff", transform=ax.transAxes)
        ax.text(0.5, 0.08, card["seat"], ha="center", va="center", fontsize=14,
                color="#7fc3e8", transform=ax.transAxes)

    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)
    report(out_path)


def gen_results_chart(results: list[dict], out_path: Path, title: str) -> None:
    seats = [r["seat"] for r in results]
    values = []
    colors = []
    annotations = []
    round_palette = {2: "#8e44ad", 6: "#c0392b", 9: "#e67e22", 10: "#d35400", 12: "#7f8c8d"}
    for r in results:
        if r["status"] == "survive":
            values.append(r["final_cash"] / 10_000)
            colors.append("#2ca02c")
            annotations.append(f"{r['final_cash']:,.0f}円")
        else:
            values.append(0)
            colors.append(round_palette.get(r["elim_round"], "#7f8c8d"))
            annotations.append(f"R{r['elim_round']} {r['note']}")

    fig, ax = plt.subplots(figsize=(10, 6), dpi=120)
    bars = ax.bar(seats, values, color=colors)
    for bar, ann, val in zip(bars, annotations, values):
        y = val if val > 0 else 0.5
        ax.text(bar.get_x() + bar.get_width() / 2, y + max(values) * 0.02, ann,
                ha="center", va="bottom", fontsize=8, rotation=60)
    ax.set_title(title)
    ax.set_ylabel("最終現金（万円）")
    ax.set_ylim(0, max(values) * 1.35 if max(values) > 0 else 1)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    report(out_path)


def gen_roster_table(roster: list[tuple], out_path: Path, title: str) -> None:
    fig, ax = plt.subplots(figsize=(6, 6), dpi=120)
    ax.axis("off")
    rows = [[pid, model, f"{loan // 10_000}万円"] for pid, model, loan in roster]
    table = ax.table(cellText=rows, colLabels=["席", "モデル", "借入"],
                      loc="center", cellLoc="center")
    table.auto_set_font_size(False)
    table.set_fontsize(11)
    table.scale(1, 1.6)
    ax.set_title(title, pad=20)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    report(out_path)


# ---------------------------------------------------------------------------
# サイクル9.11: 常設ガイド用の図解3枚
# ---------------------------------------------------------------------------

GUIDE_IMAGES_DIR = ROOT / "doc" / "blog" / "guide" / "images"


def gen_guide_flow(out_path: Path) -> None:
    """1ラウンドの流れ（交渉→コミット→公開→決済→財務 の横流れ図）。"""
    steps = [
        ("① 交渉", "他プレイヤーと\n情報交換・駆け引き"),
        ("② コミット", "カードと行動を\n非公開で決定"),
        ("③ 公開", "全員の宣言・行動が\n一斉に開示される"),
        ("④ 決済", "市場の勝敗に応じて\n現金が動く"),
        ("⑤ 財務", "利息・強制返済で\n借金が精算される"),
    ]
    fig, ax = plt.subplots(figsize=(12, 4), dpi=100)
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    n = len(steps)
    box_w = 0.16
    gap = (1 - box_w * n) / (n + 1)
    y = 0.55
    for i, (heading, body) in enumerate(steps):
        x = gap + i * (box_w + gap)
        rect = plt.Rectangle((x, y - 0.18), box_w, 0.36, fill=True,
                              facecolor="#eef4fb", edgecolor="#2b5d8a", linewidth=2)
        ax.add_patch(rect)
        ax.text(x + box_w / 2, y + 0.10, heading, ha="center", va="center",
                fontsize=15, fontweight="bold", color="#1b3a55")
        ax.text(x + box_w / 2, y - 0.06, body, ha="center", va="center",
                fontsize=10, color="#333333")
        if i < n - 1:
            arrow_x0 = x + box_w
            arrow_x1 = arrow_x0 + gap
            ax.annotate("", xy=(arrow_x1, y), xytext=(arrow_x0, y),
                        arrowprops=dict(arrowstyle="->", color="#2b5d8a", linewidth=2))
    ax.set_title("1ラウンドの流れ", fontsize=18, fontweight="bold", pad=16)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    report(out_path)


def gen_guide_money(out_path: Path) -> None:
    """お金の循環（システム／プレイヤー／市場 の三者間の簡易フロー図）。"""
    fig, ax = plt.subplots(figsize=(9, 6.75), dpi=100)
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)

    nodes = {
        "system": (0.5, 0.85, "システム（胴元）"),
        "player": (0.5, 0.45, "プレイヤー"),
        "market": (0.5, 0.08, "市場"),
    }
    for x, y, label in nodes.values():
        rect = plt.Rectangle((x - 0.22, y - 0.08), 0.44, 0.16, fill=True,
                              facecolor="#fff5e6", edgecolor="#a86b1c", linewidth=2)
        ax.add_patch(rect)
        ax.text(x, y, label, ha="center", va="center", fontsize=15,
                fontweight="bold", color="#5a3d0c")

    # システム → プレイヤー（借金）
    ax.annotate("", xy=(0.34, 0.53), xytext=(0.34, 0.77),
                arrowprops=dict(arrowstyle="->", color="#1f77b4", linewidth=2))
    ax.text(0.20, 0.65, "借金", ha="center", va="center", fontsize=11, color="#1f77b4")

    # プレイヤー → システム（Entry Fee・強制返済・利息）
    ax.annotate("", xy=(0.66, 0.77), xytext=(0.66, 0.53),
                arrowprops=dict(arrowstyle="->", color="#d62728", linewidth=2))
    ax.text(0.86, 0.65, "Entry Fee\n強制返済・利息", ha="center", va="center",
            fontsize=10, color="#d62728")

    # 市場 → プレイヤー（賞金）
    ax.annotate("", xy=(0.34, 0.37), xytext=(0.34, 0.16),
                arrowprops=dict(arrowstyle="->", color="#2ca02c", linewidth=2))
    ax.text(0.20, 0.27, "賞金", ha="center", va="center", fontsize=11, color="#2ca02c")

    # プレイヤー間（送金・契約・トレード）
    ax.annotate("", xy=(0.90, 0.45), xytext=(0.72, 0.45),
                arrowprops=dict(arrowstyle="<->", color="#7a4fa3", linewidth=2))
    ax.text(0.90, 0.36, "送金・契約・\nトレード", ha="left", va="center",
            fontsize=10, color="#7a4fa3")
    ax.text(0.10, 0.45, "（プレイヤー間）", ha="right", va="center",
            fontsize=9, color="#7a4fa3")

    ax.set_title("お金の循環", fontsize=18, fontweight="bold", pad=16)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    report(out_path)


def gen_guide_mechanics(out_path: Path) -> None:
    """4つの仕掛け（倍掛け・市場高騰・正式契約・カードトレード）を2×2の枠で。"""
    cells = [
        ("倍掛け", "宣言した行動が的中すると\n利益（または損失）が倍になる"),
        ("市場高騰", "同じ市場に人気が集中すると\n価格が跳ね上がる"),
        ("正式契約", "破れば即死のペナルティ付き\n拘束力のある約束を交わせる"),
        ("カードトレード", "手持ちのカードを\n他プレイヤーと売買できる"),
    ]
    fig, ax = plt.subplots(figsize=(10, 6), dpi=100)
    ax.axis("off")
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    colors = ["#2b5d8a", "#a86b1c", "#8a2b2b", "#3d7a4f"]
    positions = [(0.02, 0.52, 0.46, 0.44), (0.52, 0.52, 0.46, 0.44),
                 (0.02, 0.04, 0.46, 0.44), (0.52, 0.04, 0.46, 0.44)]
    for (heading, body), color, (x, y, w, h) in zip(cells, colors, positions):
        rect = plt.Rectangle((x, y), w, h, fill=True, facecolor="#f7f7f7",
                              edgecolor=color, linewidth=3)
        ax.add_patch(rect)
        ax.text(x + w / 2, y + h - 0.08, heading, ha="center", va="center",
                fontsize=17, fontweight="bold", color=color)
        ax.text(x + w / 2, y + h / 2 - 0.06, body, ha="center", va="center",
                fontsize=12, color="#333333")
    ax.set_title("4つの仕掛け", fontsize=18, fontweight="bold", pad=16)
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)
    report(out_path)


def run_guide() -> None:
    """常設ガイド用の図解3枚を doc/blog/guide/images/ に生成する。"""
    GUIDE_IMAGES_DIR.mkdir(parents=True, exist_ok=True)
    font_name = resolve_font()
    print(f"font: {font_name}")
    gen_guide_flow(GUIDE_IMAGES_DIR / "guide_flow.png")
    gen_guide_money(GUIDE_IMAGES_DIR / "guide_money.png")
    gen_guide_mechanics(GUIDE_IMAGES_DIR / "guide_mechanics.png")


# ---------------------------------------------------------------------------
# 記事への埋め込み
# ---------------------------------------------------------------------------

IMG_RE_TEMPLATE = r"!\[[^\]]*\]\(images/{name}\)"


def insert_after_lead(text: str, image_line: str, image_filename: str) -> str:
    if re.search(IMG_RE_TEMPLATE.format(name=re.escape(image_filename)), text):
        return text
    m = re.match(r"^---\n.*?\n---\n", text, re.S)
    if not m:
        return image_line + "\n\n" + text
    end = m.end()
    rest = text[end:]
    rest = rest.lstrip("\n")
    return text[:end] + "\n" + image_line + "\n\n" + rest


def insert_after_heading(text: str, heading_prefix: str, image_line: str, image_filename: str) -> str:
    if re.search(IMG_RE_TEMPLATE.format(name=re.escape(image_filename)), text):
        return text
    pattern = re.compile(r"^(" + re.escape(heading_prefix) + r".*)$", re.M)
    m = pattern.search(text)
    if not m:
        return text
    line_end = m.end()
    return text[:line_end] + "\n\n" + image_line + text[line_end:]


def embed_images(cfg: dict, date: str) -> None:
    posts_dir = cfg["posts_dir"]

    # D: 対戦表（roster_post 冒頭）
    roster_path = posts_dir / cfg["roster_post"]
    text = roster_path.read_text(encoding="utf-8")
    text = insert_after_lead(text, f"![{date} 対戦表：席・モデル・借入](images/{cfg['roster_image']})",
                              cfg["roster_image"])
    roster_path.write_text(text, encoding="utf-8")

    # C: 結果チャート（results_post 冒頭）
    results_path = posts_dir / cfg["results_post"]
    text = results_path.read_text(encoding="utf-8")
    text = insert_after_lead(text, f"![{date} 最終結果チャート](images/{cfg['results_image']})",
                              cfg["results_image"])
    results_path.write_text(text, encoding="utf-8")

    # A + B: 各手記
    for order, (seat, filename) in cfg["memoirs"].items():
        path = posts_dir / filename
        text = path.read_text(encoding="utf-8")
        asset_img = f"{order}_{seat}_assets.png"
        moment_img = f"{order}_{seat}_moment.png"

        text = insert_after_lead(text, f"![{seat}の現金と借金の推移](images/{asset_img})", asset_img)

        card = MOMENT_CARDS[date][order]
        alt = card.get("round_label") or card.get("heading") or "決定的瞬間"
        text = insert_after_heading(text, "## 転機",
                                     f"![{seat}の決定的瞬間: {alt}](images/{moment_img})",
                                     moment_img)
        path.write_text(text, encoding="utf-8")
        print(f"embedded: {filename}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", choices=["20260824", "20260830", "20260921"])
    parser.add_argument("--guide", action="store_true",
                         help="サイクル9.11: 常設ガイド用の図解3枚を doc/blog/guide/images/ に生成する")
    args = parser.parse_args()

    if args.guide:
        run_guide()
        return
    if not args.date:
        parser.error("--date か --guide のいずれかを指定してください")

    cfg = DATE_CONFIG[args.date]
    images_dir = cfg["images_dir"]
    images_dir.mkdir(parents=True, exist_ok=True)

    font_name = resolve_font()
    print(f"font: {font_name}")

    seats = [pid for pid, _, _ in cfg["roster"]]
    events = load_events(cfg["log_path"])
    series_all = reconstruct_series(events, seats)

    # 検証: R12生還席の最終値をドシエ最終結果（RESULTS_*）と突合
    for r in cfg["results"]:
        if r["status"] != "survive":
            continue
        pid = r["seat"]
        s = series_all[pid]
        if not s["rounds"]:
            print(f"WARNING: {pid} の系列が空です")
            continue
        recon_cash = s["cash"][-1]
        if recon_cash != r["final_cash"]:
            print(f"WARNING: {pid} 最終現金不一致: 復元={recon_cash:,} 記事={r['final_cash']:,}")
        else:
            print(f"OK: {pid} 最終現金一致 {recon_cash:,}円")

    # A: 資産推移（手記12本）
    for order, (seat, _) in cfg["memoirs"].items():
        out = images_dir / f"{order}_{seat}_assets.png"
        gen_asset_chart(seat, order, series_all[seat], out)

    # B: 決定的瞬間カード（手記12本）
    for order, card in MOMENT_CARDS[args.date].items():
        seat = card["seat"]
        out = images_dir / f"{order}_{seat}_moment.png"
        gen_moment_card(order, card, out)

    # C: 結果チャート
    # cfg["results_title"] があれば優先（サイクル10.17で20260921用に追加。既存2日付の分岐は無変更）
    title = cfg.get("results_title") or (
        "8月24日戦 最終結果" if args.date == "20260824" else "8月30日戦（改訂ルール） 最終結果"
    )
    gen_results_chart(cfg["results"], images_dir / cfg["results_image"], title)

    # D: 対戦表
    title2 = cfg.get("roster_title") or f"{args.date} 対戦表"
    gen_roster_table(cfg["roster"], images_dir / cfg["roster_image"], title2)

    # 記事への埋め込み
    embed_images(cfg, args.date)


if __name__ == "__main__":
    main()
