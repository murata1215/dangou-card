# 8/24 本戦 素材（material.md）

> 定義・手法の正は `doc/blog/pipeline.md` を参照。本ファイルは8/24試合固有の数値・年表・物語構成メモ。
> 記事本文は書かない（本サイクルの対象外）。すべて機械集計値＋逐語出典。

対象ログ: `logs/llm/trial_C_l12_r12_20260824/`（12人・12ラウンド・ルールセット確認は`trial_manifest.json`）

## §1. 概要・対戦カード

| 席 | モデル | 初期借入額 | 最終結果 |
|---|---|---|---|
| P01 | L1:Claude Haiku 4.5 | 4,000,000円 | 脱落（R10 FORCED_LIQUIDATION / bankruptcy） |
| P02 | L5:Kimi K2.6 (L) | — | **生還**（¥5,036,313） |
| P03 | L6:DeepSeek V4 Flash (L) | — | 脱落（R12 FORCED_LIQUIDATION / condition_not_met＝生還ライン未達） |
| P04 | L6:DeepSeek V4 Flash (L) | — | 脱落（R12 FORCED_LIQUIDATION / bankruptcy） |
| P05 | L3:Gemini 3.5 Flash-Lite | — | **生還**（¥5,004,647） |
| P06 | L2:GPT-4.1 Mini | — | 脱落（R6 BANKRUPTCY / bankruptcy） |
| P07 | L5:Kimi K2.6 (L) | — | 脱落（R6 FORCED_LIQUIDATION / bankruptcy） |
| P08 | L1:Claude Haiku 4.5 | — | 脱落（R12 FORCED_LIQUIDATION / condition_not_met） |
| P09 | L2:GPT-4.1 Mini | — | 脱落（R6 FORCED_LIQUIDATION / bankruptcy） |
| P10 | L4:Grok 4.3 | — | **生還**（¥2,850,787） |
| P11 | L4:Grok 4.3 | — | **生還**（¥4,464,647） |
| P12 | L3:Gemini 3.5 Flash-Lite | — | **生還**（¥4,969,302） |

（初期借入額はP01のみ`extract.json`から例示掲載。全席分は各`dossiers/PXX.md`§1に記載）

**総括**: 生還5/12。同型モデル対決が2組（P01/P08=Claude Haiku、P03/P04=DeepSeek、P10/P11=Grok、P05/P12=Gemini）
存在し、P03/P04（DeepSeek）は2席とも脱落、P10/P11（Grok）・P05/P12（Gemini）は2席とも生還と、
モデル単位で明暗が分かれた。P01/P08（Claude Haiku）はP01がR10脱落、P08がR12脱落（僅差の条件未達）で対照的。

コスト: 総額 $6.8108 / 1,449 APIコール / トークン合計 8,654,770。
モデル別コスト: `{'claude-haiku-4-5-20251001': $2.4697, 'kimi-k2.6': $1.7858, 'deepseek-v4-flash': $0.1623, 'gemini-3.5-flash-lite': $0.5564, 'gpt-4.1-mini': $0.2260, 'grok-4.3': $1.6106}`

## §3. 年表（ラウンド別市場結果）

出典: `stats.json::market_results`（`MARKET_RESULT`イベント集計）+ `extract_cash_series()`（公開情報層）。
高騰=同一市場に既定人数超の参加が集中し賞金プールが倍増する演出。

| R | M01 | M02 | M03 | 特記 |
|---|---|---|---|---|
| 1 | 参加5/P11勝/114万円 | **高騰** 参加7/P09勝/268万円 | 参加0（空き巣不成立・繰越64万円） | 開幕。M03が唯一の空き巣不成立 |
| 2 | 参加5/P05勝/114万円 | 参加3/P08勝/94万円 | 参加4/P10・P11分配/各84万円 | |
| 3 | **高騰** 参加7/P10勝/268万円 | 参加4/P11勝/104万円 | 参加1/P08勝/74万円 | |
| 4 | 参加4/P05勝/104万円 | 参加5/P03・P08・P10・P11分配/各28.5万円 | 参加3/P02勝/94万円 | 4名分配の激戦 |
| 5 | 参加3/P04勝/94万円 | 参加5/P05勝/114万円 | 参加4/P02勝/104万円 | |
| 6 | 参加3/P10勝/94万円 | 参加3/P03勝/94万円 | 参加5/P02・P08分配/各57万円 | **P06/P07/P09が同時脱落**（資金破綻） |
| 7 | 参加3/P01勝/94万円 | 参加3/P02勝/94万円 | 参加3/P03・P05分配/各47万円 | |
| 8 | **高騰** 参加5/P12勝/228万円 | 参加1/P04勝/74万円 | 参加3/P02勝/94万円 | |
| 9 | 参加4/P01・P12分配/各52万円 | 参加1/P04勝/74万円 | 参加4/P02・P08分配/各52万円 | |
| 10 | 参加3/P11勝/94万円 | 参加4/P04勝/104万円 | 参加2/P05勝/84万円 | **P01が脱落**（資金破綻） |
| 11 | 参加3/P03勝/94万円 | 参加1/P10勝/74万円 | 参加4/P02勝/104万円 | |
| 12 | **高騰** 参加5/P05勝/484万円 | 参加1/P11勝/202万円 | 参加2/P12勝/212万円 | 最終R×3倍市場。**P03/P04/P08が同時脱落** |

サージ市場（高騰）は4回発生（R1-M02, R3-M01, R8-M01, R12-M01）。空き巣不成立（参加0）はR1-M03の1回のみ。

倍掛け（DOUBLE_UP）は23件解決、うち成功/失敗の内訳は各ドシエ§6・`stats.json::double_up_resolved`参照。

## §4. 指標（A/B共通定義。B側検算済み）

| 指標 | 値 |
|---|---|
| pass率 | 342/1156 = 29.6% |
| ミスマッチ(a) target_market vs COMMIT | 553/1140 = 48.5% |
| ミスマッチ(b) 宣言regex vs COMMIT | 62/115 = 53.9% |
| dedup済み交渉決定数 | 1140（events側`NEGOTIATION_ACTION`合計1156、うち16件は`events_only`=自動pass。§3参照） |
| retry分布 | retry0=1433 / retry1=14 / retry2=2 |
| emotion分布 | 疑151 / 楽636 / 焦315 / 奸148 / 怒1 / 哀10 / 平静2 |
| reason_category分布 | — （このフィールドはCycle 8新設。8/24試合には存在しない） |
| キーワード出現数（strategy.reason+current_goal内） | 高騰189 / 倍掛け459 / 契約41 / 借入0 |
| memory status分布 | ok_recovered=39 / ok=70 / rejected_no_memory_key=3（切り詰めあり17件） |
| final_reflection/completion_reflection status | final_reflection/ok=6 / completion_reflection/ok=5 / final_reflection/ok_recovered=1 |
| salvage対象フィールド | emotion=1 / defeat_cause=1 / comment=1（1件のsalvageで3フィールドとも復元） |

**A/Bの比較で注意**: 8/24（A）と8/30（B）は「2世代差」（A→v0.7ベースライン(8/28)→v0.8改訂→B）。
A/Bを直接並べて「v0.8でこう変わった」と語ると2世代分を1世代に帰属させる誤りになる（`pipeline.md`にはない
本試合固有の警告としてここに明記）。ミスマッチ率はA(48.5%/53.9%)→B(34.9%/41.1%)と改善しているが、
これはv0.7→v0.8の変化だけでなくv0.7自体（8/28試合）の影響も混ざっている可能性がある。

**Aで算出不能な指標（既知・意図的な「—」）**:
`contract_propose`/`contract_sign`/`card_trade_*`/`bounty_*` は全0件（D1機能未実装期）、
`reason_category`はフィールド非存在、`DOUBLE_UP`の`outcome_reason`はフィールド非存在、
`CONTRACT_EXPIRED`/`ELIMINATION`イベント型は非存在（`BANKRUPTCY`/`FORCED_LIQUIDATION`のみ）。

## §6. 物語の焦点（脱落順・題材候補）

脱落順（R6同時3名 → R10単独1名 → R12同時3名）＋生還者は最後に描くのが自然な構成。

1. **R6 同時脱落トリオ**: P06(GPT-4.1 Mini・BANKRUPTCY)/P07(Kimi K2.6・FORCED_LIQUIDATION)/
   P09(GPT-4.1 Mini・FORCED_LIQUIDATION)。3席とも「資金破綻」が理由。P06はコスト最小級（$0.109）
   だった一方で最も早く脱落しており、「発言が少ない=見えないリスクを取っていた」という対比が使える。
2. **R10 単独脱落**: P01(Claude Haiku 4.5)。倍掛けの連続没収（R8で94万円全額没収）が致命傷。
   final_reflectionのcommentが自己分析として非常に詳細（987字、salvageなしのok）で、
   「借入額120万円というリスキーな選択」を自ら振り返っている一級の引用素材。
3. **R12 同時脱落トリオ**: P04(DeepSeek V4 Flash・bankruptcy)、P03/P08(condition_not_met＝
   現金は残っていたが生還ライン未達で没収)。特にP03(¥1,368,511没収)・P08(¥336,580没収)は
   「破産ではなく基準未達」という8/24試合特有の脱落理由であり、"あと一歩"の物語が描ける。
4. **生還者5席**: P02(Kimi K2.6・¥5,036,313・最終首位)、P05(Gemini 3.5 Flash-Lite・¥5,004,647)、
   P12(Gemini 3.5 Flash-Lite・¥4,969,302)、P11(Grok 4.3・¥4,464,647)、P10(Grok 4.3・¥2,850,787・
   生還ライン200万円ギリギリの薄氷の生還)。P10は生還者の中で唯一「薄氷」の物語になり得る。

**モデル対決の軸**（§1参照）: DeepSeek(P03/P04)全滅、Grok(P10/P11)・Gemini(P05/P12)全生還、
Claude Haiku(P01/P08)は明暗分かれ、という構図は開幕記事のフックに使える。

## §7. 制作メモ・QA注記

- **パーサ遵守**: 本サイクルの抽出は`llm.response_parser`の公開関数のみを経由（独自JSON抽出なし）。
  検証は`doc/blog/pipeline.md`§4のB側既知値検算（完全一致）をAにも同一ロジックで適用。
- **`trace.py`の既知不具合を回避**: DM宛先キー不一致・匿名放送未対応・リトライ重複未除去
  （詳細は`pipeline.md`§2）。本サイクルの受信DM抽出は`trace.secret`を使わず、`llm_logs`の
  再パース結果を自前で`(round,pid,turn)`突合して構築した。
- **`post_game_reflection`は0件**（両試合共通）。
- **events joinの閉包確認**: 採用決定数(1140) + events_only(16) = NEGOTIATION_ACTION総数(1156) ✅
- **8/24試合固有の脱落理由多様性**: `bankruptcy`（現金不足で強制返済不能）と`condition_not_met`
  （現金はあるが生還ライン未達で没収）の2種が混在する点は、8/30試合（`contract_violation`も追加）との
  比較で言及可能。
- **読み取り専用**: 本ファイル作成にあたり`logs/`への書き込み・LLM API呼び出しは一切発生していない。
