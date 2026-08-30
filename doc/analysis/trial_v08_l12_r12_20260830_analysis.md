# v0.8 本戦（trial_C_l12_r12_v08_20260830）結果分析 — v0.7 比較

対象: `logs/llm/trial_C_l12_r12_v08_20260830/`（実行 2026-08-30、11,863.5秒 / $9.4352 / 1,465コール）
比較対象: `logs/llm/trial_C_l12_r12_20260828/`（v0.7、実行 2026-08-27〜28、12,545.5秒 / $8.90前後 / 1,626コール）

両試合は `game01_seat_map.json` がバイト単位で同一（席⇔モデル対応・seed 1202とも一致）。
P01/P08=L1 Claude Haiku 4.5、P02/P07=L5 Kimi K2.6、P03/P04=L6 DeepSeek V4 Flash、
P05/P12=L3 Gemini 3.5 Flash-Lite、P06/P09=L2 GPT-4.1 Mini、P10/P11=L4 Grok 4.3。
**純粋なレギュレーション差分（v0.7→v0.8: 契約無料化・提案失効・解除条件・倍掛け順序/DOUBLEガード・
公開情報の提示・便益/コスト対称化）の比較として成立する。**

本分析は `game01_events.jsonl`（構造化イベント）と `llm_logs/game01_P##_llm_calls.jsonl`（LLM生I/O）を
読み取り専用で集計した。`target_market`・`reason_category` 等の strategy 系フィールドは
`llm.response_parser.parse_response()` / `extract_json()`（本番コード、テキスト解析のみ・API呼び出しなし）
で再パースした。作業スクリプトは `/tmp/cycle85_work/`（リポジトリ外）に置き、`logs/` は一切書き換えていない。

## 比較要約表

| 指標 | v0.7 | v0.8 | 増減 |
|---|---|---|---|
| 生還者数 | 5/12 (41.7%) | 6/12 (50.0%) | +1人 |
| 総コスト | ~$8.90 | $9.4352 | +6.1% |
| 総LLMコール数 | 1,626 | 1,465 | -9.9% |
| NEGOTIATION_ACTION総数 | 1,370 | 1,189 | -13.2% |
| pass率 | 439/1,370=32.0% | 331/1,189=27.8% | -4.2pt |
| contract_propose | 6 | 23 | ×3.8 |
| contract_sign | 4 | 19 | ×4.75 |
| CONTRACT_EXPIRED | — | 5 | 新設 |
| card_trade_propose | 1 | 0 | -1 |
| card_trade_reject | 4 | 0 | -4 |
| transfer / repay | 3 / 6 | 0 / 0 | 消滅 |
| anonymous_broadcast | 0 | 4 | 新規使用 |
| DOUBLE_UP_CHOSEN | 18 | 26 | +8 |
| DOUBLE_UP成功/没収 | 6/12 | 12/14 | 成功率33%→46% |
| DOUBLE_UP_BLOCKED | — | 1 | 新設ガードが発火 |
| 市場高騰(surged) | 6件 | 7件 | +1件 |
| 空き巣不成立(参加0・carryover) | 0件 | 3件 | 新規発生 |
| ACTION_ERROR | 0 | 1 | +1 |
| MANDATORY_REPAY_FAILED | 2 | 4 | +2 |
| FORCED_LIQUIDATION | 5 | 5 | 同数 |
| 契約違反脱落 | 0 | 1（P06 R2） | 新規種別 |
| target_market(strategy)とCOMMIT不一致率 | 691/1,359=50.8% | 412/1,182=34.9% | -15.9pt |
| broadcast/dm宣言文とCOMMIT不一致率（v0.7と同一regex定義） | 125/243=51.4% | 108/263=41.1% | -10.3pt |
| reason_category（strategy JSONの新フィールド） | — | 導入・全交渉手番の約9割で使用 | 新設 |

---

## 1. 最終結果

### v0.8

| 席 | モデル | 借入額 | 最終Cash | 結果 |
|---|---|---|---|---|
| P01 | L1:Claude Haiku 4.5 | 700万 | 5,458,511円 | **生還** |
| P02 | L5:Kimi K2.6 (L) | 400万 | 0円 | 脱落(R9, bankruptcy) |
| P03 | L6:DeepSeek V4 Flash (L) | 400万 | 6,407,719円 | **生還** |
| P04 | L6:DeepSeek V4 Flash (L) | 600万 | 5,993,580円 | **生還** |
| P05 | L3:Gemini 3.5 Flash-Lite | 120万 | 0円 | 脱落(R12, bankruptcy) |
| P06 | L2:GPT-4.1 Mini | 300万 | 0円 | 脱落(R2, contract_violation) |
| P07 | L5:Kimi K2.6 (L) | 400万 | 4,247,719円 | **生還** |
| P08 | L1:Claude Haiku 4.5 | 400万 | 0円 | 脱落(R9, bankruptcy) |
| P09 | L2:GPT-4.1 Mini | 400万 | 0円 | 脱落(R9, bankruptcy) |
| P10 | L4:Grok 4.3 | 500万 | 7,656,647円 | **生還** |
| P11 | L4:Grok 4.3 | 500万 | 6,324,647円 | **生還** |
| P12 | L3:Gemini 3.5 Flash-Lite | 120万 | 0円 | 脱落(R6, bankruptcy) |

生還6人：P10, P03, P11, P04, P01, P07（生還率50.0%）。脱落は R2(1) / R6(1) / R9(3) / R12(1) に分散。
`FORCED_LIQUIDATION` 5件（P06 R2 contract_violation, P12 R6 bankruptcy, P02/P08/P09 R9 bankruptcy 同時）
+ `BANKRUPTCY`（settlement前段階のR12・P05）1件で計6脱落。

### v0.7（既存分析からの再掲・比較用）

生還5人：P01, P08, P11, P04, P12（生還率41.7%）。脱落7人（P02 R12 bankruptcy, P03 R12 condition_not_met,
P05 R6 bankruptcy, P06 R12 condition_not_met, P07 R12 bankruptcy, P09 R11 bankruptcy, P10 R12 condition_not_met）。
v0.7 は脱落原因の多くが「R12時点で借金完済したが現金200万円未満」の `condition_not_met`（3件）だったのに対し、
v0.8 は `condition_not_met` による脱落が0件で、全脱落が `bankruptcy`（返済不能・強制清算、4件）または
`contract_violation`（1件、P06）に置き換わった。生還率は+8.3pt改善したが、これは脱落理由の質的変化
（「僅差の資産不足」から「支払不能」への転換）を伴っており、単純な生存率改善と読むには留保が必要。

**脱落ラウンド分布**: v0.7 = R6(1)/R11(1)/R12(5) と後半集中。v0.8 = R2(1)/R6(1)/R9(3)/R12(1) と中盤(R9)に
分散。R9で3人同時強制清算（P02/P08/P09、いずれも `required=381,130` の同額義務返済に失敗）が発生しており、
同一ラウンドでの連鎖破産が v0.8 の特徴的パターン。

**借入額との対応**: v0.8生還者の借入額は700万/400万/600万/400万/500万/500万（平均516.7万）、脱落者は
400万/120万/300万/400万/400万/120万（平均290万）。v0.7同様、借入額が多い（初期資金が厚い）席ほど
生還しやすい傾向は継続。

---

## 2. 正式契約

### 全体件数

| 指標 | v0.7 | v0.8 |
|---|---|---|
| 提案(contract_propose) | 6 | 23 |
| 署名(contract_sign) | 4 | 19 |
| 失効(CONTRACT_EXPIRED) | —（イベント自体が存在しない） | 5 |
| 型A執行(TYPE_A_EXECUTION) | 1 | 1 |
| 型A不能(TYPE_A_FAILURE) | —（イベント自体が存在しない） | 1 |
| 型B違反(TYPE_B_VIOLATION) | 0 | 0 |
| ACTION_ERROR(契約関連) | 0 | 1（P01 R7、同一契約への二重署名エラー） |

v0.8では提案23件のうち19件が署名（片務含む、当事者2〜3名の複数当事者契約1件: `C_4ec0e527` P09/P03/P04）
され、5件が `CONTRACT_EXPIRED`（提案者以外が未署名のまま失効）した。内訳整合: 23提案の宛先ベースで
19署名+5失効=24 > 23提案数となるのは、`C_4ec0e527`（3当事者契約、P09提案・P03署名→1件のCONTRACT_EXPIRED）
のように1提案に対して署名イベントと失効イベントが両方発生するケース（部分署名のまま失効）を含むため。
v0.7は6提案のうち4件成立・不成立2件（うち `C_750591ab` は履行不可能な条件を仕込んだ一方的提案）で、
「失効」という独立イベント種別自体が存在しなかった（提案は無期限に有効なまま試合終了を迎えていた）。

型A執行はv0.7・v0.8とも1件ずつ成功しているが、v0.8では別に**型A不能（TYPE_A_FAILURE）が1件発生**しており、
これがP06の契約違反脱落の直接原因（後述）。

**契約提案が23件（v0.7の3.8倍）に増えた背景**: v0.8で `contract_fee` が0円（v0.7でも実は0円設定だったが、
本サイクルの直前 サイクル9.0 で確認した通り `contract_fee` はもともと `baseline_v1_s2()` で0固定であり、
発行料自体はv0.7からv0.8への変更点ではない）。実際の変化は、negotiationフェイズのuser_promptに
「contract_proposeの雛形」がその場の生存者・ラウンドから機械的に埋めた具体例として提示されるようになった
プロンプト改善（P06のR1 user_promptで実測）と、契約解除条件・提案失効という「試しに出しても損しない」
仕組みが整ったことの両方が寄与したと考えられる。

### P06 深掘り（R1〜R2 全行動 + R2交渉ログ）

P06（L2:GPT-4.1 Mini、25コールのみで脱落＝12席中最少）はR1交渉フェイズ（turn1〜10）で以下の行動を取った：

1. turn1: `contract_propose`（P07宛、`C_84b5947d`。義務: P07→R1 M01不参加/P06→R2にP07へ20万円支払い）
2. turn2: broadcast（M03参加を宣言）
3. turn3: pass
4. turn4: broadcast（M03参加を再宣言）
5. turn5: broadcast（M03で強いカードを使うと宣言）
6. turn6: pass
7. turn7: dm→P03（M02参加を宣言）
8. turn8: dm→P03（同内容繰り返し）
9. turn9: pass
10. turn10: broadcast（M02参加・P07との契約に言及）
11. commit: `market_commit M02 ONE_PAIR`

R1中に `C_84b5947d` はP07に署名され成立（契約成立イベント `contract_sign` turn3）。この契約の義務は
「P06→R2にP07へ200,000円を type_a_payment で支払う」というもの。

R2（turn1〜10）ではP06は一貫して「契約支払い20万円を確実に履行」「Entry Fee・強制返済を賄いつつ安全策」
という `reason`/`current_goal` を繰り返し記述しながら、実際のアクションは **broadcast×2・dm×1・pass×7** の
みで、`contract_cancel` も `repay` も `transfer` も一度も選択していない。R2 commitは `market_commit M03
HIGH_CARD`。つまりP06は「型Aの200,000円支払いはR2決済フェイズで自動執行される」と理解しており（実際
type_a_paymentはプレイヤー操作を必要としないAtomic執行）、解除や代替手段を検討した形跡はなかった。

R2決済フェイズで実際に発生したのは:
```
TYPE_A_FAILURE round=2 step=5: player_id=P06, reason="Atomic execution failed - insufficient free cash"
ELIMINATION round=2 step=7: player_id=P06, reason="contract_violation"
FORCED_LIQUIDATION round=2 step=8: player_id=P06, reason="contract_violation",
  cash_before=2,546,250, debt_before=2,791,250, debt_repaid=2,546,250,
  bad_debt=245,000, cards_destroyed=10
```
P06はR2決済時点で **cash 2,546,250円を保有**していながら、`free_cash`（型A決済に充当可能な余剰現金）
が200,000円に届かず、契約不履行（`contract_violation`）として即時強制清算・脱落となった。総現金の
多寡と支払い可否が乖離する典型例であり、直前サイクル（9.0）の Free Cash 依存箇所調査で指摘した
「現金は潤沢でも free_cash が枯渇すると型A決済が失敗する」構造上の脆さが、v0.8最初の本戦で即座に
実例として現れたことになる。P06自身の交渉ログには free_cash という語自体への言及がなく、
「なぜ支払えなかったか」を自覚しないまま脱落した可能性が高い。

---

## 3. カードトレード

| 指標 | v0.7 | v0.8 |
|---|---|---|
| 提案(card_trade_propose) | 1 | **0** |
| 拒否(card_trade_reject) | 4 | 0 |
| 成立(card_trade_accept) | 0 | 0 |

v0.8では `card_trade_*` 系アクションが**完全に0件**（提案すらされなかった）。v0.7は唯一の提案（P03提案、
4人に対しO_ce40ca33で個別トレードオファー）が全員に拒否された1件のみで、v0.7でも「実質機能していない」
と結論づけられていたが、v0.8ではその提案行為自体が消えた。契約提案が23件へ急増した一方でカードトレード
提案が0件になったのは、v0.8のプロンプトで `contract_propose` の雛形が具体的に提示される一方、
`card_trade_propose` には同等の雛形提示が無い（`de_accept・card_trade_reject（提案されているトレードなし）`
という定型文のみ）ことが一因と考えられる（項目10で仮説として整理）。

---

## 4. 倍掛け（DOUBLE_UP）

| 指標 | v0.7 | v0.8 |
|---|---|---|
| DOUBLE_UP_CHOSEN | 18 | 26 |
| DOUBLE_UP_RESOLVED | 18 | 26 |
| 成功(success) | 6 | 12 |
| 没収(forfeit) | 12 | 14 |
| DOUBLE_UP_BLOCKED | 0 | 1 |
| 成功率 | 33.3% | 46.2%（resolved中） |

v0.8で新設された `outcome_reason` フィールドにより、没収の内訳が分解できるようになった：
- `no_win`（次ラウンド市場で全く勝てなかった）: forfeit 14件中13件
- `solo_only_win`（勝ったが単独参加＝独占勝利のみで、DOUBLEガード対象外）: forfeit 14件中1件
  （P03、R9選択→R10 solo_only_win で没収。単独勝利＝総取りは成立しているが倍掛け対象外というルール
  が実戦で初めて発火した）

`DOUBLE_UP_BLOCKED` はR6、P05（`eligible_prize=1,140,000`, `cash=1,217,043`, `min_repay=109,345`）で発火。
最低返済額を確保できないと倍掛けを選択させないガードが機能した実例であり、直後（同R6決済フェイズ）に
P05は継続して `MANDATORY_REPAY_FAILED` こそ発生していないが、R6は乗り切ったもののR12で最終的に
`BANKRUPTCY` に至っている（=ガードは「その場の即死」は防いだが、長期的な資金枯渇までは防げなかった）。

v0.7には `outcome_reason` フィールドが存在せず、成功/没収の理由分解はできない（旧 `from_solo_market`
フィールドは常にFalseで実質死んだフィールドだったことが別サイクルの調査で判明済み）。

---

## 5. 市場（高騰・空き巣・宣言と実COMMITのミスマッチ）

| 指標 | v0.7 | v0.8 |
|---|---|---|
| 高騰(surged=true) | 6件（R2/M01, R3/M03, R4/M02, R9/M03, R10/M02, R11/M01） | 7件（R3/M03, R4/M01, R7/M01, R8/M01, R10/M01, R11/M02, R12/M03） |
| 参加者0・carryover発生 | 0件 | 3件（R7/M03=64万円繰越, R11/M03=64万円繰越, R12/M02=192万円繰越） |
| 参加人数(全36市場の平均) | — | v0.8平均 3.2人/市場（v0.7同水準） |

v0.8で新たに「空き巣狙いが集まりすぎて誰も来ない市場」（参加者0人・賞金が次ラウンド繰越）が3件発生した。
特にR11/M03とR12/M03は連続する「M03を避ける」動きの結果であり、R12は最終ラウンドの3倍ボーナスがM03に
繰り越された状態で決着（実際にはR12決済でM03に4人が参加し高騰込みで解消：`total_pool=5,920,000`）。

### 宣言と実COMMITのミスマッチ率（2通りの定義で算出）

**(a) strategy JSONの `target_market` フィールド と 同ラウンドの `COMMIT.market_id` の不一致**
（v0.8で新たに `parse_response()` 経由で再パース可能になった、負担コストの軽い内部宣言）

| | v0.7 | v0.8 |
|---|---|---|
| n | 1,359 | 1,182 |
| 一致 | 668 | 770 |
| 不一致 | 691 | 412 |
| 不一致率 | 50.8% | **34.9%** |

**(b) broadcast/dmメッセージ本文中の市場参加宣言（正規表現抽出）と 同ラウンドの `COMMIT.market_id` の不一致**
（`doc/analysis/l12_r12_20260828_facts.md` §8-1 と**完全に同一の抽出方法**：他プレイヤーへの公開/個別発言から
「M0[1-3]に参加/行く/勝負する」等の宣言を正規表現で抽出。v0.7側の 116/230=50.4% は本節と定義が異なる
点に注意 — facts.mdの `230件` は「対応する実COMMITがある宣言のみ」を分母にしており、本節ではv0.7・v0.8
両方を同一コードで再集計し直したため件数がわずかに異なる = 243件）

| | v0.7 | v0.8 |
|---|---|---|
| n | 243 | 263 |
| 一致 | 118 | 155 |
| 不一致（実COMMIT無しを含む） | 125 | 108 |
| 不一致率 | 51.4% | **41.1%** |

いずれの定義でもv0.8は不一致率が10〜16pt低下しており、「宣言と実行の乖離」（v0.7で確認された欺瞞/
心変わりの多さ）はv0.8で明確に縮小した。ラウンド別ではv0.8の乱れが大きいのはR6・R8・R9（宣言数の
半分前後が不一致）で、これは高騰・DOUBLE_UP集中ラウンドと重なり、勝敗が読みにくい局面ほど宣言が
翻りやすい傾向は両バージョン共通。

---

## 6. 交渉の使い方

| 指標 | v0.7 | v0.8 |
|---|---|---|
| NEGOTIATION_ACTION総数 | 1,370 | 1,189 |
| dm | 431 | 498 |
| broadcast | 476 | 314 |
| pass | 439 (32.0%) | 331 (27.8%) |
| anonymous_broadcast | 0 | 4 |
| bounty_post/cancel | 0 | 0 |
| transfer | 3 | 0 |
| repay | 6 (成功4/失敗2) | 0 |

pass率は32.0%→27.8%へ4.2pt低下し、dm比率が上昇（broadcast減・dm増）。パス以外の行動がより
具体的な個別交渉（dm）に寄っている。一方、v0.7で使われていた `transfer`（現金送金）・`repay`（自主返済）
は v0.8で**0件**になった。v0.8では正式契約の型A決済が「宣言不要の自動履行」であるため、v0.7的な
「口約束→個別送金」という迂回ルートの必要性が薄れた可能性がある一方、返済に関しては
`MANDATORY_REPAY_FAILED` が v0.7の2件→v0.8の4件へ増えており、自主的な `repay` が使われなくなったこと
自体が財務悪化に無関係とは言い切れない（項目8で再論）。

新設の `anonymous_broadcast` は4件（P07×3、P02×1）使用された。匿名通信という新機能は使われたが、
サンプルが少なく効果測定は困難。

### reason_category分布（v0.8新設フィールド、v0.7は該当フィールドが存在しない="—"）

`strategy.reason_category` は交渉/コミット/借入選択フェイズの回答に任意で含められる新フィールド
（プロンプトの選択肢: 情報収集・様子見／戦略的沈黙／返答待ち／資金・カード制約／行動枠温存／
関係構築・合意形成／情報発信・牽制／その他）。`parse_response()` で再パースした結果:

| reason_category | 件数 | 割合 |
|---|---|---|
| 関係構築・合意形成 | 367 | 39.0% |
| 情報収集・様子見 | 200 | 21.2% |
| 情報発信・牽制 | 146 | 15.5% |
| 戦略的沈黙 | 113 | 12.0% |
| 資金・カード制約 | 75 | 8.0% |
| 行動枠温存 | 55 | 5.8% |
| 返答待ち | 9 | 1.0% |

「関係構築・合意形成」が突出して多く、契約提案急増（項目2）と整合する。席別ではP09（80件中80件が
「関係構築・合意形成」、それ以外0）のように単一カテゴリに偏る席がある一方、P01・P04・P08は
「戦略的沈黙」を多用（passの多用と対応）するなど、モデルごとの傾向差が見える。

---

## 7. 新表示の利用痕跡

`strategy.reason` + `strategy.current_goal` 中のキーワード出現回数（`parse_response()` 再パース、負ラウンド重複含む延べ数、席別）:

| キーワード | v0.7 (延べ) | v0.8 (延べ) |
|---|---|---|
| 高騰 | 229 | 624 |
| 倍掛け | 394 | 693 |
| 契約 | 121 | 389 |
| 借入 | 9 | 22 |

いずれのキーワードもv0.8で顕著に増加（「高騰」2.7倍、「倍掛け」1.8倍、「契約」3.2倍）しており、
公開情報の提示（市場高騰しきい値・倍掛け残高・契約状態の可視化）が strategy 記述の語彙に反映されている
ことが確認できる。特に「契約」への言及がv0.7比3.2倍というのは、実際の契約提案数の増加（3.8倍）と
方向が一致する。

通知イベント（`CONTRACT_EXPIRED` 等）直後の行動変化は、5件のCONTRACT_EXPIRED発生ラウンドのうち
R2（`C_732ef481`失効、P11提案）で、次のP11のnegotiation応答（R3）に「契約失効」等の直接的な言及は
確認できなかった（`reason`にはM03/競合状況の話のみ）。件数が5件と少なく、通知への明示的反応の
有無を統計的に判定するにはサンプル不足。

---

## 8. 破産と財務

### v0.8のラウンド別脱落

| R | 種別 | 対象 | cash_before | debt_before | bad_debt |
|---|---|---|---|---|---|
| 2 | FORCED_LIQUIDATION(contract_violation) | P06 | 2,546,250 | 2,791,250 | 245,000 |
| 6 | FORCED_LIQUIDATION(bankruptcy) | P12 | 77,043 | 765,411 | 688,368 |
| 9 | FORCED_LIQUIDATION(bankruptcy)×3同時 | P02, P08, P09 | 各146,886/246,886/246,886 | 各1,524,520 | 各1,377,634/1,277,634/1,277,634 |
| 12 | BANKRUPTCY | P05 | 35,875 | 117,795 | 81,920 |

R9の3人同時強制清算は全員 `required=381,130` の同一必要返済額で `MANDATORY_REPAY_FAILED` を記録した
直後に発生しており、同一ラウンドの利払い・強制返済スケジュールが複数席に同時に牙を剥いた形。
`MANDATORY_REPAY_FAILED` はv0.8で4件（R6:P12, R9:P02/P08/P09）、v0.7は2件（R6:P05, R11:P09）と倍増。

「⚠ 今Rで市場賞金…」等のFinance見込み警告について: `user_prompt` を横断grepしたが、`⚠`記号や
明示的な「今Rで市場賞金」という定型フレーズを含む警告文言は今回のv0.8プロンプトには確認できず
（該当パターンなし）。財務逼迫の情報は `reason_category`「資金・カード制約」使用（75件、P03が47件と
突出）や `strategy.reason` 中の「契約支払いを確実に履行」「Entry Fee」等の言及として間接的に現れている。

---

## 9. 技術（JSON成功率・エラー・費用）

### JSON成功率（`parse_response()`/`extract_json()` 再パース、全フェイズ）

| 席 | モデル | v0.7 | v0.8 |
|---|---|---|---|
| P01 | Claude Haiku 4.5 | 133/144=92.4% | 138/148=93.2% |
| P02 | Kimi K2.6 | 143/145=98.6% | 107/112=95.5% |
| P03 | DeepSeek V4 Flash | 143/159=89.9% | 145/148=98.0% |
| P04 | DeepSeek V4 Flash | 141/151=93.4% | 143/148=96.6% |
| P05 | Gemini 3.5 Flash-Lite | 72/72=100.0% | 146/146=100.0% |
| P06 | GPT-4.1 Mini | 147/147=100.0% | 25/25=100.0% |
| P07 | Kimi K2.6 | 145/146=99.3% | 149/151=98.7% |
| P08 | Claude Haiku 4.5 | 137/149=91.9% | 101/109=92.7% |
| P09 | GPT-4.1 Mini | 135/135=100.0% | 109/109=100.0% |
| P10 | Grok 4.3 | 149/150=99.3% | 148/148=100.0% |
| P11 | Grok 4.3 | 148/148=100.0% | 148/148=100.0% |
| P12 | Gemini 3.5 Flash-Lite | 145/145=100.0% | 73/73=100.0% |

再パース値は `trial_C_report.md` 記載のJSON率（例P01: 146/148=99%）とやや異なる。これは report.md の
JSON率が negotiation/commit/loan_choice フェイズのみを対象とするのに対し、上表は reflection・
double_up・final_reflection等**全フェイズ**を含めて `extract_json()` が None を返した件数まで
失敗計上しているため（reflectionフェイズの `rejected_no_memory_key` 等、ゲーム進行上は許容される
"柔らかい失敗" も含む）。negotiation/commit/loan_choiceに限定した集計は report.md の値と一致することを
確認済み。

**ACTION_ERROR**: v0.8=1件（P01、R7、`contract_sign` を同一契約`C_5ea36c40`に対して二重送信、
`ValueError`）。v0.7=0件。

**retry_count分布**: v0.8 = retry0:1,462 / retry1:3。v0.7 = retry0:1,674 / retry1:16 / retry2:1。
v0.8はリトライ発生率が明確に低い（3/1,465=0.2% vs 17/1,691=1.0%）。

### memory抽出失敗・切り詰め・salvage（`honsou_l12_r12_v08_20260830.log` の警告を player/round に紐付け）

`extract_memory_with_status()` / `normalize_memory_with_truncation()`（本番関数、max_chars=3000）で
reflectionフェイズ全レコードを再パースし、実行時ログの警告4+3+1=8行と突合した:

| 種別 | 実行時ログの記述 | 特定した席・R |
|---|---|---|
| rejected_no_memory_key | ×4 | P05 R5, P12 R1, P12 R2, P12 R5（うちP12 R1→R2は連続で「fallback 2 rounds in a row」と実行時ログに明記） |
| truncated | 3009→2996 chars | P04 R2 |
| truncated | 3105→2996 chars | P03 R9 |
| truncated | 3031→3000 chars | P03 R11 |
| salvage(final_reflection相当) | `salvaged fields=['emotion','defeat_cause','comment']` | 特定不能（下記注） |

memory失敗4件・切り詰め3件は完全に一意に特定できた（件数・文字数とも実行時ログと1対1で一致）。
最後の1件（salvage）については、`parse_final_reflection()` を全席の `final_reflection` フェイズ
（6席）に対して再実行したところ全席 `status=ok, salvaged=[]` で該当なしだった。近い事例として
`parse_post_game_reflection()`（`completion_reflection` フェイズ）でP03が `status=ok_recovered,
salvaged=['emotion','comment']` を記録したが、実行時ログの `defeat_cause` を含む3フィールド構成とは
一致しない。**この1件のみ、静的な再パースでは実行時ログの文言と完全一致する発生源を特定できなかった**
（P03のcompletion_reflectionが最も近い候補だが、フィールド構成の相違から確証は持てない。原因は不明）。

### 費用（席別・モデル別）

| モデル | v0.7合計 | v0.8合計 |
|---|---|---|
| Claude Haiku 4.5 (P01+P08) | $2.9043 | $2.8414 |
| Kimi K2.6 (P02+P07) | $2.8875 | $3.1129 |
| DeepSeek V4 Flash (P03+P04) | $0.1863 | $0.2233 |
| Gemini 3.5 Flash-Lite (P05+P12) | $0.4879 | $0.5983 |
| GPT-4.1 Mini (P06+P09) | $0.5259 | $0.2928 |
| Grok 4.3 (P10+P11) | $1.9125 | $2.3666 |
| **合計** | **$8.9044** | **$9.4352** |

平均入力トークン/コール（席別）はv0.8で軒並み増加している（例: P01 4,048→5,159、P07 8,371→10,175、
P02 8,384→10,030）。これは v0.8 で追加された公開情報の提示（市場高騰しきい値・倍掛け残高・契約
一覧・雛形提示）がプロンプトを恒常的に肥大化させたことの直接的な帰結であり、総コスト+6.1%の主因は
「コール数減（-9.9%）」を「1コールあたり入力トークン増」が上回ったためと考えられる。

---

## 10. 総括

| # | v0.8の狙い | 数字に出たか | 仮説（未達の場合） |
|---|---|---|---|
| 1 | 契約の活性化（無料化・雛形提示） | **○** 提案6→23、署名4→19、reason_category「関係構築・合意形成」が最頻値、「契約」語彙3.2倍 | — |
| 2 | 提案失効・解除条件による損失回避 | **△** CONTRACT_EXPIRED 5件は機能したが、`contract_cancel` は0件使用のまま（P06の深掘りで確認した通り、義務者側からの能動的解除の発想自体が観測されなかった） | プロンプト: 「解除できる」という選択肢の提示が失効通知より目立たない可能性 |
| 3 | 倍掛け順序・DOUBLEガード | **○** BLOCKED 1件・solo_only_win没収1件がいずれも初回本戦で実発火。ガード自体は機能したが、P05はブロック後もR12で最終的に破産しており、単発の即死回避が長期資金繰りまで保証しないことが分かった | — |
| 4 | 公開情報の提示による情報格差縮小 | **○** target_marketミスマッチ率50.8%→34.9%、宣言文ミスマッチ率51.4%→41.1%といずれも改善。キーワード出現も全て増加 | — |
| 5 | 便益/コスト対称化 | **△** transfer/repayが0件化し、代わりにMANDATORY_REPAY_FAILEDが2→4件に増加。「対称化」で自主返済のインセンティブが変わった可能性はあるが、強制清算増加という副作用も観測された | ルール: 型A自動決済への依存が高まり、任意のrepayを使う動機（利息軽減等）が相対的に弱まった可能性。要追加計測 |
| 6 | カードトレードの活性化（間接効果として期待） | **×** 提案が1→0に減少。「機能しなかった」というよりむしろ「試みられなくなった」 | プロンプト: contract_proposeには雛形例が具体的に提示される一方、card_trade_proposeには同等の誘導が無い（定型文「提案されているトレードなし」のみ）。モデル特性: 契約という選択肢が提示されたことで、モデルがcard_tradeを検討候補から外した可能性 |
| 7 | ACTION_ERROR等の技術的失敗低減 | **△** ACTION_ERROR 0→1（P01の二重署名）、retry率は1.0%→0.2%へ改善したが、reflection系の「柔らかい失敗」（memory rejected 4件・truncated 3件）は依然として発生 | — |
| 8 | 生存率の改善 | **○（表面上）** 41.7%→50.0%。ただし脱落理由の質がcondition_not_met（僅差の資産不足）からbankruptcy中心へ転換しており、「際どく落ちる」から「支払不能で落ちる」への変化を伴う | — |

**次に測るべきこと**:
1. `contract_cancel` が0件のまま推移するかを次の本戦でも確認し、0件が継続する場合はプロンプトへの
   解除選択肢の明示を検討する根拠とする。
2. card_trade系アクションが2試合連続で実質0件（提案1件→0件）になったことを踏まえ、契約とカード
   トレードのどちらが優先的に選ばれているかをプロンプト内の提示順・雛形有無で条件を分けて検証する。
3. Free Cash起因の型A不能（P06のケース）が偶発的なものか構造的なものかを、複数本戦での再現率で
   確認する（サイクル9.0のFree Cash依存箇所調査と接続）。
4. R9の3人同時強制清算のような「同一ラウンド同一必要額での連鎖破産」が借入額設計や利息スケジュール
   の副作用でないかを、借入額分布と返済スケジュールの数値的な突き合わせで検証する。

---

## 付記: 集計方法・再現性

- イベント集計: `/tmp/cycle85_work/agg_events.py`（`game01_events.jsonl` 読込・`event_type`/`data.action`別集計、
  BANKRUPTCY/FORCED_LIQUIDATION/ELIMINATION/SURVIVAL_CHECK/契約/カードトレード/DOUBLE_UP/MARKET_RESULT/
  COMMIT/ACTION_ERROR等の全文ダンプ）
- strategy系再パース: `/tmp/cycle85_work/parse_llm_logs.py`（`llm.response_parser.parse_response()` で
  loan_choice/negotiation/commitを、`extract_json()` でdouble_up/reflection/completion_reflection/
  final_reflectionをパース。target_marketとCOMMIT.market_idの突合、reason_category集計、キーワード集計、
  broadcast/dmメッセージ本文からの市場宣言regex抽出は `doc/analysis/l12_r12_20260828_facts.md` §8-1と
  同一の正規表現を使用）
- memory系の紐付け: `llm.response_parser.extract_memory_with_status()` / `normalize_memory_with_truncation()`
  / `parse_final_reflection()` / `parse_post_game_reflection()` を対話的に実行し、
  `honsou_l12_r12_v08_20260830.log` の警告文言と件数・数値が一致する記録を特定
- いずれもテキスト解析のみでLLM API呼び出しは0件。作業スクリプトはリポジトリに含めていない
  （`/tmp/cycle85_work/` に配置、`git status` に現れない）
