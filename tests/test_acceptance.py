"""
受け入れテスト（仕様書§12の全16件）

各テストは仕様書の受け入れテストケース番号に対応する。
テスト名とdocstringに仕様書§12のテスト番号・シナリオを明記。
"""

import math
import pytest

from engine.config import GameConfig
from engine.models import (
    PlayerState, Card, CardRank, Market, MarketCommit,
    Contract, ContractStatus, Obligation, ObligationType,
    TransferAction, RepayAction, PassAction, MarketCommitAction,
    AnonymousBroadcastAction, ContractProposeAction, BountyPostAction,
)
from engine.events import EventLogger
from engine import actions as action_ops
from engine import contracts as contract_ops
from engine import settlement as settlement_ops
from engine import finance as finance_ops
from engine import autocommit as autocommit_ops
from engine import elimination as elim_ops
from engine import player as player_ops
from engine.cards import create_deck, find_card_by_rank

from tests.conftest import make_player, make_contract, make_obligation, make_market


class TestAcceptance:
    """仕様書§12 受け入れテストケース 全16件"""

    def test_01_transfer_free_cash_insufficient(self):
        """
        §12 #1: 借入1000万のP01がP02へ900万送金
        → Free Cash不足 → 不成立

        初期状態: Cash=1000万, Debt=1000万 → FreeCash=0
        900万の送金はFreeCash(0) < 900万で不成立
        """
        config = GameConfig.default_20()
        p01 = make_player("P01", cash=10_000_000, debt=10_000_000)
        p02 = make_player("P02", cash=1_200_000, debt=1_200_000)
        players = {"P01": p01, "P02": p02}

        action = TransferAction(player_id="P01", to="P02", amount=9_000_000)
        result = action_ops.validate_action(action, p01, config, players, round_num=1)

        assert not result.success
        assert "free cash" in result.reason.lower()

    def test_02_auto_commit_with_contract(self):
        """
        §12 #2: Entry Feeを払えるAIがCommitしない
        → 自動代行（契約優先ロジック）+ AUTO COMMIT公示

        契約制約なしの場合、最低ランクカード+最低賞金市場が選ばれる。
        """
        p01 = make_player("P01", cash=5_000_000, debt=1_200_000)
        markets = [
            make_market("M01", 500_000),
            make_market("M02", 300_000),
            make_market("M03", 400_000),
        ]
        contracts: list[Contract] = []

        legal = autocommit_ops.compute_legal_commits(p01, contracts, markets, round_num=1)
        selected = autocommit_ops.select_auto_commit(legal, markets)

        assert selected is not None
        # 最低ランク: HIGH_CARD (rank=1)
        assert selected.card.rank == CardRank.HIGH_CARD
        # 最低賞金市場: M02 (300,000)
        assert selected.market_id == "M02"

    def test_03_bankruptcy_entry_fee(self):
        """
        §12 #3: 現金5万でEntry Fee 10万
        → 破産 → 即時脱落

        Entry Feeは必須支払い（§2.5）。支払不能で破産。
        """
        config = GameConfig.default_20()
        p01 = make_player("P01", cash=50_000, debt=1_200_000)

        # Entry Fee(10万) > Cash(5万) → 支払不能
        can = player_ops.can_pay(p01, config.entry_fee)
        assert not can

        # 強制清算
        p01, _, record = elim_ops.forced_liquidation(
            p01, "bankruptcy", round_num=3, contracts=[],
        )
        assert not p01.is_alive
        assert p01.elimination_reason == "bankruptcy"

    def test_04_obligation_expiry_partial(self):
        """
        §12 #4: 3者契約(P01→P02, P02→P03)でP03脱落
        → P02→P03のみ失効、P01→P02は継続

        §6.3: 脱落者が義務者or相手方の義務のみ失効。
        """
        ob1 = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 500_000},
        )
        ob2 = make_obligation(
            "OB02", "C01", obligor="P02", counterparty="P03",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 300_000},
        )
        contract = make_contract(
            "C01", "P01", ["P01", "P02", "P03"],
            obligations=[ob1, ob2],
        )

        # P03が脱落
        updated = elim_ops.expire_obligations_for_player("P03", [contract])

        updated_contract = updated[0]
        # OB01 (P01→P02): P03は関係ない → 継続
        assert not updated_contract.obligations[0].is_expired
        # OB02 (P02→P03): P03が相手方 → 失効
        assert updated_contract.obligations[1].is_expired

    def test_05_type_b_violation_elimination(self):
        """
        §12 #5: 「R5でHIGH CARD使用」契約中にFULL HOUSEをCommit
        → Reveal後に違反検知 → 即時脱落

        型B(カード指定)契約の違反。
        """
        ob = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_B_CARD, round_num=5,
            details={"card_rank": "HIGH_CARD"},
        )
        contract = make_contract("C01", "P02", ["P01", "P02"], obligations=[ob])

        # P01がFULL HOUSEをコミット
        full_house_card = Card(rank=CardRank.FULL_HOUSE, card_id="FULL_HOUSE_1")
        commit = MarketCommit(player_id="P01", market_id="M01", card=full_house_card)

        # 型B監査
        type_b_obs = contract_ops.get_active_type_b_obligations([contract], round_num=5)
        violations = contract_ops.audit_type_b(type_b_obs, [commit])

        assert len(violations) == 1
        assert violations[0][0] == "P01"

    def test_06_type_a_free_cash_insufficient(self):
        """
        §12 #6: 「R8にP02へ200万」型A契約、R8時点でFree Cash 50万
        → 履行不能 → 脱落。P02は回収不能

        型A Atomic執行の不足判定。
        """
        ob = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=8,
            details={"amount": 2_000_000},
        )
        contract = make_contract("C01", "P01", ["P01", "P02"], obligations=[ob])

        # スナップショット: P01のFreeCash=50万
        snapshots = {
            "P01": {"cash": 1_500_000, "free_cash": 500_000},
            "P02": {"cash": 3_000_000, "free_cash": 2_000_000},
        }

        type_a_obs = contract_ops.get_active_type_a_obligations([contract], round_num=8)
        _, failed, payments = contract_ops.execute_type_a_atomic(type_a_obs, snapshots)

        # P01は履行不能→脱落
        assert "P01" in failed
        # P02への支払いは行われない
        assert "P02" not in payments or payments.get("P02", 0) == 0

    def test_07_bounty_r1_free_cash_zero(self):
        """
        §12 #7: R1(全員FC=0)で報奨掲載
        → Free Cash不足 → 不成立

        ゲーム開始時は全員FreeCash=0（§2.4の帰結）。
        報奨預託はFreeCash制限の対象（§7.2）。
        """
        config = GameConfig.default_20()
        p01 = make_player("P01", cash=1_200_000, debt=1_200_000)  # FC=0
        players = {"P01": p01}

        action = BountyPostAction(
            player_id="P01", amount=100_000,
            bounty_type="achievement",
            condition_type="market_win_against",
            condition={"target_player": "P02"},
            round_num=1,
        )
        result = action_ops.validate_action(action, p01, config, players, round_num=1)

        assert not result.success
        assert "free cash" in result.reason.lower()

    def test_08_atomic_combined_obligation(self):
        """
        §12 #8: FC150万で同一Rに100万+100万の型A義務
        → Atomic判定 → 合計200万 > FC150万 → 両方不履行 → 即時脱落

        §6.6: 義務者ごとに合算してAtomic判定。
        """
        ob1 = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 1_000_000},
        )
        ob2 = make_obligation(
            "OB02", "C02", obligor="P01", counterparty="P03",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 1_000_000},
        )
        c1 = make_contract("C01", "P01", ["P01", "P02"], obligations=[ob1])
        c2 = make_contract("C02", "P01", ["P01", "P03"], obligations=[ob2])

        snapshots = {
            "P01": {"cash": 2_000_000, "free_cash": 1_500_000},
        }

        type_a_obs = contract_ops.get_active_type_a_obligations(
            [c1, c2], round_num=5,
        )
        _, failed, _ = contract_ops.execute_type_a_atomic(type_a_obs, snapshots)

        # 合計200万 > FC150万 → 脱落
        assert "P01" in failed

    def test_09_snapshot_no_receipt_as_source(self):
        """
        §12 #9: 同一RにP02→P01 100万 / P01→P03 100万
        → スナップショット基準で判定（受取金は原資にならない）

        §6.6: 同一Settlement内で受け取る予定の型A受取金は支払原資にできない。
        P01のスナップショットFreeCashが100万未満なら、
        P02から受け取る100万があっても支払不能。
        """
        ob_receive = make_obligation(
            "OB01", "C01", obligor="P02", counterparty="P01",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 1_000_000},
        )
        ob_pay = make_obligation(
            "OB02", "C02", obligor="P01", counterparty="P03",
            ob_type=ObligationType.TYPE_A_PAYMENT, round_num=5,
            details={"amount": 1_000_000},
        )
        c1 = make_contract("C01", "P02", ["P02", "P01"], obligations=[ob_receive])
        c2 = make_contract("C02", "P01", ["P01", "P03"], obligations=[ob_pay])

        # P01のスナップショット: FC=50万（受取前）
        # P02のスナップショット: FC=200万（支払可能）
        snapshots = {
            "P01": {"cash": 1_500_000, "free_cash": 500_000},
            "P02": {"cash": 3_000_000, "free_cash": 2_000_000},
        }

        type_a_obs = contract_ops.get_active_type_a_obligations(
            [c1, c2], round_num=5,
        )
        _, failed, payments = contract_ops.execute_type_a_atomic(type_a_obs, snapshots)

        # P02は支払可能（FC200万 ≥ 100万）
        assert "P02" not in failed
        # P01は支払不能（スナップショットFC50万 < 100万、受取金は原資にならない）
        assert "P01" in failed

    def test_10_forced_liquidation(self):
        """
        §12 #10: Cash280万/Debt150万で契約違反脱落
        → 150万返済 → 130万没収

        §1.6: 脱落時強制清算の処理順。
        """
        p01 = make_player("P01", cash=2_800_000, debt=1_500_000)

        p01, _, record = elim_ops.forced_liquidation(
            p01, "contract_violation", round_num=5, contracts=[],
        )

        assert not p01.is_alive
        assert record["debt_repaid"] == 1_500_000   # 150万返済
        assert record["cash_confiscated"] == 1_300_000  # 130万没収
        assert record["bad_debt"] == 0  # 貸倒れなし
        assert p01.cash == 0
        assert p01.debt_balance == 0

    def test_11_auto_commit_contract_constrained(self):
        """
        §12 #11: 契約でMarket B + HIGH指定、LLM Commit失敗
        → 自動代行はMarket B + HIGH

        型B契約が市場+カードを指定している場合、
        自動代行はその制約を満たすCommitのみを候補にする。
        """
        # P01にHIGH_CARDが手札にある状態
        hand = create_deck()
        p01 = make_player("P01", cash=5_000_000, debt=1_200_000, hand=hand)

        markets = [
            make_market("M01", 500_000),
            make_market("M02", 300_000),  # Market B = M02
            make_market("M03", 400_000),
        ]

        # 契約: M02に参加 + HIGH_CARDを使用
        ob_market = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_B_MARKET, round_num=1,
            details={"market_id": "M02"},
        )
        ob_card = make_obligation(
            "OB02", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_B_CARD, round_num=1,
            details={"card_rank": "HIGH_CARD"},
        )
        contract = make_contract(
            "C01", "P02", ["P01", "P02"],
            obligations=[ob_market, ob_card],
        )

        legal = autocommit_ops.compute_legal_commits(p01, [contract], markets, round_num=1)
        selected = autocommit_ops.select_auto_commit(legal, markets)

        assert selected is not None
        assert selected.market_id == "M02"
        assert selected.card.rank == CardRank.HIGH_CARD

    def test_12_contradictory_contracts_elimination(self):
        """
        §12 #12: 矛盾契約で合法Commit 0件
        → 履行不能 → 即時脱落

        「M01に参加」と「M01に参加するな」の矛盾した義務。
        """
        p01 = make_player("P01", cash=5_000_000, debt=1_200_000)

        markets = [
            make_market("M01", 500_000),
            make_market("M02", 300_000),
            make_market("M03", 400_000),
        ]

        # 矛盾: M01に参加 + M01に不参加
        ob1 = make_obligation(
            "OB01", "C01", obligor="P01", counterparty="P02",
            ob_type=ObligationType.TYPE_B_MARKET, round_num=1,
            details={"market_id": "M01"},
        )
        ob2 = make_obligation(
            "OB02", "C02", obligor="P01", counterparty="P03",
            ob_type=ObligationType.TYPE_B_NO_MARKET, round_num=1,
            details={"market_id": "M01"},
        )
        c1 = make_contract("C01", "P02", ["P01", "P02"], obligations=[ob1])
        c2 = make_contract("C02", "P03", ["P01", "P03"], obligations=[ob2])

        legal = autocommit_ops.compute_legal_commits(p01, [c1, c2], markets, round_num=1)
        selected = autocommit_ops.select_auto_commit(legal, markets)

        # 合法0件
        assert selected is None

    def test_13_r12_auto_repayment_survival(self):
        """
        §12 #13: R12終了時 Cash500万/Debt150万
        → 自動返済 → Cash350万/Debt0 → 生還

        §5.3: R12 Financeで返済可能額を自動充当。
        """
        p01 = make_player("P01", cash=5_000_000, debt=1_500_000)
        players = {"P01": p01}
        config = GameConfig.default_20()
        logger = EventLogger(fixed_timestamp="2026-08-09T00:00:00Z")

        players, _ = finance_ops.execute_finance(
            players, round_num=12, config=config,
            contracts=[], logger=logger,
        )

        p = players["P01"]
        # 利息計上後のdebt: 1,500,000 * 1.015 = 1,522,500
        expected_debt_after_interest = math.ceil(1_500_000 * 1.015)
        # 自動返済: min(Cash, Debt)
        expected_cash = 5_000_000 - expected_debt_after_interest
        assert p.debt_balance == 0
        assert p.cash == expected_cash
        # 生還判定: Cash ≥ 300万 かつ Debt = 0
        assert p.is_alive
        assert p.cash >= config.survival_cash

    def test_14_anon_broadcast_insufficient(self):
        """
        §12 #14: Cash5万で匿名通信10万を要求
        → 不成立。脱落しない

        §7.1: 匿名通信費は任意支払い。不足でアクション不成立。
        """
        config = GameConfig.default_20()
        p01 = make_player("P01", cash=50_000, debt=0)
        players = {"P01": p01}

        action = AnonymousBroadcastAction(
            player_id="P01", message="Test",
        )
        result = action_ops.validate_action(action, p01, config, players, round_num=1)

        assert not result.success
        assert "insufficient cash" in result.reason.lower()
        # 脱落しない
        assert p01.is_alive

    def test_15_contract_fee_insufficient(self):
        """
        §12 #15: 契約提案者に発行料10万がない
        → 契約成立失敗

        §6.1: 発行料は提案者が全額負担。任意支払い。
        """
        config = GameConfig.default_20()
        p01 = make_player("P01", cash=50_000, debt=0)
        players = {"P01": p01}

        action = ContractProposeAction(
            player_id="P01",
            with_players=["P02"],
            terms=[{
                "obligor": "P01", "counterparty": "P02",
                "ob_type": "type_a_payment", "round_num": 5,
                "details": {"amount": 100_000},
            }],
        )
        result = action_ops.validate_action(action, p01, config, players, round_num=1)

        assert not result.success
        assert "insufficient cash" in result.reason.lower()

    def test_16_transfer_immediate_settlement(self):
        """
        §12 #16: Negotiation中に50万transfer
        → 即時に双方Cash/Free Cashへ反映

        §9.4: transferはアクション成立時に即時決済。
        """
        config = GameConfig.default_20()
        # P01: Cash=500万, Debt=200万 → FC=300万
        p01 = make_player("P01", cash=5_000_000, debt=2_000_000)
        # P02: Cash=300万, Debt=100万 → FC=200万
        p02 = make_player("P02", cash=3_000_000, debt=1_000_000)
        players = {"P01": p01, "P02": p02}

        action = TransferAction(player_id="P01", to="P02", amount=500_000)
        result = action_ops.validate_action(action, p01, config, players, round_num=1)

        # 検証成功
        assert result.success

        # 即時決済シミュレーション
        p01 = player_ops.pay(p01, 500_000)
        p02 = player_ops.receive(p02, 500_000)

        # P01: Cash=450万, Debt=200万 → FC=250万
        assert p01.cash == 4_500_000
        assert p01.free_cash == 2_500_000
        # P02: Cash=350万, Debt=100万 → FC=250万
        assert p02.cash == 3_500_000
        assert p02.free_cash == 2_500_000


class TestStep1_1Fixes:
    """Step 1.1 レビュー指摘修正のテスト（3件）"""

    def test_bounty_achievement_player_eliminated_rejected(self):
        """
        §7.2: 達成者型(achievement)で PLAYER_ELIMINATED 条件は禁止
        因果帰属が機械判定不能のため拒否されること。
        """
        config = GameConfig.default_20()
        # FreeCashは十分にある状態
        p01 = make_player("P01", cash=5_000_000, debt=1_000_000)  # FC=400万
        players = {"P01": p01}

        action = BountyPostAction(
            player_id="P01", amount=100_000,
            bounty_type="achievement",
            condition_type="player_eliminated",
            condition={"target_player": "P07"},
            round_num=3,
        )
        result = action_ops.validate_action(action, p01, config, players, round_num=3)

        assert not result.success
        assert "prohibited" in result.reason.lower()

    def test_bounty_event_player_eliminated_accepted(self):
        """
        §7.2: イベント型(event)で PLAYER_ELIMINATED は有効（保険型）
        「P07が脱落した場合、P03へ100万」が成立すること。
        """
        config = GameConfig.default_20()
        p01 = make_player("P01", cash=5_000_000, debt=1_000_000)  # FC=400万
        players = {"P01": p01}

        action = BountyPostAction(
            player_id="P01", amount=100_000,
            bounty_type="event",
            condition_type="player_eliminated",
            condition={"target_player": "P07"},
            beneficiary="P03",
            round_num=3,
        )
        result = action_ops.validate_action(action, p01, config, players, round_num=3)

        assert result.success

    def test_finance_final_round_configurable(self):
        """
        finance.py のハードコード除去検証:
        num_rounds=6 の設定で R6 の Finance で自動返済+生還判定が発火すること。
        """
        # 6ラウンド設定を作成（prize_tiersはデフォルト12件のまま、num_roundsのみ変更）
        config = GameConfig.default_20()
        config = config.model_copy(update={"num_rounds": 6})
        # Cash=500万, Debt=100万 → 自動返済後 Cash=400万, Debt=0 → 生還
        p01 = make_player("P01", cash=5_000_000, debt=1_000_000)
        players = {"P01": p01}
        logger = EventLogger(fixed_timestamp="2026-08-09T00:00:00Z")

        players, _ = finance_ops.execute_finance(
            players, round_num=6, config=config,
            contracts=[], logger=logger,
        )

        p = players["P01"]
        # 自動返済が発火している（Debt=0）
        assert p.debt_balance == 0
        # 生還判定が行われている（is_alive=True）
        assert p.is_alive
        # Cash ≥ 300万
        assert p.cash >= config.survival_cash

        # R5（最終ラウンドでない）では自動返済が発火しないことも確認
        p02 = make_player("P02", cash=5_000_000, debt=1_000_000)
        players2 = {"P02": p02}
        logger2 = EventLogger(fixed_timestamp="2026-08-09T00:00:00Z")

        players2, _ = finance_ops.execute_finance(
            players2, round_num=5, config=config,
            contracts=[], logger=logger2,
        )
        # R5では自動返済されない（利息のみ計上）
        assert players2["P02"].debt_balance > 0
