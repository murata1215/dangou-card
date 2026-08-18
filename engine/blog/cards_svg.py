"""
宣伝・世界観ビジュアル（SVGカード）生成

記事・SNS・シリーズ告知に使い回せるビジュアルを SVG 文字列として組み立てる。

なぜ SVG か:
- このホストは日本語フォントが未インストールで、PIL/matplotlib で PNG 化すると
  日本語が化ける（roster_image.py がタイトルを英語フォールバックしているのが実例）。
- SVG なら閲覧側デバイス/ブラウザの CJK フォントで正しく描画される。
- 依存追加なし・純Pythonの文字列テンプレートで完結。キャラ絵は base64 data URI で埋め込む。

提供する4種:
- build_model_card_svg  : モデル紹介カード（会社/ティア/キャッチ/価格）
- build_title_card_svg  : シリーズ・タイトル画像（banner / square）
- build_result_card_svg : 最終順位・リザルトカード
- build_quote_card_svg  : 名言カード（DM引用）
"""

from __future__ import annotations

import base64
import io
from pathlib import Path

try:
    from PIL import Image  # キャラ絵の縮小・再圧縮に使用（未導入でも原寸埋め込みで動作）
except Exception:  # pragma: no cover
    Image = None  # type: ignore

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EMOTIONS_DIR = _PROJECT_ROOT / "viewer" / "static" / "emotions"

# 閲覧側デバイスの日本語フォントを使うためのフォントスタック
FONT_STACK = (
    "'Hiragino Sans','Hiragino Kaku Gothic ProN','Yu Gothic',"
    "'Noto Sans CJK JP','Noto Sans JP','Meiryo',sans-serif"
)

# カイジ風・暗色パレット（roster_image.py の配色を流用）
BG = "#0d0d10"
BG2 = "#141419"
PANEL = "#1a1a20"
EDGE = "#3c3c48"
ACCENT = "#c9a227"        # くすんだ金
ACCENT_HI = "#e8d278"     # 明るい金
TEXT = "#e8e8ec"
SUBTEXT = "#96969f"
SURVIVE = "#2ea06a"       # 生還(緑)
DANGER = "#b03a3a"        # 脱落/破産(赤)

# モデル別キャッチコピー（編集可）
MODEL_TAGLINES: dict[str, str] = {
    "M1": "質実剛健。読み合いを制する本命。",
    "M2": "王道の交渉巧者。そつのない立ち回り。",
    "M3": "俊足の情報巧者。軽さを手数に変える。",
    "M4": "型破りの賭博師。振れ幅で勝負を懸ける。",
    "M5": "熟考派。長考をいとわず、論理で押し切る。",
    "M6": "冷静な実利主義。コスパで削り勝つ。",
    "L1": "小さな巨人。軽量級ながら油断ならない。",
    "L2": "手数の刺客。安さで数を撃つ。",
    "L3": "電光石火。とにかく速く、軽く動く。",
    "L4": "廉価版の暴れ馬。安さで場を掻き回す。",
    "L5": "熟考派の弟。安価に論理を回す。",
    "L6": "実利のバジェット枠。堅実に安く。",
    "H1": "頂点の読み合い。格の違いを見せつける。",
    "H2": "最上位の交渉巧者。隙のない完成形。",
    "H3": "圧倒的な情報処理力。桁違いの精度。",
    "H4": "旗艦の中の旗艦。破格の一手を放つ。",
    "H5": "極限の熟考。時間をかけて必ず勝つ。",
    "H6": "頂点の実利主義。強さもコスパも譲らない。",
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _esc(s: str) -> str:
    """XMLエスケープ。"""
    return (
        str(s)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _png_data_uri(path: Path, max_px: int | None = None) -> str | None:
    """PNG を base64 data URI 文字列にする。無ければ None。

    max_px を渡すと、その長辺以下に縮小・再圧縮してから埋め込む（PIL があれば）。
    元絵は 1254px/1.5MB と大きく、原寸埋め込みだと SVG が肥大化するため。
    """
    try:
        if not path.exists():
            return None
        data = path.read_bytes()
        if max_px and Image is not None:
            im = Image.open(io.BytesIO(data))
            if max(im.size) > max_px:
                im = im.convert("RGBA")
                im.thumbnail((max_px, max_px))
                buf = io.BytesIO()
                im.save(buf, "PNG", optimize=True)
                data = buf.getvalue()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        pass
    return None


def _face_uri(vendor: str, emotion: str, max_px: int | None = None) -> str | None:
    return _png_data_uri(_EMOTIONS_DIR / f"{vendor}_{emotion}.png", max_px=max_px)


def _wrap(text: str, n: int) -> list[str]:
    """n文字で折り返す（日本語向けの単純折り返し）。句読点直後で優先的に区切る。"""
    lines: list[str] = []
    cur = ""
    for ch in text:
        cur += ch
        if len(cur) >= n and ch in "、。，．,. 　」』":
            lines.append(cur)
            cur = ""
        elif len(cur) >= n + 4:  # 区切りが来ない場合の強制改行
            lines.append(cur)
            cur = ""
    if cur:
        lines.append(cur)
    return lines


def _registry_tier(model_key: str) -> str | None:
    """MODEL_REGISTRYからtier("H"/"M"/"L")を引く。未登録キーやレジストリ未定義（tier==""）はNone。"""
    try:
        from llm.models import get_model
        tier = get_model(model_key).tier
        return tier or None
    except Exception:
        return None


def _tier_label(model_key: str) -> str:
    tier = _registry_tier(model_key)
    if tier is None:
        # レジストリ未登録キー向けフォールバック（従来ヒューリスティック）
        tier = "M" if model_key.startswith("M") else "L"
    if tier == "H":
        return "強量級 ・ フラッグシップ級"
    if tier == "M":
        return "中量級 ・ フラッグシップ級"
    return "軽量級 ・ ライトウェイト"


def _price_pitch(model_key: str, output_price: float) -> str:
    tier = _registry_tier(model_key)
    if tier is None:
        tier = "M" if model_key.startswith("M") else "L"
    if tier == "H":
        return "文句なしの最上位級。頂点の思考力に懸ける。"
    if tier == "M":
        if output_price <= 4.5:
            return "旗艦級の実力ながら、価格の安さで市場を席巻中。"
        return "実力本位のフラッグシップ。相応の価格。"
    return "軽量級。とにかく安く、手数で勝負。"


def _face_group(vendor: str, emotion: str, x: float, y: float, size: float, uid: str,
                ring: str = ACCENT) -> str:
    """白タイル地＋角丸クリップ＋金リングでキャラ絵を配置する SVG 断片。"""
    # 表示サイズの約1.6倍(高精細)まで縮小して埋め込み、SVGの肥大化を防ぐ（上限480px）
    uri = _face_uri(vendor, emotion, max_px=min(480, max(120, int(size * 1.6))))
    r = 24
    parts = [
        f'<rect x="{x}" y="{y}" width="{size}" height="{size}" rx="{r}" '
        f'fill="#f5f5f5"/>',
    ]
    if uri:
        parts.append(f'<clipPath id="clip-{uid}"><rect x="{x}" y="{y}" '
                     f'width="{size}" height="{size}" rx="{r}"/></clipPath>')
        parts.append(
            f'<image x="{x}" y="{y}" width="{size}" height="{size}" '
            f'clip-path="url(#clip-{uid})" preserveAspectRatio="xMidYMid meet" '
            f'xlink:href="{uri}"/>'
        )
    parts.append(
        f'<rect x="{x}" y="{y}" width="{size}" height="{size}" rx="{r}" '
        f'fill="none" stroke="{ring}" stroke-width="4"/>'
    )
    return "".join(parts)


def _svg_open(w: int, h: int) -> str:
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" '
        f'xmlns:xlink="http://www.w3.org/1999/xlink" '
        f'width="{w}" height="{h}" viewBox="0 0 {w} {h}">'
    )


def _suit_decor(w: int, h: int, opacity: float = 0.06) -> str:
    """背景に薄くトランプのスートを散らす装飾。"""
    suits = ["\u2660", "\u2665", "\u2666", "\u2663"]  # ♠♥♦♣
    spots = [
        (w * 0.10, h * 0.18, 120), (w * 0.88, h * 0.22, 150),
        (w * 0.20, h * 0.82, 160), (w * 0.82, h * 0.80, 120),
        (w * 0.50, h * 0.50, 220),
    ]
    out = []
    for i, (cx, cy, sz) in enumerate(spots):
        out.append(
            f'<text x="{cx:.0f}" y="{cy:.0f}" font-family="{FONT_STACK}" '
            f'font-size="{sz}" fill="{ACCENT}" fill-opacity="{opacity}" '
            f'text-anchor="middle" dominant-baseline="central">{suits[i % 4]}</text>'
        )
    return "".join(out)


def _man(yen: int | None) -> str:
    """円→万円表記（小数第1位まで、端数があれば表示）。"""
    if yen is None:
        return "—"
    v = yen / 10000.0
    if abs(v - round(v)) < 1e-6:
        return f"{int(round(v))}万円"
    return f"{v:.1f}万円"


# ---------------------------------------------------------------------------
# ① モデル紹介カード
# ---------------------------------------------------------------------------

def build_model_card_svg(
    *,
    pid: str,
    model_key: str,
    name: str,
    provider: str,
    vendor: str,
    input_price: float,
    output_price: float,
    model_id: str = "",
    emotion: str = "ease",
    tagline: str | None = None,
) -> str:
    """モデル紹介カード（縦1080×1350）を SVG 文字列で返す。"""
    W, H = 1080, 1350
    tag = tagline or MODEL_TAGLINES.get(model_key, "頭脳戦プラットフォーム、参戦。")
    tier = _tier_label(model_key)
    pitch = _price_pitch(model_key, output_price)

    s = [_svg_open(W, H)]
    # 背景
    s.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
    s.append(f'<rect x="24" y="24" width="{W-48}" height="{H-48}" rx="28" '
             f'fill="{BG2}" stroke="{EDGE}" stroke-width="2"/>')
    # 透かし「嘘八百万 参戦」
    s.append(
        f'<text x="{W/2}" y="{H/2}" font-family="{FONT_STACK}" font-size="300" '
        f'font-weight="900" fill="{ACCENT}" fill-opacity="0.05" text-anchor="middle" '
        f'dominant-baseline="central" transform="rotate(-16 {W/2} {H/2})">嘘八百万</text>'
    )
    s.append(
        f'<text x="{W/2}" y="{H/2+180}" font-family="{FONT_STACK}" font-size="90" '
        f'font-weight="700" fill="{ACCENT}" fill-opacity="0.06" text-anchor="middle" '
        f'transform="rotate(-16 {W/2} {H/2})">談合カード 参戦</text>'
    )
    # ヘッダ帯
    s.append(f'<rect x="24" y="24" width="{W-48}" height="118" rx="28" fill="#08080a"/>')
    s.append(f'<rect x="24" y="130" width="{W-48}" height="4" fill="{ACCENT}"/>')
    s.append(f'<text x="70" y="98" font-family="{FONT_STACK}" font-size="34" '
             f'letter-spacing="6" fill="{SUBTEXT}">CHALLENGER / 参戦者</text>')
    s.append(f'<text x="{W-70}" y="104" font-family="{FONT_STACK}" font-size="64" '
             f'font-weight="900" fill="{ACCENT_HI}" text-anchor="end">{_esc(pid)}</text>')

    # キャラ絵
    face = 470
    fx = (W - face) / 2
    fy = 190
    s.append(_face_group(vendor, emotion, fx, fy, face, uid=f"model-{pid}"))

    # 会社バッジ
    prov = _esc(provider or vendor.title())
    badge_w = max(220, len(prov) * 22 + 80)
    bx = (W - badge_w) / 2
    by = fy + face + 26
    s.append(f'<rect x="{bx}" y="{by}" width="{badge_w}" height="56" rx="28" '
             f'fill="none" stroke="{ACCENT}" stroke-width="2"/>')
    s.append(f'<text x="{W/2}" y="{by+38}" font-family="{FONT_STACK}" font-size="30" '
             f'letter-spacing="3" fill="{ACCENT}" text-anchor="middle">{prov}</text>')

    # モデル名
    ny = by + 128
    s.append(f'<text x="{W/2}" y="{ny}" font-family="{FONT_STACK}" font-size="76" '
             f'font-weight="900" fill="{ACCENT_HI}" text-anchor="middle">{_esc(name)}</text>')
    if model_id:
        s.append(f'<text x="{W/2}" y="{ny+42}" font-family="{FONT_STACK}" font-size="26" '
                 f'fill="{SUBTEXT}" text-anchor="middle">{_esc(model_id)}</text>')
    # ティア
    s.append(f'<text x="{W/2}" y="{ny+92}" font-family="{FONT_STACK}" font-size="32" '
             f'fill="{TEXT}" text-anchor="middle">{_esc(tier)}</text>')

    # キャッチ
    ty = ny + 156
    for i, line in enumerate(_wrap(tag, 18)):
        s.append(f'<text x="{W/2}" y="{ty + i*50}" font-family="{FONT_STACK}" '
                 f'font-size="40" font-weight="700" fill="{TEXT}" '
                 f'text-anchor="middle">{_esc(line)}</text>')

    # 価格パネル
    px, py, pw, ph = 90, H - 300, W - 180, 176
    s.append(f'<rect x="{px}" y="{py}" width="{pw}" height="{ph}" rx="20" '
             f'fill="{PANEL}" stroke="{EDGE}" stroke-width="2"/>')
    s.append(f'<text x="{px+30}" y="{py+44}" font-family="{FONT_STACK}" font-size="24" '
             f'letter-spacing="3" fill="{SUBTEXT}">PRICE / 価格（100万トークンあたり・USD）</text>')
    colx1 = px + pw * 0.28
    colx2 = px + pw * 0.72
    s.append(f'<text x="{colx1:.0f}" y="{py+104}" font-family="{FONT_STACK}" font-size="52" '
             f'font-weight="900" fill="{ACCENT_HI}" text-anchor="middle">${input_price:g}</text>')
    s.append(f'<text x="{colx1:.0f}" y="{py+138}" font-family="{FONT_STACK}" font-size="24" '
             f'fill="{SUBTEXT}" text-anchor="middle">入力 / input</text>')
    s.append(f'<text x="{colx2:.0f}" y="{py+104}" font-family="{FONT_STACK}" font-size="52" '
             f'font-weight="900" fill="{ACCENT_HI}" text-anchor="middle">${output_price:g}</text>')
    s.append(f'<text x="{colx2:.0f}" y="{py+138}" font-family="{FONT_STACK}" font-size="24" '
             f'fill="{SUBTEXT}" text-anchor="middle">出力 / output</text>')
    s.append(f'<line x1="{px+pw*0.5:.0f}" y1="{py+70}" x2="{px+pw*0.5:.0f}" '
             f'y2="{py+150}" stroke="{EDGE}" stroke-width="1"/>')

    # ポジション文
    s.append(f'<text x="{W/2}" y="{py+ph+44}" font-family="{FONT_STACK}" font-size="28" '
             f'fill="{ACCENT}" text-anchor="middle">{_esc(pitch)}</text>')

    # フッタ
    s.append(f'<text x="{W/2}" y="{H-32}" font-family="{FONT_STACK}" font-size="24" '
             f'fill="{SUBTEXT}" text-anchor="middle">嘘八百万 —談合カード—　SEASON 1</text>')

    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# ② シリーズ・タイトル画像
# ---------------------------------------------------------------------------

def build_title_card_svg(size: str = "banner") -> str:
    """シリーズのキービジュアル。size='banner'(1200×630) / 'square'(1080×1080)。"""
    if size == "square":
        W, H = 1080, 1080
        title_fs, sub_fs = 200, 46
    else:
        W, H = 1200, 630
        title_fs, sub_fs = 150, 40

    cx = W / 2
    s = [_svg_open(W, H)]
    # 背景グラデ風（単色＋枠＋スート装飾）
    s.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
    s.append(_suit_decor(W, H, opacity=0.06))
    s.append(f'<rect x="20" y="20" width="{W-40}" height="{H-40}" rx="24" '
             f'fill="none" stroke="{ACCENT}" stroke-width="3"/>')

    if size == "square":
        ty = H * 0.34
        sub_y = H * 0.50
        card_y = H * 0.60
        season_y = H * 0.80
    else:
        ty = H * 0.36
        sub_y = H * 0.58
        card_y = H * 0.70
        season_y = H * 0.86

    # メインタイトル
    s.append(f'<text x="{cx}" y="{ty}" font-family="{FONT_STACK}" font-size="{title_fs}" '
             f'font-weight="900" fill="{ACCENT_HI}" text-anchor="middle" '
             f'dominant-baseline="central">嘘八百万</text>')
    s.append(f'<text x="{cx}" y="{ty + title_fs*0.62:.0f}" font-family="{FONT_STACK}" '
             f'font-size="{int(title_fs*0.32)}" letter-spacing="10" font-weight="700" '
             f'fill="{TEXT}" text-anchor="middle">— 談合カード —</text>')

    # サブコピー
    s.append(f'<text x="{cx}" y="{sub_y}" font-family="{FONT_STACK}" font-size="{sub_fs}" '
             f'font-weight="700" fill="{TEXT}" text-anchor="middle">'
             f'12ラウンドを、嘘と談合で乗り切れ。</text>')

    # SEASON リボン
    ribbon_w = 360 if size == "square" else 320
    rx = cx - ribbon_w / 2
    s.append(f'<rect x="{rx:.0f}" y="{card_y - (34 if size=="square" else 0):.0f}" '
             f'width="{ribbon_w}" height="64" rx="32" fill="{ACCENT}"/>')
    s.append(f'<text x="{cx}" y="{card_y + (8 if size=="square" else 42):.0f}" '
             f'font-family="{FONT_STACK}" font-size="34" letter-spacing="6" '
             f'font-weight="900" fill="#0d0d10" text-anchor="middle" '
             f'dominant-baseline="central">SEASON 1 / シーズン1</text>')

    # 惹句
    s.append(f'<text x="{cx}" y="{season_y}" font-family="{FONT_STACK}" '
             f'font-size="{int(sub_fs*0.7)}" fill="{SUBTEXT}" text-anchor="middle">'
             f'嘘、騙し、談合、調略 — LLM 8体の頭脳戦。</text>')

    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# ③ 最終順位・リザルトカード
# ---------------------------------------------------------------------------

_RESULT_SYMBOL = {
    "survived": ("\u2605", SURVIVE, "生還"),      # ★
    "eliminated": ("\u25bd", DANGER, "脱落"),     # ▽
    "bankruptcy": ("\u2715", DANGER, "破産"),     # ✕
}


def build_result_card_svg(standings: list[dict], emotion: str = "ease") -> str:
    """最終順位カード（縦1080×1350）。standings は cash 降順・生還優先で整列済み想定。

    各 dict: {pid, name, vendor, result, cash, final_round}
    """
    W = 1080
    header_h = 210
    row_h = 118
    n = len(standings)
    H = header_h + row_h * n + 70

    s = [_svg_open(W, H)]
    s.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
    s.append(f'<rect x="24" y="24" width="{W-48}" height="{H-48}" rx="28" '
             f'fill="{BG2}" stroke="{EDGE}" stroke-width="2"/>')
    # ヘッダ
    s.append(f'<rect x="24" y="24" width="{W-48}" height="150" rx="28" fill="#08080a"/>')
    s.append(f'<rect x="24" y="162" width="{W-48}" height="4" fill="{ACCENT}"/>')
    s.append(f'<text x="{W/2}" y="96" font-family="{FONT_STACK}" font-size="60" '
             f'font-weight="900" fill="{ACCENT_HI}" text-anchor="middle">最終順位</text>')
    s.append(f'<text x="{W/2}" y="140" font-family="{FONT_STACK}" font-size="26" '
             f'letter-spacing="4" fill="{SUBTEXT}" text-anchor="middle">'
             f'FINAL STANDINGS ・ 生還ライン 現金200万円＋借金0</text>')

    y = header_h
    for i, row in enumerate(standings):
        pid = row.get("pid", "")
        name = row.get("name", "")
        vendor = row.get("vendor", "anthropic")
        result = row.get("result") or "eliminated"
        cash = row.get("cash")
        fr = row.get("final_round")
        sym, color, jp = _RESULT_SYMBOL.get(result, ("\u25bd", DANGER, "脱落"))

        ry = y + i * row_h
        # 行背景（生還のみ強調）
        if result == "survived":
            s.append(f'<rect x="48" y="{ry}" width="{W-96}" height="{row_h-14}" rx="16" '
                     f'fill="#12241b" stroke="{SURVIVE}" stroke-width="2"/>')
        else:
            s.append(f'<rect x="48" y="{ry}" width="{W-96}" height="{row_h-14}" rx="16" '
                     f'fill="{PANEL}" stroke="{EDGE}" stroke-width="1"/>')
        # 順位
        s.append(f'<text x="96" y="{ry + (row_h-14)/2 + 18:.0f}" font-family="{FONT_STACK}" '
                 f'font-size="52" font-weight="900" fill="{ACCENT}" text-anchor="middle">'
                 f'{i+1}</text>')
        # 顔アイコン
        fsz = row_h - 40
        s.append(_face_group(vendor, emotion, 138, ry + 12, fsz,
                             uid=f"res-{pid}", ring=color))
        # pid + モデル名
        tx = 138 + fsz + 26
        s.append(f'<text x="{tx}" y="{ry + 46}" font-family="{FONT_STACK}" font-size="28" '
                 f'font-weight="700" fill="{ACCENT}">{_esc(pid)}</text>')
        s.append(f'<text x="{tx}" y="{ry + 84}" font-family="{FONT_STACK}" font-size="34" '
                 f'font-weight="700" fill="{TEXT}">{_esc(name)}</text>')
        # 結果シンボル + 資産
        s.append(f'<text x="{W-70}" y="{ry + 50}" font-family="{FONT_STACK}" font-size="40" '
                 f'font-weight="900" fill="{color}" text-anchor="end">'
                 f'{sym} {jp}</text>')
        detail = _man(cash)
        if result != "survived" and fr:
            detail += f'（R{fr}）'
        s.append(f'<text x="{W-70}" y="{ry + 90}" font-family="{FONT_STACK}" font-size="30" '
                 f'fill="{SUBTEXT}" text-anchor="end">{_esc(detail)}</text>')

    s.append(f'<text x="{W/2}" y="{H-34}" font-family="{FONT_STACK}" font-size="24" '
             f'fill="{SUBTEXT}" text-anchor="middle">嘘八百万 —談合カード—　SEASON 1</text>')
    s.append("</svg>")
    return "".join(s)


# ---------------------------------------------------------------------------
# ④ 名言カード（DM引用）
# ---------------------------------------------------------------------------

def build_quote_card_svg(
    *,
    quote: str,
    pid: str,
    name: str,
    vendor: str,
    round_num: int | None = None,
    to_pid: str | None = None,
    emotion: str = "ease",
) -> str:
    """名言（DM）カード（正方形1080×1080）。"""
    W = H = 1080
    s = [_svg_open(W, H)]
    s.append(f'<rect width="{W}" height="{H}" fill="{BG}"/>')
    s.append(f'<rect x="24" y="24" width="{W-48}" height="{H-48}" rx="28" '
             f'fill="{BG2}" stroke="{EDGE}" stroke-width="2"/>')
    # 大きな引用符
    s.append(f'<text x="90" y="290" font-family="{FONT_STACK}" font-size="240" '
             f'font-weight="900" fill="{ACCENT}" fill-opacity="0.18">\u201c</text>')

    # 引用本文
    lines = _wrap(quote, 16)
    lines = lines[:6]
    fs = 54 if len(lines) <= 4 else 44
    total = len(lines) * (fs + 18)
    start_y = (H * 0.46) - total / 2 + fs
    for i, line in enumerate(lines):
        s.append(f'<text x="{W/2}" y="{start_y + i*(fs+18):.0f}" font-family="{FONT_STACK}" '
                 f'font-size="{fs}" font-weight="700" fill="{TEXT}" '
                 f'text-anchor="middle">{_esc(line)}</text>')

    # 話者
    fsz = 150
    fx = (W - fsz) / 2
    fy = H - 300
    s.append(_face_group(vendor, emotion, fx, fy, fsz, uid=f"quote-{pid}"))
    meta = _esc(pid)
    if name:
        meta += f'　{_esc(name)}'
    s.append(f'<text x="{W/2}" y="{fy + fsz + 44}" font-family="{FONT_STACK}" font-size="34" '
             f'font-weight="700" fill="{ACCENT_HI}" text-anchor="middle">{meta}</text>')
    ctx_parts = []
    if round_num:
        ctx_parts.append(f'R{round_num}')
    if to_pid:
        ctx_parts.append(f'DM → {_esc(to_pid)}')
    if ctx_parts:
        s.append(f'<text x="{W/2}" y="{fy + fsz + 82}" font-family="{FONT_STACK}" '
                 f'font-size="26" fill="{SUBTEXT}" text-anchor="middle">'
                 f'{" ・ ".join(ctx_parts)}</text>')

    s.append(f'<text x="{W/2}" y="{H-36}" font-family="{FONT_STACK}" font-size="24" '
             f'fill="{SUBTEXT}" text-anchor="middle">嘘八百万 —談合カード—</text>')
    s.append("</svg>")
    return "".join(s)
