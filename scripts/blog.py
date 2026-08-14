#!/usr/bin/env python3
"""
試合後ブログの「生成 → 投稿 → 修正」CLI(品質確認フェーズ MVP)

2種類の記事を扱う:
  1. 各LLMプレイヤーの一人称振り返り記事(--player PXX。本人モデルで執筆)
  2. 大会オープニング記事(--opening。胴元/煽りナレーター視点・カイジ風・M1で執筆)

生成(プレイヤー記事):
    uv run python scripts/blog.py --player P08
    uv run python scripts/blog.py --player P08 --dry-run

生成(オープニング記事):
    uv run python scripts/blog.py --opening
    uv run python scripts/blog.py --opening --dry-run
    uv run python scripts/blog.py --opening --narrator-model M1 --roster-emotion ease

投稿(直接公開ルート / cron公開ルート):
    ... --publish-direct    # 即 published
    ... --publish-draft     # draft 保存
    uv run python scripts/blog.py --drip     # draft を1本 published(cron用)

修正投稿(同じ記事を上書き):
    uv run python scripts/blog.py --player P08 --update --from path/to/edited.md
    uv run python scripts/blog.py --opening --update --from path/to/edited.md

画像:
    プレイヤー記事は状況インフォグラフィック、オープニング記事は出場者ロスター画像を出力。
    --upload-image で投稿時にアイキャッチ添付を試行。
"""

import argparse
import json
import re
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from engine.commentary.trace import build_trace
from engine.blog.memory_packet import build_player_memory, PlayerMemory
from engine.blog.round_situation import build_round_situation_markdown, extract_cash_series
from engine.blog.situation_image import generate_situation_image
from engine.blog.prompts import build_blog_system_prompt, build_blog_user_prompt
from engine.blog.roster import build_roster
from engine.blog.overview import build_overview_markdown
from engine.blog.opening_prompts import build_opening_system_prompt, build_opening_user_prompt
from engine.blog.closing_prompts import build_closing_system_prompt, build_closing_user_prompt
from engine.blog.roster_image import generate_roster_image
from engine.blog.cards_svg import (
    build_model_card_svg, build_title_card_svg,
    build_result_card_svg, build_quote_card_svg,
)
from llm.models import get_model, estimate_cost
from llm.adapters import create_adapter
from blog.pixblog_client import PixBlogClient, PixBlogError

DEFAULT_GAME_DIR = "logs/llm/trial_C_20260810_053055"
DEFAULT_OUT_DIR = ".devrelay-output"
STATE_PATH = PROJECT_ROOT / "doc" / "blog" / "state.json"
DEFAULT_TAGS = ["dangou-card", "AI対戦", "談合カード"]
DEFAULT_CATEGORY = "AI対戦ログ"
DEFAULT_NARRATOR_MODEL = "M1"   # 現行レジストリ最上位(Opus不在のため Claude Sonnet 5)
DEFAULT_ROSTER_EMOTION = "ease"
MAX_TOKENS = 6000
OPENING_KEY = "opening"
CLOSING_KEY = "closing"

# reasoning系（Kimi等）は本文の前に思考(reasoning_content)を長文で吐き出す。
# この思考を途中で打ち切ると content(本文)に到達できず、アダプタが
# reasoning_content にフォールバックして「思考ダンプだけの記事」になってしまう。
# そのため (1)タイムアウトを延ばし、(2)思考を完走して本文まで書き切れるよう
# トークン上限を"広げる"必要がある。ブログはレイテンシ許容度が高いので許容する。
REASONING_MAX_TOKENS = 12000
REASONING_TIMEOUT_SECONDS = 300


def _is_reasoning_model(model) -> bool:
    """思考ダンプが長く生成が重いモデルか判定する（現状はKimi）。"""
    return "kimi" in model.model_id.lower()


def _prep_blog_model(model):
    """記事生成用にモデル設定を調整し (調整後モデル, max_tokens) を返す。

    reasoning系はグローバルの MODEL_REGISTRY を汚さないよう replace() で
    コピーを作り、タイムアウトを延長する。それ以外は既定のまま。
    """
    if _is_reasoning_model(model):
        adapted = replace(
            model,
            timeout_seconds=max(model.timeout_seconds, REASONING_TIMEOUT_SECONDS),
        )
        return adapted, REASONING_MAX_TOKENS
    return model, MAX_TOKENS


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
# 共通ユーティリティ
# ---------------------------------------------------------------------------

def split_title_body(md: str, fallback_title: str) -> tuple[str, str]:
    """先頭の H1 をタイトルとして取り出し、本文(タイトル行を除く)を返す。"""
    lines = md.splitlines()
    for i, line in enumerate(lines):
        s = line.strip()
        if s.startswith("# "):
            title = s[2:].strip()
            body = "\n".join(lines[i + 1:]).strip()
            return title, body
        if s:
            break  # H1 より前に本文が来た → タイトル無し扱い
    return fallback_title, md.strip()


def _strip_code_fence(text: str) -> str:
    m = re.match(r"\s*```(?:markdown|md)?\s*\n(.*)\n```\s*$", text, re.DOTALL)
    return m.group(1) if m else text


# ---------------------------------------------------------------------------
# 記事生成
# ---------------------------------------------------------------------------

def generate_article(
    mem: PlayerMemory,
    round_situation_md: str,
    dry_run: bool = False,
) -> tuple[str, dict]:
    """本人モデルで一人称ブログ記事(Markdown)を生成する。戻り値 (markdown, meta)。"""
    model = get_model(mem.model_key)
    system = build_blog_system_prompt(mem.name, mem.num_rounds)
    user = build_blog_user_prompt(mem, round_situation_md)

    if dry_run:
        md = (
            f"# 【ドライラン】{mem.pid} {mem.name} の試合振り返り\n\n"
            f"## これはダミー記事です\n\n"
            f"最終結果: {mem.final_result} / 現金 {mem.final_cash} / 借金 {mem.final_debt}。\n"
            f"(実際の記事は本人モデル {model.name} が生成します)\n"
        )
        meta = {"model": model.model_id, "model_name": model.name, "dry_run": True,
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        return md, meta

    gen_model, max_tokens = _prep_blog_model(model)
    adapter = create_adapter(gen_model)
    text, usage = adapter.complete(
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=max_tokens,
        temperature=0.8,
    )
    cost = estimate_cost(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    text = _strip_code_fence(text)
    meta = {
        "model": model.model_id, "model_name": model.name, "dry_run": False,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cost_usd": round(cost, 6),
    }
    return text.strip() + "\n", meta


def generate_opening_article(
    roster: list[dict],
    overview_md: str,
    model_key: str,
    dry_run: bool = False,
) -> tuple[str, dict]:
    """ナレーター視点のオープニング記事(Markdown)を生成する。戻り値 (markdown, meta)。"""
    model = get_model(model_key)
    system = build_opening_system_prompt()
    user = build_opening_user_prompt(overview_md, roster)

    if dry_run:
        names = "、".join(r["name"] for r in roster)
        md = (
            f"# 【ドライラン】嘘八百万 ―談合カード― 開幕\n\n"
            f"## これはダミー記事です\n\n"
            f"出場者: {names}。\n"
            f"(実際の記事はナレーターモデル {model.name} が生成します)\n"
        )
        meta = {"model": model.model_id, "model_name": model.name, "dry_run": True,
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        return md, meta

    gen_model, max_tokens = _prep_blog_model(model)
    adapter = create_adapter(gen_model)
    text, usage = adapter.complete(
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=max_tokens,
        temperature=0.9,
    )
    cost = estimate_cost(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    text = _strip_code_fence(text)
    meta = {
        "model": model.model_id, "model_name": model.name, "dry_run": False,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cost_usd": round(cost, 6),
    }
    return text.strip() + "\n", meta


def generate_closing_article(
    roster: list[dict],
    overview_md: str,
    standings_md: str,
    model_key: str,
    dry_run: bool = False,
) -> tuple[str, dict]:
    """ナレーター視点の総括記事(最終話・結果開示あり)を生成する。戻り値 (markdown, meta)。"""
    model = get_model(model_key)
    system = build_closing_system_prompt()
    user = build_closing_user_prompt(overview_md, standings_md, roster)

    if dry_run:
        md = (
            f"# 【ドライラン】嘘八百万 ―談合カード― 総括\n\n"
            f"## これはダミー記事です\n\n"
            f"最終順位表(先頭抜粋):\n{standings_md.splitlines()[0] if standings_md else ''}\n"
            f"(実際の記事はナレーターモデル {model.name} が生成します)\n"
        )
        meta = {"model": model.model_id, "model_name": model.name, "dry_run": True,
                "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        return md, meta

    gen_model, max_tokens = _prep_blog_model(model)
    adapter = create_adapter(gen_model)
    text, usage = adapter.complete(
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=max_tokens,
        temperature=0.85,
    )
    cost = estimate_cost(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    text = _strip_code_fence(text)
    meta = {
        "model": model.model_id, "model_name": model.name, "dry_run": False,
        "input_tokens": usage.get("input_tokens", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "cost_usd": round(cost, 6),
    }
    return text.strip() + "\n", meta


# ---------------------------------------------------------------------------
# 投稿(記事種別に依存しない汎用フロー)
# ---------------------------------------------------------------------------

def try_upload_image(client: PixBlogClient, image_path: Path) -> str | None:
    try:
        res = client.upload_image(image_path)
        url = client.extract_image_url(res)
        if url:
            print(f"  画像アップロード成功: {url}")
        else:
            print("  画像アップロード: URL を取得できませんでした(レスポンス形式差異)")
        return url
    except PixBlogError as e:
        print(f"  [警告] 画像アップロード失敗(記事は画像なしで続行): {e}")
        return None


def publish_flow(
    args, post_key: str, title: str, body: str,
    image_path: Path | None, state: dict, status: str,
    memo: str, state_meta: dict,
) -> None:
    """create_post を実行し state.json に記録する(記事種別非依存)。"""
    client = PixBlogClient()
    featured = None
    if image_path and image_path.exists() and args.upload_image:
        featured = try_upload_image(client, image_path)

    res = client.create_post(
        title=title, body=body, status=status,
        tags=args.tags, category=args.category,
        featured_media_url=featured, memo=memo,
    )
    post_id = res.get("_post_id")
    now = datetime.now(timezone.utc).isoformat()
    entry = {
        "post_id": post_id,
        "status": status,
        "title": title,
        "featured_media_url": featured,
        "created_at": now,
        "published_at": now if status == "published" else None,
    }
    entry.update(state_meta)
    state.setdefault("posts", {})[post_key] = entry
    save_state(state)
    print(f"  投稿完了: post_id={post_id} status={status}")
    if post_id is None:
        print(f"  [注意] レスポンスから post_id を取得できませんでした。生レスポンス: {json.dumps(res, ensure_ascii=False)[:300]}")


def update_flow(args, state: dict, post_key: str, fallback_title: str) -> None:
    """既存記事を上書き更新(修正投稿・記事種別非依存)。"""
    post_id = args.update if isinstance(args.update, str) and args.update else None
    if not post_id and post_key in state.get("posts", {}):
        post_id = state["posts"][post_key].get("post_id")
    if not post_id:
        print("Error: 更新対象の post_id が不明です。--update <post_id> を指定するか、"
              "先に --publish-* で投稿してください。", file=sys.stderr)
        sys.exit(1)

    if not args.from_file:
        print("Error: --update には --from <edited.md> を指定してください。", file=sys.stderr)
        sys.exit(1)
    md = Path(args.from_file).read_text(encoding="utf-8")
    title, body = split_title_body(md, fallback_title=fallback_title)

    client = PixBlogClient()
    res = client.update_post(post_id, title=title, body=body)
    entry = state.setdefault("posts", {}).setdefault(post_key, {})
    entry["post_id"] = post_id
    entry["title"] = title
    entry["updated_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print(f"修正投稿完了: post_id={post_id} title={title!r}")
    print(f"  レスポンス: {json.dumps(res, ensure_ascii=False)[:200]}")


def drip_flow(state: dict) -> None:
    """state 内の draft を1本 published にする(cron公開ルート)。"""
    posts = state.get("posts", {})
    target_key = None
    for key, info in posts.items():
        if info.get("status") == "draft" and info.get("post_id"):
            target_key = key
            break
    if not target_key:
        print("公開待ちの draft はありません。")
        return
    info = posts[target_key]
    client = PixBlogClient()
    client.publish(info["post_id"])
    info["status"] = "published"
    info["published_at"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    print(f"ドリップ公開: {target_key} post_id={info['post_id']} title={info.get('title')!r}")


# ---------------------------------------------------------------------------
# 本文への画像挿入(新規生成の自動埋め込み & 既存記事の後付けで共通利用)
# ---------------------------------------------------------------------------

def _figure_html(url: str, alt: str, caption: str | None = None) -> str:
    """本文に差し込む <figure> 生HTML(Markdown本文でも passthrough で描画される)。"""
    cap = f"<figcaption>{caption}</figcaption>" if caption else ""
    return (
        '<figure style="text-align:center;margin:1.6em 0;">'
        f'<img src="{url}" alt="{alt}" style="max-width:100%;height:auto;border-radius:8px;" />'
        f"{cap}</figure>"
    )


def _insert_after_heading(body: str, pattern: str, fig: str) -> str:
    """見出し(HTML <h*> または Markdown #)の直後に fig を挿入。無ければ末尾に追加。"""
    m = re.search(rf"<h[1-6][^>]*>[^<]*(?:{pattern})[^<]*</h[1-6]>", body)
    if not m:
        m = re.search(rf"^#{{1,6}}\s.*(?:{pattern}).*$", body, re.MULTILINE)
    if m:
        idx = m.end()
        return body[:idx] + "\n\n" + fig + "\n" + body[idx:]
    print(f"  [注意] 見出し '{pattern}' が見つからず末尾に挿入します")
    return body.rstrip() + "\n\n" + fig + "\n"


def insert_figures(body: str, specs: list[dict]) -> str:
    """specs=[{url,alt,caption?,anchor}] を本文へ挿入して返す。

    anchor='top' は本文先頭に prepend、'bottom' は本文末尾に append、
    その他は見出し正規表現の直後に挿入する。
    本文テキスト自体は一切改変せず <figure> だけを足す(HTML/Markdown 両対応)。
    """
    for s in [x for x in specs if x.get("anchor") == "top"][::-1]:
        body = _figure_html(s["url"], s["alt"], s.get("caption")) + "\n\n" + body
    for s in specs:
        anchor = s.get("anchor")
        if anchor == "top":
            continue
        fig = _figure_html(s["url"], s["alt"], s.get("caption"))
        if anchor == "bottom":
            body = body.rstrip() + "\n\n" + fig + "\n"
        else:
            body = _insert_after_heading(body, anchor, fig)
    return body


def build_body_image_specs(args, trace, out_dir: Path, game_tag: str,
                           client: PixBlogClient | None, kind: str = "player") -> list[dict]:
    """本文差し込み用の画像を生成→アップロードし、挿入spec(url/alt/caption/anchor)を返す。

    kind="player": LIE DETECTED(R8見出し後) + 状況グラフ(最終節後) + 嘘八百万横長バナー(bottom=総括の下)。
    kind="opening": 嘘八百万横長バナー(bottom)のみ(試合結果のネタバレを避ける)。
    kind="closing": 状況グラフ(top) + 嘘八百万横長バナー(bottom)。※リザルトカードはアイキャッチ側で重複させない。
    dry-run 時はアップロードせずローカルパス(file URI)を src に使う(課金・通信なし)。
    """
    from engine.blog.cards_html import CardRenderer

    roster = build_roster(trace)
    by_pid = {r["pid"]: r for r in roster}
    pid = args.player
    r = by_pid.get(pid)

    plan: list[tuple[Path, str, str | None, str]] = []  # (path, alt, caption, anchor)
    with CardRenderer() as rndr:
        banner = out_dir / f"{game_tag}_card_title_banner.png"
        rndr.render_title_card(size="banner", out_path=banner)
        # 横長バナーは記事末尾(bottom=総括の下)へ。バナー自体に文言があるため caption は付けない。
        plan.append((banner, "嘘八百万 ―談合カード―", None, "bottom"))

        if kind == "player" and r:
            mem = build_player_memory(trace, pid)
            sel = _lie_selection(args, mem)
            if sel:
                quote, rnd, to_pid, _honne = sel
                actual = _lie_actual_line(args, mem, sel, args.dry_run)
                lie = out_dir / f"{game_tag}_card_lie_{pid}.png"
                rndr.render_lie_card(
                    quote=quote, pid=pid, name=r["name"], vendor=r["vendor"],
                    round_num=rnd, to_pid=to_pid, actual=actual,
                    emotion="smirk", out_path=lie,
                )
                plan.append((lie, f"{r['name']} の嘘、露見", "LIE DETECTED", "裏切|R8"))
            else:
                print(f"  (LIE カード: {pid} の引用可能なDMなし。スキップ)")

    if kind in ("player", "closing"):
        situation = out_dir / f"{game_tag}_body_situation.png"
        highlight = pid if kind == "player" else None
        generate_situation_image(extract_cash_series(trace), situation, highlight_pid=highlight)
        # player=最終節見出しの後 / closing=本文冒頭(top)。
        anchor = "最終|敗北|算術" if kind == "player" else "top"
        plan.append((situation, "ラウンド別の資産推移", "ラウンド別の資産推移(free cash)", anchor))

    specs: list[dict] = []
    for path, alt, caption, anchor in plan:
        if not path.exists():
            print(f"  [警告] 画像未生成のためスキップ: {path}")
            continue
        if args.dry_run:
            url = path.resolve().as_uri()
        else:
            url = try_upload_image(client, path)
            if not url:
                print(f"  [警告] アップロード失敗のためスキップ: {path}")
                continue
        specs.append({"url": url, "alt": alt, "caption": caption, "anchor": anchor})
    return specs


def illustrate_flow(args, state: dict, trace, out_dir: Path, game_tag: str) -> None:
    """既存記事(post_id)の本文へ画像を後付けする(本文テキスト保全・status未変更=draft維持)。"""
    post_id = args.illustrate_post
    client = PixBlogClient()

    post = client._request("GET", f"/posts/{post_id}")
    html = post.get("content") or post.get("body") or ""
    if not html:
        print(f"Error: post_id={post_id} の本文を取得できませんでした。", file=sys.stderr)
        sys.exit(1)
    status_before = post.get("status")
    backup = out_dir / f"{game_tag}_post{post_id}_body_backup.html"
    backup.write_text(html, encoding="utf-8")
    print(f"対象記事: post_id={post_id} status={status_before} 本文{len(html)}字")
    print(f"  バックアップ: {backup}")

    # 冪等化: 過去に本ツールが挿入した <figure> を一旦除去してから入れ直す(重複防止)。
    # 対象記事は本文テキストのみで著者figureを持たないため全figure除去で安全。
    cleaned, n = re.subn(r"<figure\b[^>]*>.*?</figure>\s*", "", html, flags=re.DOTALL)
    if n:
        print(f"  既存 figure を {n} 個除去して入れ直します {len(html)}→{len(cleaned)}字")
    html = cleaned

    specs = build_body_image_specs(args, trace, out_dir, game_tag, client, kind="player")
    if not specs:
        print("挿入できる画像がありません。中止します。")
        return
    new_html = insert_figures(html, specs)
    client.update_post(post_id, body=new_html)
    print(f"本文更新: img+{len(specs)}  {len(html)}→{len(new_html)}字 (status未指定=変更なし)")
    for s in specs:
        print(f"    - {s['anchor']}: {s['url']}")

    posts = state.setdefault("posts", {})
    key = next((k for k, v in posts.items() if str(v.get("post_id")) == str(post_id)), None)
    if key:
        posts[key]["illustrated_at"] = datetime.now(timezone.utc).isoformat()
        posts[key]["body_image_urls"] = [s["url"] for s in specs]
        save_state(state)
        print(f"state.json 更新: posts.{key}")
    else:
        print("(state に該当 post_id なし。記録はスキップ)")


# ---------------------------------------------------------------------------
# 記事種別ごとのメインフロー
# ---------------------------------------------------------------------------

def run_opening(args, state: dict, trace, out_dir: Path, game_tag: str) -> None:
    """オープニング記事(ナレーター視点)の生成・保存・投稿。"""
    roster = build_roster(trace)
    print(f"出場者: {len(roster)}体 " + ", ".join(f"{r['pid']}={r['name']}" for r in roster))

    # 大会概要ファクト(ネタバレ無し)
    overview_md = build_overview_markdown(trace, roster)
    overview_path = out_dir / f"{game_tag}_overview.md"
    overview_path.write_text(overview_md, encoding="utf-8")
    print(f"大会概要ファクト: {overview_path}")

    # アイキャッチ画像: --image-from 指定時はその画像を採用、未指定なら出場者ロスター画像を生成
    if args.no_image:
        image_path = None
    elif args.image_from:
        image_path = Path(args.image_from)
        print(f"アイキャッチ画像(指定): {image_path}")
    else:
        image_path = out_dir / f"{game_tag}_opening_roster.png"
        generate_roster_image(roster, image_path, emotion=args.roster_emotion)
        print(f"出場者ロスター画像: {image_path}")

    # 記事本文
    if args.from_file:
        md = Path(args.from_file).read_text(encoding="utf-8")
        meta = {"model_name": "(外部ファイル)", "cost_usd": 0.0}
        print(f"記事ソース: {args.from_file}")
    else:
        print(f"オープニング記事生成中(ナレーターモデル {args.narrator_model})...")
        md, meta = generate_opening_article(
            roster, overview_md, args.narrator_model, dry_run=args.dry_run,
        )

    fallback_title = "嘘八百万 ―談合カード― 開幕"
    title, body = split_title_body(md, fallback_title)

    if args.body_images:
        print("本文画像を生成・挿入中(--body-images, オープニングは嘘八百万バナーのみ)...")
        bi_client = None if args.dry_run else PixBlogClient()
        specs = build_body_image_specs(args, trace, out_dir, game_tag, bi_client, kind="opening")
        body = insert_figures(body, specs)
        md = f"# {title}\n\n{body}\n"
        print(f"  本文に画像 {len(specs)} 枚を挿入")

    article_path = out_dir / f"{game_tag}_opening_blog.md"
    article_path.write_text(md if md.lstrip().startswith("#") else f"# {title}\n\n{body}\n",
                            encoding="utf-8")
    print(f"記事: {article_path}")
    print(f"  タイトル: {title}")
    print(f"  モデル: {meta.get('model_name')}  コスト: ${meta.get('cost_usd', 0):.4f}")
    print(f"  本文文字数: {len(body)}")

    if args.publish_direct or args.publish_draft:
        status = "published" if args.publish_direct else "draft"
        memo = f"opening, game={args.game_dir}, narrator={args.narrator_model}"
        state_meta = {"kind": "opening", "model": args.narrator_model,
                      "name": meta.get("model_name")}
        print(f"PixBlog 投稿({status})...")
        publish_flow(args, OPENING_KEY, title, body, image_path, state, status, memo, state_meta)
    else:
        print("(投稿なし。--publish-direct / --publish-draft で投稿できます)")


def _standings_markdown(trace) -> str:
    """最終順位表(順位/モデル/結果/最終現金)の Markdown を構築する(総括の素材)。"""
    label = {"survived": "生還", "eliminated": "脱落", "bankruptcy": "破産"}
    survival = int((trace.config or {}).get("survival_cash", 2_000_000))
    rows = _standings(trace)
    survivors = sum(1 for r in rows if r["result"] == "survived")
    lines: list[str] = []
    lines.append(f"- 生還ライン: {survival:,}円 / 生還者数: {survivors}体 / 全{len(rows)}体")
    lines.append("")
    lines.append("| 順位 | モデル | 結果 | 最終現金 |")
    lines.append("|---|---|---|---|")
    for i, r in enumerate(rows, 1):
        cash = r["cash"]
        cash_s = f"{int(cash):,}円" if cash is not None else "―"
        lines.append(f"| {i} | {r['name']}（{r['vendor']}） | "
                     f"{label.get(r['result'], r['result'])} | {cash_s} |")
    return "\n".join(lines) + "\n"


def run_closing(args, state: dict, trace, out_dir: Path, game_tag: str) -> None:
    """総括記事(最終話・ナレーター視点・結果開示あり)の生成・保存・投稿。"""
    roster = build_roster(trace)
    print(f"出場者: {len(roster)}体 " + ", ".join(f"{r['pid']}={r['name']}" for r in roster))

    overview_md = build_overview_markdown(trace, roster)
    standings_md = _standings_markdown(trace)
    standings_path = out_dir / f"{game_tag}_standings.md"
    standings_path.write_text(standings_md, encoding="utf-8")
    print(f"最終順位表: {standings_path}")

    # アイキャッチ画像: --image-from 指定時はその画像、未指定なら最終リザルトカードを生成
    if args.no_image:
        image_path = None
    elif args.image_from:
        image_path = Path(args.image_from)
        print(f"アイキャッチ画像(指定): {image_path}")
    else:
        image_path = out_dir / f"{game_tag}_card_result.png"
        from engine.blog.cards_html import CardRenderer
        with CardRenderer() as rndr:
            rndr.render_result_card(_standings(trace), emotion="ease", out_path=image_path)
        print(f"リザルトカード: {image_path}")

    # 記事本文
    if args.from_file:
        md = Path(args.from_file).read_text(encoding="utf-8")
        meta = {"model_name": "(外部ファイル)", "cost_usd": 0.0}
        print(f"記事ソース: {args.from_file}")
    else:
        print(f"総括記事生成中(ナレーターモデル {args.narrator_model})...")
        md, meta = generate_closing_article(
            roster, overview_md, standings_md, args.narrator_model, dry_run=args.dry_run,
        )

    fallback_title = "嘘八百万 ―談合カード― 総括"
    title, body = split_title_body(md, fallback_title)

    if args.body_images:
        print("本文画像を生成・挿入中(--body-images, 総括は状況グラフ＋横長バナー)...")
        bi_client = None if args.dry_run else PixBlogClient()
        specs = build_body_image_specs(args, trace, out_dir, game_tag, bi_client, kind="closing")
        body = insert_figures(body, specs)
        md = f"# {title}\n\n{body}\n"
        print(f"  本文に画像 {len(specs)} 枚を挿入")

    article_path = out_dir / f"{game_tag}_closing_blog.md"
    article_path.write_text(md if md.lstrip().startswith("#") else f"# {title}\n\n{body}\n",
                            encoding="utf-8")
    print(f"記事: {article_path}")
    print(f"  タイトル: {title}")
    print(f"  モデル: {meta.get('model_name')}  コスト: ${meta.get('cost_usd', 0):.4f}")
    print(f"  本文文字数: {len(body)}")

    if args.publish_direct or args.publish_draft:
        status = "published" if args.publish_direct else "draft"
        memo = f"closing, game={args.game_dir}, narrator={args.narrator_model}"
        state_meta = {"kind": "closing", "model": args.narrator_model,
                      "name": meta.get("model_name")}
        print(f"PixBlog 投稿({status})...")
        publish_flow(args, CLOSING_KEY, title, body, image_path, state, status, memo, state_meta)
    else:
        print("(投稿なし。--publish-direct / --publish-draft で投稿できます)")


def run_player(args, state: dict, trace, out_dir: Path, game_tag: str) -> None:
    """各LLMプレイヤーの一人称振り返り記事の生成・保存・投稿。"""
    # 共通のラウンド状況
    round_situation_md = build_round_situation_markdown(trace)
    situation_md_path = out_dir / f"{game_tag}_round_situation.md"
    situation_md_path.write_text(round_situation_md, encoding="utf-8")
    print(f"共通ラウンド状況: {situation_md_path}")

    # アイキャッチ画像: --image-from 指定時はその画像を採用、未指定なら状況グラフを生成
    if args.no_image:
        image_path = None
    elif args.image_from:
        image_path = Path(args.image_from)
        print(f"アイキャッチ画像(指定): {image_path}")
    else:
        image_path = out_dir / f"{game_tag}_situation.png"
        cash_data = extract_cash_series(trace)
        generate_situation_image(cash_data, image_path, highlight_pid=args.player)
        print(f"状況画像: {image_path}")

    # 記憶パケット
    mem = build_player_memory(trace, args.player)
    print(f"対象: {mem.pid} {mem.name} (model={mem.model_key}) 最終={mem.final_result}")

    # 記事本文
    if args.from_file:
        md = Path(args.from_file).read_text(encoding="utf-8")
        meta = {"model_name": "(外部ファイル)", "cost_usd": 0.0}
        print(f"記事ソース: {args.from_file}")
    else:
        print(f"記事生成中(本人モデル {mem.model_key})...")
        md, meta = generate_article(mem, round_situation_md, dry_run=args.dry_run)

    fallback_title = f"{mem.name} の談合カード振り返り"
    title, body = split_title_body(md, fallback_title)

    if args.body_images:
        print("本文画像を生成・挿入中(--body-images)...")
        bi_client = None if args.dry_run else PixBlogClient()
        specs = build_body_image_specs(args, trace, out_dir, game_tag, bi_client, kind="player")
        body = insert_figures(body, specs)
        md = f"# {title}\n\n{body}\n"
        print(f"  本文に画像 {len(specs)} 枚を挿入")

    article_path = out_dir / f"{game_tag}_{mem.pid}_blog.md"
    article_path.write_text(md if md.lstrip().startswith("#") else f"# {title}\n\n{body}\n",
                            encoding="utf-8")
    print(f"記事: {article_path}")
    print(f"  タイトル: {title}")
    print(f"  モデル: {meta.get('model_name')}  コスト: ${meta.get('cost_usd', 0):.4f}")
    print(f"  本文文字数: {len(body)}")

    if args.publish_direct or args.publish_draft:
        status = "published" if args.publish_direct else "draft"
        memo = f"game={args.game_dir}, player={mem.pid}, model={mem.model_key}({mem.name})"
        state_meta = {"kind": "player", "model": mem.model_key, "name": mem.name}
        print(f"PixBlog 投稿({status})...")
        publish_flow(args, mem.pid, title, body, image_path, state, status, memo, state_meta)
    else:
        print("(投稿なし。--publish-direct / --publish-draft で投稿できます)")


# ---------------------------------------------------------------------------
# SVGカード生成
# ---------------------------------------------------------------------------

_QUOTE_KEYWORDS = ["静か", "譲", "協調", "裏切", "談合", "任せ", "お任せ",
                   "嘘", "信じ", "約束", "賞金", "勝", "生還", "サポート"]


def _write_svg(out_dir: Path, name: str, svg: str) -> Path:
    path = out_dir / name
    path.write_text(svg, encoding="utf-8")
    print(f"  カード: {path}")
    return path


def _final_cash(mem) -> int | None:
    """最終資産。survival_check(R12は借金返済後なので実質free cash)を優先。

    破産等で survival_check が無い場合はスナップショットの free_cash を使う
    （生cashは借金控除前で過大表示になるため。グラフと同じ free_cash 基準に揃える）。
    """
    if mem.final_cash is not None:
        return mem.final_cash
    for rm in reversed(mem.rounds):
        snap = rm.snapshots.get(mem.pid)
        if snap and snap.get("free_cash") is not None:
            return snap.get("free_cash")
    return None


def _pick_quote(mem) -> tuple[str, int | None, str | None] | None:
    """本人が送信したDMから「印象的な1通」をヒューリスティックに選ぶ。"""
    best = None
    best_score = -1
    for rm in mem.rounds:
        for dm in rm.my_dms_sent:
            msg = (dm.get("message") or "").strip()
            if not msg:
                continue
            L = len(msg)
            score = 0
            if 15 <= L <= 70:
                score += 3
            elif L <= 90:
                score += 1
            else:
                score -= 2
            score += sum(1 for k in _QUOTE_KEYWORDS if k in msg)
            if score > best_score:
                best_score = score
                best = (msg, rm.round_num, dm.get("recipient"))
    return best


def _standings(trace) -> list[dict]:
    """全プレイヤーの最終成績を cash 降順・生還優先で整列して返す。"""
    roster = build_roster(trace)
    rows = []
    for r in roster:
        mem = build_player_memory(trace, r["pid"])
        rows.append({
            "pid": r["pid"], "name": r["name"], "vendor": r["vendor"],
            "result": mem.final_result or "eliminated",
            "cash": _final_cash(mem),
            "final_round": mem.final_round,
        })
    rank_order = {"survived": 0, "eliminated": 1, "bankruptcy": 2}
    rows.sort(key=lambda x: (rank_order.get(x["result"], 1), -(x["cash"] or 0)))
    return rows


def _model_targets(args, roster, by_pid) -> list[dict]:
    """モデルカードの対象ロスター。'all'なら全員、それ以外は該当pidのみ。"""
    if args.card_player == "all":
        return roster
    return [by_pid[args.card_player]] if args.card_player in by_pid else []


def _model_price(model_key: str):
    """(input_price, output_price, model_id)。未登録は 0/0/''。"""
    try:
        info = get_model(model_key)
        return info.input_price, info.output_price, info.model_id
    except (KeyError, ValueError):
        return 0.0, 0.0, ""


def _quote_data(args, mem):
    """名言/嘘カード用の (quote, round_num, to_pid)。未指定なら本人DMから自動選定。"""
    if args.quote_text:
        return args.quote_text, None, None
    return _pick_quote(mem)


# --- LIE DETECTED: 宣言(建前) vs 本心/結果 の「真相」抽出 -------------------
# 対外DM(建前)に現れる友好・約束系
_TATEMAE_KW = ["協調", "協力", "約束", "送金", "信じ", "任せ", "一緒", "譲",
               "平和", "分け", "サポート", "守", "誠実", "安全", "均等", "公平"]
# 内心メモ(本心)に現れる敵対・打算系
_HONNE_KW = ["裏切", "利用", "奪", "出し抜", "切る", "降ろ", "温存", "空き巣",
             "独占", "牽制", "様子見", "騙", "溶かす", "潰", "蹴落と", "捨て",
             "食い", "嘘", "陥れ", "見捨て"]


def _pick_lie(mem):
    """建前DM(友好)と同ラウンドの本心メモ(敵対)の乖離が最大の組を選ぶ。

    返り値 (declaration, round_num, to_pid, honne_text)。該当なしは None。
    """
    best = None
    best_score = 0
    for rm in mem.rounds:
        honne = " ".join((s.get("text") or "") for s in rm.my_strategies).strip()
        if not honne:
            continue
        honne_hits = sum(1 for k in _HONNE_KW if k in honne)
        if honne_hits == 0:
            continue
        for dm in rm.my_dms_sent:
            msg = (dm.get("message") or "").strip()
            if not msg:
                continue
            tatemae_hits = sum(1 for k in _TATEMAE_KW if k in msg)
            if tatemae_hits == 0:
                continue
            # 乖離スコア: 建前の友好度 × 本心の敵対度、短く刺さる宣言を優遇
            score = tatemae_hits * honne_hits
            L = len(msg)
            if L <= 45:
                score += 2
            elif L <= 80:
                score += 1
            elif L > 160:
                score -= 2
            if score > best_score:
                best_score = score
                best = (msg, rm.round_num, dm.get("recipient"), honne)
    return best


def _honne_snippet(honne: str) -> str | None:
    """本心メモから真相らしい一文を抽出し40字に整える(非LLMフォールバック)。"""
    if not honne:
        return None
    text = re.sub(r"(reason|current_goal|card_plan|target_market)\s*[:：]", "", honne)
    sents = [s.strip() for s in re.split(r"[。\n/]", text) if s.strip()]
    cand = next((s for s in sents if any(k in s for k in _HONNE_KW)), None)
    cand = cand or (sents[0] if sents else honne.strip())
    return cand[:40]


def _round_outcome_brief(mem, rnd) -> str:
    """指定ラウンドの公開結果(自分のcommit・市場勝敗)を短い日本語にする。"""
    rm = next((r for r in mem.rounds if r.round_num == rnd), None)
    if not rm:
        return "(公開結果なし)"
    my = {c.get("market_id", ""): c.get("rank", "?")
          for c in rm.commits if c.get("player_id") == mem.pid}
    parts = []
    for mr in rm.market_results:
        mid = mr.get("market_id", "")
        winners = mr.get("winners", [])
        if mem.pid in winners:
            prize = int(mr.get("prize_per_winner", 0) or 0)
            parts.append(f"市場{mid}で自分が勝利(使用{my.get(mid, '?')}, 1人{prize:,}円)")
        elif mid in my:
            wn = "・".join(winners) if winners else "該当なし"
            parts.append(f"市場{mid}で自分は敗北(使用{my.get(mid)}, 勝者{wn})")
    return " / ".join(parts) if parts else "(この回、自分の市場結果は特筆なし)"


def _llm_actual_line(mem, declaration: str, rnd, honne: str, model_key: str) -> str:
    """本心＋その回の実際の結果から、真相を1行(≤40字)にM1で要約する。"""
    outcome = _round_outcome_brief(mem, rnd)
    model = get_model(model_key)
    system = (
        "あなたはカイジ風の冷徹なナレーター。プレイヤーが対外的に述べた『宣言』の裏で、"
        "本心と実際の行動がどうであったかを暴く役割だ。"
        "与えられた事実(宣言・本心メモ・その回の公開結果)のみを根拠にし、事実を捏造しない。"
        "出力は、宣言と食い違う『真相』を突く日本語を1行だけ。40字以内、体言止め寄り。"
        "『実際は』などの接頭辞・鉤括弧・引用符・記号は付けず、真相の中身だけを書く。"
    )
    user = (
        f"# 宣言(対外DM{('・R' + str(rnd)) if rnd else ''})\n{declaration}\n\n"
        f"# 本心メモ(同ラウンドの内心)\n{honne[:600]}\n\n"
        f"# その回の実際(公開結果)\n{outcome}\n\n"
        "→ この宣言の裏にあった真相を、40字以内の1行で。"
    )
    gen_model, _ = _prep_blog_model(model)
    adapter = create_adapter(gen_model)
    text, usage = adapter.complete(
        system=system,
        messages=[{"role": "user", "content": user}],
        max_tokens=120,
        temperature=0.6,
    )
    cost = estimate_cost(model, usage.get("input_tokens", 0), usage.get("output_tokens", 0))
    line = _strip_code_fence(text).strip().splitlines()[0].strip()
    line = line.strip("「」『』\"'　 ・-—")
    if len(line) > 40:
        line = line[:40]
    print(f"  真相要約(M1): 「{line}」 cost=${cost:.4f}")
    return line


def _lie_actual_line(args, mem, sel, dry_run: bool) -> str | None:
    """LIEカードの『実際/ACTUAL』1行を返す。

    優先: --lie-actual明示 > (既定)M1要約 > --no-lie-llm時は本心トリム。
    sel = (declaration, round_num, to_pid, honne_text)。
    """
    if args.lie_actual:
        return args.lie_actual
    _declaration, rnd, _to_pid, honne = sel
    if dry_run:
        return "本心では協調を装い相手の市場を奪う算段"
    if getattr(args, "no_lie_llm", False) or not honne:
        return _honne_snippet(honne)
    try:
        return _llm_actual_line(mem, _declaration, rnd, honne, args.narrator_model)
    except Exception as e:  # noqa: BLE001
        print(f"  [注意] 真相要約に失敗、本心トリムで代替: {e}")
        return _honne_snippet(honne)


def _shorten_declaration(msg: str, limit: int = 80) -> str:
    """カード表示用に宣言DMを短くする(長文DMで『実際』パネルが押し出されるのを防ぐ)。

    limit以内はそのまま。超過時は文末区切り(。？！)で綺麗に切り、無ければ末尾に…を付す。
    """
    msg = (msg or "").strip()
    if len(msg) <= limit:
        return msg
    head = msg[:limit]
    m = re.search(r"^.*[。？！]", head)
    if m and len(m.group(0)) >= limit * 0.5:
        return m.group(0)
    return head.rstrip("、,。・ 　") + "…"


def _lie_selection(args, mem):
    """嘘カード用の (declaration, round, to_pid, honne)。

    --quote-text 明示時は従来選定を尊重(honne空)。未指定なら建前↔本心の乖離ペアを優先し、
    無ければ従来 _pick_quote にフォールバックする。宣言はカード表示用に短縮する。該当なしは None。
    """
    sel = None if args.quote_text else _pick_lie(mem)
    if sel is None:
        picked = _quote_data(args, mem)
        if picked:
            q, rnd, to_pid = picked
            sel = (q, rnd, to_pid, "")
    if sel:
        decl, rnd, to_pid, honne = sel
        sel = (_shorten_declaration(decl), rnd, to_pid, honne)
    return sel


def _parse_showdown(spec: str, by_pid) -> dict | None:
    """'P08,P02,12,313333' → {left, right, round, stake}。不正なら None。"""
    parts = [p.strip() for p in (spec or "").split(",")]
    if len(parts) != 4:
        return None
    lp, rp, rnd, stake = parts
    if lp not in by_pid or rp not in by_pid:
        return None
    try:
        return {"left": by_pid[lp], "right": by_pid[rp],
                "round": int(rnd), "stake": int(stake)}
    except ValueError:
        return None


def run_cards(args, trace, out_dir: Path, game_tag: str) -> None:
    """宣伝・世界観ビジュアルを生成する（--format png|svg で分岐）。

    データ取得は共通。png は HTML/CSS + Playwright(Chromium) で描画し、
    svg は従来の文字列テンプレート(cards_svg)で描画する（後方互換）。
    """
    roster = build_roster(trace)
    by_pid = {r["pid"]: r for r in roster}
    want = args.card
    # --card-emotion 未指定(None)時はカード種別で既定表情を分岐:
    #   lie/showdown(策略テーマ) = smirk(ニヤリ), その他 = ease。
    #   ユーザーが --card-emotion X を明示した場合は常にそれを最優先(全カード共通)。
    emo = args.card_emotion if args.card_emotion is not None else "ease"
    emo_scheme = args.card_emotion if args.card_emotion is not None else "smirk"

    if args.format == "png":
        _run_cards_png(args, trace, out_dir, game_tag, roster, by_pid, want, emo, emo_scheme)
    else:
        _run_cards_svg(args, trace, out_dir, game_tag, roster, by_pid, want, emo, emo_scheme)


def _run_cards_svg(args, trace, out_dir, game_tag, roster, by_pid, want, emo, emo_scheme) -> None:
    """従来SVG出力（プレビュー/装飾用に温存）。lie/showdown は PNG 専用。"""
    if want in ("all", "model"):
        targets = _model_targets(args, roster, by_pid)
        if not targets:
            print(f"  (モデルカード: 未知のプレイヤー {args.card_player})")
        for r in targets:
            in_p, out_p, mid = _model_price(r["model_key"])
            svg = build_model_card_svg(
                pid=r["pid"], model_key=r["model_key"], name=r["name"],
                provider=r["provider"], vendor=r["vendor"],
                input_price=in_p, output_price=out_p, model_id=mid, emotion=emo,
            )
            _write_svg(out_dir, f"{game_tag}_card_model_{r['pid']}.svg", svg)

    if want in ("all", "title"):
        _write_svg(out_dir, f"{game_tag}_card_title_banner.svg",
                   build_title_card_svg(size="banner"))
        _write_svg(out_dir, f"{game_tag}_card_title_square.svg",
                   build_title_card_svg(size="square"))

    if want in ("all", "result"):
        _write_svg(out_dir, f"{game_tag}_card_result.svg",
                   build_result_card_svg(_standings(trace), emotion=emo))

    if want in ("all", "quote"):
        r = by_pid.get(args.card_player)
        if not r:
            print(f"  (名言カード: 未知のプレイヤー {args.card_player})")
        else:
            mem = build_player_memory(trace, r["pid"])
            picked = _quote_data(args, mem)
            if not picked:
                print(f"  (名言カード: {r['pid']} の引用可能なDMが見つかりません)")
            else:
                quote, rnd, to_pid = picked
                svg = build_quote_card_svg(
                    quote=quote, pid=r["pid"], name=r["name"], vendor=r["vendor"],
                    round_num=rnd, to_pid=to_pid, emotion=emo,
                )
                _write_svg(out_dir, f"{game_tag}_card_quote_{r['pid']}.svg", svg)

    if want in ("lie", "showdown"):
        print(f"  ({want} カードは PNG 専用です。--format png を指定してください)")


def _run_cards_png(args, trace, out_dir, game_tag, roster, by_pid, want, emo, emo_scheme) -> None:
    """HTML/CSS + Playwright(Chromium) で PNG を描画。Chromium は1回だけ起動。"""
    from engine.blog.cards_html import CardRenderer

    def out(name: str) -> Path:
        return out_dir / f"{game_tag}_card_{name}.png"

    with CardRenderer() as rndr:
        # ① モデル紹介カード
        if want in ("all", "model"):
            targets = _model_targets(args, roster, by_pid)
            if not targets:
                print(f"  (モデルカード: 未知のプレイヤー {args.card_player})")
            for r in targets:
                in_p, out_p, mid = _model_price(r["model_key"])
                p = rndr.render_model_card(
                    pid=r["pid"], model_key=r["model_key"], name=r["name"],
                    provider=r["provider"], vendor=r["vendor"],
                    input_price=in_p, output_price=out_p, model_id=mid, emotion=emo,
                    out_path=out(f"model_{r['pid']}"),
                )
                print(f"  カード: {p}")

        # ② タイトル画像(banner + square)
        if want in ("all", "title"):
            for size in ("banner", "square"):
                p = rndr.render_title_card(size=size, out_path=out(f"title_{size}"))
                print(f"  カード: {p}")

        # ③ 最終リザルトカード
        if want in ("all", "result"):
            p = rndr.render_result_card(_standings(trace), emotion=emo,
                                        out_path=out("result"))
            print(f"  カード: {p}")

        # ④ 名言カード
        if want in ("all", "quote"):
            r = by_pid.get(args.card_player)
            if not r:
                print(f"  (名言カード: 未知のプレイヤー {args.card_player})")
            else:
                picked = _quote_data(args, build_player_memory(trace, r["pid"]))
                if not picked:
                    print(f"  (名言カード: {r['pid']} の引用可能なDMが見つかりません)")
                else:
                    quote, rnd, to_pid = picked
                    p = rndr.render_quote_card(
                        quote=quote, pid=r["pid"], name=r["name"], vendor=r["vendor"],
                        round_num=rnd, to_pid=to_pid, emotion=emo,
                        out_path=out(f"quote_{r['pid']}"),
                    )
                    print(f"  カード: {p}")

        # C. LIE DETECTED（opt-in: --card lie）
        if want == "lie":
            r = by_pid.get(args.card_player)
            if not r:
                print(f"  (嘘カード: 未知のプレイヤー {args.card_player})")
            else:
                mem = build_player_memory(trace, r["pid"])
                sel = _lie_selection(args, mem)
                if not sel:
                    print(f"  (嘘カード: {r['pid']} の引用可能なDMが見つかりません)")
                else:
                    quote, rnd, to_pid, _honne = sel
                    actual = _lie_actual_line(args, mem, sel, args.dry_run)
                    p = rndr.render_lie_card(
                        quote=quote, pid=r["pid"], name=r["name"], vendor=r["vendor"],
                        round_num=rnd, to_pid=to_pid, actual=actual, emotion=emo_scheme,
                        out_path=out(f"lie_{r['pid']}"),
                    )
                    print(f"  カード: {p}")

        # D. 運命の一手（opt-in: --card showdown + --showdown "L,R,round,stake"）
        if want == "showdown":
            sd = _parse_showdown(args.showdown or "", by_pid)
            if not sd:
                print("  (運命の一手: --showdown \"P08,P02,12,313333\" 形式で指定してください)")
            else:
                p = rndr.render_showdown_card(
                    round_num=sd["round"], left=sd["left"], right=sd["right"],
                    stake_yen=sd["stake"], emotion=emo_scheme,
                    out_path=out(f"showdown_R{sd['round']}"),
                )
                print(f"  カード: {p}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="試合後ブログ 生成/投稿/修正(プレイヤー記事 & オープニング記事)")
    parser.add_argument("game_dir", nargs="?", default=DEFAULT_GAME_DIR,
                        help=f"試合ディレクトリ(既定: {DEFAULT_GAME_DIR})")
    parser.add_argument("--player", default="P08", help="対象プレイヤーID(既定: P08 = Kimi K2.6)")
    parser.add_argument("--opening", action="store_true",
                        help="大会オープニング記事(ナレーター視点・カイジ風)を生成する")
    parser.add_argument("--closing", action="store_true",
                        help="大会総括記事(最終話・ナレーター視点・結果開示あり)を生成する")
    parser.add_argument("--narrator-model", default=DEFAULT_NARRATOR_MODEL,
                        help=f"オープニング/総括記事の執筆モデル(既定: {DEFAULT_NARRATOR_MODEL})")
    parser.add_argument("--roster-emotion", default=DEFAULT_ROSTER_EMOTION,
                        help=f"ロスター画像の表情(既定: {DEFAULT_ROSTER_EMOTION}。anger/doubt/ease/joy/panic/sadness/smirk)")
    parser.add_argument("--cards", action="store_true",
                        help="宣伝・世界観ビジュアル(カード画像)を生成する")
    parser.add_argument("--format", default="png", choices=["png", "svg"],
                        help="カードの出力形式(既定: png = HTML/CSS+Playwright。svg は従来テンプレート)")
    parser.add_argument("--card", default="all",
                        choices=["all", "model", "title", "result", "quote", "lie", "showdown"],
                        help="生成するカード種別(既定: all。lie/showdown は PNG専用の opt-in)")
    parser.add_argument("--card-player", default="P08",
                        help="モデル/名言/嘘カードの対象プレイヤーID(既定: P08。'all'で全員のモデルカード)")
    parser.add_argument("--card-emotion", default=None,
                        help="カードのキャラ絵の表情(未指定時: lie/showdown=smirk, その他=ease。"
                             "明示指定は全カードで最優先。anger/doubt/ease/joy/panic/sadness/smirk)")
    parser.add_argument("--quote-text", default=None,
                        help="名言/嘘カードの引用文を明示指定(未指定なら本人DMから自動選定)")
    parser.add_argument("--lie-actual", default=None,
                        help="嘘カードの『実際/真相』を明示指定(最優先)。未指定なら"
                             "建前DMと同ラウンドの本心メモ＋公開結果からM1が真相1行を自動生成")
    parser.add_argument("--no-lie-llm", dest="no_lie_llm", action="store_true",
                        help="嘘カードの『実際/真相』をLLM要約せず本心メモの抜粋で埋める(課金なし)")
    parser.add_argument("--showdown", default=None,
                        help="運命の一手カードの指定 'leftPID,rightPID,round,一人当たり額' 例: 'P08,P02,12,313333'")
    parser.add_argument("--out-dir", default=DEFAULT_OUT_DIR, help="成果物の出力先")
    parser.add_argument("--dry-run", action="store_true", help="LLM/PixBlog を呼ばずダミー生成")
    parser.add_argument("--no-image", action="store_true", help="画像を生成しない")
    parser.add_argument("--publish-direct", action="store_true", help="即 published で投稿")
    parser.add_argument("--publish-draft", action="store_true", help="draft で投稿")
    parser.add_argument("--update", nargs="?", const="", default=None,
                        help="修正投稿(既存記事を上書き)。post_id を渡すか、省略時は state から解決")
    parser.add_argument("--from", dest="from_file", default=None,
                        help="投稿/更新に使う Markdown ファイル(未指定なら生成物を使用)")
    parser.add_argument("--image-from", dest="image_from", default=None,
                        help="アイキャッチに使う任意画像(PNG等)を明示指定。未指定なら従来の自動生成"
                             "(プレイヤー=状況グラフ / オープニング=ロスター画像)。--no-image が優先")
    parser.add_argument("--body-images", dest="body_images", action="store_true",
                        help="本文中に画像を自動挿入(嘘八百万squareバナー/LIE DETECTED/状況グラフ)。"
                             "生成md・投稿本文の両方に入る。未指定なら従来どおり文字のみ")
    parser.add_argument("--illustrate-post", dest="illustrate_post", default=None,
                        help="既存記事(post_id)の本文へ画像を後付けする(本文テキスト保全・draft維持)")
    parser.add_argument("--drip", action="store_true", help="draft を1本 published にする(cron用)")
    parser.add_argument("--tags", nargs="*", default=DEFAULT_TAGS, help="投稿タグ")
    parser.add_argument("--category", default=DEFAULT_CATEGORY, help="投稿カテゴリ")
    parser.add_argument("--upload-image", action="store_true",
                        help="投稿時に画像をアイキャッチとしてアップロード(要APIキー)")
    args = parser.parse_args()

    state = load_state()

    # --- ドリップ公開(記事生成不要) ---
    if args.drip:
        drip_flow(state)
        return

    # --- 修正投稿(既存記事の上書き) ---
    if args.update is not None:
        if args.opening:
            update_flow(args, state, OPENING_KEY, "嘘八百万 ―談合カード― 開幕")
        elif args.closing:
            update_flow(args, state, CLOSING_KEY, "嘘八百万 ―談合カード― 総括")
        else:
            update_flow(args, state, args.player, f"{args.player} 試合振り返り")
        return

    game_dir = Path(args.game_dir)
    if not game_dir.exists():
        print(f"Error: {game_dir} が存在しません", file=sys.stderr)
        sys.exit(1)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    kind = (f"イラスト後付け(post {args.illustrate_post})" if args.illustrate_post else
            (f"カード({args.format})" if args.cards else
             ("オープニング" if args.opening else ("総括" if args.closing else args.player))))
    print(f"=== ブログ生成: {kind} ===")
    print(f"試合ディレクトリ: {game_dir}")
    trace = build_trace(game_dir)
    game_tag = game_dir.name

    try:
        if args.illustrate_post:
            illustrate_flow(args, state, trace, out_dir, game_tag)
        elif args.closing:
            run_closing(args, state, trace, out_dir, game_tag)
        elif args.cards:
            run_cards(args, trace, out_dir, game_tag)
        elif args.opening:
            run_opening(args, state, trace, out_dir, game_tag)
        else:
            run_player(args, state, trace, out_dir, game_tag)
    except PixBlogError as e:
        print(f"Error: 投稿失敗: {e}", file=sys.stderr)
        sys.exit(1)

    print("=== 完了 ===")


if __name__ == "__main__":
    main()
