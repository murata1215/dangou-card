"""
FINAL_REFLECTION 小規模疎通テスト（実API最小コスト・6モデル×1コール）

次のL12×R12本番でFINAL_REFLECTION（脱落者の最終コメント）が全モデルで
問題なく動くかを、フルゲームを回さずに最小コストで確認する。

設計（doc/trials/final_reflection_smoke_2026-08-22.md 参照）:
- 現行L12ロスターのユニーク6モデル（L1〜L6、get_model()で解決。ハードコードしない）を
  各1回だけ実APIへ呼ぶ（合計6call、実測コスト見積 ≈$0.03）。
- 各モデルに、実R12ログの脱落パターンを模した合成脱落コンテキストを1つずつ割り当てる。
  ゲームは起動せず、Bot 12体だけの実Gameインスタンスを対象ラウンドまで進めて
  本物の会話・市場結果を作った上で、engine.elimination.forced_liquidation()（本物の
  清算ロジック）で対象seatを合成脱落させ、実際のevent形状（BANKRUPTCY /
  AUTO_COMMIT_FAILURE / ELIMINATION+FORCED_LIQUIDATION / FORCED_LIQUIDATION単独 /
  SURVIVAL_CHECK+FORCED_LIQUIDATION）を記録する。そのseatだけLLMAgentへ差し替え、
  Game._phase_final_reflection() を実際に呼ぶ（_build_elimination_context() /
  build_final_reflection_prompt() / final_reflect() / parse_final_reflection() /
  ログ後付けを全て実経路で通す）。
- 実R12ログ（logs/llm/trial_C_l12_r12_20260822/）はHandover Memoryサンプルの
  読み取り参照専用（書き込み一切なし）。

API-free gate（必須、実API呼出しの前に自動実行）:
    --dry-run          : 6シーンを構築し、RefuseToCallAdapterで0callを証明。
                          rendered promptをdry_run_prompts.mdへ出力。
    --parser-selftest  : parse_final_reflection() の4段フォールバックを検証。
    --mock             : FakeOkAdapterで6シーン全通しし、ルーティング/
                          sent_max_tokens==FINAL_REFLECTION_MAX_TOKENS（GameConfig由来）/
                          Memory・state非破壊/二重発火なしを検証。
実API実行時は、上記3ゲート相当を必ず内部で自動実行し、1つでも失敗したら
実APIには一切進まない（AdapterErrorやHTTP接続を一切発生させない）。

安全策:
- HARD_MAX_CALLS=6（絶対上限）、--max-cost-per-call・--max-cost で二重の予算天井。
- BudgetedAdapter（scripts/model_matrix.py、変更なしで再利用）で1モデル1callに強制。
- --run-id ディレクトリを exist_ok=False で作成 → 二重送信を機械的に阻止。
- 2連続の予期しない失敗で残りを中断（個別失敗は継続、システム的な障害は停止）。
- 出力は logs/smoke/final_reflection_{run_id}/ 専用ディレクトリのみ
  （logs/llm/trial_* には一切書き込まない）。

使用例:
    uv run python scripts/final_reflection_smoke.py --dry-run
    uv run python scripts/final_reflection_smoke.py --parser-selftest
    uv run python scripts/final_reflection_smoke.py --mock
    uv run python scripts/final_reflection_smoke.py --run-id fr_smoke_20260822 \\
        --max-calls 6 --max-cost 0.08

    # --cases でCASE_SPECSの部分集合だけを対象にできる（例: L1/L6のみの再試験）。
    # final_reflection_max_tokens=3000化後、L1のworst_case見積が$0.02に近いため、
    # per-callキャップは0.03以上を推奨:
    uv run python scripts/final_reflection_smoke.py --dry-run --cases C1,C6
    uv run python scripts/final_reflection_smoke.py --mock --cases C1,C6
    uv run python scripts/final_reflection_smoke.py --run-id fr_smoke2_20260822 --cases C1,C6 \\
        --max-calls 2 --max-cost 0.05 --max-cost-per-call 0.03
"""

import argparse
import copy
import json
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv
load_dotenv()

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine import elimination as elim_ops
from bots import BOT_REGISTRY

from llm.models import get_model, ModelInfo
from llm.costing import worst_case_cost
from llm.adapters import create_adapter, AdapterError
from llm.prompt_builder import build_system_prompt, build_final_reflection_prompt
from llm.response_parser import (
    parse_final_reflection, extract_memory_with_status, VALID_EMOTIONS,
)
from llm.llm_agent import LLMAgent
from llm.llm_logger import LLMLogger
from llm.game_cost_budget import GameCostBudget

from scripts.model_matrix import BudgetedAdapter

HARD_MAX_CALLS = 6  # このスクリプトが1回の実行で許容する絶対上限（安全弁）

REAL_LOG_DIR = Path("logs/llm/trial_C_l12_r12_20260822")  # 読み取り参照専用

FINAL_REFLECTION_MAX_TOKENS = GameConfig.baseline_v1_s2(num_players=12).final_reflection_max_tokens
# ↑ ハードコードせず GameConfig から導出する。engine/config.py の
#   final_reflection_max_tokens を変更すれば、このスクリプトの見積・
#   mockアサーション・BudgetedAdapter(max_tokens_cap=...) も自動追従する。


# =========================================================================
# 6ケース定義（モデルは get_model() で解決。model_idのハードコードはしない）
# =========================================================================

@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    model_key: str
    round_num: int
    reason: str
    event_shape: str
    description: str
    real_log_analogue: str
    fallback_memory: str
    seed: int


CASE_SPECS: list[CaseSpec] = [
    CaseSpec(
        case_id="C1", model_key="L1", round_num=4, reason="bankruptcy",
        event_shape="bankruptcy_commit",
        description="commitフェイズEntry Fee破産（単独BANKRUPTCY）",
        real_log_analogue="P06 R9 相当",
        fallback_memory="R3終了時点: 現金8万、借金310万。Free Cashが尽きており、次RのEntry Fee10万円が払えない見込み。",
        seed=900101,
    ),
    CaseSpec(
        case_id="C2", model_key="L2", round_num=6, reason="contract_violation",
        event_shape="auto_commit_failure",
        description="commitフェイズ契約矛盾によるAUTO_COMMIT_FAILURE",
        real_log_analogue="P09 R6 相当",
        fallback_memory="R5終了時点: P02とM01不可侵契約、P05とM03協調契約を二重に締結。両立できない可能性が高く、次Rはどちらかを必ず違反する。",
        seed=900102,
    ),
    CaseSpec(
        case_id="C3", model_key="L3", round_num=7, reason="contract_violation",
        event_shape="settlement_elimination",
        description="settlementフェイズELIMINATION(step7)+FORCED_LIQUIDATION(step8)",
        real_log_analogue="P01/P07 R11 相当",
        fallback_memory="R6終了時点: 署名済み契約の義務を履行できるカードが手札に残っていない。次Rの契約義務は自動的に不履行になる見込み。",
        seed=900103,
    ),
    CaseSpec(
        case_id="C4", model_key="L4", round_num=9, reason="bankruptcy",
        event_shape="finance_forced_liquidation",
        description="financeフェイズ強制最低返済不能による単独FORCED_LIQUIDATION",
        real_log_analogue="P02 R5 相当",
        fallback_memory="R8終了時点: 現金3万、借金420万。強制最低返済額を賄えない見込み。",
        seed=900104,
    ),
    CaseSpec(
        case_id="C5", model_key="L5", round_num=12, reason="condition_not_met",
        event_shape="survival_check",
        description="R12 financeフェイズSURVIVAL_CHECK+FORCED_LIQUIDATION（最終ラウンド生存条件未達）",
        real_log_analogue="P12 R12 相当",
        fallback_memory="R11終了時点: 借金180万円が完済できておらず、現金も生還条件の300万円に届いていない。最終Rでの逆転は絶望的。",
        seed=900105,
    ),
    CaseSpec(
        case_id="C6", model_key="L6", round_num=2, reason="bankruptcy",
        event_shape="bankruptcy_commit",
        description="早期ラウンドのEntry Fee破産（Memoryほぼ空でのfinal_reflect）",
        real_log_analogue="P11 R3 相当",
        fallback_memory="",  # 意図的にほぼ空: _render_memory_block(None)分岐を通す
        seed=900106,
    ),
]

_CASE_SPECS_BY_ID: dict[str, CaseSpec] = {c.case_id: c for c in CASE_SPECS}


def select_cases(spec: str | None) -> list[CaseSpec]:
    """--cases "C1,C6" のようなカンマ区切りIDで CASE_SPECS を絞り込む。
    指定順を保持する。省略時（None）は CASE_SPECS 全件。未知IDはエラー。"""
    if not spec:
        return list(CASE_SPECS)
    ids = [s.strip() for s in spec.split(",") if s.strip()]
    unknown = [i for i in ids if i not in _CASE_SPECS_BY_ID]
    if unknown:
        raise ValueError(
            f"未知のcase_id: {unknown}（既知: {sorted(_CASE_SPECS_BY_ID)}）"
        )
    return [_CASE_SPECS_BY_ID[i] for i in ids]


# =========================================================================
# シーン構築（Bot 12体の実Gameを対象ラウンドまで進め、合成脱落を適用）
# =========================================================================

def build_bot_roster(num_players: int) -> list[str]:
    """BOT_REGISTRY（8種）をnum_players分に循環させる。API課金は一切発生しない。"""
    keys = list(BOT_REGISTRY.keys())
    reps = (num_players // len(keys)) + 1
    return (keys * reps)[:num_players]


def build_warmup_game(case: CaseSpec, num_players: int = 12) -> Game:
    """Bot 12体でGameを構築し、対象ラウンドまで通常フェイズを進める（APIコール0）。"""
    roster = build_bot_roster(num_players)
    agents: dict[str, Any] = {}
    for i, bot_name in enumerate(roster):
        pid = f"P{i + 1:02d}"
        bot_cls = BOT_REGISTRY[bot_name]
        agents[pid] = bot_cls(seed=case.seed * 100 + i)

    config = GameConfig.baseline_v1_s2(num_players=num_players).model_copy(
        update={"final_reflection_enabled": False},  # 温め中は発火させない
    )
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=case.seed, logger=logger)
    game._setup()

    for r in range(1, case.round_num + 1):
        game.current_round = r
        game._phase_market_open(r)
        game._phase_negotiation(r)
        game._phase_commit(r)
        game._phase_settlement(r)
        game._phase_finance(r)
        game._phase_reflection(r)

    return game


def apply_synthetic_elimination(game: Game, pid: str, round_num: int, reason: str, event_shape: str) -> dict[str, Any]:
    """
    実際の engine.elimination.forced_liquidation() で対象seatを脱落させ、
    5つの実event形状のいずれかを本物と同じ log() 呼出しパターンで記録する
    （engine/game.py・engine/settlement.py・engine/finance.pyの該当箇所と同一のdata構造）。

    Game state・エンジン判定を「読み取り専用で表示用に補足する」FINAL_REFLECTION自体とは違い、
    ここはその前段（対象seatを合成的に脱落させる本試験専用の準備処理）であることに注意。
    """
    p = game.players[pid]
    pre_cash, pre_debt = p.cash, p.debt_balance

    p2, contracts2, record = elim_ops.forced_liquidation(p, reason, round_num, game.contracts)
    game.players[pid] = p2
    game.contracts = contracts2

    if event_shape == "bankruptcy_commit":
        game.logger.log("BANKRUPTCY", round_num, "commit", data={
            "player_id": pid, "reason": "Cannot pay entry fee", **record,
        })
    elif event_shape == "auto_commit_failure":
        game.logger.log("AUTO_COMMIT_FAILURE", round_num, "commit", data={
            "player_id": pid,
            "reason": "No legal commits available (contradictory contracts)",
            **record,
        })
    elif event_shape == "settlement_elimination":
        game.logger.log("ELIMINATION", round_num, "settlement", step=7, data={
            "player_id": pid, "reason": "contract_violation",
        })
        game.logger.log("FORCED_LIQUIDATION", round_num, "settlement", step=8, data=record)
    elif event_shape == "finance_forced_liquidation":
        game.logger.log("FORCED_LIQUIDATION", round_num, "finance", data=record)
    elif event_shape == "survival_check":
        game.logger.log("SURVIVAL_CHECK", round_num, "finance", data={
            "player_id": pid, "result": "eliminated", "reason": "condition_not_met",
            "cash": pre_cash, "debt": pre_debt,
        })
        game.logger.log("FORCED_LIQUIDATION", round_num, "finance", data=record)
    else:
        raise ValueError(f"unknown event_shape: {event_shape}")

    return record


def select_target_pid(game: Game) -> str:
    """対象ラウンド終了時点で生存している最小番号のプレイヤーを選ぶ。"""
    alive = sorted(pid for pid, p in game.players.items() if p.is_alive)
    if not alive:
        raise RuntimeError("no alive players remain to apply synthetic elimination")
    return alive[0]


# =========================================================================
# 実R12ログからのHandover Memoryサンプル読み取り（参照専用、書き込みなし）
# =========================================================================

def harvest_real_memory_samples(base_dir: Path, n: int) -> list[str]:
    """実R12ログの各プレイヤーの最終reflectionから、実物のMemory文字列を読み取り専用で採取する。"""
    llm_dir = base_dir / "llm_logs"
    if not llm_dir.exists():
        return []
    samples: list[str] = []
    for fp in sorted(llm_dir.glob("*_llm_calls.jsonl")):
        best_text = None
        try:
            with fp.open(encoding="utf-8") as f:
                for line in f:
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    if rec.get("phase") == "reflection" and rec.get("response_text"):
                        best_text = rec["response_text"]
        except Exception:
            continue
        if best_text:
            memory, _status = extract_memory_with_status(best_text)
            if memory:
                samples.append(memory.strip())
        if len(samples) >= n:
            break
    return samples


def resolve_memory_samples(cases: list[CaseSpec]) -> list[str | None]:
    """指定されたcases分のMemoryサンプルを解決する。実ログ優先、無ければfallback。

    --cases でCASE_SPECSの部分集合（例: C1,C6のみ）が渡された場合も、
    casesの要素（case.fallback_memory）自体を見て判定するため、
    元のCASE_SPECS中の並び順・添字には依存しない。
    """
    real_samples = harvest_real_memory_samples(REAL_LOG_DIR, len(cases))
    resolved: list[str | None] = []
    for i, case in enumerate(cases):
        if case.fallback_memory == "":
            # C6は意図的にMemoryほぼ空の分岐を通す（実ログがあっても上書きしない）
            resolved.append(None)
        elif i < len(real_samples):
            resolved.append(real_samples[i])
        else:
            resolved.append(case.fallback_memory)
    return resolved


# =========================================================================
# 判定ヘルパー（15検証項目のうち自動判定できるもの）
# =========================================================================

def detect_wrapper_leak(comment: str | None) -> bool:
    """comment本文にJSON/コードフェンスのラッパーが漏れていないか（検証項目9）"""
    if not comment:
        return False
    stripped = comment.strip()
    if stripped.startswith("```"):
        return True
    if stripped.startswith("{") and '"comment"' in stripped:
        return True
    if '"comment":' in stripped or "'comment':" in stripped:
        return True
    return False


_CAUSE_KEYWORDS: dict[str, list[str]] = {
    "bankruptcy": ["破産", "資金", "現金", "返済", "借金", "支払", "Entry Fee", "エントリー"],
    "contract_violation": ["契約", "違反", "義務", "履行", "矛盾"],
    "condition_not_met": ["生存条件", "生還", "条件", "完済", "現金"],
}
_ELIMINATION_KEYWORDS = ["脱落", "負け", "敗", "終わ", "ゲームオーバー", "退場"]


def judge_cause_understanding(comment: str | None, reason: str) -> str:
    """検証項目12の自動一次判定（最終判断は人間が全6件を読んで行う）"""
    if not comment or not comment.strip():
        return "auto_fail_empty"
    kws = _CAUSE_KEYWORDS.get(reason, [])
    reason_hit = any(k in comment for k in kws)
    elim_hit = any(k in comment for k in _ELIMINATION_KEYWORDS)
    if reason_hit and elim_hit:
        return "auto_pass"
    if reason_hit or elim_hit:
        return "auto_partial"
    return "auto_flag"


# =========================================================================
# アダプタ差し替え（API-free gate用）
# =========================================================================

class RefuseToCallAdapter:
    """--dry-run用: complete()が呼ばれたら記録しつつ例外を投げる（0call証明用）"""

    def __init__(self) -> None:
        self.calls_attempted = 0
        self.last_max_tokens: int | None = None

    def complete(self, system: str, messages: list[dict[str, str]],
                 max_tokens: int = 1000, temperature: float = 0.7,
                 request_options: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
        self.calls_attempted += 1
        self.last_max_tokens = max_tokens
        raise AssertionError("RefuseToCallAdapter: API call attempted during --dry-run")


class FakeOkAdapter:
    """--mock用: 固定JSON応答を返す（API未接続）。送信パラメータを全て記録する。"""

    def __init__(self, model_info: ModelInfo, response_text: str) -> None:
        self.model_info = model_info
        self._text = response_text
        self.calls: list[dict[str, Any]] = []
        self.last_max_tokens: int | None = None
        self.last_temperature: float | None = None

    def complete(self, system: str, messages: list[dict[str, str]],
                 max_tokens: int = 1000, temperature: float = 0.7,
                 request_options: dict[str, Any] | None = None) -> tuple[str, dict[str, Any]]:
        self.calls.append({
            "system_len": len(system), "max_tokens": max_tokens, "temperature": temperature,
        })
        self.last_max_tokens = max_tokens
        self.last_temperature = temperature
        usage = {
            "input_tokens": 1200, "output_tokens": 220, "total_tokens": 1420,
            "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0,
            "reasoning_tokens": 0, "finish_reason": "stop", "usage_raw": None,
            "requested_model": self.model_info.model_id, "response_model": self.model_info.model_id,
        }
        return self._text, usage


class RecordingAdapter:
    """実API用: innerをラップし、直近complete()の実送信パラメータ（クランプ後の
    max_tokens等）を記録する。BudgetedAdapterの内側に置くことで、
    BudgetedAdapterのクランプ後・実際にプロバイダへ送られる値を捕捉できる。"""

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


def get_sent_max_tokens(adapter: Any) -> int | None:
    """adapter（あるいはそのラップ先のいずれか）からlast_max_tokensを辿って取得する。"""
    seen = adapter
    for _ in range(5):
        if seen is None:
            return None
        if getattr(seen, "last_max_tokens", None) is not None:
            return seen.last_max_tokens
        seen = getattr(seen, "_inner", None)
    return None


MOCK_RESPONSE_TEXT = (
    '{"emotion": "哀", "defeat_cause": "資金繰りの失敗", '
    '"comment": "資金管理が甘く、契約の履行順序を見誤ったのが敗因だった。'
    '次があれば早期に手元流動性を確保したい。皆の健闘を祈る。"}'
)


# =========================================================================
# 1シーン実行（LLMAgentへの差し替え + Game._phase_final_reflection() 呼出）
# =========================================================================

def prepare_agent_and_context(
    game: Game, pid: str, model_info: ModelInfo, adapter: Any,
    llm_logger: LLMLogger, memory_sample: str | None,
    game_cost_budget: GameCostBudget | None,
) -> LLMAgent:
    """LLMAgentを構築し、対象seatへ差し替える（final_reflection_enabledはここで有効化）"""
    game.config = game.config.model_copy(update={"final_reflection_enabled": True})
    agent = LLMAgent(
        player_id=pid, model_info=model_info, adapter=adapter,
        llm_logger=llm_logger, config=game.config,
    )
    agent._system_prompt = build_system_prompt(pid, game.config)
    agent._memory = memory_sample or ""
    if game_cost_budget is not None:
        agent.set_game_cost_budget(game_cost_budget)
    game.agents[pid] = agent
    return agent


def snapshot_state(game: Game, agent: LLMAgent) -> dict[str, Any]:
    return {
        "players": {pid: p.model_copy(deep=True) for pid, p in game.players.items()},
        "memory": agent._memory,
        "memory_history": copy.deepcopy(agent.memory_history),
    }


def state_unchanged(before: dict[str, Any], after: dict[str, Any]) -> bool:
    if before["memory"] != after["memory"]:
        return False
    if before["memory_history"] != after["memory_history"]:
        return False
    if set(before["players"]) != set(after["players"]):
        return False
    for pid, p_before in before["players"].items():
        if p_before.model_dump() != after["players"][pid].model_dump():
            return False
    return True


def run_one_case(
    case: CaseSpec, memory_sample: str | None, adapter_factory,
    llm_logger: LLMLogger, game_cost_budget_factory,
) -> dict[str, Any]:
    """
    1ケースを最初から最後まで実行する（Bot warmup → 合成脱落 → LLMAgent差し替え →
    Game._phase_final_reflection() 実行 → 二重発火チェック → 非破壊チェック）。

    adapter_factory(model_info) -> adapter インスタンス（dry-run/mock/実APIで差し替え）
    game_cost_budget_factory(event_logger) -> GameCostBudget | None
    """
    record: dict[str, Any] = {
        "case_id": case.case_id, "model_key": case.model_key,
        "round": case.round_num, "reason": case.reason,
        "event_shape": case.event_shape, "description": case.description,
    }

    game = build_warmup_game(case)
    target_pid = select_target_pid(game)
    record["player_id"] = target_pid

    liquidation_record = apply_synthetic_elimination(
        game, target_pid, case.round_num, case.reason, case.event_shape,
    )
    record["liquidation"] = liquidation_record

    model_info = get_model(case.model_key)
    record["model_id"] = model_info.model_id
    record["provider"] = model_info.provider
    record["base_url"] = model_info.base_url

    adapter = adapter_factory(model_info)
    budget = game_cost_budget_factory(game.logger)

    agent = prepare_agent_and_context(
        game, target_pid, model_info, adapter, llm_logger, memory_sample, budget,
    )

    ctx = game._build_elimination_context(target_pid, case.round_num)
    record["reason_label"] = ctx["reason_label"]
    record["elimination_event_type"] = ctx["event_type"]
    record["elimination_phase"] = ctx["phase"]

    before = snapshot_state(game, agent)
    start = time.time()
    game._phase_final_reflection(case.round_num)
    latency_ms = round((time.time() - start) * 1000, 1)
    after = snapshot_state(game, agent)

    record["latency_ms"] = latency_ms
    record["memory_unchanged"] = (before["memory"] == after["memory"]
                                   and before["memory_history"] == after["memory_history"])
    record["state_unchanged"] = state_unchanged(before, after)

    # 検証項目15前半: 想定通りcallは1回だけ発生したか
    calls_after_first = getattr(adapter, "calls_made", None)
    if calls_after_first is None:
        calls_after_first = len(getattr(adapter, "calls", []))
    record["calls_made"] = calls_after_first

    # 検証項目15後半: 同一プレイヤーへの二重発火が起きないか
    # （_final_reflection_done による dedup。もう一度同じフェイズを呼んでも
    #   final_reflect() が再度呼ばれない = adapter呼出し回数が増えない）
    game._phase_final_reflection(case.round_num)
    calls_after_second = getattr(adapter, "calls_made", None)
    if calls_after_second is None:
        calls_after_second = len(getattr(adapter, "calls", []))
    record["second_call_fired"] = calls_after_second > calls_after_first

    reflection = agent.final_reflection or {}
    record["parse_status"] = reflection.get("status")
    record["emotion"] = reflection.get("emotion")
    record["defeat_cause"] = reflection.get("defeat_cause")
    record["comment"] = reflection.get("comment", "")
    record["comment_chars"] = reflection.get("chars", 0)
    record["truncated"] = reflection.get("truncated", False)
    record["salvaged"] = reflection.get("salvaged", [])
    record["ok"] = reflection.get("status") not in (None, "empty", "budget_blocked", "error")
    record["wrapper_leak"] = detect_wrapper_leak(record["comment"])
    record["fallback_used"] = record["parse_status"] not in ("ok",)
    record["cause_understanding"] = judge_cause_understanding(record["comment"], case.reason)
    record["emotion_valid"] = record["emotion"] in VALID_EMOTIONS if record["emotion"] else False
    record["length_ok"] = 20 <= record["comment_chars"] <= 2000 if record["comment"] else False

    # LLMログの最新エントリからusage/コストを取り出す（実API/mock共通）
    entries = llm_logger.entries
    last_entry = entries[-1] if entries else {}
    record["sent_max_tokens"] = get_sent_max_tokens(adapter)
    record["input_tokens"] = last_entry.get("input_tokens")
    record["output_tokens"] = last_entry.get("output_tokens")
    record["cost_usd"] = last_entry.get("cost_usd")
    record["budget_blocked"] = bool(last_entry.get("budget_blocked", False))
    record["api_called"] = last_entry.get("api_called")
    record["finish_reason"] = last_entry.get("finish_reason")
    # 検証項目5: provider側max_tokens打ち切りが発生していないか（特にL1で重要）
    record["truncated_by_provider"] = record["finish_reason"] == "max_tokens"

    return record


# =========================================================================
# G4: parse_final_reflection() セルフテスト（Game/API不要）
# =========================================================================

def run_parser_selftest() -> bool:
    cases = [
        ("clean json", '{"emotion": "哀", "defeat_cause": "資金不足", "comment": "負けた"}', "ok"),
        ("fenced json", '```json\n{"emotion": "怒", "defeat_cause": "裏切り", "comment": "P05に裏切られた"}\n```', "ok"),
        ("no comment key", '{"emotion": "哀", "defeat_cause": "破産", "final_word": "無念だ"}', "ok_assembled"),
        ("plain text", 'ただの散文コメントです。JSONではありません。', "ok_plaintext"),
        ("empty", '', "empty"),
        # C6実測: finish_reason=stop・raw改行がcomment内にありjson.loads失敗
        (
            "C6-shape raw newline in comment",
            '```json\n{"emotion": "哀", "defeat_cause": "初動の借入額設定ミス", '
            '"comment": "悔しい\n最後まで頑張った"}\n```',
            "ok_recovered",
        ),
        # C1実測: finish_reason=max_tokensでcomment本文の途中で物理的に切断（閉じ引用符/波括弧なし）
        (
            "C1-shape truncated mid-comment",
            '```json\n{"emotion": "怒", "defeat_cause": "初期借入額の過小判断", '
            '"comment": "本当に悔しい。もっと慎重に判断すべきだった。特に序盤の',
            "ok_recovered",
        ),
    ]
    all_ok = True
    print("=== G4: parse_final_reflection() セルフテスト ===")
    for label, text, expected in cases:
        result = parse_final_reflection(text, max_chars=2000)
        status = result["status"]
        ok = status == expected
        all_ok = all_ok and ok
        print(f"  [{'OK' if ok else 'NG'}] {label}: status={status} (expected={expected})")

    # C1/C6形の3キー救済確認（emotion/defeat_cause/commentが全て失われないこと）
    for label, text in [
        ("C6-shape 3-key salvage", cases[5][1]),
        ("C1-shape 3-key salvage", cases[6][1]),
    ]:
        result = parse_final_reflection(text, max_chars=2000)
        keys_ok = (
            result["emotion"] in VALID_EMOTIONS
            and bool(result["defeat_cause"])
            and bool(result["comment"])
            and "comment" not in result["comment"]
            and "{" not in result["comment"]
        )
        all_ok = all_ok and keys_ok
        print(f"  [{'OK' if keys_ok else 'NG'}] {label}: emotion={result['emotion']} "
              f"defeat_cause={result['defeat_cause']!r} comment={result['comment'][:30]!r}...")

    # 切り詰め確認
    long_text = json.dumps({"emotion": "哀", "defeat_cause": "長文", "comment": "あ" * 3000}, ensure_ascii=False)
    result = parse_final_reflection(long_text, max_chars=2000)
    trunc_ok = result["truncated"] is True and result["chars"] <= 2000
    all_ok = all_ok and trunc_ok
    print(f"  [{'OK' if trunc_ok else 'NG'}] overflow truncation: truncated={result['truncated']} chars={result['chars']}")

    # 無効emotionの正規化確認
    bad_emotion = parse_final_reflection('{"emotion": "喜び", "comment": "テスト"}', max_chars=2000)
    emo_ok = bad_emotion["emotion"] is None
    all_ok = all_ok and emo_ok
    print(f"  [{'OK' if emo_ok else 'NG'}] invalid emotion normalized to None: emotion={bad_emotion['emotion']}")

    print(f"=== G4結果: {'PASS' if all_ok else 'FAIL'} ===")
    return all_ok


# =========================================================================
# G3: --dry-run（0 API call を証明しつつ、6プロンプトをレンダリングして出力）
# =========================================================================

def run_dry_run(out_dir: Path, cases: list[CaseSpec] | None = None) -> bool:
    """
    G3: シーン構築・脱落コンテキスト導出・プロンプトレンダリングまでを実経路で行うが、
    Game._phase_final_reflection() / LLMAgent.final_reflect() は一切呼び出さない
    （= adapter.complete() へ到達する経路自体を経由しない）。

    RefuseToCallAdapter は「万一どこかの経路が誤って呼び出してしまった場合の安全網」
    として各シーンへ注入し、最後に calls_attempted == 0 であることを確認する
    （tests/test_final_reflection.py と同じ idiom）。

    cases省略時はCASE_SPECS全件（既存挙動と完全互換）。
    """
    cases = cases if cases is not None else CASE_SPECS
    print(f"=== G3: --dry-run（0 API call の証明 + プロンプトレンダリング, cases={[c.case_id for c in cases]}） ===")
    memory_samples = resolve_memory_samples(cases)
    dummy_llm_logger = LLMLogger(output_dir=out_dir, game_id="dryrun")
    prompts_md: list[str] = ["# FINAL_REFLECTION smoke: dry-run rendered prompts\n"]
    total_worst = 0.0
    all_ok = True

    try:
        for case, memory_sample in zip(cases, memory_samples):
            model_info = get_model(case.model_key)
            adapter = RefuseToCallAdapter()

            game = build_warmup_game(case)
            target_pid = select_target_pid(game)
            apply_synthetic_elimination(game, target_pid, case.round_num, case.reason, case.event_shape)

            # LLMAgentを実際に構築・差し替えるところまでは実経路を通す（配線確認）。
            # ただし final_reflect() / _phase_final_reflection() は呼ばない。
            prepare_agent_and_context(
                game, target_pid, model_info, adapter, dummy_llm_logger,
                memory_sample, game_cost_budget=None,
            )

            ctx = game._build_elimination_context(target_pid, case.round_num)
            vs = game._build_visible_state(case.round_num, for_player_id=target_pid)
            sysp = build_system_prompt(target_pid, game.config)
            userp = build_final_reflection_prompt(
                game.players[target_pid], case.round_num, vs, game.config,
                ctx, memory=memory_sample,
            )

            zero_calls = adapter.calls_attempted == 0
            all_ok = all_ok and zero_calls
            print(f"  [{case.case_id}] model={case.model_key}({model_info.model_id}) "
                  f"calls_attempted={adapter.calls_attempted} {'OK' if zero_calls else 'NG(呼ばれた)'}")

            worst = worst_case_cost(model_info, sysp, userp, FINAL_REFLECTION_MAX_TOKENS)
            total_worst += worst
            prompts_md.append(
                f"\n## {case.case_id}: {case.model_key} ({model_info.model_id}) "
                f"— {case.description}\n"
                f"- player_id: {target_pid} / round: {case.round_num} / reason: {case.reason}\n"
                f"- worst_case_cost(max_tokens={FINAL_REFLECTION_MAX_TOKENS}): ${worst:.5f}\n"
                f"- system prompt chars: {len(sysp)}\n\n```text\n{userp}\n```\n"
            )
    finally:
        dummy_llm_logger.close()

    (out_dir / "dry_run_prompts.md").write_text("".join(prompts_md), encoding="utf-8")
    print(f"\n合計worst_case_cost見積 = ${total_worst:.5f}")
    print(f"出力: {out_dir / 'dry_run_prompts.md'}")
    print(f"=== G3結果: {'PASS' if all_ok else 'FAIL'} ===")
    return all_ok


# =========================================================================
# G5/G6: --mock（FakeOkAdapterで6シーン全通し + ルーティング/非破壊/二重発火チェック）
# =========================================================================

def run_mock(out_dir: Path, cases: list[CaseSpec] | None = None) -> bool:
    cases = cases if cases is not None else CASE_SPECS
    print(f"=== G5/G6: --mock（ルーティング・max_tokens・非破壊・二重発火の検証, cases={[c.case_id for c in cases]}） ===")
    memory_samples = resolve_memory_samples(cases)
    llm_logger = LLMLogger(output_dir=out_dir, game_id="mock")
    all_ok = True
    records: list[dict[str, Any]] = []

    try:
        for case, memory_sample in zip(cases, memory_samples):
            expected_model = get_model(case.model_key)

            def factory(m, _text=MOCK_RESPONSE_TEXT):
                return FakeOkAdapter(m, _text)

            rec = run_one_case(
                case, memory_sample, factory, llm_logger,
                game_cost_budget_factory=lambda _l: GameCostBudget(
                    per_player_cap_usd=1.0, game_cap_usd=1.0, event_logger=_l,
                ),
            )
            records.append(rec)

            checks = {
                "routing": rec["model_id"] == expected_model.model_id,
                f"max_tokens={FINAL_REFLECTION_MAX_TOKENS}": rec["sent_max_tokens"] == FINAL_REFLECTION_MAX_TOKENS,
                "1 call only": rec["calls_made"] == 1,
                "no second call": not rec["second_call_fired"],
                "parse ok": rec["parse_status"] == "ok",
                "emotion valid": rec["emotion_valid"],
                "state_unchanged": rec["state_unchanged"],
                "memory_unchanged": rec["memory_unchanged"],
                "no wrapper leak": not rec["wrapper_leak"],
            }
            ok = all(checks.values())
            all_ok = all_ok and ok
            print(f"  [{'OK' if ok else 'NG'}] {case.case_id} {case.model_key}({rec['model_id']}): "
                  f"{ {k: v for k, v in checks.items() if not v} if not ok else 'all checks passed'}")
    finally:
        llm_logger.save()
        llm_logger.close()

    print(f"=== G5/G6結果: {'PASS' if all_ok else 'FAIL'} ===")
    return all_ok


# =========================================================================
# 実API実行
# =========================================================================

def run_real(args: argparse.Namespace, out_dir: Path, cases: list[CaseSpec] | None = None) -> int:
    cases = cases if cases is not None else CASE_SPECS
    print(f"=== 実APIプリフライト（G3→G4→G5/G6を自動実行、1つでも失敗したら実APIには進まない）cases={[c.case_id for c in cases]} ===")
    preflight_dir = out_dir / "_preflight"
    preflight_dir.mkdir(parents=True, exist_ok=True)
    if not run_parser_selftest():
        print("\n中断: G4（パーサーセルフテスト）に失敗しました。実APIは呼びません。")
        return 1
    if not run_dry_run(preflight_dir / "dry_run", cases):
        print("\n中断: G3（dry-run 0call証明）に失敗しました。実APIは呼びません。")
        return 1
    if not run_mock(preflight_dir / "mock", cases):
        print("\n中断: G5/G6（mock全シーン検証）に失敗しました。実APIは呼びません。")
        return 1
    print("=== プリフライト全通過。実APIコールへ進みます ===\n")

    # --- コスト事前計算（実際にAPIを呼ぶ前に必ず表示・上限チェック） ---
    memory_samples = resolve_memory_samples(cases)
    plan_total = 0.0
    plan_rows = []
    for case, memory_sample in zip(cases, memory_samples):
        model_info = get_model(case.model_key)
        game = build_warmup_game(case)
        target_pid = select_target_pid(game)
        apply_synthetic_elimination(game, target_pid, case.round_num, case.reason, case.event_shape)
        ctx = game._build_elimination_context(target_pid, case.round_num)
        vs = game._build_visible_state(case.round_num, for_player_id=target_pid)
        cfg = game.config.model_copy(update={"final_reflection_enabled": True})
        sysp = build_system_prompt(target_pid, cfg)
        userp = build_final_reflection_prompt(game.players[target_pid], case.round_num, vs, cfg, ctx, memory=memory_sample)
        worst = worst_case_cost(model_info, sysp, userp, FINAL_REFLECTION_MAX_TOKENS)
        plan_total += worst
        plan_rows.append((case.case_id, case.model_key, model_info.model_id, worst))
        print(f"  [計画] {case.case_id} {case.model_key} ({model_info.model_id}): "
              f"worst_case=${worst:.5f} (cap/call=${args.max_cost_per_call:.4f})")
        if worst > args.max_cost_per_call:
            print(f"\n中断: {case.case_id} の worst_case見積 ${worst:.5f} が --max-cost-per-call "
                  f"${args.max_cost_per_call:.4f} を超えます。実APIは呼びません。")
            return 1
    print(f"  [計画] 合計worst_case見積 = ${plan_total:.5f} (上限 ${args.max_cost:.4f})")
    if plan_total > args.max_cost:
        print(f"\n中断: 合計worst_case見積 ${plan_total:.5f} が --max-cost ${args.max_cost:.4f} を超えます。"
              "実APIは呼びません。")
        return 1

    if len(cases) > args.max_calls or len(cases) > HARD_MAX_CALLS:
        print(f"\n中断: ケース数({len(cases)})が --max-calls({args.max_calls}) "
              f"またはハード上限({HARD_MAX_CALLS})を超えます。")
        return 1

    llm_logger = LLMLogger(output_dir=out_dir, game_id="real")
    manifest = {
        "run_id": args.run_id,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "cases": [c.case_id for c in cases],
        "models": {c.case_id: get_model(c.model_key).model_id for c in cases},
        "final_reflection_max_tokens": FINAL_REFLECTION_MAX_TOKENS,
        "max_calls": args.max_calls, "max_cost": args.max_cost,
        "max_cost_per_call": args.max_cost_per_call, "max_retries": args.max_retries,
        "plan_total_worst_case_usd": plan_total,
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    records: list[dict[str, Any]] = []
    consecutive_failures = 0
    calls_made = 0
    jsonl_path = out_dir / "calls.jsonl"

    try:
        with jsonl_path.open("w", encoding="utf-8") as f:
            for case, memory_sample in zip(cases, memory_samples):
                if calls_made >= args.max_calls or calls_made >= HARD_MAX_CALLS:
                    print(f"中断: コール上限に到達したため {case.case_id} 以降は呼びません。")
                    break

                model_info = get_model(case.model_key)
                print(f"\n[{case.case_id}] {case.model_key} ({model_info.model_id}) 実行中...")

                def factory(m):
                    raw = create_adapter(m, max_retries=args.max_retries)
                    recording = RecordingAdapter(raw)
                    return BudgetedAdapter(
                        recording, m, max_calls=1, max_cost=args.max_cost_per_call,
                        max_tokens_cap=FINAL_REFLECTION_MAX_TOKENS,
                    )

                def budget_factory(event_logger, _cap=args.max_cost_per_call):
                    return GameCostBudget(
                        per_player_cap_usd=_cap, game_cap_usd=_cap, event_logger=event_logger,
                        abort_on_block=False,
                    )

                try:
                    rec = run_one_case(case, memory_sample, factory, llm_logger, budget_factory)
                    calls_made += rec.get("calls_made", 0) or 0
                    records.append(rec)
                    f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
                    f.flush()

                    if rec["ok"]:
                        consecutive_failures = 0
                        print(f"  OK status={rec['parse_status']} emotion={rec['emotion']} "
                              f"defeat_cause={rec['defeat_cause']} chars={rec['comment_chars']} "
                              f"latency={rec['latency_ms']}ms cost=${rec.get('cost_usd')}")
                    else:
                        consecutive_failures += 1
                        print(f"  NG status={rec['parse_status']} budget_blocked={rec['budget_blocked']}")
                except Exception as e:
                    consecutive_failures += 1
                    err_rec = {
                        "case_id": case.case_id, "model_key": case.model_key,
                        "ok": False, "error": f"{type(e).__name__}: {str(e)[:300]}",
                    }
                    records.append(err_rec)
                    f.write(json.dumps(err_rec, ensure_ascii=False) + "\n")
                    f.flush()
                    print(f"  NG 例外発生: {type(e).__name__}: {str(e)[:300]}")

                if consecutive_failures >= args.abort_after_consecutive_failures:
                    print(f"\n中断: {consecutive_failures}連続の予期しない失敗のため残りのケースは呼びません。")
                    break
    finally:
        llm_logger.save()
        llm_logger.close()

    report_path = out_dir / "report.md"
    write_report(records, plan_total, out_dir, report_path)
    print(f"\n出力: {jsonl_path}")
    print(f"出力: {report_path}")

    return 0 if all(r.get("ok") for r in records) and len(records) == len(cases) else 1


def write_report(records: list[dict[str, Any]], plan_total: float, out_dir: Path, path: Path) -> None:
    lines = [
        "# FINAL_REFLECTION smoke test — call records",
        "",
        f"- output_dir: `{out_dir}`",
        f"- 計画worst_case合計: ${plan_total:.5f}",
        f"- 実測コスト合計: ${sum(r.get('cost_usd') or 0.0 for r in records):.6f}",
        "",
        "| case | model_key | model_id | reason | ok | parse_status | salvaged | finish_reason | truncated_by_provider | in_tok | out_tok | cost | latency_ms | emotion | defeat_cause | fallback | wrapper_leak | cause_understanding | state_unchanged | memory_unchanged | second_call |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        lines.append(
            f"| {r.get('case_id')} | {r.get('model_key')} | {r.get('model_id')} | {r.get('reason')} | "
            f"{r.get('ok')} | {r.get('parse_status')} | {r.get('salvaged')} | {r.get('finish_reason')} | "
            f"{r.get('truncated_by_provider')} | {r.get('input_tokens')} | {r.get('output_tokens')} | "
            f"{r.get('cost_usd')} | {r.get('latency_ms')} | {r.get('emotion')} | {r.get('defeat_cause')} | "
            f"{r.get('fallback_used')} | {r.get('wrapper_leak')} | {r.get('cause_understanding')} | "
            f"{r.get('state_unchanged')} | {r.get('memory_unchanged')} | {r.get('second_call_fired')} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


# =========================================================================
# main
# =========================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FINAL_REFLECTION 6モデル×1コール実API疎通テスト")
    parser.add_argument("--dry-run", action="store_true", help="0 API callでプロンプト生成のみ確認（G3）")
    parser.add_argument("--parser-selftest", action="store_true", help="parse_final_reflection()セルフテスト（G4）")
    parser.add_argument("--mock", action="store_true", help="FakeOkAdapterで6シーン全通し確認（G5/G6）")
    parser.add_argument("--cases", default=None,
                         help="カンマ区切りのcase_idで対象を絞り込む（例: C1,C6）。省略時はCASE_SPECS全件（6件）")
    parser.add_argument("--run-id", default=None, help="実API実行時の一意ID（既存ディレクトリなら中断=二重送信防止）")
    parser.add_argument("--out", default="logs/smoke", help="出力先ベースディレクトリ")
    parser.add_argument("--max-calls", type=int, default=6, help=f"総コール数上限（デフォルト6、絶対上限{HARD_MAX_CALLS}）")
    parser.add_argument("--max-cost", type=float, default=0.08,
                         help="累計worst_case見積上限USD（3000tok化後、2ケース(L1/L6)再試験では0.05推奨）")
    parser.add_argument("--max-cost-per-call", type=float, default=0.02,
                         help="1callあたりworst_case見積上限USD（3000tok化後、L1のworst_case見積は"
                              "$0.01895前後になるため、実行時は0.03以上を推奨）")
    parser.add_argument("--max-retries", type=int, default=None, help="adapter内部リトライ回数を上書き（省略時は本番既定）")
    parser.add_argument("--abort-after-consecutive-failures", type=int, default=2,
                         help="この回数連続で予期しない失敗が起きたら残りを中断")
    args = parser.parse_args(argv)

    if args.max_calls > HARD_MAX_CALLS:
        print(f"エラー: --max-calls={args.max_calls} はハード上限{HARD_MAX_CALLS}を超えています。")
        return 1

    try:
        cases = select_cases(args.cases)
    except ValueError as e:
        print(f"エラー: {e}")
        return 1

    modes = [args.dry_run, args.parser_selftest, args.mock]
    if sum(bool(m) for m in modes) > 1:
        print("エラー: --dry-run / --parser-selftest / --mock は同時に指定できません。")
        return 1

    if args.parser_selftest:
        return 0 if run_parser_selftest() else 1

    if args.dry_run:
        out_dir = Path(args.out) / "final_reflection_dry_run"
        out_dir.mkdir(parents=True, exist_ok=True)
        return 0 if run_dry_run(out_dir, cases) else 1

    if args.mock:
        out_dir = Path(args.out) / "final_reflection_mock"
        out_dir.mkdir(parents=True, exist_ok=True)
        return 0 if run_mock(out_dir, cases) else 1

    # --- 実API実行 ---
    run_id = args.run_id or datetime.now().strftime("fr_smoke_%Y%m%d_%H%M%S")
    args.run_id = run_id
    out_dir = Path(args.out) / f"final_reflection_{run_id}"
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"エラー: 出力ディレクトリ {out_dir} は既に存在します（二重送信防止のため中断）。"
              "別の --run-id を指定してください。")
        return 1

    return run_real(args, out_dir, cases)


if __name__ == "__main__":
    sys.exit(main())
