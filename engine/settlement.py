"""
Settlement 8Step処理モジュール

§5.2に基づくReveal & Settlementフェイズの8Step処理を
厳密な順序で実行する。ルールエンジンの最重要モジュール。
"""

from typing import Any

from engine.models import (
    PlayerState, Market, MarketCommit, MarketResult,
    Contract, Bounty, GameEvent,
)
from engine.config import GameConfig
from engine.events import EventLogger
from engine import market as market_ops
from engine import contracts as contract_ops
from engine import bounty as bounty_ops
from engine import elimination as elim_ops
from engine import player as player_ops


def _should_surge(num_participants: int, alive_count: int, config: GameConfig) -> bool:
    """
    市場高騰の発動判定（§S2.2 + 少人数時の全員参加要件）

    - surge_enabled=False → 常にFalse
    - alive_count <= surge_full_participation_max_alive → 全員参加のみ高騰
    - それ以外 → 参加者 > 生存者/2 で高騰（現行どおり）

    Args:
        num_participants: その市場への参加者数
        alive_count: 現在の生存者数
        config: ゲーム設定
    """
    if not config.surge_enabled:
        return False
    if alive_count <= config.surge_full_participation_max_alive:
        return num_participants == alive_count
    return num_participants > alive_count / 2


def execute_settlement(
    players: dict[str, PlayerState],
    markets: list[Market],
    commits: list[MarketCommit],
    contracts: list[Contract],
    bounties: list[Bounty],
    round_num: int,
    config: GameConfig,
    logger: EventLogger,
) -> tuple[
    dict[str, PlayerState],
    list[Contract],
    list[Bounty],
    dict[str, int],
    list[MarketResult],
]:
    """
    Settlement 8Step処理を実行する（§5.2）

    Step 1: Reveal — 全市場の参加者・使用カードを公開
    Step 2: Market Settlement — Entry Fee加算→勝敗→賞金支払い→カード消滅
    Step 3: 型B監査 — 違反者を脱落確定、全義務即時失効
    Step 4: スナップショット — 市場賞金反映後のCash/FreeCashを固定
    Step 5: 型A Atomic執行 — 義務者別合算→FreeCash判定→一括執行or不履行
    Step 6: 報奨判定 — 達成者型・イベント型（同一Settlement脱落で発火）
    Step 7: 脱落公示
    Step 8: 強制清算

    Args:
        players: 全プレイヤーの状態辞書
        markets: 当該ラウンドの市場リスト
        commits: 全コミット
        contracts: 全契約リスト
        bounties: 全報奨リスト
        round_num: ラウンド番号
        config: ゲーム設定
        logger: イベントロガー

    Returns:
        (更新されたplayers, 更新されたcontracts, 更新されたbounties,
         キャリーオーバー辞書, 市場結果リスト)
    """
    # Settlement全体で蓄積する脱落者セット
    eliminated_this_settlement: set[str] = set()
    carryovers: dict[str, int] = {}

    # =========================================================================
    # Step 1: Reveal — 全市場の参加者・使用カードを公開
    # =========================================================================
    # S2: 霧のラウンド — カード情報をマスク（§S2.1）
    is_fog = round_num in config.fog_rounds
    logger.log("REVEAL", round_num, "settlement", step=1, data={
        "commits": [
            {"player_id": c.player_id, "market_id": c.market_id,
             "card": "FOG" if is_fog else c.card.card_id,
             "rank": "FOG" if is_fog else c.card.rank.name}
            for c in commits
        ],
        "fog": is_fog,
    })

    # =========================================================================
    # Step 2: Market Settlement — Entry Fee加算→勝敗→賞金支払い→カード消滅
    # =========================================================================
    # 市場ごとにコミットを分類
    commits_by_market: dict[str, list[MarketCommit]] = {}
    for m in markets:
        commits_by_market[m.market_id] = []
    for c in commits:
        commits_by_market.setdefault(c.market_id, []).append(c)

    # S2: 市場高騰判定用 — 生存者数を取得（§S2.2）
    alive_count = sum(
        1 for p in players.values()
        if p.is_alive and p.player_id not in eliminated_this_settlement
    )

    market_results: list[MarketResult] = []
    for market in markets:
        mc = commits_by_market.get(market.market_id, [])
        is_surge = _should_surge(len(mc), alive_count, config)
        result = market_ops.resolve_market(market, mc, config.entry_fee, surge=is_surge)
        market_results.append(result)

        if not result.winners:
            # 参加者0: キャリーオーバー（§4.7）
            carryovers[market.market_id] = result.total_pool
        else:
            carryovers[market.market_id] = 0

        # 勝者に賞金支払い
        for winner_id in result.winners:
            p = players[winner_id]
            p = player_ops.receive(p, result.prize_per_winner)
            players[winner_id] = p

        # 全参加者のカード消滅
        for mc_item in mc:
            p = players[mc_item.player_id]
            p = player_ops.use_card(p, mc_item.card)
            players[mc_item.player_id] = p

        logger.log("MARKET_RESULT", round_num, "settlement", step=2, data={
            "market_id": market.market_id,
            "participants": len(mc),
            "winners": result.winners,
            "prize_per_winner": result.prize_per_winner,
            "total_pool": result.total_pool,
            "carryover": carryovers.get(market.market_id, 0),
            "surged": is_surge,
        })

    # =========================================================================
    # Step 3: 行動契約(型B)監査
    # =========================================================================
    type_b_obs = contract_ops.get_active_type_b_obligations(contracts, round_num)
    violations = contract_ops.audit_type_b(type_b_obs, commits)

    # 違反者を脱落確定（§5.2: この時点で脱落確定）
    violators: set[str] = set()
    for player_id, ob in violations:
        violators.add(player_id)
        logger.log("TYPE_B_VIOLATION", round_num, "settlement", step=3, data={
            "player_id": player_id,
            "obligation_id": ob.obligation_id,
            "ob_type": ob.ob_type.value,
            "details": ob.details,
        })

    # 脱落確定者の全義務を即時失効（§5.2 Step 3の注記）
    for violator_id in violators:
        contracts = elim_ops.expire_obligations_for_player(violator_id, contracts)
        eliminated_this_settlement.add(violator_id)

    # =========================================================================
    # Step 4: 型A判定用スナップショット
    # =========================================================================
    # 市場賞金反映後のCash/FreeCashを固定（§6.6）
    # 同一Settlement内の型A受取金は支払原資にできない
    snapshots: dict[str, dict[str, int]] = {}
    for pid, p in players.items():
        if p.is_alive and pid not in eliminated_this_settlement:
            snapshots[pid] = {
                "cash": p.cash,
                "free_cash": p.free_cash,
            }

    logger.log("SNAPSHOT", round_num, "settlement", step=4, data={
        "snapshots": snapshots,
    })

    # =========================================================================
    # Step 5: 型A契約のAtomic執行
    # =========================================================================
    type_a_obs = contract_ops.get_active_type_a_obligations(
        contracts, round_num, excluded_players=eliminated_this_settlement,
    )

    if type_a_obs:
        updated_obs, failed_obligors, payments = contract_ops.execute_type_a_atomic(
            type_a_obs, snapshots,
        )

        # 義務のステータスを契約に反映
        fulfilled_ids = {ob.obligation_id for ob in updated_obs if ob.is_fulfilled}
        contracts = contract_ops.fulfill_obligations(contracts, fulfilled_ids)

        # 支払いをプレイヤーに反映
        for pid, amount in payments.items():
            if pid in players:
                p = players[pid]
                p = p.model_copy(update={"cash": p.cash + amount})
                players[pid] = p

        # 型A支払い成功のログ
        for ob in updated_obs:
            if ob.is_fulfilled:
                logger.log("TYPE_A_EXECUTION", round_num, "settlement", step=5, data={
                    "obligation_id": ob.obligation_id,
                    "obligor": ob.obligor,
                    "counterparty": ob.counterparty,
                    "amount": ob.details.get("amount", 0),
                })

        # 履行不能者を脱落確定
        for obligor_id in failed_obligors:
            eliminated_this_settlement.add(obligor_id)
            # 脱落者の義務を失効
            contracts = elim_ops.expire_obligations_for_player(obligor_id, contracts)
            logger.log("TYPE_A_FAILURE", round_num, "settlement", step=5, data={
                "player_id": obligor_id,
                "reason": "Atomic execution failed - insufficient free cash",
            })

    # =========================================================================
    # Step 6: 公開報奨の判定・支払い
    # =========================================================================
    # 達成者型
    achievement_triggered = bounty_ops.evaluate_achievement_bounties(
        bounties, market_results, round_num,
    )
    for bounty, recipient_id in achievement_triggered:
        if recipient_id in players and players[recipient_id].is_alive:
            p = players[recipient_id]
            p = player_ops.receive(p, bounty.amount)
            players[recipient_id] = p
            # 報奨を消化（無効化）
            bounties = [
                b.model_copy(update={"is_active": False}) if b.bounty_id == bounty.bounty_id else b
                for b in bounties
            ]
            logger.log("BOUNTY_TRIGGERED", round_num, "settlement", step=6, data={
                "bounty_id": bounty.bounty_id,
                "type": "achievement",
                "recipient": recipient_id,
                "amount": bounty.amount,
            })

    # イベント型（同一Settlement内の脱落で発火、§7.2）
    event_triggered = bounty_ops.evaluate_event_bounties(
        bounties, eliminated_this_settlement, round_num,
    )
    for bounty, recipient_id in event_triggered:
        if recipient_id in players and players[recipient_id].is_alive:
            # 受取人が脱落していない場合のみ支払い
            if recipient_id not in eliminated_this_settlement:
                p = players[recipient_id]
                p = player_ops.receive(p, bounty.amount)
                players[recipient_id] = p
                bounties = [
                    b.model_copy(update={"is_active": False}) if b.bounty_id == bounty.bounty_id else b
                    for b in bounties
                ]
                logger.log("BOUNTY_TRIGGERED", round_num, "settlement", step=6, data={
                    "bounty_id": bounty.bounty_id,
                    "type": "event",
                    "recipient": recipient_id,
                    "amount": bounty.amount,
                })

    # =========================================================================
    # Step 7: 脱落処理の公示
    # =========================================================================
    for pid in eliminated_this_settlement:
        p = players[pid]
        if p.is_alive:  # まだ脱落処理されていない場合
            # 脱落理由の判定
            reason = "contract_violation"
            logger.log("ELIMINATION", round_num, "settlement", step=7, data={
                "player_id": pid,
                "reason": reason,
            })

    # =========================================================================
    # Step 8: 脱落時強制清算（§1.6）
    # =========================================================================
    for pid in eliminated_this_settlement:
        p = players[pid]
        if p.is_alive:
            p, contracts, record = elim_ops.forced_liquidation(
                p, "contract_violation", round_num, contracts,
            )
            players[pid] = p
            logger.log("FORCED_LIQUIDATION", round_num, "settlement", step=8, data=record)

    return players, contracts, bounties, carryovers, market_results
