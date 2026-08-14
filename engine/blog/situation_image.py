"""
状況インフォグラフィック画像の生成 (matplotlib)

「一目で試合状況がわかる」共通イメージを PNG で出力する。
- 全プレイヤーの現金推移(ラウンド別)を折れ線で表示
- 生還ライン(現金≧survival_cash)を水平の破線で表示
- 途中破産に X マーカー、最終ラウンドに 生還/脱落 マーカー
- 対象プレイヤー(highlight_pid)は太線＋強調色で表示

日本語フォントが見つかればラベルを日本語に、無ければ英語にフォールバック(豆腐文字回避)。
生成方式(B): matplotlib。数値はトレースの実データのみを使用する。
"""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # ヘッドレス環境
import matplotlib.pyplot as plt
from matplotlib import font_manager


# 探索する日本語対応フォント候補(存在すれば使う)
_JP_FONT_CANDIDATES = [
    "Noto Sans CJK JP", "Noto Sans JP", "IPAexGothic", "IPAGothic",
    "TakaoGothic", "VL Gothic", "Source Han Sans JP", "Yu Gothic",
    "Hiragino Sans", "MS Gothic",
]


def _resolve_jp_font() -> str | None:
    """利用可能な日本語フォント名を返す。無ければ None。"""
    available = {f.name for f in font_manager.fontManager.ttflist}
    for cand in _JP_FONT_CANDIDATES:
        if cand in available:
            return cand
    # ttflistに名前が無くてもfindfontで拾える場合がある
    for cand in _JP_FONT_CANDIDATES:
        try:
            path = font_manager.findfont(cand, fallback_to_default=False)
            if path and Path(path).exists():
                font_manager.fontManager.addfont(path)
                return font_manager.FontProperties(fname=path).get_name()
        except Exception:
            continue
    return None


# 強調色(対象プレイヤー) / その他
_HIGHLIGHT_COLOR = "#e8442c"
_OTHER_CMAP = "tab10"


def generate_situation_image(
    cash_data: dict,
    out_path: str | Path,
    highlight_pid: str | None = None,
    title: str | None = None,
) -> Path:
    """
    現金推移インフォグラフィックを生成して PNG を保存する。

    Args:
        cash_data: round_situation.extract_cash_series() の戻り値
        out_path: 出力PNGパス
        highlight_pid: 太線強調するプレイヤーID(記事の主役)
        title: 図タイトル(未指定なら自動生成)
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    jp_font = _resolve_jp_font()
    if jp_font:
        plt.rcParams["font.family"] = jp_font
    plt.rcParams["axes.unicode_minus"] = False
    jp = jp_font is not None

    def L(ja: str, en: str) -> str:
        return ja if jp else en

    rounds = cash_data["rounds"]
    series = cash_data["series"]
    names = cash_data["names"]
    bankruptcies = cash_data["bankruptcies"]
    final = cash_data["final"]
    survival_cash = cash_data["survival_cash"]

    fig, ax = plt.subplots(figsize=(11, 6.2), dpi=140)
    fig.patch.set_facecolor("#f7f7fb")
    ax.set_facecolor("#ffffff")

    cmap = plt.get_cmap(_OTHER_CMAP)
    pids = sorted(series.keys())

    def to_million(v):
        return None if v is None else v / 1_000_000.0

    # --- 各プレイヤーの折れ線 ---
    for i, pid in enumerate(pids):
        is_hi = pid == highlight_pid
        ys = [to_million(v) for v in series[pid]]
        xs = [r for r, y in zip(rounds, ys) if y is not None]
        yv = [y for y in ys if y is not None]
        if not xs:
            continue
        label = f"{pid} {names.get(pid, pid)}"
        color = _HIGHLIGHT_COLOR if is_hi else cmap(i % 10)
        ax.plot(
            xs, yv,
            marker="o", markersize=5 if is_hi else 3.5,
            linewidth=3.4 if is_hi else 1.6,
            color=color, alpha=1.0 if is_hi else 0.75,
            zorder=6 if is_hi else 3,
            label=label + (" ★" if is_hi else ""),
        )

        # 破産マーカー(X): 破産ラウンドの残高、無ければ最後に判明した残高位置
        if pid in bankruptcies:
            br = bankruptcies[pid]
            if br in xs:
                bx, by = br, yv[xs.index(br)]
            else:
                bx, by = xs[-1], yv[-1]
            ax.scatter([bx], [by], marker="X", s=150, color="#222",
                       zorder=10, edgecolors="white", linewidths=1.0)

        # 最終ラウンドの生還/脱落マーカー
        if pid in final and xs:
            res = final[pid].get("result")
            fx, fy = xs[-1], yv[-1]
            if res == "survived":
                ax.scatter([fx], [fy], marker="*", s=260, color="#1a9d4a",
                           zorder=9, edgecolors="white", linewidths=1.0)
            elif res == "eliminated":
                ax.scatter([fx], [fy], marker="v", s=110, color="#b23",
                           zorder=7, edgecolors="white", linewidths=0.8)

    # --- 生還ライン ---
    ax.axhline(
        survival_cash / 1_000_000.0,
        linestyle="--", color="#1a9d4a", linewidth=1.8, alpha=0.9, zorder=2,
    )
    ax.text(
        rounds[0], survival_cash / 1_000_000.0,
        L(f"  生還ライン {survival_cash/1_000_000:.0f}M円",
          f"  Survival line {survival_cash/1_000_000:.0f}M"),
        color="#137a38", va="bottom", ha="left", fontsize=10, fontweight="bold",
    )

    # --- 装飾 ---
    if title is None:
        title = L("試合状況: 現金推移と生還ライン", "Match Overview: Cash Trajectory & Survival Line")
    ax.set_title(title, fontsize=15, fontweight="bold", pad=14)
    ax.set_xlabel(L("ラウンド", "Round"), fontsize=11)
    ax.set_ylabel(L("自由資金(百万円)", "Free cash (million yen)"), fontsize=11)
    ax.set_xticks(rounds)
    ax.grid(True, linestyle=":", alpha=0.45)
    ax.margins(x=0.02)

    leg = ax.legend(
        loc="upper left", bbox_to_anchor=(1.01, 1.0),
        fontsize=9, frameon=True, title=L("プレイヤー", "Players"),
    )
    if leg.get_title():
        leg.get_title().set_fontsize(10)

    # 凡例注記
    note = L("★=生還  ▽=脱落(最終)  ✕=途中破産",
             "*=survived  v=eliminated(final)  X=bankruptcy")
    fig.text(0.012, 0.015, note, fontsize=9, color="#444")

    fig.tight_layout(rect=(0, 0.03, 1, 1))
    fig.savefig(out_path, facecolor=fig.get_facecolor())
    plt.close(fig)
    return out_path
