"""
脱落時の最終コメント（FINAL_REFLECTION）機能のテスト

脱落確定ラウンドの末尾で、脱落者本人に1回だけ最終コメント（敗因・他プレイヤー評価・
最後の一言など）を書かせる、演出/記録専用の機能を検証する。

設計:
- Reflectionフェイズ（通常の生存者向け引き継ぎメモリ）の直後に専用フェイズを設け、
  「is_alive=False かつ elimination_round==round_num」で確定した脱落者を state から
  導出して1回だけ final_reflect() を呼ぶ（engineが同一脱落で複数eventを出しても
  二重発火しない設計）
- config.final_reflection_enabled=False（既定）では一切発火しない
- API失敗・budget block・parse失敗のいずれでもGameResult・生存判定には一切影響しない
- self._memory / memory_history には一切書き込まない（通常のHandover Memoryと独立）

検証観点:
1. 発火条件（無効時は発火しない・生存者は呼ばれない・一度呼ばれたら再度呼ばれない）
2. 各脱落サイト（commit/settlement/finance/AUTO_COMMIT_FAILURE/R12 SURVIVAL_CHECK）の
   elimination_context導出
3. 同一脱落に対する二重event（ELIMINATION+FORCED_LIQUIDATION等）でも1回のみ発火
4. 同一ラウンド内の複数脱落がそれぞれ1回ずつ発火
5. 失敗時の安全側動作（GameResultに一切影響しない）
6. parse_final_reflection()のフォールバック挙動
7. build_final_reflection_prompt()の描画内容
8. LLMAgent.final_reflect()がself._memory/memory_historyを一切変更しない
9. 実ログ（trial_C_l12_r12_20260822/game01_events.jsonl）に対するオフライン読み取り専用の
   クロスチェック（7人の脱落者を1回ずつ、5人の生存者を0回として導出できること）
"""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import StubAgent
from engine.models import MarketCommit

from llm.prompt_builder import build_final_reflection_prompt, build_completion_reflection_prompt
from llm.response_parser import parse_final_reflection, VALID_EMOTIONS

from tests.conftest import make_player, make_market


def _commit_for(pid: str, market_id: str, hand):
    card = hand[0]
    return MarketCommit(player_id=pid, market_id=market_id, card=card)


class RecordingFinalReflectAgent(StubAgent):
    """final_reflect() 呼び出しを記録するテスト用エージェント"""

    def __init__(self, result: dict | None = None):
        self.final_reflect_calls: list[tuple[str, int, dict]] = []
        self._result = result or {
            "status": "ok", "comment": "負けた", "emotion": "哀",
            "defeat_cause": "資金不足", "chars": 3, "truncated": False,
        }

    def final_reflect(self, player_state, round_num, visible_state, elimination_context):
        self.final_reflect_calls.append(
            (player_state.player_id, round_num, elimination_context)
        )
        return self._result


class ExplodingFinalReflectAgent(StubAgent):
    """final_reflect()が必ず例外を投げるテスト用エージェント"""

    def __init__(self):
        self.called = False

    def final_reflect(self, player_state, round_num, visible_state, elimination_context):
        self.called = True
        raise RuntimeError("boom")


def _make_game(config, agent_factory=RecordingFinalReflectAgent, num_players=8):
    agents = {f"P{i+1:02d}": agent_factory() for i in range(num_players)}
    game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
    game._setup()
    return game, agents


def _settle_round(game: Game, round_num: int) -> None:
    game._phase_market_open(round_num)
    pids = list(game.players)
    hand = game.players[pids[0]].hand
    game._current_commits = [_commit_for(pids[0], "M01", hand)]
    game._phase_settlement(round_num)
    game._phase_finance(round_num)


def _eliminate(game: Game, pid: str, round_num: int, reason: str = "bankruptcy") -> None:
    """テスト用: state直接操作で脱落を確定させる（elimination.pyは経由しない）"""
    p = game.players[pid]
    game.players[pid] = p.model_copy(update={
        "is_alive": False,
        "elimination_reason": reason,
        "elimination_round": round_num,
    })


class TestFinalReflectionGating:
    """Game._phase_final_reflection() の発火条件"""

    def test_disabled_by_default(self):
        config = GameConfig.baseline_v1(8)
        assert config.final_reflection_enabled is False
        game, agents = _make_game(config)
        _settle_round(game, 1)
        _eliminate(game, "P02", 1)
        game._phase_final_reflection(1)
        assert agents["P02"].final_reflect_calls == []

    def test_enabled_on_s2_presets(self):
        assert GameConfig.baseline_v1_s2(8).final_reflection_enabled is True
        assert GameConfig.default_8_s2().final_reflection_enabled is True

    def test_survivors_never_called(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        game, agents = _make_game(config)
        _settle_round(game, 1)
        game._phase_final_reflection(1)
        for agent in agents.values():
            assert agent.final_reflect_calls == []

    def test_newly_eliminated_player_called_once(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        game, agents = _make_game(config)
        _settle_round(game, 1)
        _eliminate(game, "P02", 1)
        game._phase_final_reflection(1)
        assert len(agents["P02"].final_reflect_calls) == 1
        pid, rnd, ctx = agents["P02"].final_reflect_calls[0]
        assert pid == "P02"
        assert rnd == 1

    def test_once_called_never_called_again_in_later_round(self):
        """一度final_reflectされたプレイヤーは、後続ラウンドで再度呼ばれない"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        game, agents = _make_game(config)
        _settle_round(game, 1)
        _eliminate(game, "P02", 1)
        game._phase_final_reflection(1)
        assert len(agents["P02"].final_reflect_calls) == 1

        _settle_round(game, 2)
        game._phase_final_reflection(2)
        assert len(agents["P02"].final_reflect_calls) == 1

    def test_multiple_eliminations_same_round_each_called_once(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        game, agents = _make_game(config)
        _settle_round(game, 1)
        _eliminate(game, "P02", 1, "bankruptcy")
        _eliminate(game, "P05", 1, "contract_violation")
        game._phase_final_reflection(1)
        assert len(agents["P02"].final_reflect_calls) == 1
        assert len(agents["P05"].final_reflect_calls) == 1
        assert agents["P01"].final_reflect_calls == []

    def test_double_event_does_not_duplicate_call(self):
        """
        同一脱落に対しengineが2つraw event（ELIMINATION+FORCED_LIQUIDATION）を
        出しても、state派生方式のため呼び出しは1回のみ
        """
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        game, agents = _make_game(config)
        _settle_round(game, 1)
        _eliminate(game, "P02", 1, "contract_violation")
        game.logger.log("ELIMINATION", 1, "settlement", step=7, data={
            "player_id": "P02", "reason": "contract_violation",
        })
        game.logger.log("FORCED_LIQUIDATION", 1, "settlement", step=8, data={
            "player_id": "P02", "reason": "contract_violation", "round": 1,
            "cash_before": 100_000, "debt_before": 500_000,
            "debt_repaid": 100_000, "bad_debt": 400_000,
            "cash_confiscated": 0, "cards_destroyed": 2,
        })
        game._phase_final_reflection(1)
        assert len(agents["P02"].final_reflect_calls) == 1

    def test_bot_without_final_reflect_is_skipped_silently(self):
        """final_reflectを持たないagent（Bot/StubAgent）は例外を出さずスキップされる"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        agents = {f"P{i+1:02d}": StubAgent() for i in range(8)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        _settle_round(game, 1)
        _eliminate(game, "P02", 1)
        game._phase_final_reflection(1)  # 例外を投げずに完走すること

    def test_agent_exception_does_not_stop_others_or_raise(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        agents = {f"P{i+1:02d}": RecordingFinalReflectAgent() for i in range(8)}
        agents["P02"] = ExplodingFinalReflectAgent()
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        _settle_round(game, 1)
        _eliminate(game, "P02", 1)
        _eliminate(game, "P03", 1)
        game._phase_final_reflection(1)  # 例外を投げずに完走すること
        assert agents["P02"].called is True
        assert len(agents["P03"].final_reflect_calls) == 1

    def test_logs_final_reflection_event_on_success(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        game, agents = _make_game(config)
        _settle_round(game, 1)
        _eliminate(game, "P02", 1, "bankruptcy")
        game._phase_final_reflection(1)

        events = [e for e in game.logger.events if e.event_type == "FINAL_REFLECTION"]
        assert len(events) == 1
        data = events[0].data
        assert data["player_id"] == "P02"
        assert data["round"] == 1
        assert data["elimination_reason"] == "bankruptcy"
        assert data["status"] == "ok"
        assert data["comment"] == "負けた"
        assert data["emotion"] == "哀"

    def test_logs_final_reflection_event_on_agent_exception(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        agents = {f"P{i+1:02d}": RecordingFinalReflectAgent() for i in range(8)}
        agents["P02"] = ExplodingFinalReflectAgent()
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        _settle_round(game, 1)
        _eliminate(game, "P02", 1)
        game._phase_final_reflection(1)

        events = [e for e in game.logger.events if e.event_type == "FINAL_REFLECTION"]
        assert len(events) == 1
        assert events[0].data["status"] == "error"


class TestBuildEliminationContext:
    """Game._build_elimination_context() のreason_label導出"""

    def test_bankruptcy_context(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        game, _ = _make_game(config)
        _settle_round(game, 1)
        _eliminate(game, "P02", 1, "bankruptcy")
        game.logger.log("BANKRUPTCY", 1, "commit", data={
            "player_id": "P02", "reason": "Cannot pay entry fee", "round": 1,
            "cash_before": 50_000, "debt_before": 300_000,
            "debt_repaid": 50_000, "bad_debt": 250_000,
            "cash_confiscated": 0, "cards_destroyed": 3,
        })
        ctx = game._build_elimination_context("P02", 1)
        assert ctx["reason"] == "bankruptcy"
        assert "破産" in ctx["reason_label"]
        assert ctx["phase"] == "commit"
        assert ctx["event_type"] == "BANKRUPTCY"
        assert ctx["liquidation"]["debt_repaid"] == 50_000
        assert ctx["liquidation"]["cards_destroyed"] == 3
        assert ctx["is_final_round"] is False

    def test_contract_violation_context_settlement(self):
        """settlementのELIMINATION（宣言のみ）+FORCED_LIQUIDATION（清算実体）の合成"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        game, _ = _make_game(config)
        _settle_round(game, 1)
        _eliminate(game, "P02", 1, "contract_violation")
        game.logger.log("ELIMINATION", 1, "settlement", step=7, data={
            "player_id": "P02", "reason": "contract_violation",
        })
        game.logger.log("FORCED_LIQUIDATION", 1, "settlement", step=8, data={
            "player_id": "P02", "reason": "contract_violation", "round": 1,
            "cash_before": 200_000, "debt_before": 600_000,
            "debt_repaid": 200_000, "bad_debt": 400_000,
            "cash_confiscated": 0, "cards_destroyed": 1,
        })
        ctx = game._build_elimination_context("P02", 1)
        assert ctx["reason"] == "contract_violation"
        assert "契約違反" in ctx["reason_label"]
        assert ctx["liquidation"]["debt_repaid"] == 200_000
        assert ctx["phase"] == "settlement"

    def test_auto_commit_failure_augments_reason_label(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        game, _ = _make_game(config)
        _settle_round(game, 1)
        _eliminate(game, "P02", 1, "contract_violation")
        game.logger.log("AUTO_COMMIT_FAILURE", 1, "commit", data={
            "player_id": "P02",
            "reason": "No legal commits available (contradictory contracts)",
            "round": 1,
            "cash_before": 100_000, "debt_before": 500_000,
            "debt_repaid": 100_000, "bad_debt": 400_000,
            "cash_confiscated": 0, "cards_destroyed": 2,
        })
        ctx = game._build_elimination_context("P02", 1)
        assert ctx["reason"] == "contract_violation"
        assert "矛盾" in ctx["reason_label"]
        assert ctx["event_type"] == "AUTO_COMMIT_FAILURE"

    def test_mandatory_repay_bankruptcy_context_finance(self):
        """financeのFORCED_LIQUIDATION単独（強制返済不能による破産）"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 5})
        game, _ = _make_game(config)
        _settle_round(game, 1)
        _eliminate(game, "P02", 1, "bankruptcy")
        game.logger.log("FORCED_LIQUIDATION", 1, "finance", data={
            "player_id": "P02", "reason": "bankruptcy", "round": 1,
            "cash_before": 0, "debt_before": 500_000,
            "debt_repaid": 0, "bad_debt": 500_000,
            "cash_confiscated": 0, "cards_destroyed": 4,
        })
        ctx = game._build_elimination_context("P02", 1)
        assert ctx["phase"] == "finance"
        assert ctx["event_type"] == "FORCED_LIQUIDATION"
        assert ctx["liquidation"]["bad_debt"] == 500_000

    def test_r12_survival_check_context(self):
        """最終ラウンド生存条件未達（SURVIVAL_CHECK + FORCED_LIQUIDATION）"""
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 12})
        game, _ = _make_game(config)
        _eliminate(game, "P02", 12, "condition_not_met")
        game.logger.log("SURVIVAL_CHECK", 12, "finance", data={
            "player_id": "P02", "result": "eliminated", "reason": "condition_not_met",
            "cash": 500_000, "debt": 0,
        })
        game.logger.log("FORCED_LIQUIDATION", 12, "finance", data={
            "player_id": "P02", "reason": "condition_not_met", "round": 12,
            "cash_before": 500_000, "debt_before": 0,
            "debt_repaid": 0, "bad_debt": 0,
            "cash_confiscated": 500_000, "cards_destroyed": 0,
        })
        ctx = game._build_elimination_context("P02", 12)
        assert ctx["reason"] == "condition_not_met"
        assert "生存条件" in ctx["reason_label"] or "最終ラウンド" in ctx["reason_label"]
        assert ctx["is_final_round"] is True
        assert ctx["liquidation"]["cash_confiscated"] == 500_000

    def test_all_three_reasons_produce_distinct_labels(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 12})
        game, _ = _make_game(config)
        labels = set()
        for i, reason in enumerate(["bankruptcy", "contract_violation", "condition_not_met"]):
            pid = f"P0{i+1}"
            _eliminate(game, pid, 1, reason)
            ctx = game._build_elimination_context(pid, 1)
            labels.add(ctx["reason_label"])
        assert len(labels) == 3


class TestFinalReflectionPrompt:
    """build_final_reflection_prompt() の描画内容"""

    def _base_ctx(self, **overrides):
        ctx = {
            "player_id": "P01", "round": 5, "reason": "bankruptcy",
            "reason_label": "破産による脱落（Entry Feeまたは必須返済を支払えなかった）",
            "phase": "commit", "event_type": "BANKRUPTCY",
            "liquidation": {
                "debt_repaid": 300_000, "bad_debt": 200_000,
                "cash_confiscated": 0, "cards_destroyed": 2,
            },
            "is_final_round": False, "survival_cash": 3_000_000,
        }
        ctx.update(overrides)
        return ctx

    def test_prompt_contains_round_and_reason_label(self):
        player = make_player("P01", cash=0, debt=500_000)
        visible_state = {"messages": [], "last_round_results": None, "my_obligations": []}
        config = GameConfig.baseline_v1_s2(8)
        ctx = self._base_ctx()
        prompt = build_final_reflection_prompt(player, 5, visible_state, config, ctx)
        assert "ラウンド5" in prompt
        assert ctx["reason_label"] in prompt

    def test_prompt_contains_liquidation_details(self):
        player = make_player("P01", cash=0, debt=500_000)
        visible_state = {"messages": [], "last_round_results": None, "my_obligations": []}
        config = GameConfig.baseline_v1_s2(8)
        ctx = self._base_ctx()
        prompt = build_final_reflection_prompt(player, 5, visible_state, config, ctx)
        assert "清算結果" in prompt
        assert "30万円" in prompt or "300000" in prompt or "30万" in prompt

    def test_prompt_mentions_final_round_survival_condition(self):
        player = make_player("P01", cash=1_000_000, debt=0)
        visible_state = {"messages": [], "last_round_results": None, "my_obligations": []}
        config = GameConfig.baseline_v1_s2(8)
        ctx = self._base_ctx(reason="condition_not_met", is_final_round=True)
        prompt = build_final_reflection_prompt(player, 12, visible_state, config, ctx)
        assert "生還条件" in prompt

    def test_prompt_includes_memory_when_present(self):
        player = make_player("P01", cash=0, debt=500_000)
        visible_state = {"messages": [], "last_round_results": None, "my_obligations": []}
        config = GameConfig.baseline_v1_s2(8)
        ctx = self._base_ctx()
        prompt = build_final_reflection_prompt(
            player, 5, visible_state, config, ctx, memory="R4でP03に裏切られた",
        )
        assert "R4でP03に裏切られた" in prompt

    def test_prompt_requests_json_with_three_keys(self):
        player = make_player("P01", cash=0, debt=500_000)
        visible_state = {"messages": [], "last_round_results": None, "my_obligations": []}
        config = GameConfig.baseline_v1_s2(8)
        ctx = self._base_ctx()
        prompt = build_final_reflection_prompt(player, 5, visible_state, config, ctx)
        assert '"emotion"' in prompt
        assert '"defeat_cause"' in prompt
        assert '"comment"' in prompt

    def test_prompt_respects_final_reflection_max_chars(self):
        player = make_player("P01", cash=0, debt=500_000)
        visible_state = {"messages": [], "last_round_results": None, "my_obligations": []}
        config = GameConfig.baseline_v1_s2(8).model_copy(
            update={"final_reflection_max_chars": 777}
        )
        ctx = self._base_ctx()
        prompt = build_final_reflection_prompt(player, 5, visible_state, config, ctx)
        assert "777字" in prompt

    def test_prompt_targets_800_1500_and_never_mentions_3000(self):
        """プロンプトは800〜1500字程度の簡潔さを促すが、
        final_reflection_max_tokens=3000（ハード上限）という数値自体は
        一切露出させない（provider側truncation対策の実装詳細を隠す）。"""
        player = make_player("P01", cash=0, debt=500_000)
        visible_state = {"messages": [], "last_round_results": None, "my_obligations": []}
        config = GameConfig.baseline_v1_s2(8).model_copy(
            update={"final_reflection_max_chars": 777}
        )
        ctx = self._base_ctx()
        prompt = build_final_reflection_prompt(player, 5, visible_state, config, ctx)
        assert "800" in prompt
        assert "1500" in prompt
        assert "777字" in prompt
        assert "3000" not in prompt


class TestParseFinalReflection:
    """parse_final_reflection() のフォールバック挙動"""

    def test_empty_text(self):
        result = parse_final_reflection("")
        assert result["status"] == "empty"
        assert result["comment"] == ""

    def test_plain_json(self):
        text = '{"emotion": "哀", "defeat_cause": "資金不足", "comment": "悔しい"}'
        result = parse_final_reflection(text)
        assert result["status"] == "ok"
        assert result["comment"] == "悔しい"
        assert result["emotion"] == "哀"
        assert result["defeat_cause"] == "資金不足"

    def test_fenced_json(self):
        text = '```json\n{"emotion": "怒", "defeat_cause": "裏切り", "comment": "P03を許さない"}\n```'
        result = parse_final_reflection(text)
        assert result["status"] == "ok"
        assert result["comment"] == "P03を許さない"

    def test_raw_newline_salvage(self):
        text = '```json\n{"comment": "最後まで\n頑張った"}\n```'
        result = parse_final_reflection(text)
        assert result["status"] == "ok_recovered"
        assert result["comment"] == "最後まで\n頑張った"

    def test_missing_comment_key_but_other_text_assembled(self):
        text = '{"emotion": "哀", "defeat_cause": "資金不足", "final_word": "さようなら"}'
        result = parse_final_reflection(text)
        assert result["status"] == "ok_assembled"
        assert "さようなら" in result["comment"]

    def test_missing_comment_key_no_fallback_text(self):
        text = '{"emotion": "哀"}'
        result = parse_final_reflection(text)
        assert result["status"] == "rejected_no_comment"
        assert result["comment"] == ""

    def test_plain_prose_accepted_as_plaintext(self):
        text = "悔しい負けだった。P03を信用したのが間違いだった。"
        result = parse_final_reflection(text)
        assert result["status"] == "ok_plaintext"
        assert result["comment"] == text

    def test_invalid_emotion_normalized_to_none(self):
        text = '{"emotion": "excited", "defeat_cause": "運が悪かった", "comment": "また会おう"}'
        result = parse_final_reflection(text)
        assert result["emotion"] is None
        assert result["comment"] == "また会おう"

    def test_over_limit_truncated_at_safe_boundary(self):
        long_comment = "これは長い最終コメントです。" * 200
        text = json.dumps({"emotion": "楽", "defeat_cause": "運", "comment": long_comment})
        result = parse_final_reflection(text, max_chars=100)
        assert result["truncated"] is True
        assert len(result["comment"]) <= 100
        assert result["chars"] == len(result["comment"])

    def test_salvaged_field_empty_on_clean_json(self):
        text = '{"emotion": "哀", "defeat_cause": "資金不足", "comment": "悔しい"}'
        result = parse_final_reflection(text)
        assert result["status"] == "ok"
        assert result["salvaged"] == []

    # --- 2026-08-22 実API疎通試験のC1/C6を踏まえた回帰テスト ---
    # (doc/trials/final_reflection_smoke_2026-08-22.md)

    def test_c1_truncated_json_salvages_all_three_keys(self):
        """C1(L1)実測: finish_reason=max_tokensでcomment本文の途中がそのまま
        物理的に切断される（閉じクオート/波括弧が無い）。emotion/defeat_causeは
        先頭側にあり閉じているため、切断されても失われてはならない。"""
        text = (
            '```json\n{"emotion": "怒", "defeat_cause": "初期借入額の過小判断", '
            '"comment": "本当に悔しい。もっと慎重に判断すべきだった。特に序盤の'
        )
        result = parse_final_reflection(text)
        assert result["status"] == "ok_recovered"
        assert result["emotion"] == "怒"
        assert result["defeat_cause"] == "初期借入額の過小判断"
        assert result["comment"]
        assert "comment" not in result["comment"]
        assert "{" not in result["comment"]
        assert set(result["salvaged"]) == {"emotion", "defeat_cause", "comment"}

    def test_c6_raw_newline_in_comment_salvages_all_three_keys(self):
        """C6(L6)実測: finish_reason=stopだがcomment内の生改行でjson.loadsが
        失敗する。emotion/defeat_causeはcommentより前にあり閉じているため、
        raw改行1個のせいで丸ごと失われてはならない。"""
        text = (
            '```json\n{"emotion": "哀", "defeat_cause": "初動の借入額設定ミス", '
            '"comment": "本当に悔しい\n最後まで頑張った"}\n```'
        )
        result = parse_final_reflection(text)
        assert result["status"] == "ok_recovered"
        assert result["emotion"] == "哀"
        assert result["defeat_cause"] == "初動の借入額設定ミス"
        assert result["comment"] == "本当に悔しい\n最後まで頑張った"
        assert set(result["salvaged"]) == {"emotion", "defeat_cause", "comment"}

    def test_bounded_recovery_does_not_swallow_following_keys(self):
        """comment が最後のキーでない場合でも、境界サルベージは comment の
        閉じクオートで正しく止まり、後続の emotion キーを飲み込まない
        （貪欲マッチ_recover_string_field()ではこれができない = 新ヘルパーの理由）。"""
        text = '{"comment": "本文だよ\nまだ続く", "emotion": "哀"}'
        result = parse_final_reflection(text)
        assert result["status"] == "ok_recovered"
        assert result["comment"] == "本文だよ\nまだ続く"
        assert result["emotion"] == "哀"
        assert "emotion" not in result["comment"]

    def test_truncated_before_comment_key_uses_defeat_cause(self):
        """comment キー自体が出現する前に応答が打ち切られた場合、comment は
        復旧不能でも defeat_cause が取れていれば空文字にはしない。"""
        text = '```json\n{"emotion": "焦", "defeat_cause": "資金繰りの失敗"'
        result = parse_final_reflection(text)
        assert result["status"] == "ok_assembled"
        assert result["emotion"] == "焦"
        assert result["defeat_cause"] == "資金繰りの失敗"
        assert result["comment"] == "資金繰りの失敗"

    def test_unsalvageable_wrapper_never_leaks_braces(self):
        """個別サルベージが全滅した完全復旧不能ラッパーでも、生のJSON構造
        （{ や "comment" 等のキー名）そのものをcommentへ流出させない。
        既存のok_plaintext（genuineな素の散文）とは明確に別ステータスにする。"""
        text = '{"emo'
        result = parse_final_reflection(text)
        assert result["status"] == "ok_plaintext_wrapper"
        assert "{" not in result["comment"]
        assert "}" not in result["comment"]
        assert "comment" not in result["comment"]
        assert "```" not in result["comment"]

    def test_fenced_valid_json_still_ok(self):
        """回帰ガード: 正常な閉じフェンスJSONは従来どおりstrict経路でstatus=okになる
        （salvageティアに一切触れない）。"""
        text = '```json\n{"emotion": "楽", "defeat_cause": "油断", "comment": "また挑戦したい"}\n```'
        result = parse_final_reflection(text)
        assert result["status"] == "ok"
        assert result["salvaged"] == []

    def test_plain_prose_has_no_emotion_or_cause(self):
        """回帰ガード: JSON/ラッパーの痕跡が無い素の散文は、emotion/defeat_causeが
        Noneのままok_plaintextで採用される（ok_plaintext_wrapperとは別経路）。"""
        text = "悔しい負けだった。P03を信用したのが間違いだった。"
        result = parse_final_reflection(text)
        assert result["status"] == "ok_plaintext"
        assert result["emotion"] is None
        assert result["defeat_cause"] is None
        assert result["salvaged"] == []


class TestFinalReflectionMaxTokensConfig:
    """FINAL_REFLECTION出力上限のconfig既定値（3000tokens化）"""

    def test_config_default_max_tokens_is_3000(self):
        config = GameConfig.baseline_v1_s2(num_players=12)
        assert config.final_reflection_max_tokens == 3000


class TestLLMAgentFinalReflect:
    """LLMAgent.final_reflect() の動作（モック使用）"""

    def _make_agent(self, max_tokens=1000):
        from llm.llm_agent import LLMAgent
        from llm.models import ModelInfo

        model_info = ModelInfo(
            model_id="test-model", provider="Test", name="Test Model",
            adapter_type="openai_compat", input_price=0.0, output_price=0.0,
            env_key="TEST_API_KEY", base_url="http://localhost",
            timeout_seconds=10, max_tokens=4000, extra_params=None,
        )
        logger = MagicMock()
        logger.total_cost = 0.0
        config = GameConfig.baseline_v1_s2(8).model_copy(
            update={"final_reflection_max_tokens": max_tokens}
        )
        agent = LLMAgent("P01", model_info, MagicMock(), logger, config)
        agent._config = config
        agent._system_prompt = "test"
        return agent

    def _make_player(self):
        return make_player("P01", cash=0, debt=500_000)

    def _visible_state(self):
        return {"messages": [], "last_round_results": None, "my_obligations": []}

    def _ctx(self):
        return {
            "player_id": "P01", "round": 3, "reason": "bankruptcy",
            "reason_label": "破産による脱落",
            "phase": "commit", "event_type": "BANKRUPTCY",
            "liquidation": {"debt_repaid": 0, "bad_debt": 0,
                             "cash_confiscated": 0, "cards_destroyed": 0},
            "is_final_round": False, "survival_cash": 3_000_000,
        }

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_final_reflect_returns_parsed_result(self, mock_call):
        mock_call.return_value = (
            '{"emotion": "哀", "defeat_cause": "資金不足", "comment": "悔しい"}', {}
        )
        agent = self._make_agent()
        result = agent.final_reflect(self._make_player(), 3, self._visible_state(), self._ctx())
        assert result["status"] == "ok"
        assert result["comment"] == "悔しい"
        assert agent.final_reflection == result

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_final_reflect_passes_max_tokens_override(self, mock_call):
        mock_call.return_value = ('{"comment": "ok"}', {})
        agent = self._make_agent(max_tokens=1000)
        agent.final_reflect(self._make_player(), 3, self._visible_state(), self._ctx())
        _, kwargs = mock_call.call_args
        assert kwargs.get("max_tokens_override") == 1000

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_final_reflect_never_touches_memory(self, mock_call):
        mock_call.return_value = (
            '{"emotion": "哀", "defeat_cause": "資金不足", "comment": "悔しい"}', {}
        )
        agent = self._make_agent()
        agent._memory = "既存メモ"
        agent.memory_history = [{"round": 2, "memory": "既存メモ"}]
        agent.final_reflect(self._make_player(), 3, self._visible_state(), self._ctx())
        assert agent._memory == "既存メモ"
        assert agent.memory_history == [{"round": 2, "memory": "既存メモ"}]

    def test_final_reflect_skipped_on_cost_exceeded(self):
        agent = self._make_agent()
        agent._cost_exceeded = True
        result = agent.final_reflect(self._make_player(), 3, self._visible_state(), self._ctx())
        assert result["status"] == "skipped"
        assert result["comment"] == ""

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_final_reflect_budget_blocked(self, mock_call):
        from llm.game_cost_budget import BudgetBlockedError
        mock_call.side_effect = BudgetBlockedError("blocked")
        agent = self._make_agent()
        result = agent.final_reflect(self._make_player(), 3, self._visible_state(), self._ctx())
        assert result["status"] == "budget_blocked"
        assert result["comment"] == ""

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_final_reflect_empty_response(self, mock_call):
        mock_call.return_value = ("", {})
        agent = self._make_agent()
        result = agent.final_reflect(self._make_player(), 3, self._visible_state(), self._ctx())
        assert result["status"] == "empty"
        assert result["comment"] == ""

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_final_reflect_logs_phase_final_reflection(self, mock_call):
        mock_call.return_value = (
            '{"emotion": "哀", "defeat_cause": "資金不足", "comment": "悔しい"}', {}
        )
        agent = self._make_agent()
        agent.final_reflect(self._make_player(), 3, self._visible_state(), self._ctx())
        # _call_llmの第1引数（phase）に"final_reflection"が渡されていること
        args, _ = mock_call.call_args
        assert args[0] == "final_reflection"

    @patch.object(__import__("llm.llm_agent", fromlist=["LLMAgent"]).LLMAgent, "_call_llm")
    def test_llm_log_entry_gets_final_reflection_fields(self, mock_call):
        mock_call.return_value = (
            '{"emotion": "哀", "defeat_cause": "資金不足", "comment": "悔しい"}', {}
        )
        agent = self._make_agent()
        agent.llm_logger._entries = [{}]
        agent.final_reflect(self._make_player(), 3, self._visible_state(), self._ctx())
        entry = agent.llm_logger._entries[-1]
        assert entry["final_reflection_status"] == "ok"
        assert entry["emotion"] == "哀"


REAL_LOG_PATH = (
    Path(__file__).resolve().parent.parent
    / "logs" / "llm" / "trial_C_l12_r12_20260822" / "game01_events.jsonl"
)


class TestR12OfflineCrossCheck:
    """
    実ログ（読み取り専用）に対するオフラインクロスチェック。

    ログファイルには一切書き込まない。GAME_ENDイベントのdata.eliminated
    （各要素にplayer_id/reason/round）から、Game._phase_final_reflection()と
    同じ導出ロジック（state由来、ラウンドごとに一意）で対象が重複なく
    導出できることを確認する。既知の7人の脱落者が1回ずつ、5人の生存者
    （data.survivors）が0回として導出されることを検証する。
    """

    def test_seven_eliminated_once_five_survivors_zero(self):
        if not REAL_LOG_PATH.exists():
            import pytest
            pytest.skip(f"real trial log not found: {REAL_LOG_PATH}")

        game_end = None
        with open(REAL_LOG_PATH, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                event = json.loads(line)
                if event.get("event_type") == "GAME_END":
                    game_end = event
        assert game_end is not None, "GAME_END event not found in real log"

        eliminated = game_end["data"]["eliminated"]
        survivors = game_end["data"]["survivors"]

        # 実装の導出ロジック（is_alive=False かつ elimination_round==round のときだけ
        # 1回拾う）を、GAME_ENDの確定済みeliminatedリストに対して模倣する。
        # eliminatedリストの各要素は既に「1脱落=1エントリ」に正規化されているため、
        # ここではその不変条件（重複が無いこと）そのものを検証する。
        seen: set[str] = set()
        derived_map: dict[str, int] = {}
        for entry in eliminated:
            pid = entry["player_id"]
            assert pid not in seen, f"{pid} appears more than once in eliminated list"
            seen.add(pid)
            derived_map[pid] = entry["round"]

        expected_eliminated = {
            "P11": 3, "P02": 5, "P09": 6, "P06": 9, "P01": 11, "P07": 11, "P12": 12,
        }
        expected_survivors = {"P03", "P04", "P05", "P08", "P10"}

        for pid, rnd in expected_eliminated.items():
            assert pid in derived_map, f"{pid} not derived as eliminated"
            assert derived_map[pid] == rnd, (
                f"{pid} derived at round {derived_map[pid]}, expected {rnd}"
            )
        assert len(derived_map) == 7

        survivor_ids = {p["player_id"] for p in survivors}
        assert survivor_ids == expected_survivors
        for pid in expected_survivors:
            assert pid not in derived_map, f"survivor {pid} incorrectly derived"


class RecordingBothVariantsAgent(StubAgent):
    """final_reflect() / completion_reflect() 両方の呼び出しを記録するテスト用エージェント"""

    def __init__(self):
        self.final_reflect_calls: list[tuple[str, int, dict]] = []
        self.completion_reflect_calls: list[tuple[str, int, dict]] = []

    def final_reflect(self, player_state, round_num, visible_state, elimination_context):
        self.final_reflect_calls.append(
            (player_state.player_id, round_num, elimination_context)
        )
        return {
            "status": "ok", "comment": "負けた", "emotion": "哀",
            "defeat_cause": "資金不足", "chars": 3, "truncated": False,
        }

    def completion_reflect(self, player_state, round_num, visible_state, completion_context):
        self.completion_reflect_calls.append(
            (player_state.player_id, round_num, completion_context)
        )
        return {
            "status": "ok", "comment": "生き残った", "emotion": "楽",
            "defeat_cause": "慎重な立ち回り", "chars": 5, "truncated": False,
        }


class TestFinalReflectionFiringMatrix:
    """FINAL_REFLECTIONのelimination/completion variant発火マトリクス"""

    def test_r3_elimination_fires_once_then_never(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 12})
        game, agents = _make_game(config, agent_factory=RecordingBothVariantsAgent)
        _settle_round(game, 3)
        _eliminate(game, "P02", 3)
        game._phase_final_reflection(3)
        for r in range(4, 13):
            game._phase_final_reflection(r)

        assert len(agents["P02"].final_reflect_calls) == 1
        assert agents["P02"].completion_reflect_calls == []

    def test_r12_condition_not_met_gets_elimination_variant_once(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 12})
        game, agents = _make_game(config, agent_factory=RecordingBothVariantsAgent)
        _settle_round(game, 12)
        _eliminate(game, "P02", 12, reason="condition_not_met")
        game._phase_final_reflection(12)

        assert len(agents["P02"].final_reflect_calls) == 1
        assert agents["P02"].completion_reflect_calls == []
        events = [
            e for e in game.logger.events
            if e.event_type == "FINAL_REFLECTION" and e.data["player_id"] == "P02"
        ]
        assert len(events) == 1
        assert events[0].data["variant"] == "elimination"

    def test_r12_survivor_gets_completion_variant_once(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 12})
        game, agents = _make_game(config, agent_factory=RecordingBothVariantsAgent)
        # 実際の資金蓄積を経由せず、生還条件（借金0・現金≧survival_cash）を
        # state直接操作で満たす（_eliminate()と同じ先例。_phase_finance(12)の
        # 実処理を経由せず、_phase_final_reflection(12)単体のpass2ロジックを検証する）
        for pid, p in list(game.players.items()):
            game.players[pid] = p.model_copy(update={
                "cash": config.survival_cash, "debt_balance": 0,
            })
        game._phase_final_reflection(12)

        for agent in agents.values():
            assert len(agent.completion_reflect_calls) == 1
            assert agent.final_reflect_calls == []
        events = [e for e in game.logger.events if e.event_type == "FINAL_REFLECTION"]
        assert len(events) == 8
        assert all(e.data["variant"] == "completion" for e in events)

    def test_game_wide_total_is_exactly_twelve(self):
        """7脱落＋5生還（trial_C_l12_r12_20260822の形）でFINAL_REFLECTIONが12件、
        pid集合が全ロスターと一致すること"""
        config = GameConfig.baseline_v1_s2(12).model_copy(update={"num_rounds": 12})
        game, agents = _make_game(
            config, agent_factory=RecordingBothVariantsAgent, num_players=12,
        )
        _settle_round(game, 1)
        eliminated_map = {
            "P11": 3, "P02": 5, "P09": 6, "P06": 9, "P01": 11, "P07": 11, "P12": 12,
        }
        for pid, r in eliminated_map.items():
            _eliminate(game, pid, r)
        for r in range(1, 13):
            game._phase_final_reflection(r)

        events = [e for e in game.logger.events if e.event_type == "FINAL_REFLECTION"]
        assert len(events) == 12
        assert {e.data["player_id"] for e in events} == set(agents)
        eliminated_events = {
            e.data["player_id"] for e in events if e.data["variant"] == "elimination"
        }
        completion_events = {
            e.data["player_id"] for e in events if e.data["variant"] == "completion"
        }
        assert eliminated_events == set(eliminated_map)
        assert completion_events == set(agents) - set(eliminated_map)

    def test_duplicate_elimination_events_do_not_double_fire(self):
        config = GameConfig.baseline_v1_s2(8).model_copy(update={"num_rounds": 12})
        game, agents = _make_game(config, agent_factory=RecordingBothVariantsAgent)
        _settle_round(game, 3)
        _eliminate(game, "P02", 3, "contract_violation")
        game.logger.log("ELIMINATION", 3, "settlement", step=7, data={
            "player_id": "P02", "reason": "contract_violation",
        })
        game._phase_final_reflection(3)
        game.logger.log("ELIMINATION", 3, "settlement", step=7, data={
            "player_id": "P02", "reason": "contract_violation",
        })
        game._phase_final_reflection(3)  # 再invoke

        assert len(agents["P02"].final_reflect_calls) == 1

    def test_completion_prompt_never_says_eliminated(self):
        config = GameConfig.baseline_v1_s2(4).model_copy(update={"num_rounds": 3})
        agents = {f"P{i+1:02d}": StubAgent() for i in range(4)}
        game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
        game._setup()
        ctx = game._build_completion_context("P01", 3)
        visible_state = game._build_visible_state(3, for_player_id="P01")

        prompt = build_completion_reflection_prompt(
            game.players["P01"], 3, visible_state, config, ctx,
        )

        assert "脱落" not in prompt

    def test_stop_after_round_produces_zero_completion_events(self):
        config = GameConfig.baseline_v1_s2(1).model_copy(update={"num_rounds": 12})
        agent = RecordingBothVariantsAgent()
        logger = EventLogger(fixed_timestamp="t")
        result = Game(config, {"P01": agent}, logger=logger, stop_after_round=3).run()

        assert result.round_count == 3
        assert agent.completion_reflect_calls == []
        events = [e for e in logger.events if e.event_type == "FINAL_REFLECTION"]
        assert all(e.data.get("variant") != "completion" for e in events)

    def test_state_and_memory_unmutated(self):
        config = GameConfig.baseline_v1_s2(4).model_copy(update={"num_rounds": 3})
        game, agents = _make_game(
            config, agent_factory=RecordingBothVariantsAgent, num_players=4,
        )
        _settle_round(game, 3)
        players_before = {pid: p.model_copy() for pid, p in game.players.items()}
        contracts_before = list(game.contracts)

        game._phase_final_reflection(3)

        for pid, p in game.players.items():
            assert p == players_before[pid]
        assert game.contracts == contracts_before
