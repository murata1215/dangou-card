"""
v0.10 型C（条件付き金銭契約）の Settlement 統合テスト

engine.settlement.execute_settlement() を通した結合レベルの検証。
単体レベル（validate_type_c_details / evaluate_type_c_condition /
get_active_type_c_obligations / expire_obligations）は tests/test_contracts.py
で既に検証済みのため、ここでは Settlement の8Step順序・Atomic合流・
不変条件（スナップショット原則・倍掛け除外）に絞って検証する。
"""

from engine.config import GameConfig
from engine.models import (
    Card, CardRank, Market, MarketCommit, ObligationType, DoubleUpDeposit,
)
from engine import contracts as contract_ops
from engine.settlement import execute_settlement
from engine.events import EventLogger
from tests.conftest import make_player, make_contract, make_obligation, make_market


def _config(**overrides) -> GameConfig:
    """型C有効・高騰無効・entry_fee基準のテスト用最小設定"""
    base = {"type_c_enabled": True, "surge_enabled": False, "free_cash_mode": "entry_fee"}
    base.update(overrides)
    return GameConfig(**base)


def _commit(pid: str, market_id: str, rank: CardRank = CardRank.HIGH_CARD) -> MarketCommit:
    """make_player()の既定フルデッキに実在するcard_id（例: HIGH_CARD_1）を使う。
    use_card()はplayer.handとcard_idの完全一致を要求するため、
    プレイヤーIDを含む独自card_idは使えない（各プレイヤーは独立にフルデッキを持つ）。
    """
    return MarketCommit(player_id=pid, market_id=market_id,
                         card=Card(rank=rank, card_id=f"{rank.name}_1"))


def _type_c_ob(obligor, counterparty, round_num, amount, condition_type, condition,
               obligation_id="C1_OB01", contract_id="C1"):
    return make_obligation(
        obligation_id, contract_id, obligor, counterparty, ObligationType.TYPE_C_CONDITIONAL,
        round_num=round_num,
        details={"amount": amount, "condition_type": condition_type, "condition": condition},
    )


class TestMarketWinnerCondition:
    """condition_type=market_winner の発火/不発（§2.1）"""

    def test_fires_and_merges_into_atomic_with_type_a(self):
        """型C発火時、型Aと同一Atomicに合流し実際に支払われる"""
        ob = _type_c_ob("P01", "P02", round_num=3, amount=300_000,
                         condition_type="market_winner",
                         condition={"market_id": "M01", "target_player": "P01"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {"P01": make_player("P01", 2_000_000), "P02": make_player("P02", 500_000)}
        commits = [_commit("P01", "M01")]
        market = make_market("M01", 1_000_000)
        logger = EventLogger()
        type_c_records: list[dict] = []

        players, contracts, *_ = execute_settlement(
            players, [market], commits, [c], [], round_num=3, config=_config(),
            logger=logger, type_c_records=type_c_records,
        )

        evaluated = [e for e in logger.events if e.event_type == "TYPE_C_EVALUATED"]
        assert len(evaluated) == 1
        assert evaluated[0].data["result"] == "fired"
        # 支払われている: P01（義務者、市場勝者でもある）→P02へ30万
        assert players["P02"].cash == 500_000 + 300_000
        assert len(type_c_records) == 1
        assert type_c_records[0]["result"] == "fired"

    def test_not_fired_when_target_not_winner(self):
        ob = _type_c_ob("P01", "P02", round_num=3, amount=300_000,
                         condition_type="market_winner",
                         condition={"market_id": "M01", "target_player": "P03"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {
            "P01": make_player("P01", 2_000_000), "P02": make_player("P02", 500_000),
            "P03": make_player("P03", 500_000),
        }
        commits = [_commit("P01", "M01")]
        market = make_market("M01", 1_000_000)
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, [market], commits, [c], [], round_num=3, config=_config(), logger=logger,
        )

        evaluated = [e for e in logger.events if e.event_type == "TYPE_C_EVALUATED"]
        assert evaluated[0].data["result"] == "not_fired"
        # 不発は残高に一切触れない（P02のcashは変化なし）
        assert players["P02"].cash == 500_000
        # 型Aの履行イベントとしては記録されない
        assert [e for e in logger.events if e.event_type == "TYPE_A_EXECUTION"] == []

    def test_zero_participants_not_fired(self):
        """参加者0人（§2.1）→ winners=[] のため不成立"""
        ob = _type_c_ob("P01", "P02", round_num=3, amount=300_000,
                         condition_type="market_winner",
                         condition={"market_id": "M01", "target_player": "P01"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {"P01": make_player("P01", 2_000_000), "P02": make_player("P02", 500_000)}
        market = make_market("M01", 1_000_000)
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, [market], [], [c], [], round_num=3, config=_config(), logger=logger,
        )

        evaluated = [e for e in logger.events if e.event_type == "TYPE_C_EVALUATED"]
        assert evaluated[0].data["result"] == "not_fired"

    def test_solo_participation_winner_fires(self):
        """空き巣（参加者1人）でも成立する（§2.1）"""
        ob = _type_c_ob("P01", "P02", round_num=3, amount=300_000,
                         condition_type="market_winner",
                         condition={"market_id": "M01", "target_player": "P01"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {"P01": make_player("P01", 2_000_000), "P02": make_player("P02", 500_000)}
        commits = [_commit("P01", "M01")]
        market = make_market("M01", 1_000_000)
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, [market], commits, [c], [], round_num=3, config=_config(), logger=logger,
        )
        evaluated = [e for e in logger.events if e.event_type == "TYPE_C_EVALUATED"]
        assert evaluated[0].data["result"] == "fired"

    def test_target_player_past_eliminated_not_fired(self):
        """対象者が過去に脱落済み（不参加）→ market_winner は不成立、
        しかし evaluate_type_c_condition(eliminated)なら成立する非対称性を確認"""
        ob = _type_c_ob("P01", "P02", round_num=3, amount=300_000,
                         condition_type="market_winner",
                         condition={"market_id": "M01", "target_player": "P03"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {
            "P01": make_player("P01", 2_000_000), "P02": make_player("P02", 500_000),
            "P03": make_player("P03", 0).model_copy(update={"is_alive": False}),
        }
        commits = [_commit("P01", "M01")]
        market = make_market("M01", 1_000_000)
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, [market], commits, [c], [], round_num=3, config=_config(), logger=logger,
        )
        evaluated = [e for e in logger.events if e.event_type == "TYPE_C_EVALUATED"]
        assert evaluated[0].data["result"] == "not_fired"


class TestEliminatedCondition:
    """condition_type=eliminated の発火/不発（§2.1）"""

    def test_fires_via_same_settlement_type_b_violation(self):
        """同一Settlementの型B監査脱落でeliminated成立"""
        # P03が型B義務（M01参加）を負っているが違反してM02に参加 → 同一Settlementで脱落
        ob_b = make_obligation("C2_OB01", "C2", "P03", "P01", ObligationType.TYPE_B_MARKET,
                                round_num=3, details={"market_id": "M01"})
        c_b = make_contract("C2", "P01", ["P01", "P03"], [ob_b])

        ob_c = _type_c_ob("P01", "P02", round_num=3, amount=200_000,
                           condition_type="eliminated", condition={"target_player": "P03"})
        c_c = make_contract("C1", "P01", ["P01", "P02"], [ob_c])

        players = {
            "P01": make_player("P01", 1_000_000), "P02": make_player("P02", 500_000),
            "P03": make_player("P03", 500_000),
        }
        commits = [_commit("P01", "M01"), _commit("P03", "M02")]  # P03は違反
        markets = [make_market("M01", 500_000), make_market("M02", 500_000)]
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, markets, commits, [c_b, c_c], [], round_num=3, config=_config(),
            logger=logger,
        )

        assert players["P03"].is_alive is False
        evaluated = [
            e for e in logger.events
            if e.event_type == "TYPE_C_EVALUATED" and e.data["obligation_id"] == "C1_OB01"
        ]
        assert evaluated[0].data["result"] == "fired"
        assert players["P02"].cash == 500_000 + 200_000

    def test_fires_via_past_round_elimination(self):
        """過去ラウンドで既に脱落済み（is_alive=False）でも成立"""
        ob = _type_c_ob("P01", "P02", round_num=3, amount=200_000,
                         condition_type="eliminated", condition={"target_player": "P03"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {
            "P01": make_player("P01", 1_000_000), "P02": make_player("P02", 500_000),
            "P03": make_player("P03", 0).model_copy(update={"is_alive": False}),
        }
        commits = [_commit("P01", "M01")]
        market = make_market("M01", 500_000)
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, [market], commits, [c], [], round_num=3, config=_config(), logger=logger,
        )
        evaluated = [e for e in logger.events if e.event_type == "TYPE_C_EVALUATED"]
        assert evaluated[0].data["result"] == "fired"
        assert players["P02"].cash == 500_000 + 200_000

    def test_not_fired_when_target_alive_and_not_eliminated(self):
        ob = _type_c_ob("P01", "P02", round_num=3, amount=200_000,
                         condition_type="eliminated", condition={"target_player": "P03"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {
            "P01": make_player("P01", 1_000_000), "P02": make_player("P02", 500_000),
            "P03": make_player("P03", 500_000),
        }
        commits = [_commit("P01", "M01")]
        market = make_market("M01", 500_000)
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, [market], commits, [c], [], round_num=3, config=_config(), logger=logger,
        )
        evaluated = [e for e in logger.events if e.event_type == "TYPE_C_EVALUATED"]
        assert evaluated[0].data["result"] == "not_fired"
        assert players["P02"].cash == 500_000  # 不発は残高に触れない

    def test_third_party_not_yet_due_obligation_untouched(self):
        """第三者（条件対象者）の脱落では義務は失効せず、決済ラウンドで判定される
        （決済ラウンド未到来のうちは get_active_type_c_obligations がそもそも
        対象外にするため触れられない）"""
        ob = _type_c_ob("P01", "P02", round_num=6, amount=200_000,
                         condition_type="eliminated", condition={"target_player": "P03"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob], round_created=1)
        # round_num=6が決済対象。round_num=3時点ではまだ対象外
        active_at_3 = contract_ops.get_active_type_c_obligations([c], 3)
        assert active_at_3 == []
        active_at_6 = contract_ops.get_active_type_c_obligations([c], 6)
        assert len(active_at_6) == 1
        assert active_at_6[0].is_expired is False


class TestMarketSurgeCondition:
    """condition_type=market_surge の発火/不発（§2.1）"""

    def test_fires_when_surge_triggered(self):
        ob = _type_c_ob("P01", "P02", round_num=3, amount=200_000,
                         condition_type="market_surge", condition={"market_id": "M01"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {
            "P01": make_player("P01", 1_000_000), "P02": make_player("P02", 500_000),
            "P03": make_player("P03", 500_000),
        }
        commits = [_commit("P01", "M01"), _commit("P03", "M01")]  # 2人参加、生存者2人 → surge
        market = make_market("M01", 500_000)
        config = _config(surge_enabled=True, num_players=2)
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, [market], commits, [c], [], round_num=3, config=config, logger=logger,
        )
        evaluated = [e for e in logger.events if e.event_type == "TYPE_C_EVALUATED"]
        assert evaluated[0].data["result"] == "fired"
        assert players["P02"].cash == 500_000 + 200_000

    def test_not_fired_when_no_surge(self):
        ob = _type_c_ob("P01", "P02", round_num=3, amount=200_000,
                         condition_type="market_surge", condition={"market_id": "M01"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {"P01": make_player("P01", 1_000_000), "P02": make_player("P02", 500_000)}
        commits = [_commit("P01", "M01")]  # surge無効設定なので発生しない
        market = make_market("M01", 500_000)
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, [market], commits, [c], [], round_num=3, config=_config(), logger=logger,
        )
        evaluated = [e for e in logger.events if e.event_type == "TYPE_C_EVALUATED"]
        assert evaluated[0].data["result"] == "not_fired"
        assert players["P02"].cash == 500_000


class TestAtomicMergeAndEliminationCascade:
    """型A+発火型Cの合算Atomic、支払不能→全滅→脱落（§1.3, §3不変条件, §6.6）"""

    def test_insufficient_capacity_all_fail_and_obligor_eliminated(self):
        """型A50万＋発火型C100万、capacity不足 → 全滅（両方とも不履行）→脱落。
        受取側は未回収のまま"""
        ob_a = make_obligation("C1_OB01", "C1", "P01", "P02", ObligationType.TYPE_A_PAYMENT,
                                round_num=3, details={"amount": 500_000})
        ob_c = _type_c_ob("P01", "P03", round_num=3, amount=1_000_000,
                           condition_type="market_winner",
                           condition={"market_id": "M01", "target_player": "P01"},
                           obligation_id="C1_OB02")
        c = make_contract("C1", "P01", ["P01", "P02", "P03"], [ob_a, ob_c])
        players = {
            "P01": make_player("P01", 800_000),  # 50万+100万=150万必要だが80万しかない
            "P02": make_player("P02", 500_000),
            "P03": make_player("P03", 500_000),
        }
        commits = [_commit("P01", "M01")]
        market = make_market("M01", 500_000)
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, [market], commits, [c], [], round_num=3, config=_config(), logger=logger,
        )

        assert players["P01"].is_alive is False
        # 両受取先とも未回収（残高が支払い分だけ増えていない）
        assert players["P02"].cash == 500_000
        assert players["P03"].cash == 500_000
        assert [e for e in logger.events if e.event_type == "TYPE_A_FAILURE"] != []

    def test_eliminated_party_type_c_excluded_from_atomic(self):
        """脱落者の型C（obligor/counterpartyのいずれも）はAtomicから除外される"""
        # P03が同一Settlementで型B違反により脱落する
        ob_b = make_obligation("C2_OB01", "C2", "P03", "P01", ObligationType.TYPE_B_MARKET,
                                round_num=3, details={"market_id": "M01"})
        c_b = make_contract("C2", "P01", ["P01", "P03"], [ob_b])
        # P03がcounterpartyの型C義務（market_winner=P01, P03は無関係の受取人）
        ob_c = _type_c_ob("P01", "P03", round_num=3, amount=300_000,
                           condition_type="market_winner",
                           condition={"market_id": "M01", "target_player": "P01"})
        c_c = make_contract("C1", "P01", ["P01", "P03"], [ob_c])

        players = {
            "P01": make_player("P01", 1_000_000), "P03": make_player("P03", 500_000),
        }
        commits = [_commit("P01", "M01"), _commit("P03", "M02")]  # P03違反
        markets = [make_market("M01", 500_000), make_market("M02", 500_000)]
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, markets, commits, [c_b, c_c], [], round_num=3, config=_config(),
            logger=logger,
        )
        assert players["P03"].is_alive is False
        # P03は脱落済みのため型Cが発火判定されても支払いは実行されない
        assert [e for e in logger.events if e.event_type == "TYPE_A_EXECUTION"] == []
        # P01のcashは市場勝利分（賞金）のみ増え、型C支払30万分は減っていない
        # （Entry FeeはCommitフェイズで実引き落とし済みの前提のためexecute_settlement
        # 単体呼び出しのこのテストでは差し引かれない。cash = 1,000,000 + 市場獲得額）
        market_result = next(
            e for e in logger.events if e.event_type == "MARKET_RESULT" and e.data["market_id"] == "M01"
        )
        expected = 1_000_000 + market_result.data["prize_per_winner"]
        assert players["P01"].cash == expected


class TestSnapshotPrincipleAndDoubleUpExclusion:
    """スナップショット原則・倍掛け除外の確認（§6.6, 検証項目5・6）"""

    def test_type_c_receipt_not_usable_as_own_payment_source_same_settlement(self):
        """型C受取は同一Settlementの支払原資にならない（スナップショット原則）。
        P02は型Aで50万支払う義務があり現金30万しかないが、同時に型Cで100万を
        受け取る予定でも、支払原資には使えず履行不能で脱落する"""
        ob_a = make_obligation("C1_OB01", "C1", "P02", "P03", ObligationType.TYPE_A_PAYMENT,
                                round_num=3, details={"amount": 500_000})
        ob_c = _type_c_ob("P01", "P02", round_num=3, amount=1_000_000,
                           condition_type="market_winner",
                           condition={"market_id": "M01", "target_player": "P01"},
                           obligation_id="C1_OB02")
        c = make_contract("C1", "P01", ["P01", "P02", "P03"], [ob_a, ob_c])
        players = {
            "P01": make_player("P01", 1_500_000),
            "P02": make_player("P02", 300_000),
            "P03": make_player("P03", 500_000),
        }
        commits = [_commit("P01", "M01")]
        market = make_market("M01", 500_000)
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, [market], commits, [c], [], round_num=3, config=_config(), logger=logger,
        )
        assert players["P02"].is_alive is False

    def test_type_c_receipt_excluded_from_double_up_success(self):
        """型C受取は倍掛け成功判定に含まれない（既存挙動の確認、非市場勝利のため）"""
        ob = _type_c_ob("P01", "P02", round_num=3, amount=200_000,
                         condition_type="eliminated", condition={"target_player": "P99"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {
            "P01": make_player("P01", 1_000_000), "P02": make_player("P02", 500_000),
        }
        commits = [_commit("P01", "M01")]
        market = make_market("M01", 500_000)
        # P02は市場に参加せず型Cのみ受け取る。倍掛け成功判定は市場勝利のみで行われる
        dep = DoubleUpDeposit(player_id="P02", deposit_amount=100_000,
                              deposited_round=2, success_round=3)
        logger = EventLogger()

        players, contracts, bounties, carryovers, market_results, du_summary = execute_settlement(
            players, [market], commits, [c], [], round_num=3, config=_config(), logger=logger,
            double_up_deposits=[dep],
        )
        # P02は市場に不参加＝non_solo_winnersに含まれない→倍掛けは失敗（没収）
        assert du_summary["fail"] == 1
        assert du_summary["success"] == 0
        assert dep.success is False


class TestTypeCEnabledFlagGating:
    """type_c_enabled=False で S1 完全回帰を保証する"""

    def test_disabled_flag_skips_all_type_c_processing(self):
        ob = _type_c_ob("P01", "P02", round_num=3, amount=200_000,
                         condition_type="market_winner",
                         condition={"market_id": "M01", "target_player": "P01"})
        c = make_contract("C1", "P01", ["P01", "P02"], [ob])
        players = {"P01": make_player("P01", 1_000_000), "P02": make_player("P02", 500_000)}
        commits = [_commit("P01", "M01")]
        market = make_market("M01", 500_000)
        logger = EventLogger()

        players, contracts, *_ = execute_settlement(
            players, [market], commits, [c], [], round_num=3,
            config=_config(type_c_enabled=False), logger=logger,
        )
        assert [e for e in logger.events if e.event_type == "TYPE_C_EVALUATED"] == []
        # 発火条件を満たしていても支払いは一切発生しない（無効時は完全に無視）
        assert players["P02"].cash == 500_000
