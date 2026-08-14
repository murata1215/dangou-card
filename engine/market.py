"""
市場ロジックモジュール

§4に基づく市場生成・賞金スケジュール・キャリーオーバー・勝敗判定を提供する。
"""

from engine.config import GameConfig
from engine.models import Market, MarketCommit, MarketResult
from engine.rng import GameRng


def generate_markets(
    round_num: int,
    config: GameConfig,
    carryovers: dict[str, int],
    rng: GameRng | None = None,
) -> list[Market]:
    """
    ラウンドの3市場を生成する（§4.1）

    賞金スケジュールに基づき基本賞金を算出し、
    前ラウンドからのキャリーオーバーを加算する。

    Args:
        round_num: ラウンド番号（1-12）
        config: ゲーム設定
        carryovers: 各市場IDのキャリーオーバー額 {"M01": 0, "M02": 0, "M03": 0}
        rng: 乱数ジェネレータ（market_distribution="random"時に必要）

    Returns:
        3市場のリスト
    """
    # 当該ラウンドの総賞金（0-indexed）
    round_total = config.prize_tiers[round_num - 1]

    # S2: 最終市場 — R12の基本賞金をN倍（§S2.3）
    if round_num == config.num_rounds and config.final_market_multiplier > 1:
        round_total *= config.final_market_multiplier

    # 3市場への配分
    if config.market_distribution == "equal":
        # 均等配分（端数は最初の市場に付与）
        base = round_total // config.num_markets
        prizes = [base] * config.num_markets
        prizes[0] += round_total - base * config.num_markets
    elif config.market_distribution == "weighted":
        # 傾斜配分（50%:30%:20%）
        ratios = [0.5, 0.3, 0.2]
        prizes = [int(round_total * r) for r in ratios]
        prizes[-1] += round_total - sum(prizes)  # 端数調整
    elif config.market_distribution == "random":
        # ランダム配分
        if rng is None:
            raise ValueError("rng is required for random market distribution")
        prizes = rng.distribute_prize_random(round_total, config.num_markets)
    else:
        raise ValueError(f"Unknown market_distribution: {config.market_distribution}")

    markets: list[Market] = []
    for i in range(config.num_markets):
        market_id = f"M{i + 1:02d}"
        markets.append(Market(
            market_id=market_id,
            base_prize=prizes[i],
            carryover=carryovers.get(market_id, 0),
        ))

    return markets


def resolve_market(
    market: Market,
    commits: list[MarketCommit],
    entry_fee: int,
    surge: bool = False,
) -> MarketResult:
    """
    市場の勝敗を判定し結果を返す（§4.5, §4.6）

    処理手順:
    1. Entry Feeをプールへ加算
    2. 最高ランク判定
    3. 同ランク時は均等分配（端数切捨て）
    4. 参加者1人→その人が獲得
    5. 参加者0人→キャリーオーバー（呼び出し側で処理）

    Args:
        market: 対象市場
        commits: この市場への全コミット
        entry_fee: 1人あたりEntry Fee

    Returns:
        市場決着結果
    """
    num_participants = len(commits)

    # Entry Feeをプールに加算（§4.2）
    total_pool = market.prize_pool + entry_fee * num_participants

    # S2: 市場高騰 — プール2倍（§S2.2）
    if surge:
        total_pool *= 2

    if num_participants == 0:
        # 参加者0: キャリーオーバー（§4.7）
        return MarketResult(
            market_id=market.market_id,
            participants=[],
            winners=[],
            prize_per_winner=0,
            total_pool=total_pool,
        )

    # 最高ランクを特定
    max_rank = max(c.card.rank for c in commits)

    # 最高ランクのプレイヤーを特定（複数いれば全員が勝者）
    winners = [c.player_id for c in commits if c.card.rank == max_rank]

    # 賞金分配（同ランク時は均等分配、§4.6、端数切捨て）
    prize_per_winner = total_pool // len(winners)

    return MarketResult(
        market_id=market.market_id,
        participants=commits,
        winners=winners,
        prize_per_winner=prize_per_winner,
        total_pool=total_pool,
        surged=surge,
    )
