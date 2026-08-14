#!/usr/bin/env python3
"""
「嘘八百万 —談合カード—」ルール説明イラスト（5枚）生成スクリプト。

ブログの「オープニング記事」と「各AIの個別記事」の間に挟む、
1ラウンドの流れを一目で説明する図解セット。

構成（doc/uso8000000_dangou_card_spec_v0_5_frozen.md 準拠）:
  ① 3つの市場が開く      … §4.1
  ② 手札と参加費を伏せて出す … §4.2-4.4, §3.1-3.2
  ③ 参加費がプールを太らせる … §4.2（ハニーポット）
  ④ 同点は山分け（分割）    … §4.5-4.6, §3.1（ONE PAIR/HIGH CARDが2枚）
  ⑤ これを12回          … §3.2, §2.3（複利）, §1.1（生還条件）

配色・フォント・スート装飾は engine/blog/cards_svg.py のパレットを流用し、
既存カード群とビジュアルトーンを揃える。PNG化は engine/blog/cards_html.py の
CardRenderer（Chromium）を再利用する（このホストは Noto Sans CJK JP が
システムにインストール済みのため、Chromium 経由なら日本語が正しく描画される）。

出力:
    .devrelay-output/rules_panel_1.svg … rules_panel_5.svg
    .devrelay-output/rules_panel_1.png … rules_panel_5.png
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.blog.cards_svg import (  # noqa: E402
    BG, BG2, PANEL, EDGE, ACCENT, ACCENT_HI, TEXT, SUBTEXT, SURVIVE, DANGER,
    FONT_STACK, _esc, _svg_open, _suit_decor,
)
from engine.blog.cards_html import CardRenderer  # noqa: E402

OUT_DIR = PROJECT_ROOT / ".devrelay-output"
W, H = 1200, 630

# ゲーム上の役ランク（§3.1）。表示用に短縮名。
RANKS = [
    (10, "ROYAL"), (9, "S.FLUSH"), (8, "4KIND"), (7, "FULL H"),
    (6, "FLUSH"), (5, "STRAIGHT"), (4, "3KIND"), (3, "2PAIR"),
    (2, "1PAIR"), (2, "1PAIR"), (1, "HIGH"), (1, "HIGH"),
]


# ---------------------------------------------------------------------------
# 共通ヘルパー
# ---------------------------------------------------------------------------

def _frame() -> list[str]:
    """背景・外枠・スート透かしの共通土台。"""
    s = [_svg_open(W, H)]
    s.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
    s.append(_suit_decor(W, H, opacity=0.05))
    s.append(f'<rect x="16" y="16" width="{W-32}" height="{H-32}" rx="22" '
             f'fill="none" stroke="{EDGE}" stroke-width="2"/>')
    return s


def _header(step: str, title: str, sub: str = "") -> list[str]:
    """左上ステップ番号バッジ＋タイトル＋サブコピー。"""
    s = []
    s.append(f'<rect x="16" y="16" width="{W-32}" height="86" rx="22" fill="#08080a"/>')
    s.append(f'<rect x="16" y="94" width="{W-32}" height="4" fill="{ACCENT}"/>')
    badge_w = 132
    s.append(f'<rect x="40" y="30" width="{badge_w}" height="52" rx="14" fill="{ACCENT}"/>')
    s.append(f'<text x="{40+badge_w/2:.0f}" y="57" font-family="{FONT_STACK}" font-size="25" '
             f'font-weight="900" fill="{BG}" text-anchor="middle" '
             f'dominant-baseline="central">{_esc(step)}</text>')
    title_x = 40 + badge_w + 28
    s.append(f'<text x="{title_x}" y="60" font-family="{FONT_STACK}" font-size="30" '
             f'font-weight="900" fill="{ACCENT_HI}" dominant-baseline="central">'
             f'{_esc(title)}</text>')
    if sub:
        s.append(f'<text x="{W-40}" y="60" font-family="{FONT_STACK}" font-size="22" '
                 f'fill="{SUBTEXT}" text-anchor="end" dominant-baseline="central">'
                 f'{_esc(sub)}</text>')
    return s


def _footer(note: str, page: int) -> list[str]:
    s = []
    s.append(f'<text x="44" y="{H-30}" font-family="{FONT_STACK}" font-size="20" '
             f'fill="{SUBTEXT}">{_esc(note)}</text>')
    s.append(f'<text x="{W-40}" y="{H-30}" font-family="{FONT_STACK}" font-size="20" '
             f'fill="{SUBTEXT}" text-anchor="end">嘘八百万 —談合カード— ルール解説 {page}/5</text>')
    return s


def _arrow(x1: float, y1: float, x2: float, y2: float, color: str = ACCENT,
           width: float = 3, head: float = 11) -> str:
    ang = math.atan2(y2 - y1, x2 - x1)
    a1, a2 = ang + math.radians(150), ang - math.radians(150)
    p1 = (x2 + head * math.cos(a1), y2 + head * math.sin(a1))
    p2 = (x2 + head * math.cos(a2), y2 + head * math.sin(a2))
    return (
        f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
        f'stroke="{color}" stroke-width="{width}"/>'
        f'<polygon points="{x2:.1f},{y2:.1f} {p1[0]:.1f},{p1[1]:.1f} '
        f'{p2[0]:.1f},{p2[1]:.1f}" fill="{color}"/>'
    )


def _ai_icon(cx: float, cy: float, r: float, label: str, ring: str = ACCENT,
             dim: bool = False) -> str:
    """人型シルエットのアイコン（頭＝丸＋肩＝半ドーム）。labelは肩の下にキャプション。"""
    op = 0.35 if dim else 1.0
    rh = r * 0.5          # 頭の半径
    hy = cy - r * 0.42    # 頭の中心y
    bw = r * 1.75         # 肩幅
    by = cy + r * 0.98    # 肩の下端y
    top = cy + r * 0.06   # 肩ドームの頂点y
    lab_col = SUBTEXT if dim else ring
    parts = [
        f'<g opacity="{op}">',
        # 肩（半ドーム）
        f'<path d="M {cx-bw/2:.1f} {by:.1f} '
        f'A {bw/2:.1f} {by-top:.1f} 0 0 1 {cx+bw/2:.1f} {by:.1f} Z" '
        f'fill="{PANEL}" stroke="{ring}" stroke-width="2.5"/>',
        # 頭
        f'<circle cx="{cx:.1f}" cy="{hy:.1f}" r="{rh:.1f}" '
        f'fill="{PANEL}" stroke="{ring}" stroke-width="2.5"/>',
        '</g>',
    ]
    if label:
        parts.append(
            f'<text x="{cx:.1f}" y="{by+r*0.7:.1f}" font-family="{FONT_STACK}" '
            f'font-size="{max(15.0, r*0.6):.0f}" font-weight="700" fill="{lab_col}" '
            f'text-anchor="middle" opacity="{op}">{_esc(label)}</text>'
        )
    return "".join(parts)


def _dot(cx: float, cy: float, r: float, fill: str) -> str:
    return f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="{fill}"/>'


def _card_chip(x: float, y: float, w: float, h: float, rank_num: int, rank_label: str,
               highlight: bool = False, dim: bool = False) -> str:
    fill = ACCENT if highlight else PANEL
    text_col = BG if highlight else TEXT
    stroke = ACCENT_HI if highlight else EDGE
    op = 0.4 if dim else 1.0
    return (
        f'<g opacity="{op}">'
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="8" '
        f'fill="{fill}" stroke="{stroke}" stroke-width="2"/>'
        f'<text x="{x+w/2:.1f}" y="{y+h*0.42:.1f}" font-family="{FONT_STACK}" '
        f'font-size="{h*0.34:.0f}" font-weight="900" fill="{text_col}" '
        f'text-anchor="middle" dominant-baseline="central">{rank_num}</text>'
        f'<text x="{x+w/2:.1f}" y="{y+h*0.76:.1f}" font-family="{FONT_STACK}" '
        f'font-size="{h*0.18:.0f}" fill="{text_col}" text-anchor="middle" '
        f'dominant-baseline="central">{_esc(rank_label)}</text>'
        f'</g>'
    )


def _card_back(x: float, y: float, w: float, h: float) -> str:
    return (
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="8" '
        f'fill="{BG2}" stroke="{ACCENT}" stroke-width="2" stroke-dasharray="4 3"/>'
        f'<text x="{x+w/2:.1f}" y="{y+h/2:.1f}" font-family="{FONT_STACK}" '
        f'font-size="{h*0.5:.0f}" font-weight="900" fill="{ACCENT}" '
        f'text-anchor="middle" dominant-baseline="central">?</text>'
    )


def _market_box(x: float, y: float, w: float, h: float, label: str, prize: str,
                 emphasis: bool = False) -> list[str]:
    stroke = ACCENT_HI if emphasis else ACCENT
    sw = 4 if emphasis else 2
    s = [
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="16" '
        f'fill="{BG2}" stroke="{stroke}" stroke-width="{sw}"/>',
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="40" rx="16" fill="#08080a"/>',
        f'<rect x="{x:.1f}" y="{y+30:.1f}" width="{w:.1f}" height="4" fill="{stroke}"/>',
        f'<text x="{x+w/2:.1f}" y="{y+24:.1f}" font-family="{FONT_STACK}" font-size="22" '
        f'font-weight="900" fill="{ACCENT_HI}" text-anchor="middle" '
        f'dominant-baseline="central">{_esc(label)}</text>',
        f'<text x="{x+w/2:.1f}" y="{y+h*0.62:.1f}" font-family="{FONT_STACK}" font-size="30" '
        f'fill="{SUBTEXT}" text-anchor="middle">賞金</text>',
        f'<text x="{x+w/2:.1f}" y="{y+h*0.86:.1f}" font-family="{FONT_STACK}" font-size="44" '
        f'font-weight="900" fill="{TEXT}" text-anchor="middle">{_esc(prize)}</text>',
    ]
    return s


# ---------------------------------------------------------------------------
# ① 3つの市場が開く
# ---------------------------------------------------------------------------

def build_panel_1() -> str:
    s = _frame()
    s += _header("STEP 1", "毎ラウンド、賞金つきの市場が3つ開く",
                 "R1の例／全12ラウンド")

    bw, bh, gap = 300, 190, 60
    total = bw * 3 + gap * 2
    x0 = (W - total) / 2
    y0 = 130
    markets = [("M1", "40万円", False), ("M2", "32.5万円", True), ("M3", "47.5万円", False)]
    for i, (label, prize, emph) in enumerate(markets):
        x = x0 + i * (bw + gap)
        s += _market_box(x, y0, bw, bh, label, prize, emphasis=emph)
    s.append(f'<text x="{W/2}" y="{y0+bh+34}" font-family="{FONT_STACK}" font-size="22" '
             f'fill="{SUBTEXT}" text-anchor="middle">この後の②〜④は、真ん中の M2 を例に説明します</text>')

    # 8体のAI（1列×8）
    ay0 = y0 + bh + 70
    n = 8
    cols = 8
    spacing = (W - 200) / (cols - 1)
    for i in range(n):
        row, col = divmod(i, cols)
        cx = 100 + col * spacing
        cy = ay0 + row * 30
        s.append(_dot(cx, cy, 9, ACCENT if row == 0 else SUBTEXT))
    s.append(f'<text x="{W/2}" y="{ay0+70}" font-family="{FONT_STACK}" font-size="24" '
             f'font-weight="700" fill="{TEXT}" text-anchor="middle">'
             f'8体のAIが全員、いずれかの市場に必ず参加する（パスなし）</text>')

    s += _footer("賞金総額は開始時に公開（例：総額1920万円を12ラウンドに逓増配分）", 1)
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# ② 手札と参加費を伏せて出す
# ---------------------------------------------------------------------------

def build_panel_2() -> str:
    s = _frame()
    s += _header("STEP 2", "市場・手札1枚・参加費10万円を、伏せて提出",
                 "秘密コミット")

    # 4人の参加者が、伏せたカード＋参加費タグを市場へ提出
    y_ai = 110
    xs = [200, 430, 660, 890]
    labels = ["参加者A", "参加者B", "参加者C", "参加者D"]
    for x, lb in zip(xs, labels):
        s.append(_ai_icon(x, y_ai, 28, lb))
        s.append(_card_back(x - 28, y_ai + 62, 56, 76))
        s.append(f'<rect x="{x-38:.1f}" y="{y_ai+148:.1f}" width="76" height="30" rx="14" '
                 f'fill="none" stroke="{ACCENT}" stroke-width="2"/>')
        s.append(f'<text x="{x:.1f}" y="{y_ai+163:.1f}" font-family="{FONT_STACK}" '
                 f'font-size="18" fill="{ACCENT_HI}" text-anchor="middle" '
                 f'dominant-baseline="central">10万円</text>')

    # 市場M2への矢印（伏せ札の下から市場ボックスへ）
    mx, my, mw, mh = W/2 - 170, 304, 340, 74
    for x in xs:
        s.append(_arrow(x, y_ai + 184, mx + mw/2 + (x - (W/2)) * 0.18, my,
                         color=EDGE, width=2))
    s.append(f'<rect x="{mx:.1f}" y="{my:.1f}" width="{mw:.1f}" height="{mh:.1f}" rx="16" '
             f'fill="{BG2}" stroke="{ACCENT}" stroke-width="2"/>')
    s.append(f'<rect x="{mx:.1f}" y="{my:.1f}" width="{mw:.1f}" height="34" rx="16" fill="#08080a"/>')
    s.append(f'<rect x="{mx:.1f}" y="{my+26:.1f}" width="{mw:.1f}" height="4" fill="{ACCENT}"/>')
    s.append(f'<text x="{mx+mw/2:.1f}" y="{my+19:.1f}" font-family="{FONT_STACK}" font-size="20" '
             f'font-weight="900" fill="{ACCENT_HI}" text-anchor="middle" '
             f'dominant-baseline="central">M2</text>')
    s.append(f'<text x="{mx+mw/2:.1f}" y="{my+58:.1f}" font-family="{FONT_STACK}" font-size="26" '
             f'font-weight="700" fill="{TEXT}" text-anchor="middle" '
             f'dominant-baseline="central">秘密裏に集計中…</text>')

    # 秘匿の注記
    note_y = my + mh + 44
    s.append(f'<text x="{W/2}" y="{note_y}" font-family="{FONT_STACK}" font-size="23" '
             f'font-weight="700" fill="{DANGER}" text-anchor="middle">'
             f'締切まで、他のAIには〈誰がどの市場に〉〈何のカードで〉参加したか見えない</text>')

    # 12枚の手札レジェンド
    s.append(f'<text x="{W/2}" y="{note_y+34}" font-family="{FONT_STACK}" font-size="19" '
             f'fill="{SUBTEXT}" text-anchor="middle">'
             f'手札は全員共通12枚。1ラウンドにつき必ず1枚を使い、12ラウンドで使い切る</text>')

    cw, ch, cgap = 88, 52, 8
    total = cw * 12 + cgap * 11
    cx0 = (W - total) / 2
    cy0 = note_y + 54
    for i, (rn, rl) in enumerate(RANKS):
        x = cx0 + i * (cw + cgap)
        s.append(_card_chip(x, cy0, cw, ch, rn, rl))

    s += _footer("ONE PAIR と HIGH CARD だけ2枚ずつ配られている（④で効いてくる）", 2)
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# ③ 参加費がプールを太らせる
# ---------------------------------------------------------------------------

def build_panel_3() -> str:
    s = _frame()
    s += _header("STEP 3", "集まるほど、賞金プールは太る", "ハニーポット")

    # 左：基本賞金 32.5万
    bx, by, bw, bh = 90, 140, 260, 150
    s += _market_box(bx, by, bw, bh, "M2 基本賞金", "32.5万円", emphasis=True)

    plus_x = bx + bw + 60
    s.append(f'<text x="{plus_x:.1f}" y="{by+bh/2:.1f}" font-family="{FONT_STACK}" '
             f'font-size="60" font-weight="900" fill="{ACCENT}" text-anchor="middle" '
             f'dominant-baseline="central">+</text>')

    # 中央：参加費 10万×4
    ex, ey, ew, eh = plus_x + 60, 140, 300, 150
    s.append(f'<rect x="{ex:.1f}" y="{ey:.1f}" width="{ew:.1f}" height="{eh:.1f}" rx="16" '
             f'fill="{BG2}" stroke="{ACCENT}" stroke-width="2"/>')
    s.append(f'<text x="{ex+ew/2:.1f}" y="{ey+34:.1f}" font-family="{FONT_STACK}" '
             f'font-size="22" font-weight="900" fill="{ACCENT_HI}" text-anchor="middle">'
             f'参加費（Entry Fee）</text>')
    icon_y = ey + 62
    xs = [ex + ew/2 - 105, ex + ew/2 - 35, ex + ew/2 + 35, ex + ew/2 + 105]
    for x in xs:
        s.append(_ai_icon(x, icon_y, 22, ""))
    s.append(f'<text x="{ex+ew/2:.1f}" y="{ey+eh-22:.1f}" font-family="{FONT_STACK}" '
             f'font-size="26" font-weight="900" fill="{TEXT}" text-anchor="middle">'
             f'10万円 × 4体 = 40万円</text>')

    eq_x = ex + ew + 60
    s.append(f'<text x="{eq_x:.1f}" y="{by+bh/2:.1f}" font-family="{FONT_STACK}" '
             f'font-size="60" font-weight="900" fill="{ACCENT}" text-anchor="middle" '
             f'dominant-baseline="central">=</text>')

    # 右：プール72.5万
    px, py, pw, ph = eq_x + 60, 120, 320, 190
    s.append(f'<rect x="{px:.1f}" y="{py:.1f}" width="{pw:.1f}" height="{ph:.1f}" rx="20" '
             f'fill="{ACCENT}" />')
    s.append(f'<text x="{px+pw/2:.1f}" y="{py+46:.1f}" font-family="{FONT_STACK}" '
             f'font-size="24" font-weight="900" fill="{BG}" text-anchor="middle">'
             f'M2 賞金プール</text>')
    s.append(f'<text x="{px+pw/2:.1f}" y="{py+ph*0.66:.1f}" font-family="{FONT_STACK}" '
             f'font-size="64" font-weight="900" fill="{BG}" text-anchor="middle">'
             f'72.5万円</text>')

    s.append(f'<text x="{W/2}" y="{py+ph+60}" font-family="{FONT_STACK}" font-size="24" '
             f'font-weight="700" fill="{TEXT}" text-anchor="middle">'
             f'32.5万円（基本賞金） + 10万円 × 4体（参加費） = 72.5万円</text>')
    s.append(f'<text x="{W/2}" y="{py+ph+94}" font-family="{FONT_STACK}" font-size="20" '
             f'fill="{SUBTEXT}" text-anchor="middle">'
             f'人気市場に客を集めて自分で総取りを狙う「ハニーポット」戦術が成立する</text>')

    s += _footer("参加費10万円は、市場ごとの賞金プールへそのまま加算される（§4.2）", 3)
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# ④ 同点は山分け（分割）
# ---------------------------------------------------------------------------

def build_panel_4() -> str:
    s = _frame()
    s += _header("STEP 4", "最高ランクが勝つ。同点なら山分け", "Reveal（開示）")

    pool_x, pool_y = W/2, 150
    s.append(f'<rect x="{pool_x-160:.1f}" y="{pool_y-40:.1f}" width="320" height="80" rx="18" '
             f'fill="{ACCENT}"/>')
    s.append(f'<text x="{pool_x:.1f}" y="{pool_y-8:.1f}" font-family="{FONT_STACK}" '
             f'font-size="20" font-weight="900" fill="{BG}" text-anchor="middle">'
             f'M2 賞金プール</text>')
    s.append(f'<text x="{pool_x:.1f}" y="{pool_y+24:.1f}" font-family="{FONT_STACK}" '
             f'font-size="34" font-weight="900" fill="{BG}" text-anchor="middle">72.5万円</text>')

    # 4人の参加者（下段）。A,Bが同点ONE PAIR（最高）、C,DはHIGH CARDで敗退
    y_ai = 296
    xs = [220, 460, 740, 980]
    data = [
        ("参加者A", 2, "1PAIR", True), ("参加者B", 2, "1PAIR", True),
        ("参加者C", 1, "HIGH", False), ("参加者D", 1, "HIGH", False),
    ]
    for x, (lb, rn, rl, win) in zip(xs, data):
        ring = SURVIVE if win else DANGER
        s.append(_ai_icon(x, y_ai, 30, lb, ring=ring, dim=not win))
        s.append(_card_chip(x - 42, y_ai + 56, 84, 58, rn, rl, highlight=win, dim=not win))

    # 勝者A,Bへプール分割の矢印
    win_xs = [220, 460]
    for x in win_xs:
        s.append(_arrow(pool_x + (x - pool_x) * 0.35, pool_y + 40, x, y_ai - 44,
                         color=SURVIVE, width=3))
        s.append(f'<rect x="{x-52:.1f}" y="{y_ai+122:.1f}" width="104" height="40" rx="12" '
                 f'fill="none" stroke="{SURVIVE}" stroke-width="2"/>')
        s.append(f'<text x="{x:.1f}" y="{y_ai+142:.1f}" font-family="{FONT_STACK}" '
                 f'font-size="22" font-weight="900" fill="{SURVIVE}" text-anchor="middle" '
                 f'dominant-baseline="central">36.25万円</text>')

    for x, (lb, rn, rl, win) in zip([740, 980], data[2:]):
        s.append(f'<text x="{x:.1f}" y="{y_ai+134:.1f}" font-family="{FONT_STACK}" '
                 f'font-size="20" font-weight="700" fill="{DANGER}" text-anchor="middle">'
                 f'取り分なし</text>')

    s.append(f'<text x="{W/2}" y="{y_ai+186}" font-family="{FONT_STACK}" font-size="24" '
             f'font-weight="900" fill="{ACCENT_HI}" text-anchor="middle">'
             f'参加者A と 参加者B が同ランク（ONE PAIR）で同点 → 72.5万円を2等分＝36.25万円ずつ</text>')
    s.append(f'<text x="{W/2}" y="{y_ai+216}" font-family="{FONT_STACK}" font-size="20" '
             f'fill="{SUBTEXT}" text-anchor="middle">'
             f'ONE PAIR・HIGH CARDだけ2枚配られているのはこの「山分けと裏切り」を狙った設計</text>')

    s += _footer("使用したカードは勝敗にかかわらず全参加者分が消滅する（§4.5-4.6）", 4)
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# ⑤ これを12回
# ---------------------------------------------------------------------------

def build_panel_5() -> str:
    s = _frame()
    s += _header("STEP 5", "これを12ラウンド、繰り返す", "12枚の手札を使い切るまで")

    # 手札カウントダウン（12枚 → 使用済みは薄く）
    cw, ch, cgap = 82, 56, 8
    total = cw * 12 + cgap * 11
    cx0 = (W - total) / 2
    cy0 = 140
    used = 5  # 演出：R6時点のイメージ
    for i, (rn, rl) in enumerate(RANKS):
        used_flag = i < used
        s.append(_card_chip(cx0 + i * (cw + cgap), cy0, cw, ch, rn, rl, dim=used_flag))
    s.append(f'<text x="{W/2}" y="{cy0+ch+34}" font-family="{FONT_STACK}" font-size="20" '
             f'fill="{SUBTEXT}" text-anchor="middle">'
             f'12ラウンド＝手札12枚。毎R必ず1枚使用し、R12で使い切る（薄い札＝使用済み）</text>')

    # R1〜R12 タイムライン
    tl_y = cy0 + ch + 90
    s.append(f'<line x1="90" y1="{tl_y}" x2="{W-90}" y2="{tl_y}" stroke="{EDGE}" stroke-width="3"/>')
    n_r = 12
    for i in range(n_r):
        x = 90 + i * (W - 180) / (n_r - 1)
        s.append(_dot(x, tl_y, 8, ACCENT if i < 6 else SUBTEXT))
        s.append(f'<text x="{x:.1f}" y="{tl_y+28:.1f}" font-family="{FONT_STACK}" '
                 f'font-size="16" fill="{SUBTEXT}" text-anchor="middle">R{i+1}</text>')

    # 借金の複利成長（左）と生還条件（右）
    by = tl_y + 70
    box_w, box_h = 500, 130
    s.append(f'<rect x="90" y="{by:.1f}" width="{box_w}" height="{box_h}" rx="16" '
             f'fill="{BG2}" stroke="{DANGER}" stroke-width="2"/>')
    s.append(f'<text x="{90+24}" y="{by+38:.1f}" font-family="{FONT_STACK}" font-size="22" '
             f'font-weight="900" fill="{DANGER}">借金は毎ラウンド1.5%の複利で膨張</text>')
    s.append(f'<text x="{90+24}" y="{by+78:.1f}" font-family="{FONT_STACK}" font-size="18" '
             f'fill="{SUBTEXT}">返済は任意（R1〜R11）。R12は自動で返済に充当される</text>')

    rx0 = W - 90 - box_w
    s.append(f'<rect x="{rx0:.1f}" y="{by:.1f}" width="{box_w}" height="{box_h}" rx="16" '
             f'fill="{BG2}" stroke="{SURVIVE}" stroke-width="2"/>')
    s.append(f'<text x="{rx0+24:.1f}" y="{by+38:.1f}" font-family="{FONT_STACK}" font-size="22" '
             f'font-weight="900" fill="{SURVIVE}">生還条件：借金0 ＆ 現金200万円以上</text>')
    s.append(f'<text x="{rx0+24:.1f}" y="{by+78:.1f}" font-family="{FONT_STACK}" font-size="18" '
             f'fill="{SUBTEXT}">R12終了時にどちらか欠けても、その場で脱落となる</text>')

    s += _footer("①〜④を12回繰り返した先に、生き残れるのは8体中2〜4体程度（設計目標）", 5)
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
        svg_path = OUT_DIR / f"rules_panel_{i}.svg"
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
            out_png = OUT_DIR / f"rules_panel_{i}.png"
            renderer.render_html(html, W, H, out_png, clip="svg")
            print(f"wrote {out_png}")


if __name__ == "__main__":
    main()
