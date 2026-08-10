"""
Financeフェイズモジュール

§5.3に基づくFinanceフェイズの処理を提供する。
- R1-11: 利息計上 → 任意返済 → 状態更新
- R12: 利息計上 → 自動返済 → 生還判定
"""

from engine.models import PlayerState
from engine.config import GameConfig
from engine.events import EventLogger
from engine import player as player_ops
from engine import elimination as elim_ops


def execute_finance(
    players: dict[str, PlayerState],
    round_num: int,
    config: GameConfig,
    contracts: list,
    logger: EventLogger,
    pending_repayments: dict[str, int] | None = None,
) -> tuple[dict[str, PlayerState], list]:
    """
    Financeフェイズを実行する（§5.3）

    R1-11: 利息計上 → 任意返済 → 状態更新・ログ保存
    R12:   利息計上 → 自動返済 → Debt=0確認 → Cash≧生還条件額確認 → 生還/脱落判定

    Args:
        players: 全プレイヤーの状態辞書
        round_num: ラウンド番号（1-12）
        config: ゲーム設定
        contracts: 全契約リスト（脱落時の義務失効用）
        logger: イベントロガー
        pending_repayments: Negotiation中に出されたrepayアクションの結果
                            {player_id: 返済額}

    Returns:
        (更新されたplayers, 更新されたcontracts)
    """
    if pending_repayments is None:
        pending_repayments = {}

    # --- Step 1: 利息計上 ---
    for pid, p in list(players.items()):
        if not p.is_alive:
            continue
        if p.debt_balance <= 0:
            continue
        old_debt = p.debt_balance
        p = player_ops.apply_interest(p, config.interest_rate)
        interest = p.debt_balance - old_debt
        players[pid] = p
        logger.log("INTEREST", round_num, "finance", data={
            "player_id": pid,
            "old_debt": old_debt,
            "interest": interest,
            "new_debt": p.debt_balance,
        })

    if round_num < config.num_rounds:
        # --- R1-11: 任意返済 ---
        for pid, amount in pending_repayments.items():
            if pid not in players:
                continue
            p = players[pid]
            if not p.is_alive:
                continue
            if amount > 0:
                old_cash = p.cash
                old_debt = p.debt_balance
                p = player_ops.repay_debt(p, amount)
                actual_repaid = old_cash - p.cash
                players[pid] = p
                if actual_repaid > 0:
                    logger.log("REPAYMENT", round_num, "finance", data={
                        "player_id": pid,
                        "amount": actual_repaid,
                        "new_cash": p.cash,
                        "new_debt": p.debt_balance,
                    })
    else:
        # --- R12: 自動返済 ---
        for pid, p in list(players.items()):
            if not p.is_alive:
                continue
            if p.debt_balance > 0:
                old_cash = p.cash
                old_debt = p.debt_balance
                # 返済可能額を自動充当（§2.3: R12 Finance）
                repay_amount = min(p.cash, p.debt_balance)
                p = player_ops.repay_debt(p, repay_amount)
                players[pid] = p
                logger.log("AUTO_REPAYMENT", round_num, "finance", data={
                    "player_id": pid,
                    "amount": repay_amount,
                    "new_cash": p.cash,
                    "new_debt": p.debt_balance,
                })

        # --- R12: 生還/脱落判定 ---
        for pid, p in list(players.items()):
            if not p.is_alive:
                continue

            survived = True
            reason = None

            # Debt=0 チェック
            if p.debt_balance > 0:
                survived = False
                reason = "condition_not_met"

            # Cash≧生還条件額 チェック
            if survived and p.cash < config.survival_cash:
                survived = False
                reason = "condition_not_met"

            if survived:
                logger.log("SURVIVAL_CHECK", round_num, "finance", data={
                    "player_id": pid,
                    "result": "survived",
                    "cash": p.cash,
                    "debt": p.debt_balance,
                })
            else:
                logger.log("SURVIVAL_CHECK", round_num, "finance", data={
                    "player_id": pid,
                    "result": "eliminated",
                    "reason": reason,
                    "cash": p.cash,
                    "debt": p.debt_balance,
                })
                # 脱落処理（条件未達による脱落は強制清算も行う）
                p, contracts, record = elim_ops.forced_liquidation(
                    p, reason or "condition_not_met", round_num, contracts,
                )
                players[pid] = p
                logger.log("FORCED_LIQUIDATION", round_num, "finance", data=record)

    return players, contracts
