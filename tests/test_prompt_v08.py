"""
v0.8 サイクル8.2: プロンプト文面の一括修正 の受け入れテスト

Step1〜3で `llm/prompt_builder.py` に加えた変更のうち、既存テストファイル
（test_cycle5_prompt_salience.py / test_prompt_finance.py / test_llm.py 等）
がカバーしない項目をここでまとめて機械保証する。API非呼出・engine非変更。

対象:
  - 契約発行料の条件分岐（fee=0で「発行料」非出現 / fee>0で従来文）
  - system promptの中核事実（脱落4種別・無料・型A支払原資）
  - negotiationの公開情報6ブロック（他者倍掛け・契約・初期借入・トレード成立・
    高騰しきい値・総賞金予算）
  - Financeの見込み行がEntry Fee込みの1行式で、不足時に⚠警告が別行で出ること
  - contract_proposeの雛形（他者ID・round_num≥今R・R12は型A例を省略）
  - 締め指示直後に "pass" のJSON例が残っていないこと（2-9）
  - トレード提案の支払方向（誰が渡す/誰が払うか）明示
  - 契約・トレード関連の通知（5種）が事実文で描画されること
  - build_double_up_promptの次R見通しブロック・破産可能性警告
  - reflectionでdouble_up_blocked通知が読めること
  - system prompt文字数の回帰ガード（≤8300字）
"""

from engine.config import GameConfig
from llm.prompt_builder import (
    build_commit_prompt,
    build_double_up_prompt,
    build_negotiation_prompt,
    build_reflection_prompt,
    build_system_prompt,
)
from tests.conftest import make_market, make_player
from tests.test_cycle5_prompt_salience import _make_player, _base_visible_state


class TestContractFeeWording:
    """2-7/2-8: 契約発行料が0円のときは「発行料」文言・端数「0万円」を一切出さない"""

    def test_fee_zero_no_issue_fee_wording(self):
        config = GameConfig.baseline_v1_s2(12)
        assert config.contract_fee == 0
        sp = build_system_prompt("P01", config)
        assert "発行料" not in sp
        assert "無料" in sp

        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = _base_visible_state(alive_players=["P01", "P02", "P03"])
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        assert "発行料" not in prompt
        assert "無料" in prompt

    def test_fee_positive_shows_traditional_wording(self):
        config = GameConfig.baseline_v1(12)
        assert config.contract_fee > 0
        fee_man = config.contract_fee // 10_000
        sp = build_system_prompt("P01", config)
        assert f"発行料{fee_man}万円" in sp

        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = _base_visible_state(alive_players=["P01", "P02", "P03"])
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        assert f"発行料{fee_man}万円" in prompt


class TestSystemPromptCoreFacts:
    """system promptの中核事実（言い換えても意味が保持されていること）"""

    def test_elimination_four_types(self):
        config = GameConfig.baseline_v1_s2(12)
        sp = build_system_prompt("P01", config)
        assert "4種別" in sp
        for word in ["契約違反", "破産(Entry Fee)", "破産(強制返済)", "条件未達"]:
            assert word in sp

    def test_type_a_funding_source_fact(self):
        config = GameConfig.baseline_v1_s2(12)
        sp = build_system_prompt("P01", config)
        assert "型A支払原資" in sp


class TestNegotiationPublicInfoBlocks:
    """2-3: negotiationに公開情報6ブロックが揃って出ること"""

    def _prompt(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = _base_visible_state(
            alive_players=["P01", "P02", "P03"],
            double_ups=[{"player_id": "P02", "deposit": 180_000}],
            initial_loans={"P01": 500_000, "P02": 500_000, "P03": 500_000},
            used_cards={"P01": ["ONE_PAIR"], "P02": []},
            contracts_public=[
                {"contract_id": "C_X1", "parties": ["P01", "P03"], "status": "active"},
            ],
            trades_completed=[{"round": 4, "players": ["P02", "P03"]}],
            total_prize_budget=9_000_000,
        )
        return build_negotiation_prompt(player, 6, 3, vs, config)

    def test_others_double_ups_block(self):
        prompt = self._prompt()
        assert "## 倍掛け中のプレイヤー" in prompt
        assert (
            "P02: 預託18万円（今Rの市場で賞金を得れば36万円受領、得られなければ没収）"
            in prompt
        )

    def test_contracts_public_block(self):
        prompt = self._prompt()
        assert "## 正式契約の状況（公示）" in prompt
        assert "[C_X1] 当事者: P01, P03" in prompt

    def test_initial_loans_block(self):
        prompt = self._prompt()
        assert "## 初期借入額（公開情報。現金・借金残高・Free Cashは秘匿）" in prompt
        assert "P02: 50万円" in prompt

    def test_trades_completed_block(self):
        prompt = self._prompt()
        assert "## 成立済みカードトレード（当事者名のみ公開。交換内容は秘匿）" in prompt
        assert "R4: P02 ⇄ P03" in prompt

    def test_surge_threshold_and_prize_budget(self):
        prompt = self._prompt()
        assert "市場高騰のしきい値" in prompt
        assert "総賞金予算（このゲーム全体・システム基本賞金の合計）: 900万円" in prompt


class TestFinanceForecastEntryFeeAndWarning:
    """2-1: Finance見込みがEntry Fee込みの1行式・不足時は⚠が別行で出る"""

    def test_commit_prompt_deficit_shows_entry_fee_and_warning_line(self):
        config = GameConfig.baseline_v1_s2(num_players=12)
        player = make_player("P06", cash=84_063, debt=450_596)
        prompt = build_commit_prompt(
            player, [make_market("M01", 500_000)], 9, {"used_cards": {}}, config,
        )
        forecast_line = (
            "今R賞金0の場合の現金見込み: 84063円 − Entry Fee 100000円"
            " − 強制返済 114339円 = -130276円（-14万円）"
        )
        assert forecast_line in prompt
        warning_line = "⚠ 今Rで市場賞金 130276円以上を得られなければ、強制返済不能で破産脱落します"
        assert warning_line in prompt
        # 数値見込み行そのものには⚠を含まない（警告は独立した別行）
        assert "⚠" not in forecast_line


class TestContractProposeTemplate:
    """2-8: 雛形は今の生存者から実在の他者IDを埋め、round_numは今R以降、R12は型A例を省略"""

    def test_template_uses_real_other_player_and_round_num_at_least_current(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = _base_visible_state(alive_players=["P01", "P02", "P03"])
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        idx = prompt.find("## contract_propose の雛形")
        assert idx != -1
        block = prompt[idx:idx + 500]
        assert '"with": ["P02"]' in block
        assert "round_num は今R（6）以降" in block
        assert '"round_num": 7' in block  # type_b: round_num + 1
        assert '"round_num": 8' in block  # type_a: round_num + 2

    def test_final_round_omits_type_a_example(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = _base_visible_state(alive_players=["P01", "P02", "P03"])
        prompt = build_negotiation_prompt(player, 12, 1, vs, config)
        idx = prompt.find("## contract_propose の雛形")
        assert idx != -1
        block = prompt[idx:idx + 500]
        assert "type_a_payment" not in block
        assert "※最終R（R12）はSettlementが同R内で完結するため" in block


class TestNoPassInExampleResponse:
    """2-9: 締め指示末尾の記入例に pass のJSON例が残っていないこと"""

    def test_negotiation_closing_has_no_pass_json_example(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = _base_visible_state(alive_players=["P01", "P02", "P03"])
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        tail = prompt[prompt.rfind("## いま選べるアクション"):]
        assert '"type": "pass"' not in tail
        assert "例: " not in tail


class TestTradePaymentDirection:
    """2-5: 提案中のカードトレードで誰が何を渡し、現金は誰が払うかが明示される"""

    def test_cash_paid_by_proposer(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = _base_visible_state(
            alive_players=["P01", "P02", "P03"],
            trades_pending=[{
                "trade_id": "T_1", "proposer": "P02", "round_proposed": 6,
                "cash_amount": 50_000, "give_card_rank": "ONE_PAIR",
                "with_player": "P01", "receive_card_rank": "FLUSH",
            }],
        )
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        assert "P02が渡す: ONE_PAIR ／ P01が渡す: FLUSH ＋ 現金5万円（P02が払う）" in prompt
        assert "受諾すればあなたは現金を受け取ります" in prompt


class TestNotificationKinds:
    """5-2/D-6系: 契約・トレード関連の通知が事実文で描画されること（代表5種）"""

    def test_five_notice_kinds_render(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        notices = [
            {"kind": "cancel_completed", "contract_id": "C_A", "by": "P02", "turn": 2},
            {"kind": "contract_expired", "contract_id": "C_B", "turn": 3,
             "unsigned_by": ["P02"]},
            {"kind": "trade_expired", "trade_id": "T_1", "with_player": "P02", "turn": 4},
            {"kind": "trade_failed_funds", "trade_id": "T_2", "with_player": "P03",
             "turn": 5, "short_side": "proposer", "cash_amount": 50_000},
            {"kind": "double_up_blocked", "eligible_prize": 200_000, "cash": 50_000,
             "min_repay": 80_000},
        ]
        vs = _base_visible_state(
            alive_players=["P01", "P02", "P03"], my_contract_notices=notices,
        )
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        assert "C_A は生存する全当事者の合意で解除されました" in prompt
        assert "C_B は署名が揃わないまま今Rの交渉が終了し、失効しました（未署名: P02）" in prompt
        assert "トレード T_1（相手: P02）は未受諾のまま今Rの交渉が終了し、失効しました" in prompt
        assert (
            "トレード T_2（相手: P03）は、提案者のFree Cash不足（必要額 5万円）"
            "のため不成立になりました" in prompt
        )
        assert "このRの倍掛け選択は自動的にTAKEになりました" in prompt


class TestDoubleUpNextRoundForecast:
    """A2/A3: DOUBLEを選んだ場合の次ラウンド見通しブロックと破産可能性警告"""

    def test_next_round_forecast_and_bankruptcy_warning(self):
        config = GameConfig.baseline_v1_s2(num_players=12)
        player = make_player("P01", cash=1_290_854, debt=1_279_974)
        prompt = build_double_up_prompt(player, 940_000, 11, {}, config)
        assert "## DOUBLEを選んだ場合の次ラウンド（R12）見通し" in prompt
        assert "次Rの強制最低返済額（概算）: 659331円（65万円）" in prompt
        assert (
            "次RのEntry Fee 100000円と強制最低返済を差し引いた現金見込み: "
            "-1058064円（-106万円）" in prompt
        )
        assert (
            "⚠ この見込みでは次Rの強制最低返済額に1058064円不足し、破産の可能性があります"
            in prompt
        )

    def test_rule_mentions_type_a_funding_and_no_re_double(self):
        config = GameConfig.baseline_v1_s2(num_players=12)
        player = make_player("P02", cash=5_000_000, debt=100_000)
        prompt = build_double_up_prompt(player, 200_000, 3, {}, config)
        assert "次Rの型A（現金）支払義務の原資になります" in prompt
        assert "その場でさらにDOUBLEすることはできません" in prompt


class TestReflectionDoubleUpBlocked:
    """C4: reflectionでdouble_up_blocked通知が読めること"""

    def test_reflection_shows_double_up_blocked_notice(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        notices = [{
            "kind": "double_up_blocked", "eligible_prize": 200_000,
            "cash": 50_000, "min_repay": 80_000,
        }]
        vs = _base_visible_state(
            alive_players=["P01", "P02", "P03"], my_contract_notices=notices,
        )
        prompt = build_reflection_prompt(player, 6, vs, config)
        assert "このRの倍掛け選択は自動的にTAKEになりました" in prompt
        assert "DOUBLEは選択できません" in prompt


class TestSystemPromptLengthRegressionGuard:
    """system prompt文字数の回帰ガード（v0.8サイクル8.2で7100→8300字へ改定済み）"""

    def test_system_prompt_within_budget(self):
        config = GameConfig.baseline_v1_s2(12)
        prompt = build_system_prompt("P01", config)
        assert len(prompt) <= 8300, len(prompt)
