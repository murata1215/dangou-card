"""
構造化インテントプロトコル

ルールベースBot間の交渉用。dm/broadcastのmessageフィールドに
JSON文字列を載せる。エンジンは不透明文字列として扱い、
Bot層のみで解釈する。

将来のLLM Botは自由文でやり取りするため、本プロトコルはBot専用の暫定。
"""

import json
from typing import Any


def make_message(intent: str, **kwargs: Any) -> str:
    """
    構造化インテントメッセージを生成する

    Args:
        intent: インテント名（propose_collusion / accept / decline / claim）
        **kwargs: インテント固有のパラメータ

    Returns:
        JSON文字列
    """
    payload = {"intent": intent, **kwargs}
    return json.dumps(payload, ensure_ascii=False)


def parse_message(raw: str) -> dict[str, Any] | None:
    """
    メッセージからインテントをパースする

    非JSON文字列やintentキーがない場合はNoneを返す（LLMの自由文にも対応）。

    Args:
        raw: メッセージ文字列

    Returns:
        パース結果のdict、またはNone
    """
    try:
        data = json.loads(raw)
        if isinstance(data, dict) and "intent" in data:
            return data
    except (json.JSONDecodeError, TypeError):
        pass
    return None
