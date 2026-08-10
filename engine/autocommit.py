"""
自動代行Commitモジュール

§4.4に基づく自動代行Commit処理を提供する。
Commit不能時（無効出力/タイムアウト）にシステムが代行する。
"""

from engine.models import (
    Card, CardRank, Market, MarketCommit,
    PlayerState, Contract, Obligation, ObligationType,
)
from engine.contracts import get_all_type_b_for_player


def compute_legal_commits(
    player: PlayerState,
    contracts: list[Contract],
    markets: list[Market],
    round_num: int,
) -> list[MarketCommit]:
    """
    契約を満たす合法Commit集合を計算する（§4.4）

    全(市場, カード)の組合せを列挙し、
    当該プレイヤーの型B契約をすべて満たすものだけをフィルタする。

    Args:
        player: プレイヤー状態
        contracts: 全契約リスト
        markets: 当該ラウンドの市場リスト
        round_num: ラウンド番号

    Returns:
        合法なMarketCommitのリスト
    """
    # 当該プレイヤーの当該ラウンドの型B義務を取得
    type_b_obs = get_all_type_b_for_player(contracts, player.player_id, round_num)

    legal: list[MarketCommit] = []

    # 全(市場, カード)の組合せを列挙
    for market in markets:
        for card in player.hand:
            commit = MarketCommit(
                player_id=player.player_id,
                market_id=market.market_id,
                card=card,
            )
            # この組合せが全型B義務を満たすかチェック
            if _satisfies_all_obligations(commit, type_b_obs):
                legal.append(commit)

    return legal


def _satisfies_all_obligations(
    commit: MarketCommit,
    obligations: list[Obligation],
) -> bool:
    """
    1つのCommitが全型B義務を満たすか判定する

    Args:
        commit: 判定対象のコミット
        obligations: チェックする型B義務リスト

    Returns:
        全義務を満たせばTrue
    """
    for ob in obligations:
        if ob.ob_type == ObligationType.TYPE_B_MARKET:
            # 指定市場に参加すること
            required_market = ob.details.get("market_id")
            if commit.market_id != required_market:
                return False

        elif ob.ob_type == ObligationType.TYPE_B_CARD:
            # 指定カードを使用すること
            required_rank = ob.details.get("card_rank")
            if commit.card.rank.name != required_rank:
                return False

        elif ob.ob_type == ObligationType.TYPE_B_NO_MARKET:
            # 指定市場に参加しないこと
            forbidden_market = ob.details.get("market_id")
            if commit.market_id == forbidden_market:
                return False

    return True


def select_auto_commit(
    legal_commits: list[MarketCommit],
    markets: list[Market],
) -> MarketCommit | None:
    """
    合法Commit集合から自動代行で選択するCommitを返す（§4.4）

    優先順位:
    1. 最低ランクの未使用カード
    2. 最低賞金市場
    3. 市場ID順（辞書順）

    Args:
        legal_commits: 合法Commitリスト
        markets: 当該ラウンドの市場リスト

    Returns:
        選択されたMarketCommit、合法0件ならNone
    """
    if not legal_commits:
        return None

    if len(legal_commits) == 1:
        return legal_commits[0]

    # 市場の賞金辞書を作成
    market_prizes: dict[str, int] = {}
    for m in markets:
        market_prizes[m.market_id] = m.prize_pool

    # 優先順位でソート
    # ①最低ランクカード ②最低賞金市場 ③市場ID順
    sorted_commits = sorted(
        legal_commits,
        key=lambda c: (
            c.card.rank.value,
            market_prizes.get(c.market_id, 0),
            c.market_id,
        ),
    )

    return sorted_commits[0]
