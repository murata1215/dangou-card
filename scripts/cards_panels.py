#!/usr/bin/env python3
"""
「嘘八百万 —談合カード—」カード解説イラスト（5枚）生成スクリプト。

図解記事（scripts/rules_panels.py の5枚）の"続編"として、カード（役）そのものの
見方を説明する図解セット。役の強さ・手札構成・勝敗・引き分け・使い切りを扱う。

構成（doc/uso8000000_dangou_card_spec_v0_5_frozen.md 準拠）:
  ① 役の強さで勝負      … §3.1（10種のランク）
  ② 手札は全員共通12枚   … §3.1-3.2（内訳・毎R1枚）
  ③ 最高ランクが総取り   … §4.5（勝敗判定）
  ④ マーク無関係／同役は山分け … §4.6（同ランク均等分配）
  ⑤ カードは使い切り     … §3.2, §4.5（使用済みは消滅）

配色・フォント・描画ヘルパーは scripts/rules_panels.py / engine/blog/cards_svg.py
を流用し、図解記事とビジュアルトーンを完全に揃える。PNG化は CardRenderer を再利用。

出力:
    .devrelay-output/cards_panel_1.svg … cards_panel_5.svg
    .devrelay-output/cards_panel_1.png … cards_panel_5.png
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.blog.cards_svg import (  # noqa: E402
    BG, BG2, PANEL, EDGE, ACCENT, ACCENT_HI, TEXT, SUBTEXT, SURVIVE, DANGER,
    FONT_STACK, _esc, _svg_open, _suit_decor,
)
from engine.blog.cards_html import CardRenderer  # noqa: E402

# scripts/ 内の描画ヘルパーを流用（script-dir が sys.path[0] にある前提）
from rules_panels import (  # noqa: E402
    W, H, OUT_DIR, RANKS,
    _frame, _header, _arrow, _ai_icon, _dot, _card_chip, _card_back, _market_box,
)

# 役ランキング（強い順の10種）。§3.1。表示用の短縮名＋日本語名。
RANK_LADDER = [
    (10, "ROYAL", "ロイヤル"),
    (9, "S.FLUSH", "ストフラ"),
    (8, "4KIND", "フォーカード"),
    (7, "FULL H", "フルハウス"),
    (6, "FLUSH", "フラッシュ"),
    (5, "STRAIGHT", "ストレート"),
    (4, "3KIND", "スリーカード"),
    (3, "2PAIR", "ツーペア"),
    (2, "1PAIR", "ワンペア"),
    (1, "HIGH", "ハイカード"),
]

TOTAL_PAGES = 5


# ---------------------------------------------------------------------------
# 共通ヘルパー（カード編フッター）
# ---------------------------------------------------------------------------

def _footer(note: str, page: int) -> list[str]:
    s = []
    s.append(f'<text x="44" y="{H-30}" font-family="{FONT_STACK}" font-size="20" '
             f'fill="{SUBTEXT}">{_esc(note)}</text>')
    s.append(f'<text x="{W-40}" y="{H-30}" font-family="{FONT_STACK}" font-size="20" '
             f'fill="{SUBTEXT}" text-anchor="end">'
             f'嘘八百万 —談合カード— カードの見方 {page}/{TOTAL_PAGES}</text>')
    return s


def _keybox(x: float, y: float, w: float, h: float, text: str,
            color: str = ACCENT) -> list[str]:
    """要点を囲む横長ハイライトボックス（1行）。"""
    return [
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="16" '
        f'fill="{BG2}" stroke="{color}" stroke-width="2"/>',
        f'<text x="{x+w/2:.1f}" y="{y+h/2:.1f}" font-family="{FONT_STACK}" font-size="24" '
        f'font-weight="900" fill="{color}" text-anchor="middle" '
        f'dominant-baseline="central">{_esc(text)}</text>',
    ]


def _suit(cx: float, cy: float, size: float, glyph: str, red: bool = False) -> str:
    col = DANGER if red else TEXT
    return (f'<text x="{cx:.1f}" y="{cy:.1f}" font-family="{FONT_STACK}" '
            f'font-size="{size:.0f}" fill="{col}" text-anchor="middle" '
            f'dominant-baseline="central">{_esc(glyph)}</text>')


def _pool_box(cx: float, cy: float, w: float, h: float, amount: str,
              label: str = "賞金プール") -> list[str]:
    return [
        f'<rect x="{cx-w/2:.1f}" y="{cy-h/2:.1f}" width="{w:.1f}" height="{h:.1f}" '
        f'rx="18" fill="{ACCENT}"/>',
        f'<text x="{cx:.1f}" y="{cy-h*0.16:.1f}" font-family="{FONT_STACK}" '
        f'font-size="22" font-weight="900" fill="{BG}" text-anchor="middle" '
        f'dominant-baseline="central">{_esc(label)}</text>',
        f'<text x="{cx:.1f}" y="{cy+h*0.22:.1f}" font-family="{FONT_STACK}" '
        f'font-size="38" font-weight="900" fill="{BG}" text-anchor="middle" '
        f'dominant-baseline="central">{_esc(amount)}</text>',
    ]


# ---------------------------------------------------------------------------
# ① 役の強さで勝負
# ---------------------------------------------------------------------------

def build_panel_1() -> str:
    s = _frame()
    s += _header("CARD 1", "カードは「ポーカーの役」の強さで勝負",
                 "役ランキング／全10種")

    cw, ch, gap = 100, 130, 8
    total = cw * 10 + gap * 9
    x0 = (W - total) / 2
    y0 = 150
    for i, (rn, rl, jp) in enumerate(RANK_LADDER):
        x = x0 + i * (cw + gap)
        s.append(_card_chip(x, y0, cw, ch, rn, rl, highlight=(i == 0)))
        s.append(f'<text x="{x+cw/2:.1f}" y="{y0+ch+22:.1f}" font-family="{FONT_STACK}" '
                 f'font-size="15" fill="{SUBTEXT}" text-anchor="middle">{_esc(jp)}</text>')

    bar_y = y0 + ch + 54
    s.append(f'<text x="{x0:.1f}" y="{bar_y:.1f}" font-family="{FONT_STACK}" font-size="22" '
             f'font-weight="900" fill="{ACCENT_HI}" text-anchor="start" '
             f'dominant-baseline="central">◀ 強い</text>')
    s.append(f'<text x="{x0+total:.1f}" y="{bar_y:.1f}" font-family="{FONT_STACK}" '
             f'font-size="22" font-weight="900" fill="{SUBTEXT}" text-anchor="end" '
             f'dominant-baseline="central">弱い ▶</text>')
    s.append(f'<line x1="{x0+70:.1f}" y1="{bar_y:.1f}" x2="{x0+total-70:.1f}" '
             f'y2="{bar_y:.1f}" stroke="{EDGE}" stroke-width="2"/>')

    s += _keybox(150, 452, W - 300, 92,
                 "提出した役の「ランク（強さ）」だけで勝敗が決まる。数字が大きいほど強い。")

    s += _footer("役は全10種。強い役ほど「どこで切るか」が悩ましくなる", 1)
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# ② 手札は全員共通12枚
# ---------------------------------------------------------------------------

def build_panel_2() -> str:
    s = _frame()
    s += _header("CARD 2", "手札は全員共通の12枚。内訳はこう",
                 "手札構成／引き運の差はゼロ")

    cw, ch, gap = 88, 120, 8
    total = cw * 12 + gap * 11
    x0 = (W - total) / 2
    y0 = 140
    dup_idx = []
    for i, (rn, rl) in enumerate(RANKS):
        x = x0 + i * (cw + gap)
        dup = rn <= 2  # ワンペア(2)・ハイカード(1) が2枚ずつ
        if dup:
            dup_idx.append(i)
        s.append(_card_chip(x, y0, cw, ch, rn, rl, highlight=dup))

    # 2枚ずつの4枚（末尾）にブラケット
    bx0 = x0 + dup_idx[0] * (cw + gap)
    bx1 = x0 + dup_idx[-1] * (cw + gap) + cw
    by = y0 + ch + 18
    s.append(f'<path d="M {bx0:.1f} {by:.1f} L {bx0:.1f} {by+12:.1f} '
             f'L {bx1:.1f} {by+12:.1f} L {bx1:.1f} {by:.1f}" '
             f'fill="none" stroke="{ACCENT_HI}" stroke-width="2.5"/>')
    s.append(f'<text x="{(bx0+bx1)/2:.1f}" y="{by+38:.1f}" font-family="{FONT_STACK}" '
             f'font-size="19" font-weight="900" fill="{ACCENT_HI}" text-anchor="middle">'
             f'この「ワンペア」と「ハイカード」だけ2枚ずつ</text>')

    s += _keybox(150, 372, W - 300, 84,
                 "ハイカード×2・ワンペア×2、ツーペア〜ロイヤルは各1枚 ＝ 合計12枚")
    s.append(f'<text x="{W/2}" y="490" font-family="{FONT_STACK}" font-size="21" '
             f'font-weight="700" fill="{TEXT}" text-anchor="middle">'
             f'毎ラウンド必ず1枚を使い、12ラウンドで手札を使い切る（パスなし）</text>')
    s.append(f'<text x="{W/2}" y="524" font-family="{FONT_STACK}" font-size="19" '
             f'fill="{SUBTEXT}" text-anchor="middle">'
             f'同じ役で並びやすい2種があるから、「山分け」と「裏切り」が生まれる</text>')

    s += _footer("配られる12枚は全員同一。差がつくのは引きではなく"
                 "「使い方」だけ", 2)
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# ③ 最高ランクが総取り
# ---------------------------------------------------------------------------

def build_panel_3() -> str:
    s = _frame()
    s += _header("CARD 3", "役を出し合い、最高ランクが賞金を総取り",
                 "勝敗の決め方")

    s += _pool_box(W / 2, 150, 320, 84, "72.5万円", label="賞金プール（例）")

    y_ai = 300
    xs = [220, 460, 740, 980]
    data = [
        ("参加者A", 5, "STRAIGHT", True),
        ("参加者B", 4, "3KIND", False),
        ("参加者C", 2, "1PAIR", False),
        ("参加者D", 1, "HIGH", False),
    ]
    for x, (lb, rn, rl, win) in zip(xs, data):
        ring = SURVIVE if win else DANGER
        s.append(_ai_icon(x, y_ai, 30, lb, ring=ring, dim=not win))
        s.append(_card_chip(x - 42, y_ai + 56, 84, 58, rn, rl,
                            highlight=win, dim=not win))

    # 勝者Aへプールの矢印
    s.append(_arrow(W / 2 + (220 - W / 2) * 0.4, 190, 220, y_ai - 44,
                    color=SURVIVE, width=3))
    s.append(f'<rect x="168" y="{y_ai+124:.1f}" width="104" height="38" rx="12" '
             f'fill="none" stroke="{SURVIVE}" stroke-width="2"/>')
    s.append(f'<text x="220" y="{y_ai+143:.1f}" font-family="{FONT_STACK}" '
             f'font-size="20" font-weight="900" fill="{SURVIVE}" text-anchor="middle" '
             f'dominant-baseline="central">総取り</text>')
    for x in (460, 740, 980):
        s.append(f'<text x="{x}" y="{y_ai+134:.1f}" font-family="{FONT_STACK}" '
                 f'font-size="20" font-weight="700" fill="{DANGER}" '
                 f'text-anchor="middle">取り分なし</text>')

    s.append(f'<text x="{W/2}" y="520" font-family="{FONT_STACK}" font-size="23" '
             f'font-weight="900" fill="{ACCENT_HI}" text-anchor="middle">'
             f'いちばん強い役（STRAIGHT）を出した参加者Aが、賞金プールを総取り</text>')
    s.append(f'<text x="{W/2}" y="552" font-family="{FONT_STACK}" font-size="19" '
             f'fill="{SUBTEXT}" text-anchor="middle">'
             f'ランクが1つでも上なら勝ち。負けた側の役は関係なく、勝者がすべて得る</text>')

    s += _footer("勝敗は「役のランク比較」だけ。カードの数字の細かさは見ない", 3)
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# ④ マーク無関係／同役は山分け
# ---------------------------------------------------------------------------

def build_panel_4() -> str:
    s = _frame()
    s += _header("CARD 4", "マークは無関係。同じ役なら「山分け」",
                 "引き分けのルール")

    # 上段：スート4種は勝敗に無関係
    suits = [("\u2660", False), ("\u2665", True), ("\u2666", True), ("\u2663", False)]
    sx0 = W / 2 - (len(suits) - 1) * 70 / 2
    for i, (g, red) in enumerate(suits):
        s.append(_suit(sx0 + i * 70, 136, 44, g, red=red))
    s.append(f'<text x="{W/2}" y="180" font-family="{FONT_STACK}" font-size="21" '
             f'font-weight="700" fill="{TEXT}" text-anchor="middle">'
             f'スート（♠♥♦♣）は飾り。勝敗にはいっさい関係しない</text>')

    # 賞金プール（例）
    s += _pool_box(W / 2, 250, 300, 76, "72.5万円", label="賞金プール（例）")

    # 下段：同じ ONE PAIR の2人が山分け
    y_ai = 378
    xs = [340, 860]
    for x in xs:
        s.append(_ai_icon(x, y_ai, 28, "参加者（ワンペア）", ring=SURVIVE))
        s.append(_card_chip(x - 42, y_ai + 52, 84, 54, 2, "1PAIR", highlight=True))
        s.append(_arrow(W / 2 + (x - W / 2) * 0.35, 292, x, y_ai - 40,
                        color=SURVIVE, width=3))
        s.append(f'<text x="{x}" y="{y_ai+124:.1f}" font-family="{FONT_STACK}" '
                 f'font-size="22" font-weight="900" fill="{SURVIVE}" text-anchor="middle" '
                 f'dominant-baseline="central">36.25万円ずつ</text>')
    s.append(f'<text x="{W/2}" y="{y_ai:.1f}" font-family="{FONT_STACK}" font-size="40" '
             f'font-weight="900" fill="{ACCENT}" text-anchor="middle" '
             f'dominant-baseline="central">＝</text>')

    s += _keybox(150, 524, W - 300, 48,
                 "最高ランクが同じなら引き分け＝賞金プールを均等に山分け（端数切捨て）")

    s += _footer("同じ役同士は勝負がつかない。だから2枚ある役ほど"
                 "「談合」の温床になる", 4)
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# ⑤ カードは使い切り
# ---------------------------------------------------------------------------

def build_panel_5() -> str:
    s = _frame()
    s += _header("CARD 5", "使ったカードは消える。12枚で12ラウンド",
                 "カードは使い切り")

    # 手札カウントダウン（使用済みは薄く）
    cw, ch, gap = 82, 56, 8
    total = cw * 12 + gap * 11
    cx0 = (W - total) / 2
    cy0 = 138
    used = 5
    for i, (rn, rl) in enumerate(RANKS):
        s.append(_card_chip(cx0 + i * (cw + gap), cy0, cw, ch, rn, rl, dim=i < used))
    s.append(f'<text x="{W/2}" y="{cy0+ch+32:.1f}" font-family="{FONT_STACK}" '
             f'font-size="20" fill="{SUBTEXT}" text-anchor="middle">'
             f'薄い札＝使用済み。使ったカードは勝っても負けても消える（R6時点のイメージ）</text>')

    # R1〜R12 タイムライン
    tl_y = cy0 + ch + 84
    s.append(f'<line x1="90" y1="{tl_y:.1f}" x2="{W-90}" y2="{tl_y:.1f}" '
             f'stroke="{EDGE}" stroke-width="3"/>')
    for i in range(12):
        x = 90 + i * (W - 180) / 11
        s.append(_dot(x, tl_y, 8, ACCENT if i < 6 else SUBTEXT))
        s.append(f'<text x="{x:.1f}" y="{tl_y+28:.1f}" font-family="{FONT_STACK}" '
                 f'font-size="16" fill="{SUBTEXT}" text-anchor="middle">R{i+1}</text>')

    # 左右の注記ボックス
    by = tl_y + 66
    box_w, box_h = 500, 120
    s.append(f'<rect x="90" y="{by:.1f}" width="{box_w}" height="{box_h}" rx="16" '
             f'fill="{BG2}" stroke="{DANGER}" stroke-width="2"/>')
    s.append(f'<text x="114" y="{by+38:.1f}" font-family="{FONT_STACK}" font-size="22" '
             f'font-weight="900" fill="{DANGER}">使ったカードは消滅</text>')
    s.append(f'<text x="114" y="{by+76:.1f}" font-family="{FONT_STACK}" font-size="18" '
             f'fill="{SUBTEXT}">勝敗に関わらず消える。他のAIへ移ることもない</text>')

    rx0 = W - 90 - box_w
    s.append(f'<rect x="{rx0:.1f}" y="{by:.1f}" width="{box_w}" height="{box_h}" rx="16" '
             f'fill="{BG2}" stroke="{SURVIVE}" stroke-width="2"/>')
    s.append(f'<text x="{rx0+24:.1f}" y="{by+38:.1f}" font-family="{FONT_STACK}" '
             f'font-size="22" font-weight="900" fill="{SURVIVE}">温存か、切るか</text>')
    s.append(f'<text x="{rx0+24:.1f}" y="{by+76:.1f}" font-family="{FONT_STACK}" '
             f'font-size="18" fill="{SUBTEXT}">強い役をどのラウンドで出すかが最大の駆け引き</text>')

    s += _footer("R12で全員が手札ゼロ。温存も浪費もできない、有限の12枚", 5)
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

PANELS = [build_panel_1, build_panel_2, build_panel_3, build_panel_4, build_panel_5]


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    svg_paths = []
    for i, builder in enumerate(PANELS, start=1):
        svg = builder()
        svg_path = OUT_DIR / f"cards_panel_{i}.svg"
        svg_path.write_text(svg, encoding="utf-8")
        svg_paths.append(svg_path)
        print(f"wrote {svg_path}")

    with CardRenderer(scale=2) as renderer:
        for i, svg_path in enumerate(svg_paths, start=1):
            svg = svg_path.read_text(encoding="utf-8")
            html = (
                f'<!DOCTYPE html><html><head><meta charset="utf-8">'
                f'<style>html,body{{margin:0;padding:0;background:{BG};}}</style>'
                f'</head><body>{svg}</body></html>'
            )
            out_png = OUT_DIR / f"cards_panel_{i}.png"
            renderer.render_html(html, W, H, out_png, clip="svg")
            print(f"wrote {out_png}")


if __name__ == "__main__":
    main()
