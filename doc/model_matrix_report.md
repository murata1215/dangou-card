# 18モデル マトリクス最終レポート

- run_id: run_20260818_041810
- 通算コスト: $1.5591 / $3.00
- 通算コール数: 135

| Vendor | Tier | Model | API | JSON | Game | Returned | Latency | Cost | 備考 |
|---|---|---|---|---|---|---|---|---|---|
| DeepSeek | L | L6 (deepseek-v4-flash) | ✓ | ✓ | ✓ | deepseek-v4-flash | 3267ms | $0.0011 |  |
| OpenAI | L | L2 (gpt-4.1-mini) | ✓ | ✓ | ✓ | gpt-4.1-mini-2025-04-14 | 1946ms | $0.0045 |  |
| Google | L | L3 (gemini-3.5-flash-lite) | ✓ | ✓ | ✓ | gemini-3.5-flash-lite | 669ms | $0.0049 |  |
| xAI | L | L4 (grok-4.3) | ✓ | ✓ | ✓ | grok-4.3 | 3073ms | $0.0209 |  |
| Moonshot | L | L5 (kimi-k2.6) | ✓ | ✓ | ✓ | kimi-k2.6 | 666ms | $0.0177 |  |
| Anthropic | L | L1 (claude-haiku-4-5-20251001) | ✓ | ✓ | ✓ | claude-haiku-4-5-20251001 | 2358ms | $0.0256 |  |
| DeepSeek | M | M6 (deepseek-v4-flash) | ✓ | ✓ | ✓ | deepseek-v4-flash | 2169ms | $0.0003 |  |
| Moonshot | M | M5 (kimi-k2.6) | ✓ | ✓ | ✓ | kimi-k2.6 | 758ms | $0.0182 |  |
| xAI | M | M4 (grok-4.5) | ✓ | ✓ | ✓ | grok-4.5 | 1327ms | $0.0436 |  |
| OpenAI | M | M2 (gpt-4.1) | ✓ | ✓ | ✓ | gpt-4.1-2025-04-14 | 1652ms | $0.0283 |  |
| Anthropic | M | M1 (claude-sonnet-5) | ✓ | ✓ | ✓ | claude-sonnet-5 | 3481ms | $0.0398 |  |
| Google | M | M3 (gemini-3.5-flash) | ✓ | ✓ | ✓ | gemini-3.5-flash | 2747ms | $0.0475 |  |
| DeepSeek | H | H6 (deepseek-v4-pro) | ✓ | ✓ | ✓ | deepseek-v4-pro | 3648ms | $0.0063 |  |
| Moonshot | H | H5 (kimi-k3) | ✓ | ✓ | ✓ | kimi-k3 | 1794ms | $0.0561 |  |
| xAI | H | H4 (grok-4.6) | ✓ | ✓ | ✓ | grok-4.6 | 3052ms | $0.2553 | P3 runtime: max_tokens=2000, effort=—, timeout=180 |
| Anthropic | H | H1 (claude-opus-5) | ✓ | ✓ | ✓ | claude-opus-5 | 3462ms | $0.3162 | P3 runtime: max_tokens=8000, effort=high, timeout=600 |
| Google | H | H3 (gemini-3.1-pro-preview) | ✓ | ✓ | ✓ | gemini-3.1-pro-preview | 5004ms | $0.0864 |  |
| OpenAI | H | H2 (gpt-5.6-sol) | ✓ | ✓ | ✓ | gpt-5.6-sol | 5333ms | $0.1129 |  |

`✓`=通過 / `✗`=失敗 / `—`=未実行（ゲート除外・予算スキップ・キー未設定）。
Returned列: Phase 1のAPI実返却モデル名。`⚠`=requestedと不一致（要確認）。`—`=取得不能または未実行。
注記: ベンダー間で output_tokens を直接比較しないこと。Anthropicはthinkingをoutput_tokensに合算、OpenAIはreasoningをcompletionに内包、Geminiのみ差分課金。
