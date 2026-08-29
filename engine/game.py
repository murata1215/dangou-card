"""
メインゲームループモジュール

5フェイズ進行のオーケストレータ。
Market Open → Negotiation → Commit → Settlement → Finance
の順で12ラウンドを実行する。
"""

import uuid
from typing import Any

from engine.config import GameConfig
from engine.models import (
    PlayerState, Market, MarketCommit, MarketResult, Contract, ContractStatus, Bounty,
    Obligation, ObligationType,
    CardRank, Card, Action, PassAction, TransferAction, RepayAction,
    ContractProposeAction, ContractSignAction, ContractCancelAction, AnonymousBroadcastAction,
    BountyPostAction, BountyCancelAction, MarketCommitAction,
    DoubleUpDeposit,
    CardTradeProposal, CardTradeStatus,
    CardTradeProposeAction, CardTradeAcceptAction, CardTradeRejectAction,
)
from engine.events import EventLogger
from engine.rng import GameRng
from engine.negotiation import PlayerAgent
from engine import player as player_ops
from engine import market as market_ops
from engine import settlement as settlement_ops
from engine import finance as finance_ops
from engine import actions as action_ops
from engine import contracts as contract_ops
from engine import bounty as bounty_ops
from engine import autocommit as autocommit_ops
from engine import elimination as elim_ops
from engine.cards import find_card_by_rank


class GameResult:
    """
    ゲーム終了時の結果

    生還者・脱落者・最終順位・経済統計を記録する。
    """

    def __init__(
        self,
        players: dict[str, PlayerState],
        round_count: int,
        round_snapshots: list[dict] | None = None,
        double_up_deposits: list[DoubleUpDeposit] | None = None,
    ):
        self.players = players
        self.round_count = round_count
        self.round_snapshots = round_snapshots or []
        self.double_up_deposits = double_up_deposits or []

        # 生還者: is_alive=True かつ 脱落理由なし
        self.survivors = sorted(
            [p for p in players.values() if p.is_alive],
            key=lambda p: -p.cash,  # 最終資産降順
        )
        # 脱落者
        self.eliminated = [p for p in players.values() if not p.is_alive]

    def __repr__(self) -> str:
        return (
            f"GameResult(survivors={len(self.survivors)}, "
            f"eliminated={len(self.eliminated)})"
        )


def _obligation_status(ob: Obligation, round_num: int) -> str:
    """義務1件の表示用ステータスを導出する（表示専用・engine判定には非使用）

    engine には義務レベルの status フィールドが無く、is_fulfilled / is_expired /
    round_num の3つから読み手が組み立てる必要がある。優先順は
    履行済 > 失効 > 期限経過 > 今R期限 > 未到来。
    LLMプロンプト（my_contracts）向けの派生情報のみで、判定ロジックには影響しない。
    """
    if ob.is_fulfilled:
        return "fulfilled"   # 履行済
    if ob.is_expired:
        return "expired"     # 失効（当事者脱落 §6.3 等）
    if ob.round_num < round_num:
        return "past"        # 期限経過（監査済み・もう発火しない）
    if ob.round_num == round_num:
        return "due"         # 今ラウンドが期限
    return "upcoming"        # 未到来


class Game:
    """
    談合カードのメインゲームクラス

    ゲーム全体の進行を管理する。
    各フェイズの処理を呼び出し、状態遷移を管理する。
    """

    def __init__(
        self,
        config: GameConfig,
        agents: dict[str, PlayerAgent],
        seed: int = 42,
        logger: EventLogger | None = None,
        cost_budget: Any | None = None,
        stop_after_round: int | None = None,
    ):
        """
        Args:
            config: ゲーム設定
            agents: プレイヤーID → PlayerAgent の辞書
            seed: 乱数シード（同一seedで完全再現可能）
            logger: イベントロガー（Noneなら自動生成）
        """
        self.config = config
        self.agents = agents
        self.seed = seed
        self.rng = GameRng(seed)
        self.logger = logger or EventLogger()
        # Phase C の段階試験だけが指定する、安全な途中停止地点。GameConfigの
        # num_rounds は変えないため、指定Rは最終Rではなく通常Rとして処理される。
        self.stop_after_round = stop_after_round
        # 本戦runnerだけが明示注入する。model_matrix等のスモークではNoneのまま。
        self.cost_budget = cost_budget
        if cost_budget is not None:
            for agent in agents.values():
                setter = getattr(agent, "set_game_cost_budget", None)
                if callable(setter):
                    setter(cost_budget)

        # ゲーム状態
        self.players: dict[str, PlayerState] = {}
        self.contracts: list[Contract] = []
        self.bounties: list[Bounty] = []
        self.carryovers: dict[str, int] = {}  # 市場ID → キャリーオーバー額
        self._last_round_results: dict | None = None  # 前ラウンドの市場決着サマリ（§8.1公開情報）
        self._current_auto_pids: set[str] = set()  # A-6: 当該ラウンドでAUTOになったplayer_id集合
        # A-4: 自分の過去AUTO COMMIT履歴（round/requested/reason/actual）。秘匿情報として
        # for_player_id が一致する本人にのみ visible_state 経由で渡す。
        self._auto_commit_history: dict[str, list[dict]] = {}
        self.current_round: int = 0
        self.pending_repayments: dict[str, int] = {}  # Negotiation中のrepay記録

        # 各プレイヤーのNegotiation状態
        self._action_counts: dict[str, int] = {}
        self._anon_broadcast_counts: dict[str, int] = {}
        self._round_messages: list[dict] = []  # ラウンド内のメッセージ履歴（Bot交渉用）
        # anonymous_broadcastの実送信者記録（_round_messagesのindex → player_id）。
        # 本人の visible_state にだけ返す私的情報（Cycle 4: self-marker）。
        # _round_messages / _god_transcript には積まない（§8.2の秘匿境界を壊さないため）。
        # 【必須】_round_messages と必ず同一ライフサイクルでクリアする
        # （_reset_round_message_state() 以外で再代入しない。片方だけ残すと
        # 新ラウンドでindexが再利用された際に前ラウンドの所有者が誤適用される）。
        self._anon_broadcast_owners: dict[int, str] = {}
        # 当該ラウンドで不成立になった自分のアクション記録（player_id → list）。
        # 本人の visible_state にだけ返す私的情報。他プレイヤーには渡さない。
        self._action_failures: dict[str, list[dict]] = {}

        # 当該ラウンドで自分が当事者の契約に起きた解除関連の状態変化（player_id → list）。
        # 本人の visible_state にだけ返す私的情報。_round_messages / _god_transcript には
        # 積まない（§8.2の秘匿境界を壊さないため。contract_cancel はメッセージを生成しないが、
        # 相手当事者がそれに気づく手段が無いと AUTO_PASS_ON_NO_NEWS により起床せず、
        # 全会一致に到達しない — この通知はその起床トリガ専用）。
        self._contract_notices: dict[str, list[dict]] = {}

        # POST_GAME_REFLECTION専用の神視点transcript。ラウンド跨ぎで消さない。
        # 【絶対条件】_visible_messages() / _build_visible_state() から一切参照しない。
        # CoT reasoning・Handover Memory と同格の構造的隔離対象（rules/project.md:45-59）。
        self._god_transcript: list[dict] = []

        # S2: 霧のラウンドで使用されたカードID集合
        self._fog_card_ids: set[str] = set()

        # S2: 倍掛け預託リスト
        self.double_up_deposits: list[DoubleUpDeposit] = []

        # v0.8 E11: 直近Settlementで解決された倍掛け一覧（visible_state公開用の下地）
        self._last_double_ups_resolved: list[dict] = []

        # S2: ラウンドスナップショット（指標収集用）
        self._round_snapshots: list[dict] = []

        # S2: カードトレード提案リスト（v0.7 §3）
        self.trade_proposals: list[CardTradeProposal] = []
        self._trade_counts: dict[str, int] = {}  # ラウンド内トレード回数

        # FINAL_REFLECTION: 脱落者の最終コメントを呼んだplayer_idの集合。
        # state派生（is_alive/elimination_round）で対象を決めるため、
        # 同一脱落に対しengineが複数eventを出しても二重発火しない。
        self._final_reflection_done: set[str] = set()

        # POST_GAME_REFLECTION: 答え合わせを呼んだplayer_idの集合。全12名
        # （生還者＋脱落者）を対象に、通常完了経路でのみゲーム全体で1回だけ発火する。
        self._post_game_reflection_done: set[str] = set()

    def run(self) -> GameResult:
        """
        ゲームを実行する

        12ラウンドを順に処理し、最終結果を返す。

        Returns:
            ゲーム結果
        """
        self._setup()

        # 通常試合は従来どおりbudget blockを当該agentのfallbackへ委ねる。
        # 一方、実コストを厳格に止める試験では、setup中の1回目のblock後に
        # 追加のAPI呼出しを発生させないため、ラウンド開始前に終了する。
        if self._abort_requested_for_budget():
            return self._finalize()

        for round_num in range(1, self.config.num_rounds + 1):
            self.current_round = round_num

            # 生存者がいなければ早期終了
            alive_count = sum(1 for p in self.players.values() if p.is_alive)
            if alive_count == 0:
                break

            self._phase_market_open(round_num)
            self._phase_negotiation(round_num)
            if self._abort_requested_for_budget():
                return self._finalize()
            self._phase_commit(round_num)
            if self._abort_requested_for_budget():
                return self._finalize()
            self._phase_settlement(round_num)
            if self._abort_requested_for_budget():
                return self._finalize()
            self._phase_finance(round_num)
            if self._abort_requested_for_budget():
                return self._finalize()
            self._phase_reflection(round_num)
            if self._abort_requested_for_budget():
                return self._finalize()
            # 脱落者の最終コメント（演出/記録専用。ゲーム結果には影響しない）。
            # budget中断チェックは入れない — この後はログ集計のみで追加APIが発生しないため。
            self._phase_final_reflection(round_num)
            # AFTER分析で「各ラウンド終了時の生存者数」を推測せず取得できるよう、
            # すべての通常ゲームで軽量な集計イベントを残す。
            self._log_round_complete(round_num)
            if self._stop_requested_after_round(round_num):
                return self._finalize()

        # 通常完了経路のみ: 結果確定後に全員へ神視点開示つきの答え合わせを行う。
        # GameResultは構築済みで、このフェイズは戻り値・勝敗判定に一切影響しない。
        result = self._finalize()
        self._phase_post_game_reflection(result)
        return result

    def _abort_requested_for_budget(self) -> bool:
        """試験限定の予算中断要求を1回だけイベント化する。

        GameCostBudgetは通常の本戦でfallbackを許すため、ここでは
        ``abort_on_block=True`` を明示したrunnerだけを対象にする。中断しても
        GAME_ENDを残し、既に逐次保存されたイベント・LLMログをAFTER集計に使える
        ようにする。
        """
        if self.cost_budget is None or not getattr(self.cost_budget, "abort_requested", False):
            return False
        if not getattr(self, "_budget_abort_logged", False):
            first_block = self.cost_budget.blocks[0] if self.cost_budget.blocks else None
            self.logger.log("GAME_ABORTED", self.current_round, "budget", data={
                "reason": "llm_budget_blocked",
                "first_budget_block": first_block,
            })
            self._budget_abort_logged = True
        return True

    def _stop_requested_after_round(self, round_num: int) -> bool:
        """段階試験の通常R完了後停止を1回だけイベント化する。"""
        if self.stop_after_round is None or round_num < self.stop_after_round:
            return False
        if not getattr(self, "_trial_stop_logged", False):
            self.logger.log("GAME_STOPPED", round_num, "end", data={
                "reason": "trial_stop_after_round",
                "stop_after_round": self.stop_after_round,
                "completed_round": round_num,
            })
            self._trial_stop_logged = True
        return True

    def _log_round_complete(self, round_num: int) -> None:
        """Finance後の生存者数を、分析用に1ラウンド1回だけ記録する。"""
        alive_ids = sorted(pid for pid, player in self.players.items() if player.is_alive)
        self.logger.log("ROUND_COMPLETE", round_num, "end", data={
            "alive_count": len(alive_ids),
            "alive_players": alive_ids,
        })

    def _setup(self) -> None:
        """
        ゲームセットアップ（§2）

        各エージェントに借入額を選択させ、初期状態を生成する。
        """
        game_config = {
                "num_players": self.config.num_players,
                "num_rounds": self.config.num_rounds,
                "total_prize": self.config.total_prize,
                "survival_cash": self.config.survival_cash,
                "interest_rate": self.config.interest_rate,
                "entry_fee": self.config.entry_fee,
        }
        if self.cost_budget is not None:
            game_config.update({
                "per_player_game_cost_cap_usd": self.config.per_player_game_cost_cap_usd,
                "game_cost_cap_usd": self.config.game_cost_cap_usd,
            })
        self.logger.log("GAME_START", 0, "setup", data={
            "config": game_config,
            "seed": self.seed,
        })

        for pid, agent in self.agents.items():
            loan = agent.choose_loan(self.config)
            # 借入額を範囲内にクランプ
            loan = max(self.config.loan_min, min(self.config.loan_max, loan))
            self.players[pid] = player_ops.create_player(pid, loan)

            self.logger.log("LOAN_CHOSEN", 0, "setup", data={
                "player_id": pid,
                "loan_amount": loan,
            })
            # 厳格試験では、借入選択でblockした時点で残り席のAPIを呼ばない。
            if self.cost_budget is not None and getattr(self.cost_budget, "abort_requested", False):
                break

    def _phase_market_open(self, round_num: int) -> None:
        """
        Phase 1: Market Open（§5.1）

        3市場と賞金を公開（キャリーオーバー反映）。
        """
        self._current_markets = market_ops.generate_markets(
            round_num, self.config, self.carryovers, self.rng,
        )

        self.logger.log("MARKET_OPEN", round_num, "market_open", data={
            "markets": [
                {"market_id": m.market_id, "base_prize": m.base_prize,
                 "carryover": m.carryover, "prize_pool": m.prize_pool}
                for m in self._current_markets
            ],
        })

    def _reset_round_message_state(self) -> None:
        """ラウンド開始時のメッセージ系stateリセット（Cycle 4）。

        _anon_broadcast_owners は _round_messages のindexをキーにするため、
        両者は必ず同時にクリアしなければならない。片方だけ残すと、新ラウンドで
        indexが0から再利用された際に前ラウンドの所有者が新ラウンドの別プレイヤーの
        匿名発言へ誤適用され、匿名性破壊・AI自己認識の誤認識という重大なstate
        corruptionになる。このリセットは本メソッド内でのみ行い、他の箇所で
        self._round_messages / self._anon_broadcast_owners を再代入しないこと。
        """
        self._round_messages = []
        self._anon_broadcast_owners = {}

    def _phase_negotiation(self, round_num: int) -> None:
        """
        Phase 2: Negotiation（§5.1）

        1アクション×最大10巡、毎巡ランダム手番。
        passは枠を消費しない。全員連続パスで早期終了。
        """
        self.pending_repayments = {}
        self._action_counts = {pid: 0 for pid in self.players}
        self._anon_broadcast_counts = {pid: 0 for pid in self.players}
        self._trade_counts = {pid: 0 for pid in self.players}
        self._reset_round_message_state()  # ラウンド開始時にクリア（_round_messages / _anon_broadcast_owners を同時に）
        self._action_failures = {pid: [] for pid in self.players}
        self._contract_notices = {pid: [] for pid in self.players}

        alive_ids = [pid for pid, p in self.players.items() if p.is_alive]

        for turn in range(1, self.config.negotiation_max_turns + 1):
            # 毎巡ランダム手番（§5.1）
            turn_order = self.rng.shuffle_turn_order(alive_ids)
            all_passed = True

            for pid in turn_order:
                p = self.players[pid]
                if not p.is_alive:
                    continue

                # アクション上限チェック（passは非カウント）
                if self._action_counts.get(pid, 0) >= self.config.negotiation_max_actions:
                    continue

                agent = self.agents[pid]
                visible_state = self._build_visible_state(round_num, for_player_id=pid)

                action = agent.negotiate(p, round_num, turn, visible_state)

                # アクション検証
                result = action_ops.validate_action(
                    action, p, self.config, self.players,
                    round_num, self._anon_broadcast_counts.get(pid, 0),
                    contracts=self.contracts,
                )

                if isinstance(action, PassAction):
                    # passは常に成功、枠を消費しない
                    self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                        "player_id": pid, "action": "pass", "turn": turn,
                        "source": getattr(action, "source", "llm"),
                    })
                    continue

                all_passed = False

                if not result.success:
                    # アクション不成立（任意支払い不足等）
                    # 枠は原則消費（§2.5 — このルールは変更しない）
                    if result.consumes_action:
                        self._action_counts[pid] = self._action_counts.get(pid, 0) + 1
                    # 不成立の事実と理由を本人に返せるよう記録する。eventログだけでは
                    # LLMへ一切届かず、同じ不成立を繰り返して枠を使い切る事故が実測された
                    # （trial_C_l12_r12_20260822: P06 が R7-R9 で25回のDM不成立、R9破産）。
                    self._record_action_failure(pid, action, result.reason, turn,
                                                result.consumes_action)
                    self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                        "player_id": pid, "action": action.type,
                        "success": False, "reason": result.reason, "turn": turn,
                    })
                    continue

                # アクション実行
                self._execute_negotiation_action(action, pid, round_num, turn)

                if result.consumes_action:
                    self._action_counts[pid] = self._action_counts.get(pid, 0) + 1

            if all_passed:
                # 全員連続パスで早期終了（§5.1）
                self.logger.log("NEGOTIATION_EARLY_END", round_num, "negotiation", data={
                    "turn": turn,
                })
                break

        # ラウンド末: 未受諾のトレード提案を自動失効（v0.7.1）
        for tp in self.trade_proposals:
            if tp.status == CardTradeStatus.PROPOSED and tp.round_proposed == round_num:
                tp.status = CardTradeStatus.EXPIRED
                # v0.8 I2: 提案者へ通知（自動失効）
                bucket = self._contract_notices.setdefault(tp.proposer, [])
                bucket.append({
                    "turn": self.config.negotiation_max_turns,
                    "kind": "trade_expired",
                    "trade_id": tp.trade_id,
                    "with_player": tp.with_player,
                })
                if len(bucket) > self._CONTRACT_NOTICE_MAX:
                    del bucket[: -self._CONTRACT_NOTICE_MAX]

        # ラウンド末: 当該ラウンドで提案され署名が揃わなかった契約を自動失効（v0.8 D5）
        for i, c in enumerate(self.contracts):
            if c.status == ContractStatus.PROPOSED and c.round_created == round_num:
                self.contracts[i] = c.model_copy(update={"status": ContractStatus.EXPIRED})
                proposer = self.contracts[i].proposer
                if proposer in self.players and self.players[proposer].is_alive:
                    unsigned = [p for p in c.parties if p not in c.signed_by]
                    bucket = self._contract_notices.setdefault(proposer, [])
                    bucket.append({
                        "turn": self.config.negotiation_max_turns,
                        "kind": "contract_expired",
                        "contract_id": c.contract_id,
                        "unsigned_by": unsigned,
                    })
                    if len(bucket) > self._CONTRACT_NOTICE_MAX:
                        del bucket[: -self._CONTRACT_NOTICE_MAX]
                self.logger.log("CONTRACT_EXPIRED", round_num, "negotiation", data={
                    "contract_id": c.contract_id,
                    "proposer": proposer,
                    "parties": list(c.parties),
                    "signed_by": list(c.signed_by),
                })

    _ACTION_FAILURE_MEMO_MAX = 12
    """本人へ返す不成立記録の保持上限（1ラウンド最大10アクション＋余裕）"""

    _CONTRACT_NOTICE_MAX = 12
    """本人へ返す契約解除通知の保持上限（_ACTION_FAILURE_MEMO_MAX と同じ作法）"""

    def _record_action_failure(
        self, pid: str, action: Action, reason: str | None,
        turn: int, consumed: bool,
    ) -> None:
        """不成立アクションを本人の私的記録へ積む（表示専用・engine判定に不介入）。

        【秘匿】message本文は絶対に含めない。含めるのは type / 宛先ID / engineが返した
        reason / 巡 / 枠消費の有無だけで、いずれも本人自身の行為とengineの返答であり
        他人の秘匿情報を含まない。それでも他プレイヤーへ渡らないよう、公開は
        _build_visible_state() の for_player_id ブロック内に限定する。
        """
        entry: dict[str, Any] = {
            "turn": turn,
            "action": getattr(action, "type", "unknown"),
            "reason": reason or "",
            "consumed_slot": bool(consumed),
        }
        target = getattr(action, "to", None)
        if target is None:
            with_players = getattr(action, "with_players", None)
            if with_players:
                target = ", ".join(with_players)
        if target:
            entry["target"] = target
        bucket = self._action_failures.setdefault(pid, [])
        bucket.append(entry)
        if len(bucket) > self._ACTION_FAILURE_MEMO_MAX:
            del bucket[: -self._ACTION_FAILURE_MEMO_MAX]

    def _notify_contracts_expired_by_elimination(
        self, before_contracts: list[Contract], eliminated_pid: str,
    ) -> None:
        """脱落によりPROPOSED→EXPIREDへ遷移した契約について、提案者へ通知する（v0.8 D5）。

        elim_ops.forced_liquidation() 呼び出し直後、self.contracts が更新された後に呼ぶこと。
        before_contracts には呼び出し前の self.contracts のスナップショット（浅いコピーで可、
        Contractはmodel_copyで再生成されるため参照比較で十分）を渡す。
        """
        before_by_id = {c.contract_id: c for c in before_contracts}
        for c in self.contracts:
            prev = before_by_id.get(c.contract_id)
            if prev is None or prev.status == c.status:
                continue
            if c.status != ContractStatus.EXPIRED or prev.status != ContractStatus.PROPOSED:
                continue
            proposer = c.proposer
            if proposer in self.players and self.players[proposer].is_alive:
                unsigned = [p for p in c.parties if p not in c.signed_by]
                bucket = self._contract_notices.setdefault(proposer, [])
                bucket.append({
                    "turn": 0,
                    "kind": "contract_expired",
                    "contract_id": c.contract_id,
                    "unsigned_by": unsigned,
                })
                if len(bucket) > self._CONTRACT_NOTICE_MAX:
                    del bucket[: -self._CONTRACT_NOTICE_MAX]

    def _execute_negotiation_action(
        self, action: Action, pid: str, round_num: int, turn: int,
    ) -> None:
        """
        Negotiation中のアクションを実行する

        P2修正: アクション起因の例外をキャッチし、当該アクションを不成立として
        試合を続行する。ゲームルールは変更しない（適用境界の防御のみ）。
        """
        try:
            self._execute_negotiation_action_inner(action, pid, round_num, turn)
        except Exception as e:
            # アクション適用エラー: 不成立として記録し試合続行
            self._record_action_failure(
                pid, action, "実行時エラーにより不成立", turn, consumed=True,
            )
            self.logger.log("ACTION_ERROR", round_num, "negotiation", data={
                "player_id": pid,
                "action": getattr(action, "type", "unknown"),
                "error": str(e)[:200],
                "error_type": type(e).__name__,
                "turn": turn,
            })

    def _execute_negotiation_action_inner(
        self, action: Action, pid: str, round_num: int, turn: int,
    ) -> None:
        """アクション実行の内部処理（例外はP2防御層でキャッチ）"""
        p = self.players[pid]

        if isinstance(action, TransferAction):
            # 即時決済（§9.4）
            sender = p
            receiver = self.players[action.to]
            sender = player_ops.pay(sender, action.amount)
            receiver = player_ops.receive(receiver, action.amount)
            self.players[pid] = sender
            self.players[action.to] = receiver
            self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                "player_id": pid, "action": "transfer", "to": action.to,
                "amount": action.amount, "success": True, "turn": turn,
            })

        elif isinstance(action, RepayAction):
            # 借金返済（Negotiation中のアクション）
            old_cash = p.cash
            p = player_ops.repay_debt(p, action.amount)
            actual = old_cash - p.cash
            self.players[pid] = p
            self.pending_repayments[pid] = self.pending_repayments.get(pid, 0) + actual
            self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                "player_id": pid, "action": "repay", "amount": actual,
                "success": True, "turn": turn,
            })

        elif isinstance(action, ContractProposeAction):
            # 契約提案
            # 発行料を徴収
            p = player_ops.pay(p, self.config.contract_fee)
            self.players[pid] = p
            # 契約オブジェクト生成
            all_parties = [pid] + [pp for pp in action.with_players if pp != pid]
            contract = contract_ops.create_contract(
                proposer=pid,
                parties=all_parties,
                terms=action.terms,
                round_created=round_num,
            )
            self.contracts.append(contract)
            self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                "player_id": pid, "action": "contract_propose",
                "contract_id": contract.contract_id,
                "parties": all_parties, "success": True, "turn": turn,
            })

        elif isinstance(action, ContractSignAction):
            # 契約署名
            for i, c in enumerate(self.contracts):
                if c.contract_id == action.contract_id:
                    self.contracts[i] = contract_ops.sign_contract(c, pid)
                    self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                        "player_id": pid, "action": "contract_sign",
                        "contract_id": action.contract_id,
                        "success": True, "turn": turn,
                    })
                    break

        elif isinstance(action, ContractCancelAction):
            # 契約解除（全当事者合意・§6）。可否は validate_action で検証済みだが、
            # 生存当事者の集合はここ（実行時点）で確定させる。
            alive_parties = {pid_ for pid_, pl in self.players.items() if pl.is_alive}
            for i, c in enumerate(self.contracts):
                if c.contract_id == action.contract_id:
                    updated, cancelled = contract_ops.request_cancel(
                        c, pid, alive_parties, round_num, turn,
                    )
                    self.contracts[i] = updated

                    # 解除は _round_messages を生成しないため、相手LLMが AUTO_PASS で
                    # 起床せず全会一致に到達しない（llm/llm_agent.py の空回り削減機構との
                    # 相互作用）。当事者だけに届く内部通知で起床させる。発言枠・アクション
                    # 枠は消費せず、engine の判定ロジック（contract_ops）には一切影響しない
                    # ——表示・起床専用の私的情報。
                    notice: dict[str, Any] = {
                        "turn": turn,
                        "kind": "cancel_completed" if cancelled else "cancel_requested",
                        "contract_id": updated.contract_id,
                        "by": pid,
                        "cancel_requested_by": list(updated.cancel_requested_by),
                        "pending": [
                            p for p in updated.parties
                            if p in self.players and self.players[p].is_alive
                            and p not in updated.cancel_requested_by
                        ],
                    }
                    # 送信先は「自分以外の生存当事者」。成立(cancelled)・部分同意
                    # (未成立)のどちらも同じ規則で届く——要求者自身はイベントを直接
                    # 見ているため対象外、脱落者は届けても読めないため対象外。
                    recipients = [
                        p for p in updated.parties
                        if p != pid and p in self.players and self.players[p].is_alive
                    ]
                    for p in recipients:
                        bucket = self._contract_notices.setdefault(p, [])
                        bucket.append(dict(notice))
                        if len(bucket) > self._CONTRACT_NOTICE_MAX:
                            del bucket[: -self._CONTRACT_NOTICE_MAX]

                    self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                        "player_id": pid, "action": "contract_cancel",
                        "contract_id": action.contract_id,
                        "cancel_requested_by": list(updated.cancel_requested_by),
                        "parties": list(updated.parties),
                        "cancelled": cancelled, "success": True, "turn": turn,
                    })
                    if cancelled:
                        self.logger.log("CONTRACT_CANCELLED", round_num, "negotiation", data={
                            "contract_id": action.contract_id,
                            "parties": list(updated.parties),
                            "cancel_requested_by": list(updated.cancel_requested_by),
                            "turn": turn,
                        })
                    break

        elif isinstance(action, AnonymousBroadcastAction):
            # 匿名通信（§7.1: 掲載者は秘匿、本文は全員に公開）
            p = player_ops.pay(p, self.config.anon_broadcast_fee)
            self.players[pid] = p
            self._anon_broadcast_counts[pid] = self._anon_broadcast_counts.get(pid, 0) + 1
            self._round_messages.append({
                "sender": None,  # §8.2: 匿名通信の掲載者は秘匿
                "type": "anonymous_broadcast",
                "message": action.message,
                "turn": turn,
            })
            # Cycle 4: 送信者本人だけが自分の匿名発言だと識別できるようにするための
            # 私的所有者記録（_round_messagesのindexキー）。_round_messages自体には
            # 積まない（§8.2の秘匿境界を壊さないため）。この分岐はバリデーション成功
            # パスにのみ到達するため、reject された匿名通信は登録されない。
            self._anon_broadcast_owners[len(self._round_messages) - 1] = pid
            self._god_transcript.append({
                "round": round_num, "turn": turn, "type": "anonymous_broadcast",
                "message": action.message, "actual_sender": pid,
            })
            self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                "player_id": pid, "action": "anonymous_broadcast",
                "success": True, "turn": turn,
            })

        elif isinstance(action, BountyPostAction):
            # 報奨掲載
            bounty = bounty_ops.create_bounty(
                poster=pid,
                amount=action.amount,
                bounty_type_str=action.bounty_type,
                condition_type_str=action.condition_type,
                condition=action.condition,
                round_num=action.round_num,
                beneficiary=action.beneficiary,
                anonymous=action.anonymous,
                surcharge_rate=self.config.anon_bounty_surcharge,
            )
            p = player_ops.pay(p, bounty.deposited)
            self.players[pid] = p
            self.bounties.append(bounty)
            self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                "player_id": pid, "action": "bounty_post",
                "bounty_id": bounty.bounty_id, "amount": bounty.amount,
                "deposited": bounty.deposited, "success": True, "turn": turn,
            })

        elif isinstance(action, BountyCancelAction):
            # 報奨取り下げ
            for i, b in enumerate(self.bounties):
                if b.bounty_id == action.bounty_id and b.poster == pid:
                    self.bounties[i] = bounty_ops.cancel_bounty(b)
                    # 預託金返還
                    p = player_ops.receive(p, b.deposited)
                    self.players[pid] = p
                    self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                        "player_id": pid, "action": "bounty_cancel",
                        "bounty_id": action.bounty_id,
                        "refunded": b.deposited, "success": True, "turn": turn,
                    })
                    break

        elif isinstance(action, CardTradeProposeAction):
            # カードトレード提案（v0.7 §3 / v0.7.1 ブロードキャスト対応）
            if not self.config.card_trade_enabled:
                return
            if round_num > self.config.card_trade_last_round:
                return
            if self._trade_counts.get(pid, 0) >= self.config.card_trade_max_per_round:
                return

            offer_id = f"O_{uuid.uuid4().hex[:8]}"
            created_ids = []

            for target_pid in action.with_players:
                target = self.players.get(target_pid)
                if target is None or not target.is_alive:
                    continue
                # 受取カード所持チェック（持っていない宛先はスキップ）
                try:
                    recv_rank = CardRank[action.receive_card]
                except KeyError:
                    continue
                if not any(c.rank == recv_rank for c in target.hand):
                    continue

                trade_id = f"T_{uuid.uuid4().hex[:8]}"
                proposal = CardTradeProposal(
                    trade_id=trade_id,
                    offer_id=offer_id,
                    proposer=pid,
                    with_player=target_pid,
                    give_card_rank=action.give_card,
                    receive_card_rank=action.receive_card,
                    cash_amount=action.cash_amount,
                    round_proposed=round_num,
                )
                self.trade_proposals.append(proposal)
                created_ids.append(trade_id)

            if created_ids:
                self._trade_counts[pid] = self._trade_counts.get(pid, 0) + 1
                self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                    "player_id": pid, "action": "card_trade_propose",
                    "offer_id": offer_id,
                    "trade_ids": created_ids,
                    "with_players": action.with_players,
                    "success": True, "turn": turn,
                })

        elif isinstance(action, CardTradeAcceptAction):
            # カードトレード受諾（v0.7 §3）
            if not self.config.card_trade_enabled:
                return
            # 提案を検索
            proposal = None
            for tp in self.trade_proposals:
                if tp.trade_id == action.trade_id and tp.status == CardTradeStatus.PROPOSED:
                    proposal = tp
                    break
            if proposal is None:
                return
            if pid != proposal.with_player:
                return
            if round_num > self.config.card_trade_last_round:
                return
            if self._trade_counts.get(pid, 0) >= self.config.card_trade_max_per_round:
                return
            # 提案者の枠は propose 時に消費済み — accept 時には再チェックしない

            proposer_state = self.players[proposal.proposer]
            accepter_state = self.players[pid]
            if not proposer_state.is_alive or not accepter_state.is_alive:
                proposal.status = CardTradeStatus.EXPIRED
                return

            # カード再検証（提案後にカードを使った可能性）
            give_rank = CardRank[proposal.give_card_rank]
            recv_rank = CardRank[proposal.receive_card_rank]
            give_card = next((c for c in proposer_state.hand if c.rank == give_rank), None)
            recv_card = next((c for c in accepter_state.hand if c.rank == recv_rank), None)
            if give_card is None or recv_card is None:
                proposal.status = CardTradeStatus.EXPIRED
                return

            # 現金再検証
            if proposal.cash_amount > 0 and proposal.cash_amount > proposer_state.free_cash:
                proposal.status = CardTradeStatus.EXPIRED
                return
            if proposal.cash_amount < 0 and abs(proposal.cash_amount) > accepter_state.free_cash:
                proposal.status = CardTradeStatus.EXPIRED
                return

            # アトミック実行: カード交換
            proposer_state = player_ops.swap_card(proposer_state, give_card, recv_card)
            accepter_state = player_ops.swap_card(accepter_state, recv_card, give_card)

            # 現金移動
            if proposal.cash_amount > 0:
                proposer_state = player_ops.pay(proposer_state, proposal.cash_amount)
                accepter_state = player_ops.receive(accepter_state, proposal.cash_amount)
            elif proposal.cash_amount < 0:
                accepter_state = player_ops.pay(accepter_state, abs(proposal.cash_amount))
                proposer_state = player_ops.receive(proposer_state, abs(proposal.cash_amount))

            self.players[proposal.proposer] = proposer_state
            self.players[pid] = accepter_state
            proposal.status = CardTradeStatus.ACCEPTED
            self._trade_counts[pid] = self._trade_counts.get(pid, 0) + 1
            # 提案者の枠は propose 時に消費済み — accept で再加算しない

            # 同一 offer_id の残りの提案を EXPIRED に（v0.7.1 ブロードキャスト）
            if proposal.offer_id:
                for tp in self.trade_proposals:
                    if (tp.offer_id == proposal.offer_id
                            and tp.trade_id != proposal.trade_id
                            and tp.status == CardTradeStatus.PROPOSED):
                        tp.status = CardTradeStatus.EXPIRED

            self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                "player_id": pid, "action": "card_trade_accept",
                "trade_id": proposal.trade_id, "with": proposal.proposer,
                "success": True, "turn": turn,
            })

        elif isinstance(action, CardTradeRejectAction):
            # カードトレード拒否（v0.7.1）
            if not self.config.card_trade_enabled:
                return
            for tp in self.trade_proposals:
                if tp.trade_id == action.trade_id and tp.status == CardTradeStatus.PROPOSED:
                    if pid == tp.with_player:
                        tp.status = CardTradeStatus.REJECTED
                        self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                            "player_id": pid, "action": "card_trade_reject",
                            "trade_id": tp.trade_id, "proposer": tp.proposer,
                            "success": True, "turn": turn,
                        })
                    break

        elif hasattr(action, 'message'):
            # dm, broadcast: ログ + メッセージ履歴に蓄積
            from engine.models import DmAction, BroadcastAction
            msg_record: dict = {
                "sender": pid, "type": action.type,
                "message": action.message, "turn": turn,
            }
            if isinstance(action, DmAction):
                msg_record["to"] = action.to
            self._round_messages.append(msg_record)
            self._god_transcript.append({
                "round": round_num, "turn": turn, "type": action.type,
                "message": action.message, "sender": pid,
                "to": action.to if isinstance(action, DmAction) else None,
            })
            self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                "player_id": pid, "action": action.type,
                "success": True, "turn": turn,
            })
        else:
            self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                "player_id": pid, "action": action.type,
                "success": True, "turn": turn,
            })

    def _phase_commit(self, round_num: int) -> None:
        """
        Phase 3: Commit（§5.1）

        全生存AIが「市場+カード」を秘密提出。
        Entry Fee不足者はCommit前に破産・脱落。
        """
        self._current_commits: list[MarketCommit] = []
        self._current_auto_pids = set()  # A-6: 当ラウンドでAUTOになったplayer_id

        alive_ids = [pid for pid, p in self.players.items() if p.is_alive]

        for pid in alive_ids:
            p = self.players[pid]

            # Entry Fee支払い（§4.2: 必須支払い）
            if not player_ops.can_pay(p, self.config.entry_fee):
                # 破産→即時脱落（§1.2, §4.4）
                _contracts_before = list(self.contracts)
                p, self.contracts, record = elim_ops.forced_liquidation(
                    p, "bankruptcy", round_num, self.contracts,
                )
                self.players[pid] = p
                self._notify_contracts_expired_by_elimination(_contracts_before, pid)
                self.logger.log("BANKRUPTCY", round_num, "commit", data={
                    "player_id": pid,
                    "reason": "Cannot pay entry fee",
                    **record,
                })
                continue

            # Entry Fee支払い
            p = player_ops.pay(p, self.config.entry_fee)
            self.players[pid] = p

            # エージェントからCommit取得
            agent = self.agents[pid]
            visible_state = self._build_visible_state(round_num, for_player_id=pid)

            try:
                commit_action = agent.commit(p, self._current_markets, round_num, visible_state)
            except Exception:
                commit_action = None

            # Commit検証
            # A-3: validate_action() の却下理由を捨てずに保持する（従来は result.success
            # のみ見て result.reason を握り潰していた。AUTO発生時の診断に必須）。
            valid_commit = False
            reject_reason: str | None = None
            if commit_action is not None:
                result = action_ops.validate_action(
                    commit_action, p, self.config, self.players, round_num,
                )
                valid_commit = result.success
                if not result.success:
                    reject_reason = result.reason
            else:
                reject_reason = "no_valid_response"

            if valid_commit and commit_action is not None:
                # 正常コミット
                rank = CardRank[commit_action.card_rank]
                card = find_card_by_rank(p.hand, rank)
                if card:
                    mc = MarketCommit(
                        player_id=pid,
                        market_id=commit_action.market_id,
                        card=card,
                    )
                    self._current_commits.append(mc)
                    self.logger.log("COMMIT", round_num, "commit", data={
                        "player_id": pid, "market_id": mc.market_id,
                        "card": card.card_id, "auto": False,
                    })
                    continue

            # --- 自動代行Commit（§4.4） ---
            legal = autocommit_ops.compute_legal_commits(
                p, self.contracts, self._current_markets, round_num,
            )
            selected = autocommit_ops.select_auto_commit(legal, self._current_markets)

            # A-1: 本人が何を指定しようとしたか（requested_*）を、取得できた場合のみ記録
            requested_market_id = getattr(commit_action, "market_id", None)
            requested_card_rank = getattr(commit_action, "card_rank", None)

            if selected is None:
                # 合法Commit 0件 → 履行不能 → 契約違反 → 即時脱落
                # Entry Feeは既に支払済みなので返金しない
                # A-2（バグ修正）: 従来は **record の展開順により診断理由
                # ("No legal commits available (contradictory contracts)") が
                # record["reason"]="contract_violation" に上書きされ、握り潰されていた。
                # 診断理由は failure_detail に退避し、reason は既存互換のため変更しない。
                blocking_obligations = [
                    {"ob_type": ob.ob_type.value, "details": dict(ob.details)}
                    for ob in contract_ops.get_all_type_b_for_player(
                        self.contracts, pid, round_num,
                    )
                ]
                _contracts_before = list(self.contracts)
                p, self.contracts, record = elim_ops.forced_liquidation(
                    p, "contract_violation", round_num, self.contracts,
                )
                self.players[pid] = p
                self._notify_contracts_expired_by_elimination(_contracts_before, pid)
                self.logger.log("AUTO_COMMIT_FAILURE", round_num, "commit", data={
                    "player_id": pid,
                    **record,
                    "failure_detail": "No legal commits available (contradictory contracts)",
                    "legal_commit_count": 0,
                    "blocking_obligations": blocking_obligations,
                    "requested_market_id": requested_market_id,
                    "requested_card_rank": requested_card_rank,
                    "reject_reason": reject_reason,
                })
                # A-4: 本人の秘匿履歴に記録（脱落済みのため次ラウンドprompt自体は
                # もう届かないが、途中脱落しない他ケースとの一貫性のため記録は残す）
                self._auto_commit_history.setdefault(pid, []).append({
                    "round": round_num,
                    "requested_market_id": requested_market_id,
                    "requested_card_rank": requested_card_rank,
                    "reason": reject_reason,
                    "actual_market_id": None,
                    "actual_card": None,
                    "failure": True,
                })
            else:
                self._current_commits.append(selected)
                self._current_auto_pids.add(pid)
                self.logger.log("COMMIT", round_num, "commit", data={
                    "player_id": pid,
                    "market_id": selected.market_id,
                    "card": selected.card.card_id,
                    "auto": True,
                })
                # AUTO COMMIT公示（§4.4）
                # A-1: 本人が指定したもの（requested_*）・却下理由・実際に提出されたもの
                # (actual_*) を1イベントに揃える。requested_* が None のケースは
                # 「コミット自体が取得できなかった」（agent例外/parse失敗）ことを示す。
                self.logger.log("AUTO_COMMIT", round_num, "commit", data={
                    "player_id": pid,
                    "message": f"{pid}: AUTO COMMIT",
                    "requested_market_id": requested_market_id,
                    "requested_card_rank": requested_card_rank,
                    "reason": reject_reason,
                    "actual_market_id": selected.market_id,
                    "actual_card": selected.card.card_id,
                })
                # A-4: 本人の秘匿履歴に記録（次ラウンドprompt用）
                self._auto_commit_history.setdefault(pid, []).append({
                    "round": round_num,
                    "requested_market_id": requested_market_id,
                    "requested_card_rank": requested_card_rank,
                    "reason": reject_reason,
                    "actual_market_id": selected.market_id,
                    "actual_card": selected.card.card_id,
                    "failure": False,
                })
                # A-8: validation失敗経路（agent.commit()自体は例外を投げず有効な
                # アクションを返したが、validate_action()がそれを却下した経路）でのみ
                # 本人のAUTOカウンタへ反映する。commit_action is None の場合は
                # agent.commit() 内部の例外経路で既に auto_commit_count が加算済み
                # のため、ここで呼ぶと二重カウントになる。
                if commit_action is not None and hasattr(agent, "note_auto_commit"):
                    agent.note_auto_commit(reject_reason)

    def _phase_settlement(self, round_num: int) -> None:
        """Phase 4: Reveal & Settlement（§5.2）+ S2倍掛け処理

        v0.8 D2: 前ラウンド預託の解決（旧Step1）はexecute_settlement()内部
        （Step2市場結果確定後・Step3型B監査前）へ移設済み。ここでは
        execute_settlement()が返すdu_summaryからカウンタを受け取り、
        今ラウンド勝者へのTAKE/DOUBLE提示（_process_double_up、旧Step2）のみを
        Settlement後に呼ぶ。
        """
        (
            self.players,
            self.contracts,
            self.bounties,
            new_carryovers,
            market_results,
            du_summary,
        ) = settlement_ops.execute_settlement(
            self.players,
            self._current_markets,
            self._current_commits,
            self.contracts,
            self.bounties,
            round_num,
            self.config,
            self.logger,
            double_up_deposits=self.double_up_deposits if self.config.double_up_enabled else None,
        )

        # S2: 霧のラウンドで使用されたカードを記録
        if round_num in self.config.fog_rounds:
            for c in self._current_commits:
                self._fog_card_ids.add(c.card.card_id)

        # S2: 倍掛け処理（前R預託の解決結果はexecute_settlement内で確定済み）
        du_success = du_summary.get("success", 0)
        du_fail = du_summary.get("fail", 0)
        du_solo_forfeit = du_summary.get("solo_forfeit", 0)
        self._last_double_ups_resolved = du_summary.get("resolved", [])
        if self.config.double_up_enabled:
            self._process_double_up(round_num, market_results)

        # S2: ラウンドスナップショット記録
        surge_count = sum(1 for mr in market_results if mr.surged)
        alive_count = sum(1 for p in self.players.values() if p.is_alive)
        self._round_snapshots.append({
            "round": round_num,
            "alive_count": alive_count,
            "total_assets": sum(p.cash for p in self.players.values() if p.is_alive),
            "total_debt": sum(p.debt_balance for p in self.players.values() if p.is_alive),
            "surge_count": surge_count,
            "double_up_success": du_success,
            "double_up_fail": du_fail,
            "double_up_solo_forfeit": du_solo_forfeit,
            "active_deposits": len([d for d in self.double_up_deposits if not d.resolved]),
        })

        # 前ラウンド結果サマリを保存（§8.1公開情報: 参加者・使用カード・勝者・獲得額・高騰・繰越）
        # 次ラウンドのvisible_stateに載せ、プレイヤーへ確実に周知する。
        # commits: 参加者IDと使用カードランクのペア。§8.1「各市場の参加者・使用カード（決着後）」の
        # うちカードが一度もプロンプトに描画されていなかった実装漏れの是正（participantsは後方互換で残す）。
        # 霧のラウンド（config.fog_rounds）はカードランクを "FOG" に伏せる。
        is_fog_round = round_num in self.config.fog_rounds
        self._last_round_results = {
            "round": round_num,
            "markets": [
                {
                    "market_id": mr.market_id,
                    "participants": [c.player_id for c in mr.participants],
                    "commits": [
                        {
                            "player_id": c.player_id,
                            "card_rank": "FOG" if is_fog_round else c.card.rank.name,
                            # A-6: AUTO COMMITだったかどうか（§8.1公開情報「AUTO COMMIT発生」）
                            "auto": c.player_id in self._current_auto_pids,
                        }
                        for c in mr.participants
                    ],
                    "winners": mr.winners,
                    "prize_per_winner": mr.prize_per_winner,
                    "total_pool": mr.total_pool,
                    "surged": mr.surged,
                    "carryover_to_next": new_carryovers.get(mr.market_id, 0),
                }
                for mr in market_results
            ],
        }

        # キャリーオーバー更新
        if round_num < self.config.num_rounds:
            # R12以外: キャリーオーバーを次ラウンドへ
            self.carryovers = new_carryovers
        else:
            # R12: キャリーオーバーは消滅（§4.7）
            self.carryovers = {}

    def _process_double_up(
        self, round_num: int, market_results: list[MarketResult],
    ) -> None:
        """
        S2: 倍掛け処理 — 今ラウンドの勝者への選択提示（v0.8 D2）

        前ラウンド預託の解決（旧Step1）は execute_settlement() 内部へ移設済み
        （settlement.execute_settlement() の呼び出し元 _phase_settlement()
        が既に解決結果=du_summaryを受け取っている）。ここでは今ラウンドの
        市場勝者にTAKE/DOUBLEを提示する（旧Step2、R12以外）。
        """
        # 今ラウンドの市場勝者を特定（player_id → 獲得額マップ）
        round_winners: dict[str, int] = {}
        # ソロ市場を除外した勝者マップ（倍掛け成功判定用, §6.2）
        non_solo_winners: dict[str, int] = {}
        # 空き巣市場かどうかのマップ（market_id → participants==1）
        solo_markets: set[str] = set()

        # パス1: ソロ市場の特定
        for mr in market_results:
            if len(mr.participants) == 1:
                solo_markets.add(mr.market_id)

        # パス2: 勝者集計
        for mr in market_results:
            for winner_id in mr.winners:
                round_winners[winner_id] = round_winners.get(winner_id, 0) + mr.prize_per_winner
                if mr.market_id not in solo_markets:
                    non_solo_winners[winner_id] = non_solo_winners.get(winner_id, 0) + mr.prize_per_winner

        # 今ラウンドの勝者に倍掛け選択を提示（R12以外、R11が最後）
        if round_num < self.config.num_rounds:
            for winner_id, prize_won in round_winners.items():
                p = self.players[winner_id]
                if not p.is_alive or prize_won <= 0:
                    continue

                visible_state = self._build_visible_state(round_num, for_player_id=winner_id)

                # 成功した倍掛けの2倍払い出し分は強制TAKE（連鎖禁止）
                # → 元の市場賞金のみが倍掛け対象
                # 今の prize_won には2倍払い出し分が含まれうるので、
                # 元の市場賞金のみを算出
                du_payout_this_round = 0
                for dep in self.double_up_deposits:
                    if (dep.resolved and dep.success
                            and dep.success_round == round_num
                            and dep.player_id == winner_id):
                        du_payout_this_round += dep.deposit_amount * 2

                eligible_prize = prize_won - du_payout_this_round
                if eligible_prize <= 0:
                    continue

                agent = self.agents[winner_id]
                try:
                    wants_double = agent.choose_double_up(
                        p, eligible_prize, round_num, visible_state,
                    )
                except Exception:
                    wants_double = False

                if wants_double:
                    # 賞金を回収して預託
                    p = player_ops.pay(p, eligible_prize)
                    self.players[winner_id] = p
                    deposit = DoubleUpDeposit(
                        player_id=winner_id,
                        deposit_amount=eligible_prize,
                        deposited_round=round_num,
                        success_round=round_num + 1,
                    )
                    self.double_up_deposits.append(deposit)
                    self.logger.log("DOUBLE_UP_CHOSEN", round_num, "settlement", data={
                        "player_id": winner_id,
                        "deposit": eligible_prize,
                        "success_round": round_num + 1,
                    })

    def _phase_finance(self, round_num: int) -> None:
        """Phase 5: Finance（§5.3）"""
        self.players, self.contracts = finance_ops.execute_finance(
            self.players,
            round_num,
            self.config,
            self.contracts,
            self.logger,
            # R1-11のrepayはNegotiation中に即時処理済み。
            # Financeでの任意返済は追加分のみ（現状は空）
            pending_repayments={},
        )
        # v0.8 I1: 残存義務ゼロになったACTIVE契約（ゾンビ契約）をCLOSEDへ畳む
        self.contracts = contract_ops.close_contracts_without_remaining_obligations(
            self.contracts, round_num,
        )

    def _phase_reflection(self, round_num: int) -> None:
        """
        Phase 6: Reflection（引き継ぎメモリ）

        ラウンド終了後、各生存AIに「次ラウンドへ持ち越す自由記述メモ」を
        1枚だけ書かせる。前ラウンドのmemory＋当ラウンドの会話・契約・結果を
        材料に、何を残すかはモデルの自由（フォーマット強制なし）。

        - config.memory_enabled が False なら何もしない（既定Falseで旧挙動保持）
        - 最終ラウンドはスキップ（次ラウンドが存在せずメモが使われないため）
        - 1体のAPI失敗でゲーム全体を止めないよう、例外は握りつぶす
        - エンジンの計算ロジック・戻り値シグネチャには一切影響しない
        """
        if not self.config.memory_enabled:
            return
        if round_num >= self.config.num_rounds:
            return

        alive_ids = [pid for pid, p in self.players.items() if p.is_alive]
        for pid in alive_ids:
            agent = self.agents[pid]
            p = self.players[pid]
            visible_state = self._build_visible_state(round_num, for_player_id=pid)
            try:
                agent.reflect(p, round_num, visible_state)
            except Exception:
                # メモ更新の失敗はゲーム進行に影響させない（前ラウンドのmemoryを維持）
                continue

    _ELIMINATION_EVENT_TYPES = {
        "BANKRUPTCY", "AUTO_COMMIT_FAILURE", "FORCED_LIQUIDATION",
        "ELIMINATION", "SURVIVAL_CHECK",
    }

    def _build_elimination_context(self, pid: str, round_num: int) -> dict:
        """
        FINAL_REFLECTION prompt用に、脱落原因・清算結果を確定済みstateと
        event logから読み取り専用で組み立てる（Game state・エンジン判定には
        一切書き戻さない、表示用データの取得のみ）。

        reason は PlayerState.elimination_reason（確定state）をそのまま使う。
        phase/event_type/liquidation は当該ラウンド・当該playerのevent logを
        走査して補足する（無くてもreasonだけで最低限のprompt文言は組める）。
        """
        p = self.players[pid]
        reason = p.elimination_reason or "unknown"

        liquidation: dict[str, Any] | None = None
        event_type: str | None = None
        phase: str | None = None
        has_auto_commit_failure = False

        for event in self.logger.events:
            if event.round_num != round_num or event.event_type not in self._ELIMINATION_EVENT_TYPES:
                continue
            data = event.data or {}
            if data.get("player_id") != pid:
                continue
            if event.event_type == "AUTO_COMMIT_FAILURE":
                has_auto_commit_failure = True
            if liquidation is None and "cash_before" in data:
                # 強制清算recordを持つevent（BANKRUPTCY/AUTO_COMMIT_FAILUREは
                # elim_ops.forced_liquidation()のrecordを直接マージ、
                # settlement/financeのFORCED_LIQUIDATIONはrecordそのもの）
                liquidation = {
                    "cash_before": data.get("cash_before"),
                    "debt_before": data.get("debt_before"),
                    "debt_repaid": data.get("debt_repaid"),
                    "bad_debt": data.get("bad_debt"),
                    "cash_confiscated": data.get("cash_confiscated"),
                    "cards_destroyed": data.get("cards_destroyed"),
                }
                event_type = event.event_type
                phase = event.phase
            elif event_type is None:
                event_type = event.event_type
                phase = event.phase

        reason_labels = {
            "contract_violation": "契約違反による脱落（署名した義務を履行できなかった／違反した）",
            "bankruptcy": "破産による脱落（Entry Feeまたは必須返済を支払えなかった）",
            "condition_not_met": (
                f"最終ラウンド生存条件未達（借金完済かつ現金"
                f"{self.config.survival_cash // 10_000}万円以上を満たせなかった）"
            ),
        }
        reason_label = reason_labels.get(reason, f"脱落（理由: {reason}）")
        if reason == "contract_violation" and has_auto_commit_failure:
            reason_label += "。契約義務が互いに矛盾し、合法なコミットが1つも存在しなかった"

        return {
            "player_id": pid,
            "round": round_num,
            "reason": reason,
            "reason_label": reason_label,
            "phase": phase,
            "event_type": event_type,
            "liquidation": liquidation,
            "is_final_round": round_num >= self.config.num_rounds,
            "survival_cash": self.config.survival_cash,
        }

    def _build_completion_context(self, pid: str, round_num: int) -> dict:
        """
        FINAL_REFLECTION completion variant（R12完走した生還者）prompt用に、
        本人が正当に知っている公開情報のみで組み立てる読み取り専用コンテキスト。

        survivor_ids は `_build_visible_state()["alive_players"]` と同じ公開情報
        （sorted pid一覧）。**順位（rank）はここに含めない** —
        他生還者の相対資産順を漏らすと「本人が知っていた情報のみ」条件に反する
        （順位はPOST_GAME_REFLECTIONまで持ち越す）。

        脱落系キー（reason/reason_label/phase/event_type/liquidation）は
        Noneで明示的に埋め、_build_elimination_context()とイベントschemaの
        キー集合を両variantで揃える。
        """
        p = self.players[pid]
        survivor_ids = sorted(
            other_pid for other_pid, other in self.players.items() if other.is_alive
        )
        return {
            "player_id": pid,
            "round": round_num,
            "survived": True,
            "final_cash": p.cash,
            "final_debt": p.debt_balance,
            "survival_cash": self.config.survival_cash,
            "num_rounds": self.config.num_rounds,
            "survivor_ids": survivor_ids,
            "survivor_count": len(survivor_ids),
            "total_players": len(self.players),
            "reason": None,
            "reason_label": None,
            "phase": None,
            "event_type": None,
            "liquidation": None,
            "is_final_round": True,
        }

    def _phase_final_reflection(self, round_num: int) -> None:
        """
        Phase 7: Final Reflection（脱落者/完走者の最終コメント。演出/記録専用）

        当ラウンドで新たに脱落したプレイヤー本人に、脱落の事実と原因を伝えて
        1回だけ最終コメントを書かせる（elimination variant）。ゲーム状態・戻り値・
        生存判定には一切触れない。通常Reflectionと違いR12もスキップせず、
        R12末では生存者にも1回だけ完走版（completion variant）を書かせる。

        R12生存者の結果はこのフェイズ時点で既に確定済み
        （_phase_finance(12) がR12自動返済とsurvival checkを完了させ、
        _phase_reflection(12) は即returnする）。よって「ループ内では結果が
        未確定」という懸念は存在しない。

        対象は `self.players` の確定state（is_alive/elimination_round）から派生する
        （各脱落サイトへのqueue push不要）。同一脱落に対しengineが複数eventを
        出しても、call前に `_final_reflection_done` へ追加するため二重発火しない。
        elimination対象（not is_alive）とcompletion対象（is_alive）は
        `is_alive` の極性で構造的に排他であり、1playerにつきどちらか片方が
        ちょうど1回だけ実行される（ゲーム全体でFINAL_REFLECTIONは1player 1回）。
        """
        if not self.config.final_reflection_enabled:
            return

        targets: list[tuple[str, str]] = []
        # pass 1: 当ラウンドの脱落者（既存挙動、完全に不変）
        for pid, p in sorted(self.players.items()):
            if (
                not p.is_alive
                and p.elimination_round == round_num
                and pid not in self._final_reflection_done
            ):
                targets.append((pid, "elimination"))
        # pass 2: R12完走の生還者。survival checkは_phase_finance(12)で確定済み
        if round_num >= self.config.num_rounds:
            marked = {pid for pid, _ in targets} | self._final_reflection_done
            for pid, p in sorted(self.players.items()):
                if p.is_alive and pid not in marked:
                    targets.append((pid, "completion"))

        for pid, variant in targets:
            # 例外が起きても再試行しないよう、call前に必ずマークする（1player 1回保証）
            self._final_reflection_done.add(pid)
            agent = self.agents.get(pid)
            if variant == "elimination":
                method = getattr(agent, "final_reflect", None)
                if not callable(method):
                    continue  # Bot/StubAgent等はfinal_reflectを持たないので対象外
                ctx = self._build_elimination_context(pid, round_num)
                visible_state = self._build_visible_state(round_num, for_player_id=pid)
                try:
                    result = method(self.players[pid], round_num, visible_state, ctx)
                except Exception as e:
                    result = {"status": "error", "error": str(e)[:200]}
            else:
                method = getattr(agent, "completion_reflect", None)
                if not callable(method):
                    continue  # Bot/StubAgent等はcompletion_reflectを持たないので対象外
                ctx = self._build_completion_context(pid, round_num)
                visible_state = self._build_visible_state(round_num, for_player_id=pid)
                try:
                    result = method(self.players[pid], round_num, visible_state, ctx)
                except Exception as e:
                    result = {"status": "error", "error": str(e)[:200]}
            if not isinstance(result, dict):
                result = {}
            self.logger.log("FINAL_REFLECTION", round_num, "final_reflection", data={
                "player_id": pid,
                "round": round_num,
                "variant": variant,
                "elimination_reason": ctx["reason"],
                "elimination_phase": ctx["phase"],
                "elimination_event": ctx["event_type"],
                "model_id": getattr(getattr(agent, "model_info", None), "model_id", None),
                "status": result.get("status"),
                "emotion": result.get("emotion"),
                "defeat_cause": result.get("defeat_cause"),
                "comment": result.get("comment", ""),
                "comment_chars": result.get("chars", 0),
                "truncated": result.get("truncated", False),
                "salvaged": result.get("salvaged", []),
            })

    def _build_god_shared_block(self, result: "GameResult") -> dict[str, Any]:
        """
        POST_GAME_REFLECTION用の共有ブロック（12人分バイト同一）。

        `self.players` と `self.logger.events` からの読み取り専用で構築する
        （`_build_elimination_context()` と同じ先例）。`result.survivors` は
        `game.py:57-60` で既にcash降順に整列済みなので、ここで順位を
        再計算して定義がずれることを避け、そのまま使う。

        round_digest は MARKET_RESULT（市場勝者・賞金）のみを1ラウンド1行に圧縮する
        （REVEALのカード単位内訳は含めない）。理由は2つ:
        1. 12人×12ラウンドのカード単位内訳はprompt長が組合せ爆発する
           （test_prompt_size_ceilingを容易に超える）。
        2. `engine/settlement.py` はfog_roundsのカードを"FOG"にマスクし、
           コミット自体もラウンド跨ぎで復元不能（`self._current_commits`はリセット
           される）。市場勝者（player_id）はfogの影響を受けないため、この設計は
           fog_rounds対応でもKeyErrorにならず優雅に劣化する。
        """
        roster = sorted(self.players.keys())

        # 最終順位表: 生還者はcash降順（GameResult.survivorsで確定済み）。
        # 脱落者は「脱落ラウンドが遅いほど上位」（後半まで生き残った実績を評価）、
        # 同ラウンド脱落はpid昇順で決定的に並べる。
        eliminated_sorted = sorted(
            result.eliminated,
            key=lambda p: (-(p.elimination_round or 0), p.player_id),
        )
        final_standings: list[str] = []
        rank_by_player: dict[str, int] = {}
        rank = 0
        for p in result.survivors:
            rank += 1
            rank_by_player[p.player_id] = rank
            final_standings.append(
                f"{rank}位 {p.player_id} 生還 現金{p.cash // 10_000}万円"
            )
        for p in eliminated_sorted:
            rank += 1
            rank_by_player[p.player_id] = rank
            reason = p.elimination_reason or "unknown"
            final_standings.append(
                f"{rank}位 {p.player_id} R{p.elimination_round}脱落（{reason}） "
                f"現金{p.cash // 10_000}万円"
            )

        # ラウンドダイジェスト: MARKET_RESULTのみ（上記docstring参照）
        market_events_by_round: dict[int, list] = {}
        for event in self.logger.events:
            if event.event_type != "MARKET_RESULT":
                continue
            market_events_by_round.setdefault(event.round_num, []).append(event)

        round_digest: list[str] = []
        for r in range(1, result.round_count + 1):
            events = sorted(
                market_events_by_round.get(r, []),
                key=lambda e: (e.data or {}).get("market_id", ""),
            )
            if not events:
                round_digest.append(f"R{r}: （市場結果なし）")
                continue
            parts = []
            for e in events:
                data = e.data or {}
                winners = data.get("winners") or []
                prize = data.get("prize_per_winner")
                market_id = data.get("market_id", "?")
                winners_label = "・".join(winners) if winners else "勝者なし"
                prize_label = f"{prize // 10_000}万円" if isinstance(prize, int) else "不明"
                parts.append(f"{market_id}→{winners_label}({prize_label})")
            round_digest.append(f"R{r}: " + "; ".join(parts))

        # 違反判定用の索引: TYPE_B_VIOLATIONはobligation_idを持つので直接、
        # TYPE_A_FAILUREはobligation_idを持たないため(round, obligor)の組で近似する
        # （同一義務者が同一ラウンドに複数のTYPE_A義務を持つ場合は区別できないが、
        # POST_GAME_REFLECTIONは演出/記録専用なのでこの近似を許容する）。
        violated_obligation_ids: set[str] = set()
        type_a_failed_pairs: set[tuple[int, str]] = set()
        for event in self.logger.events:
            data = event.data or {}
            if event.event_type == "TYPE_B_VIOLATION":
                ob_id = data.get("obligation_id")
                if ob_id:
                    violated_obligation_ids.add(ob_id)
            elif event.event_type == "TYPE_A_FAILURE":
                pid = data.get("player_id")
                if pid:
                    type_a_failed_pairs.add((event.round_num, pid))

        ob_type_labels = {
            ObligationType.TYPE_A_PAYMENT: "A",
            ObligationType.TYPE_B_MARKET: "B(市場指定)",
            ObligationType.TYPE_B_CARD: "B(カード指定)",
            ObligationType.TYPE_B_NO_MARKET: "B(市場禁止)",
        }
        contract_ledger: list[str] = []
        violation_ledger: list[str] = []
        for contract in self.contracts:
            # 解除済み契約（§6・全当事者合意）の義務は「－解除」で表示し、
            # ✓履行/✗違反のいずれにも混入させない（request_cancel が未処理義務を
            # is_expired=True にするだけで消しはしないため、区別しないと"✓履行"と
            # 誤表示される）。
            cancelled = contract.status == ContractStatus.CANCELLED
            for ob in contract.obligations:
                label = ob_type_labels.get(ob.ob_type, str(ob.ob_type))
                if ob.ob_type == ObligationType.TYPE_A_PAYMENT:
                    amount = ob.details.get("amount")
                    detail_label = f"{amount // 10_000}万円" if isinstance(amount, int) else "?"
                elif ob.ob_type == ObligationType.TYPE_B_MARKET:
                    detail_label = str(ob.details.get("market_id", "?"))
                elif ob.ob_type == ObligationType.TYPE_B_CARD:
                    detail_label = str(ob.details.get("rank") or ob.details.get("card_id") or "?")
                else:
                    detail_label = str(ob.details.get("market_id", "?"))

                if cancelled:
                    status = f"－解除(R{contract.cancelled_round})"
                elif ob.round_num > result.round_count:
                    status = "－未到達"
                elif ob.ob_type == ObligationType.TYPE_A_PAYMENT:
                    status = "✗不履行" if (ob.round_num, ob.obligor) in type_a_failed_pairs else "✓履行"
                else:
                    status = "✗違反" if ob.obligation_id in violated_obligation_ids else "✓履行"

                line = f"{ob.obligor}→{ob.counterparty} 型{label} {detail_label}(R{ob.round_num}) {status}"
                contract_ledger.append(line)
                if status.startswith("✗"):
                    violation_ledger.append(line)

        return {
            "roster": roster,
            "final_standings": final_standings,
            "round_digest": round_digest,
            "contract_ledger": contract_ledger,
            "violation_ledger": violation_ledger,
            "rank_by_player": rank_by_player,
        }

    def _build_post_game_context(
        self, pid: str, result: "GameResult", shared: dict[str, Any],
    ) -> dict[str, Any]:
        """
        POST_GAME_REFLECTION prompt用の、本人固有の神視点開示ブロックを組み立てる。

        `self._god_transcript`（DM本文・匿名通信の真の掲載者）と `self.contracts`
        （非当事者だった契約の条項）、TYPE_B_VIOLATION/TYPE_A_FAILUREイベント
        （本人に対して破られた約束）から、本人が正当な手段では知り得なかった
        事実のみを抽出する。**この関数の出力はGame stateに一切書き戻さない**
        （読み取り専用、`_build_elimination_context()`と同じ先例）。
        """
        p = self.players[pid]
        survived = p.is_alive

        revelations: list[str] = []

        # (1) 本人が送った/受け取ったDMの相手と要旨（本人には既知の情報だが、
        #     「秘匿されていた」のは相手の受信有無ではなく本文そのものではないため、
        #     ここでは本人が当事者のDMは対象外とし、代わりに(2)(3)(4)に注力する。
        #     ただし要件上「本人宛/本人発DMの相手と要旨」も明示的に含める。
        for entry in self._god_transcript:
            if entry.get("type") != "dm":
                continue
            sender = entry.get("sender")
            to = entry.get("to")
            if sender != pid and to != pid:
                continue
            other = to if sender == pid else sender
            direction = "あなたが送った" if sender == pid else "あなたが受け取った"
            msg = entry.get("message") or ""
            excerpt = msg if len(msg) <= 80 else msg[:80] + "…"
            revelations.append(
                f"R{entry.get('round')}: {other}との非公開DM（{direction}）: 「{excerpt}」"
            )

        # (2) 匿名通信の真の掲載者（本人が読めた公開ログ上の全匿名通信が対象。
        #     個人別の既読判定は行わない — 公開ログは全員に等しく見えるため、
        #     「誰が本当に発信したか」を全件開示するのが最も安全側の近似）
        for entry in self._god_transcript:
            if entry.get("type") != "anonymous_broadcast":
                continue
            actual = entry.get("actual_sender")
            if not actual or actual == pid:
                continue
            msg = entry.get("message") or ""
            excerpt = msg if len(msg) <= 60 else msg[:60] + "…"
            revelations.append(
                f"R{entry.get('round')}: 匿名通信「{excerpt}」の真の掲載者は {actual} でした"
            )

        # (3) 本人が非当事者だった契約の条項（存在は公示されているが内容は秘匿）
        for contract in self.contracts:
            if pid in contract.parties or not contract.obligations:
                continue
            ob = contract.obligations[0]
            label = {
                ObligationType.TYPE_A_PAYMENT: "型A(金銭)",
                ObligationType.TYPE_B_MARKET: "型B(市場指定)",
                ObligationType.TYPE_B_CARD: "型B(カード指定)",
                ObligationType.TYPE_B_NO_MARKET: "型B(市場禁止)",
            }.get(ob.ob_type, str(ob.ob_type))
            parties_label = "・".join(contract.parties)
            revelations.append(
                f"R{contract.round_created}: {parties_label}の間に非公開の契約"
                f"（{label}、義務{len(contract.obligations)}件）がありました"
            )

        # (4) 本人に対して破られた約束（TYPE_B_VIOLATION: obligation_idで
        #     相手方=本人の義務を特定。TYPE_A_FAILUREはobligation_idを持たないため、
        #     当該ラウンド・当該義務者のTYPE_A義務のうち相手方=本人が一意に
        #     決まる場合のみ言及する）
        ob_by_id: dict[str, Any] = {}
        for contract in self.contracts:
            for ob in contract.obligations:
                ob_by_id[ob.obligation_id] = ob

        for event in self.logger.events:
            data = event.data or {}
            if event.event_type == "TYPE_B_VIOLATION":
                ob = ob_by_id.get(data.get("obligation_id"))
                if ob is not None and ob.counterparty == pid:
                    revelations.append(
                        f"R{event.round_num}: {ob.obligor}があなたへの約束"
                        f"（型{ob.ob_type.value}）を破りました"
                    )
            elif event.event_type == "TYPE_A_FAILURE":
                obligor = data.get("player_id")
                candidates = [
                    ob for ob in ob_by_id.values()
                    if ob.obligor == obligor
                    and ob.round_num == event.round_num
                    and ob.ob_type == ObligationType.TYPE_A_PAYMENT
                    and ob.counterparty == pid
                ]
                if len(candidates) == 1:
                    revelations.append(
                        f"R{event.round_num}: {obligor}からあなたへの支払いが履行されませんでした"
                    )

        # 情報量を絞る（設計上8〜12項目目安。多すぎる場合は先頭から優先）
        revelations = revelations[:12]

        return {
            "player_id": pid,
            "own_rank": shared["rank_by_player"].get(pid),
            "survived": survived,
            "elimination_reason": None if survived else p.elimination_reason,
            "elimination_round": None if survived else p.elimination_round,
            "final_cash": p.cash,
            "final_debt": p.debt_balance,
            "shared": shared,
            "revelations": revelations,
        }

    def _phase_post_game_reflection(self, result: "GameResult") -> None:
        """
        Phase 8: Post-Game Reflection（ゲーム完全終了後、全員の答え合わせ。
        演出/記録専用。game state・戻り値・勝敗判定には一切影響しない）

        `_finalize()`でGameResultが確定した直後、通常完了経路でのみ発火する
        （run():203の1箇所のみが呼び出し元。予算abort・stop_after_roundの
        早期returnは対象外 — `_finalize()`は既にこれらを`completed:False`と
        タグ付け済み）。生還者・脱落者の全12名を対象とし、1player 1回のみ。

        このフェイズだけが `build_post_game_reflection_prompt()` 経由で
        神視点情報（DM本文・匿名通信の真の掲載者・非公開契約条項・破られた約束）
        をpromptへ投入してよい（`rules/project.md`の許可リストで固定）。
        """
        if not self.config.post_game_reflection_enabled:
            return
        shared = self._build_god_shared_block(result)
        for pid in sorted(self.players):
            if pid in self._post_game_reflection_done:
                continue
            # 例外が起きても再試行しないよう、call前に必ずマークする（1player 1回保証）
            self._post_game_reflection_done.add(pid)
            agent = self.agents.get(pid)
            method = getattr(agent, "post_game_reflect", None)
            if not callable(method):
                continue  # Bot/StubAgent等はpost_game_reflectを持たないので対象外
            ctx = self._build_post_game_context(pid, result, shared)
            try:
                out = method(self.players[pid], self.current_round, ctx)
            except Exception as e:
                out = {"status": "error", "error": str(e)[:200]}
            if not isinstance(out, dict):
                out = {}
            self.logger.log("POST_GAME_REFLECTION", self.current_round, "post_game", data={
                "player_id": pid,
                "round": self.current_round,
                "survived": ctx["survived"],
                "elimination_reason": ctx["elimination_reason"],
                "elimination_round": ctx["elimination_round"],
                "final_cash": ctx["final_cash"],
                "final_rank": ctx["own_rank"],
                "model_id": getattr(getattr(agent, "model_info", None), "model_id", None),
                "status": out.get("status"),
                "emotion": out.get("emotion"),
                "key_insight": out.get("key_insight"),
                "self_assessment": out.get("self_assessment"),
                "biggest_revelation": out.get("biggest_revelation"),
                "best_player": out.get("best_player"),
                "most_deceptive_player": out.get("most_deceptive_player"),
                "changed_opinion": out.get("changed_opinion"),
                "comment": out.get("comment", ""),
                "comment_chars": out.get("chars", 0),
                "truncated": out.get("truncated", False),
                "salvaged": out.get("salvaged", []),
            })

    def _finalize(self) -> GameResult:
        """ゲーム終了処理"""
        result = GameResult(
            self.players, self.current_round,
            round_snapshots=self._round_snapshots,
            double_up_deposits=self.double_up_deposits,
        )

        end_data = {
            "survivors": [
                {"player_id": p.player_id, "cash": p.cash}
                for p in result.survivors
            ],
            "eliminated": [
                {"player_id": p.player_id, "reason": p.elimination_reason,
                 "round": p.elimination_round}
                for p in result.eliminated
            ],
            "completed": not (
                getattr(self, "_budget_abort_logged", False)
                or getattr(self, "_trial_stop_logged", False)
            ),
            "abort_reason": "llm_budget_blocked" if getattr(self, "_budget_abort_logged", False) else None,
            "stop_reason": "trial_stop_after_round" if getattr(self, "_trial_stop_logged", False) else None,
        }
        if self.cost_budget is not None:
            end_data.update(self.cost_budget.snapshot())
        self.logger.log("GAME_END", self.current_round, "end", data=end_data)

        return result

    def _visible_messages(self, for_player_id: str | None) -> list[dict]:
        """
        §8.2: DM本文は秘匿情報。当事者（送信者・宛先）以外にはメタデータのみ返す。

        for_player_id が None の場合（現状 commit/double_up フェイズの一部呼び出し）は
        誰とも一致しないため、全DMが安全側（redacted）に倒れる。
        broadcast・anonymous_broadcast は常に本文を含める（§8.1公開情報）。

        Cycle 4: anonymous_broadcast の送信者本人にだけ、self._anon_broadcast_owners
        （_round_messagesのindex→player_id）を参照して "is_mine": True を付与する。
        _god_transcript は一切参照しない。他プレイヤー・for_player_id=None（Bot/内部）
        向けコピーには is_mine キー自体を含めない（redactedと同じ「真のときだけ付ける」慣習）。
        """
        out: list[dict] = []
        for i, m in enumerate(self._round_messages):
            if m.get("type") == "dm" and for_player_id not in (m.get("sender"), m.get("to")):
                redacted = {k: v for k, v in m.items() if k != "message"}
                redacted["redacted"] = True
                out.append(redacted)
            elif (
                m.get("type") == "anonymous_broadcast"
                and for_player_id is not None
                and self._anon_broadcast_owners.get(i) == for_player_id
            ):
                mine = dict(m)
                mine["is_mine"] = True
                out.append(mine)
            else:
                out.append(dict(m))
        return out

    def _eliminated_parties(self, parties: list[str]) -> list[str]:
        """契約当事者のうち脱落済みの者を返す（contracts_public / my_contracts で共有）"""
        return [
            pid for pid in parties
            if pid in self.players and not self.players[pid].is_alive
        ]

    def _build_visible_state(self, round_num: int, for_player_id: str | None = None) -> dict:
        """
        公開情報の辞書を構築する（§8）

        エージェントに渡す情報。秘匿情報は含まない。
        for_player_id が指定された場合、そのプレイヤーが当事者である
        提案中（PROPOSED）契約を contracts_pending に含める（§6.4: 内容は当事者のみ）。
        DMの本文もfor_player_idが当事者の場合のみ含まれる（§8.2、_visible_messages参照）。
        """
        state = {
            "round_num": round_num,
            "markets": [
                {"market_id": m.market_id, "prize_pool": m.prize_pool,
                 "base_prize": m.base_prize, "carryover": m.carryover}
                for m in getattr(self, "_current_markets", [])
            ],
            "last_round_results": self._last_round_results,
            # A-4: 前ラウンドにAUTO COMMITが発生したplayer_idのリスト（公開情報）。
            # RULES_SUMMARY(L506)は「AUTO COMMIT発生」を既に公開情報と明記しており、
            # 新たな秘匿情報の追加ではない。理由・requestedはここに含めない。
            "last_round_auto_players": sorted({
                c["player_id"]
                for mr in (self._last_round_results or {}).get("markets", [])
                for c in mr.get("commits", [])
                if c.get("auto")
            }),
            "alive_players": [
                pid for pid, p in self.players.items() if p.is_alive
            ],
            # §8.1公開情報「脱落者と理由種別」。RULES_SUMMARY(L240)は公示を約束して
            # いるが、これを届けるプロンプトが1つも無かった（仕様と実装の乖離）。
            # 結果、脱落を知らずに書かれた引き継ぎメモが毎R再注入されDM不成立ループを
            # 起こした（trial_C_l12_r12_20260822 の P06）。
            "eliminated_players": [
                {"player_id": pid, "round": p.elimination_round,
                 "reason": p.elimination_reason}
                for pid, p in sorted(self.players.items()) if not p.is_alive
            ],
            "initial_loans": {
                pid: p.initial_loan for pid, p in self.players.items()
            },
            "used_cards": {
                pid: [
                    "FOG" if c.card_id in self._fog_card_ids else c.card_id
                    for c in p.used_cards
                ]
                for pid, p in self.players.items()
            },
            "contracts_public": [
                {
                    "contract_id": c.contract_id,
                    "parties": c.parties,
                    "status": c.status.value,
                    # 表示用の派生情報のみ。脱落者が当事者の未履行義務は
                    # engine/elimination.py:expire_obligations_for_player() で失効済みだが
                    # status は active のままなので、素で出すと「死んだ相手との契約が
                    # まだ生きている」と誤読される（D4）。
                    "eliminated_parties": self._eliminated_parties(c.parties),
                    # 全当事者合意による解除（§6）の履歴。契約ID・当事者・statusは
                    # 元々全員に公開されている情報のため、解除ラウンドの追加公開は
                    # 新たな秘匿情報の漏洩ではない（terms/outcomesは引き続きgod限定）。
                    "cancelled_round": c.cancelled_round,
                }
                for c in self.contracts
                # v0.8 D5/I1: expired（署名不成立で失効）・closed（残存義務ゼロで畳んだ
                # ゾンビ契約）も生のstatus文字列のまま公示に含める。
                if c.status.value in ("active", "completed", "cancelled", "expired", "closed")
            ],
            "bounties_public": [
                {"bounty_id": b.bounty_id, "amount": b.amount,
                 "condition_type": b.condition_type.value,
                 "condition": b.condition,
                 "poster": None if b.anonymous else b.poster,
                 "is_active": b.is_active}
                for b in self.bounties
            ],
            "messages": self._visible_messages(for_player_id),
            "double_ups": [
                {"player_id": d.player_id, "deposit": d.deposit_amount,
                 "success_round": d.success_round}
                for d in self.double_up_deposits if not d.resolved
            ],
        }

        # 当事者向け: 提案中の契約（当事者のみ閲覧可能）
        if for_player_id is not None:
            state["contracts_pending"] = [
                {
                    "contract_id": c.contract_id,
                    "proposer": c.proposer,
                    "parties": c.parties,
                    "signed_by": list(c.signed_by),
                    "round_created": c.round_created,
                    "obligations": [
                        {
                            "obligor": ob.obligor,
                            "counterparty": ob.counterparty,
                            "ob_type": ob.ob_type.value,
                            "round_num": ob.round_num,
                            "details": dict(ob.details),
                        }
                        for ob in c.obligations
                    ],
                }
                for c in self.contracts
                if c.status == ContractStatus.PROPOSED
                and for_player_id in c.parties
            ]

            # 当事者向け: 自分が当事者である成立済み契約の全容（毎ラウンド再提示用）
            # LLMは1-shot呼出しで会話履歴を持たず、契約の中身を保持する手段が
            # 自由記述メモしかない。my_obligations は「今R以降に自分が履行すべき
            # TODO」であり (a) 受益者側(counterparty)には何も出ない (b) 過去Rの
            # 義務は消える。そのため「自分の契約が何だったか」を照会する経路が
            # 存在せず、メモが劣化すると「activeだが内容不明の契約」に化けた
            # （実run trial_C_l12_r12_20260822 の P03/C_90159021）。
            # 秘匿性: contracts_pending と同じく for_player_id in c.parties で制限。
            # engine判定ロジックには影響しない — プロンプトへの情報提示のみ。
            #
            # ACTIVE に加え CANCELLED も含める（§6・全当事者合意による契約解除）。
            # 解除済み契約を落とすと「解除できたのか自分では確認できない」状態になる
            # （authoritative ledgerから確認できることが要件）。解除済み契約の義務は
            # ob_status を "cancelled" で一律上書きする（違反判定には影響しない・表示専用）。
            state["my_contracts"] = [
                {
                    "contract_id": c.contract_id,
                    "parties": list(c.parties),
                    "round_created": c.round_created,
                    "status": c.status.value,
                    "eliminated_parties": self._eliminated_parties(c.parties),
                    "cancelled_round": c.cancelled_round,
                    "cancel_requested_by": list(c.cancel_requested_by),
                    "obligations": [
                        {
                            "obligation_id": ob.obligation_id,
                            "obligor": ob.obligor,
                            "counterparty": ob.counterparty,
                            "ob_type": ob.ob_type.value,
                            "round_num": ob.round_num,
                            "details": dict(ob.details),
                            "ob_status": (
                                "cancelled" if c.status == ContractStatus.CANCELLED
                                else _obligation_status(ob, round_num)
                            ),
                        }
                        for ob in c.obligations
                    ],
                }
                for c in self.contracts
                if c.status in (ContractStatus.ACTIVE, ContractStatus.CANCELLED)
                and for_player_id in c.parties
            ]

            # A-4: 自分の過去AUTO COMMIT履歴（round/requested/reason/actual）。秘匿情報。
            # 「自分が何を指定してAUTOになったか」を機械的に確認できる経路が存在せず、
            # 同じ取り違え（例: 手札に無いカードを指定）を翌ラウンドも繰り返す事故が
            # 確認された（trial_C_l12_r12_20260822 の P09 R3→R4）ための追加。
            state["my_auto_commits"] = list(
                self._auto_commit_history.get(for_player_id, []),
            )

            # 当事者向け: 署名済み契約の未履行義務一覧（自分が obligor のもの）
            # 帳簿ミス起因の契約違反脱落を防ぐための情報提示（§7.3の思想を契約に適用）
            # engine判定ロジックには影響しない — プロンプトへの情報提示のみ
            state["my_obligations"] = [
                {
                    "contract_id": c.contract_id,
                    "obligor": ob.obligor,
                    "counterparty": ob.counterparty,
                    "ob_type": ob.ob_type.value,
                    "round_num": ob.round_num,
                    "details": dict(ob.details),
                }
                for c in self.contracts
                if c.status == ContractStatus.ACTIVE
                for ob in c.obligations
                if ob.obligor == for_player_id
                and not ob.is_fulfilled
                and not ob.is_expired
                and ob.round_num >= round_num  # 過去ラウンドの義務は除外
            ]

            # 当事者向け: 当該ラウンドで不成立になった自分のアクション（私的情報）
            # §2.5により不成立でも枠を消費するため、理由が返らないと同じ失敗の反復で
            # アクション枠を自滅的に使い切る。engine判定には一切影響しない。
            state["my_failed_actions"] = [
                dict(f) for f in self._action_failures.get(for_player_id, [])
            ]

            # 当事者向け: 自分が当事者の契約に起きた解除関連の状態変化（私的情報）。
            # contract_cancel はメッセージを生成しないため、相手が読める手段が
            # これしか無い。AUTO_PASS_ON_NO_NEWS の第3の起床トリガとして
            # llm/llm_agent.py が件数の増分を見る。engine判定には影響しない。
            state["my_contract_notices"] = [
                dict(n) for n in self._contract_notices.get(for_player_id, [])
            ]
            state["my_action_budget"] = {
                "used": self._action_counts.get(for_player_id, 0),
                "max": self.config.negotiation_max_actions,
            }

            # カードトレード提案（当事者のみ可視, v0.7.1: 受信者に他宛先非公開）
            pending_trades = []
            for tp in self.trade_proposals:
                if tp.status != CardTradeStatus.PROPOSED:
                    continue
                if for_player_id not in (tp.proposer, tp.with_player):
                    continue
                entry: dict = {
                    "trade_id": tp.trade_id,
                    "proposer": tp.proposer,
                    "with_player": tp.with_player,
                    "give_card_rank": tp.give_card_rank,
                    "receive_card_rank": tp.receive_card_rank,
                    "cash_amount": tp.cash_amount,
                    "round_proposed": tp.round_proposed,
                }
                # 提案者にのみ offer_id・全宛先・各宛先ステータスを公開
                if for_player_id == tp.proposer and tp.offer_id:
                    entry["offer_id"] = tp.offer_id
                    entry["all_targets"] = [
                        t.with_player for t in self.trade_proposals
                        if t.offer_id == tp.offer_id
                    ]
                    entry["target_statuses"] = {
                        t.with_player: t.status.value
                        for t in self.trade_proposals
                        if t.offer_id == tp.offer_id
                    }
                pending_trades.append(entry)
            state["trades_pending"] = pending_trades

        return state
