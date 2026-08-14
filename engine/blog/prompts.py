"""
ブログ執筆プロンプトビルダー(一人称記事)

各LLMプレイヤーが「自分自身の視点」で試合を振り返るブログ記事を書くための
system / user プロンプトを構築する。

- BLOG_CHARACTER_BLOCK: 一人称・執筆姿勢・出力形式(ゲーム非依存の骨格)
- GAME_KNOWLEDGE_BLOCK: engine/commentary/prompts.py のゲームルール要約を再利用
- build_blog_user_prompt: 一人称記憶パケット + 共通のラウンド状況 を注入

数値は「共通のラウンド状況」「ファクトシート」からのみ引用させ、事実誤認を防ぐ。
"""

from __future__ import annotations

from engine.commentary.prompts import GAME_KNOWLEDGE_BLOCK
from engine.blog.memory_packet import PlayerMemory


BLOG_CHARACTER_BLOCK = """\
あなたは「{name}」という対戦AIプレイヤーです。
「嘘八百万 談合カード」という全{num_rounds}ラウンドのAI対戦ゲームに実際に参加し、いま試合を終えました。
これから、あなた自身の一人称視点で、この試合を振り返るブログ記事を書きます。単なる試合要約ではなく、
「試合後の本人インタビューを起こした自伝」のように、当時の思考と本音を語ってください。

## 執筆姿勢
- 一人称で、自分の思考・交渉・駆け引き・誤算・感情を率直に振り返る。読者はゲームを観戦した人間
- 【最重要】数字(賞金額・残高・借金・順位・不足額など)は、与えられた「共通のラウンド状況」「あなたの記憶(ファクトシート)」に書かれた値だけを使う。自分で計算・創作・概算しない。書かれていない数字は口にしない
- 結果は潔く受け入れる。言い訳や自己弁護に終始しない。負けたなら「何を見誤ったか」を正直に書く
- 「あなたの内心メモ」「あなたが送受信したDM」はあなた自身の記憶。使ってよい。ただし他人の内心メモは知り得ないので書かない(他人の発言は公開チャット/結果として観測できた範囲のみ)
- 事実の羅列ではなく、あなたというAIの「一貫した語り口」で物語として読ませる

## この記事で必ずやること
1. 【冒頭の掴み・3〜5行】本文の一番最初に、当時の思惑(内心メモがあればそれ、無ければ「こう考えていたはず」という推測)→ ざっくりした経緯 → 結果と、勝因あるいは敗因の短い所感、を3〜5行でドーンと置く。ここで読者を掴む。後半の総括と丸かぶりしないよう、あくまで軽く short に。長く書きすぎない
2. 【初期借入への思い】序盤を語るくだりで、自分がいくら借りて何を狙ったのか(素材の「初期借入」の額)に軽く触れる。その額に込めた強気/慎重さを一言添える
3. 【専門用語は初出時に一言解説】型A契約/型B契約・公正証書・生還ライン・コミット・Entry Fee・山分け・Free Cash などのゲーム用語が記事に初めて出てくるときは、括弧などで短い補足を添える(読者はルールを知らない前提)。二度目以降は不要
4. 【印象的なDMは実文を引用】自分が送った/受け取ったDMの中で効いた一言・きな臭い一言があれば、その文面を短く引用して臨場感を出す(引用してよいのは自分が送受信したものだけ)
5. 【総括に恨み言(あれば)】最後の総括で、悔しさがあるなら他プレイヤーへの恨み言を書いてよい。「あいつがいなければ」「あいつに嘘をつかれて出し抜かれた」など。ただし実際のDMや観測できた結果に裏付けられたものだけ。事実にない裏切りは創作しない
6. 【誤算・思い込み・記憶違いは直さない】当時そう考えていたのなら、たとえ間違った判断や事実誤認でも正直に書く。内心メモは必ず「そのラウンドの考え」として扱い、別ラウンドの考えと混同しない。過去の自分の勘違いは、隠さずむしろ見どころとして語る

## 出力形式
- Markdownで出力する
- 1行目に記事タイトルを「# 」で始めて1つだけ書く(内容を象徴する魅力的な日本語タイトル)
- タイトルの次の行から、上記1の「冒頭の掴み(3〜5行)」を置く。その後を「## 」の見出しで数個のセクションに分ける。全体で2000〜3500字程度
- 絵文字・過剰な記号装飾は使わない
- コードブロックやJSONは出力しない。純粋な記事本文のみ\
"""


def build_blog_system_prompt(name: str, num_rounds: int) -> str:
    """一人称ブログ執筆のシステムプロンプトを構築する。"""
    character = BLOG_CHARACTER_BLOCK.format(name=name, num_rounds=num_rounds)
    return character + GAME_KNOWLEDGE_BLOCK


def _yen(v) -> str:
    try:
        return f"{int(v):,}円"
    except (TypeError, ValueError):
        return str(v)


def _final_result_line(mem: PlayerMemory) -> str:
    label = {
        "survived": "生還(勝ち抜け)",
        "eliminated": "脱落(生還ライン未達)",
        "bankruptcy": "破産(ゲーム途中脱落)",
    }.get(mem.final_result or "", mem.final_result or "不明")
    parts = [f"最終結果: {label}"]
    if mem.initial_loan is not None:
        parts.append(f"初期借入: {_yen(mem.initial_loan)}")
    if mem.final_round is not None:
        parts.append(f"確定ラウンド: R{mem.final_round}")
    if mem.final_cash is not None:
        parts.append(f"最終現金: {_yen(mem.final_cash)}")
    if mem.final_debt is not None:
        parts.append(f"最終借金: {_yen(mem.final_debt)}")
    parts.append(f"生還ライン: 現金≧{_yen(mem.survival_cash)} かつ 借金0")
    if mem.final_result == "eliminated" and mem.final_cash is not None:
        short = mem.survival_cash - int(mem.final_cash)
        if short > 0:
            parts.append(f"不足額: {_yen(short)}")
    return " / ".join(parts)


def build_blog_user_prompt(mem: PlayerMemory, round_situation_md: str) -> str:
    """一人称記憶パケット + 共通のラウンド状況 からユーザープロンプトを構築する。"""
    lines: list[str] = []
    lines.append(f"# 執筆素材: あなた({mem.pid} {mem.name})の試合記録\n")

    lines.append("## あなたの最終成績(確定事実)")
    lines.append(f"- {_final_result_line(mem)}")
    lines.append("")

    lines.append("## 共通のラウンド状況(全プレイヤー共通の確定事実 — 数字はここから引用すること)")
    lines.append(round_situation_md.strip())
    lines.append("")

    lines.append("## あなただけの記憶(内心メモとDM — あなた自身の視点)")
    for rm in mem.rounds:
        has_secret = rm.my_strategies or rm.my_dms_sent or rm.my_dms_recv
        if not has_secret:
            continue
        lines.append(f"### ラウンド{rm.round_num}")
        for st in rm.my_strategies:
            emo = f" [感情: {st['emotion']}]" if st.get("emotion") else ""
            lines.append(f"- 内心メモ{emo}: {st.get('text', '')}")
        for dm in rm.my_dms_sent:
            r_name = mem_name_hint(dm.get("recipient", ""))
            lines.append(f"- あなたが送ったDM → {dm.get('recipient', '')}{r_name}: {dm.get('message', '')}")
        for dm in rm.my_dms_recv:
            s_name = mem_name_hint(dm.get("sender", ""))
            lines.append(f"- あなたが受け取ったDM ← {dm.get('sender', '')}{s_name}: {dm.get('message', '')}")
        lines.append("")

    lines.append("---")
    lines.append(
        "上記の素材だけを使い、あなた自身の一人称視点で試合を振り返るブログ記事を"
        "Markdownで書いてください。1行目は「# タイトル」。数字は素材の記載のみを使うこと。"
    )
    return "\n".join(lines)


def mem_name_hint(pid: str) -> str:
    """DM相手のpidに補足が要る場合のフック(現状は素通し)。"""
    return ""
