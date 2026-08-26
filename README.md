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

### スモークテスト（実API 0コール）

`scripts/contract_cancel_smoke.py`は`contract_cancel`機能の経路をAPI-freeで確認する
4モードを持つ（いずれも実ネットワークAPIへ0コール）。

```bash
uv run python scripts/contract_cancel_smoke.py --repro     # Phase A: AUTO_PASS問題の再現/修正確認
uv run python scripts/contract_cancel_smoke.py --dry-run   # 0 API-equivalent call の証明
uv run python scripts/contract_cancel_smoke.py --mock      # ScriptAdapter経由の全経路疎通確認
uv run python scripts/contract_cancel_smoke.py --scripted  # 4席本番経路スモーク（10項目チェック）
```

`--real --model L6 --run-id <id> --max-calls N --max-cost X --max-cost-per-call Y`は
実API（DeepSeek V4 Flash限定・`REAL_MODEL_ALLOWLIST`で機械的に制限）を叩くモードで、
人間の明示承認後にのみ実行する。

`scripts/replay_probe.py`は過去トライアルログ（例: `trial_C_l12_r12_20260824`）を
読み取り専用で再生し、baseline（当時のprompt）とpatched（現行コードで再生成した
prompt）を同一モデルへ投げて対比するA/Bハーネス。`--execute`以外は実API0件。

```bash
uv run python scripts/replay_probe.py --dry-run                       # 既定27件・API0件
uv run python scripts/replay_probe.py --dry-run --targets-file <json> # 選抜差し替え・API0件
uv run python scripts/replay_probe.py --execute --preflight-only ...  # 見積のみ・API0件
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
（既定3000字、`memory_max_chars`で調整。上限超過分は意味境界を優先して切り詰める）を1枚だけ書かせる。渡すのは常に最新の1枚のみ（累積しない）。
フォーマットは強制しないため、何を残し何を捨てるかはモデルの判断に委ねられる。詳細は
`doc/uso8000000_dangou_card_spec_v0_8_season2.md` §9.6を参照。

### 脱落者の最終振り返り（FINAL_REFLECTION）

`config.final_reflection_enabled=True`（`baseline_v1_s2`/`default_8_s2`プリセットのみ既定True）
にすると、LLMエージェントが脱落した瞬間に1回だけ追加コールが発生し、`emotion`（感情）・
`defeat_cause`（本人が考える敗因）・`comment`（最期のコメント）を自由記述で書かせる
（`final_reflection_max_tokens`で上限調整、既定3000tokens）。1脱落=1回発火を保証しており、
`FINAL_REFLECTION`イベントとしてログに記録される。Viewerでは脱落ラウンドの詳細に表示できるが、
`comment`/`defeat_cause`は一人称の自由記述でも§8.2秘匿情報を含み得るため、公開viewでは
`emotion`のみを見せ、本文は神視点限定にしている（詳細は`rules/project.md`該当節）。

### DM（ダイレクトメッセージ）の秘匿性

DMの本文は送信者・宛先の当事者にしか見えない。非当事者には`[送信者→宛先] （非公開のDM）`と
メタデータのみが表示され、本文は`visible_state`の段階でキーごと削除される（reasoning・memoryと
同じ構造的秘匿）。broadcastは全員に本文が公開され、匿名通信（`anonymous_broadcast`）は本文は
全員公開だが掲載者は秘匿される。詳細は仕様書§8.2・`rules/project.md`の該当節を参照。

### 全当事者合意による契約解除（contract_cancel）

同一R・同一市場に両立不能な条件の契約が二重に成立すると、1ラウンドに出せるカードは1枚しか
ないためどちらかが必ず型B違反で脱落する。これを解消するため、生存する全当事者が
`contract_cancel`アクション（`{"type": "contract_cancel", "contract_id": "..."}`）を出すと
契約が`ACTIVE`から`CANCELLED`へ遷移する。解除可否は永続フラグを持たず、義務が
（型Aなら）未履行かつ（型Bなら）未監査かつ期限が現在R以降であることから毎回導出される。
片側だけが同意した状態では契約台帳に`⏳ 解除同意 1/2`のように表示され、全員揃うまで
`ACTIVE`のまま。解除された契約の以後の義務は、Settlement・型B監査・型A執行・脱落判定の
既存ロジック（`status != ACTIVE`で弾く）にそのまま乗るため対象外になる。詳細は
`rules/project.md`「§ACTIVEからの唯一の実遷移」を参照。

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
  --resume --tier all --max-cost 0.23 --max-cost-per-model 0.03 \
  --max-calls 24 --max-tokens 400 --retries 0 --max-cost-total 0.25
```

実API前には同じ引数へ `--dry-run` を付け、出力をレビューしてから別途承認する。dry-runはAPI・state・
`phase2_calls.jsonl` を変更しないが、集計レポートを再生成する副作用がある。

Phase 2で失敗済みモデルだけを単発再試行する場合は、`--models <key> --retry-failed --max-calls 1`を使う。
H1（Claude Opus 5）はPhase 2だけflat transport schemaと専用promptを使い、受信後にcanonical actionへ正規化する。
M3（Gemini 3.5 Flash）は`reasoning_effort="minimal"`・512 tokens、H3（Gemini 3.1 Pro Preview）は`low`・
912 tokensでcanonical schemaを維持する。Geminiの`reasoning_effort`と`thinking_level`/`thinking_budget`は併送しない。
M3/H3の予約を同時に確保するとこのrunのtotal capを超えるため、M3の結果・実費を確認してからH3を別承認で実行する。

Phase 3のミニゲーム統合もstrict実行である。matrix経路に限りadapter/SDK retryとtemperature互換
フォールバックを無効化するため、loan・negotiation・commitの各logical callは最大1 transport sendとなる。
JSON parse correctionは意図した別logical callとして最大7 call/modelまで許容する。モデル単位の
`phase3_calls.jsonl` 集計にはrequested/response model、model match、logical calls、negotiation/commit
correction数を追加し、raw response・usage・finish reason・latency・costは従来どおり個別LLMログに保存する。

Phase 3だけは、明示的な再試験で `--effort {low,medium,high,xhigh,max}` と
`--timeout-seconds N` を使える。effortは現在H1（Claude Opus 5）のadaptive thinking対応経路だけへ送信し、
timeoutはregistryを書き換えないruntime `ModelInfo` copyへ適用する。実行結果には
`effective_max_tokens`、`runtime_effort`、`runtime_timeout_seconds` をJSONL・state・レポートへ保存するため、
attempt条件を保存ログから再現できる。未指定時のrequest specは従来どおりである。

2026-08-20の18モデルPhase 3最終cost、H1/H4再試験、Provider Console提供値、およびM3のR12実績は
[`doc/cost/api_cost_estimate_2026-08-20.md`](doc/cost/api_cost_estimate_2026-08-20.md) に固定している。
Console値とR12円建て実績はユーザー提供値であり、run内usageからの実測とは区別している。

新モデルを評価する際は、正式ロスターのキー（`M3`等）を直接書き換えず、`M3_G37_EVAL`のような
`_EVAL`接尾辞の検証専用キーを`MODEL_REGISTRY`に追加する。正式キーは既存モデルを指したまま変更されない。
Gemini 3.7 Flash検証（`scripts/gemini_37_eval.py`）は、この方式でPhase 1〜3を実施し、正式M3
（Gemini 3.5 Flash）比で実測64.04%低コストと確認した（詳細: `doc/gemini_37_flash_eval_report.md`）。

コスト計算は全フェーズで実測usageベースの `_usage_cost()` に統一されており、Geminiのhidden
thinking・xAI等のキャッシュ割引・Anthropicのusage慣習差を正しく反映する。詳細は `rules/project.md`
の該当節を参照。

## JSONLイベント仕様

`logs/` にJSONL形式で全イベントを出力。各行は1イベント:

```json
{"event_type": "MARKET_RESULT", "timestamp": "...", "round_num": 1, "phase": "settlement", "step": 2, "data": {...}}
```

主要イベント種別: `GAME_START`, `LOAN_CHOSEN`, `MARKET_OPEN`, `NEGOTIATION_ACTION`, `COMMIT`, `AUTO_COMMIT`, `BANKRUPTCY`, `REVEAL`, `MARKET_RESULT`, `TYPE_B_VIOLATION`, `SNAPSHOT`, `TYPE_A_EXECUTION`, `TYPE_A_FAILURE`, `BOUNTY_TRIGGERED`, `ELIMINATION`, `FORCED_LIQUIDATION`, `INTEREST`, `REPAYMENT`, `AUTO_REPAYMENT`, `SURVIVAL_CHECK`, `FINAL_REFLECTION`, `GAME_END`

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

公開Viewerの再起動・障害切り分けは[Viewer運用マニュアル](doc/viewer_operations.md)を参照。開発用の起動方法は`viewer/README.md`を参照。

通常のViewer APIはDM本文・宛先、匿名発言者、契約条項を非公開にする。運用者向けの神視点は、ヘッダーの`VIEW: PUBLIC 🔒`/`VIEW: GOD 🔓`切替で別管理の`VIEWER_GOD_TOKEN`を入力した場合だけ利用できる。一度有効化すればタブ内では以後の全プレイヤー詳細で再入力不要（tokenはタブ限定のsessionStorageに保持し、タブを閉じると失効）。tokenの保管・反映・確認手順も[Viewer運用マニュアル](doc/viewer_operations.md)を正とする。

脱落者のラウンド詳細には「脱落時コメント」セクション（FINAL_REFLECTION）を表示する。通常表示では感情のみ、
神視点では敗因・最後の言葉の本文まで見られる（自由記述が§8.2秘匿情報を含み得るため）。

神視点のDM・公開発言には、LLMログとEngineイベントの突合結果として`✅ 成立`/`❌ 不成立`/`❔ 未検証`の
delivery badgeを表示する。脱落済みプレイヤー宛のDM等、engineに拒否されたアクション（行動枠は消費済み）が
通常メッセージと見分けがつかない形で表示されないようにするための表示（`rules/project.md`該当節）。

## プロジェクト構成

```
engine/           # ルールエンジン本体（16モジュール）
viewer/           # 観戦ビューア（FastAPI）+ LP（static/lp.html）
tests/            # テスト（受け入れ16件 + 追加テスト）
scripts/          # ドライラン・シミュレーション・LLM試験・スモークスクリプト
doc/              # 仕様書・ドキュメント
```
