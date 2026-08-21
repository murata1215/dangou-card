# 2026-08-20 L1〜L6 R12 試験前 API Console 基準値（BEFORE）

## 目的と記録方針

これは、L1〜L6を各1席投入する Season 2 / 12ラウンド / 1試合の実戦試験に対する、
**試験前（BEFORE）**のProvider Console表示を固定する資料である。

- 取得時点: 2026-08-20 21:58 JST頃
- 出典: ユーザー提供スクリーンショットからの転記。画像自体はrepo内に保存していない。
- この資料はConsole比較専用である。repo内のPhase 1〜3実測を扱う
  `api_cost_estimate_2026-08-20.md` へは混在させない。
- AFTERは同一Console・可能な限り同一表示範囲で採取し、本表との差分と、集計範囲の
  差による比較不能性を併記する。

## BEFORE値

| Provider | 対象 / 表示範囲 | BEFORE値 | AFTER差分の精度・注意 |
|---|---|---|---|
| Anthropic Claude Console | API key `dangou-card`。Costの画面表示期間は転記値からは不明。 | Cost `$9.37`、Last used `2026/08/20` | key単位表示であれば試験差分に比較的近いが、期間表示をAFTER採取時に確認する。 |
| OpenAI API Keys | Project API key `dangou-card`。Monthly spendはproject月次表示。 | Monthly spend `$3.16`、Last used `2026/08/20` | project内の試験外利用を含み得るため、キー単独の実課金と断定しない。 |
| Google AI Studio | Project `STBProject`、`Jul 24 - Aug 20, 2026 / 28日` のproject月次使用量。 | `¥8,706 / ¥10,000`、headroom 約`¥1,294` | L3専用ではなくproject・期間全体。実行直前に残額と試験外消費を人間が確認し、安全ゲートを通す。 |
| Moonshot / Kimi | アカウント残高・当日／月次／累計の画面表示。 | Available Balance `$18.61851`、Today's `$0.06356`、Monthly `$6.31793`、Total `$6.38149`、Recharge `$20.00000`、Voucher `$5.00000` | key単位ではない。試験外利用があればConsole差分を単独費用とみなせない。 |
| xAI | team-levelの30日Credits／累計tokens／requests画面。 | Credits remaining `$17.57`、usage `$2.51`、Tokens `1,132,626`、Requests `808` | 30日・team集計。内部usage-based costと1対1で突合しない。 |
| DeepSeek | アカウント全体のtop-up残高・累計cost／requests／tokens画面。 | Topped-up `$19.25`、Total cost `$0.74`、Requests `2,399`、Tokens `10,536,490` | key・試験単位ではない。画面告知のpeak/off-peak pricing（2026-08-17 00:00 Beijing Time開始）をAFTER評価時に考慮する。 |

## 実行前の必須安全ゲート

1. L1〜L6のmodel_id・価格表・runtime設定を、実送信前に当日の公式Provider料金情報と照合する。
2. `trial_manifest.json` が全6席の `effective_max_output_tokens=2000`、CoT無効、
   `$5/player`・`$40/game`、budget block中断有効を記録していることを確認する。
3. Googleの残額と表示期間を人間が再確認する。過去R12ログに基づくL3再価格計算の
   安全側見積もりがheadroom内に収まらない、または試験外消費を分離できない場合は送信しない。
4. `LLM_BUDGET_BLOCKED` が1件でも発生した場合、上限を変更せず `GAME_ABORTED` として終了する。

## AFTER記入欄

実行完了または中断後、別の結果資料に次を記入する。

- 各ProviderのAFTER値、採取時刻、BEFORE差分
- Console差分と内部usage-based costの差、および集計範囲が異なるため比較不能な項目
- L1〜L6別 Calls / Input / Cached / Output / Thinking / Cost
- 完走／中断、ラウンド別生存者、脱落理由、最終順位・Cash、API error／timeout／retry
