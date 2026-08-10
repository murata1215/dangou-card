# TestFlight連携ガイド: 観戦ビューア公開

## アーキテクチャ判定: 案A（uso8m常駐 + Caddy逆プロキシ）を推奨

### 理由
- ビューアはuso8mユーザーで常駐し、`0.0.0.0:9025`にlisten
- Caddyが`dangou-card-viewer.devrelay.io`から逆プロキシ
- ログへのアクセス権限問題なし（uso8m自身が読む）
- ホームディレクトリのパーミッション変更不要（700のまま）

### 案B（devrelayユーザーで実行）が非推奨の理由
- uso8mのホームが700のため、devrelayユーザーからログを読めない
- ホームのパーミッション変更はセキュリティリスク
- ログの同期/コピーは複雑でデータ遅延が発生

---

## けいすけ側で必要な作業

### 1. Caddy設定ファイルの作成
```bash
sudo tee /etc/caddy/sites.d/dangou-card-viewer.devrelay.io > /dev/null <<'EOF'
dangou-card-viewer.devrelay.io {
  reverse_proxy localhost:9025
}
EOF
```

### 2. Caddyの再読み込み
```bash
sudo systemctl reload caddy
```

### 3. ビューアの常駐起動（uso8mユーザーで）
```bash
# uso8mユーザーにログイン
su - uso8m

# 起動
cd /home/uso8m/dangou-card
nohup uv run python -m viewer.server > logs/viewer.log 2>&1 &
echo $! > viewer.pid

# 停止（必要時）
kill $(cat viewer.pid)
```

### 4. 認証トークンを設定する場合（オプション）
```bash
# 起動時にVIEWER_TOKENを設定
VIEWER_TOKEN=your_secret_token nohup uv run python -m viewer.server > logs/viewer.log 2>&1 &

# アクセス時
# https://dangou-card-viewer.devrelay.io/?token=your_secret_token
```

---

## ポート情報
- 使用ポート: **9025**（空き確認済み。TestFlight既存は9002〜9022）
- バインドアドレス: `0.0.0.0`（Caddyからアクセス可能にするため）

## 環境変数一覧
| 変数名 | 既定値 | 説明 |
|---|---|---|
| `VIEWER_HOST` | `0.0.0.0` | バインドアドレス |
| `VIEWER_PORT` | `9025` | ポート |
| `VIEWER_ROOT_PATH` | (空) | サブパス配信用 |
| `VIEWER_LOG_ROOT` | `logs/llm` | ログディレクトリ |
| `VIEWER_TOKEN` | (空) | 簡易認証トークン（未設定=認証なし） |
