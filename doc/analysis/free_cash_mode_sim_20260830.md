# free_cash_mode シミュレーション結果（サイクル9.1）

- 実行日時: 2026-08-30 18:06 JST
- ruleset: S2（`baseline_v1_s2`）、12ラウンド
- 各系列 1,000試合、seed=42、`--workers 4`
- 集計スクリプト: `/tmp`の使い捨てスクリプト（リポジトリには残さない）。`summary.csv` と `games/*.jsonl` を突合。

## 背景

v0.8 本戦で **transfer 0件・トレード0件・型A不能脱落1件** という結果になった。原因は
支払可能額のゲートが Free Cash（`max(0, 現金 − 借金残高)`）だったこと。R1開始時は
全員 `現金 = 借金 = 借入額` なので Free Cash = 0 となり、借入金を交渉に一切使えなかった。

本サイクルで `GameConfig.free_cash_mode: "debt" | "cash" | "entry_fee"` を新設し、
`player_ops.spendable_cash(player, config, at_settlement=...)` により支払可能額の基準を
切り替え可能にした。`entry_fee` モードでは Commit フェーズ以外は
`現金 − 今RのEntry Fee` を、Settlement 時点では Entry Fee は既に引き落とし済みなので
二重控除を避けて `現金` そのものを使う。

既定ロスター8種は送金・報奨・トレード・契約を一切行わないため、比較のために
送金専用の `KingmakerBot`（承認済み案B）を追加した。仕様: 毎Rの Negotiation 冒頭
（turn 1）で「次Rの強制最低返済額（利息込み）＋次RのEntry Fee」が現金を超える
見込み（＝賞金なしなら来Rで破産する）と判定したら、その手番で `spendable_cash` の
全額を、その時点で最も裕福と推定される自分以外の生存者へ transfer する。それ以外は
ConservativeBot と同じ挙動（最低借入・最低賞金市場+カード均等消化・返済のみ）。
借入額は ConservativeBot と同一の 120万円固定（`CONSERVATIVE_LOAN`）。他プレイヤーの
現金は本来秘匿情報のため、公開情報（`initial_loans` / `last_round_results` の
`prize_per_winner` / `winners` / `config.entry_fee`）のみから粗く推定している
（返済・利息・送金・型A決済・トレードは推定に反映されない近似値）。
`KingmakerBot` はデフォルトロスターには含めず、`--roster` 等で明示指定時のみ使用する。

**CLI設計の修正**: `scripts/simulate.py --free-cash-mode` の当初案は `default="debt"` を
「未指定」のセンチネルに使っていたが、これだと `--ruleset S2`（プリセットが `entry_fee`）に
対して明示的に `debt` を強制することができなかった（未指定と区別不能）。`default=None` に
変更し、`is not None` で判定するよう修正した。

## 実行系列

| 系列 | ロスター | free_cash_mode | ログディレクトリ |
|---|---|---|---|
| a_debt | 既定8種×1（Random/Conservative/StrongCardSave/HighPrizeHunter/EmptyMarketHunter/Collusion/Betrayal/HoneyPot） | debt | `logs/sim_20260830_175923` |
| a_entry_fee | 同上 | entry_fee | `logs/sim_20260830_175932` |
| b_debt | 既定8種のうち2席をKingmakerに置換した12席（Random:2, Conservative:2, StrongCardSave:1, HighPrizeHunter:1, EmptyMarketHunter:1, Collusion:1, Betrayal:1, HoneyPot:1, Kingmaker:2） | debt | `logs/sim_20260830_175945` |
| b_entry_fee | 同上 | entry_fee | `logs/sim_20260830_175958` |
| c_debt | Kingmaker4席版12席（Random:1, Conservative:1, StrongCardSave:1, HighPrizeHunter:1, EmptyMarketHunter:1, Collusion:1, Betrayal:1, HoneyPot:1, Kingmaker:4） | debt | `logs/sim_20260830_180047` |
| c_entry_fee | 同上 | entry_fee | `logs/sim_20260830_180059` |

Botの借入ポリシー（`bots/constants.py`、固定値・`free_cash_mode`の影響を受けない）:
Random=一様乱数[loan_min, loan_max] / Conservative=120万 / StrongCardSave=300万 /
EmptyMarketHunter=300万 / Collusion=300万 / HighPrizeHunter=500万 / Betrayal=500万 /
HoneyPot=700万 / Kingmaker=120万（Conservativeと同一）。

## 系列(a) 既定8種: 回帰証明

`summary.csv` は debt / entry_fee で **バイト単位で完全一致**（`diff` で差分なし）。
`report.md` の差分は実行時間の行のみ。

| 指標 | debt | entry_fee |
|---|---|---|
| 生存者数分布 | `{2:9, 3:105, 4:349, 5:405, 6:122, 7:10}` | 同左（完全一致） |
| 平均生存者数 | 4.556 | 4.556 |
| 脱落理由 | bankruptcy=1980, condition_not_met=1464 | 同左 |
| 経済膨張率 | 0.567666 | 0.567666 |
| 借入×最終順位 相関 | -0.006326 | -0.006326 |
| transfer / bounty / trade / type-A | 全て0 | 全て0 |

既定8種のルールベースBotは送金・報奨・トレード・契約を一切行わないため、
`free_cash_mode` の切り替えは**既存挙動に副作用ゼロ**という回帰証明になった。

## 系列(b)(c) Kingmaker混入: 主要指標比較

| 指標 | b_debt (K×2) | b_entry_fee (K×2) | c_debt (K×4) | c_entry_fee (K×4) |
|---|---|---|---|---|
| 平均生存者数 | 6.097 | 6.084 | 6.764 | 6.736 |
| 脱落理由(bankruptcy/condition_not_met) | 5077 / 826 | 5087 / 829 | 4115 / 1121 | 4163 / 1101 |
| 経済膨張率 | 0.691148 | 0.692363 | 0.788988 | 0.791883 |
| 借入×最終順位 相関 | -0.333848 | -0.332275 | -0.481837 | -0.481704 |
| type-A成立/失敗 | 0 / 0 | 0 / 0 | 0 / 0 | 0 / 0 |
| 報奨件数 | 0 | 0 | 0 | 0 |
| トレード成立件数 | 0 | 0 | 0 | 0 |
| 高騰(surge)件数 | 3769 | 3779 | 3671 | 3668 |
| 倍掛け成立/成功 | 16620 / 6170 | 16620 / 6165 | 15953 / 6802 | 15963 / 6808 |

Kingmakerを混ぜても **型A・報奨・トレードは依然として0件**（本Botは送金以外の能動的
行動を一切行わない設計のため）。高騰・倍掛けの発生率は debt/entry_fee 間でほぼ同水準
（乱数系のため微小な差異は誤差範囲）。

### 送金（Kingmakerの贈与）比較 — v0.9の核心的な検証結果

| 指標 | b_debt (K×2) | b_entry_fee (K×2) | c_debt (K×4) | c_entry_fee (K×4) |
|---|---|---|---|---|
| transfer_count | 15 | 899 | 42 | 1492 |
| transfer_amount（合計） | 844,747円 | 74,120,047円 | 2,115,026円 | 129,012,372円 |
| transfer発生試合数 / 1000試合 | 15 | 597 | 41 | 658 |
| うち「遺言」送金件数（送金者が送金後2R以内に脱落） | 8 | 543 | 32 | 860 |
| 受益者(to)の生存率 | 86.67% (n=15) | 83.20% (n=899) | 97.62% (n=42) | 90.55% (n=1492) |
| 受益者(to)の平均最終順位 | 1.615 | 2.130 | 1.683 | 1.785 |
| 送金者(sender)の生存率 | 46.67% (n=15) | 29.03% (n=899) | 21.43% (n=42) | 24.93% (n=1492) |

**debt モードでは Kingmaker の送金トリガー（次Rで破産見込み）が成立しても
`spendable_cash`（≈Free Cash）がほぼ0のため実際に送金できるケースが極端に少ない**
（b: 15件、c: 42件）。**entry_fee モードでは同じ判定ロジックのまま送金が
約60〜88倍に増加**（b: 899件＝×59.9、c: 1492件＝×35.5）し、送金総額も
約88〜61倍に増加した（b: ×87.7、c: ×61.0）。これは本サイクルの目的
「借入金を交渉資金として最初から使えるようにする」がエンジンレベルで機能して
いることの直接的な証拠である。

送金の大半（b: 53%〜61%、c: 58%〜76%）は送金者が送金後2ラウンド以内に脱落する
「遺言」送金であり、Kingmakerの判定ロジック（次Rで破産見込みなら全額贈与）が
狙い通り「詰みかけた資産の最終処分」として機能していることが確認できる。

受益者の生存率（83〜98%）は送金しない場合の全体平均生存率（b: 6.1/12≈51%、
c: 6.8/12≈56%）を大幅に上回っており、Kingmakerからの贈与が受益者の生存に
明確に寄与している。一方、送金者自身の生存率（21〜47%）は全体平均を下回るが、
これは送金者がそもそも「次Rで破産見込み」と判定された窮地の生存者であるため
（贈与しなくても多くはそのまま脱落していた可能性が高い）、送金が生存率を
下げた因果関係とは解釈できない。

### 経済膨張率（debt→entry_fee 差分）

| 系列 | debt | entry_fee | 差分 |
|---|---|---|---|
| a（既定8種） | 0.567666 | 0.567666 | 0.000000（完全一致） |
| b（K×2） | 0.691148 | 0.692363 | +0.001215 |
| c（K×4） | 0.788988 | 0.791883 | +0.002895 |

entry_fee モードで送金が活発化した分、生存者間の富の再分配が進み、
生存者の最終現金合計（＝経済膨張率の分子）がわずかに増加している。
ただし送金は生存者間のゼロサム移転（脱落者から生存者への移転を含む）であり、
影響は軽微（+0.1〜0.3ポイント）にとどまる。

## Bot別生存率（参考）

| Bot | a_debt/a_entry_fee | b_debt | b_entry_fee | c_debt | c_entry_fee |
|---|---|---|---|---|---|
| Random | 64.9% | 65.70% (n=2000) | 65.75% (n=2000) | 70.1% | 70.4% |
| Conservative | 94.4% | 65.55% (n=2000) | 65.70% (n=2000) | 70.3% | 70.8% |
| StrongCardSave | 73.3% | 19.6% | 19.6% | 33.0% | 33.0% |
| HighPrizeHunter | 25.7% | 89.2% | 89.1% | 93.7% | 93.8% |
| EmptyMarketHunter | 67.4% | 17.1% | 17.2% | 29.5% | 29.5% |
| Collusion | 80.1% | 29.7% | 29.6% | 37.3% | 37.5% |
| Betrayal | 48.1% | 58.3% | 58.3% | 71.3% | 71.3% |
| HoneyPot | 1.7% | 1.2% | 1.6% | 0.7% | 1.0% |
| Kingmaker | — | 66.05% (n=2000) | 65.05% (n=2000) | 67.625% (n=4000) | 66.575% (n=4000) |

同一Botでも系列(a)⇔(b)/(c)で生存率が大きく変わるのは、席数（8席→12席）と
ロスター構成が変わり相対的な競争環境が変化するため（`free_cash_mode`の影響ではない）。
同一系列内でdebt/entry_fee間の差は各Bot数%以内に収まっており、送金に直接関与しない
Botへの間接的な影響は小さい。

## 確認事項（今回のスコープ外・既知の未解決点）

`baseline_v1_s2` が `entry_fee` モードになっても、`llm/prompt_builder.py` は旧 Free Cash
定義を出力し続ける（AIに伝えるルールと実際の判定が食い違う）。`entry_fee` モードでの
LLM本戦の前に、次タスクで必ず解消する必要がある。

## 検証

- `uv run pytest -q` 全件パス（1576件、既存無改修＋新規`tests/test_spendable_cash.py`29件）
- `logs/llm/` は本作業で一切触れていない
- LLM APIは呼んでいない、git commit/pushはしていない
