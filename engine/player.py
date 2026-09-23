"""
プレイヤー状態管理モジュール

§2に基づくプレイヤーの資金管理（Cash/Debt/FreeCash）、
手札管理、生存状態管理を提供する。
"""

import math
from typing import Iterable, NamedTuple, Sequence

from engine.models import Card, CardRank, DoubleUpDeposit, PlayerState
from engine.cards import create_deck
from engine.config import GameConfig


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

    Deprecated:
        v0.9以降、プレイヤー間送金の支払可否判定には
        spendable_cash(player, config, at_settlement=False) を使う。
        本関数は §2.4 のFree Cash定義そのものを表す参照実装として残す
        （現在エンジン内に呼び出し元はない）。

    Args:
        player: プレイヤー状態
        amount: 必要額
    """
    return player.free_cash >= amount


def spendable_cash(player: PlayerState, config: GameConfig, *, at_settlement: bool) -> int:
    """
    プレイヤー間の価値移転（送金・型A契約・報奨預託・カードトレード現金）に
    使える金額（支払可能額）を返す（v0.9）。

    config.free_cash_mode で判定基準を切り替える:
      - "debt"      : max(0, cash - debt_balance) … §2.4 Free Cash（既定）
      - "cash"      : cash（借金の横流し防止を撤廃）
      - "entry_fee" : Negotiation時 max(0, cash - config.entry_fee) /
                      Settlement時 cash

    Args:
        player: プレイヤー状態
        config: ゲーム設定
        at_settlement: Settlement Step4スナップショット等、当ラウンドの
            Entry Feeが既にCommitで実引き落とし済みの時点での評価なら True。
            Negotiationフェーズ（送金・報奨・トレード提案・トレード成立
            再検証）では False。

    Note:
        at_settlement の区別は "entry_fee" モードでのみ意味を持つ
        （二重控除の回避。GameConfig.free_cash_mode のdocstring参照）。
        "debt" / "cash" モードでは at_settlement の値に関わらず結果は同じ。
    """
    mode = config.free_cash_mode
    if mode == "cash":
        return player.cash
    if mode == "entry_fee":
        if at_settlement:
            return player.cash
        return max(0, player.cash - config.entry_fee)
    # "debt"（既定・旧挙動）
    return max(0, player.cash - player.debt_balance)


def total_assets(
    player: PlayerState, double_up_deposits: Sequence[DoubleUpDeposit],
) -> int:
    """
    資産（首位公示・v0.10サイクル10.2）を返す。

    資産 = 現金 + 未解決の倍掛け預託額 − 借金残高（利息計上後）。

    free_cash（§2.4）と異なり max(0, ...) でクリップしない。借金超過分を
    負の資産として表現できることが、首位判定（資産最大の生存者を探す）で
    プレイヤー間の実際の優劣を保つために必要なため。

    倍掛け預託は成立時点で現金から実引き落とし済み（DoubleUpDeposit生成の
    設計）なので、未解決（resolved=False）の預託額を現金へ足し戻す。これに
    より倍掛け中の現金を「見かけ上減らして首位を隠す」抜け道を塞ぐ。

    Args:
        player: プレイヤー状態
        double_up_deposits: ゲーム全体の倍掛け預託リスト（player_idで絞り込む）
    """
    pending_deposit = sum(
        d.deposit_amount
        for d in double_up_deposits
        if d.player_id == player.player_id and not d.resolved
    )
    return player.cash + pending_deposit - player.debt_balance


class AssetRank(NamedTuple):
    """自己順位通知（v0.10 サイクル10.3）用の順位情報。

    金額フィールドを意図的に持たない。`dict(rank_info)` 一発で絶対額が
    プロンプト・ログへ流れる経路を作らないため。
    """

    rank: int
    tied: bool
    n_alive: int


def assets_ranking(
    players: Iterable[PlayerState],
    double_up_deposits: Sequence[DoubleUpDeposit],
) -> dict[str, AssetRank]:
    """
    生存者の資産（total_assets、首位公示10.2と同一定義・同一時点）から
    競技順位（standard competition ranking）を計算する（v0.10 サイクル10.3）。

    同額は同順位、次の順位は同額者数分スキップする（1,2,2,4）。
    分母（n_alive）は生存者数で、脱落者は順位・分母の両方から除外する。
    並びは (-assets, player_id) で決定的にする（引数の順序に依存しない）。

    Args:
        players: ゲーム全プレイヤー（is_alive=Falseは除外される）
        double_up_deposits: ゲーム全体の倍掛け預託リスト

    Returns:
        player_id -> AssetRank の辞書。キーは生存者のみ
        （脱落者は不在。呼び出し側の .get() が None で「通知しない」を表現する）。
        生存者0人なら空辞書を返す。
    """
    alive = [p for p in players if p.is_alive]
    if not alive:
        return {}
    n_alive = len(alive)
    ordered = sorted(
        alive,
        key=lambda p: (-total_assets(p, double_up_deposits), p.player_id),
    )
    result: dict[str, AssetRank] = {}
    assets_by_id = {p.player_id: total_assets(p, double_up_deposits) for p in ordered}
    rank = 1
    i = 0
    while i < len(ordered):
        current_assets = assets_by_id[ordered[i].player_id]
        j = i
        while j < len(ordered) and assets_by_id[ordered[j].player_id] == current_assets:
            j += 1
        group_size = j - i
        tied = group_size > 1
        for k in range(i, j):
            result[ordered[k].player_id] = AssetRank(rank=rank, tied=tied, n_alive=n_alive)
        rank += group_size
        i = j
    return result


def insufficient_funds_reason(config: GameConfig, purpose: str) -> str:
    """
    支払可能額不足時の理由文字列を free_cash_mode に応じて生成する（v0.9）。

    Args:
        config: ゲーム設定
        purpose: "transfer" / "bounty" / "trade" / "type_a" のいずれか
            （"debt"モードでの英語文言の出し分けにのみ使用）
    """
    mode = config.free_cash_mode
    if mode == "entry_fee":
        return (
            f"支払可能額（現金 − 今RのEntry Fee {config.entry_fee}円）"
            "を超えています"
        )
    if mode == "cash":
        return "現金が不足しています"
    # "debt"（既定）: 既存の英語文言を完全維持
    return {
        "transfer": "Insufficient free cash for transfer",
        "bounty": "Insufficient free cash for bounty deposit",
        "trade": "Insufficient free cash for trade payment",
        "type_a": "Atomic execution failed - insufficient free cash",
    }[purpose]


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
    found = False
    new_hand = []
    for c in player.hand:
        if c.card_id == card.card_id and not found:
            found = True
            continue
        new_hand.append(c)
    if not found:
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


def compute_mandatory_repayment(debt_balance: int, remaining_rounds: int, k: int = 0) -> int:
    """
    強制最低返済額を計算する（v0.7 §2.2）

    式: ceil(debt_balance ÷ (remaining_rounds + k))
    R12（remaining=1, k=0）の場合は debt_balance 全額。

    Args:
        debt_balance: 利息計上後の借金残高
        remaining_rounds: 残り返済回数（R1終了時=12, R12終了時=1）
        k: 緩和パラメータ（0=完全均等、大きいほど序盤が軽い）

    Returns:
        最低返済額（切り上げ）
    """
    divisor = remaining_rounds + k
    if divisor <= 0:
        return debt_balance
    return math.ceil(debt_balance / divisor)


def swap_card(player: PlayerState, give_card: Card, receive_card: Card) -> PlayerState:
    """
    手札のカードを交換する（v0.7 §3）

    give_card を手札から除去し、receive_card を手札に追加する。
    手札の総枚数は変化しない。

    Args:
        player: プレイヤー状態
        give_card: 差し出すカード
        receive_card: 受け取るカード

    Returns:
        更新されたPlayerState

    Raises:
        ValueError: give_card が手札にない場合
    """
    new_hand = [c for c in player.hand if c.card_id != give_card.card_id]
    if len(new_hand) == len(player.hand):
        raise ValueError(f"{player.player_id} does not have card {give_card.card_id}")
    # card_id 衝突回避: 受け取りカードの ID が手札内で重複する場合はサフィックスで一意化
    actual_card = receive_card
    if any(c.card_id == receive_card.card_id for c in new_hand):
        new_id = f"{receive_card.card_id}_t"
        counter = 2
        while any(c.card_id == new_id for c in new_hand):
            new_id = f"{receive_card.card_id}_t{counter}"
            counter += 1
        actual_card = Card(rank=receive_card.rank, card_id=new_id)
    new_hand.append(actual_card)
    return player.model_copy(update={"hand": new_hand})


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
