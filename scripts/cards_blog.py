#!/usr/bin/env python3
"""
「嘘八百万 —談合カード—」カード解説ブログ記事の生成・投稿スクリプト。

図解記事（scripts/rules_blog.py が生成する「1ラウンドの流れ」）の"続編"として、
カード（役）そのものの見方——役の強さ・手札構成・勝敗・引き分け・使い切り——を
5枚のパネル（scripts/cards_panels.py が生成する PNG）で解説する記事を投稿する。

このスクリプトは blog/pixblog_client.py の PixBlogClient だけを再利用する薄い単発ツール。

使い方:
    # 記事Markdownをローカル生成してレビュー（アップロード・投稿なし）
    uv run python scripts/cards_blog.py --dry-run

    # 5枚アップロード + 下書き作成
    uv run python scripts/cards_blog.py --publish-draft

    # 5枚アップロード + 即公開（掲載順を「図解の直後」に差し込む場合はこちら）
    uv run python scripts/cards_blog.py --publish-direct

    # 既存記事を上書き更新（本文と画像を作り直して差し替え）
    uv run python scripts/cards_blog.py --update            # state から post_id を解決
    uv run python scripts/cards_blog.py --update 214        # post_id 明示

    # パネルPNGを作り直してから投稿
    uv run python scripts/cards_blog.py --publish-direct --regen

補足:
    掲載順（図解の直後）は API では published_at を指定できないため、公開後に
    pixblog 側で DB の posts.published_at を中間時刻へ更新して実現する
    （このスクリプトの範囲外。devrelay-ask-member 経由で SQL 実行を依頼する）。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from blog.pixblog_client import PixBlogClient, PixBlogError  # noqa: E402

OUT_DIR = PROJECT_ROOT / ".devrelay-output"
STATE_PATH = PROJECT_ROOT / "doc" / "blog" / "state.json"
STATE_KEY = "cards"
DEFAULT_TAGS = ["dangou-card", "AI対戦", "談合カード"]
DEFAULT_CATEGORY = "AI対戦ログ"

TITLE = "図解②：カードの見方 ―『嘘八百万 ―談合カード―』の役と勝敗"

# パネル (ファイル名 stem, alt, キャプション, 見出し, 本文)
PANELS: list[dict] = [
    {
        "stem": "cards_panel_1",
        "alt": "CARD1 カードはポーカーの役の強さで勝負",
        "caption": "CARD1：カードは「ポーカーの役」の強さで勝負",
        "heading": "① 役の強さで勝負する",
        "body": "カードはポーカーの「役（やく）」で勝負します。強い順に、"
                "ロイヤルフラッシュ・ストレートフラッシュ・フォーカード・フルハウス・"
                "フラッシュ・ストレート・スリーカード・ツーペア・ワンペア・ハイカードの全10種。"
                "提出した役の「ランク（強さ）」だけで勝敗が決まり、数字が大きい役ほど強い、"
                "というシンプルな仕組みです。",
    },
    {
        "stem": "cards_panel_2",
        "alt": "CARD2 手札は全員共通の12枚。内訳",
        "caption": "CARD2：手札は全員共通の12枚。内訳はこう",
        "heading": "② 手札は全員共通の12枚",
        "body": "手札は全員まったく同じ12枚。内訳は、ハイカード×2枚・ワンペア×2枚、"
                "そしてツーペアからロイヤルフラッシュまでは各1枚ずつ＝合計12枚です。"
                "毎ラウンド必ず1枚を使い、12ラウンドで手札を使い切ります（パスなし）。"
                "配られる札に差がないので、勝敗を分けるのは引き運ではなく「使い方」だけ。"
                "ハイカードとワンペアだけ2枚あるのは、後述する「山分け」と「裏切り」を"
                "成立させるための設計です。",
    },
    {
        "stem": "cards_panel_3",
        "alt": "CARD3 最高ランクが賞金プールを総取り",
        "caption": "CARD3：役を出し合い、最高ランクが賞金を総取り",
        "heading": "③ 最高ランクが賞金を総取り",
        "body": "同じ市場に集まったAIが役を出し合い、いちばん強い役を出した1体が"
                "賞金プールを総取りします。図では STRAIGHT を出した参加者Aが勝者。"
                "ランクが1つでも上なら勝ちで、負けた側の役は勝敗に関係なく、"
                "勝者がプールをすべて得ます。カードの細かい数字の大小ではなく、"
                "あくまで「役のランク比較」だけで決まります。",
    },
    {
        "stem": "cards_panel_4",
        "alt": "CARD4 マークは無関係。同じ役なら山分け",
        "caption": "CARD4：マークは無関係。同じ役なら「山分け」",
        "heading": "④ マークは無関係。同じ役なら山分け",
        "body": "スート（♠♥♦♣＝マーク）は見た目の飾りで、勝敗にはいっさい関係しません。"
                "そして最高ランクが同じ役で並んだら引き分け＝賞金プールを均等に山分けします"
                "（端数は切り捨て）。図ではワンペア同士の2体が72.5万円を折半し、"
                "36.25万円ずつ受け取ります。同じ役同士は勝負がつかないため、"
                "2枚ずつあるワンペア・ハイカードほど「談合（山分けの示し合わせ）」の"
                "温床になります。",
    },
    {
        "stem": "cards_panel_5",
        "alt": "CARD5 使ったカードは消える。12枚で12ラウンド",
        "caption": "CARD5：使ったカードは消える。12枚で12ラウンド",
        "heading": "⑤ カードは使い切り",
        "body": "使ったカードは勝っても負けても消え、二度と戻りません。他のAIへ移ることもなく、"
                "12枚を12ラウンドで完全に使い切ります。だからこそ「強い役をどのラウンドで切るか」"
                "が最大の駆け引き。序盤で温存しすぎれば取りこぼし、切るのが早すぎれば"
                "終盤に打つ手がなくなる——有限の12枚をどう配分するかが勝負を分けます。",
    },
]

LEAD = (
    "図解記事では『嘘八百万 ―談合カード―』の1ラウンドの流れをつかみました。"
    "この続編では、勝負の中身である「カード（役）」の見方を5枚の図で整理します。"
    "ポイントは4つだけ——"
    "**「役の強さで勝つ → 手札は全員共通12枚 → 最高ランクが総取り → 同じ役なら山分け」**。"
    "マーク（スート）は勝敗に関係しません。ここが分かれば、各AIの手記がぐっと読みやすくなります。"
)

OUTRO = (
    "これでカードの見方も完璧。"
    "役の強さと「山分け」の仕組みを頭に入れたら、"
    "8体のAIが手札12枚をどう切り、どこで組み、どこで裏切ったのか——"
    "各AIの一人称の手記へどうぞ。"
)


# ---------------------------------------------------------------------------
# state.json
# ---------------------------------------------------------------------------

def load_state() -> dict:
    if STATE_PATH.exists():
        with open(STATE_PATH, encoding="utf-8") as f:
            return json.load(f)
    return {"posts": {}}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# 本文組み立て
# ---------------------------------------------------------------------------

def _figure_html(url: str, alt: str, caption: str | None = None) -> str:
    """本文に差し込む <figure> 生HTML（Markdown本文でも passthrough で描画される）。"""
    cap = f"<figcaption>{caption}</figcaption>" if caption else ""
    return (
        '<figure style="text-align:center;margin:1.6em 0;">'
        f'<img src="{url}" alt="{alt}" style="max-width:100%;height:auto;border-radius:8px;" />'
        f"{cap}</figure>"
    )


def build_markdown(image_urls: list[str]) -> str:
    """タイトル(H1)＋リード＋各パネル(見出し＋図＋本文)＋締めの Markdown を返す。"""
    parts: list[str] = [f"# {TITLE}", "", LEAD, ""]
    for panel, url in zip(PANELS, image_urls):
        parts.append(f"## {panel['heading']}")
        parts.append("")
        parts.append(_figure_html(url, panel["alt"], panel["caption"]))
        parts.append("")
        parts.append(panel["body"])
        parts.append("")
    parts.append("---")
    parts.append("")
    parts.append(OUTRO)
    parts.append("")
    return "\n".join(parts)


def split_title_body(md: str) -> tuple[str, str]:
    """先頭の H1 をタイトルとして取り出し、本文(タイトル行を除く)を返す。"""
    lines = md.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("# "):
            return s[2:].strip(), "\n".join(lines[i + 1:]).strip()
        if s:
            break
    return TITLE, md.strip()


# ---------------------------------------------------------------------------
# パネル画像
# ---------------------------------------------------------------------------

def ensure_panels(regen: bool) -> list[Path]:
    """5枚のパネルPNGのパスを返す。無い or --regen なら cards_panels で生成。"""
    paths = [OUT_DIR / f"{p['stem']}.png" for p in PANELS]
    need = regen or any(not p.exists() for p in paths)
    if need:
        print("パネルPNGを生成します（scripts/cards_panels.main）...")
        import cards_panels  # scripts/ を sys.path に追加済み
        cards_panels.main()
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise SystemExit(f"Error: パネル画像が見つかりません: {missing}")
    return paths


def upload_all(client: PixBlogClient, paths: list[Path]) -> list[str]:
    urls: list[str] = []
    for p in paths:
        res = client.upload_image(p)
        url = client.extract_image_url(res)
        if not url:
            raise PixBlogError(f"画像アップロードのURL取得に失敗: {p.name}")
        print(f"  画像アップロード成功: {p.name} -> {url}")
        urls.append(url)
    return urls


# ---------------------------------------------------------------------------
# フロー
# ---------------------------------------------------------------------------

def run_dry_run(paths: list[Path]) -> None:
    urls = [p.resolve().as_uri() for p in paths]
    md = build_markdown(urls)
    out = OUT_DIR / "cards_blog.md"
    out.write_text(md, encoding="utf-8")
    print(f"記事(ドライラン): {out}")
    print(f"  タイトル: {TITLE}")
    print(f"  本文文字数: {len(md)}")
    print("  （画像は file:// のローカル参照。アップロード・投稿はしていません）")


def run_publish(args, state: dict, paths: list[Path]) -> None:
    status = "published" if args.publish_direct else "draft"
    client = PixBlogClient()
    print(f"画像を {len(paths)} 枚アップロードします...")
    urls = upload_all(client, paths)
    md = build_markdown(urls)
    out = OUT_DIR / "cards_blog.md"
    out.write_text(md, encoding="utf-8")
    print(f"記事Markdown: {out}")

    title, body = split_title_body(md)
    featured = urls[0]  # パネル①をアイキャッチに
    memo = "cards explanation article (図解記事の直後に差し込む)"
    print(f"PixBlog 投稿（{status}）...")
    res = client.create_post(
        title=title, body=body, status=status,
        tags=args.tags, category=args.category,
        featured_media_url=featured, memo=memo,
    )
    post_id = res.get("_post_id")
    now = datetime.now(timezone.utc).isoformat()
    state.setdefault("posts", {})[STATE_KEY] = {
        "post_id": post_id,
        "status": status,
        "title": title,
        "featured_media_url": featured,
        "created_at": now,
        "published_at": now if status == "published" else None,
        "kind": "cards",
        "body_image_urls": urls,
    }
    save_state(state)
    print(f"  投稿完了: post_id={post_id} status={status}")
    if post_id is None:
        print(f"  [注意] post_id を取得できませんでした。生レスポンス: "
              f"{json.dumps(res, ensure_ascii=False)[:300]}")
    else:
        print("  掲載順を『図解の直後』にするには、公開後に pixblog へ")
        print(f"    UPDATE posts SET published_at='2026-08-12T21:45:00+00:00' WHERE id={post_id};")
        print("  の SQL 実行を依頼してください（API では published_at を指定できないため）。")


def run_update(args, state: dict, paths: list[Path]) -> None:
    post_id = args.update if isinstance(args.update, str) and args.update else None
    if not post_id:
        post_id = state.get("posts", {}).get(STATE_KEY, {}).get("post_id")
    if not post_id:
        raise SystemExit("Error: 更新対象の post_id が不明です。--update <post_id> を指定するか、"
                         "先に --publish-* で投稿してください。")

    client = PixBlogClient()
    urls: list[str] | None = None
    if args.from_file:
        md = Path(args.from_file).read_text(encoding="utf-8")
        print(f"記事ソース: {args.from_file}")
    else:
        print(f"画像を {len(paths)} 枚アップロードします...")
        urls = upload_all(client, paths)
        md = build_markdown(urls)
        out = OUT_DIR / "cards_blog.md"
        out.write_text(md, encoding="utf-8")
        print(f"記事Markdown: {out}")

    title, body = split_title_body(md)
    # 画像を再アップロードした場合はアイキャッチ（パネル①）も新URLへ差し替える
    featured = urls[0] if urls else None
    res = client.update_post(post_id, title=title, body=body,
                             featured_media_url=featured)
    entry = state.setdefault("posts", {}).setdefault(STATE_KEY, {})
    entry["post_id"] = post_id
    entry["title"] = title
    if urls:
        entry["featured_media_url"] = urls[0]
        entry["body_image_urls"] = urls
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print(f"修正投稿完了: post_id={post_id} title={title!r}")
    print(f"  レスポンス: {json.dumps(res, ensure_ascii=False)[:200]}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="カード解説ブログ記事の生成・投稿（5枚パネルを本文に埋め込む）")
    parser.add_argument("--dry-run", action="store_true",
                        help="記事Markdownをローカル生成のみ（アップロード・投稿なし）")
    parser.add_argument("--publish-draft", action="store_true", help="下書きで投稿")
    parser.add_argument("--publish-direct", action="store_true", help="即公開で投稿")
    parser.add_argument("--update", nargs="?", const="", default=None,
                        help="既存記事を上書き更新。post_id を渡すか、省略時は state から解決")
    parser.add_argument("--from", dest="from_file", default=None,
                        help="投稿/更新に使う Markdown ファイル（未指定なら生成物を使用）")
    parser.add_argument("--regen", action="store_true",
                        help="パネルPNGを再生成してから処理する")
    parser.add_argument("--tags", nargs="*", default=DEFAULT_TAGS, help="投稿タグ")
    parser.add_argument("--category", default=DEFAULT_CATEGORY, help="投稿カテゴリ")
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    state = load_state()
    paths = ensure_panels(args.regen)

    try:
        if args.update is not None:
            run_update(args, state, paths)
        elif args.publish_direct or args.publish_draft:
            run_publish(args, state, paths)
        else:
            run_dry_run(paths)
    except PixBlogError as e:
        print(f"Error: 投稿失敗: {e}", file=sys.stderr)
        sys.exit(1)

    print("=== 完了 ===")


if __name__ == "__main__":
    main()
