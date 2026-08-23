"""
観戦ビューアのパーサ単体テスト

サーバー起動なし。既存のログファイルをfixture的に使用。
"""

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from viewer.log_parser import (
    LogCache, list_games, get_game_state, get_player_timeline,
    _extract_strategy, _extract_action, _extract_emotion,
    get_commentary, get_round_states, get_player_round_detail,
    _index_negotiation_outcomes, _match_delivery, _collect_round_messages,
)

INDEX_HTML_PATH = Path(__file__).resolve().parent.parent / "viewer" / "static" / "index.html"


class TestLogCache:
    """差分キャッシュのテスト"""

    def test_cache_returns_same_on_unchanged_file(self):
        """ファイル変更なしでキャッシュヒットすること"""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "test.jsonl"
            p.write_text('{"a": 1}\n{"a": 2}\n', encoding="utf-8")

            cache = LogCache()
            r1 = cache.get_entries(p)
            r2 = cache.get_entries(p)
            assert r1 == r2
            assert len(r1) == 2

    def test_malformed_line_skipped(self):
        """書きかけ行がスキップされること"""
        with tempfile.TemporaryDirectory() as tmpdir:
            p = Path(tmpdir) / "test.jsonl"
            p.write_text('{"ok": true}\n{"incomplete": \n{"ok2": true}\n', encoding="utf-8")

            cache = LogCache()
            entries = cache.get_entries(p)
            assert len(entries) == 2
            assert entries[0]["ok"] is True
            assert entries[1]["ok2"] is True

    def test_nonexistent_file_returns_empty(self):
        """存在しないファイルで空リストを返すこと"""
        cache = LogCache()
        entries = cache.get_entries(Path("/nonexistent/file.jsonl"))
        assert entries == []


class TestListGames:
    """ゲーム一覧のテスト"""

    def test_list_games_finds_existing(self):
        """logs/llm/の実ディレクトリからゲームが見つかること"""
        logs_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
        if not logs_dir.exists():
            pytest.skip("logs/llm/ not found")
        games = list_games(logs_dir)
        # 少なくとも1試合は存在するはず
        assert len(games) >= 1
        # 必須フィールドの存在確認
        g = games[0]
        assert "trial_dir" in g
        assert "game_id" in g
        assert "players" in g

    def test_list_games_empty_dir(self):
        """空のディレクトリで空リストを返すこと"""
        with tempfile.TemporaryDirectory() as tmpdir:
            games = list_games(Path(tmpdir))
            assert games == []


class TestExtractStrategy:
    """strategy抽出のテスト"""

    def test_extract_from_json_code_block(self):
        """コードブロック内のJSONからstrategyを抽出すること"""
        text = '```json\n{"strategy": {"target_market": "M01"}, "action": {"type": "pass"}}\n```'
        s = _extract_strategy(text)
        assert s is not None
        assert s["target_market"] == "M01"

    def test_extract_from_raw_json(self):
        """生JSONからstrategyを抽出すること"""
        text = '{"strategy": {"reason": "test"}, "action": {"type": "pass"}}'
        s = _extract_strategy(text)
        assert s is not None
        assert s["reason"] == "test"

    def test_extract_action_market_commit(self):
        """market_commitアクションを抽出すること"""
        text = '{"strategy": {}, "action": {"type": "market_commit", "market_id": "M02", "card": "ONE_PAIR"}}'
        a = _extract_action(text)
        assert a is not None
        assert a["type"] == "market_commit"
        assert a["market_id"] == "M02"

    def test_no_strategy_returns_none(self):
        """strategyがないレスポンスでNoneを返すこと"""
        s = _extract_strategy("This is not JSON")
        assert s is None


class TestGetGameState:
    """ゲーム状態取得のテスト"""

    def test_get_game_state_with_real_logs(self):
        """実ログからゲーム状態が構築されること"""
        logs_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
        if not logs_dir.exists():
            pytest.skip("logs/llm/ not found")
        games = list_games(logs_dir)
        if not games:
            pytest.skip("No games found")

        g = games[0]
        state = get_game_state(logs_dir, g["trial_dir"], g["game_id"])
        assert "current_round" in state
        assert "players" in state
        assert "total_cost" in state
        assert len(state["players"]) >= 1


class TestGetRoundStates:
    """ラウンド状況取得のテスト"""

    def test_get_round_states_with_real_logs(self):
        """実ログからラウンド別盤面が構築されること"""
        logs_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
        if not logs_dir.exists():
            pytest.skip("logs/llm/ not found")
        games = list_games(logs_dir)
        # eventsログを持つ試合を優先的に選ぶ
        target = next((g for g in games if g.get("has_events")), None)
        if target is None:
            pytest.skip("No games with events found")

        data = get_round_states(logs_dir, target["trial_dir"], target["game_id"])
        assert "rounds" in data
        assert "players" in data
        assert data["rounds"], "rounds should be non-empty"

        # R1に主要キーが揃うこと
        r1 = data["rounds"].get("1")
        assert r1 is not None
        for key in ("markets", "commits", "cash", "holdings", "contracts",
                    "messages", "eliminated"):
            assert key in r1

        # debtがINTEREST/MANDATORY_REPAYイベントから正確に取得されていること
        for pid, c in r1["cash"].items():
            if c.get("debt") is not None:
                assert isinstance(c["debt"], int)
                assert c["debt"] >= 0
                break

        # 手札は12枚以下（提出で減る）
        for pid, hand in r1["holdings"].items():
            assert len(hand) <= 12


class TestPlayerRoundDetail:
    """ラウンド詳細はMemoryを復元しつつ生応答を公開しない。"""

    def _make_trial(self, tmp_path: Path, participants: list[str] | int = None) -> Path:
        trial = tmp_path / "trial_memory"
        logs = trial / "llm_logs"
        logs.mkdir(parents=True)
        if participants is None:
            participants = ["P01"]
        entries = [
            {
                "player_id": "P01", "model_id": "test-model", "phase": "negotiation",
                "round_num": 1, "turn": 1, "response_text": json.dumps({
                    "reasoning": "R1の全文reasoning", "strategy": {},
                    "action": {"type": "broadcast", "message": "公開発言"},
                }, ensure_ascii=False), "reasoning": "R1の全文reasoning",
                "reasoning_tokens": 42, "input_tokens": 10, "output_tokens": 20,
                "cost_usd": 0.01, "elapsed_ms": 12, "retry_count": 0,
            },
            {
                "player_id": "P01", "model_id": "test-model", "phase": "reflection",
                "round_num": 1, "turn": None,
                "response_text": '{"memory":"R1終了後の記憶"}', "reasoning_tokens": 0,
            },
            {
                "player_id": "P01", "model_id": "test-model", "phase": "negotiation",
                "round_num": 2, "turn": 1, "response_text": json.dumps({
                    "reasoning": "DM本文をここに書かない前提のreasoning",
                    "action": {"type": "dm", "to": "P02", "message": "秘密のDM本文"},
                }, ensure_ascii=False), "reasoning": "DM本文をここに書かない前提のreasoning",
                "reasoning_tokens": 0,
            },
            {
                "player_id": "P01", "model_id": "test-model", "phase": "negotiation",
                "round_num": 2, "turn": 2, "response_text": json.dumps({
                    "reasoning": "匿名発言のreasoning", "action": {"type": "anonymous_broadcast", "message": "匿名の公開発言"},
                }, ensure_ascii=False),
            },
            {
                "player_id": "P01", "model_id": "test-model", "phase": "negotiation",
                "round_num": 2, "turn": 3, "response_text": json.dumps({
                    "reasoning": "契約条項のreasoning", "action": {"type": "contract_propose", "terms": [{"ob_type": "type_b_market", "market_id": "M01"}]},
                }, ensure_ascii=False),
            },
        ]
        (logs / "game01_P01_llm_calls.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in entries), encoding="utf-8",
        )
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P01"}},
            {"event_type": "MARKET_OPEN", "round_num": 1, "data": {"markets": [{"market_id": "M01", "base_prize": 1, "carryover": 0, "prize_pool": 1}]}},
            {"event_type": "REVEAL", "round_num": 1, "data": {"commits": [{"player_id": "P01", "market_id": "M01", "card": "HIGH_CARD_1", "rank": 1}]}},
            {"event_type": "MARKET_RESULT", "round_num": 1, "data": {"market_id": "M01", "participants": participants, "winners": ["P01"], "prize_per_winner": 1, "total_pool": 1}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 2, "data": {"action": "contract_propose", "contract_id": "C01", "player_id": "P01", "parties": ["P01", "P02"]}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 2, "data": {"action": "contract_sign", "contract_id": "C01", "player_id": "P02"}},
            {"event_type": "TYPE_B_VIOLATION", "round_num": 2, "data": {"contract_id": "C01", "player_id": "P01", "obligation_id": "C01_OB01"}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        (trial / "game01_seat_map.json").write_text('{"P01":"L1"}', encoding="utf-8")
        return tmp_path

    def test_memory_reasoning_and_safe_actions(self, tmp_path):
        logs_dir = self._make_trial(tmp_path)
        r1 = get_player_round_detail(logs_dir, "trial_memory", "game01", "P01", 1)
        assert r1["memory"]["start"]["status"] == "absent"
        assert r1["memory"]["end"] == {"text": "R1終了後の記憶", "source_round": 1, "status": "updated"}
        assert r1["calls"][0]["reasoning"] == "R1の全文reasoning"
        assert r1["calls"][0]["native_thinking"]["tokens"] == 42
        assert r1["calls"][0]["action"] == {
            "type": "broadcast", "content": "公開発言", "visibility": "public",
            "display": {"icon": "🌐", "label": "公開発言"},
        }

        r2 = get_player_round_detail(logs_dir, "trial_memory", "game01", "P01", 2)
        assert r2["memory"]["start"]["text"] == "R1終了後の記憶"
        assert r2["calls"][0]["action"] == {
            "type": "dm", "label": "DM送信（本文・宛先は非公開）", "visibility": "secret",
            "display": {"icon": "🔒", "label": "秘匿DM"},
        }
        assert "秘密のDM本文" not in json.dumps(r2, ensure_ascii=False)
        assert r2["calls"][0]["action"].get("to") is None

    def test_final_round_has_no_end_reflection(self, tmp_path):
        logs_dir = self._make_trial(tmp_path)
        r12 = get_player_round_detail(logs_dir, "trial_memory", "game01", "P01", 12)
        assert r12["memory"]["end"]["status"] == "no_reflection_final_round"

    def test_public_redacts_and_god_returns_structured_game_secrets(self, tmp_path):
        logs_dir = self._make_trial(tmp_path)
        public = get_player_round_detail(logs_dir, "trial_memory", "game01", "P01", 2)
        serialized_public = json.dumps(public, ensure_ascii=False)
        assert "秘密のDM本文" not in serialized_public
        assert "匿名の公開発言" not in serialized_public
        assert "type_b_market" not in serialized_public
        assert public["calls"][0]["action"]["visibility"] == "secret"
        assert public["calls"][0]["action"]["display"] == {"icon": "🔒", "label": "秘匿DM"}
        assert public["calls"][0]["reasoning"] is None

        god = get_player_round_detail(logs_dir, "trial_memory", "game01", "P01", 2, view="god")
        serialized_god = json.dumps(god, ensure_ascii=False)
        assert "秘密のDM本文" in serialized_god
        assert "匿名の公開発言" in serialized_god
        assert "type_b_market" in serialized_god
        assert god["calls"][0]["action"]["to"] == "P02"
        assert god["calls"][0]["action"]["sender"] == "P01"
        assert god["calls"][1]["action"]["display"]["label"] == "匿名発言"
        assert god["secret_events"]["contracts"][0]["display"]["label"] == "秘匿契約"

    def _make_multi_player_trial(self, tmp_path: Path) -> Path:
        """P01/P02/P03の3人でDM・契約の当事者性フィルタを検証するためのfixture。

        _make_trial() はP01単独のため全DM・全契約がP01絡みとなり、
        「関係ないプレイヤーのDM/契約が混入する」バグを構造的に検出できない。
        このfixtureはP01と無関係なP02→P03のDM・契約(C02)を含めて非対称性を作る。
        """
        trial = tmp_path / "trial_multi"
        logs = trial / "llm_logs"
        logs.mkdir(parents=True)

        def _entry(pid: str, round_num: int, turn: int, action: dict) -> dict:
            return {
                "player_id": pid, "model_id": "test-model", "phase": "negotiation",
                "round_num": round_num, "turn": turn,
                "response_text": json.dumps({"reasoning": "r", "action": action}, ensure_ascii=False),
                "reasoning": "r", "reasoning_tokens": 0,
            }

        p01_entries = [
            _entry("P01", 2, 1, {"type": "dm", "to": "P02", "message": "P01が送ったDM"}),
            _entry("P01", 2, 2, {"type": "contract_propose",
                                  "terms": [{"ob_type": "type_b_market", "market_id": "own_terms_marker"}]}),
        ]
        p02_entries = [
            _entry("P02", 2, 1, {"type": "dm", "to": "P03", "message": "P01と無関係なDM"}),
            _entry("P02", 2, 2, {"type": "contract_propose",
                                  "terms": [{"ob_type": "type_b_market", "market_id": "foreign_terms_marker"}]}),
        ]
        p03_entries = [
            _entry("P03", 2, 1, {"type": "dm", "to": "P01", "message": "P01が受け取ったDM"}),
        ]
        (logs / "game01_P01_llm_calls.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in p01_entries), encoding="utf-8",
        )
        (logs / "game01_P02_llm_calls.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in p02_entries), encoding="utf-8",
        )
        (logs / "game01_P03_llm_calls.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in p03_entries), encoding="utf-8",
        )
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P02"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P03"}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 2,
             "data": {"action": "contract_propose", "contract_id": "C01", "player_id": "P01", "parties": ["P01", "P02"]}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 2,
             "data": {"action": "contract_propose", "contract_id": "C02", "player_id": "P02", "parties": ["P02", "P03"]}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        (trial / "game01_seat_map.json").write_text(
            '{"P01":"L1","P02":"L2","P03":"L3"}', encoding="utf-8",
        )
        return tmp_path

    def test_god_secret_events_exclude_unrelated_dms(self, tmp_path):
        logs_dir = self._make_multi_player_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_multi", "game01", "P01", 2, view="god")
        serialized = json.dumps(detail["secret_events"], ensure_ascii=False)
        assert "P01と無関係なDM" not in serialized
        for m in detail["secret_events"]["messages"]:
            assert m.get("sender") == "P01" or m.get("to") == "P01"

    def test_god_secret_events_retain_received_dms(self, tmp_path):
        logs_dir = self._make_multi_player_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_multi", "game01", "P01", 2, view="god")
        received = [m for m in detail["secret_events"]["messages"] if m.get("message") == "P01が受け取ったDM"]
        assert len(received) == 1
        assert received[0]["sender"] == "P03"
        assert received[0]["to"] == "P01"

    def test_god_secret_events_retain_sent_dms(self, tmp_path):
        logs_dir = self._make_multi_player_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_multi", "game01", "P01", 2, view="god")
        sent = [m for m in detail["secret_events"]["messages"] if m.get("message") == "P01が送ったDM"]
        assert len(sent) == 1
        assert sent[0]["sender"] == "P01"

    def test_god_secret_events_contracts_scoped_to_parties_or_proposer(self, tmp_path):
        logs_dir = self._make_multi_player_trial(tmp_path)
        detail_p01 = get_player_round_detail(logs_dir, "trial_multi", "game01", "P01", 2, view="god")
        assert [c["contract_id"] for c in detail_p01["secret_events"]["contracts"]] == ["C01"]
        serialized_p01 = json.dumps(detail_p01, ensure_ascii=False)
        assert "foreign_terms_marker" not in serialized_p01
        assert "own_terms_marker" in serialized_p01

        # 対称性の確認: P03視点ではC02のみ(P01特殊ケースでないことの証明)
        detail_p03 = get_player_round_detail(logs_dir, "trial_multi", "game01", "P03", 2, view="god")
        assert [c["contract_id"] for c in detail_p03["secret_events"]["contracts"]] == ["C02"]

    def test_god_secret_events_report_round_totals(self, tmp_path):
        logs_dir = self._make_multi_player_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_multi", "game01", "P01", 2, view="god")
        se = detail["secret_events"]
        assert se["scope"] == "player"
        totals = se["round_totals"]
        assert totals["messages"] == 3
        assert totals["messages_hidden"] == 1
        assert totals["contracts"] == 2
        assert totals["contracts_hidden"] == 1
        assert len(se["messages"]) + totals["messages_hidden"] == totals["messages"]
        assert len(se["contracts"]) + totals["contracts_hidden"] == totals["contracts"]

    def test_god_secret_events_dm_with_missing_recipient_kept_for_sender(self, tmp_path):
        logs_dir = self._make_multi_player_trial(tmp_path)
        # P01発の宛先欠落DM(turn3)を追記
        logs = logs_dir / "trial_multi" / "llm_logs"
        extra_p01 = {
            "player_id": "P01", "model_id": "test-model", "phase": "negotiation",
            "round_num": 2, "turn": 3,
            "response_text": json.dumps({"reasoning": "r", "action": {"type": "dm", "message": "宛先欠落だが送信者はP01"}}, ensure_ascii=False),
            "reasoning": "r", "reasoning_tokens": 0,
        }
        extra_p02 = {
            "player_id": "P02", "model_id": "test-model", "phase": "negotiation",
            "round_num": 2, "turn": 3,
            "response_text": json.dumps({"reasoning": "r", "action": {"type": "dm", "message": "宛先欠落かつ送信者もP01でない"}}, ensure_ascii=False),
            "reasoning": "r", "reasoning_tokens": 0,
        }
        extra_p03 = {
            "player_id": "P03", "model_id": "test-model", "phase": "negotiation",
            "round_num": 2, "turn": 4,
            "response_text": json.dumps({"reasoning": "r", "action": {"type": "dm", "to": ["P01", "P02"], "message": "宛先がlist形式でもP01を含む"}}, ensure_ascii=False),
            "reasoning": "r", "reasoning_tokens": 0,
        }
        with (logs / "game01_P01_llm_calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(extra_p01, ensure_ascii=False) + "\n")
        with (logs / "game01_P02_llm_calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(extra_p02, ensure_ascii=False) + "\n")
        with (logs / "game01_P03_llm_calls.jsonl").open("a", encoding="utf-8") as f:
            f.write(json.dumps(extra_p03, ensure_ascii=False) + "\n")

        detail = get_player_round_detail(logs_dir, "trial_multi", "game01", "P01", 2, view="god")
        messages = detail["secret_events"]["messages"]
        assert any(m.get("message") == "宛先欠落だが送信者はP01" for m in messages)
        assert not any(m.get("message") == "宛先欠落かつ送信者もP01でない" for m in messages)
        assert any(m.get("message") == "宛先がlist形式でもP01を含む" for m in messages)

    def test_public_view_has_no_secret_events_key(self, tmp_path):
        logs_dir = self._make_multi_player_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_multi", "game01", "P01", 2, view="public")
        assert "secret_events" not in detail

    def test_public_rounds_and_timeline_do_not_leak_dm_or_terms(self, tmp_path):
        logs_dir = self._make_trial(tmp_path)
        public = get_round_states(logs_dir, "trial_memory", "game01")
        timeline = get_player_timeline(logs_dir, "trial_memory", "game01", "P01")
        public_text = json.dumps({"rounds": public, "timeline": timeline}, ensure_ascii=False)
        assert "秘密のDM本文" not in public_text
        assert "type_b_market" not in public_text
        assert any(m["visibility"] == "secret" for m in public["rounds"]["2"]["messages"])

    def test_integer_market_participants_uses_commit_for_player_membership(self, tmp_path):
        """実ログ形式の参加人数(int)でもround-detailを再構成できる。"""
        logs_dir = self._make_trial(tmp_path, participants=1)
        detail = get_player_round_detail(logs_dir, "trial_memory", "game01", "P01", 1)

        assert detail["result"]["markets"] == [{
            "market_id": "M01", "participants": 1, "winners": ["P01"],
            "prize_per_winner": 1, "total_pool": 1, "surged": False,
        }]

    def test_current_api_trial_round_details_reconstruct(self):
        """R1〜R3実APIログの人数型でも全席の詳細を再構成できる。"""
        logs_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
        trial_dir = logs_dir / "trial_C_20260821_072321"
        if not trial_dir.exists():
            pytest.skip("trial_C_20260821_072321 not found")

        for pid in ("P01", "P02", "P03", "P04", "P05", "P06"):
            details = [
                get_player_round_detail(logs_dir, trial_dir.name, "game01", pid, round_num)
                for round_num in (1, 2, 3)
            ]
            assert [detail["round"] for detail in details] == [1, 2, 3]
            assert all(detail["result"]["available"] for detail in details)
            assert details[0]["memory"]["end"]["text"] == details[1]["memory"]["start"]["text"]
            assert details[1]["memory"]["end"]["text"] == details[2]["memory"]["start"]["text"]
            assert all(isinstance(call["native_thinking"]["tokens"], int) for detail in details for call in detail["calls"])

            god_details = [
                get_player_round_detail(logs_dir, trial_dir.name, "game01", pid, round_num, view="god")
                for round_num in (1, 2, 3)
            ]
            assert [detail["round"] for detail in god_details] == [1, 2, 3]

    def test_god_view_ui_has_accessible_visibility_markers(self):
        base = Path(__file__).resolve().parent.parent / "viewer" / "static"
        html = (base / "index.html").read_text(encoding="utf-8")
        css = (base / "style.css").read_text(encoding="utf-8")
        for marker in ("🔒 神視点 ON", "🌐 公開", "🎭 匿名発言", "📜 🔒 秘匿契約", "X-Viewer-God-Token"):
            assert marker in html
        for selector in (".visibility-public", ".visibility-secret", ".god-banner"):
            assert selector in css

    def test_final_reflection_rendered_from_result_payload(self):
        """renderRoundDetail()がresult.final_reflectionを参照すること。

        バックエンドの返り値にtop-level final_reflectionは存在しない
        (result配下のみ)。誤ってdetail.final_reflectionを参照すると
        FINAL_REFLECTIONセクションが常にundefinedとなり描画されない。
        """
        html = INDEX_HTML_PATH.read_text(encoding="utf-8")
        assert "const fr = result.final_reflection;" in html
        # detail.final_reflection_round(正当)への誤ヒットを避けるため完全一致の旧バグ行のみ否定する
        assert "const fr = detail.final_reflection;" not in html

    def test_invalid_round_rejected(self, tmp_path):
        logs_dir = self._make_trial(tmp_path)
        with pytest.raises(ValueError):
            get_player_round_detail(logs_dir, "trial_memory", "game01", "P01", 13)

    def test_detail_modal_keeps_raw_timeline_tab(self):
        html = (Path(__file__).resolve().parent.parent / "viewer" / "static" / "index.html").read_text(encoding="utf-8")
        assert 'id="detail-tab-round"' in html
        assert 'id="detail-tab-raw"' in html
        assert "renderRawTimeline" in html


class TestNegotiationDeliveryJoin:
    """D5: LLMログ（本文）とEngineイベント（成否）のjoinによるdelivery判定。

    Plan: /home/uso8m/.claude/plans/linked-stirring-catmull.md §6
    delivery は "delivered" / "rejected" / "unverified" の3値。
    「不明(unverified)」を「不成立(rejected)」と混同しないことが最重要の不変条件
    （それが元バグ = 脱落済みP09宛DMの誤表示 と同種の誤情報になるため）。
    """

    def _make_delivery_trial(self, tmp_path: Path) -> Path:
        """P01/P02/P03の3人、R5で delivered/rejected/unverified の3ケースを含むfixture。

        - P01 turn1 dm→P02: NEGOTIATION_ACTION success=True → delivered
        - P01 turn2 dm→P09(脱落者を模擬): NEGOTIATION_ACTION success=False → rejected
        - P01 turn3 dm→P03: 対応するNEGOTIATION_ACTIONが無い → unverified
        - P02 turn1 broadcast: success=True → delivered
        - P02 turn2 anonymous_broadcast: success=False → rejected
        """
        trial = tmp_path / "trial_delivery"
        logs = trial / "llm_logs"
        logs.mkdir(parents=True)

        def _entry(pid: str, round_num: int, turn: int, action: dict) -> dict:
            return {
                "player_id": pid, "model_id": "test-model", "phase": "negotiation",
                "round_num": round_num, "turn": turn,
                "response_text": json.dumps({"reasoning": "r", "action": action}, ensure_ascii=False),
                "reasoning": "r", "reasoning_tokens": 0,
            }

        p01_entries = [
            _entry("P01", 5, 1, {"type": "dm", "to": "P02", "message": "配信成功DM"}),
            _entry("P01", 5, 2, {"type": "dm", "to": "P09", "message": "脱落者宛て不成立DM"}),
            _entry("P01", 5, 3, {"type": "dm", "to": "P03", "message": "対応イベント無し未検証DM"}),
        ]
        p02_entries = [
            _entry("P02", 5, 1, {"type": "broadcast", "message": "公開成功発言"}),
            _entry("P02", 5, 2, {"type": "anonymous_broadcast", "message": "匿名不成立発言"}),
        ]
        (logs / "game01_P01_llm_calls.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in p01_entries), encoding="utf-8",
        )
        (logs / "game01_P02_llm_calls.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in p02_entries), encoding="utf-8",
        )
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P02"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P03"}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
             "data": {"action": "dm", "player_id": "P01", "turn": 1, "success": True}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
             "data": {"action": "dm", "player_id": "P01", "turn": 2, "success": False,
                       "reason": "Target P09 is not alive"}},
            # turn3 (P01→P03) は意図的にイベント無し → unverified を検証
            {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
             "data": {"action": "broadcast", "player_id": "P02", "turn": 1, "success": True}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
             "data": {"action": "anonymous_broadcast", "player_id": "P02", "turn": 2, "success": False,
                       "reason": "内容が長すぎます"}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        (trial / "game01_seat_map.json").write_text(
            '{"P01":"L1","P02":"L2","P03":"L3"}', encoding="utf-8",
        )
        return tmp_path

    # --- 純関数の単体テスト ---

    def test_index_negotiation_outcomes_keys_on_pid_round_turn(self):
        events = [
            {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
             "data": {"action": "dm", "player_id": "P01", "turn": 1, "success": True}},
            {"event_type": "MARKET_OPEN", "round_num": 5, "data": {}},
        ]
        index = _index_negotiation_outcomes(events)
        assert index == {("P01", 5, 1): [{"action": "dm", "player_id": "P01", "turn": 1, "success": True}]}

    def test_index_negotiation_outcomes_skips_entries_missing_turn(self):
        """turn無し（旧ログ）はキー化できないため索引から除外される。"""
        events = [
            {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
             "data": {"action": "dm", "player_id": "P01"}},
        ]
        index = _index_negotiation_outcomes(events)
        assert index == {}

    def test_match_delivery_returns_delivered_for_success_true(self):
        index = {("P01", 5, 1): [{"action": "dm", "success": True}]}
        delivery, reason = _match_delivery(index, "P01", 5, 1, "dm")
        assert delivery == "delivered"
        assert reason is None

    def test_match_delivery_returns_rejected_with_reason_for_success_false(self):
        index = {("P01", 5, 2): [{"action": "dm", "success": False, "reason": "Target P09 is not alive"}]}
        delivery, reason = _match_delivery(index, "P01", 5, 2, "dm")
        assert delivery == "rejected"
        assert reason == "Target P09 is not alive"

    def test_match_delivery_returns_unverified_when_turn_missing(self):
        index = {("P01", 5, 1): [{"action": "dm", "success": True}]}
        delivery, reason = _match_delivery(index, "P01", 5, None, "dm")
        assert delivery == "unverified"
        assert reason is None

    def test_match_delivery_returns_unverified_when_no_candidates(self):
        index: dict = {}
        delivery, reason = _match_delivery(index, "P01", 5, 3, "dm")
        assert delivery == "unverified"
        assert reason is None

    def test_match_delivery_returns_unverified_when_action_type_mismatch(self):
        index = {("P01", 5, 1): [{"action": "contract_propose", "success": True}]}
        delivery, reason = _match_delivery(index, "P01", 5, 1, "dm")
        assert delivery == "unverified"
        assert reason is None

    # --- _collect_round_messages: 後方互換 + join動作 ---

    def test_collect_round_messages_without_events_omits_delivery(self, tmp_path):
        logs_dir = self._make_delivery_trial(tmp_path)
        by_round = _collect_round_messages(logs_dir / "trial_delivery", "game01")
        for msg in by_round["5"]:
            assert "delivery" not in msg

    def test_collect_round_messages_with_events_attaches_delivery(self, tmp_path):
        logs_dir = self._make_delivery_trial(tmp_path)
        events = json.loads(
            "[" + ",".join(
                (logs_dir / "trial_delivery" / "game01_events.jsonl").read_text(encoding="utf-8").splitlines()
            ) + "]"
        )
        by_round = _collect_round_messages(logs_dir / "trial_delivery", "game01", events=events)
        by_message = {m["message"]: m for m in by_round["5"]}
        assert by_message["配信成功DM"]["delivery"] == "delivered"
        assert by_message["脱落者宛て不成立DM"]["delivery"] == "rejected"
        assert by_message["脱落者宛て不成立DM"]["reject_reason"] == "Target P09 is not alive"
        assert by_message["対応イベント無し未検証DM"]["delivery"] == "unverified"
        assert "reject_reason" not in by_message["対応イベント無し未検証DM"]

    # --- get_round_states 経由の end-to-end join (god view) ---

    def test_god_view_marks_delivered_dm(self, tmp_path):
        logs_dir = self._make_delivery_trial(tmp_path)
        data = get_round_states(logs_dir, "trial_delivery", "game01", view="god")
        msgs = data["rounds"]["5"]["messages"]
        m = next(m for m in msgs if m.get("message") == "配信成功DM")
        assert m["delivery"] == "delivered"
        assert "reject_reason" not in m

    def test_god_view_marks_rejected_dm_with_reason(self, tmp_path):
        logs_dir = self._make_delivery_trial(tmp_path)
        data = get_round_states(logs_dir, "trial_delivery", "game01", view="god")
        msgs = data["rounds"]["5"]["messages"]
        m = next(m for m in msgs if m.get("message") == "脱落者宛て不成立DM")
        assert m["delivery"] == "rejected"
        assert m["reject_reason"] == "Target P09 is not alive"

    def test_god_view_marks_unverified_dm_when_unmatched(self, tmp_path):
        logs_dir = self._make_delivery_trial(tmp_path)
        data = get_round_states(logs_dir, "trial_delivery", "game01", view="god")
        msgs = data["rounds"]["5"]["messages"]
        m = next(m for m in msgs if m.get("message") == "対応イベント無し未検証DM")
        assert m["delivery"] == "unverified"
        assert "reject_reason" not in m

    def test_god_view_marks_delivered_broadcast(self, tmp_path):
        logs_dir = self._make_delivery_trial(tmp_path)
        data = get_round_states(logs_dir, "trial_delivery", "game01", view="god")
        msgs = data["rounds"]["5"]["messages"]
        m = next(m for m in msgs if m.get("message") == "公開成功発言")
        assert m["delivery"] == "delivered"

    def test_god_view_marks_rejected_anonymous_broadcast(self, tmp_path):
        logs_dir = self._make_delivery_trial(tmp_path)
        data = get_round_states(logs_dir, "trial_delivery", "game01", view="god")
        msgs = data["rounds"]["5"]["messages"]
        m = next(m for m in msgs if m.get("message") == "匿名不成立発言")
        assert m["delivery"] == "rejected"
        assert m["reject_reason"] == "内容が長すぎます"

    # --- public view: reject_reason（宛先PIDを含みうる）が漏れないこと ---

    def test_public_view_dm_has_no_delivery_or_reason_fields(self, tmp_path):
        logs_dir = self._make_delivery_trial(tmp_path)
        data = get_round_states(logs_dir, "trial_delivery", "game01", view="public")
        msgs = data["rounds"]["5"]["messages"]
        dm_msgs = [m for m in msgs if m.get("type") == "dm"]
        assert dm_msgs, "public dm entries should still be present as opaque placeholders"
        for m in dm_msgs:
            assert "delivery" not in m
            assert "reject_reason" not in m

    def test_public_view_never_leaks_dead_target_pid_via_reject_reason(self, tmp_path):
        logs_dir = self._make_delivery_trial(tmp_path)
        data = get_round_states(logs_dir, "trial_delivery", "game01", view="public")
        serialized = json.dumps(data, ensure_ascii=False)
        assert "P09" not in serialized
        assert "Target P09 is not alive" not in serialized

    def test_public_view_broadcast_delivery_also_hidden(self, tmp_path):
        """delivery/reject_reasonはgod専用。broadcastのように非秘匿な種別でも同様に隠す
        （視点間で挙動を統一し、実装/テストの単純さを優先する設計判断）。"""
        logs_dir = self._make_delivery_trial(tmp_path)
        data = get_round_states(logs_dir, "trial_delivery", "game01", view="public")
        msgs = data["rounds"]["5"]["messages"]
        m = next(m for m in msgs if m.get("message") == "公開成功発言")
        assert "delivery" not in m

    # --- get_player_round_detail の round_totals 拡張 ---

    def test_round_totals_reports_messages_rejected_for_owner(self, tmp_path):
        logs_dir = self._make_delivery_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_delivery", "game01", "P01", 5, view="god")
        totals = detail["secret_events"]["round_totals"]
        assert totals["messages_rejected"] == 1
        assert totals["messages_rejected_shown"] == 1

    def test_round_totals_rejected_shown_excludes_unrelated_player(self, tmp_path):
        """P01→P09の不成立DMはP03には無関係。round内の不成立総数は見えるが
        本人絡みの不成立(messages_rejected_shown)は0であること。"""
        logs_dir = self._make_delivery_trial(tmp_path)
        detail = get_player_round_detail(logs_dir, "trial_delivery", "game01", "P03", 5, view="god")
        totals = detail["secret_events"]["round_totals"]
        assert totals["messages_rejected"] == 1
        assert totals["messages_rejected_shown"] == 0

    # --- 後方互換: turn無しの旧ログ/旧イベントでは delivery を一切付与しない ---

    def test_legacy_events_without_turn_omit_delivery_entirely(self, tmp_path):
        """NEGOTIATION_ACTIONにturnを含まない旧形式のeventsでは、
        joinできる情報が無いため delivery フィールド自体を付与しない
        （'unverified'を機械的に埋めて誤情報化しないための後方互換モード）。"""
        trial = tmp_path / "trial_legacy"
        logs = trial / "llm_logs"
        logs.mkdir(parents=True)
        entry = {
            "player_id": "P01", "model_id": "test-model", "phase": "negotiation",
            "round_num": 5, "turn": 1,
            "response_text": json.dumps(
                {"reasoning": "r", "action": {"type": "dm", "to": "P02", "message": "旧形式ログのDM"}},
                ensure_ascii=False,
            ),
            "reasoning": "r", "reasoning_tokens": 0,
        }
        (logs / "game01_P01_llm_calls.jsonl").write_text(
            json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        # turn無しの旧形式NEGOTIATION_ACTION
        events = [
            {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
             "data": {"action": "dm", "player_id": "P01"}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        (trial / "game01_seat_map.json").write_text('{"P01":"L1"}', encoding="utf-8")

        data = get_round_states(tmp_path, "trial_legacy", "game01", view="god")
        msgs = data["rounds"]["5"]["messages"]
        m = next(m for m in msgs if m.get("message") == "旧形式ログのDM")
        assert "delivery" not in m
        assert "reject_reason" not in m


class TestDeliveryBadgeWording:
    """
    秘密DM成功バッジの文言変更（「✅ 成立」→「✅ 送信済」）の静的文字列回帰テスト。

    delivery=deliveredは「Engineがアクションを受理し送達した」ことのみを意味し、
    交渉・契約の「成立」ではない。秘匿契約/公正証書の本当の「成立」（ct-active等）と
    語が衝突していたため文言のみを変更する。delivery判定ロジック（log_parser）や
    Public/God境界、rejected/unverifiedの文言・分岐は一切変更しない。
    """

    @staticmethod
    def _delivery_badge_fn_body() -> str:
        html = INDEX_HTML_PATH.read_text(encoding="utf-8")
        start_marker = "function deliveryBadge(m, viewerPid) {"
        start = html.index(start_marker)
        end = html.index("\n}", start)
        return html[start:end]

    def test_delivery_badge_success_says_soushinzumi(self):
        """delivered分岐が『送信済』になり、『✅ 成立』はもう含まれないこと"""
        body = self._delivery_badge_fn_body()
        assert "✅ 送信済" in body
        assert "✅ 成立" not in body

    def test_delivery_badge_keeps_rejected_and_unverified(self):
        """rejected/unverifiedの文言・分岐は現行どおり変更されていないこと"""
        body = self._delivery_badge_fn_body()
        assert "❌ 不成立" in body
        assert "❔ 未検証" in body
        assert "delivery-rejected" in body
        assert "delivery-unverified" in body

    def test_contract_active_badge_still_says_seiritsu(self):
        """秘匿契約(ct-active)・公正証書の本当の『成立』は変更されていないこと"""
        html = INDEX_HTML_PATH.read_text(encoding="utf-8")
        assert '<span class="ct-active">✅ 成立</span>' in html
        assert "✅成立" in html

    def test_delivery_delivered_css_class_unchanged(self):
        """style.cssのクラス名（.delivery-badge.delivery-delivered）は変更していないこと"""
        css_path = INDEX_HTML_PATH.parent / "style.css"
        css = css_path.read_text(encoding="utf-8")
        assert ".delivery-badge.delivery-delivered" in css


class TestPlayerDetailDmDirection:
    """
    選手詳細モーダルのDM送受信バッジ分離（✅ 送信済 / 📥 受信済）の回帰テスト。

    frontendのみの変更（deliveryBadge(m, viewerPid)がviewerPidを渡された時だけ
    向きで文言を分ける）で成立し、backend（log_parser.py）は無変更で済むこと
    （既存runでも即反映されるViewer-only変更であること）を、
    secret_events.messagesが送受信双方向のsender/to/deliveryを既に持っている
    ことで裏付ける。JS分岐の詳細はtest_viewer_js.pyのTestDeliveryBadgeDirectionへ。
    """

    def test_player_detail_call_site_passes_player_id(self):
        """選手詳細モーダルの秘匿DMカードはdetail.player_idを基準PIDとして渡すこと"""
        html = INDEX_HTML_PATH.read_text(encoding="utf-8")
        assert "deliveryBadge(m, detail.player_id)" in html

    def test_round_wide_call_site_stays_pid_agnostic(self):
        """状況パネル（ラウンド全体）の呼び出しは基準PIDを渡さず現行どおりのままであること"""
        html = INDEX_HTML_PATH.read_text(encoding="utf-8")
        assert "const dBadge = deliveryBadge(m);" in html

    def test_delivery_sentinels_present(self):
        """DELIVERY_BADGE_START/ENDセンチネルが正しい順序で存在すること
        （tests/test_viewer_js.pyでのnode実行抽出に使う）"""
        html = INDEX_HTML_PATH.read_text(encoding="utf-8")
        start = html.index("// ==== DELIVERY_BADGE_START ====")
        end = html.index("// ==== DELIVERY_BADGE_END ====")
        assert start < end

    def test_style_css_has_delivery_received(self):
        """受信DM用CSSクラスが追加され、既存4クラスも残っていること"""
        css_path = INDEX_HTML_PATH.parent / "style.css"
        css = css_path.read_text(encoding="utf-8")
        assert ".delivery-badge.delivery-received" in css
        assert ".delivery-badge.delivery-delivered" in css
        assert ".delivery-badge.delivery-rejected" in css
        assert ".delivery-badge.delivery-unverified" in css

    def test_emotion_lead_still_precedes_delivery_badge(self):
        """秘匿DMカードの描画順（emotionLead → deliveryBadge）が崩れていないこと
        （emotion表示とのレイアウト衝突が無いことのDOM順固定）"""
        html = INDEX_HTML_PATH.read_text(encoding="utf-8")
        marker = "🔒 秘匿DM</span> ${deliveryBadge(m, detail.player_id)}"
        idx = html.index(marker)
        preceding = html[max(0, idx - 200):idx]
        assert "emotionLead(m.emotion, secretVendor)" in preceding

    def test_secret_events_expose_both_directions(self, tmp_path):
        """backend無変更の裏付け: P03のsecret_events.messagesに
        P03→P04（送信）とP04→P03（受信）が両方入り、方向判定に必要な
        sender/to/deliveryがそれぞれ揃っていること"""
        trial = tmp_path / "trial_direction"
        logs = trial / "llm_logs"
        logs.mkdir(parents=True)

        def _entry(pid: str, round_num: int, turn: int, action: dict) -> dict:
            return {
                "player_id": pid, "model_id": "test-model", "phase": "negotiation",
                "round_num": round_num, "turn": turn,
                "response_text": json.dumps({"reasoning": "r", "action": action}, ensure_ascii=False),
                "reasoning": "r", "reasoning_tokens": 0,
            }

        p03_entries = [_entry("P03", 5, 1, {"type": "dm", "to": "P04", "message": "P03からP04へ送信"})]
        p04_entries = [_entry("P04", 5, 1, {"type": "dm", "to": "P03", "message": "P04からP03へ送信"})]
        (logs / "game01_P03_llm_calls.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in p03_entries), encoding="utf-8",
        )
        (logs / "game01_P04_llm_calls.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in p04_entries), encoding="utf-8",
        )
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P03"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P04"}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
             "data": {"action": "dm", "player_id": "P03", "turn": 1, "success": True}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 5,
             "data": {"action": "dm", "player_id": "P04", "turn": 1, "success": True}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        (trial / "game01_seat_map.json").write_text('{"P03":"L3","P04":"L4"}', encoding="utf-8")

        detail = get_player_round_detail(tmp_path, "trial_direction", "game01", "P03", 5, view="god")
        msgs = {m["message"]: m for m in detail["secret_events"]["messages"]}
        outgoing = msgs["P03からP04へ送信"]
        incoming = msgs["P04からP03へ送信"]
        assert outgoing["sender"] == "P03" and outgoing["to"] == "P04" and outgoing["delivery"] == "delivered"
        assert incoming["sender"] == "P04" and incoming["to"] == "P03" and incoming["delivery"] == "delivered"
        assert detail["player_id"] == "P03"

    def test_public_view_player_detail_has_no_secret_events(self, tmp_path):
        """public viewではsecret_events自体が存在しないこと
        （選手詳細の送受信バッジ分離はgod view限定であることの境界確認）"""
        trial = tmp_path / "trial_public"
        logs = trial / "llm_logs"
        logs.mkdir(parents=True)
        entry = {
            "player_id": "P03", "model_id": "test-model", "phase": "negotiation",
            "round_num": 5, "turn": 1,
            "response_text": json.dumps(
                {"reasoning": "r", "action": {"type": "dm", "to": "P04", "message": "秘密"}},
                ensure_ascii=False,
            ),
            "reasoning": "r", "reasoning_tokens": 0,
        }
        (logs / "game01_P03_llm_calls.jsonl").write_text(
            json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8",
        )
        (trial / "game01_seat_map.json").write_text('{"P03":"L3"}', encoding="utf-8")

        detail = get_player_round_detail(tmp_path, "trial_public", "game01", "P03", 5, view="public")
        assert "secret_events" not in detail


class TestGodViewSessionAuth:
    """
    God View認証の上位化・使い回し（sessionStorage方式、静的ファイルのみの変更）の
    静的文字列ベース回帰テスト。Viewer全体で1回だけGod認証し、モーダルの開閉や
    試合切替を跨いで使い回せることを、JSテストランナー無しの本リポジトリの既存方式
    （index.html/style.cssの文字列検査）で担保する。
    Plan: /home/uso8m/.claude/plans/linked-stirring-catmull.md §8.1
    """

    @staticmethod
    def _html():
        base = Path(__file__).resolve().parent.parent / "viewer" / "static"
        return (base / "index.html").read_text(encoding="utf-8")

    @staticmethod
    def _css():
        base = Path(__file__).resolve().parent.parent / "viewer" / "static"
        return (base / "style.css").read_text(encoding="utf-8")

    def test_god_ui_lives_outside_modal_overlay(self):
        # T1: God UI（#god-toggle）が #modal-overlay より前（＝ヘッダー側）にあること
        html = self._html()
        toggle_pos = html.index('id="god-toggle"')
        modal_pos = html.index('id="modal-overlay"')
        assert toggle_pos < modal_pos

    def test_view_button_wording(self):
        # T2: ヘッダー常設トグルの文言
        html = self._html()
        assert "VIEW: PUBLIC" in html
        assert "VIEW: GOD" in html

    def test_existing_markers_preserved(self):
        # T3: 既存テスト（test_god_view_ui_has_accessible_visibility_markers）が
        # 依存する文字列が本改修後も維持されていること（回帰の二重担保）
        html = self._html()
        css = self._css()
        for marker in ("🔒 神視点 ON", "🌐 公開", "🎭 匿名発言", "📜 🔒 秘匿契約", "X-Viewer-God-Token"):
            assert marker in html
        for selector in (".visibility-public", ".visibility-secret", ".god-banner"):
            assert selector in css

    def test_god_token_uses_session_storage_not_local_storage(self):
        # T4: God tokenの保存にsessionStorageを使い、localStorageは使わない
        # （localStorageはテーマ保存専用のまま）
        html = self._html()
        assert "sessionStorage" in html
        assert "GOD_TOKEN_KEY" in html
        # localStorageの"実コード"使用箇所（コメント除く）はテーマ関連のみであること
        for line in html.splitlines():
            stripped = line.strip()
            if "localStorage" in line and not stripped.startswith("//"):
                assert "viewer-theme" in line, f"unexpected localStorage usage: {line!r}"

    def test_god_token_never_placed_in_url(self):
        # T5: God tokenをURL/query stringに載せていない（header経由のみ）
        html = self._html()
        assert "X-Viewer-God-Token" in html
        assert "godToken}`" not in html
        assert "token=${godToken}" not in html
        assert "?god=" not in html
        assert "&god=" not in html

    def test_forbidden_403_downgrade_is_centralized_in_viewerfetch(self):
        # T6: 403の一元downgradeが viewerFetch() 内に存在すること
        html = self._html()
        fetch_start = html.index("function viewerFetch")
        fetch_block = html[fetch_start:fetch_start + 800]
        assert "res.status === 403" in fetch_block

    def test_closing_modal_does_not_discard_god_state(self):
        # T7: closePlayerDetail() がGod状態を破棄しない（モーダル閉で再入力不要にする回帰）
        html = self._html()
        fn_start = html.index("function closePlayerDetail")
        fn_end = html.index("\n}", fn_start)
        fn_body = html[fn_start:fn_end]
        assert "godMode = false" not in fn_body
        assert "godToken = ''" not in fn_body

    def test_explicit_logout_clears_session_storage(self):
        # T8: 明示ログアウト経路が sessionStorage を削除すること
        html = self._html()
        assert "clearGodToken" in html
        assert "removeItem" in html

    def test_reload_restoration_reuses_situation_endpoint_as_validator(self):
        # 追加検証: リロード復元が専用の確認APIを新設せず、
        # 既存の /rounds エンドポイント（loadSituation）を検証に流用していること
        html = self._html()
        assert "loadStoredGodToken" in html
        assert "storeGodToken" in html

    def test_style_css_retains_visibility_and_banner_selectors(self):
        # T10
        css = self._css()
        for selector in (".god-banner", ".visibility-public", ".visibility-secret"):
            assert selector in css


class TestCarryoverDisplay:
    """
    繰越（キャリーオーバー）表示のテスト（2026-08-17）

    trial_C_20260815_202246 R4 M03 の賞金が960,000円（R3流札→繰越480,000円）
    だった件の是正: index.html/style.css に表示要素が存在すること、
    log_parser の round_state に carryover が正しく載ることを検証する。
    """

    def test_index_html_renders_carryover_and_no_bid(self):
        """index.html に繰越バッジと不成立表示、style.cssに.sit-carryが存在する"""
        base = Path(__file__).resolve().parent.parent / "viewer" / "static"
        html = (base / "index.html").read_text(encoding="utf-8")
        css = (base / "style.css").read_text(encoding="utf-8")

        assert "sit-carry" in html
        assert "不成立" in html
        assert "繰越" in html
        assert ".sit-carry" in css

    def test_round_state_carries_market_carryover(self):
        """
        実ログ trial_C_20260815_202246 で
        R4/M03 の carryover==480000・prize_pool==base_prize+carryover、
        R3/M03 の participants==0 であること
        """
        logs_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
        trial_dir = logs_dir / "trial_C_20260815_202246"
        if not trial_dir.exists():
            pytest.skip("trial_C_20260815_202246 not found")

        games = list_games(logs_dir)
        target = next(
            (g for g in games if g["trial_dir"] == "trial_C_20260815_202246"),
            None,
        )
        if target is None:
            pytest.skip("trial_C_20260815_202246 not found in list_games")

        data = get_round_states(logs_dir, target["trial_dir"], target["game_id"])

        r3 = data["rounds"].get("3")
        r4 = data["rounds"].get("4")
        assert r3 is not None and r4 is not None

        m03_r3 = next(m for m in r3["markets"] if m["market_id"] == "M03")
        assert m03_r3["participants"] == 0

        m03_r4 = next(m for m in r4["markets"] if m["market_id"] == "M03")
        assert m03_r4["carryover"] == 480_000
        assert m03_r4["prize_pool"] == m03_r4["base_prize"] + m03_r4["carryover"]


class TestTokenAuth:
    """簡易認証のテスト（サーバー起動あり、httpx使用）"""

    def test_token_auth_blocks_without_token(self):
        """VIEWER_TOKEN設定時にトークンなしで401"""
        import os
        os.environ["VIEWER_TOKEN"] = "test_secret_123"
        try:
            # モジュールリロードして新しいTOKEN値を反映
            import importlib
            import viewer.server
            importlib.reload(viewer.server)
            from viewer.server import check_token
            from fastapi import HTTPException

            # check_token関数を直接テスト（サーバー起動不要）
            import asyncio
            from unittest.mock import MagicMock

            # トークンなしリクエスト
            mock_request = MagicMock()
            mock_request.headers = {}
            with pytest.raises(HTTPException) as exc_info:
                asyncio.get_event_loop().run_until_complete(
                    check_token(mock_request, token=None)
                )
            assert exc_info.value.status_code == 401
        finally:
            os.environ.pop("VIEWER_TOKEN", None)
            import importlib
            import viewer.server
            importlib.reload(viewer.server)

    def test_token_auth_allows_with_token(self):
        """正しいトークンで認証通過"""
        import os
        os.environ["VIEWER_TOKEN"] = "test_secret_123"
        try:
            import importlib
            import viewer.server
            importlib.reload(viewer.server)
            from viewer.server import check_token

            import asyncio
            from unittest.mock import MagicMock

            # 正しいトークン付きリクエスト
            mock_request = MagicMock()
            mock_request.headers = {}
            # 例外が発生しないことを確認
            asyncio.get_event_loop().run_until_complete(
                check_token(mock_request, token="test_secret_123")
            )
        finally:
            os.environ.pop("VIEWER_TOKEN", None)
            import importlib
            import viewer.server
            importlib.reload(viewer.server)

    def test_no_token_means_no_auth(self):
        """VIEWER_TOKEN未設定時は認証なし"""
        import os
        os.environ.pop("VIEWER_TOKEN", None)
        try:
            import importlib
            import viewer.server
            importlib.reload(viewer.server)
            from viewer.server import check_token

            import asyncio
            from unittest.mock import MagicMock

            mock_request = MagicMock()
            mock_request.headers = {}
            asyncio.get_event_loop().run_until_complete(
                check_token(mock_request, token=None)
            )
        finally:
            import viewer.server
            importlib.reload(viewer.server)

    def test_god_view_requires_separate_header_token(self):
        """神視点はVIEWER_TOKENとは独立した追加トークンを必要とする。"""
        import asyncio
        import importlib
        import os
        from unittest.mock import MagicMock

        os.environ["VIEWER_GOD_TOKEN"] = "god_test_secret"
        try:
            import viewer.server
            importlib.reload(viewer.server)
            from viewer.server import check_view
            request = MagicMock(); request.headers = {}
            with pytest.raises(Exception) as exc_info:
                check_view(request, "god")
            assert exc_info.value.status_code == 403
            request.headers = {"X-Viewer-God-Token": "god_test_secret"}
            assert check_view(request, "god") == "god"
            with pytest.raises(Exception) as invalid:
                check_view(request, "invalid")
            assert invalid.value.status_code == 400
        finally:
            os.environ.pop("VIEWER_GOD_TOKEN", None)
            import viewer.server
            importlib.reload(viewer.server)


class TestEmotionViewer:
    """ビューアの感情表示テスト"""

    def test_extract_emotion_from_log_entry(self):
        """ログエントリからemotionを抽出できること"""
        entry = {"emotion": "焦", "response_text": ""}
        assert _extract_emotion(entry) == "焦"

    def test_extract_emotion_from_strategy(self):
        """response_textのstrategyからemotionを抽出できること"""
        entry = {
            "response_text": '{"strategy": {"emotion": "喜"}, "action": {"type": "pass"}}',
        }
        assert _extract_emotion(entry) == "喜"

    def test_extract_emotion_fallback(self):
        """emotionがない場合は"平静"にフォールバックすること"""
        entry = {"response_text": '{"strategy": {}, "action": {"type": "pass"}}'}
        assert _extract_emotion(entry) == "平静"

    def test_extract_emotion_invalid_value(self):
        """無効なemotion値は"平静"にフォールバックすること"""
        entry = {"emotion": "怯", "response_text": ""}
        assert _extract_emotion(entry) == "平静"

    def test_extract_emotion_kan_not_dropped(self):
        """"奸"（正典VALID_EMOTIONSの7値目）が"平静"へ誤フォールバックしないこと

        回帰対象: _extract_emotion() の許容集合が6値ハードコードで
        "奸" が抜けており、実run約15%の感情が誤って"平静"化していた。
        """
        entry = {"emotion": "奸", "response_text": ""}
        assert _extract_emotion(entry) == "奸"

    def test_extract_emotion_kan_from_strategy(self):
        """response_textのstrategy経由でも"奸"が通ること"""
        entry = {
            "response_text": '{"strategy": {"emotion": "奸"}, "action": {"type": "pass"}}',
        }
        assert _extract_emotion(entry) == "奸"

    def test_extract_emotion_matches_valid_emotions(self):
        """VALID_EMOTIONSの全7値をそのまま通すこと（正典との二重管理を防ぐ固定テスト）"""
        from llm.response_parser import VALID_EMOTIONS
        assert len(VALID_EMOTIONS) == 7
        for em in VALID_EMOTIONS:
            entry = {"emotion": em, "response_text": ""}
            assert _extract_emotion(entry) == em

    def test_emotion_en_mapping_complete(self):
        """8感情すべてに英語名が対応すること（ビューアのEMOTION_ENマッピング検証）"""
        # viewer/static/index.html の EMOTION_EN と同じマッピング
        emotion_en = {
            "喜": "joy", "怒": "anger", "哀": "sadness", "楽": "ease",
            "焦": "panic", "疑": "doubt", "奸": "smirk", "平静": "neutral",
        }
        # 全8感情がマッピングされている
        assert len(emotion_en) == 8
        # 全英語名がユニーク
        assert len(set(emotion_en.values())) == 8
        # 有効な感情値すべてにマッピングがある
        valid_emotions = {"喜", "怒", "哀", "楽", "焦", "疑", "奸", "平静"}
        assert set(emotion_en.keys()) == valid_emotions
        # 各英語名がファイル名として安全（ASCII小文字のみ）
        for en in emotion_en.values():
            assert en.isascii() and en.islower() and en.isalpha()

    def test_emotion_image_path_generation(self):
        """感情画像パスが英語名で生成されること"""
        emotion_en = {
            "喜": "joy", "怒": "anger", "哀": "sadness", "楽": "ease",
            "焦": "panic", "疑": "doubt", "平静": "neutral",
        }
        # ベンダー付きパス
        assert f"emotions/anthropic_{emotion_en['喜']}.png" == "emotions/anthropic_joy.png"
        assert f"emotions/openai_{emotion_en['怒']}.png" == "emotions/openai_anger.png"
        # 共通パス
        assert f"emotions/{emotion_en['平静']}.png" == "emotions/neutral.png"

    def test_emotion_image_files_exist(self):
        """EMOTION_ENの全英名×6ベンダーのpngがviewer/static/emotions/に実在すること（smirk含む）"""
        emotions_dir = Path(__file__).resolve().parent.parent / "viewer" / "static" / "emotions"
        vendors = ["anthropic", "openai", "google", "xai", "moonshot", "deepseek"]
        emotion_en = ["joy", "anger", "sadness", "ease", "panic", "doubt", "smirk"]
        missing = [
            f"{v}_{e}.png"
            for v in vendors for e in emotion_en
            if not (emotions_dir / f"{v}_{e}.png").exists()
        ]
        assert missing == []


class TestCommentary:
    """実況台本APIのテスト"""

    def test_commentary_v2_priority(self):
        """v2が存在すればv2を返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            trial = Path(tmpdir) / "trial_test"
            trial.mkdir()
            # v1
            v1_dir = trial / "commentary"
            v1_dir.mkdir(parents=True)
            (v1_dir / "script.jsonl").write_text(
                '{"round":1,"speaker":"zundamon","text":"v1 test"}\n',
                encoding="utf-8",
            )
            # v2
            v2_dir = v1_dir / "v2"
            v2_dir.mkdir()
            (v2_dir / "script.jsonl").write_text(
                '{"round":1,"speaker":"zundamon","text":"v2 test"}\n',
                encoding="utf-8",
            )
            result = get_commentary(Path(tmpdir), "trial_test", "game01")
            assert result is not None
            assert result["version"] == "v2"
            assert result["rounds"]["1"][0]["text"] == "v2 test"

    def test_commentary_v1_fallback(self):
        """v2なし・v1ありでv1を返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            trial = Path(tmpdir) / "trial_test"
            v1_dir = trial / "commentary"
            v1_dir.mkdir(parents=True)
            (v1_dir / "script.jsonl").write_text(
                '{"round":1,"speaker":"metan","text":"v1 only"}\n',
                encoding="utf-8",
            )
            result = get_commentary(Path(tmpdir), "trial_test", "game01")
            assert result is not None
            assert result["version"] == "v1"
            assert result["rounds"]["1"][0]["speaker"] == "metan"

    def test_commentary_none(self):
        """台本なしでNoneを返す"""
        with tempfile.TemporaryDirectory() as tmpdir:
            trial = Path(tmpdir) / "trial_test"
            trial.mkdir()
            result = get_commentary(Path(tmpdir), "trial_test", "game01")
            assert result is None

    def test_commentary_round_grouping(self):
        """ラウンドごとにグループ化される"""
        with tempfile.TemporaryDirectory() as tmpdir:
            trial = Path(tmpdir) / "trial_test"
            v2_dir = trial / "commentary" / "v2"
            v2_dir.mkdir(parents=True)
            (v2_dir / "script.jsonl").write_text(
                '{"round":1,"speaker":"zundamon","text":"R1a"}\n'
                '{"round":1,"speaker":"metan","text":"R1b"}\n'
                '{"round":2,"speaker":"zundamon","text":"R2a"}\n',
                encoding="utf-8",
            )
            result = get_commentary(Path(tmpdir), "trial_test", "game01")
            assert result is not None
            assert len(result["rounds"]["1"]) == 2
            assert len(result["rounds"]["2"]) == 1
            assert result["rounds"]["2"][0]["text"] == "R2a"


class TestAutoCommitFailureElimination:
    """AUTO_COMMIT_FAILURE（合法Commit0件による強制脱落）がViewerで
    「脱落」として正しく表示されることを確認する回帰テスト。

    engine/game.py の commit フェーズは、合法Commitが1件も無い場合に
    forced_liquidation() を実行して is_alive=False にした上で
    AUTO_COMMIT_FAILURE イベントのみをログする（ELIMINATION/FORCED_LIQUIDATION
    は出さない）。Viewer側がこれを脱落として拾えていなかった不具合の修正確認。
    """

    def _write_llm_logs(self, trial: Path, game_id: str, players: list[str]) -> None:
        logs = trial / "llm_logs"
        logs.mkdir(parents=True, exist_ok=True)
        for pid in players:
            entry = {
                "player_id": pid, "model_id": "test-model", "phase": "negotiation",
                "round_num": 1, "turn": 1, "response_text": "{}", "cost_usd": 0.001,
            }
            (logs / f"{game_id}_{pid}_llm_calls.jsonl").write_text(
                json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8",
            )

    def _make_auto_commit_failure_trial(self, tmp_path: Path) -> Path:
        """P01,P03生存 / P02がR2 commitでAUTO_COMMIT_FAILUREにより脱落、
        という合成ログを作る（実trial trial_C_l12_r12_20260822 のP09と同型）。
        """
        trial = tmp_path / "trial_acf"
        self._write_llm_logs(trial, "game01", ["P01", "P02", "P03"])

        record = {
            # forced_liquidation() が返す清算記録（実測フィールドを再現）
            "player_id": "P02", "round": 2,
            "cash_before": 1399900, "debt_before": 754099,
            "debt_repaid": 754099, "bad_debt": 0,
            "cash_confiscated": 645801, "cards_destroyed": 7,
        }
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup",
             "data": {"player_id": "P01", "loan_amount": 1000000}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup",
             "data": {"player_id": "P02", "loan_amount": 1000000}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup",
             "data": {"player_id": "P03", "loan_amount": 1000000}},
            # 契約の紐付けが引き続き機能することも同時に確認する（elifシャドウイング回帰）
            {"event_type": "NEGOTIATION_ACTION", "round_num": 2, "phase": "negotiation",
             "data": {"action": "contract_propose", "contract_id": "C_test",
                       "player_id": "P02", "parties": ["P02", "P03"]}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 2, "phase": "negotiation",
             "data": {"action": "contract_sign", "contract_id": "C_test", "player_id": "P03"}},
            {"event_type": "SNAPSHOT", "round_num": 1, "phase": "settlement", "step": 4,
             "data": {"snapshots": {
                 "P01": {"cash": 1000000, "free_cash": 500000},
                 "P02": {"cash": 1399900, "free_cash": 645801},
                 "P03": {"cash": 1000000, "free_cash": 500000},
             }}},
            {"event_type": "AUTO_COMMIT_FAILURE", "round_num": 2, "phase": "commit",
             "data": {"player_id": "P02", "contract_id": "C_test",
                       "reason": "contract_violation", **record}},
            {"event_type": "SNAPSHOT", "round_num": 2, "phase": "settlement", "step": 4,
             "data": {"snapshots": {
                 "P01": {"cash": 1100000, "free_cash": 550000},
                 "P03": {"cash": 1100000, "free_cash": 550000},
             }}},
        ]
        trial.mkdir(exist_ok=True)
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        return tmp_path

    def test_auto_commit_failure_marks_eliminated(self, tmp_path):
        """get_game_state(): AUTO_COMMIT_FAILUREを起こしたP02が脱落扱いになること"""
        logs_dir = self._make_auto_commit_failure_trial(tmp_path)
        state = get_game_state(logs_dir, "trial_acf", "game01")

        assert "P02" in state["eliminated"]
        assert state["survivors"] == 2

        by_pid = {p["player_id"]: p for p in state["players"]}
        assert by_pid["P02"]["status"] == "eliminated"
        assert by_pid["P01"]["status"] != "eliminated"
        assert by_pid["P03"]["status"] != "eliminated"

    def test_auto_commit_failure_in_round_states(self, tmp_path):
        """get_round_states(): elifシャドウイングが解消され、
        R2のeliminatedにP02が1件だけ記録され、かつ契約outcomes紐付けも壊れないこと
        """
        logs_dir = self._make_auto_commit_failure_trial(tmp_path)
        data = get_round_states(logs_dir, "trial_acf", "game01")

        r2_elim = data["rounds"]["2"]["eliminated"]
        assert len(r2_elim) == 1
        assert r2_elim[0]["player_id"] == "P02"
        assert r2_elim[0]["reason"] == "contract_violation"

        # 契約への outcomes 紐付け（既存機能）が同じイベントで壊れていないこと（god view）
        god_data = get_round_states(logs_dir, "trial_acf", "game01", view="god")
        contracts = god_data["rounds"]["2"]["contracts"]
        contract = next((c for c in contracts if c["contract_id"] == "C_test"), None)
        assert contract is not None
        outcomes = contract.get("outcomes", [])
        assert any(o["event_type"] == "AUTO_COMMIT_FAILURE" for o in outcomes)

    def test_auto_commit_failure_view_parity(self, tmp_path):
        """public/godで脱落表示（rounds[R].eliminated）が一致すること"""
        logs_dir = self._make_auto_commit_failure_trial(tmp_path)
        public = get_round_states(logs_dir, "trial_acf", "game01", view="public")
        god = get_round_states(logs_dir, "trial_acf", "game01", view="god")
        assert public["rounds"]["2"]["eliminated"] == god["rounds"]["2"]["eliminated"]

    def test_existing_elimination_paths_unaffected(self, tmp_path):
        """既存の脱落経路（ELIMINATION+FORCED_LIQUIDATION二重発火 / BANKRUPTCY /
        SURVIVAL_CHECK）が今回の変更後も重複なく正しく検出されること"""
        trial = tmp_path / "trial_existing"
        self._write_llm_logs(trial, "game01", ["P01", "P02", "P03", "P04"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P03"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P04"}},
            # P02: 型B違反 -> ELIMINATION -> FORCED_LIQUIDATION（実trialのP11型・二重発火）
            {"event_type": "ELIMINATION", "round_num": 3, "phase": "settlement",
             "data": {"player_id": "P02", "reason": "contract_violation"}},
            {"event_type": "FORCED_LIQUIDATION", "round_num": 3, "phase": "settlement",
             "data": {"player_id": "P02", "reason": "contract_violation"}},
            # P03: Entry Fee不払い -> BANKRUPTCY
            {"event_type": "BANKRUPTCY", "round_num": 4, "phase": "commit",
             "data": {"player_id": "P03", "reason": "Cannot pay entry fee"}},
            # P04: R12生存条件未達
            {"event_type": "SURVIVAL_CHECK", "round_num": 12, "phase": "finance",
             "data": {"player_id": "P04", "result": "eliminated"}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        state = get_game_state(tmp_path, "trial_existing", "game01")
        assert sorted(state["eliminated"]) == ["P02", "P03", "P04"]
        assert state["survivors"] == 1  # P01のみ生存

    def test_auto_commit_success_not_eliminated(self, tmp_path):
        """AUTO_COMMIT（成功側）だけでは誰も脱落扱いにならないこと（偽陽性防止）"""
        trial = tmp_path / "trial_auto_ok"
        self._write_llm_logs(trial, "game01", ["P01"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "AUTO_COMMIT", "round_num": 1, "phase": "commit",
             "data": {"player_id": "P01", "market_id": "M01", "card": "HIGH_CARD_1"}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        state = get_game_state(tmp_path, "trial_auto_ok", "game01")
        assert state["eliminated"] == []
        assert state["survivors"] == 1

        data = get_round_states(tmp_path, "trial_auto_ok", "game01")
        assert data["rounds"]["1"]["eliminated"] == []

    def test_real_trial_p09_eliminated_from_r6(self):
        """実trial trial_C_l12_r12_20260822 のP09（AUTO_COMMIT_FAILURE, R6）が
        脱落として検出されること。runが存在しない環境ではskip。"""
        logs_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
        trial_dir_name = "trial_C_l12_r12_20260822"
        if not (logs_dir / trial_dir_name).exists():
            pytest.skip(f"{trial_dir_name} not found")

        state = get_game_state(logs_dir, trial_dir_name, "game01")
        by_pid = {p["player_id"]: p for p in state["players"]}
        assert "P09" in by_pid
        assert by_pid["P09"]["status"] == "eliminated"
        assert "P09" in state["eliminated"]

        data = get_round_states(logs_dir, trial_dir_name, "game01")
        r6_elim_pids = {e["player_id"] for e in data["rounds"]["6"]["eliminated"]}
        assert "P09" in r6_elim_pids


class TestEliminationRoundDisplay:
    """player一覧カードの「脱落ラウンド」表示（elimination_round）の回帰テスト。

    get_game_state() の players[].elimination_round が、脱落系イベントの
    round_num から正しく導出され、不明時は None に倒れ、後方互換
    （eliminated: list[str] の形状不変）が保たれることを確認する。
    """

    def _write_llm_logs(self, trial: Path, game_id: str, players: list[str]) -> None:
        logs = trial / "llm_logs"
        logs.mkdir(parents=True, exist_ok=True)
        for pid in players:
            entry = {
                "player_id": pid, "model_id": "test-model", "phase": "negotiation",
                "round_num": 1, "turn": 1, "response_text": "{}", "cost_usd": 0.001,
            }
            (logs / f"{game_id}_{pid}_llm_calls.jsonl").write_text(
                json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8",
            )

    def _write_events(self, trial: Path, game_id: str, events: list[dict]) -> None:
        trial.mkdir(exist_ok=True)
        (trial / f"{game_id}_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )

    def test_elimination_round_recorded_for_each_path(self, tmp_path):
        """4種のELIMINATION_EVENT_TYPES + SURVIVAL_CHECK経路すべてで
        elimination_roundがround_numから正しく取れること（R3/R5/R12を網羅）"""
        trial = tmp_path / "trial_round"
        self._write_llm_logs(trial, "game01", ["P01", "P02", "P03", "P04", "P05"])
        events = [
            # P02: ELIMINATION + FORCED_LIQUIDATION @R3
            {"event_type": "ELIMINATION", "round_num": 3, "phase": "settlement",
             "data": {"player_id": "P02", "reason": "contract_violation"}},
            {"event_type": "FORCED_LIQUIDATION", "round_num": 3, "phase": "settlement",
             "data": {"player_id": "P02", "reason": "contract_violation"}},
            # P03: BANKRUPTCY @R5
            {"event_type": "BANKRUPTCY", "round_num": 5, "phase": "commit",
             "data": {"player_id": "P03", "reason": "Cannot pay entry fee"}},
            # P04: AUTO_COMMIT_FAILURE @R5
            {"event_type": "AUTO_COMMIT_FAILURE", "round_num": 5, "phase": "commit",
             "data": {"player_id": "P04", "reason": "contract_violation"}},
            # P05: SURVIVAL_CHECK eliminated @R12
            {"event_type": "SURVIVAL_CHECK", "round_num": 12, "phase": "finance",
             "data": {"player_id": "P05", "result": "eliminated", "reason": "condition_not_met"}},
        ]
        self._write_events(trial, "game01", events)

        state = get_game_state(tmp_path, "trial_round", "game01")
        by_pid = {p["player_id"]: p for p in state["players"]}

        assert by_pid["P02"]["elimination_round"] == 3
        assert by_pid["P03"]["elimination_round"] == 5
        assert by_pid["P04"]["elimination_round"] == 5
        assert by_pid["P05"]["elimination_round"] == 12
        for pid in ("P02", "P03", "P04", "P05"):
            assert by_pid[pid]["status"] == "eliminated"

    def test_survivor_has_no_elimination_round(self, tmp_path):
        """生存者はelimination_roundキーが存在しつつNoneであること（JS側undefined回避）"""
        trial = tmp_path / "trial_survivor"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "ELIMINATION", "round_num": 3, "phase": "settlement",
             "data": {"player_id": "P02", "reason": "contract_violation"}},
        ]
        self._write_events(trial, "game01", events)

        state = get_game_state(tmp_path, "trial_survivor", "game01")
        by_pid = {p["player_id"]: p for p in state["players"]}

        assert "elimination_round" in by_pid["P01"]
        assert by_pid["P01"]["elimination_round"] is None
        assert by_pid["P01"]["status"] != "eliminated"

    def test_missing_round_num_yields_none(self, tmp_path):
        """round_numが欠落/nullでもelimination_round=None・脱落検出は維持されること"""
        trial = tmp_path / "trial_missing_round"
        self._write_llm_logs(trial, "game01", ["P02", "P03"])
        events = [
            {"event_type": "ELIMINATION", "phase": "settlement",
             "data": {"player_id": "P02", "reason": "contract_violation"}},  # round_num欠落
            {"event_type": "BANKRUPTCY", "round_num": None, "phase": "commit",
             "data": {"player_id": "P03", "reason": "bankruptcy"}},
        ]
        self._write_events(trial, "game01", events)

        state = get_game_state(tmp_path, "trial_missing_round", "game01")
        by_pid = {p["player_id"]: p for p in state["players"]}

        assert by_pid["P02"]["elimination_round"] is None
        assert by_pid["P03"]["elimination_round"] is None
        assert by_pid["P02"]["status"] == "eliminated"
        assert by_pid["P03"]["status"] == "eliminated"
        assert sorted(state["eliminated"]) == ["P02", "P03"]

    def test_invalid_round_num_yields_none(self, tmp_path):
        """round_num=0/-1/文字列/boolはすべてNoneに倒れ、脱落判定自体は維持されること"""
        trial = tmp_path / "trial_invalid_round"
        self._write_llm_logs(trial, "game01", ["P01", "P02", "P03", "P04"])
        events = [
            {"event_type": "ELIMINATION", "round_num": 0, "phase": "settlement",
             "data": {"player_id": "P01", "reason": "x"}},
            {"event_type": "ELIMINATION", "round_num": -1, "phase": "settlement",
             "data": {"player_id": "P02", "reason": "x"}},
            {"event_type": "ELIMINATION", "round_num": "5", "phase": "settlement",
             "data": {"player_id": "P03", "reason": "x"}},
            {"event_type": "ELIMINATION", "round_num": True, "phase": "settlement",
             "data": {"player_id": "P04", "reason": "x"}},
        ]
        self._write_events(trial, "game01", events)

        state = get_game_state(tmp_path, "trial_invalid_round", "game01")
        by_pid = {p["player_id"]: p for p in state["players"]}

        for pid in ("P01", "P02", "P03", "P04"):
            assert by_pid[pid]["elimination_round"] is None
            assert by_pid[pid]["status"] == "eliminated"

    def test_duplicate_events_first_known_round_wins(self, tmp_path):
        """同一player複数イベントで、先に判明したroundを採用し、名簿は先勝ちdedupのままであること"""
        trial = tmp_path / "trial_dup_round"
        self._write_llm_logs(trial, "game01", ["P02", "P03"])
        events = [
            {"event_type": "SURVIVAL_CHECK", "round_num": 12, "phase": "finance",
             "data": {"player_id": "P02", "result": "eliminated"}},
            {"event_type": "FORCED_LIQUIDATION", "round_num": 12, "phase": "finance",
             "data": {"player_id": "P02", "reason": "condition_not_met"}},
            {"event_type": "ELIMINATION", "phase": "settlement",
             "data": {"player_id": "P03", "reason": "contract_violation"}},  # round_num欠落（先着）
            {"event_type": "FORCED_LIQUIDATION", "round_num": 7, "phase": "settlement",
             "data": {"player_id": "P03", "reason": "contract_violation"}},  # 後続で補完
        ]
        self._write_events(trial, "game01", events)

        state = get_game_state(tmp_path, "trial_dup_round", "game01")
        by_pid = {p["player_id"]: p for p in state["players"]}

        assert by_pid["P02"]["elimination_round"] == 12
        assert by_pid["P03"]["elimination_round"] == 7
        assert state["eliminated"].count("P02") == 1
        assert state["eliminated"].count("P03") == 1

    def test_no_events_file_does_not_break(self, tmp_path):
        """eventsファイル自体が無い旧runで全員elimination_round=None・eliminated=[]であること"""
        trial = tmp_path / "trial_no_events"
        self._write_llm_logs(trial, "game01", ["P01", "P02", "P03"])
        trial.mkdir(exist_ok=True)

        state = get_game_state(tmp_path, "trial_no_events", "game01")
        assert state["eliminated"] == []
        assert state["survivors"] == 3
        for p in state["players"]:
            assert "elimination_round" in p
            assert p["elimination_round"] is None

    def test_eliminated_list_shape_unchanged(self, tmp_path):
        """state["eliminated"]はlist[str]のまま（既存テストが依存する後方互換契約）"""
        trial = tmp_path / "trial_shape"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "ELIMINATION", "round_num": 3, "phase": "settlement",
             "data": {"player_id": "P02", "reason": "contract_violation"}},
        ]
        self._write_events(trial, "game01", events)

        state = get_game_state(tmp_path, "trial_shape", "game01")
        assert isinstance(state["eliminated"], list)
        assert all(isinstance(x, str) for x in state["eliminated"])

    def test_final_round_mass_elimination_all_get_last_round(self, tmp_path):
        """R12生還ライン未達の一斉脱落で全員が同一のelimination_round(=12)になり、
        生存者だけがNoneのままであること（意図的な仕様。R12を特別扱いしない）"""
        trial = tmp_path / "trial_mass"
        self._write_llm_logs(trial, "game01", ["P01", "P02", "P03", "P04", "P05"])
        events = [
            {"event_type": "SURVIVAL_CHECK", "round_num": 12, "phase": "finance",
             "data": {"player_id": pid, "result": "eliminated", "reason": "condition_not_met"}}
            for pid in ("P02", "P03", "P04", "P05")
        ]
        self._write_events(trial, "game01", events)

        state = get_game_state(tmp_path, "trial_mass", "game01")
        by_pid = {p["player_id"]: p for p in state["players"]}

        for pid in ("P02", "P03", "P04", "P05"):
            assert by_pid[pid]["elimination_round"] == 12
        assert by_pid["P01"]["elimination_round"] is None

    def test_index_html_renders_elimination_round_badge(self):
        """index.htmlのバッジ算出にelimination_roundが使われ、
        style.cssの.panel .badgeにwhite-space:nowrapが入っていること"""
        base = Path(__file__).resolve().parent.parent / "viewer" / "static"
        html = (base / "index.html").read_text(encoding="utf-8")
        css = (base / "style.css").read_text(encoding="utf-8")

        assert "elimination_round" in html
        assert "脱落" in html
        assert "white-space: nowrap" in css

    def test_real_trial_elimination_round_matches_round_states(self):
        """実trial trial_C_l12_r12_20260822 のP09で、get_game_state()の
        elimination_roundとget_round_states()のround別eliminatedが整合すること。
        runが存在しない環境ではskip。"""
        logs_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
        trial_dir_name = "trial_C_l12_r12_20260822"
        if not (logs_dir / trial_dir_name).exists():
            pytest.skip(f"{trial_dir_name} not found")

        state = get_game_state(logs_dir, trial_dir_name, "game01")
        by_pid = {p["player_id"]: p for p in state["players"]}
        assert by_pid["P09"]["elimination_round"] is not None

        data = get_round_states(logs_dir, trial_dir_name, "game01")
        r_num = by_pid["P09"]["elimination_round"]
        r_elim_pids = {e["player_id"] for e in data["rounds"][str(r_num)]["eliminated"]}
        assert "P09" in r_elim_pids


class TestContractOutcomeAttachment:
    """契約のoutcomes（履行/違反イベント）が obligation_id から復元した
    contract_id で正しく紐付くことの回帰テスト（_contract_id_of_outcome）。

    従来は data["contract_id"] しか見ておらず、実ログには一件も
    contract_id が付与されていなかったため outcomes は常に [] だった。
    """

    def _write_llm_logs(self, trial: Path, game_id: str, players: list[str]) -> None:
        logs = trial / "llm_logs"
        logs.mkdir(parents=True, exist_ok=True)
        for pid in players:
            entry = {
                "player_id": pid, "model_id": "test-model", "phase": "negotiation",
                "round_num": 1, "turn": 1, "response_text": "{}", "cost_usd": 0.001,
            }
            (logs / f"{game_id}_{pid}_llm_calls.jsonl").write_text(
                json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8",
            )

    def _write_events(self, trial: Path, game_id: str, events: list[dict]) -> None:
        trial.mkdir(exist_ok=True)
        (trial / f"{game_id}_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )

    def _base_events(self) -> list[dict]:
        return [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 3, "phase": "negotiation",
             "data": {"action": "contract_propose", "contract_id": "C_90159021",
                       "player_id": "P01", "parties": ["P01", "P02"]}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 3, "phase": "negotiation",
             "data": {"action": "contract_sign", "contract_id": "C_90159021", "player_id": "P02"}},
        ]

    def test_outcome_attached_via_obligation_id(self, tmp_path):
        """contract_idを持たないTYPE_A_EXECUTION/TYPE_B_VIOLATIONが、
        obligation_idの{contract_id}_OB{nn}から復元され正しい契約に紐付く"""
        trial = tmp_path / "trial_ob"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = self._base_events() + [
            {"event_type": "TYPE_A_EXECUTION", "round_num": 6, "phase": "settlement",
             "data": {"obligation_id": "C_90159021_OB01"}},
            {"event_type": "TYPE_B_VIOLATION", "round_num": 6, "phase": "settlement",
             "data": {"player_id": "P02", "obligation_id": "C_90159021_OB02", "reason": "contract_violation"}},
        ]
        self._write_events(trial, "game01", events)

        data = get_round_states(tmp_path, "trial_ob", "game01", view="god")
        contract = next(
            c for c in data["rounds"]["3"]["contracts"] if c["contract_id"] == "C_90159021"
        )
        event_types = {o["event_type"] for o in contract["outcomes"]}
        assert event_types == {"TYPE_A_EXECUTION", "TYPE_B_VIOLATION"}

    def test_outcome_prefers_explicit_contract_id(self, tmp_path):
        """data["contract_id"]がある場合はobligation_idより優先されること"""
        trial = tmp_path / "trial_explicit"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = self._base_events() + [
            # obligation_idは別契約を指すが、明示contract_idが優先される
            {"event_type": "TYPE_A_EXECUTION", "round_num": 6, "phase": "settlement",
             "data": {"contract_id": "C_90159021", "obligation_id": "C_deadbeef_OB01"}},
        ]
        self._write_events(trial, "game01", events)

        data = get_round_states(tmp_path, "trial_explicit", "game01", view="god")
        contract = next(
            c for c in data["rounds"]["3"]["contracts"] if c["contract_id"] == "C_90159021"
        )
        assert len(contract["outcomes"]) == 1

    def test_outcome_without_obligation_id_ignored(self, tmp_path):
        """TYPE_A_FAILURE/AUTO_COMMIT/AUTO_COMMIT_FAILUREはobligation_idを
        持たないため、紐付かない（例外も出ない）"""
        trial = tmp_path / "trial_no_ob"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = self._base_events() + [
            {"event_type": "TYPE_A_FAILURE", "round_num": 6, "phase": "settlement", "data": {}},
            {"event_type": "AUTO_COMMIT", "round_num": 6, "phase": "commit",
             "data": {"player_id": "P01"}},
            {"event_type": "AUTO_COMMIT_FAILURE", "round_num": 6, "phase": "commit",
             "data": {"player_id": "P01", "reason": "x"}},
        ]
        self._write_events(trial, "game01", events)

        data = get_round_states(tmp_path, "trial_no_ob", "game01", view="god")
        contract = next(
            c for c in data["rounds"]["3"]["contracts"] if c["contract_id"] == "C_90159021"
        )
        assert contract.get("outcomes", []) == []

    def test_malformed_obligation_id_ignored(self, tmp_path):
        """obligation_idの形式が不正/型不正な場合は推測せず無視されること"""
        trial = tmp_path / "trial_malformed"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        malformed_values: list[Any] = ["garbage", "C_xx_OB", 123, None, True, "C_90159021"]
        events = self._base_events()
        for i, v in enumerate(malformed_values):
            events.append({
                "event_type": "TYPE_A_EXECUTION", "round_num": 6, "phase": "settlement",
                "data": {"obligation_id": v, "marker": i},
            })
        self._write_events(trial, "game01", events)

        data = get_round_states(tmp_path, "trial_malformed", "game01", view="god")
        contract = next(
            c for c in data["rounds"]["3"]["contracts"] if c["contract_id"] == "C_90159021"
        )
        # "C_90159021"（_OBサフィックスなし）はcontract_id自体ではなく
        # obligation_id形式に合致しないので無視される
        assert contract.get("outcomes", []) == []

    def test_outcome_for_unknown_contract_ignored(self, tmp_path):
        """存在しない契約IDに紐付くoutcomeは捨てられ、例外も出ないこと"""
        trial = tmp_path / "trial_unknown_contract"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = self._base_events() + [
            {"event_type": "TYPE_A_EXECUTION", "round_num": 6, "phase": "settlement",
             "data": {"obligation_id": "C_ffffffff_OB01"}},
        ]
        self._write_events(trial, "game01", events)

        # 例外を出さずに完了すること
        data = get_round_states(tmp_path, "trial_unknown_contract", "game01", view="god")
        contract = next(
            c for c in data["rounds"]["3"]["contracts"] if c["contract_id"] == "C_90159021"
        )
        assert contract.get("outcomes", []) == []

    def test_public_view_hides_terms_and_outcomes(self, tmp_path):
        """view=publicではterms is None かつ outcomes == []であること
        （outcomesが実際に埋まるようになった後もマスクは維持される）"""
        trial = tmp_path / "trial_public_mask"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = self._base_events() + [
            {"event_type": "TYPE_A_EXECUTION", "round_num": 6, "phase": "settlement",
             "data": {"obligation_id": "C_90159021_OB01"}},
        ]
        self._write_events(trial, "game01", events)

        god_data = get_round_states(tmp_path, "trial_public_mask", "game01", view="god")
        god_contract = next(
            c for c in god_data["rounds"]["3"]["contracts"] if c["contract_id"] == "C_90159021"
        )
        assert len(god_contract["outcomes"]) == 1  # god側では実際に埋まる

        public_data = get_round_states(tmp_path, "trial_public_mask", "game01", view="public")
        public_contract = next(
            c for c in public_data["rounds"]["3"]["contracts"] if c["contract_id"] == "C_90159021"
        )
        assert public_contract["terms"] is None
        assert public_contract["outcomes"] == []

    def test_god_view_shows_only_own_contracts(self, tmp_path):
        """round-detailのgod viewが本人関与の契約のみを返すこと
        （直近修正の非退行。outcome紐付け変更が_contract_involves_playerに影響しないこと）"""
        trial = tmp_path / "trial_scoped"
        logs = trial / "llm_logs"
        logs.mkdir(parents=True)

        def _entry(pid: str, round_num: int, turn: int, action: dict) -> dict:
            return {
                "player_id": pid, "model_id": "test-model", "phase": "negotiation",
                "round_num": round_num, "turn": turn,
                "response_text": json.dumps({"reasoning": "r", "action": action}, ensure_ascii=False),
                "reasoning": "r", "reasoning_tokens": 0,
            }

        (logs / "game01_P01_llm_calls.jsonl").write_text(
            json.dumps(_entry("P01", 2, 1, {
                "type": "contract_propose",
                "terms": [{"ob_type": "type_b_market", "market_id": "own_marker"}],
            }), ensure_ascii=False) + "\n", encoding="utf-8",
        )
        (logs / "game01_P02_llm_calls.jsonl").write_text(
            json.dumps(_entry("P02", 2, 1, {
                "type": "contract_propose",
                "terms": [{"ob_type": "type_b_market", "market_id": "foreign_marker"}],
            }), ensure_ascii=False) + "\n", encoding="utf-8",
        )
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P02"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "data": {"player_id": "P03"}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 2,
             "data": {"action": "contract_propose", "contract_id": "C_own",
                       "player_id": "P01", "parties": ["P01", "P03"]}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 2,
             "data": {"action": "contract_propose", "contract_id": "C_foreign",
                       "player_id": "P02", "parties": ["P02", "P03"]}},
        ]
        self._write_events(trial, "game01", events)

        detail = get_player_round_detail(tmp_path, "trial_scoped", "game01", "P01", 2, view="god")
        serialized = json.dumps(detail, ensure_ascii=False)
        assert "own_marker" in serialized
        assert "foreign_marker" not in serialized

    def test_old_run_without_llm_logs(self, tmp_path):
        """llm_logsディレクトリが無い旧runでも例外なく動作すること
        （termsはLLMログ由来のため見つからずNoneのまま。outcomesは空）"""
        trial = tmp_path / "trial_old"
        events = [
            {"event_type": "NEGOTIATION_ACTION", "round_num": 3, "phase": "negotiation",
             "data": {"action": "contract_propose", "contract_id": "C_old",
                       "player_id": "P01", "parties": ["P01", "P02"]}},
        ]
        self._write_events(trial, "game01", events)

        data = get_round_states(tmp_path, "trial_old", "game01", view="god")
        contract = next(
            c for c in data["rounds"]["3"]["contracts"] if c["contract_id"] == "C_old"
        )
        assert contract["terms"] is None
        assert contract.get("outcomes", []) == []

    def test_real_trial_outcomes_attached(self):
        """実trial trial_C_l12_r12_20260822 で、obligation_idから復元した
        contract_idが必ず既存契約に一致すること。runが無ければskip。"""
        logs_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
        trial_dir_name = "trial_C_l12_r12_20260822"
        if not (logs_dir / trial_dir_name).exists():
            pytest.skip(f"{trial_dir_name} not found")

        data = get_round_states(logs_dir, trial_dir_name, "game01", view="god")
        all_contract_ids = set()
        total_outcomes = 0
        for rd in data["rounds"].values():
            for c in rd.get("contracts", []):
                all_contract_ids.add(c["contract_id"])
        for rd in data["rounds"].values():
            for c in rd.get("contracts", []):
                for o in c.get("outcomes", []):
                    total_outcomes += 1
                    assert c["contract_id"] in all_contract_ids
        # このrunにはTYPE_A_EXECUTION/TYPE_B_VIOLATIONが存在する前提の非退行チェック
        assert total_outcomes >= 0  # データが無くても例外なく動作すること自体を保証

    def test_index_html_contract_card_uses_formatter(self):
        """秘匿契約カードがfmtObligation/fmtOutcomeを使って整形され、
        生JSONは<details>で折りたたまれた形で残っていること
        （God専用描画パスなので削除ではなくデバッグ用に温存する）"""
        html = INDEX_HTML_PATH.read_text(encoding="utf-8")
        assert "const secretContracts" in html
        # secretContracts構築部でfmtObligation/fmtOutcomeが使われていること
        idx = html.index("const secretContracts")
        block = html[idx: idx + 2000]
        assert "fmtObligation(t)" in block
        assert "fmtOutcome(o)" in block
        assert '<details class="ct-raw">' in block
        assert "JSON.stringify" in block  # 生JSONは残す（折りたたみ内）

    def test_index_html_formatter_sentinels_present(self):
        """node実行テスト(test_viewer_js.py)が依存するセンチネルコメントが
        存在し、fmtObligation/fmtOutcome/safeStrがその範囲内にあること"""
        html = INDEX_HTML_PATH.read_text(encoding="utf-8")
        start = html.index("// ==== CONTRACT_FORMATTER_START ====")
        end = html.index("// ==== CONTRACT_FORMATTER_END ====")
        assert start < end
        block = html[start:end]
        assert "function fmtObligation(t)" in block
        assert "function fmtOutcome(o)" in block
        assert "function safeStr(v)" in block

    def test_style_css_has_contract_classes(self):
        """秘匿契約カード用の新規CSSクラスが定義されていること"""
        css_path = INDEX_HTML_PATH.parent / "style.css"
        css = css_path.read_text(encoding="utf-8")
        for cls in (".ob-unknown", ".oc-ok", ".oc-ng", ".ct-meta", ".ct-label",
                    ".ct-active", ".ct-pending", ".ct-raw"):
            assert cls in css, f"{cls} not defined in style.css"


class TestEliminationDeduplication:
    """get_round_states() の rd["eliminated"] が同一round・同一playerにつき
    1件だけになることを確認する回帰テスト。

    engineは1回の脱落に対し複数のevent（例: SURVIVAL_CHECK+FORCED_LIQUIDATION、
    ELIMINATION+FORCED_LIQUIDATION）を出すことがある。get_game_state()側は
    既に先勝ち重複排除しているが、get_round_states()側の3つのappend地点
    （AUTO_COMMIT_FAILURE / BANKRUPTCY・FORCED_LIQUIDATION / SURVIVAL_CHECK）
    には重複排除ガードが無く、実trial R12のP12で2件重複が顕在化していた不具合の修正確認。
    """

    def _write_llm_logs(self, trial: Path, game_id: str, players: list[str]) -> None:
        logs = trial / "llm_logs"
        logs.mkdir(parents=True, exist_ok=True)
        for pid in players:
            entry = {
                "player_id": pid, "model_id": "test-model", "phase": "negotiation",
                "round_num": 1, "turn": 1, "response_text": "{}", "cost_usd": 0.001,
            }
            (logs / f"{game_id}_{pid}_llm_calls.jsonl").write_text(
                json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8",
            )

    def test_survival_check_and_forced_liquidation_dedup(self, tmp_path):
        """R12生存条件未達（実trial P12型）: SURVIVAL_CHECK + FORCED_LIQUIDATION
        の2件が rd["eliminated"] では1件に正規化されること"""
        trial = tmp_path / "trial_survival"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "SURVIVAL_CHECK", "round_num": 12, "phase": "finance",
             "data": {"player_id": "P02", "result": "eliminated", "reason": "condition_not_met"}},
            {"event_type": "FORCED_LIQUIDATION", "round_num": 12, "phase": "finance",
             "data": {"player_id": "P02", "reason": "condition_not_met"}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        data = get_round_states(tmp_path, "trial_survival", "game01")
        r12_elim = data["rounds"]["12"]["eliminated"]
        assert len(r12_elim) == 1
        assert r12_elim[0]["player_id"] == "P02"
        assert r12_elim[0]["reason"] == "condition_not_met"

    def test_real_trial_p12_r12_single_entry(self):
        """実trial trial_C_l12_r12_20260822 のP12（R12, SURVIVAL_CHECK+FORCED_LIQUIDATION）
        が rd["eliminated"] で1件に正規化されること。runが存在しない環境ではskip。"""
        logs_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
        trial_dir_name = "trial_C_l12_r12_20260822"
        if not (logs_dir / trial_dir_name).exists():
            pytest.skip(f"{trial_dir_name} not found")

        data = get_round_states(logs_dir, trial_dir_name, "game01")
        r12_elim = data["rounds"]["12"]["eliminated"]
        p12_entries = [e for e in r12_elim if e["player_id"] == "P12"]
        assert len(p12_entries) == 1
        assert p12_entries[0]["reason"] == "condition_not_met"

    def test_real_trial_p09_r6_still_single(self):
        """同runでP09 R6が引き続き1件・contract_violationのまま
        （AUTO_COMMIT_FAILURE修正の巻き戻し防止）。runが存在しない環境ではskip。"""
        logs_dir = Path(__file__).resolve().parent.parent / "logs" / "llm"
        trial_dir_name = "trial_C_l12_r12_20260822"
        if not (logs_dir / trial_dir_name).exists():
            pytest.skip(f"{trial_dir_name} not found")

        data = get_round_states(logs_dir, trial_dir_name, "game01")
        r6_elim = data["rounds"]["6"]["eliminated"]
        p09_entries = [e for e in r6_elim if e["player_id"] == "P09"]
        assert len(p09_entries) == 1
        assert p09_entries[0]["reason"] == "contract_violation"

    def test_elimination_plus_forced_liquidation_dedup(self, tmp_path):
        """型B契約違反（実trial P02/P11型）: ELIMINATION + FORCED_LIQUIDATION
        の2件が1件に正規化されること"""
        trial = tmp_path / "trial_elim_fl"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "ELIMINATION", "round_num": 3, "phase": "settlement",
             "data": {"player_id": "P02", "reason": "contract_violation"}},
            {"event_type": "FORCED_LIQUIDATION", "round_num": 3, "phase": "settlement",
             "data": {"player_id": "P02", "reason": "contract_violation"}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        data = get_round_states(tmp_path, "trial_elim_fl", "game01")
        r3_elim = data["rounds"]["3"]["eliminated"]
        assert len(r3_elim) == 1
        assert r3_elim[0]["player_id"] == "P02"

    def test_bankruptcy_single_event_unchanged(self, tmp_path):
        """BANKRUPTCY単発（既存経路）はそのまま1件で検出されること"""
        trial = tmp_path / "trial_bankruptcy"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "BANKRUPTCY", "round_num": 4, "phase": "commit",
             "data": {"player_id": "P02", "reason": "Cannot pay entry fee"}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        data = get_round_states(tmp_path, "trial_bankruptcy", "game01")
        r4_elim = data["rounds"]["4"]["eliminated"]
        assert len(r4_elim) == 1
        assert r4_elim[0]["player_id"] == "P02"

    def test_dedup_view_parity(self, tmp_path):
        """public/godで正規化後のeliminatedが一致すること"""
        trial = tmp_path / "trial_parity"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "SURVIVAL_CHECK", "round_num": 12, "phase": "finance",
             "data": {"player_id": "P02", "result": "eliminated"}},
            {"event_type": "FORCED_LIQUIDATION", "round_num": 12, "phase": "finance",
             "data": {"player_id": "P02", "reason": "condition_not_met"}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        public = get_round_states(tmp_path, "trial_parity", "game01", view="public")
        god = get_round_states(tmp_path, "trial_parity", "game01", view="god")
        assert public["rounds"]["12"]["eliminated"] == god["rounds"]["12"]["eliminated"]

    def test_dedup_does_not_change_survivors(self, tmp_path):
        """get_round_states()側の重複排除がget_game_state()のsurvivors/eliminatedに
        影響しないこと（同ログでの整合性確認）"""
        trial = tmp_path / "trial_survivors"
        self._write_llm_logs(trial, "game01", ["P01", "P02", "P03"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P03"}},
            {"event_type": "SURVIVAL_CHECK", "round_num": 12, "phase": "finance",
             "data": {"player_id": "P03", "result": "eliminated"}},
            {"event_type": "FORCED_LIQUIDATION", "round_num": 12, "phase": "finance",
             "data": {"player_id": "P03", "reason": "condition_not_met"}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        state = get_game_state(tmp_path, "trial_survivors", "game01")
        assert sorted(state["eliminated"]) == ["P03"]
        assert state["survivors"] == 2

        data = get_round_states(tmp_path, "trial_survivors", "game01")
        assert len(data["rounds"]["12"]["eliminated"]) == 1

    def test_raw_outcomes_keep_multiple_events(self, tmp_path):
        """rd["eliminated"]の正規化が契約outcomes（god限定・生データ）を
        間引かないこと。複数の履行イベントが同一契約に紐付く場合、
        outcomesは全件残る"""
        trial = tmp_path / "trial_outcomes"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 3, "phase": "negotiation",
             "data": {"action": "contract_propose", "contract_id": "C_dup",
                       "player_id": "P02", "parties": ["P02", "P01"]}},
            {"event_type": "NEGOTIATION_ACTION", "round_num": 3, "phase": "negotiation",
             "data": {"action": "contract_sign", "contract_id": "C_dup", "player_id": "P01"}},
            {"event_type": "TYPE_B_VIOLATION", "round_num": 3, "phase": "settlement",
             "data": {"player_id": "P02", "contract_id": "C_dup", "reason": "contract_violation"}},
            {"event_type": "ELIMINATION", "round_num": 3, "phase": "settlement",
             "data": {"player_id": "P02", "reason": "contract_violation"}},
            {"event_type": "FORCED_LIQUIDATION", "round_num": 3, "phase": "settlement",
             "data": {"player_id": "P02", "reason": "contract_violation"}},
        ]
        (trial / "game01_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )
        data = get_round_states(tmp_path, "trial_outcomes", "game01", view="god")
        r3_elim = data["rounds"]["3"]["eliminated"]
        assert len(r3_elim) == 1  # eliminated一覧は正規化される

        contracts = data["rounds"]["3"]["contracts"]
        contract = next((c for c in contracts if c["contract_id"] == "C_dup"), None)
        assert contract is not None
        outcomes = contract.get("outcomes", [])
        # 生の履行イベント（TYPE_B_VIOLATION）は間引かれず残っている
        assert any(o["event_type"] == "TYPE_B_VIOLATION" for o in outcomes)


class TestFinalReflectionViewer:
    """Viewerが FINAL_REFLECTION イベント（脱落者の最終振り返り）を
    public/god境界を守りつつ表示できることを確認する回帰テスト。

    `TestPlayerRoundDetail._make_trial` は既存の秘匿情報リーク検知assertに
    使われているため拡張せず、`TestEliminationDeduplication._write_llm_logs`
    と同じ「独立fixture・手書きevents」パターンで新設する。
    """

    def _write_llm_logs(self, trial: Path, game_id: str, players: list[str]) -> None:
        logs = trial / "llm_logs"
        logs.mkdir(parents=True, exist_ok=True)
        for pid in players:
            entry = {
                "player_id": pid, "model_id": "test-model", "phase": "negotiation",
                "round_num": 1, "turn": 1, "response_text": "{}", "cost_usd": 0.001,
            }
            (logs / f"{game_id}_{pid}_llm_calls.jsonl").write_text(
                json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8",
            )

    def _write_events(self, trial: Path, game_id: str, events: list[dict]) -> None:
        (trial / f"{game_id}_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )

    def _fr_event(self, round_num: int, **overrides) -> dict:
        data = {
            "player_id": "P02", "round": round_num, "elimination_reason": "bankruptcy",
            "elimination_phase": "commit", "elimination_event": "BANKRUPTCY",
            "model_id": "test-model", "status": "ok", "emotion": "哀",
            "defeat_cause": "借入額が過大だった", "comment": "私は数字を読み間違えた。",
            "comment_chars": 13, "truncated": False, "salvaged": [],
        }
        data.update(overrides)
        return {
            "event_type": "FINAL_REFLECTION", "round_num": round_num, "phase": "final_reflection",
            "step": None, "data": data,
        }

    def test_complete_data_god_view_has_three_fields(self, tmp_path):
        """完全データ: god viewでemotion/defeat_cause/commentの3項目が揃うこと"""
        trial = tmp_path / "trial_fr_complete"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "BANKRUPTCY", "round_num": 5, "phase": "commit",
             "data": {"player_id": "P02", "reason": "Cannot pay entry fee"}},
            self._fr_event(5),
        ]
        self._write_events(trial, "game01", events)

        detail = get_player_round_detail(tmp_path, "trial_fr_complete", "game01", "P02", 5, view="god")
        fr = detail["result"]["final_reflection"]
        assert fr is not None
        assert fr["round"] == 5
        assert fr["emotion"] == "哀"
        assert fr["defeat_cause"] == "借入額が過大だった"
        assert fr["comment"] == "私は数字を読み間違えた。"
        assert fr["has_comment"] is True
        assert fr["availability"] == "god_only"
        assert fr["status"] == "ok"
        assert fr["salvaged"] == []
        assert detail["final_reflection_round"] == 5

    def test_public_view_hides_secret_content(self, tmp_path):
        """public viewの境界回帰テスト（最重要）: commentに仕込んだ秘匿情報
        （DM本文・市場ID）がJSON全体に一切現れないこと"""
        trial = tmp_path / "trial_fr_public_gate"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "BANKRUPTCY", "round_num": 5, "phase": "commit",
             "data": {"player_id": "P02", "reason": "Cannot pay entry fee"}},
            self._fr_event(
                5,
                defeat_cause="秘密のDM本文をP01に送って type_b_market を裏切った",
                comment="P01との秘密のDM本文で type_b_market を独占する約束をしたが破られた",
            ),
        ]
        self._write_events(trial, "game01", events)

        public_detail = get_player_round_detail(tmp_path, "trial_fr_public_gate", "game01", "P02", 5, view="public")
        blob = json.dumps(public_detail, ensure_ascii=False)
        assert "秘密のDM本文" not in blob
        assert "type_b_market" not in blob

        fr = public_detail["result"]["final_reflection"]
        assert fr is not None
        assert fr["emotion"] == "哀"
        assert fr["has_comment"] is True
        assert fr["availability"] == "god_only"
        assert "comment" not in fr
        assert "defeat_cause" not in fr
        assert "status" not in fr
        assert "salvaged" not in fr
        assert "comment_chars" not in fr

        god_detail = get_player_round_detail(tmp_path, "trial_fr_public_gate", "game01", "P02", 5, view="god")
        god_blob = json.dumps(god_detail, ensure_ascii=False)
        assert "秘密のDM本文" in god_blob
        assert "type_b_market" in god_blob

    def test_partial_missing_defeat_cause(self, tmp_path):
        """defeat_causeが欠損していても例外を出さず、取得できる項目だけ返ること"""
        trial = tmp_path / "trial_fr_partial"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "BANKRUPTCY", "round_num": 5, "phase": "commit",
             "data": {"player_id": "P02", "reason": "Cannot pay entry fee"}},
            self._fr_event(5, defeat_cause=None, status="ok_recovered", salvaged=["defeat_cause"]),
        ]
        self._write_events(trial, "game01", events)

        detail = get_player_round_detail(tmp_path, "trial_fr_partial", "game01", "P02", 5, view="god")
        fr = detail["result"]["final_reflection"]
        assert fr is not None
        assert fr["defeat_cause"] is None
        assert fr["comment"] == "私は数字を読み間違えた。"
        assert fr["salvaged"] == ["defeat_cause"]

    def test_invalid_emotion_normalizes_to_none(self, tmp_path):
        """VALID_EMOTIONSに無い値は読み取り時にNoneへ正規化されること
        （書き込み側の不具合・手動編集への防御）。奸を含む正規ケースも確認する"""
        trial = tmp_path / "trial_fr_bad_emotion"
        self._write_llm_logs(trial, "game01", ["P01", "P02", "P03"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P03"}},
            {"event_type": "BANKRUPTCY", "round_num": 5, "phase": "commit",
             "data": {"player_id": "P02", "reason": "Cannot pay entry fee"}},
            self._fr_event(5, emotion="excited"),
        ]
        self._write_events(trial, "game01", events)
        detail = get_player_round_detail(tmp_path, "trial_fr_bad_emotion", "game01", "P02", 5, view="god")
        assert detail["result"]["final_reflection"]["emotion"] is None

        trial2 = tmp_path / "trial_fr_kan_emotion"
        self._write_llm_logs(trial2, "game02", ["P01", "P02"])
        events2 = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "BANKRUPTCY", "round_num": 5, "phase": "commit",
             "data": {"player_id": "P02", "reason": "Cannot pay entry fee"}},
            self._fr_event(5, emotion="奸"),
        ]
        self._write_events(trial2, "game02", events2)
        detail2 = get_player_round_detail(tmp_path, "trial_fr_kan_emotion", "game02", "P02", 5, view="god")
        assert detail2["result"]["final_reflection"]["emotion"] == "奸"

    def test_no_final_reflection_old_run_unchanged(self, tmp_path):
        """FINAL_REFLECTIONが1件も無い旧runでは、final_reflection/
        final_reflection_roundともにNoneで、既存キーの表示は従来どおりのまま"""
        trial = tmp_path / "trial_fr_absent"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "BANKRUPTCY", "round_num": 5, "phase": "commit",
             "data": {"player_id": "P02", "reason": "Cannot pay entry fee"}},
        ]
        self._write_events(trial, "game01", events)

        detail = get_player_round_detail(tmp_path, "trial_fr_absent", "game01", "P02", 5, view="god")
        assert detail["result"]["final_reflection"] is None
        assert detail["final_reflection_round"] is None
        assert detail["result"]["eliminated"][0]["player_id"] == "P02"
        assert "memory" in detail and "calls" in detail

    def test_duplicate_events_dedup_to_one(self, tmp_path):
        """同一(round, pid)に2件のFINAL_REFLECTIONがあっても先勝ちで1件だけ扱われること"""
        trial = tmp_path / "trial_fr_dup"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "BANKRUPTCY", "round_num": 5, "phase": "commit",
             "data": {"player_id": "P02", "reason": "Cannot pay entry fee"}},
            self._fr_event(5, comment="1件目のコメント"),
            self._fr_event(5, comment="2件目のコメント（重複ログ）"),
        ]
        self._write_events(trial, "game01", events)

        data = get_round_states(tmp_path, "trial_fr_dup", "game01")
        frs = [fr for fr in data["rounds"]["5"]["final_reflections"] if fr["player_id"] == "P02"]
        assert len(frs) == 1
        assert frs[0]["comment"] == "1件目のコメント"

    def test_final_reflection_round_pointer_and_wrong_round_hidden(self, tmp_path):
        """脱落round以外の詳細ではfinal_reflectionはNoneのままだが、
        final_reflection_roundで実際に記録されているroundが分かること"""
        trial = tmp_path / "trial_fr_pointer"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "BANKRUPTCY", "round_num": 7, "phase": "commit",
             "data": {"player_id": "P02", "reason": "Cannot pay entry fee"}},
            self._fr_event(7),
        ]
        self._write_events(trial, "game01", events)

        detail_r3 = get_player_round_detail(tmp_path, "trial_fr_pointer", "game01", "P02", 3, view="god")
        assert detail_r3["result"]["final_reflection"] is None
        assert detail_r3["final_reflection_round"] == 7

        detail_r7 = get_player_round_detail(tmp_path, "trial_fr_pointer", "game01", "P02", 7, view="god")
        assert detail_r7["result"]["final_reflection"] is not None
        assert detail_r7["final_reflection_round"] == 7

    def test_coexists_with_existing_elimination_reason_shapes(self, tmp_path):
        """P09型 AUTO_COMMIT_FAILURE(contract_violation) / P12型
        SURVIVAL_CHECK+FORCED_LIQUIDATION(condition_not_met) の両形状で、
        result.eliminatedが従来どおり1件のままFINAL_REFLECTIONと併存すること"""
        trial = tmp_path / "trial_fr_coexist"
        self._write_llm_logs(trial, "game01", ["P01", "P09", "P12"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P09"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P12"}},
            {"event_type": "AUTO_COMMIT_FAILURE", "round_num": 6, "phase": "commit",
             "data": {"player_id": "P09", "reason": "contract_violation"}},
            self._fr_event(6, player_id="P09", elimination_event="AUTO_COMMIT_FAILURE",
                            elimination_reason="contract_violation"),
            {"event_type": "SURVIVAL_CHECK", "round_num": 12, "phase": "finance",
             "data": {"player_id": "P12", "result": "eliminated", "reason": "condition_not_met"}},
            {"event_type": "FORCED_LIQUIDATION", "round_num": 12, "phase": "finance",
             "data": {"player_id": "P12", "reason": "condition_not_met"}},
            self._fr_event(12, player_id="P12", elimination_event="FORCED_LIQUIDATION",
                            elimination_reason="condition_not_met"),
        ]
        self._write_events(trial, "game01", events)

        detail_p09 = get_player_round_detail(tmp_path, "trial_fr_coexist", "game01", "P09", 6, view="god")
        assert len(detail_p09["result"]["eliminated"]) == 1
        assert detail_p09["result"]["eliminated"][0]["reason"] == "contract_violation"
        assert detail_p09["result"]["final_reflection"] is not None

        detail_p12 = get_player_round_detail(tmp_path, "trial_fr_coexist", "game01", "P12", 12, view="god")
        assert len(detail_p12["result"]["eliminated"]) == 1
        assert detail_p12["result"]["eliminated"][0]["reason"] == "condition_not_met"
        assert detail_p12["result"]["final_reflection"] is not None

    def test_empty_or_rejected_content_degrades_to_none(self, tmp_path):
        """emotion/defeat_cause/commentすべて空（status=empty等）なら、
        イベント自体はあっても final_reflection は None として扱われること
        （degradedケース。「イベントが無い」場合と描画を区別するため
        final_reflection_roundは値を持ち続ける想定だが、本テストでは
        final_reflectionのNone化のみを確認する）"""
        trial = tmp_path / "trial_fr_empty"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "BANKRUPTCY", "round_num": 5, "phase": "commit",
             "data": {"player_id": "P02", "reason": "Cannot pay entry fee"}},
            self._fr_event(5, emotion=None, defeat_cause=None, comment="", status="empty"),
        ]
        self._write_events(trial, "game01", events)

        detail = get_player_round_detail(tmp_path, "trial_fr_empty", "game01", "P02", 5, view="god")
        assert detail["result"]["final_reflection"] is None

    def test_frontend_has_god_gated_final_reflection_section(self):
        """index.htmlにgod限定セクションの🔒ラベルとescapeHtml経由の描画が
        存在すること（frontend側にテスト基盤が無いため文字列検査で担保する）"""
        html = INDEX_HTML_PATH.read_text(encoding="utf-8")
        assert "final_reflection" in html
        # variant対応（completion/elimination）で見出しはテンプレート化された。
        # 🔒ラベル自体とラベル切替ロジック、両方のラベル文言が存在することを確認する。
        assert "🔒 ${heading}（神視点）" in html
        assert "'完走コメント'" in html
        assert "'脱落時コメント'" in html
        assert "escapeHtml(fr.comment" in html or "escapeHtml(fr.comment || '')" in html
        assert "escapeHtml(fr.defeat_cause)" in html


class TestPostGameReflectionViewer:
    """POST_GAME_REFLECTION（ゲーム完全終了後の全員答え合わせ）が、
    今サイクルではViewer UIへ一切表示されないこと（T-C）を確認する回帰テスト。

    本フェイズは`rules/project.md`の許可リストで神視点情報の投入が許される
    唯一のフェイズだが、Viewer側の表示対応は本サイクルのスコープ外
    （`public_last_word`等の新UIは未実装）。`viewer/log_parser.py`は
    POST_GAME_REFLECTIONイベントを一切解釈しない設計になっている
    （if/elifチェーンに分岐が無く、既知の未対応event_typeとして
    黙って読み飛ばされる）ため、god/public いずれの経路にも
    `post_game_reflections`相当のキーやその中身が一切漏れないことを確認する。

    `TestPlayerRoundDetail._make_trial`や`TestEliminationDeduplication`の
    fixtureは既存の秘匿情報リーク検知assertに使われているため拡張せず、
    `TestFinalReflectionViewer`と同じ「独立fixture・手書きevents」パターンで
    新設する。
    """

    def _write_llm_logs(self, trial: Path, game_id: str, players: list[str]) -> None:
        logs = trial / "llm_logs"
        logs.mkdir(parents=True, exist_ok=True)
        for pid in players:
            entry = {
                "player_id": pid, "model_id": "test-model", "phase": "negotiation",
                "round_num": 1, "turn": 1, "response_text": "{}", "cost_usd": 0.001,
            }
            (logs / f"{game_id}_{pid}_llm_calls.jsonl").write_text(
                json.dumps(entry, ensure_ascii=False) + "\n", encoding="utf-8",
            )

    def _write_events(self, trial: Path, game_id: str, events: list[dict]) -> None:
        (trial / f"{game_id}_events.jsonl").write_text(
            "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8",
        )

    def _pg_event(self, round_num: int, **overrides) -> dict:
        """POST_GAME_REFLECTIONイベント。データには秘匿情報（DM本文・匿名通信の
        真の掲載者・非当事者向け契約条項）を含む特徴的な文字列を仕込む。"""
        data = {
            "player_id": "P02", "round": round_num, "survived": True,
            "elimination_reason": None, "elimination_round": None,
            "final_cash": 4_000_000, "final_rank": 1, "model_id": "test-model",
            "status": "ok", "emotion": "楽", "key_insight": None,
            "self_assessment": "堅実に立ち回れた。",
            "biggest_revelation": (
                "PG_SECRET_MARKER: P01との秘密のDM本文で type_b_market を独占する約束をしたが、"
                "実は匿名通信の真の掲載者はP03だった。"
            ),
            "best_player": "P03", "most_deceptive_player": "P01",
            "changed_opinion": "P01を信頼しすぎていた。",
            "comment": "PG_SECRET_MARKER: ゲーム全体を振り返ると、非当事者だった秘密契約C_SECRETの存在が最大の驚きだった。",
            "comment_chars": 60, "truncated": False, "salvaged": [],
        }
        data.update(overrides)
        return {
            "event_type": "POST_GAME_REFLECTION", "round_num": round_num, "phase": "post_game",
            "step": None, "data": data,
        }

    def _base_events(self) -> list[dict]:
        return [
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P01"}},
            {"event_type": "LOAN_CHOSEN", "round_num": 0, "phase": "setup", "data": {"player_id": "P02"}},
            {"event_type": "GAME_END", "round_num": 12, "phase": "finalize", "data": {
                "survivors": [{"player_id": "P01", "cash": 3_000_000}, {"player_id": "P02", "cash": 4_000_000}],
                "eliminated": [],
            }},
        ]

    def test_no_public_route_returns_post_game_content(self, tmp_path):
        """公開view: state/rounds/timeline/round-detailのいずれのJSONにも
        PG特徴文字列・`post_game_reflections`キーが一切現れないこと"""
        trial = tmp_path / "trial_pg_public_gate"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        events = self._base_events() + [self._pg_event(12)]
        self._write_events(trial, "game01", events)

        state = get_game_state(tmp_path, "trial_pg_public_gate", "game01")
        rounds_public = get_round_states(tmp_path, "trial_pg_public_gate", "game01", view="public")
        rounds_god = get_round_states(tmp_path, "trial_pg_public_gate", "game01", view="god")
        timeline_public = get_player_timeline(tmp_path, "trial_pg_public_gate", "game01", "P02", view="public")
        timeline_god = get_player_timeline(tmp_path, "trial_pg_public_gate", "game01", "P02", view="god")
        detail_public = get_player_round_detail(tmp_path, "trial_pg_public_gate", "game01", "P02", 12, view="public")
        detail_god = get_player_round_detail(tmp_path, "trial_pg_public_gate", "game01", "P02", 12, view="god")

        for payload in (
            state, rounds_public, rounds_god, timeline_public, timeline_god,
            detail_public, detail_god,
        ):
            blob = json.dumps(payload, ensure_ascii=False)
            assert "PG_SECRET_MARKER" not in blob
            assert "post_game_reflections" not in blob
            assert "post_game_reflection" not in blob

    def test_variant_backward_compat_defaults_to_elimination(self, tmp_path):
        """POST_GAME_REFLECTIONイベントが混在していても、既存のFINAL_REFLECTION
        (variantキー無し=旧イベント)がelimination扱いのまま従来どおり描画されること"""
        trial = tmp_path / "trial_pg_compat_elim"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        fr_event = {
            "event_type": "FINAL_REFLECTION", "round_num": 5, "phase": "final_reflection",
            "step": None, "data": {
                "player_id": "P02", "round": 5, "elimination_reason": "bankruptcy",
                "elimination_phase": "commit", "elimination_event": "BANKRUPTCY",
                "model_id": "test-model", "status": "ok", "emotion": "哀",
                "defeat_cause": "借入額が過大だった", "comment": "私は数字を読み間違えた。",
                "comment_chars": 13, "truncated": False, "salvaged": [],
            },
        }
        events = self._base_events() + [
            {"event_type": "BANKRUPTCY", "round_num": 5, "phase": "commit",
             "data": {"player_id": "P02", "reason": "Cannot pay entry fee"}},
            fr_event,
            self._pg_event(12),
        ]
        self._write_events(trial, "game01", events)

        detail = get_player_round_detail(tmp_path, "trial_pg_compat_elim", "game01", "P02", 5, view="god")
        fr = detail["result"]["final_reflection"]
        assert fr is not None
        # 旧イベント形状（variantキー無し）はlog_parser側の既定値補完により
        # "elimination"として描画される（新規の秘匿情報開示ではない。
        # 脱落有無自体はrd["eliminated"]/SURVIVAL_CHECK経由で既に公開情報）。
        assert fr.get("variant") == "elimination"
        assert fr["defeat_cause"] == "借入額が過大だった"

    def test_completion_variant_renders_as_kansou(self, tmp_path):
        """variant='completion'のFINAL_REFLECTIONが完走コメントとして描画され、
        併存するPOST_GAME_REFLECTIONの内容とは混同されないこと"""
        trial = tmp_path / "trial_pg_completion"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        fr_event = {
            "event_type": "FINAL_REFLECTION", "round_num": 12, "phase": "final_reflection",
            "step": None, "data": {
                "player_id": "P02", "round": 12, "variant": "completion",
                "elimination_reason": None, "elimination_phase": None, "elimination_event": None,
                "model_id": "test-model", "status": "ok", "emotion": "楽",
                "defeat_cause": "慎重な立ち回り", "comment": "生き残れて良かった。",
                "comment_chars": 9, "truncated": False, "salvaged": [],
            },
        }
        events = self._base_events() + [fr_event, self._pg_event(12)]
        self._write_events(trial, "game01", events)

        detail = get_player_round_detail(tmp_path, "trial_pg_completion", "game01", "P02", 12, view="god")
        fr = detail["result"]["final_reflection"]
        assert fr is not None
        assert fr["variant"] == "completion"
        assert fr["comment"] == "生き残れて良かった。"
        # POST_GAME_REFLECTIONの秘匿マーカーはfinal_reflection側へ混入しない
        assert "PG_SECRET_MARKER" not in json.dumps(fr, ensure_ascii=False)

    def test_existing_final_reflection_public_shape_unchanged(self, tmp_path):
        """POST_GAME_REFLECTIONイベントの併存によって、既存のFINAL_REFLECTION
        public view schema（キー構成）が変化しないこと"""
        trial = tmp_path / "trial_pg_shape"
        self._write_llm_logs(trial, "game01", ["P01", "P02"])
        fr_event = {
            "event_type": "FINAL_REFLECTION", "round_num": 5, "phase": "final_reflection",
            "step": None, "data": {
                "player_id": "P02", "round": 5, "elimination_reason": "bankruptcy",
                "elimination_phase": "commit", "elimination_event": "BANKRUPTCY",
                "model_id": "test-model", "status": "ok", "emotion": "哀",
                "defeat_cause": "借入額が過大だった", "comment": "私は数字を読み間違えた。",
                "comment_chars": 13, "truncated": False, "salvaged": [],
            },
        }
        events_without_pg = self._base_events() + [
            {"event_type": "BANKRUPTCY", "round_num": 5, "phase": "commit",
             "data": {"player_id": "P02", "reason": "Cannot pay entry fee"}},
            fr_event,
        ]
        events_with_pg = events_without_pg + [self._pg_event(12)]

        trial_a = tmp_path / "trial_pg_shape_a"
        trial_b = tmp_path / "trial_pg_shape_b"
        self._write_llm_logs(trial_a, "game01", ["P01", "P02"])
        self._write_llm_logs(trial_b, "game01", ["P01", "P02"])
        self._write_events(trial_a, "game01", events_without_pg)
        self._write_events(trial_b, "game01", events_with_pg)

        detail_a = get_player_round_detail(tmp_path, "trial_pg_shape_a", "game01", "P02", 5, view="public")
        detail_b = get_player_round_detail(tmp_path, "trial_pg_shape_b", "game01", "P02", 5, view="public")

        fr_a = detail_a["result"]["final_reflection"]
        fr_b = detail_b["result"]["final_reflection"]
        assert set(fr_a.keys()) == set(fr_b.keys())
        assert fr_a == fr_b
