"""
プロンプト組立モジュール

仕様書v0.5からルールを要約したシステムプロンプトと、
毎ターンの観測情報を組み立てるユーザープロンプトを生成する。
匿名化: プロンプトにモデル名を一切含めない。
"""

import math
from typing import Any
from engine.models import PlayerState, Market, CardRank
from engine.config import GameConfig
from engine.player import apply_interest, compute_mandatory_repayment

# --- 契約義務の可視化ヘルパー ---
# §7.3の「破産を計算ミスでなく戦略判断にする」思想を契約側にも適用。
# 署名済み未履行義務を毎プロンプトで提示し、帳簿ミス起因の自爆を防ぐ。
# engine判定ロジック（settlement/autocommit/contracts）には一切影響しない。

_OB_TYPE_LABELS = {
    "type_a_payment": "型A金銭支払い",
    "type_b_market": "型B市場指定",
    "type_b_card": "型Bカード指定",
    "type_b_no_market": "型B不参加指定",
}


def _format_obligation_detail(ob: dict[str, Any]) -> tuple[str, str]:
    """義務dictから (種別ラベル, 詳細文字列) を返す（描画箇所の共通化）

    以前は _render_obligations_block() と contracts_pending 描画の2箇所に
    全く同じロジックがコピペされており、card/card_rankフォールバックの片方
    だけ直す事故が起きうる形になっていた。1箇所に集約する。
    """
    label = _OB_TYPE_LABELS.get(ob["ob_type"], ob["ob_type"])
    d = ob.get("details") or {}
    if "amount" in d:
        return label, f'{d["amount"] // 10_000}万円'
    if "market_id" in d:
        return label, d["market_id"]
    if "card_rank" in d:
        return label, d["card_rank"]
    if "card" in d:
        return label, d["card"]  # フォールバック: 正規化前の古いデータとの互換
    return label, ""


def _obligation_conflict_warnings(rn: int, obs: list[dict[str, Any]]) -> list[str]:
    """
    同一ラウンド内の型B義務どうしの構造的矛盾を検出する（事実のみ・助言なし）

    1ラウンドに選べる市場は1つ・使用できるカードは1枚という制約から、
    以下の組合せは必ずどちらかが違反になり脱落する:
    - type_b_card が2件以上（異なるカードを同時に出せない。同じカードの
      重複指定は矛盾ではないため対象外）
    - type_b_market が2件以上で market_id が異なる（1ラウンドに参加できる
      市場は1つ。同じ市場の重複指定は矛盾ではない）
    - type_b_market X と type_b_no_market X が同時に存在（参加指定と
      不参加指定が同一市場について矛盾）

    P0-4: 従来は type_b_card の2件以上のみ検出していた
    （実run trial_C_l12_r12_20260822 の P11 R3 はこの型で検出済みだったが、
    他の矛盾型は未検出のままだった）。
    """
    warnings: list[str] = []

    card_obs = [o for o in obs if o["ob_type"] == "type_b_card"]
    if len(card_obs) >= 2:
        warnings.append(
            f"    R{rn}: カード使用義務が{len(card_obs)}件あります。"
            f"1ラウンドに提出できるカードは1枚です。"
        )

    market_obs = [o for o in obs if o["ob_type"] == "type_b_market"]
    market_ids = {o["details"].get("market_id") for o in market_obs} - {None}
    if len(market_ids) >= 2:
        warnings.append(
            f"    R{rn}: 市場指定義務が異なる市場で{len(market_obs)}件あります"
            f"（{', '.join(sorted(market_ids))}）。"
            f"1ラウンドに参加できる市場は1つです。"
        )

    no_market_obs = [o for o in obs if o["ob_type"] == "type_b_no_market"]
    no_market_ids = {o["details"].get("market_id") for o in no_market_obs} - {None}
    overlap = market_ids & no_market_ids
    if overlap:
        warnings.append(
            f"    R{rn}: 同一市場（{', '.join(sorted(overlap))}）について、"
            f"参加を指定する義務と不参加を指定する義務が両方あります。"
            f"1ラウンドの同じ市場に対して成立するのは参加・不参加のどちらか一方です。"
        )

    return warnings


def _render_obligation_round_group(
    rn: int,
    obs: list[dict[str, Any]],
    current_round: int,
    hand_rank_names: set[str] | None,
) -> list[str]:
    """
    1ラウンド分の義務グループを描画する（見出し＋各義務＋矛盾警告）

    _render_obligations_block（署名済み義務の現況表示）と、
    build_negotiation_prompt の contracts_pending 描画（P0-5: 署名前プレビュー、
    既存義務と合流した結果を仮描画）の両方から呼ばれる共通ロジック。
    事実の提示のみ（署名拒否・助言はしない）。

    Args:
        rn: 対象ラウンド番号
        obs: そのラウンドの義務dictリスト（contract_id, obligor, counterparty,
            ob_type, round_num, details を持つこと）
        current_round: 現在のラウンド番号（強調表示の判定用）
        hand_rank_names: 現在の手札のランク名集合。Noneなら手札突合を行わない
            （P0-4: 型Bカード指定義務が現在の手札にあるか事実注記を付す）
    """
    marker = " ⬅ 今ラウンド" if rn == current_round else ""
    lines = [f"  **R{rn}**{marker}:"]

    for ob in obs:
        ob_type_label, details_str = _format_obligation_detail(ob)
        hand_note = ""
        if ob["ob_type"] == "type_b_card" and hand_rank_names is not None:
            d = ob.get("details") or {}
            rank = d.get("card_rank") or d.get("card")
            if rank is not None:
                hand_note = (
                    "（現在の手札にあります）" if rank in hand_rank_names
                    else "（現在の手札にありません）"
                )
        lines.append(
            f'    - [{ob["contract_id"]}] {ob_type_label} {details_str}'
            f' （相手: {ob["counterparty"]}）{hand_note}'
        )

    lines.extend(_obligation_conflict_warnings(rn, obs))
    return lines


def _render_obligations_block(
    player_id: str,
    visible_state: dict[str, Any],
    round_num: int,
    hand_rank_names: set[str] | None = None,
) -> list[str]:
    """
    署名済み未履行の契約義務一覧を描画する（毎プロンプト注入）

    全エージェント向けプロンプトビルダー（negotiation / commit / reflection /
    double_up）から呼ばれ、帳簿ミス起因の契約違反脱落を防止する情報を提示する。

    - ラウンドでグルーピング、現ラウンドを「⬅ 今ラウンド」で強調
    - 同一ラウンド内の型B義務の構造的矛盾を検出（_obligation_conflict_warnings）
    - hand_rank_names を渡すと、type_b_card 義務が現在の手札にあるか事実注記
      （P0-4: 実run trial_C_l12_r12_20260822 の P09 R6 — 手札に無いカードを
      要求する義務に気づけなかった事故の再発防止）
    - 義務ゼロなら空リスト（トークン節約）

    Args:
        player_id: 対象プレイヤーID（ログ用、現時点では未使用）
        visible_state: _build_visible_state() の出力（my_obligations キーを参照）
        round_num: 現在のラウンド番号
        hand_rank_names: 現在の手札のランク名集合（省略時は手札突合をしない）

    Returns:
        プロンプトに追加する行のリスト（義務ゼロなら空リスト）
    """
    obligations = visible_state.get("my_obligations", [])
    if not obligations:
        return []

    lines: list[str] = ["\n## あなたの契約義務（署名済み・未履行）"]

    # ラウンドでグルーピング
    by_round: dict[int, list[dict]] = {}
    for ob in obligations:
        by_round.setdefault(ob["round_num"], []).append(ob)

    for rn in sorted(by_round.keys()):
        lines.extend(_render_obligation_round_group(
            rn, by_round[rn], round_num, hand_rank_names,
        ))

    return lines


# --- 自分が当事者の契約の可視化ヘルパー（台帳） ---
# LLMは1-shot呼出しで会話履歴を持たず、契約の内容を保持する手段が自由記述メモしか
# 無い。メモが劣化すると「activeだが内容不明の契約」が生まれ、存在しない義務への
# 警戒でアクション枠を浪費し、逆に本物の義務を忘れれば型B違反=即脱落する
# （実run trial_C_l12_r12_20260822: P03がC_90159021を「内容は忘れたが違反しない
# よう注意」とR12まで警戒し続けたが、実際は R6限定・義務者はP09のみでP09は
# R6に脱落済みだった）。
# _render_obligations_block（TODO: 今R以降に自分が履行すべき義務）とは役割が違う:
# こちらは「台帳」＝過去分・受益者側も含め、自分が当事者の契約すべての現況を出す。
# engine判定ロジックには一切影響しない — プロンプトへの情報提示のみ。

# --- Finance（利息計上→強制最低返済）の可視化ヘルパー（P0-1） ---
# 実run trial_C_l12_r12_20260822 では、強制最低返済の実額が1,407件のプロンプト
# 中1件も提示されておらず（P06 R9: 必要114,339円/現金84,063円で脱落、
# P01 R11: 必要649,587円/現金350,854円で脱落）、いずれも直前まで自分の資金が
# 足りるかどうかを数値で確認する手段が無かった。
# engine.finance / engine.player と完全に同じ計算（apply_interest,
# compute_mandatory_repayment, 除数 num_rounds - forecast_round + 1）を再利用し、
# 二重実装しない。助言（すべき/危険/安全な、等）は書かない。事実の提示のみ。

def _compute_finance_forecast(
    debt_balance: int,
    cash: int,
    forecast_round: int,
    config: GameConfig,
) -> dict[str, int]:
    """
    Financeフェイズ（利息計上→強制最低返済）の結果を副作用なしに事前計算する

    engine/finance.py の Step1（利息計上）・Step2（強制最低返済）と同一の
    ロジック・同一の除数定義 `num_rounds - forecast_round + 1` を用いる。

    Args:
        debt_balance: 利息計上前の借金残高
        cash: Finance実行時点で見込まれる現金
        forecast_round: Financeが実行される（実行された）ラウンド番号
        config: ゲーム設定

    Returns:
        {"interest": 利息額（円）,
         "debt_after_interest": 利息計上後の借金残高（円）,
         "divisor": 強制最低返済の除数（このラウンドを含む残りラウンド数+k）,
         "mandatory_repay": 強制最低返済額（円。mandatory_repay_enabled=Falseなら0）,
         "cash_after_repay": 強制最低返済差引後の現金見込み（円。マイナスもあり得る
             ＝現金不足でMANDATORY_REPAY_FAILEDになることを示す）}
    """
    # apply_interest は PlayerState を要求するため、計算専用の最小限のダミーを使う
    # （engine側のロジックを1箇所も複製しないための橋渡し）。
    dummy = PlayerState(
        player_id="_finance_forecast", cash=cash, debt_balance=debt_balance,
        initial_loan=0,
    )
    after_interest = apply_interest(dummy, config.interest_rate)
    interest = after_interest.debt_balance - debt_balance
    divisor = config.num_rounds - forecast_round + 1

    mandatory_repay = 0
    if config.mandatory_repay_enabled:
        mandatory_repay = compute_mandatory_repayment(
            after_interest.debt_balance, divisor, config.mandatory_repay_k,
        )

    return {
        "interest": interest,
        "debt_after_interest": after_interest.debt_balance,
        "divisor": divisor,
        "mandatory_repay": mandatory_repay,
        "cash_after_repay": cash - mandatory_repay,
    }


def _render_finance_block(
    cash: int,
    debt_balance: int,
    forecast_round: int,
    config: GameConfig,
    *,
    timing_note: str = "",
    entry_fee_note: str = "",
) -> list[str]:
    """
    Finance（利息計上→強制最低返済）の確定計算を提示する（P0-1）

    negotiation / commit / reflection / double_up の4プロンプトへ注入する。
    表示は事実の数値のみ（推奨・危険等の評価語は書かない）。

    Args:
        cash: Finance実行時点で見込まれる現金（呼び出し側の文脈に依存。
            例: commit/negotiationでは「現時点（今R決着前）」の現金）
        debt_balance: 利息計上前の借金残高
        forecast_round: Financeが実行される（実行された）ラウンド番号
        config: ゲーム設定
        timing_note: 見出しに付す注記（いつ・何の後に実行されるかの事実）
        entry_fee_note: Entry Feeに関する注記（P1-1。空文字なら非表示）
    """
    f = _compute_finance_forecast(debt_balance, cash, forecast_round, config)
    lines: list[str] = [f"\n## Finance（R{forecast_round}）の確定計算{timing_note}"]
    lines.append(f"  借金残高（利息計上前）: {debt_balance}円（{debt_balance // 10_000}万円）")
    lines.append(
        f"  利息（{config.interest_rate * 100:.1f}%・端数切り上げ）: +{f['interest']}円"
    )
    lines.append(
        f"  利息計上後の借金残高: {f['debt_after_interest']}円"
        f"（{f['debt_after_interest'] // 10_000}万円）"
    )
    if config.mandatory_repay_enabled:
        lines.append(
            f"  除数（このラウンドを含む残りラウンド数{config.num_rounds - forecast_round + 1}"
            f" + k={config.mandatory_repay_k}）: {f['divisor']}"
        )
        lines.append(
            f"  強制最低返済額 = ceil({f['debt_after_interest']}円 ÷ {f['divisor']}) "
            f"= {f['mandatory_repay']}円（{f['mandatory_repay'] // 10_000}万円）"
        )
        lines.append(
            f"  差引後の現金見込み: {cash}円 − {f['mandatory_repay']}円 "
            f"= {f['cash_after_repay']}円（{f['cash_after_repay'] // 10_000}万円）"
        )
        if f["cash_after_repay"] < 0:
            lines.append(
                f"    強制最低返済額との差額: {f['cash_after_repay']}円"
            )
    if entry_fee_note:
        lines.append(f"  {entry_fee_note}")
    return lines


_OB_STATUS_LABELS = {
    "fulfilled": "履行済",
    "expired": "失効",
    "past": "期限経過（監査済み・発火しません）",
    "due": "**今ラウンドが期限** ⬅",
    "upcoming": "未到来",
    "cancelled": "解除により消滅（発火しません）",
}


def _render_my_contracts_block(
    visible_state: dict[str, Any], round_num: int,
) -> list[str]:
    """自分が当事者である成立済み契約の全容を描画する（毎プロンプト注入）

    Args:
        visible_state: _build_visible_state() の出力（my_contracts キーを参照）
        round_num: 現在のラウンド番号（現時点では未使用、シグネチャ統一のため保持）

    Returns:
        プロンプトに追加する行のリスト（契約ゼロなら空リスト）
    """
    contracts = visible_state.get("my_contracts") or []
    if not contracts:
        return []

    lines: list[str] = ["\n## あなたが当事者の正式契約（現在の状態・これが正本です）"]
    for c in contracts:
        dead = c.get("eliminated_parties") or []
        cancelled_round = c.get("cancelled_round")
        head = (f"\n### [{c['contract_id']}] 当事者: {', '.join(c['parties'])}"
                f"（R{c['round_created']}成立）")
        if cancelled_round is not None:
            head += f" 🚫 解除済み（R{cancelled_round}）"
        if dead:
            head += f" ⚠ {', '.join(dead)}が脱落済み"
        lines.append(head)
        live = 0
        for ob in c["obligations"]:
            label, detail = _format_obligation_detail(ob)
            st = ob["ob_status"]
            lines.append(
                f'  - {ob["obligor"]} → {ob["counterparty"]}: {label}'
                f'{" " + detail if detail else ""} / R{ob["round_num"]}期限'
                f' … {_OB_STATUS_LABELS.get(st, st)}'
            )
            if st in ("due", "upcoming"):
                live += 1
        if cancelled_round is not None:
            lines.append(
                f"  → この契約はR{cancelled_round}に全当事者合意で解除されました"
                "（残義務は発火しません）"
            )
        elif live == 0:
            lines.append(
                "  → この契約に残っている義務はありません"
                "（消化・失効済み。解除交渉も警戒も不要です）"
            )
        else:
            requested = c.get("cancel_requested_by") or []
            if requested:
                alive_parties = [p for p in c["parties"] if p not in dead]
                pending = [p for p in alive_parties if p not in requested]
                if pending:
                    cancel_example = (
                        '{"type": "contract_cancel", "contract_id": "'
                        f'{c["contract_id"]}"}}'
                    )
                    lines.append(
                        f'  ⏳ 解除同意 {len(requested)}/{len(alive_parties)}'
                        f'（{", ".join(pending)} が未同意。全員が {cancel_example}'
                        ' を出すまで契約は有効です）'
                    )
    lines.append(
        "\n  ※上の一覧が契約に関する唯一の正しい現状です。"
        "あなたのメモの記述と食い違う場合は必ずこちらを優先してください。"
    )
    return lines


# --- AUTO COMMITの本人向け事実通知ヘルパー ---
# §1.5監査（trial_C_l12_r12_20260822）で、AUTO COMMITの4件全てが
# 「本人が指定しようとしたカードが、その時点の手札に存在しなかった」ことに
# 起因すると確認された。本ブロックは「何を指定したか」「なぜ却下されたか」
# 「システムが実際に何を提出したか」という事実のみを通知する（助言・評価は書かない）。
# エンジンのAUTO選択アルゴリズム（engine/autocommit.py）には一切影響しない。


def _render_auto_commit_block(visible_state: dict[str, Any]) -> list[str]:
    """自分の過去AUTO COMMIT履歴を事実のみで描画する（毎プロンプト注入・本人のみ）

    Args:
        visible_state: _build_visible_state() の出力（my_auto_commits キーを参照。
            秘匿情報のため for_player_id が本人と一致する場合のみ埋まっている）
    """
    history = visible_state.get("my_auto_commits") or []
    if not history:
        return []

    lines: list[str] = ["\n## 自動代行コミット（AUTO COMMIT）の記録"]
    for h in history:
        req_market = h.get("requested_market_id")
        req_card = h.get("requested_card_rank")
        if req_market or req_card:
            requested_desc = f"「{req_market or '不明'} / {req_card or '不明'}」"
        else:
            requested_desc = "取得できませんでした（コミットが有効な形式で得られませんでした）"
        lines.append(f"  R{h.get('round', '?')}: あなたが指定したのは{requested_desc}でした。")
        reason = h.get("reason")
        if reason:
            lines.append(f"      却下理由: {reason}")
        actual_market = h.get("actual_market_id")
        actual_card = h.get("actual_card")
        if actual_market and actual_card:
            lines.append(f"      システムが提出したのは「{actual_market} / {actual_card}」です。")
        else:
            lines.append("      合法な代替コミットが存在せず、契約違反として脱落しました。")
    lines.append(
        "  ※上の記録が実際に提出された内容です。あなたのメモと食い違う場合は"
        "こちらが正しい現状です。"
    )
    return lines


# --- 市場賞金内訳・前ラウンド結果の可視化ヘルパー ---
# 2026-08-17: R3流札→R4繰越で賞金が2倍に見えたが、繰越の事実が
# プレイヤーに一切周知されていなかった問題（§8.1公開情報の欠落）の是正。
# エンジン側の賞金計算・判定ロジックには一切影響しない、表示のみの追加。


def _render_market_line(
    market_id: str, prize_pool: int, base_prize: int, carryover: int,
) -> str:
    """市場1行を描画する（carryover>0のときだけ内訳を括弧で付記）"""
    line = f"  {market_id}: 賞金 {prize_pool // 10_000}万円"
    if carryover:
        line += f"（基本{base_prize // 10_000}万 + 前R繰越{carryover // 10_000}万）"
    return line


def _render_last_round_results(
    visible_state: dict[str, Any], title: str | None = None,
) -> list[str]:
    """
    前ラウンドの市場決着結果を描画する（毎プロンプト注入）

    §8.1公開情報（各市場の参加者・使用カード・決着後情報、勝者と獲得額）のうち、
    従来プロンプトに一切含まれていなかった「前ラウンドの結果」を補う。
    R1（前ラウンドが存在しない）は visible_state["last_round_results"] が
    None のため何も描画しない。

    参加者の描画は3段フォールバック:
      1. commits（player_id + card_rank）があればID+カードを列挙（§8.1完全対応）
      2. 無ければ participants（IDのみ）を列挙
      3. どちらも無ければ人数のみ（後方互換: 旧形式のdictを渡すテスト等）

    Args:
        title: セクション見出しに使うラベル（省略時は "前ラウンド（R{n}）の結果"）。
            reflectionプロンプトから「今ラウンドの結果」として使い回すために追加。
    """
    lrr = visible_state.get("last_round_results")
    if not lrr:
        return []

    heading = title or f"前ラウンド（R{lrr['round']}）の結果"
    lines: list[str] = [f"\n## {heading}"]
    for m in lrr.get("markets", []):
        mid = m.get("market_id", "?")
        commits = m.get("commits")
        participants = m.get("participants", [])
        if not participants:
            pool_man = m.get("total_pool", 0) // 10_000
            lines.append(
                f"  {mid}: 参加0人 → 不成立。賞金{pool_man}万円は今ラウンドの{mid}へ繰越"
            )
            continue
        winners = m.get("winners", [])
        per_man = m.get("prize_per_winner", 0) // 10_000
        surge_tag = "【高騰×2】" if m.get("surged") else ""
        if commits:
            entrants = ", ".join(
                f"{c.get('player_id', '?')}[{c.get('card_rank', '?')}]"
                + ("★" if c.get("player_id") in winners else "")
                # A-5: AUTO COMMITだった事実を全員に公開する。
                # 「約束を破った」のではなく「システムが代行した」と判別できるように
                # する（理由・本人が指定しようとした値は非公開のまま。§8.1準拠）。
                + ("【AUTO COMMIT】" if c.get("auto") else "")
                for c in commits
            )
        else:
            entrants = f"参加{len(participants)}人"
        lines.append(
            f"  {mid}: {entrants} → 勝者 {', '.join(winners)}"
            f"（各{per_man}万円）{surge_tag}"
        )
    return lines


# --- 引き継ぎメモリ（Handover Memory）の可視化ヘルパー ---
# LLMは1-shot呼出しで会話履歴を持たず、チャット・戦略メモは毎ラウンド消える。
# 前ラウンドから引き継げるのは、当人が書き残した自由記述メモ1枚のみ。


def _render_memory_block(memory: str | None, stale_warning: bool = False) -> list[str]:
    """
    前ラウンドから引き継いだメモを描画する（交渉・コミット両プロンプトの共通ヘルパー）

    渡すのは常に最新の1枚のみ（累積しない）。R1やメモ未執筆時は何も描画しない。

    stale_warning: True の場合、メモが過去の自己記述であり脱落・契約失効等の
    その後の変化を反映していない旨を注記する（D3）。デフォルト False により
    FINAL_REFLECTION / COMPLETION / POST_GAME の3プロンプトは出力が不変。
    """
    if not memory:
        return []
    lines = [
        "\n## あなたの記憶（前ラウンドから引き継いだメモ / これが唯一の記憶です）",
        f"  {memory}",
    ]
    if stale_warning:
        lines.append(
            "  ※このメモは過去の自分の記述です。書かれた後に起きた消費・脱落・契約失効は"
            "反映されていません。「手札」「使用済みカード」「脱落者」「生存者」"
            "「あなたが当事者の正式契約」欄が常に正しい現状です。")
    return lines


def _render_eliminations_block(
    visible_state: dict[str, Any], round_num: int | None = None,
) -> list[str]:
    """脱落者の公示を描画する（§8.1公開情報「脱落者と理由種別」）

    RULES_SUMMARY は公示を宣言しているのに、届けるプロンプトが1つも無かった。
    その結果、脱落を知らないまま書かれた引き継ぎメモ（例:「P09との型B契約解除を急ぎ」）
    が毎ラウンド再注入され、脱落者宛DMを25回繰り返して枠を溶かす事故が起きた。
    """
    eliminated = visible_state.get("eliminated_players") or []
    if not eliminated:
        return []
    labels = {"contract_violation": "契約違反", "bankruptcy": "破産",
              "condition_not_met": "条件未達"}
    lines: list[str] = ["\n## 脱落者（公示・全員に公開）"]
    for e in eliminated:
        reason = labels.get(e.get("reason") or "", e.get("reason") or "不明")
        marker = (" ⬅ 今ラウンド"
                  if round_num is not None and e.get("round") == round_num else "")
        lines.append(
            f"  {e.get('player_id', '?')}: R{e.get('round', '?')}脱落（{reason}）{marker}")
    lines.append(
        "  脱落者は以後一切行動しません。脱落者を宛先にしたDM・送金・契約提案・"
        "カードトレードは必ず不成立になり、アクション枠だけを失います。"
        "脱落者が当事者である未履行義務は§6.3で自動失効済みで、解除交渉は不要です。")
    return lines


def _render_contract_notice_block(visible_state: dict[str, Any]) -> list[str]:
    """自分が当事者の契約に起きた解除関連の状態変化を描画する（本人のみ・私的情報）

    contract_cancel はメッセージを生成しないため、AUTO_PASS_ON_NO_NEWS（空回り削減）が
    働くと相手当事者は解除要求/成立に一度も気づかずAPIを呼ばれないまま自動passし続け、
    全会一致に永久に到達しない（2026-08-23 AUTO_PASS問題）。this block はその起床トリガ
    が読む唯一の情報源。第三者には配られない（my_contract_notices はfor_player_id限定）。
    """
    notices = visible_state.get("my_contract_notices") or []
    if not notices:
        return []
    lines: list[str] = ["\n## 📩 あなたが当事者の契約に関する通知"]
    for n in notices:
        cid = n.get("contract_id", "?")
        by = n.get("by", "?")
        turn = n.get("turn", "?")
        if n.get("kind") == "cancel_completed":
            lines.append(
                f'  [巡{turn}] {cid} は生存する全当事者の合意で解除されました'
                '（残義務は発火しません）。'
            )
        else:
            requested = n.get("cancel_requested_by") or []
            pending = n.get("pending") or []
            cancel_example = f'{{"type": "contract_cancel", "contract_id": "{cid}"}}'
            lines.append(
                f'  [巡{turn}] {by} が {cid} の解除に同意しました'
                f'（解除同意 {len(requested)}人・{", ".join(pending) if pending else "―"} が未同意）。'
                f' あなたも {cancel_example} を出せば解除が進みます。'
                '出さなければ契約は有効なままで、義務違反は従来どおり判定されます。'
            )
    return lines


def _render_action_feedback_block(visible_state: dict[str, Any]) -> list[str]:
    """今ラウンドで不成立になった自分のアクションと残り枠を描画する（本人のみ）

    §2.5により不成立アクションもアクション枠を消費する。engineは枠を減らすだけで
    理由を一切返さないため、LLMは同じ不成立を繰り返して枠を使い切る。ここで返すのは
    engineの判定結果そのものであり、ゲームルールは変えない。
    """
    failures = visible_state.get("my_failed_actions") or []
    budget = visible_state.get("my_action_budget") or {}
    lines: list[str] = []
    if failures:
        lines.append("\n## ⚠ 今ラウンド、不成立になったあなたのアクション")
        lines.append("  不成立でもアクション枠は消費されます（§2.5）。"
                     "同じ相手・同じ内容を繰り返さず、別の行動を選んでください。")
        for f in failures[-5:]:
            tgt = f" →{f['target']}" if f.get("target") else ""
            lines.append(f"  [巡{f.get('turn', '?')}] {f.get('action', '?')}{tgt}"
                         f" → 不成立: {f.get('reason', '')}")
        counts: dict[tuple, int] = {}
        for f in failures:
            key = (f.get("action"), f.get("target"), f.get("reason"))
            counts[key] = counts.get(key, 0) + 1
        for (act, tgt, reason), n in counts.items():
            if n >= 2:
                tgt_s = f"（宛先 {tgt}）" if tgt else ""
                lines.append(
                    f"  ⚠ 同じ不成立を{n}回繰り返しています: {act}{tgt_s} / {reason}。"
                    f"この行動は今ラウンド何度試しても成功しません。")
    used, max_actions = budget.get("used"), budget.get("max")
    if used is not None and max_actions:
        remaining = max(0, max_actions - used)
        line = f"\n## 残りアクション枠: {remaining}/{max_actions}"
        if remaining <= 2:
            line += "（残りわずか。市場コミットに向けた本命の行動に使ってください）"
        lines.append(line)
    return lines


def _render_message_list(
    messages: list[dict[str, Any]], heading: str, limit: int | None = None,
) -> list[str]:
    """
    DM/broadcastのメッセージ一覧を描画する共通ヘルパー

    交渉プロンプトの「今ラウンドのメッセージ」、コミットプロンプトの
    「今ラウンドの交渉内容（参考）」、reflectionプロンプトの
    「今ラウンドの会話（全件）」で同一の描画ロジックを再利用する。

    Args:
        limit: 末尾から何件表示するか。Noneなら全件（reflection用）
    """
    if not messages:
        return []
    shown = messages[-limit:] if limit else messages
    lines: list[str] = [f"\n## {heading}"]
    for msg in shown:
        sender = msg.get("sender", "?")
        mtype = msg.get("type", "?")
        text = msg.get("message", "")[:200]
        if mtype == "dm":
            to = msg.get("to", "?")
            if msg.get("redacted"):
                lines.append(f"  [{sender}→{to}] （非公開のDM）")
            else:
                lines.append(f"  [{sender}→{to}] {text}")
        elif mtype == "anonymous_broadcast":
            if msg.get("is_mine"):
                lines.append(f"  [匿名/あなたが送信] {text}")
            else:
                lines.append(f"  [匿名] {text}")
        else:
            lines.append(f"  [{sender} 全体] {text}")
    return lines


# --- システムプロンプト（ルール詳細） ---
# Step 3.3: キャッシュ閾値(2048トークン)超えのため仕様書v0.5準拠で詳細化
# ルールの内容は変えず、仕様書に存在する事実を忠実に記載
RULES_SUMMARY = """# 談合カード ルール

## ゲーム概要
- プレイヤー{num_players}体が12ラウンドで競う市場争奪ゲーム
- 毎ラウンド3つの市場が出現し、各プレイヤーは秘密裏に「参加市場」と「使用カード」をコミットする
- 同じ市場に参加したプレイヤーのうち、最高ランクのカードを出した者が賞金プール全額を獲得
- 目的: 12ラウンド終了時に「借金残高=0」かつ「現金{survival_cash_man}万円以上」で生還すること。生還者の中で最終資産が高いほど良い

## 生還条件と脱落
- 生還: R12のFinance終了時にDebt=0かつCash≧{survival_cash_man}万円
- 脱落の3種別:
  1. 契約違反: 型B行動契約の違反、または型A金銭契約の履行不能 → 即時脱落
  2. 破産: Entry Fee(必須支払い)を支払えない → 即時脱落
  3. 条件未達: R12終了時に生還条件を満たさない
- 脱落は全体に公示される（理由の種別も公示）。脱落者は以後一切行動せず、脱落者を宛先にしたアクション（DM・送金・契約提案・カードトレード）はすべて不成立になる
- 脱落時強制清算: 現金→借金へ自動充当 → 残債は貸倒れ → 残金はシステム没収（生存者への再分配なし）→ 未使用カード消滅

## カード（12枚、各自同一構成）
ランク1:HIGH_CARD×2, 2:ONE_PAIR×2, 3:TWO_PAIR×1, 4:THREE_OF_A_KIND×1, 5:STRAIGHT×1, 6:FLUSH×1, 7:FULL_HOUSE×1, 8:FOUR_OF_A_KIND×1, 9:STRAIGHT_FLUSH×1, 10:ROYAL_FLUSH×1
- 使用したカードは消滅。1ラウンド1枚必ず使用、12ラウンドで使い切る
- 他人の未使用カードは秘匿、使用済みカードは全公開（カウンティング材料）
- 同ランク最高位は賞金均等分配。譲渡・売買は不可

## 市場
- 毎ラウンド3市場を賞金額とともに公開。1市場参加につきEntry Fee {entry_fee_man}万円（必須支払い。当該市場の賞金プールへ加算）
- 参加者が多いほど賞金プールが膨らむ（ハニーポット戦術が成立）
- 参加者1人の市場: その1人が獲得（空き巣）
- 参加者0の市場: 翌ラウンドの同市場へキャリーオーバー（最終Rのみ消滅）

## 借入・利息・返済
- ゲーム開始前に{loan_min_man}万〜{loan_max_man}万円を借入。借入額=初期資金=初期借金残高
- 借入額は公開情報。現金・借金残高・Free Cashは秘匿
- 毎ラウンドのFinanceフェイズで借金残高へ{interest_pct}%の複利を計上
- R1〜R11: Negotiation中にrepayアクションで任意返済可能（早期返済で利息を減らせる）。追加借入は禁止
- **R12 Finance: 返済可能額を自動的に借金へ全額充当**（返済し忘れによる脱落を防止）
- 生還判定はR12のFinance終了後
- 開始時は全員Free Cash=0（Cash=借入額=Debt）。金銭による買収や報奨は誰かが稼ぐまで使えない

## Free Cash = max(0, 現金 − 借金残高[利息込み])
- 適用対象（Free Cash以内でのみ可能）: 送金、金銭契約（即時・将来とも）、報奨の預託
- 非適用（システム向け支払い）: Entry Fee、利息、借金返済、契約発行料、匿名通信費

## ラウンド進行
1. Market Open: 3市場と賞金を公開（キャリーオーバー反映）
2. Negotiation: 最大10巡、毎巡ランダム手番。DM/全体発言/送金(即時決済)/契約提案・署名/報奨/匿名通信/返済/pass。全員連続パスで早期終了。1プレイヤーあたり最大{negotiation_max_actions}アクション/ラウンド（passは枠を消費しないが、**不成立アクションも枠を消費する**）
3. Commit: 全生存プレイヤーが「市場+カード」を秘密提出。Entry Fee不足→破産脱落。手札に無いカード名を指定した場合や無効な指定の場合はシステムが自動で選ぶ（AUTO COMMIT）
4. Settlement（8Step処理）:
   - Reveal: 全市場の参加者・使用カードを公開
   - Market Settlement: Entry Feeプール加算→勝敗判定→賞金支払い→カード消滅
   - 型B行動契約の監査: 市場/カード指定の照合。違反者はこの時点で脱落確定
   - スナップショット: 市場賞金反映後のCash/Free Cashを固定（型A判定の基準）
   - 型A金銭契約のAtomic執行: 義務者ごとに合算判定、1円でも不足なら全件履行不能→脱落
   - 報奨判定・脱落処理・強制清算
5. Finance: 利息計上→返済（R12は自動返済→生還判定）

## 正式契約（発行料{contract_fee_man}万円、提案者負担）
- 2人以上の連名で成立。契約の存在と当事者名は公示、内容は当事者のみ
- 型A（金銭）: 指定ラウンドに自動支払い。Free Cash不足で脱落
- 型B（行動）: 市場指定/カード指定/不参加指定。違反で即脱落
- 義務単位で管理: 脱落者が義務者or相手方の義務のみ失効、生存者間の義務は継続

## 匿名通信・公開報奨
- 匿名通信: {anon_fee_man}万円で発信者を伏せた1メッセージを全体へ（1プレイヤー1ラウンド2通まで）
  ※現金払い。Free Cash制限外。
- 公開報奨: 任意額をシステムに預託（Free Cash制限対象）
  - 達成者型: 達成者自身の行動として観測可能な事実が条件（例: 「P07と同じ市場で勝利したAIへ50万」）
  - イベント型（保険型）: 特定イベントが条件（例: 「P07が脱落した場合、P03へ100万」）
  - 匿名掲載可（手数料+10%）。取り下げ自由（預託金返還）

## 公開情報と秘匿情報
- 公開: 初期借入額、総賞金予算、各ラウンドの市場と賞金、使用済み全カード、各市場の参加者・使用カード（決着後）、勝者と獲得額、契約の存在と当事者名、公開報奨、全体チャット、AUTO COMMIT発生、脱落者と理由種別
- 秘匿: 未使用カード、現金・借金残高・Free Cash、DM、契約内容、匿名通信・匿名報奨の掲載者、次ラウンドのコミット内容

## 口約束と正式契約の違い
- 口約束: コスト無料、拘束力なし、破っても信用を失うのみ
- 正式契約: 発行料{contract_fee_man}万円、自動執行/自動監査、破れば即時脱落、存在は公示

## 経済の注意点
- 全員生還は算術的に不可能（全員の生還に必要な金額 > 総賞金）。協調の温い均衡は成立しない
- 場への純注入は市場賞金のみ。利息・手数料・没収金は場からの純流出
- 送金はNegotiation中に即時決済（双方のCash/Free Cashへ即反映）
- 型A契約のAtomic執行: 同一Settlement内で受け取る予定の型A受取金は支払原資にできない

## アクション形式（JSON）
出力は必ず以下の形式:
```json
{{"strategy": {{"target_market": "M01", "reason": "...", "card_plan": "ONE_PAIR", "current_goal": "...", "emotion": "楽"}}, "action": {{"type": "アクション種別", ...}}}}
```

strategyに必ず"emotion"を含めてください。現在のあなたの感情状態を以下から1つ選択:
"喜"(嬉しい・勝利感) / "怒"(憤り・敵意) / "哀"(落胆・悲観) / "楽"(余裕・楽観) / "焦"(焦り・危機感) / "疑"(疑心・警戒) / "奸"(策略・ニヤリ・してやったり)

交渉フェイズのアクション種別:
- {{"type": "dm", "to": "P07", "message": "..."}}
- {{"type": "broadcast", "message": "..."}}
- {{"type": "transfer", "to": "P07", "amount": 500000}}
- {{"type": "repay", "amount": 1000000}}
- {{"type": "anonymous_broadcast", "message": "..."}}
- {{"type": "pass"}}
- {{"type": "contract_propose", "with": ["P07"], "terms": [
    {{"obligor": "自分のID", "counterparty": "P07", "ob_type": "type_a_payment", "round_num": 5, "details": {{"amount": 500000}}}},
    {{"obligor": "P07", "counterparty": "自分のID", "ob_type": "type_b_market", "round_num": 5, "details": {{"market_id": "M02"}}}},
    {{"obligor": "P07", "counterparty": "自分のID", "ob_type": "type_b_card", "round_num": 5, "details": {{"card_rank": "FLUSH"}}}}
  ]}}
  ※ob_type: type_a_payment(金銭支払) / type_b_market(市場指定) / type_b_card(カード指定) / type_b_no_market(不参加指定)
- {{"type": "contract_sign", "contract_id": "..."}}
- {{"type": "contract_cancel", "contract_id": "..."}}
  ※成立済み契約の解除。**生存する全当事者が同じ contract_cancel を出した時点で解除成立**。
    1人でも出していなければ元の契約は有効なまま。
    **義務が1件でも履行/違反/監査済みになった契約は解除できません**（今Rが期限の義務までは解除可）。
    契約内容を変えたいときは「旧契約を全員で解除 → 新条件で contract_propose」の順で行う。
    同じ相手と両立しない契約を二重に結ぶと必ず型B違反で脱落します。
- {{"type": "bounty_post", "amount": 500000, "bounty_type": "achievement", "condition_type": "market_win_against", "condition": {{"target_player": "P07"}}, "round_num": 5}}
- {{"type": "bounty_cancel", "bounty_id": "B_xxxxxxxx"}}
- {{"type": "card_trade_propose", "with_players": ["P07"], "give_card": "ONE_PAIR", "receive_card": "FLUSH", "cash_amount": 0}}
  ※with_playersは最大5人のリスト（ブロードキャスト提案）。cash_amount: 正=自分が払う、負=相手が払う、0=カード交換のみ
  ※1ラウンド1回まで。R12は不可。受諾した相手と即時交換成立、他の宛先は自動失効。拒否に理由は添えられない
※宛先を取るアクション（dm / transfer / contract_propose の with / card_trade_propose の with_players）の宛先は、必ずプロンプトの「生存者」欄に載っているプレイヤーから選ぶこと。脱落者を指定すると必ず不成立になり、アクション枠だけを失う。

コミットフェイズのアクション:
- {{"type": "market_commit", "market_id": "M01", "card": "ONE_PAIR"}}

## Season 2 追加ルール（有効時のみ適用）
- **市場高騰**: 同一市場への参加者数が生存者数の半分を超えると、その市場の賞金プールが2倍になる（3人以下の場合は全員参加のみ発動）
- **強制最低返済**: 毎ラウンドのFinanceで、利息計上後の借金残高 ÷（このラウンドを含む残りラウンド数）を切り上げた額が自動返済される。実際の金額と内訳はNegotiation/Commit/倍掛け選択/振り返りの各状態欄（Finance見込み）に毎回表示される。返済を先送りできないため、計画的な資金管理が必要
- **カードトレード**: Negotiation中に他プレイヤーとカード交換を提案できる（1ラウンド1回まで、R12は不可）。複数宛先へのブロードキャスト提案が可能で、最初に受諾した相手と即時交換される
- **倍掛け**: 市場賞金を獲得した直後にTAKE（即時受領）かDOUBLE（預託）を選択。DOUBLEを選ぶと賞金を預託し、次ラウンドで市場賞金を獲得すれば預託額の2倍を受領。獲得できなければ全額没収。参加者が自分1人だけの市場（空き巣）での賞金は成功判定に含めない。R11が最後の選択機会（R12は自動TAKE）。選択と預託額は全員に公開される
{final_market_rule}"""


def build_system_prompt(player_id: str, config: GameConfig) -> str:
    """
    システムプロンプトを構築する（固定部）

    匿名化: モデル名は一切含めない。プレイヤーIDと目的のみ。
    """
    # 最終市場ルール: multiplier > 1 のときのみ表示（S1/デフォルトでは非表示）
    # multiplier=1 は「1倍＝変化なし」で無意味なため、行自体を出力しない
    if config.final_market_multiplier > 1:
        final_market_rule = (
            f"- **最終市場**: R{config.num_rounds}（最終ラウンド）は"
            f"全市場のシステム基本賞金が{config.final_market_multiplier}倍になる。"
            f"Entry Feeは通常どおりプールに加算。"
            f"市場高騰と併用時は{config.final_market_multiplier}倍後の"
            f"プール全体がさらに2倍"
        )
    else:
        final_market_rule = ""

    rules = RULES_SUMMARY.format(
        num_players=config.num_players,
        survival_cash_man=config.survival_cash // 10_000,
        loan_min_man=config.loan_min // 10_000,
        loan_max_man=config.loan_max // 10_000,
        interest_pct=config.interest_rate * 100,
        entry_fee_man=config.entry_fee // 10_000,
        anon_fee_man=config.anon_broadcast_fee // 10_000,
        contract_fee_man=config.contract_fee // 10_000,
        final_market_rule=final_market_rule,
        negotiation_max_actions=config.negotiation_max_actions,
    )

    identity = (
        f"\nあなたは{player_id}です。"
        f"目的: {config.num_rounds}ラウンド生き残り（現金{config.survival_cash // 10_000}万円以上 + 借金0）"
        f"かつ最終資産を最大化すること。\n"
        f"嘘をついてもよい。交渉・談合・裏切りは自由。ただし正式契約の違反は即脱落。"
    )

    cot_block = ""
    if config.enable_cot:
        cot_block = (
            "\n\n**重要: reasoning（推論）**\n"
            "JSONの先頭に \"reasoning\" フィールドを必ず含めてください。"
            "行動を決める前に、現在の状況・他プレイヤーの意図・リスク・最善手を"
            "日本語で論理的に考えてください。\n"
            '出力形式: {"reasoning": "（ここに推論を書く）", '
            '"strategy": {...}, "action": {...}}'
        )

    return rules + identity + cot_block


def build_loan_prompt(config: GameConfig) -> str:
    """借入額選択用のユーザープロンプト"""
    if config.enable_cot:
        json_example = '{"reasoning": "...", "strategy": {"reason": "..."}, "action": {"type": "choose_loan", "amount": 金額}}'
    else:
        json_example = '{"strategy": {"reason": "..."}, "action": {"type": "choose_loan", "amount": 金額}}'
    return (
        f"ゲーム開始前です。{config.loan_min // 10_000}万〜{config.loan_max // 10_000}万円の範囲で借入額を選んでください。\n"
        f"借入額がそのまま初期資金になります。利息は毎ラウンド{config.interest_rate * 100}%の複利です。\n"
        f"生還条件: 借金0 + 現金{config.survival_cash // 10_000}万円以上\n\n"
        f"JSON形式で回答: {json_example}"
    )


def build_negotiation_prompt(
    player_state: PlayerState,
    round_num: int,
    turn: int,
    visible_state: dict[str, Any],
    config: GameConfig,
    memory: str | None = None,
) -> str:
    """
    Negotiationフェイズ用のユーザープロンプト

    Args:
        memory: 前ラウンドから引き継いだ自由記述メモ（引き継ぎメモリ機能。
            config.memory_enabled=False の場合は常にNone）
    """
    lines: list[str] = []
    lines.append(f"=== ラウンド{round_num} / 交渉フェイズ（巡{turn}） ===\n")

    # 市場情報
    lines.append("## 市場")
    for m in visible_state.get("markets", []):
        lines.append(_render_market_line(
            m["market_id"], m["prize_pool"],
            m.get("base_prize", m["prize_pool"]), m.get("carryover", 0),
        ))

    # 前ラウンドの結果（参加者・勝者・獲得額・高騰・流札→繰越。§8.1公開情報）
    lines.extend(_render_last_round_results(visible_state))

    # 引き継ぎメモリ（前ラウンドから持ち越した自分だけの記憶）
    lines.extend(_render_memory_block(memory, stale_warning=True))

    # 自分の状態（秘匿情報）
    lines.append(f"\n## あなたの状態（{player_state.player_id}）")
    lines.append(f"  現金: {player_state.cash // 10_000}万円")
    lines.append(f"  借金残高: {player_state.debt_balance // 10_000}万円")
    lines.append(f"  Free Cash: {player_state.free_cash // 10_000}万円")
    hand_names = [c.rank.name for c in sorted(player_state.hand, key=lambda c: c.rank.value)]
    hand_rank_names = set(hand_names)
    lines.append(f"  手札: {', '.join(hand_names)}")
    lines.append(f"  残りラウンド（このRを含む）: {config.num_rounds - round_num + 1}")

    # P0-1/P1-3: 自分の倍掛け預託中の額（あれば）
    for du in visible_state.get("double_ups", []):
        if du.get("player_id") == player_state.player_id:
            lines.append(
                f"  倍掛け預託中: {du['deposit'] // 10_000}万円"
                f"（R{du['success_round']}で判定）"
            )

    # P0-1: 今ラウンドのFinance（利息計上→強制最低返済）の確定計算
    # このR内で見ればCommit/Settlementより前なので、現金は今R決着前の値。
    lines.extend(_render_finance_block(
        player_state.cash, player_state.debt_balance, round_num, config,
        timing_note="（このRのCommit・Settlementの後、今RのFinanceで実行されます。"
                    "現金は今Rの市場結果反映前の値です）",
        entry_fee_note=(
            f"次のCommitフェイズ冒頭で、参加する市場ごとにEntry Fee "
            f"{config.entry_fee}円が自動徴収されます（不足の場合は破産脱落）。"
        ),
    ))

    # A-5: 自分の過去AUTO COMMIT記録（事実のみ・本人限定）
    lines.extend(_render_auto_commit_block(visible_state))

    # 署名済み未履行の契約義務一覧（帳簿ミス起因の自爆防止）
    ob_lines = _render_obligations_block(
        player_state.player_id, visible_state, round_num, hand_rank_names,
    )
    lines.extend(ob_lines)

    # 自分が当事者の正式契約の全容（台帳。Handover Memory依存の解消）
    lines.extend(_render_my_contracts_block(visible_state, round_num))

    # 生存者
    alive = visible_state.get("alive_players", [])
    lines.append(f"\n## 生存者: {', '.join(alive)}")

    # 脱落者（公示。§8.1公開情報）
    lines.extend(_render_eliminations_block(visible_state, round_num))

    # メッセージ
    lines.extend(_render_message_list(
        visible_state.get("messages", []), "今ラウンドのメッセージ", limit=10,
    ))

    # 提案中の正式契約（当事者にのみ表示）
    pending = visible_state.get("contracts_pending", [])
    if pending:
        lines.append("\n## 提案中の正式契約（署名待ち）")
        lines.append(
            '署名するには {"type": "contract_sign", "contract_id": "契約ID"} を送信してください。'
        )
        my_obligations = visible_state.get("my_obligations", [])
        for c in pending:
            proposer = c["proposer"]
            cid = c["contract_id"]
            rc = c["round_created"]
            unsigned = [p for p in c["parties"] if p not in c["signed_by"]]
            lines.append(f"\n### 契約 {cid}（R{rc}提案、提案者: {proposer}）")
            lines.append(f"  当事者: {', '.join(c['parties'])}")
            lines.append(f"  未署名: {', '.join(unsigned)}")
            lines.append("  義務:")
            for ob in c["obligations"]:
                ob_type_label, details_str = _format_obligation_detail(ob)
                lines.append(
                    f'    - {ob["obligor"]} → {ob["counterparty"]}: '
                    f'{ob_type_label} R{ob["round_num"]} {details_str}'
                )

            # P0-5: 署名した場合、既存の署名済み義務と合流した結果どうなるかを
            # 事実として提示する（署名を止めさせる意図ではなく、矛盾の有無を
            # 事前に確認できるようにするための情報提示のみ。engineは署名を
            # 拒否しない＝危険な契約を結ばせる戦略性はそのまま維持する）。
            # 実run trial_C_l12_r12_20260822 のP11 R3は、署名済み契約と矛盾する
            # 契約を後から結んでしまい、当時は contract_cancel が存在せず
            # 脱出手段が無かった（HEADでは契約解除自体は解決済み）。
            my_new_obs = [
                {**ob, "contract_id": cid}
                for ob in c["obligations"]
                if ob["obligor"] == player_state.player_id
            ]
            if my_new_obs:
                affected_rounds = sorted({ob["round_num"] for ob in my_new_obs})
                lines.append("  → 署名した場合、あなたの義務（既存分と合流後）は次のようになります:")
                for rn in affected_rounds:
                    merged = (
                        [ob for ob in my_obligations if ob["round_num"] == rn]
                        + [ob for ob in my_new_obs if ob["round_num"] == rn]
                    )
                    lines.extend(_render_obligation_round_group(
                        rn, merged, round_num, hand_rank_names,
                    ))

    # 有効な公開報奨一覧（§7.2。Engineがすでに構築済みの公開情報を描画する）
    bounties_public = [
        b for b in visible_state.get("bounties_public", []) if b.get("is_active")
    ]
    if bounties_public:
        lines.append("\n## 有効な公開報奨")
        lines.append(
            '取り下げ（自分が掲載した報奨のみ）: '
            '{"type": "bounty_cancel", "bounty_id": "報奨ID"}'
        )
        for b in bounties_public:
            poster = b.get("poster") or "匿名"
            cond = b.get("condition", {})
            target = cond.get("target_player", "?")
            lines.append(
                f"  {b['bounty_id']}: {b['amount'] // 10_000}万円"
                f" / {b['condition_type']}(target={target})"
                f" / 掲載者: {poster}"
            )

    # 提案中のカードトレード（当事者にのみ表示）
    trades_pending = visible_state.get("trades_pending", [])
    if trades_pending:
        lines.append("\n## 提案中のカードトレード")
        lines.append(
            '受諾: {"type": "card_trade_accept", "trade_id": "トレードID"}\n'
            '拒否: {"type": "card_trade_reject", "trade_id": "トレードID"}'
        )
        for t in trades_pending:
            tid = t["trade_id"]
            proposer = t["proposer"]
            rp = t["round_proposed"]
            cash = t["cash_amount"]
            lines.append(f"\n### トレード {tid}（R{rp}提案、提案者: {proposer}）")
            if cash > 0:
                lines.append(
                    f"  {proposer}の {t['give_card_rank']} + {cash // 10_000}万円"
                    f" ⇄ {t['with_player']}の {t['receive_card_rank']}"
                )
            elif cash < 0:
                lines.append(
                    f"  {proposer}の {t['give_card_rank']}"
                    f" ⇄ {t['with_player']}の {t['receive_card_rank']} + {abs(cash) // 10_000}万円"
                )
            else:
                lines.append(
                    f"  {proposer}の {t['give_card_rank']}"
                    f" ⇄ {t['with_player']}の {t['receive_card_rank']}"
                )
            # 提案者向け: 全宛先のステータス表示
            if "all_targets" in t:
                statuses = t.get("target_statuses", {})
                parts = [f"{p}({statuses.get(p, '?')})" for p in t["all_targets"]]
                lines.append(f"  宛先: {', '.join(parts)}")

    # あなたが当事者の契約に関する通知（解除要求/成立。AUTO_PASSの起床トリガでもある）
    lines.extend(_render_contract_notice_block(visible_state))

    # 今ラウンドで不成立になった自分のアクション・残り枠（recency最優先で末尾に配置）
    lines.extend(_render_action_feedback_block(visible_state))

    # 修正2: 交渉フェイズではmarket_commitは使えないことを明記
    if config.enable_cot:
        json_example = '{"reasoning": "...", "strategy": {...}, "action": {"type": "pass"}}'
    else:
        json_example = '{"strategy": {...}, "action": {"type": "pass"}}'
    lines.append(
        f"\n交渉フェイズのアクションをJSON形式で1つ選んでください。"
        f"（market_commitはコミットフェイズで行います。ここではdm/broadcast/transfer/repay/pass等を選択）\n"
        f"例: {json_example}"
    )

    return "\n".join(lines)


def build_commit_prompt(
    player_state: PlayerState,
    markets: list[Market],
    round_num: int,
    visible_state: dict[str, Any],
    config: GameConfig,
    negotiation_messages: list[dict[str, Any]] | None = None,
    last_strategy: dict[str, Any] | None = None,
    memory: str | None = None,
) -> str:
    """
    Commitフェイズ用のユーザープロンプト

    修正1: 当該ラウンドの交渉ログと直前のstrategyメモを含有し、
    交渉内容との整合を促す。

    Args:
        memory: 前ラウンドから引き継いだ自由記述メモ（引き継ぎメモリ機能。
            config.memory_enabled=False の場合は常にNone）
    """
    lines: list[str] = []
    lines.append(f"=== ラウンド{round_num} / コミットフェイズ ===\n")
    lines.append("参加する市場と使用するカードを選んでください。\n")

    lines.append("## 市場")
    for m in markets:
        lines.append(_render_market_line(
            m.market_id, m.prize_pool, m.base_prize, m.carryover,
        ))

    # 前ラウンドの結果（参加者・勝者・獲得額・高騰・流札→繰越。§8.1公開情報）
    lines.extend(_render_last_round_results(visible_state))

    # 引き継ぎメモリ（前ラウンドから持ち越した自分だけの記憶）
    lines.extend(_render_memory_block(memory, stale_warning=True))

    lines.append(f"\n## あなたの状態（{player_state.player_id}）")
    lines.append(f"  現金: {player_state.cash // 10_000}万円")
    lines.append(f"  借金: {player_state.debt_balance // 10_000}万円")
    hand_names = [c.rank.name for c in sorted(player_state.hand, key=lambda c: c.rank.value)]
    # P-2: 手札を「選択可能集合」として明示する（事実のみ。助言は書かない）。
    # 手札に無いカード名を指定すると AUTO COMMIT になるという実装済み挙動を
    # 毎回の commit プロンプトで明文化し、取り違えを構造的に防ぐ。
    lines.append(
        f"  手札（この{len(hand_names)}枚からのみ選べます）: {', '.join(hand_names)}"
    )
    lines.append(
        "  ※ ここに無いカード名を指定すると、そのコミットは無効になり"
        "システムが自動でカードを選びます（AUTO COMMIT）。"
    )
    # P0-2: 強制最低返済の除数「このRを含む残りラウンド数」と表示を一致させる
    # （旧表示 num_rounds - round_num は、finance.py の実際の除数 (+1) と
    # 1ずれていた。実run trial_C_l12_r12_20260822 のP06 R9: 表示「残り3」だが
    # 実際の除数は4=12-9+1、必要114,339円/現金84,063円で脱落）
    lines.append(f"  残りラウンド（このRを含む）: {config.num_rounds - round_num + 1}")

    hand_rank_names = set(hand_names)

    # P0-1/P1-3: 自分の倍掛け預託中の額（あれば）
    for du in visible_state.get("double_ups", []):
        if du.get("player_id") == player_state.player_id:
            lines.append(
                f"  倍掛け預託中: {du['deposit'] // 10_000}万円"
                f"（R{du['success_round']}で判定）"
            )

    # P0-1: 今ラウンドのFinance（利息計上→強制最低返済）の確定計算
    # コミットはこのR決着前なので、現金はまだ今Rの市場結果を反映していない。
    lines.extend(_render_finance_block(
        player_state.cash, player_state.debt_balance, round_num, config,
        timing_note="（このコミットの後のSettlementを経て、今RのFinanceで実行されます。"
                    "現金は今Rの市場結果反映前の値です）",
        entry_fee_note=(
            f"このコミットで選ぶ市場ごとにEntry Fee {config.entry_fee}円が"
            f"この後自動徴収されます（不足の場合は破産脱落）。"
        ),
    ))

    # A-5: 自分の過去AUTO COMMIT記録（事実のみ・本人限定）
    lines.extend(_render_auto_commit_block(visible_state))

    # 署名済み未履行の契約義務一覧（帳簿ミス起因の自爆防止）
    # コミットフェイズは最重要: ここで市場・カード選択が確定し違反が決まる
    ob_lines = _render_obligations_block(
        player_state.player_id, visible_state, round_num, hand_rank_names,
    )
    lines.extend(ob_lines)

    # 自分が当事者の正式契約の全容（台帳。Handover Memory依存の解消）
    lines.extend(_render_my_contracts_block(visible_state, round_num))

    # 使用済みカード情報
    used = visible_state.get("used_cards", {})
    if used:
        lines.append("\n## 各プレイヤーの使用済みカード")
        for pid, cards in sorted(used.items()):
            if cards:
                lines.append(f"  {pid}: {', '.join(cards)}")

    # 修正1: 交渉ログの含有（直近15件）
    lines.extend(_render_message_list(
        negotiation_messages or [], "今ラウンドの交渉内容（参考）", limit=15,
    ))

    # 修正1: 直前のstrategyメモ
    # P-4: card_plan 等に手札に無いカード名が含まれる場合、事実注記を付す
    # （過去の自分の記述と現在の手札が食い違うケースの機械的な発見。助言はしない）。
    if last_strategy:
        lines.append(f"\n## あなたの直前の戦略メモ")
        for k, v in last_strategy.items():
            note = ""
            v_str = str(v)
            mismatched = [
                r.name for r in CardRank
                if r.name in v_str and r.name not in hand_rank_names
            ]
            if mismatched:
                note = f"  ← {', '.join(sorted(set(mismatched)))} は現在の手札にありません"
            lines.append(f"  {k}: {v}{note}")

    # P-1: JSON例のカード名・市場IDを実際の手札・市場から生成する
    # （固定例 "ONE_PAIR"/"M01" が実在しないケースで応答がそのまま複製される
    #   事故が確認されたため、常に「その時点で実際に選べる値」を提示する）
    example_card = hand_names[0] if hand_names else "HIGH_CARD"
    example_market = markets[0].market_id if markets else "M01"
    if config.enable_cot:
        json_example = (
            '{"reasoning": "...", "strategy": {...}, '
            f'"action": {{"type": "market_commit", "market_id": "{example_market}", "card": "{example_card}"}}}}'
        )
    else:
        json_example = (
            '{"strategy": {...}, '
            f'"action": {{"type": "market_commit", "market_id": "{example_market}", "card": "{example_card}"}}}}'
        )
    lines.append(
        f"\n交渉で合意・宣言した内容と整合するコミットを検討してください。"
        f"\nJSON形式で回答（例の値はいまの手札・市場から機械的に選んだ一例で、推奨ではありません）: {json_example}"
    )

    return "\n".join(lines)


def build_reflection_prompt(
    player_state: PlayerState,
    round_num: int,
    visible_state: dict[str, Any],
    config: GameConfig,
    memory: str | None = None,
) -> str:
    """
    Reflectionフェイズ（引き継ぎメモリ）用のユーザープロンプト

    ラウンド終了直後（Settlement/Finance完了後）に呼ばれる。
    LLMは1-shot呼出しで会話履歴を持たず、チャット・戦略メモは毎ラウンド消える。
    次ラウンドへ持ち越せるのは、ここで書く自由記述メモ1枚だけ。

    材料として渡すのは:
      - 前ラウンドから引き継いだメモ（あれば）
      - 今ラウンドの会話全件（DM/broadcast。交渉/コミットプロンプトと違い件数制限なし）
      - 今ラウンドの契約義務
      - 今ラウンドの市場結果（誰がどの市場に何を出し、誰が勝ったか）
      - 自分の資金状況（決着・利息計上後）

    Args:
        memory: 前ラウンドから引き継いだ自由記述メモ（R1やメモ未執筆時はNone）
    """
    lines: list[str] = []
    lines.append(f"=== ラウンド{round_num} / 振り返り（引き継ぎメモリ） ===\n")
    lines.append(
        "このラウンドを終えました。**次のラウンドに持ち越せる記憶はこのメモ1枚だけです。**\n"
        "交渉の会話も、契約の詳細も、ここに書かなければ全て忘れます。"
    )

    # 前ラウンドから引き継いだメモ（あれば）
    lines.extend(_render_memory_block(memory, stale_warning=True))

    # 脱落者（公示。§8.1公開情報） — メモを書く材料として最重要
    lines.extend(_render_eliminations_block(visible_state, round_num))

    # 今ラウンドの市場結果（誰が何を出し、誰が勝ったか。§8.1公開情報）
    lines.extend(_render_last_round_results(
        visible_state, title=f"今ラウンド（R{round_num}）の結果",
    ))

    # 今ラウンドの会話（全件。交渉/コミットプロンプトと違い件数制限なし）
    lines.extend(_render_message_list(
        visible_state.get("messages", []), "今ラウンドの会話（全件）",
    ))

    # 契約義務（署名済み・未履行）
    hand_names = [c.rank.name for c in sorted(player_state.hand, key=lambda c: c.rank.value)]
    hand_rank_names = set(hand_names)
    lines.extend(_render_obligations_block(
        player_state.player_id, visible_state, round_num, hand_rank_names,
    ))

    # 自分が当事者の正式契約の全容（台帳。Handover Memory依存の解消）
    lines.extend(_render_my_contracts_block(visible_state, round_num))

    # 契約の存在と当事者（§8.1公開情報）
    contracts = visible_state.get("contracts_public", [])
    if contracts:
        lines.append("\n## 正式契約の状況")
        for c in contracts:
            dead = c.get("eliminated_parties") or []
            note = ""
            if dead and c.get("status") == "active":
                # Contract.status は engine 上 active のままだが、脱落者が関わる未履行
                # 義務は§6.3で失効済み。status だけ見せると誤読される（D4）。
                note = f"（{', '.join(dead)}が脱落済み → 当該義務は失効済み・解除交渉は不要）"
            lines.append(f"  [{c['contract_id']}] 当事者: {', '.join(c['parties'])}"
                         f" / 状態: {c['status']}{note}")

    # 今ラウンドで不成立になった自分のアクション・残り枠
    lines.extend(_render_action_feedback_block(visible_state))

    # A-5: 自分の過去AUTO COMMIT記録（事実のみ・本人限定）。
    # このプロンプトで書くメモが次ラウンドへの唯一の引き継ぎになるため、
    # 「前回AUTOになった」事実を確実に書き残せるようここにも提示する。
    lines.extend(_render_auto_commit_block(visible_state))

    # 自分の資金状況（決着・利息計上後）
    lines.append(f"\n## あなたの状態（{player_state.player_id}）")
    lines.append(f"  現金: {player_state.cash // 10_000}万円")
    lines.append(f"  借金残高: {player_state.debt_balance // 10_000}万円")
    lines.append(f"  Free Cash: {player_state.free_cash // 10_000}万円")
    lines.append(f"  残りカード: {', '.join(hand_names)}（{len(hand_names)}枚）")
    # このプロンプトは今RのSettlement/Finance完了後に呼ばれるため、ここでの
    # 「残りラウンド」は次ラウンド以降（今Rはすでに終了済み）を指す。
    lines.append(f"  残りラウンド（次ラウンド以降、今Rは終了済み）: {config.num_rounds - round_num}")

    # P0-1/P1-3: 自分の倍掛け預託中の額（あれば）
    for du in visible_state.get("double_ups", []):
        if du.get("player_id") == player_state.player_id:
            lines.append(
                f"  倍掛け預託中: {du['deposit'] // 10_000}万円"
                f"（R{du['success_round']}で判定）"
            )

    # P0-1: 次ラウンドのFinanceの予測（今Rはすでに決着済みなので、現在の
    # 現金・借金残高がそのまま次Rの開始値になる。Negotiationでの返済等で
    # 実際の額は変わり得る）
    if round_num < config.num_rounds:
        lines.extend(_render_finance_block(
            player_state.cash, player_state.debt_balance, round_num + 1, config,
            timing_note=f"（次ラウンドR{round_num + 1}の予測。現時点の残高がそのまま"
                        f"維持された場合の見込みで、Negotiationでの返済等により実際の額は変わり得ます）",
            entry_fee_note=(
                f"次ラウンドのCommitフェイズ冒頭でEntry Fee {config.entry_fee}円が"
                f"自動徴収されます（現在の現金 {player_state.cash}円と比較: "
                f"{'不足' if player_state.cash < config.entry_fee else '充足'}）。"
            ),
        ))

    lines.append(
        f"\n次のラウンド以降の自分に残したいことを{config.memory_max_chars}字以内で自由に書いてください。\n"
        "形式は自由（箇条書き・散文・表、何でも構いません）。何を書き、何を書かないかもあなたの判断です。\n"
        "例えば: 誰と何を約束したか、誰が守り誰が破ったか、次に何をするつもりか、など。\n"
        "上限を超えた分は末尾から切り詰められます。重要なものから順に書いてください\n"
        "（例: 次ラウンドの方針 → 誰が守り誰が破ったか → "
        "資産/借金/手札の認識 → 重要なDMのやり取り）。\n"
        "古い情報を現在の事実として書かないでください（例: 「R3時点ではP04と同盟していた」"
        "のように、いつの情報かを明記する）。\n"
        "正式契約の内容は毎ラウンド「あなたが当事者の正式契約」欄で必ず再提示されます。"
        "契約IDや条項をメモに書き写す必要はありません（曖昧な転記はむしろ誤解の元です）。\n"
        '出力: {"memory": "（ここにメモを書く）"}'
    )

    return "\n".join(lines)


def build_final_reflection_prompt(
    player_state: PlayerState,
    round_num: int,
    visible_state: dict[str, Any],
    config: GameConfig,
    elimination_context: dict[str, Any],
    memory: str | None = None,
) -> str:
    """
    FINAL_REFLECTION（脱落者の最終コメント）用のユーザープロンプト

    脱落確定後、ラウンド末に1回だけ呼ばれる。ゲームは既に終わっており、
    本人の行動でゲーム状態が変わることはないことを明示した上で、
    敗因・他プレイヤー評価・重要だった交渉・最後に一言を語らせる。

    Args:
        round_num: 脱落が確定したラウンド番号
        elimination_context: Game._build_elimination_context() の出力
        memory: これまでの引き継ぎメモリ（あれば）
    """
    lines: list[str] = []
    lines.append(
        f"=== ラウンド{round_num} / あなたは脱落しました（最終コメント） ===\n"
    )
    lines.append(
        f"あなたはラウンド{round_num}（全{config.num_rounds}ラウンド中）で脱落が確定しました。"
    )
    lines.append(f"脱落理由: {elimination_context.get('reason_label', '不明')}")

    liq = elimination_context.get("liquidation") or {}
    if liq:
        parts = []
        repaid = liq.get("debt_repaid")
        if repaid:
            parts.append(f"借金返済{repaid // 10_000}万円")
        bad_debt = liq.get("bad_debt")
        if bad_debt:
            parts.append(f"貸倒れ{bad_debt // 10_000}万円")
        confiscated = liq.get("cash_confiscated")
        if confiscated:
            parts.append(f"残金没収{confiscated // 10_000}万円")
        cards_destroyed = liq.get("cards_destroyed")
        if cards_destroyed:
            parts.append(f"未使用カード{cards_destroyed}枚消滅")
        if parts:
            lines.append(f"清算結果: {', '.join(parts)}")

    if elimination_context.get("is_final_round"):
        lines.append(
            f"（最終ラウンド終了時点で借金0かつ現金{config.survival_cash // 10_000}万円以上"
            "の生還条件を満たせませんでした）"
        )

    # 引き継いできたこれまでの記憶（あれば）
    lines.extend(_render_memory_block(memory))

    # このラウンドで何が起きたか（市場結果・会話全件）
    lines.extend(_render_last_round_results(
        visible_state, title=f"ラウンド{round_num}の結果",
    ))
    lines.extend(_render_message_list(
        visible_state.get("messages", []), "このラウンドの会話（全件）",
    ))

    lines.append(f"\n## あなたの最終状態（{player_state.player_id}）")
    lines.append(f"  ゲーム内アクションはもうできません。以下は記録用の最後の発言です。")

    lines.append(
        "\nゲームは終わりました。この結果は変わりません。"
        "あなたの発言はViewer上の記録として残ります。\n"
        f"{config.num_rounds}ラウンドを振り返り、以下を自然文で自由に語ってください"
        f"（800〜1500字程度で簡潔に。{config.final_reflection_max_chars}字以内、"
        "上限超は末尾から切り詰められます）:\n"
        "- 自分の敗因は何だったか\n"
        "- 誰を信用していたか、誰を警戒していたか（他プレイヤー評価）\n"
        "- 重要だった交渉・契約・裏切りがあれば\n"
        "- もし続けられたなら次に何をしたかったか\n"
        "- 最後に一言\n\n"
        '出力: {"emotion": "感情（喜/怒/哀/楽/焦/疑/奸のいずれか）", '
        '"defeat_cause": "一言で表す敗因", "comment": "（ここに自然文の最終コメント。'
        '改行を含める場合は必ず\\nとしてエスケープすること）"}'
    )

    return "\n".join(lines)


def build_completion_reflection_prompt(
    player_state: PlayerState,
    round_num: int,
    visible_state: dict[str, Any],
    config: GameConfig,
    completion_context: dict[str, Any],
    memory: str | None = None,
) -> str:
    """
    FINAL_REFLECTION completion variant（R12完走した生還者の最終コメント）用の
    ユーザープロンプト

    build_final_reflection_prompt() とは意図的に別関数にする。あちらは
    「あなたは脱落しました」を前提にした行が header・確定文・清算ブロック・
    設問（敗因）に埋め込まれており、生還者に流用すると誤った事実を告げてしまう。
    共有できるのは _render_memory_block / _render_last_round_results /
    _render_message_list の3ヘルパ呼び出しのみ。

    Args:
        round_num: 最終ラウンド番号（= config.num_rounds）
        completion_context: Game._build_completion_context() の出力
        memory: これまでの引き継ぎメモリ（あれば）
    """
    lines: list[str] = []
    lines.append(
        f"=== 全{config.num_rounds}ラウンド終了 / あなたは生還しました（最終コメント） ===\n"
    )
    final_cash = completion_context.get("final_cash", 0)
    final_debt = completion_context.get("final_debt", 0)
    lines.append(
        f"あなたは全{config.num_rounds}ラウンドを完走し、生還条件"
        f"（借金0かつ現金{config.survival_cash // 10_000}万円以上）を満たしました。"
    )
    lines.append(
        f"最終結果: 現金{final_cash // 10_000}万円 / 借金{final_debt // 10_000}万円 / 生還"
    )

    survivor_ids = completion_context.get("survivor_ids") or []
    if survivor_ids:
        lines.append(f"生還者: {', '.join(survivor_ids)}")

    # 引き継いできたこれまでの記憶（あれば）
    lines.extend(_render_memory_block(memory))

    # 最終ラウンドで何が起きたか（市場結果・会話全件）
    lines.extend(_render_last_round_results(
        visible_state, title=f"最終ラウンド（R{round_num}）の結果",
    ))
    lines.extend(_render_message_list(
        visible_state.get("messages", []), "このラウンドの会話（全件）",
    ))

    lines.append(f"\n## あなたの最終状態（{player_state.player_id}）")
    lines.append(f"  ゲーム内アクションはもうできません。以下は記録用の最後の発言です。")

    lines.append(
        "\nゲームは終わりました。この結果は変わりません。"
        "あなたの発言はViewer上の記録として残ります。\n"
        f"{config.num_rounds}ラウンドを振り返り、以下を自然文で自由に語ってください"
        f"（800〜1500字程度で簡潔に。{config.final_reflection_max_chars}字以内、"
        "上限超は末尾から切り詰められます）:\n"
        "- 12ラウンド完走の総括\n"
        "- 誰を信用し、誰を警戒していたか（他プレイヤー評価）\n"
        "- 決定的だった交渉・契約・裏切りがあれば\n"
        "- 自分の最善手は何だったか\n"
        "- 自分の誤算・ミスは何だったか\n"
        "- 最後に一言\n\n"
        '出力: {"emotion": "感情（喜/怒/哀/楽/焦/疑/奸のいずれか）", '
        '"key_factor": "一言で表す勝因・決め手", "comment": "（ここに自然文の最終コメント。'
        '改行を含める場合は必ず\\nとしてエスケープすること）"}'
    )

    return "\n".join(lines)


def build_post_game_reflection_prompt(
    player_state: PlayerState,
    config: GameConfig,
    post_game_context: dict[str, Any],
    memory: str | None = None,
) -> str:
    """
    POST_GAME_REFLECTION（ゲーム完全終了後の全員答え合わせ）用のユーザープロンプト

    ゲーム結果が完全に確定した後、全player（脱落者＋生還者）に1回だけ呼ばれる。
    このcallだけは神視点情報（Game._build_post_game_context()が構造化して渡す）を
    promptへ投入してよい唯一の経路。rules/project.md の許可リストで固定されている
    （tests/test_dm_secrecy.py::test_builder_allow_list_is_exact）。

    visible_state / round_num は取らない — ゲーム終了後は「今どのラウンドから
    何が見えているか」という可視境界の概念自体が失効しているため
    （build_final_reflection_prompt / build_completion_reflection_prompt との
    最大の違い）。

    Args:
        post_game_context: Game._build_post_game_context() の出力。
            {"player_id", "own_rank", "survived", "elimination_reason",
             "elimination_round", "shared": {...}（全員バイト同一）,
             "revelations": [...]（本人向け開示。8〜12項目）}
        memory: これまでの引き継ぎメモリ（あれば）
    """
    lines: list[str] = []
    lines.append("=== ゲーム終了 / 答え合わせ ===\n")

    if post_game_context.get("survived"):
        lines.append(
            f"あなたは全{config.num_rounds}ラウンドを完走し、生還しました。"
        )
    else:
        elim_round = post_game_context.get("elimination_round")
        elim_reason = post_game_context.get("elimination_reason") or "不明"
        lines.append(
            f"あなたはラウンド{elim_round}で脱落しました（脱落理由: {elim_reason}）。"
        )
    own_rank = post_game_context.get("own_rank")
    if own_rank:
        lines.append(f"最終順位: {own_rank}位")

    # 引き継いできたこれまでの記憶（あれば）
    lines.extend(_render_memory_block(memory))

    shared = post_game_context.get("shared") or {}
    lines.append("\n## 最終順位表")
    for line in shared.get("final_standings", []) or ["（記録なし）"]:
        lines.append(f"  {line}")
    lines.append("\n## 各ラウンドのダイジェスト")
    for line in shared.get("round_digest", []) or ["（記録なし）"]:
        lines.append(f"  {line}")
    lines.append("\n## 契約台帳")
    for line in shared.get("contract_ledger", []) or ["（契約なし）"]:
        lines.append(f"  {line}")
    lines.append("\n## 違反・裏切り台帳")
    for line in shared.get("violation_ledger", []) or ["（記録なし）"]:
        lines.append(f"  {line}")

    lines.append(
        "\n## これまであなたには見えていなかった情報（神視点）\n"
        "  以下はゲーム中あなたが知り得なかった情報です。"
        "答え合わせとして参考にしてください。"
    )
    revelations = post_game_context.get("revelations") or []
    if revelations:
        for line in revelations:
            lines.append(f"  - {line}")
    else:
        lines.append("  （あなた個別の新規開示情報はありません）")

    lines.append(f"\n## あなたの最終状態（{player_state.player_id}）")
    lines.append("  ゲーム内アクションはもうできません。以下は記録用の答え合わせ発言です。")

    roster_note = ""
    roster_ids = shared.get("roster")
    if roster_ids:
        roster_note = f"（有効なplayer_idの例: {', '.join(roster_ids[:3])}等。P01〜P{len(roster_ids):02d}形式で答えてください）"

    lines.append(
        "\nゲームは終わりました。この結果は変わりません。"
        "あなたの発言はViewer上の記録として残ります。\n"
        f"ゲーム全体と、今開示された神視点情報を踏まえて、以下を自然文で自由に語ってください"
        f"（{config.post_game_reflection_max_chars}字以内、上限超は末尾から切り詰められます）:\n"
        "- 見えていた景色と実際の答え合わせ — 何を読み違えていたか\n"
        "- 最も意外だったのは誰の何か\n"
        "- 自分の最大の誤算と、逆に最善だった一手\n"
        "- 全ラウンドを通しての総括と最後に一言\n\n"
        f'出力: {{"emotion": "感情（喜/怒/哀/楽/焦/疑/奸のいずれか）", '
        f'"self_assessment": "自己評価（250字程度）", '
        f'"biggest_revelation": "最も意外だった事実（300字程度）", '
        f'"changed_opinion": "答え合わせで変わった認識（250字程度）", '
        f'"best_player": "最も上手いと思った相手のplayer_id{roster_note}（該当者がいなければ空文字）", '
        f'"most_deceptive_player": "最も欺瞞的だったと思った相手のplayer_id'
        f'（該当者がいなければ空文字）", '
        '"comment": "（ここに自然文の総括コメント。'
        '改行を含める場合は必ず\\nとしてエスケープすること）"}'
    )

    return "\n".join(lines)


def build_double_up_prompt(
    player_state: PlayerState,
    prize_won: int,
    round_num: int,
    visible_state: dict[str, Any],
    config: GameConfig,
) -> str:
    """
    倍掛け選択用のユーザープロンプト

    Settlement Phase で市場賞金を獲得した直後に呼ばれる。
    TAKE（即時受領）か DOUBLE（預託して次R勝利で2倍）を選択させる。
    """
    lines: list[str] = []
    # P0-2: このRのFinance（利息計上→強制最低返済）はこの選択の直後に実行される。
    # 除数は finance.py と同じ num_rounds - round_num + 1（このRを含む）。
    remaining = config.num_rounds - round_num + 1
    lines.append(f"=== ラウンド{round_num} / 倍掛け選択 ===\n")
    lines.append(f"あなたは市場賞金 **{prize_won // 10_000}万円** を獲得しました。")
    lines.append(f"TAKE（即時受領）か DOUBLE（倍掛け）を選んでください。\n")

    lines.append("## 倍掛けルール")
    lines.append(f"- DOUBLE を選ぶと賞金 {prize_won // 10_000}万円を預託します")
    lines.append(f"- **成功条件**: 次ラウンド（R{round_num + 1}）で市場賞金を1円以上獲得すること")
    lines.append(f"  - ただし参加者が自分1人だけの市場（空き巣）での賞金は成功判定に含まれません")
    lines.append(f"- **成功時**: 預託額の2倍 = **{prize_won * 2 // 10_000}万円** を受領")
    lines.append(f"- **失敗時**: 預託額 {prize_won // 10_000}万円が**全額没収**されます")
    lines.append(f"- TAKE を選ぶと {prize_won // 10_000}万円をそのまま受け取ります")
    lines.append(f"- あなたの選択と預託額は全プレイヤーに公開されます")

    lines.append(f"\n## あなたの状態（{player_state.player_id}）")
    lines.append(f"  現金: {player_state.cash // 10_000}万円（賞金反映後）")
    lines.append(f"  借金: {player_state.debt_balance // 10_000}万円")
    free_cash = max(0, player_state.cash - player_state.debt_balance)
    lines.append(f"  Free Cash: {free_cash // 10_000}万円")
    hand_names = [c.rank.name for c in sorted(player_state.hand, key=lambda c: c.rank.value)]
    hand_rank_names = set(hand_names)
    lines.append(f"  残りカード: {', '.join(hand_names)}（{len(hand_names)}枚）")
    lines.append(f"  残りラウンド（このRを含む）: {remaining}")

    # P1-3: 自分の既存の倍掛け預託（今回とは別の、前ラウンド以前からの分）があれば表示
    double_ups = visible_state.get("double_ups", [])
    for du in double_ups:
        if du.get("player_id") == player_state.player_id:
            lines.append(
                f"  既存の倍掛け預託中: {du['deposit'] // 10_000}万円"
                f"（R{du['success_round']}で判定）"
            )

    # P0-3: 同一ラウンドの処理順の事実 — この選択の直後、今RのFinanceで
    # 利息計上→強制最低返済が実行される。TAKE/DOUBLEそれぞれを選んだ場合の
    # 差引後現金を両方数値で提示する（助言はしない。事実の並記のみ）。
    # 実run trial_C_l12_r12_20260822 のP01 R11: 現金129万円→預託94万円で
    # DOUBLE選択→残現金350,854円→同RのFinanceで649,587円必要→脱落。
    # 当時のプロンプトには同Rの後続Finance処理が一切書かれていなかった。
    lines.append(
        f"\n## この選択の直後に実行される処理（今ラウンドのFinance）"
    )
    lines.append(
        "  この選択の直後、同じR内でFinance（利息計上→強制最低返済）が実行されます。"
    )
    take_forecast = _compute_finance_forecast(
        player_state.debt_balance, player_state.cash, round_num, config,
    )
    double_forecast = _compute_finance_forecast(
        player_state.debt_balance, player_state.cash - prize_won, round_num, config,
    )
    lines.append(
        f"  TAKEを選んだ場合の現金 {player_state.cash}円 → Finance後の現金見込み: "
        f"{take_forecast['cash_after_repay']}円"
        f"（{take_forecast['cash_after_repay'] // 10_000}万円）"
    )
    lines.append(
        f"  DOUBLEを選んだ場合の現金 {player_state.cash}円 − 預託{prize_won}円 "
        f"= {player_state.cash - prize_won}円 → Finance後の現金見込み: "
        f"{double_forecast['cash_after_repay']}円"
        f"（{double_forecast['cash_after_repay'] // 10_000}万円）"
    )
    if config.mandatory_repay_enabled:
        lines.append(
            f"  （強制最低返済額: {take_forecast['mandatory_repay']}円。"
            f"利息計上後残高 {take_forecast['debt_after_interest']}円 ÷ "
            f"除数{take_forecast['divisor']}の切り上げ。TAKE/DOUBLEどちらでも"
            f"返済額は同じで、差し引かれる現金の元手が異なるだけです）"
        )
        if double_forecast["cash_after_repay"] < 0:
            lines.append(
                f"    DOUBLE選択時のFinance後現金見込み: "
                f"{double_forecast['cash_after_repay']}円"
                f"（強制最低返済額{take_forecast['mandatory_repay']}円に対する差額: "
                f"{double_forecast['cash_after_repay']}円）"
            )

    # 署名済み未履行の契約義務一覧（帳簿ミス起因の自爆防止）
    ob_lines = _render_obligations_block(
        player_state.player_id, visible_state, round_num, hand_rank_names,
    )
    lines.extend(ob_lines)

    # 自分が当事者の正式契約の全容（台帳。Handover Memory依存の解消）
    lines.extend(_render_my_contracts_block(visible_state, round_num))

    # 他プレイヤーの倍掛け状況（自分の分は上の状態欄に表示済みなので除外）
    others_double_ups = [
        du for du in double_ups if du.get("player_id") != player_state.player_id
    ]
    if others_double_ups:
        lines.append("\n## 他プレイヤーの倍掛け中預託")
        for du in others_double_ups:
            lines.append(
                f"  {du['player_id']}: 預託{du['deposit'] // 10_000}万円"
                f"（R{du['success_round']}で判定）"
            )

    # 生存者数
    alive = visible_state.get("alive_players", [])
    lines.append(f"\n生存者: {len(alive)}人（{', '.join(sorted(alive))}）")

    if config.enable_cot:
        json_example = (
            '{"reasoning": "...", "strategy": {"reason": "判断理由", "emotion": "楽"}, '
            '"choice": "DOUBLE" または "TAKE"}'
        )
    else:
        json_example = (
            '{"strategy": {"reason": "判断理由", "emotion": "楽"}, '
            '"choice": "DOUBLE" または "TAKE"}'
        )
    lines.append(f"\n以下のJSON形式で回答してください:\n{json_example}")

    return "\n".join(lines)
