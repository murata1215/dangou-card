"""
宣伝・世界観ビジュアル（HTML/CSS → PNG）生成

SVG の手置き座標をやめ、HTML/CSS の実測レイアウト（flex/grid）を Chromium で
レンダリングして PNG 化する。OGP/SNS/YouTube サムネは PNG が必須のため。

- 文字は必ずソースデータ由来（数値・フォント名を改変しない）。
- キャラ絵は既存の viewer/static/emotions/*.png を base64 data URI で埋め込む
  （新規画像生成はしない・file:// 依存を避け決定的）。
- パレット/キャッチ/ヘルパは cards_svg から流用して重複を避ける。

主要API:
- CardRenderer: Chromium を1回だけ起動して複数カードを描画する context manager。
- render_model_card / render_title_card / render_result_card /
  render_quote_card / render_lie_card / render_showdown_card
"""

from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from engine.blog import cards_svg as _c

_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

_env = Environment(
    loader=FileSystemLoader(str(_TEMPLATES_DIR)),
    autoescape=select_autoescape(["html", "xml"]),
)


def _num(v: float) -> str:
    """価格などの数値を末尾ゼロを畳んで文字列化（$0.95 / $4.0 → 4）。"""
    return f"{v:g}"


# ---------------------------------------------------------------------------
# Chromium レンダラ（1回だけ起動して使い回す）
# ---------------------------------------------------------------------------

class CardRenderer:
    """`with CardRenderer() as r: r.render(...)` で使う。Chromium を1度だけ起動。"""

    def __init__(self, scale: int = 2):
        self.scale = scale
        self._pw = None
        self._browser = None

    def __enter__(self) -> "CardRenderer":
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(args=["--force-color-profile=srgb"])
        return self

    def __exit__(self, *exc) -> None:
        try:
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    def render_html(self, html: str, width: int, height: int | None,
                    out_path: str | Path, clip: str = ".card") -> Path:
        """HTML 文字列を PNG に。height=None なら要素の実寸（可変高）で切り出す。"""
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        page = self._browser.new_page(
            viewport={"width": width, "height": height or 1024},
            device_scale_factor=self.scale,
        )
        try:
            page.set_content(html, wait_until="networkidle")
            el = page.query_selector(clip)
            if el is None:
                raise RuntimeError(f"clip selector not found: {clip}")
            el.screenshot(path=str(out_path))
        finally:
            page.close()
        return out_path

    def render_template(self, template: str, ctx: dict, width: int,
                        height: int | None, out_path: str | Path) -> Path:
        html = _env.get_template(template).render(width=width, height=height, **ctx)
        return self.render_html(html, width, height, out_path)

    # --- 各カードの薄いラッパ（cards_svg の引数を踏襲） ---------------------

    def render_model_card(
        self, *, pid: str, model_key: str, name: str, provider: str, vendor: str,
        input_price: float, output_price: float, model_id: str = "",
        emotion: str = "ease", tagline: str | None = None,
        out_path: str | Path,
    ) -> Path:
        ctx = {
            "pid": pid,
            "name": name,
            "provider": provider or vendor.title(),
            "model_id": model_id,
            "tier": _c._tier_label(model_key),
            "tagline": tagline or _c.MODEL_TAGLINES.get(model_key, "頭脳戦プラットフォーム、参戦。"),
            "pitch": _c._price_pitch(model_key, output_price),
            "input_price": _num(input_price),
            "output_price": _num(output_price),
            "face_uri": _c._face_uri(vendor, emotion, max_px=560) or "",
        }
        return self.render_template("model_card.html", ctx, 1080, 1350, out_path)

    def render_title_card(self, *, size: str = "banner", out_path: str | Path) -> Path:
        if size == "square":
            width, height = 1080, 1080
            ctx = {"title_fs": 200, "sub_fs": 46, "gap": 40}
        else:
            width, height = 1200, 630
            ctx = {"title_fs": 150, "sub_fs": 40, "gap": 26}
        return self.render_template("title_card.html", ctx, width, height, out_path)

    def render_result_card(self, standings: list[dict], *, emotion: str = "ease",
                           out_path: str | Path) -> Path:
        rows = []
        for i, r in enumerate(standings):
            result = r.get("result") or "eliminated"
            sym, _color, jp = _c._RESULT_SYMBOL.get(result, ("\u25bd", _c.DANGER, "脱落"))
            detail = _c._man(r.get("cash"))
            fr = r.get("final_round")
            if result != "survived" and fr:
                detail += f"（R{fr}）"
            rows.append({
                "rank": i + 1,
                "pid": r.get("pid", ""),
                "name": r.get("name", ""),
                "result": result,
                "sym": sym,
                "jp": jp,
                "detail": detail,
                "face_uri": _c._face_uri(r.get("vendor", "anthropic"), emotion, max_px=180) or "",
            })
        # 可変高（height=None → 要素実寸で切り出し）
        return self.render_template("result_card.html", {"rows": rows}, 1080, None, out_path)

    def render_quote_card(
        self, *, quote: str, pid: str, name: str, vendor: str,
        round_num: int | None = None, to_pid: str | None = None,
        emotion: str = "ease", out_path: str | Path,
    ) -> Path:
        ctx = {
            "quote": str(quote),
            "quote_fs": 54 if len(str(quote)) <= 40 else 44,
            "pid": pid,
            "name": name,
            "context": _quote_context(round_num, to_pid),
            "face_uri": _c._face_uri(vendor, emotion, max_px=300) or "",
        }
        return self.render_template("quote_card.html", ctx, 1080, 1080, out_path)

    def render_lie_card(
        self, *, quote: str, pid: str, name: str, vendor: str,
        round_num: int | None = None, to_pid: str | None = None,
        actual: str | None = None, emotion: str = "doubt",
        out_path: str | Path,
    ) -> Path:
        qlen = len(str(quote))
        # 宣言が長いほど小さく。『実際/ACTUAL』パネルと話者を必ず収めるため段階的に縮小。
        quote_fs = 48 if qlen <= 44 else (38 if qlen <= 72 else 32)
        ctx = {
            "quote": str(quote),
            "quote_fs": quote_fs,
            "pid": pid,
            "name": name,
            "context": _quote_context(round_num, to_pid),
            "actual": actual,
            "face_uri": _c._face_uri(vendor, emotion, max_px=240) or "",
        }
        return self.render_template("lie_card.html", ctx, 1080, 1080, out_path)

    def render_showdown_card(
        self, *, round_num: int, left: dict, right: dict, stake_yen: int,
        emotion: str = "doubt", out_path: str | Path,
    ) -> Path:
        def side(d: dict) -> dict:
            return {
                "pid": d.get("pid", ""),
                "name": d.get("name", ""),
                "face_uri": _c._face_uri(d.get("vendor", "anthropic"), emotion, max_px=480) or "",
            }

        ctx = {
            "round_num": round_num,
            "left": side(left),
            "right": side(right),
            "stake": _c._man(stake_yen),
        }
        return self.render_template("showdown_card.html", ctx, 1080, 1080, out_path)


def _quote_context(round_num: int | None, to_pid: str | None) -> str:
    parts = []
    if round_num:
        parts.append(f"R{round_num}")
    if to_pid:
        parts.append(f"DM → {to_pid}")
    return " ・ ".join(parts)
