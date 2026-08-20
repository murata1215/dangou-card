"""Phase 2専用 Structured Outputs schema と静的complexity検証。"""

from __future__ import annotations

import json
import math
from typing import Any


_EMOTIONS = ["喜", "怒", "哀", "楽", "焦", "疑", "奸"]
_ACTION_TYPES = [
    "pass", "dm", "broadcast", "transfer", "repay", "contract_propose",
    "contract_sign", "anonymous_broadcast", "bounty_post", "bounty_cancel",
    "card_trade_propose", "card_trade_accept", "card_trade_reject",
]
_H1_LIGHT_FIELDS = (
    "action_type", "emotion", "to", "message", "amount",
    "with_players_json", "terms_json", "contract_id", "bounty_type",
    "condition_type", "condition_target_player", "round_num", "anonymous",
    "beneficiary", "bounty_id", "give_card", "receive_card", "cash_amount",
    "trade_id",
)
_BOUNTY_TYPES = ["", "achievement", "event"]
_CONDITION_TYPES = ["", "market_win_against", "same_market", "player_eliminated"]
_TERM_KEYS = {"obligor", "counterparty", "ob_type", "round_num", "details"}
_DETAIL_KEYS = {"amount", "market_id", "card_rank"}
_OB_TYPES = {"type_a_payment", "type_b_market", "type_b_card", "type_b_no_market"}


def build_phase2_response_schema() -> dict[str, Any]:
    """Provider-neutral canonical negotiation response schema.

    Parser aliases are deliberately excluded: structured output emits only the
    current prompt's canonical spellings while the parser remains backward
    compatible with historic responses.
    """
    details = {
        "type": "object",
        "properties": {
            "amount": {"type": "integer"},
            "market_id": {"type": "string"},
            "card_rank": {"type": "string"},
        },
        "additionalProperties": False,
    }


    term = {
        "type": "object",
        "properties": {
            "obligor": {"type": "string"},
            "counterparty": {"type": "string"},
            "ob_type": {"type": "string", "enum": ["type_a_payment", "type_b_market", "type_b_card", "type_b_no_market"]},
            "round_num": {"type": "integer"},
            "details": details,
        },
        "required": ["obligor", "counterparty", "ob_type", "round_num", "details"],
        "additionalProperties": False,
    }
    condition = {
        "type": "object",
        "properties": {"target_player": {"type": "string"}},
        "required": ["target_player"],
        "additionalProperties": False,
    }
    action = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": _ACTION_TYPES},
            # Required sentinel keeps the schema within Anthropic's 24 optional
            # parameter limit. It is ignored for non-bounty actions; achievement
            # bounties use "" as the existing falsy no-beneficiary value.
            "beneficiary": {"type": "string"},
            "to": {"type": "string"}, "message": {"type": "string"},
            "amount": {"type": "integer"}, "with": {"type": "array", "items": {"type": "string"}},
            "terms": {"type": "array", "items": term}, "contract_id": {"type": "string"},
            "bounty_type": {"type": "string", "enum": ["achievement", "event"]},
            "condition_type": {"type": "string", "enum": ["market_win_against", "same_market", "player_eliminated"]},
            "condition": condition, "round_num": {"type": "integer"}, "anonymous": {"type": "boolean"},
            "bounty_id": {"type": "string"}, "with_players": {"type": "array", "items": {"type": "string"}},
            "give_card": {"type": "string"}, "receive_card": {"type": "string"},
            "cash_amount": {"type": "integer"}, "trade_id": {"type": "string"},
        },
        "required": ["type", "beneficiary"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "strategy": {
                "type": "object",
                "properties": {
                    "target_market": {"type": "string"}, "reason": {"type": "string"},
                    "card_plan": {"type": "string"}, "current_goal": {"type": "string"},
                    "emotion": {"type": "string", "enum": _EMOTIONS},
                },
                "required": ["emotion"], "additionalProperties": False,
            },
            "action": action,
        },
        "required": ["strategy", "action"], "additionalProperties": False,
    }


def build_h1_phase2_light_schema() -> dict[str, Any]:
    """Return H1-only flat transport schema for Anthropic Structured Outputs."""
    properties: dict[str, dict[str, Any]] = {
        "action_type": {"type": "string", "enum": _ACTION_TYPES},
        "emotion": {"type": "string", "enum": _EMOTIONS},
        "to": {"type": "string"}, "message": {"type": "string"},
        "amount": {"type": "integer"}, "with_players_json": {"type": "string"},
        "terms_json": {"type": "string"}, "contract_id": {"type": "string"},
        "bounty_type": {"type": "string", "enum": _BOUNTY_TYPES},
        "condition_type": {"type": "string", "enum": _CONDITION_TYPES},
        "condition_target_player": {"type": "string"}, "round_num": {"type": "integer"},
        "anonymous": {"type": "boolean"}, "beneficiary": {"type": "string"},
        "bounty_id": {"type": "string"}, "give_card": {"type": "string"},
        "receive_card": {"type": "string"}, "cash_amount": {"type": "integer"},
        "trade_id": {"type": "string"},
    }
    return {"type": "object", "properties": properties, "required": list(_H1_LIGHT_FIELDS),
            "additionalProperties": False}


def schema_complexity(schema: dict[str, Any]) -> dict[str, int]:
    """Count Anthropic's documented optional/union parameters and objects."""
    counts = {"optional_parameters": 0, "union_parameters": 0, "objects": 0, "objects_with_additional_properties_false": 0}

    def walk(node: Any) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "object":
            counts["objects"] += 1
            if node.get("additionalProperties") is False:
                counts["objects_with_additional_properties_false"] += 1
            required = set(node.get("required", []))
            for name, child in node.get("properties", {}).items():
                if name not in required:
                    counts["optional_parameters"] += 1
                if isinstance(child, dict) and ("anyOf" in child or isinstance(child.get("type"), list)):
                    counts["union_parameters"] += 1
                walk(child)
        elif node.get("type") == "array":
            walk(node.get("items"))
        else:
            for child in node.get("anyOf", []):
                walk(child)

    walk(schema)
    return counts


def validate_phase2_schema_complexity(schema: dict[str, Any]) -> dict[str, int]:
    counts = schema_complexity(schema)
    if counts["optional_parameters"] > 24:
        raise ValueError("Phase 2 schema exceeds Anthropic optional-parameter limit (24)")
    if counts["union_parameters"] > 16:
        raise ValueError("Phase 2 schema exceeds Anthropic union-parameter limit (16)")
    if counts["objects"] != counts["objects_with_additional_properties_false"]:
        raise ValueError("Every Phase 2 schema object must set additionalProperties=false")
    return counts


def validate_h1_phase2_light_schema(schema: dict[str, Any]) -> dict[str, int]:
    """Validate H1's stricter local regression limits before provider calls."""
    counts = validate_phase2_schema_complexity(schema)
    if counts != {
        "optional_parameters": 0,
        "union_parameters": 0,
        "objects": 1,
        "objects_with_additional_properties_false": 1,
    }:
        raise ValueError("H1 light schema must be one flat object with no optional or union parameters")
    properties = schema.get("properties", {})
    if tuple(properties) != _H1_LIGHT_FIELDS or schema.get("required") != list(_H1_LIGHT_FIELDS):
        raise ValueError("H1 light schema properties or required fields do not match the transport contract")
    enum_fields = [value for value in properties.values() if "enum" in value]
    if len(enum_fields) != 4 or sum(len(value["enum"]) for value in enum_fields) != 27:
        raise ValueError("H1 light schema enum regression limit exceeded")
    if len(json.dumps(schema, ensure_ascii=False, separators=(",", ":")).encode()) > 1280:
        raise ValueError("H1 light schema serialized byte regression limit exceeded")
    return counts


def _decode_string_array(value: Any, field: str) -> list[str]:
    if not isinstance(value, str):
        raise ValueError(f"{field} must be a JSON string")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{field} must contain valid JSON") from exc
    if not isinstance(decoded, list) or any(not isinstance(item, str) for item in decoded):
        raise ValueError(f"{field} must decode to an array of strings")
    return decoded


def _decode_terms(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, str):
        raise ValueError("terms_json must be a JSON string")
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError as exc:
        raise ValueError("terms_json must contain valid JSON") from exc
    if not isinstance(decoded, list):
        raise ValueError("terms_json must decode to an array")
    for index, term in enumerate(decoded):
        if not isinstance(term, dict) or set(term) != _TERM_KEYS:
            raise ValueError(f"terms_json[{index}] must contain exactly the canonical term keys")
        if (not isinstance(term["obligor"], str) or not isinstance(term["counterparty"], str)
                or term["ob_type"] not in _OB_TYPES or type(term["round_num"]) is not int):
            raise ValueError(f"terms_json[{index}] has invalid canonical term values")
        details = term["details"]
        if not isinstance(details, dict) or not set(details) <= _DETAIL_KEYS:
            raise ValueError(f"terms_json[{index}].details has unknown keys")
        if ("amount" in details and type(details["amount"]) is not int) or (
                "market_id" in details and not isinstance(details["market_id"], str)) or (
                "card_rank" in details and not isinstance(details["card_rank"], str)):
            raise ValueError(f"terms_json[{index}].details has invalid value types")
    return decoded


def normalize_h1_phase2_transport(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a validated H1 flat transport object to the canonical response.

    A non-sentinel value in a field irrelevant to the selected action is an
    invalid model response, not data to silently discard.
    """
    if not isinstance(data, dict) or set(data) != set(_H1_LIGHT_FIELDS):
        raise ValueError("H1 transport must contain exactly the flat schema fields")
    for field in ("action_type", "emotion", "to", "message", "contract_id", "bounty_type",
                  "condition_type", "condition_target_player", "beneficiary", "bounty_id",
                  "give_card", "receive_card", "trade_id"):
        if not isinstance(data[field], str):
            raise ValueError(f"H1 transport field {field} must be a string")
    for field in ("amount", "round_num", "cash_amount"):
        if type(data[field]) is not int:
            raise ValueError(f"H1 transport field {field} must be an integer")
    if type(data["anonymous"]) is not bool:
        raise ValueError("H1 transport field anonymous must be a boolean")
    if data["action_type"] not in _ACTION_TYPES or data["emotion"] not in _EMOTIONS:
        raise ValueError("H1 transport action_type or emotion is invalid")
    if data["bounty_type"] not in _BOUNTY_TYPES or data["condition_type"] not in _CONDITION_TYPES:
        raise ValueError("H1 transport bounty enum is invalid")

    with_players = _decode_string_array(data["with_players_json"], "with_players_json")
    terms = _decode_terms(data["terms_json"])
    action_type = data["action_type"]
    used_fields: dict[str, set[str]] = {
        "pass": set(),
        "dm": {"to", "message"},
        "broadcast": {"message"},
        "transfer": {"to", "amount"},
        "repay": {"amount"},
        "contract_propose": {"with_players_json", "terms_json"},
        "contract_sign": {"contract_id"},
        "anonymous_broadcast": {"message"},
        "bounty_post": {"amount", "bounty_type", "condition_type", "condition_target_player", "round_num", "anonymous", "beneficiary"},
        "bounty_cancel": {"bounty_id"},
        "card_trade_propose": {"with_players_json", "give_card", "receive_card", "cash_amount"},
        "card_trade_accept": {"trade_id"},
        "card_trade_reject": {"trade_id"},
    }
    ignored_strings = {"to", "message", "contract_id", "bounty_type", "condition_type",
                       "condition_target_player", "beneficiary", "bounty_id", "give_card",
                       "receive_card", "trade_id"}
    ignored_ints = {"amount", "round_num", "cash_amount"}
    relevant = used_fields[action_type]
    for field in ignored_strings - relevant:
        if data[field] != "":
            raise ValueError(f"H1 transport field {field} must be its sentinel for {action_type}")
    for field in ignored_ints - relevant:
        if data[field] != 0:
            raise ValueError(f"H1 transport field {field} must be its sentinel for {action_type}")
    if "anonymous" not in relevant and data["anonymous"] is not False:
        raise ValueError(f"H1 transport field anonymous must be its sentinel for {action_type}")
    if "with_players_json" not in relevant and with_players:
        raise ValueError(f"H1 transport field with_players_json must be its sentinel for {action_type}")
    if "terms_json" not in relevant and terms:
        raise ValueError(f"H1 transport field terms_json must be its sentinel for {action_type}")

    action: dict[str, Any] = {"type": action_type}
    if action_type == "dm":
        action.update(to=data["to"], message=data["message"])
    elif action_type in {"broadcast", "anonymous_broadcast"}:
        action["message"] = data["message"]
    elif action_type == "transfer":
        action.update(to=data["to"], amount=data["amount"])
    elif action_type == "repay":
        action["amount"] = data["amount"]
    elif action_type == "contract_propose":
        action.update({"with": with_players, "terms": terms})
    elif action_type == "contract_sign":
        action["contract_id"] = data["contract_id"]
    elif action_type == "bounty_post":
        action.update(
            amount=data["amount"], bounty_type=data["bounty_type"],
            condition_type=data["condition_type"],
            condition={"target_player": data["condition_target_player"]},
            beneficiary=data["beneficiary"], round_num=data["round_num"],
            anonymous=data["anonymous"],
        )
    elif action_type == "bounty_cancel":
        action["bounty_id"] = data["bounty_id"]
    elif action_type == "card_trade_propose":
        action.update(with_players=with_players, give_card=data["give_card"],
                      receive_card=data["receive_card"], cash_amount=data["cash_amount"])
    elif action_type in {"card_trade_accept", "card_trade_reject"}:
        action["trade_id"] = data["trade_id"]
    return {"strategy": {"emotion": data["emotion"]}, "action": action}


def structured_schema_input_reserve_tokens(schema: dict[str, Any]) -> int:
    """Conservative local reserve for schema payload/provider formatting overhead."""
    return math.ceil(len(json.dumps(schema, ensure_ascii=False, separators=(",", ":"))) / 3)
