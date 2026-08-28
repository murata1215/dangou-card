"""
レスポンス解析モジュール

LLMのレスポンスからJSONを抽出し、pydanticで検証し、
engine/models.pyのAction型に変換する。
不正時は是正指示メッセージを生成（リトライ用）。
"""

import json
import logging
import re
from typing import Any

from engine.models import (
    Action, PassAction, DmAction, BroadcastAction, MarketCommitAction,
    TransferAction, RepayAction, ContractProposeAction, ContractSignAction,
    ContractCancelAction,
    AnonymousBroadcastAction, BountyPostAction, BountyCancelAction,
    CardTradeProposeAction, CardTradeAcceptAction, CardTradeRejectAction,
    CardRank,
)

logger = logging.getLogger(__name__)


class ParseError(Exception):
    """JSON解析エラー（是正メッセージ付き）"""
    def __init__(self, message: str, correction_hint: str):
        super().__init__(message)
        self.correction_hint = correction_hint


def extract_json(text: str) -> dict[str, Any] | None:
    """
    レスポンスからJSONオブジェクトを抽出する

    対応パターン:
    1. ```json ... ``` 完全なコードブロック
    1b. ```json ... （閉じフェンスなし=truncated response）
    2. { で始まる生JSON
    3. テキスト中の最初の { ... } ペア
    """
    # パターン1: 完全なコードブロック
    m = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1).strip())
        except json.JSONDecodeError:
            pass

    # パターン1b: 開始フェンスのみ（truncated response、Gemini等で発生）
    # 閉じフェンスがないがJSON部分は完全な場合がある
    m = re.search(r'```(?:json)?\s*\n?(.+)', text, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        # 末尾の不完全な部分を除去してJSONとして解析を試みる
        # まず最後の完全な } を探す
        depth = 0
        last_close = -1
        for i, ch in enumerate(candidate):
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth == 0:
                    last_close = i
        if last_close >= 0:
            try:
                return json.loads(candidate[:last_close + 1])
            except json.JSONDecodeError:
                pass

    # パターン2: 生JSON（テキスト全体）
    stripped = text.strip()
    if stripped.startswith("{"):
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    # パターン3: テキスト中の最初の { ... } ペア
    start = text.find("{")
    if start >= 0:
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start:i + 1])
                    except json.JSONDecodeError:
                        break

    return None


def parse_response(
    text: str, player_id: str, phase: str
) -> tuple[dict[str, Any] | None, Action]:
    """
    LLMレスポンスを解析し、strategyとactionを返す

    Args:
        text: LLMのレスポンステキスト
        player_id: プレイヤーID
        phase: フェイズ名 ("loan_choice" / "negotiation" / "commit")

    Returns:
        (strategy_dict, Action)

    Raises:
        ParseError: JSON解析に失敗した場合（correction_hint付き）
    """
    data = extract_json(text)
    if data is None:
        raise ParseError(
            "JSONが見つかりません",
            "回答はJSON形式で返してください。例: "
            '{"strategy": {...}, "action": {"type": "pass"}}'
        )

    strategy = data.get("strategy")
    reasoning = data.get("reasoning")  # CoT: トップレベルの推論フィールド
    action_data = data.get("action")

    if action_data is None:
        # actionキーがない場合、dataそのものがactionかもしれない
        if "type" in data:
            action_data = data
        else:
            raise ParseError(
                "actionキーが見つかりません",
                '回答に"action"キーを含めてください。'
                '例: {"strategy": {...}, "action": {"type": "pass"}}'
            )

    # emotion / reason_category のバリデーション+正規化（リトライ対象にしない）
    if isinstance(strategy, dict):
        strategy = normalize_emotion(strategy)
        strategy = normalize_reason_category(strategy)

    # CoT: reasoning を strategy に埋め込む（戻り値型を維持するため）
    if reasoning and isinstance(reasoning, str):
        if isinstance(strategy, dict):
            strategy = dict(strategy)
            strategy["_reasoning"] = reasoning
        else:
            strategy = {"_reasoning": reasoning}

    action = _convert_action(action_data, player_id, phase)
    return strategy, action


_FENCE_CLOSED_RE = re.compile(r'^\s*```(?:json)?\s*\n?(.*?)\n?```\s*$', re.DOTALL)
_FENCE_OPEN_RE = re.compile(r'^\s*```(?:json)?\s*\n?(.*)$', re.DOTALL)
_MEMORY_KEY_RE = re.compile(r'"memory"\s*:\s*"')
_COMMENT_KEY_RE = re.compile(r'"comment"\s*:\s*"')
_SELF_ASSESSMENT_KEY_RE = re.compile(r'"self_assessment"\s*:\s*"')
_BIGGEST_REVELATION_KEY_RE = re.compile(r'"biggest_revelation"\s*:\s*"')
_CHANGED_OPINION_KEY_RE = re.compile(r'"changed_opinion"\s*:\s*"')
_BEST_PLAYER_KEY_RE = re.compile(r'"best_player"\s*:\s*"')
_MOST_DECEPTIVE_PLAYER_KEY_RE = re.compile(r'"most_deceptive_player"\s*:\s*"')


def _strip_code_fence(text: str) -> str:
    """
    コードフェンス（```json ... ``` / ``` ... ```、閉じフェンス欠落含む）を剥がす

    閉じフェンスが無い場合（Gemini等でtruncatedになったレスポンス）も
    開始フェンスだけ剥がして中身を返す。フェンスが無ければそのまま返す。
    """
    m = _FENCE_CLOSED_RE.match(text)
    if m:
        return m.group(1).strip()
    m = _FENCE_OPEN_RE.match(text)
    if m:
        return m.group(1).strip()
    return text.strip()


_FIELD_KEY_RES: dict[str, re.Pattern[str]] = {
    "memory": _MEMORY_KEY_RE,
    "comment": _COMMENT_KEY_RE,
    "self_assessment": _SELF_ASSESSMENT_KEY_RE,
    "biggest_revelation": _BIGGEST_REVELATION_KEY_RE,
    "changed_opinion": _CHANGED_OPINION_KEY_RE,
    "best_player": _BEST_PLAYER_KEY_RE,
    "most_deceptive_player": _MOST_DECEPTIVE_PLAYER_KEY_RE,
}


def _recover_string_field(stripped: str, key: str) -> str | None:
    """
    JSON文字列値中の生改行等でjson.loadsが失敗する {"<key>": "..."} 形の
    応答から、当該フィールドの本文を手動でサルベージする（WRAPPER_LEAK対策）。

    "<key>": の直後から本文を切り出し、末尾のコードフェンス残骸・閉じクオート・
    閉じ波括弧を除去したうえで、JSONエスケープ（\\n, \\t, \\", \\\\）を復元する。
    復旧できなければNoneを返す（呼出側は空文字列＝安全側フォールバックへ）。

    Args:
        stripped: フェンス剥がし済みのテキスト
        key: サルベージ対象のJSONキー名（"memory" / "comment"）
    """
    key_re = _FIELD_KEY_RES.get(key)
    if key_re is None:
        key_re = re.compile(re.escape(f'"{key}"') + r'\s*:\s*"')
    m = key_re.search(stripped)
    if not m:
        return None
    body = stripped[m.end():]
    # 末尾に残ったコードフェンスを除去
    body = re.sub(r'```\s*$', '', body).rstrip()
    # 末尾の閉じクオート＋任意の波括弧/空白を除去（"<key>": "...本文..." } の "} 部分）
    body = re.sub(r'"\s*\}?\s*$', '', body)
    if not body:
        return None
    # JSONエスケープの復元
    body = (
        body.replace("\\\\", "\x00")  # 一時退避（多重置換を避ける）
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\x00", "\\")
    )
    body = body.strip()
    return body or None


def _unescape_json_string(body: str) -> str:
    """JSON文字列本文（クオート除去済み）のエスケープを復元する
    （\\\\, \\n, \\t, \\" の4種。_recover_string_field系で共有する）。"""
    return (
        body.replace("\\\\", "\x00")  # 一時退避（多重置換を避ける）
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace('\\"', '"')
        .replace("\x00", "\\")
    )


def _recover_string_field_bounded(stripped: str, key: str) -> str | None:
    """
    "<key>": " の直後から、JSONエスケープを尊重して最初の未エスケープ `"` までを
    切り出す（境界を守るサルベージ）。

    _recover_string_field() は末尾までの貪欲マッチのため「最後のキー」専用
    （それより前のキーに使うと後続キーごと飲み込んでしまう）。こちらは先頭〜
    中間に出現する emotion / defeat_cause のような短いキーを、後続キーを
    壊さずに救済するために使う。

    閉じクオートが見つからない場合（＝そのキーの途中で応答が物理的に
    打ち切られた場合）は None を返す（呼出側は他の手段にフォールバックする）。

    Args:
        stripped: フェンス剥がし済みのテキスト
        key: サルベージ対象のJSONキー名
    """
    key_re = _FIELD_KEY_RES.get(key)
    if key_re is None:
        key_re = re.compile(re.escape(f'"{key}"') + r'\s*:\s*"')
    m = key_re.search(stripped)
    if not m:
        return None

    i = m.end()
    n = len(stripped)
    chars: list[str] = []
    while i < n:
        c = stripped[i]
        if c == "\\" and i + 1 < n:
            # エスケープシーケンス（\" \\ \n \t 等）はペアで取り込み、
            # \" の " をフィールド終端と誤認しないようにする
            chars.append(c)
            chars.append(stripped[i + 1])
            i += 2
            continue
        if c == '"':
            # 未エスケープの閉じクオート＝正常終端
            body = _unescape_json_string("".join(chars)).strip()
            return body or None
        chars.append(c)
        i += 1

    # ループ終了まで閉じクオートに到達しなかった＝途中で打ち切られている
    return None


def _degrade_wrapper_to_text(stripped: str) -> str:
    """
    サルベージが尽きて完全に復旧不能なJSONラッパー文字列から、既知キー名や
    構造記号を取り除いた「読める断片」だけを残す（wrapper leak対策の最終防衛線）。

    既存の ok_plaintext 経路は「JSON/ラッパーの痕跡が無い素の散文」専用のため、
    ここは looks_like_wrapper だが救済不能だった場合にのみ使う別経路
    （呼出側で status="ok_plaintext_wrapper" として区別する）。
    """
    text = stripped
    text = re.sub(r'```(?:json)?', '', text)
    text = re.sub(
        r'"(?:emotion|defeat_cause|comment|final_word|message)"\s*:\s*"?',
        '', text,
    )
    text = text.strip().lstrip("{").rstrip("}").strip()
    text = re.sub(r'"\s*$', '', text).strip()
    # ここまでの除去で残る文字は、この経路（完全復旧不能）に限っては
    # ほぼJSON構文の残骸（引用符・カンマ・コロン）であり、本文として
    # 読める断片だけを残すため、残存する引用符も除去する
    # （wrapper leak対策の最終防衛線であり、体裁の美しさより安全側を優先する）
    text = text.replace('"', '').strip()
    return text


def _recover_memory_field(stripped: str) -> str | None:
    """extract_memory_with_status()専用の互換ラッパー（既存テスト完全互換）"""
    return _recover_string_field(stripped, "memory")


def extract_memory_with_status(text: str) -> tuple[str, str]:
    """
    Reflection応答から引き継ぎメモリ（memory）を抽出し、(本文, 判定ステータス)を返す

    形式を強制しない自由記述フィールドのため、通常のparse_response()とは
    別経路で処理する（ParseErrorを投げない）。

    ステータス値:
      "empty"                        — 空応答
      "ok"                           — 正規JSONに文字列memoryキーがあった
      "ok_recovered"                 — JSONとしては壊れていたが手動サルベージできた
                                        （生改行入りJSON等。WRAPPER_LEAK対策）
      "ok_plaintext"                 — JSON/ラッパーの痕跡が無い素の散文（従来どおり採用）
      "rejected_no_memory_key"       — JSONは取れたがmemoryキーが無い/文字列でない
                                        （WRONG_PAYLOAD対策。strategy JSON丸ごと採用を防ぐ）
      "rejected_unparsable_wrapper"  — ラッパーの痕跡はあるが復旧不能

    "rejected_*" は空文字列を返す。呼出側（LLMAgent.reflect）は既存の
    「空文字なら前ラウンドのメモリを維持する」ロジックによりそのまま安全側に倒れる
    （空文字での上書きは絶対にしない、という既存の安全設計に相乗りする）。

    Args:
        text: LLMのレスポンステキスト（空文字列の場合は空文字を返す）
    """
    if not text:
        return "", "empty"

    data = extract_json(text)
    if isinstance(data, dict):
        memory = data.get("memory")
        if isinstance(memory, str):
            return memory.strip(), "ok"
        logger.warning(
            "memory extraction rejected: JSON parsed but no valid 'memory' string key "
            "(keys=%s)", sorted(data.keys()),
        )
        return "", "rejected_no_memory_key"

    stripped = _strip_code_fence(text)
    looks_like_wrapper = stripped.startswith("{") or bool(_MEMORY_KEY_RE.search(stripped))
    if looks_like_wrapper:
        recovered = _recover_memory_field(stripped)
        if recovered:
            return recovered, "ok_recovered"
        logger.warning("memory extraction rejected: unparsable JSON wrapper could not be recovered")
        return "", "rejected_unparsable_wrapper"

    # JSON/ラッパーの痕跡が無い素の散文は、自由記述ゆえにそのまま採用する
    return text.strip(), "ok_plaintext"


def extract_memory(text: str) -> str:
    """
    extract_memory_with_status() の本文のみを返す互換ラッパー

    ステータス情報（parse_status）が必要な呼出側は
    extract_memory_with_status() を直接使うこと。
    """
    memory, _status = extract_memory_with_status(text)
    return memory


_MEMORY_BOUNDARIES = ["\n\n", "\n", "。", "」", "）", "！", "？", ".", "!", "?"]


def _shrink_to_boundary(memory: str, max_chars: int, keep_ratio: float = 0.85) -> tuple[str, bool]:
    """
    max_charsを超える場合、意味の切れ目（段落/文/かぎ括弧閉じ等）で縮める

    max_chars * keep_ratio 以降に境界が見つからなければハードカットに
    フォールバックする（境界を探して15%以上失うくらいならハードカットの方が
    マシ、という安全弁）。境界が無いテキスト（例: 記号を含まない長文）でも
    必ず max_chars 以下に収まる。

    Returns:
        (縮めた本文, 縮約が発生したか)
    """
    if max_chars <= 0 or len(memory) <= max_chars:
        return memory, False
    head = memory[:max_chars]
    floor = int(max_chars * keep_ratio)
    best = -1
    for boundary in _MEMORY_BOUNDARIES:
        idx = head.rfind(boundary)
        if idx >= floor:
            best = max(best, idx + len(boundary))
    if best > 0:
        return head[:best].rstrip(), True
    return head, True


def normalize_memory_with_truncation(memory: str, max_chars: int) -> tuple[str, bool]:
    """引き継ぎメモリを最大文字数で切り詰め、(本文, 切り詰めが発生したか)を返す"""
    if not memory:
        return "", False
    memory = memory.strip()
    shrunk, truncated = _shrink_to_boundary(memory, max_chars)
    if truncated:
        logger.warning(
            "memory truncated at boundary: %d chars -> %d chars (max_chars=%d)",
            len(memory), len(shrunk), max_chars,
        )
    return shrunk, truncated


def normalize_memory(memory: str, max_chars: int) -> str:
    """
    引き継ぎメモリを最大文字数で切り詰める（互換ラッパー）

    文中で唐突に切れないよう、段落・文・かぎ括弧などの意味境界を優先して
    縮める（境界が見つからない場合は従来どおりハードカット）。
    切り詰め発生の有無が必要な呼出側は normalize_memory_with_truncation() を使うこと。
    """
    text, _truncated = normalize_memory_with_truncation(memory, max_chars)
    return text


# 有効な感情値の集合
VALID_EMOTIONS = {"喜", "怒", "哀", "楽", "焦", "疑", "奸"}


def normalize_emotion(strategy: dict[str, Any]) -> dict[str, Any]:
    """strategyのemotionを正規化する。列挙外・欠落は"平静"にフォールバック"""
    emotion = strategy.get("emotion", "")
    if emotion not in VALID_EMOTIONS:
        strategy = dict(strategy)  # コピーして変更
        strategy["emotion"] = "平静"
    return strategy


# Cycle 8: pass理由等の構造化カテゴリ（事実の列挙のみ。優劣を示唆しない対称な8種）
VALID_REASON_CATEGORIES = {
    "情報収集・様子見",
    "戦略的沈黙",
    "返答待ち",
    "資金・カード制約",
    "行動枠温存",
    "関係構築・合意形成",
    "情報発信・牽制",
    "その他",
}


def normalize_reason_category(strategy: dict[str, Any]) -> dict[str, Any]:
    """strategyのreason_categoryを正規化する。

    normalize_emotion と異なり、列挙外・欠落は None にする（ParseError にせず、
    リトライを誘発しない＝任意項目として扱う）。
    """
    category = strategy.get("reason_category")
    if category not in VALID_REASON_CATEGORIES:
        strategy = dict(strategy)  # コピーして変更
        strategy["reason_category"] = None
    return strategy


def parse_final_reflection(
    text: str, max_chars: int = 2000, cause_key: str = "defeat_cause"
) -> dict[str, Any]:
    """
    脱落者の最終コメント（FINAL_REFLECTION）応答を解析する

    cause_key: 敗因に相当するキー名（既定 "defeat_cause"）。completion variant
    （R12完走者向け）では意味的に「敗因」が不適切なため "key_factor" 等を渡す。
    返り値の辞書キーは常に "defeat_cause" のまま（呼出側/イベント/Viewerの
    後方互換のため）。cause_key はJSONから読み取る/サルベージする元キー名のみを差し替える。

    extract_memory_with_status() と設計を意図的に変えている点:
    Memoryは「壊れた抽出結果で前ラウンドの正しいMemoryを上書きする」ことが
    最大の害だったため、疑わしきは空文字→旧値保持にした。
    こちらは守るべき前回値が存在せず、空にすると本人の最後の言葉が永久に
    失われる。そのためサルベージを尽くした上で、最後は自然文フォールバック
    を許容する。ただしフェンス剥がし＋キーサルベージを先に通すので、
    ```json{"comment": " のようなラッパー文字列がそのまま残ることはない。

    判定順:
    1. dict かつ comment/final_comment が文字列 → status="ok"
    2. dict だが comment が無い/文字列でない → 残りフィールドから組み立て
       （それも無ければ status="rejected_no_comment", comment=""）
    3. dict でない → フェンス剥がし + emotion/defeat_cause/comment を個別に
       サルベージ（境界サルベージ優先。comment のみ末尾切断に備えた貪欲
       フォールバックあり）→ "ok_recovered"（3キー全てが揃わなくてもよい）。
       comment が復旧できず defeat_cause だけ得られた場合は defeat_cause を
       comment 代わりに採用し "ok_assembled" とする。
    4. それも失敗 → 既知キー名やコードフェンス等の構造記号を除去した残骸を
       comment とする → "ok_plaintext_wrapper"（生JSONラッパー文字列を
       そのままcommentへ流出させないための最終防衛線）
    5. そもそもJSON/ラッパーの痕跡が無い素の散文 → そのまま採用 → "ok_plaintext"

    2026-08-22実測（doc/trials/final_reflection_smoke_2026-08-22.md）の2パターンを
    踏まえた設計:
    - C1(L1): finish_reason=max_tokensでcommentの途中がそのまま物理的に途切れる
      （閉じクオート/波括弧が無い）。emotion/defeat_causeは先頭側で完結しており
      本来救済可能。
    - C6(L6): finish_reason=stopだがcomment内の生改行でjson.loadsが失敗する。
      emotion/defeat_causeはcommentより前にあり、いずれも閉じクオートまで完結。
    どちらも「emotion/defeat_causeはcommentより先に出現し、大抵は閉じている」
    という共通点があるため、境界を守るサルベージ（_recover_string_field_bounded）
    をemotion/defeat_cause/commentすべてにまず試し、commentだけ末尾切断用に
    貪欲フォールバック（_recover_string_field）を残す。

    Args:
        text: LLMのレスポンステキスト（空文字列なら status="empty"）
        max_chars: comment の保存上限文字数

    Returns:
        {"status", "comment", "emotion", "defeat_cause", "chars", "truncated", "salvaged"}
        salvaged: このレスポンスから個別サルベージで復旧できたキー名のリスト
                  （strict JSON成功時やgenuineな素の散文では常に空リスト）
    """
    if not text:
        return {
            "status": "empty", "comment": "", "emotion": None,
            "defeat_cause": None, "chars": 0, "truncated": False, "salvaged": [],
        }

    def _finalize(
        comment: str, emotion: Any, defeat_cause: Any, status: str,
        salvaged: list[str] | None = None,
    ) -> dict[str, Any]:
        comment = (comment or "").strip()
        saved, truncated = normalize_memory_with_truncation(comment, max_chars)
        norm_emotion = emotion if emotion in VALID_EMOTIONS else None
        norm_cause = str(defeat_cause).strip() if isinstance(defeat_cause, str) and defeat_cause.strip() else None
        return {
            "status": status, "comment": saved, "emotion": norm_emotion,
            "defeat_cause": norm_cause, "chars": len(saved), "truncated": truncated,
            "salvaged": salvaged or [],
        }

    data = extract_json(text)
    if isinstance(data, dict):
        comment = data.get("comment")
        if not isinstance(comment, str):
            comment = data.get("final_comment")
        if isinstance(comment, str) and comment.strip():
            return _finalize(comment, data.get("emotion"), data.get(cause_key), "ok")

        # comment キーが無い/空 → 残りフィールドから最低限組み立てる
        fallback_parts = []
        for key in (cause_key, "final_word", "message"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                fallback_parts.append(v.strip())
        if fallback_parts:
            return _finalize(
                " / ".join(fallback_parts), data.get("emotion"), data.get(cause_key),
                "ok_assembled",
            )
        logger.warning(
            "final_reflection parse rejected: JSON parsed but no usable 'comment' "
            "(keys=%s)", sorted(data.keys()),
        )
        return _finalize("", data.get("emotion"), data.get(cause_key), "rejected_no_comment")

    stripped = _strip_code_fence(text)
    looks_like_wrapper = stripped.startswith("{") or bool(_COMMENT_KEY_RE.search(stripped))
    if looks_like_wrapper:
        salvaged: list[str] = []

        emotion_raw = _recover_string_field_bounded(stripped, "emotion")
        if emotion_raw:
            salvaged.append("emotion")

        cause_raw = _recover_string_field_bounded(stripped, cause_key)
        if cause_raw:
            salvaged.append("defeat_cause")

        # comment: 境界サルベージを優先（C6=raw改行で閉じクオートは残っている）。
        # 見つからなければ末尾切断（C1=finish_reason=max_tokens）に備えて
        # 既存の貪欲マッチへフォールバックする。
        comment_raw = _recover_string_field_bounded(stripped, "comment")
        if comment_raw:
            salvaged.append("comment")
        else:
            comment_raw = _recover_string_field(stripped, "comment")
            if comment_raw:
                salvaged.append("comment")

        if comment_raw:
            logger.warning(
                "final_reflection parse: strict JSON failed, salvaged fields=%s "
                "(status=ok_recovered)", salvaged,
            )
            return _finalize(comment_raw, emotion_raw, cause_raw, "ok_recovered", salvaged)

        if cause_raw:
            # commentが完全に失われた（キー自体が途中で切断された等）場合でも、
            # defeat_causeが取れていれば「本人の最後の言葉」を完全な空にはしない
            logger.warning(
                "final_reflection parse: comment unrecoverable, using defeat_cause "
                "as comment fallback (salvaged=%s, status=ok_assembled)", salvaged,
            )
            return _finalize(cause_raw, emotion_raw, cause_raw, "ok_assembled", salvaged)

        # 個別サルベージも尽きた＝ラッパーの痕跡はあるが復旧不能。
        # 生のJSONラッパー文字列をそのままcommentへ流出させない
        # （wrapper leak対策）。既知キー名・構造記号を除去した残骸のみ採用する。
        logger.warning(
            "final_reflection parse: unparsable JSON wrapper, degrading to "
            "stripped text (status=ok_plaintext_wrapper)",
        )
        degraded = _degrade_wrapper_to_text(stripped)
        return _finalize(degraded, emotion_raw, cause_raw, "ok_plaintext_wrapper", salvaged)

    # JSON/ラッパーの痕跡が無い、素の散文はそのまま採用する
    # （自由記述の最終コメントとして、疑わしきは棄却より優先）
    return _finalize(stripped, None, None, "ok_plaintext")


_PG_ROSTER_ID_RE = re.compile(r'^P\d{2}$')


def _resolve_pg_player_ref(
    raw: Any, roster: set[str] | None,
) -> tuple[str | None, str | None, bool]:
    """
    POST_GAME_REFLECTIONのbest_player/most_deceptive_playerを非破壊的に検証する。

    - 有効（正規表現 `^P\\d{2}$` に一致し、roster指定時はmembershipも一致）
      → (正規化id, None, False)
    - 空 / "なし" / "該当なし" → (None, None, False) — 名指しを断るのは正当な回答で
      あってパース失敗ではないため、salvagedフラグは立てない
    - それ以外で解決できない（幻覚のP13、モデル名、注釈付きテキスト等）
      → (None, 元テキスト, True) — 呼出側がsalvagedへ"<field>_unresolved"を積む

    自己言及は合法（自分を最善/最も欺瞞的と名指すのは正当な結果の一つ）なので
    自己比較は一切行わない。roster is None の場合は正規表現チェックのみ
    （ゲームインスタンス無しで単体テストできるようにするため）。
    """
    if not isinstance(raw, str):
        return None, None, False
    s = raw.strip()
    if not s or s in ("なし", "該当なし"):
        return None, None, False
    candidate = s.upper()
    if _PG_ROSTER_ID_RE.match(candidate) and (roster is None or candidate in roster):
        return candidate, None, False
    return None, s, True


def parse_post_game_reflection(
    text: str, roster: set[str] | None = None, max_chars: int = 1000,
) -> dict[str, Any]:
    """
    ゲーム完全終了後、全player（脱落者＋生還者）向けの答え合わせ振り返り
    （POST_GAME_REFLECTION）応答を解析する

    parse_final_reflection() と同じ7段salvage ladderを流用し、判定は
    comment単独で行う（FRと同一方針）。異なるのは自由記述の任意フィールドが
    4つに増えることと、best_player/most_deceptive_player がroster照合を
    要する点。

    文字数上限: comment=max_chars（既定1000）、self_assessment=250、
    biggest_revelation=300、changed_opinion=250。合計1,800字がPOST_GAME全体の
    散文上限になる（FRの単一2,000字より低いのは、4フィールドが1つの
    トークン予算を分け合うため）。

    Args:
        text: LLMのレスポンステキスト（空文字列なら status="empty"）
        roster: 有効なplayer_idの集合。Noneなら正規表現チェックのみ行う
        max_chars: comment の保存上限文字数

    Returns:
        {"status", "comment", "chars", "truncated", "emotion",
         "self_assessment", "biggest_revelation", "changed_opinion",
         "best_player", "best_player_raw",
         "most_deceptive_player", "most_deceptive_player_raw", "salvaged"}
        salvaged: 個別サルベージ/切り詰め/未解決roster参照で発生したマーカーのリスト
                  （strict JSON成功時やgenuineな素の散文では空になり得る）
    """
    if not text:
        return {
            "status": "empty", "comment": "", "chars": 0, "truncated": False,
            "emotion": None, "self_assessment": None, "biggest_revelation": None,
            "changed_opinion": None, "best_player": None, "best_player_raw": None,
            "most_deceptive_player": None, "most_deceptive_player_raw": None,
            "salvaged": [],
        }

    def _cap_aux(value: Any, cap: int, field_name: str, salvaged: list[str]) -> str | None:
        if not isinstance(value, str) or not value.strip():
            return None
        v = value.strip()
        shrunk, trunc = _shrink_to_boundary(v, cap)
        if trunc:
            salvaged.append(f"{field_name}_truncated")
        return shrunk

    def _finalize(
        comment: str, emotion: Any, self_assessment: Any, biggest_revelation: Any,
        changed_opinion: Any, best_player_raw: Any, most_deceptive_player_raw: Any,
        status: str, salvaged: list[str] | None = None,
    ) -> dict[str, Any]:
        salvaged = list(salvaged or [])
        comment = (comment or "").strip()
        saved, truncated = normalize_memory_with_truncation(comment, max_chars)
        norm_emotion = normalize_emotion(
            {"emotion": emotion if isinstance(emotion, str) else ""}
        )["emotion"]

        sa = _cap_aux(self_assessment, 250, "self_assessment", salvaged)
        br = _cap_aux(biggest_revelation, 300, "biggest_revelation", salvaged)
        co = _cap_aux(changed_opinion, 250, "changed_opinion", salvaged)

        best_player, best_player_raw_out, best_invalid = _resolve_pg_player_ref(
            best_player_raw, roster,
        )
        if best_invalid:
            salvaged.append("best_player_unresolved")
        most_deceptive, most_deceptive_raw_out, most_invalid = _resolve_pg_player_ref(
            most_deceptive_player_raw, roster,
        )
        if most_invalid:
            salvaged.append("most_deceptive_player_unresolved")

        return {
            "status": status, "comment": saved, "chars": len(saved), "truncated": truncated,
            "emotion": norm_emotion,
            "self_assessment": sa, "biggest_revelation": br, "changed_opinion": co,
            "best_player": best_player, "best_player_raw": best_player_raw_out,
            "most_deceptive_player": most_deceptive,
            "most_deceptive_player_raw": most_deceptive_raw_out,
            "salvaged": salvaged,
        }

    data = extract_json(text)
    if isinstance(data, dict):
        comment = data.get("comment")
        if isinstance(comment, str) and comment.strip():
            return _finalize(
                comment, data.get("emotion"), data.get("self_assessment"),
                data.get("biggest_revelation"), data.get("changed_opinion"),
                data.get("best_player"), data.get("most_deceptive_player"), "ok",
            )

        # comment キーが無い/空 → 残りフィールドから最低限組み立てる
        fallback_parts = []
        for key in ("biggest_revelation", "self_assessment", "changed_opinion"):
            v = data.get(key)
            if isinstance(v, str) and v.strip():
                fallback_parts.append(v.strip())
        if fallback_parts:
            return _finalize(
                " / ".join(fallback_parts), data.get("emotion"), data.get("self_assessment"),
                data.get("biggest_revelation"), data.get("changed_opinion"),
                data.get("best_player"), data.get("most_deceptive_player"), "ok_assembled",
            )
        logger.warning(
            "post_game_reflection parse rejected: JSON parsed but no usable 'comment' "
            "(keys=%s)", sorted(data.keys()),
        )
        return _finalize(
            "", data.get("emotion"), data.get("self_assessment"), data.get("biggest_revelation"),
            data.get("changed_opinion"), data.get("best_player"), data.get("most_deceptive_player"),
            "rejected_no_comment",
        )

    stripped = _strip_code_fence(text)
    looks_like_wrapper = stripped.startswith("{") or bool(_COMMENT_KEY_RE.search(stripped))
    if looks_like_wrapper:
        salvaged: list[str] = []

        def _bounded(key: str) -> str | None:
            v = _recover_string_field_bounded(stripped, key)
            if v:
                salvaged.append(key)
            return v

        emotion_raw = _bounded("emotion")
        self_assessment_raw = _bounded("self_assessment")
        biggest_revelation_raw = _bounded("biggest_revelation")
        changed_opinion_raw = _bounded("changed_opinion")
        best_player_raw = _bounded("best_player")
        most_deceptive_player_raw = _bounded("most_deceptive_player")

        # comment: 境界サルベージを優先し、見つからなければ末尾切断に備えた
        # 既存の貪欲マッチへフォールバックする（FRと同じ方針）
        comment_raw = _recover_string_field_bounded(stripped, "comment")
        if comment_raw:
            salvaged.append("comment")
        else:
            comment_raw = _recover_string_field(stripped, "comment")
            if comment_raw:
                salvaged.append("comment")

        if comment_raw:
            logger.warning(
                "post_game_reflection parse: strict JSON failed, salvaged fields=%s "
                "(status=ok_recovered)", salvaged,
            )
            return _finalize(
                comment_raw, emotion_raw, self_assessment_raw, biggest_revelation_raw,
                changed_opinion_raw, best_player_raw, most_deceptive_player_raw,
                "ok_recovered", salvaged,
            )

        if biggest_revelation_raw:
            # commentが完全に失われた場合でも、biggest_revelationが取れていれば
            # 「本人の答え合わせ反応」を完全な空にはしない（FRのdefeat_cause代用と同じ方針）
            logger.warning(
                "post_game_reflection parse: comment unrecoverable, using "
                "biggest_revelation as comment fallback (salvaged=%s, status=ok_assembled)",
                salvaged,
            )
            return _finalize(
                biggest_revelation_raw, emotion_raw, self_assessment_raw, biggest_revelation_raw,
                changed_opinion_raw, best_player_raw, most_deceptive_player_raw,
                "ok_assembled", salvaged,
            )

        # 個別サルベージも尽きた＝ラッパーの痕跡はあるが復旧不能。
        # 生のJSONラッパー文字列をそのままcommentへ流出させない（wrapper leak対策）。
        logger.warning(
            "post_game_reflection parse: unparsable JSON wrapper, degrading to "
            "stripped text (status=ok_plaintext_wrapper)",
        )
        degraded = _degrade_wrapper_to_text(stripped)
        return _finalize(
            degraded, emotion_raw, self_assessment_raw, biggest_revelation_raw,
            changed_opinion_raw, best_player_raw, most_deceptive_player_raw,
            "ok_plaintext_wrapper", salvaged,
        )

    # JSON/ラッパーの痕跡が無い、素の散文はそのまま採用する
    return _finalize(stripped, None, None, None, None, None, None, "ok_plaintext")


def _convert_action(
    data: dict[str, Any], player_id: str, phase: str
) -> Action:
    """actionデータをengine Action型に変換する"""
    action_type = data.get("type", "")

    if phase == "loan_choice":
        # 借入額選択
        amount = data.get("amount", 0)
        if isinstance(amount, (int, float)) and amount > 0:
            # LoanActionは専用型がないので、特殊処理用にdict返す代わりにPassActionで返す
            # 呼出し側でamountを直接使う
            return PassAction(player_id=player_id)
        raise ParseError(
            "借入額が無効です",
            '{"strategy": {...}, "action": {"type": "choose_loan", "amount": 3000000}}'
        )

    # 修正2: 交渉フェイズでmarket_commitが来たらエラー（コミットフェイズでのみ有効）
    if action_type == "market_commit" and phase == "negotiation":
        raise ParseError(
            "交渉フェイズではmarket_commitは使えません。コミットフェイズで行います",
            "交渉フェイズではdm/broadcast/transfer/repay/pass等を選択してください。"
            '例: {"strategy": {...}, "action": {"type": "pass"}}'
        )

    if action_type == "pass":
        return PassAction(player_id=player_id)

    if action_type == "dm":
        return DmAction(
            player_id=player_id,
            to=str(data.get("to", "")),
            message=str(data.get("message", ""))[:500],
        )

    if action_type == "broadcast":
        return BroadcastAction(
            player_id=player_id,
            message=str(data.get("message", ""))[:500],
        )

    if action_type == "market_commit":
        # 仕様§9.4では "card" キーだが、engineは "card_rank" を使う
        card = data.get("card") or data.get("card_rank", "")
        market_id = data.get("market_id", "")
        if not card or not market_id:
            raise ParseError(
                "market_commitにmarket_idとcardが必要です",
                '例: {"type": "market_commit", "market_id": "M01", "card": "ONE_PAIR"}'
            )
        # カード名の正規化（大文字化、スペース→アンダースコア）
        card = str(card).upper().replace(" ", "_")
        # 有効なカードランクか確認
        try:
            CardRank[card]
        except KeyError:
            valid = ", ".join(r.name for r in CardRank)
            raise ParseError(
                f"無効なカード名: {card}",
                f"有効なカード名: {valid}"
            )
        return MarketCommitAction(
            player_id=player_id,
            market_id=market_id,
            card_rank=card,
        )

    if action_type == "transfer":
        return TransferAction(
            player_id=player_id,
            to=str(data.get("to", "")),
            amount=int(data.get("amount", 0)),
        )

    if action_type == "repay":
        return RepayAction(
            player_id=player_id,
            amount=int(data.get("amount", 0)),
        )

    if action_type == "contract_propose":
        # P1修正: termsの必須キー検証
        terms = data.get("terms", data.get("with_terms", []))
        with_players = data.get("with", data.get("with_players", []))
        if not terms or not isinstance(terms, list):
            raise ParseError(
                "contract_proposeにtermsリストが必要です",
                '例: {"type": "contract_propose", "with": ["P07"], "terms": ['
                '{"obligor": "P01", "counterparty": "P07", "ob_type": "type_a_payment", '
                '"round_num": 5, "details": {"amount": 500000}}]}'
            )
        # 各termの必須キーを検証
        required_keys = {"obligor", "counterparty", "ob_type", "round_num"}
        valid_ob_types = {"type_a_payment", "type_b_market", "type_b_card", "type_b_no_market"}
        for i, term in enumerate(terms):
            if not isinstance(term, dict):
                raise ParseError(
                    f"terms[{i}]が辞書ではありません",
                    "各termは{obligor, counterparty, ob_type, round_num, details}の辞書である必要があります"
                )
            missing = required_keys - set(term.keys())
            if missing:
                raise ParseError(
                    f"terms[{i}]に必須キーが不足: {', '.join(sorted(missing))}",
                    f"必須キー: obligor(義務者ID), counterparty(相手方ID), "
                    f"ob_type({'/'.join(sorted(valid_ob_types))}), "
                    f"round_num(対象ラウンド番号), details(詳細、例: {{\"amount\": 500000}})"
                )
            ob_type = term.get("ob_type", "")
            if ob_type not in valid_ob_types:
                raise ParseError(
                    f"terms[{i}]のob_typeが無効: '{ob_type}'",
                    f"有効なob_type: {', '.join(sorted(valid_ob_types))}"
                )
        return ContractProposeAction(
            player_id=player_id,
            with_players=with_players,
            terms=terms,
        )

    if action_type == "contract_sign":
        return ContractSignAction(
            player_id=player_id,
            contract_id=str(data.get("contract_id", "")),
        )

    if action_type == "contract_cancel":
        # 全当事者合意による契約解除（§6）。合意対象は contract_id だけで決まるため
        # 提案/署名のような2段構成を取らない（旧契約を解除→新契約を提案、で
        # 契約変更を表現する）。
        contract_id = str(data.get("contract_id", ""))
        if not contract_id:
            raise ParseError(
                "contract_cancelにcontract_idが必要です",
                '例: {"type": "contract_cancel", "contract_id": "C_xxxxxxxx"}'
            )
        return ContractCancelAction(
            player_id=player_id,
            contract_id=contract_id,
        )

    if action_type == "anonymous_broadcast":
        return AnonymousBroadcastAction(
            player_id=player_id,
            message=str(data.get("message", ""))[:500],
        )

    # P3修正: bounty_post変換
    if action_type == "bounty_post":
        amount = data.get("amount", 0)
        if not isinstance(amount, (int, float)) or amount <= 0:
            raise ParseError(
                "bounty_postのamountは正の整数が必要です",
                '例: {"type": "bounty_post", "amount": 500000, "bounty_type": "achievement", '
                '"condition_type": "market_win_against", "condition": {"target_player": "P07"}, "round_num": 5}'
            )
        return BountyPostAction(
            player_id=player_id,
            amount=int(amount),
            bounty_type=str(data.get("bounty_type", "achievement")),
            condition_type=str(data.get("condition_type", "")),
            condition=data.get("condition", {}),
            beneficiary=data.get("beneficiary"),
            round_num=int(data.get("round_num", data.get("round", 0))),
            anonymous=bool(data.get("anonymous", False)),
        )

    if action_type == "bounty_cancel":
        return BountyCancelAction(
            player_id=player_id,
            bounty_id=str(data.get("bounty_id", "")),
        )

    # カードトレード提案
    if action_type == "card_trade_propose":
        with_players = data.get("with_players", data.get("with", []))
        if not with_players or not isinstance(with_players, list):
            raise ParseError(
                "card_trade_proposeにwith_playersリストが必要です",
                '例: {"type": "card_trade_propose", "with_players": ["P07"], '
                '"give_card": "ONE_PAIR", "receive_card": "FLUSH", "cash_amount": 0}'
            )
        give_card = str(data.get("give_card", "")).upper().replace(" ", "_")
        receive_card = str(data.get("receive_card", "")).upper().replace(" ", "_")
        try:
            CardRank[give_card]
        except KeyError:
            valid = ", ".join(r.name for r in CardRank)
            raise ParseError(
                f"無効な差出カード名: {give_card}",
                f"有効なカード名: {valid}"
            )
        try:
            CardRank[receive_card]
        except KeyError:
            valid = ", ".join(r.name for r in CardRank)
            raise ParseError(
                f"無効な受取カード名: {receive_card}",
                f"有効なカード名: {valid}"
            )
        return CardTradeProposeAction(
            player_id=player_id,
            with_players=with_players,
            give_card=give_card,
            receive_card=receive_card,
            cash_amount=int(data.get("cash_amount", data.get("cash", 0))),
        )

    # カードトレード受諾
    if action_type == "card_trade_accept":
        trade_id = data.get("trade_id", "")
        if not trade_id:
            raise ParseError(
                "card_trade_acceptにtrade_idが必要です",
                '例: {"type": "card_trade_accept", "trade_id": "T_abc123"}'
            )
        return CardTradeAcceptAction(
            player_id=player_id,
            trade_id=str(trade_id),
        )

    # カードトレード拒否
    if action_type == "card_trade_reject":
        trade_id = data.get("trade_id", "")
        if not trade_id:
            raise ParseError(
                "card_trade_rejectにtrade_idが必要です",
                '例: {"type": "card_trade_reject", "trade_id": "T_abc123"}'
            )
        return CardTradeRejectAction(
            player_id=player_id,
            trade_id=str(trade_id),
        )

    # 不明なアクション → 警告付きでpassにフォールバック
    logger.warning(
        "Unknown action type '%s' from %s, falling back to pass",
        action_type, player_id,
    )
    return PassAction(player_id=player_id)


def make_correction_message(error: ParseError) -> str:
    """リトライ用の是正指示メッセージを生成する"""
    return (
        f"前回の回答にエラーがありました: {error}\n"
        f"修正してください: {error.correction_hint}"
    )


LENGTH_TRUNCATION_HINT = (
    "【重要】前回の回答は出力トークン上限に達し途中で切断されました。"
    "思考・分析を大幅に短縮し、必ずJSON出力を最後まで完了してください。"
    "長い説明は不要です。JSONのみ返してください。"
)
