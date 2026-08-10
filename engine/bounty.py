"""
公開報奨モジュール

§7.2に基づく報奨の掲載・取り下げ・判定を提供する。
- 達成者型（型A）: 観測可能な行動条件のみ
- イベント型/保険型（型B）: 特定イベント（脱落等）を条件
"""

import math
import uuid
from typing import Any

from engine.models import (
    Bounty, BountyType, BountyConditionType,
    MarketResult, PlayerState,
)


def create_bounty(
    poster: str,
    amount: int,
    bounty_type_str: str,
    condition_type_str: str,
    condition: dict[str, Any],
    round_num: int,
    beneficiary: str | None = None,
    anonymous: bool = False,
    surcharge_rate: float = 0.10,
) -> Bounty:
    """
    報奨オブジェクトを生成する

    預託額 = 報奨額 + (匿名の場合は手数料+10%)
    手数料は報奨額に対する割合。

    Args:
        poster: 掲載者のプレイヤーID
        amount: 報奨額
        bounty_type_str: "achievement" or "event"
        condition_type_str: 条件種別文字列
        condition: 条件パラメータ
        round_num: 対象ラウンド
        beneficiary: 受取人（イベント型で指定）
        anonymous: 匿名掲載フラグ
        surcharge_rate: 匿名手数料率（デフォルト10%）

    Returns:
        報奨オブジェクト
    """
    bounty_id = f"B_{uuid.uuid4().hex[:8]}"
    bounty_type = BountyType(bounty_type_str)
    condition_type = BountyConditionType(condition_type_str)

    # 預託額の計算
    if anonymous:
        # 匿名手数料 = 報奨額 × surcharge_rate（切り上げ）
        surcharge = math.ceil(amount * surcharge_rate)
        deposited = amount + surcharge
    else:
        deposited = amount

    return Bounty(
        bounty_id=bounty_id,
        poster=poster,
        amount=amount,
        bounty_type=bounty_type,
        condition_type=condition_type,
        condition=condition,
        beneficiary=beneficiary,
        round_num=round_num,
        anonymous=anonymous,
        is_active=True,
        deposited=deposited,
    )


def cancel_bounty(bounty: Bounty) -> Bounty:
    """
    報奨を取り下げる（預託金返還）

    Args:
        bounty: 取り下げる報奨

    Returns:
        取り下げ後の報奨
    """
    return bounty.model_copy(update={"is_active": False})


def evaluate_achievement_bounties(
    bounties: list[Bounty],
    market_results: list[MarketResult],
    round_num: int,
) -> list[tuple[Bounty, str]]:
    """
    達成者型報奨を判定する（§7.2）

    市場結果から達成条件を満たすかチェック。

    Args:
        bounties: 有効な報奨リスト
        market_results: 当該ラウンドの市場結果
        round_num: 対象ラウンド

    Returns:
        (発火した報奨, 受取人ID) のリスト
    """
    triggered: list[tuple[Bounty, str]] = []

    for bounty in bounties:
        if not bounty.is_active:
            continue
        if bounty.round_num != round_num:
            continue
        if bounty.bounty_type != BountyType.ACHIEVEMENT:
            continue

        if bounty.condition_type == BountyConditionType.MARKET_WIN_AGAINST:
            # 「特定プレイヤーと同一市場に参加し、より高いカードで勝利」
            target = bounty.condition.get("target_player")
            for result in market_results:
                target_participated = False
                for p in result.participants:
                    if p.player_id == target:
                        target_participated = True
                        break
                if not target_participated:
                    continue
                # 勝者の中でtarget以外が条件達成
                for winner_id in result.winners:
                    if winner_id != target:
                        triggered.append((bounty, winner_id))
                        break  # 1つの報奨につき1人（最初の勝者）

        elif bounty.condition_type == BountyConditionType.SAME_MARKET:
            # 「特定プレイヤーと同一市場に参加」
            target = bounty.condition.get("target_player")
            for result in market_results:
                target_participated = any(
                    p.player_id == target for p in result.participants
                )
                if target_participated:
                    for p in result.participants:
                        if p.player_id != target and p.player_id != bounty.poster:
                            triggered.append((bounty, p.player_id))
                            break

    return triggered


def evaluate_event_bounties(
    bounties: list[Bounty],
    eliminated_players: set[str],
    round_num: int,
) -> list[tuple[Bounty, str]]:
    """
    イベント型（保険型）報奨を判定する（§7.2）

    同一Settlement内の脱落イベントで発火する。

    Args:
        bounties: 有効な報奨リスト
        eliminated_players: 同一Settlement内で脱落したプレイヤーID
        round_num: 対象ラウンド

    Returns:
        (発火した報奨, 受取人ID) のリスト
    """
    triggered: list[tuple[Bounty, str]] = []

    for bounty in bounties:
        if not bounty.is_active:
            continue
        if bounty.round_num != round_num:
            continue
        if bounty.bounty_type != BountyType.EVENT:
            continue

        if bounty.condition_type == BountyConditionType.PLAYER_ELIMINATED:
            target = bounty.condition.get("target_player")
            if target in eliminated_players:
                # 受取人が指定されている（かつ脱落していない）場合にのみ発火
                if bounty.beneficiary and bounty.beneficiary not in eliminated_players:
                    triggered.append((bounty, bounty.beneficiary))

    return triggered
