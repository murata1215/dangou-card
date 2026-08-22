# Gemini 3.5 Flash / 3.7 Flash comparison pricing — 2026-08-22 JST

Phase 3 comparison-only price snapshot. The formal `M3` definition is not changed by this document.

## Primary source

- [Gemini Developer API pricing](https://ai.google.dev/gemini-api/docs/pricing), checked 2026-08-22 JST.
- [Gemini OpenAI compatibility](https://ai.google.dev/gemini-api/docs/openai), checked 2026-08-22 JST.

## Standard paid rates used by the synchronous comparison

| model | input / 1M | output including thinking / 1M | registry match |
|---|---:|---:|---|
| `gemini-3.5-flash` | $1.50 | $9.00 | `M3`: yes |
| `gemini-3.7-flash` | $0.75 through 2026-12-31 | $3.75 through 2026-12-31 | `M3_G37_EVAL`: yes |

The comparison does not use batch, flex, priority, caching, grounding, or tools. Cost is calculated from returned usage with the Standard input price and the output price, which includes thinking tokens. Gemini's OpenAI-compatible usage can report `reasoning_tokens=0`; hidden thinking is therefore separately observed as `total_tokens - input_tokens - output_tokens`.

## Equivalent request-policy boundary

Both models use the OpenAI-compatible payload `reasoning_effort="low"`, `max_tokens=2000`, no `temperature` / `top_p` / `top_k`, and no native `thinking_level` or `thinking_budget`. Google documents that `reasoning_effort="low"` maps to Gemini 3 Flash low thinking. This equalizes the requested interface level; it does not assert equal internal reasoning-token consumption across different model generations.
