"""
経済パラメータ設定モジュール

ゲーム全体の経済パラメータを管理する。
20人版（デフォルト）と12人版（テスト大会用）の切替が可能。
仕様書§11の確定パラメータ一覧に対応。
"""

from pydantic import BaseModel, field_validator


class GameConfig(BaseModel):
    """
    ゲーム経済パラメータの設定クラス

    全パラメータはコンストラクタ引数で上書き可能。
    default_20() / default_12() クラスメソッドで標準設定を取得できる。
    """

    # --- 基本構成 ---
    num_players: int = 20
    """プレイヤー数（§11: 20）"""

    num_rounds: int = 12
    """ラウンド数（§11: 12）"""

    num_markets: int = 3
    """1ラウンドあたりの市場数（§11: 3）"""

    # --- 借入・利息 ---
    loan_min: int = 1_200_000
    """借入最低額（§2.1: 120万円）"""

    loan_max: int = 10_000_000
    """借入最大額（§2.1: 1000万円）"""

    interest_rate: float = 0.015
    """利率（§2.3: 1.5%複利/ラウンド）"""

    # --- 市場・Entry Fee ---
    entry_fee: int = 100_000
    """Entry Fee（§4.2: 10万円、プール加算）"""

    total_prize: int = 19_200_000
    """総賞金予算（§11: 1920万円）"""

    prize_tiers: list[int] = [1_200_000, 1_200_000, 1_200_000, 1_200_000,
                              1_600_000, 1_600_000, 1_600_000, 1_600_000,
                              2_000_000, 2_000_000, 2_000_000, 2_000_000]
    """各ラウンドの総賞金額（§4.1: 逓増傾斜。R1-4:120万, R5-8:160万, R9-12:200万）"""

    market_distribution: str = "equal"
    """ラウンド内3市場への配分方式: "equal"(均等) / "weighted"(傾斜) / "random"(ランダム)"""

    # --- 生還条件 ---
    survival_cash: int = 3_000_000
    """生還条件の現金額（§1.1: 300万円）"""

    # --- 契約・通信 ---
    contract_fee: int = 100_000
    """契約発行料（§6.1: 10万円、提案者負担）"""

    anon_broadcast_fee: int = 100_000
    """匿名通信費（§7.1: 10万円）"""

    anon_broadcast_limit: int = 2
    """匿名通信の1ラウンド/人あたり上限（§7.1: 2通）"""

    anon_bounty_surcharge: float = 0.10
    """匿名報奨の手数料率（§7.2: +10%）"""

    # --- 交渉 ---
    negotiation_max_actions: int = 10
    """Negotiationの1プレイヤーあたり最大アクション数（§5.1: 10回、passは非カウント）"""

    negotiation_max_turns: int = 10
    """Negotiationの最大巡数（§5.1: 最大10巡）"""

    @field_validator("prize_tiers")
    @classmethod
    def validate_prize_tiers(cls, v: list[int]) -> list[int]:
        """賞金スケジュールが1ラウンド分以上あることを検証"""
        if len(v) < 1:
            raise ValueError(f"prize_tiers must have at least 1 entry, got {len(v)}")
        return v

    @classmethod
    def default_20(cls) -> "GameConfig":
        """
        20人版デフォルト設定を返す

        仕様書§11の確定パラメータに準拠。
        """
        return cls()

    @classmethod
    def default_12(cls) -> "GameConfig":
        """
        12人版テスト設定を返す

        モデルロスター仕様書§7に基づき、
        総賞金を人数比(12/20)で縮小。傾斜も比例縮小。
        """
        # 12/20 = 0.6 の比率で各ラウンド賞金を縮小
        ratio = 12 / 20
        base_tiers = [1_200_000] * 4 + [1_600_000] * 4 + [2_000_000] * 4
        scaled_tiers = [int(t * ratio) for t in base_tiers]
        total = sum(scaled_tiers)

        return cls(
            num_players=12,
            total_prize=total,
            prize_tiers=scaled_tiers,
        )

    @classmethod
    def default_8(cls) -> "GameConfig":
        """
        8人版シミュレーション設定を返す

        Bot 8種×1体用。総賞金を人数比(8/20)で縮小。
        逓増傾斜も比例縮小。
        """
        ratio = 8 / 20
        base_tiers = [1_200_000] * 4 + [1_600_000] * 4 + [2_000_000] * 4
        scaled_tiers = [int(t * ratio) for t in base_tiers]
        total = sum(scaled_tiers)

        return cls(
            num_players=8,
            total_prize=total,
            prize_tiers=scaled_tiers,
        )

    @classmethod
    def baseline_v1(cls, num_players: int = 8) -> "GameConfig":
        """
        RULESET_BASELINE_V1: Bot実験で確定した標準設定

        フラット賞金・prize_scale=2.0織込み・survival_cash=200万。
        ルール・経済パラメータはこのプリセットで固定し、以後変更しない。
        """
        base_total = 19_200_000  # 20人版総賞金
        ratio = num_players / 20
        total = int(base_total * ratio * 2.0)  # prize_scale=2.0織込み
        per_round = total // 12
        flat_tiers = [per_round] * 12
        flat_tiers[-1] += total - sum(flat_tiers)  # 端数調整
        return cls(
            num_players=num_players,
            total_prize=total,
            prize_tiers=flat_tiers,
            survival_cash=2_000_000,
        )
