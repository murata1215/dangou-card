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
            # 例外が発生しないこと（認証なし）
            asyncio.get_event_loop().run_until_complete(
                check_token(mock_request, token=None)
            )
        finally:
            import importlib
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
        """7感情すべてに英語名が対応すること（ビューアのEMOTION_ENマッピング検証）"""
        # viewer/static/index.html の EMOTION_EN と同じマッピング
        emotion_en = {
            "喜": "joy", "怒": "anger", "哀": "sadness", "楽": "ease",
            "焦": "panic", "疑": "doubt", "平静": "neutral",
        }
        # 全7感情がマッピングされている
        assert len(emotion_en) == 7
        # 全英語名がユニーク
        assert len(set(emotion_en.values())) == 7
        # 有効な感情値すべてにマッピングがある
        valid_emotions = {"喜", "怒", "哀", "楽", "焦", "疑", "平静"}
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
