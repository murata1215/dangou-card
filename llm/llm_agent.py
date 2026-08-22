"""
LLMAgent — PlayerAgentの実装

LLM APIを使ってゲームに参加するエージェント。
プロンプト構築→APIコール→レスポンス解析→Action変換を行う。
コスト上限ガード・リトライ・ログ記録を管理する。

修正1: commit時に交渉ログ・strategyメモを含有
修正4: 交渉空回り削減（新規メッセージなしで自動pass）
"""

import logging
import time
from typing import Any

from engine.negotiation import PlayerAgent
from engine.models import (
    Action, PassAction, MarketCommitAction, PlayerState, Market,
)
from engine.config import GameConfig
from llm.adapters import AnthropicAdapter, OpenAICompatAdapter, GeminiStub, AdapterError, _classify_error
from llm.models import ModelInfo
from llm.costing import usage_cost, worst_case_cost
from llm.game_cost_budget import BudgetBlockedError, GameCostBudget, Reservation
from llm.constants import (
    DEFAULT_TEMPERATURE, DEFAULT_MAX_TOKENS, MAX_RETRIES,
    AUTO_PASS_ON_NO_NEWS,
)
from llm.prompt_builder import (
    build_system_prompt, build_loan_prompt,
    build_negotiation_prompt, build_commit_prompt,
    build_double_up_prompt, build_reflection_prompt,
    build_final_reflection_prompt,
)
from llm.response_parser import (
    parse_response, extract_json, ParseError, make_correction_message,
    LENGTH_TRUNCATION_HINT, extract_memory_with_status, normalize_memory_with_truncation,
    parse_final_reflection,
)
from llm.llm_logger import LLMLogger

logger = logging.getLogger(__name__)


class LLMAgent(PlayerAgent):
    """
    LLM APIを使用するプレイヤーエージェント
    """

    def __init__(
        self,
        player_id: str,
        model_info: ModelInfo,
        adapter: AnthropicAdapter | OpenAICompatAdapter | GeminiStub,
        llm_logger: LLMLogger,
        config: GameConfig | None = None,
    ) -> None:
        self.player_id = player_id
        self.model_info = model_info
        self.adapter = adapter
        self.llm_logger = llm_logger
        self._config: GameConfig | None = config
        self._system_prompt: str = ""
        self._cost_exceeded: bool = False
        self._game_cost_budget: GameCostBudget | None = None
        # strategyメモの記録（観戦・分析用）
        self.strategy_history: list[dict[str, Any]] = []
        # AUTO COMMIT発生回数の記録
        self.auto_commit_count: int = 0
        self.total_calls: int = 0
        self.valid_json_count: int = 0
        # Phase 3集計で、JSON parse失敗後に実際に送った修正prompt数を監査する。
        # loan選択には修正retryが無いため、negotiation / commitだけを分離して数える。
        self.negotiation_correction_count: int = 0
        self.commit_correction_count: int = 0
        # 修正4: 前回のメッセージ数を追跡（空回り検出用）
        self._last_message_count: int = 0
        # 修正1: 当該ラウンドの交渉メッセージを保持
        self._current_round_messages: list[dict[str, Any]] = []
        self._current_round: int = 0
        # 引き継ぎメモリ（Handover Memory）: 前ラウンドから引き継ぐ自由記述メモ1枚
        # （常に最新の1枚のみ保持。累積しない）。visible_stateには絶対に入れない。
        self._memory: str = ""
        # memoryの全履歴（観戦・分析用。エージェントインスタンス内に閉じる）
        self.memory_history: list[dict[str, Any]] = []
        # Reflection抽出のfallback（旧Memory保持）が連続何回起きたか。
        # 2回以上の連続fallbackは、そのモデルがreflection形式を守れていない
        # シグナルとして可観測にする（memory_fix 2026-08-22）
        self.memory_fallback_streak: int = 0
        self.memory_fallback_count: int = 0
        # 脱落時の最終コメント（FINAL_REFLECTION）。観戦/分析用に保持するのみで、
        # 次ラウンドのプロンプトには一切注入されない（脱落者なので次ラウンドが無い）。
        self.final_reflection: dict[str, Any] | None = None

    def _update_last_log_emotion(self, strategy: dict[str, Any]) -> None:
        """strategyからemotion・reasoningを抽出し、llm_loggerの最新エントリに後付けする"""
        emotion = strategy.get("emotion", "平静") if isinstance(strategy, dict) else "平静"
        reasoning = strategy.get("_reasoning") if isinstance(strategy, dict) else None
        if self.llm_logger._entries:
            self.llm_logger._entries[-1]["emotion"] = emotion
            if reasoning is not None:
                self.llm_logger._entries[-1]["reasoning"] = reasoning

    def _update_last_log_memory(
        self,
        *,
        chars_raw: int,
        chars_saved: int,
        parse_status: str,
        truncated: bool,
        fallback_reason: str | None,
    ) -> None:
        """
        reflect()の抽出/正規化結果をllm_loggerの最新エントリに後付けする
        （_update_last_log_emotionと同じ post-hoc mutation パターン。
        llm_logger.save()が試合終了時にファイルへ全書き直しするため、
        逐次書き込み行が古くても最終的にはこの値が反映される）

        秘密情報・APIキーはここに含めない。プロンプト/Memory本文は
        response_text に既に全文があるため重複させない。
        """
        if self.llm_logger._entries:
            entry = self.llm_logger._entries[-1]
            entry["memory_chars_raw"] = chars_raw
            entry["memory_chars_saved"] = chars_saved
            entry["memory_parse_status"] = parse_status
            entry["memory_truncated"] = truncated
            entry["fallback_reason"] = fallback_reason
            entry["memory_fallback_streak"] = self.memory_fallback_streak

    def _update_last_log_final_reflection(self, result: dict[str, Any]) -> None:
        """
        final_reflect()の抽出結果をllm_loggerの最新エントリに後付けする
        （_update_last_log_memoryと同じ post-hoc mutation パターン）。

        秘密情報・APIキーはここに含めない。comment本文は response_text に
        既に全文があるため重複させない。
        """
        if self.llm_logger._entries:
            entry = self.llm_logger._entries[-1]
            entry["final_reflection_status"] = result.get("status")
            entry["final_reflection_chars"] = result.get("chars", 0)
            entry["final_reflection_truncated"] = result.get("truncated", False)
            entry["emotion"] = result.get("emotion")
            entry["final_reflection_salvaged"] = result.get("salvaged", [])

    def set_game_cost_budget(self, budget: GameCostBudget) -> None:
        """Gameから明示注入される本戦専用の共有budget。"""
        self._game_cost_budget = budget

    def _call_llm(
        self,
        phase: str,
        round_num: int,
        user_prompt: str,
        turn: int | None = None,
        retry_count: int = 0,
        max_tokens_override: int | None = None,
    ) -> tuple[str, dict[str, int]]:
        """
        LLM APIを呼び出し、レスポンスとusageを返す

        本戦budgetの事前予約がcapを超える場合はBudgetBlockedErrorを送出する。
        呼出元はこれを通常のAPI/JSON失敗と区別して即fallbackへ移行する。
        修正7: AdapterError発生時にerror_typeを分類してログに記録

        Args:
            max_tokens_override: このcallだけmodel_info.max_tokensより優先する
                出力トークン上限（例: final_reflection）。既定Noneで既存挙動は不変。
        """
        effective_max_tokens = max_tokens_override or self.model_info.max_tokens or DEFAULT_MAX_TOKENS
        reservation: Reservation | None = None
        if self._game_cost_budget is not None:
            reserved = worst_case_cost(self.model_info, self._system_prompt, user_prompt, effective_max_tokens)
            try:
                reservation = self._game_cost_budget.reserve(
                    self.player_id, reserved, round_num=round_num, phase=phase, turn=turn,
                )
            except BudgetBlockedError as e:
                self._cost_exceeded = True
                self.llm_logger.log_budget_block(
                    player_id=self.player_id, model_id=self.model_info.model_id,
                    phase=phase, round_num=round_num, turn=turn,
                    system_prompt=self._system_prompt, user_prompt=user_prompt,
                    reason=e.reason, estimated_cost_usd=reserved,
                    player_spent_usd=self._game_cost_budget.player_spent_usd.get(self.player_id, 0.0),
                    game_spent_usd=self._game_cost_budget.game_spent_usd,
                    per_player_cap_usd=self._game_cost_budget.per_player_cap_usd,
                    game_cap_usd=self._game_cost_budget.game_cap_usd,
                )
                raise
        start = time.time()
        error_msg = None
        error_type = None
        try:
            text, usage = self.adapter.complete(
                system=self._system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
                max_tokens=effective_max_tokens,
                temperature=DEFAULT_TEMPERATURE,
            )
        except AdapterError as e:
            text = ""
            usage = {"input_tokens": 0, "output_tokens": 0}
            error_msg = str(e)[:200]
            error_type = _classify_error(e)
        except Exception as e:
            text = ""
            usage = {"input_tokens": 0, "output_tokens": 0}
            error_msg = str(e)[:200]
            error_type = "other"

        elapsed = (time.time() - start) * 1000
        cost = usage_cost(self.model_info, usage)
        if reservation is not None:
            if error_msg is None:
                self._game_cost_budget.settle(reservation, cost)
            else:
                self._game_cost_budget.release(reservation)

        finish_reason = usage.get("finish_reason")
        self.llm_logger.log_call(
            player_id=self.player_id,
            model_id=self.model_info.model_id,
            phase=phase,
            round_num=round_num,
            turn=turn,
            system_prompt=self._system_prompt,
            user_prompt=user_prompt,
            response_text=text,
            usage=usage,
            cost=cost,
            elapsed_ms=elapsed,
            error=error_msg,
            error_type=error_type,
            finish_reason=finish_reason,
            unit_price_input=self.model_info.input_price,
            unit_price_output=self.model_info.output_price,
            retry_count=retry_count,
        )

        self.total_calls += 1

        return text, usage

    def choose_loan(self, config: GameConfig) -> int:
        """ゲーム開始前の借入額をLLMに選択させる"""
        self._config = config
        self._system_prompt = build_system_prompt(self.player_id, config)

        user_prompt = build_loan_prompt(config)
        try:
            text, _ = self._call_llm("loan_choice", 0, user_prompt)
        except BudgetBlockedError:
            return 3_000_000

        data = extract_json(text)
        if data:
            action_data = data.get("action", data)
            amount = action_data.get("amount", 0)
            if isinstance(amount, (int, float)) and config.loan_min <= amount <= config.loan_max:
                return int(amount)

        return 3_000_000

    def negotiate(
        self,
        player_state: PlayerState,
        round_num: int,
        turn: int,
        visible_state: dict,
    ) -> Action:
        """
        Negotiationフェイズで1アクションをLLMに選択させる

        修正4: 新規メッセージがなくturn>=2なら自動pass
        """
        if self._cost_exceeded or self._config is None:
            return PassAction(player_id=self.player_id)

        # ラウンドが変わったら交渉メッセージをリセット
        if round_num != self._current_round:
            self._current_round = round_num
            self._current_round_messages = []
            self._last_message_count = 0

        # 修正4: 空回り削減 — 新規メッセージがなくturn>=2なら自動pass
        if AUTO_PASS_ON_NO_NEWS and turn >= 2:
            current_msg_count = len(visible_state.get("messages", []))
            if current_msg_count <= self._last_message_count:
                return PassAction(player_id=self.player_id)

        # メッセージ数を更新
        self._last_message_count = len(visible_state.get("messages", []))

        # 修正1: 交渉メッセージを蓄積（commit時に渡すため）
        for msg in visible_state.get("messages", []):
            if msg not in self._current_round_messages:
                self._current_round_messages.append(msg)

        user_prompt = build_negotiation_prompt(
            player_state, round_num, turn, visible_state, self._config,
            memory=self._memory or None,
        )

        # リトライループ
        messages_so_far = user_prompt
        for retry in range(MAX_RETRIES + 1):
            try:
                text, usage = self._call_llm(
                    "negotiation", round_num, messages_so_far, turn=turn,
                    retry_count=retry,
                )
            except BudgetBlockedError:
                return PassAction(player_id=self.player_id)

            if not text:
                return PassAction(player_id=self.player_id)

            try:
                strategy, action = parse_response(text, self.player_id, "negotiation")
                self.valid_json_count += 1
                if strategy:
                    self.strategy_history.append({
                        "round": round_num, "turn": turn, "strategy": strategy,
                    })
                    # emotionをログの最新エントリに後付け
                    self._update_last_log_emotion(strategy)
                return action
            except ParseError as e:
                if retry < MAX_RETRIES:
                    self.negotiation_correction_count += 1
                    correction = make_correction_message(e)
                    # finish_reason=length の場合は切断ヒントを追加
                    if usage.get("finish_reason") == "length":
                        correction = LENGTH_TRUNCATION_HINT + "\n" + correction
                    messages_so_far = user_prompt + "\n\n" + correction
                    continue

        return PassAction(player_id=self.player_id)

    def commit(
        self,
        player_state: PlayerState,
        markets: list[Market],
        round_num: int,
        visible_state: dict,
    ) -> MarketCommitAction:
        """
        Commitフェイズで市場+カードをLLMに選択させる

        修正1: 交渉ログと直前strategyメモを含有
        """
        if self._cost_exceeded or self._config is None:
            self.auto_commit_count += 1
            raise ValueError("Cost exceeded or config not set")

        # 修正1: 直前のstrategyメモを取得
        last_strategy = None
        for sh in reversed(self.strategy_history):
            if sh.get("round") == round_num:
                last_strategy = sh.get("strategy")
                break

        user_prompt = build_commit_prompt(
            player_state, markets, round_num, visible_state, self._config,
            negotiation_messages=self._current_round_messages,
            last_strategy=last_strategy,
            memory=self._memory or None,
        )

        original_prompt = user_prompt
        for retry in range(MAX_RETRIES + 1):
            try:
                text, usage = self._call_llm("commit", round_num, user_prompt, retry_count=retry)
            except BudgetBlockedError:
                self.auto_commit_count += 1
                raise

            if not text:
                self.auto_commit_count += 1
                raise ValueError("Empty response from LLM")

            try:
                strategy, action = parse_response(text, self.player_id, "commit")
                self.valid_json_count += 1
                if strategy:
                    self.strategy_history.append({
                        "round": round_num, "turn": None, "strategy": strategy,
                    })
                    self._update_last_log_emotion(strategy)
                if isinstance(action, MarketCommitAction):
                    return action
                raise ParseError(
                    "commitフェイズではmarket_commitアクションが必要です",
                    '例: {"strategy": {...}, "action": {"type": "market_commit", "market_id": "M01", "card": "ONE_PAIR"}}'
                )
            except ParseError as e:
                if retry < MAX_RETRIES:
                    self.commit_correction_count += 1
                    correction = make_correction_message(e)
                    if usage.get("finish_reason") == "length":
                        correction = LENGTH_TRUNCATION_HINT + "\n" + correction
                    user_prompt = original_prompt + "\n\n" + correction
                    continue

        self.auto_commit_count += 1
        raise ValueError("Failed to get valid commit from LLM")

    def choose_double_up(
        self,
        player_state: PlayerState,
        prize_won: int,
        round_num: int,
        visible_state: dict,
    ) -> bool:
        """
        S2: 倍掛け選択をLLMに判断させる

        TAKE（False）/ DOUBLE（True）を返す。
        パース失敗・コスト超過時は安全側の TAKE にフォールバック。
        """
        import logging
        logger = logging.getLogger(__name__)

        if self._cost_exceeded or self._config is None:
            return False

        user_prompt = build_double_up_prompt(
            player_state, prize_won, round_num, visible_state, self._config,
        )

        try:
            text, _ = self._call_llm("double_up", round_num, user_prompt)
        except BudgetBlockedError:
            return False
        if not text:
            return False

        data = extract_json(text)
        if data:
            self.valid_json_count += 1
            # strategy があればログに記録
            strategy = data.get("strategy")
            if strategy and isinstance(strategy, dict):
                self.strategy_history.append({
                    "round": round_num, "turn": None, "strategy": strategy,
                })
                self._update_last_log_emotion(strategy)

            choice = str(data.get("choice", "")).upper().strip()
            if choice == "DOUBLE":
                return True
            if choice == "TAKE":
                return False

            # choice が DOUBLE/TAKE 以外の場合
            logger.warning(
                "Invalid double_up choice '%s' from %s R%d, defaulting to TAKE",
                choice, self.player_id, round_num,
            )
            return False

        # JSON パース失敗
        logger.warning(
            "Failed to parse double_up response from %s R%d, defaulting to TAKE",
            self.player_id, round_num,
        )
        return False

    def reflect(
        self,
        player_state: PlayerState,
        round_num: int,
        visible_state: dict,
    ) -> None:
        """
        引き継ぎメモリ（Handover Memory）: ラウンド終了後の振り返り

        次ラウンドへ持ち越す自由記述メモを1枚だけ書かせる。
        フォーマットは強制しない。リトライはしない（失敗しても致命的でないため）。

        失敗時（空応答・API例外）は self._memory を変更しない
        —— 前ラウンドのメモをそのまま維持する。空文字での上書きは絶対にしない。
        """
        if self._cost_exceeded or self._config is None:
            return

        user_prompt = build_reflection_prompt(
            player_state, round_num, visible_state, self._config,
            memory=self._memory or None,
        )

        try:
            text, _ = self._call_llm("reflection", round_num, user_prompt)
        except BudgetBlockedError:
            return
        if not text:
            # API失敗・空応答 — 前ラウンドのメモを維持
            return

        memory, parse_status = extract_memory_with_status(text)

        if not memory:
            # 抽出失敗（strategyのみのJSON等でmemoryキーが無い/文字列でない、
            # または壊れたJSONラッパーを復旧できなかった）、あるいは本当に
            # 空だった場合 — 前ラウンドのメモを維持する（空文字での上書きは
            # 絶対にしない）。fallback発生を可観測にする。
            self.memory_fallback_streak += 1
            self.memory_fallback_count += 1
            if self.memory_fallback_streak >= 2:
                logger.warning(
                    "%s: memory extraction fallback %d rounds in a row "
                    "(status=%s, R%d)",
                    self.player_id, self.memory_fallback_streak, parse_status, round_num,
                )
            self._update_last_log_memory(
                chars_raw=0, chars_saved=len(self._memory), parse_status=parse_status,
                truncated=False, fallback_reason=parse_status,
            )
            return

        self.memory_fallback_streak = 0
        self.valid_json_count += 1
        chars_raw = len(memory)
        saved, truncated = normalize_memory_with_truncation(memory, self._config.memory_max_chars)
        self._memory = saved
        self.memory_history.append({"round": round_num, "memory": self._memory})
        self._update_last_log_memory(
            chars_raw=chars_raw, chars_saved=len(saved), parse_status=parse_status,
            truncated=truncated, fallback_reason=None,
        )

    def final_reflect(
        self,
        player_state: PlayerState,
        round_num: int,
        visible_state: dict,
        elimination_context: dict,
    ) -> dict[str, Any]:
        """
        脱落確定後の最終コメント（FINAL_REFLECTION）

        脱落したプレイヤー本人に、脱落の事実と原因を伝えて1回だけ最後の
        コメントを書かせる。演出/記録専用: ゲーム状態・戻り値シグネチャ・
        生存判定には一切影響しない。呼出はGame側で「1player 1回」が保証される
        （このメソッド自体は何度呼ばれても副作用のない読み取り専用処理）。

        self._memory / memory_history には**絶対に触れない**
        （脱落者なので次ラウンドが無く、通常Handover Memoryを上書きしてはならない）。

        retryは0（reflect()と同じ方針）。1回きりの演出callで失敗しても
        失うものが無く、retryを入れるとコストとbudget blockリスクだけが増える。
        失敗は全てstatus付きdictで返し、例外はGame側で握られる。
        """
        if self._config is None:
            return {"status": "skipped", "reason": "no_config", "comment": "",
                     "emotion": None, "defeat_cause": None, "chars": 0, "truncated": False,
                     "salvaged": []}
        if self._cost_exceeded:
            return {"status": "skipped", "reason": "cost_exceeded", "comment": "",
                     "emotion": None, "defeat_cause": None, "chars": 0, "truncated": False,
                     "salvaged": []}

        user_prompt = build_final_reflection_prompt(
            player_state, round_num, visible_state, self._config,
            elimination_context, memory=self._memory or None,
        )
        try:
            text, _ = self._call_llm(
                "final_reflection", round_num, user_prompt,
                max_tokens_override=self._config.final_reflection_max_tokens,
            )
        except BudgetBlockedError:
            return {"status": "budget_blocked", "comment": "", "emotion": None,
                     "defeat_cause": None, "chars": 0, "truncated": False, "salvaged": []}
        if not text:
            return {"status": "empty", "comment": "", "emotion": None,
                     "defeat_cause": None, "chars": 0, "truncated": False, "salvaged": []}

        result = parse_final_reflection(text, max_chars=self._config.final_reflection_max_chars)
        self.final_reflection = result  # 観戦/分析用に保持（次ラウンドへは渡さない）
        self._update_last_log_final_reflection(result)
        return result
