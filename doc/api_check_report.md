# 6社12モデル API疎通チェックレポート

- 実行日: 2026-08-10 05:18
- 実行時間: 117.6秒
- 総コスト: $0.0325

## サマリ: 疎通OK 12/12, JSON成立 12/12

## 結果表

| # | ベンダー | モデル | 疎通 | レイテンシ | JSON | JSON ms | コスト | 備考 |
|---|---|---|---|---|---|---|---|---|
| L1 | Anthropic | Claude Haiku 4.5 | ✓ | 1695ms | ✓ | 2565ms | $0.0012 |  |
| L2 | OpenAI | GPT-4.1 Mini | ✓ | 1978ms | ✓ | 2064ms | $0.0002 |  |
| L3 | Google | Gemini 3.5 Flash-Lite | ✓ | 597ms | ✓ | 735ms | $0.0000 |  |
| L4 | xAI | Grok 4.3 | ✓ | 1709ms | ✓ | 4674ms | $0.0005 |  |
| L5 | Moonshot | Kimi K2.6 (L) | ✓ | 1028ms | ✓ | 43308ms | $0.0136 |  |
| L6 | DeepSeek | DeepSeek V3 (L) | ✓ | 833ms | ✓ | 1094ms | $0.0001 |  |
| M1 | Anthropic | Claude Sonnet 5 | ✓ | 3850ms | ✓ | 3449ms | $0.0016 |  |
| M2 | OpenAI | GPT-4.1 | ✓ | 991ms | ✓ | 1636ms | $0.0009 |  |
| M3 | Google | Gemini 3.5 Flash | ✓ | 1255ms | ✓ | 3459ms | $0.0001 |  |
| M4 | xAI | Grok 4.5 | ✓ | 1094ms | ✓ | 5863ms | $0.0036 |  |
| M5 | Moonshot | Kimi K2.6 | ✓ | 949ms | ✓ | 30203ms | $0.0105 |  |
| M6 | DeepSeek | DeepSeek V3 | ✓ | 985ms | ✓ | 1540ms | $0.0001 |  |

## ベンダー別注意点

- **Anthropic**: JSON出力成功
- **DeepSeek**: JSON出力成功
- **Google**: JSON出力成功
- **Moonshot**: JSON出力成功
- **OpenAI**: JSON出力成功
- **xAI**: JSON出力成功

## Step 3C推奨8体の提案

JSON成立+レイテンシ上位8体:

- **L3** Gemini 3.5 Flash-Lite (Google, 735ms)
- **L6** DeepSeek V3 (L) (DeepSeek, 1094ms)
- **M6** DeepSeek V3 (DeepSeek, 1540ms)
- **M2** GPT-4.1 (OpenAI, 1636ms)
- **L2** GPT-4.1 Mini (OpenAI, 2064ms)
- **L1** Claude Haiku 4.5 (Anthropic, 2565ms)
- **M1** Claude Sonnet 5 (Anthropic, 3449ms)
- **M3** Gemini 3.5 Flash (Google, 3459ms)
