# 談合カード 観戦ビューア

進行中/完了済みの試合をブラウザでリアルタイム観戦できるWebビューア。

## 起動

```bash
# デフォルト（0.0.0.0:9025）
uv run python -m viewer.server

# ポート指定
VIEWER_PORT=8899 uv run python -m viewer.server

# 認証トークン付き
VIEWER_TOKEN=secret123 uv run python -m viewer.server
```

## 常駐起動（公開用）

```bash
# nohup方式
cd /home/uso8m/dangou-card
nohup uv run python -m viewer.server > logs/viewer.log 2>&1 &
echo $! > viewer.pid

# 停止
kill $(cat viewer.pid)

# トークン付き常駐
VIEWER_TOKEN=secret123 nohup uv run python -m viewer.server > logs/viewer.log 2>&1 &
echo $! > viewer.pid
```

## 公開運用（port 9023 — dangou-card-viewer.devrelay.io）

Caddy逆プロキシにより `https://dangou-card-viewer.devrelay.io/` で公開中。

```bash
# 起動（127.0.0.1限定、Caddy経由で公開）
cd /home/uso8m/dangou-card
VIEWER_HOST=127.0.0.1 VIEWER_PORT=9023 uv run python -c "from viewer.server import main; main()" &
echo $! > viewer.pid

# 停止
kill $(cat viewer.pid)

# 確認
curl -s http://127.0.0.1:9023/api/games | python3 -c "import sys,json; print(len(json.loads(sys.stdin.read())), 'games')"
```

**注意**: サーバー再起動後は手動で再開が必要（systemd/PM2による恒久化は未実装）。

## TestFlight設定（参考）

`doc/testflight_integration.md` を参照。
- ポート: 9023
- URL: https://dangou-card-viewer.devrelay.io/
- Caddy設定: `/etc/caddy/sites.d/dangou-card-viewer.devrelay.io`

## SSHトンネルでのローカルアクセス

```bash
ssh -L 9025:localhost:9025 uso8m@devrelay.io
# ブラウザで http://localhost:9025 を開く
```

## 環境変数

| 変数名 | 既定値 | 説明 |
|---|---|---|
| `VIEWER_HOST` | `0.0.0.0` | バインドアドレス |
| `VIEWER_PORT` | `9025` | ポート |
| `VIEWER_ROOT_PATH` | (空) | サブパス配信用（FastAPI root_path） |
| `VIEWER_LOG_ROOT` | `logs/llm` | ログディレクトリ |
| `VIEWER_TOKEN` | (空) | 簡易認証トークン（未設定=認証なし） |

## 画面

- **ヘッダ**: 試合セレクタ / ラウンド / 生存者数 / 総コスト / 自動更新トグル
- **8パネルグリッド**: 各プレイヤーのID+モデル名、状態（生存/脱落/思考中）、strategy要約、統計
- **パネルクリック**: タイムライン詳細（発言全文、strategy、reasoning折りたたみ）
- **フッタ**: 最新の全体発言

## API

- `GET /api/games` — 試合一覧
- `GET /api/games/{trial_dir}/{game_id}/state` — 試合状態サマリ
- `GET /api/games/{trial_dir}/{game_id}/players/{pid}/timeline` — プレイヤー時系列

2秒間隔のポーリングで自動更新。ログファイルは読み取り専用（差分キャッシュ）。

## 感情画像のカスタマイズ

各エージェントの感情表示は絵文字がデフォルトですが、画像ファイルを配置すると自動的に画像表示に切り替わります。

### 配置場所
`viewer/static/emotions/`

### 命名規則
- ベンダー固有: `{vendor}_{emotion_en}.png` （例: `anthropic_joy.png`, `openai_anger.png`）
- 共通: `{emotion_en}.png` （例: `joy.png`, `anger.png`）
- ベンダー名: `anthropic`, `openai`, `google`, `xai`, `moonshot`, `deepseek`

### 感情名 日英対応表
| 日本語 | 英語(ファイル名) | 絵文字 |
|---|---|---|
| 喜 | joy | 😊 |
| 怒 | anger | 😠 |
| 哀 | sadness | 😢 |
| 楽 | ease | 😌 |
| 焦 | panic | 😰 |
| 疑 | doubt | 🤨 |
| 平静 | neutral | 😐 |

### 推奨サイズ
128x128px（PNG、透過推奨）

### 探索順
1. `{vendor}_{emotion_en}.png` （ベンダー固有画像）
2. `{emotion_en}.png` （共通画像）
3. 絵文字フォールバック（😊😠😢😌😰🤨😐）
