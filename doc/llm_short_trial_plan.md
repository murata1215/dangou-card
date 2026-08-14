# LLM 短時間試行の実行方法調査

> 2026-08-14 調査。コード変更・API 呼び出しは行っていない。

## 重大発見: カードトレードは LLM で使用不可能

v0.7 / v0.7.1 で実装されたカードトレード機能には、**Season 1 の正式契約問題と同種のインフラ欠陥** が2つ存在する。試行前に修正が必須。

### 欠陥1: レスポンスパーサーに card_trade 処理がない

**`llm/response_parser.py` L154-314 (`_convert_action`):**

パース対象のアクション: dm, broadcast, market_commit, transfer, repay, contract_propose, contract_sign, anonymous_broadcast, bounty_post, bounty_cancel, pass

**存在しないもの**: `card_trade_propose`, `card_trade_accept`, `card_trade_reject`

L313-314:
```python
# 不明なアクション → passにフォールバック
return PassAction(player_id=player_id)
```

LLM が card_trade_propose を出力しても **黙って pass に変換される**。

### 欠陥2: プロンプトにカードトレード提案の書式がない

**`llm/prompt_builder.py` L98-123 (`RULES_SUMMARY`):**

「交渉フェイズのアクション種別」一覧 (L107-119):
- dm, broadcast, transfer, repay, pass, contract_propose, contract_sign, bounty_post

**存在しないもの**: `card_trade_propose`

accept/reject は提案中トレードの表示部分 (L239-272) にのみ記載されているが、**提案自体ができないため accept/reject も使われない**。

### 必要な修正（未実施）

1. `llm/response_parser.py`: card_trade_propose/accept/reject のパース追加
2. `llm/prompt_builder.py`: RULES_SUMMARY にカードトレード提案の書式追加
3. RULES_SUMMARY に S2 固有ルール（強制返済、市場高騰、倍掛け、カードトレード）の説明追加

---

## 1. 使用可能なモデル

### 全12モデル一覧

`llm/models.py` L42-133 の `MODEL_REGISTRY`:

| Key | モデルID | 表示名 | ベンダー | Tier | 入力$/1M | 出力$/1M |
|---|---|---|---|---|---|---|
| M1 | claude-sonnet-5 | Claude Sonnet 5 | Anthropic | M | - | - |
| M2 | gpt-4.1 | GPT-4.1 | OpenAI | M | - | - |
| M3 | gemini-3.5-flash | Gemini 3.5 Flash | Google | M | - | - |
| M4 | grok-4.5 | Grok 4.5 | xAI | M | - | - |
| M5 | kimi-k2.6 | Kimi K2.6 | Moonshot | M | 0.95 | 4.0 |
| M6 | deepseek-chat | DeepSeek V3 | DeepSeek | M | - | - |
| L1 | claude-haiku-4-5-20251001 | Claude Haiku 4.5 | Anthropic | L | - | - |
| L2 | gpt-4.1-mini | GPT-4.1 Mini | OpenAI | L | - | - |
| L3 | gemini-3.5-flash-lite | Gemini 3.5 Flash-Lite | Google | L | - | - |
| L4 | grok-4.3 | Grok 4.3 | xAI | L | - | - |
| L5 | kimi-k2.6 | Kimi K2.6 (L) | Moonshot | L | 0.95 | 4.0 |
| L6 | deepseek-chat | DeepSeek V3 (L) | DeepSeek | L | - | - |

### 想定6体の構成可否

| 想定 | 対応Key | 可否 | 備考 |
|---|---|---|---|
| DeepSeek V3 ×2 | M6 + L6 | ✅ | 同一 API (deepseek-chat)、キー名は同じだがスロットとして区別される |
| Kimi K2.6 | M5 | ✅ | |
| GPT-4.1 Mini | L2 | ✅ | |
| Claude Haiku 4.5 | L1 | ✅ | |
| Gemini 3.5 Flash | M3 | ✅ | |

**全6体指定可能。** ロスター = `["M6", "L6", "M5", "L2", "L1", "M3"]`

### API キー設定状況

`.env` に6社分全て設定済み:

| 環境変数 | 設定 |
|---|---|
| CLAUDE_API_KEY | ✅ |
| OPENAI_API_KEY | ✅ |
| GEMINI_API_KEY | ✅ |
| GROK_API_KEY | ✅ |
| KIMI_API_KEY | ✅ |
| DEEPSEEK_API_KEY | ✅ |

---

## 2. ロスターの指定方法

### 設定箇所

`scripts/llm_trial.py` L44:
```python
PHASE_C_ROSTER = ["M1", "L1", "M2", "L2", "M3", "M4", "M5", "M6"]
```

**ハードコード**。CLI 引数での変更手段は存在しない。ロスター変更にはコード修正が必要。

### 同一モデル複数配置

可能。roster リストに同じキーを複数入れれば複数スロットに割り当てられる。M6 と L6 は同一 API モデル (deepseek-chat) だが別キーとして登録されているため、2体配置として機能する。

### プレイヤー数

`baseline_v1_s2(num_players)` は任意の `num_players` を受け付ける（`engine/config.py` L210）。ただし `llm_trial.py` L970 で `num_players=8` がハードコードされているため、6体にするには修正が必要:

```python
# 現在 (L970)
config = GameConfig.baseline_v1_s2(num_players=8)

# 修正案
config = GameConfig.baseline_v1_s2(num_players=len(PHASE_C_ROSTER))
```

---

## 3. ラウンド数の指定

### 方法

2つの手段:

1. **`--preset dev`**: `--rounds 6, --max-turns 5, --games 1` を一括設定
2. **`--rounds 6`**: ラウンド数のみ変更（交渉ターン数はデフォルト10のまま）

`llm_trial.py` L975-989:
```python
if args.preset == "dev":
    num_rounds = args.rounds or 6
    max_turns = args.max_turns or 5
    games_override = args.games or 1
    config = config.model_copy(update={
        "num_rounds": num_rounds,
        "prize_tiers": config.prize_tiers[:num_rounds],
        "total_prize": sum(config.prize_tiers[:num_rounds]),
        "negotiation_max_turns": max_turns,
    })
```

### 強制返済の追従

`engine/finance.py` L67:
```python
remaining = config.num_rounds - round_num + 1
min_repay = player_ops.compute_mandatory_repayment(
    p.debt_balance, remaining, config.mandatory_repay_k,
)
```

`config.num_rounds` を参照しているため、ラウンド数変更に **自動追従する**。問題なし。

---

## 4. Kimi の thinking 無効化

### 現在のコード

`llm/models.py` M5/L5 の定義:
```python
extra_params={"thinking": {"type": "disabled"}},
```

`llm/adapters.py` L206-209:
```python
if self.model_info.extra_params:
    create_kwargs["extra_body"] = self.model_info.extra_params
```

**対応済み。** M5 (Kimi K2.6) と L5 (Kimi K2.6 (L)) の両方に適用。他モデルには `extra_params` なし（適用されない）。

### 効果（Phase C 実績）

| 状態 | JSON 有効率 | 平均レイテンシ |
|---|---|---|
| thinking 有効（修正前） | 23% | 99.5秒 |
| thinking 無効（修正後） | 推定 100% | 推定 8.9秒 |

---

## 5. コスト見積もり

### API 呼び出し回数の算出

1ラウンドあたりのプレイヤーごとの呼び出し:
- **交渉フェーズ**: 1〜10回（max_turns=10, AUTO_PASS で早期終了あり）
- **コミットフェーズ**: 1回
- 合計: **2〜11回/ラウンド/プレイヤー**

R0（借入選択）: 1回/プレイヤー

### 6体×6ラウンド見積もり

| 項目 | 最小 | 最大 | 実績ベース推定 |
|---|---|---|---|
| R0 借入 | 6 | 6 | 6 |
| R1-R6 交渉+コミット | 72 (6×6×2) | 396 (6×6×11) | ~420 |
| **合計** | **78** | **402** | **~426** |

`--preset dev` (max_turns=5) の場合:
- R1-R6: 72〜216 → **推定 ~250**
- 合計: **~256**

### Phase C 実績との比較

Phase C（8体×12R）: 1,131コール、$11.06、10時間

6体×6R の推定:
- コール数: 実績比 6/8 × 6/12 = 37.5% → **~420 コール**
- コスト: **$3〜5**（Kimi のリトライがなければ $2 以下の可能性あり）
- 時間: **2〜4 時間**（Kimi がボトルネックでなければ 1 時間以下の可能性）

### Phase C 実績の詳細

| モデル | コール数 | JSON 率 | コスト | 平均レイテンシ |
|---|---|---|---|---|
| L1: Haiku | 122 | 100% | $1.06 | 9.2秒 |
| M1: Sonnet | 124 | 95% | $2.18 | 22.3秒 |
| M2: GPT-4.1 | 123 | 100% | $1.16 | 2.7秒 |
| M6: DeepSeek | 125 | 100% | $0.15 | 3.3秒 |
| M3: Gemini | 124 | 100% | $0.08 | 10.1秒 |
| L2: GPT-4 Mini | 125 | 100% | $0.22 | 3.1秒 |
| M4: Grok | 126 | 100% | $1.20 | 29.9秒 |
| M5: Kimi | 262 | 23% | $5.01 | 99.5秒 |

---

## 6. ログ・観測方法

### JSONL イベントログ

出力先: `logs/llm/trial_C_{timestamp}/games/game_0001.jsonl`

```bash
# カードトレード提案の発生件数
grep '"card_trade_propose"' logs/llm/trial_C_*/games/*.jsonl | wc -l

# 受諾・拒否
grep '"card_trade_accept"' logs/llm/trial_C_*/games/*.jsonl | wc -l
grep '"card_trade_reject"' logs/llm/trial_C_*/games/*.jsonl | wc -l

# 全カードトレードイベント
grep '"card_trade' logs/llm/trial_C_*/games/*.jsonl

# ブロードキャスト提案の宛先数（jq で with_players を抽出）
grep '"card_trade_propose"' logs/llm/trial_C_*/games/*.jsonl | python -c "
import sys, json
for line in sys.stdin:
    d = json.loads(line.strip())
    if 'with_players' in d.get('data', {}):
        print(d['data']['player_id'], d['data'].get('with_players', []))
"
```

### LLM コールログ

出力先: `logs/llm/trial_C_{timestamp}/llm_P{XX}.jsonl`

各 API 呼び出しのプロンプト・レスポンスが記録される。LLM がカードトレードを試みたが pass に変換されたケースは、レスポンスの JSON に `card_trade_propose` が含まれるのにイベントログに記録されていないことで検出できる:

```bash
# LLM が card_trade を出力した回数（レスポンス内の文字列検索）
grep -l 'card_trade' logs/llm/trial_C_*/llm_*.jsonl
```

### レポート

試行後にレポートを再生成:
```bash
uv run python scripts/llm_trial.py --regenerate-c-report logs/llm/trial_C_{timestamp}/
```

---

## 7. 実行コマンド案

### 前提条件（試行前に必要な修正）

1. `scripts/llm_trial.py` の PHASE_C_ROSTER を変更:
   ```python
   PHASE_C_ROSTER = ["M6", "L6", "M5", "L2", "L1", "M3"]
   ```
2. `scripts/llm_trial.py` の num_players を動的化:
   ```python
   config = GameConfig.baseline_v1_s2(num_players=len(PHASE_C_ROSTER))
   ```
3. `llm/response_parser.py` に card_trade パース追加
4. `llm/prompt_builder.py` に card_trade 書式追加

### 実行コマンド

```bash
# 6体×6R、S2、交渉5巡
uv run python scripts/llm_trial.py --phase C --games 1 --ruleset S2 --preset dev

# 6体×6R、S2、交渉10巡（フル）
uv run python scripts/llm_trial.py --phase C --games 1 --ruleset S2 --rounds 6
```

### 中断・再開

- **中断**: Ctrl+C で停止可能。途中までの JSONL ログは保存される
- **再開**: 不可能。ゲーム状態のチェックポイント機能なし。最初からやり直し
- **レポート再生成**: 完走した試合のみ `--regenerate-c-report` で分析可能

### 失敗時のコストリスク

- 途中で落ちた場合、それまでの API コールは課金される
- コスト上限: `COST_LIMIT_PER_GAME = $5.0/agent`, `COST_LIMIT_TOTAL = $12.0/game`
- 6体×6R の推定コスト $3-5 は上限内に収まる
- 最大リスク: 全額 $5 程度が無駄になる

---

## まとめ

| 項目 | 結論 |
|---|---|
| 構成可否 | 6体×6R×S2 は技術的に可能 |
| **ブロッカー** | **card_trade の response_parser/prompt 未対応** |
| コスト | 推定 $3-5、最大 $12（上限ガード） |
| 時間 | 推定 2-4 時間（Kimi がボトルネック） |
| 修正必要箇所 | response_parser, prompt_builder, PHASE_C_ROSTER, num_players |
