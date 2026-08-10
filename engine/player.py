"""
プレイヤー状態管理モジュール

§2に基づくプレイヤーの資金管理（Cash/Debt/FreeCash）、
手札管理、生存状態管理を提供する。
"""

import math

from engine.models import Card, CardRank, PlayerState
from engine.cards import create_deck


def create_player(player_id: str, loan_amount: int) -> PlayerState:
    """
    新規プレイヤーを作成する（§2.2）

    初期配布:
    - 現金 = 借入額
    - 借金残高 = 借入額
    - 手札 = 12枚のデッキ

    Args:
        player_id: プレイヤーID（例: "P01"）
        loan_amount: 借入額（120万〜1000万）

    Returns:
        初期化されたPlayerState
    """
    return PlayerState(
        player_id=player_id,
        cash=loan_amount,
        debt_balance=loan_amount,
        initial_loan=loan_amount,
        hand=create_deck(),
    )


def can_pay(player: PlayerState, amount: int) -> bool:
    """
    現金が足りるか判定（Cash ≥ amount）

    Entry Fee等のシステム向け支払い判定に使用。
    FreeCash制限は適用しない。

    Args:
        player: プレイヤー状態
        amount: 必要額
    """
    return player.cash >= amount


def can_pay_free_cash(player: PlayerState, amount: int) -> bool:
    """
    FreeCash以内で支払えるか判定（§2.4）

    送金・金銭契約・報奨預託等のプレイヤー間価値移転に適用。

    Args:
        player: プレイヤー状態
        amount: 必要額
    """
    return player.free_cash >= amount


def pay(player: PlayerState, amount: int) -> PlayerState:
    """
    現金を支払う（Cashを減少）

    事前に can_pay / can_pay_free_cash で検証済みであること。

    Args:
        player: プレイヤー状態
        amount: 支払額

    Returns:
        更新されたPlayerState
    """
    return player.model_copy(update={"cash": player.cash - amount})


def receive(player: PlayerState, amount: int) -> PlayerState:
    """
    現金を受け取る（Cashを増加）

    Args:
        player: プレイヤー状態
        amount: 受取額

    Returns:
        更新されたPlayerState
    """
    return player.model_copy(update={"cash": player.cash + amount})


def use_card(player: PlayerState, card: Card) -> PlayerState:
    """
    手札からカードを使用する（§3.2）

    使用したカードは hand から removed、used_cards に追加される。
    カードは消滅扱い（他AIへ移転不可）。

    Args:
        player: プレイヤー状態
        card: 使用するカード

    Returns:
        更新されたPlayerState

    Raises:
        ValueError: 指定カードが手札にない場合
    """
    new_hand = [c for c in player.hand if c.card_id != card.card_id]
    if len(new_hand) == len(player.hand):
        raise ValueError(f"{player.player_id} does not have card {card.card_id}")
    new_used = list(player.used_cards) + [card]
    return player.model_copy(update={"hand": new_hand, "used_cards": new_used})


def has_card_rank(player: PlayerState, rank: CardRank) -> bool:
    """
    手札に指定ランクのカードがあるか判定

    Args:
        player: プレイヤー状態
        rank: 探すカードランク
    """
    return any(c.rank == rank for c in player.hand)


def apply_interest(player: PlayerState, rate: float) -> PlayerState:
    """
    利息を計上する（§2.3）

    毎ラウンドのFinanceフェイズで借金残高に複利を計上。
    端数は切り上げ（保守的解釈: 借り手に不利な方向）。

    Args:
        player: プレイヤー状態
        rate: 利率（例: 0.015 = 1.5%）

    Returns:
        利息計上後のPlayerState
    """
    interest = math.ceil(player.debt_balance * rate)
    new_debt = player.debt_balance + interest
    return player.model_copy(update={"debt_balance": new_debt})


def repay_debt(player: PlayerState, amount: int) -> PlayerState:
    """
    借金を返済する（§2.3）

    返済額はmin(指定額, 現金残高, 借金残高)に調整される。
    FreeCash制限は非適用（システム向け支払い、§2.4）。

    Args:
        player: プレイヤー状態
        amount: 返済希望額

    Returns:
        返済後のPlayerState
    """
    actual = min(amount, player.cash, player.debt_balance)
    return player.model_copy(update={
        "cash": player.cash - actual,
        "debt_balance": player.debt_balance - actual,
    })


def eliminate(
    player: PlayerState,
    reason: str,
    round_num: int,
) -> PlayerState:
    """
    プレイヤーを脱落させる

    Args:
        player: プレイヤー状態
        reason: 脱落理由（"contract_violation" / "bankruptcy" / "condition_not_met"）
        round_num: 脱落ラウンド

    Returns:
        脱落後のPlayerState
    """
    return player.model_copy(update={
        "is_alive": False,
        "elimination_reason": reason,
        "elimination_round": round_num,
    })
