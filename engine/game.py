"""
メインゲームループモジュール

5フェイズ進行のオーケストレータ。
Market Open → Negotiation → Commit → Settlement → Finance
の順で12ラウンドを実行する。
"""

from typing import Any

from engine.config import GameConfig
from engine.models import (
    PlayerState, Market, MarketCommit, Contract, Bounty,
    CardRank, Card, Action, PassAction, TransferAction, RepayAction,
    ContractProposeAction, ContractSignAction, AnonymousBroadcastAction,
    BountyPostAction, BountyCancelAction, MarketCommitAction,
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
    ):
        self.players = players
        self.round_count = round_count

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

        # ゲーム状態
        self.players: dict[str, PlayerState] = {}
        self.contracts: list[Contract] = []
        self.bounties: list[Bounty] = []
        self.carryovers: dict[str, int] = {}  # 市場ID → キャリーオーバー額
        self.current_round: int = 0
        self.pending_repayments: dict[str, int] = {}  # Negotiation中のrepay記録

        # 各プレイヤーのNegotiation状態
        self._action_counts: dict[str, int] = {}
        self._anon_broadcast_counts: dict[str, int] = {}
        self._round_messages: list[dict] = []  # ラウンド内のメッセージ履歴（Bot交渉用）

    def run(self) -> GameResult:
        """
        ゲームを実行する

        12ラウンドを順に処理し、最終結果を返す。

        Returns:
            ゲーム結果
        """
        self._setup()

        for round_num in range(1, self.config.num_rounds + 1):
            self.current_round = round_num

            # 生存者がいなければ早期終了
            alive_count = sum(1 for p in self.players.values() if p.is_alive)
            if alive_count == 0:
                break

            self._phase_market_open(round_num)
            self._phase_negotiation(round_num)
            self._phase_commit(round_num)
            self._phase_settlement(round_num)
            self._phase_finance(round_num)

        return self._finalize()

    def _setup(self) -> None:
        """
        ゲームセットアップ（§2）

        各エージェントに借入額を選択させ、初期状態を生成する。
        """
        self.logger.log("GAME_START", 0, "setup", data={
            "config": {
                "num_players": self.config.num_players,
                "num_rounds": self.config.num_rounds,
                "total_prize": self.config.total_prize,
                "survival_cash": self.config.survival_cash,
                "interest_rate": self.config.interest_rate,
                "entry_fee": self.config.entry_fee,
            },
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
                visible_state = self._build_visible_state(round_num)

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
            # 匿名通信
            p = player_ops.pay(p, self.config.anon_broadcast_fee)
            self.players[pid] = p
            self._anon_broadcast_counts[pid] = self._anon_broadcast_counts.get(pid, 0) + 1
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
            visible_state = self._build_visible_state(round_num)

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
        """Phase 4: Reveal & Settlement（§5.2）"""
        (
            self.players,
            self.contracts,
            self.bounties,
            new_carryovers,
            _market_results,
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

        # キャリーオーバー更新
        if round_num < self.config.num_rounds:
            # R12以外: キャリーオーバーを次ラウンドへ
            self.carryovers = new_carryovers
        else:
            # R12: キャリーオーバーは消滅（§4.7）
            self.carryovers = {}

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

    def _finalize(self) -> GameResult:
        """ゲーム終了処理"""
        result = GameResult(self.players, self.current_round)

        self.logger.log("GAME_END", self.current_round, "end", data={
            "survivors": [
                {"player_id": p.player_id, "cash": p.cash}
                for p in result.survivors
            ],
            "eliminated": [
                {"player_id": p.player_id, "reason": p.elimination_reason,
                 "round": p.elimination_round}
                for p in result.eliminated
            ],
        })

        return result

    def _build_visible_state(self, round_num: int) -> dict:
        """
        公開情報の辞書を構築する（§8）

        エージェントに渡す情報。秘匿情報は含まない。
        """
        return {
            "round_num": round_num,
            "markets": [
                {"market_id": m.market_id, "prize_pool": m.prize_pool}
                for m in getattr(self, "_current_markets", [])
            ],
            "alive_players": [
                pid for pid, p in self.players.items() if p.is_alive
            ],
            "initial_loans": {
                pid: p.initial_loan for pid, p in self.players.items()
            },
            "used_cards": {
                pid: [c.card_id for c in p.used_cards]
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
            "messages": list(self._round_messages),
        }
