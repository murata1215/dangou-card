"""
脱落・強制清算モジュール

§1.6に基づく脱落時の強制清算処理を提供する。
脱落理由の種別: 契約違反(contract_violation) / 破産(bankruptcy) / 条件未達(condition_not_met)
"""

from typing import Any

from engine.models import PlayerState, Contract, Obligation
from engine import player as player_ops


def forced_liquidation(
    p: PlayerState,
    reason: str,
    round_num: int,
    contracts: list[Contract],
) -> tuple[PlayerState, list[Contract], dict[str, Any]]:
    """
    脱落時強制清算を実行する（§1.6）

    処理順:
    1. 現金を借金返済へ自動充当: 返済額 = min(Cash, DebtBalance)
    2. 残債は貸倒れとして記録
    3. 返済後に残った現金はシステムへ没収（再分配しない）
    4. 未使用カードは消滅
    5. 未履行義務は義務単位で失効（§6.3）

    Args:
        p: 脱落するプレイヤーの状態
        reason: 脱落理由
        round_num: 脱落ラウンド
        contracts: 全契約リスト（義務失効を反映するため）

    Returns:
        (更新されたPlayerState, 更新された契約リスト, 清算記録dict)
    """
    # --- 清算記録の初期化 ---
    record: dict[str, Any] = {
        "player_id": p.player_id,
        "reason": reason,
        "round": round_num,
        "cash_before": p.cash,
        "debt_before": p.debt_balance,
    }

    # --- Step 1: 借金返済 ---
    repayment = min(p.cash, p.debt_balance)
    p = p.model_copy(update={
        "cash": p.cash - repayment,
        "debt_balance": p.debt_balance - repayment,
    })
    record["debt_repaid"] = repayment

    # --- Step 2: 貸倒れ ---
    record["bad_debt"] = p.debt_balance  # 返済しきれなかった残債

    # --- Step 3: 残金没収 ---
    record["cash_confiscated"] = p.cash
    p = p.model_copy(update={"cash": 0, "debt_balance": 0})

    # --- Step 4: カード消滅 ---
    record["cards_destroyed"] = len(p.hand)
    p = p.model_copy(update={"hand": []})

    # --- Step 5: 脱落状態にする ---
    p = player_ops.eliminate(p, reason, round_num)

    # --- Step 6: 義務失効（§6.3） ---
    updated_contracts = expire_obligations_for_player(p.player_id, contracts)

    return p, updated_contracts, record


def expire_obligations_for_player(
    player_id: str,
    contracts: list[Contract],
) -> list[Contract]:
    """
    脱落者に関連する未履行義務を失効させる（§6.3）

    - 脱落者が義務者(obligor)または相手方(counterparty)である未履行義務のみ失効
    - 生存者間の義務は継続
    - 「3者契約で生贄を脱落させて全体解除」は不可能

    Args:
        player_id: 脱落者のプレイヤーID
        contracts: 全契約リスト

    Returns:
        義務失効を反映した契約リスト
    """
    updated: list[Contract] = []
    for contract in contracts:
        new_obligations: list[Obligation] = []
        for ob in contract.obligations:
            if (not ob.is_fulfilled and not ob.is_expired and
                    (ob.obligor == player_id or ob.counterparty == player_id)):
                # 脱落者が関わる未履行義務を失効
                new_obligations.append(ob.model_copy(update={"is_expired": True}))
            else:
                new_obligations.append(ob)
        updated.append(contract.model_copy(update={"obligations": new_obligations}))

    return updated
