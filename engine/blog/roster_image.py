"""
参加者ロスター画像の生成 (PIL)

viewer/static/emotions/{vendor}_{emotion}.png のキャラ絵を使い、本戦の顔ぶれを
一枚の「出場者ボード」に合成する。カイジ風の暗色テーマ＋タイトル帯付き。
オープニング記事のアイキャッチ(featured_media)として使う。

新規画像生成はしない。既存アセットの合成のみ。

- generate_roster_image(roster, out_path, emotion="ease", title=...): PNG を出力
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont
from matplotlib import font_manager

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_EMOTIONS_DIR = _PROJECT_ROOT / "viewer" / "static" / "emotions"

# 探索する日本語対応フォント候補
_JP_FONT_CANDIDATES = [
    "Noto Sans CJK JP", "Noto Sans JP", "IPAexGothic", "IPAGothic",
    "TakaoGothic", "VL Gothic", "Source Han Sans JP", "Yu Gothic",
    "Hiragino Sans", "MS Gothic",
]

# カイジ風の暗色テーマ
_BG = (13, 13, 16)
_CARD_BG = (26, 26, 32)
_CARD_EDGE = (60, 60, 72)
_ACCENT = (201, 162, 39)      # くすんだ金
_TEXT = (232, 232, 236)
_SUBTEXT = (150, 150, 160)
_TITLE_COLOR = (232, 210, 120)


def _jp_font_path() -> str | None:
    """日本語グリフを持つフォントの実ファイルパスを返す。無ければ None。"""
    for cand in _JP_FONT_CANDIDATES:
        try:
            path = font_manager.findfont(cand, fallback_to_default=False)
            if path and Path(path).exists():
                return path
        except Exception:
            continue
    return None


def _latin_font_path() -> str:
    """ラテン文字用フォントの実ファイルパス(常に返る)。"""
    return font_manager.findfont("DejaVu Sans")


def _load_font(path: str | None, size: int) -> ImageFont.FreeTypeFont:
    try:
        if path:
            return ImageFont.truetype(path, size)
    except Exception:
        pass
    try:
        return ImageFont.truetype(font_manager.findfont("DejaVu Sans"), size)
    except Exception:
        return ImageFont.load_default()


def _text_w(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    l, t, r, b = draw.textbbox((0, 0), text, font=font)
    return r - l


def _fit(draw, text: str, font_path, max_w: int, start: int, min_size: int = 12):
    """max_w に収まる最大サイズのフォントを返す。"""
    size = start
    while size > min_size:
        f = _load_font(font_path, size)
        if _text_w(draw, text, f) <= max_w:
            return f
        size -= 2
    return _load_font(font_path, min_size)


def generate_roster_image(
    roster: list[dict],
    out_path: str | Path,
    emotion: str = "ease",
    title: str = "嘘八百万 ―談合カード― 出場者",
    subtitle: str = "シリーズその一 / LLM 頭脳戦",
) -> Path:
    """出場者ボード PNG を生成する。"""
    out_path = Path(out_path)
    n = len(roster)
    cols = min(4, n) if n else 1
    rows = (n + cols - 1) // cols

    # レイアウト寸法
    face = 200
    cell_pad = 22
    label_h = 78
    cell_w = face + cell_pad * 2
    cell_h = face + cell_pad + label_h
    margin = 40
    title_h = 150

    W = margin * 2 + cols * cell_w
    H = title_h + margin + rows * cell_h + margin

    img = Image.new("RGB", (W, H), _BG)
    draw = ImageDraw.Draw(img)

    jp = _jp_font_path()
    latin = _latin_font_path()

    # --- タイトル帯 ---
    draw.rectangle([0, 0, W, title_h], fill=(8, 8, 10))
    draw.rectangle([0, title_h - 4, W, title_h], fill=_ACCENT)
    title_font_path = jp or latin
    # 日本語フォントが無ければ英語タイトルにフォールバック
    if jp is None:
        title = "USO-8-MILLION : DANGO CARD  Roster"
        subtitle = "Series #1 / LLM mind games"
    tf = _fit(draw, title, title_font_path, W - margin * 2, 52, 24)
    tw = _text_w(draw, title, tf)
    draw.text(((W - tw) // 2, 34), title, font=tf, fill=_TITLE_COLOR)
    sf = _fit(draw, subtitle, title_font_path, W - margin * 2, 26, 14)
    sw = _text_w(draw, subtitle, sf)
    draw.text(((W - sw) // 2, 96), subtitle, font=sf, fill=_SUBTEXT)

    # --- 各セル ---
    name_font_path = jp or latin
    for i, r in enumerate(roster):
        cr, cc = divmod(i, cols)
        x0 = margin + cc * cell_w
        y0 = title_h + margin + cr * cell_h
        x1 = x0 + cell_w
        y1 = y0 + cell_h

        # カード背景
        draw.rounded_rectangle([x0 + 6, y0 + 6, x1 - 6, y1 - 6],
                               radius=16, fill=_CARD_BG, outline=_CARD_EDGE, width=2)

        # 顔絵
        vendor = r.get("vendor", "anthropic")
        face_path = _EMOTIONS_DIR / f"{vendor}_{emotion}.png"
        fx = x0 + cell_pad + 6
        fy = y0 + cell_pad
        if face_path.exists():
            try:
                fimg = Image.open(face_path).convert("RGBA")
                fimg = fimg.resize((face - 12, face - 12))
                # 顔絵の下地(明るい絵なので白タイル)
                draw.rounded_rectangle([fx, fy, fx + face - 12, fy + face - 12],
                                       radius=12, fill=(245, 245, 245))
                img.paste(fimg, (fx, fy), fimg)
            except Exception:
                draw.rectangle([fx, fy, fx + face - 12, fy + face - 12], fill=(40, 40, 48))
        else:
            draw.rectangle([fx, fy, fx + face - 12, fy + face - 12], fill=(40, 40, 48))

        # ラベル(pid + モデル名 + provider)
        inner_w = cell_w - cell_pad * 2
        tx = x0 + cell_pad + 2
        ty = fy + (face - 12) + 10

        pid = str(r.get("pid", ""))
        pf = _load_font(name_font_path, 20)
        draw.text((tx, ty), pid, font=pf, fill=_ACCENT)

        name = str(r.get("name", ""))
        nf = _fit(draw, name, name_font_path, inner_w, 24, 12)
        draw.text((tx, ty + 26), name, font=nf, fill=_TEXT)

        prov = str(r.get("provider", ""))
        vf = _load_font(name_font_path, 15)
        draw.text((tx, ty + 54), prov, font=vf, fill=_SUBTEXT)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG")
    return out_path
