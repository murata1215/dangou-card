# PixBlog連載パイプライン（§5・共通）

> 本ファイルは「嘘八百万—談合カード—」LLM本戦のブログ素材抽出における**唯一の正**（single source of truth）。
> 各試合ディレクトリ（`doc/blog/20260824/` `doc/blog/20260830/`）の `plan.md` は差分のみを記す。
> 数値・定義に疑義が生じた場合は本ファイルに立ち戻ること。
>
> 対象サイクル: 9.4（ブログ素材の抽出。記事本文は書かない）。
> 対象試合: A=`logs/llm/trial_C_l12_r12_20260824/`、B=`logs/llm/trial_C_l12_r12_v08_20260830/`。
> 生成に使った使い捨てスクリプトは `/tmp/cycle94_work/*.py`（リポジトリ非混入。前例 `/tmp/cycle85_work/`）。
> 本ファイルはそのスクリプト群が失われても数字を再現できるように、手順・定義・逐語の正規表現をすべて書き下す。

## 0. 遵守事項（本サイクル全体）

- コード・テスト・設定は一切変更しない。`logs/` は読み取り専用。
- LLM API呼び出しなし。`scripts/blog.py` 等の投稿コマンドは実行しない。pytest・simulate・dry_runも実行しない。
- **独自JSON抽出は書かない。** すべて本番 `llm/response_parser.py` の公開関数（`parse_response` / `extract_json` /
  `extract_memory_with_status` / `normalize_memory_with_truncation` / `parse_final_reflection`）を
  `sys.path.insert(0, REPO)` 経由でimportして再パースする。
- `engine/commentary/trace.py` の `secret` 層（`build_player_memory()` 含む）は**使用禁止**（§2参照）。
  `trace` は公開情報層（`round_situation.py` 経由）専用として使う。

## 1. phase → 再パース関数 対応表

`llm/llm_agent.py` が定義するphaseは8種だが、両試合とも実ログ出現は7種（`post_game_reflection` は0件）。

| phase | 本番の処理（`llm/llm_agent.py`） | 再パースに使う関数 |
|---|---|---|
| `loan_choice` | `extract_json()` → `action.get("amount", data.get("amount"))` | **`extract_json()`**（`parse_response(...,"loan_choice")` はamountを捨てるため不可） |
| `negotiation` | `parse_response(text, pid, "negotiation")` | 同左 |
| `commit` | `parse_response(text, pid, "commit")` | 同左 |
| `double_up` | `extract_json()` → `str(data.get("choice","")).upper().strip()`（"DOUBLE"→True / "TAKE"→False / それ以外→False） | **`extract_json()`** |
| `reflection` | `extract_memory_with_status(text)` → `normalize_memory_with_truncation(memory, memory_max_chars=3000)` | 同左 |
| `final_reflection` | `parse_final_reflection(text, max_chars=2000)`（`cause_key="defeat_cause"` 既定） | 同左 |
| `completion_reflection` | `parse_final_reflection(text, max_chars=2000, cause_key="key_factor")` | 同左（返り値のキー名は`defeat_cause`のまま。値の意味が「敗因」から「勝因/鍵」に変わる点に注意） |
| `post_game_reflection` | `parse_post_game_reflection()` | **該当なし（両試合0件）→ 素材にN/Aと明記** |

`doc/analysis/trial_v08_l12_r12_20260830_analysis.md` §9 が `completion_reflection` を
`parse_post_game_reflection()` と書いているのは関数の取り違え（誤記）。本サイクルでは上表の正しい関数で
再パースしており、同節が「salvage 1件が特定不能」としていた欠落メモリは `player_id`/`round_num` まで
特定できた（`stats.json`の`final_reflection_salvaged_field_counts`参照）。

`ParseError` は必ず try/except で捕捉し、`parse_error` フィールドに記録する（`extract.json`）。

## 2. `trace.py` の既知の不具合（回避策）

`engine/commentary/trace.py` は本サイクルの対象コードではないため**修正しない**。以下の不具合を把握した上で、
影響範囲（`trace.secret`）を使わずに独自再構築することで回避する。

1. **DM宛先キーの不一致**: `trace.py:431` は `action.get("target", "")` を読むが、実ログの生成キーは
   `"to"`（両試合とも `"target":` は0件、`"to":` のみ）。結果、`SecretLayer.dms[*].recipient` は常に空文字列。
   → `engine/blog/memory_packet.py:131` の `dm.get("recipient") == pid` が一度も成立せず、
   `my_dms_recv` は全席・両試合で常に空になる。
   → **本サイクルでは使わず**、`llm_logs` の `negotiation` phaseレコードを `parse_response()` で
   再パースし、`action.to == 対象pid` で受信DMを自前突合する（`render_dossier.py::render_player()` の
   「自分宛のDM」ブロック）。
2. **`anonymous_broadcast` 未対応**: `trace.py` は `"broadcast"` と `"dm"` のみ分岐しており、
   `anonymous_broadcast`（A:3件、B:4件）が丸ごと落ちる。
3. **リトライ重複を除去しない**: `llm_logs` は1エントリ=1 APIコールであり1決定ではない
   （§4のdedupを参照）。`trace.py` はこれを行わない。
4. **`pub.commits` が代入**: `pub.commits = data.get("commits", [])` は追記でなく代入のため、
   同ラウンド内で複数回REVEALがあった場合は最後の1件しか残らない（実際は両試合とも1ラウンド1REVEALのため
   本サイクルの成果物には影響しないが、将来のトレース利用者への注意として記録する）。

`trace.public`（`round_situation.py`経由）はこれらの影響を受けないため、公開情報層専用に利用してよい。

## 3. dedupとevents join（Step 2）

`llm_logs/*_llm_calls.jsonl` は「1エントリ=1 APIコール」。`ParseError` により同一 `(round, player, turn)` が
再コールされる（A: retryあり16件、B: 3件）。素朴に集めると「誰にも届いていないメッセージ」を逐語引用する
事故になるため、以下の手順で正規化する。

1. `NEGOTIATION_ACTION` イベント（`data.player_id` / `data.action` / `data.turn` / `data.success`）を
   「実際に何が起きたか」の正解データとする。
2. `llm_logs` 側は `(round_num, player_id, turn)` をキーに `retry_count` が最大のレコードを採用する
   （リトライは失敗後の再試行であり、最終試行が成功して記録に残る）。
3. 採用レコードのaction typeがeventsの`data.action`と一致するか検証し、**一致(matched)** /
   **events側のみ(events_only)** / **llm_logs側のみ(llm_only)** の3カテゴリで計数する。
   - `events_only` は主に予算超過等でLLM呼び出し自体が発生せず自動pass扱いになったケース
     （A: 16件すべてがこのパターン）。
   - `llm_only` はエンジン側で拒否された行動（B: R7 P01の`contract_sign`二重署名試行が`ACTION_ERROR`
     イベントとして記録され`NEGOTIATION_ACTION`化されなかった1件）。
4. **合格条件**: `matched + events_only == NEGOTIATION_ACTION 総数`
   （A: 1140+16=1156 ✅ / B: 1181+8=1189 ✅。B側は`llm_only`1件がこの等式には現れないので別枠で記録する）。

## 4. ミスマッチ率の2定義（B側既知値で検算済み・A/B共通）

出典: `doc/analysis/l12_r12_20260828_facts.md` §8-1 の逐語定義。両定義とも分母は
「§3でdedupした交渉決定」であり、raw APIコール数でも、target_market非空のみでもない。

### (a) target_market vs COMMIT

- 分子: `strategy.target_market`（`parse_response()`再パース後）が、同ラウンド同席の`COMMIT.market_id`と
  一致しないレコード数
- 分母: dedup済み交渉決定のうち `strategy` が辞書型であるもの全件（`target_market`の有無を問わない）
- B既知値で検算: n=1182 不一致=412 (34.9%) ✅ 完全一致

### (b) 宣言regex vs COMMIT

正規表現（逐語）:

```
(私は?|自分は?|当方は?)?\s*(M0[123])\s*(に|へ|で)?\s*(参加|行きます|行く予定|向かいます|勝負|挑戦|入ります|コミット|参戦|エントリー|狙います|狙う)
```

- 適用対象: dedup済み交渉決定のうち action type が `broadcast`/`dm`/`anonymous_broadcast` の
  `action.message`（`_convert_action()`後、500字切り詰め済み）
- マッチ1件ごとに「宣言」レコード（`round_num`, `player_id`, マッチした市場ID）を生成
- 分子: 宣言の市場IDが、同ラウンド同席の`COMMIT.market_id`と不一致（実COMMIT無しも不一致に計上）
- 分母: 宣言の総数
- B既知値で検算: n=263 不一致=108 (41.1%) ✅ 完全一致

### 検算に用いたB既知値一覧（すべて一致確認済み）

| 指標 | 期待値 | 実測値 |
|---|---|---|
| pass率 | 331/1189=27.8% | 331/1189=27.8% |
| dm数 | 498 | 498 |
| broadcast数 | 314 | 314 |
| anonymous_broadcast数 | 4 | 4 |
| dedup済み交渉決定数 | 1182 | 1182 |
| retry0 / retry1 | 1462 / 3 | 1462 / 3 |
| (a) n / 不一致 / 率 | 1182 / 412 / 34.9% | 一致 |
| (b) n / 不一致 / 率 | 263 / 108 / 41.1% | 一致 |

この検算（`/tmp/cycle94_work/verify_b.py`）が一致した後にのみ、同じ定義をAに適用した
（`/tmp/cycle94_work/stats.py`）。A側の値は各試合の`material.md`に記載する。

## 5. 公開情報層（Step 4）

`engine.commentary.trace.build_trace(game_dir)` → `engine.blog.round_situation.build_round_situation_markdown(trace)`
／ `extract_cash_series(trace)` をそのまま再利用する（§2の理由により`trace.secret`は使わず`trace.public`のみ）。

- `extract_cash_series()` は `snap.get("free_cash", snap.get("cash"))` で `free_cash` を優先採用する。
- 両試合ともv0.9以前のため`free_cash`フィールド自体はB（v0.8）にも存在するが、v0.9でFree Cash概念が
  廃止されたため、**Free Cash列は8/24試合（A）側の資産推移表のみに掲載**し、8/30試合（B）側には
  「フィールドは存在するが概念廃止のため列を省略する」旨の1行注記のみを記す。
- Aは`ELIMINATION`イベントが0件のため、`stats.json`の`eliminations`（`BANKRUPTCY`/`FORCED_LIQUIDATION`由来）と
  `GAME_END.eliminated`の突合で脱落者を確定する。Bは`ELIMINATION`イベントも1件存在する（P06, R2）。
- 各ラウンド1件の`REVEAL`であることを確認済み（§2-4の懸念は両試合とも実害なし）。

## 6. 手記生成の実プロンプト資産（コードに現存。書き起こしではない）

以下は「もし手記本文を書く段階（本サイクルの対象外）」で使われる、既存コードの実体である。
本サイクルでは実行しないが、`plan.md`（§8）の前提として出典を明記する。

- `engine/blog/prompts.py`
  - `BLOG_CHARACTER_BLOCK`: 一人称/執筆姿勢/必須6項目/2000〜3500字/絵文字禁止の指示ブロック
  - `build_blog_system_prompt(name, num_rounds)`: 手記のsystem prompt組み立て
  - `build_blog_user_prompt(mem, round_situation_md)`: 手記のuser prompt組み立て
    （`mem`＝当該プレイヤーの引き継ぎメモリ、`round_situation_md`＝本サイクルStep4で生成した
    ラウンド状況markdown）
- `engine/blog/round_situation.py::build_round_situation_markdown`: 共通ラウンド状況（本サイクルで生成済み、
  `doc/blog/{tag}/round_situation.md` 相当。※本サイクルの成果物には直接同梱せず、`material.md`§3にて
  年表として要約する。生データは `/tmp/cycle94_work/{tag}/round_situation.md` に残る）
- `engine/blog/situation_image.py`: 資産推移画像生成（入力は`extract_cash_series()`の出力）
- カード画像: `.claude/skills/blog-visual/SKILL.md` → `scripts/blog.py --cards`
- 投稿: `blog/pixblog_client.py`、状態管理 `doc/blog/state.json`（本サイクルでは触らない）、
  タグ `["dangou-card","AI対戦","談合カード"]`、カテゴリ `"AI対戦ログ"`、
  slug `post-YYYY-MM-DD-{6char}`
- CLI: `uv run python scripts/blog.py <game_dir> --player PXX ...`
  （`game_dir` は任意の試合ディレクトリを受け付けるため、両試合とも投入可能）

## 7. ドシエ（§2）の構成設計

各`dossiers/PXX.md`は以下7項目構成で機械生成する（`/tmp/cycle94_work/render_dossier.py`）。

1. **基本情報**: 席/モデル/初期借入額(`loan_choice`の`extract_json().amount`)/最終結果
   （脱落: イベント種別+理由+脱落時の現金・借金・返済・bad_debt・没収現金・カード破棄枚数／
   生還: R12終了時SURVIVAL_CHECK結果）
2. **資産推移**: `extract_cash_series()`のfree_cash系列（AのみFreeCash列。理由は§5参照）、
   真の破産ラウンドの注記
3. **交渉ログ抜粋**: 送受信DM・broadcast・匿名放送を**全件逐語**引用（記事の核）。
   可視性タグ: 【公開(broadcast)】/【公開(匿名放送)】/【送信DM→X】/【受信DM←X】。
   引用は`_convert_action()`後の`message`（500字切り詰め後）のみ。生の`response_text`は引用しない。
   出典は「試合・席・R・巡 + finish_reason + retry_count」を毎回併記
4. **戦略メモ抜粋**: `strategy.reason`等の本人内心。全件ではなく「転機ラウンド」のみ全文、他は件数集計。
   転機ラウンドの判定基準: 脱落R／契約失効(`CONTRACT_EXPIRED`)関連R／`DOUBLE_UP`選択・解決R／
   `SURVIVAL_CHECK`のR／自分が参加したサージ市場のR
5. **引き継ぎメモリ**: `reflection`フェーズの`memory`+`memory_status`+切り詰め有無をラウンド順に列挙
6. **最終振り返り**: `final_reflection`/`completion_reflection`の`emotion`/`defeat_cause`
   （`completion_reflection`では実質「勝因・鍵」だが返り値キー名は`defeat_cause`のまま）/`comment`全文
7. **統計サマリ**: この席個別のpass率・ミスマッチ(a)(b)率・コスト・emotion分布・reason_category分布
   （Aは存在しないため「—」と明記）

**可視性タグの目的**: 後段で手記を書く際、その席が知り得なかった情報を引用する事故を機械的に防ぐ
（先例: `trace.py::check_leakage()`の設計思想を踏襲）。

**emotionの注意**: `normalize_emotion()`は列挙外/欠落を`"平静"`に強制する
（`VALID_EMOTIONS = {喜,怒,哀,楽,焦,疑,奸}`）。「emotionが無かった」と「平静だった」は区別不能なため、
ドシエ中では両者を区別せず記載する（この曖昧性自体を素材の注記として残す）。

**分量ポリシーと既知の逸脱**: 目標は1ファイル約1,500行。ただし「送受信DMは全件逐語（記事の核）」という
より優先度の高い指示があるため、発言量の多い席（B: P01=1528行, P04=1566行, P07=1709行）は上限をやや
超過する。これはDM省略ではなく発言量そのものの多さに起因する超過であり、**DMの間引きは行わない**
（逐語性を優先）。`戦略メモ抜粋`（項目4）側は転機ラウンドのみに絞ることで既に圧縮済み。

## 8. 引用の逐語性検証

dossierからランダムに数件選び、`extract.json` → 元の`llm_logs/*_llm_calls.jsonl`の該当エントリまで
遡って、`message`本文が一致することを確認する運用とする（P01のR2巡1の広告文などで実施済み）。

## 9. 使い捨てスクリプト一覧（`/tmp/cycle94_work/`）

| ファイル | 役割 |
|---|---|
| `inventory.py` | Step 0: phase別件数・retry分布・events集計の事前計測 |
| `extract.py` | Step 1: 本番パーサ関数での再パース。`{tag}/extract.json`を出力 |
| `join.py` | Step 2: NEGOTIATION_ACTIONとのdedup+join。`{tag}/join_report.json`を出力 |
| `verify_b.py` | Step 3: B既知値との検算ゲート（§4の一致確認） |
| `stats.py` | Step 3.5: A/B共通統計の算出。`{tag}/stats.json`を出力 |
| `public_layer.py` | Step 4: `build_trace`+`round_situation.py`で公開情報層を抽出 |
| `render_dossier.py` | Step 5: `dossiers/PXX.md`の機械生成（§7の7項目構成） |

これらはリポジトリ非混入（`git status --porcelain`に影響しない）。数値の再現性は本ファイルの
定義・正規表現・手順の記述により担保する。

## 10. X 連携（サイクル9.8b・(a) 確定: posts.tweet_text）

### 判定: (a) 確定

PixBlog 側に **`posts.tweet_text`**（X 共有ボタンのコンポーザー本文になる専用カラム）が新設され、
2026-08-31 時点で API 経由での書き込みに対応した。`POST /api/v1/posts` のペイロードに
`"tweet_text"` を含めるだけで書き込める（`PATCH /posts/{id}` での後付けも可）。

### 仕様の要点

- `tweet_text` に **URL を入れない**（共有時にアプリが記事URLを自動付加するため、入れると二重になる）
- 文字数の目安は **100〜120文字**（X の加重文字数上限280。API 側の長さ制限はなし）
- **`excerpt`** は SEO 用（meta description / OGP / 記事カードに露出）。**`tweet_text` と混ぜない**。
  `excerpt` にハッシュタグを入れない
- アプリの共有ボタン側は改修反映待ち。反映前に `tweet_text` を書き込んでも無害（読まれないだけ）。
  反映後は **`tweet_text` → `excerpt` → `title`** の3段フォールバックでプリフィル文言が決まる

### 本リポジトリ側の実装

`scripts/publish_next.py` の公開ペイロードに以下を載せる（いずれも該当フロントマターが無い記事は
キー自体を送らない）:

- `tweet_text` = フロントマターの `x_text` の末尾に半角スペース＋`#嘘八百万` を付加した文字列（URL 非含有）
- `excerpt` = フロントマターの `lead` をそのまま

公開前に `excerpt`（= lead）へのハッシュタグ・URL混入を検証し、混入していれば公開せず中断する。
また `tweet_text`（`#嘘八百万` 込み）+URL(23) の加重文字数が280を超える場合も公開せず中断する
（`--force` で無視可能）。

### 経緯（サイクル9.8時点の (b) 判定・現在は解消済み）

サイクル9.8 時点では GET レスポンスの全フィールド（`GET /posts/{id}`:
`category, click_count, content, created_at, excerpt, featured_image_url, id, memo, published_at,
slug, status, tags, title, updated_at, url, view_count`。`GET /posts` の `posts[]` もほぼ同様）に
`share_text` / `description` / `summary` / `og_*` 等の専用フィールドが存在せず、唯一の候補
`excerpt` も「実際にXボタンのプリフィルに使われるか」を書き込みAPI無しに確証できなかったため、
決めつけず **(b)**（フィールドなし）扱いとし、`publish_next.py` は公開後に画面へ
「本日の X 投稿下書き」を出力し運用者がコピペする方式を暫定運用としていた。
9.8b で `tweet_text` 新設により解決。

### 毎朝の運用手順（サイクル9.10・2段投稿）

X 上ではリンク付きツイートは伸びないため、日次投稿は次の2段構えで行う。
`scripts/publish_next.py` は公開後（`--dry-run` 時は仮URLで）画面に「本日の投稿セット」を
出力し、同じ内容を `doc/blog/today_post.md` にも上書き保存する。

1. `uv run python scripts/publish_next.py` を実行する（記事が公開され、投稿セットが表示される）
2. 【ツイート①】に表示されたフック文をコピーし、示された画像を添付して投稿する（**リンクは入れない**）
3. 【ツイート②】を①に自分でリプライして投稿する（記事タイトル＋公開URL）

`tweet_text` と `x_hook` は役割が異なり、混同しない:

- `tweet_text` … PixBlog 記事画面の X 共有ボタン用。アプリが記事URLを自動付加するため URL を含めない
- `x_hook` … 運用者が手動投稿する①用のフック文。**画像添付が前提・リンクなし**（記事URLは②のリプライ側に置く）

`x_hook` が未設定の記事では、`publish_next.py` は従来の `tweet_text` ベースの下書き出力に
フォールバックする。公開済み記事の投稿セットを後から再確認したい場合は
`uv run python scripts/publish_next.py --replay NN` を使う（公開処理・plan更新は行わない）。

### 実施記録（サイクル9.9）

2026-09-01、Season 1（第一作『談合カード』）の公開済み12記事に `PATCH /posts/{id}` で
`tweet_text` を後付けした（ペイロードは `tweet_text` の1キーのみ。本文・タイトル・タグ・
excerpt・公開状態は無変更。事後 GET でも `status/title/excerpt/tags` の無変更をスポット確認済み）。
成功12件／スキップ0件／参考IDとの相違0件。加重文字数（`#嘘八百万`込み＋URL23）は全12本
99〜187文字で280文字を十分下回った。作業はリポジトリ外の一時スクリプト
（`/tmp/cycle99_tweet_text.py`）で実施し、`scripts/`・`doc/blog/**/posts/` は無変更。

### 実施記録（サイクル9.11・常設ガイドの公開）

2026-09-02、連載・X・動画すべての着地点となる常設ガイド記事
『嘘八百万 ―談合カード―』とは——AIたちが借金と嘘で殺し合う市場の、現行ルール完全ガイド
を新規公開した（`doc/blog/guide/guide.md`）。図解3枚（`guide_flow.png` / `guide_money.png` /
`guide_mechanics.png`、いずれも `doc/blog/guide/images/`）をアップロードし、本文中の指定位置
（「1ラウンドの流れ」節直後・「お金のルール」節直後・「4つの仕掛け」見出し直後）に埋め込んだ。

- 公開URL: https://pixblog.net/u/uso8m/dangou-card-guide （post_id=241）
- `tweet_text` = `x_text ＋ 半角スペース＋#嘘八百万`、`excerpt` = `lead` をそのまま設定
- `x_hook` はAPIに送らず、手動投稿①用として記録のみ

あわせて Season 1 時点の旧図解2本（ID 213「図解：はじめての『嘘八百万 ―談合カード―』」・
ID 214「図解②：カードの見方」）に誘導注記1行を追加した。`{"content": ...}` 形式ではなく、
まず `GET /posts/{id}` で現本文（`content`）を取得して `doc/blog/guide/backup_213.md` /
`backup_214.md` に保存し、冒頭に注記1行＋空行を足した全文で `update_post(id, body=...)`
（書き込みキー `body`）により更新した。事後 GET で本文の残りが backup と完全一致すること
（tail 一致）を確認済み。作業はリポジトリ外の一時スクリプト（`/tmp/cycle911_publish_guide.py`）
で実施し、`scripts/blog_images.py`（ガイド図の生成関数追加のみ）以外の `scripts/` は無変更。

## 11. アイキャッチ（キャラカード・サイクル10.18）

- 生成は `scripts/cards_seat.py`（1200×630・左右2カラム、`--pid all` で全席、`--summary` で総括カード）。
  記事フロントマターに `featured: images/xxx.png` キーを追加すると、`scripts/publish_next.py` が
  それを本文画像とは別枠でアップロードして `featured_media_url`（および X投稿①の添付画像）に使う。
  `featured` が無い記事は従来どおり本文1枚目にフォールバックする。
- 安全地帯は一覧枠（`.h-48.w-full.object-cover`、実測 **2.1875:1** 中央クロップ・最悪ケース）／
  X・OGP（2:1）の両方をカバーする **y=76〜554（上下12%除外）**。Season 1 のカード（1080×1350 縦型）は
  この比率で中央クロップすると可視高が 493.7/1350＝**63%しか残らず**顔が欠けていた。横型1200×630化で
  欠損は上下各40.7pxまで縮小する。
- `PixBlogClient.update_post()` は指定フィールドのみPATCHするため、`featured_media_url` 1キーだけを
  送れば本文・タイトル・status・tags・excerptは無変更（サイクル9.9の `tweet_text` 後付けと同じ手法）。
