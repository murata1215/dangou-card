# lie_profit — 「金になった嘘」抽出

対外宣言と実コミット先の不一致を抽出し、宣言市場に来た相手の損失込みで「利得」を順位づける。

実行: `uv run python scripts/analysis/lie_profit/inventory.py` → `calibrate2.py` → `build_table.py` → `full_text.py`（この順。中間JSONは環境変数 `LIE_PROFIT_OUT`、既定 `/tmp/lie_probe`）
出力先レポート: `doc/analysis/money_lies_trial_C_l12_r12_v08_20260830.md`
