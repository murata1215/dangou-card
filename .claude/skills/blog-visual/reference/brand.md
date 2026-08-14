# 談合カード ビジュアル規格（brand）

## 配色（カイジ風・黒/濃紺/金）
`engine/blog/templates/base.css` の `:root` が単一の情報源。

| 変数 | 値 | 用途 |
|---|---|---|
| `--bg` | `#0d0d10` | カード地（最暗） |
| `--bg2` | `#141419` | パネル地 |
| `--panel` | `#1a1a20` | 価格/枠パネル |
| `--edge` | `#3c3c48` | 枠線 |
| `--accent` | `#c9a227` | 金（見出し・強調） |
| `--accent-hi` | `#e8d278` | 明るい金（数値・タイトル） |
| `--text` | `#e8e8ec` | 本文 |
| `--subtext` | `#96969f` | 補足 |
| `--survive` | `#2ea06a` | 生還（緑） |
| `--danger` | `#b03a3a` | 脱落/破産/嘘（赤） |

## サイズ
- OGP バナー: 1200×630（`--card title` の banner）
- SNS スクエア: 1080×1080（title square / quote / lie / showdown）
- 縦（モデル紹介）: 1080×1350
- リザルト: 1080×**可変高**（行数に応じて要素クリップで切り出し）
- `device_scale_factor=2` で 2倍解像度出力（高精細）。

## フォント
`font-family:'Noto Sans CJK JP','Noto Sans JP','Yu Gothic',sans-serif`。
このホストは `fonts-noto-cjk` 導入済（`fc-list :lang=ja` で確認可）。Chromium が
fontconfig 経由で自動解決する。フォント名は改変しない。

## カード種別と主データ源
| 種別 | テンプレ | データ源 |
|---|---|---|
| モデル紹介 | `model_card.html` | `build_roster` + `get_model`（価格/model_id）+ `MODEL_TAGLINES`/`_tier_label`/`_price_pitch` |
| タイトル | `title_card.html` | 固定コピー（SEASON 1） |
| 最終リザルト | `result_card.html` | `_standings`（free_cash基準・生還優先で整列） |
| 名言 | `quote_card.html` | `_pick_quote`（本人送信DM）or `--quote-text` |
| LIE DETECTED | `lie_card.html` | 宣言=DM引用、`--lie-actual` は任意（事実がある時のみ） |
| 運命の一手 | `showdown_card.html` | `--showdown "L,R,round,額"`（opt-in・all には含めない） |

## 禁止事項（品質と誠実性）
1. **数値の捏造禁止**: 順位・価格・金額・ラウンド・宛先はすべてソースデータ由来。
2. **SVG で文章を段組みしない**: 折り返しはブラウザ（CSS）に任せる。手置き座標で日本語を並べない。
3. **AI画像に文字を描かせない**: キャラ絵＝絵、文字＝プログラム描画で分業。
4. **事実の捏造をしない**: `--lie-actual` は観測できた事実がある時だけ。無指定なら宛先/Rのみ。
5. キャラ絵は `viewer/static/emotions/{vendor}_{emotion}.png` の既存アセットのみ（新規生成しない）。

## キャラ絵（vendor × emotion）
- vendor: anthropic / openai / google / xai / moonshot / deepseek
- emotion: anger / doubt / ease / joy / panic / sadness / smirk（ニヤリ/悪い顔）
  - `--card-emotion` 未指定時の既定: lie(LIE DETECTED)/showdown(運命の一手)=smirk、その他(model/result/quote)=ease。明示指定は全カードで最優先。
- data URI 埋め込み時に長辺を縮小（`cards_svg._face_uri(..., max_px=…)`）してファイルサイズを抑える。
