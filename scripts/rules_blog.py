#!/usr/bin/env python3
"""
「嘘八百万 —談合カード—」ルール説明ブログ記事の生成・投稿スクリプト。

5枚のルール説明パネル（scripts/rules_panels.py が生成する PNG）を本文に埋め込み、
オープニング記事と各AIの個別記事の間に置く "ルール解説パート" を PixBlog へ投稿する。

このスクリプトは scripts/blog.py 本体（engine.* を多数 import する重量級）には依存せず、
blog/pixblog_client.py の PixBlogClient だけを再利用する薄い単発ツール。

使い方:
    # 記事Markdownをローカル生成してレビュー（アップロード・投稿なし）
    uv run python scripts/rules_blog.py --dry-run

    # 5枚アップロード + 下書き作成
    uv run python scripts/rules_blog.py --publish-draft

    # 5枚アップロード + 即公開（掲載順を「オープニングとGrokの間」に差し込む場合はこちら）
    uv run python scripts/rules_blog.py --publish-direct

    # 既存記事を上書き更新（本文と画像を作り直して差し替え）
    uv run python scripts/rules_blog.py --update            # state から post_id を解決
    uv run python scripts/rules_blog.py --update 212        # post_id 明示

    # パネルPNGを作り直してから投稿
    uv run python scripts/rules_blog.py --publish-direct --regen

補足:
    掲載順（オープニングとGrokの間）は API では published_at を指定できないため、
    公開後に pixblog 側で DB の posts.published_at を中間時刻へ更新して実現する
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
STATE_KEY = "rules"
DEFAULT_TAGS = ["dangou-card", "AI対戦", "談合カード"]
DEFAULT_CATEGORY = "AI対戦ログ"

TITLE = "図解：はじめての『嘘八百万 ―談合カード―』 ―― 1ラウンドの流れを5枚で"

# パネル (ファイル名 stem, alt, キャプション, 見出し, 本文)
PANELS: list[dict] = [
    {
        "stem": "rules_panel_1",
        "alt": "STEP1 毎ラウンド、賞金つきの市場が3つ開く",
        "caption": "STEP1：毎ラウンド、賞金つきの市場が3つ開く",
        "heading": "① 市場が3つ開く",
        "body": "毎ラウンド、賞金つきの市場が3つ開きます。8体のAIは全員、"
                "そのいずれかに必ず参加します（パスはできません）。"
                "市場ごとに基本賞金が違うので、「どこに乗るか」がまず最初の駆け引きです。",
    },
    {
        "stem": "rules_panel_2",
        "alt": "STEP2 市場・手札1枚・参加費10万円を、伏せて提出",
        "caption": "STEP2：市場・手札1枚・参加費10万円を、伏せて提出",
        "heading": "② 伏せて出す（秘密コミット）",
        "body": "参加する市場・手札1枚・参加費10万円を、他のAIに見えないよう"
                "秘密裏に提出します。締切まで、誰がどの市場に何のカードで参加したかは分かりません。"
                "手札は全員共通の12枚で、毎ラウンド必ず1枚使い、12ラウンドで使い切ります。",
    },
    {
        "stem": "rules_panel_3",
        "alt": "STEP3 集まるほど、賞金プールは太る",
        "caption": "STEP3：集まるほど、賞金プールは太る",
        "heading": "③ プールが太る（ハニーポット）",
        "body": "参加費10万円は、そのまま市場の賞金プールに加算されます。"
                "基本賞金32.5万円＋参加費10万円×4体＝72.5万円。"
                "人気市場に人を集めて自分で総取りを狙う「ハニーポット」戦術が成立します。",
    },
    {
        "stem": "rules_panel_4",
        "alt": "STEP4 最高ランクが勝つ。同点なら山分け",
        "caption": "STEP4：最高ランクが勝つ。同点なら山分け",
        "heading": "④ 最高ランクが勝つ。同点なら山分け",
        "body": "開示（Reveal）。いちばん強い手札が勝ち、賞金プールを総取りします。"
                "同じ最高ランクで並んだら山分けです。図では参加者Aと参加者Bが"
                "ONE PAIRで同点になり、72.5万円を2等分＝36.25万円ずつ。"
                "ONE PAIRとHIGH CARDだけ2枚配られているのは、この「山分けと裏切り」を狙った設計です。",
    },
    {
        "stem": "rules_panel_5",
        "alt": "STEP5 これを12ラウンド、繰り返す",
        "caption": "STEP5：これを12ラウンド、繰り返す",
        "heading": "⑤ これを12ラウンド繰り返す",
        "body": "手札12枚を使い切るまで、12ラウンド。"
                "借金は毎ラウンド1.5%の複利で膨らんでいきます。"
                "最後に「借金0かつ現金200万円以上」で生還、届かなければ脱落です。",
    },
]

LEAD = (
    "『嘘八百万 ―談合カード―』は、8体のAIが12ラウンドの市場争奪・交渉・契約で"
    "「生還」を目指す対戦ゲームです。各AIの手記（個別記事）を読む前に、"
    "まずはゲームの流れを5枚の図でざっとつかんでおきましょう。"
    "難しいルールは後回しでOK。"
    "**「市場に入る → 伏せて出す → プールが太る → 山分け → これを12回」**——"
    "それだけ分かれば十分です。"
)

OUTRO = (
    "ルールはこれで十分。"
    "では、8体のAIが実際に何を考え、どう手を組み、どう裏切り、どう散っていったのか——"
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
    """5枚のパネルPNGのパスを返す。無い or --regen なら rules_panels で生成。"""
    paths = [OUT_DIR / f"{p['stem']}.png" for p in PANELS]
    need = regen or any(not p.exists() for p in paths)
    if need:
        print("パネルPNGを生成します（scripts/rules_panels.main）...")
        import rules_panels  # scripts/ を sys.path に追加済み
        rules_panels.main()
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
    out = OUT_DIR / "rules_blog.md"
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
    out = OUT_DIR / "rules_blog.md"
    out.write_text(md, encoding="utf-8")
    print(f"記事Markdown: {out}")

    title, body = split_title_body(md)
    featured = urls[0]  # パネル①をアイキャッチに
    memo = "rules explanation article (opening と 個別記事の間に差し込む)"
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
        "kind": "rules",
        "body_image_urls": urls,
    }
    save_state(state)
    print(f"  投稿完了: post_id={post_id} status={status}")
    if post_id is None:
        print(f"  [注意] post_id を取得できませんでした。生レスポンス: "
              f"{json.dumps(res, ensure_ascii=False)[:300]}")
    else:
        print("  掲載順を『オープニングとGrokの間』にするには、公開後に pixblog へ")
        print(f"    UPDATE posts SET published_at='2026-08-12T12:30:00Z' WHERE id={post_id};")
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
        out = OUT_DIR / "rules_blog.md"
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
        description="ルール説明ブログ記事の生成・投稿（5枚パネルを本文に埋め込む）")
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
