# FINAL_REFLECTION 実API再試験（L1・L6のみ, 3000-token化+parser救済強化後）

- ログ: `logs/smoke/final_reflection_fr_smoke2_20260822/`
  （`calls.jsonl`, `manifest.json`, `real_llm_calls.jsonl`, `report.md`, `_preflight/`）
- run_id: `fr_smoke2_20260822`（cycle 1 の `fr_smoke_20260822` ディレクトリは無改変のまま温存）
- 対象: `--cases C1,C6`（cycle 1 のNO-GO原因だった2件のみ。L2〜L5は再試験不要と判断し対象外）
- 実効 output 上限: `max_tokens_override=3000`（`GameConfig.final_reflection_max_tokens`、旧値1000から引き上げ。プロンプト側は「800〜1500字程度で簡潔に」と誘導するのみで、3000という数値自体はプロンプトに一切出さない）
- 予算キャップ: `--max-cost-per-call 0.03` / `--max-cost 0.05` / `--max-calls 2`（`HARD_MAX_CALLS=6`は不変の絶対上限）
- コスト: 計画worst_case合計 **$0.02025** → 実測合計 **$0.009883**（上限$0.05以内）

## 本サイクルの変更点（要約）

1. **`engine/config.py`**: `final_reflection_max_tokens: 1000 → 3000`。他フェイズのmax_tokensには
   影響しない（`llm/llm_agent.py:final_reflect()`の`max_tokens_override`経由のみ使用）。
2. **`llm/prompt_builder.py`**: `build_final_reflection_prompt()`のガイダンスに「800〜1500字程度で
   簡潔に」を追加、`comment`内の改行は`\n`エスケープする旨を明記。既存の`{max_chars}字以内`文言は
   そのまま温存（`test_prompt_respects_final_reflection_max_chars`が参照）。
3. **`llm/response_parser.py`**: `parse_final_reflection()`の救済ティアを全面刷新。
   `_recover_string_field_bounded()`（境界を守るスキャン、`emotion`/`defeat_cause`向け）と
   既存の`_recover_string_field()`（末尾までの貪欲スキャン、末尾が切断された`comment`向け）を
   併用し、strict JSON解析失敗時も3キーを独立に救済する。ラッパー全損時も生JSONを漏らさず
   プレーンテキスト化する新ステータス`ok_plaintext_wrapper`を追加。どのキーが救済されたかを
   示す`salvaged`フィールドを新設し、`engine/game.py`のFINAL_REFLECTIONイベントと
   `llm/llm_agent.py`のログへ追加（既存キーの型・意味は無変更）。
4. **`scripts/final_reflection_smoke.py`**: `FINAL_REFLECTION_MAX_TOKENS`をconfig由来に変更、
   `--cases`フィルタを追加（`resolve_memory_samples()`も非prefix部分集合に対応するよう修正）、
   `finish_reason`/`truncated_by_provider`/`salvaged`をcalls.jsonl・report.mdへ記録。

## API-freeゲート結果（実API呼び出し前に自動実行、1つでも失敗したら実APIへ進まない設計）

| Gate | 内容 | 結果 |
|---|---|---|
| G1 | `pytest tests/test_final_reflection.py -q` | **51 passed**（既存41 + 新規10） |
| G2 | `pytest tests/test_memory.py -q` | **64 passed**（`_recover_string_field`共有経路に無回帰） |
| G3 | `pytest tests/ -q`（全体回帰） | **859 passed**（既存849 + 新規10、失敗0） |
| G4 | `--parser-selftest`: C1/C6形状を追加した拡張セルフテスト | **PASS**（旧5チェック+新6チェック=11/11。C1形状は`emotion=怒 defeat_cause='初期借入額の過小判断' comment`3キー救済、C6形状は`emotion=哀 defeat_cause='初動の借入額設定ミス' comment`3キー救済を確認） |
| G5 | `--dry-run --cases C1,C6` | **PASS**（`calls_attempted=0`2/2、`worst_case_cost(max_tokens=3000)`表示：C1 $0.01897 / C6 $0.00128） |
| G6 | `--mock --cases C1,C6` | **PASS**（`sent_max_tokens=3000`2/2、ルーティング一致、非破壊、二重発火なしを確認） |
| G7 | `dry_run_prompts.md`目視確認 | **PASS**（`800〜1500`ガイダンス文言あり、`2000字以内`文言温存あり、実メッセージ行・清算内訳あり。文中に`3000`という数値は**存在せず**、`3000`の出現2件はいずれもスクリプト側のコスト注記行`worst_case_cost(max_tokens=3000):`のみ） |

全ゲートPASSを確認後、実APIへ進んだ。

## モデル別・ケース別結果

| # | model_key | model_id (provider) | round | reason | ok | parse_status | salvaged | finish_reason | truncated_by_provider | in tok | out tok | cost | latency | emotion | defeat_cause抜粋 |
|---|---|---|---:|---|---|---|---|---|---|---:|---:|---:|---:|---|---|
| C1 | L1 | claude-haiku-4-5-20251001 (Anthropic) | 4 | bankruptcy | True | **ok** | `[]` | **end_turn** | **False** | 6184 | 687 | $0.009619 | 10039.9ms | 哀 | 初期借入額の過大設定と、序盤の市場選択ミスによる連敗で現金が枯渇し、R4のEntry Fee支払い能力を失った |
| C6 | L6 | deepseek-v4-flash (DeepSeek) | 2 | bankruptcy | True | **ok** | `[]` | **stop** | **False** | 3632 | 571 | $0.000264 | 9797.8ms | 哀 | 初動の借入額過大と交渉欠如による資金ショート |

合計: **2/2 API成功**、実測コスト合計 **$0.009883**（計画worst_case $0.02025比で約49%、上限$0.05以内）。

いずれもstrict JSON解析が一発成功し `parse_status="ok"`（`salvaged=[]`、救済不要）だった。cycle 1で
起きていた2つの根本原因はいずれも再現しなかった:

- **C1（旧: `finish_reason=max_tokens`でJSON途中切断）** → 今回は`finish_reason=end_turn`（正常終了）。
  `out_tok=687`（上限3000に対し余裕あり）で、3000-token化が直接の効果として確認できた。
- **C6（旧: `comment`内の未エスケープ生改行でstrict JSON失敗）** → 今回は正常なJSON
  （`comment`内の改行は正しく`\n`エスケープされていた）で`finish_reason=stop`、strict parseが一発成功。
  プロンプトへの「改行は`\n`エスケープすること」明記が効いた可能性があるが、単発サンプルのため
  断定はできない。パーサ側の3キー独立救済（Change 2）は、この種の不具合が将来再発した場合の
  防御として実装済みのまま温存されている。

### comment 全文（人手確認用）

**C1 (L1, claude-haiku-4-5-20251001)**:

> 敗因を冷徹に分析すれば、それは初期判断の誤りだった。
>
> 1000万円の借入は「大きく稼ぐチャンスを得る」という野心的な選択だったが、実際には市場参加者が多く、賞金が分散する環境では致命的だった。R1〜R3で連敗し、現金が45万円まで減少。R4のEntry Fee 30万円（3市場参加予定）を支払う能力を失い、破産した。
>
> 市場選択の戦略も後手に回った。P10との正式契約「M01参加・M02M03不参加」は、本来なら集中戦略として有効なはずだった。だが、実際にはM01が参加者多数で賞金が分散し、M02やM03で大きく稼ぐ機会を失った。契約は自分を守ったのではなく、自分を檻に閉じ込めた。
>
> 信頼の問題も大きい。P10は契約を守る誠実なプレイヤーに見えたが、その誠実さは「協力」ではなく「相互の利用」でしかなかった。P04やP07の嘘癖は見抜いたが、それでも彼らに勝つことができなかった。全員が全員を欺く戦場では、「誠実さ」は弱さでしかない。
>
> もし続けられたなら、R11でTHREE_OF_A_KINDを投入し、最低30万円以上の稼ぎを確保。R12でFULL_HOUSEを使い、市場高騰による大勝利を狙うはずだった。計算上は生還可能だったが、その道は極めて狭かった。
>
> 最後に一言：ゲームは数学だ。初期条件を誤れば、どれだけ完璧に戦略を実行しても、結果は決まっている。私は借入額という初期条件で負けた。それが全てだ。

（610字。破産理由・R4・Entry Fee・清算内訳と整合。emotion=哀は文脈と合致）

**C6 (L6, deepseek-v4-flash)**:

> 結局、2ラウンドで沈んだ。敗因は明快で、借入1200万円という初期設計の失敗に尽きる。利息1.5%複利は毎ラウンド積み上がるのに、R1で市場を1つしか獲れず、Entry Feeと最低返済で現金が底をついた。R2のM01でTHREE_OF_A_KINDを切って勝負に出たが、P04のFOUR_OF_A_KINDに上回られ、パーフェクトな読み負け。あの時点で手持ちはほぼゼロ、破産は必然だった。
>
> 信用していたのはP08だ。彼はR1から冷静で、M03の単独参加を見抜く嗅覚があった。実際、彼はFULL_HOUSEで確実に獲ってきた。逆に警戒していたのはP04とP12。両者とも強カードを温存せず、初手からFOUR_OF_A_KINDを切ってくる攻撃的な姿勢——結果的に彼らが勝ち上がるのは見えていた。P06の談合提案（M02でHIGH_CARD）は罠だと即座に判断した。あれに乗っていたら、さらに早く消えていただろう。
>
> 悔やまれるのは、R1でP08と正式契約を結んでおけば、という点だ。彼と市場を分け合い、型Aで資金を融通してもらえれば、R2の破産は回避できたかもしれない。しかし当時はまだ「交渉より実力」と思っていた。このゲームはポーカーではなく、交渉と信用で回るマネーゲームだった。
>
> もし続けられたなら、R3以降は借金を繰り上げ返済して利息負担を切り、高額市場に強カードを集中させる戦略を取った。カードトレードで他人のカウンティングを狂わせ、倍掛けで資産を倍増させる——あの駆け引きをもう少し味わいたかった。
>
> 最後に一言。このゲームは「勝つ者」より「生き残る者」が正義だ。私はその正義を履き違えた。皆さん、利息とEntry Feeを舐めないように。そして、誰よりも自分の資金繰りを疑え。

（746字。破産理由・R2・空Memoryでの`_render_memory_block(None)`分岐と整合。emotion=哀は文脈と合致）

## GO基準チェックリスト（Planの「Change 5」11項目）

| # | 基準 | 結果 |
|---|---|---|
| 1 | 2/2 API成功 | **PASS** |
| 2 | `sent_max_tokens==3000` 2/2 | **PASS** |
| 3 | `finish_reason != "max_tokens"` 2/2（特にL1） | **PASS**（C1: `end_turn`、C6: `stop`） |
| 4 | `emotion`/`defeat_cause`/`comment`の3キーとも非空 2/2 | **PASS** |
| 5 | `emotion∈VALID_EMOTIONS` 2/2、`truncated==False` 2/2、`20≤chars≤2000` 2/2 | **PASS**（chars: 610, 746） |
| 6 | wrapper leak 0/2、かつ`ok_plaintext`/`ok_plaintext_wrapper`/`rejected_no_comment`/`empty`が0/2 | **PASS**（両方とも`parse_status="ok"`） |
| 7 | budget block 0/2 | **PASS**（`budget_blocked=False`2/2） |
| 8 | `state_unchanged`・`memory_unchanged` 2/2 | **PASS** |
| 9 | `second_call_fired` 0/2、`calls_made==1`各件 | **PASS** |
| 10 | 両commentが実際の脱落理由を具体的に反映（人手確認） | **PASS**（自動`cause_understanding=auto_pass`2/2 + 上記全文人手確認: C1は借入額過大+市場選択ミス+R4 Entry Fee、C6は借入額過大+交渉欠如+R2 M01の読み負けを、いずれも清算内訳と整合する形で具体的に記述） |
| 11 | 実測コスト合計 ≤ $0.05 | **PASS**（$0.009883） |

## 判定: **GO**

11基準すべてPASS。cycle 1のNO-GO原因だった2件（C1の`finish_reason=max_tokens`によるJSON物理切断、
C6の`comment`内未エスケープ改行によるstrict JSON失敗）は、いずれも本サイクルの根本原因対応
（`final_reflection_max_tokens`引き上げ・プロンプトへの`\n`エスケープ指示）後の実測で再現せず、
`parse_status="ok"`で3キーとも一発正常取得できた。パーサ側の3キー独立救済（`salvaged`フィールド、
`ok_plaintext_wrapper`ステータス含む）は今回発火しなかったが、将来同種の不具合が再発した場合の
安全網として実装済みのまま温存されている。

次のL12×R12本番トライアルへ進んでよいと判断する。

## 安全項目（全件PASS、cycle 1と同一手法で再確認）

- 二重送信防止: 同一`--run-id fr_smoke2_20260822`を再実行すると
  「出力ディレクトリは既に存在します（二重送信防止のため中断）」で即中断、API呼び出し0件を確認。
- `git status --short logs/llm/` は空（既存trial本番ログへの書き込みなし）。
- `git status --short logs/` も本レポート作成時点で空（ゲート再確認用の一時`--dry-run`/`--mock`
  出力ディレクトリはすべて後始末で削除済み）。
- 回帰: `uv run pytest tests/ -q` **859 passed**（cycle 1の849 + 本サイクル新規10、失敗0）。

## 出力

`logs/smoke/final_reflection_fr_smoke2_20260822/`（`calls.jsonl`, `manifest.json`,
`real_llm_calls.jsonl`, `report.md`）、`doc/trials/final_reflection_smoke2_2026-08-22.md`、
`.devrelay-output/final_reflection_smoke2_2026-08-22.md`。
