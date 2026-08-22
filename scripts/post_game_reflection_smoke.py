"""
POST_GAME_REFLECTION 小規模疎通テスト（実API最小コスト・6モデル×1コール）

次のL12×R12本番でPOST_GAME_REFLECTION（ゲーム完全終了後の全員答え合わせ）が
全モデルで問題なく動くかを、フルゲームを複数回回さずに最小コストで確認する。

設計（scripts/final_reflection_smoke.py と対をなす。基本骨格は同一だが、
FINAL_REFLECTIONと違い対象は「脱落者」限定ではなく「生還者＋脱落者の全員」で
あり、`Game._build_post_game_context()` が神視点情報（DM本文・匿名通信の
真の掲載者・非当事者契約条項・破られた約束）を積極的にpromptへ混ぜる点が
最大の違いである）:
- 現行L12ロスターのユニーク6モデル（L1〜L6、get_model()で解決。ハードコードしない）を
  各1回だけ実APIへ呼ぶ（合計6call）。
- 各モデルに、Bot 12体だけの実Gameインスタンスを**最後まで**（全12ラウンド＋
  `_finalize()`）自然に進めさせ（合成脱落は使わない — POST_GAME_REFLECTIONは
  ラウンド単位の脱落イベント形状に依存しないため）、その自然な結果
  （生還/脱落）からケースごとに希望する属性（survived True/False）に合致する
  最初のプレイヤーを対象seatに選ぶ。対象seatだけLLMAgentへ差し替え、
  `Game._phase_post_game_reflection(result)` を実際に呼ぶ
  （`_build_god_shared_block()` / `_build_post_game_context()` /
  `build_post_game_reflection_prompt()` / `post_game_reflect()` /
  `parse_post_game_reflection()` / ログ後付けを全て実経路で通す）。
- 実R12ログ（logs/llm/trial_C_l12_r12_20260822/）はHandover Memoryサンプルの
  読み取り参照専用（書き込み一切なし。final_reflection_smoke.pyと同じ流用元）。

API-free gate（必須、実API呼出しの前に自動実行）:
    --dry-run          : 6シーンを構築し、RefuseToCallAdapterで0callを証明。
                          rendered promptをdry_run_prompts.mdへ出力。
    --parser-selftest  : parse_post_game_reflection() のsalvage ladder・
                          roster照合を検証。
    --mock             : FakeOkAdapterで6シーン全通しし、ルーティング/
                          sent_max_tokens==POST_GAME_REFLECTION_MAX_TOKENS
                          （GameConfig由来）/ Memory・state非破壊/
                          二重発火なしを検証。
実API実行時は、上記3ゲート相当を必ず内部で自動実行し、1つでも失敗したら
実APIには一切進まない（AdapterErrorやHTTP接続を一切発生させない）。

安全策:
- HARD_MAX_CALLS=6（絶対上限）、--max-cost-per-call・--max-cost で二重の予算天井。
- BudgetedAdapter（scripts/model_matrix.py、変更なしで再利用）で1モデル1callに強制。
- --run-id ディレクトリを exist_ok=False で作成 → 二重送信を機械的に阻止。
- 2連続の予期しない失敗で残りを中断（個別失敗は継続、システム的な障害は停止）。
- 出力は logs/smoke/post_game_reflection_{run_id}/ 専用ディレクトリのみ
  （logs/llm/trial_* には一切書き込まない）。

使用例:
    uv run python scripts/post_game_reflection_smoke.py --dry-run
    uv run python scripts/post_game_reflection_smoke.py --parser-selftest
    uv run python scripts/post_game_reflection_smoke.py --mock
    uv run python scripts/post_game_reflection_smoke.py --run-id pg_smoke_20260822 \\
        --max-calls 6 --max-cost 0.08

    # --cases でCASE_SPECSの部分集合だけを対象にできる。
    uv run python scripts/post_game_reflection_smoke.py --dry-run --cases C1,C6
    uv run python scripts/post_game_reflection_smoke.py --mock --cases C1,C6
    uv run python scripts/post_game_reflection_smoke.py --run-id pg_smoke2_20260822 --cases C1,C6 \\
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
from engine.game import Game, GameResult
from bots import BOT_REGISTRY

from llm.models import get_model, ModelInfo
from llm.costing import worst_case_cost
from llm.adapters import create_adapter, AdapterError
from llm.prompt_builder import build_system_prompt, build_post_game_reflection_prompt
from llm.response_parser import (
    parse_post_game_reflection, extract_memory_with_status, VALID_EMOTIONS,
)
from llm.llm_agent import LLMAgent
from llm.llm_logger import LLMLogger
from llm.game_cost_budget import GameCostBudget

from scripts.model_matrix import BudgetedAdapter

HARD_MAX_CALLS = 6  # このスクリプトが1回の実行で許容する絶対上限（安全弁）

REAL_LOG_DIR = Path("logs/llm/trial_C_l12_r12_20260822")  # 読み取り参照専用

POST_GAME_REFLECTION_MAX_TOKENS = GameConfig.baseline_v1_s2(num_players=12).post_game_reflection_max_tokens
POST_GAME_REFLECTION_MAX_CHARS = GameConfig.baseline_v1_s2(num_players=12).post_game_reflection_max_chars
# ↑ ハードコードせず GameConfig から導出する。engine/config.py の値を
#   変更すれば、このスクリプトの見積・mockアサーション・
#   BudgetedAdapter(max_tokens_cap=...) も自動追従する。


# =========================================================================
# 6ケース定義（モデルは get_model() で解決。model_idのハードコードはしない）
# =========================================================================

@dataclass(frozen=True)
class CaseSpec:
    case_id: str
    model_key: str
    want_survived: bool
    description: str
    fallback_memory: str
    seed: int


CASE_SPECS: list[CaseSpec] = [
    CaseSpec(
        case_id="C1", model_key="L1", want_survived=True,
        description="生還者の答え合わせ（全12R完走）",
        fallback_memory="R11終了時点: 現金380万、借金0。安全圏で逃げ切りを図っていた。",
        seed=910101,
    ),
    CaseSpec(
        case_id="C2", model_key="L2", want_survived=False,
        description="脱落者の答え合わせ（Bot自然対局での早期脱落）",
        fallback_memory="R3終了時点: 現金8万、借金310万。資金繰りが厳しかった。",
        seed=910102,
    ),
    CaseSpec(
        case_id="C3", model_key="L3", want_survived=True,
        description="生還者の答え合わせ（Memoryほぼ空でのpost_game_reflect）",
        fallback_memory="",  # 意図的にほぼ空: _render_memory_block(None)分岐を通す
        seed=910103,
    ),
    CaseSpec(
        case_id="C4", model_key="L4", want_survived=False,
        description="脱落者の答え合わせ（中盤脱落、契約多め想定）",
        fallback_memory="R6終了時点: 複数プレイヤーと契約を締結していたが、履行できず信用を落とした。",
        seed=910104,
    ),
    CaseSpec(
        case_id="C5", model_key="L5", want_survived=True,
        description="生還者の答え合わせ（僅差での生還想定）",
        fallback_memory="R11終了時点: 現金310万、借金180万。ギリギリの資金繰りが続いていた。",
        seed=910105,
    ),
    CaseSpec(
        case_id="C6", model_key="L6", want_survived=False,
        description="脱落者の答え合わせ（終盤脱落想定）",
        fallback_memory="R9終了時点: 借金の返済計画が崩れ、資金繰りが急速に悪化していた。",
        seed=910106,
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
# シーン構築（Bot 12体の実Gameを最後まで自然進行させる）
# =========================================================================

def build_bot_roster(num_players: int) -> list[str]:
    """BOT_REGISTRY（8種）をnum_players分に循環させる。API課金は一切発生しない。"""
    keys = list(BOT_REGISTRY.keys())
    reps = (num_players // len(keys)) + 1
    return (keys * reps)[:num_players]


def build_full_bot_game(seed: int, num_players: int = 12) -> tuple[Game, GameResult]:
    """
    Bot roster でフルゲームを最後まで実行する（APIコール0）。

    post_game_reflection_enabledは既定Falseのまま run() を通す。
    run()末尾の _phase_post_game_reflection() は config無効のため即returnし、
    _post_game_reflection_done は汚染されない（後段でtarget差し替え後に
    改めて有効化して同フェイズを呼び出せる）。
    """
    roster = build_bot_roster(num_players)
    agents: dict[str, Any] = {}
    for i, bot_name in enumerate(roster):
        pid = f"P{i + 1:02d}"
        bot_cls = BOT_REGISTRY[bot_name]
        agents[pid] = bot_cls(seed=seed * 100 + i)

    config = GameConfig.baseline_v1_s2(num_players=num_players)
    logger = EventLogger()
    game = Game(config=config, agents=agents, seed=seed, logger=logger)
    result = game.run()
    return game, result


def select_target_pid(game: Game, result: GameResult, want_survived: bool) -> str:
    """自然対局の結果から、希望する属性（生還/脱落）に合致する最初のプレイヤーを選ぶ。
    希望する属性の対象が存在しなければ、存在する方へフォールバックする
    （seedの巡り合わせで全員生還/全員脱落になった場合の安全弁）。"""
    survivors = sorted(p.player_id for p in result.survivors)
    eliminated = sorted(p.player_id for p in result.eliminated)
    pool = survivors if want_survived else eliminated
    if not pool:
        pool = survivors or eliminated
    if not pool:
        raise RuntimeError("no players in result at all（both survivors and eliminated are empty）")
    return pool[0]


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

    --cases でCASE_SPECSの部分集合が渡された場合も、casesの要素
    （case.fallback_memory）自体を見て判定するため、元のCASE_SPECS中の
    並び順・添字には依存しない。
    """
    real_samples = harvest_real_memory_samples(REAL_LOG_DIR, len(cases))
    resolved: list[str | None] = []
    for i, case in enumerate(cases):
        if case.fallback_memory == "":
            # C3は意図的にMemoryほぼ空の分岐を通す（実ログがあっても上書きしない）
            resolved.append(None)
        elif i < len(real_samples):
            resolved.append(real_samples[i])
        else:
            resolved.append(case.fallback_memory)
    return resolved


# =========================================================================
# 判定ヘルパー（自動判定できる検証項目）
# =========================================================================

def detect_wrapper_leak(comment: str | None) -> bool:
    """comment本文にJSON/コードフェンスのラッパーが漏れていないか"""
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


def judge_revelation_reference(comment: str | None, revelations: list[str]) -> str:
    """自由記述が神視点開示（revelations）を踏まえた内容になっているかの一次判定
    （最終判断は人間が全6件を読んで行う）。開示自体が0件のケースでは判定不能とする。"""
    if not revelations:
        return "no_revelations_to_reference"
    if not comment or not comment.strip():
        return "auto_fail_empty"
    return "needs_human_review"


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
            "input_tokens": 1500, "output_tokens": 260, "total_tokens": 1760,
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
    '{"emotion": "楽", "self_assessment": "堅実に立ち回れたと思う。", '
    '"biggest_revelation": "終盤の匿名通信の真の掲載者が意外な人物だった。", '
    '"changed_opinion": "序盤に信頼していた相手への評価が変わった。", '
    '"best_player": "", "most_deceptive_player": "", '
    '"comment": "全体を通して資金管理を優先した立ち回りが功を奏した。'
    '答え合わせを見て、いくつかの読み違いに気づいたが悔いはない。皆の健闘を称えたい。"}'
)


# =========================================================================
# 1シーン実行（LLMAgentへの差し替え + Game._phase_post_game_reflection() 呼出）
# =========================================================================

def prepare_agent_and_context(
    game: Game, pid: str, model_info: ModelInfo, adapter: Any,
    llm_logger: LLMLogger, memory_sample: str | None,
    game_cost_budget: GameCostBudget | None,
) -> LLMAgent:
    """LLMAgentを構築し、対象seatへ差し替える（post_game_reflection_enabledはここで有効化）"""
    game.config = game.config.model_copy(update={"post_game_reflection_enabled": True})
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
    1ケースを最初から最後まで実行する（Bot対局を最後まで進める → 対象seat選定 →
    LLMAgent差し替え → Game._phase_post_game_reflection() 実行 → 二重発火チェック →
    非破壊チェック）。

    adapter_factory(model_info) -> adapter インスタンス（dry-run/mock/実APIで差し替え）
    game_cost_budget_factory(event_logger) -> GameCostBudget | None
    """
    record: dict[str, Any] = {
        "case_id": case.case_id, "model_key": case.model_key,
        "want_survived": case.want_survived, "description": case.description,
    }

    game, result = build_full_bot_game(case.seed)
    target_pid = select_target_pid(game, result, case.want_survived)
    record["player_id"] = target_pid
    survived_ids = {p.player_id for p in result.survivors}
    record["actual_survived"] = target_pid in survived_ids
    record["want_matched_actual"] = record["actual_survived"] == case.want_survived
    record["round_count"] = result.round_count

    model_info = get_model(case.model_key)
    record["model_id"] = model_info.model_id
    record["provider"] = model_info.provider
    record["base_url"] = model_info.base_url

    adapter = adapter_factory(model_info)
    budget = game_cost_budget_factory(game.logger)

    agent = prepare_agent_and_context(
        game, target_pid, model_info, adapter, llm_logger, memory_sample, budget,
    )

    shared = game._build_god_shared_block(result)
    ctx = game._build_post_game_context(target_pid, result, shared)
    record["revelations_count"] = len(ctx["revelations"])
    record["own_rank"] = ctx["own_rank"]

    before = snapshot_state(game, agent)
    start = time.time()
    game._phase_post_game_reflection(result)
    latency_ms = round((time.time() - start) * 1000, 1)
    after = snapshot_state(game, agent)

    record["latency_ms"] = latency_ms
    record["memory_unchanged"] = (before["memory"] == after["memory"]
                                   and before["memory_history"] == after["memory_history"])
    record["state_unchanged"] = state_unchanged(before, after)

    # 検証: 想定通りcallは1回だけ発生したか
    calls_after_first = getattr(adapter, "calls_made", None)
    if calls_after_first is None:
        calls_after_first = len(getattr(adapter, "calls", []))
    record["calls_made"] = calls_after_first

    # 検証: 同一プレイヤーへの二重発火が起きないか
    # （_post_game_reflection_done による dedup。もう一度同じフェイズを呼んでも
    #   post_game_reflect() が再度呼ばれない = adapter呼出し回数が増えない）
    game._phase_post_game_reflection(result)
    calls_after_second = getattr(adapter, "calls_made", None)
    if calls_after_second is None:
        calls_after_second = len(getattr(adapter, "calls", []))
    record["second_call_fired"] = calls_after_second > calls_after_first

    reflection = agent.post_game_reflection or {}
    record["parse_status"] = reflection.get("status")
    record["emotion"] = reflection.get("emotion")
    record["self_assessment"] = reflection.get("self_assessment")
    record["biggest_revelation"] = reflection.get("biggest_revelation")
    record["changed_opinion"] = reflection.get("changed_opinion")
    record["best_player"] = reflection.get("best_player")
    record["best_player_raw"] = reflection.get("best_player_raw")
    record["most_deceptive_player"] = reflection.get("most_deceptive_player")
    record["most_deceptive_player_raw"] = reflection.get("most_deceptive_player_raw")
    record["comment"] = reflection.get("comment", "")
    record["comment_chars"] = reflection.get("chars", 0)
    record["truncated"] = reflection.get("truncated", False)
    record["salvaged"] = reflection.get("salvaged", [])
    record["ok"] = reflection.get("status") not in (None, "empty", "budget_blocked", "error")
    record["wrapper_leak"] = detect_wrapper_leak(record["comment"])
    record["fallback_used"] = record["parse_status"] not in ("ok",)
    record["revelation_reference"] = judge_revelation_reference(record["comment"], ctx["revelations"])
    record["emotion_valid"] = record["emotion"] in VALID_EMOTIONS if record["emotion"] else False
    record["length_ok"] = (
        20 <= record["comment_chars"] <= POST_GAME_REFLECTION_MAX_CHARS if record["comment"] else False
    )

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
    # provider側max_tokens打ち切りが発生していないか
    record["truncated_by_provider"] = record["finish_reason"] == "max_tokens"

    return record


# =========================================================================
# G4: parse_post_game_reflection() セルフテスト（Game/API不要）
# =========================================================================

def run_parser_selftest() -> bool:
    all_ok = True
    print("=== G4: parse_post_game_reflection() セルフテスト ===")

    basic_cases = [
        (
            "clean json all fields",
            '{"emotion": "楽", "self_assessment": "堅実", "biggest_revelation": "意外な裏切り", '
            '"changed_opinion": "評価が変わった", "best_player": "P03", '
            '"most_deceptive_player": "P05", "comment": "総括コメントです"}',
            "ok",
        ),
        (
            "fenced json",
            '```json\n{"emotion": "哀", "comment": "答え合わせを終えて複雑な気持ちだ"}\n```',
            "ok",
        ),
        (
            "no comment key, has aux fields",
            '{"emotion": "疑", "biggest_revelation": "意外な事実"}',
            "ok_assembled",
        ),
        (
            "no comment, no aux fields (rejected)",
            '{"emotion": "焦"}',
            "rejected_no_comment",
        ),
        (
            "plain text",
            'ただの散文の答え合わせコメントです。JSONではありません。',
            "ok_plaintext",
        ),
        (
            "empty",
            '',
            "empty",
        ),
        (
            "truncated mid-comment (recovery)",
            '```json\n{"emotion": "怒", "self_assessment": "反省点が多い", '
            '"comment": "答え合わせをして最も驚いたのは、',
            "ok_recovered",
        ),
    ]
    for label, text, expected in basic_cases:
        result = parse_post_game_reflection(text, max_chars=POST_GAME_REFLECTION_MAX_CHARS)
        status = result["status"]
        ok = status == expected
        all_ok = all_ok and ok
        print(f"  [{'OK' if ok else 'NG'}] {label}: status={status} (expected={expected})")

    # roster照合: 有効/無効/自己言及/該当なし
    roster = {"P01", "P02", "P03"}
    roster_cases = [
        ("valid best_player", '{"comment": "テスト", "best_player": "P02"}', "P02", False),
        ("self-reference allowed", '{"comment": "テスト", "best_player": "P01"}', "P01", False),
        ("nashi -> None, not unresolved", '{"comment": "テスト", "best_player": "該当なし"}', None, False),
        ("empty string -> None, not unresolved", '{"comment": "テスト", "best_player": ""}', None, False),
        ("hallucinated id -> unresolved", '{"comment": "テスト", "best_player": "P99"}', None, True),
        ("model name -> unresolved", '{"comment": "テスト", "best_player": "GPT-5"}', None, True),
    ]
    for label, text, expected_val, expect_unresolved in roster_cases:
        result = parse_post_game_reflection(text, roster=roster, max_chars=POST_GAME_REFLECTION_MAX_CHARS)
        val_ok = result["best_player"] == expected_val
        unresolved_ok = ("best_player_unresolved" in result["salvaged"]) == expect_unresolved
        ok = val_ok and unresolved_ok
        all_ok = all_ok and ok
        print(f"  [{'OK' if ok else 'NG'}] {label}: best_player={result['best_player']!r} "
              f"salvaged={result['salvaged']} (expected_val={expected_val!r}, expect_unresolved={expect_unresolved})")

    # comment切り詰め確認
    long_comment = json.dumps(
        {"emotion": "哀", "comment": "あ" * (POST_GAME_REFLECTION_MAX_CHARS + 500)}, ensure_ascii=False,
    )
    result = parse_post_game_reflection(long_comment, max_chars=POST_GAME_REFLECTION_MAX_CHARS)
    trunc_ok = result["truncated"] is True and result["chars"] <= POST_GAME_REFLECTION_MAX_CHARS
    all_ok = all_ok and trunc_ok
    print(f"  [{'OK' if trunc_ok else 'NG'}] comment overflow truncation: "
          f"truncated={result['truncated']} chars={result['chars']}")

    # 補助フィールド（self_assessment等）の個別切り詰め確認（cap=250/300/250）
    aux_long = json.dumps({
        "comment": "本体コメント",
        "self_assessment": "あ" * 400,
        "biggest_revelation": "い" * 400,
        "changed_opinion": "う" * 400,
    }, ensure_ascii=False)
    result = parse_post_game_reflection(aux_long, max_chars=POST_GAME_REFLECTION_MAX_CHARS)
    aux_ok = (
        result["self_assessment"] is not None and len(result["self_assessment"]) <= 250
        and result["biggest_revelation"] is not None and len(result["biggest_revelation"]) <= 300
        and result["changed_opinion"] is not None and len(result["changed_opinion"]) <= 250
        and "self_assessment_truncated" in result["salvaged"]
        and "biggest_revelation_truncated" in result["salvaged"]
        and "changed_opinion_truncated" in result["salvaged"]
    )
    all_ok = all_ok and aux_ok
    print(f"  [{'OK' if aux_ok else 'NG'}] aux field truncation (250/300/250 caps): "
          f"sa_len={len(result['self_assessment'] or '')} "
          f"br_len={len(result['biggest_revelation'] or '')} "
          f"co_len={len(result['changed_opinion'] or '')}")

    # 無効emotionの正規化確認
    # 注意: parse_post_game_reflection() は共通の normalize_emotion() を再利用しており、
    # 列挙外emotionは"平静"へフォールバックする（parse_final_reflection()の独自インライン
    # 実装が None へ落とすのとは異なる、既存実装どおりの正当な差異）。
    bad_emotion = parse_post_game_reflection('{"emotion": "喜び", "comment": "テスト"}', max_chars=POST_GAME_REFLECTION_MAX_CHARS)
    emo_ok = bad_emotion["emotion"] == "平静"
    all_ok = all_ok and emo_ok
    print(f"  [{'OK' if emo_ok else 'NG'}] invalid emotion normalized to '平静' (normalize_emotion() fallback, "
          f"differs from parse_final_reflection()'s None): emotion={bad_emotion['emotion']}")

    print(f"=== G4結果: {'PASS' if all_ok else 'FAIL'} ===")
    return all_ok


# =========================================================================
# G3: --dry-run（0 API call を証明しつつ、6プロンプトをレンダリングして出力）
# =========================================================================

def run_dry_run(out_dir: Path, cases: list[CaseSpec] | None = None) -> bool:
    """
    G3: シーン構築・答え合わせコンテキスト導出・プロンプトレンダリングまでを実経路で行うが、
    Game._phase_post_game_reflection() / LLMAgent.post_game_reflect() は一切呼び出さない
    （= adapter.complete() へ到達する経路自体を経由しない）。

    RefuseToCallAdapter は「万一どこかの経路が誤って呼び出してしまった場合の安全網」
    として各シーンへ注入し、最後に calls_attempted == 0 であることを確認する。

    cases省略時はCASE_SPECS全件（既存挙動と完全互換）。
    """
    cases = cases if cases is not None else CASE_SPECS
    print(f"=== G3: --dry-run（0 API call の証明 + プロンプトレンダリング, cases={[c.case_id for c in cases]}） ===")
    memory_samples = resolve_memory_samples(cases)
    dummy_llm_logger = LLMLogger(output_dir=out_dir, game_id="dryrun")
    prompts_md: list[str] = ["# POST_GAME_REFLECTION smoke: dry-run rendered prompts\n"]
    total_worst = 0.0
    all_ok = True

    try:
        for case, memory_sample in zip(cases, memory_samples):
            model_info = get_model(case.model_key)
            adapter = RefuseToCallAdapter()

            game, result = build_full_bot_game(case.seed)
            target_pid = select_target_pid(game, result, case.want_survived)

            # LLMAgentを実際に構築・差し替えるところまでは実経路を通す（配線確認）。
            # ただし post_game_reflect() / _phase_post_game_reflection() は呼ばない。
            prepare_agent_and_context(
                game, target_pid, model_info, adapter, dummy_llm_logger,
                memory_sample, game_cost_budget=None,
            )

            shared = game._build_god_shared_block(result)
            ctx = game._build_post_game_context(target_pid, result, shared)
            sysp = build_system_prompt(target_pid, game.config)
            userp = build_post_game_reflection_prompt(
                game.players[target_pid], game.config, ctx, memory=memory_sample,
            )

            zero_calls = adapter.calls_attempted == 0
            all_ok = all_ok and zero_calls
            survived_label = "survived" if target_pid in {p.player_id for p in result.survivors} else "eliminated"
            print(f"  [{case.case_id}] model={case.model_key}({model_info.model_id}) "
                  f"target={target_pid}({survived_label}) revelations={len(ctx['revelations'])} "
                  f"calls_attempted={adapter.calls_attempted} {'OK' if zero_calls else 'NG(呼ばれた)'}")

            worst = worst_case_cost(model_info, sysp, userp, POST_GAME_REFLECTION_MAX_TOKENS)
            total_worst += worst
            prompts_md.append(
                f"\n## {case.case_id}: {case.model_key} ({model_info.model_id}) "
                f"— {case.description}\n"
                f"- player_id: {target_pid} / {survived_label} / revelations: {len(ctx['revelations'])}\n"
                f"- worst_case_cost(max_tokens={POST_GAME_REFLECTION_MAX_TOKENS}): ${worst:.5f}\n"
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
                f"max_tokens={POST_GAME_REFLECTION_MAX_TOKENS}": rec["sent_max_tokens"] == POST_GAME_REFLECTION_MAX_TOKENS,
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
            print(f"  [{'OK' if ok else 'NG'}] {case.case_id} {case.model_key}({rec['model_id']}) "
                  f"target={rec['player_id']}(want_survived={rec['want_survived']}, "
                  f"actual_survived={rec['actual_survived']}): "
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
        game, result = build_full_bot_game(case.seed)
        target_pid = select_target_pid(game, result, case.want_survived)
        shared = game._build_god_shared_block(result)
        ctx = game._build_post_game_context(target_pid, result, shared)
        cfg = game.config.model_copy(update={"post_game_reflection_enabled": True})
        sysp = build_system_prompt(target_pid, cfg)
        userp = build_post_game_reflection_prompt(game.players[target_pid], cfg, ctx, memory=memory_sample)
        worst = worst_case_cost(model_info, sysp, userp, POST_GAME_REFLECTION_MAX_TOKENS)
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
        "post_game_reflection_max_tokens": POST_GAME_REFLECTION_MAX_TOKENS,
        "post_game_reflection_max_chars": POST_GAME_REFLECTION_MAX_CHARS,
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
                        max_tokens_cap=POST_GAME_REFLECTION_MAX_TOKENS,
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
                              f"target={rec['player_id']}(survived={rec['actual_survived']}) "
                              f"chars={rec['comment_chars']} latency={rec['latency_ms']}ms "
                              f"cost=${rec.get('cost_usd')}")
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
        "# POST_GAME_REFLECTION smoke test — call records",
        "",
        f"- output_dir: `{out_dir}`",
        f"- 計画worst_case合計: ${plan_total:.5f}",
        f"- 実測コスト合計: ${sum(r.get('cost_usd') or 0.0 for r in records):.6f}",
        "",
        "| case | model_key | model_id | target | want_survived | actual_survived | revelations | ok | parse_status | salvaged | finish_reason | truncated_by_provider | in_tok | out_tok | cost | latency_ms | emotion | best_player | most_deceptive_player | fallback | wrapper_leak | state_unchanged | memory_unchanged | second_call |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in records:
        lines.append(
            f"| {r.get('case_id')} | {r.get('model_key')} | {r.get('model_id')} | {r.get('player_id')} | "
            f"{r.get('want_survived')} | {r.get('actual_survived')} | {r.get('revelations_count')} | "
            f"{r.get('ok')} | {r.get('parse_status')} | {r.get('salvaged')} | {r.get('finish_reason')} | "
            f"{r.get('truncated_by_provider')} | {r.get('input_tokens')} | {r.get('output_tokens')} | "
            f"{r.get('cost_usd')} | {r.get('latency_ms')} | {r.get('emotion')} | {r.get('best_player')} | "
            f"{r.get('most_deceptive_player')} | {r.get('fallback_used')} | {r.get('wrapper_leak')} | "
            f"{r.get('state_unchanged')} | {r.get('memory_unchanged')} | {r.get('second_call_fired')} |"
        )
    path.write_text("\n".join(lines), encoding="utf-8")


# =========================================================================
# main
# =========================================================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="POST_GAME_REFLECTION 6モデル×1コール実API疎通テスト")
    parser.add_argument("--dry-run", action="store_true", help="0 API callでプロンプト生成のみ確認（G3）")
    parser.add_argument("--parser-selftest", action="store_true", help="parse_post_game_reflection()セルフテスト（G4）")
    parser.add_argument("--mock", action="store_true", help="FakeOkAdapterで6シーン全通し確認（G5/G6）")
    parser.add_argument("--cases", default=None,
                         help="カンマ区切りのcase_idで対象を絞り込む（例: C1,C6）。省略時はCASE_SPECS全件（6件）")
    parser.add_argument("--run-id", default=None, help="実API実行時の一意ID（既存ディレクトリなら中断=二重送信防止）")
    parser.add_argument("--out", default="logs/smoke", help="出力先ベースディレクトリ")
    parser.add_argument("--max-calls", type=int, default=6, help=f"総コール数上限（デフォルト6、絶対上限{HARD_MAX_CALLS}）")
    parser.add_argument("--max-cost", type=float, default=0.10,
                         help="累計worst_case見積上限USD（post_game_reflectionはFRより出力トークン予算が大きいため既定を高めに設定）")
    parser.add_argument("--max-cost-per-call", type=float, default=0.025,
                         help="1callあたりworst_case見積上限USD")
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
        out_dir = Path(args.out) / "post_game_reflection_dry_run"
        out_dir.mkdir(parents=True, exist_ok=True)
        return 0 if run_dry_run(out_dir, cases) else 1

    if args.mock:
        out_dir = Path(args.out) / "post_game_reflection_mock"
        out_dir.mkdir(parents=True, exist_ok=True)
        return 0 if run_mock(out_dir, cases) else 1

    # --- 実API実行 ---
    run_id = args.run_id or datetime.now().strftime("pg_smoke_%Y%m%d_%H%M%S")
    args.run_id = run_id
    out_dir = Path(args.out) / f"post_game_reflection_{run_id}"
    try:
        out_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError:
        print(f"エラー: 出力ディレクトリ {out_dir} は既に存在します（二重送信防止のため中断）。"
              "別の --run-id を指定してください。")
        return 1

    return run_real(args, out_dir, cases)


if __name__ == "__main__":
    sys.exit(main())
