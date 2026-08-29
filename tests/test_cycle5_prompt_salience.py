"""
Cycle 5: D1（契約系アクションの実戦消滅）ほか prompt salience 修正の回帰テスト

背景（詳細は本ファイル執筆時点のPlan「Cycle 5」参照）:
n=1実走（trial_C_l12_r12_20260824）で契約系アクション（contract_propose/
contract_sign/bounty_post/card_trade_propose/anonymous_broadcast）が
1件も試行されなかった（D1[S1]）。原因はengineのgateではなくprompt側の
risk-framing偏り（コストのみ記述・便益が1行も無い）とsalience低下（締め
指示がS2の6アクションを1つも列挙していない）に絞り込まれた。

本Cycleは仕様§13.2「S2は観察対象のまま、ルール変更による強制は行わない」
に従い、prompt文言のみを次の5方向で修正する（engine/は一切変更しない）:
  1. 便益とコストを同格に並べる新セクション（対称化）
  2. 失敗コスト文を回避条件とセットの条件文にする（中立化）
  3. 締め指示でその時点で選べるアクションを全列挙する（D2）
  4. 倍掛け成功条件を毎回再掲する（P01-D2）
  5. Handover Memoryに「公式状態優先」を明示する

本ファイルはtests/test_dead_target_and_feedback.py・tests/test_double_up_llm.py
と同形式の文字列アサーションで、上記5方向の文言を固定する。
"""

import re

from engine.config import GameConfig
from engine.models import PlayerState, Card, CardRank

from llm.prompt_builder import (
    build_system_prompt,
    build_negotiation_prompt,
    build_commit_prompt,
    build_reflection_prompt,
    build_double_up_prompt,
)


# --- 共通ヘルパー（本ファイル内限定・engineにもllmにも影響しない） ---

def _make_player(pid: str = "P01", cash: int = 500_000, debt: int = 0) -> PlayerState:
    hand = [Card(rank=r, card_id=f"c{i}") for i, r in enumerate(list(CardRank)[:6])]
    return PlayerState(
        player_id=pid, cash=cash, debt_balance=debt, initial_loan=debt, hand=hand,
    )


def _base_visible_state(**overrides) -> dict:
    base = {
        "markets": [],
        "alive_players": ["P01", "P02"],
        "eliminated_players": [],
        "double_ups": [],
        "messages": [],
    }
    base.update(overrides)
    return base


def _extract_available_unavailable(prompt: str) -> tuple[str, str]:
    """締め指示直前の「## いま選べるアクション」ブロックから
    available行・unavailable行（無ければ空文字）を取り出す（D2用ヘルパー）
    """
    lines = prompt.split("\n")
    idx = next(i for i, l in enumerate(lines) if l == "## いま選べるアクション")
    available_line = lines[idx + 1]
    unavailable_line = ""
    if idx + 2 < len(lines) and lines[idx + 2].startswith("  いま選べないもの:"):
        unavailable_line = lines[idx + 2]
    return available_line, unavailable_line


class TestActionBenefitSymmetry:
    """§2-A: 対称化 — 各アクションの便益/コスト同格併記"""

    def test_each_s2_action_has_benefit_line(self):
        config = GameConfig.baseline_v1_s2(12)
        prompt = build_system_prompt("P01", config)
        actions = [
            "contract_propose", "contract_sign", "contract_cancel", "bounty_post",
            "card_trade_propose", "anonymous_broadcast", "transfer", "repay",
        ]
        lines = [l for l in prompt.splitlines() if l.strip().startswith("- ")]
        for action in actions:
            matched = [l for l in lines if action in l]
            assert matched, f"no benefit line found for {action}"
            assert any("便益=" in l and "コスト=" in l for l in matched), (
                f"{action} の行に便益=とコスト=が同一行に無い: {matched}"
            )

    def test_contract_benefit_wording(self):
        config = GameConfig.baseline_v1_s2(12)
        prompt = build_system_prompt("P01", config)
        # v0.8サイクル8.2 Step1でRULES_SUMMARYの契約節が圧縮され、
        # 「システムが自動で監査・執行」→「システムが自動執行/自動監査」に言い換えられた
        # （事実は不変。文言のみ）
        assert "システムが自動執行/自動監査" in prompt
        assert "縛る効果" in prompt

    def test_no_new_format_placeholder(self):
        """全プリセットでKeyErrorなし・新セクション内に未置換の{}が残らない"""
        presets = [
            GameConfig.default_8_s2(),
            GameConfig.baseline_v1(),
            GameConfig.baseline_v1_s2(),
            GameConfig.default_12(),
            GameConfig.default_20(),
        ]
        for config in presets:
            prompt = build_system_prompt("P01", config)
            section = prompt.split("## 各アクションの使いどころ")[1].split(
                "コミットフェイズのアクション:")[0]
            assert "{" not in section
            assert "}" not in section


class TestNeutralizedWarnings:
    """§2-B: 中立化 — 失敗コスト文に回避条件を併記"""

    def test_failed_action_slot_has_avoidance(self):
        config = GameConfig.baseline_v1_s2(12)
        prompt = build_system_prompt("P01", config)
        # v0.8サイクル8.2 Step1の圧縮で強調表現(**...**)付きに言い換えられた
        assert "**不成立アクションは枠を消費する**" in prompt
        assert "宛先が「生存者」欄にあるか・Free Cash十分か確認すれば避けられる" in prompt

    def test_dead_target_has_avoidance(self):
        config = GameConfig.baseline_v1_s2(12)
        prompt = build_system_prompt("P01", config)
        # v0.8サイクル8.2 Step1で1文に統合された（事実は不変）
        assert "脱落者指定は不成立でアクション枠を失うが" in prompt
        assert "生存者欄から選ぶ限り起きない" in prompt

    def test_double_contract_warning_has_remedy(self):
        config = GameConfig.baseline_v1_s2(12)
        prompt = build_system_prompt("P01", config)
        assert "「旧契約を全員で解除→新条件でcontract_propose」" in prompt
        assert "二重に結ぶと必ず型B違反で脱落します" not in prompt

    def test_eliminations_block_is_neutral(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player()
        vs = _base_visible_state(
            eliminated_players=[{"player_id": "P03", "round": 2, "reason": "bankruptcy"}],
        )
        prompt = build_negotiation_prompt(player, 3, 1, vs, config)
        assert "通常どおり成立します" in prompt
        assert "カードトレードは必ず不成立になり" not in prompt


class TestClosingEnumeration:
    """§2-C: D2 — 締め指示でその時点で選べるアクションを全列挙"""

    def _pending_fixture(self) -> dict:
        pending_contract = {
            "proposer": "P02", "contract_id": "C_1", "round_created": 4,
            "parties": ["P01", "P02"], "signed_by": ["P02"],
            "obligations": [
                {"obligor": "P02", "counterparty": "P01", "ob_type": "type_a_payment",
                 "round_num": 5, "details": {"amount": 100_000}},
            ],
        }
        trade_pending = {
            "trade_id": "T_1", "proposer": "P02", "round_proposed": 4, "cash_amount": 0,
            "give_card_rank": "ONE_PAIR", "with_player": "P01", "receive_card_rank": "FLUSH",
        }
        my_contract = {
            "contract_id": "C_2", "parties": ["P01", "P02"], "round_created": 1,
            "status": "active", "eliminated_parties": [], "cancelled_round": None,
            "obligations": [
                {"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment",
                 "round_num": 10, "details": {"amount": 50_000}, "ob_status": "upcoming"},
            ],
        }
        return _base_visible_state(
            contracts_pending=[pending_contract],
            trades_pending=[trade_pending],
            my_contracts=[my_contract],
        )

    def test_all_six_s2_actions_listed(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=500_000, debt=100_000)
        prompt = build_negotiation_prompt(player, 5, 1, self._pending_fixture(), config)
        available, _ = _extract_available_unavailable(prompt)
        for action in [
            "contract_propose", "contract_sign", "contract_cancel", "bounty_post",
            "card_trade_propose", "anonymous_broadcast",
        ]:
            assert action in available, f"{action} が選べるアクションに列挙されていない"

    def test_r12_excludes_card_trade(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=500_000, debt=100_000)
        prompt = build_negotiation_prompt(player, 12, 1, self._pending_fixture(), config)
        available, unavailable = _extract_available_unavailable(prompt)
        assert "card_trade_propose" not in available
        assert "card_trade_propose（R12は不可）" in unavailable

    def test_no_pending_excludes_sign_and_trade_accept(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=500_000, debt=100_000)
        vs = _base_visible_state()
        prompt = build_negotiation_prompt(player, 5, 1, vs, config)
        available, _ = _extract_available_unavailable(prompt)
        assert "contract_sign" not in available
        assert "card_trade_accept" not in available

    def test_zero_free_cash_marks_transfer_unavailable(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=100_000, debt=100_000)  # free_cash = 0
        vs = _base_visible_state()
        prompt = build_negotiation_prompt(player, 5, 1, vs, config)
        _, unavailable = _extract_available_unavailable(prompt)
        assert "transfer・bounty_post（Free Cash 0）" in unavailable

    def test_closing_no_longer_says_only_five(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player()
        vs = _base_visible_state()
        prompt = build_negotiation_prompt(player, 5, 1, vs, config)
        assert "ここではdm/broadcast/transfer/repay/pass等を選択" not in prompt

    def test_low_cash_marks_contract_propose_unavailable(self):
        # v0.8 D1でbaseline_v1_s2はcontract_fee=0（無料化）になったため、
        # 「発行料の現金不足」表示の回帰確認には非0の手数料を明示指定する。
        config = GameConfig.baseline_v1_s2(12).model_copy(update={"contract_fee": 100_000})
        player = _make_player(cash=50_000, debt=0)  # < contract_fee(100,000)
        vs = _base_visible_state()
        prompt = build_negotiation_prompt(player, 5, 1, vs, config)
        available, unavailable = _extract_available_unavailable(prompt)
        assert "contract_propose" not in available
        assert "contract_propose（発行料の現金不足）" in unavailable

    def test_anonymous_broadcast_unavailable_when_cash_short(self):
        config = GameConfig.baseline_v1_s2(12)
        vs = _base_visible_state()
        short_player = _make_player(cash=50_000, debt=0)  # < anon_broadcast_fee(100,000)
        short_prompt = build_negotiation_prompt(short_player, 5, 1, vs, config)
        _, unavailable = _extract_available_unavailable(short_prompt)
        assert "anonymous_broadcast（現金不足）" in unavailable

        enough_player = _make_player(cash=200_000, debt=0)
        enough_prompt = build_negotiation_prompt(enough_player, 5, 1, vs, config)
        available, _ = _extract_available_unavailable(enough_prompt)
        assert "anonymous_broadcast" in available

    def test_card_trade_round_limit_not_claimed(self):
        """visible_stateに無い情報（当ラウンド実施済みフラグ）は出さない（誤情報の回帰防止）"""
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player()
        vs = _base_visible_state()
        normal_prompt = build_negotiation_prompt(player, 5, 1, vs, config)
        assert "本ラウンド実施済み" not in normal_prompt

        r12_prompt = build_negotiation_prompt(player, 12, 1, vs, config)
        assert "R12は不可" in r12_prompt
        assert "本ラウンド実施済み" not in r12_prompt

    def test_sign_unavailable_phrase_avoids_reserved_wording(self):
        """§2-E-(b)の回帰防止: 署名待ちの契約が無いとき「署名待ち」を出さない"""
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player()
        vs = _base_visible_state()
        prompt = build_negotiation_prompt(player, 5, 1, vs, config)
        assert "署名できる契約がない" in prompt
        assert "署名待ち" not in prompt


class TestOneMarketPerRound:
    """§3: P01-D1 — 「1ラウンド1市場」の無条件明示"""

    def test_rules_summary_unconditional(self):
        config = GameConfig.baseline_v1_s2(12)
        prompt = build_system_prompt("P01", config)
        assert "1ラウンドに参加できる市場は1つだけ" in prompt

    def test_commit_prompt_states_it(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player()
        vs = _base_visible_state()
        prompt = build_commit_prompt(player, [], 1, vs, config)
        assert "1ラウンドに参加できる市場は1つだけです" in prompt

    def test_entry_fee_notes_are_singular(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player()
        vs = _base_visible_state()
        neg = build_negotiation_prompt(player, 5, 1, vs, config)
        commit = build_commit_prompt(player, [], 5, vs, config)
        for prompt in (neg, commit):
            assert "市場ごとにEntry Fee" not in prompt
            assert "1市場" in prompt

    def test_entry_fee_anchor_preserved(self):
        """§2-E-(a)の回帰防止: 既存テストが依存するアンカー文字列を保持"""
        config = GameConfig.baseline_v1_s2(12)
        prompt = build_system_prompt("P01", config)
        entry_line = next(
            l for l in prompt.splitlines() if "市場参加につきEntry Fee" in l
        )
        assert "10万円" in entry_line
        # v0.8サイクル8.2 Step1で「1ラウンド1市場なので毎ラウンド」→「1R1市場なので毎R」に短縮
        assert "1R1市場なので毎R" in entry_line


class TestDoubleUpRestatement:
    """§4: P01-D2 — 倍掛け成功条件の再掲（4プロンプト共通）"""

    def _du_fixture(self) -> dict:
        du = {"player_id": "P01", "deposit": 400_000, "success_round": 7}
        return _base_visible_state(double_ups=[du])

    def _assert_restated(self, prompt: str) -> None:
        for phrase in ["成功条件", "参加者が2人以上の市場", "空き巣", "全額没収"]:
            assert phrase in prompt, phrase

    def test_negotiation(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=800_000, debt=300_000)
        prompt = build_negotiation_prompt(player, 6, 3, self._du_fixture(), config)
        self._assert_restated(prompt)

    def test_commit(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=800_000, debt=300_000)
        prompt = build_commit_prompt(player, [], 6, self._du_fixture(), config)
        self._assert_restated(prompt)

    def test_reflection(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=800_000, debt=300_000)
        prompt = build_reflection_prompt(player, 6, self._du_fixture(), config)
        self._assert_restated(prompt)

    def test_double_up_existing(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=800_000, debt=300_000)
        prompt = build_double_up_prompt(player, 500_000, 6, self._du_fixture(), config)
        assert "既存の倍掛け預託中" in prompt
        self._assert_restated(prompt)

    def test_no_deposit_no_extra_lines(self):
        """回帰防止・長さ保証: 預託が無ければ成功条件3行は出ない"""
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=800_000, debt=300_000)
        vs = _base_visible_state()
        prompt = build_negotiation_prompt(player, 6, 3, vs, config)
        assert "参加者が2人以上の市場" not in prompt


class TestMemoryAuthority:
    """§5: Handover Memoryの「公式状態優先」明示"""

    def test_official_state_precedence(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=800_000, debt=300_000)
        vs = _base_visible_state()
        neg = build_negotiation_prompt(player, 6, 3, vs, config, memory="past note")
        commit = build_commit_prompt(player, [], 6, vs, config, memory="past note")
        refl = build_reflection_prompt(player, 6, vs, config, memory="past note")
        for prompt in (neg, commit, refl):
            assert "倍掛け預託中とその成功条件" in prompt
            assert "必ず欄の側を正とし" in prompt
            assert "このメモは過去の自分の記述です" in prompt


class TestPromptLengthBudget:
    """§6: prompt長の目標値（fixtureベースの実測）"""

    def test_negotiation_prompt_length_budget(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=800_000, debt=300_000)

        pending_contract = {
            "proposer": "P02", "contract_id": "C_PEND1", "round_created": 5,
            "parties": ["P01", "P02"], "signed_by": ["P02"],
            "obligations": [
                {"obligor": "P02", "counterparty": "P01", "ob_type": "type_a_payment",
                 "round_num": 6, "details": {"amount": 300_000}},
            ],
        }
        trade_pending = {
            "trade_id": "T_1", "proposer": "P02", "round_proposed": 6, "cash_amount": 50_000,
            "give_card_rank": "ONE_PAIR", "with_player": "P01", "receive_card_rank": "FLUSH",
        }
        messages = [
            {"sender": f"P{(i % 5) + 2:02d}", "type": "broadcast", "message": "x" * 60}
            for i in range(10)
        ]
        memory = "あ" * 3000

        worst_vs = _base_visible_state(
            markets=[
                {"market_id": "M01", "prize_pool": 900_000, "base_prize": 900_000, "carryover": 0},
                {"market_id": "M02", "prize_pool": 1_200_000, "base_prize": 600_000, "carryover": 600_000},
                {"market_id": "M03", "prize_pool": 500_000, "base_prize": 500_000, "carryover": 0},
            ],
            alive_players=[f"P{i:02d}" for i in range(1, 13)],
            double_ups=[{"player_id": "P01", "deposit": 400_000, "success_round": 7}],
            messages=messages,
            contracts_pending=[pending_contract],
            trades_pending=[trade_pending],
        )
        worst_prompt = build_negotiation_prompt(player, 6, 5, worst_vs, config, memory=memory)
        # v0.8サイクル8.2でFinance見込みの1行式統合・公開情報ブロック・署名待ち契約の
        # 受取プレビュー等を追加したため、worst caseの実測値が6599→6609字に増加。
        # 実測6609字を100単位切り上げ+約5%の安全マージンを取り7100へ改定（判断1）
        assert len(worst_prompt) <= 7100, len(worst_prompt)

        std_vs = _base_visible_state(
            markets=[{"market_id": "M01", "prize_pool": 900_000, "base_prize": 900_000, "carryover": 0}],
            alive_players=[f"P{i:02d}" for i in range(1, 13)],
            messages=[{"sender": "P02", "type": "broadcast", "message": "hello there"}],
        )
        std_memory = "い" * 1264
        std_prompt = build_negotiation_prompt(player, 6, 3, std_vs, config, memory=std_memory)
        assert len(std_prompt) <= 3550, len(std_prompt)

    def test_system_prompt_length_budget(self):
        config = GameConfig.baseline_v1_s2(12)
        prompt = build_system_prompt("P01", config)
        # Cycle 8.2 Step0実測: 8,177字（H1予約額0.03459 <= 0.035の制約から
        # 逆算した8,300を上限とする。Step1のRULES_SUMMARY圧縮後もこの範囲内）
        assert len(prompt) <= 8300, len(prompt)


class TestActionDescriptionMapping:
    """Cycle 5.2: 締め指示の選べるアクションに対応付け説明句を添える

    D1残存要因（Cycle 5.1実測: 契約系変換率baseline0/81→patched2/81に留まった）は、
    「いま口頭で合意した内容→どのアクションで拘束力を持たせるか」の対応付けが
    締め指示に1行も無かったことと推定。各availableアクションに15字前後の
    中立な説明句を括弧で添える。推奨・優先度・助言語彙は書かない。
    """

    def _pending_fixture(self) -> dict:
        pending_contract = {
            "proposer": "P02", "contract_id": "C_1", "round_created": 4,
            "parties": ["P01", "P02"], "signed_by": ["P02"],
            "obligations": [
                {"obligor": "P02", "counterparty": "P01", "ob_type": "type_a_payment",
                 "round_num": 5, "details": {"amount": 100_000}},
            ],
        }
        trade_pending = {
            "trade_id": "T_1", "proposer": "P02", "round_proposed": 4, "cash_amount": 0,
            "give_card_rank": "ONE_PAIR", "with_player": "P01", "receive_card_rank": "FLUSH",
        }
        my_contract = {
            "contract_id": "C_2", "parties": ["P01", "P02"], "round_created": 1,
            "status": "active", "eliminated_parties": [], "cancelled_round": None,
            "obligations": [
                {"obligor": "P01", "counterparty": "P02", "ob_type": "type_a_payment",
                 "round_num": 10, "details": {"amount": 50_000}, "ob_status": "upcoming"},
            ],
        }
        return _base_visible_state(
            contracts_pending=[pending_contract],
            trades_pending=[trade_pending],
            my_contracts=[my_contract],
        )

    ADVICE_WORDS = [
        # ユーザー指定
        "推奨", "有利", "おすすめ", "使うべき", "積極的", "活用",
        "すべき", "した方がよい", "した方が",
        # tests/test_prompt_finance.py:432 JUDGMENT_WORDS
        "安全", "危険", "履行不能", "署名すべき", "署名しない",
        "返済すべき", "避けるべき", "必ず違反で脱落", "矛盾",
        # tests/test_prompt_hand.py:269 ADVICE_WORDS
        "すべきです", "推奨します", "推奨されます", "注意しましょう", "気をつけましょう",
    ]

    def test_each_available_action_has_description(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=500_000, debt=100_000)
        prompt = build_negotiation_prompt(player, 5, 1, self._pending_fixture(), config)
        available, _ = _extract_available_unavailable(prompt)
        entries = available.strip().split(" / ")
        assert len(entries) == 13, entries
        for entry in entries:
            m = re.match(r"^[a-z_]+（[^（）]+）$", entry)
            assert m, f"説明句が付いていない: {entry}"

    def test_no_advice_vocabulary_in_descriptions(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=500_000, debt=100_000)
        prompt = build_negotiation_prompt(player, 5, 1, self._pending_fixture(), config)
        available, unavailable = _extract_available_unavailable(prompt)
        for word in self.ADVICE_WORDS:
            assert word not in available, f"助言語彙 '{word}' が選べるアクション行に含まれています"
            assert word not in unavailable, f"助言語彙 '{word}' が選べないもの行に含まれています"

    def test_contract_propose_description_maps_verbal_agreement(self):
        config = GameConfig.baseline_v1_s2(12)
        player = _make_player(cash=500_000, debt=100_000)
        prompt = build_negotiation_prompt(player, 5, 1, self._pending_fixture(), config)
        available, _ = _extract_available_unavailable(prompt)
        entry = next(e for e in available.split(" / ") if e.startswith("contract_propose"))
        # v0.8サイクル8.2 2-7で説明文を仕様文言へ変更。baseline_v1_s2はcontract_fee=0の
        # ため「発行料」は出ず「無料」になる（fee>0の場合はtest_fees_follow_configで確認）
        assert "正式契約を提案" in entry
        assert "相手が署名すれば成立" in entry
        assert "無料" in entry

    def test_fees_follow_config(self):
        config = GameConfig.baseline_v1_s2(12)
        config.contract_fee = 300_000
        config.anon_broadcast_fee = 300_000
        player = _make_player(cash=500_000, debt=100_000)
        prompt = build_negotiation_prompt(player, 5, 1, self._pending_fixture(), config)
        available, _ = _extract_available_unavailable(prompt)
        assert "発行料30万円" in available
        assert "。30万円" in available
        assert "発行料10万円" not in available
        assert "。10万円" not in available

    def test_unavailable_phrases_unchanged(self):
        # v0.8 D1でbaseline_v1_s2はcontract_fee=0（無料化）になったため、
        # 「発行料の現金不足」表示の回帰確認には非0の手数料を明示指定する。
        config = GameConfig.baseline_v1_s2(12).model_copy(update={"contract_fee": 100_000})
        player = _make_player(cash=500_000, debt=0)
        vs = _base_visible_state()
        prompt = build_negotiation_prompt(player, 5, 1, vs, config)
        _, unavailable = _extract_available_unavailable(prompt)
        for phrase in [
            "repay（借金なし）",
            "contract_sign（署名できる契約がない）",
            "contract_cancel（あなたが当事者の有効な契約なし）",
            "card_trade_accept・card_trade_reject（提案されているトレードなし）",
        ]:
            assert phrase in unavailable, phrase

        short_player = _make_player(cash=50_000, debt=0)
        short_prompt = build_negotiation_prompt(short_player, 5, 1, vs, config)
        _, short_unavailable = _extract_available_unavailable(short_prompt)
        assert "anonymous_broadcast（現金不足）" in short_unavailable
        assert "contract_propose（発行料の現金不足）" in short_unavailable

        zero_free_cash_player = _make_player(cash=100_000, debt=100_000)
        zero_free_cash_prompt = build_negotiation_prompt(zero_free_cash_player, 5, 1, vs, config)
        _, zero_free_cash_unavailable = _extract_available_unavailable(zero_free_cash_prompt)
        assert "transfer・bounty_post（Free Cash 0）" in zero_free_cash_unavailable

        r12_prompt = build_negotiation_prompt(player, 12, 1, vs, config)
        _, r12_unavailable = _extract_available_unavailable(r12_prompt)
        assert "card_trade_propose（R12は不可）" in r12_unavailable
