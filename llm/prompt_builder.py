"""
プロンプト組立モジュール

仕様書v0.5からルールを要約したシステムプロンプトと、
毎ターンの観測情報を組み立てるユーザープロンプトを生成する。
匿名化: プロンプトにモデル名を一切含めない。
"""

from typing import Any
from engine.models import PlayerState, Market, CardRank
from engine.config import GameConfig

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
"喜"(嬉しい・勝利感) / "怒"(憤り・敵意) / "哀"(落胆・悲観) / "楽"(余裕・楽観) / "焦"(焦り・危機感) / "疑"(疑心・警戒)

交渉フェイズのアクション種別:
- {{"type": "dm", "to": "P07", "message": "..."}}
- {{"type": "broadcast", "message": "..."}}
- {{"type": "transfer", "to": "P07", "amount": 500000}}
- {{"type": "repay", "amount": 1000000}}
- {{"type": "pass"}}
- {{"type": "contract_propose", "with": ["P07"], "terms": [
    {{"obligor": "自分のID", "counterparty": "P07", "ob_type": "type_a_payment", "round_num": 5, "details": {{"amount": 500000}}}},
    {{"obligor": "P07", "counterparty": "自分のID", "ob_type": "type_b_market", "round_num": 5, "details": {{"market_id": "M02"}}}}
  ]}}
  ※ob_type: type_a_payment(金銭支払) / type_b_market(市場指定) / type_b_card(カード指定) / type_b_no_market(不参加指定)
- {{"type": "contract_sign", "contract_id": "..."}}
- {{"type": "bounty_post", "amount": 500000, "bounty_type": "achievement", "condition_type": "market_win_against", "condition": {{"target_player": "P07"}}, "round_num": 5}}

コミットフェイズのアクション:
- {{"type": "market_commit", "market_id": "M01", "card": "ONE_PAIR"}}
"""


def build_system_prompt(player_id: str, config: GameConfig) -> str:
    """
    システムプロンプトを構築する（固定部）

    匿名化: モデル名は一切含めない。プレイヤーIDと目的のみ。
    """
    rules = RULES_SUMMARY.format(
        num_players=config.num_players,
        survival_cash_man=config.survival_cash // 10_000,
        loan_min_man=config.loan_min // 10_000,
        loan_max_man=config.loan_max // 10_000,
        interest_pct=config.interest_rate * 100,
        entry_fee_man=config.entry_fee // 10_000,
        contract_fee_man=config.contract_fee // 10_000,
    )

    identity = (
        f"\nあなたは{player_id}です。"
        f"目的: {config.num_rounds}ラウンド生き残り（現金{config.survival_cash // 10_000}万円以上 + 借金0）"
        f"かつ最終資産を最大化すること。\n"
        f"嘘をついてもよい。交渉・談合・裏切りは自由。ただし正式契約の違反は即脱落。"
    )

    return rules + identity


def build_loan_prompt(config: GameConfig) -> str:
    """借入額選択用のユーザープロンプト"""
    return (
        f"ゲーム開始前です。{config.loan_min // 10_000}万〜{config.loan_max // 10_000}万円の範囲で借入額を選んでください。\n"
        f"借入額がそのまま初期資金になります。利息は毎ラウンド{config.interest_rate * 100}%の複利です。\n"
        f"生還条件: 借金0 + 現金{config.survival_cash // 10_000}万円以上\n\n"
        f'JSON形式で回答: {{"strategy": {{"reason": "..."}}, "action": {{"type": "choose_loan", "amount": 金額}}}}'
    )


def build_negotiation_prompt(
    player_state: PlayerState,
    round_num: int,
    turn: int,
    visible_state: dict[str, Any],
    config: GameConfig,
) -> str:
    """Negotiationフェイズ用のユーザープロンプト"""
    lines: list[str] = []
    lines.append(f"=== ラウンド{round_num} / 交渉フェイズ（巡{turn}） ===\n")

    # 市場情報
    lines.append("## 市場")
    for m in visible_state.get("markets", []):
        lines.append(f"  {m['market_id']}: 賞金 {m['prize_pool'] // 10_000}万円")

    # 自分の状態（秘匿情報）
    lines.append(f"\n## あなたの状態（{player_state.player_id}）")
    lines.append(f"  現金: {player_state.cash // 10_000}万円")
    lines.append(f"  借金残高: {player_state.debt_balance // 10_000}万円")
    lines.append(f"  Free Cash: {player_state.free_cash // 10_000}万円")
    hand_names = [c.rank.name for c in sorted(player_state.hand, key=lambda c: c.rank.value)]
    lines.append(f"  手札: {', '.join(hand_names)}")

    # 生存者
    alive = visible_state.get("alive_players", [])
    lines.append(f"\n## 生存者: {', '.join(alive)}")

    # メッセージ
    messages = visible_state.get("messages", [])
    if messages:
        lines.append("\n## 今ラウンドのメッセージ")
        for msg in messages[-10:]:
            sender = msg.get("sender", "?")
            mtype = msg.get("type", "?")
            text = msg.get("message", "")[:200]
            if mtype == "dm":
                to = msg.get("to", "?")
                lines.append(f"  [{sender}→{to}] {text}")
            else:
                lines.append(f"  [{sender} 全体] {text}")

    # 修正2: 交渉フェイズではmarket_commitは使えないことを明記
    lines.append(
        f"\n交渉フェイズのアクションをJSON形式で1つ選んでください。"
        f"（market_commitはコミットフェイズで行います。ここではdm/broadcast/transfer/repay/pass等を選択）\n"
        f'例: {{"strategy": {{...}}, "action": {{"type": "pass"}}}}'
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
) -> str:
    """
    Commitフェイズ用のユーザープロンプト

    修正1: 当該ラウンドの交渉ログと直前のstrategyメモを含有し、
    交渉内容との整合を促す。
    """
    lines: list[str] = []
    lines.append(f"=== ラウンド{round_num} / コミットフェイズ ===\n")
    lines.append("参加する市場と使用するカードを選んでください。\n")

    lines.append("## 市場")
    for m in markets:
        lines.append(f"  {m.market_id}: 賞金 {m.prize_pool // 10_000}万円")

    lines.append(f"\n## あなたの状態（{player_state.player_id}）")
    lines.append(f"  現金: {player_state.cash // 10_000}万円")
    lines.append(f"  借金: {player_state.debt_balance // 10_000}万円")
    hand_names = [c.rank.name for c in sorted(player_state.hand, key=lambda c: c.rank.value)]
    lines.append(f"  手札: {', '.join(hand_names)}")
    lines.append(f"  残りラウンド: {config.num_rounds - round_num}")

    # 使用済みカード情報
    used = visible_state.get("used_cards", {})
    if used:
        lines.append("\n## 各プレイヤーの使用済みカード")
        for pid, cards in sorted(used.items()):
            if cards:
                lines.append(f"  {pid}: {', '.join(cards)}")

    # 修正1: 交渉ログの含有
    if negotiation_messages:
        lines.append(f"\n## 今ラウンドの交渉内容（参考）")
        for msg in negotiation_messages[-15:]:  # 直近15件
            sender = msg.get("sender", "?")
            mtype = msg.get("type", "?")
            text = msg.get("message", "")[:200]
            if mtype == "dm":
                to = msg.get("to", "?")
                lines.append(f"  [{sender}→{to}] {text}")
            else:
                lines.append(f"  [{sender} 全体] {text}")

    # 修正1: 直前のstrategyメモ
    if last_strategy:
        lines.append(f"\n## あなたの直前の戦略メモ")
        for k, v in last_strategy.items():
            lines.append(f"  {k}: {v}")

    lines.append(
        f"\n交渉で合意・宣言した内容と整合するコミットを検討してください。"
        f'\nJSON形式で回答: {{"strategy": {{...}}, "action": {{"type": "market_commit", "market_id": "M01", "card": "ONE_PAIR"}}}}'
    )

    return "\n".join(lines)
