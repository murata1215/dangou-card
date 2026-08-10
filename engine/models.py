"""
Pydantic v2 スキーマ定義モジュール

ゲーム内の全エンティティ（カード・市場・プレイヤー・契約・報奨・アクション・イベント）を
Pydantic v2 のモデルとして定義する。
"""

from __future__ import annotations

from enum import IntEnum, Enum
from typing import Literal, Annotated, Any

from pydantic import BaseModel, Field, computed_field


# =============================================================================
# カード関連（§3）
# =============================================================================

class CardRank(IntEnum):
    """
    カードのランク定義（§3.1）

    数値が大きいほど強い。ONE_PAIR/HIGH_CARDのみ2枚、他は1枚。
    """
    HIGH_CARD = 1
    ONE_PAIR = 2
    TWO_PAIR = 3
    THREE_OF_A_KIND = 4
    STRAIGHT = 5
    FLUSH = 6
    FULL_HOUSE = 7
    FOUR_OF_A_KIND = 8
    STRAIGHT_FLUSH = 9
    ROYAL_FLUSH = 10


class Card(BaseModel):
    """
    1枚のカード

    同ランク複数枚の場合を区別するためcard_idを持つ。
    例: HIGH_CARD_1, HIGH_CARD_2, ONE_PAIR_1, ONE_PAIR_2
    """
    rank: CardRank
    card_id: str

    def __hash__(self) -> int:
        return hash(self.card_id)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Card):
            return self.card_id == other.card_id
        return NotImplemented


# =============================================================================
# 市場関連（§4）
# =============================================================================

class Market(BaseModel):
    """
    1つの市場（§4.1）

    base_prizeはスケジュールによる基本賞金。
    carryoverは前ラウンドからの繰越金。
    Entry Feeはresolution時にプールへ加算される。
    """
    market_id: str
    """市場ID（例: "M01", "M02", "M03"）"""

    base_prize: int
    """スケジュールによる基本賞金"""

    carryover: int = 0
    """前ラウンドからのキャリーオーバー額"""

    @computed_field
    @property
    def prize_pool(self) -> int:
        """Entry Fee加算前の賞金プール"""
        return self.base_prize + self.carryover


class MarketCommit(BaseModel):
    """
    プレイヤーの市場コミット（§4.3）

    秘密提出される「市場+カード」の組合せ。
    """
    player_id: str
    market_id: str
    card: Card


class MarketResult(BaseModel):
    """
    市場決着結果

    勝者・獲得額・参加者情報を記録する。
    """
    market_id: str
    participants: list[MarketCommit]
    """参加者のコミット一覧"""

    winners: list[str]
    """勝者のプレイヤーID（同ランク時は複数）"""

    prize_per_winner: int
    """1人あたりの獲得額（均等分配時は端数切捨）"""

    total_pool: int
    """Entry Fee加算後の総賞金プール"""


# =============================================================================
# プレイヤー関連（§2）
# =============================================================================

class PlayerState(BaseModel):
    """
    プレイヤーの状態（§2）

    現金・借金・手札・生存状態を管理する。
    Free Cash = max(0, Cash - DebtBalance) は computed_field で自動算出。
    """
    player_id: str
    """プレイヤーID（"P01"〜"P20"）"""

    cash: int
    """現金残高"""

    debt_balance: int
    """借金残高（利息込み）"""

    initial_loan: int
    """初期借入額（公開情報、§2.1）"""

    hand: list[Card] = Field(default_factory=list)
    """未使用カード"""

    used_cards: list[Card] = Field(default_factory=list)
    """使用済みカード（全公開情報、§3.2）"""

    is_alive: bool = True
    """生存フラグ"""

    elimination_reason: str | None = None
    """脱落理由（"contract_violation" / "bankruptcy" / "condition_not_met"）"""

    elimination_round: int | None = None
    """脱落したラウンド番号"""

    @computed_field
    @property
    def free_cash(self) -> int:
        """
        自由資金（§2.4）

        Free Cash = max(0, 現金残高 − 借金残高[利息込み])
        借金横流し防止のため、借金分を差し引いた残りのみが自由に使える。
        """
        return max(0, self.cash - self.debt_balance)


# =============================================================================
# 契約関連（§6）
# =============================================================================

class ObligationType(str, Enum):
    """
    義務の種別（§6.2）

    型A: 金銭移動（自動執行）
    型B: 行動制約（自動監査、違反=即時脱落）
    """
    TYPE_A_PAYMENT = "type_a_payment"
    """金銭移動（即時 or 指定ラウンド）"""

    TYPE_B_MARKET = "type_b_market"
    """指定ラウンドに参加する市場の指定"""

    TYPE_B_CARD = "type_b_card"
    """指定ラウンドで使用するカードの指定"""

    TYPE_B_NO_MARKET = "type_b_no_market"
    """特定市場へ参加しないこと"""


class Obligation(BaseModel):
    """
    1つの義務単位（§6.3）

    契約は義務の集合として管理される。
    各義務は「義務者」と「相手方」の組で定義され、
    脱落時は義務単位で失効判定が行われる。
    """
    obligation_id: str
    """義務ID"""

    contract_id: str
    """所属する契約のID"""

    obligor: str
    """義務者のプレイヤーID"""

    counterparty: str
    """相手方のプレイヤーID"""

    ob_type: ObligationType
    """義務の種別"""

    round_num: int
    """対象ラウンド（このラウンドのSettlementで判定/執行）"""

    details: dict[str, Any]
    """
    詳細パラメータ:
    - 型A: {"amount": int} — 支払額
    - 型B_MARKET: {"market_id": str} — 参加すべき市場
    - 型B_CARD: {"card_rank": str} — 使用すべきカードランク名
    - 型B_NO_MARKET: {"market_id": str} — 参加してはいけない市場
    """

    is_fulfilled: bool = False
    """履行済みフラグ"""

    is_expired: bool = False
    """失効済みフラグ（脱落者関連で失効した場合True）"""


class ContractStatus(str, Enum):
    """契約のステータス"""
    PROPOSED = "proposed"
    """提案中（署名待ち）"""

    ACTIVE = "active"
    """成立・有効"""

    COMPLETED = "completed"
    """全義務履行済み"""

    EXPIRED = "expired"
    """全義務失効済み"""


class Contract(BaseModel):
    """
    正式契約（§6）

    2人以上の連名で成立。存在と当事者名は公示、内容は当事者のみ閲覧可能。
    """
    contract_id: str
    """契約ID"""

    proposer: str
    """提案者のプレイヤーID（発行料負担者）"""

    parties: list[str]
    """全当事者のプレイヤーID"""

    signed_by: list[str] = Field(default_factory=list)
    """署名済みのプレイヤーID"""

    obligations: list[Obligation] = Field(default_factory=list)
    """義務のリスト"""

    round_created: int
    """契約が提案されたラウンド"""

    status: ContractStatus = ContractStatus.PROPOSED
    """契約ステータス"""


# =============================================================================
# 報奨関連（§7.2）
# =============================================================================

class BountyType(str, Enum):
    """
    報奨の種別（§7.2）
    """
    ACHIEVEMENT = "achievement"
    """達成者型（型A）: 達成者自身の行動として観測可能な事実を条件"""

    EVENT = "event"
    """イベント型/保険型（型B）: 特定イベント（脱落等）を条件"""


class BountyConditionType(str, Enum):
    """報奨条件の種別"""
    MARKET_WIN_AGAINST = "market_win_against"
    """特定プレイヤーと同一市場に参加し、より高いカードで勝利"""

    SAME_MARKET = "same_market"
    """特定プレイヤーと同一市場に参加"""

    PLAYER_ELIMINATED = "player_eliminated"
    """特定プレイヤーが脱落"""


class Bounty(BaseModel):
    """
    公開報奨（§7.2）

    預託金はシステムに預けられ、条件達成時に支払われる。
    匿名掲載可能（手数料+10%）。取り下げ自由（預託金返還）。
    """
    bounty_id: str
    """報奨ID"""

    poster: str
    """掲載者のプレイヤーID"""

    amount: int
    """報奨額"""

    bounty_type: BountyType
    """報奨の種別"""

    condition_type: BountyConditionType
    """条件の種別"""

    condition: dict[str, Any]
    """
    条件パラメータ:
    - MARKET_WIN_AGAINST: {"target_player": str}
    - SAME_MARKET: {"target_player": str}
    - PLAYER_ELIMINATED: {"target_player": str}
    """

    beneficiary: str | None = None
    """受取人（イベント型で指定。達成者型はNone→条件達成者が受取人）"""

    round_num: int
    """対象ラウンド（このラウンドのSettlementで判定）"""

    anonymous: bool = False
    """匿名掲載フラグ"""

    is_active: bool = True
    """有効フラグ（取り下げ時にFalse）"""

    deposited: int
    """預託額（報奨額 + 匿名手数料）"""


# =============================================================================
# アクション（§9.4）
# =============================================================================

class DmAction(BaseModel):
    """DM送信アクション"""
    type: Literal["dm"] = "dm"
    player_id: str
    to: str
    message: str


class BroadcastAction(BaseModel):
    """全体発言アクション"""
    type: Literal["broadcast"] = "broadcast"
    player_id: str
    message: str


class MarketCommitAction(BaseModel):
    """市場コミットアクション（§4.3）"""
    type: Literal["market_commit"] = "market_commit"
    player_id: str
    market_id: str
    card_rank: str
    """使用するカードのランク名（例: "ONE_PAIR"）"""


class ContractProposeAction(BaseModel):
    """
    契約提案アクション（§6.1）

    発行料10万円は提案者負担。terms は義務定義のリスト。
    """
    type: Literal["contract_propose"] = "contract_propose"
    player_id: str
    with_players: list[str]
    """契約相手のプレイヤーID"""

    terms: list[dict[str, Any]]
    """
    義務定義のリスト。各要素:
    {
        "obligor": str,
        "counterparty": str,
        "ob_type": str,
        "round_num": int,
        "details": dict
    }
    """


class ContractSignAction(BaseModel):
    """契約署名アクション"""
    type: Literal["contract_sign"] = "contract_sign"
    player_id: str
    contract_id: str


class AnonymousBroadcastAction(BaseModel):
    """匿名通信アクション（§7.1）"""
    type: Literal["anonymous_broadcast"] = "anonymous_broadcast"
    player_id: str
    message: str


class BountyPostAction(BaseModel):
    """報奨掲載アクション（§7.2）"""
    type: Literal["bounty_post"] = "bounty_post"
    player_id: str
    amount: int
    bounty_type: str
    """"achievement" or "event" """

    condition_type: str
    """条件種別"""

    condition: dict[str, Any]
    """条件パラメータ"""

    beneficiary: str | None = None
    """受取人（イベント型で必要）"""

    round_num: int
    """対象ラウンド"""

    anonymous: bool = False


class BountyCancelAction(BaseModel):
    """報奨取り下げアクション"""
    type: Literal["bounty_cancel"] = "bounty_cancel"
    player_id: str
    bounty_id: str


class TransferAction(BaseModel):
    """
    送金アクション（§9.4）

    即時決済。amount <= FreeCash をシステム検証。
    """
    type: Literal["transfer"] = "transfer"
    player_id: str
    to: str
    amount: int


class RepayAction(BaseModel):
    """
    借金返済アクション（§2.3）

    Negotiation中のアクション。FreeCash非適用（システム向け支払い）。
    """
    type: Literal["repay"] = "repay"
    player_id: str
    amount: int


class PassAction(BaseModel):
    """
    パスアクション（§5.1）

    手番スキップ。アクション枠を消費しない。
    """
    type: Literal["pass"] = "pass"
    player_id: str


# アクションのUnion型（discriminated union）
Action = Annotated[
    DmAction | BroadcastAction | MarketCommitAction |
    ContractProposeAction | ContractSignAction |
    AnonymousBroadcastAction | BountyPostAction | BountyCancelAction |
    TransferAction | RepayAction | PassAction,
    Field(discriminator="type")
]


# =============================================================================
# イベント（ログ用）
# =============================================================================

class GameEvent(BaseModel):
    """
    ゲームイベント（JSONL出力用）

    全ゲームイベントを時系列で記録する。
    リプレイ・観戦UIの将来入力となるため、
    イベント種別・タイムスタンプ・ラウンド・フェーズ・全状態変化を含む。
    """
    event_type: str
    """イベント種別（例: GAME_START, MARKET_RESULT, ELIMINATION等）"""

    timestamp: str
    """ISO8601形式のタイムスタンプ"""

    round_num: int
    """ラウンド番号（0=ゲーム開始前）"""

    phase: str
    """フェイズ名（market_open / negotiation / commit / settlement / finance）"""

    step: int | None = None
    """Settlement内のStep番号（1-8、Settlement以外はNone）"""

    data: dict[str, Any] = Field(default_factory=dict)
    """イベント固有データ"""
