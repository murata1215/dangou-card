"""
アクション検証・実行モジュール

§9.4に基づく全11種アクションの検証(validate)と実行(execute)を提供する。
各アクションの不成立条件と副作用を厳密に管理する。
"""

from typing import Any

from engine.models import (
    Action, DmAction, BroadcastAction, MarketCommitAction,
    ContractProposeAction, ContractSignAction, ContractCancelAction,
    AnonymousBroadcastAction,
    BountyPostAction, BountyCancelAction, TransferAction, RepayAction,
    PassAction, CardTradeProposeAction, CardTradeAcceptAction, CardTradeRejectAction,
    CardTradeProposal, CardTradeStatus,
    PlayerState, CardRank, Contract, ContractStatus, ObligationType,
)
from engine.config import GameConfig
from engine.contracts import (
    can_cancel_contract, normalize_type_b_card_details, validate_type_b_card_details,
    is_card_tradable,
)
from engine import player as player_ops


class ActionResult:
    """
    アクション実行結果

    success: 成功したか
    reason: 失敗理由（成功時はNone）
    consumes_action: アクション枠を消費するか（passは消費しない）
    """

    def __init__(
        self,
        success: bool,
        reason: str | None = None,
        consumes_action: bool = True,
    ):
        self.success = success
        self.reason = reason
        self.consumes_action = consumes_action


def validate_action(
    action: Action,
    player: PlayerState,
    config: GameConfig,
    players: dict[str, PlayerState],
    round_num: int,
    anon_broadcast_count: int = 0,
    contracts: list[Contract] | None = None,
    trade_counts: dict[str, int] | None = None,
    trade_proposals: list[CardTradeProposal] | None = None,
) -> ActionResult:
    """
    アクションのバリデーションを行う

    実行前に呼び出し、成功でなければアクション不成立。
    §2.5に従い、任意支払い不足は脱落ではなくアクション不成立。

    Args:
        action: 検証するアクション
        player: アクション実行者の状態
        config: ゲーム設定
        players: 全プレイヤーの状態辞書
        round_num: 現在のラウンド番号
        anon_broadcast_count: 当該ラウンドの匿名通信使用回数
        contracts: 全契約リスト（ContractCancelActionの検証、および
            CardTradeProposeAction/CardTradeAcceptActionのE8検証に使用。
            省略時（None/空）は contract_cancel が常に不成立になり、
            E8検証はスキップされる。既存の呼び出し元との互換のため
            キーワード引数・デフォルトNoneとする）
        trade_counts: 当該ラウンドの「成立済み」トレード回数
            {player_id: 件数}（v0.8 E7）。Noneなら回数枠検証をスキップする
            （既存の呼び出し元との互換のためキーワード引数・デフォルトNone）
        trade_proposals: 全トレード提案リスト（v0.8 E7/E8。
            CardTradeAcceptActionの検証で対象提案を引くために使用）。
            Noneなら提案者側の検証をスキップする

    Returns:
        ActionResult（成功/失敗+理由）
    """
    if not player.is_alive:
        return ActionResult(False, "Player is eliminated")

    if isinstance(action, PassAction):
        # passは常に成功、アクション枠非消費（§5.1）
        return ActionResult(True, consumes_action=False)

    if isinstance(action, DmAction):
        # DM: 相手が生存していること
        target = players.get(action.to)
        if target is None or not target.is_alive:
            return ActionResult(False, f"Target {action.to} is not alive")
        return ActionResult(True)

    if isinstance(action, BroadcastAction):
        # 全体発言: 制約なし
        return ActionResult(True)

    if isinstance(action, TransferAction):
        # 送金: amount <= FreeCash かつ相手が生存（§9.4）
        if action.amount <= 0:
            return ActionResult(False, "Transfer amount must be positive")
        target = players.get(action.to)
        if target is None or not target.is_alive:
            return ActionResult(False, f"Target {action.to} is not alive")
        if action.amount > player_ops.spendable_cash(player, config, at_settlement=False):
            return ActionResult(False, player_ops.insufficient_funds_reason(config, "transfer"))
        return ActionResult(True)

    if isinstance(action, RepayAction):
        # 借金返済: FreeCash非適用（§2.4）、Cash以内かつDebt以内
        if action.amount <= 0:
            return ActionResult(False, "Repay amount must be positive")
        if action.amount > player.cash:
            return ActionResult(False, "Insufficient cash for repayment")
        if action.amount > player.debt_balance:
            return ActionResult(False, "Repay amount exceeds debt balance")
        return ActionResult(True)

    if isinstance(action, ContractProposeAction):
        # 契約提案: 発行料10万を支払えること（任意支払い、§6.1）
        # 発行料はFreeCash非適用（システム向け支払い、§2.4）
        if player.cash < config.contract_fee:
            return ActionResult(False, "Insufficient cash for contract fee")
        # 相手方は全員生存していること。脱落者は署名できず§6.3で当該義務は失効するため、
        # 成立しえない契約に発行料10万を払わせない。
        # NOTE: 資金チェックより後に置くこと（test_acceptance.py::test_15 が
        #       相手をplayers dictに含めずに "insufficient cash" を期待する）。
        for target_pid in action.with_players:
            if target_pid == action.player_id:
                continue
            target = players.get(target_pid)
            if target is None or not target.is_alive:
                return ActionResult(False, f"Counterparty {target_pid} is not alive")

        # v0.8 D6: terms検証。silent脱落トラップ（実行時例外での不成立）を避け、
        # ここでActionResult(False, reason)として明示的に不成立を返す。
        if not action.with_players:
            return ActionResult(False, "with_players must not be empty")
        if action.player_id in action.with_players:
            return ActionResult(False, "with_players must not include the proposer")
        if len(set(action.with_players)) != len(action.with_players):
            return ActionResult(False, "with_players must not contain duplicates")

        parties = {action.player_id, *action.with_players}
        valid_market_ids = {f"M{i+1:02d}" for i in range(config.num_markets)}

        if not action.terms:
            return ActionResult(False, "terms must not be empty")

        for term in action.terms:
            obligor = term.get("obligor")
            counterparty = term.get("counterparty")
            ob_type = term.get("ob_type")
            term_round = term.get("round_num")
            details = dict(term.get("details", {}))

            if obligor not in parties:
                return ActionResult(False, f"obligor {obligor!r} is not a party of this contract")
            if counterparty not in parties:
                return ActionResult(
                    False, f"counterparty {counterparty!r} is not a party of this contract",
                )
            if obligor == counterparty:
                return ActionResult(False, "obligor and counterparty must differ")

            if not isinstance(term_round, int) or isinstance(term_round, bool):
                return ActionResult(False, f"round_num must be an int, got {term_round!r}")
            if not (round_num <= term_round <= config.num_rounds):
                return ActionResult(
                    False,
                    f"round_num {term_round} is out of range "
                    f"({round_num}..{config.num_rounds})",
                )

            if ob_type == ObligationType.TYPE_A_PAYMENT.value:
                amount = details.get("amount")
                if not isinstance(amount, int) or isinstance(amount, bool) or amount <= 0:
                    return ActionResult(
                        False, f"type_a_payment amount must be a positive int, got {amount!r}",
                    )
            elif ob_type in (
                ObligationType.TYPE_B_MARKET.value, ObligationType.TYPE_B_NO_MARKET.value,
            ):
                market_id = details.get("market_id")
                if market_id not in valid_market_ids:
                    return ActionResult(
                        False, f"Invalid market_id: {market_id!r} (valid: {sorted(valid_market_ids)})",
                    )
            elif ob_type == ObligationType.TYPE_B_CARD.value:
                details = normalize_type_b_card_details(details)
                error = validate_type_b_card_details(details)
                if error is not None:
                    return ActionResult(False, error)
            else:
                return ActionResult(False, f"Unknown ob_type: {ob_type!r}")

        return ActionResult(True)

    if isinstance(action, ContractSignAction):
        # 契約署名: 特別な資金チェックなし（署名のみ）
        return ActionResult(True)

    if isinstance(action, ContractCancelAction):
        # 契約解除（全当事者合意・§6）: 対象契約の存在・当事者性・解除可否を
        # ここで検証する。propose時と最終同意時の両方でこの関数が呼ばれるため、
        # 「解除成立の瞬間に一部履行済みだった」場合の再チェックもここで自然に効く。
        # 手数料なし・特別な資金チェックなし（新たな証書を発行しないため）。
        target: Contract | None = None
        for c in (contracts or []):
            if c.contract_id == action.contract_id:
                target = c
                break
        if target is None:
            return ActionResult(False, f"Contract {action.contract_id} not found")
        if action.player_id not in target.parties:
            return ActionResult(False, f"You are not a party of {action.contract_id}")
        if target.status == ContractStatus.CANCELLED:
            return ActionResult(False, f"Contract {action.contract_id} is already cancelled")
        ok, reason = can_cancel_contract(target, round_num)
        if not ok:
            return ActionResult(False, reason)
        if action.player_id in target.cancel_requested_by:
            return ActionResult(False, f"You already requested cancel of {action.contract_id}")
        return ActionResult(True)

    if isinstance(action, AnonymousBroadcastAction):
        # 匿名通信: 10万円支払可 + 上限2通/R（§7.1、任意支払い）
        if player.cash < config.anon_broadcast_fee:
            return ActionResult(False, "Insufficient cash for anonymous broadcast")
        if anon_broadcast_count >= config.anon_broadcast_limit:
            return ActionResult(False, "Anonymous broadcast limit reached")
        return ActionResult(True)

    if isinstance(action, BountyPostAction):
        # 達成者型(achievement)で PLAYER_ELIMINATED 条件は禁止（§7.2: 因果帰属が機械判定不能）
        if action.bounty_type == "achievement" and action.condition_type == "player_eliminated":
            return ActionResult(False, "Achievement bounty with player_eliminated condition is prohibited (§7.2)")
        # 報奨掲載: 預託+手数料 <= FreeCash（§7.2、任意支払い）
        import math
        deposited = action.amount
        if action.anonymous:
            deposited += math.ceil(action.amount * config.anon_bounty_surcharge)
        if deposited > player_ops.spendable_cash(player, config, at_settlement=False):
            return ActionResult(False, player_ops.insufficient_funds_reason(config, "bounty"))
        if action.amount <= 0:
            return ActionResult(False, "Bounty amount must be positive")
        return ActionResult(True)

    if isinstance(action, BountyCancelAction):
        # 報奨取り下げ: 掲載者本人チェックは呼び出し側で
        return ActionResult(True)

    if isinstance(action, MarketCommitAction):
        # 市場コミット: カードが手札にあること
        rank_name = action.card_rank
        try:
            rank = CardRank[rank_name]
        except KeyError:
            return ActionResult(False, f"Invalid card rank: {rank_name}")
        has = any(c.rank == rank for c in player.hand)
        if not has:
            return ActionResult(False, f"Card {rank_name} not in hand")
        return ActionResult(True)

    if isinstance(action, CardTradeProposeAction):
        # カードトレード提案: 有効化・カード所持・FreeCash・宛先数上限
        if not config.card_trade_enabled:
            return ActionResult(False, "Card trade is disabled")
        if not action.with_players:
            return ActionResult(False, "No target players specified")
        if len(action.with_players) > config.card_trade_broadcast_max:
            return ActionResult(False, f"Too many targets: {len(action.with_players)} > {config.card_trade_broadcast_max}")
        # v0.8 G1: 自分自身とはトレードできない（E7で提案時の枠消費が無くなり、
        # 自己受諾が到達可能になったための必須の防御）
        others = [t for t in action.with_players if t != player.player_id]
        if not others:
            return ActionResult(False, "Cannot trade with yourself")
        try:
            give_rank = CardRank[action.give_card]
        except KeyError:
            return ActionResult(False, f"Invalid card rank: {action.give_card}")
        if not any(c.rank == give_rank for c in player.hand):
            return ActionResult(False, f"Card {action.give_card} not in hand")
        try:
            CardRank[action.receive_card]
        except KeyError:
            return ActionResult(False, f"Invalid card rank: {action.receive_card}")
        if action.cash_amount > 0 and action.cash_amount > player_ops.spendable_cash(
            player, config, at_settlement=False
        ):
            return ActionResult(False, player_ops.insufficient_funds_reason(config, "trade"))
        # v0.8 E7: 「1R1回」は成立ベース。提案時点で今R成立済み回数のみ検査する
        # （提案は枠を消費しない。受諾で初めて双方の枠を消費する）
        if trade_counts is not None and trade_counts.get(player.player_id, 0) >= config.card_trade_max_per_round:
            return ActionResult(False, "今ラウンドのトレード回数上限")
        # v0.8 E8: 契約で使用予定のカードは手放せない
        if not is_card_tradable(contracts, player, action.give_card, round_num):
            return ActionResult(False, "契約で使用予定のカードはトレードできません")
        # 生存宛先が1人もいなければ不成立。1人でも生きていれば提案は成立させ、
        # 脱落宛先は engine/game.py の既存スキップに委ねる。
        if not any(
            (t := players.get(p)) is not None and t.is_alive
            for p in others
        ):
            return ActionResult(False, "No living target for card trade")
        return ActionResult(True)

    if isinstance(action, CardTradeAcceptAction):
        # カードトレード受諾: trade_id の存在チェックは execute 側で行う。
        # v0.8 E7/E8: 対象提案が判明し、かつ自分宛の場合のみ回数枠・拘束カードを
        # 検査する。見つからない/自分宛でない場合は従来どおり ActionResult(True) とし、
        # apply側のsilent return（不成立扱い）に委ねる（既存テスト互換）。
        proposal = None
        if trade_proposals:
            for tp in trade_proposals:
                if tp.trade_id == action.trade_id and tp.status == CardTradeStatus.PROPOSED:
                    proposal = tp
                    break
        if proposal is not None and proposal.with_player == player.player_id:
            if trade_counts is not None:
                if trade_counts.get(player.player_id, 0) >= config.card_trade_max_per_round:
                    return ActionResult(False, "今ラウンドのトレード回数上限")
                if trade_counts.get(proposal.proposer, 0) >= config.card_trade_max_per_round:
                    return ActionResult(False, "今ラウンドのトレード回数上限（提案者側）")
            # v0.8 E8: 受諾者が渡すカード・提案者が渡すカードの双方を再検証する
            # （提案後に当事者が新たに契約を結んで拘束された場合、受諾させると
            # 確定で型B違反→脱落となるため）
            if not is_card_tradable(contracts, player, proposal.receive_card_rank, round_num):
                return ActionResult(False, "契約で使用予定のカードはトレードできません")
            proposer_state = players.get(proposal.proposer)
            if proposer_state is not None and not is_card_tradable(
                contracts, proposer_state, proposal.give_card_rank, round_num,
            ):
                return ActionResult(False, "契約で使用予定のカードはトレードできません（提案者側）")
        return ActionResult(True)

    if isinstance(action, CardTradeRejectAction):
        # カードトレード拒否: 回数枠を消費しない
        return ActionResult(True, consumes_action=False)

    return ActionResult(False, "Unknown action type")
