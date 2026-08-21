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
    CardRank, Card, Action, PassAction, TransferAction, RepayAction,
    ContractProposeAction, ContractSignAction, AnonymousBroadcastAction,
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
        self.current_round: int = 0
        self.pending_repayments: dict[str, int] = {}  # Negotiation中のrepay記録

        # 各プレイヤーのNegotiation状態
        self._action_counts: dict[str, int] = {}
        self._anon_broadcast_counts: dict[str, int] = {}
        self._round_messages: list[dict] = []  # ラウンド内のメッセージ履歴（Bot交渉用）

        # S2: 霧のラウンドで使用されたカードID集合
        self._fog_card_ids: set[str] = set()

        # S2: 倍掛け預託リスト
        self.double_up_deposits: list[DoubleUpDeposit] = []

        # S2: ラウンドスナップショット（指標収集用）
        self._round_snapshots: list[dict] = []

        # S2: カードトレード提案リスト（v0.7 §3）
        self.trade_proposals: list[CardTradeProposal] = []
        self._trade_counts: dict[str, int] = {}  # ラウンド内トレード回数

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
            # AFTER分析で「各ラウンド終了時の生存者数」を推測せず取得できるよう、
            # すべての通常ゲームで軽量な集計イベントを残す。
            self._log_round_complete(round_num)
            if self._stop_requested_after_round(round_num):
                return self._finalize()

        return self._finalize()

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
        self._round_messages = []  # ラウンド開始時にクリア

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
                )

                if isinstance(action, PassAction):
                    # passは常に成功、枠を消費しない
                    self.logger.log("NEGOTIATION_ACTION", round_num, "negotiation", data={
                        "player_id": pid, "action": "pass", "turn": turn,
                    })
                    continue

                all_passed = False

                if not result.success:
                    # アクション不成立（任意支払い不足等）
                    # 枠は原則消費（§2.5）
                    if result.consumes_action:
                        self._action_counts[pid] = self._action_counts.get(pid, 0) + 1
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

        alive_ids = [pid for pid, p in self.players.items() if p.is_alive]

        for pid in alive_ids:
            p = self.players[pid]

            # Entry Fee支払い（§4.2: 必須支払い）
            if not player_ops.can_pay(p, self.config.entry_fee):
                # 破産→即時脱落（§1.2, §4.4）
                p, self.contracts, record = elim_ops.forced_liquidation(
                    p, "bankruptcy", round_num, self.contracts,
                )
                self.players[pid] = p
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
            valid_commit = False
            if commit_action is not None:
                result = action_ops.validate_action(
                    commit_action, p, self.config, self.players, round_num,
                )
                valid_commit = result.success

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

            if selected is None:
                # 合法Commit 0件 → 履行不能 → 契約違反 → 即時脱落
                # Entry Feeは既に支払済みなので返金しない
                p, self.contracts, record = elim_ops.forced_liquidation(
                    p, "contract_violation", round_num, self.contracts,
                )
                self.players[pid] = p
                self.logger.log("AUTO_COMMIT_FAILURE", round_num, "commit", data={
                    "player_id": pid,
                    "reason": "No legal commits available (contradictory contracts)",
                    **record,
                })
            else:
                self._current_commits.append(selected)
                self.logger.log("COMMIT", round_num, "commit", data={
                    "player_id": pid,
                    "market_id": selected.market_id,
                    "card": selected.card.card_id,
                    "auto": True,
                })
                # AUTO COMMIT公示（§4.4）
                self.logger.log("AUTO_COMMIT", round_num, "commit", data={
                    "player_id": pid,
                    "message": f"{pid}: AUTO COMMIT",
                })

    def _phase_settlement(self, round_num: int) -> None:
        """Phase 4: Reveal & Settlement（§5.2）+ S2倍掛け処理"""
        (
            self.players,
            self.contracts,
            self.bounties,
            new_carryovers,
            market_results,
        ) = settlement_ops.execute_settlement(
            self.players,
            self._current_markets,
            self._current_commits,
            self.contracts,
            self.bounties,
            round_num,
            self.config,
            self.logger,
        )

        # S2: 霧のラウンドで使用されたカードを記録
        if round_num in self.config.fog_rounds:
            for c in self._current_commits:
                self._fog_card_ids.add(c.card.card_id)

        # S2: 倍掛け処理
        du_success = 0
        du_fail = 0
        du_solo_success = 0
        if self.config.double_up_enabled:
            du_success, du_fail, du_solo_success = self._process_double_up(
                round_num, market_results,
            )

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
            "double_up_solo_success": du_solo_success,
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
    ) -> tuple[int, int, int]:
        """
        S2: 倍掛け処理

        1. 前ラウンドの預託を解決（今ラウンドの市場結果で判定）
        2. 今ラウンドの勝者に倍掛け選択を提示（R12以外）

        Returns:
            (成功数, 失敗数, 空き巣成功数)
        """
        du_success = 0
        du_fail = 0
        du_solo_success = 0

        # 今ラウンドの市場勝者を特定（player_id → 獲得額マップ）
        round_winners: dict[str, int] = {}
        # ソロ市場を除外した勝者マップ（倍掛け成功判定用, §6.2）
        non_solo_winners: dict[str, int] = {}
        # 空き巣市場かどうかのマップ（market_id → participants==1）
        solo_markets: set[str] = set()
        # 勝者がどの市場で勝ったか
        winner_markets: dict[str, list[str]] = {}

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
                if winner_id not in winner_markets:
                    winner_markets[winner_id] = []
                winner_markets[winner_id].append(mr.market_id)

        # Step 1: 前ラウンドの預託を解決
        for dep in self.double_up_deposits:
            if dep.resolved or dep.success_round != round_num:
                continue
            dep.resolved = True
            p = self.players[dep.player_id]
            if not p.is_alive:
                # 脱落済みなら没収
                self.logger.log("DOUBLE_UP_RESOLVED", round_num, "settlement", data={
                    "player_id": dep.player_id, "result": "forfeit_eliminated",
                    "deposit": dep.deposit_amount,
                })
                du_fail += 1
                continue

            if dep.player_id in non_solo_winners and non_solo_winners[dep.player_id] > 0:
                # 成功: 2倍払い出し
                payout = dep.deposit_amount * 2
                dep.success = True
                # 空き巣チェック: 成功市場が全て空き巣だったか
                won_markets = winner_markets.get(dep.player_id, [])
                all_solo = all(m in solo_markets for m in won_markets)
                dep.from_solo_market = all_solo
                if all_solo:
                    du_solo_success += 1

                p = player_ops.receive(p, payout)
                self.players[dep.player_id] = p
                self.logger.log("DOUBLE_UP_RESOLVED", round_num, "settlement", data={
                    "player_id": dep.player_id, "result": "success",
                    "deposit": dep.deposit_amount, "payout": payout,
                    "from_solo_market": all_solo,
                })
                du_success += 1
            else:
                # 失敗: 没収
                self.logger.log("DOUBLE_UP_RESOLVED", round_num, "settlement", data={
                    "player_id": dep.player_id, "result": "forfeit",
                    "deposit": dep.deposit_amount,
                })
                du_fail += 1

        # Step 2: 今ラウンドの勝者に倍掛け選択を提示（R12以外、R11が最後）
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

        return du_success, du_fail, du_solo_success

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
        """
        out: list[dict] = []
        for m in self._round_messages:
            if m.get("type") == "dm" and for_player_id not in (m.get("sender"), m.get("to")):
                redacted = {k: v for k, v in m.items() if k != "message"}
                redacted["redacted"] = True
                out.append(redacted)
            else:
                out.append(dict(m))
        return out

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
            "alive_players": [
                pid for pid, p in self.players.items() if p.is_alive
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
                {"contract_id": c.contract_id, "parties": c.parties,
                 "status": c.status.value}
                for c in self.contracts
                if c.status.value in ("active", "completed")
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
