"""発言単位のEmotion可視化: 束縛・秘匿性・互換性のテスト

Plan: .claude/plans/tingly-beaming-tiger.md
「Emotion可視化（喜怒哀楽＋絵）」Phase 1 の検証。

対象:
- get_player_round_detail() の calls[].emotion
- get_round_states() / _collect_round_messages() の messages[].emotion
- _project_message() の可視性ルール（匿名発言・DMの秘匿境界を新たに破らないこと）
"""
import json
from pathlib import Path

import pytest

from llm.response_parser import VALID_EMOTIONS
from viewer.log_parser import get_player_round_detail, get_round_states


def _entry(pid: str, round_num: int, turn: int, action: dict, emotion: str | None = None) -> dict:
    """LLM callログ1件分。emotion指定時は entry 直下（llm_agent._update_last_log_emotion
    と同じ後付けパターン）に書く。未指定時はキー自体を持たせない（旧ログ互換の再現）。"""
    entry = {
        "player_id": pid, "model_id": "test-model", "phase": "negotiation",
        "round_num": round_num, "turn": turn,
        "response_text": json.dumps(
            {"strategy": {"reason": "r"}, "action": action}, ensure_ascii=False,
        ),
        "reasoning": "r", "reasoning_tokens": 0,
    }
    if emotion is not None:
        entry["emotion"] = emotion
    return entry


def _make_emotion_trial(tmp_path: Path) -> Path:
    """P01/P02/P03の3人、R5で各actionタイプ×emotionの束縛・秘匿性を検証するfixture。

    - turn1: P01 dm→P02 "疑"    （秘匿。public では emotion 抑制対象）
    - turn2: P01 broadcast "楽"  （公開。public でも emotion 表示対象）
    - turn3: P01 anonymous_broadcast "焦"（匿名。public では emotion 非表示、god のみ）
    - turn4: P01 pass "奸"       （非秘匿action。奸バグの回帰確認を兼ねる）
    - turn5: P01 dm→P03（emotion欠損＝旧ログ相当）
    - turn6: P01 contract_propose "喜"（秘匿。public では emotion 抑制対象）
    """
    trial = tmp_path / "trial_emotion"
    logs = trial / "llm_logs"
    logs.mkdir(parents=True)

    p01_entries = [
        _entry("P01", 5, 1, {"type": "dm", "to": "P02", "message": "疑いのDM"}, emotion="疑"),
        _entry("P01", 5, 2, {"type": "broadcast", "message": "楽観ブロードキャスト"}, emotion="楽"),
        _entry("P01", 5, 3, {"type": "anonymous_broadcast", "message": "焦りの匿名発言"}, emotion="焦"),
        _entry("P01", 5, 4, {"type": "pass"}, emotion="奸"),
        _entry("P01", 5, 5, {"type": "dm", "to": "P03", "message": "感情欠損DM"}, emotion=None),
        _entry("P01", 5, 6, {"type": "contract_propose", "terms": []}, emotion="喜"),
    ]
    (logs / "game01_P01_llm_calls.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in p01_entries), encoding="utf-8",
    )
    (logs / "game01_P02_llm_calls.jsonl").write_text("", encoding="utf-8")
    (logs / "game01_P03_llm_calls.jsonl").write_text("", encoding="utf-8")

    events = [
        {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P01"}},
        {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P02"}},
        {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P03"}},
        {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
         "data": {"action": "dm", "player_id": "P01", "turn": 1, "success": True}},
        {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
         "data": {"action": "broadcast", "player_id": "P01", "turn": 2, "success": True}},
        {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
         "data": {"action": "anonymous_broadcast", "player_id": "P01", "turn": 3, "success": True}},
        {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
         "data": {"action": "dm", "player_id": "P01", "turn": 5, "success": False,
                   "reason": "Target P03 unreachable"}},
    ]
    (trial / "game01_events.jsonl").write_text(
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
    )
    (trial / "game01_seat_map.json").write_text(
        '{"P01":"L1","P02":"L2","P03":"L3"}', encoding="utf-8",
    )
    return tmp_path


class TestEmotionBindingRoundDetail:
    """A. 取得・束縛: get_player_round_detail() の calls[].emotion"""

    def test_round_detail_calls_have_emotion_in_god(self, tmp_path):
        logs_dir = _make_emotion_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_emotion", "game01", "P01", 5, view="god")
        by_turn = {c["turn"]: c for c in detail["calls"]}
        assert by_turn[1]["emotion"] == "疑"
        assert by_turn[2]["emotion"] == "楽"
        assert by_turn[3]["emotion"] == "焦"
        assert by_turn[4]["emotion"] == "奸"  # 奸バグの回帰確認
        assert by_turn[6]["emotion"] == "喜"

    def test_round_detail_call_emotion_when_missing(self, tmp_path):
        """ログにemotion無し＆strategyにも無いcallは"平静"（別値を捏造しない）"""
        logs_dir = _make_emotion_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_emotion", "game01", "P01", 5, view="god")
        by_turn = {c["turn"]: c for c in detail["calls"]}
        assert by_turn[5]["emotion"] == "平静"

    def test_one_call_one_action_emotion_binding(self, tmp_path):
        """同一turnのcallとそのactionのemotionが一致すること（1:1束縛の固定）"""
        logs_dir = _make_emotion_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_emotion", "game01", "P01", 5, view="god")
        by_turn = {c["turn"]: c for c in detail["calls"]}
        assert by_turn[1]["action"]["type"] == "dm"
        assert by_turn[1]["emotion"] == "疑"
        assert by_turn[2]["action"]["type"] == "broadcast"
        assert by_turn[2]["emotion"] == "楽"


class TestEmotionBindingRoundMessages:
    def test_round_messages_carry_emotion(self, tmp_path):
        logs_dir = _make_emotion_trial(tmp_path)
        rs = get_round_states(logs_dir, "trial_emotion", "game01", view="god")
        msgs = rs["rounds"]["5"]["messages"]
        by_msg = {m.get("message"): m for m in msgs}
        assert by_msg["疑いのDM"]["emotion"] == "疑"
        assert by_msg["楽観ブロードキャスト"]["emotion"] == "楽"
        assert by_msg["焦りの匿名発言"]["emotion"] == "焦"


class TestEmotionVisibilityBoundary:
    """B. 秘匿性: 匿名発言・DMの秘匿境界を新たに破らないこと（最重要）"""

    def test_public_anonymous_broadcast_has_no_emotion(self, tmp_path):
        logs_dir = _make_emotion_trial(tmp_path)
        rs = get_round_states(logs_dir, "trial_emotion", "game01", view="public")
        msgs = rs["rounds"]["5"]["messages"]
        anon = next(m for m in msgs if m.get("type") == "anonymous_broadcast")
        assert "emotion" not in anon

    def test_god_anonymous_broadcast_has_emotion(self, tmp_path):
        logs_dir = _make_emotion_trial(tmp_path)
        rs = get_round_states(logs_dir, "trial_emotion", "game01", view="god")
        msgs = rs["rounds"]["5"]["messages"]
        anon = next(m for m in msgs if m.get("type") == "anonymous_broadcast")
        assert anon["emotion"] == "焦"

    def test_public_dm_has_no_emotion(self, tmp_path):
        logs_dir = _make_emotion_trial(tmp_path)
        rs = get_round_states(logs_dir, "trial_emotion", "game01", view="public")
        msgs = rs["rounds"]["5"]["messages"]
        dms = [m for m in msgs if m.get("type") == "dm"]
        assert dms, "public dm entries should still be present as opaque placeholders"
        for m in dms:
            assert "emotion" not in m

    def test_public_broadcast_has_emotion(self, tmp_path):
        logs_dir = _make_emotion_trial(tmp_path)
        rs = get_round_states(logs_dir, "trial_emotion", "game01", view="public")
        msgs = rs["rounds"]["5"]["messages"]
        bc = next(m for m in msgs if m.get("type") == "broadcast")
        assert bc["emotion"] == "楽"

    def test_public_call_emotion_suppressed_for_sensitive(self, tmp_path):
        logs_dir = _make_emotion_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_emotion", "game01", "P01", 5, view="public")
        by_turn = {c["turn"]: c for c in detail["calls"]}
        # dm / anonymous_broadcast / contract_propose は public では抑制
        assert by_turn[1]["emotion"] is None  # dm
        assert by_turn[3]["emotion"] is None  # anonymous_broadcast
        assert by_turn[6]["emotion"] is None  # contract_propose
        # broadcast / pass は public でも出る
        assert by_turn[2]["emotion"] == "楽"  # broadcast
        assert by_turn[4]["emotion"] == "奸"  # pass

    def test_public_view_no_new_keys_leak(self, tmp_path):
        """public投影のキー集合が期待集合と一致すること（broadcastのみemotionを持つ）"""
        logs_dir = _make_emotion_trial(tmp_path)
        rs = get_round_states(logs_dir, "trial_emotion", "game01", view="public")
        msgs = rs["rounds"]["5"]["messages"]
        for m in msgs:
            if m.get("type") == "broadcast":
                assert "emotion" in m
            elif m.get("type") == "anonymous_broadcast":
                assert "emotion" not in m
                assert "actual_sender" not in m
            elif m.get("type") == "dm":
                assert "emotion" not in m
                assert "message" not in m  # 本文自体が非公開のまま


class TestEmotionCompatibilityAndSafety:
    """C. 互換・堅牢性 / D. 値域"""

    def test_old_run_without_emotion_field_ok(self, tmp_path):
        """emotionキーの無い旧形式ログでも例外にならず"平静"に落ちること"""
        logs_dir = _make_emotion_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_emotion", "game01", "P01", 5, view="god")
        by_turn = {c["turn"]: c for c in detail["calls"]}
        assert by_turn[5]["emotion"] == "平静"

    def test_unknown_emotion_value_falls_back(self, tmp_path):
        """未知値は"平静"（強引な類義語マッピングをしない）"""
        trial = tmp_path / "trial_emotion_unknown"
        logs = trial / "llm_logs"
        logs.mkdir(parents=True)
        entries = [_entry("P01", 5, 1, {"type": "pass"}, emotion="憂")]
        (logs / "game01_P01_llm_calls.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8",
        )
        (trial / "game01_events.jsonl").write_text(
            json.dumps({"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P01"}}) + "\n",
            encoding="utf-8",
        )
        detail = get_player_round_detail(tmp_path, "trial_emotion_unknown", "game01", "P01", 5, view="god")
        assert detail["calls"][0]["emotion"] == "平静"

    def test_delivery_badge_coexists_with_emotion(self, tmp_path):
        """delivered/rejected/unverifiedがemotion追加後も従来どおり付くこと"""
        logs_dir = _make_emotion_trial(tmp_path)
        rs = get_round_states(logs_dir, "trial_emotion", "game01", view="god")
        msgs = rs["rounds"]["5"]["messages"]
        delivered_dm = next(m for m in msgs if m.get("message") == "疑いのDM")
        assert delivered_dm["delivery"] == "delivered"
        assert delivered_dm["emotion"] == "疑"

        rejected_dm = next(m for m in msgs if m.get("message") == "感情欠損DM")
        assert rejected_dm["delivery"] == "rejected"
        assert rejected_dm["reject_reason"] == "Target P03 unreachable"
        assert rejected_dm["emotion"] == "平静"

    def test_secret_event_player_scope_unchanged(self, tmp_path):
        """secret_events.round_totals（hidden件数・rejected件数）がemotion追加前と同値"""
        logs_dir = _make_emotion_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_emotion", "game01", "P02", 5, view="god")
        totals = detail["secret_events"]["round_totals"]
        # R5の秘匿DM総数は2件（turn1 P01→P02, turn5 P01→P03）。
        # P02視点で関与するのは turn1 のみ → hidden=1
        assert totals["messages"] == 2
        assert totals["messages_hidden"] == 1

    def test_emotion_value_is_always_from_enum(self, tmp_path):
        """出力されるemotionは必ずVALID_EMOTIONS ∪ {"平静"}の要素であること"""
        logs_dir = _make_emotion_trial(tmp_path)
        allowed = VALID_EMOTIONS | {"平静"}
        detail = get_player_round_detail(logs_dir, "trial_emotion", "game01", "P01", 5, view="god")
        for c in detail["calls"]:
            assert c["emotion"] in allowed
        rs = get_round_states(logs_dir, "trial_emotion", "game01", view="god")
        for m in rs["rounds"]["5"]["messages"]:
            if "emotion" in m:
                assert m["emotion"] in allowed
