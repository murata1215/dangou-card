"""
観戦ビューアのパーサ単体テスト

サーバー起動なし。既存のログファイルをfixture的に使用。
"""

import json
import tempfile
from pathlib import Path

import pytest

from viewer.log_parser import (
    LogCache, list_games, get_game_state, get_player_timeline,
    _extract_strategy, _extract_action, _extract_emotion,
    get_commentary, get_round_states, get_player_round_detail,
)


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

    def test_invalid_round_rejected(self, tmp_path):
        logs_dir = self._make_trial(tmp_path)
        with pytest.raises(ValueError):
            get_player_round_detail(logs_dir, "trial_memory", "game01", "P01", 13)

    def test_detail_modal_keeps_raw_timeline_tab(self):
        html = (Path(__file__).resolve().parent.parent / "viewer" / "static" / "index.html").read_text(encoding="utf-8")
        assert 'id="detail-tab-round"' in html
        assert 'id="detail-tab-raw"' in html
        assert "renderRawTimeline" in html


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
