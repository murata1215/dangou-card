"""
プロンプト組立モジュール

仕様書v0.5からルールを要約したシステムプロンプトと、
毎ターンの観測情報を組み立てるユーザープロンプトを生成する。
匿名化: プロンプトにモデル名を一切含めない。
"""

from typing import Any
from engine.models import PlayerState, Market, CardRank
from engine.config import GameConfig

# --- 契約義務の可視化ヘルパー ---
# §7.3の「破産を計算ミスでなく戦略判断にする」思想を契約側にも適用。
# 署名済み未履行義務を毎プロンプトで提示し、帳簿ミス起因の自爆を防ぐ。
# engine判定ロジック（settlement/autocommit/contracts）には一切影響しない。


def _render_obligations_block(
    player_id: str,
    visible_state: dict[str, Any],
    round_num: int,
) -> list[str]:
    """
    署名済み未履行の契約義務一覧を描画する（毎プロンプト注入）

    全エージェント向けプロンプトビルダー（negotiation / commit / double_up）
    から呼ばれ、帳簿ミス起因の契約違反脱落を防止する情報を提示する。

    - ラウンドでグルーピング、現ラウンドを「⬅ 今ラウンド」で強調
    - 同一ラウンドに type_b_card 義務が2件以上あればコンフリクト警告
      （1ラウンド1枚制約との矛盾を計算済みで通知）
    - 義務ゼロなら空リスト（トークン節約）

    Args:
        player_id: 対象プレイヤーID（ログ用、現時点では未使用）
        visible_state: _build_visible_state() の出力（my_obligations キーを参照）
        round_num: 現在のラウンド番号

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
        obs = by_round[rn]
        # 現ラウンドを強調表示（自爆は現ラウンドの多重義務で起きるため）
        marker = " ⬅ 今ラウンド" if rn == round_num else ""
        lines.append(f"  **R{rn}**{marker}:")

        # 各義務の描画
        card_obs_count = 0
        for ob in obs:
            # 義務種別のラベル変換
            ob_type_label = {
                "type_a_payment": "型A金銭支払い",
                "type_b_market": "型B市場指定",
                "type_b_card": "型Bカード指定",
                "type_b_no_market": "型B不参加指定",
            }.get(ob["ob_type"], ob["ob_type"])
            # 詳細情報の描画（金額 / 市場ID / カードrank）
            details_str = ""
            if "amount" in ob["details"]:
                details_str = f'{ob["details"]["amount"] // 10_000}万円'
            elif "market_id" in ob["details"]:
                details_str = ob["details"]["market_id"]
            elif "card_rank" in ob["details"]:
                details_str = ob["details"]["card_rank"]
            elif "card" in ob["details"]:
                # フォールバック: 正規化前の古いデータとの互換
                details_str = ob["details"]["card"]
            lines.append(
                f'    - [{ob["contract_id"]}] {ob_type_label} {details_str}'
                f' （相手: {ob["counterparty"]}）'
            )
            if ob["ob_type"] == "type_b_card":
                card_obs_count += 1

        # コンフリクト警告: 同一ラウンドにカード使用義務が2件以上
        # → 1ラウンドに出せるのは1枚なので必ず違反で脱落する
        # この警告は情報提示のみ（署名拒否・自動修正はしない）
        if card_obs_count >= 2:
            lines.append(
                f"    ⚠ R{rn}: カード使用義務が{card_obs_count}件"
                f"（1ラウンドに出せるのは1枚 → 必ず違反で脱落）"
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


def _render_memory_block(memory: str | None) -> list[str]:
    """
    前ラウンドから引き継いだメモを描画する（交渉・コミット両プロンプトの共通ヘルパー）

    渡すのは常に最新の1枚のみ（累積しない）。R1やメモ未執筆時は何も描画しない。
    """
    if not memory:
        return []
    return [
        "\n## あなたの記憶（前ラウンドから引き継いだメモ / これが唯一の記憶です）",
        f"  {memory}",
    ]


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
- 脱落は全体に公示される（理由の種別も公示）
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
2. Negotiation: 最大10巡、毎巡ランダム手番。DM/全体発言/送金(即時決済)/契約提案・署名/報奨/匿名通信/返済/pass。全員連続パスで早期終了
3. Commit: 全生存プレイヤーが「市場+カード」を秘密提出。Entry Fee不足→破産脱落
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
- 匿名通信: {entry_fee_man}万円で発信者を伏せた1メッセージを全体へ（1プレイヤー1ラウンド2通まで）
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
- {{"type": "pass"}}
- {{"type": "contract_propose", "with": ["P07"], "terms": [
    {{"obligor": "自分のID", "counterparty": "P07", "ob_type": "type_a_payment", "round_num": 5, "details": {{"amount": 500000}}}},
    {{"obligor": "P07", "counterparty": "自分のID", "ob_type": "type_b_market", "round_num": 5, "details": {{"market_id": "M02"}}}},
    {{"obligor": "P07", "counterparty": "自分のID", "ob_type": "type_b_card", "round_num": 5, "details": {{"card_rank": "FLUSH"}}}}
  ]}}
  ※ob_type: type_a_payment(金銭支払) / type_b_market(市場指定) / type_b_card(カード指定) / type_b_no_market(不参加指定)
- {{"type": "contract_sign", "contract_id": "..."}}
- {{"type": "bounty_post", "amount": 500000, "bounty_type": "achievement", "condition_type": "market_win_against", "condition": {{"target_player": "P07"}}, "round_num": 5}}
- {{"type": "card_trade_propose", "with_players": ["P07"], "give_card": "ONE_PAIR", "receive_card": "FLUSH", "cash_amount": 0}}
  ※with_playersは最大5人のリスト（ブロードキャスト提案）。cash_amount: 正=自分が払う、負=相手が払う、0=カード交換のみ
  ※1ラウンド1回まで。R12は不可。受諾した相手と即時交換成立、他の宛先は自動失効。拒否に理由は添えられない

コミットフェイズのアクション:
- {{"type": "market_commit", "market_id": "M01", "card": "ONE_PAIR"}}

## Season 2 追加ルール（有効時のみ適用）
- **市場高騰**: 同一市場への参加者数が生存者数の半分を超えると、その市場の賞金プールが2倍になる（3人以下の場合は全員参加のみ発動）
- **強制最低返済**: 毎ラウンドのFinanceで「借金残高 ÷ 残りラウンド数」が自動返済される。返済を先送りできないため、計画的な資金管理が必要
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
        contract_fee_man=config.contract_fee // 10_000,
        final_market_rule=final_market_rule,
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
    lines.extend(_render_memory_block(memory))

    # 自分の状態（秘匿情報）
    lines.append(f"\n## あなたの状態（{player_state.player_id}）")
    lines.append(f"  現金: {player_state.cash // 10_000}万円")
    lines.append(f"  借金残高: {player_state.debt_balance // 10_000}万円")
    lines.append(f"  Free Cash: {player_state.free_cash // 10_000}万円")
    hand_names = [c.rank.name for c in sorted(player_state.hand, key=lambda c: c.rank.value)]
    lines.append(f"  手札: {', '.join(hand_names)}")

    # 署名済み未履行の契約義務一覧（帳簿ミス起因の自爆防止）
    ob_lines = _render_obligations_block(player_state.player_id, visible_state, round_num)
    lines.extend(ob_lines)

    # 生存者
    alive = visible_state.get("alive_players", [])
    lines.append(f"\n## 生存者: {', '.join(alive)}")

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
                ob_type_label = {
                    "type_a_payment": "型A金銭支払い",
                    "type_b_market": "型B市場指定",
                    "type_b_card": "型Bカード指定",
                    "type_b_no_market": "型B不参加指定",
                }.get(ob["ob_type"], ob["ob_type"])
                details_str = ""
                if "amount" in ob["details"]:
                    details_str = f'{ob["details"]["amount"] // 10_000}万円'
                elif "market_id" in ob["details"]:
                    details_str = ob["details"]["market_id"]
                elif "card_rank" in ob["details"]:
                    details_str = ob["details"]["card_rank"]
                elif "card" in ob["details"]:
                    # フォールバック: 正規化前の古いデータとの互換
                    details_str = ob["details"]["card"]
                lines.append(
                    f'    - {ob["obligor"]} → {ob["counterparty"]}: '
                    f'{ob_type_label} R{ob["round_num"]} {details_str}'
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
    lines.extend(_render_memory_block(memory))

    lines.append(f"\n## あなたの状態（{player_state.player_id}）")
    lines.append(f"  現金: {player_state.cash // 10_000}万円")
    lines.append(f"  借金: {player_state.debt_balance // 10_000}万円")
    hand_names = [c.rank.name for c in sorted(player_state.hand, key=lambda c: c.rank.value)]
    lines.append(f"  手札: {', '.join(hand_names)}")
    lines.append(f"  残りラウンド: {config.num_rounds - round_num}")

    # 署名済み未履行の契約義務一覧（帳簿ミス起因の自爆防止）
    # コミットフェイズは最重要: ここで市場・カード選択が確定し違反が決まる
    ob_lines = _render_obligations_block(player_state.player_id, visible_state, round_num)
    lines.extend(ob_lines)

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
    if last_strategy:
        lines.append(f"\n## あなたの直前の戦略メモ")
        for k, v in last_strategy.items():
            lines.append(f"  {k}: {v}")

    if config.enable_cot:
        json_example = '{"reasoning": "...", "strategy": {...}, "action": {"type": "market_commit", "market_id": "M01", "card": "ONE_PAIR"}}'
    else:
        json_example = '{"strategy": {...}, "action": {"type": "market_commit", "market_id": "M01", "card": "ONE_PAIR"}}'
    lines.append(
        f"\n交渉で合意・宣言した内容と整合するコミットを検討してください。"
        f"\nJSON形式で回答: {json_example}"
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
    lines.extend(_render_memory_block(memory))

    # 今ラウンドの市場結果（誰が何を出し、誰が勝ったか。§8.1公開情報）
    lines.extend(_render_last_round_results(
        visible_state, title=f"今ラウンド（R{round_num}）の結果",
    ))

    # 今ラウンドの会話（全件。交渉/コミットプロンプトと違い件数制限なし）
    lines.extend(_render_message_list(
        visible_state.get("messages", []), "今ラウンドの会話（全件）",
    ))

    # 契約義務（署名済み・未履行）
    lines.extend(_render_obligations_block(player_state.player_id, visible_state, round_num))

    # 契約の存在と当事者（§8.1公開情報）
    contracts = visible_state.get("contracts_public", [])
    if contracts:
        lines.append("\n## 正式契約の状況")
        for c in contracts:
            lines.append(
                f"  [{c['contract_id']}] 当事者: {', '.join(c['parties'])} / 状態: {c['status']}"
            )

    # 自分の資金状況（決着・利息計上後）
    lines.append(f"\n## あなたの状態（{player_state.player_id}）")
    lines.append(f"  現金: {player_state.cash // 10_000}万円")
    lines.append(f"  借金残高: {player_state.debt_balance // 10_000}万円")
    lines.append(f"  Free Cash: {player_state.free_cash // 10_000}万円")
    hand_names = [c.rank.name for c in sorted(player_state.hand, key=lambda c: c.rank.value)]
    lines.append(f"  残りカード: {', '.join(hand_names)}（{len(hand_names)}枚）")
    lines.append(f"  残りラウンド: {config.num_rounds - round_num}")

    lines.append(
        f"\n次のラウンド以降の自分に残したいことを{config.memory_max_chars}字以内で自由に書いてください。\n"
        "形式は自由（箇条書き・散文・表、何でも構いません）。何を書き、何を書かないかもあなたの判断です。\n"
        "例えば: 誰と何を約束したか、誰が守り誰が破ったか、次に何をするつもりか、など。\n"
        "上限を超えた分は末尾から切り詰められます。重要なものから順に書いてください\n"
        "（例: 次ラウンドの方針 → 有効な契約・未履行の約束 → 誰が守り誰が破ったか → "
        "資産/借金/手札の認識 → 重要なDMのやり取り）。\n"
        "古い情報を現在の事実として書かないでください（例: 「R3時点ではP04と同盟していた」"
        "のように、いつの情報かを明記する）。\n"
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
    remaining = config.num_rounds - round_num
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
    lines.append(f"  残りカード: {', '.join(hand_names)}（{len(hand_names)}枚）")
    lines.append(f"  残りラウンド: {remaining}")

    # 署名済み未履行の契約義務一覧（帳簿ミス起因の自爆防止）
    ob_lines = _render_obligations_block(player_state.player_id, visible_state, round_num)
    lines.extend(ob_lines)

    # 他プレイヤーの倍掛け状況
    double_ups = visible_state.get("double_ups", [])
    if double_ups:
        lines.append("\n## 他プレイヤーの倍掛け中預託")
        for du in double_ups:
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
