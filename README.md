# 嘘八百万 —談合カード— ルールエンジン

20体のAIが、12枚の有限カードと借金を抱え、12回の市場を交渉・嘘・契約で奪い合い、最後に借金を完済して300万円以上を残せるかを競うゲーム。

## セットアップ

```bash
uv sync
```

## テスト実行

```bash
# 全テスト（受け入れ16件 + 追加テスト）
uv run pytest tests/ -v
```

## ドライラン

```bash
# 12人×12ラウンド（デフォルト）
uv run python scripts/dry_run.py

# 20人版
uv run python scripts/dry_run.py --players 20

# シード指定
uv run python scripts/dry_run.py --seed 12345
```

## パラメータ設定

`engine/config.py` の `GameConfig` で全経済パラメータを管理。

```python
from engine.config import GameConfig

config = GameConfig.default_20()  # 20人版
config = GameConfig.default_12()  # 12人版（テスト大会用）
config = GameConfig(survival_cash=2_000_000)  # カスタム
```

## LLMトライアル実行（本番API）

`scripts/llm_trial.py` を直接フォアグラウンド実行すると、Claude/DevRelayセッションのタイムアウトで長時間試合が巻き込まれてkillされることがある。12ラウンドフル試合など時間のかかるトライアルは、必ずデタッチ起動ラッパーを使うこと。

```bash
# デタッチ起動（setsid nohup で親プロセスから完全に切り離す）
bash scripts/run_trial.sh --phase C --ruleset S2 --roster "M3,M3,M3,L2,L2,L2,M6,M6,M6" --games 1 --seed 504

# 進行確認（最新トライアルのラウンド・完了状態・PID生存・座席マップ・ログ末尾を表示）
bash scripts/check_trial.sh
```

CoT (Chain-of-Thought) を有効化する場合は `--cot` フラグを追加する（LLM応答JSONに `reasoning` フィールドを要求。デフォルト無効）。

```bash
bash scripts/run_trial.sh --phase C --ruleset S2 --roster "..." --cot --rounds 1 --games 1 --seed 600
```

`--rounds N` でラウンド数を打ち切ることができ、スモークテスト等の低コスト確認に使う。

## JSONLイベント仕様

`logs/` にJSONL形式で全イベントを出力。各行は1イベント:

```json
{"event_type": "MARKET_RESULT", "timestamp": "...", "round_num": 1, "phase": "settlement", "step": 2, "data": {...}}
```

主要イベント種別: `GAME_START`, `LOAN_CHOSEN`, `MARKET_OPEN`, `NEGOTIATION_ACTION`, `COMMIT`, `AUTO_COMMIT`, `BANKRUPTCY`, `REVEAL`, `MARKET_RESULT`, `TYPE_B_VIOLATION`, `SNAPSHOT`, `TYPE_A_EXECUTION`, `TYPE_A_FAILURE`, `BOUNTY_TRIGGERED`, `ELIMINATION`, `FORCED_LIQUIDATION`, `INTEREST`, `REPAYMENT`, `AUTO_REPAYMENT`, `SURVIVAL_CHECK`, `GAME_END`

## 観戦ビューア / LP

進行中・完了済みの試合をブラウザで観戦できる FastAPI ビューア（`viewer/`）。
公開URL `https://dangou-card-viewer.devrelay.io/`（Caddy → 127.0.0.1:9023）。

- `/` … **LP（ランディングページ）**。「▶ 観戦する」→ `/watch`、「📖 敗者の手記を読む」→ ブログ(PixBlog)。記事ピックアップは PixBlog `feed.json` から動的取得（取得失敗時は静的フォールバック）
- `/watch` … 観戦ビューア本体（相対パス依存のため末尾スラッシュ無しで配信）
- `/api/*` … 試合データAPI

```bash
uv run python -m viewer.server           # 既定 0.0.0.0:9025
VIEWER_HOST=127.0.0.1 VIEWER_PORT=9023 uv run python -c "from viewer.server import main; main()"  # 公開用（手動起動）
```

公開ビューアは **user systemd unit `dangou-viewer.service`** で常駐化しており（`Restart=always` + linger 有効）、
クラッシュ・マシン再起動後も自動復帰する。手動起動は不要。

```bash
systemctl --user status dangou-viewer.service   # 稼働確認
systemctl --user restart dangou-viewer.service  # 再起動
journalctl --user -u dangou-viewer.service -f   # ログ追尾
```

unit 定義: `~/.config/systemd/user/dangou-viewer.service`（`ExecStart` は `.venv/bin/python -c "from viewer.server import main; main()"`、`VIEWER_HOST=127.0.0.1` / `VIEWER_PORT=9023`）。

詳細は `viewer/README.md` を参照。

## プロジェクト構成

```
engine/           # ルールエンジン本体（16モジュール）
viewer/           # 観戦ビューア（FastAPI）+ LP（static/lp.html）
tests/            # テスト（受け入れ16件 + 追加テスト）
scripts/          # ドライランスクリプト
doc/              # 仕様書・ドキュメント
```
