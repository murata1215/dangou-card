# l12r12_dr_1201 DevRelay 席 504 タイムアウト原因切り分け

- 対象: `logs/llm/trial_C_l12r12_dr_1201/`（12体×12R本戦、seed=1201、試合時間 38,133.5秒、2026-09-20 23:51:43〜2026-09-21 10:27:16 UTC）
- 集計スクリプト: `/tmp/c1012/timeout.py`（リポジトリ外）
- 分析のみ・コード/設定/テスト変更なし・LLM API/DevRelay呼び出し0件

## 1. 席別エラー・所要時間表（全12席）

| pid | model | calls | err | 504 | other_err | retry>0 | ok mean/med/p90/max (s) | 入力tok平均(ok) |
|---|---|---:|---:|---:|---:|---:|---|---:|
| P01 | L3 Gemini 3.5 Flash-Lite | 121 | 0 | 0 | 0 | 0 | 1.7 / 1.6 / 2.2 / 7.4 | 8,900 |
| P02 | DR_OPUS Claude Opus 5 | 145 | 1 | 1 | 0 | 0 | 30.4 / 25.0 / 57.9 / 104.4 | 15,293 |
| P03 | DR_TERRA GPT-5.6 Terra | 111 | 0 | 0 | 0 | 0 | 22.0 / 19.6 / 33.4 / 51.0 | 34,579 |
| P04 | L4 Grok 4.3 | 147 | 0 | 0 | 0 | 0 | 7.0 / 6.7 / 9.5 / 14.9 | 9,646 |
| P05 | L2 GPT-4.1 Mini | 37 | 0 | 0 | 0 | 0 | 2.8 / 2.5 / 3.8 / 6.1 | 14,073 |
| P06 | DR_FABLE Claude Fable 5.1 | 148 | 0 | 0 | 0 | 0 | 28.5 / 24.2 / 52.9 / 91.2 | 15,392 |
| P07 | L6 DeepSeek V4 Flash | 151 | 0 | 0 | 0 | 4 | 3.3 / 2.7 / 4.0 / 12.0 | 16,154 |
| P08 | L1 Claude Haiku 4.5 | 110 | 0 | 0 | 0 | 1 | 12.4 / 12.2 / 15.3 / 26.5 | 13,075 |
| P09 | L5 Kimi K2.6 | 146 | 0 | 0 | 0 | 0 | 16.6 / 13.8 / 22.7 / 103.6 | 13,981 |
| P10 | DR_SOL GPT-5.6 Sol | 148 | 0 | 0 | 0 | 0 | 18.6 / 15.9 / 32.2 / 56.3 | 33,305 |
| **P11** | DR_OPUS48 Claude Opus 4.8 | 143 | **57** | **57** | 0 | 0 | 56.6 / 57.3 / 88.2 / 104.1 | 13,672 |
| **P12** | DR_SONNET5 Claude Sonnet 5 | 146 | **15** | **15** | 0 | 0 | 45.4 / 41.8 / 85.0 / 102.2 | 14,296 |

- エラーは全件 `error_type=timeout`、メッセージ `DevRelay HTTP 504: timeout: raw-completion timed out on agent side`。504以外のエラー種別は全12席で0件。
- `retry_count>0`（JSON不正時の修正リトライ、`llm/constants.py::MAX_RETRIES=2`）はP07 4件・P08 1件のみで、DevRelay 6席では0件。API側504/429リトライ（`llm/constants.py::API_MAX_RETRIES=2`）が発火した記録もない。

## 2. 打ち切り秒数の推定

504コールの `elapsed_ms`（秒換算）分布:

- P11 (n=57): min 108.0 / med 108.1 / mean 108.1 / max 108.5
- P12 (n=15): min 108.0 / med 108.1 / mean 108.1 / max 108.3

72件すべてが **108.0〜108.5秒** の狭い帯に張り付いており、httpx側のReadTimeout（`llm/providers/devrelay_http.py` L230-231: `http_timeout = timeout_seconds(120) + HTTP_TIMEOUT_MARGIN_S(30) = 150秒`）には一度も到達していない。クライアントは `timeoutS: 120` をDevRelayへ送信している（同 L238, `llm/models.py` のDR_OPUS48/DR_SONNET5とも `timeout_seconds=120`）。

**推定**: DevRelayエージェント側の打ち切りは約108秒（= timeoutS 120の90%、または固定108秒のいずれか。この repo からは確認不可、推定値）。成功コールの最大は104.4秒（P02）で108秒未満に収まっており矛盾しない。

## 3. 仮説A「モデルが遅い」の検証

- 成功コール所要時間: P11 p90 88.2s / p95 97.5s / max 104.1s（打ち切り108sとの差はp90で約20s、maxで約4s）。P06 p90 52.9s、P02 p90 57.9sと比べP11は明確に遅い側。
- **出力速度（tok/s中央値）はほぼ同一**: P02 55.8 / P06 54.3 / **P11 56.4** / P12 75.5。生成スループット自体に差はない。
- 差は**出力トークン量**: 出力tok中央値 P02 1,422 / P06 1,286 / **P11 3,175** / **P12 2,936**。可視応答文字数（resp_chars）はP02 618・P06 600・P11 586・P12 536とほぼ同じ約600文字。→ `out_tok/resp_chars` = P02 2.46・P06 2.30・**P11 4.59・P12 5.83**（API直のHaiku P08は0.80）。Opus 4.8 / Sonnet 5 は可視出力に対する内部（hidden thinking）トークン比率がOpus 5 / Fable の約2倍。
- 所要時間と出力トークンの相関は **P02 0.98 / P06 0.99 / P11 0.98 / P12 0.98** と全席で極めて高い一方、入力トークンとの相関は0.26〜0.57と弱い。所要時間は入力の長さではなく出力（thinking込み）量にほぼ比例する。
- tok/s中央値×108秒 ≈ 6,000〜6,100トークンが打ち切りの壁に相当し、P11の成功コール最大出力は6,645トークン（104.1秒）と整合する。
- P11の504はR2-8で17/83件、**R9-12で40/48件（83%）**に集中。R9-12の成功コール出力中央値はR9 3,340・R10 5,723・R11 5,195トークンと後半ほど増加しており、同時間帯（06-09 UTC）のtok/sは56〜63で低下していない → サーバー混雑による速度低下ではなく、生成する（hidden thinking）トークン量そのものの増加が原因。

**判定**: 仮説A（速度低下ではなく「出力トークン量が打ち切り時間内に収まらない」という意味でのモデル特性）が最有力。

## 4. 仮説B「順番待ち」の検証

- エンジンは完全逐次実行（`engine/game.py`・`scripts/llm_trial.py` にThreadPool/asyncio/並列処理なし）。全1,553コールの実行区間（`timestamp - elapsed_ms` 〜 `timestamp`）を総当たりで重なり判定 → **1秒超の重なり0件、最大同時実行数0**。
- 直前60秒以内に開始された他のDevRelay 6席のコール数（近似同時実行数）: P11 504時 平均1.19（n=57）/ 成功時 平均1.53（n=86）、P12 504時 平均0.93（n=15）/ 成功時 平均1.03（n=131）。**504時の方がむしろ少ない**ため、同時実行量との相関は見られない。
- フェイズ別: P11 504はnegotiation 55件・commit 2件（calls全体は negotiation 115 / commit 12 / reflection 11 / double_up 3）。P12 504は全15件negotiation。
- ラウンド別: P11は R2-8: 2,4,2,1,2,2,4件、**R9-12: 11,10,9,10件**。P12はR3,7-12にまばらに発生しR9(4件)・R11(4件)がやや多い。

**判定**: 仮説Bは棄却。順番待ち・同時実行競合を示す証拠はない。

## 5. 仮説C「設定不一致」の検証

- P11: 全143件で `model_id=devrelay/claude-opus-4-8`、成功86件の `response_model=claude-opus-4-8`、`requested_ai=ai=claude` で統一。P12も全146件 `devrelay/claude-sonnet-5` / `claude-sonnet-5` / `claude`/`claude` で統一。
- `system_prompt` はP11・P12とも全コールで1種類のみ（試合全体で内容変化なし＝キャッシュ・プロンプト構成の途中変化なし）。
- プロンプト長（system+user文字数）: P11 504時平均17,401字 / 成功時平均16,033字、P12 504時平均17,178字 / 成功時平均16,758字。ラウンドが進むほど双方とも長くなる自然な傾向の範囲内で、504コールだけが突出して長いわけではない。

**判定**: 仮説Cは棄却。リクエスト内容・モデルID・aiフィールドに成功/504間の設定差は確認できない。

## 6. エンジン側の再試行の実態

- `llm/providers/devrelay_http.py` L252-295: リトライ対象は **429（targetBusy/rateLimited）とhttpx.ConnectErrorのみ**（`RETRY_BASE_SECONDS=5`の指数バックオフ）。504は L278-282 `if response.status_code != 200: raise AdapterError(...)` で**即座に例外**、リトライされない。
- `llm/llm_agent.py::_call_llm` L243-247 は `AdapterError` を捕捉して `text=""` に変換するのみで再試行しない。
  - `commit()` L436-438: `if not text: ... raise ValueError("Empty response from LLM")` → **JSON不正時のMAX_RETRIES=2ループには入らずAPI呼び出しは1回のみ**。
  - `negotiation()` も同様に L373-374 で即 `PassAction(source="parse_failed")`。
- `engine/game.py` L1107-1110: `agent.commit()` の例外を握りつぶし `commit_action=None` → `reject_reason="no_valid_response"` → L1207 `AUTO_COMMIT` イベントで代行。
- 実測（P11）:
  - R9: `game01_P11_llm_calls.jsonl` 106行目（phase=commit, retry_count=0, elapsed 108.0s, error_type=timeout）→ `game01_events.jsonl` 1597行目 `AUTO_COMMIT`（`reason: no_valid_response`, `actual_market_id: M01`, `actual_card: STRAIGHT_1`）。
  - R10: 同118行目（retry_count=0, elapsed 108.1s）→ events 1755行目 `AUTO_COMMIT`（`actual_card: FULL_HOUSE_1`）。
  - 両ラウンドとも**commitへの試行は1回のみ**で、失敗後の再試行は行われていない。

## 7. 試合時間への影響

- 504コールの `elapsed_ms` 合計: **7,890.3秒**。試合全体38,133.5秒に対し **20.7%**。
  - P11: 6,160.7秒（試合全体の16.2%、P11自身の総所要11,030.1秒の55.9%）
  - P12: 1,621.4秒（試合全体の4.3%）
  - P02: 104.4秒（1件のみ）

## 8. 結論

**A/B/Cのうち最有力はA**。ただし内実は「モデルの生成速度が遅い」のではなく「Opus 4.8 / Sonnet 5 の応答が可視出力に対して内部（hidden thinking）トークンを多く使い、打ち切り時間内に収まりきらない」こと。根拠となる数字3つ:

1. 504のelapsed_msが72件すべて108.0〜108.5秒に張り付く（打ち切りは一定値であることを示す）。
2. 所要時間と出力トークンの相関が全席0.98前後と極めて高い一方、生成速度（tok/s中央値56前後）はP02/P06/P11で同一 — P11の出力トークン中央値3,175はOpus5の1,422の2.2倍、`out_tok/可視文字数`比はP11 4.59・P12 5.83に対しP02 2.46・P06 2.30。
3. エンジン側は完全逐次実行で重なり0件、504時の直前60秒同時実行数（平均1.19）はむしろ成功時（1.53）より少ない — 順番待ちの兆候なし。

### 対処案

**エンジン側でできること**（本分析では未実装・提案のみ）:
- `commit()` / negotiation の API エラー（AdapterError）発生時に、JSON不正時と同様に1回程度の再試行を追加する（現状は0回でAUTO_COMMITに直結）。
- DevRelay席（DR_OPUS48/DR_SONNET5等）の `timeout_seconds` をDevRelay契約上限の180秒まで引き上げ、打ち切り前に完了する余地を広げる。

**エージェント側の設定で確認すべき項目**（この repo には設定値がなく、名称・存在は推定）:
- raw-completionのエージェント側打ち切り秒数の実体（`timeoutS`の90%なのか、108秒固定なのか）。
- Opus 4.8 / Sonnet 5 のthinking有効化状態とbudget（`thinking.budget_tokens` や `maxThinkingTokens` に相当する設定）。DevRelay契約上 `max_tokens` は送信していないため、モデル側の出力上限既定値も要確認。
- 同時実行数上限（本試合では逐次実行のため未検証・仮説Bとしては棄却したが、将来並列実行を導入する場合は別途確認が必要）。

### 未集計（理由）

- 504コールの実際の出力トークン数: レスポンスが得られないためログに記録されず、未集計。
- DevRelayサーバー側のキュー・負荷状態: このプロジェクトのログには含まれず、未集計。
