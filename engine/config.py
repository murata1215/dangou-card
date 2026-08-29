"""
経済パラメータ設定モジュール

ゲーム全体の経済パラメータを管理する。
20人版（デフォルト）と12人版（テスト大会用）の切替が可能。
仕様書§11の確定パラメータ一覧に対応。
"""

from pydantic import BaseModel, field_validator

HANDOVER_MEMORY_MAX_CHARS = 3000
"""
引き継ぎメモリ（Handover Memory）の既定最大文字数の単一ソース。

GameConfig.memory_max_chars の既定値、およびviewer側の表示上限
（viewer/log_parser.py: _extract_memory_text）が参照する。
実ログ102件の実測分布で 3000字は 101/102 (99.0%) を無切断でカバーする
（2026-08-22時点の分析。doc/changelog.md参照）。
"""


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

    # --- Season 2 拡張 ---
    fog_rounds: list[int] = []
    """霧のラウンド（S2: [4, 8]）— 使用カード非公開"""

    surge_enabled: bool = False
    """市場高騰の有効フラグ（S2: True）— 参加者>生存者/2でプール2倍"""

    surge_full_participation_max_alive: int = 0
    """市場高騰の全員参加要件の境界人数。この人数以下では全員参加を要求する（0で従来挙動）"""

    final_market_multiplier: int = 1
    """最終市場の基本賞金倍率（S2: 3）— R12基本賞金をN倍"""

    double_up_enabled: bool = False
    """倍掛けの有効フラグ（S2: True）— TAKE or 2倍賭け"""

    mandatory_repay_enabled: bool = False
    """強制最低返済の有効フラグ（S2: True）— v0.7 §2"""

    mandatory_repay_k: int = 0
    """強制返済の緩和パラメータ k（§2.2: k=0 で完全均等返済）"""

    card_trade_enabled: bool = False
    """カードトレードの有効フラグ（S2: True）— v0.7 §3"""

    card_trade_max_per_round: int = 1
    """1プレイヤーあたり1ラウンドのトレード上限（§3.6）"""

    card_trade_last_round: int = 11
    """トレード可能な最終ラウンド（§3.6: R12は不可）"""

    card_trade_broadcast_max: int = 5
    """ブロードキャスト提案の宛先数上限（v0.7.1）"""

    # --- CoT (Chain-of-Thought) ---
    enable_cot: bool = False
    """True: LLMにreasoningフィールド（推論）を要求する。神視点のみ記録、他プレイヤーにはリークしない"""

    # --- 交渉 ---
    negotiation_max_actions: int = 10
    """Negotiationの1プレイヤーあたり最大アクション数（§5.1: 10回、passは非カウント）"""

    negotiation_max_turns: int = 10
    """Negotiationの最大巡数（§5.1: 最大10巡）"""

    # --- 引き継ぎメモリ（Handover Memory） ---
    memory_enabled: bool = False
    """
    True: 各ラウンド終了後、AIに次ラウンドへ引き継ぐ自由記述メモ（memory）を
    1枚だけ書かせる。前ラウンドのmemory＋当ラウンドの会話・契約・結果を材料に、
    次の自分に何を残すかはモデルの自由。渡すのは常に最新の1枚のみ（累積しない）。
    既定False。S2プリセットでのみTrue。
    """

    memory_max_chars: int = HANDOVER_MEMORY_MAX_CHARS
    """引き継ぎメモリの最大文字数（超過分は意味境界を優先して切り詰める）"""

    # --- 脱落時の最終コメント（FINAL_REFLECTION） ---
    final_reflection_enabled: bool = False
    """
    True: 脱落確定ラウンドの末尾で、脱落者本人に1回だけ最終コメント
    （敗因・他プレイヤー評価・最後の一言など）を書かせる。
    ゲーム結果・勝敗・資産・契約・生存判定には一切影響しない演出/記録専用。
    既定Falseで旧挙動保持。memory_enabled とは独立フラグ
    （Memoryを切ってもFINAL_REFLECTIONだけ試せる）。
    """

    final_reflection_max_chars: int = 2000
    """最終コメントの保存上限（超過分は意味境界を優先して切り詰める）"""

    final_reflection_max_tokens: int = 3000
    """final_reflection callだけに適用するmax_output_tokens。
    これはprovider側の物理的truncationを避けるためのハード上限であり、
    狙う長さではない（プロンプト側は800〜1500字程度の簡潔な本文を促す。
    3000という数値自体はプロンプトに一切出さない）。2026-08-22の実API
    疎通試験で旧値1000だとL1(claude-haiku-4-5)がfinish_reason=max_tokens
    で出力途中切断されることを実測したため引き上げた。"""

    # --- ゲーム終了後の全員答え合わせ（POST_GAME_REFLECTION） ---
    post_game_reflection_enabled: bool = False
    """
    True: ゲーム完全終了後（結果確定・GAME_END後）、全player（脱落者＋生還者）に
    1回だけ神視点の答え合わせ振り返りを書かせる。匿名通信の真の掲載者・DM本文・
    秘密契約条項などゲーム中は秘匿されていた情報をpromptに投入する唯一のcall。
    ゲーム結果・勝敗・資産・契約・生存判定には一切影響しない演出/記録専用。
    既定Falseで旧挙動保持。本サイクルではプリセット（default_8_s2/baseline_v1_s2）
    でもFalseのまま据え置き、smokeで実入力トークンを実測してから別コミットで有効化する。
    """

    post_game_reflection_max_chars: int = 1000
    """POST_GAME_REFLECTIONの散文フィールド1つあたりの保存上限目安。
    comment単独の上限であり、他の任意フィールドを含めた合計上限は
    parse_post_game_reflection()側で別途1,800字に制御する。"""

    post_game_reflection_max_tokens: int = 3000
    """post_game_reflection callだけに適用するmax_output_tokens。
    final_reflection_max_tokensと同じ3000を据え置く。FINAL_REFLECTION実測
    （L1 687 / L6 571 tok、上限の19〜23%）はフィールド数がPOST_GAMEの
    7キーの半分程度であり、それでも上限に対して十分な余裕がある。"""

    # --- 本戦LLMコスト上限（GameCostBudgetを明示注入した試合だけで有効） ---
    per_player_game_cost_cap_usd: float = 5.0
    """本戦1試合におけるplayer単位のLLMコスト上限（USD）"""

    game_cost_cap_usd: float = 40.0
    """本戦1試合における全LLM合計コスト上限（USD）"""

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
    def default_8_s2(cls) -> "GameConfig":
        """
        8人版S2設定を返す

        default_8()ベースにSeason 2拡張を全有効化。
        """
        base = cls.default_8()
        return base.model_copy(update={
            "fog_rounds": [],
            "surge_enabled": True,
            "surge_full_participation_max_alive": 3,
            "final_market_multiplier": 3,
            "double_up_enabled": True,
            "mandatory_repay_enabled": True,
            "mandatory_repay_k": 0,
            "card_trade_enabled": True,
            "memory_enabled": True,
            "final_reflection_enabled": True,
        })

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

    @classmethod
    def baseline_v1_s2(cls, num_players: int = 8) -> "GameConfig":
        """
        RULESET_BASELINE_V1 + Season 2拡張

        baseline_v1ベースにS2メカニクスを全有効化。
        """
        base = cls.baseline_v1(num_players)
        return base.model_copy(update={
            "fog_rounds": [],
            "surge_enabled": True,
            "surge_full_participation_max_alive": 3,
            "final_market_multiplier": 3,
            "double_up_enabled": True,
            "mandatory_repay_enabled": True,
            "mandatory_repay_k": 0,
            "card_trade_enabled": True,
            "memory_enabled": True,
            "final_reflection_enabled": True,
            # v0.8 D1: 契約発行料を無料化。デフォルト(100,000)は温存し、
            # S2プリセットでのみ0円に上書きする。
            "contract_fee": 0,
        })
