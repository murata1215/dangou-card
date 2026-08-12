"""観戦ビューアの LP / ルーティングの受け入れテスト。

- `/`      … LP（ランディングページ）を返す
- `/watch` … 観戦ビューア本体(index.html)を返す
- `/api/games` … 200（試合一覧API）
"""
from fastapi.testclient import TestClient

from viewer.server import app

client = TestClient(app)


def test_root_serves_landing_page():
    r = client.get("/")
    assert r.status_code == 200
    body = r.text
    # LP 固有のマーカ
    assert "観戦する" in body
    assert "敗者の手記を読む" in body
    # 観戦導線とブログ導線
    assert 'href="watch"' in body
    assert "pixblog.net/u/uso8m" in body


def test_watch_serves_viewer():
    r = client.get("/watch")
    assert r.status_code == 200
    # 観戦ビューア本体のマーカ（title）
    assert "観戦ビューア" in r.text
    # 相対パスが崩れないよう末尾スラッシュを付けない設計
    assert "api/games" in r.text  # 相対fetchが本体に残っている


def test_watch_no_trailing_slash_redirect():
    # /watch は末尾スラッシュ無しで直接200（/watch/ への恒久リダイレクトを張らない）
    r = client.get("/watch", follow_redirects=False)
    assert r.status_code == 200


def test_api_games_ok():
    r = client.get("/api/games")
    assert r.status_code == 200
    assert isinstance(r.json(), list)
