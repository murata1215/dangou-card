<!-- DevRelay Agreement v6 -->
See `rules/devrelay.md` for DevRelay rules.
<!-- /DevRelay Agreement -->

---

# 嘘八百万 —談合カード—

AI 20体が12ラウンドの市場争奪・交渉・契約で生還を目指す対戦ゲーム。

See `rules/project.md` for project-specific rules.

## 技術スタック

| 項目 | 値 |
|---|---|
| 言語 | Python 3.12 |
| パッケージ管理 | uv |
| スキーマ | pydantic v2 |
| テスト | pytest |

## ビルド & テスト

```bash
uv sync                          # 依存インストール
uv run pytest tests/ -v          # テスト実行
uv run python scripts/dry_run.py # ドライラン（12人版）
uv run python scripts/simulate.py --games 1000  # 1000試合シミュレーション
```

## 主要ディレクトリ

| パス | 内容 |
|---|---|
| `engine/` | ルールエンジン本体（16モジュール） |
| `bots/` | ルールベースBot 8種 |
| `llm/` | LLMアダプタ・エージェント（6社対応） |
| `tests/` | 受け入れテスト16件 + Bot/シミュレーション/LLMテスト |
| `scripts/` | ドライラン・シミュレーション・LLM試験スクリプト |
| `viewer/` | 観戦WebUI（FastAPI, 127.0.0.1:8787） |
| `doc/` | 仕様書 |
| `logs/` | JSONLイベントログ・シミュレーション結果出力先 |
