# 2026-08-20 APIコスト検証の固定記録

## 目的とデータ区分

2026-08-20に完了した `run_20260818_041810` のPhase 3検証、H1/H4再試験、および今後のR12コスト計測方針を固定する。

- **repo内実測**: `state.json`、`phase3_calls.jsonl`、Phase 3 call logから抽出した値。
- **ユーザー提供Console値**: Provider Consoleの画面値。今回、Consoleへの再アクセスは行っていない。
- **ユーザー提供R12実績**: M3の円建て実績。今回のPhase 3 runから独立検証した値ではない。

金額の集計範囲を混同しないため、以下を区別する。

| 集計範囲 | 金額 | 意味 |
|---|---:|---|
| Phase 3最終18モデル | $0.927449 | 各モデルの最終stateにある最新attemptだけの合計 |
| Phase 3履歴 | $1.331959 | M3/H1/H4の失敗・再試験attemptも含む22行の合計 |
| run累計 | $1.559130600 | Phase 1/2を含むstate budgetの累計 |

## Phase 3最終結果（18モデル）

以下は各モデルの最終Phase 3 stateを固定した表である。全18モデルがPASSしている。

| Key | Provider | Model | Status | Logical calls | Corrections | Final cost (USD) |
|---|---|---|---|---:|---:|---:|
| L6 | DeepSeek | deepseek-v4-flash | PASS | 3 | 0 | $0.001076 |
| L2 | OpenAI | gpt-4.1-mini | PASS | 3 | 0 | $0.002882 |
| L3 | Google | gemini-3.5-flash-lite | PASS | 3 | 0 | $0.003726 |
| L4 | xAI | grok-4.3 | PASS | 3 | 0 | $0.015298 |
| L5 | Moonshot | kimi-k2.6 | PASS | 3 | 0 | $0.013213 |
| L1 | Anthropic | claude-haiku-4-5-20251001 | PASS | 3 | 0 | $0.019373 |
| M6 | DeepSeek | deepseek-v4-flash | PASS | 3 | 0 | $0.000257 |
| M5 | Moonshot | kimi-k2.6 | PASS | 3 | 0 | $0.013831 |
| M4 | xAI | grok-4.5 | PASS | 3 | 0 | $0.030474 |
| M2 | OpenAI | gpt-4.1 | PASS | 3 | 0 | $0.020158 |
| M1 | Anthropic | claude-sonnet-5 | PASS | 3 | 0 | $0.035642 |
| M3 | Google | gemini-3.5-flash | PASS | 3 | 0 | $0.041427 |
| H6 | DeepSeek | deepseek-v4-pro | PASS | 3 | 0 | $0.004648 |
| H5 | Moonshot | kimi-k3 | PASS | 3 | 0 | $0.041397 |
| H4 | xAI | grok-4.6 | PASS | 3 | 0 | $0.213650 |
| H1 | Anthropic | claude-opus-5 | PASS | 3 | 0 | $0.307040 |
| H3 | Google | gemini-3.1-pro-preview | PASS | 4 | 1 | $0.075542 |
| H2 | OpenAI | gpt-5.6-sol | PASS | 3 | 0 | $0.087815 |
| **Total** |  |  | **PASS 18 / 18** | **55** | **1** | **$0.927449** |

根拠: [state.json](../../logs/model_matrix/run_20260818_041810/state.json)、[Phase 3 aggregate](../../logs/model_matrix/run_20260818_041810/phase3_calls.jsonl)、[Phase 3 report](../../logs/model_matrix/run_20260818_041810/phase3_report.md)。

## H1 Claude Opus 5: timeoutと完走baseline

### 直前の60秒timeout attempt

repo内実測では、`max_tokens=8000` / `effort=high`で、loanとcommitは可視JSONを返した一方、negotiationが既定60秒でtimeoutした。

| Runtime audit | Result | Logical calls | Internal cost |
|---|---|---:|---:|
| max tokens=8000, effort=high, timeout=null | FAIL | 3 | $0.097555 |

ユーザー提供Console値は Anthropic `$8.87 -> $9.08`（表示差分 `+$0.21`）。内部実績との差は`$0.112445`である。timeoutした処理がProvider側で課金されたか、内部usageに返らなかったかは、この記録だけでは断定できない。したがって、このattemptはR12費用試算の根拠として弱い。

### 600秒timeoutでの完走baseline

timeoutだけを600秒へ変更し、performance条件は `max_tokens=8000` / adaptive thinking / `effort=high` のまま維持した。

| Runtime audit | Retry | Result | Logical calls | Corrections | Internal cost |
|---|---:|---|---:|---:|---:|
| max tokens=8000, effort=high, timeout=600 | 0 | PASS | 3 | 0 | $0.307040 |

| Call | Finish reason | Input / Output / Reasoning tokens | Internal cost |
|---|---|---:|---:|
| loan | `end_turn` | 140 / 657 / 423 | $0.017125 |
| negotiation | `end_turn` | 357 / 7,587 / 6,946 | $0.212245 |
| commit | `end_turn` | 567 / 2,162 / 1,885 | $0.077670 |

このattemptは`valid_json_count=2`、AUTO COMMIT 0、response model `claude-opus-5`、model match `match`である。negotiationは119.3秒で成功し、8,000-token上限には到達していない。

ユーザー提供Console値は Anthropic `$9.08 -> $9.37`（表示差分 `+$0.29`）。内部実績`$0.307040`との差は`$0.017040`で、同一オーダーではあるが、Console表示との厳密一致を意味しない。

## H4 Grok 4.6: timeout再試験

repo内実測では、`max_tokens=2000`、runtime timeout=180秒、retry=0でPASSした。

| Runtime audit | Result | Logical calls | Internal cost | 結果 |
|---|---|---:|---:|---|
| max tokens=2000, effort=null, timeout=180 | PASS | 3 | $0.213650 | loanは約181秒でtimeout、negotiation/commitは成功 |

ユーザー提供xAI Console値:

| 指標 | 事前 | 事後 | 表示差分 |
|---|---:|---:|---:|
| Credits usage | $2.42 | $2.51 | +$0.09 |
| Credits remaining | $17.67 | $17.57 | -$0.10 |
| Tokens | 1,114,136 | 1,132,626 | +18,490 |
| Requests | 800 | 808 | +8 |

xAI画面はteam-levelかつ30日集計であるため、H4のrun内`$0.213650`と1対1で照合する用途には向かない。Console値は参考値として保持する。

## M3 Gemini 3.5 Flash: R12実績

以下はユーザー提供の実績値であり、今回のPhase 3短時間試験からの推定ではない。

- R12 × 4試合 × 3体: 約8,000円
- 概算: 約667円 / 体 / R12試合

R12費用を見積もる際は、この実績をPhase 3の短時間call平均からの線形外挿より優先する。

## 棄却した線形外挿

「Phase 3の数call平均単価をR12へ線形外挿する」試算は、過小評価のため棄却する。

- R12では後半ほどgame state・会話履歴などでinputが増える。
- negotiation call数は固定ではない。
- reasoning比率はモデルごとに大きく異なる。
- H1の完走attemptではnegotiation 1 callだけで`$0.212245`となり、単純なcall平均を代表値にできない。

このため、現時点では18席R12の確定予算を出さない。

## 次のR12実測方針

1. **Stage 1**: L系12席 × R12 × 1試合
2. **Stage 2**: L6 + M6 = 12席 × R12 × 1試合
3. **Stage 3**: L6 + M6 + H6 = 18席。本番予算はStage 1/2実績を受けて算出する。

次回以降のR12実測では、少なくとも次を記録する。

- model / seat
- rounds survived
- logical calls / negotiation calls / correction calls
- input / output / reasoning tokens
- internal actual cost
- provider Console delta
- latency / timeout count
- PASS / FAIL
- 取得可能な場合はround別コスト推移

## 参照と制約

- run内costは保存済みusageから計算された一次記録とする。
- Provider Console値とM3 R12円建て実績は、ユーザー提供値として明確に区別する。
- 本資料の作成時に外部API、Provider Console、ゲーム再試験は実行していない。
