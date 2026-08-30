"""
実プロンプトダンプ生成スクリプト（読み取り専用ツール）

ダミー入力（実在の対戦ログとは無関係の架空データ）で `llm/prompt_builder.py` の
各プロンプトビルダー（build_system_prompt / build_negotiation_prompt /
build_commit_prompt / build_reflection_prompt / build_double_up_prompt）を呼び出し、
実際の出力全文を Markdown として `doc/prompt_dump/actual_prompts_20260830_v2.md`
へ書き出す。

- LLM API は一切呼び出さない。`llm/adapters.py` は import しない
- import するのは `engine.config` / `engine.models` / `engine.cards` /
  `llm.prompt_builder.build_{system,negotiation,commit,reflection,double_up}_prompt`
  のみ
- `engine/` のロジックには一切影響しない（呼び出すだけで変更・実行はしない）
- 実行すると出力ファイルを上書きする

使い方:
    uv run python scripts/dump_prompts.py

v0.8サイクル8.2で作成されたダミーシナリオ（P07視点・GameConfig.baseline_v1_s2(12)・
パターンA=R1契約ゼロ席・パターンB=R7契約リッチ席）を土台に、サイクル8.3のダンプv2
レビュー修正（F1〜F17）を反映するための最小限のダミー値変更のみを加えている
（詳細は本ファイル末尾の main() 直前のコメントを参照）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import GameConfig
from engine.models import Card, CardRank, PlayerState, Market
from engine.cards import create_deck
from llm.prompt_builder import (
    build_system_prompt,
    build_negotiation_prompt,
    build_commit_prompt,
    build_reflection_prompt,
    build_double_up_prompt,
)

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "doc" / "prompt_dump" / "actual_prompts_20260830_v2.md"

# v1（doc/prompt_dump/actual_prompts_20260829.md）の実測文字数。v1自体は本スクリプトで
# 再生成しないため、「v1比」列の計算用に定数として保持する。
V1_CHARS = {
    "system": 7_079,
    "neg_a": 1_397,
    "neg_b": 4_277,
    "commit": 2_662,
    "double_up": 1_588,
    "reflection": 3_114,
}


def _build_config() -> GameConfig:
    return GameConfig.baseline_v1_s2(12)


def _alive_all_12() -> list[str]:
    return [f"P{i:02d}" for i in range(1, 13)]


# =============================================================================
# パターンA: R1・契約ゼロ席
# =============================================================================

def _pattern_a_inputs():
    config = _build_config()
    player = PlayerState(
        player_id="P07", cash=4_000_000, debt_balance=4_000_000,
        initial_loan=4_000_000, hand=create_deck(),
    )
    visible_state = {
        "markets": [
            {"market_id": "M01", "prize_pool": 1_500_000, "base_prize": 1_500_000, "carryover": 0},
            {"market_id": "M02", "prize_pool": 1_000_000, "base_prize": 1_000_000, "carryover": 0},
            {"market_id": "M03", "prize_pool": 2_000_000, "base_prize": 2_000_000, "carryover": 0},
        ],
        "alive_players": _alive_all_12(),
        "eliminated_players": [],
        "double_ups": [],
        "messages": [],
        "initial_loans": {pid: 4_000_000 for pid in _alive_all_12()},
        "total_prize_budget": 45_000_000,
        "my_action_budget": {"used": 0, "max": 10},
    }
    return player, visible_state, config


# =============================================================================
# パターンB: R7・契約リッチ席（negotiation/commit/double_up/reflectionで共有）
# =============================================================================

def _pattern_b_base_state() -> dict:
    alive = ["P01", "P02", "P03", "P04", "P05", "P06", "P07", "P10", "P11", "P12"]
    markets = [
        {"market_id": "M01", "prize_pool": 1_500_000, "base_prize": 1_500_000, "carryover": 0},
        {"market_id": "M02", "prize_pool": 1_800_000, "base_prize": 1_000_000, "carryover": 800_000},
        {"market_id": "M03", "prize_pool": 2_000_000, "base_prize": 2_000_000, "carryover": 0},
    ]
    last_round_results = {
        "round": 6,
        "markets": [
            {
                "market_id": "M01",
                "commits": [
                    {"player_id": "P02", "card_rank": "FLUSH"},
                    {"player_id": "P05", "card_rank": "STRAIGHT_FLUSH"},
                    {"player_id": "P11", "card_rank": "TWO_PAIR", "auto": True},
                ],
                "participants": ["P02", "P05", "P11"],
                "winners": ["P05"], "prize_per_winner": 1_800_000,
                "total_pool": 1_800_000, "surged": False,
            },
            {
                "market_id": "M02", "commits": [], "participants": [],
                "winners": [], "prize_per_winner": 0, "total_pool": 800_000, "surged": False,
            },
            {
                "market_id": "M03",
                "commits": [
                    {"player_id": "P07", "card_rank": "THREE_OF_A_KIND"},
                    {"player_id": "P03", "card_rank": "ONE_PAIR"},
                    {"player_id": "P09", "card_rank": "HIGH_CARD"},
                    {"player_id": "P12", "card_rank": "TWO_PAIR"},
                    {"player_id": "P01", "card_rank": "ONE_PAIR"},
                    {"player_id": "P04", "card_rank": "HIGH_CARD"},
                    {"player_id": "P06", "card_rank": "TWO_PAIR"},
                ],
                "participants": ["P07", "P03", "P09", "P12", "P01", "P04", "P06"],
                "winners": ["P07"], "prize_per_winner": 4_200_000,
                "total_pool": 4_200_000, "surged": True,
            },
        ],
    }
    my_obligations = [
        {"contract_id": "C_4A7F21", "obligor": "P07", "counterparty": "P03",
         "ob_type": "type_b_market", "round_num": 7, "details": {"market_id": "M02"}},
        {"contract_id": "C_4A7F21", "obligor": "P07", "counterparty": "P03",
         "ob_type": "type_a_payment", "round_num": 8, "details": {"amount": 1_200_000}},
    ]
    my_contracts = [
        {
            "contract_id": "C_4A7F21", "parties": ["P07", "P03"], "round_created": 4,
            "obligations": [
                {"obligor": "P07", "counterparty": "P03", "ob_type": "type_b_no_market",
                 "round_num": 5, "details": {"market_id": "M01"}, "ob_status": "past"},
                {"obligor": "P07", "counterparty": "P03", "ob_type": "type_b_market",
                 "round_num": 7, "details": {"market_id": "M02"}, "ob_status": "due"},
                {"obligor": "P03", "counterparty": "P07", "ob_type": "type_a_payment",
                 "round_num": 9, "details": {"amount": 800_000}, "ob_status": "upcoming"},
                {"obligor": "P07", "counterparty": "P03", "ob_type": "type_a_payment",
                 "round_num": 8, "details": {"amount": 1_200_000}, "ob_status": "upcoming"},
            ],
            "eliminated_parties": [], "cancelled_round": None,
            "cancel_requested_by": ["P03"],
        },
        {
            "contract_id": "C_91B0C4", "parties": ["P07", "P09"], "round_created": 3,
            "obligations": [
                {"obligor": "P09", "counterparty": "P07", "ob_type": "type_a_payment",
                 "round_num": 6, "details": {"amount": 500_000}, "ob_status": "expired"},
            ],
            "eliminated_parties": ["P09"], "cancelled_round": None,
            "cancel_requested_by": [],
        },
    ]
    contracts_pending = [
        {
            "contract_id": "C_D30E88", "proposer": "P12", "parties": ["P07", "P12"],
            "signed_by": ["P12"], "round_created": 7,
            "obligations": [
                {"obligor": "P07", "counterparty": "P12", "ob_type": "type_b_card",
                 "round_num": 7, "details": {"card_rank": "FLUSH"}},
                {"obligor": "P12", "counterparty": "P07", "ob_type": "type_a_payment",
                 "round_num": 8, "details": {"amount": 1_200_000}},
            ],
        },
    ]
    # v0.8サイクル8.3 F1回帰テスト用ダミー変更: 現金の払い手をP02→P07（受諾者側）へ反転
    # （旧: cash_amount=+600,000でP02が払う。新: -600,000でP07=あなたが払う）
    trades_pending = [
        {
            "trade_id": "T_5C1A", "proposer": "P02", "round_proposed": 7,
            "cash_amount": -600_000, "give_card_rank": "FLUSH",
            "with_player": "P07", "receive_card_rank": "ROYAL_FLUSH",
        },
    ]
    my_trades_this_round = [
        {"trade_id": "T_9A11B2", "status": "proposed", "cash_amount": 0,
         "give_card_rank": "ONE_PAIR", "with_player": "P10", "receive_card_rank": "STRAIGHT_FLUSH"},
        {"trade_id": "T_7C4401", "status": "rejected", "cash_amount": 200_000,
         "give_card_rank": "TWO_PAIR", "with_player": "P11", "receive_card_rank": "FOUR_OF_A_KIND"},
        {"trade_id": "T_2D9E80", "status": "expired", "cash_amount": -150_000,
         "give_card_rank": "STRAIGHT", "with_player": "P12", "receive_card_rank": "FLUSH"},
    ]
    # v0.8サイクル8.3 契約無料化後の文脈に合わせてダミーメッセージ文言を更新
    messages = [
        {"sender": "P03", "type": "dm", "to": "P07",
         "message": "M02は譲る。代わりにR9の80万は必ず払え。"},
        {"sender": "P12", "type": "broadcast",
         "message": "M03は高騰しやすい。全員で分散したほうが得だ。"},
        {"sender": "P05", "type": "anonymous_broadcast",
         "message": "P05が裏でP02と組んでいる。"},
        {"sender": "P07", "type": "dm", "to": "P12",
         "message": "契約にするなら型Aの支払いはそちら側で。"},
    ]
    notices_common = [
        {"kind": "cancel_requested", "contract_id": "C_4A7F21", "by": "P03", "turn": 3,
         "cancel_requested_by": ["P03"], "pending": ["P07"]},
        {"kind": "contract_expired", "contract_id": "C_5E10A2", "turn": 2, "unsigned_by": ["P06"]},
        {"kind": "trade_rejected", "trade_id": "T_7C4401", "with_player": "P11", "turn": 4},
        {"kind": "trade_superseded", "trade_id": "T_2D9E80", "with_player": "P12", "turn": 4,
         "accepted_trade_id": "T_8899FF"},
    ]
    return {
        "markets": markets,
        "alive_players": alive,
        "eliminated_players": [
            {"player_id": "P08", "round": 4, "reason": "bankruptcy"},
            {"player_id": "P09", "round": 6, "reason": "contract_violation"},
        ],
        "last_round_results": last_round_results,
        # v0.8サイクル8.3 F14: P05の倍掛け預託にsuccess_round=8を設定
        "double_ups": [{"player_id": "P05", "deposit": 1_800_000, "success_round": 8}],
        "double_ups_resolved": [
            {"player_id": "P11", "deposit": 900_000, "result": "success", "payout": 1_800_000},
            {"player_id": "P06", "deposit": 500_000, "result": "forfeit"},
            {"player_id": "P08", "deposit": 300_000, "result": "forfeit_eliminated"},
        ],
        "initial_loans": {pid: 4_000_000 for pid in _alive_all_12()},
        "used_cards": {
            "P01": ["HIGH_CARD", "ONE_PAIR"],
            "P03": ["ONE_PAIR", "FLUSH"],
            "P07": ["HIGH_CARD", "THREE_OF_A_KIND"],
        },
        "contracts_public": [
            {"contract_id": "C_4A7F21", "parties": ["P07", "P03"], "status": "active"},
            {"contract_id": "C_88010B", "parties": ["P02", "P04"], "status": "active"},
        ],
        "trades_completed": [
            {"round": 5, "players": ["P02", "P04"]},
            {"round": 6, "players": ["P01", "P06"]},
        ],
        "messages": messages,
        "contracts_pending": contracts_pending,
        "my_obligations": my_obligations,
        "my_contracts": my_contracts,
        "bounties_public": [
            {"bounty_id": "B_7788AA", "amount": 1_000_000, "condition_type": "market_win_against",
             "condition": {"target_player": "P05"}, "poster": None, "is_active": True},
        ],
        "trades_pending": trades_pending,
        "my_trades_this_round": my_trades_this_round,
        "my_contract_notices": notices_common,
        "my_failed_actions": [
            {"turn": 2, "action": "transfer", "target": "P09", "reason": "宛先が脱落済み"},
            {"turn": 3, "action": "transfer", "target": "P09", "reason": "宛先が脱落済み"},
        ],
        "my_action_budget": {"used": 4, "max": 10},
        "my_auto_commits": [
            {"round": 5, "requested_market_id": "M02", "requested_card_rank": "FLUSH",
             "reason": "指定カードが手札にありません",
             "actual_market_id": "M02", "actual_card": "HIGH_CARD"},
        ],
        "total_prize_budget": 45_000_000,
    }


def _pattern_b_player() -> PlayerState:
    hand = [
        Card(rank=CardRank.ONE_PAIR, card_id="ONE_PAIR_2"),
        Card(rank=CardRank.TWO_PAIR, card_id="TWO_PAIR_1"),
        Card(rank=CardRank.STRAIGHT, card_id="STRAIGHT_1"),
        Card(rank=CardRank.FULL_HOUSE, card_id="FULL_HOUSE_1"),
        Card(rank=CardRank.FOUR_OF_A_KIND, card_id="FOUR_OF_A_KIND_1"),
        Card(rank=CardRank.ROYAL_FLUSH, card_id="ROYAL_FLUSH_1"),
    ]
    return PlayerState(
        player_id="P07", cash=5_200_000, debt_balance=3_100_000,
        initial_loan=4_000_000, hand=hand,
    )


PATTERN_B_MEMORY = (
    "R6: M03で420万獲得(高騰)。P03とはC_4A7F21でR7にM02参加義務。"
    "P03は解除を打診してきたが応じていない。P12が契約提案中。P09は脱落。"
)


# =============================================================================
# 生成本体
# =============================================================================

def _fired_headings(text: str) -> str:
    """`##`/`###` 見出しを機械抽出する（8.2補遺と同一手法）"""
    heads = re.findall(r"^(#{2,3} .+)$", text, re.MULTILINE)
    return " / ".join(f"`{h}`" for h in heads)


def _fence(text: str) -> str:
    return f"````\n{text}\n````"


def generate() -> str:
    config = _build_config()

    # 1. system_prompt
    system_prompt = build_system_prompt("P07", config)

    # 2. negotiation（パターンA）
    player_a, vs_a, _ = _pattern_a_inputs()
    neg_a = build_negotiation_prompt(player_a, 1, 1, vs_a, config)

    # 3. negotiation（パターンB）
    player_b = _pattern_b_player()
    vs_b = _pattern_b_base_state()
    neg_b = build_negotiation_prompt(player_b, 7, 4, vs_b, config)

    # 4. commit（パターンBと同一visible_state。手札不一致の直前戦略メモ付き）
    markets_b_objs = [
        Market(market_id="M01", base_prize=1_500_000, carryover=0),
        Market(market_id="M02", base_prize=1_000_000, carryover=800_000),
        Market(market_id="M03", base_prize=2_000_000, carryover=0),
    ]
    commit = build_commit_prompt(
        player_b, markets_b_objs, 7, vs_b, config,
        negotiation_messages=vs_b["messages"],
        last_strategy={
            "target_market": "M02", "reason": "契約義務のためM02",
            "card_plan": "FLUSH", "current_goal": "借金完済", "emotion": "疑",
        },
    )

    # 5. double_up（R7・M03の高騰勝ちで420万円獲得直後）
    double_up = build_double_up_prompt(player_b, 4_200_000, 7, vs_b, config)

    # 6. reflection（R7末。last_round_results.roundを7に差し替え、通知にdouble_up_blockedを追加）
    vs_reflection = dict(vs_b)
    vs_reflection["last_round_results"] = {**vs_b["last_round_results"], "round": 7}
    vs_reflection["my_contract_notices"] = vs_b["my_contract_notices"] + [
        {"kind": "double_up_blocked", "eligible_prize": 600_000, "cash": 300_000, "min_repay": 400_000},
    ]
    reflection = build_reflection_prompt(player_b, 7, vs_reflection, config, memory=PATTERN_B_MEMORY)

    outputs = {
        "system": system_prompt, "neg_a": neg_a, "neg_b": neg_b,
        "commit": commit, "double_up": double_up, "reflection": reflection,
    }

    def row(key: str, label: str, fn_ref: str) -> str:
        text = outputs[key]
        chars = len(text)
        rows_n = text.count("\n") + 1
        v1 = V1_CHARS[key]
        diff = chars - v1
        sign = "+" if diff >= 0 else ""
        return f"| {label} | `{fn_ref}` | {chars:,} | {rows_n} | {sign}{diff:,}字 |"

    summary_table = "\n".join([
        "| # | プロンプト | 生成関数 | 文字数 | 行数 | v1比 |",
        "|---|---|---|---:|---:|---|",
        "| 1 " + row("system", "system_prompt", "build_system_prompt").split("|", 1)[1],
        "| 2 " + row("neg_a", "negotiation prompt（パターンA: 契約ゼロ）", "build_negotiation_prompt").split("|", 1)[1],
        "| 3 " + row("neg_b", "negotiation prompt（パターンB: 契約あり）", "build_negotiation_prompt").split("|", 1)[1],
        "| 4 " + row("commit", "commit prompt", "build_commit_prompt").split("|", 1)[1],
        "| 5 " + row("double_up", "double_up prompt", "build_double_up_prompt").split("|", 1)[1],
        "| 6 " + row("reflection", "reflection prompt", "build_reflection_prompt").split("|", 1)[1],
    ])

    doc = f"""# 嘘八百万—談合カード— 実プロンプトダンプ v2（2026-08-30）

## この文書について

本ファイルは `doc/prompt_dump/actual_prompts_20260829.md`（以下「v1」）の後継版です。v1作成後、サイクル8.1（エンジン差分）とサイクル8.2（v0.8プロンプト文面の一括修正）により `llm/prompt_builder.py` の出力文面が変化したため、**同一のダミーシナリオ・同一の手法**で全プロンプトを再生成し、最新の実出力をそのまま転記しています。

- 生成に使用した `GameConfig`: `GameConfig.baseline_v1_s2(12)`（S2ルール有効・12席・12ラウンド。v1と同一設定。`contract_fee=0` のため、本ダンプでは契約提案が「無料」と表示されます — v1の「発行料10万円」はサイクル8.1で契約無料化された結果、現行コードでは再現されません）
- ダミーの主人公プレイヤーID: **P07**（v1と同一。実在の対戦ログとは無関係の架空ID）
- 金額・カード名・契約ID・メッセージ本文・他プレイヤーの行動はv1と同一のダミー値を土台に用いており、実際のプレイ記録ではありません
- コード変更は一切行っていません。LLM APIも一切呼び出していません（`llm/adapters.py` は import すらしていません）
- 生成スクリプトは `scripts/dump_prompts.py`（リポジトリに含まれる読み取り専用ツール）です
- 全文コードブロックの外枠には4連続バッククォート（````）を使用しています。system_prompt本文にJSON例の```コードフェンスが含まれるため、3連続バッククォートでは本文が途中で切れて見えてしまうことの回避措置です
- 「発火している条件付きブロック」欄は、実際の出力テキストに含まれる `##`/`###` 見出しを機械的に抽出したものです（grepによる抽出）
- **【2026-08-30 補遺】** 初回生成時のパターンB（負債・契約リッチ席）は、v0.8サイクル8.2で追加された `visible_state` キーの一部（`double_ups_resolved` / `my_trades_this_round` / `contract_expired`/`trade_rejected`/`trade_superseded`/`double_up_blocked` 種別の通知）が空のままで、対応するプロンプトブロックが1度も出力に現れていなかった。本補遺で上記キーへダミー値を投入し、`negotiation`（パターンB）・`commit`・`double_up`・`reflection` の4プロンプトでこれらのブロックを実際に発火させたうえで再計測している。
- **【2026-08-30 補遺2（サイクル8.3）】** ダンプv2レビュー（17点、F1〜F17）の指摘を受けて `llm/prompt_builder.py` を修正し、同一シナリオで再生成した。ダミー値の変更は以下の3点のみ（それ以外の数値・シナリオは補遺1から不変）: ①F14実演のためP05の倍掛け預託に `success_round=8` を設定 ②F1の受諾者払い分岐を実演するためトレード `T_5C1A` の `cash_amount` を正から負へ反転（P02払い→P07=あなた払い） ③契約無料化後の文脈に合わせてダミーメッセージ「契約にするなら発行料はそちら持ちで。」を「契約にするなら型Aの支払いはそちら側で。」に変更。生成スクリプトは本補遺よりリポジトリ内 `scripts/dump_prompts.py` として恒久化した（サイクル8.2時点は `/tmp/` の一時スクリプトだった）。

## 文字数サマリ

{summary_table}

（1・2行目は補遺1から変更なし。3〜6行目は本補遺2でのF1〜F17反映後の値）

いずれの増減も上記のダミー値変更3点と、`llm/prompt_builder.py` のサイクル8.3修正（F1: トレード現金表示、F2: 署名待ち手札なし警告、F3: 「アクション枠を1つ消費します」、F4: 契約・トレード失効通知の[R{{n}}末]形式化、F8: 初期借入額の1行化、F9: 総賞金予算ラベル、F10: 「2倍の払出」、F11〜F13: double_up次R見通しの冒頭サマリ・結論・型A義務原資、F15: 他者倍掛けのsuccess_round明示、F16: double_up_blocked通知の預託後現金表記、F17: Finance見込み行のforecast_labelパラメータ化）に由来するものです。system_promptの増減はF5（identityの空行）・F6（宛先例のID）・F7（弱いカードで勝つ）の3点のみです。

## 1. system_prompt

### メタ情報

- **生成関数**: `build_system_prompt`
- **文字数/行数**: 上表参照
- **送信条件**: 無条件。全席・全フェイズ共通の固定文（1ゲーム内で全プレイヤー同一内容）。
- **送信フェイズ**: 全フェイズ（loan / negotiation / commit / double_up / reflection のすべてのAPI呼び出しに、組み立てたものがそのまま添付される）
- **発火している条件付きブロック**（機械抽出。grep `^##`）: {_fired_headings(system_prompt)}
  - すべて固定テンプレートで無条件に発火（system_promptに条件分岐は無い。ただし`contract_propose`の説明文中の「無料」の語はconfig.contract_feeに依存し、本ダンプでは`baseline_v1_s2`のcontract_fee=0により「無料」が出力される）

> 以下は上記ダミー入力に対する実際の出力全文です。プレイヤーID・金額・カード名・契約ID・メッセージ本文はすべてダミー値です。省略・要約は行っていません。

### 全文

{_fence(system_prompt)}

## 2. negotiation prompt（パターンA: 契約ゼロ）

### メタ情報

- **生成関数**: `build_negotiation_prompt`
- **文字数/行数**: 上表参照
- **送信条件**: R1・巡1・手札12枚フル・現金=借入額=400万円・Free Cash 0の席。契約義務なし・署名待ち契約なし・カードトレード提案なし・引き継ぎメモなし・脱落者なし・前ラウンド結果なし（R1のため）を想定したダミー。
- **送信フェイズ**: Negotiationフェイズ
- **発火している条件付きブロック**（機械抽出。grep `^##`）: {_fired_headings(neg_a)}

> 以下は上記ダミー入力に対する実際の出力全文です。プレイヤーID・金額・カード名・契約ID・メッセージ本文はすべてダミー値です。省略・要約は行っていません。

### 全文

{_fence(neg_a)}

## 3. negotiation prompt（パターンB: 契約あり）

### メタ情報

- **生成関数**: `build_negotiation_prompt`
- **文字数/行数**: 上表参照
- **送信条件**: R7・巡4。以下の条件付きブロックをすべて発火させたダミー席: 6枚に減った手札、進行中の型B義務（今R期限）を含む契約C_4A7F21（解除同意1/2、R8期限の型A支払義務120万円あり）、脱落者P09を含む消化済み契約C_91B0C4、P12提案の署名待ち契約C_D30E88（自分がまだ未署名・署名すれば型A受取が発生。要求カードFLUSHは現在の手札に無いためF2警告が発火）、P02提案のカードトレードT_5C1A（サイクル8.3でP07払いに変更）、脱落者P08/P09、AUTO COMMIT実績、倍掛け結果3件（P11成功・P06没収・P08脱落没収）、契約解除通知に加え`contract_expired`/`trade_rejected`/`trade_superseded`通知3件、不成立アクション（P09宛て2回）、公開報奨B_7788AA、他プレイヤーP05の倍掛け中預託（サイクル8.3でsuccess_round=8を明示）、公示された正式契約2件（自分1件+他者間1件）、成立済みカードトレード2件（P02⇄P04・P01⇄P06）、各プレイヤーの使用済みカード、初期借入額12人分、DM/broadcast/匿名/DM各1件、自分が今ラウンドに提案したカードトレード3件（受諾待ち/拒否/失効）
- **送信フェイズ**: Negotiationフェイズ
- **発火している条件付きブロック**（機械抽出。grep `^##`/`^###`）: {_fired_headings(neg_b)}

> 以下は上記ダミー入力に対する実際の出力全文です。プレイヤーID・金額・カード名・契約ID・メッセージ本文はすべてダミー値です。省略・要約は行っていません。

### 全文

{_fence(neg_b)}

## 4. commit prompt

### メタ情報

- **生成関数**: `build_commit_prompt`
- **文字数/行数**: 上表参照
- **送信条件**: R7・パターンBと同一visible_state。契約義務あり・直前戦略メモに手札不一致あり（`card_plan: FLUSH` が今の手札に無い）。
- **送信フェイズ**: Commitフェイズ
- **発火している条件付きブロック**（機械抽出。grep `^##`/`^###`）: {_fired_headings(commit)}
  - `commit`プロンプトは`_render_contract_notice_block`・`_render_my_trades_this_round_block`を呼ばない設計のため、通知一覧・今R提案トレード一覧は不発火のまま（仕様どおり）

> 以下は上記ダミー入力に対する実際の出力全文です。プレイヤーID・金額・カード名・契約ID・メッセージ本文はすべてダミー値です。省略・要約は行っていません。

### 全文

{_fence(commit)}

## 5. double_up prompt

### メタ情報

- **生成関数**: `build_double_up_prompt`
- **文字数/行数**: 上表参照
- **送信条件**: R7・市場M03の高騰勝ちで420万円獲得直後。パターンBと同一visible_state（契約義務あり）。
- **送信フェイズ**: Settlement内・倍掛け選択タイミング（TAKE/DOUBLE選択）
- **発火している条件付きブロック**（機械抽出。grep `^##`/`^###`）: {_fired_headings(double_up)}
  - v0.8サイクル8.3のF11〜F13修正により、「DOUBLEを選んだ場合の次ラウンド見通し」ブロックの冒頭サマリ・結論・型A義務原資の文言が刷新されている（数値の計算式自体は不変）

> 以下は上記ダミー入力に対する実際の出力全文です。プレイヤーID・金額・カード名・契約ID・メッセージ本文はすべてダミー値です。省略・要約は行っていません。

### 全文

{_fence(double_up)}

## 6. reflection prompt

### メタ情報

- **生成関数**: `build_reflection_prompt`
- **文字数/行数**: 上表参照
- **送信条件**: R7末（Settlement/Finance完了後）。パターンBと同一visible_state（`last_round_results.round`を7に差し替え、通知に`double_up_blocked`を追加）。契約義務あり。
- **送信フェイズ**: Reflection（引き継ぎメモリ執筆）フェイズ・ラウンド末に1回
- **発火している条件付きブロック**（機械抽出。grep `^##`/`^###`）: {_fired_headings(reflection)}
  - v0.8サイクル8.3のF16修正により、`double_up_blocked`通知が「預託後の現金」表記（engineの実判定と一致）で描画されている

> 以下は上記ダミー入力に対する実際の出力全文です。プレイヤーID・金額・カード名・契約ID・メッセージ本文はすべてダミー値です。省略・要約は行っていません。

### 全文

{_fence(reflection)}
"""
    return doc


def main() -> None:
    text = generate()
    OUTPUT_PATH.write_text(text, encoding="utf-8")
    print(f"wrote {OUTPUT_PATH} ({len(text):,} chars)")


if __name__ == "__main__":
    main()
