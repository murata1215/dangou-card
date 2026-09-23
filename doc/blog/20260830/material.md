# 8/30 本戦（v0.8）素材（material.md）

> 定義・手法の正は `doc/blog/pipeline.md` を参照。本ファイルは8/30試合固有の数値・年表・物語構成メモ。
> 記事本文は書かない（本サイクルの対象外）。すべて機械集計値＋逐語出典。

対象ログ: `logs/llm/trial_C_l12_r12_v08_20260830/`（12人・12ラウンド・v0.8ルールセット。`trial_manifest.json`参照）

## §1. 概要・対戦カード

| 席 | モデル | 最終結果 |
|---|---|---|
| P01 | L1:Claude Haiku 4.5 | **生還**（¥5,458,511） |
| P02 | L5:Kimi K2.6 (L) | 脱落（R9 FORCED_LIQUIDATION / bankruptcy） |
| P03 | L6:DeepSeek V4 Flash (L) | **生還**（¥6,407,719） |
| P04 | L6:DeepSeek V4 Flash (L) | **生還**（¥5,993,580） |
| P05 | L3:Gemini 3.5 Flash-Lite | 脱落（R12 BANKRUPTCY / bankruptcy） |
| P06 | L2:GPT-4.1 Mini | 脱落（R2 ELIMINATION+FORCED_LIQUIDATION / **contract_violation**） |
| P07 | L5:Kimi K2.6 (L) | **生還**（¥4,247,719） |
| P08 | L1:Claude Haiku 4.5 | 脱落（R9 FORCED_LIQUIDATION / bankruptcy） |
| P09 | L2:GPT-4.1 Mini | 脱落（R9 FORCED_LIQUIDATION / bankruptcy） |
| P10 | L4:Grok 4.3 | **生還**（¥7,656,647・最終首位） |
| P11 | L4:Grok 4.3 | **生還**（¥6,324,647） |
| P12 | L3:Gemini 3.5 Flash-Lite | 脱落（R6 FORCED_LIQUIDATION / bankruptcy） |

**総括**: 生還6/12。8/24試合と同じ席配置（同モデル）だが結果は大きく異なる：
Grok(P10/P11)は両試合とも全生還で再現性あり。DeepSeek(P03/P04)は8/24全滅→8/30全生還と正反対の結果。
Claude Haiku(P01/P08)は8/24明暗分かれ→8/30も明暗分かれ（P01生還・P08脱落）で構図は継続。
GPT-4.1 Mini(P06/P09)は8/24両者R6脱落→8/30もP06がR2最速脱落・P09がR9脱落で「早期に脆い」傾向が継続。

**この試合の最大の特徴**: P06の脱落理由が唯一`contract_violation`（契約違反）。R2の`ACTION_ERROR`
（P01がC_5ea36c40を二重署名しようとして拒否された1件、`pipeline.md`§3参照）とは別件だが、
「契約」が初めて脱落の直接原因になった試合という点で8/24試合との最大の質的差異。
契約関連イベントは`contract_propose`23件・`contract_sign`19件・`CONTRACT_EXPIRED`5件
（8/24試合はいずれも0件／イベント型自体が非存在）。

コスト: 総額 $9.4352 / 1,465 APIコール / トークン合計 12,488,778（8/24試合比 +38.5%コスト、
+44.3%トークン。契約提案・署名という新規行動枠が交渉を長引かせた可能性）。
モデル別コスト: `{'claude-haiku-4-5-20251001': $2.8414, 'kimi-k2.6': $3.1128, 'deepseek-v4-flash': $0.2233, 'gemini-3.5-flash-lite': $0.5983, 'gpt-4.1-mini': $0.2928, 'grok-4.3': $2.3666}`

## §3. 年表（ラウンド別市場結果）

出典: `stats.json::market_results`（`MARKET_RESULT`イベント集計）+ `extract_cash_series()`（公開情報層）。

| R | M01 | M02 | M03 | 特記 |
|---|---|---|---|---|
| 1 | 参加3/P01勝/94万円 | 参加4/P07勝/104万円 | 参加5/P10・P11分配/各57万円 | 開幕 |
| 2 | 参加5/P10勝/114万円 | 参加3/P09勝/94万円 | 参加4/P07勝/104万円 | **P06が契約違反で最速脱落**（cash2,546,250円を保有したまま） |
| 3 | 参加2/P07・P10分配/各42万円 | 参加3/P01勝/94万円 | **高騰** 参加6/P04・P11分配/各124万円 | |
| 4 | **高騰** 参加6/P01勝/248万円 | 参加3/P05勝/94万円 | 参加2/P04勝/84万円 | |
| 5 | 参加3/P01勝/94万円 | 参加4/P04・P07分配/各52万円 | 参加4/P11勝/104万円 | |
| 6 | 参加5/P05勝/114万円 | 参加4/P02・P04分配/各52万円 | 参加2/P11勝/84万円 | **P12が脱落**（資金破綻） |
| 7 | **高騰** 参加6/P03勝/248万円 | 参加4/P07勝/104万円 | 参加0（空き巣不成立・繰越64万円） | ACTION_ERROR: P01がC_5ea36c40二重署名試行→拒否 |
| 8 | **高騰** 参加6/P02・P11分配/各124万円 | 参加2/P03勝/84万円 | 参加2/P10勝/148万円 | |
| 9 | 参加4/P10・P11分配/各52万円 | 参加4/P03勝/104万円 | 参加2/P07勝/84万円 | **P02/P08/P09が同時脱落**（資金破綻） |
| 10 | **高騰** 参加5/P01・P04・P05・P10・P11分配/各45.6万円 | 参加1/P03勝/74万円 | 参加1/P07勝/74万円 | 5名分配の異例の高騰決着 |
| 11 | 参加2/P04勝/84万円 | **高騰** 参加5/P10勝/228万円 | 参加0（空き巣不成立・繰越64万円） | |
| 12 | 参加2/P11勝/212万円 | 参加0（空き巣不成立・繰越192万円） | **高騰** 参加4/P01・P03・P04・P07分配/各148万円 | 最終R×3倍市場。**P05が脱落**（資金破綻） |

サージ市場（高騰）は7回発生（8/24試合の4回より多い）。空き巣不成立は3回（R7-M03, R11-M03, R12-M02。
8/24試合の1回より多い）。

契約失効（CONTRACT_EXPIRED）5件のうち4件が「提案者以外は未署名のまま失効」というパターン
（R2:P11提案→P02未署名、R4:P09提案→P04未署名、R5:P07提案→未署名、R8:P07提案→未署名）。
唯一の例外はR7失効のC_5ea36c40（P01提案→P02未署名で失効。§1のACTION_ERRORと同一契約）。

倍掛け（DOUBLE_UP）は31回選択（8/24試合の34回よりやや少ない）。

## §4. 指標（A/B共通定義。本試合が検算の基準＝完全一致確認済み）

| 指標 | 値 |
|---|---|
| pass率 | 331/1189 = 27.8% |
| ミスマッチ(a) target_market vs COMMIT | 412/1182 = 34.9% |
| ミスマッチ(b) 宣言regex vs COMMIT | 108/263 = 41.1% |
| dedup済み交渉決定数 | 1182（events側`NEGOTIATION_ACTION`合計1189、うち8件`events_only`+1件`llm_only`。`pipeline.md`§3参照） |
| retry分布 | retry0=1462 / retry1=3 |
| emotion分布 | 楽828 / 焦284 / 疑101 / 奸84 / 平静3 |
| reason_category分布 | 関係構築・合意形成367 / 情報収集・様子見200 / 情報発信・牽制146 / 戦略的沈黙113 / 行動枠温存55 / 資金・カード制約75 / 返答待ち9 |
| キーワード出現数（strategy.reason+current_goal内） | 高騰1053 / 倍掛け1367 / 契約652 / 借入11 |
| memory status分布 | ok=74 / ok_recovered=29 / rejected_no_memory_key=4（切り詰めあり3件） |
| final_reflection/completion_reflection status | completion_reflection/ok=5 / final_reflection/ok=6 / completion_reflection/ok_recovered=1 |
| salvage対象フィールド | emotion=1 / defeat_cause=1 / comment=1 |

**「契約」キーワード出現数652件**（8/24試合は41件）は、契約機能が実際に交渉の話題の中心になっていた
ことの裏付け（8/24試合はD1未実装期のため契約自体が存在しない）。

## §6. 物語の焦点（脱落順・題材候補）

1. **R2 最速脱落**: P06(GPT-4.1 Mini・ELIMINATION/FORCED_LIQUIDATION・contract_violation)。
   現金2,546,250円を保有したまま脱落しており、「Free Cash制約による型A決済失敗」という
   v0.8特有の構造要因（`doc/analysis/trial_v08_l12_r12_20260830_analysis.md`で既出）。
   本サイクルのdossierでは`strategy`側の内心も含め再構成済み。
2. **R6 単独脱落**: P12(Gemini 3.5 Flash-Lite・bankruptcy)。8/24試合ではGeminiは2席とも生還していた
   ため、モデル間の再現性が崩れた1例。
3. **R7 契約の伏線**: P01がC_5ea36c40を二重署名しようとして`ACTION_ERROR`で拒否される
   （`NEGOTIATION_ACTION`化されない1件・`pipeline.md`§3の`llm_only`アノマリー）。同契約はP01提案・
   P02未署名のままR7で失効。P01自身は最終的に生還（¥5,458,511）しており、「契約が実らなくても
   個人としては勝ち抜いた」という対比が使える。
4. **R9 同時脱落トリオ**: P02(Kimi K2.6)/P08(Claude Haiku 4.5)/P09(GPT-4.1 Mini)。3席とも
   `bankruptcy`・同一cash_before帯（146,886円/246,886円/246,886円）で、資金繰りの連鎖破綻を示唆。
5. **R12 最終脱落**: P05(Gemini 3.5 Flash-Lite・bankruptcy)。最終ラウンドでの脱落は8/24試合の
   P03/P04/P08(R12)と同様のパターン（土壇場の資金繰り）だが、本試合はP05単独。
6. **生還者6席**: P10(Grok 4.3・¥7,656,647・最終首位)、P03(DeepSeek V4 Flash・¥6,407,719)、
   P11(Grok 4.3・¥6,324,647)、P04(DeepSeek V4 Flash・¥5,993,580)、P01(Claude Haiku 4.5・
   ¥5,458,511)、P07(Kimi K2.6・¥4,247,719)。DeepSeekの8/24全滅→8/30全生還の逆転が最大のフック。

## §7. 制作メモ・QA注記

- **B側検算がこの試合の全指標の基準**: `doc/analysis/trial_v08_l12_r12_20260830_analysis.md`の
  既知値（pass率・dm/broadcast/anon件数・ミスマッチ(a)(b)・retry分布）を`/tmp/cycle94_work/verify_b.py`で
  完全再現した後、同一ロジックで`stats.py`により本ファイルの全指標を算出（`pipeline.md`§4参照）。
- **`completion_reflection`の関数取り違えを訂正**: 前回分析doc §9の記載は誤り
  （`parse_post_game_reflection()`ではなく`parse_final_reflection(..., cause_key="key_factor")`が正）。
  本サイクルでは正しい関数で再パースし、salvage対象フィールド（emotion/defeat_cause/comment）まで
  特定できた。
- **`llm_only`アノマリー**: R7 P01の`contract_sign`二重署名試行（`(7,'P01',3)`キー）は
  `NEGOTIATION_ACTION`イベント化されず、`ACTION_ERROR`イベントとしてのみ記録されている
  （エンジンの正当な拒否。バグではない）。閉包検証の等式には現れないため別枠で記録
  （`pipeline.md`§3参照）。
- **events joinの閉包確認**: 採用決定数(1182) + events_only(8) = 1190 ≠ NEGOTIATION_ACTION総数(1189)。
  この1件の差分が上記`llm_only`（events側に対応イベントなし）に相当する。全体としては
  matched(1181)+mismatched(0)+events_only(8)+llm_only(1) = 1190レコードのうち1189件が
  `NEGOTIATION_ACTION`側に対応する、という関係で整合している。
- **8/24試合との比較時の注意**: A→B間には8/28試合（v0.7ベースライン）を挟む「2世代差」がある
  （`material.md 20260824`§4・`pipeline.md`参照）。本ファイルのA比較記述はいずれも定性的な言及に
  留め、「v0.8で改善した」という断定は避けている。
- **読み取り専用**: 本ファイル作成にあたり`logs/`への書き込み・LLM API呼び出しは一切発生していない。
