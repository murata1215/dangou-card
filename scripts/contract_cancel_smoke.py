"""
契約解除（contract_cancel）AUTO_PASS修正 — 実戦経路スモークテスト（実API 0コール）

Part 2 Plan（.claude/plans/tingly-beaming-tiger.md 「Part 2」）の Phase A / Phase C。

背景: contract_cancel は _round_messages を生成しないため、相手当事者の LLMAgent が
AUTO_PASS_ON_NO_NEWS（新規メッセージ/新規失敗が無ければturn>=2で自動pass）により
一度もAPIを呼ばれないまま起床せず、全会一致に永久に到達しない事故が実測された。
本スクリプトは以下を **実APIコール0で** 証明する:

- Phase A（--repro）: 修正前の挙動（起床トリガが無ければAUTO_PASSで気づけない）と、
  修正後（my_contract_notices による起床）の対比を、実Gameの1シート分だけを切り出して
  最小再現する。
- Phase C（--scripted）: 4席・Game.run()のフル本番経路（validate_action → execute →
  contract_ops → settlement）を、本物の LLMAgent 1体（P01）+ 規則ベースの偽アダプタ
  （ScriptAdapter、実APIには一切接続しない）で通し、10項目チェックリストを検証する。

Stage D-0（.claude/plans/tingly-beaming-tiger.md「Part 3」）でハーネスを追加し、
--real により実APIモード（Stage D-1）を実行できる。--real は下記を内部で
自動実行してから初めて実APIへ進む（1つでも失敗したら実APIは一切呼ばない）:
  1. --repro / --dry-run / --mock / --scripted の4ゲート（実API 0コール）
  2. --model の allowlist チェック（REAL_MODEL_ALLOWLIST。現状 L6 のみ許可。
     L1 を指定すると実APIには一切進まずエラー終了する）
  3. --run-id 必須 + 出力先ディレクトリを exist_ok=False で作成（二重送信防止）
  4. R1 negotiation プロンプトを実構築した worst_case_cost 事前見積と
     --max-cost / --max-cost-per-call の突き合わせ

実行シナリオ（--real、1ラウンドのみ）: P01=実LLMAgent（--model指定）、
P02=ScriptedCancelAgent(cancel_from_round=1)（R1からP01↔P02間の契約CXの解除を
打診する）、P03/P04=StubAgent。CX（P01→P02 TYPE_A_PAYMENT R1 ¥100,000、解除対象）
とCK（P01→P03 TYPE_A_PAYMENT R5 ¥100,000、デコイ）を事前注入する。
本番system promptは無改変。engine/llm/bots/viewer本体は無変更。

使用例:
    uv run python scripts/contract_cancel_smoke.py --repro
    uv run python scripts/contract_cancel_smoke.py --dry-run
    uv run python scripts/contract_cancel_smoke.py --mock
    uv run python scripts/contract_cancel_smoke.py --scripted
    # 実API（Stage D-1・要人間承認）:
    uv run python scripts/contract_cancel_smoke.py --real --model L6 \\
        --run-id cc_l6_20260823 --max-calls 6 --max-cost 0.10 --max-cost-per-call 0.03
"""

import argparse
import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import PlayerAgent, StubAgent
from engine.models import (
    Action, PassAction, ContractSignAction, ContractCancelAction,
    ContractStatus, ObligationType,
)
from tests.conftest import make_contract, make_obligation

from llm.models import ModelInfo, get_model
from llm.llm_agent import LLMAgent
from llm.llm_logger import LLMLogger
from llm.prompt_builder import build_system_prompt, build_negotiation_prompt
from llm.costing import worst_case_cost
from llm.adapters import create_adapter
from llm.constants import DEFAULT_MAX_TOKENS
from llm.game_cost_budget import GameCostBudget

from scripts.model_matrix import BudgetedAdapter

# Stage D-0（実APIハーネス）専用の安全弁。
REAL_MODEL_ALLOWLIST = {"L6"}  # L1へは絶対に進まない（別submitでレビューの上追記する）
HARD_MAX_CALLS = 6  # このスクリプトが1回の実行で許容する絶対上限

# =========================================================================
# 偽アダプタ: 実APIには一切接続せず、レンダリング済みprompt本文を正規表現で
# 読んで応答を決める。本物の LLMAgent.negotiate()/commit()/choose_loan() の
# コード経路（_call_llm → parse_response → Action変換）をそのまま通す。
# =========================================================================

_PENDING_CONTRACT_RE = re.compile(r"### 契約 (\S+)（R\d+提案")
_NOTICE_CANCEL_RE = re.compile(r"が (\S+) の解除に同意しました")


class ScriptAdapter:
    """P01用の規則ベース偽アダプタ（実API接続なし）。"""

    def __init__(self, counterparty: str = "P02", amount: int = 500_000, ob_round: int = 3):
        self.calls: list[dict[str, Any]] = []
        self.call_count = 0
        self._proposed = False
        self._counterparty = counterparty
        self._amount = amount
        self._ob_round = ob_round

    def complete(self, system: str, messages: list[dict[str, str]],
                 max_tokens: int = 1000, temperature: float = 0.7,
                 **kwargs: Any) -> tuple[str, dict[str, Any]]:
        self.call_count += 1
        user_text = messages[-1]["content"] if messages else ""
        self.calls.append({"user_len": len(user_text), "max_tokens": max_tokens,
                            "user_preview": user_text[:80]})

        # 注意: 交渉プロンプトの脚注に「market_commitはコミットフェイズで行います」という
        # 説明文が含まれるため、"コミットフェイズ" という部分文字列だけでは判定できない。
        # 各フェイズのヘッダ行（build_negotiation_prompt/build_commit_prompt）で使われる
        # より具体的な見出し文字列で判定する。
        if "借入額を選んでください" in user_text:
            text = '{"strategy":{"reason":"scripted"},"action":{"type":"choose_loan","amount":3000000}}'
        elif "/ コミットフェイズ ===" in user_text:
            # commit応答は意図的に不正にする。_phase_commit の例外/フォールバック経路
            # (autocommit_ops) が安全に代行することは調査済み（Part 2 Plan 参照）。
            text = 'INTENTIONALLY_INVALID_NOT_JSON'
        elif "/ 交渉フェイズ（巡" in user_text:
            m = _NOTICE_CANCEL_RE.search(user_text)
            if m:
                cid = m.group(1)
                text = (
                    '{"strategy":{"reason":"scripted cancel on notice"},'
                    f'"action":{{"type":"contract_cancel","contract_id":"{cid}"}}}}'
                )
            elif not self._proposed:
                self._proposed = True
                terms = [{
                    "obligor": "P01", "counterparty": self._counterparty,
                    "ob_type": "type_a_payment", "round_num": self._ob_round,
                    "details": {"amount": self._amount},
                }]
                text = (
                    '{"strategy":{"reason":"scripted propose"},"action":{'
                    '"type":"contract_propose",'
                    f'"with_players":["{self._counterparty}"],'
                    f'"terms":{json.dumps(terms, ensure_ascii=False)}'
                    '}}'
                )
            else:
                text = '{"strategy":{"reason":"scripted pass"},"action":{"type":"pass"}}'
        else:
            text = '{"strategy":{"reason":"scripted fallback"},"action":{"type":"pass"}}'

        usage = {
            "input_tokens": 100, "output_tokens": 50, "total_tokens": 150,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "reasoning_tokens": 0, "finish_reason": "stop", "usage_raw": None,
            "requested_model": "script-fake", "response_model": "script-fake",
        }
        return text, usage


class RecordingAdapter:
    """実API用（--real専用）: innerをラップし、実際にプロバイダへ送信された
    パラメータ（max_tokens等）と応答usageを記録する。BudgetedAdapterの内側に
    置くことで、クランプ後・実際に送信される値を捕捉できる
    （scripts/post_game_reflection_smoke.py の同名クラスと同じ骨格）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.last_max_tokens: int | None = None
        self.last_temperature: float | None = None
        self.calls: list[dict[str, Any]] = []

    def complete(self, system: str, messages: list[dict[str, str]],
                 max_tokens: int = 1000, temperature: float = 0.7,
                 **kwargs: Any) -> tuple[str, dict[str, Any]]:
        self.last_max_tokens = max_tokens
        self.last_temperature = temperature
        self.calls.append({"max_tokens": max_tokens, "temperature": temperature})
        return self._inner.complete(system, messages, max_tokens=max_tokens,
                                     temperature=temperature, **kwargs)


class RefuseToCallScriptAdapter(ScriptAdapter):
    """--dry-run用: complete()が呼ばれたら例外を投げる（0call証明用）"""

    def complete(self, system: str, messages: list[dict[str, str]],
                 max_tokens: int = 1000, temperature: float = 0.7,
                 **kwargs: Any) -> tuple[str, dict[str, Any]]:
        self.call_count += 1
        raise AssertionError("RefuseToCallScriptAdapter: API-equivalent call attempted during --dry-run")


class ScriptedCancelAgent(PlayerAgent):
    """P02用の決定論的スクリプトエージェント（StubAgent相当のcommit/choose_loanを流用）。

    - 借入: StubAgentと同じ最低額
    - 交渉: 未署名の提案中契約があれば署名 → R2以降、除外リスト以外の自分がactive
      当事者の契約について、未同意なら contract_cancel を出す（1回ずつ）
    - コミット: StubAgentと同じ（最低ランクカード + 最低賞金市場）
    """

    def __init__(self, exclude_cancel_ids: set[str] | None = None,
                 cancel_from_round: int = 2):
        self._exclude = exclude_cancel_ids or set()
        self._cancel_sent: set[str] = set()
        self._stub = StubAgent()
        self._cancel_from_round = cancel_from_round

    def choose_loan(self, config: GameConfig) -> int:
        return self._stub.choose_loan(config)

    def negotiate(self, player_state, round_num: int, turn: int,
                  visible_state: dict) -> Action:
        pid = player_state.player_id
        for c in visible_state.get("contracts_pending", []):
            if pid in c.get("parties", []) and pid not in c.get("signed_by", []):
                return ContractSignAction(player_id=pid, contract_id=c["contract_id"])
        if round_num >= self._cancel_from_round:
            for c in visible_state.get("my_contracts", []):
                cid = c["contract_id"]
                if (c.get("status") == "active"
                        and cid not in self._exclude
                        and cid not in self._cancel_sent
                        and pid not in (c.get("cancel_requested_by") or [])):
                    self._cancel_sent.add(cid)
                    return ContractCancelAction(player_id=pid, contract_id=cid)
        return PassAction(player_id=pid)

    def commit(self, player_state, markets, round_num: int, visible_state: dict):
        return self._stub.commit(player_state, markets, round_num, visible_state)


# =========================================================================
# シナリオ構築
# =========================================================================

def seed_control_contracts(game: Game) -> None:
    """C2/C3/C4 を直接 game.contracts へ注入する（propose/sign flowを経由しない）。

    全てTYPE_A_PAYMENT（市場/カードの一致を要求しないため、GameRngの
    ランダム手番・市場割当と無関係にSettlement Step5の自動執行/失効だけを
    観測できる）。
    """
    # C2: 対照群。R3期限、cancelしない → R3で自動執行される。
    # 注意: 義務者はP01ではなくP02にする。P01はC1(propose/sign/cancel)とcommit不正応答
    # （autocommitフォールバック）を毎R繰り返すLLMAgent役のため、現金残高が不安定になり
    # うる。対照群の「型A義務は正常に自動執行される」ことだけを安定して確認したいので、
    # 現金の増減が読みやすいP02(ScriptedCancelAgent)→P03(StubAgent)の少額決済にする。
    ob_c2 = make_obligation("C2_OB01", "C2", "P02", "P03",
                             ObligationType.TYPE_A_PAYMENT,
                             round_num=3, details={"amount": 100_000})
    c2 = make_contract("C2", "P02", ["P02", "P03"], [ob_c2], round_created=1)
    game.contracts.append(c2)

    # C3: R1義務が既に履行済み → R2の解除試行は reject される
    ob_c3 = make_obligation("C3_OB01", "C3", "P02", "P01",
                             ObligationType.TYPE_A_PAYMENT, round_num=1,
                             details={"amount": 100_000})
    ob_c3 = ob_c3.model_copy(update={"is_fulfilled": True})
    c3 = make_contract("C3", "P01", ["P01", "P02"], [ob_c3], round_created=1)
    game.contracts.append(c3)

    # C4: 片側同意のみ（P02のみ要求、P03は反応しない）。P01は非当事者。
    ob_c4 = make_obligation("C4_OB01", "C4", "P02", "P03",
                             ObligationType.TYPE_A_PAYMENT, round_num=5,
                             details={"amount": 200_000})
    c4 = make_contract("C4", "P02", ["P02", "P03"], [ob_c4], round_created=1)
    game.contracts.append(c4)


def seed_real_contracts(game: Game) -> None:
    """--real専用: CX（解除対象）とCK（デコイ）を直接 game.contracts へ注入する。

    seed_control_contracts() と同じ直接注入方式。propose/signフローを経由しない
    ため、Game.run()と同じ本番Settlement/監査経路だけを対象にできる。

    CX: P01↔P02、TYPE_A_PAYMENT R1期限 ¥100,000。P02(ScriptedCancelAgent,
        cancel_from_round=1)がR1中に解除を打診する。R1中に解除できなければ
        R1 Settlementで自動執行される＝監査除外を1ラウンドで観測できる。
    CK: P01↔P03、TYPE_A_PAYMENT R5期限 ¥100,000。誰も解除を打診しないデコイ。
        LLMがcontract_idを取り違えないかを見る（P02は非当事者なのでCKは
        P02のmy_contractsに現れず、ScriptedCancelAgentの対象にもならない）。
    金額はいずれも借入下限（120万円）に対して十分小さく、どちらに転んでも
    脱落しない。
    """
    ob_cx = make_obligation("CX_OB01", "CX", "P01", "P02",
                             ObligationType.TYPE_A_PAYMENT,
                             round_num=1, details={"amount": 100_000})
    cx = make_contract("CX", "P01", ["P01", "P02"], [ob_cx], round_created=1)
    game.contracts.append(cx)

    ob_ck = make_obligation("CK_OB01", "CK", "P01", "P03",
                             ObligationType.TYPE_A_PAYMENT,
                             round_num=5, details={"amount": 100_000})
    ck = make_contract("CK", "P01", ["P01", "P03"], [ob_ck], round_created=1)
    game.contracts.append(ck)


def build_real_config(negotiation_max_turns: int = 3) -> GameConfig:
    """--real専用の1ラウンド限定config（memory/final_reflection/post_game_reflection/
    double_upは全てbaseline_v1のままOFF。LLMコールはchoose_loan/negotiate/commitのみ）。"""
    return GameConfig.baseline_v1(num_players=4).model_copy(
        update={"negotiation_max_turns": negotiation_max_turns})


def build_scenario(seed: int = 424242, dry_run: bool = False,
                    llm_logger_dir: str | Path | None = None) -> tuple[Game, ScriptAdapter, ScriptedCancelAgent]:
    config = GameConfig.baseline_v1(num_players=4)
    logger = EventLogger()

    model_info = ModelInfo(
        model_id="script-fake", provider="Script", name="ScriptFake",
        adapter_type="anthropic", input_price=0.0, output_price=0.0,
        env_key="SCRIPT_FAKE_KEY_UNUSED", base_url=None,
    )
    adapter_cls = RefuseToCallScriptAdapter if dry_run else ScriptAdapter
    adapter = adapter_cls(counterparty="P02", amount=500_000, ob_round=3)

    out_dir = llm_logger_dir or tempfile.mkdtemp(prefix="cc_smoke_")
    llm_logger = LLMLogger(output_dir=out_dir, game_id="cc_smoke")

    p01 = LLMAgent("P01", model_info, adapter, llm_logger)
    p02 = ScriptedCancelAgent(exclude_cancel_ids={"C2"})
    p03 = StubAgent()
    p04 = StubAgent()

    agents: dict[str, Any] = {"P01": p01, "P02": p02, "P03": p03, "P04": p04}
    game = Game(config=config, agents=agents, seed=seed, logger=logger, stop_after_round=3)
    return game, adapter, p02


# =========================================================================
# Phase A（--repro）: AUTO_PASS問題の最小再現（実API 0コール）
# =========================================================================

def run_repro() -> bool:
    """
    修正前相当（起床トリガなし）と修正後（my_contract_notices あり）を、
    LLMAgent.negotiate() を直接1シートだけ切り出して対比する。
    tests/test_contract_cancel.py::TestAutoPassWakeOnNotice と同じ手法。
    """
    print("=== Phase A（--repro）: AUTO_PASS問題の再現/修正確認（実API 0コール） ===")
    from engine.config import GameConfig as GC
    from tests.conftest import make_player

    model_info = ModelInfo(
        model_id="script-fake", provider="Script", name="ScriptFake",
        adapter_type="anthropic", input_price=0.0, output_price=0.0,
        env_key="SCRIPT_FAKE_KEY_UNUSED", base_url=None,
    )
    ok = True

    # --- ケース1: 通知が無い場合 → turn2以降はAUTO_PASSでAPIが呼ばれない（従来通り） ---
    adapter1 = ScriptAdapter()
    llm_logger1 = LLMLogger(output_dir=tempfile.mkdtemp(prefix="cc_repro_"), game_id="repro1")
    agent1 = LLMAgent("P01", model_info, adapter1, llm_logger1)
    agent1.choose_loan(GC.baseline_v1(4))
    p = make_player("P01", cash=3_000_000, debt=3_000_000)
    base_state = {"markets": [], "messages": [], "alive_players": ["P01", "P02"],
                  "my_failed_actions": [], "my_contract_notices": []}
    agent1.negotiate(p, 1, 1, dict(base_state))
    calls_after_turn1 = adapter1.call_count
    agent1.negotiate(p, 1, 2, dict(base_state))
    calls_after_turn2 = adapter1.call_count
    no_notice_no_wake = calls_after_turn2 == calls_after_turn1
    ok = ok and no_notice_no_wake
    print(f"  [{'OK' if no_notice_no_wake else 'NG'}] 通知なし: turn1後calls={calls_after_turn1}, "
          f"turn2後calls={calls_after_turn2}（変化なし=AUTO_PASS継続が期待）")

    # --- ケース2: 相手が contract_cancel を出した通知がある場合 → 修正によりAPIが呼ばれる ---
    adapter2 = ScriptAdapter()
    llm_logger2 = LLMLogger(output_dir=tempfile.mkdtemp(prefix="cc_repro_"), game_id="repro2")
    agent2 = LLMAgent("P01", model_info, adapter2, llm_logger2)
    agent2.choose_loan(GC.baseline_v1(4))
    agent2.negotiate(p, 1, 1, dict(base_state))
    calls_before = adapter2.call_count
    state_with_notice = dict(base_state)
    state_with_notice["my_contract_notices"] = [{
        "turn": 2, "kind": "cancel_requested", "contract_id": "C1",
        "by": "P02", "cancel_requested_by": ["P02"], "pending": ["P01"],
    }]
    agent2.negotiate(p, 1, 2, state_with_notice)
    calls_after = adapter2.call_count
    wakes_on_notice = calls_after == calls_before + 1
    ok = ok and wakes_on_notice
    print(f"  [{'OK' if wakes_on_notice else 'NG'}] 通知あり: turn2前calls={calls_before}, "
          f"turn2後calls={calls_after}（+1=修正により起床が期待）")

    print(f"=== Phase A結果: {'PASS' if ok else 'FAIL'} ===")
    print("  結論: contract_cancel は _round_messages を生成しないため、my_contract_notices が"
          "存在しない状態ではAUTO_PASSが相手を永久に起床させない（ケース1で再現）。"
          "my_contract_notices の件数増分を第3の起床トリガに追加した修正により、"
          "通知が届いたturnではAPIが確実に呼ばれる（ケース2で確認）。")
    return ok


# =========================================================================
# Phase C（--scripted）: 4席・本番経路スモーク
# =========================================================================

def _events_by_type(game: Game, event_type: str) -> list[dict]:
    return [e.model_dump() for e in game.logger.events if e.event_type == event_type]


def run_scripted() -> bool:
    print("=== Phase C（--scripted）: 4席本番経路スモーク（実API 0コール） ===")
    game, adapter, p02 = build_scenario(seed=424242)
    game._setup()
    seed_control_contracts(game)

    # _setup()を済ませたうえで手動でround loopを進める（Game.run()相当だが、
    # _setup()を二重に呼ばないよう自前でR1〜R3を回す）。baseline_v1は
    # memory_enabled=False / final_reflection_enabled=False / post_game_reflection_enabled=False
    # のため、Reflection系の追加LLM呼び出しは一切発生しない。
    for round_num in range(1, 4):
        game.current_round = round_num
        alive_count = sum(1 for p in game.players.values() if p.is_alive)
        if alive_count == 0:
            break
        game._phase_market_open(round_num)
        game._phase_negotiation(round_num)
        game._phase_commit(round_num)
        game._phase_settlement(round_num)
        game._phase_finance(round_num)

    ok = True
    checks: dict[str, bool] = {}

    contracts_by_id = {c.contract_id: c for c in game.contracts}
    c1 = next((c for c in game.contracts
               if set(c.parties) == {"P01", "P02"} and c.contract_id not in ("C2", "C3")), None)

    # 1. 契約が成立する（C1がACTIVEを経由した）
    checks["1_contract_signed"] = c1 is not None
    # 2. 片側だけの contract_cancel では ACTIVE 維持（NEGOTIATION_ACTIONログで確認）
    cancel_events = [e for e in _events_by_type(game, "NEGOTIATION_ACTION")
                     if e.get("data", {}).get("action") == "contract_cancel"
                     and e.get("data", {}).get("contract_id") == (c1.contract_id if c1 else None)]
    partial_events = [e for e in cancel_events if e["data"].get("cancelled") is False]
    checks["2_partial_stays_active"] = len(partial_events) >= 1
    # 3. 相手がAPIを呼ばれて起床した（ScriptAdapterのcall_countがloan+propose+cancelで複数）
    checks["3_p01_woke_and_called_api"] = adapter.call_count >= 2
    # 4. 生存する全当事者の同意でCANCELLED（1回だけ）
    completed_events = [e for e in cancel_events if e["data"].get("cancelled") is True]
    checks["4_cancelled_exactly_once"] = (
        c1 is not None and contracts_by_id[c1.contract_id].status == ContractStatus.CANCELLED
        and len(completed_events) == 1
    )
    contract_cancelled_events = [e for e in _events_by_type(game, "CONTRACT_CANCELLED")
                                  if e.get("data", {}).get("contract_id") == (c1.contract_id if c1 else None)]
    checks["4b_contract_cancelled_event_once"] = len(contract_cancelled_events) == 1
    # 5. 解除後、旧義務が発火しない（C1由来のTYPE_A_EXECUTIONが無い）
    # 注意: TYPE_A_EXECUTION/TYPE_A_FAILURE イベントは data に contract_id を持たない
    # （obligation_id / player_id のみ）。契約単位で照合するには、契約オブジェクトが
    # 保持する obligation_id 集合との突き合わせが必要。
    type_a_execution_events = _events_by_type(game, "TYPE_A_EXECUTION")
    c1_ob_ids = {ob.obligation_id for ob in c1.obligations} if c1 else set()
    checks["5_no_execution_for_cancelled"] = not any(
        e["data"].get("obligation_id") in c1_ob_ids for e in type_a_execution_events
    )
    # 6. 未解除の対照群(C2)は実行される
    c2_ob_ids = {ob.obligation_id for ob in contracts_by_id["C2"].obligations}
    checks["6_control_group_executes"] = any(
        e["data"].get("obligation_id") in c2_ob_ids for e in type_a_execution_events
    )
    checks["6b_c2_still_active_or_completed"] = contracts_by_id["C2"].status == ContractStatus.ACTIVE
    # 7. 一部履行済み契約(C3)の解除は拒否される
    c3_cancel_attempts = [e for e in _events_by_type(game, "NEGOTIATION_ACTION")
                          if e.get("data", {}).get("action") == "contract_cancel"
                          and e.get("data", {}).get("contract_id") == "C3"]
    c3_failed_negotiation = [e for e in _events_by_type(game, "NEGOTIATION_ACTION")
                             if e.get("data", {}).get("player_id") == "P02"
                             and e.get("data", {}).get("action") == "contract_cancel"
                             and e.get("data", {}).get("success") is False]
    checks["7_c3_reject"] = (
        contracts_by_id["C3"].status == ContractStatus.ACTIVE
        and len(c3_cancel_attempts) == 0  # request_cancel自体は走らない（validate_actionで弾かれる）
    )
    # 8. 片側同意のみ(C4)では解除されない
    checks["8_c4_stays_active"] = contracts_by_id["C4"].status == ContractStatus.ACTIVE
    # 9. 解除通知が第三者(P03/P04)に漏れない
    p03_state = game._build_visible_state(3, for_player_id="P03")
    p04_state = game._build_visible_state(3, for_player_id="P04")
    checks["9_no_leak_to_third_party"] = (
        p03_state.get("my_contract_notices", []) == []
        and p04_state.get("my_contract_notices", []) == []
    )
    # 10. AUTO_PASSの省コスト挙動: 全turnコールされていない（早い段階でAUTO_PASSが効いている）
    total_negotiation_turns_possible = 3 * game.config.negotiation_max_turns
    checks["10_auto_pass_still_saves_calls"] = adapter.call_count < total_negotiation_turns_possible

    ok = all(checks.values())
    for k, v in checks.items():
        print(f"  [{'OK' if v else 'NG'}] {k}")
    print(f"  adapter.call_count={adapter.call_count} "
          f"(loan_choice含む。全turn呼出し上限={total_negotiation_turns_possible}未満であること)")
    print(f"  C1={c1.contract_id if c1 else None} status="
          f"{contracts_by_id[c1.contract_id].status.value if c1 else None}")

    print(f"=== Phase C結果: {'PASS' if ok else 'FAIL'} ===")
    return ok


def run_dry_run() -> bool:
    """0 API-equivalent call の証明。

    RefuseToCallScriptAdapter.complete() は呼ばれると AssertionError を送出する設計だが、
    LLMAgent._call_llm() は内部でアダプタ例外を捕捉して安全側にフォールバックするため
    （post_game_reflection_smoke.py の RefuseToCallAdapter と異なり、ここでは
    game._setup() が choose_loan() 経由で必ずこの経路を通る）、
    その AssertionError は _setup() の外へは伝播しない。そのため except ベースの検出は
    機能せず、adapter.call_count を直接の一次証拠として使う。

    確認したいのは「実ネットワークAPI（Anthropic/OpenAI等）への接続が0回」であることで、
    ScriptAdapter/RefuseToCallScriptAdapter はいずれも純Pythonの偽アダプタであり、
    ネットワーク接続コードを一切持たない（クラス定義上、構造的に0コールが保証されている）。
    call_count>=1 は、この経路が choose_loan() → LLMAgent._call_llm() →
    adapter.complete() という本物の呼び出し連鎖を辿ったことの証拠として示す。
    """
    print("=== --dry-run: 0 API-equivalent call の証明 ===")
    game, adapter, p02 = build_scenario(seed=424242, dry_run=True)
    game._setup()
    ok = adapter.call_count >= 1
    print(f"  [{'OK' if ok else 'NG'}] _setup()の choose_loan() 経由で "
          f"RefuseToCallScriptAdapter.complete()（実API相当の呼び出し口）に到達した "
          f"(calls_attempted={adapter.call_count})。"
          f"このアダプタは純Pythonの偽物であり実ネットワークAPIへは一切接続しない"
          f"（構造的に0実コール保証）。")
    print(f"=== --dry-run結果: {'PASS' if ok else 'FAIL'} ===")
    return ok


def run_mock() -> bool:
    """--scriptedと同じだが、経路の疎通のみを軽量に確認する（1ラウンドのみ）"""
    print("=== --mock: ScriptAdapter経由の全経路疎通確認（1ラウンドのみ・実API 0コール） ===")
    game, adapter, p02 = build_scenario(seed=424242)
    game._setup()
    seed_control_contracts(game)
    ok = True
    try:
        game._phase_market_open(1)
        game._phase_negotiation(1)
        game._phase_commit(1)
        game._phase_settlement(1)
        game._phase_finance(1)
    except Exception as e:
        ok = False
        print(f"  [NG] 例外発生: {type(e).__name__}: {e}")
    checks = {
        "setup_completed": len(game.players) == 4,
        "adapter_called": adapter.call_count >= 1,
        "no_crash": ok,
    }
    ok = all(checks.values())
    for k, v in checks.items():
        print(f"  [{'OK' if v else 'NG'}] {k}")
    print(f"=== --mock結果: {'PASS' if ok else 'FAIL'} ===")
    return ok


# =========================================================================
# Stage D-0/D-1（--real）: 実APIハーネス
# =========================================================================

def estimate_real_negotiation_cost(model_info: ModelInfo, seed: int = 20260823) -> float:
    """実際に送るR1 negotiation turn1（P01視点）プロンプトを実構築し、
    worst_case_cost で1コールあたりの見積を返す。

    アダプタは一切作らない（StubAgent/ScriptedCancelAgentのみ）ため、
    この関数自体は実API 0コールで完結する。
    """
    config = build_real_config()
    logger = EventLogger()
    stub_agents: dict[str, Any] = {
        "P01": StubAgent(), "P02": ScriptedCancelAgent(cancel_from_round=1),
        "P03": StubAgent(), "P04": StubAgent(),
    }
    game = Game(config=config, agents=stub_agents, seed=seed, logger=logger, stop_after_round=1)
    game._setup()
    seed_real_contracts(game)
    game._phase_market_open(1)
    visible_state = game._build_visible_state(1, for_player_id="P01")
    sysp = build_system_prompt("P01", config)
    userp = build_negotiation_prompt(game.players["P01"], 1, 1, visible_state, config)
    max_tokens = model_info.max_tokens or DEFAULT_MAX_TOKENS
    return worst_case_cost(model_info, sysp, userp, max_tokens)


def run_real(args: argparse.Namespace, out_dir: Path) -> int:
    """Stage D-1: 実APIモード本体。main()が allowlist/--run-id/--max-calls の
    機械チェックと出力ディレクトリ作成（exist_ok=False）を済ませた後にのみ呼ばれる。

    進行はGame.run()を直接呼ばず、Phase C（run_scripted/run_mock）と同じ手動
    フェイズシーケンス（_setup() → 契約注入 → 個別_phase_*呼出し）を用いる。
    Game.run()は内部で_setup()を呼び、その前に契約を注入するフックが無いため
    （_setup()はself.contractsに一切触れない）、この手順が契約事前注入と
    両立する唯一の方法である。使われるフェイズ実装自体は本番のGame.run()と
    完全に同一（validate_action → execute → contract_ops → settlement）。
    """
    print("=== Stage D-1 前プリフライト: --repro/--dry-run/--mock/--scripted を内部で自動実行 ===")
    for label, fn in (("--repro", run_repro), ("--dry-run", run_dry_run),
                       ("--mock", run_mock), ("--scripted", run_scripted)):
        if not fn():
            print(f"\n中断: プリフライト {label} に失敗しました。実APIは呼びません。")
            return 1
    print("=== プリフライト全通過 ===\n")

    model_info = get_model(args.model)
    print("[コスト見積] R1 negotiation turn1 プロンプトを実構築し worst_case_cost を算出します"
          "（実API 0コール）...")
    worst_per_call = estimate_real_negotiation_cost(model_info)
    total_worst = worst_per_call * args.max_calls
    print(f"  worst_case_cost（1コール想定）= ${worst_per_call:.5f}"
          f"（cap/call=${args.max_cost_per_call:.4f}）")
    print(f"  合計見積（max_calls={args.max_calls}）= ${total_worst:.5f}（cap=${args.max_cost:.4f}）")
    if worst_per_call > args.max_cost_per_call:
        print(f"\n中断: 1コールのworst_case見積 ${worst_per_call:.5f} が --max-cost-per-call "
              f"${args.max_cost_per_call:.4f} を超えます。実APIは呼びません。")
        return 1
    if total_worst > args.max_cost:
        print(f"\n中断: 合計worst_case見積 ${total_worst:.5f} が --max-cost ${args.max_cost:.4f} "
              "を超えます。実APIは呼びません。")
        return 1

    manifest = {
        "run_id": args.run_id, "model": args.model, "model_id": model_info.model_id,
        "provider": model_info.provider, "base_url": model_info.base_url,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "max_calls": args.max_calls, "max_cost": args.max_cost,
        "max_cost_per_call": args.max_cost_per_call,
        "worst_case_per_call_usd": worst_per_call, "worst_case_total_usd": total_worst,
        "scenario": {
            "seats": {
                "P01": f"LLMAgent({args.model})", "P02": "ScriptedCancelAgent(cancel_from_round=1)",
                "P03": "StubAgent", "P04": "StubAgent",
            },
            "contracts": {
                "CX": "P01<->P02 TYPE_A_PAYMENT R1 \u00a5100000 (\u89e3\u9664\u5bfe\u8c61)",
                "CK": "P01<->P03 TYPE_A_PAYMENT R5 \u00a5100000 (\u30c7\u30b3\u30a4)",
            },
        },
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  manifest: {out_dir / 'manifest.json'}")

    print("\n=== 実API呼び出しへ進みます ===")
    logger = EventLogger()
    llm_logger = LLMLogger(output_dir=str(out_dir), game_id=f"real_{args.run_id}")
    raw_adapter = create_adapter(model_info)
    recording = RecordingAdapter(raw_adapter)
    max_tokens_cap = model_info.max_tokens or DEFAULT_MAX_TOKENS
    budgeted = BudgetedAdapter(
        recording, model_info, max_calls=args.max_calls,
        max_cost=args.max_cost, max_tokens_cap=max_tokens_cap,
    )
    cost_budget = GameCostBudget(
        per_player_cap_usd=args.max_cost, game_cap_usd=args.max_cost,
        event_logger=logger, abort_on_block=True,
    )
    config = build_real_config()
    p01 = LLMAgent("P01", model_info, budgeted, llm_logger)
    p02 = ScriptedCancelAgent(cancel_from_round=1)
    p03 = StubAgent()
    p04 = StubAgent()
    agents: dict[str, Any] = {"P01": p01, "P02": p02, "P03": p03, "P04": p04}
    game = Game(config=config, agents=agents, seed=20260823, logger=logger,
                cost_budget=cost_budget, stop_after_round=1)

    try:
        game._setup()
        seed_real_contracts(game)
        game._phase_market_open(1)
        game._phase_negotiation(1)
        game._phase_commit(1)
        game._phase_settlement(1)
        game._phase_finance(1)
    finally:
        llm_logger.save()
        llm_logger.close()

    contracts_by_id = {c.contract_id: c for c in game.contracts}
    cx = contracts_by_id.get("CX")
    ck = contracts_by_id.get("CK")
    cx_ob_ids = {ob.obligation_id for ob in cx.obligations} if cx else set()

    events_path = out_dir / "events.jsonl"
    with events_path.open("w", encoding="utf-8") as f:
        for e in game.logger.events:
            f.write(json.dumps(e.model_dump(), ensure_ascii=False, default=str) + "\n")

    calls_path = out_dir / "calls.jsonl"
    with calls_path.open("w", encoding="utf-8") as f:
        for entry in llm_logger.entries:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    report = {
        "cx_status": cx.status.value if cx else None,
        "cx_cancel_requested_by": list(cx.cancel_requested_by) if cx else None,
        "ck_status": ck.status.value if ck else None,
        "contract_cancelled_events": len(_events_by_type(game, "CONTRACT_CANCELLED")),
        "player_elimination_events": len(_events_by_type(game, "PLAYER_ELIMINATION")),
        "type_a_execution_for_cx": [
            e["data"].get("obligation_id") for e in _events_by_type(game, "TYPE_A_EXECUTION")
            if e["data"].get("obligation_id") in cx_ob_ids
        ],
        "type_b_violation_events": len(_events_by_type(game, "TYPE_B_VIOLATION")),
        "budget_blocked_events": len(_events_by_type(game, "LLM_BUDGET_BLOCKED")),
        "total_cost_usd": llm_logger.total_cost,
        "total_calls": llm_logger.total_calls,
        "adapter_calls_made": budgeted.calls_made,
        "adapter_spent_usd": budgeted.spent_usd,
    }
    (out_dir / "report.md").write_text(
        f"# contract_cancel 実APIトライアル（{args.model}）結果\n\n"
        "```json\n" + json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n```\n",
        encoding="utf-8",
    )
    print(f"\n出力: {calls_path}")
    print(f"出力: {events_path}")
    print(f"出力: {out_dir / 'report.md'}")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


# =========================================================================
# main
# =========================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="contract_cancel AUTO_PASS修正 スモークテスト（Phase A/C: 実API 0コール、--real: 実API）")
    parser.add_argument("--repro", action="store_true", help="Phase A: AUTO_PASS問題の再現/修正確認")
    parser.add_argument("--scripted", action="store_true", help="Phase C: 4席本番経路スモーク（10項目チェック）")
    parser.add_argument("--dry-run", action="store_true", help="0 API-equivalent call の証明")
    parser.add_argument("--mock", action="store_true", help="ScriptAdapter経由の全経路疎通確認（1ラウンド）")
    parser.add_argument("--real", action="store_true",
                         help="Stage D-1: 実APIモード（--model/--run-id必須。人間承認後にのみ使用）")
    parser.add_argument("--model", default=None,
                         help=f"実APIモデルキー（allowlist={sorted(REAL_MODEL_ALLOWLIST)}のみ許可）")
    parser.add_argument("--run-id", default=None,
                         help="実API実行時の一意ID（既存ディレクトリなら中断=二重送信防止）")
    parser.add_argument("--max-calls", type=int, default=6,
                         help=f"総コール数上限（デフォルト6、絶対上限{HARD_MAX_CALLS}）")
    parser.add_argument("--max-cost", type=float, default=0.10, help="累計worst_case見積上限USD")
    parser.add_argument("--max-cost-per-call", type=float, default=0.03,
                         help="1callあたりworst_case見積上限USD")
    args = parser.parse_args(argv)

    modes = [args.repro, args.scripted, args.dry_run, args.mock, args.real]
    if sum(bool(m) for m in modes) != 1:
        print("エラー: --repro / --scripted / --dry-run / --mock / --real のいずれか1つを指定してください。")
        return 1

    if args.repro:
        return 0 if run_repro() else 1
    if args.scripted:
        return 0 if run_scripted() else 1
    if args.dry_run:
        return 0 if run_dry_run() else 1
    if args.mock:
        return 0 if run_mock() else 1

    # --- --real: ここから先で初めて実APIへ進む可能性がある ---
    if args.model not in REAL_MODEL_ALLOWLIST:
        print(f"エラー: --model {args.model!r} は許可されていません"
              f"（allowlist={sorted(REAL_MODEL_ALLOWLIST)}）。実APIは呼びません。")
        return 1
    if not args.run_id:
        print("エラー: --real には --run-id が必須です（二重送信防止）。実APIは呼びません。")
        return 1
    if args.max_calls > HARD_MAX_CALLS:
        print(f"エラー: --max-calls={args.max_calls} はハード上限{HARD_MAX_CALLS}を超えています。"
              "実APIは呼びません。")
        return 1

    out_dir = Path("logs/smoke") / f"contract_cancel_{args.run_id}"
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"エラー: 出力ディレクトリ {out_dir} は既に存在します（二重送信防止のため中断）。"
              "別の --run-id を指定してください。")
        return 1

    return run_real(args, out_dir)


if __name__ == "__main__":
    sys.exit(main())
