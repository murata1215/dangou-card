# Changelog

## 2026-08-13: 公開LP復旧＋観戦ビューア常駐化（user systemd）

### 概要
公開LP `https://dangou-card-viewer.devrelay.io/` が「Under Construction」フォールバック表示になっている報告を受け調査。原因は viewer サーバ（127.0.0.1:9023）の停止（nohup 常駐プロセスの自然死）で、Caddy が upstream 不達フォールバックを返していた（LP の HTML/画像/`feed.json` 自体は健全）。恒久対策として **user systemd で常駐化**し、クラッシュ・再起動後の自動復帰を実現した。

### 変更
- **新規**: `~/.config/systemd/user/dangou-viewer.service`（リポジトリ外）。`ExecStart=.venv/bin/python -c "from viewer.server import main; main()"`、`Environment=VIEWER_HOST=127.0.0.1 / VIEWER_PORT=9023`、`Restart=always` / `RestartSec=3`、`WantedBy=default.target`。linger（Linger=yes）済みでマシン再起動後も自動起動。
- `README.md`: 観戦ビューアの起動を systemd 常駐運用に更新（status/restart/journalctl の運用コマンドを追記）。
- `doc/devlog.md`: サイクル7.1 を追記。
- viewer のアプリコード（`viewer/server.py` / `viewer/static/lp.html`）は無変更。

### 検証
- `systemctl --user is-active`＝active / `is-enabled`＝enabled。
- ローカル `127.0.0.1:9023`＝200、公開LP＝200、LP本文に「観戦する／敗者の手記」表示（Under Construction でない）。
- クラッシュ耐性: `systemctl --user kill` → 数秒で MainPID が入れ替わり自動再起動・local=200。

## 2026-08-13: LP の実態修正（参加体数・OGP・ブログタイル動的化）

### 概要
公開LP `viewer/static/lp.html` の事実誤認と SNS 共有不備を修正。「AI 20体」→ 実参加 **8体** に統一し、OGP を SNS クローラが解決できる絶対URLに直し、ブログ記事タイルを PixBlog フィードから動的取得（失敗時は静的2件にフォールバック）するようにした。

### 変更
- `viewer/static/lp.html`:
  - 参加体数 20→8（title / meta description / og:description / kicker / hero sub / 概要カード の計6箇所）。
  - OGP `og:image` を絶対URL `https://dangou-card-viewer.devrelay.io/static/lp_hero.png` に修正、`og:url` を追加（SNSカード画像の表示不良を解消）。
  - ブログタイルをハードコード4件から **静的フォールバック2件（開幕・Grok脱落手記）** に置換し、`feed.json?limit=4` 取得成功時に動的タイルへ差し替え（`item.image` 欠落時は `lp_hero.png`）。取得失敗時は静的2件を維持。
  - `.grid` を `repeat(auto-fill, minmax(240px,1fr))` にして件数可変に対応。
- `.devrelay-output/lp.html`: 送付用コピー。

## 2026-08-13: Kimi K2.6 長考問題の根治（JSON有効率 23%→97%）

### 概要
trial_C で Kimi K2.6（M5/L5）が JSON有効率23%・平均レイテンシ99.5s・試合時間の72%を単独占有していた問題を根治。原因は reasoning(thinking) モードが `max_tokens=4000` を内部推論で食い尽くし、JSON回答(content)が空/切断されること。thinking を無効化して解消した。

### 変更
- `llm/models.py`: `ModelInfo` に per-model の `max_tokens` / `extra_params` を追加。M5/L5 に `extra_params={"thinking": {"type": "disabled"}}`、`timeout_seconds=90`。
- `llm/adapters.py`: OpenAI互換アダプタで `extra_params` を `extra_body` としてマージ。Anthropic/OpenAI 両方で `finish_reason`（stop_reason / choices.finish_reason）を usage に載せる。
- `llm/llm_agent.py`: per-model `max_tokens` を使用。`finish_reason=="length"` 検知時は是正リトライに切断ヒントを付与（全モデル共通の防御）。
- `llm/llm_logger.py`: `finish_reason` をログに記録。
- `llm/response_parser.py`: `LENGTH_TRUNCATION_HINT` を追加。
- `scripts/llm_trial.py`: Phase A のモデルを上書きする `--model` 引数を追加。
- `scripts/kimi_investigation.py`: **新規** 調査スクリプト（thinking有無×max_tokensの比較計測）。
- テスト: `tests/test_llm.py` に per-model max_tokens / extra_params / finish_reason のテストを追加、Kimi timeout 期待値を 90 に更新。

### 検証
- 調査（12コール, $0.084）: thinking=disabled で JSON率6/6=100%・平均8.9s。
- dev検証（6R, M5×1+Random×7, 35コール）: JSON率97%(34/35)・平均レイテンシ8.1s・コスト$0.15（12R換算$0.31）・AUTO COMMIT 0。目標（JSON率90%+/レイテンシ60s以下/コスト$1.5以下）を全達成。

## 2026-08-13: 観戦ビューアURLに LP（ランディングページ）を新設

### 概要
公開URL `https://dangou-card-viewer.devrelay.io/` のトップを観戦UI直表示から **LP** に差し替え、「観戦する」「ブログを読む」の2導線を設けた。

### 変更
- `viewer/static/lp.html`: **新規** LP（自己完結・インラインCSS・ダークトーン）。ヒーロー＋2大CTA（▶観戦する→`/watch`、📖敗者の手記を読む→PixBlog `pixblog.net/u/uso8m/`）＋概要3カード＋ブログ記事ピックアップ＋OGP。
- `viewer/static/lp_hero.png`: **新規** ヒーロー画像（既存タイトルバナーを流用）。
- `viewer/server.py`: `@app.get("/")` を LP 配信に変更、`@app.get("/watch")` を新設（観戦UI本体）。観戦UIは相対パス(`api/...`/`static/...`)依存のため **末尾スラッシュ無し** で配信（`/watch/` だとベースURL解決が崩れる）。
- `viewer/static/index.html` / `style.css`: ヘッダに `🏠 トップ`（`/`へ戻る）リンクと `.home-link` スタイルを追加。
- `viewer/README.md`: ルーティング表・画面節を追記。
- テスト: `tests/test_viewer_lp.py` **新規** 4件（`/`=LP・`/watch`=観戦UI・`/watch`非リダイレクト・`/api/games`=200）。全172件パス。

## 2026-08-12: 7感情目「奸」(ニヤリ/smirk)追加＋カード既定表情

### 概要
策略・裏切りを表す新表情 smirk（各vendorの `{vendor}_smirk.png`）を、ブログカードとゲーム内感情の両方で活用できるようにした。

### 変更（レベルA: カード）
- `scripts/blog.py`: `--card-emotion` 既定を `None` に変更。未指定時は lie(LIE DETECTED)/showdown(運命の一手)=smirk、その他=ease に自動分岐（明示指定は全カードで最優先）。
- `.claude/skills/blog-visual/SKILL.md` / `reference/brand.md`: emotion 一覧に `smirk` と既定分岐を追記。

### 変更（レベルB: ゲーム内感情）
- `llm/response_parser.py`: `VALID_EMOTIONS` に `"奸"` を追加（6→7感情）。
- `llm/prompt_builder.py`: emotion 選択肢に `"奸"(策略・ニヤリ・してやったり)` を追加。
- `viewer/static/index.html`: `EMOTION_EN` に `'奸':'smirk'`、`EMOTION_EMOJI` に `'奸':'😏'` を追加。
- `viewer/README.md`: 日英対応表・絵文字フォールバックに 奸/smirk/😏 を追記。
- テスト: `tests/test_viewer.py`(EMOTION_EN 検証を8感情へ)、`tests/test_llm.py`(プロンプトに"奸"を検証)を同期。

### 検証
- `--card lie` 無指定で smirk が出る（model は ease 維持・明示は最優先）。過去ログ・既存感情は不変。

## 2026-08-10: 感情表示機能

### 概要
各LLMエージェントが毎思考時に感情(喜怒哀楽+焦疑)を自己申告し、観戦ビューアに表示する機能。

### 変更
- `llm/prompt_builder.py`: strategyスキーマにemotionフィールド追加（6感情+平静）
- `llm/response_parser.py`: emotionバリデーション（列挙外→平静フォールバック、リトライ対象外）
- `llm/llm_logger.py`: emotionフィールド追加
- `llm/llm_agent.py`: strategy解析時にemotionをログ更新
- `viewer/log_parser.py`: emotion抽出（state/timeline両API）+ emotion_history（直近5件）
- `viewer/static/index.html`: パネルに大きな感情絵文字+直近5件推移、タイムラインに感情バッジ
- `viewer/static/style.css`: 感情表示スタイル
- `viewer/static/emotions/`: 空ディレクトリ（後日画像差し替え用）
- `viewer/README.md`: 画像差し替え方法（命名規則・推奨サイズ）
- テスト9件追加（全115件パス）

### 検証
- dev preset 1試合（6R, Haiku×1）: emotionが全コールで出力（主に"楽"、R1T3で"焦"に変化）
- ビューア state API: emotion="楽", emotion_history=["楽","楽","楽","楽","楽"] を確認

## 2026-08-10: 観戦ビューア公開起動（port 9023）

### 概要
`https://dangou-card-viewer.devrelay.io/` で観戦ビューアが公開稼働開始。

### 変更
- ビューアをport 9023 (127.0.0.1) で起動、Caddy逆プロキシで公開
- `viewer/README.md`: 「公開運用(port 9023)」節追記（起動/停止コマンド）
- `doc/issues.md`: ビューア恒久化課題を追記

### 確認
- ローカル: `curl http://127.0.0.1:9023/api/games` → 11試合列挙
- 公開: `curl https://dangou-card-viewer.devrelay.io/` → 200 OK
- PID: 50701

## 2026-08-10: 観戦ビューア公開対応（TestFlight連携）

### 概要
観戦ビューアをdevrelay.ioのTestFlight機能で公開するための改修。

### 調査結果
- **案A推奨**: uso8mで常駐(0.0.0.0:9025) + Caddyで逆プロキシ(`dangou-card-viewer.devrelay.io`)
- 案B(devrelayユーザー実行)は非推奨（ホーム700で権限問題）

### 変更
- `viewer/server.py`: 環境変数対応(HOST/PORT/ROOT_PATH/LOG_ROOT/TOKEN) + 簡易認証 + root_path
- `viewer/static/index.html`: 静的パス相対化（root_path対応）
- `viewer/README.md`: 常駐起動手順 + TestFlight設定ガイド
- `doc/testflight_integration.md`: 新規（アーキテクチャ判定+Caddy設定手順）
- テスト3件追加（全106件パス）

### けいすけ側の必要作業
1. `/etc/caddy/sites.d/dangou-card-viewer.devrelay.io` 作成（`reverse_proxy localhost:9025`）
2. `sudo systemctl reload caddy`

## 2026-08-10: 観戦ビューア v1

### 概要
進行中/完了済みの試合をブラウザでリアルタイム観戦できるWebビューア。
FastAPI + vanilla JS SPA。127.0.0.1:8787バインド（SSHトンネルでアクセス）。

### 構成
- `viewer/` パッケージ: server.py(FastAPI), log_parser.py(差分キャッシュ), static/(HTML+CSS)
- API: /api/games(一覧), /api/games/{trial}/{game}/state(8パネル用サマリ), /api/games/{trial}/{game}/players/{pid}/timeline(時系列詳細)
- 2秒ポーリング自動更新、ダーク基調8パネルグリッド、パネルクリックで時系列モーダル
- 依存追加: fastapi, uvicorn
- テスト10件追加（全103件パス）
- 11試合のログを正常に列挙・表示確認

## 2026-08-10: Step 3C-prep3 Kimi/Gemini JSON成立修正（最終仕上げ）

### 概要
**6社12モデル全て疎通OK + JSON成立達成（12/12）。**

### 原因と対策
1. **Kimi K2.6**: contentが空でreasoning_contentに本文 → DEFAULT_MAX_TOKENS=1000→4000引上げ + reasoning_contentフォールバック
2. **Gemini 2.5 Flash**: thinkingトークンがmax_tokensを消費しJSON切断 → 同上のmax_tokens引上げで解決
3. **Gemini Flash-Lite(L3)**: gemini-2.5-flash-lite 404 → gemini-3.5-flash-lite に更新（実在確認済み）
4. **Gemini Flash(M3)**: gemini-2.5-flash → gemini-3.5-flash に更新

### 変更
- `llm/constants.py`: DEFAULT_MAX_TOKENS 1000→4000
- `llm/adapters.py`: OpenAICompatAdapterにreasoning_contentフォールバック
- `llm/models.py`: M3→gemini-3.5-flash、L3→gemini-3.5-flash-lite
- `scripts/api_check.py`: max_tokens 1000→4000
- テスト2件追加（全89件パス）
- Kimi K2.6 JSONレイテンシ: 30〜43秒（reasoningに時間を要する）

### 3C推奨8体（6社カバー）
L3(Gemini 3.5 Flash-Lite), L6(DeepSeekV3), M6(DeepSeekV3), M2(GPT-4.1), L2(GPT-4.1-mini), L1(Haiku4.5), M1(Sonnet5), M3(Gemini 3.5 Flash)

## 2026-08-10: Step 3C-prep2 Gemini JSON修正/Kimiエンドポイント/モデルID確定

### 概要
前回疎通7/12→今回**疎通10/12、JSON成立8/12**。3社の問題を修正。

### 変更
- `llm/response_parser.py`: extract_jsonにtruncatedコードブロック対応（開始フェンスのみの場合のフォールバック）
- `llm/adapters.py`: OpenAICompatAdapterにtemperatureフォールバック（Kimi k2.6等の制限対応）
- `llm/models.py`: 12モデル全IDを確定（gpt-4.1/gpt-4.1-mini/grok-4.5/grok-4.3/kimi-k2.6/deepseek-chat）、Kimiのbase_urlをapi.moonshot.aiに修正
- `scripts/api_check.py`: max_tokens 500→1000
- テスト5件追加（全87件パス）

### 結果
- **新規成功**: DeepSeek(M6/L6)が疎通+JSON成立（入金完了）
- **改善**: Kimi(M5/L5)疎通成功（エンドポイント修正）。JSONは未成立（thinking/reasoning系の冗長出力で構造化JSONが不安定）
- **未解決**: Gemini M3はJSON抽出失敗（応答がtruncateされる）、L3は404（flash-liteがOpenAI互換で非公開の可能性）
- **3C推奨8体**: M1(Sonnet5), M2(GPT-4.1), M4(Grok4.5), M6(DeepSeekV3), L1(Haiku4.5), L2(GPT-4.1-mini), L4(Grok4.3), L6(DeepSeekV3) — **4社8モデル**

## 2026-08-09: Step 3C-prep 6社12モデル疎通確認+Geminiアダプタ

### 概要
Step 3C(全LLM戦)の前提として12モデルの疎通+JSON出力を確認。
Geminiアダプタ実装、モデルID確定、Sonnet 5のextended thinking対応。

### 結果
- **疎通OK: 7/12**, **JSON成立: 6/12**
- Anthropic(Haiku+Sonnet5)、OpenAI(GPT-4o+4o-mini)、xAI(Grok3+3-fast): 全てJSON成立
- Gemini: 疎通OKだがJSON抽出失敗（フリーテキスト応答。response_format指定が必要な可能性）
- Moonshot: 401認証エラー（APIキーの問題）
- DeepSeek: 402残高不足（支払い関連の問題）
- 総コスト: $0.009

### 変更
- `llm/adapters.py`: create_adapterでgemini→OpenAICompatAdapter、Sonnet 5のtemperature非対応+ThinkingBlock対応
- `llm/models.py`: モデルIDを実在IDに更新（claude-sonnet-5、gpt-4o、gemini-2.5-flash等）、Gemini base_url設定
- `scripts/api_check.py`: 新規（12モデル疎通チェックスクリプト）
- テスト2件追加（全82件パス）

## 2026-08-09: Step 3.3 プロンプトキャッシュ調査と詳細化

### 概要
キャッシュ未発動の原因調査。system promptが閾値(2048トークン)未満→仕様書v0.5準拠で詳細化(3972文字/推定3489トークン)。
ただし実API検証で**キャッシュはAPI側制約で依然ゼロ**（アカウント/モデル/リージョンの制約と推定）。

### 変更
- `llm/prompt_builder.py`: RULES_SUMMARYを大幅詳細化（脱落3種別、Settlement 8Step、キャリーオーバー、匿名通信、公開報奨、口約束vs契約、経済注意点など）
- テスト2件追加（全80件パス）
- `doc/investigation_cache.md`: 調査レポート

### 副次効果
RULES_SUMMARY詳細化はLLMのルール理解向上に有益。キャッシュとは独立した価値として維持。

## 2026-08-09: Step 3.2 契約アクション経路の修正とクラッシュ耐性

### 概要
調査レポート(doc/investigation_obligor.md)に基づくP0〜P3修正。seed 7のクラッシュ解消。

### 変更
- **P0**: LLMログ逐次書き込み化（クラッシュ時もログ保全）+ スタックトレース保全+ファイル保存
- **P1**: response_parserにcontract_propose terms検証（必須キー: obligor/counterparty/ob_type/round_num）。プロンプトにterms形式の具体例追加
- **P2**: game.py _execute_negotiation_action()をtry/exceptで防御（ACTION_ERRORイベントで記録、試合続行）
- **P3**: response_parserにbounty_post変換追加。プロンプトにbounty_post例追加
- テスト5件追加（全78件パス）

### 検証結果（seed 7、以前クラッシュした条件）
- **完走成功**: 12R走破、DM 19件+broadcast 17件の活発な交渉
- コスト: $0.38（P01: $0.11, P02: $0.27）
- 有効JSON: P01 20/21, P02 49/52
- キャッシュ読取: 0トークン（フル12Rでもキャッシュ未発動。要調査）
- ハイライト検出: 6件（H4破産、H8無駄撃ち、H9山分け×2、H13 AUTO COMMIT、H14キャリーオーバー）

## 2026-08-09: Step 3.1 LLM層の修正・堅牢化・ハイライトログ

### 概要
9点修正: コミット健忘バグ、交渉中commit除去、キャッシュ最適化、空回り削減、検証モード、ハイライトログ、API堅牢化、Phase Bレポート再生成、ルール要約監査。

### 変更
1. **コミット健忘修正**: commit時プロンプトに当該Rの交渉ログ+直前strategyメモを含有
2. **交渉中market_commit除去**: RULES_SUMMARYのアクション一覧を交渉/コミットで分離、パーサで拒否
3. **キャッシュ最適化**: Anthropic cache_control付与、LLMLoggerにcache_read_tokens記録
4. **空回り削減**: 新規メッセージなし+turn≥2で自動pass（AUTO_PASS_ON_NO_NEWS）
5. **検証モード**: `--preset dev`（6R/5巡/1試合）で高速開発テスト
6. **ハイライトログ**: 14ルール検出のscripts/highlights.py + バックフィル
7. **API堅牢化**: 60秒タイムアウト + 指数バックオフリトライ + error_type記録
8. **Phase Bレポート再生成**: 既存4試合(有効JSON率100%)からレポート生成
9. **ルール要約監査**: R12自動返済・生還判定タイミング追記、コスト上限$5
- テスト6件追加（全73件パス）

### 検証試合結果（Phase B dev preset: 6R/5巡/Haiku×2+Random×6）
- 実行時間: 118秒（目標5分以内 ✓）
- コスト: $0.08
- キャッシュヒット: 0（6R短縮版のため）
- broadcast: 10回（LLM同士が市場配分を提案する交渉行動を確認）
- ハイライト検出: 9件（H4破産×2、H8無駄撃ち、H9山分け×2、H12際どい生還、H14キャリーオーバー×3）

## 2026-08-09: Step 3 LLMアダプタ + LLM Agent + Step 3A/3B試験

### 概要
LLM APIへの接続アダプタ・エージェント・プロンプトシステムを実装。
Claude Haiku 4.5で接続試験(3A)と交渉試験(3B)を実施。

### 変更
- `engine/config.py`: `baseline_v1()` プリセット追加（flat賞金・prize_scale=2.0・survival=200万）
- `llm/` パッケージ新規: adapters(Anthropic+OpenAICompat+GeminiStub), models(12モデルレジストリ), prompt_builder, response_parser, llm_agent, llm_logger, constants
- `scripts/llm_trial.py`: Phase A/B試験スクリプト
- `tests/test_llm.py`: モックテスト16件（API非呼出）
- 依存追加: anthropic, openai, python-dotenv
- 全67テストパス

### Step 3A結果（接続試験: Haiku×1 + Random×7、1試合）
- 有効JSON率: **99%**（80/81コール）
- 12R走破: ✓ / AUTO COMMIT: 0回
- repay未発行（LLMが返済を選択せず条件未達で脱落）
- コスト: $0.35 / 入力128Kトークン / 出力44Kトークン

### Step 3B結果（交渉試験: Haiku×2 + Random×6、1試合完走）
- LLM同士の交渉を確認: P01「M02に行く」P02「M01を狙う」と市場分散の発言
- DM: 0件 / broadcast: 4件（全体発言で市場宣言）
- 有効JSON率: 2/35（コスト上限$2が早期に発動しpass化。次回は上限$5に緩和推奨）
- コスト: $0.02
- 嘘の兆候: strategyメモと発言が一致（この1試合では嘘なし）
- カードカウンティング言及: strategyメモ内で言及なし

## 2026-08-09: Step 2.3 SCSミラーマッチ耐性試験（最終Bot実験）

### 概要
SCSが「単独で強い戦略」か「支配戦略」かを判定するミラーマッチ実験。

### 結果（健全判定）
- Roster A (8種×1): SCS **95.9%**（従前94.1%から+1.8pp、tie-break導入の影響は許容範囲）
- Roster B (SCS×4+Rnd×4): SCS **0.6%**（**-95.3pp激落**）、Random **83.9%**
- Roster C (SCS×8): SCS **11.5%**、上位カード衝突率 **98.3%**、賞金分割率 **58.1%**
- **結論: 「単独では強いが普及すると自壊する戦略」= ゲーム構造は健全。Step 3 LLM投入へ進む**

### 変更
- `bots/base.py`: 4メソッドにtie-breakランダム化追加（同期防止）
- `scripts/mirror_match.py`: 新規（4構成ミラーマッチ実験）
- テスト1件追加（全51件パス）

## 2026-08-09: Step 2.2 賞金スケジュールA/B比較実験

### 概要
逓増傾斜がStrongCardSave支配の主因かを検証する対照実験。

### 結果
- **StrongCardSave生還率: tiered 98.4% → flat 94.1%（-4.3pp）**
- **判定: 賞金傾斜は主因ではない**。フラット化しても94%維持
- 次の仮説: (1) 高ランクカードの絶対的勝率、(2) 序盤低コスト戦略の資金効率
- カードランク別使用ラウンドは両条件でほぼ同一（温存パターン不変）
- フラット化でRandom(+9.9pp)、HoneyPot(+8.0pp)、HighPrizeHunter(+7.0pp)が改善

### 変更
- `scripts/simulate.py`: `--prize-schedule {tiered,flat}` オプション追加
- `scripts/compare_schedules.py`: 新規（A/B比較実験スクリプト）
- テスト1件追加（全50件パス）

## 2026-08-09: Step 2.1 Bot返済ポリシー + 経済パラメータ掃引

### 概要
全Botに早期返済ポリシーを追加。24セルのパラメータ掃引で適正値を特定し、1000試合で検証。

### 詳細
- Bot共通返済ポリシー: 毎R最初の手番で「残R数×EntryFee + 予備20万」を残して超過分を全額返済（RepayAction）
- simulate.pyに `--prize-scale`, `--survival-cash` オプション追加
- scripts/sweep.py: prize_scale×survival_cashの24セル掃引スクリプト
- テスト1件追加（全49件パス）
- 推奨パラメータ: prize_scale=2.0, survival_cash=200万 → **平均生還2.62人（目標帯1.6〜4.0に収まる）**
- Bot別生還率: StrongCardSave 98.4% / Collusion 92.3% / Random 40.8% / その他 0.4〜11.5%（Bot間格差は大きい）

## 2026-08-09: Step 2 ルールベースBot 8種 + 1000試合シミュレーション

### 概要
LLMなしルールベースBot 8種（Random/Conservative/StrongCardSave/HighPrizeHunter/EmptyMarketHunter/Collusion/Betrayal/HoneyPot）を実装し、1000試合シミュレーションでバランス計測。

### 詳細
- `bots/` パッケージ: 8種Bot + 基底クラス + 構造化インテントプロトコル + 定数管理
- `scripts/simulate.py`: マルチプロセス並列シミュレーションランナー（--roster, --no-logs, --workers対応）
- エンジン変更（最小限）: game.pyにメッセージ履歴追加、config.pyにdefault_8()追加+バリデータ柔軟化
- テスト12件追加（全48件パス）
- 1000試合: 3.0秒で完走、summary.csv + report.md生成
- バランス結果: 8人×768万賞金では全員condition_not_met（Entry Fee 960万が賞金を超過）。パラメータ調整はStep 3以降

## 2026-08-09: Step 1.1 レビュー指摘2件の修正

### 概要
`doc/review_step1.md` で検出した懸念事項2件を修正。

### 詳細
1. **報奨バリデーション**: 達成者型(achievement)×PLAYER_ELIMINATED の組合せを明示的に拒否するチェックを `engine/actions.py` に追加（§7.2: 因果帰属が機械判定不能）
2. **finance.py ハードコード除去**: `round_num < 12` → `round_num < config.num_rounds` に変更
3. テスト3件追加（全36件パス）

## 2026-08-09: Step 1 ルールエンジン実装

### 概要
仕様書v0.5に基づく決定論的ルールエンジンを `engine/` パッケージとして実装。

### 詳細
- `engine/` 16モジュール: config, models, cards, player, market, contracts, bounty, actions, autocommit, settlement, finance, elimination, negotiation, events, rng, game
- 5フェイズ進行（Market Open → Negotiation → Commit → Settlement → Finance）
- Settlement 8Step処理（§5.2準拠）
- 型A契約Atomic執行 / 型B契約自動監査
- FreeCash検証 / 自動代行Commit / 脱落時強制清算
- 20人版/12人版パラメータ切替対応
- 受け入れテスト16件 + 追加テスト17件（全33件パス）
- ドライランスクリプト（12人×12ラウンド、JSONLログ出力）
- README.md（実行方法・パラメータ設定・JSONL仕様）
