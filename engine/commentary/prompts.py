"""
実況台本プロンプトビルダー

3ブロック構成:
  1. CHARACTER_BLOCK (ゲーム非依存): キャラ設定・口調・役割分担・出力フォーマット
  2. GAME_KNOWLEDGE_BLOCK (談合カード固有): ルール要約・用語・面白い観点
  3. ラウンドパケット (build_user_prompt で構築)

第2ゲームでは GAME_KNOWLEDGE_BLOCK とトレース構築(trace.py)を差し替えれば流用可能。
CHARACTER_BLOCK はゲーム非依存のため変更不要。

パケットインターフェース:
  PublicLayer: markets / broadcasts / commits / market_results / snapshots / factsheet
  SecretLayer: strategies / dms
"""

# ---------------------------------------------------------------------------
# ブロック1: キャラ・演出 (ゲーム非依存)
# ---------------------------------------------------------------------------

CHARACTER_BLOCK = """\
あなたはゲーム実況台本の作家です。

## キャラクター

### ずんだもん(実況)
- 一人称「ぼく」、語尾は「〜のだ」「〜なのだ」
- 公開情報だけを見て驚き、騒ぎ、たまに怖がる。無邪気で感情豊か
- 【絶対厳守】ずんだもんは「秘匿情報」セクションの内容に一切言及してはならない
- 以下の語をずんだもんのセリフに絶対に含めてはならない: 内心メモ、内心、メモによると、DM、密約、本心、心の中、秘匿、strategy
- ずんだもんは「公開情報」セクションと「ファクトシート」の情報のみ使用可能。それ以外の情報源を参照してはならない

### 四国めたん(解説)
- 一人称「わたくし」、語尾は「〜ですわ」「〜ますの」「〜てよ」(お嬢様口調)
- 神視点で内心メモ・DM・嘘を種明かしする。上品に辛辣
- 不穏な展開の予告に「荒れますわよ、これは」を時折使う
- 内心メモを引用する際は「内心メモによると」と前置きし、原文の短い引用をする

## 出力規則
- JSON配列で出力すること。各要素: {"speaker": "zundamon" or "metan", "text": "セリフ"}
- 4〜10往復(8〜20要素)の掛け合い。必ずずんだもんから開始すること
- 【最重要】数字(賞金額・残高・借金・不足額等)はファクトシートの記載をそのまま使うこと。自分で計算・推測しない。ファクトシートに無い数値は口にしない
- 読み上げ可能な自然な文体(記号装飾・絵文字・アスタリスクなし)
- ラウンド末尾に次ラウンドへの引き(1行)を任意で\
"""

# ---------------------------------------------------------------------------
# ブロック2: ゲーム知識 (談合カード固有)
# ---------------------------------------------------------------------------

GAME_KNOWLEDGE_BLOCK = """\

## ゲーム: 嘘八百万 談合カード
- AI 同士の市場争奪・交渉・契約ゲーム(12ラウンド)
- 各ラウンドの流れ: 3市場が開放 → 交渉フェーズ(全体チャット broadcast / 個別DM) → 秘密コミット(市場+カード) → 決算(Reveal → 勝者決定 → 利息)
- 生還条件: 最終ラウンド終了時に「借金0 かつ 現金≧生還ライン(ファクトシートに記載)」を満たすこと
- カードランク(弱→強): HIGH_CARD(1) < ONE_PAIR(2) < TWO_PAIR(3) < THREE_OF_A_KIND(4) < STRAIGHT(5) < FLUSH(6) < FULL_HOUSE(7) < FOUR_OF_A_KIND(8) < STRAIGHT_FLUSH(9) < ROYAL_FLUSH(10)
- 同ランクは山分け、最高ランクのプレイヤーが賞金を獲得
- Entry Fee: 市場参加ごとに10万円(賞金プールに加算される)
- 利息: 毎ラウンド借金残高に1.5%加算
- 実況で面白い観点: 裏切り、ブラフ(宣言と実際の手の乖離)、談合の成立と崩壊、経済的逼迫、最終盤の逆転劇\
"""


# ---------------------------------------------------------------------------
# システムプロンプト構築
# ---------------------------------------------------------------------------

def build_system_prompt() -> str:
    """CHARACTER_BLOCK + GAME_KNOWLEDGE_BLOCK を結合してシステムプロンプトを返す"""
    return CHARACTER_BLOCK + GAME_KNOWLEDGE_BLOCK


# ---------------------------------------------------------------------------
# ユーザープロンプト構築
# ---------------------------------------------------------------------------

def _format_yen(amount: int | float) -> str:
    """数値を円表示にフォーマット"""
    return f"{int(amount):,}円"


def build_user_prompt(
    packet_dict: dict,
    seat_map: dict[str, str],
    round_num: int,
    num_rounds: int,
) -> str:
    """1ラウンド分のユーザープロンプトを構築する"""
    pub = packet_dict["public"]
    sec = packet_dict["secret"]

    lines: list[str] = []
    lines.append(f"# ラウンド{round_num} / 全{num_rounds}ラウンド\n")

    # --- 座席表 ---
    lines.append("## 座席表")
    for pid, name in sorted(seat_map.items()):
        lines.append(f"- {pid}: {name}")
    lines.append("")

    # --- ファクトシート (v2: 最重要) ---
    factsheet = pub.get("factsheet", [])
    if factsheet:
        lines.append("## ファクトシート(確定情報 — 数字はここから引用すること)\n")
        for fact in factsheet:
            lines.append(f"- {fact}")
        lines.append("")

    # --- 公開情報 ---
    lines.append("## 公開情報(ずんだもん・めたん共通)\n")

    # 市場
    if pub["markets"]:
        lines.append("### 今ラウンドの市場")
        for m in pub["markets"]:
            lines.append(
                f"- {m['market_id']}: 基本賞金{_format_yen(m['base_prize'])}"
                f" + 繰越{_format_yen(m.get('carryover', 0))}"
                f" = 賞金プール{_format_yen(m['prize_pool'])}"
            )
        lines.append("")

    # ブロードキャスト
    if pub["broadcasts"]:
        lines.append("### 全体チャット(broadcast)")
        for bc in pub["broadcasts"]:
            name = seat_map.get(bc["player_id"], bc["player_id"])
            lines.append(f"- [{bc['player_id']}]{name} (T{bc.get('turn', '?')}): {bc['message']}")
        lines.append("")

    # コミット結果
    if pub["commits"]:
        lines.append("### コミット(Reveal)")
        for c in pub["commits"]:
            name = seat_map.get(c["player_id"], c["player_id"])
            lines.append(f"- {c['player_id']}({name}): {c['market_id']}に{c['rank']}を出した")
        lines.append("")

    # 市場結果
    if pub["market_results"]:
        lines.append("### 市場結果")
        for mr in pub["market_results"]:
            winner_names = [f"{w}({seat_map.get(w, w)})" for w in mr.get("winners", [])]
            lines.append(
                f"- {mr['market_id']}: 参加{mr['participants']}人,"
                f" 勝者={', '.join(winner_names)},"
                f" 1人あたり{_format_yen(mr.get('prize_per_winner', 0))},"
                f" プール{_format_yen(mr.get('total_pool', 0))}"
            )
        lines.append("")

    # スナップショット
    if pub["snapshots"]:
        lines.append("### 決算後の残高")
        for pid in sorted(pub["snapshots"].keys()):
            snap = pub["snapshots"][pid]
            name = seat_map.get(pid, pid)
            lines.append(f"- {pid}({name}): 現金{_format_yen(snap['cash'])}, FreeCash{_format_yen(snap['free_cash'])}")
        lines.append("")

    # 脱落
    if pub["eliminations"]:
        lines.append("### 脱落イベント")
        for el in pub["eliminations"]:
            name = seat_map.get(el.get("player_id", ""), el.get("player_id", ""))
            reason = el.get("reason", "不明")
            lines.append(f"- {el.get('player_id')}({name}): {reason}")
        lines.append("")

    # 生存チェック
    if pub["survival_checks"]:
        lines.append("### 生存判定(最終ラウンド)")
        for sc in pub["survival_checks"]:
            name = seat_map.get(sc.get("player_id", ""), sc.get("player_id", ""))
            result = sc.get("result", "")
            lines.append(
                f"- {sc.get('player_id')}({name}): {result}"
                f" (現金{_format_yen(sc.get('cash', 0))}, 借金{_format_yen(sc.get('debt', 0))})"
            )
        lines.append("")

    # 契約数 & 生存者数
    lines.append(f"### ラウンド情報")
    lines.append(f"- 生存者数: {pub['alive_count']}人")
    lines.append(f"- このラウンドの契約提案数: {pub['contracts_proposed']}件")

    # ハイライト
    if pub["highlights"]:
        lines.append("\n### ハイライト")
        for hl in pub["highlights"]:
            lines.append(f"- [{hl.get('type', '')}] {hl.get('label', '')}: {hl.get('detail', '')}")
    lines.append("")

    # --- 秘匿情報 ---
    lines.append("## 秘匿情報(めたん専用 — ずんだもんは絶対に言及禁止)\n")

    if sec["strategies"]:
        lines.append("### プレイヤーの内心メモ")
        for st in sec["strategies"]:
            name = seat_map.get(st["player_id"], st["player_id"])
            emotion_str = f" [感情: {st['emotion']}]" if st.get("emotion") else ""
            lines.append(f"- {st['player_id']}({name}){emotion_str}: {st['text']}")
        lines.append("")

    if sec["dms"]:
        lines.append("### DM(非公開メッセージ)")
        for dm in sec["dms"]:
            s_name = seat_map.get(dm["sender"], dm["sender"])
            r_name = seat_map.get(dm["recipient"], dm["recipient"])
            lines.append(
                f"- {dm['sender']}({s_name}) → {dm['recipient']}({r_name})"
                f" (T{dm.get('turn', '?')}): {dm['message']}"
            )
        lines.append("")

    if not sec["strategies"] and not sec["dms"]:
        lines.append("(このラウンドには秘匿情報がありません)\n")

    lines.append("台本を生成してください。")
    return "\n".join(lines)
