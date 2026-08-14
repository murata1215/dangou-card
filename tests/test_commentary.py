"""
実況システムのテスト

- トレース構築: 実ログからパケットを正しく構築できること
- 公開/秘匿分離: PublicLayerにDM・strategy情報が混入しないこと
- 台本フォーマット: 出力形式が正しいこと
- 混入チェック: ずんだもんセリフへの秘匿情報混入を検知できること
- v2: ファクトシート構築・禁止語チェック
"""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# プロジェクトルートをパスに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from engine.commentary.trace import (
    GameTrace,
    PublicLayer,
    RoundPacket,
    SecretLayer,
    build_trace,
    check_banned_words,
    check_leakage,
    extract_secret_keywords,
    ZUNDAMON_BANNED_WORDS,
)

# ---------------------------------------------------------------------------
# テスト対象の試合ディレクトリ
# ---------------------------------------------------------------------------

TRIAL_C_DIR = PROJECT_ROOT / "logs" / "llm" / "trial_C_20260810_053055"
TRIAL_C_EXISTS = TRIAL_C_DIR.exists()


# ---------------------------------------------------------------------------
# トレース構築テスト (実ログ使用)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not TRIAL_C_EXISTS, reason="trial_C logs not available")
class TestTraceBuild:
    """実ログを使ったトレース構築テスト"""

    @pytest.fixture(scope="class")
    def trace(self) -> GameTrace:
        return build_trace(TRIAL_C_DIR)

    def test_packet_count(self, trace: GameTrace):
        """12ラウンド分のパケットが生成される"""
        assert len(trace.packets) == 12

    def test_seat_map(self, trace: GameTrace):
        """座席表が正しく読み込まれる"""
        assert len(trace.seat_map) == 8
        assert "P01" in trace.seat_map
        assert "P08" in trace.seat_map

    def test_r1_markets(self, trace: GameTrace):
        """R1の市場情報が公開層に含まれる"""
        r1 = trace.packets[0]
        assert r1.round_num == 1
        assert len(r1.public.markets) == 3
        market_ids = {m["market_id"] for m in r1.public.markets}
        assert market_ids == {"M01", "M02", "M03"}

    def test_r1_commits(self, trace: GameTrace):
        """R1のコミット結果が公開層に含まれる"""
        r1 = trace.packets[0]
        assert len(r1.public.commits) == 8  # 8プレイヤー

    def test_r1_market_results(self, trace: GameTrace):
        """R1の市場結果が公開層に含まれる"""
        r1 = trace.packets[0]
        assert len(r1.public.market_results) == 3  # 3市場

    def test_r1_snapshots(self, trace: GameTrace):
        """R1のスナップショットが公開層に含まれる"""
        r1 = trace.packets[0]
        assert len(r1.public.snapshots) >= 1
        for pid, snap in r1.public.snapshots.items():
            assert "cash" in snap
            assert "free_cash" in snap

    def test_public_no_dm_leak(self, trace: GameTrace):
        """公開層のJSON文字列にDM固有情報が含まれない"""
        for packet in trace.packets:
            pub_json = json.dumps(packet.public.to_dict(), ensure_ascii=False)
            assert '"recipient"' not in pub_json
            assert '"dms"' not in pub_json

    def test_public_no_strategy_leak(self, trace: GameTrace):
        """公開層のJSON文字列にstrategy情報が含まれない"""
        for packet in trace.packets:
            pub_json = json.dumps(packet.public.to_dict(), ensure_ascii=False)
            assert '"strategies"' not in pub_json

    def test_secret_has_strategies(self, trace: GameTrace):
        """秘匿層にstrategy情報が含まれる(少なくとも一部のラウンドで)"""
        has_strat = any(len(p.secret.strategies) > 0 for p in trace.packets)
        assert has_strat, "秘匿層にstrategyが1件もない"

    def test_secret_has_dms(self, trace: GameTrace):
        """秘匿層にDMが含まれる(少なくとも一部のラウンドで)"""
        has_dm = any(len(p.secret.dms) > 0 for p in trace.packets)
        assert has_dm, "秘匿層にDMが1件もない"

    def test_broadcasts_in_public(self, trace: GameTrace):
        """公開層にbroadcastメッセージが含まれる"""
        has_bc = any(len(p.public.broadcasts) > 0 for p in trace.packets)
        assert has_bc, "公開層にbroadcastが1件もない"

    def test_alive_count(self, trace: GameTrace):
        """生存者数が正しくカウントされる"""
        r1 = trace.packets[0]
        assert r1.public.alive_count == 8  # R1では全員生存

    def test_r12_survival_checks(self, trace: GameTrace):
        """R12に生存判定が含まれる"""
        r12 = trace.packets[11]
        assert len(r12.public.survival_checks) > 0


# ---------------------------------------------------------------------------
# v2: ファクトシート構築テスト (実ログ使用)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not TRIAL_C_EXISTS, reason="trial_C logs not available")
class TestFactsheet:
    """ファクトシート構築テスト"""

    @pytest.fixture(scope="class")
    def trace(self) -> GameTrace:
        return build_trace(TRIAL_C_DIR)

    def test_r1_has_factsheet(self, trace: GameTrace):
        """R1にファクトシートが存在する"""
        r1 = trace.packets[0]
        assert len(r1.public.factsheet) > 0

    def test_r1_factsheet_has_market_results(self, trace: GameTrace):
        """R1のファクトシートに市場結果サマリがある"""
        r1 = trace.packets[0]
        market_facts = [f for f in r1.public.factsheet if f.startswith("市場M")]
        assert len(market_facts) == 3  # 3市場分

    def test_r1_factsheet_has_balance(self, trace: GameTrace):
        """R1のファクトシートに決算後残高がある"""
        r1 = trace.packets[0]
        balance_facts = [f for f in r1.public.factsheet if "決算後残高" in f]
        assert len(balance_facts) == 1

    def test_r12_factsheet_has_all_survival_judgments(self, trace: GameTrace):
        """R12のファクトシートに全プレイヤーの生還判定がある"""
        r12 = trace.packets[11]
        survival_facts = [f for f in r12.public.factsheet if "生還判定" in f]
        # P01は破産(別のファクト)、P02-P08が生還判定 = 7件
        assert len(survival_facts) == 7

    def test_r12_p05_correctly_eliminated(self, trace: GameTrace):
        """R12のP05の生還判定が『未達、284,063円不足』を含む"""
        r12 = trace.packets[11]
        p05_facts = [f for f in r12.public.factsheet if "P05" in f and "生還判定" in f]
        assert len(p05_facts) == 1
        fact = p05_facts[0]
        assert "未達" in fact
        assert "284,063円不足" in fact

    def test_r12_p02_survived(self, trace: GameTrace):
        """R12のP02の生還判定が『達成』を含む"""
        r12 = trace.packets[11]
        p02_facts = [f for f in r12.public.factsheet if "P02" in f and "生還判定" in f]
        assert len(p02_facts) == 1
        assert "達成" in p02_facts[0]

    def test_r12_p01_bankruptcy(self, trace: GameTrace):
        """R12のファクトシートにP01の破産が記録されている"""
        r12 = trace.packets[11]
        bankruptcy_facts = [f for f in r12.public.factsheet if "破産" in f and "P01" in f]
        assert len(bankruptcy_facts) == 1

    def test_factsheet_has_interest(self, trace: GameTrace):
        """ファクトシートに利息情報がある(少なくとも一部のラウンドで)"""
        has_interest = any(
            any("利息" in f for f in p.public.factsheet)
            for p in trace.packets
        )
        assert has_interest


# ---------------------------------------------------------------------------
# 台本フォーマットテスト (モック)
# ---------------------------------------------------------------------------

class TestScriptFormat:
    """台本生成のフォーマットテスト(APIモック)"""

    def _make_mock_trace(self) -> GameTrace:
        """テスト用の最小トレースを作成"""
        pub = PublicLayer(
            markets=[{"market_id": "M01", "base_prize": 500000,
                       "carryover": 0, "prize_pool": 500000}],
            broadcasts=[{"player_id": "P01", "message": "テストなのだ", "turn": 1}],
            commits=[{"player_id": "P01", "market_id": "M01",
                       "card": "HIGH_CARD_1", "rank": "HIGH_CARD"}],
            market_results=[{"market_id": "M01", "participants": 1,
                              "winners": ["P01"], "prize_per_winner": 500000,
                              "total_pool": 500000, "carryover": 0}],
            snapshots={"P01": {"cash": 1500000, "free_cash": 500000}},
            alive_count=8,
            factsheet=["市場M01: P01(Test Bot A)=HIGH_CARD[勝利]。勝者: P01、獲得額500,000円"],
        )
        sec = SecretLayer(
            strategies=[{"player_id": "P01", "round": 1,
                          "text": "reason: テスト戦略", "emotion": "平静"}],
            dms=[{"sender": "P02", "recipient": "P01",
                   "message": "秘密のメッセージです", "turn": 2}],
        )
        packet = RoundPacket(round_num=1, public=pub, secret=sec)
        return GameTrace(
            seat_map={"P01": "Test Bot A", "P02": "Test Bot B"},
            config={"num_rounds": 1, "num_players": 8},
            packets=[packet],
        )

    def test_generate_dry_run_v2(self):
        """v2ドライランで台本が生成される"""
        from scripts.commentary import generate_commentary
        trace = self._make_mock_trace()
        entries, meta = generate_commentary(trace, dry_run=True, version="v2")

        assert len(entries) >= 2
        assert all("round" in e for e in entries)
        assert all("speaker" in e for e in entries)
        assert all("text" in e for e in entries)
        assert meta["total_calls"] == 0
        assert meta["version"] == "v2"

    def test_generate_dry_run_v1(self):
        """v1ドライランで台本が生成される"""
        from scripts.commentary import generate_commentary
        trace = self._make_mock_trace()
        entries, meta = generate_commentary(trace, dry_run=True, version="v1")

        assert len(entries) >= 2
        assert meta["version"] == "v1"

    def test_save_script_format(self, tmp_path):
        """JSONL/MD出力のフォーマットが正しい"""
        from scripts.commentary import save_script

        entries = [
            {"round": 1, "speaker": "zundamon", "text": "テストなのだ"},
            {"round": 1, "speaker": "metan", "text": "テストですわ"},
        ]
        meta = {
            "version": "v2",
            "model": "claude-haiku-4-5-20251001",
            "model_name": "Claude Haiku 4.5",
            "total_calls": 1,
            "total_retries": 0,
            "total_cost_usd": 0.01,
            "total_input_tokens": 1000,
            "total_output_tokens": 500,
            "warnings": [],
            "generated_at": "2026-08-10T00:00:00+00:00",
        }
        seat_map = {"P01": "Test Bot"}

        jsonl_path, md_path = save_script(entries, meta, tmp_path, seat_map)

        assert jsonl_path.exists()
        with open(jsonl_path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        parsed = json.loads(lines[0])
        assert parsed["speaker"] == "zundamon"
        assert parsed["round"] == 1

        assert md_path.exists()
        md_text = md_path.read_text()
        assert "ずんだもん" in md_text
        assert "めたん" in md_text
        assert "ラウンド1" in md_text
        assert "生成情報" in md_text
        assert "v2" in md_text

    def test_parse_script_response(self):
        """AIレスポンスのパースが正しく動作する"""
        from scripts.commentary import _parse_script_response

        resp = '```json\n[{"speaker": "zundamon", "text": "テスト"}]\n```'
        result = _parse_script_response(resp)
        assert len(result) == 1
        assert result[0]["speaker"] == "zundamon"

        resp2 = '[{"speaker": "metan", "text": "テスト"}]'
        result2 = _parse_script_response(resp2)
        assert len(result2) == 1

        result3 = _parse_script_response("これはJSONではない")
        assert result3 == []

    def test_is_truncated(self):
        """尻切れ検出が正しく動作する"""
        from scripts.commentary import _is_truncated

        # 正常: パースできた
        assert _is_truncated("...", [{"speaker": "zundamon"}]) is False

        # 尻切れ: パース失敗+閉じ括弧なし
        assert _is_truncated('[{"speaker": "zundamon", "text": "途中で', []) is True

        # 正常終了: 閉じ括弧あり
        assert _is_truncated('[{"speaker": "zundamon"}]', []) is False


# ---------------------------------------------------------------------------
# 混入チェックテスト
# ---------------------------------------------------------------------------

class TestLeakageCheck:
    """秘匿情報混入チェックのテスト"""

    def test_no_leakage(self):
        """正常ケース: 混入なし"""
        sec = SecretLayer(
            dms=[{"sender": "P02", "recipient": "P01",
                   "message": "秘密の作戦を教えるぞ、M02に全力で行け", "turn": 1}],
        )
        zundamon_lines = [
            "ラウンド1が始まったのだ!",
            "M01の賞金が大きいのだ!",
        ]
        warnings = check_leakage(zundamon_lines, sec)
        assert len(warnings) == 0

    def test_leakage_detected(self):
        """混入ケース: DMの内容がずんだもんのセリフに出現"""
        sec = SecretLayer(
            dms=[{"sender": "P02", "recipient": "P01",
                   "message": "秘密の作戦を教えるぞ、M02に全力で行け", "turn": 1}],
        )
        zundamon_lines = [
            "秘密の作戦を教えるぞ、M02に全力で行けって言ってたのだ!",
        ]
        warnings = check_leakage(zundamon_lines, sec)
        assert len(warnings) > 0

    def test_extract_keywords(self):
        """秘匿層からキーワードが正しく抽出される"""
        sec = SecretLayer(
            dms=[
                {"sender": "P01", "recipient": "P02",
                 "message": "短い。これは長い秘密のメッセージなので抽出される", "turn": 1},
            ],
        )
        kw = extract_secret_keywords(sec)
        assert any("長い秘密のメッセージなので抽出される" in k for k in kw)
        assert not any(k == "短い" for k in kw)


# ---------------------------------------------------------------------------
# v2: 禁止語チェックテスト
# ---------------------------------------------------------------------------

class TestBannedWords:
    """禁止語チェックのテスト"""

    def test_no_violation(self):
        """正常なセリフ: 違反なし"""
        lines = [
            "ラウンド1が始まったのだ!",
            "M01の賞金が大きいのだ!",
            "P02さんが勝ったのだ!",
        ]
        violations = check_banned_words(lines)
        assert len(violations) == 0

    def test_naishim_memo_violation(self):
        """『内心メモ』を含むセリフ: 違反検出"""
        lines = ["内心メモを見たら、P04さんの口約束があったのだ"]
        violations = check_banned_words(lines)
        assert "内心メモ" in violations

    def test_dm_violation(self):
        """『DM』を含むセリフ: 違反検出"""
        lines = ["P02さんのDMを見たのだ"]
        violations = check_banned_words(lines)
        assert "DM" in violations

    def test_naishin_violation(self):
        """『内心』を含むセリフ: 違反検出"""
        lines = ["P05さんの内心では違うことを考えてたのだ"]
        violations = check_banned_words(lines)
        assert "内心" in violations

    def test_metan_uses_banned_words_ok(self):
        """めたんのセリフは禁止語チェック対象外(check_banned_wordsはずんだもん専用)"""
        # check_banned_words自体はずんだもんのセリフのみを受け取る設計
        # めたんのセリフを含めないことがcommentary.pyの責任
        metan_lines = ["内心メモによると、P02は裏切りを計画していたわけですわ"]
        # このテストはcheck_banned_wordsが検出することを確認(めたんとして渡す場合は呼ばない)
        violations = check_banned_words(metan_lines)
        assert len(violations) > 0  # 関数自体は検出する(呼び出し側がフィルタする)

    def test_banned_words_list_not_empty(self):
        """禁止語リストが空でないこと"""
        assert len(ZUNDAMON_BANNED_WORDS) >= 10


# ---------------------------------------------------------------------------
# v2: プロンプト構築テスト
# ---------------------------------------------------------------------------

class TestPrompts:
    """プロンプト構築のテスト"""

    def test_build_system_prompt(self):
        """システムプロンプトが正しく構築される"""
        from engine.commentary.prompts import build_system_prompt, CHARACTER_BLOCK, GAME_KNOWLEDGE_BLOCK
        prompt = build_system_prompt()
        assert CHARACTER_BLOCK in prompt
        assert GAME_KNOWLEDGE_BLOCK in prompt
        assert "ずんだもん" in prompt
        assert "めたん" in prompt
        assert "談合カード" in prompt

    def test_build_user_prompt_has_factsheet(self):
        """ユーザープロンプトにファクトシートセクションが含まれる"""
        from engine.commentary.prompts import build_user_prompt
        packet_dict = {
            "public": {
                "markets": [], "broadcasts": [], "commits": [],
                "market_results": [], "eliminations": [], "survival_checks": [],
                "snapshots": {}, "alive_count": 8, "contracts_proposed": 0,
                "highlights": [],
                "factsheet": ["市場M01: テストファクト"],
            },
            "secret": {"strategies": [], "dms": []},
        }
        prompt = build_user_prompt(packet_dict, {"P01": "Bot"}, 1, 12)
        assert "ファクトシート" in prompt
        assert "市場M01: テストファクト" in prompt
