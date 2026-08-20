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

### 引き継ぎメモリ（Handover Memory）

LLMコールは1-shotで会話履歴を持たず、チャット・戦略メモは毎ラウンド消えるため、標準構成のAIは
前ラウンドの裏切り等を一切覚えていない。`config.memory_enabled=True`（S2プリセットのみ既定True）
にすると、各ラウンド終了後（Settlement/Finance完了後）に専用のReflectionフェイズが走り、各AIへ
「前ラウンドのmemory＋当ラウンドの会話・契約・結果」を材料に、次ラウンドへ持ち越す自由記述メモ
（既定1000字、`memory_max_chars`で調整）を1枚だけ書かせる。渡すのは常に最新の1枚のみ（累積しない）。
フォーマットは強制しないため、何を残し何を捨てるかはモデルの判断に委ねられる。詳細は
`doc/uso8000000_dangou_card_spec_v0_8_season2.md` §9.6を参照。

### DM（ダイレクトメッセージ）の秘匿性

DMの本文は送信者・宛先の当事者にしか見えない。非当事者には`[送信者→宛先] （非公開のDM）`と
メタデータのみが表示され、本文は`visible_state`の段階でキーごと削除される（reasoning・memoryと
同じ構造的秘匿）。broadcastは全員に本文が公開され、匿名通信（`anonymous_broadcast`）は本文は
全員公開だが掲載者は秘匿される。詳細は仕様書§8.2・`rules/project.md`の該当節を参照。

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

### 低コスト6体→18体 負荷試験（インフラ耐久確認）

Season 2 最大想定の18体でengine/llm/logging/viewerが耐えられるかを、安価3モデル（`L7`:Gemini 2.5 Flash-Lite / `L6`:DeepSeek V4 Flash / `L2`:GPT-4.1 Mini）構成で確認する手順。**モデルの強さ評価ではなく、最大人数でのインフラ耐久確認が目的**。

```bash
# STEP 1: 6体スモーク（正常系確認、seed=701）
bash scripts/run_trial.sh --phase C --ruleset S2 --roster "L7,L7,L6,L6,L2,L2" --games 1 --rounds 12 --seed 701
bash scripts/check_trial.sh

# STEP 2: 6体が Completed: YES になってから実行する18体（最大人数負荷試験、seed=811）
bash scripts/run_trial.sh --phase C --ruleset S2 --roster "L7,L7,L7,L7,L7,L7,L6,L6,L6,L6,L6,L6,L2,L2,L2,L2,L2,L2" --games 1 --rounds 12 --seed 811
bash scripts/check_trial.sh
ps -o rss=,etime= -p $(cat $(ls -t logs/llm/run_*.pid | head -1))   # メモリ(RSS,KB)と経過時間
```

注意事項:
- `--roster` はカンマの後にスペースを入れないこと（`llm_trial.py` の roster解析は `.strip()` していないため、空白入りだと `Unknown model` エラーになる）
- CoTは両方OFF（`--cot` を付けない）。低コスト負荷試験が目的で、CoT ONは出力トークン・所要時間を大きく膨らませるため
- **18体走行中はビューワーを開きっぱなしにしない**。観戦画面は2秒間隔ポーリングで進行中ログを毎回フルパースするため、18体分のログ（試合完走時で数十MB規模）を継続的に読み続けると負荷が増す。18体の描画確認は**試合完了後**に行うこと

### 18モデル段階的スモークテスト（`scripts/model_matrix.py`）

6社18モデル（強6:H1〜H6 / 中6:M1〜M6 / 軽6:L1〜L6）の実API疎通・課金実測を、Phase 0（在庫確認,$0）
→ Phase 1（疎通確認）→ Phase 2（談合カードJSON互換）→ Phase 3（ミニゲーム統合）の4段階でコスト・
コール数上限ガード付きに実行するスクリプト。進行状況は `state.json` で管理し `--resume` で再開できる。

```bash
uv run python scripts/model_matrix.py --phase 1 --models H1,H2,H3 --dry-run   # 見積のみ（$0）
uv run python scripts/model_matrix.py --phase 1 --models H1,H2,H3 --max-cost 0.1
uv run python scripts/model_matrix.py --phase 2 --resume
```

Phase 2の有料比較は、各モデルにつきクライアントからの送信を1回に抑えるため、adapter/SDK retryと
temperature互換フォールバックを無効化する。成功・parse失敗を問わず、APIが返した本文全文は
`logs/model_matrix/<run-id>/phase2_calls.jsonl` の `response_text` に保存される。既存runを継続する場合は、
通算上限を変えないよう `--max-cost-total` を明示する。

```bash
uv run python scripts/model_matrix.py --phase 2 --run-id run_20260818_041810 \
  --resume --tier all --max-cost 0.22 --max-cost-per-model 0.03 \
  --max-calls 24 --max-tokens 400 --retries 0 --max-cost-total 0.25
```

実API前には同じ引数へ `--dry-run` を付け、出力をレビューしてから別途承認する。dry-runはAPI・state・
`phase2_calls.jsonl` を変更しないが、集計レポートを再生成する副作用がある。

Phase 2で失敗済みモデルだけを単発再試行する場合は、`--models <key> --retry-failed --max-calls 1`を使う。
H1（Claude Opus 5）はPhase 2だけflat transport schemaと専用promptを使い、受信後にcanonical actionへ正規化する。
M3（Gemini 3.5 Flash）は`reasoning_effort="minimal"`・512 tokens、H3（Gemini 3.1 Pro Preview）は`low`・
912 tokensでcanonical schemaを維持する。Geminiの`reasoning_effort`と`thinking_level`/`thinking_budget`は併送しない。
M3/H3の予約を同時に確保するとこのrunのtotal capを超えるため、M3の結果・実費を確認してからH3を別承認で実行する。

コスト計算は全フェーズで実測usageベースの `_usage_cost()` に統一されており、Geminiのhidden
thinking・xAI等のキャッシュ割引・Anthropicのusage慣習差を正しく反映する。詳細は `rules/project.md`
の該当節を参照。

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
