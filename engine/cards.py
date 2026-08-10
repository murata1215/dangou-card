"""
カード管理モジュール

§3に基づくカードのデッキ生成・ランク比較を提供する。
各AIに同一構成の12枚が配布される。
"""

from engine.models import Card, CardRank


def create_deck() -> list[Card]:
    """
    §3.1に基づく12枚デッキを生成する

    構成:
    - ROYAL_FLUSH〜TWO_PAIR: 各1枚
    - ONE_PAIR: 2枚
    - HIGH_CARD: 2枚

    Returns:
        12枚のカードリスト（card_idで一意に識別可能）
    """
    deck: list[Card] = []

    # ランク10(ROYAL_FLUSH)〜3(TWO_PAIR): 各1枚
    for rank in CardRank:
        if rank in (CardRank.HIGH_CARD, CardRank.ONE_PAIR):
            # 2枚ずつ（後で追加）
            continue
        deck.append(Card(rank=rank, card_id=f"{rank.name}_1"))

    # ONE_PAIR: 2枚
    deck.append(Card(rank=CardRank.ONE_PAIR, card_id="ONE_PAIR_1"))
    deck.append(Card(rank=CardRank.ONE_PAIR, card_id="ONE_PAIR_2"))

    # HIGH_CARD: 2枚
    deck.append(Card(rank=CardRank.HIGH_CARD, card_id="HIGH_CARD_1"))
    deck.append(Card(rank=CardRank.HIGH_CARD, card_id="HIGH_CARD_2"))

    return deck


def find_card_by_rank(hand: list[Card], rank: CardRank) -> Card | None:
    """
    手札から指定ランクのカードを1枚探す

    同ランクが複数ある場合（ONE_PAIR, HIGH_CARD）は最初に見つかったものを返す。

    Args:
        hand: 手札のカードリスト
        rank: 探すカードランク

    Returns:
        見つかったCard、なければNone
    """
    for card in hand:
        if card.rank == rank:
            return card
    return None


def get_lowest_rank_card(hand: list[Card]) -> Card | None:
    """
    手札から最低ランクのカードを返す

    同ランクが複数ある場合はcard_id順で最初のものを返す。
    自動代行Commitの優先順位（§4.4: ①最低ランクの未使用カード）で使用。

    Args:
        hand: 手札のカードリスト

    Returns:
        最低ランクのCard、手札が空ならNone
    """
    if not hand:
        return None
    return min(hand, key=lambda c: (c.rank.value, c.card_id))
