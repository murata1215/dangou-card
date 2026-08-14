"""
アクション検証・実行モジュール

§9.4に基づく全11種アクションの検証(validate)と実行(execute)を提供する。
各アクションの不成立条件と副作用を厳密に管理する。
"""

from typing import Any

from engine.models import (
    Action, DmAction, BroadcastAction, MarketCommitAction,
    ContractProposeAction, ContractSignAction, AnonymousBroadcastAction,
    BountyPostAction, BountyCancelAction, TransferAction, RepayAction,
    PassAction, CardTradeProposeAction, CardTradeAcceptAction, CardTradeRejectAction,
    PlayerState, CardRank,
)
from engine.config import GameConfig


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
        if action.amount > player.free_cash:
            return ActionResult(False, "Insufficient free cash for transfer")
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
        return ActionResult(True)

    if isinstance(action, ContractSignAction):
        # 契約署名: 特別な資金チェックなし（署名のみ）
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
        if deposited > player.free_cash:
            return ActionResult(False, "Insufficient free cash for bounty deposit")
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
        if action.cash_amount > 0 and action.cash_amount > player.free_cash:
            return ActionResult(False, "Insufficient free cash for trade payment")
        return ActionResult(True)

    if isinstance(action, CardTradeAcceptAction):
        # カードトレード受諾: trade_id の存在チェックは execute 側で行う
        return ActionResult(True)

    if isinstance(action, CardTradeRejectAction):
        # カードトレード拒否: 回数枠を消費しない
        return ActionResult(True, consumes_action=False)

    return ActionResult(False, "Unknown action type")
