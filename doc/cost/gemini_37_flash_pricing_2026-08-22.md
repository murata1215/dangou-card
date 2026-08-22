# Gemini 3.7 Flash pricing snapshot — 2026-08-22 JST

Technical-evaluation snapshot only. `M3` remains Gemini 3.5 Flash; the values below are used only by `M3_G37_EVAL`.

## Official primary sources checked

- [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) — accessed 2026-08-22 JST.
- [Gemini 3.7 Flash model announcement and migration guidance](https://ai.google.dev/gemini-api/docs/latest-model) — accessed 2026-08-22 JST.
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai) — accessed 2026-08-22 JST.
- [Gemini 3.7 Flash model page](https://ai.google.dev/gemini-api/docs/models/gemini-3.7-flash) — accessed 2026-08-22 JST.

## Recorded values

| item | value |
|---|---:|
| model ID | `gemini-3.7-flash` |
| input | $0.75 / 1M tokens through 2026-12-31 |
| output, including thinking | $3.75 / 1M tokens through 2026-12-31 |
| cached input | $0.075 / 1M tokens through 2026-12-31 |
| supported thinking levels | `low`, `medium`, `high` |
| default thinking | `medium` |
| max output | 64k tokens |

The 2027-01-01 standard rate is listed by Google as $1.50 input and $7.50 output per 1M tokens. This evaluation records the current introductory rate and must be rechecked before any production roster change.

## Request policy

- Use OpenAI-compatible `reasoning_effort: "low"`; Google maps it to Gemini thinking level.
- Do not send `thinking_level` or `thinking_budget` together with `reasoning_effort`.
- Do not send deprecated sampling parameters `temperature`, `top_p`, or `top_k` to 3.7 Flash.
- `minimal` is unsupported by 3.7 Flash and must not be used.
- No API calls were made while this snapshot and harness were implemented.
