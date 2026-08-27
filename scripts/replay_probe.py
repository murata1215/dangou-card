"""
Cycle 5 / 5.1 replay probe ハーネス（D1検証用）

背景: n=1実走（trial_C_l12_r12_20260824）で契約系アクションが1件も試行
されなかった（D1[S1]）。Cycle 5でprompt文言のみを修正したが、その効果を
実測するには「同じ手番・同じ会話文脈で、新旧のsystem_prompt/締め指示だけを
差し替えて再送する」置換実験が必要になる。本スクリプトはその置換実験の
ハーネスであり、以下を実測済みの方式で行う（詳細はPlanのCycle 5 §8、
Cycle 5.1 §5.1-7参照）:

1. logged system_prompt は現行コードの build_system_prompt(player_id,
   GameConfig.baseline_v1_s2(12)) と厳密一致していた（Cycle 5適用前の実測）。
   Cycle 5適用後はsystem_prompt自体が変わるため一致しなくなるが、置換方式
   には影響しない＝patched variantは常に新コードでsystem_promptを丸ごと
   再生成する。
2. user_prompt側の静的テキストは締め指示の1チャンクのみで、対象negotiation
   レコードすべてで出現回数ちょうど1回だった。
3. 倍掛け預託行は正規表現で一意に取れる（対象27件中1件のみ該当）。

対象は次の26キー・27レコード（P03 R6 t8のみ2レコード）:
  P02: R1t1/R2t2/R3t1/R3t2/R3t5/R3t7/R6t2/R7t8（8件）
  P03: R1t2/R6t7/R6t8/R6t9/R6t10（5キー・6レコード）
  P04: R6t2（1件）
  P06: R3t2/R6t2/R6t6/R6t8（4件）
  P09: R4t4/R5t6/R5t7/R5t8/R5t10/R6t5/R6t9（7件）
  P11: R6t4（1件）

【重要】本スクリプトは既定で --dry-run 相当（--dry-run / --execute のいずれも
指定しない場合は --dry-run とみなす）であり、--execute を明示指定しない限り
LLMアダプタ（llm.adapters）を一切importしない。--execute を使う場合も、
事前見積が --max-cost-usd を超える場合は実行前に中止する（二層ガードの
1層目）。実行中も呼び出しごとに llm.game_cost_budget.GameCostBudget で
予約を行い、上限に達し次第そのサンプル境界でgracefulに停止する（2層目）。

使用方法:
    uv run python scripts/replay_probe.py --dry-run
    uv run python scripts/replay_probe.py --execute --preflight-only \\
        --samples 3 --budget-mode realistic --max-cost-usd 0.90
    uv run python scripts/replay_probe.py --execute --samples 3 \\
        --budget-mode realistic --max-cost-usd 0.90
"""

from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import math
import re
import sys
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# .envからAPIキーをos.environへ読み込む（他のllm系スクリプトと同じ規約）。
# dotenvはネットワークに触れない純粋なenv var loaderなので、--dry-run経路
# でも安全にimportできる（実際にAPIを叩くのはllm.adapters.create_adapter
# のみで、これはrun_execute()内でのみ遅延importする）。
from dotenv import load_dotenv
load_dotenv()

from engine.config import GameConfig
from engine.models import Card, CardRank, PlayerState

# 締め指示・預託行の新旧テキストを1箇所から取るため、prompt_builder の
# 実装済み関数をそのまま再利用する（ロジックの二重実装をしない）。
from llm.prompt_builder import (
    build_system_prompt,
    _available_negotiation_actions,
    _render_double_up_deposit_lines,
)

# 以下はいずれもネットワーク・APIキーに触れない純粋な参照実装（価格表・
# コスト計算・レスポンス解析・予算台帳）なので、--dry-run 経路でも安全に
# importできる。実際にAPIを叩く llm.adapters.create_adapter のみ、
# run_execute() 内で遅延importする。
from llm.constants import DEFAULT_TEMPERATURE
from llm.costing import usage_cost, worst_case_cost
from llm.game_cost_budget import BudgetBlockedError, GameCostBudget
from llm.models import ModelInfo, estimate_cost, get_model, get_model_keys_by_id
from llm.response_parser import ParseError, extract_json, parse_response

TRIAL_DIR = (
    Path(__file__).resolve().parent.parent
    / "logs" / "llm" / "trial_C_l12_r12_20260824" / "llm_logs"
)

# 主指標: 契約系アクション（D1が観察対象とした3種）
CONTRACT_ACTION_TYPES = frozenset({"contract_propose", "card_trade_propose", "bounty_post"})

# 対象手番テーブル（v2.1監査レポート §検証方式(b)から転記。26キー・27レコード）
TARGET_TURNS: dict[str, list[tuple[int, int]]] = {
    "P02": [(1, 1), (2, 2), (3, 1), (3, 2), (3, 5), (3, 7), (6, 2), (7, 8)],
    "P03": [(1, 2), (6, 7), (6, 8), (6, 9), (6, 10)],
    "P04": [(6, 2)],
    "P06": [(3, 2), (6, 2), (6, 6), (6, 8)],
    "P09": [(4, 4), (5, 6), (5, 7), (5, 8), (5, 10), (6, 5), (6, 9)],
    "P11": [(6, 4)],
}

# 20260824run時点の締め指示チャンク（negotiation・enable_cot=False固定形）。
# 対象negotiationレコード全件で出現回数ちょうど1回であることを実測済み。
OLD_CLOSING_RE = re.compile(
    r"交渉フェイズのアクションをJSON形式で1つ選んでください。"
    r"（market_commitはコミットフェイズで行います。ここではdm/broadcast/transfer/repay/pass等を選択）\n"
    r"例: (?P<json_example>.+)$",
    re.DOTALL,
)

DEPOSIT_LINE_RE = re.compile(r"  倍掛け預託中: (\d+)万円（R(\d+)で判定）")

CASH_RE = re.compile(r"現金: (\d+)万円")
DEBT_RE = re.compile(r"借金残高: (\d+)万円")

# --- Cycle 5.4: 外部targets fileによる対象選択 -------------------------------
# 「合意の返事を受け取った直後の手番」選抜（P02〜P11の27件テーブルとは別母集団）
# を --targets-file で読み込めるようにする追加モード。既定モード（TARGET_TURNS）
# の挙動・出力は一切変更しない（後方互換はdry-runのバイト比較で確認済み）。
TARGETS_FILE_SCHEMA_VERSION = 1
EXPECTED_CONFIG_PRESET = "baseline_v1_s2_12"
KNOWN_ROLES = frozenset({"proposer", "replier"})


class TargetsFileError(ValueError):
    """--targets-file の読み込み・検証に失敗した（main()が終了コード1に変換する）

    sys.exit()ではなく例外にしてあるのは、テストから pytest.raises で
    検証できるようにするため（tests/test_replay_probe_targets.py）。
    """


@dataclass
class ReplayTarget:
    player_id: str
    round_num: int
    turn: int
    system_prompt: str
    user_prompt: str
    model_id: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    # Cycle 5.4: targets file 由来の付随情報（role/peer/agreement_dm/gist等）。
    # 既定モードでは常に空dict。既存の構築箇所はいずれもキーワード引数のみ
    # なので、末尾にdefault_factory付きで足しても既存呼び出しは壊れない。
    meta: dict[str, Any] = field(default_factory=dict)


def _load_targets(targets_file: Path | None = None) -> list[ReplayTarget]:
    """TARGET_TURNSに一致するnegotiationレコードをllm_logsから抽出する

    P03 R6 t8のみ2レコード存在することが実測済み。そのため戻り値は27件に
    なる（26キーに対して+1）。このファイルは読み取り専用として扱う。

    targets_file を指定した場合は、既定のTARGET_TURNSテーブルを一切使わず
    _load_targets_from_file() に委譲する（Cycle 5.4で追加）。
    """
    if targets_file is not None:
        return _load_targets_from_file(targets_file)

    targets: list[ReplayTarget] = []
    for player_id, turns in TARGET_TURNS.items():
        path = TRIAL_DIR / f"game01_{player_id}_llm_calls.jsonl"
        if not path.exists():
            print(f"⚠ ログが見つかりません: {path}", file=sys.stderr)
            continue
        wanted = set(turns)
        with path.open() as f:
            for line in f:
                rec = json.loads(line)
                if rec.get("phase") != "negotiation":
                    continue
                key = (rec.get("round_num"), rec.get("turn"))
                if key not in wanted:
                    continue
                targets.append(ReplayTarget(
                    player_id=player_id,
                    round_num=rec["round_num"],
                    turn=rec["turn"],
                    system_prompt=rec["system_prompt"],
                    user_prompt=rec["user_prompt"],
                    model_id=rec.get("model_id", "?"),
                    input_tokens=rec.get("input_tokens", 0) or 0,
                    output_tokens=rec.get("output_tokens", 0) or 0,
                    cost_usd=rec.get("cost_usd", 0.0) or 0.0,
                ))
    return targets


def _read_targets_doc(path: Path) -> dict[str, Any]:
    """targets fileを読み、schema_version等の構造検証だけを行う（ログは触らない）"""
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as e:
        raise TargetsFileError(f"ファイルが見つかりません: {path}") from e
    try:
        doc = json.loads(raw)
    except json.JSONDecodeError as e:
        raise TargetsFileError(f"JSONが不正です: {path} ({e})") from e

    if not isinstance(doc, dict):
        raise TargetsFileError(f"トップレベルはobjectである必要があります: {path}")

    ver = doc.get("schema_version")
    if ver != TARGETS_FILE_SCHEMA_VERSION:
        raise TargetsFileError(
            f"未知の schema_version={ver!r}（本スクリプトは "
            f"{TARGETS_FILE_SCHEMA_VERSION} のみ対応）: {path}"
        )

    preset = doc.get("config_preset")
    if preset is not None and preset != EXPECTED_CONFIG_PRESET:
        raise TargetsFileError(
            f"config_preset={preset!r} は本スクリプトのハードコード設定 "
            f"{EXPECTED_CONFIG_PRESET!r} と一致しません"
        )

    src = (doc.get("generated_by") or {}).get("source_trial")
    if src:
        want = TRIAL_DIR.parent.resolve()
        got = (Path(__file__).resolve().parent.parent / src).resolve()
        if got != want:
            raise TargetsFileError(
                f"source_trial が本スクリプトのTRIAL_DIRと異なります: {got} != {want}"
            )

    targets = doc.get("targets")
    if not isinstance(targets, list) or not targets:
        raise TargetsFileError(f"targets が空、またはlistではありません: {path}")
    return doc


def _index_negotiation_records(player_id: str) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """1player分のjsonlを1度だけ読み、(round,turn)->レコード列（ファイル順）に索引する"""
    path = TRIAL_DIR / f"game01_{player_id}_llm_calls.jsonl"
    if not path.exists():
        raise TargetsFileError(f"ログが見つかりません: {path}")
    idx: dict[tuple[int, int], list[dict[str, Any]]] = {}
    with path.open() as f:
        for line in f:
            rec = json.loads(line)
            if rec.get("phase") != "negotiation":
                continue
            idx.setdefault((rec.get("round_num"), rec.get("turn")), []).append(rec)
    return idx


def _load_targets_from_file(path: Path) -> list[ReplayTarget]:
    """targets fileの targets 配列の順序をそのまま保って ReplayTarget を作る

    既定モード（TARGET_TURNS）との差:
      - 順序が player 単位ではなく **ファイル記載順** になる
      - ログ欠落・座標不一致は警告+skipではなく TargetsFileError で即時失敗
        （paired設計でサイレントにNが減るとMcNemarの分母が壊れるため）
    """
    doc = _read_targets_doc(path)
    cache: dict[str, dict[tuple[int, int], list[dict[str, Any]]]] = {}
    targets: list[ReplayTarget] = []

    for i, spec in enumerate(doc["targets"]):
        where = f"targets[{i}]"
        if not isinstance(spec, dict):
            raise TargetsFileError(f"{where}: objectではありません")
        pid = spec.get("player_id")
        if not isinstance(pid, str) or not re.fullmatch(r"P\d{2}", pid):
            raise TargetsFileError(f"{where}: player_id が不正です: {pid!r}")
        rnd, trn = spec.get("round_num"), spec.get("turn")
        for name, val in (("round_num", rnd), ("turn", trn)):
            if not isinstance(val, int) or isinstance(val, bool) or val < 1:
                raise TargetsFileError(f"{where}: {name} が不正です: {val!r}")

        meta = spec.get("meta") or {}
        if not isinstance(meta, dict):
            raise TargetsFileError(f"{where}: meta はobjectである必要があります")
        role = meta.get("role")
        if role is not None and role not in KNOWN_ROLES:
            raise TargetsFileError(
                f"{where}: 未知の role={role!r}（{sorted(KNOWN_ROLES)} のみ）"
            )

        if pid not in cache:
            cache[pid] = _index_negotiation_records(pid)
        matches = cache[pid].get((rnd, trn), [])
        if not matches:
            raise TargetsFileError(
                f"{where}: 該当するnegotiationレコードがありません: {pid} R{rnd} t{trn}"
            )

        occ = spec.get("occurrence")
        if occ is None:
            if len(matches) > 1:
                print(
                    f"⚠ {where}: {pid} R{rnd} t{trn} に{len(matches)}レコード該当。"
                    f"occurrence未指定のため全件を展開します（metaは複製）。",
                    file=sys.stderr,
                )
            chosen = matches
        else:
            if not isinstance(occ, int) or isinstance(occ, bool) or not (0 <= occ < len(matches)):
                raise TargetsFileError(
                    f"{where}: occurrence={occ!r} が範囲外（該当{len(matches)}件）"
                )
            chosen = [matches[occ]]

        for rec in chosen:
            targets.append(ReplayTarget(
                player_id=pid,
                round_num=rec["round_num"],
                turn=rec["turn"],
                system_prompt=rec["system_prompt"],
                user_prompt=rec["user_prompt"],
                model_id=rec.get("model_id", "?"),
                input_tokens=rec.get("input_tokens", 0) or 0,
                output_tokens=rec.get("output_tokens", 0) or 0,
                cost_usd=rec.get("cost_usd", 0.0) or 0.0,
                meta=dict(meta),  # 参照共有しない（record埋め込み時の汚染防止）
            ))
    return targets


def _approximate_state(user_prompt: str, round_num: int) -> tuple[PlayerState, dict[str, Any]]:
    """ログのuser_prompt本文から、締め指示の再生成に必要な最小限の状態を近似する

    【既知の制約（意図的な設計・engine変更禁止のため精緻化しない）】
    - cash/debt_balanceは「## あなたの状態」欄の最初の一致から抽出する（正確）。
    - contracts_pending / trades_pending / my_contracts の有無は、対応する
      見出し文字列がuser_prompt中に存在するかどうかの真偽値でのみ近似する
      （中身は復元しない。_available_negotiation_actions は真偽値しか見ないため
      可否判定そのものへの影響は無い）。
    - bounties_public は可否判定に使わないため空のまま。
    - 匿名通信の当ラウンド残数・カードトレードの当ラウンド実施済みフラグは
      本番のvisible_stateにも存在しない情報なので、ここでも判定に使わない
      （build_negotiation_prompt本体と同じ制約）。
    """
    cash_m = CASH_RE.search(user_prompt)
    debt_m = DEBT_RE.search(user_prompt)
    cash = int(cash_m.group(1)) * 10_000 if cash_m else 0
    debt = int(debt_m.group(1)) * 10_000 if debt_m else 0

    dummy_hand = [Card(rank=r, card_id=f"c{i}") for i, r in enumerate(list(CardRank)[:6])]
    player_state = PlayerState(
        player_id="_replay_probe", cash=cash, debt_balance=debt,
        initial_loan=debt, hand=dummy_hand,
    )

    has_pending_contract = "## 提案中の正式契約" in user_prompt
    has_pending_trade = "## 提案中のカードトレード" in user_prompt
    has_my_contract = "## あなたが当事者の正式契約" in user_prompt

    visible_state: dict[str, Any] = {
        "contracts_pending": [{"_approx": True}] if has_pending_contract else [],
        "trades_pending": [{"_approx": True}] if has_pending_trade else [],
        # my_contracts はstatus=="active"の存在有無だけが判定に使われる
        "my_contracts": [{"status": "active"}] if has_my_contract else [],
        "bounties_public": [],
    }
    return player_state, visible_state


def _build_patched_closing(user_prompt: str, round_num: int, config: GameConfig) -> str:
    """新コードの締め指示（## いま選べるアクション）を、ログから近似した状態で再生成する"""
    player_state, visible_state = _approximate_state(user_prompt, round_num)
    available, unavailable = _available_negotiation_actions(
        player_state, round_num, visible_state, config,
    )
    m = OLD_CLOSING_RE.search(user_prompt)
    json_example = m.group("json_example") if m else '{"strategy": {...}, "action": {"type": "pass"}}'

    lines = ["\n## いま選べるアクション", "  " + " / ".join(available)]
    if unavailable:
        lines.append("  いま選べないもの: " + " / ".join(unavailable))
    lines.append(
        "\n上の一覧から1つ選び、JSON形式で回答してください。"
        "（market_commitはコミットフェイズで行います。ここでは選べません）\n"
        f"例: {json_example}"
    )
    return "\n".join(lines)


def _patch_user_prompt(user_prompt: str, round_num: int, config: GameConfig) -> str:
    """締め指示チャンクの1:1置換＋倍掛け預託行があれば§4の3行へ置換する

    この関数はターゲット1件につき正確に1回だけ呼ぶこと（_prepare_targets参照）。
    2回適用すると預託行置換が非冪等（「成功条件」重複）になる。
    """
    patched = user_prompt

    def _deposit_sub(m: re.Match) -> str:
        deposit_man, success_round = int(m.group(1)), int(m.group(2))
        du = {"deposit": deposit_man * 10_000, "success_round": success_round}
        return "\n".join(_render_double_up_deposit_lines(du))

    patched = DEPOSIT_LINE_RE.sub(_deposit_sub, patched)

    if OLD_CLOSING_RE.search(patched):
        new_closing = _build_patched_closing(patched, round_num, config)
        patched = OLD_CLOSING_RE.sub(lambda _m: new_closing.lstrip("\n"), patched)
    else:
        print(
            "⚠ 締め指示チャンクが見つかりませんでした（置換をスキップ）",
            file=sys.stderr,
        )
    return patched


def _unified_diff(before: str, after: str, label: str) -> str:
    import difflib
    return "".join(difflib.unified_diff(
        before.splitlines(keepends=True),
        after.splitlines(keepends=True),
        fromfile=f"{label}.baseline", tofile=f"{label}.patched",
    ))


# ---------------------------------------------------------------------------
# Cycle 5.1: --execute 経路の実装。ここから下が5.1で追加した部分。
# ---------------------------------------------------------------------------


def _resolve_model_key(model_id: str) -> str:
    """model_idからレジストリキーを解決する（P6: M5/L5等のfirst-wins誤りを避ける）

    get_model()は宣言順first-winsでM系を返すことがあるが、価格・挙動は
    M/L同一model_idの双子で完全に一致するため実害はない。ただし記録上の
    tierを正しく残すため、Lキーがあれば優先する。
    """
    keys = get_model_keys_by_id(model_id)
    for k in keys:
        if k.startswith("L"):
            return k
    return keys[0] if keys else model_id


# ---------------------------------------------------------------------------
# Cycle 5.5: 上位・思考モデルreplay probe用の追加（--model-override / --thinking）。
# どちらのフラグも未指定なら以下のコードには一切到達せず、既定モードの挙動は
# 完全にno-op（バイト一致）を維持する。Planの調査A・B（doc: dapper-snuggling-
# cloud.md 「調査で判明した事実」節）に基づく。
# ---------------------------------------------------------------------------

# 5.4の既定--max-output-tokens=2000ではAnthropic adaptive thinking等で
# 思考トークンが可視JSON出力の余地を食い潰す（llm/models.py L162-164の
# 既知事故と同型）。--thinking medium 使用時はこの下限を要求する
# （0 API call の事前バリデーション。Plan「ハーネス変更の要旨」参照）。
THINKING_MIN_OUTPUT_TOKENS = 3000

# provider名(ModelInfo.provider)をキーに、--thinking medium 時にadapter.complete()
# へ渡すrequest_optionsを定義する。値がNoneのprovider（xAI）は「既定で常時
# 思考する」ため追加送信なし（hidden_thinking_reserve_tokensで別途会計する）。
# Anthropicの形は scripts/model_matrix.py _phase3_request_options() で実証済み
# （タスク指定の{"type":"enabled","budget_tokens":N}ではない）。DeepSeek/Moonshot
# はextra_bodyでregistryのthinking無効化(extra_params)を上書きする
# （llm/adapters.py: OpenAICompatAdapter.completeはextra_paramsの後に
# request_optionsをupdateするため完全に置き換わる）。
THINKING_REQUEST_OPTIONS: dict[str, dict[str, Any] | None] = {
    "Anthropic": {"thinking": {"type": "adaptive"}, "output_config": {"effort": "medium"}},
    "OpenAI": {"reasoning_effort": "medium"},
    "xAI": None,
    "DeepSeek": {"extra_body": {"thinking": {"type": "enabled"}}},
    "Moonshot": {"extra_body": {"thinking": {"type": "enabled"}}},
}


def _thinking_request_options(model: ModelInfo, thinking_mode: str) -> dict[str, Any] | None:
    """--thinkingフラグからadapter.complete()へ渡すrequest_optionsを決める

    thinking_mode=="off"（既定）は常にNoneを返す。run_execute()側もNoneの
    場合はrequest_optionsキーワード自体を渡さないため、既定モードの
    adapter.complete()呼び出しはCycle 5.5以前と完全に同一になる。
    """
    if thinking_mode == "off":
        return None
    return THINKING_REQUEST_OPTIONS.get(model.provider)


def _apply_model_override(targets: list["PreparedTarget"], model_key: str) -> list["PreparedTarget"]:
    """全対象のmodel_key/model_idを指定レジストリキーへ一括差し替える（--model-override）

    prompt本文（baseline/patched system/user）・meta・input_token_ratio等は
    一切変更しない（dataclasses.replace()でmodel_key/model_idの2フィールドのみ
    差し替える）。呼ばれるのは--model-override指定時のみ＝未指定時は完全no-op。
    """
    model = get_model(model_key)
    return [replace(t, model_key=model_key, model_id=model.model_id) for t in targets]


@dataclass
class PreparedTarget:
    index: int
    player_id: str
    round_num: int
    turn: int
    model_id: str
    model_key: str
    baseline_system: str
    baseline_user: str
    patched_system: str
    patched_user: str
    closing_replaced: bool
    deposit_replaced: bool
    logged_input_tokens: int
    logged_output_tokens: int
    logged_cost_usd: float
    input_token_ratio: float  # logged_input_tokens / (len(system)+len(user)) 実測比
    meta: dict[str, Any] = field(default_factory=dict)


def _prepare_targets(
    config: GameConfig, targets_file: Path | None = None,
) -> list[PreparedTarget]:
    """レコードそれぞれについてbaseline/patched promptを正確に1回だけ生成する

    P3対応: _patch_user_prompt()は非冪等（預託行を2回置換すると「成功条件」が
    重複する）ため、生成結果はここでキャッシュし、run_dry_run/run_execute の
    両方から使い回す。

    targets_file を指定した場合は既定の27件テーブルの代わりにそのファイルの
    対象手番を使う（Cycle 5.4で追加。順序はファイル記載順）。
    """
    prepared: list[PreparedTarget] = []
    for i, t in enumerate(_load_targets(targets_file)):
        patched_system = build_system_prompt(t.player_id, config)
        patched_user = _patch_user_prompt(t.user_prompt, t.round_num, config)

        dup = patched_user.count("成功条件:")
        assert dup <= 1, (
            f"倍掛け成功条件の重複を検出（非冪等な置換の兆候）: "
            f"{t.player_id} R{t.round_num} t{t.turn} (count={dup})"
        )

        char_len = len(t.system_prompt) + len(t.user_prompt)
        ratio = (t.input_tokens / char_len) if char_len else 0.5

        prepared.append(PreparedTarget(
            index=i,
            player_id=t.player_id,
            round_num=t.round_num,
            turn=t.turn,
            model_id=t.model_id,
            model_key=_resolve_model_key(t.model_id),
            baseline_system=t.system_prompt,
            baseline_user=t.user_prompt,
            patched_system=patched_system,
            patched_user=patched_user,
            closing_replaced=bool(OLD_CLOSING_RE.search(t.user_prompt)),
            deposit_replaced=bool(DEPOSIT_LINE_RE.search(t.user_prompt)),
            logged_input_tokens=t.input_tokens,
            logged_output_tokens=t.output_tokens,
            logged_cost_usd=t.cost_usd,
            input_token_ratio=ratio,
            meta=t.meta,
        ))
    return prepared


def _hardened_cost(
    model: ModelInfo, system: str, user: str, max_output_tokens: int,
    include_hidden_reserve: bool = False,
) -> float:
    """保守的な入力見積（0.80 tok/char）＋出力満額での見積（P8対応）

    llm.costing.worst_case_cost() の `(len//2)+50` ヒューリスティックは、この
    日本語promptでは実測input tokenを12〜24%過小評価することが分かっている
    （Cycle 5.1 §5.1-4）。本番のcosting.pyは触らず、このモジュール内だけで
    より保守的な見積を別途用意する。

    include_hidden_reserve（Cycle 5.5追加、既定False＝現行と完全同値）:
    Trueの場合、model.hidden_thinking_reserve_tokens（max_tokensの外側で
    課金されるhidden thinking予約分。例: grok-4.6=1536）をtotal_tokensへ
    加算する（llm.costing.worst_case_cost()と同じ会計パターン）。
    """
    approx_input_tokens = int((len(system) + len(user)) * 0.80)
    reserve = model.hidden_thinking_reserve_tokens if include_hidden_reserve else 0
    return estimate_cost(
        model, approx_input_tokens, max_output_tokens,
        total_tokens=approx_input_tokens + max_output_tokens + reserve,
    )


def _realistic_cost(
    model: ModelInfo, system: str, user: str, ratio: float,
    logged_output_tokens: int,
) -> float:
    """実測ベースの見積: ログの実input/char比を今回のprompt長に当てはめ、
    出力はログの実測output tokens×1.3を使う（Cycle 5.1 §5.1-4 realistic方式）。
    """
    approx_input_tokens = max(1, int((len(system) + len(user)) * ratio))
    approx_output_tokens = max(1, int(logged_output_tokens * 1.3))
    return estimate_cost(
        model, approx_input_tokens, approx_output_tokens,
        total_tokens=approx_input_tokens + approx_output_tokens,
    )


def _estimate_call_cost(
    mode: str, model: ModelInfo, system: str, user: str, max_output_tokens: int,
    ratio: float, logged_output_tokens: int, *, include_hidden_reserve: bool = False,
) -> float:
    if mode == "worst_case":
        return worst_case_cost(model, system, user, max_output_tokens)
    if mode == "hardened":
        return _hardened_cost(
            model, system, user, max_output_tokens,
            include_hidden_reserve=include_hidden_reserve,
        )
    if mode == "realistic":
        return _realistic_cost(model, system, user, ratio, logged_output_tokens)
    raise ValueError(f"unknown budget mode: {mode}")


class _NullEventLogger:
    """GameCostBudgetが要求するevent_loggerの最小実装（本番のEventLoggerは使わない）"""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    def log(self, event_type: str, round_num: int, phase: str, data: Any = None) -> None:
        self.events.append({
            "event_type": event_type, "round_num": round_num, "phase": phase, "data": data,
        })


def _preflight(
    targets: list[PreparedTarget], *, samples: int, variants: list[str], budget_mode: str,
    max_output_tokens: int, max_cost_usd: float, check_keys: bool = True,
    thinking_mode: str = "off",
) -> dict[str, Any]:
    """API呼び出し0件の事前ゲート。見積・締め指示置換の健全性・APIキー有無を確認する

    P4対応: closing_replacedがFalseのレコードが1件でもあれば、patchedが
    baselineに化けている可能性があるため即中止する。

    thinking_mode（Cycle 5.5追加、既定"off"）: "off"以外の場合、
    max_output_tokensがTHINKING_MIN_OUTPUT_TOKENS未満なら即中止する
    （思考トークンが可視JSON出力を食い潰す既知事故の事前検出）。
    """
    import os as _os

    if thinking_mode != "off" and max_output_tokens < THINKING_MIN_OUTPUT_TOKENS:
        print(
            f"[replay_probe] --thinking {thinking_mode} 指定時は --max-output-tokens が"
            f" {THINKING_MIN_OUTPUT_TOKENS} 以上必要です（現在 {max_output_tokens}）。"
            "思考トークンが可視JSON出力の余地を食い潰す既知の事故を避けるため中止します。",
            file=sys.stderr,
        )
        sys.exit(1)

    bad_closing = [t for t in targets if not t.closing_replaced]
    if bad_closing:
        labels = ", ".join(f"{t.player_id}R{t.round_num}t{t.turn}" for t in bad_closing)
        print(
            f"[replay_probe] 締め指示チャンクが見つからないレコードがあります"
            f"（patchedがbaselineと同一になる恐れ）: {labels}",
            file=sys.stderr,
        )
        sys.exit(1)

    per_model: dict[str, dict[str, Any]] = {}
    total = 0.0
    key_status: dict[str, bool] = {}
    for t in targets:
        model = get_model(t.model_key)
        if check_keys and model.env_key not in key_status:
            key_status[model.env_key] = bool(_os.environ.get(model.env_key))
        for variant in variants:
            system = t.baseline_system if variant == "baseline" else t.patched_system
            user = t.baseline_user if variant == "baseline" else t.patched_user
            est = _estimate_call_cost(
                budget_mode, model, system, user, max_output_tokens,
                t.input_token_ratio, t.logged_output_tokens,
                include_hidden_reserve=(thinking_mode != "off"),
            )
            est_total = est * samples
            total += est_total
            pm = per_model.setdefault(t.model_key, {"model_id": t.model_id, "n_calls": 0, "cost_usd": 0.0})
            pm["n_calls"] += samples
            pm["cost_usd"] += est_total

    return {
        "budget_mode": budget_mode,
        "samples": samples,
        "variants": variants,
        "target_count": len(targets),
        "closing_replaced_ok": True,
        "estimated_total_cost_usd": round(total, 6),
        "max_cost_usd": max_cost_usd,
        "within_budget": total <= max_cost_usd,
        "per_model": {
            k: {"model_id": v["model_id"], "n_calls": v["n_calls"], "cost_usd": round(v["cost_usd"], 6)}
            for k, v in sorted(per_model.items())
        },
        "api_key_present": key_status,
    }


def _score_response(text: str, player_id: str) -> dict[str, Any]:
    """LLM応答本文をJSON抽出・アクション解析し、契約系変換の判定材料を作る"""
    result: dict[str, Any] = {
        "json_extracted": False,
        "parse_ok": False,
        "parse_error_class": None,
        "action_type": None,
        "is_contract_action": False,
        "mentions_contract": text.count("契約"),
        "mentions_type_b": text.count("型B"),
    }
    data = extract_json(text)
    result["json_extracted"] = data is not None
    if data is None:
        result["parse_error_class"] = "no_json_found"
        return result
    try:
        _strategy, action = parse_response(text, player_id, "negotiation")
        result["parse_ok"] = True
        result["action_type"] = action.type
        result["is_contract_action"] = action.type in CONTRACT_ACTION_TYPES
    except ParseError as e:
        result["parse_error_class"] = type(e).__name__
    except Exception as e:  # noqa: BLE001 - 未知の解析失敗もデータ点として残す
        result["parse_error_class"] = type(e).__name__
    return result


def _wilson_ci(successes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    phat = successes / n
    denom = 1 + z * z / n
    center = phat + z * z / (2 * n)
    margin = z * math.sqrt(phat * (1 - phat) / n + z * z / (4 * n * n))
    return ((center - margin) / denom, (center + margin) / denom)


def _binom_cdf_half(k: int, n: int) -> float:
    """P(X <= k) for X ~ Binomial(n, 1/2)。厳密値（math.combのみ・scipy不使用）

    本スクリプトは--dry-run経路で重い数値スタックをimportしない方針
    （冒頭docstring参照）なのでscipyは使わない。CPythonのint真除算は
    中間でfloat化しないため、n=数百でも桁溢れしない。
    """
    if n <= 0:
        return 1.0
    k = max(0, min(k, n))
    return sum(math.comb(n, i) for i in range(k + 1)) / (1 << n)


def _mcnemar_exact(b: int, c: int) -> dict[str, Any]:
    """McNemar 正確検定（two-sided exact binomial, 連続修正なし）

    b = baseline非契約 → patched契約（patchedの勝ち）
    c = baseline契約   → patched非契約（baselineの勝ち）
    p = min(1, 2 * P(X <= min(b,c) | X~Bin(b+c, 0.5)))

    実測アンカー（doc/devlog参照・実装検証用）:
      Cycle 5.1: b=2, c=0 -> p = 2*(1/4)   = 0.50
      Cycle 5.3: b=2, c=1 -> p = 2*(4/8)   = 1.00
    """
    n = b + c
    p = 1.0 if n == 0 else min(1.0, 2.0 * _binom_cdf_half(min(b, c), n))
    lo, hi = _wilson_ci(b, n)
    return {
        "test": "mcnemar_exact_binomial_two_sided",
        "b_baseline0_patched1": b,
        "c_baseline1_patched0": c,
        "n_discordant": n,
        "p_value": p,
        "significant_at_005": p < 0.05,
        "prop_discordant_favoring_patched": (b / n if n else 0.0),
        "prop_wilson95": [lo, hi],
    }


def _paired_counts(
    records: list[dict[str, Any]], role: str | None = None,
) -> dict[str, int]:
    """(sample_index, target_index) をキーに baseline/patched を対にし、2x2表を作る

    - api_ok=False のcallは対から除外する（APIエラーを「非契約」と数えない）
    - 片側variantしか無い run（例: Cycle 5.3 の --variants patched）では
      n_pairs=0 となり、McNemarはp=1.0・n_discordant=0を返す（これは正常）
    """
    idx: dict[tuple[Any, Any], dict[str, dict[str, Any]]] = {}
    for r in records:
        if role is not None and r.get("role") != role:
            continue
        if not r.get("api_ok", True):
            continue
        idx.setdefault((r.get("sample_index"), r.get("target_index")), {})[r["variant"]] = r

    b = c = both = neither = 0
    for pair in idx.values():
        if "baseline" not in pair or "patched" not in pair:
            continue
        bl = bool(pair["baseline"].get("is_contract_action"))
        pt = bool(pair["patched"].get("is_contract_action"))
        if not bl and pt:
            b += 1
        elif bl and not pt:
            c += 1
        elif bl and pt:
            both += 1
        else:
            neither += 1
    return {
        "n_pairs": b + c + both + neither,
        "both_contract": both, "neither_contract": neither,
        "b_baseline0_patched1": b, "c_baseline1_patched0": c,
    }


def _role_counts(items: list[Any]) -> dict[str, int]:
    """PreparedTarget等のリストから meta.role の分布を数える（既定モードは全て_none）"""
    counts: dict[str, int] = {}
    for t in items:
        role = t.meta.get("role") if t.meta else None
        key = role or "_none"
        counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def run_dry_run(out_dir: Path, targets_file: Path | None = None) -> None:
    config = GameConfig.baseline_v1_s2(12)
    prepared = _prepare_targets(config, targets_file)
    out_dir.mkdir(parents=True, exist_ok=True)

    total_cost = 0.0
    total_in = 0
    total_out = 0
    per_model: dict[str, dict[str, float]] = {}

    for t in prepared:
        label = f"{t.player_id}_R{t.round_num}_t{t.turn}"
        record_dir = out_dir / f"{t.index:02d}_{label}"
        record_dir.mkdir(parents=True, exist_ok=True)
        (record_dir / "baseline_system_prompt.txt").write_text(t.baseline_system)
        (record_dir / "baseline_user_prompt.txt").write_text(t.baseline_user)
        (record_dir / "patched_system_prompt.txt").write_text(t.patched_system)
        (record_dir / "patched_user_prompt.txt").write_text(t.patched_user)
        (record_dir / "system_prompt.diff").write_text(
            _unified_diff(t.baseline_system, t.patched_system, f"{label}.system"))
        (record_dir / "user_prompt.diff").write_text(
            _unified_diff(t.baseline_user, t.patched_user, f"{label}.user"))

        total_cost += t.logged_cost_usd
        total_in += t.logged_input_tokens
        total_out += t.logged_output_tokens
        pm = per_model.setdefault(t.model_id, {"n": 0, "cost": 0.0})
        pm["n"] += 1
        pm["cost"] += t.logged_cost_usd

    summary = {
        "target_count": len(prepared),
        "baseline_total_cost_usd": round(total_cost, 6),
        "baseline_total_input_tokens": total_in,
        "baseline_total_output_tokens": total_out,
        "two_variant_estimate_usd": round(total_cost * 2, 6),
        "closing_replaced_count": sum(1 for t in prepared if t.closing_replaced),
        "deposit_replaced_count": sum(1 for t in prepared if t.deposit_replaced),
        "per_model": {
            k: {"n": v["n"], "cost_usd": round(v["cost"], 6)}
            for k, v in sorted(per_model.items())
        },
    }
    if targets_file is not None:
        summary["targets_source"] = str(targets_file)
        summary["role_counts"] = _role_counts(prepared)
    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))

    print(f"[replay_probe] --dry-run 完了: {len(prepared)}件抽出、diffを {out_dir} へ出力しました")
    print(f"[replay_probe] baseline実測コスト合計: ${total_cost:.4f}"
          f"（2 variant見積: ${total_cost * 2:.4f}）")
    print("[replay_probe] API呼び出し: 0件")


def _write_summary_and_csv(
    out_dir: Path, records: list[dict[str, Any]], budget: GameCostBudget,
    terminated_reason: str | None, variants: list[str],
    legacy_summary: bool = False,
) -> None:
    if records:
        fieldnames = sorted({k for r in records for k in r.keys()})
        with (out_dir / "results.csv").open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fieldnames)
            w.writeheader()
            for r in records:
                row = {
                    k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                    for k, v in r.items()
                }
                w.writerow(row)

    def _bucket(variant: str, role: str | None = None) -> dict[str, Any]:
        rs = [
            r for r in records
            if r["variant"] == variant and (role is None or r.get("role") == role)
        ]
        n = len(rs)
        n_contract = sum(1 for r in rs if r.get("is_contract_action"))
        n_parsed = sum(1 for r in rs if r.get("parse_ok"))
        n_json = sum(1 for r in rs if r.get("json_extracted"))
        n_trunc = sum(1 for r in rs if r.get("truncated"))
        lo, hi = _wilson_ci(n_contract, n)
        return {
            "n": n,
            "conversion_rate_of_attempted": {
                "num": n_contract, "den": n,
                "rate": (n_contract / n if n else 0.0), "wilson95": [lo, hi],
            },
            "conversion_rate_of_parsed": {
                "num": n_contract, "den": n_parsed,
                "rate": (n_contract / n_parsed if n_parsed else 0.0),
            },
            "json_valid_rate": {"num": n_json, "den": n, "rate": (n_json / n if n else 0.0)},
            "parse_ok_rate": {"num": n_parsed, "den": n, "rate": (n_parsed / n if n else 0.0)},
            "truncation_rate": {"num": n_trunc, "den": n, "rate": (n_trunc / n if n else 0.0)},
            "mean_mentions_contract": (
                sum(r.get("mentions_contract", 0) for r in rs) / n if n else 0.0
            ),
            "mean_mentions_type_b": (
                sum(r.get("mentions_type_b", 0) for r in rs) / n if n else 0.0
            ),
        }

    def _action_dist(variant: str, role: str | None = None) -> dict[str, int]:
        rs = [
            r for r in records
            if r["variant"] == variant and (role is None or r.get("role") == role)
        ]
        dist: dict[str, int] = {}
        for r in rs:
            key = r.get("action_type") or f"_error:{r.get('parse_error_class') or r.get('error_type')}"
            dist[key] = dist.get(key, 0) + 1
        return dict(sorted(dist.items()))

    def _by_model() -> dict[str, Any]:
        """Cycle 5.5追加: model_key別の変換率・role分布・思考トークン量等をまとめる

        既存キー（by_variant等）には触れず、summaryへの追加キーとしてのみ働く。
        """
        model_keys = sorted({r.get("model_key") for r in records if r.get("model_key")})
        out: dict[str, Any] = {}
        for mk in model_keys:
            rs = [r for r in records if r.get("model_key") == mk]
            n = len(rs)
            n_contract = sum(1 for r in rs if r.get("is_contract_action"))
            n_parsed = sum(1 for r in rs if r.get("parse_ok"))
            n_json = sum(1 for r in rs if r.get("json_extracted"))
            n_trunc = sum(1 for r in rs if r.get("truncated"))
            lo, hi = _wilson_ci(n_contract, n)
            role_dist: dict[str, int] = {}
            action_dist: dict[str, int] = {}
            finish_dist: dict[str, int] = {}
            reasoning_vals: list[float] = []
            for r in rs:
                role_dist[r.get("role") or "_none"] = role_dist.get(r.get("role") or "_none", 0) + 1
                key = r.get("action_type") or f"_error:{r.get('parse_error_class') or r.get('error_type')}"
                action_dist[key] = action_dist.get(key, 0) + 1
                fr = r.get("finish_reason") or "_none"
                finish_dist[fr] = finish_dist.get(fr, 0) + 1
                rt = r.get("reasoning_tokens")
                if rt is None:
                    rt = (r.get("usage") or {}).get("reasoning_tokens")
                if rt:
                    reasoning_vals.append(rt)
            out[mk] = {
                "model_id": rs[0].get("model_id") if rs else None,
                "n": n,
                "conversion_rate": {
                    "num": n_contract, "den": n,
                    "rate": (n_contract / n if n else 0.0), "wilson95": [lo, hi],
                },
                "json_valid_rate": (n_json / n if n else 0.0),
                "parse_ok_rate": (n_parsed / n if n else 0.0),
                "truncation_rate": (n_trunc / n if n else 0.0),
                "truncated_count": n_trunc,
                "role_distribution": dict(sorted(role_dist.items())),
                "action_type_distribution": dict(sorted(action_dist.items())),
                "finish_reason_distribution": dict(sorted(finish_dist.items())),
                "reasoning_tokens_mean": (
                    sum(reasoning_vals) / len(reasoning_vals) if reasoning_vals else 0.0
                ),
                "reasoning_tokens_max": (max(reasoning_vals) if reasoning_vals else 0),
            }
        return out

    summary = {
        "generated_at": datetime.datetime.now().isoformat(),
        "terminated_reason": terminated_reason,
        "total_calls": len(records),
        "total_cost_usd": round(sum(r.get("cost_usd", 0.0) for r in records), 6),
        "budget_snapshot": budget.snapshot(),
        "by_variant": {v: _bucket(v) for v in variants},
        "action_type_distribution": {v: _action_dist(v) for v in variants},
    }

    if not legacy_summary:
        roles_present = sorted({r.get("role") for r in records if r.get("role")})
        if roles_present:
            summary["by_variant_role"] = {
                v: {role: _bucket(v, role) for role in roles_present} for v in variants
            }
            summary["action_type_distribution_by_role"] = {
                v: {role: _action_dist(v, role) for role in roles_present} for v in variants
            }
        if "baseline" in variants and "patched" in variants:
            pc = _paired_counts(records)
            summary["mcnemar"] = _mcnemar_exact(
                pc["b_baseline0_patched1"], pc["c_baseline1_patched0"],
            ) | {"n_pairs": pc["n_pairs"]}
            if roles_present:
                summary["mcnemar_by_role"] = {}
                for role in roles_present:
                    pc_r = _paired_counts(records, role=role)
                    summary["mcnemar_by_role"][role] = _mcnemar_exact(
                        pc_r["b_baseline0_patched1"], pc_r["c_baseline1_patched0"],
                    ) | {"n_pairs": pc_r["n_pairs"]}
        # Cycle 5.5追加: model_key別内訳。--variants patched単独（5.5既定）でもn_pairs
        # なしで単独集計できるため、baseline/patched両方揃っている必要はない。
        if any(r.get("model_key") for r in records):
            summary["by_model"] = _by_model()

    (out_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"[replay_probe] --execute 完了: {len(records)} call, "
          f"実測コスト ${summary['total_cost_usd']:.4f}")
    if terminated_reason:
        print(f"[replay_probe] 中断理由: {terminated_reason}", file=sys.stderr)


def run_execute(
    targets: list[PreparedTarget], out_dir: Path, *, samples: int, variants: list[str],
    budget_mode: str, max_output_tokens: int, max_cost_usd: float, sleep_seconds: float,
    rate_limit_cooldown: float, resume: bool, legacy_summary: bool = False,
    thinking_mode: str = "off", cycle55_extra_fields: bool = False,
) -> None:
    """--execute経路の実処理。P2対応: create_adapterを正しくimportする。

    LLMアダプタのimportはこの関数の中でのみ行い、--dry-run / --preflight-only
    経路には一切波及させない。呼び出しごとにGameCostBudgetへ事前予約を行い、
    上限に達したらそのサンプル境界でgracefulに停止する（実費が承認上限を
    構造的に超えないようにする2層目のガード）。

    thinking_mode / cycle55_extra_fields（Cycle 5.5追加、既定"off"/False＝
    現行と完全同値）: thinking_modeが"off"以外ならadapter.complete()へ
    request_optionsを追加する。cycle55_extra_fieldsがTrueならresults.jsonlの
    各recordへ thinking_mode/thinking_applied/request_options/reasoning_tokens/
    max_output_tokens を追加する（既定モードのrecordスキーマは変更しない）。
    """
    from llm.adapters import AdapterError, _classify_error, create_adapter

    out_dir.mkdir(parents=True, exist_ok=True)
    raw_dir = out_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    results_path = out_dir / "results.jsonl"

    done_keys: set[tuple[int, int, str]] = set()
    prior_records: list[dict[str, Any]] = []
    if resume and results_path.exists():
        with results_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                prior_records.append(rec)
                done_keys.add((rec["sample_index"], rec["target_index"], rec["variant"]))

    budget = GameCostBudget(
        per_player_cap_usd=max_cost_usd, game_cap_usd=max_cost_usd,
        event_logger=_NullEventLogger(), abort_on_block=False,
    )

    adapters: dict[str, tuple[ModelInfo, Any]] = {}

    def _get_adapter(model_key: str) -> tuple[ModelInfo, Any]:
        if model_key not in adapters:
            model = get_model(model_key)
            adapter = create_adapter(model, max_retries=0, allow_temperature_fallback=False)
            adapters[model_key] = (model, adapter)
        return adapters[model_key]

    consecutive_errors: dict[str, int] = {}
    skipped_models: set[str] = set()
    all_results: list[dict[str, Any]] = list(prior_records)
    terminated_reason: str | None = None

    results_f = results_path.open("a")
    try:
        for sample_index in range(1, samples + 1):
            if terminated_reason:
                break
            for t in targets:
                if terminated_reason:
                    break
                for variant in variants:
                    key = (sample_index, t.index, variant)
                    if key in done_keys:
                        continue
                    if t.model_key in skipped_models:
                        continue

                    system = t.baseline_system if variant == "baseline" else t.patched_system
                    user = t.baseline_user if variant == "baseline" else t.patched_user
                    model, adapter = _get_adapter(t.model_key)

                    est = _estimate_call_cost(
                        budget_mode, model, system, user, max_output_tokens,
                        t.input_token_ratio, t.logged_output_tokens,
                        include_hidden_reserve=(thinking_mode != "off"),
                    )
                    try:
                        reservation = budget.reserve(
                            "replay_probe", est, round_num=t.round_num,
                            phase="negotiation", turn=t.turn,
                        )
                    except BudgetBlockedError:
                        terminated_reason = "budget_cap_reached"
                        break

                    uid = (
                        f"s{sample_index:02d}_{t.index:02d}_{t.player_id}"
                        f"_R{t.round_num}_t{t.turn}_{variant}"
                    )
                    start = time.time()
                    error_msg: str | None = None
                    error_type: str | None = None
                    text = ""
                    usage: dict[str, Any] = {"input_tokens": 0, "output_tokens": 0}
                    thinking_opts = _thinking_request_options(model, thinking_mode)
                    complete_kwargs: dict[str, Any] = dict(
                        system=system,
                        messages=[{"role": "user", "content": user}],
                        max_tokens=max_output_tokens,
                        temperature=DEFAULT_TEMPERATURE,
                    )
                    if thinking_opts is not None:
                        complete_kwargs["request_options"] = thinking_opts
                    try:
                        text, usage = adapter.complete(**complete_kwargs)
                    except AdapterError as e:
                        error_msg = str(e)[:200]
                        error_type = _classify_error(e)
                    except Exception as e:  # noqa: BLE001
                        error_msg = str(e)[:200]
                        error_type = "other"

                    elapsed_ms = (time.time() - start) * 1000

                    if error_msg is None:
                        cost = usage_cost(model, usage)
                        budget.settle(reservation, cost)
                        consecutive_errors[t.model_key] = 0
                    else:
                        cost = 0.0
                        budget.release(reservation)
                        consecutive_errors[t.model_key] = consecutive_errors.get(t.model_key, 0) + 1
                        if consecutive_errors[t.model_key] >= 3:
                            skipped_models.add(t.model_key)
                            print(
                                f"[replay_probe] {t.model_key} で3連続エラー。"
                                "以後このモデルの残りcallをskipします。",
                                file=sys.stderr,
                            )
                            time.sleep(rate_limit_cooldown)

                    (raw_dir / f"{uid}.txt").write_text(text)

                    if error_msg is None:
                        score = _score_response(text, t.player_id)
                    else:
                        score = {
                            "json_extracted": False, "parse_ok": False,
                            "parse_error_class": None, "action_type": None,
                            "is_contract_action": False,
                            "mentions_contract": 0, "mentions_type_b": 0,
                        }

                    record = {
                        "uid": uid,
                        "sample_index": sample_index,
                        "target_index": t.index,
                        "variant": variant,
                        "player_id": t.player_id,
                        "round_num": t.round_num,
                        "turn": t.turn,
                        "model_id": t.model_id,
                        "model_key": t.model_key,
                        "status": "ok" if error_msg is None else "error",
                        "api_ok": error_msg is None,
                        "error_type": error_type,
                        "error_msg": error_msg,
                        "usage": usage,
                        "input_tokens": usage.get("input_tokens", 0),
                        "output_tokens": usage.get("output_tokens", 0),
                        "finish_reason": usage.get("finish_reason"),
                        "truncated": usage.get("finish_reason") == "length",
                        "cost_usd": cost,
                        "reserved_usd": est,
                        "elapsed_ms": elapsed_ms,
                        **score,
                        "system_prompt_sha256": hashlib.sha256(system.encode()).hexdigest(),
                        "user_prompt_sha256": hashlib.sha256(user.encode()).hexdigest(),
                        "system_prompt_len": len(system),
                        "user_prompt_len": len(user),
                    }
                    if t.meta:
                        record["role"] = t.meta.get("role")
                        record["peer"] = t.meta.get("peer")
                        record["meta"] = t.meta
                    if cycle55_extra_fields:
                        record["thinking_mode"] = thinking_mode
                        record["thinking_applied"] = thinking_opts is not None
                        record["request_options"] = thinking_opts
                        record["reasoning_tokens"] = usage.get("reasoning_tokens", 0)
                        record["max_output_tokens"] = max_output_tokens
                    results_f.write(json.dumps(record, ensure_ascii=False) + "\n")
                    results_f.flush()
                    all_results.append(record)
                    time.sleep(sleep_seconds)
    except KeyboardInterrupt:
        terminated_reason = terminated_reason or "keyboard_interrupt"
    finally:
        results_f.close()
        _write_summary_and_csv(
            out_dir, all_results, budget, terminated_reason, variants,
            legacy_summary=legacy_summary,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Cycle 5/5.1 D1検証用replay probe（--dry-runが既定、--executeはコスト上限必須）",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="APIを呼ばず、置換後prompt/diffのみ出力する（既定）")
    parser.add_argument("--execute", action="store_true",
                        help="実際にAPIを呼ぶ（--max-cost-usdによる事前ゲート必須）")
    parser.add_argument("--preflight-only", action="store_true",
                        help="--execute指定時、見積とAPIキー確認のみ行いAPIを呼ばずに終了する")
    parser.add_argument("--max-cost-usd", type=float, default=0.20,
                        help="--execute時の事前見積上限（既定: $0.20）")
    parser.add_argument("--budget-mode", choices=["realistic", "worst_case", "hardened"],
                        default="hardened", help="事前見積の方式（既定: hardened＝最も保守的）")
    parser.add_argument("--samples", type=int, default=1,
                        help="1レコード×1variantあたりのサンプル数（既定: 1）")
    parser.add_argument("--variants", type=str, default="baseline,patched",
                        help="カンマ区切り。baseline / patched のいずれか、または両方")
    parser.add_argument("--max-output-tokens", type=int, default=2000,
                        help="0824本走と同じ既定値2000")
    parser.add_argument("--only", type=str, default=None,
                        help="対象レコードindexのカンマ区切り（例: 8,9）")
    parser.add_argument("--models", type=str, default=None,
                        help="対象model_idのカンマ区切り（部分実行・スモーク用）")
    parser.add_argument("--sleep-seconds", type=float, default=1.0,
                        help="各call間のインターバル秒")
    parser.add_argument("--rate-limit-cooldown", type=float, default=20.0,
                        help="同一モデルで3連続エラー時の追加待機秒")
    parser.add_argument("--resume", action="store_true",
                        help="既存out-dirのresults.jsonlを読み、未実施分だけ実行する")
    parser.add_argument("--out-dir", type=str, default=None,
                        help="出力先（既定: logs/replay_probe/<timestamp>/）")
    parser.add_argument("--targets-file", type=str, default=None,
                        help="Cycle 5.4: 対象手番を外部JSONから読み込む（既定: 内蔵27件選抜）")
    parser.add_argument("--legacy-summary", action="store_true",
                        help="Cycle 5.4で追加したrole分割/McNemarキーをsummaryへ含めない")
    parser.add_argument("--model-override", type=str, default=None,
                        help="Cycle 5.5: 全対象のmodel_keyを指定レジストリキー"
                             "（例: M1/H4/H6/TERRA）へ一括差し替える。"
                             "--only/--modelsフィルタ適用後・preflight前に適用。"
                             "--dry-run（既定含む）とは併用不可")
    parser.add_argument("--thinking", choices=["off", "medium"], default="off",
                        help="Cycle 5.5: providerごとのthinking/reasoning "
                             "request_optionsを有効化する（既定off＝完全no-op）")
    args = parser.parse_args()

    targets_file = Path(args.targets_file) if args.targets_file else None

    # P1対応: --dry-run --execute の同時指定を明示エラーにする
    # （旧実装はdry_run=Trueが既定のままexecuteだけ見ていたため、この組み
    #   合わせで無言でAPIを叩いてしまうバグがあった）。
    if args.dry_run and args.execute:
        parser.error("--dry-run と --execute は同時指定できません")
    if not args.dry_run and not args.execute:
        args.dry_run = True

    # Cycle 5.5: --model-override は --dry-run（既定含む）と併用不可。
    # dry-run経路に5.5コードが一切届かないことを構造的に保証する。
    if args.model_override and args.dry_run:
        parser.error("--model-override は --dry-run（既定含む）と併用できません（--executeが必須）")

    if args.out_dir:
        out_dir = Path(args.out_dir)
    else:
        ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        suffix = "_preflight" if args.preflight_only else ""
        out_dir = (
            Path(__file__).resolve().parent.parent
            / "logs" / "replay_probe" / f"{ts}{suffix}"
        )

    # P11対応: 出力先がlogs/llm/配下(読み取り専用ログ)に重ならないことを保証する
    llm_logs_root = (Path(__file__).resolve().parent.parent / "logs" / "llm").resolve()
    resolved_out = out_dir.resolve()
    if resolved_out == llm_logs_root or llm_logs_root in resolved_out.parents:
        print(
            f"[replay_probe] 出力先が logs/llm/ 配下です（読み取り専用ログ保護のため禁止）: "
            f"{resolved_out}",
            file=sys.stderr,
        )
        sys.exit(1)

    if args.dry_run:
        try:
            run_dry_run(out_dir, targets_file)
        except TargetsFileError as e:
            print(f"[replay_probe] --targets-file の検証に失敗しました: {e}", file=sys.stderr)
            sys.exit(1)
        return

    # --execute経路
    config = GameConfig.baseline_v1_s2(12)
    try:
        targets = _prepare_targets(config, targets_file)
    except TargetsFileError as e:
        print(f"[replay_probe] --targets-file の検証に失敗しました: {e}", file=sys.stderr)
        sys.exit(1)

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    for v in variants:
        if v not in ("baseline", "patched"):
            parser.error(f"不明なvariant: {v}（baseline/patchedのみ）")

    if args.only:
        wanted_idx = {int(x) for x in args.only.split(",")}
        targets = [t for t in targets if t.index in wanted_idx]
    if args.models:
        wanted_models = {m.strip() for m in args.models.split(",")}
        targets = [t for t in targets if t.model_id in wanted_models]

    if not targets:
        print("[replay_probe] 対象レコードが0件です（--only/--modelsの指定を確認）", file=sys.stderr)
        sys.exit(1)

    # Cycle 5.5: --only/--models フィルタ適用後・_preflight()の前に適用する
    # （preflightの単価・APIキー判定・budget.reserve()を実モデルに一致させるため）。
    if args.model_override:
        targets = _apply_model_override(targets, args.model_override)

    preflight = _preflight(
        targets, samples=args.samples, variants=variants, budget_mode=args.budget_mode,
        max_output_tokens=args.max_output_tokens, max_cost_usd=args.max_cost_usd,
        thinking_mode=args.thinking,
    )
    print(json.dumps(preflight, ensure_ascii=False, indent=2))

    if not preflight["within_budget"]:
        print(
            f"[replay_probe] 事前見積 ${preflight['estimated_total_cost_usd']:.4f} が"
            f" 上限 ${args.max_cost_usd:.4f} を超えるため実行を中止します"
            f"（budget_mode={args.budget_mode}）。",
            file=sys.stderr,
        )
        sys.exit(1)

    missing_keys = [k for k, present in preflight["api_key_present"].items() if not present]
    if missing_keys:
        print(f"[replay_probe] APIキー未設定の env_key があります: {missing_keys}", file=sys.stderr)
        sys.exit(1)

    if args.preflight_only:
        print("[replay_probe] --preflight-only: 見積・キー確認のみ完了。API呼び出しは行っていません。")
        return

    cycle55_extra_fields = bool(args.model_override) or args.thinking != "off"
    run_execute(
        targets, out_dir, samples=args.samples, variants=variants,
        budget_mode=args.budget_mode, max_output_tokens=args.max_output_tokens,
        max_cost_usd=args.max_cost_usd, sleep_seconds=args.sleep_seconds,
        rate_limit_cooldown=args.rate_limit_cooldown, resume=args.resume,
        legacy_summary=args.legacy_summary,
        thinking_mode=args.thinking, cycle55_extra_fields=cycle55_extra_fields,
    )


if __name__ == "__main__":
    main()
