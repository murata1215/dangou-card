"""
Settlement 8Step処理モジュール

§5.2に基づくReveal & Settlementフェイズの8Step処理を
厳密な順序で実行する。ルールエンジンの最重要モジュール。
"""

from typing import Any

from engine.models import (
    PlayerState, Market, MarketCommit, MarketResult,
    Contract, Bounty, GameEvent, DoubleUpDeposit,
)
from engine.config import GameConfig
from engine.events import EventLogger
from engine import market as market_ops
from engine import contracts as contract_ops
from engine import bounty as bounty_ops
from engine import elimination as elim_ops
from engine import player as player_ops


def _summarize_market_wins(market_results: list[MarketResult], player_id: str) -> dict:
    """1プレイヤーが当ラウンドで獲得した市場賞金を、空き巣（参加者1人）市場か
    否かで分類する（Cycle 8: ログ記録専用のヘルパー。v0.8 E2でgame.pyから移設）。

    判定ロジック（空き巣市場の定義=len(participants)==1、勝者集計）は
    execute_settlement内の倍掛けStep1（solo_markets / non_solo_winners の算出）と
    完全に同一の条件式を用いる。**このヘルパーの戻り値は倍掛け成功判定
    （§6.2ゲート、non_solo_winners[pid] > 0）には一切使用しない**。ログ出力
    専用であり、賞金額・成功可否の計算経路には影響しない。

    Returns:
        {"won_any": bool, "solo_wins": int, "non_solo_wins": int,
         "solo_prize": int, "non_solo_prize": int}
    """
    solo_wins = 0
    non_solo_wins = 0
    solo_prize = 0
    non_solo_prize = 0
    for mr in market_results:
        if player_id not in mr.winners:
            continue
        if len(mr.participants) == 1:
            solo_wins += 1
            solo_prize += mr.prize_per_winner
        else:
            non_solo_wins += 1
            non_solo_prize += mr.prize_per_winner
    return {
        "won_any": (solo_wins + non_solo_wins) > 0,
        "solo_wins": solo_wins,
        "non_solo_wins": non_solo_wins,
        "solo_prize": solo_prize,
        "non_solo_prize": non_solo_prize,
    }


def resolve_double_up_deposits(
    players: dict[str, PlayerState],
    market_results: list[MarketResult],
    double_up_deposits: list[DoubleUpDeposit] | None,
    round_num: int,
    logger: EventLogger,
) -> dict[str, Any]:
    """
    倍掛け前ラウンド預託の解決（v0.8 D2・旧 game.py _process_double_up Step1）

    今Rの市場結果（market_results）確定直後・型B監査より前に呼ぶことを想定する
    （execute_settlement()内部でStep2直後・Step3直前に呼ばれる）。players/deposits
    は直接ミューテートする（呼び出し元と同一オブジェクトを共有する前提）。

    Args:
        players: 全プレイヤーの状態辞書（成功時の払出をここに反映）
        market_results: 当該ラウンドの市場結果リスト（Step2で確定済みのもの）
        double_up_deposits: 倍掛け預託リスト。success_round == round_num かつ
            未解決のものを解決する。Noneまたは空なら何もしない。
        round_num: 現在のラウンド番号
        logger: イベントロガー

    Returns:
        {"success": int, "fail": int, "solo_forfeit": int, "resolved": [...]}
    """
    du_success = 0
    du_fail = 0
    du_solo_forfeit = 0
    du_resolved: list[dict[str, Any]] = []

    if double_up_deposits:
        # 今ラウンドの市場勝者を特定（player_id → 獲得額マップ、ソロ市場除外）
        non_solo_winners: dict[str, int] = {}
        solo_markets: set[str] = set()
        for mr in market_results:
            if len(mr.participants) == 1:
                solo_markets.add(mr.market_id)
        for mr in market_results:
            if mr.market_id in solo_markets:
                continue
            for winner_id in mr.winners:
                non_solo_winners[winner_id] = (
                    non_solo_winners.get(winner_id, 0) + mr.prize_per_winner
                )

        for dep in double_up_deposits:
            if dep.resolved or dep.success_round != round_num:
                continue
            dep.resolved = True
            p = players[dep.player_id]
            # Cycle 8: ログ記録専用の勝敗内訳（判定ロジックには使用しない。
            # 判定は直後の non_solo_winners[...] > 0 ゲートのみで行う＝無変更）
            win_summary = _summarize_market_wins(market_results, dep.player_id)

            if not p.is_alive:
                # Settlement開始前に既に脱落済みなら没収
                logger.log("DOUBLE_UP_RESOLVED", round_num, "settlement", step=2, data={
                    "player_id": dep.player_id, "result": "forfeit_eliminated",
                    "deposit": dep.deposit_amount,
                    "outcome_reason": "eliminated",
                    "solo_wins": win_summary["solo_wins"],
                    "non_solo_wins": win_summary["non_solo_wins"],
                })
                du_fail += 1
                du_resolved.append({
                    "player_id": dep.player_id, "deposit": dep.deposit_amount,
                    "result": "forfeit_eliminated", "payout": 0,
                })
                continue

            if dep.player_id in non_solo_winners and non_solo_winners[dep.player_id] > 0:
                # 成功: 2倍払い出し（判定条件は無変更）
                payout = dep.deposit_amount * 2
                dep.success = True

                p = player_ops.receive(p, payout)
                players[dep.player_id] = p
                logger.log("DOUBLE_UP_RESOLVED", round_num, "settlement", step=2, data={
                    "player_id": dep.player_id, "result": "success",
                    "deposit": dep.deposit_amount, "payout": payout,
                    "outcome_reason": "non_solo_win",
                    "solo_wins": win_summary["solo_wins"],
                    "non_solo_wins": win_summary["non_solo_wins"],
                })
                du_success += 1
                du_resolved.append({
                    "player_id": dep.player_id, "deposit": dep.deposit_amount,
                    "result": "success", "payout": payout,
                })
            else:
                # 失敗: 没収。won_any=Trueなのに non_solo_winners が 0 ということは、
                # 勝利はあったが全て空き巣（参加者1人）市場だったため成功判定から
                # 除外された（§6.2）ケース＝ solo_only_win。won_any=Falseなら
                # そもそも当ラウンド無勝利＝ no_win。
                if win_summary["won_any"]:
                    outcome_reason = "solo_only_win"
                    dep.forfeited_by_solo_only = True
                    du_solo_forfeit += 1
                else:
                    outcome_reason = "no_win"
                logger.log("DOUBLE_UP_RESOLVED", round_num, "settlement", step=2, data={
                    "player_id": dep.player_id, "result": "forfeit",
                    "deposit": dep.deposit_amount,
                    "outcome_reason": outcome_reason,
                    "solo_wins": win_summary["solo_wins"],
                    "non_solo_wins": win_summary["non_solo_wins"],
                })
                du_fail += 1
                du_resolved.append({
                    "player_id": dep.player_id, "deposit": dep.deposit_amount,
                    "result": "forfeit", "payout": 0,
                })

    return {
        "success": du_success,
        "fail": du_fail,
        "solo_forfeit": du_solo_forfeit,
        "resolved": du_resolved,
    }


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
    double_up_deposits: list[DoubleUpDeposit] | None = None,
) -> tuple[
    dict[str, PlayerState],
    list[Contract],
    list[Bounty],
    dict[str, int],
    list[MarketResult],
    dict[str, Any],
]:
    """
    Settlement 8Step処理を実行する（§5.2）

    Step 1: Reveal — 全市場の参加者・使用カードを公開
    Step 2: Market Settlement — Entry Fee加算→勝敗→賞金支払い→カード消滅
    Step 2.5: 倍掛け前ラウンド預託の解決（v0.8 D2・S2拡張）——
        今Rの市場結果確定直後・型B監査より前に払い出す。これにより2倍払出が
        Step4の型Aスナップショットに含まれ、型A支払原資として使える。
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
        double_up_deposits: 倍掛け預託リスト（v0.8 D2）。dep.success_round == round_num の
            ものをここで解決する。要素は同一オブジェクトを直接ミューテートする
            （game.py の self.double_up_deposits と共有される前提）。省略時は倍掛け処理なし。

    Returns:
        (更新されたplayers, 更新されたcontracts, 更新されたbounties,
         キャリーオーバー辞書, 市場結果リスト, 倍掛けStep1サマリdict
         {"success": int, "fail": int, "solo_forfeit": int, "resolved": [...]})
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
    # Step 2.5: 倍掛け前ラウンド預託の解決（v0.8 D2）
    # =========================================================================
    # 今Rの市場結果（market_results）が確定した直後・型B監査より前に払い出す。
    # これにより2倍払出後のcashがStep4スナップショットに反映され、型A支払原資
    # として使える（旧実装はexecute_settlement()の外＝Step8より後で解決していた）。
    du_summary = resolve_double_up_deposits(
        players, market_results, double_up_deposits, round_num, logger,
    )

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

    # v0.8 I1: 残存義務ゼロになったACTIVE契約（ゾンビ契約）をCLOSEDへ畳む
    contracts = contract_ops.close_contracts_without_remaining_obligations(contracts, round_num)

    return players, contracts, bounties, carryovers, market_results, du_summary
