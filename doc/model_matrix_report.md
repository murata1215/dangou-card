# 18モデル マトリクス最終レポート

- run_id: run_20260818_041810
- 通算コスト: $0.2127 / $0.25
- 通算コール数: 66

| Vendor | Tier | Model | API | JSON | Game | Returned | Latency | Cost | 備考 |
|---|---|---|---|---|---|---|---|---|---|
| DeepSeek | L | L6 (deepseek-v4-flash) | ✓ | ✓ | — | deepseek-v4-flash | 3267ms | $0.0001 |  |
| OpenAI | L | L2 (gpt-4.1-mini) | ✓ | ✓ | — | gpt-4.1-mini-2025-04-14 | 1946ms | $0.0016 |  |
| Google | L | L3 (gemini-3.5-flash-lite) | ✓ | ✓ | — | gemini-3.5-flash-lite | 669ms | $0.0012 |  |
| xAI | L | L4 (grok-4.3) | ✓ | ✓ | — | grok-4.3 | 3073ms | $0.0056 |  |
| Moonshot | L | L5 (kimi-k2.6) | ✓ | ✓ | — | kimi-k2.6 | 666ms | $0.0045 |  |
| Anthropic | L | L1 (claude-haiku-4-5-20251001) | ✓ | ✓ | — | claude-haiku-4-5-20251001 | 2358ms | $0.0062 |  |
| DeepSeek | M | M6 (deepseek-v4-flash) | ✓ | ✓ | — | deepseek-v4-flash | 2169ms | $0.0001 |  |
| Moonshot | M | M5 (kimi-k2.6) | ✓ | ✓ | — | kimi-k2.6 | 758ms | $0.0044 |  |
| xAI | M | M4 (grok-4.5) | ✓ | ✓ | — | grok-4.5 | 1327ms | $0.0131 |  |
| OpenAI | M | M2 (gpt-4.1) | ✓ | ✓ | — | gpt-4.1-2025-04-14 | 1652ms | $0.0081 |  |
| Anthropic | M | M1 (claude-sonnet-5) | ✓ | ✓ | — | claude-sonnet-5 | 3481ms | $0.0041 |  |
| Google | M | M3 (gemini-3.5-flash) | ✓ | ✗ | — | gemini-3.5-flash | 2747ms | $0.0092 | JSON抽出失敗 |
| DeepSeek | H | H6 (deepseek-v4-pro) | ✓ | ✓ | — | deepseek-v4-pro | 3648ms | $0.0016 |  |
| Moonshot | H | H5 (kimi-k3) | ✓ | ✓ | — | kimi-k3 | 1794ms | $0.0147 |  |
| xAI | H | H4 (grok-4.6) | ✓ | ✓ | — | grok-4.6 | 3052ms | $0.0417 |  |
| Anthropic | H | H1 (claude-opus-5) | ✓ | ✓ | — | claude-opus-5 | 3462ms | $0.0092 |  |
| Google | H | H3 (gemini-3.1-pro-preview) | ✓ | ✗ | — | gemini-3.1-pro-preview | 5004ms | $0.0145 | JSON抽出失敗 |
| OpenAI | H | H2 (gpt-5.6-sol) | ✓ | ✓ | — | gpt-5.6-sol | 5333ms | $0.0251 |  |

`✓`=通過 / `✗`=失敗 / `—`=未実行（ゲート除外・予算スキップ・キー未設定）。
Returned列: Phase 1のAPI実返却モデル名。`⚠`=requestedと不一致（要確認）。`—`=取得不能または未実行。
注記: ベンダー間で output_tokens を直接比較しないこと。Anthropicはthinkingをoutput_tokensに合算、OpenAIはreasoningをcompletionに内包、Geminiのみ差分課金。

- Phase 3: pending → 再開: `uv run python scripts/model_matrix.py --phase 3 --run-id run_20260818_041810 --resume`
