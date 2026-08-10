"""
レスポンス解析モジュール

LLMのレスポンスからJSONを抽出し、pydanticで検証し、
engine/models.pyのAction型に変換する。
不正時は是正指示メッセージを生成（リトライ用）。
"""

import json
import re
from typing import Any

from engine.models import (
    Action, PassAction, DmAction, BroadcastAction, MarketCommitAction,
    TransferAction, RepayAction, ContractProposeAction, ContractSignAction,
    AnonymousBroadcastAction, BountyPostAction, BountyCancelAction,
    CardRank,
)


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

    # emotionのバリデーション+正規化（リトライ対象にしない）
    if isinstance(strategy, dict):
        strategy = normalize_emotion(strategy)

    action = _convert_action(action_data, player_id, phase)
    return strategy, action


# 有効な感情値の集合
VALID_EMOTIONS = {"喜", "怒", "哀", "楽", "焦", "疑"}


def normalize_emotion(strategy: dict[str, Any]) -> dict[str, Any]:
    """strategyのemotionを正規化する。列挙外・欠落は"平静"にフォールバック"""
    emotion = strategy.get("emotion", "")
    if emotion not in VALID_EMOTIONS:
        strategy = dict(strategy)  # コピーして変更
        strategy["emotion"] = "平静"
    return strategy


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

    # 不明なアクション → passにフォールバック
    return PassAction(player_id=player_id)


def make_correction_message(error: ParseError) -> str:
    """リトライ用の是正指示メッセージを生成する"""
    return (
        f"前回の回答にエラーがありました: {error}\n"
        f"修正してください: {error.correction_hint}"
    )
