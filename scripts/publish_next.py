#!/usr/bin/env python3
"""
publish_next.py — 連載記事の「次の1本」を選び、日次で PixBlog に公開する投稿基盤（サイクル9.8・9.8b・9.10）。

使い方:
    uv run python scripts/publish_next.py --dry-run           # 実公開せず流れを確認
    uv run python scripts/publish_next.py --dry-run --order 03  # order を明示指定して確認
    uv run python scripts/publish_next.py --check-lengths     # 16本の tweet_text / x_hook 加重文字数を検算
    uv run python scripts/publish_next.py                     # 実公開（本番）
    uv run python scripts/publish_next.py --force              # 280超過でも中断せず続行（要注意）
    uv run python scripts/publish_next.py --replay 03          # 公開済み order の投稿セットを再表示のみ

挙動概要:
  1. `doc/blog/20260824/plan.md` → `doc/blog/20260830/plan.md` の順で「## 投稿状態」節を走査し、
     状態が「原稿済（未投稿）」の最小 order を1本選ぶ（--order で明示指定も可）。
  2. その記事が本文で参照する images/*.png（1〜2枚）を PixBlog にアップロードし、
     本文中の `images/...` 参照を公開URLへ書き換えた「一時コピー」を作る
     （リポジトリ内の原稿ファイルそのものは書き換えない）。
  3. フロントマターの title / slug / category / tags で記事を公開する。
     `x_text` の末尾に半角スペース＋`#嘘八百万` を付加した文字列を `tweet_text`
     （PixBlog 側で新設された、X共有ボタンのコンポーザー本文専用カラム）に、
     `lead` をそのまま `excerpt`（SEO用。tweet_text とは別物）に設定する
     （サイクル9.8b・pipeline.md「10. X 連携」節で (a) 確定）。
     tweet_text に URL は含めない（アプリが共有時に記事URLを自動付加するため）。
     x_text/lead が無い記事ではそれぞれのキー自体を送らない。
     `x_hook`（サイクル9.10新設）は API には一切送らない。運用者が手動投稿する①用のフック文。
  4. 公開前に、excerpt（lead）へのハッシュタグ/URL混入と、tweet_text（#嘘八百万込み）+URL の
     加重文字数280超過を検証する。問題があれば公開せず中断する（280超過は --force で無視可）。
  5. 公開後、plan.md の該当行と doc/blog/INDEX.md を更新する。
  6. 最後に「本日の投稿セット」を画面出力し、同じ内容を doc/blog/today_post.md に上書き保存する。
     X は①リンクなしのフック文＋画像添付、②①へのリプライでリンク、の2段投稿を前提とする
     （サイクル9.10）。x_hook が無い記事では従来の下書き出力（x_text＋ハッシュタグ＋URL）にフォールバックする。
  7. 二重公開ガード: plan.md が「公開済」の order は対象にしない。
     さらに実公開直前に GET /posts で同一タイトルの既存記事が無いか確認し、あれば中断する。
  8. --replay NN: 公開処理・plan更新を一切行わず、plan.md 上で既に「公開済」の order について
     投稿セットを再表示するだけ（API 呼び出しなし）。未公開の order を指定すると「公開済みなし」を報告する。

このスクリプトは PixBlog の書き込みAPI（POST/PATCH/画像アップロード）を、
--dry-run / --replay 時には一切呼ばない（--dry-run は GETのみ、--replay はAPI呼び出し自体なし）。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv  # noqa: E402
load_dotenv()

from blog.pixblog_client import PixBlogClient, PixBlogError  # noqa: E402

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

DATES = ["20260824", "20260830", "20260921"]  # サイクル10.16: L12R12実験席混合戦(総括1本)を追加
BLOG_ROOT = PROJECT_ROOT / "doc" / "blog"
INDEX_PATH = BLOG_ROOT / "INDEX.md"
TODAY_POST_PATH = BLOG_ROOT / "today_post.md"  # サイクル9.10: 投稿セットの上書き保存先

STATUS_DRAFT_DONE = "原稿済（未投稿）"
STATUS_PUBLISHED_PREFIX = "公開済"

DEFAULT_TAGS = ["dangou-card", "AI対戦", "談合カード"]
DEFAULT_CATEGORY = "AI対戦ログ"

FRONTMATTER_RE = re.compile(r"^---\n(.*?)\n---\n(.*)$", re.DOTALL)
IMAGE_RE = re.compile(r"!\[([^\]]*)\]\((images/[^)]+)\)")
STATUS_TABLE_ROW_RE = re.compile(r"^\|\s*(\d+)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|\s*(.+?)\s*\|$")

X_URL_WEIGHT = 23  # X（旧Twitter）が全URLに対して一律にカウントする加重文字数
X_MAX_LEN = 280

# サイクル9.8b: PixBlog 側で posts.tweet_text が新設され、API（POST /api/v1/posts）で
# 書き込み可能になったため (a) 確定。詳細は doc/blog/pipeline.md「10. X 連携」節を参照。
X_FIELD_CONCLUSION = "a"
X_TWEET_FIELD = "tweet_text"    # X 共有ボタンのコンポーザー本文（アプリが記事URLを自動付加するため
                                 # ここには URL を含めない）
X_EXCERPT_FIELD = "excerpt"     # SEO 用（meta description / OGP / 記事カード）。tweet_text とは別物
X_HASHTAG = "#嘘八百万"          # tweet_text の末尾に半角スペース区切りで付加するハッシュタグ


# ---------------------------------------------------------------------------
# X 加重文字数
# ---------------------------------------------------------------------------

def x_weighted_len(s: str) -> int:
    """X（旧Twitter）の加重文字数。半角相当(概ね U+0000-U+10FF ほか一部記号域)は1、
    それ以外（全角含む）は2としてカウントする。"""
    total = 0
    for ch in s:
        o = ord(ch)
        if (0x0000 <= o <= 0x10FF) or (0x2000 <= o <= 0x200D) \
           or (0x2010 <= o <= 0x201F) or (0x2032 <= o <= 0x2037):
            total += 1
        else:
            total += 2
    return total


def x_draft_len(x_text: str) -> int:
    """x_text + 半角スペース + URL(23固定) の合計加重文字数（サイクル9.8時点の計測値との比較用）。"""
    return x_weighted_len(x_text) + 1 + X_URL_WEIGHT


def build_tweet_text(x_text: str) -> str:
    """x_text の末尾に半角スペース + #嘘八百万 を付加した tweet_text を組み立てる。
    URL は含めない（PixBlog アプリが共有時に記事URLを自動付加するため、ここに入れると二重になる）。
    """
    return f"{x_text} {X_HASHTAG}"


def tweet_draft_len(tweet_text: str) -> int:
    """tweet_text（#嘘八百万込み） + 半角スペース + URL(23固定) の合計加重文字数。"""
    return x_weighted_len(tweet_text) + 1 + X_URL_WEIGHT


def validate_excerpt(lead: str) -> list[str]:
    """excerpt（= lead）に混入していてはいけない要素（ハッシュタグ・URL）を検出して理由を返す。"""
    problems = []
    if "#" in lead:
        problems.append("ハッシュタグ（#）が含まれています")
    if re.search(r"https?://", lead):
        problems.append("URL（http:// または https://）が含まれています")
    return problems


def hook_len(x_hook: str) -> int:
    """x_hook 単体（URLを含めない前提）の加重文字数。280との比較にそのまま使う。"""
    return x_weighted_len(x_hook)


# ---------------------------------------------------------------------------
# サイクル9.10: 投稿セット（画像添付フック文 + リプライ用リンク）
# ---------------------------------------------------------------------------

# ファイル名から添付画像の種別を判定する接尾辞（優先度順に glob する）
_IMAGE_SUFFIX_BY_KIND = {"system": "roster", "summary": "results", "memoir": "moment"}


def _article_kind(article_name: str) -> str:
    if "_system" in article_name:
        return "system"
    if "_summary" in article_name:
        return "summary"
    return "memoir"


def select_attachment_image(date: str, order: str, article_name: str, body: str) -> Path | None:
    """投稿セット①に添付する画像のローカル絶対パスを選ぶ。

    手記（memoir）→ 同記事の `*_moment.png`、system → `*_roster.png`、summary → `*_results.png`。
    見つからなければ本文中の最初の画像参照。それも無ければ None。
    アップロード後のURLではなく、X に直接添付するためのローカル絶対パスを返す。
    """
    images_dir = BLOG_ROOT / date / "images"
    kind = _article_kind(article_name)
    suffix = _IMAGE_SUFFIX_BY_KIND[kind]
    matches = sorted(images_dir.glob(f"{order}_*{suffix}.png")) if images_dir.exists() else []
    if matches:
        return matches[0].resolve()
    images = find_images(body)
    if images:
        _, rel = images[0]
        candidate = BLOG_ROOT / date / rel
        if candidate.exists():
            return candidate.resolve()
    return None


def build_post_set(order: str, x_hook: str, title: str, url: str, image_path: Path | None) -> str:
    """指示された「投稿セット」形式の文字列を組み立てる。"""
    image_line = str(image_path) if image_path else "（見つかりません）"
    return (
        f"===== 本日の投稿セット（order {order}） =====\n"
        "【ツイート①】↓コピーして、下の画像を添付して投稿（リンクは入れない）\n"
        f"{x_hook}\n"
        "\n"
        f"  添付画像: {image_line}\n"
        "\n"
        "【ツイート②】↓①に自分でリプライして投稿\n"
        f"▶ {title}\n"
        f"{url}\n"
        "========================================="
    )


def write_today_post(header: str, post_set: str) -> None:
    """doc/blog/today_post.md に投稿セットを上書き保存する。"""
    TODAY_POST_PATH.write_text(f"{header}\n\n{post_set}\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# frontmatter / 本文パース（このプロジェクトの単純な `key: value` 形式専用の簡易パーサ）
# ---------------------------------------------------------------------------

def parse_frontmatter(md_text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(md_text)
    if not m:
        raise ValueError("フロントマターが見つかりません（--- で開始・終了する形式である必要があります）")
    fm_text, body = m.group(1), m.group(2)
    fm: dict = {}
    for line in fm_text.splitlines():
        if not line.strip() or ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        value = value.strip()
        if value.startswith("[") and value.endswith("]"):
            fm[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
        elif len(value) >= 2 and value.startswith('"') and value.endswith('"'):
            fm[key] = value[1:-1]
        else:
            fm[key] = value
    return fm, body


def find_images(body: str) -> list[tuple[str, str]]:
    """本文中の![alt](images/xxx.png) を出現順に返す。"""
    return IMAGE_RE.findall(body)


def discover_all_posts() -> list[dict]:
    """両日付の posts/*.md を全て走査し、frontmatter を付けて返す（--check-lengths 用）。"""
    entries = []
    for date in DATES:
        posts_dir = BLOG_ROOT / date / "posts"
        if not posts_dir.exists():
            continue
        for path in sorted(posts_dir.glob("*.md")):
            fm, body = parse_frontmatter(path.read_text(encoding="utf-8"))
            entries.append({"date": date, "path": path, "fm": fm, "body": body})
    entries.sort(key=lambda e: (e["date"], int(e["fm"].get("order", 0))))
    return entries


# ---------------------------------------------------------------------------
# plan.md の「## 投稿状態」節パース
# ---------------------------------------------------------------------------

def parse_status_table(plan_text: str) -> list[dict]:
    marker = "## 投稿状態"
    idx = plan_text.find(marker)
    if idx == -1:
        raise ValueError(f"'{marker}' 節が見つかりません（plan.md に未追加の可能性）")
    rows = []
    in_table = False
    for line in plan_text[idx:].splitlines():
        s = line.strip()
        if not s.startswith("|"):
            if in_table:
                break
            continue
        m = STATUS_TABLE_ROW_RE.match(s)
        if not m:
            in_table = True  # ヘッダ/区切り行
            continue
        order, file_, status, url = m.groups()
        if order == "order":
            continue
        in_table = True
        rows.append({"order": order, "file": file_.strip("`"), "status": status, "url": url})
    return rows


def select_next(order_override: str | None):
    """(date, plan_path, plan_text, row) を返す。見つからなければ終了する。"""
    for date in DATES:
        plan_path = BLOG_ROOT / date / "plan.md"
        text = plan_path.read_text(encoding="utf-8")
        rows = parse_status_table(text)
        rows.sort(key=lambda r: int(r["order"]))
        for row in rows:
            if order_override is not None and row["order"] != order_override:
                continue
            if row["status"].startswith(STATUS_PUBLISHED_PREFIX):
                print(f"[中断] order={row['order']} は既に「{row['status']}」です（二重公開ガード）。")
                sys.exit(1)
            if order_override is not None or row["status"] == STATUS_DRAFT_DONE:
                return date, plan_path, text, row
        if order_override is not None:
            continue
    print(f"[中断] 対象の記事が見つかりません（order指定: {order_override!r}）。")
    sys.exit(1)


# ---------------------------------------------------------------------------
# 二重公開ガード(2): API上の同一タイトル既存記事チェック（読み取りのみ）
# ---------------------------------------------------------------------------

def find_existing_post_by_title(client: PixBlogClient, title: str) -> dict | None:
    page = 1
    while True:
        res = client._request("GET", f"/posts?page={page}")
        for post in res.get("posts", []):
            if post.get("title") == title:
                return post
        pagination = res.get("pagination", {})
        total_pages = int(pagination.get("total_pages", 1))
        if page >= total_pages:
            return None
        page += 1


# ---------------------------------------------------------------------------
# 画像アップロード（scripts/blog.py::try_upload_image と同等のラッパ）
# ---------------------------------------------------------------------------

def try_upload_image(client: PixBlogClient, image_path: Path) -> str | None:
    try:
        res = client.upload_image(image_path)
        url = client.extract_image_url(res)
        if url:
            print(f"  画像アップロード成功: {image_path.name} -> {url}")
        else:
            print(f"  [警告] 画像アップロード: URL を取得できませんでした（{image_path.name}）")
        return url
    except PixBlogError as e:
        print(f"  [警告] 画像アップロード失敗（{image_path.name}）: {e}")
        return None


# ---------------------------------------------------------------------------
# plan.md / INDEX.md の更新
# ---------------------------------------------------------------------------

def update_plan_status(plan_path: Path, plan_text: str, order: str, new_status: str, url: str) -> None:
    lines = plan_text.splitlines(keepends=False)
    out = []
    for line in lines:
        m = STATUS_TABLE_ROW_RE.match(line.strip())
        if m and m.group(1) == order:
            file_ = m.group(2)
            out.append(f"| {order} | {file_} | {new_status} | {url} |")
        else:
            out.append(line)
    plan_path.write_text("\n".join(out) + "\n", encoding="utf-8")


def update_index_status(date: str, published_count: int, total_count: int = 8) -> None:
    if not INDEX_PATH.exists():
        return
    text = INDEX_PATH.read_text(encoding="utf-8")
    lines = text.splitlines()
    out = []
    for line in lines:
        if f"| {date[:4]}-{date[4:6]}-{date[6:]} |" in line or date in line:
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) == 4:
                cells[2] = f"公開中（{published_count}/{total_count}）" if published_count < total_count else "公開完了"
                out.append("| " + " | ".join(cells) + " |")
                continue
        out.append(line)
    INDEX_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# 公開ペイロード組み立て
# ---------------------------------------------------------------------------

def build_body_with_public_urls(body: str, url_map: dict[str, str]) -> str:
    """本文中の images/xxx.png 参照を url_map の公開URLへ置換した「一時コピー」用文字列を返す。
    リポジトリ内の原稿ファイルはこの関数では一切書き換えない（呼び出し元も書き換えない）。
    """
    def _sub(m: re.Match) -> str:
        alt, rel = m.group(1), m.group(2)
        return f"![{alt}]({url_map.get(rel, rel)})"
    return IMAGE_RE.sub(_sub, body)


def build_publish_payload(fm: dict, body_with_urls: str, featured_url: str | None) -> dict:
    payload = {
        "title": fm.get("title", ""),
        "body": body_with_urls,
        "content_format": "markdown",
        "status": "published",
        "tags": fm.get("tags", DEFAULT_TAGS),
        "category": fm.get("category", DEFAULT_CATEGORY),
        "slug": fm.get("slug"),
    }
    if featured_url:
        payload["featured_media_url"] = featured_url
    if X_FIELD_CONCLUSION == "a":
        if fm.get("x_text"):
            payload[X_TWEET_FIELD] = build_tweet_text(fm["x_text"])
        if fm.get("lead"):
            payload[X_EXCERPT_FIELD] = fm["lead"]
    return payload


# ---------------------------------------------------------------------------
# サブコマンド: --check-lengths
# ---------------------------------------------------------------------------

def cmd_check_lengths() -> None:
    entries = discover_all_posts()
    print(f"{'order':>5} | {'file':<28} | {'x_text':>7} | {'tweet_text':>10} | {'+URL':>5} | 判定 | {'x_hook':>6} | hook判定 | excerpt")
    print("-" * 118)
    overflow = []
    excerpt_ng = []
    hook_overflow = []
    for e in entries:
        x_text = e["fm"].get("x_text")
        lead = e["fm"].get("lead")
        x_hook = e["fm"].get("x_hook")
        order = e["fm"].get("order", "?")
        name = e["path"].name

        if lead:
            problems = validate_excerpt(lead)
            excerpt_col = "OK" if not problems else "NG:" + "/".join(problems)
            if problems:
                excerpt_ng.append((order, name, problems))
        else:
            excerpt_col = "—"

        if x_hook:
            hw = hook_len(x_hook)
            hook_verdict = "OK" if hw <= X_MAX_LEN else "★超過"
            if hw > X_MAX_LEN:
                hook_overflow.append((order, name, hw))
        else:
            hw = None
            hook_verdict = "未設定"

        if not x_text:
            hw_col = f"{hw:>6}" if hw is not None else f"{'—':>6}"
            print(f"{order:>5} | {name:<28} | {'—':>7} | {'—':>10} | {'—':>5} | x_text未設定 | {hw_col} | {hook_verdict:<6} | {excerpt_col}")
            continue

        tw = build_tweet_text(x_text)
        w = x_weighted_len(x_text)
        tw_w = x_weighted_len(tw)
        d = tweet_draft_len(tw)
        verdict = "OK" if d <= X_MAX_LEN else "★超過"
        if d > X_MAX_LEN:
            overflow.append((order, name, d))
        hw_col = f"{hw:>6}" if hw is not None else f"{'—':>6}"
        print(f"{order:>5} | {name:<28} | {w:>7} | {tw_w:>10} | {d:>5} | {verdict:<4} | {hw_col} | {hook_verdict:<6} | {excerpt_col}")
    print("-" * 118)
    if overflow:
        print(f"[報告] tweet_text+URL が280超過 {len(overflow)} 件（削らず報告のみ）:")
        for order, name, d in overflow:
            print(f"  order={order} {name}: {d} 文字（280超過）")
    else:
        print("全16本、tweet_text（#嘘八百万込み）+URLで280文字以内。")
    if excerpt_ng:
        print(f"[報告] excerpt（lead）にハッシュタグ/URL混入 {len(excerpt_ng)} 件:")
        for order, name, problems in excerpt_ng:
            print(f"  order={order} {name}: {', '.join(problems)}")
    else:
        print("全16本、excerpt（lead）にハッシュタグ・URLの混入なし。")
    if hook_overflow:
        print(f"[報告] x_hook（単体・URLなし）が280超過 {len(hook_overflow)} 件（削らず報告のみ）:")
        for order, name, hw in hook_overflow:
            print(f"  order={order} {name}: {hw} 文字（280超過）")
    else:
        print("全16本、x_hook（単体・URLなし）で280文字以内。")


# ---------------------------------------------------------------------------
# メインフロー（--dry-run / 実公開 共通）
# ---------------------------------------------------------------------------

def run_publish(order_override: str | None, dry_run: bool, force: bool = False) -> None:
    date, plan_path, plan_text, row = select_next(order_override)
    article_path = BLOG_ROOT / date / "posts" / Path(row["file"]).name
    print(f"[選定] {date} order={row['order']} file={article_path.name} status={row['status']!r}")

    md_text = article_path.read_text(encoding="utf-8")
    fm, body = parse_frontmatter(md_text)
    images = find_images(body)  # [(alt, "images/xxx.png"), ...]
    print(f"[画像] 本文参照 {len(images)} 枚: {[rel for _, rel in images]}")

    x_text = fm.get("x_text")
    lead = fm.get("lead")
    if not x_text:
        print("[警告] この記事に x_text が設定されていません。tweet_text は送信されず、X下書き出力はタイトルのみになります。")
    if not lead:
        print("[警告] この記事に lead が設定されていません。excerpt は送信されません。")

    # --- 事前検証(1): excerpt（lead）へのハッシュタグ/URL混入チェック -----------------
    if lead:
        problems = validate_excerpt(lead)
        if problems:
            print(f"[中断] excerpt（lead）にハッシュタグ/URLが含まれています: {', '.join(problems)}")
            sys.exit(1)
        print("[確認] excerpt（lead）にハッシュタグ・URLの混入なし。")

    # --- 事前検証(2): tweet_text（#嘘八百万込み）+URL の加重文字数チェック ------------
    tweet_text: str | None = None
    if x_text:
        tweet_text = build_tweet_text(x_text)
        d = tweet_draft_len(tweet_text)
        if d > X_MAX_LEN:
            if force:
                print(f"[警告] tweet_text（#嘘八百万込み）+URL が {d} 文字で上限 {X_MAX_LEN} を超えています（--force により続行）。")
            else:
                print(f"[中断] tweet_text（#嘘八百万込み）+URL が {d} 文字で上限 {X_MAX_LEN} を超えています。"
                      f" --force を付けると無視して続行できます。")
                sys.exit(1)
        else:
            print(f"[確認] tweet_text（#嘘八百万込み）+URL = {d} 文字（上限{X_MAX_LEN}以内）。")
        tw_len = x_weighted_len(tweet_text)
        if tw_len > 120:
            print(f"[参考] tweet_text の加重文字数（{tw_len}）が目安100〜120字を超えています（上限280は満たしています）。")

    client = PixBlogClient()

    # 二重公開ガード(2): 同一タイトルの既存記事チェック（読み取りのみ・dry-run でも実行可）
    try:
        existing = find_existing_post_by_title(client, fm.get("title", ""))
    except PixBlogError as e:
        print(f"[警告] 既存記事チェックに失敗しました（続行し、実公開直前に再確認します）: {e}")
        existing = None
    if existing:
        print(f"[中断] 同一タイトルの記事が既にAPI上に存在します: post_id={existing.get('id')} url={existing.get('url')}")
        sys.exit(1)
    print("[確認] 同一タイトルの既存記事なし。")

    if dry_run:
        print("\n===== dry-run: アップロード予定 =====")
        placeholder_map = {}
        for i, (alt, rel) in enumerate(images, start=1):
            placeholder = f"https://pixblog.net/uploads/DRYRUN_PLACEHOLDER_{i}.png"
            placeholder_map[rel] = placeholder
            print(f"  {i}. {rel} (alt={alt!r}) -> アップロード予定（未実行） -> {placeholder} (仮)")

        featured_placeholder = next(iter(placeholder_map.values()), None)
        body_with_urls = build_body_with_public_urls(body, placeholder_map)
        payload = build_publish_payload(fm, body_with_urls, featured_placeholder)

        print("\n===== dry-run: 公開ペイロード（本文冒頭200字） =====")
        preview = dict(payload)
        preview["body"] = payload["body"][:200] + ("…" if len(payload["body"]) > 200 else "")
        print(json.dumps(preview, ensure_ascii=False, indent=2))

        print("\n===== dry-run: 画像URL置換の差分例 =====")
        for alt, rel in images[:2]:
            print(f"  ![{alt}]({rel})  ->  ![{alt}]({placeholder_map[rel]})")

        draft_url = "https://pixblog.net/u/uso8m/(公開後に確定するURL・dry-runのため未確定)"
        x_hook = fm.get("x_hook")
        print()
        if x_hook:
            image_path = select_attachment_image(date, row["order"], article_path.name, body)
            post_set = build_post_set(row["order"], x_hook, fm.get("title", ""), draft_url, image_path)
            print(post_set)
            hw = hook_len(x_hook)
            verdict = "OK" if hw <= X_MAX_LEN else "★超過（280超過。削らず報告のみ）"
            print(f"（x_hook 加重文字数: {hw} / {X_MAX_LEN}・{verdict}）")
            header = f"# 投稿セット {datetime.now(timezone.utc).astimezone().strftime('%Y-%m-%d')}（order {row['order']}・dry-run・仮URL）"
            write_today_post(header, post_set)
            print(f"[出力] {TODAY_POST_PATH} に上書き保存しました（dry-run・仮URL）。")
        elif x_text:
            print("===== 本日の X 投稿下書き（dry-run・仮URL・x_hook未設定のためフォールバック） =====")
            draft = f"{x_text} {X_HASHTAG} {draft_url}"
            print(draft)
            print(f"（加重文字数の目安: tweet_text単体={x_weighted_len(tweet_text)} / URL込み想定={tweet_draft_len(tweet_text)} / 上限{X_MAX_LEN}）")
            print("※ x_hook が未設定のため、従来の tweet_text ベースの下書き出力にフォールバックしています。")
        else:
            print("===== 本日の X 投稿下書き（dry-run・仮URL・x_hook/x_text とも未設定） =====")
            print(f"{fm.get('title', '')} {draft_url}")
        print("\n[dry-run] 実公開・画像アップロードは行っていません。")
        return

    # ------------------------------------------------------------------
    # 実公開経路（本サイクルでは呼び出さない。--dry-run を付けずに実行した場合のみ到達）
    # ------------------------------------------------------------------
    if len(images) > 2:
        print(f"[中断] 本文の画像参照が3枚以上あります（{len(images)}枚）。仕様は1〜2枚のみ許容します。")
        sys.exit(1)

    url_map: dict[str, str] = {}
    for alt, rel in images:
        img_path = BLOG_ROOT / date / rel
        url = try_upload_image(client, img_path)
        if url:
            url_map[rel] = url
    featured_url = next(iter(url_map.values()), None)

    body_with_urls = build_body_with_public_urls(body, url_map)
    payload = build_publish_payload(fm, body_with_urls, featured_url)

    # 一時コピー（リポジトリ原稿は書き換えない）
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as tmp:
        tmp.write(body_with_urls)
        tmp_path = tmp.name
    print(f"[一時コピー] 公開用本文を一時ファイルに生成: {tmp_path}")

    res = client._request("POST", "/posts", payload)
    post_id = res.get("id") or res.get("post_id")
    public_url = res.get("url") or (f"https://pixblog.net/u/uso8m/{res.get('slug')}" if res.get("slug") else None)

    # サイクル10.16で発覚: POST /posts は payload の status に関わらず常に draft で作成される
    # （blog/pixblog_client.py の docstring 通り）。PATCH で明示的に published へ遷移させないと
    # 公開URLが404のままになるため、この1回のPATCHを追加する（既存の他挙動は無変更）。
    if post_id:
        pub_res = client.publish(post_id)
        public_url = pub_res.get("url") or public_url
        print(f"[公開完了] post_id={post_id} status={pub_res.get('status')} url={public_url}")
    else:
        print(f"[警告] post_id を取得できず、published への遷移PATCHをスキップしました: {res}")

    today = datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d")
    new_status = f"{STATUS_PUBLISHED_PREFIX}（{today}・{public_url}）"
    update_plan_status(plan_path, plan_text, row["order"], new_status, public_url or "")

    rows_after = parse_status_table(plan_path.read_text(encoding="utf-8"))
    published = sum(1 for r in rows_after if r["status"].startswith(STATUS_PUBLISHED_PREFIX))
    update_index_status(date, published, total_count=len(rows_after))

    print()
    x_hook = fm.get("x_hook")
    if x_hook:
        image_path = select_attachment_image(date, row["order"], article_path.name, body)
        post_set = build_post_set(row["order"], x_hook, fm.get("title", ""), public_url or "", image_path)
        print(post_set)
        hw = hook_len(x_hook)
        verdict = "OK" if hw <= X_MAX_LEN else "★超過（280超過。削らず報告のみ）"
        print(f"（x_hook 加重文字数: {hw} / {X_MAX_LEN}・{verdict}）")
        header = f"# 投稿セット {today}（order {row['order']}）"
        write_today_post(header, post_set)
        print(f"[出力] {TODAY_POST_PATH} に上書き保存しました。")
    elif x_text:
        print("===== 本日の X 投稿下書き（x_hook未設定のためフォールバック） =====")
        draft = f"{x_text} {X_HASHTAG} {public_url}"
        print(draft)
        print(f"（加重文字数: {tweet_draft_len(tweet_text)} / {X_MAX_LEN}）")
        print("※ x_hook が未設定のため、従来の tweet_text ベースの下書き出力にフォールバックしています。")
    else:
        print("===== 本日の X 投稿下書き（x_hook/x_text とも未設定） =====")
        print(f"{fm.get('title', '')} {public_url}")


# ---------------------------------------------------------------------------
# サブコマンド: --replay NN（公開処理・plan更新なし。API呼び出しなし）
# ---------------------------------------------------------------------------

def cmd_replay(order: str) -> None:
    order = order.zfill(2)
    for date in DATES:
        plan_path = BLOG_ROOT / date / "plan.md"
        if not plan_path.exists():
            continue
        rows = parse_status_table(plan_path.read_text(encoding="utf-8"))
        for row in rows:
            if row["order"] != order:
                continue
            if not row["status"].startswith(STATUS_PUBLISHED_PREFIX):
                print(f"[中断] order={order} は公開済みなしです（状態: {row['status']!r}）。--replay は公開済み記事のみ対象です。")
                sys.exit(1)
            article_path = BLOG_ROOT / date / "posts" / Path(row["file"]).name
            md_text = article_path.read_text(encoding="utf-8")
            fm, body = parse_frontmatter(md_text)
            url = row["url"] if row["url"] and row["url"] != "—" else "（公開URL不明・plan.mdを確認してください）"
            x_hook = fm.get("x_hook")
            x_text = fm.get("x_text")
            print(f"[再表示] {date} order={order} file={article_path.name} status={row['status']!r}")
            if x_hook:
                image_path = select_attachment_image(date, order, article_path.name, body)
                post_set = build_post_set(order, x_hook, fm.get("title", ""), url, image_path)
                print(post_set)
            elif x_text:
                print("===== 投稿セット（x_hook未設定のためフォールバック） =====")
                print(f"{x_text} {X_HASHTAG} {url}")
            else:
                print(f"{fm.get('title', '')} {url}")
            print("\n[replay] 公開処理・plan更新・today_post.md への書き込みは行っていません。")
            return
    print(f"[中断] order={order} は公開済みなしです（plan.md に該当行がありません）。")
    sys.exit(1)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="実公開・画像アップロードを行わず流れのみ確認する")
    parser.add_argument("--order", type=str, default=None, help="対象の order を明示指定する（例: 01）")
    parser.add_argument("--check-lengths", action="store_true", help="16本の tweet_text / x_hook 加重文字数を検算して終了する")
    parser.add_argument("--force", action="store_true", help="tweet_text（#嘘八百万込み）+URL が280文字を超えていても中断せず続行する")
    parser.add_argument("--replay", type=str, default=None, help="公開済み order の投稿セットを再表示する（公開処理・plan更新なし。例: 03）")
    args = parser.parse_args()

    if args.check_lengths:
        cmd_check_lengths()
        return

    if args.replay is not None:
        cmd_replay(args.replay)
        return

    order_override = args.order.zfill(2) if args.order else None
    run_publish(order_override, dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
