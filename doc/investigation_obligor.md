# 調査レポート: 'obligor' KeyError（Step 3.2調査）

## 結論

**原因**: `engine/contracts.py` L44-48 で `term["obligor"]`、`term["counterparty"]`、`term["ob_type"]`、`term["round_num"]` を直接 `[]` アクセスで取得しているが、LLMはtermsの内部構造を知らない。プロンプトの contract_propose 説明は `"terms": [...]` と省略されており、termsに必要なフィールド（obligor/counterparty/ob_type/round_num/details）の形式が一切記載されていない。LLMが独自形式でtermsを構築→**KeyError: 'obligor'** が発生。

**エラー経路**:
```
LLMレスポンス
  → llm/response_parser.py _convert_action(): ContractProposeAction(terms=LLMの生terms)
  → engine/game.py L286: contract_ops.create_contract(terms=action.terms)
  → engine/contracts.py L44: term["obligor"]  ← KeyError
```

**トレースバック消失の原因**: `scripts/llm_trial.py` L423 `except Exception as e: print(f"  エラー: {e}")` でスタックトレースが握りつぶされ、「エラー: 'obligor'」の1行のみが表示された。

**LLMログ未保存の原因**: エラーが `game.run()` 内で発生 → `run_trial_game()` がexceptionで終了 → `llm_logger.save()` が呼ばれず → `llm_logs/` ディレクトリが空のまま。

---

## スタックトレース全文（再現）

以下はログ記録済みデータに基づく最小再現コードで取得した完全なスタックトレース:

```python
# 再現コード（APIを呼ばない）
from engine.contracts import create_contract

llm_terms = [
    {"type": "payment", "player": "P02", "amount": 500000, "round": 5}
]
create_contract("P01", ["P01", "P02"], llm_terms, round_created=3)
```

```
Traceback (most recent call last):
  File "<string>", line 11, in <module>
  File "/home/uso8m/dangou-card/engine/contracts.py", line 45, in create_contract
    obligor=term["obligor"],
            ~~~~^^^^^^^^^^^
KeyError: 'obligor'
```

---

## LLMの契約提案の生データ

**問題のseed=7の試合（logs/llm/trial_B_20260809_205144/）**: LLMログディレクトリが空（エラーによりllm_logger.save()が呼ばれず未保存）。LLMが出力した実際のcontract_proposeの生JSONは**回復不能**。

**既存の完走4試合（trial_B_20260809_175839/）**: 全8ファイル（game01〜04 × P01,P02）を全文検索したが、`"type": "contract_propose"` をアクションとして出力したケースは**0件**。contract/proposeの語はstrategyメモやbroadcast内の自由文に登場するが、アクション出力としては未使用。つまり**seed=7の試合が初の自発的契約提案**であり、その記録が失われている。

**推定されるLLMの出力形式**（プロンプトの指示と一般的なLLMの出力パターンから推定）:

```json
{
  "strategy": {"reason": "P02と市場分割の契約を提案する"},
  "action": {
    "type": "contract_propose",
    "with": ["P02"],
    "terms": [
      {"type": "market_assignment", "player": "P01", "market": "M01", "round": 5},
      {"type": "market_assignment", "player": "P02", "market": "M02", "round": 5}
    ]
  }
}
```

この形式にはエンジンが期待する `obligor`、`counterparty`、`ob_type`、`round_num`、`details` のいずれも存在しない。

---

## 形式不一致の対比表

| フィールド | エンジン期待 (contracts.py L42-50) | LLMが渡す可能性が高い形式 |
|---|---|---|
| `obligor` | `term["obligor"]` — 必須、直接`[]`アクセス | **不在**（"player"/"from"等の独自キー） |
| `counterparty` | `term["counterparty"]` — 必須、直接`[]`アクセス | **不在**（"to"/"target"等） |
| `ob_type` | `ObligationType(term["ob_type"])` — 必須 | **不在**（"type"等で独自値） |
| `round_num` | `term["round_num"]` — 必須、直接`[]`アクセス | "round"（キー名不一致） |
| `details` | `term.get("details", {})` — `.get()`で安全 | 不在（amountを直接配置） |

---

## 類似リスク一覧

| アクション | リスク | 現状 | 説明 |
|---|---|---|---|
| `contract_propose` | **高** | termsの形式変換が未実装 | 今回のクラッシュ原因 |
| `bounty_post` | **中** | response_parserに変換処理がない | 不明アクション→passにフォールバック（クラッシュはしないが機能しない） |
| `contract_sign` | **低** | contract_idのみでシンプル | `.get()`ではなく直接アクセスだがクラッシュリスクは低い |
| `transfer` | **低** | amount/toの`.get()`で安全 | デフォルト値にフォールバック |
| `repay` | **低** | amountの`.get()`で安全 | デフォルト値にフォールバック |
| `dm`/`broadcast` | **低** | message文字列のみ | 安全 |

---

## エラーハンドリングの現状評価

### llm_trial.py L423

```python
except Exception as e:
    print(f"  エラー: {e}")
```

- スタックトレースが完全に失われる
- エラーの発生元（response_parser? game.py? contracts.py?）が判別不能
- `all_results` に追加されないためレポートも空になる

### llm_logger.save() の呼出しタイミング

```python
# run_trial_game() の最後で呼ばれる（L86-87）
for agent in llm_agents:
    agent.llm_logger.save()
```

- `game.run()` が例外で終了すると到達しない
- エラー発生までのLLMログ（contract_proposeの生出力を含む）が全て消失

---

## 修正方針の提案

| 優先度 | 修正内容 | 対象ファイル | 理由 |
|---|---|---|---|
| **P0** | `llm_trial.py` のexceptに `traceback.format_exc()` を記録・表示 | `scripts/llm_trial.py` | 再発時の原因特定に必須 |
| **P0** | `run_trial_game()` にfinally節を追加し、エラー時もllm_logger.save()を呼出 | `scripts/llm_trial.py` | LLMの生出力の保全に必須 |
| **P1** | `response_parser` で contract_propose の terms をエンジン形式に変換。LLMの独自キー（player→obligor、to→counterparty等）を吸収 | `llm/response_parser.py` | 根本原因の修正 |
| **P1** | プロンプトに contract_propose の terms の具体的形式を例示（obligor/counterparty/ob_type/round_num/detailsの全フィールド） | `llm/prompt_builder.py` | LLMの出力精度向上 |
| **P2** | `engine/game.py` の contract_propose 処理で KeyError をキャッチし、アクション不成立+エラーログで試合続行 | `engine/game.py` | 試合を落とさない防御（ゲームルール自体は変更しない） |
| **P3** | bounty_post の変換処理を response_parser に追加 | `llm/response_parser.py` | 将来の類似バグ予防 |
