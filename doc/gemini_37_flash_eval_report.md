# Gemini 3.7 Flash technical evaluation report

Status: **Phase 1–3 passed; Phase 4 onward not run.**

## Planned commands

```bash
uv run python scripts/model_matrix.py --phase 0 --models M3,M3_G37_EVAL
uv run python scripts/model_matrix.py --phase 1 --models M3_G37_EVAL --max-tokens 2000 --retries 0 --max-calls 5 --max-cost 0.10 --max-cost-per-model 0.10 --run-id gemini37_eval
uv run python scripts/gemini_37_eval.py json --model M3_G37_EVAL --max-tokens 2000 --thinking low --max-parser-corrections 1 --max-calls 8 --max-cost 0.30
uv run python scripts/gemini_37_eval.py compare --models M3,M3_G37_EVAL --cases loan_choice,negotiation,commit,double_up --repeats 3 --max-tokens 2000 --thinking low --max-parser-corrections 1 --max-calls 48 --max-cost 0.48
uv run python scripts/gemini_37_eval.py game --model M3_G37_EVAL --rounds 3 --random-bots 11 --seed 901 --max-tokens 2000 --thinking low --per-player-cost-cap 0.30 --game-cost-cap 0.45 --max-calls 48 --abort-on-budget-block
```

## Result table

| phase | JSON rate | correction retries | latency | input/output/total/thinking | average cost | verdict |
|---|---:|---:|---:|---|---:|---|
| API smoke | 5/5 (100%) | 0 | avg 3,835ms; max 11,341ms | 160 / 25 / 384 / 199 | $0.000192 | PASS |
| game JSON | 4/4 (100%) | 0 | avg 4,053ms; max 4,611ms | 13,043 / 481 / 14,366 / 842 | $0.003686 | PASS |
| M3 vs 3.7 | both 12/12 (100%) | 0 | M3 avg 3,715ms; 3.7 avg 3,073ms | M3 39,129 / 1,995 / 46,896 / 5,772; 3.7 39,129 / 1,557 / 43,635 / 2,949 | M3 $0.010716; 3.7 $0.003854 | PASS |
| S2 R1–R3 | pending | pending | pending | pending | pending | pending |

## Phase 1 actual API result — 2026-08-22 JST

- Command: `uv run python scripts/gemini_37_eval.py smoke --model M3_G37_EVAL --calls 5 --max-tokens 2000 --thinking low --max-calls 5 --max-cost 0.10 --run-id phase1_20260822`
- Requested and returned model: `gemini-3.7-flash` for all 5 calls.
- Request policy: OpenAI-compatible `reasoning_effort="low"`, no `temperature` / `top_p` / `top_k`, `max_tokens=2000`.
- Transport retry: 0 for every logical call. All finishes were `stop`; JSON was exactly `{"ok": true}`.
- Usage totals: input 160, visible output 25, total 384, hidden-thinking difference 199 tokens. The OpenAI-compatible `reasoning_tokens` field was 0; Gemini thinking is represented by `total - input - output` in this response.
- Cost: $0.000960 total, $0.000192 per call. This equals $0.000120 input plus $0.000840 output/thinking at the recorded $0.75/$3.75 per-1M rates and remains below the $0.10 hard cap.
- Per-call latency: 11,341 / 2,836 / 1,684 / 1,874 / 1,441ms. The first call is a cold-start outlier; it is included in the average.
- Evidence: `logs/gemini_37_eval/phase1_20260822/smoke_calls.jsonl` and `smoke_summary.json`.
- Stop guards did not fire. Phase 2, M3 comparison, and S2/R3 integration were not executed.

## Phase 2 actual API result — 2026-08-22 JST

- Command: `uv run python scripts/gemini_37_eval.py json --model M3_G37_EVAL --cases loan_choice,negotiation,commit,double_up --thinking low --max-tokens 2000 --max-parser-corrections 1 --max-calls 8 --max-cost 0.12 --run-id phase2_20260822`
- All four prompt-builder/parser paths passed on the first response. `strategy.emotion` was present and valid in every response; parser corrections, transport retries, parse errors, and additional correction cost were all zero.
- Every response returned `gemini-3.7-flash`, finished with `stop`, and used `max_tokens=2000` without a length truncation. The responses used Markdown JSON fences, which the production `extract_json` parser correctly handled.

| case | latency | input / output / total / thinking | cost | result |
|---|---:|---|---:|---|
| loan_choice | 3,938ms | 2,937 / 129 / 3,274 / 208 | $0.003467 | PASS |
| negotiation | 3,810ms | 3,512 / 143 / 3,892 / 237 | $0.004059 | PASS |
| commit | 3,853ms | 3,313 / 130 / 3,655 / 212 | $0.003767 | PASS |
| double_up | 4,611ms | 3,281 / 79 / 3,545 / 185 | $0.003451 | PASS |

- Totals: input 13,043, visible output 481, total 14,366, hidden-thinking difference 842. `reasoning_tokens` remained 0 in the OpenAI-compatible response, so the Gemini thinking usage is measured as `total - input - output`.
- Cost: $0.014743 recorded by the runner ($0.003686 average/call), below the $0.12 shared hard cap. Each call was guarded by `BudgetedAdapter` worst-case reservation before sending.
- The negotiation prompt included a Japanese DM, active/pending contract information, a pending card trade, S2 rule text, and Handover Memory; the commit path received the same Memory and contract obligation context.
- Evidence: `logs/gemini_37_eval/phase2_20260822/json_calls.jsonl` and `json_summary.json`. M3 comparison and S2/R3 integration were not executed.

## Phase 3 actual API result — 2026-08-22 JST

- Command: `uv run python scripts/gemini_37_eval.py compare --models M3,M3_G37_EVAL --cases loan_choice,negotiation,commit,double_up --repeats 3 --thinking low --max-tokens 2000 --max-parser-corrections 1 --max-calls 48 --max-cost 0.48 --run-id phase3_20260822`
- Conditions were identical at the request interface: fixed Phase 2 prompt-builder inputs, `reasoning_effort="low"`, `max_tokens=2000`, no sampling parameters, and transport retry 0. Google Standard pricing was reconfirmed as M3 $1.50/$9.00 and 3.7 $0.75/$3.75 per 1M input/output-including-thinking tokens; see `doc/cost/gemini_35_37_comparison_pricing_2026-08-22.md`.
- Each model completed 12 logical calls (4 cases x 3) with 12/12 initial JSON successes. There were no parser corrections, parse errors, `length` finishes, transport retries, model mismatches, usage inconsistencies, or budget blocks. Every valid result included a valid action and emotion.
- Returned model was `gemini-3.5-flash` for every M3 call and `gemini-3.7-flash` for every evaluation-key call. All finishes were `stop`; `reasoning_tokens` was 0, so hidden thinking is recorded as `total - input - visible output`.

| model | latency avg / median / max / p95 | input / output / total / thinking | total cost | cost / successful JSON |
|---|---|---|---:|---:|
| M3 / 3.5 | 3,715 / 3,511 / 5,402 / 5,402ms | 39,129 / 1,995 / 46,896 / 5,772 | $0.128597 | $0.010716 |
| M3_G37_EVAL / 3.7 | 3,073 / 2,753 / 6,095 / 6,095ms | 39,129 / 1,557 / 43,635 / 2,949 | $0.046244 | $0.003854 |

| case | M3 cost / successful JSON | 3.7 cost / successful JSON |
|---|---:|---:|
| loan_choice | $0.009620 | $0.003436 |
| negotiation | $0.013029 | $0.004368 |
| commit | $0.010427 | $0.003678 |
| double_up | $0.009791 | $0.003932 |

- 3.7 cost delta was **-$0.006862 per logical call**, a **64.04% reduction** versus M3. It also used 2,823 fewer hidden-thinking tokens and 438 fewer visible-output tokens over the matched 12 calls; mean latency was 642ms lower, while its single worst observation was 693ms higher.
- Total actual comparison cost was **$0.174841**, within the shared $0.48 hard cap. Phase 3 is PASS and supports proceeding to a separately approved Phase 4 S2/R1–R3 integration test. It does not authorize changing formal M3.
- Evidence: `logs/gemini_37_eval/phase3_20260822/compare_calls.jsonl` and `compare_summary.json`.

Promotion of 3.7 to formal M3 requires explicit human approval after all results, pricing reconciliation, zero Gemini-caused AUTO COMMIT in the 3R run, and a confirmed measured cost advantage.
