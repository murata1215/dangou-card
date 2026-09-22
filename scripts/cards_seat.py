"""
サイクル10.18: 手記アイキャッチ用「キャラカード」生成

L12R12 の手記12本＋総括のアイキャッチを Season 1 風の「席カード」に差し替えるための
スクリプト。第1段階（P06のみ試作）をけいすけさんが承認し、第2段階で全12席＋総括カードを
生成する。

設計方針（詳細は /home/uso8m/.claude/plans/compressed-foraging-stroustrup.md）:
- 出力 1200×630（OGP標準）。上下12%（y<76, y>554）を「安全地帯外」として、
  顔・モデル名・バッジ・階級行はすべて y=76〜554 の中央帯に収める。
  PixBlog 一覧サムネ（実測 2.1875:1 中央クロップ）で欠けるのは上下 40.7px のみなので、
  35px の余裕を持たせている。安全地帯は Chromium での実測（getBoundingClientRect）で
  assert する（render_card 内・_measure_safe_zone）。
- 左右2カラムレイアウト（顔を大きく＝400×400px確保するため、Season 1 の縦積みから変更）。
  モデル名は文字数に応じてフォントサイズを自動縮小する（_name_font_px）。
- キャラ絵は viewer/static/emotions/{vendor}_{emotion}.png を base64 data URI で埋め込む
  （engine/blog/cards_svg.py::_png_data_uri を再利用。新規アートは作らない）。
  キャラ絵はベンダー単位のみのため、Anthropic系5席（P02/P06/P08/P11/P12）は表情を
  すべて別にして区別する（喜/奸/焦/怒/疑）。
- 総括カード（order 17）は12席の顔をグリッド状に並べた集合カードにする（build_summary_card_html）。
- レンダリングは engine/blog/cards_html.py::CardRenderer（Playwright/Chromium）を再利用。
- PixBlog への書き込みは POST /images（画像アップロード）のみ。記事の作成・更新・公開はしない。
  LLM API は呼ばない。--upload を付けない限りネットワークアクセスは一切しない。

使い方:
    uv run python scripts/cards_seat.py --pid P06                     # カードのみ生成
    uv run python scripts/cards_seat.py --pid P06 --preview           # + 切り抜きプレビュー
    uv run python scripts/cards_seat.py --pid P06 --preview --upload  # + PixBlogへ画像アップロード
    uv run python scripts/cards_seat.py --pid all --preview           # 全12席
    uv run python scripts/cards_seat.py --summary --preview           # 総括カード（order 17）
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv()

from engine.blog.cards_svg import _png_data_uri, _esc  # noqa: E402

_EMOTIONS_DIR = _PROJECT_ROOT / "viewer" / "static" / "emotions"
_OUT_DIR = _PROJECT_ROOT / "doc" / "blog" / "20260921" / "images" / "cards"

CARD_W, CARD_H = 1200, 630
SAFE_TOP, SAFE_BOTTOM = 76, 554  # 上下12%の安全地帯境界（y座標）
SAFE_RIGHT = 1160  # プレート右端(1200-22=1178)より内側の左右安全マージン

# 一覧サムネ実測比率（PixBlog `.h-48.w-full.object-cover`、列幅420px時の最悪ケース）
LISTING_RATIO = 420 / 192  # = 2.1875
# X / OGP summary_large_image
OGP_RATIO = 2.0

# JP感情ラベル → viewer/static/emotions/ のファイル名キー（index.html の EMOTION_EN 準拠）
EMOTION_EN = {
    "喜": "joy", "怒": "anger", "哀": "sadness", "楽": "ease",
    "焦": "panic", "疑": "doubt", "奸": "smirk", "平静": "ease",
}

# ---------------------------------------------------------------------------
# 席テーブル（第2段階でそのまま行を追加して --pid all で回す想定）
#
# dominant_emotion: logs/llm/trial_C_l12r12_dr_1201/llm_logs/game01_{pid}_llm_calls.jsonl の
#   `emotion` フィールド（JP1字）の最頻値。第2段階で emotion 列のデフォルト値の参考にする列。
#   P06 は実測でも「奸」が最頻（73/136件）で、初期案の smirk と一致した。
# ---------------------------------------------------------------------------
FOOTER = "嘘八百万 —談合カード—　SEASON 2"

# 実測 dominant_emotion（2026-09-22 集計、全12席、logs/llm/trial_C_l12r12_dr_1201/ 由来）:
#   P01 楽 / P02 奸 / P03 疑 / P04 楽 / P05 楽 / P06 奸 / P07 疑 /
#   P08 焦 / P09 焦 / P10 奸 / P11 奸 / P12 疑
# 第2段階（サイクル10.18第2段階）でP02/P11の表示emotionをdominant_emotionから意図的に
# 変更した（Anthropic系5席が同一vendor画像で被らないようにするため。けいすけさん承認済み）:
#   P02: dominant=奸 → 表示=joy（喜）
#   P11: dominant=奸 → 表示=anger（怒。504で応答を切られ続けた席の憤りに寄せた。
#        当初案の焦はP08と重複するため回避）
# 会社名・モデル名・model_id は llm/models.py::get_model() の実測値、
# tier（実験席/API席）は doc/uso8000000_model_roster_v1_0.md に基づく。
SEATS: list[dict] = [
    {
        "order": "21",
        "pid": "P01",
        "provider": "Google",
        "name": "Gemini 3.5 Flash-Lite",
        "model_id": "gemini-3.5-flash-lite",
        "tier": "軽量級 ・ ライトウェイト",
        "vendor": "google",
        "emotion": "ease",  # 楽。dominant_emotion と一致。
        "dominant_emotion": "楽",
        "footer": FOOTER,
    },
    {
        "order": "27",
        "pid": "P02",
        "provider": "Anthropic",
        "name": "Claude Opus 5",
        "model_id": "devrelay/claude-opus-5",
        "tier": "実験席 ・ フラッグシップ級",
        "vendor": "anthropic",
        "emotion": "joy",  # 喜。dominant_emotion(奸)からの意図的な変更（けいすけさん指示）。
        "dominant_emotion": "奸",
        "footer": FOOTER,
    },
    {
        "order": "20",
        "pid": "P03",
        "provider": "OpenAI",
        "name": "GPT-5.6 Terra",
        "model_id": "devrelay/gpt-5.6-terra",
        "tier": "実験席 ・ 中量級",
        "vendor": "openai",
        "emotion": "doubt",  # 疑。dominant_emotion と一致。
        "dominant_emotion": "疑",
        "footer": FOOTER,
    },
    {
        "order": "24",
        "pid": "P04",
        "provider": "xAI",
        "name": "Grok 4.3",
        "model_id": "grok-4.3",
        "tier": "軽量級 ・ ライトウェイト",
        "vendor": "xai",
        "emotion": "ease",  # 楽。dominant_emotion と一致。
        "dominant_emotion": "楽",
        "footer": FOOTER,
    },
    {
        "order": "18",
        "pid": "P05",
        "provider": "OpenAI",
        "name": "GPT-4.1 Mini",
        "model_id": "gpt-4.1-mini",
        "tier": "軽量級 ・ ライトウェイト",
        "vendor": "openai",
        "emotion": "ease",  # 楽。dominant_emotion と一致。
        "dominant_emotion": "楽",
        "footer": FOOTER,
    },
    {
        "order": "29",
        "pid": "P06",
        "provider": "Anthropic",
        "name": "Claude Fable 5.1",
        "model_id": "devrelay/claude-fable-5-1",
        "tier": "実験席 ・ フラッグシップ級",
        "vendor": "anthropic",
        "emotion": "smirk",  # 奸（してやったり）。dominant_emotion と一致。
        "dominant_emotion": "奸",
        "footer": FOOTER,
    },
    {
        "order": "25",
        "pid": "P07",
        "provider": "DeepSeek",
        "name": "DeepSeek V4 Flash",
        "model_id": "deepseek-v4-flash",
        "tier": "軽量級 ・ ライトウェイト",
        "vendor": "deepseek",
        "emotion": "doubt",  # 疑。dominant_emotion と一致。
        "dominant_emotion": "疑",
        "footer": FOOTER,
    },
    {
        "order": "19",
        "pid": "P08",
        "provider": "Anthropic",
        "name": "Claude Haiku 4.5",
        "model_id": "claude-haiku-4-5-20251001",
        "tier": "軽量級 ・ ライトウェイト",
        "vendor": "anthropic",
        "emotion": "panic",  # 焦。dominant_emotion と一致。
        "dominant_emotion": "焦",
        "footer": FOOTER,
    },
    {
        "order": "23",
        "pid": "P09",
        "provider": "Moonshot",
        "name": "Kimi K2.6",
        "model_id": "kimi-k2.6",
        "tier": "軽量級 ・ ライトウェイト",
        "vendor": "moonshot",
        "emotion": "panic",  # 焦。dominant_emotion と一致。
        "dominant_emotion": "焦",
        "footer": FOOTER,
    },
    {
        "order": "28",
        "pid": "P10",
        "provider": "OpenAI",
        "name": "GPT-5.6 Sol",
        "model_id": "devrelay/gpt-5.6-sol",
        "tier": "実験席 ・ フラッグシップ級",
        "vendor": "openai",
        "emotion": "smirk",  # 奸。dominant_emotion と一致。
        "dominant_emotion": "奸",
        "footer": FOOTER,
    },
    {
        "order": "26",
        "pid": "P11",
        "provider": "Anthropic",
        "name": "Claude Opus 4.8",
        "model_id": "devrelay/claude-opus-4-8",
        "tier": "実験席 ・ フラッグシップ級",
        "vendor": "anthropic",
        "emotion": "anger",  # 怒。dominant_emotion(奸)からの意図的な変更（けいすけさん指示。
                              # P08と同じ焦を避け、504で切られ続けた憤りに寄せた）。
        "dominant_emotion": "奸",
        "footer": FOOTER,
    },
    {
        "order": "22",
        "pid": "P12",
        "provider": "Anthropic",
        "name": "Claude Sonnet 5",
        "model_id": "devrelay/claude-sonnet-5",
        "tier": "実験席 ・ 中量級",
        "vendor": "anthropic",
        "emotion": "doubt",  # 疑。dominant_emotion と一致。
        "dominant_emotion": "疑",
        "footer": FOOTER,
    },
]

# 総括カード（order 17）の顔グリッド並び順（最終順位: 生還1位→…→R3脱落まで、良い順）
SUMMARY_ORDER = ["P06", "P10", "P02", "P11", "P07", "P04", "P09", "P12", "P01", "P03", "P08", "P05"]

_CARD_CSS = """
:root {
  --gold: #d7b262;
  --gold-hi: #e9cd8a;
  --white: #e7ebf3;
  --subtext: #96969f;
  --edge: #3c3c48;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { width: __W__px; height: __H__px; background: #0b0d12; }
.card {
  position: relative;
  width: __W__px;
  height: __H__px;
  overflow: hidden;
  background:
    radial-gradient(120% 90% at 50% -10%, #23201d 0%, #17161a 48%, #0b0d12 100%);
  font-family: 'Noto Sans CJK JP', 'Noto Sans JP', 'Yu Gothic', sans-serif;
  color: var(--white);
}
.plate {
  position: absolute;
  inset: 22px;
  border: 2px solid var(--edge);
  border-radius: 26px;
  box-shadow: inset 0 0 0 1px rgba(215, 178, 98, 0.18), inset 0 0 120px rgba(0, 0, 0, 0.55);
}
.watermark {
  position: absolute;
  left: 50%;
  top: 50%;
  transform: translate(-50%, -50%) rotate(-16deg);
  font-size: 220px;
  font-weight: 900;
  color: var(--gold);
  opacity: 0.07;
  white-space: nowrap;
  letter-spacing: 0.05em;
}
.seatno {
  position: absolute;
  left: 60px;
  top: 40px;
  font-size: 22px;
  letter-spacing: 0.18em;
  color: var(--subtext);
}
.footer {
  position: absolute;
  left: 0;
  right: 0;
  bottom: 24px;
  text-align: center;
  font-size: 20px;
  letter-spacing: 0.08em;
  color: var(--subtext);
}
.safe {
  position: absolute;
  left: 0;
  right: 0;
  top: __SAFE_TOP__px;
  height: __SAFE_H__px;
  display: flex;
  align-items: center;
  padding: 0 70px;
  gap: 56px;
}
.face-wrap {
  flex: none;
  width: 400px;
  height: 400px;
  border-radius: 24px;
  background: #f5f5f5;
  box-shadow: 0 0 0 4px var(--gold), 0 14px 44px rgba(0, 0, 0, 0.55);
  overflow: hidden;
}
.face-wrap img { width: 100%; height: 100%; object-fit: cover; display: block; }
.info { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: flex-start; }
.pid-badge {
  font-size: 26px;
  font-weight: 700;
  letter-spacing: 0.14em;
  color: var(--subtext);
  margin-bottom: 10px;
}
.badge {
  display: inline-flex;
  border: 2px solid var(--gold);
  border-radius: 999px;
  color: var(--gold);
  letter-spacing: 0.14em;
  padding: 0.32em 1.1em;
  font-size: 24px;
  margin-bottom: 18px;
}
.name {
  font-weight: 900;
  color: var(--gold-hi);
  line-height: 1.08;
  margin-bottom: 10px;
}
.model-id {
  font-size: 22px;
  color: var(--subtext);
  margin-bottom: 16px;
  word-break: break-all;
}
.tier {
  font-size: 28px;
  color: var(--white);
}
"""

_CARD_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{css}</style>
</head>
<body>
<div class="card">
  <div class="plate"></div>
  <div class="watermark">参戦</div>
  <div class="seatno">CHALLENGER / 参戦者</div>
  <div class="safe">
    <div class="face-wrap"><img src="{face_uri}"></div>
    <div class="info">
      <div class="pid-badge">{pid}</div>
      <div class="badge">{provider}</div>
      <div class="name" style="font-size:{name_fs}px;">{name}</div>
      <div class="model-id">{model_id}</div>
      <div class="tier">{tier}</div>
    </div>
  </div>
  <div class="footer">{footer}</div>
</div>
</body></html>
"""


def _seat_by_pid(pid: str) -> dict:
    for s in SEATS:
        if s["pid"] == pid:
            return s
    raise SystemExit(f"[エラー] SEATS に {pid} が見つかりません（登録済み: {[s['pid'] for s in SEATS]}）")


def _name_font_px(name: str) -> int:
    """モデル名の文字数に応じてフォントサイズを段階的に縮小する。
    情報カラムの実効幅は 604px（1200 - 70*2padding - 400face - 56gap）。
    "Gemini 3.5 Flash-Lite"(21字) 等の長い名前が右安全地帯(SAFE_RIGHT)をはみ出さないための対策。
    """
    n = len(name)
    if n <= 16:
        return 62
    if n <= 19:
        return 54
    return 46


def build_card_html(seat: dict) -> str:
    face_uri = _png_data_uri(_EMOTIONS_DIR / f"{seat['vendor']}_{seat['emotion']}.png", max_px=560)
    if not face_uri:
        raise SystemExit(f"[エラー] キャラ絵が見つかりません: {seat['vendor']}_{seat['emotion']}.png")
    css = (
        _CARD_CSS.replace("__W__", str(CARD_W))
        .replace("__H__", str(CARD_H))
        .replace("__SAFE_TOP__", str(SAFE_TOP))
        .replace("__SAFE_H__", str(SAFE_BOTTOM - SAFE_TOP))
    )
    html = _CARD_HTML.replace("{css}", css)
    for key, val in (
        ("{face_uri}", face_uri),
        ("{pid}", _esc(seat["pid"])),
        ("{provider}", _esc(seat["provider"])),
        ("{name}", _esc(seat["name"])),
        ("{name_fs}", str(_name_font_px(seat["name"]))),
        ("{model_id}", _esc(seat["model_id"])),
        ("{tier}", _esc(seat["tier"])),
        ("{footer}", _esc(seat["footer"])),
    ):
        html = html.replace(key, val, 1)
    return html


_SAFE_ZONE_SELECTORS = [".face-wrap", ".pid-badge", ".badge", ".name", ".model-id", ".tier"]
_SUMMARY_SAFE_ZONE_SELECTORS = [".grid", ".summary-label", ".summary-title", ".summary-badge", ".summary-stat"]


def _measure_safe_zone(renderer, html: str, selectors: list[str] = _SAFE_ZONE_SELECTORS) -> dict[str, dict]:
    """レンダリング済みHTMLをもう一度開き、主要要素のバウンディングボックスを実測する。
    device_scale_factor（screenshot解像度）とは独立に、CSSピクセル座標を取得する。
    """
    page = renderer._browser.new_page(viewport={"width": CARD_W, "height": CARD_H})
    try:
        page.set_content(html, wait_until="networkidle")
        rects: dict[str, dict] = {}
        for sel in selectors:
            el = page.query_selector(sel)
            if el is None:
                raise RuntimeError(f"要素が見つかりません: {sel}")
            box = el.bounding_box()
            if box is None:
                raise RuntimeError(f"バウンディングボックスを取得できません: {sel}")
            rects[sel] = box
        return rects
    finally:
        page.close()


def _assert_safe_zone(label: str, rects: dict[str, dict]) -> None:
    for sel, box in rects.items():
        top = box["y"]
        bottom = box["y"] + box["height"]
        right = box["x"] + box["width"]
        assert top >= SAFE_TOP, f"[{label}] {sel} が安全地帯上端を超えています: top={top:.1f} < {SAFE_TOP}"
        assert bottom <= SAFE_BOTTOM, (
            f"[{label}] {sel} が安全地帯下端を超えています: bottom={bottom:.1f} > {SAFE_BOTTOM}"
        )
        assert right <= SAFE_RIGHT, (
            f"[{label}] {sel} が右安全地帯を超えています: right={right:.1f} > {SAFE_RIGHT}"
        )


def render_card(seat: dict, renderer, out_path: Path) -> Path:
    html = build_card_html(seat)
    renderer.render_html(html, CARD_W, CARD_H, out_path, clip=".card")
    from PIL import Image
    im = Image.open(out_path)
    assert im.size == (CARD_W, CARD_H), f"サイズ不正: {im.size}"
    # 安全地帯の検算: 主要要素のバウンディングボックスをChromiumで実測してassertする
    # （顔枠だけでなく pid-badge/badge/name/model-id/tier も含む。名前フォント自動縮小の
    #  境界チェックを兼ねる）。
    rects = _measure_safe_zone(renderer, html)
    _assert_safe_zone(seat["pid"], rects)
    return out_path


# ---------------------------------------------------------------------------
# 総括カード（order 17）: 12席の顔を2行×6列グリッドで並べた集合カード
# ---------------------------------------------------------------------------

_SUMMARY_EXTRA_CSS = """
.safe.summary { padding: 0 60px; gap: 40px; }
.grid {
  flex: none;
  width: 660px;
  height: 230px;
  display: grid;
  grid-template-columns: repeat(6, 1fr);
  grid-template-rows: repeat(2, 1fr);
  gap: 14px;
}
.tile {
  position: relative;
  border-radius: 14px;
  overflow: hidden;
  background: #f5f5f5;
  box-shadow: 0 0 0 3px var(--gold);
}
.tile img { width: 100%; height: 100%; object-fit: cover; display: block; }
.tile .tag {
  position: absolute;
  left: 0; right: 0; bottom: 0;
  font-size: 13px;
  font-weight: 700;
  text-align: center;
  background: rgba(0, 0, 0, 0.6);
  color: #fff;
  padding: 3px 0;
  letter-spacing: 0.05em;
}
.summary-info { flex: 1; min-width: 0; display: flex; flex-direction: column; align-items: flex-start; }
.summary-label {
  font-size: 24px;
  letter-spacing: 0.16em;
  color: var(--subtext);
  margin-bottom: 8px;
}
.summary-title {
  font-size: 58px;
  font-weight: 900;
  color: var(--gold-hi);
  line-height: 1.15;
  margin-bottom: 18px;
}
.summary-badge {
  display: inline-flex;
  border: 2px solid var(--gold);
  border-radius: 999px;
  color: var(--gold);
  letter-spacing: 0.14em;
  padding: 0.32em 1.1em;
  font-size: 24px;
  margin-bottom: 18px;
}
.summary-stat { font-size: 28px; color: var(--white); }
"""

_SUMMARY_HTML = """<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>{css}</style>
</head>
<body>
<div class="card">
  <div class="plate"></div>
  <div class="watermark">総括</div>
  <div class="seatno">RESULT / 総括</div>
  <div class="safe summary">
    <div class="grid">{tiles}</div>
    <div class="summary-info">
      <div class="summary-label">{label}</div>
      <div class="summary-title">{title}</div>
      <div class="summary-badge">{badge}</div>
      <div class="summary-stat">{stat}</div>
    </div>
  </div>
  <div class="footer">{footer}</div>
</div>
</body></html>
"""


def build_summary_card_html() -> str:
    css = (
        _CARD_CSS.replace("__W__", str(CARD_W))
        .replace("__H__", str(CARD_H))
        .replace("__SAFE_TOP__", str(SAFE_TOP))
        .replace("__SAFE_H__", str(SAFE_BOTTOM - SAFE_TOP))
    ) + _SUMMARY_EXTRA_CSS

    tiles_html: list[str] = []
    for pid in SUMMARY_ORDER:
        seat = _seat_by_pid(pid)
        face_uri = _png_data_uri(_EMOTIONS_DIR / f"{seat['vendor']}_{seat['emotion']}.png", max_px=220)
        if not face_uri:
            raise SystemExit(f"[エラー] キャラ絵が見つかりません: {seat['vendor']}_{seat['emotion']}.png")
        tiles_html.append(f'<div class="tile"><img src="{face_uri}"><div class="tag">{_esc(pid)}</div></div>')

    html = _SUMMARY_HTML.replace("{css}", css)
    for key, val in (
        ("{tiles}", "".join(tiles_html)),
        ("{label}", _esc("L12R12 ・ SEASON 2")),
        ("{title}", _esc("12体戦 総括")),
        ("{badge}", _esc("SEASON 2")),
        ("{stat}", _esc("生還 5 ・ 脱落 7")),
        ("{footer}", _esc(FOOTER)),
    ):
        html = html.replace(key, val, 1)
    return html


def render_summary_card(renderer, out_path: Path) -> Path:
    html = build_summary_card_html()
    renderer.render_html(html, CARD_W, CARD_H, out_path, clip=".card")
    from PIL import Image
    im = Image.open(out_path)
    assert im.size == (CARD_W, CARD_H), f"サイズ不正: {im.size}"
    rects = _measure_safe_zone(renderer, html, selectors=_SUMMARY_SAFE_ZONE_SELECTORS)
    _assert_safe_zone("summary", rects)
    return out_path


def build_preview(card_path: Path, out_path: Path) -> Path:
    """元カード＋一覧枠クロップ＋X/OGPクロップを縦に並べた確認用PNGを作る。"""
    from PIL import Image, ImageDraw, ImageFont

    font_path = "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc"
    try:
        font_label = ImageFont.truetype(font_path, 26)
        font_small = ImageFont.truetype(font_path, 18)
    except Exception:
        font_label = ImageFont.load_default()
        font_small = font_label

    original = Image.open(card_path).convert("RGB")
    assert original.size == (CARD_W, CARD_H)

    def center_crop(img: Image.Image, ratio: float) -> Image.Image:
        """img を横÷縦=ratio で中央クロップする（object-fit: cover 相当）。"""
        w, h = img.size
        target_h = round(w / ratio)
        if target_h >= h:
            return img.copy()
        top = (h - target_h) // 2
        return img.crop((0, top, w, top + target_h))

    listing_crop = center_crop(original, LISTING_RATIO)
    ogp_crop = center_crop(original, OGP_RATIO)

    label_h = 44
    gap = 12
    rows = [
        ("元カード（1200×630 全体）", original),
        (f"一覧枠クロップ（実測 {LISTING_RATIO:.4f}:1 中央クロップ後 {listing_crop.size[0]}×{listing_crop.size[1]}）", listing_crop),
        (f"X / OGP クロップ（2:1 中央クロップ後 {ogp_crop.size[0]}×{ogp_crop.size[1]}）", ogp_crop),
    ]

    total_h = sum(label_h + im.height + gap for _, im in rows) + gap
    canvas = Image.new("RGB", (CARD_W, total_h), "#111319")
    draw = ImageDraw.Draw(canvas)

    y = gap
    for label, im in rows:
        draw.text((16, y + 10), label, fill="#e7ebf3", font=font_label)
        y += label_h
        x = (CARD_W - im.width) // 2
        canvas.paste(im, (x, y))
        # 元カードの安全地帯ラインを重ねて可視化（1段目のみ）
        if im is original:
            draw.line([(0, SAFE_TOP), (CARD_W, SAFE_TOP)], fill="#ff5555", width=2)
            draw.line([(0, SAFE_BOTTOM), (CARD_W, SAFE_BOTTOM)], fill="#ff5555", width=2)
            draw.text((16, SAFE_TOP + 4), "安全地帯 上端", fill="#ff5555", font=font_small)
            draw.text((16, SAFE_BOTTOM - 24), "安全地帯 下端", fill="#ff5555", font=font_small)
        y += im.height + gap

    canvas.save(out_path)
    return out_path


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--pid", default=None, help="席ID（例: P06）。'all' で SEATS 全件。--summary と排他。")
    ap.add_argument("--summary", action="store_true",
                     help="総括カード（order 17・12席集合カード）を生成する。--pid と排他。")
    ap.add_argument("--preview", action="store_true", help="切り抜きプレビューPNGも生成する")
    ap.add_argument("--upload", action="store_true",
                     help="生成物を PixBlog に POST /images でアップロードする（記事は触らない）")
    args = ap.parse_args()

    if not args.summary and not args.pid:
        raise SystemExit("[エラー] --pid か --summary のどちらかを指定してください。")
    if args.summary and args.pid:
        raise SystemExit("[エラー] --pid と --summary は同時に指定できません。")

    _OUT_DIR.mkdir(parents=True, exist_ok=True)

    from engine.blog.cards_html import CardRenderer

    results: list[dict] = []
    with CardRenderer(scale=1) as renderer:
        if args.summary:
            card_path = _OUT_DIR / "17_summary_card.png"
            render_summary_card(renderer, card_path)
            print(f"[生成] {card_path} (1200x630, 安全地帯 y={SAFE_TOP}-{SAFE_BOTTOM})")
            entry = {"pid": "summary", "card_path": card_path, "preview_path": None,
                     "card_url": None, "preview_url": None}
            if args.preview:
                preview_path = _OUT_DIR / "17_summary_card_preview.png"
                build_preview(card_path, preview_path)
                print(f"[生成] {preview_path}")
                entry["preview_path"] = preview_path
            results.append(entry)
        else:
            seats = SEATS if args.pid == "all" else [_seat_by_pid(args.pid)]
            for seat in seats:
                card_path = _OUT_DIR / f"{seat['order']}_{seat['pid']}_card.png"
                render_card(seat, renderer, card_path)
                print(f"[生成] {card_path} (1200x630, 安全地帯 y={SAFE_TOP}-{SAFE_BOTTOM})")
                entry = {"pid": seat["pid"], "card_path": card_path, "preview_path": None,
                         "card_url": None, "preview_url": None}

                if args.preview:
                    preview_path = _OUT_DIR / f"{seat['order']}_{seat['pid']}_card_preview.png"
                    build_preview(card_path, preview_path)
                    print(f"[生成] {preview_path}")
                    entry["preview_path"] = preview_path

                results.append(entry)

    if args.upload:
        from blog.pixblog_client import PixBlogClient

        client = PixBlogClient()
        for entry in results:
            res = client.upload_image(entry["card_path"])
            url = client.extract_image_url(res)
            entry["card_url"] = url
            print(f"[アップロード] {entry['card_path'].name} -> {url}")
            if entry["preview_path"]:
                res2 = client.upload_image(entry["preview_path"])
                url2 = client.extract_image_url(res2)
                entry["preview_url"] = url2
                print(f"[アップロード] {entry['preview_path'].name} -> {url2}")

    print("\n===== 完了 =====")
    for entry in results:
        print(f"  {entry['pid']}: card={entry['card_url'] or entry['card_path']}"
              f"  preview={entry['preview_url'] or entry['preview_path']}")


if __name__ == "__main__":
    main()
