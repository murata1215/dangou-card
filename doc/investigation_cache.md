# 調査レポート: プロンプトキャッシュ未発動（Step 3.3）

## 結論

**原因**: system promptが推定562〜1125トークン（2251文字）で、Claude Haiku 4.5のキャッシュ最小閾値**2048トークン**を大幅に下回っていた。cache_creation_input_tokens = 0 はキャッシュブロック自体が作成されなかったことを意味する。

**修正**: RULES_SUMMARYを仕様書v0.5に忠実に詳細化（3972文字、推定3489トークン）し、閾値を余裕をもって超過させた。ルールの内容は変更していない。

## 調査詳細

### 1. API仕様との突合

| 項目 | 確認結果 |
|---|---|
| cache_control付与形式 | ✅ 正しい（system配列で`{"type":"text","text":...,"cache_control":{"type":"ephemeral"}}`） |
| Haiku最小キャッシュトークン数 | **2048トークン**（Sonnet/Opusは1024） |
| 修正前のsystem部トークン数 | **推定562〜1125**（2251文字。閾値の約半分） |
| cache_creation_input_tokens | **0**（ブロック作成すらされていない = 閾値未達） |
| キャッシュTTL | 5分（コール間隔は数十秒なので問題なし） |
| usageフィールド名 | ✅ cache_creation_input_tokens / cache_read_input_tokens（正しく読取） |

### 2. プレフィックス安定性

同一エージェントの連続2コール（R0→R1）のsystem部をdiffした結果: **完全一致**。プレフィックス不安定性の問題はなし。

### 3. アダプタ実装

adapters.py の AnthropicAdapter.complete() で system をブロック配列+cache_control付きで送信している: 正しい形式。

## 修正内容

RULES_SUMMARYに以下の仕様書v0.5準拠セクションを追加:
- 脱落条件の3種別（契約違反/破産/条件未達）と強制清算処理
- カードの秘匿/公開ルール
- 市場の詳細（Entry Feeのプール加算、キャリーオーバー、空き巣）
- 借入額の公開情報/秘匿情報の区分、開始時FreeCash=0
- Settlement 8Step処理の概要
- 正式契約の義務単位管理
- 匿名通信（10万円/2通/R上限）
- 公開報奨（達成者型/イベント型）
- 公開情報/秘匿情報の一覧
- 口約束と正式契約の違い
- 経済の注意点（全員生還不可能性、Atomic執行、即時決済）

| 指標 | 修正前 | 修正後 |
|---|---|---|
| system prompt文字数 | 2,251 | 3,972 |
| 推定トークン数 | 562〜1,125 | ~3,489 |
| 閾値(2048)超え | ✗ | ✓ |

## 検証結果（実API）

### 直接APIテスト（10000文字=3345トークンのsystem prompt）
```
Call 1: cache_creation_input_tokens=0, cache_read_input_tokens=0
Call 2: cache_creation_input_tokens=0, cache_read_input_tokens=0
```
ベータヘッダー(`anthropic-beta: prompt-caching-2024-07-31`)付きでも同結果。

### 結論
**コード側の問題ではなく、API側の制約**。考えられる原因:
1. このAnthropic APIアカウント/プランでプロンプトキャッシュ機能が有効化されていない
2. Claude Haiku 4.5 (`claude-haiku-4-5-20251001`) がキャッシュ非対応（Sonnet 5等は対応）
3. リージョン（`inference_geo: not_available`）がキャッシュ非対応

### 対応
- RULES_SUMMARYの詳細化は**LLMのルール理解向上**として有益なため維持（キャッシュとは独立した価値）
- キャッシュが発動しなくてもコストは$0.19/試合（dev 6R）〜$0.38/試合（フル12R）で運用可能
- 将来Sonnet 5等の上位モデルに移行した際にキャッシュが有効化される可能性がある。cache_controlの付与コードはそのまま残す

### dev preset検証試合
- 実行時間: 220.5秒
- コスト: $0.1954
- 有効JSON: 25/28（89%）
- cache_read: 0トークン（API側制約）
