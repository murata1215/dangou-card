"""
v0.8 サイクル8.2/8.3: プロンプト文面の一括修正・仕上げ の受け入れテスト

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

サイクル8.3（ダンプv2レビュー17点、F1〜F17）で追加:
  - F1: トレード提案の現金表示が支払者と食い違わないこと（提案者払い/受諾者払い両方）
  - F2: 署名待ち契約プレビューで、手札に無いカード指定義務への警告が
    今R期限/将来R期限で書き分けられること（署名済み義務側は従来文言のまま）
  - F3: 「アクション枠を1つ消費します」の文言
  - F4: 契約・トレード失効通知が[R{n}末]形式でround_numを持つこと
  - F5〜F7: system promptの微修正（identityの空行・宛先例のID・弱いカードで勝つ）
  - F8: 初期借入額ブロックが1行の横並び表示になること
  - F9: 総賞金予算ラベルが最終市場倍率込みの事実を反映すること
  - F10: 「2倍の払出」表記
  - F11〜F13: double_up次R見通しの冒頭サマリ・結論（破産/没収の区別）・
    型A義務の原資（成功時payout/失敗時Entry Fee控除後現金）
  - F15: 他プレイヤーの倍掛け中預託がsuccess_roundを明示すること
  - F16: double_up_blocked通知がengineの実判定（預託後の現金）に合うこと
  - F17: Finance見込み行のforecast_labelパラメータ化（reflectionは次R明示）
"""

from engine.config import GameConfig
from llm.prompt_builder import (
    build_commit_prompt,
    build_double_up_prompt,
    build_negotiation_prompt,
    build_reflection_prompt,
    build_system_prompt,
    _render_finance_block,
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
            double_ups=[{"player_id": "P02", "deposit": 180_000, "success_round": 7}],
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
        # v0.8サイクル8.3 F15: success_roundが与えられていればR番号を明示する
        assert (
            "P02: 預託18万円（R7の市場で賞金を得れば36万円受領、得られなければ没収）"
            in prompt
        )

    def test_contracts_public_block(self):
        prompt = self._prompt()
        assert "## 正式契約の状況（公示）" in prompt
        assert "[C_X1] 当事者: P01, P03" in prompt

    def test_initial_loans_block(self):
        prompt = self._prompt()
        assert "## 初期借入額（公開情報。現金・借金残高・Free Cashは秘匿）" in prompt
        # v0.8サイクル8.3 F8: 12行の縦列挙から1行の横並びへ圧縮
        assert "P01: 50万 / P02: 50万 / P03: 50万" in prompt

    def test_trades_completed_block(self):
        prompt = self._prompt()
        assert "## 成立済みカードトレード（当事者名のみ公開。交換内容は秘匿）" in prompt
        assert "R4: P02 ⇄ P03" in prompt

    def test_surge_threshold_and_prize_budget(self):
        prompt = self._prompt()
        assert "市場高騰のしきい値" in prompt
        # v0.8サイクル8.3 F9: total_prize_budgetは最終市場倍率込みで算出されているため
        # ラベルもその事実に合わせる（baseline_v1_s2はfinal_market_multiplier=3）
        assert "総賞金予算（全12R・R12の3倍込み）: 900万円" in prompt


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
    """2-5/v0.8サイクル8.3 F1: 提案中のカードトレードで誰が何を渡し、
    現金は「払う側」に付けて誰が払うか明示される。閲覧者自身は「あなた」に置換される。
    """

    def test_cash_paid_by_proposer(self):
        """cash_amount > 0: 提案者が払う（現金は提案者の「渡す」句に付く）"""
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
        assert (
            "P02が渡す: ONE_PAIR ＋ 現金5万円（P02が払う） ／ あなたが渡す: FLUSH"
            in prompt
        )
        assert "あなたの立場: 受諾候補（受諾すればあなたは現金を受け取ります）" in prompt

    def test_cash_paid_by_target(self):
        """cash_amount < 0: 受諾者（自分）が払う（現金は受諾者の「渡す」句に付く）"""
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = _base_visible_state(
            alive_players=["P01", "P02", "P03"],
            trades_pending=[{
                "trade_id": "T_1", "proposer": "P02", "round_proposed": 6,
                "cash_amount": -50_000, "give_card_rank": "ONE_PAIR",
                "with_player": "P01", "receive_card_rank": "FLUSH",
            }],
        )
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        assert (
            "P02が渡す: ONE_PAIR ／ あなたが渡す: FLUSH ＋ 現金5万円（あなたが払う）"
            in prompt
        )
        assert (
            "あなたの立場: 受諾候補（受諾すればあなたは現金を支払います"
            f"（Free Cash {player.free_cash}円以内で可））" in prompt
        )


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
        # v0.8サイクル8.3 F4: [R{n}末]形式・相手IDは残す（承認時ノート指定）
        assert "[R6末] C_B は R6 の交渉終了時に署名が揃わず失効しました（未署名: P02）" in prompt
        assert "[R6末] トレード T_1（相手: P02）は R6 の交渉終了時に失効しました" in prompt
        assert (
            "トレード T_2（相手: P03）は、提案者のFree Cash不足（必要額 5万円）"
            "のため不成立になりました" in prompt
        )
        # v0.8サイクル8.3 F16: engineの実判定（預託後の現金）に合わせた文言。
        # 「受領後」という誤った語は使わない
        assert (
            "[倍掛け] R6の賞金200000円の倍掛けは、預託後の現金-150000円が"
            "今Rの強制最低返済額80000円を下回るため選択できず、自動的にTAKEになりました"
            in prompt
        )
        assert "受領後" not in prompt


class TestDoubleUpNextRoundForecast:
    """A2/A3: DOUBLEを選んだ場合の次ラウンド見通しブロック（v0.8サイクル8.3 F11/F12/F13）"""

    def test_next_round_forecast_and_deposit_forfeit_bankruptcy_warning(self):
        """F11/F12: z < 0（強制返済不能で破産脱落、回避に必要な額を明示）"""
        config = GameConfig.baseline_v1_s2(num_players=12)
        player = make_player("P01", cash=1_290_854, debt=1_279_974)
        prompt = build_double_up_prompt(player, 940_000, 11, {}, config)
        assert "## DOUBLEを選んだ場合の次ラウンド（R12）見通し" in prompt
        # F11: 冒頭の現金見込みサマリ行
        assert (
            "R12開始時の現金見込み: -298733円 → Entry Fee 100000円控除後 -398733円"
            in prompt
        )
        assert "次Rの市場賞金0（＝DOUBLE失敗）を仮定した最低ラインで概算します" in prompt
        assert "次Rの強制最低返済額（概算）: 659331円（65万円）" in prompt
        assert (
            "次RのEntry Fee 100000円と強制最低返済を差し引いた現金見込み: "
            "-1058064円（-106万円）" in prompt
        )
        # F12: 預託没収と強制返済不能を区別し、回避額まで明示する
        assert (
            "⚠ R12で市場賞金を得られなければ、預託940000円の没収に加えて"
            "強制返済不能で破産脱落します（回避にはR12で市場賞金1058064円以上が必要）"
            in prompt
        )
        assert "破産の可能性があります" not in prompt

    def test_next_round_forecast_deposit_forfeit_but_repay_ok(self):
        """F12: z >= 0（預託没収はするが強制返済は払える、余裕額を明示）"""
        config = GameConfig.baseline_v1_s2(num_players=12)
        player = make_player("P02", cash=5_000_000, debt=100_000)
        prompt = build_double_up_prompt(player, 200_000, 3, {}, config)
        assert "## DOUBLEを選んだ場合の次ラウンド（R4）見通し" in prompt
        assert (
            "R4で市場賞金を得られなければ預託200000円は没収されますが、"
            "強制返済は払えます（余裕4679547円）" in prompt
        )

    def test_next_r_type_a_obligation_shortfall_defaults_to_elimination(self):
        """F13: 失敗時にEntry Fee控除後現金では不足し、履行不能で脱落するケース"""
        config = GameConfig.baseline_v1_s2(num_players=12)
        player = make_player("P01", cash=1_290_854, debt=1_279_974)
        vs = {"my_obligations": [{
            "contract_id": "C_X", "round_num": 12, "ob_type": "type_a_payment",
            "obligor": "P01", "counterparty": "P02", "details": {"amount": 500_000},
        }]}
        prompt = build_double_up_prompt(player, 940_000, 11, vs, config)
        assert "次R支払期限の型A義務: 支払い合計500000円 / 受取合計0円" in prompt
        assert (
            "成功時はR12の払出1880000円から払えます。"
            "失敗時はEntry Fee控除後の現金-398733円では898733円不足し、"
            "履行不能で脱落します" in prompt
        )

    def test_next_r_type_a_obligation_payable_even_on_failure(self):
        """F13: 失敗時もEntry Fee控除後現金から払えるケース（残額を明示）"""
        config = GameConfig.baseline_v1_s2(num_players=12)
        player = make_player("P02", cash=5_000_000, debt=100_000)
        vs = {"my_obligations": [{
            "contract_id": "C_Y", "round_num": 4, "ob_type": "type_a_payment",
            "obligor": "P02", "counterparty": "P03", "details": {"amount": 1_000_000},
        }]}
        prompt = build_double_up_prompt(player, 200_000, 3, vs, config)
        assert "次R支払期限の型A義務: 支払い合計1000000円 / 受取合計0円" in prompt
        assert (
            "成功時はR4の払出400000円から払えます。"
            "失敗時もEntry Fee控除後の現金4689850円から払えます（残り3689850円）"
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
        # v0.8サイクル8.3 F16: engineの実判定（預託後の現金）に合わせた文言
        assert (
            "[倍掛け] R6の賞金200000円の倍掛けは、預託後の現金-150000円が"
            "今Rの強制最低返済額80000円を下回るため選択できず、自動的にTAKEになりました"
            in prompt
        )
        assert "受領後" not in prompt


class TestPendingSignatureHandWarning:
    """v0.8サイクル8.3 F2: 署名待ち契約プレビューで、手札に無いカード指定義務への
    警告が今R期限/将来R期限で書き分けられる（署名済み義務側は従来文言のまま）。
    """

    def _make_vs(self, ob_round_num: int):
        return _base_visible_state(
            alive_players=["P01", "P02", "P03"],
            contracts_pending=[{
                "contract_id": "C_F2", "proposer": "P02", "parties": ["P01", "P02"],
                "signed_by": ["P02"], "round_created": 6,
                "obligations": [
                    {"obligor": "P01", "counterparty": "P02", "ob_type": "type_b_card",
                     "round_num": ob_round_num, "details": {"card_rank": "ROYAL_FLUSH"}},
                ],
            }],
        )

    def test_current_round_deadline_warns_elimination_confirmed(self):
        """義務の期限が今R（rn == current_round）: 違反脱落が確定する旨を明示"""
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = self._make_vs(6)
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        assert (
            "（⚠ 現在の手札にありません。今Rが期限のため、"
            "署名すると今回の監査で違反脱落が確定します）" in prompt
        )

    def test_future_round_deadline_warns_need_to_acquire_via_trade(self):
        """義務の期限が将来R（rn > current_round）: トレードで入手できなければ
        違反脱落する旨を明示"""
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = self._make_vs(8)
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        assert (
            "（⚠ 現在の手札にありません。R8までにトレードで"
            "入手できなければ違反脱落します）" in prompt
        )

    def test_signed_obligations_block_unaffected_by_f2(self):
        """回帰防止: 署名済み義務（_render_obligations_block経由）は
        pending_signing未指定＝Falseのままなので従来の簡潔な文言を維持する"""
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = _base_visible_state(
            alive_players=["P01", "P02", "P03"],
            my_obligations=[{
                "contract_id": "C_F2B", "obligor": "P01", "counterparty": "P02",
                "ob_type": "type_b_card", "round_num": 6,
                "details": {"card_rank": "ROYAL_FLUSH"},
            }],
        )
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        assert "（現在の手札にありません）" in prompt
        assert "違反脱落が確定します" not in prompt
        assert "違反脱落します）" not in prompt


class TestActionSlotWording:
    """v0.8サイクル8.3 F3: 署名済み契約への再署名は「アクション枠を1つ消費します」"""

    def test_action_slot_wording_present(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = _base_visible_state(
            alive_players=["P01", "P02", "P03"],
            contracts_pending=[{
                "contract_id": "C_F3", "proposer": "P01", "parties": ["P01", "P02"],
                "signed_by": ["P01"], "round_created": 6, "obligations": [],
            }],
        )
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        assert "アクション枠を1つ消費します。" in prompt
        assert "その巡の発言を1回失います" not in prompt


class TestFinanceForecastLabel:
    """v0.8サイクル8.3 F17: forecast_labelパラメータで見込み行の主語を切り替えられる"""

    def test_default_label_is_this_round_zero_prize(self):
        prompt_lines = _render_finance_block(
            100_000, 50_000, 3, GameConfig.baseline_v1_s2(12),
            entry_fee_deduction=100_000,
        )
        text = "\n".join(prompt_lines)
        assert "今R賞金0の場合の現金見込み: " in text

    def test_custom_label_used_verbatim(self):
        prompt_lines = _render_finance_block(
            100_000, 50_000, 4, GameConfig.baseline_v1_s2(12),
            entry_fee_deduction=100_000,
            forecast_label="次R（R4）賞金0の場合",
        )
        text = "\n".join(prompt_lines)
        assert "次R（R4）賞金0の場合の現金見込み: " in text
        assert "今R賞金0の場合の現金見込み: " not in text

    def test_reflection_uses_next_round_label(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player("P01", cash=800_000, debt=300_000)
        vs = _base_visible_state(alive_players=["P01", "P02", "P03"])
        prompt = build_reflection_prompt(player, 6, vs, config)
        assert "次R（R7）賞金0の場合の現金見込み: " in prompt


class TestSystemPromptMicroWordingF5F6F7:
    """v0.8サイクル8.3 F5/F6/F7: system promptの微修正"""

    def test_f5_blank_line_before_identity(self):
        config = GameConfig.baseline_v1_s2(12)
        sp = build_system_prompt("P07", config)
        assert "\n\nあなたはP07です。" in sp

    def test_f6_dm_transfer_examples_use_seat_relative_other(self):
        config = GameConfig.baseline_v1_s2(12)
        sp = build_system_prompt("P07", config)
        assert '{"type": "dm", "to": "P08", "message": "..."}' in sp
        assert '{"type": "transfer", "to": "P08", "amount": 500000}' in sp

    def test_f7_contract_benefit_line_mentions_weak_card_win(self):
        config = GameConfig.baseline_v1_s2(12)
        sp = build_system_prompt("P07", config)
        assert "不参加市場を作って弱いカードで勝つ、使用カードを固定する" in sp


class TestSystemPromptLengthRegressionGuard:
    """system prompt文字数の回帰ガード（v0.8サイクル8.2で7100→8300字へ改定済み）"""

    def test_system_prompt_within_budget(self):
        config = GameConfig.baseline_v1_s2(12)
        prompt = build_system_prompt("P01", config)
        assert len(prompt) <= 8300, len(prompt)
