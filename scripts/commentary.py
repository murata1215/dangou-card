#!/usr/bin/env python3
"""
実況台本生成スクリプト

完了済み試合ログからトレースを構築し、
ずんだもん(実況) × 四国めたん(解説) の掛け合い台本をAI生成する。

Usage:
    uv run python scripts/commentary.py logs/llm/trial_C_20260810_053055/
    uv run python scripts/commentary.py logs/llm/trial_C_20260810_053055/ --version v2
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

# プロジェクトルートをsys.pathに追加
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from dotenv import load_dotenv
load_dotenv()

from engine.commentary.trace import (
    GameTrace,
    SecretLayer,
    build_trace,
    check_banned_words,
    check_leakage,
)
from engine.commentary.prompts import (
    build_system_prompt,
    build_user_prompt,
)
from llm.models import get_model, estimate_cost
from llm.adapters import create_adapter

# ---------------------------------------------------------------------------
# 定数
# ---------------------------------------------------------------------------

COST_LIMIT = 1.0  # USD
MAX_RETRIES_TOTAL = 3  # 全体でのリトライ上限

# v1互換: インラインシステムプロンプト
SYSTEM_PROMPT_V1 = """\
あなたはゲーム実況台本の作家です。
「嘘八百万 談合カード」というAI同士の市場争奪・交渉ゲームの実況・解説の掛け合い台本を書きます。

## キャラクター

### ずんだもん(実況)
- 一人称「ぼく」、語尾は「〜のだ」「〜なのだ」
- 公開情報だけを見て驚き、騒ぎ、たまに怖がる。無邪気で感情豊か
- 【絶対厳守】ずんだもんは「秘匿情報」セクションの内容に一切言及してはならない。DM内容、プレイヤーの内心メモ、非公開の戦略には触れない。ずんだもんはそれらを知り得ない立場である

### 四国めたん(解説)
- 一人称「わたくし」、語尾は「〜ですわ」「〜ますの」「〜てよ」(お嬢様口調)
- 神視点で内心メモ・DM・嘘を種明かしする。上品に辛辣
- 不穏な展開の予告に「荒れますわよ、これは」を時折使う
- 内心メモを引用する際は「内心メモによると」と前置きし、原文の短い引用をする

## 出力規則
- JSON配列で出力すること。各要素: {"speaker": "zundamon" or "metan", "text": "セリフ"}
- 4〜10往復(8〜20要素)の掛け合い
- 数字(賞金額・残高・借金等)は入力データの実数値のみ使用し、絶対に創作しない
- 読み上げ可能な自然な文体(記号装飾・絵文字・アスタリスクなし)
- ラウンド末尾に次ラウンドへの引きを1行入れてもよい
- 必ずずんだもんから開始すること\
"""


# ---------------------------------------------------------------------------
# v1互換: ユーザープロンプト構築
# ---------------------------------------------------------------------------

def _format_yen(amount: int | float) -> str:
    """数値を円表示にフォーマット"""
    return f"{int(amount):,}円"


def _build_user_prompt_v1(
    packet_dict: dict,
    seat_map: dict[str, str],
    round_num: int,
    num_rounds: int,
) -> str:
    """v1互換: 1ラウンド分のユーザープロンプトを構築する"""
    pub = packet_dict["public"]
    sec = packet_dict["secret"]

    lines: list[str] = []
    lines.append(f"# ラウンド{round_num} / 全{num_rounds}ラウンド\n")

    lines.append("## 座席表")
    for pid, name in sorted(seat_map.items()):
        lines.append(f"- {pid}: {name}")
    lines.append("")

    lines.append("## 公開情報(ずんだもん・めたん共通)\n")

    if pub["markets"]:
        lines.append("### 今ラウンドの市場")
        for m in pub["markets"]:
            lines.append(
                f"- {m['market_id']}: 基本賞金{_format_yen(m['base_prize'])}"
                f" + 繰越{_format_yen(m.get('carryover', 0))}"
                f" = 賞金プール{_format_yen(m['prize_pool'])}"
            )
        lines.append("")

    if pub["broadcasts"]:
        lines.append("### 全体チャット(broadcast)")
        for bc in pub["broadcasts"]:
            name = seat_map.get(bc["player_id"], bc["player_id"])
            lines.append(f"- [{bc['player_id']}]{name} (T{bc.get('turn', '?')}): {bc['message']}")
        lines.append("")

    if pub["commits"]:
        lines.append("### コミット(Reveal)")
        for c in pub["commits"]:
            name = seat_map.get(c["player_id"], c["player_id"])
            lines.append(f"- {c['player_id']}({name}): {c['market_id']}に{c['rank']}を出した")
        lines.append("")

    if pub["market_results"]:
        lines.append("### 市場結果")
        for mr in pub["market_results"]:
            winner_names = [f"{w}({seat_map.get(w, w)})" for w in mr.get("winners", [])]
            lines.append(
                f"- {mr['market_id']}: 参加{mr['participants']}人,"
                f" 勝者={', '.join(winner_names)},"
                f" 1人あたり{_format_yen(mr.get('prize_per_winner', 0))},"
                f" プール{_format_yen(mr.get('total_pool', 0))}"
            )
        lines.append("")

    if pub["snapshots"]:
        lines.append("### 決算後の残高")
        for pid in sorted(pub["snapshots"].keys()):
            snap = pub["snapshots"][pid]
            name = seat_map.get(pid, pid)
            lines.append(f"- {pid}({name}): 現金{_format_yen(snap['cash'])}, FreeCash{_format_yen(snap['free_cash'])}")
        lines.append("")

    if pub["eliminations"]:
        lines.append("### 脱落イベント")
        for el in pub["eliminations"]:
            name = seat_map.get(el.get("player_id", ""), el.get("player_id", ""))
            reason = el.get("reason", "不明")
            lines.append(f"- {el.get('player_id')}({name}): {reason}")
        lines.append("")

    if pub["survival_checks"]:
        lines.append("### 生存判定(最終ラウンド)")
        for sc in pub["survival_checks"]:
            name = seat_map.get(sc.get("player_id", ""), sc.get("player_id", ""))
            result = sc.get("result", "")
            lines.append(
                f"- {sc.get('player_id')}({name}): {result}"
                f" (現金{_format_yen(sc.get('cash', 0))}, 借金{_format_yen(sc.get('debt', 0))})"
            )
        lines.append("")

    lines.append(f"### ラウンド情報")
    lines.append(f"- 生存者数: {pub['alive_count']}人")
    lines.append(f"- このラウンドの契約提案数: {pub['contracts_proposed']}件")

    if pub["highlights"]:
        lines.append("\n### ハイライト")
        for hl in pub["highlights"]:
            lines.append(f"- [{hl.get('type', '')}] {hl.get('label', '')}: {hl.get('detail', '')}")
    lines.append("")

    lines.append("## 秘匿情報(めたん専用 — ずんだもんは絶対に言及禁止)\n")

    if sec["strategies"]:
        lines.append("### プレイヤーの内心メモ")
        for st in sec["strategies"]:
            name = seat_map.get(st["player_id"], st["player_id"])
            emotion_str = f" [感情: {st['emotion']}]" if st.get("emotion") else ""
            lines.append(f"- {st['player_id']}({name}){emotion_str}: {st['text']}")
        lines.append("")

    if sec["dms"]:
        lines.append("### DM(非公開メッセージ)")
        for dm in sec["dms"]:
            s_name = seat_map.get(dm["sender"], dm["sender"])
            r_name = seat_map.get(dm["recipient"], dm["recipient"])
            lines.append(
                f"- {dm['sender']}({s_name}) → {dm['recipient']}({r_name})"
                f" (T{dm.get('turn', '?')}): {dm['message']}"
            )
        lines.append("")

    if not sec["strategies"] and not sec["dms"]:
        lines.append("(このラウンドには秘匿情報がありません)\n")

    lines.append("台本を生成してください。")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# 台本パーサー
# ---------------------------------------------------------------------------

def _parse_script_response(response_text: str) -> list[dict]:
    """AIレスポンスからJSON配列を抽出する"""
    m = re.search(r"```(?:json)?\s*\n?(.*?)```", response_text, re.DOTALL)
    text = m.group(1).strip() if m else response_text.strip()

    idx = text.find("[")
    if idx < 0:
        return []
    text = text[idx:]

    try:
        parsed = json.loads(text)
        if isinstance(parsed, list):
            return parsed
    except json.JSONDecodeError:
        for suffix in ["]", "}]", '"}]', '"}]']:
            try:
                parsed = json.loads(text + suffix)
                if isinstance(parsed, list):
                    return parsed
            except json.JSONDecodeError:
                continue
    return []


def _is_truncated(response_text: str, entries: list[dict]) -> bool:
    """レスポンスが途中で切れているか判定する"""
    if entries:
        return False  # パースできたなら尻切れではない
    text = response_text.strip()
    if not text:
        return True
    # JSON配列の閉じ括弧で終わっていなければ尻切れの可能性
    return not text.endswith("]")


# ---------------------------------------------------------------------------
# 台本生成
# ---------------------------------------------------------------------------

def generate_commentary(
    trace: GameTrace,
    dry_run: bool = False,
    version: str = "v2",
) -> tuple[list[dict], dict]:
    """
    トレースから台本を生成する。

    Args:
        trace: GameTrace
        dry_run: API呼び出しなしでダミー生成
        version: "v1" or "v2"

    Returns:
        (script_entries, meta)
    """
    model = get_model("L1")  # claude-haiku-4-5
    adapter = create_adapter(model)

    # v1/v2でプロンプト構築を切り替え
    if version == "v2":
        system_prompt = build_system_prompt()
        prompt_builder = build_user_prompt
        max_tokens = 4000
    else:
        system_prompt = SYSTEM_PROMPT_V1
        prompt_builder = _build_user_prompt_v1
        max_tokens = 2000

    all_entries: list[dict] = []
    total_cost = 0.0
    total_calls = 0
    total_input_tokens = 0
    total_output_tokens = 0
    total_retries = 0
    all_warnings: list[str] = []
    banned_word_violations = 0
    num_rounds = trace.config.get("num_rounds", 12)

    for packet in trace.packets:
        if total_cost >= COST_LIMIT:
            all_warnings.append(f"コスト上限${COST_LIMIT}に到達、R{packet.round_num}以降を中断")
            break

        user_prompt = prompt_builder(
            packet.to_dict(),
            trace.seat_map,
            packet.round_num,
            num_rounds,
        )

        if dry_run:
            entries = [
                {"speaker": "zundamon", "text": f"ラウンド{packet.round_num}なのだ!"},
                {"speaker": "metan", "text": f"ラウンド{packet.round_num}ですわ。"},
            ]
        else:
            # --- API呼び出し (リトライ付き) ---
            entries, call_cost, call_usage = _generate_round(
                adapter, model, system_prompt, user_prompt,
                max_tokens, packet, version,
                all_warnings, total_retries,
            )
            total_cost += call_cost
            total_calls += call_usage["calls"]
            total_retries += call_usage["retries"]
            total_input_tokens += call_usage["input_tokens"]
            total_output_tokens += call_usage["output_tokens"]

        # エントリにround情報を付加
        for entry in entries:
            all_entries.append({
                "round": packet.round_num,
                "speaker": entry.get("speaker", "unknown"),
                "text": entry.get("text", ""),
                "refs": entry.get("refs"),
            })

        # 秘匿情報混入チェック
        zundamon_lines = [
            e.get("text", "") for e in entries
            if e.get("speaker") == "zundamon"
        ]
        leak_warnings = check_leakage(zundamon_lines, packet.secret)
        for w in leak_warnings:
            all_warnings.append(f"R{packet.round_num}: {w}")

        print(f"  R{packet.round_num:02d}: {len(entries)}セリフ, "
              f"コスト累計${total_cost:.4f}")

    meta = {
        "version": version,
        "model": model.model_id,
        "model_name": model.name,
        "total_calls": total_calls,
        "total_retries": total_retries,
        "total_cost_usd": round(total_cost, 6),
        "total_input_tokens": total_input_tokens,
        "total_output_tokens": total_output_tokens,
        "banned_word_violations": banned_word_violations,
        "warnings": all_warnings,
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }

    return all_entries, meta


def _generate_round(
    adapter,
    model,
    system_prompt: str,
    user_prompt: str,
    max_tokens: int,
    packet,
    version: str,
    all_warnings: list[str],
    total_retries_so_far: int,
) -> tuple[list[dict], float, dict]:
    """
    1ラウンド分の台本を生成する(リトライ付き)。

    Returns:
        (entries, cost, usage_info)
    """
    cost = 0.0
    calls = 0
    retries = 0
    input_tokens = 0
    output_tokens = 0

    current_prompt = user_prompt

    for attempt in range(2):  # 最大2回(初回 + リトライ1回)
        response_text, usage = adapter.complete(
            system=system_prompt,
            messages=[{"role": "user", "content": current_prompt}],
            max_tokens=max_tokens,
            temperature=0.8,
        )
        call_cost = estimate_cost(
            model,
            usage.get("input_tokens", 0),
            usage.get("output_tokens", 0),
        )
        cost += call_cost
        calls += 1
        input_tokens += usage.get("input_tokens", 0)
        output_tokens += usage.get("output_tokens", 0)

        entries = _parse_script_response(response_text)

        # 尻切れチェック
        if _is_truncated(response_text, entries):
            if attempt == 0 and total_retries_so_far + retries < MAX_RETRIES_TOTAL:
                all_warnings.append(f"R{packet.round_num}: 尻切れ検出、再生成")
                retries += 1
                continue
            else:
                all_warnings.append(f"R{packet.round_num}: 尻切れ(リトライ上限)")
                entries = [
                    {"speaker": "zundamon", "text": f"ラウンド{packet.round_num}、何かが起きたのだ!"},
                    {"speaker": "metan", "text": "解析に問題がありましたわ。"},
                ]
                break

        if not entries:
            all_warnings.append(f"R{packet.round_num}: レスポンスのパースに失敗")
            entries = [
                {"speaker": "zundamon", "text": f"ラウンド{packet.round_num}、何かが起きたのだ!"},
                {"speaker": "metan", "text": "解析に問題がありましたわ。"},
            ]
            break

        # v2: 禁止語チェック
        if version == "v2":
            zundamon_lines = [
                e.get("text", "") for e in entries
                if e.get("speaker") == "zundamon"
            ]
            violations = check_banned_words(zundamon_lines)
            if violations:
                if attempt == 0 and total_retries_so_far + retries < MAX_RETRIES_TOTAL:
                    all_warnings.append(
                        f"R{packet.round_num}: 禁止語違反({', '.join(violations)})、再生成"
                    )
                    retries += 1
                    # 是正指示を追加して再生成
                    current_prompt = (
                        user_prompt
                        + f"\n\n【再生成指示】前回の生成でずんだもんが秘匿情報に言及しました。"
                        f"以下の語をずんだもんのセリフに含めないでください: {', '.join(violations)}。"
                        f"ずんだもんは公開情報とファクトシートのみ参照可能です。"
                    )
                    continue
                else:
                    all_warnings.append(
                        f"R{packet.round_num}: 禁止語違反({', '.join(violations)})残存(リトライ上限)"
                    )
        break

    usage_info = {
        "calls": calls,
        "retries": retries,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }
    return entries, cost, usage_info


# ---------------------------------------------------------------------------
# 出力
# ---------------------------------------------------------------------------

def save_script(
    entries: list[dict],
    meta: dict,
    output_dir: Path,
    seat_map: dict[str, str],
) -> tuple[Path, Path]:
    """台本をJSONL + Markdown形式で保存する"""
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- script.jsonl ---
    jsonl_path = output_dir / "script.jsonl"
    with open(jsonl_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    # --- script.md ---
    md_path = output_dir / "script.md"
    speaker_names = {"zundamon": "ずんだもん", "metan": "めたん"}

    with open(md_path, "w", encoding="utf-8") as f:
        version = meta.get("version", "v1")
        f.write(f"# 談合カード 実況台本 ({version})\n\n")

        # 座席表
        f.write("## 出場者\n\n")
        for pid, name in sorted(seat_map.items()):
            f.write(f"- {pid}: {name}\n")
        f.write("\n---\n\n")

        # ラウンドごとのセリフ
        current_round = None
        for entry in entries:
            rn = entry.get("round", 0)
            if rn != current_round:
                if current_round is not None:
                    f.write("\n---\n\n")
                current_round = rn
                f.write(f"## ラウンド{rn}\n\n")

            speaker = entry.get("speaker", "unknown")
            display = speaker_names.get(speaker, speaker)
            text = entry.get("text", "")
            f.write(f"**{display}**: {text}\n\n")

        # 生成メタ
        f.write("\n---\n\n")
        f.write("## 生成情報\n\n")
        f.write(f"- バージョン: {meta.get('version', 'N/A')}\n")
        f.write(f"- モデル: {meta.get('model', 'N/A')} ({meta.get('model_name', '')})\n")
        f.write(f"- APIコール数: {meta.get('total_calls', 0)}\n")
        f.write(f"- リトライ数: {meta.get('total_retries', 0)}\n")
        f.write(f"- 総コスト: ${meta.get('total_cost_usd', 0):.4f}\n")
        f.write(f"- 入力トークン合計: {meta.get('total_input_tokens', 0):,}\n")
        f.write(f"- 出力トークン合計: {meta.get('total_output_tokens', 0):,}\n")
        f.write(f"- 生成日時: {meta.get('generated_at', 'N/A')}\n")

        if meta.get("warnings"):
            f.write("\n### 警告\n\n")
            for w in meta["warnings"]:
                f.write(f"- {w}\n")

    return jsonl_path, md_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="実況台本生成")
    parser.add_argument(
        "game_dir",
        type=str,
        help="試合ディレクトリ (例: logs/llm/trial_C_20260810_053055/)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="API呼び出しなしでダミー台本を生成",
    )
    parser.add_argument(
        "--version",
        choices=["v1", "v2"],
        default="v2",
        help="生成バージョン (デフォルト: v2)",
    )
    args = parser.parse_args()

    game_dir = Path(args.game_dir)
    if not game_dir.exists():
        print(f"Error: {game_dir} does not exist", file=sys.stderr)
        sys.exit(1)

    version = args.version

    print(f"=== 実況台本生成 ({version}) ===")
    print(f"試合ディレクトリ: {game_dir}")
    print()

    # トレース構築
    print("トレース構築中...")
    trace = build_trace(game_dir)
    print(f"  座席数: {len(trace.seat_map)}")
    print(f"  ラウンド数: {len(trace.packets)}")
    print()

    # 台本生成
    mode = "ドライラン" if args.dry_run else "実生成"
    print(f"台本生成中 ({mode}, {version})...")
    entries, meta = generate_commentary(trace, dry_run=args.dry_run, version=version)
    print()

    # 保存先の決定
    if version == "v2":
        output_dir = game_dir / "commentary" / "v2"
    else:
        output_dir = game_dir / "commentary"
    jsonl_path, md_path = save_script(entries, meta, output_dir, trace.seat_map)

    print(f"=== 完了 ===")
    print(f"  JSONL: {jsonl_path}")
    print(f"  MD:    {md_path}")
    print(f"  コール数: {meta['total_calls']}")
    print(f"  リトライ: {meta['total_retries']}")
    print(f"  コスト: ${meta['total_cost_usd']:.4f}")
    if meta["warnings"]:
        print(f"  警告: {len(meta['warnings'])}件")
        for w in meta["warnings"]:
            print(f"    - {w}")


if __name__ == "__main__":
    main()
