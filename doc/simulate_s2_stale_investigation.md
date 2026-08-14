# simulate.py の S2 設定が v0.6 のままである問題の調査

## 概要

`scripts/simulate.py --ruleset S2` が v0.7 の S2 プリセット (`baseline_v1_s2()`) を使用せず、v0.6 相当の不完全な S2 設定を独自に構築している。強制返済・カードトレード・高騰全員参加要件が欠落し、`fog_rounds` も廃止済みの `[4, 8]` のまま。

---

## 1. simulate.py の設定経路

### 設定構築の流れ

1. `create_config_for_roster()` (L57-80) でプレイヤー数に応じた **S1 ベース設定** を取得
   - 8人: `GameConfig.default_8()`
   - 12人: `GameConfig.default_12()`
   - 20人: `GameConfig.default_20()`
   - その他: 比例縮小で独自構築
2. `--ruleset S2` の場合、L378-384 で **独自に S2 パラメータを上書き**

```python
# scripts/simulate.py L377-384
if args.ruleset == "S2":
    config = config.model_copy(update={
        "fog_rounds": [4, 8],          # ← v0.7 で [] に変更済みだが古いまま
        "surge_enabled": True,
        "final_market_multiplier": 3,
        "double_up_enabled": True,
    })
```

**`default_8_s2()` や `baseline_v1_s2()` のプリセットは一切使われていない。**

### `--ruleset` オプション

| 値 | デフォルト | 対応設定 |
|---|---|---|
| `S1` | ○（既定値） | `default_8()` 等の S1 プリセットそのまま |
| `S2` | | S1 ベース + 独自上書き（4項目のみ） |

大文字小文字は `choices=["S1", "S2"]` で制約されており、`s2` は argparse がエラーにする。

---

## 2. プリセットとの差分（全フィールド比較）

| パラメータ | simulate.py `--ruleset S2` | `baseline_v1_s2()` (正) | 差異 |
|---|---|---|---|
| `fog_rounds` | **[4, 8]** | **[]** | v0.6 のまま。v0.7 で霧は廃止 |
| `surge_enabled` | True | True | 一致 |
| `surge_full_participation_max_alive` | **0** (default) | **3** | 欠落。高騰の全員参加要件が無効 |
| `final_market_multiplier` | 3 | 3 | 一致 |
| `double_up_enabled` | True | True | 一致 |
| `mandatory_repay_enabled` | **False** (default) | **True** | 欠落。強制返済が無効 |
| `mandatory_repay_k` | 0 (default) | 0 | 結果一致（偶然） |
| `card_trade_enabled` | **False** (default) | **True** | 欠落。カードトレードが無効 |
| `card_trade_max_per_round` | 1 (default) | 1 | 一致（偶然） |
| `card_trade_last_round` | 11 (default) | 11 | 一致（偶然） |
| `card_trade_broadcast_max` | 5 (default) | 5 | 一致（偶然） |
| 賞金体系 | **逓増傾斜** (default_8) | **フラット+prize_scale=2.0** | 異なる |
| `survival_cash` | **3,000,000** | **2,000,000** | 異なる |

**6項目が不正。** 特に `fog_rounds=[4,8]`（v0.7 で廃止済み）、`mandatory_repay_enabled`、`card_trade_enabled` の欠落が深刻。

---

## 3. 「S1 で実行されてしまった」問題

直近タスク (cmssmog17414ajvrwtm7gfb95) の実行ログで、`simulate.py --games 1000` を `--ruleset S2` なしで実行し S1 になったやり直しが発生。

**原因**: `--ruleset` のデフォルト値が `"S1"` であり、引数を省略すると S1 で実行される。ロジックのバグではなく、単純な引数指定忘れ。

---

## 4. 他のスクリプトの確認

### 問題のあるスクリプト

#### `scripts/simulate_s2_comparison.py` L145-167

```python
def _create_config(roster: list[str], ruleset: str) -> GameConfig:
    num = len(roster)
    ratio = num / 20
    base_tiers = [1_200_000] * 4 + [1_600_000] * 4 + [2_000_000] * 4
    scaled_tiers = [int(t * ratio) for t in base_tiers]
    total = sum(scaled_tiers)
    config = GameConfig(
        num_players=num, total_prize=total, prize_tiers=scaled_tiers,
    )
    if ruleset == "S2":
        config = config.model_copy(update={
            "fog_rounds": [4, 8],
            "surge_enabled": True,
            "final_market_multiplier": 3,
            "double_up_enabled": True,
        })
    return config
```

`simulate.py` と全く同じ古い S2 設定。プリセット不使用。

### 問題のないスクリプト

| スクリプト | 設定ソース | 状態 |
|---|---|---|
| `scripts/simulate_mandatory_repay.py` | `GameConfig.baseline_v1_s2(len(roster))` | 正しい |
| `scripts/simulate_surge_change.py` | `GameConfig.baseline_v1_s2(len(roster))` | 正しい |
| `scripts/dry_run.py` | `GameConfig.default_12()` / `default_20()` | S1 のみ。問題なし |
| `scripts/sweep.py` | `GameConfig.default_8()` | S1 のみ。問題なし |
| `scripts/compare_schedules.py` | `GameConfig.default_8()` | S1 のみ。問題なし |
| `scripts/mirror_match.py` | `GameConfig.default_8()` | S1 のみ。問題なし |

### LLM 本戦 (`scripts/llm_trial.py`)

```python
# L966
config = GameConfig.baseline_v1(num_players=8)
```

**S1 (`baseline_v1`) のみ対応。** `--ruleset` オプション自体が存在しない。Season 2 本戦を実行するには S2 対応の追加が必要。

ただし現時点で S2 本戦は未実施のため、**過去の本戦結果 (Phase C) は S1 として正しい**。

---

## 5. 過去のシミュレーション結果の信頼性

| 実行経路 | 使用設定 | 信頼性 |
|---|---|---|
| `simulate.py --ruleset S2` | v0.6 相当（不完全） | **信頼できない** |
| `simulate_s2_comparison.py` | v0.6 相当（不完全） | **信頼できない** |
| `simulate_mandatory_repay.py` | `baseline_v1_s2()` | 正しい |
| `simulate_surge_change.py` | `baseline_v1_s2()` | 正しい |
| LLM 本戦 Phase C | `baseline_v1()` (S1) | S1 として正しい |

### 影響を受ける過去結果

- `simulate.py --ruleset S2` で取得した結果は全て v0.6 相当の不完全な S2 設定。ただし直近タスクでの `simulate.py --ruleset S2` 実行は高騰率確認のみに使用されており、高騰関連パラメータ (`surge_enabled`) は正しく設定されていたため、**高騰率の数値自体は有効**（`surge_full_participation_max_alive` の欠落を除く）。
- `simulate_s2_comparison.py` は S1/S2 比較レポートに使用されたが、S2 側が不完全なため比較結果は参考値。

---

## 6. 修正方針の提案

### 方針A: プリセット関数への統一（推奨）

`simulate.py` と `simulate_s2_comparison.py` の S2 設定構築を `baseline_v1_s2()` プリセットに差し替える。

```python
# simulate.py 修正案
if args.ruleset == "S2":
    config = GameConfig.baseline_v1_s2(len(roster))
else:
    config = GameConfig.baseline_v1(len(roster))
```

**メリット**:
- 今後 S2 パラメータが追加されても追従漏れが起きない
- 単一の設定ソース（config.py のプリセット）に統一される

**影響**:
- 賞金体系が逓増→フラットに変わる（baseline_v1 系の仕様）
- `survival_cash` が 3,000,000 → 2,000,000 に変わる
- 過去結果との再現性が断絶するが、そもそも過去結果が不正なため問題なし

### 方針B: llm_trial.py の S2 対応

`--ruleset S2` オプションを追加し、`baseline_v1_s2()` を使用:

```python
if args.ruleset == "S2":
    config = GameConfig.baseline_v1_s2(num_players=8)
else:
    config = GameConfig.baseline_v1(num_players=8)
```

### 影響範囲

- 既存テスト: 影響なし（テストは `simulate.py` を経由しない）
- 既存スクリプト: `simulate.py` と `simulate_s2_comparison.py` の出力が変わる
- 過去結果: 上記「信頼性」セクション参照
