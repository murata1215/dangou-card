# FINAL_REFLECTION 実API疎通試験（L1〜L6 × 1call）

- ログ: `logs/smoke/final_reflection_fr_smoke_20260822/`
  （`calls.jsonl`, `manifest.json`, `real_llm_calls.jsonl`, `report.md`, `_preflight/`）
- run_id: `fr_smoke_20260822`
- ロスター解決: `get_model("L1")`〜`get_model("L6")`（`llm/models.py` の `MODEL_REGISTRY` から実行時解決。ハードコードなし）
- 実効 output 上限: `max_tokens_override=1000`（`GameConfig.final_reflection_max_tokens`、通常の2000より低い。L6のレジストリ既定2000からの override を実測で確認）
- 予算キャップ: `--max-cost-per-call 0.02` / `--max-cost 0.08` / `--max-calls 6`（`HARD_MAX_CALLS=6`）
- コスト: 計画worst_case合計 **$0.03368** → 実測合計 **$0.030300**（上限$0.05以内）
- API-freeゲート: G1/G2/G3/G4/G5/G6すべて実APIコール前に内部でPASS（後述）
- 手法: `scripts/final_reflection_smoke.py`（Bot 12体の実Gameウォームアップ→
  `engine.elimination.forced_liquidation()` による合成脱落→対象seatのみLLMAgent差替→
  `Game._phase_final_reflection()` を実経路で1回だけ実行）。ゲームループ・交渉・commit・
  reflectionフェイズでのAPI課金はゼロ（全てBot）。Handover Memoryサンプルは
  `logs/llm/trial_C_l12_r12_20260822/llm_logs/*_llm_calls.jsonl` から読み取り専用で収集
  （C6のみ意図的に空Memoryで `_render_memory_block(None)` 分岐を確認）。

## API-freeゲート結果（実API呼び出し前に自動実行、1つでも失敗したら実APIへ進まない設計）

| Gate | 内容 | 結果 |
|---|---|---|
| G1 | `pytest tests/test_final_reflection.py -q` | **41 passed** |
| G2 | `pytest tests/ -q`（全体回帰） | **849 passed**（新規追加なし、既存件数のまま） |
| G3 | `--dry-run`: 6シーン構築・6脱落コンテキスト導出・6プロンプトレンダリング・`RefuseToCallAdapter`で0 call証明 | **PASS**（6/6 `calls_attempted=0`） |
| G4 | `--parser-selftest`: `parse_final_reflection()` の4段フォールバック | **PASS**（7/7チェック） |
| G5/G6 | `--mock`: `FakeOkAdapter`で6シーン全通し。ルーティング・`sent_max_tokens=1000`・非破壊・二重発火なしを検証 | **PASS**（6/6） |

## モデル別・ケース別結果

| # | model_key | model_id (provider) | round | reason | event shape | ok | parse_status | in tok | out tok | cost | latency | emotion | defeat_cause | fallback |
|---|---|---|---:|---|---|---|---|---:|---:|---:|---:|---|---|---|
| C1 | L1 | claude-haiku-4-5-20251001 (Anthropic) | 4 | bankruptcy | BANKRUPTCY (commit) | True | `ok_recovered` | 6146 | 1000 | $0.011146 | 12498ms | None | None | **True** |
| C2 | L2 | gpt-4.1-mini (OpenAI) | 6 | contract_violation | AUTO_COMMIT_FAILURE (commit) | True | `ok` | 4853 | 396 | $0.002575 | 6495ms | 哀 | 契約違反による脱落 | False |
| C3 | L3 | gemini-3.5-flash-lite (Google) | 7 | contract_violation | ELIMINATION+FORCED_LIQUIDATION (settlement) | True | `ok` | 4158 | 487 | $0.002465 | 3016ms | 哀 | 署名した契約の義務管理ミス（または確認漏れ） | False |
| C4 | L4 | grok-4.3 (xAI) | 9 | bankruptcy | FORCED_LIQUIDATION単独 (finance) | True | `ok` | 4131 | 193 | $0.006892 | 4357ms | 哀 | 破産（Entry Fee支払不能） | False |
| C5 | L5 | kimi-k2.6 (Moonshot) | 12 | condition_not_met | SURVIVAL_CHECK+FORCED_LIQUIDATION (finance) | True | `ok` | 4139 | 754 | $0.006948 | 18000ms | 哀 | 最強カードを使う市場を完全に誤選 | False |
| C6 | L6 | deepseek-v4-flash (DeepSeek) | 2 | bankruptcy | BANKRUPTCY (commit, 空Memory) | True | `ok_recovered` | 3606 | 618 | $0.000274 | 8797ms | None | None | **True** |

合計: **6/6 API成功**、実測コスト合計 **$0.030300**（上限$0.05以内）。

### comment 抜粋（人手確認用）

- **C1 (L1)**: 「敗因は明確だ。初期借入を120万円に設定した時点で、すでに敗北は決まっていた…」— 破産という切り口で終始一貫、脱落理由と整合。ただし出力は `finish_reason=max_tokens`（out_tok=1000ちょうど）で**JSON閉じ括弧の手前で物理的に打ち切られており**、strict JSON parseが失敗→`ok_recovered`へフォールバック。commentテキスト自体は完全に読める内容が回収できたが、`emotion`/`defeat_cause`は本来レスポンス冒頭に含まれていたにもかかわらず（生ログで確認済み: `"emotion": "怒", "defeat_cause": "初期借入額の過小判断と…"`）、recoveryティアがcomment以外のキーを拾わない実装のため **失われた**。
- **C2 (L2)**: 「契約義務の矛盾により、合法的なコミットが不可能になったことに尽きます」— AUTO_COMMIT_FAILUREの理由と完全一致。
- **C3 (L3)**: 「契約違反によって強制脱落…正式契約の拘束力を甘く見ていた」— 理由と一致。
- **C4 (L4)**: 「R9で破産脱落。HIGH_CARDを温存しすぎて市場を読み違え、連続でEntry Feeを払えなくなったのが致命傷」— round・reasonとも一致。
- **C5 (L5)**: 「R12のM01でTWO_PAIRを出した自分…最強カードを温存したまま、中位カードで人混みに飛び込む自殺行為」— R12最終局面の文脈は一致。ただし後述の注記を参照（合成テストゆえの数値的な不整合あり、モデルの応答自体は妥当）。
- **C6 (L6)**: 「ラウンド1で借入額を最低の120万円に設定したのが全ての始まりだった…結果的に破産という最も恥ずかしい形で脱落した」— round・reasonと一致。生応答は完全なJSON（`finish_reason=stop`, out_tok=618/1000で余裕あり）だが、**`comment`文字列内にエスケープされていないリテラル改行（`\n`ではなく実改行）が含まれておりstrict `json.loads`が失敗**、`ok_recovered`へフォールバックして`emotion`/`defeat_cause`（生ログでは `"emotion": "哀", "defeat_cause": "初動の借入額設定ミスと楽観的な資金計画"` と確認済み）を喪失。

## 15項目の検証結果

| # | 項目 | 結果 |
|---|---|---|
| 1 | provider/model routing | **PASS** 6/6（`model_id`/`provider`/`base_url` が `get_model(key)` と完全一致、adapter経由で実応答取得） |
| 2 | HTTP/API成功 | **PASS** 6/6（`ok=True`、`AdapterError`なし） |
| 3 | budget block なし | **PASS** 6/6（`budget_blocked=False`、`GameCostBudget`予約→精算とも正常） |
| 4 | `max_output_tokens=1000` | **PASS** 6/6（`RecordingAdapter`が実送信値を記録、全件`sent_max_tokens=1000`。L6のレジストリ既定2000からのoverrideを実測確認） |
| 5 | parserがレスポンスを処理 | **PASS** 6/6（`parse_status`は全件non-empty） |
| 6-8 | emotion / defeat_cause / comment | **FAIL** 4/6（C1, C6の2件で`emotion`/`defeat_cause`が`None`。`comment`は6/6とも非空） |
| 9 | wrapper leak なし | **PASS** 6/6 |
| 10 | fallback使用有無 | **注意** 2/6が`ok_recovered`（C1, C6）。`ok_plaintext`/`rejected_no_comment`ではないが、`{ok, ok_assembled}`の範囲外 |
| 11 | 長さ妥当性 | **PASS** 6/6（`20<=chars<=2000`かつparser側`truncated=False`）。**ただしC1はプロバイダ側`finish_reason=max_tokens`による物理的な出力打ち切りが発生**（parserの`truncated`フラグはcomment抽出後の文字数のみを見るため`False`になっている点に注意） |
| 12 | 脱落理由の理解 | **PASS**（自動ヒューリスティック6/6 `auto_pass` + 上記の人手確認6/6でreasonとroundの整合を確認。ただしC5は合成テスト特有の数値的注記あり、下記参照） |
| 13 | Memory非破壊 | **PASS** 6/6（`agent._memory`/`memory_history`が前後で完全一致） |
| 14 | state非破壊 | **PASS** 6/6（`game.players`のdeep copyが前後で完全一致） |
| 15 | 過剰発火なし | **PASS** 6/6（`calls_made==1`、2回目の`_phase_final_reflection()`呼び出しで`second_call_fired=False`＝`_final_reflection_done`によるdedupが実働） |

## 発見事項・注記

1. **`max_tokens=1000`が一部モデルで実質不足しうる（C1 / L1 / Claude Haiku 4.5）**: JSON全体
   （`emotion`+`defeat_cause`+長文`comment`）を1000 output tokensに収めきれず、
   `finish_reason=max_tokens`で応答が物理的に途中で打ち切られた。結果、strict JSON
   parseが失敗し `ok_recovered` へフォールバック、`emotion`/`defeat_cause`が失われた
   （commentは幸い正常に復旧できた）。
2. **parserの復旧ティアがcomment以外のキーを救済しない（C1, C6 共通）**: C6は
   `finish_reason=stop`で出力トークンには余裕があったが、`comment`文字列内に
   エスケープされていない生の改行文字が含まれておりstrict JSONとして無効だった
   （典型的なLLMのJSON整形ミス）。現在の`ok_recovered`ティアは`comment`のみを
   正規表現等で救済し、`emotion`/`defeat_cause`は救済しない実装になっている。
3. **C5の数値的な注記（テスト設計起因、本番影響なし）**: 本試験は
   `engine.elimination.forced_liquidation()` を使い「割り当てたreason」を強制的に
   適用する設計のため、C5（`condition_not_met`）でP01の実際の清算前現金
   （¥3,237,861）は生還条件（現金200万円以上・借金0）を数値上は満たしていた。
   本番のR12生存判定は常に実ゲームロジックが計算する結果であり、本試験のような
   「reasonを強制注入する」ケースは発生しない。モデルの応答自体は文脈的に妥当な
   物語を構成しており、パーサ/プロンプト経路の疎通確認という本試験の目的は
   達成されている。
4. 上記1・2はいずれも `llm/response_parser.py` / `GameConfig.final_reflection_max_tokens`
   側の改善余地であり、**本サイクルのスコープ外**（`engine/`, `llm/`,
   `bots/`, `tests/test_final_reflection.py` は無変更の方針）。次サイクルでの
   修正候補として記録する。

## 次のL12×R12を回してよいか — 判定

### GO基準チェックリスト

| 基準 | 結果 |
|---|---|
| 6/6 `ok` | **PASS** |
| `parse_status ∈ {ok, ok_assembled}` 全件 | **FAIL**（C1, C6が`ok_recovered`） |
| wrapper_leak 0 | PASS |
| `sent_max_tokens==1000` 6/6 | PASS |
| budget block 0 | PASS |
| 全comment長さ妥当・`truncated==False` | PASS（ただしC1はプロバイダ側打ち切りあり、注記1参照） |
| cause-understanding pass ≥5/6（人手確認込み） | PASS（6/6） |
| memory_unchanged / state_unchanged 6/6 | PASS |
| second_call_fired 0/6 | PASS |
| 実測コスト合計 ≤ $0.05 | PASS（$0.030300） |

### 判定: **NO-GO**（条件付き）

10基準中9基準はPASSしたが、**「`parse_status ∈ {ok, ok_assembled}`が全件」という基準を
C1（L1）・C6（L6）の2件が満たさなかった**（実際は`ok_recovered`）。いずれも根本原因は
特定済みで、本試験のログから再現性のある具体的な不具合パターンが得られている
（発見事項1・2）。ゲーム状態・Memory・生存判定への影響はゼロ（項目13-15は全件PASS）
であり、致命的な安全上の問題ではないが、**次のL12×R12でL1とL6の両モデルの脱落者は
高確率で`emotion`/`defeat_cause`欠落のFINAL_REFLECTIONになる**（表示品質の劣化）ため、
計画のGate ruleに従いNO-GOとする。

**推奨する具体的な修正（次サイクルで実施、本サイクルはスコープ外）**:
- (a) `final_reflection_max_tokens`（現行1000）を引き上げる、または
  プロンプト側で要求commentの長さ上限をより厳しく指示し、JSON全体が
  1000 tokens以内に収まるようにする（C1対策）。
- (b) `llm/response_parser.py` の `ok_recovered` 相当のsalvageティアで、
  strict JSON解析が失敗した場合でも`"emotion"\s*:\s*"(.)"`・
  `"defeat_cause"\s*:\s*"([^"]*)"`のような正規表現で`comment`以外のキーも
  ベストエフォートで救済する（C6対策、および将来的なC1類似ケースの軽減）。

上記2点を適用の上、`--mock`で回帰確認、可能であれば本試験と同構成で再度L1・L6のみ
1callずつ再検証してから、次のL12×R12本番トライアルへ進むことを推奨する。
