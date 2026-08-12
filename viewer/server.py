"""
観戦ビューア FastAPI サーバー

環境変数で設定可能:
  VIEWER_HOST     バインドアドレス（既定: 0.0.0.0）
  VIEWER_PORT     ポート（既定: 9025）
  VIEWER_ROOT_PATH  サブパス配信用（既定: 空）
  VIEWER_LOG_ROOT   ログディレクトリ（既定: logs/llm）
  VIEWER_TOKEN    簡易認証トークン（未設定: 認証なし）
"""

import os
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Depends, HTTPException, Query, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from viewer.log_parser import (
    list_games, get_game_state, get_player_timeline, get_commentary,
    get_round_states,
)

# --- 環境変数による設定 ---
# デフォルトのログディレクトリ
DEFAULT_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs" / "llm"
STATIC_DIR = Path(__file__).resolve().parent / "static"

HOST = os.environ.get("VIEWER_HOST", "0.0.0.0")
PORT = int(os.environ.get("VIEWER_PORT", "9025"))
ROOT_PATH = os.environ.get("VIEWER_ROOT_PATH", "")
LOGS_DIR = Path(os.environ.get("VIEWER_LOG_ROOT", str(DEFAULT_LOGS_DIR)))
TOKEN = os.environ.get("VIEWER_TOKEN", "")

# --- FastAPIアプリ ---
app = FastAPI(
    title="談合カード 観戦ビューア",
    version="1.1",
    root_path=ROOT_PATH,
)

# 静的ファイル配信
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# --- 簡易認証（VIEWER_TOKEN設定時のみ有効） ---
async def check_token(
    request: Request,
    token: Optional[str] = Query(None),
):
    """
    VIEWER_TOKEN設定時: ?token= クエリまたは X-Viewer-Token ヘッダで照合
    VIEWER_TOKEN未設定時: 認証なし（従来どおり）
    """
    if not TOKEN:
        return  # 認証なし
    # クエリパラメータまたはヘッダから取得
    provided = token or request.headers.get("X-Viewer-Token", "")
    if provided != TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized")


@app.get("/")
async def landing(_=Depends(check_token)):
    """ランディングページ（LP）"""
    return FileResponse(str(STATIC_DIR / "lp.html"))


@app.get("/watch")
async def watch(_=Depends(check_token)):
    """観戦ビューア本体（相対パス依存のため末尾スラッシュ無しで配信）"""
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/games")
async def api_games(_=Depends(check_token)):
    """試合一覧を返す"""
    return list_games(LOGS_DIR)


@app.get("/api/games/{trial_dir}/{game_id}/state")
async def api_game_state(trial_dir: str, game_id: str, _=Depends(check_token)):
    """試合の現在状態サマリを返す"""
    return get_game_state(LOGS_DIR, trial_dir, game_id)


@app.get("/api/games/{trial_dir}/{game_id}/commentary")
async def api_commentary(trial_dir: str, game_id: str, _=Depends(check_token)):
    """実況台本データを返す"""
    data = get_commentary(LOGS_DIR, trial_dir, game_id)
    if data is None:
        raise HTTPException(status_code=404, detail="Commentary not found")
    return data


@app.get("/api/games/{trial_dir}/{game_id}/rounds")
async def api_rounds(trial_dir: str, game_id: str, _=Depends(check_token)):
    """ラウンド別の盤面状況を返す"""
    return get_round_states(LOGS_DIR, trial_dir, game_id)


@app.get("/api/games/{trial_dir}/{game_id}/players/{pid}/timeline")
async def api_player_timeline(
    trial_dir: str, game_id: str, pid: str, _=Depends(check_token),
):
    """プレイヤーの時系列を返す"""
    return get_player_timeline(LOGS_DIR, trial_dir, game_id, pid)


def main():
    """サーバー起動"""
    import uvicorn
    print("=== 談合カード 観戦ビューア ===")
    print(f"URL: http://{HOST}:{PORT}")
    if ROOT_PATH:
        print(f"Root path: {ROOT_PATH}")
    if TOKEN:
        print(f"認証: 有効（VIEWER_TOKEN設定済み）")
    else:
        print(f"認証: なし（公開アクセス可）")
    print(f"ログ: {LOGS_DIR}")
    print("---")
    uvicorn.run(app, host=HOST, port=PORT)
