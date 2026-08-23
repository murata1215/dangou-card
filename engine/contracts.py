"""
正式契約（公正証書）モジュール

§6に基づく契約管理を提供する。
- 義務単位の管理
- 型A（金銭契約・自動執行）/ 型B（行動契約・自動監査）
- Atomic執行（§6.6）
- 義務失効（§6.3）
"""

from typing import Any
import uuid

from engine.models import (
    Contract, ContractStatus, Obligation, ObligationType,
    MarketCommit, PlayerState, CardRank,
)


def create_contract(
    proposer: str,
    parties: list[str],
    terms: list[dict[str, Any]],
    round_created: int,
) -> Contract:
    """
    契約オブジェクトを生成する（署名前の状態）

    Args:
        proposer: 提案者のプレイヤーID
        parties: 全当事者（提案者含む）のプレイヤーID
        terms: 義務定義のリスト
        round_created: 提案ラウンド

    Returns:
        提案状態の契約
    """
    contract_id = f"C_{uuid.uuid4().hex[:8]}"

    obligations: list[Obligation] = []
    for i, term in enumerate(terms):
        details = dict(term.get("details", {}))

        # type_b_card 義務の details キー正規化:
        # LLMが "card" や "rank" キーで送信する場合があるため "card_rank" に統一。
        # 判定ロジック(audit_type_b)・autocommit・プロンプト表示が全て
        # "card_rank" を参照するため、入口1箇所で正規化して全消費者を修正。
        if term.get("ob_type") == "type_b_card" and "card_rank" not in details:
            if "card" in details:
                details["card_rank"] = details.pop("card")
            elif "rank" in details:
                details["card_rank"] = details.pop("rank")

        # type_b_card 義務のバリデーション:
        # 正規化しても card_rank が取得できない、または CardRank enum に
        # 存在しないrank名の場合、silent脱落トラップを防ぐため契約提案を弾く。
        # 呼び出し元のP2防御層(game.py)でキャッチされアクション不成立になる。
        if term.get("ob_type") == "type_b_card":
            rank_name = details.get("card_rank")
            if rank_name is None or rank_name not in CardRank.__members__:
                raise ValueError(
                    f"Invalid type_b_card obligation: card_rank={rank_name!r} "
                    f"(valid: {', '.join(CardRank.__members__)})"
                )

        ob = Obligation(
            obligation_id=f"{contract_id}_OB{i + 1:02d}",
            contract_id=contract_id,
            obligor=term["obligor"],
            counterparty=term["counterparty"],
            ob_type=ObligationType(term["ob_type"]),
            round_num=term["round_num"],
            details=details,
        )
        obligations.append(ob)

    return Contract(
        contract_id=contract_id,
        proposer=proposer,
        parties=parties,
        signed_by=[proposer],  # 提案者は自動署名
        obligations=obligations,
        round_created=round_created,
        status=ContractStatus.PROPOSED,
    )


def sign_contract(contract: Contract, signer: str) -> Contract:
    """
    契約に署名する

    全当事者が署名すると契約が成立（ACTIVE）になる。

    Args:
        contract: 対象契約
        signer: 署名者のプレイヤーID

    Returns:
        署名反映後の契約

    Raises:
        ValueError: 署名者が当事者でない、または既に署名済みの場合
    """
    if signer not in contract.parties:
        raise ValueError(f"{signer} is not a party of contract {contract.contract_id}")
    if signer in contract.signed_by:
        raise ValueError(f"{signer} already signed contract {contract.contract_id}")

    new_signed = list(contract.signed_by) + [signer]
    new_status = contract.status

    # 全当事者が署名したら成立
    if set(new_signed) >= set(contract.parties):
        new_status = ContractStatus.ACTIVE

    return contract.model_copy(update={
        "signed_by": new_signed,
        "status": new_status,
    })


def get_active_type_b_obligations(
    contracts: list[Contract],
    round_num: int,
) -> list[Obligation]:
    """
    指定ラウンドの有効な型B義務を取得する

    Settlement Step 3（型B監査）で使用。

    Args:
        contracts: 全契約リスト
        round_num: 対象ラウンド

    Returns:
        有効な型B義務のリスト
    """
    result: list[Obligation] = []
    for contract in contracts:
        if contract.status != ContractStatus.ACTIVE:
            continue
        for ob in contract.obligations:
            if (ob.round_num == round_num
                    and not ob.is_fulfilled
                    and not ob.is_expired
                    and ob.ob_type in (
                        ObligationType.TYPE_B_MARKET,
                        ObligationType.TYPE_B_CARD,
                        ObligationType.TYPE_B_NO_MARKET,
                    )):
                result.append(ob)
    return result


def audit_type_b(
    obligations: list[Obligation],
    commits: list[MarketCommit],
) -> list[tuple[str, Obligation]]:
    """
    型B契約（行動契約）を監査する（§5.2 Step 3）

    市場指定/カード指定/不参加指定をCommit結果と照合し、
    違反者を検出する。

    Args:
        obligations: 当該ラウンドの型B義務
        commits: 当該ラウンドの全コミット

    Returns:
        違反のリスト [(player_id, 違反した義務), ...]
    """
    # プレイヤーID→コミットの辞書
    commit_by_player: dict[str, MarketCommit] = {}
    for c in commits:
        commit_by_player[c.player_id] = c

    violations: list[tuple[str, Obligation]] = []

    for ob in obligations:
        commit = commit_by_player.get(ob.obligor)
        if commit is None:
            # コミットがない（脱落済み等）→監査スキップ
            continue

        if ob.ob_type == ObligationType.TYPE_B_MARKET:
            # 指定市場に参加しているか
            required_market = ob.details.get("market_id")
            if commit.market_id != required_market:
                violations.append((ob.obligor, ob))

        elif ob.ob_type == ObligationType.TYPE_B_CARD:
            # 指定カードを使用しているか
            required_rank = ob.details.get("card_rank")
            if commit.card.rank.name != required_rank:
                violations.append((ob.obligor, ob))

        elif ob.ob_type == ObligationType.TYPE_B_NO_MARKET:
            # 指定市場に参加していないか
            forbidden_market = ob.details.get("market_id")
            if commit.market_id == forbidden_market:
                violations.append((ob.obligor, ob))

    return violations


def get_active_type_a_obligations(
    contracts: list[Contract],
    round_num: int,
    excluded_players: set[str] | None = None,
) -> list[Obligation]:
    """
    指定ラウンドの有効な型A義務を取得する

    Settlement Step 5（型A Atomic執行）で使用。
    Step 3で脱落確定した者の義務は除外する。

    Args:
        contracts: 全契約リスト
        round_num: 対象ラウンド
        excluded_players: 除外するプレイヤーID（Step 3で脱落確定した者）

    Returns:
        有効な型A義務のリスト
    """
    excluded = excluded_players or set()
    result: list[Obligation] = []
    for contract in contracts:
        if contract.status != ContractStatus.ACTIVE:
            continue
        for ob in contract.obligations:
            if (ob.round_num == round_num
                    and not ob.is_fulfilled
                    and not ob.is_expired
                    and ob.ob_type == ObligationType.TYPE_A_PAYMENT
                    and ob.obligor not in excluded
                    and ob.counterparty not in excluded):
                result.append(ob)
    return result


def execute_type_a_atomic(
    obligations: list[Obligation],
    snapshots: dict[str, dict[str, int]],
) -> tuple[list[Obligation], list[str], dict[str, int]]:
    """
    型A契約のAtomic執行を行う（§6.6, §5.2 Step 5）

    義務者ごとに当該Settlement期限の支払義務を合算し、
    スナップショットのFreeCash以内なら全件一括執行、
    1円でも不足なら全件履行不能=脱落。

    重要: 同一Settlement内で受け取る予定の型A受取金は支払原資にできない（§6.6）

    Args:
        obligations: 当該ラウンドの型A義務
        snapshots: プレイヤーID → {"cash": int, "free_cash": int} のスナップショット
                   （§5.2 Step 4で市場賞金反映後に撮影）

    Returns:
        (更新された義務リスト, 脱落者IDリスト, 実行された支払い {player_id: 差分})
    """
    # 義務者ごとに支払合計を算出
    obligor_totals: dict[str, int] = {}
    obligor_obligations: dict[str, list[Obligation]] = {}
    for ob in obligations:
        amount = ob.details.get("amount", 0)
        obligor_totals[ob.obligor] = obligor_totals.get(ob.obligor, 0) + amount
        obligor_obligations.setdefault(ob.obligor, []).append(ob)

    failed_obligors: list[str] = []
    payments: dict[str, int] = {}  # player_id → Cash変動額（正=増加、負=減少）

    # 各義務者について判定
    for obligor, total in obligor_totals.items():
        snap = snapshots.get(obligor, {"cash": 0, "free_cash": 0})
        if total <= snap["free_cash"]:
            # 全件執行可能 → 一括支払い
            payments[obligor] = payments.get(obligor, 0) - total
            # 各受取人に加算
            for ob in obligor_obligations[obligor]:
                amount = ob.details.get("amount", 0)
                payments[ob.counterparty] = payments.get(ob.counterparty, 0) + amount
        else:
            # 1円でも不足 → 全件履行不能 → 脱落
            failed_obligors.append(obligor)

    # 義務のステータスを更新
    updated: list[Obligation] = []
    for ob in obligations:
        if ob.obligor in failed_obligors:
            # 履行不能: 義務はそのまま（脱落後の清算で失効処理）
            updated.append(ob)
        else:
            # 履行完了
            updated.append(ob.model_copy(update={"is_fulfilled": True}))

    return updated, failed_obligors, payments


def fulfill_obligations(
    contracts: list[Contract],
    fulfilled_ids: set[str],
) -> list[Contract]:
    """
    指定された義務IDの義務を履行済みにする

    Args:
        contracts: 全契約リスト
        fulfilled_ids: 履行済みにする義務ID

    Returns:
        更新された契約リスト
    """
    updated: list[Contract] = []
    for contract in contracts:
        new_obs: list[Obligation] = []
        for ob in contract.obligations:
            if ob.obligation_id in fulfilled_ids:
                new_obs.append(ob.model_copy(update={"is_fulfilled": True}))
            else:
                new_obs.append(ob)
        updated.append(contract.model_copy(update={"obligations": new_obs}))
    return updated


def get_all_type_b_for_player(
    contracts: list[Contract],
    player_id: str,
    round_num: int,
) -> list[Obligation]:
    """
    指定プレイヤーの指定ラウンドにおける有効な型B義務をすべて返す

    自動代行Commit計算（§4.4）で使用。
    契約を満たす合法Commit集合を算出するために必要。

    Args:
        contracts: 全契約リスト
        player_id: プレイヤーID
        round_num: ラウンド番号

    Returns:
        当該プレイヤーの有効な型B義務リスト
    """
    result: list[Obligation] = []
    for contract in contracts:
        if contract.status != ContractStatus.ACTIVE:
            continue
        for ob in contract.obligations:
            if (ob.obligor == player_id
                    and ob.round_num == round_num
                    and not ob.is_fulfilled
                    and not ob.is_expired
                    and ob.ob_type in (
                        ObligationType.TYPE_B_MARKET,
                        ObligationType.TYPE_B_CARD,
                        ObligationType.TYPE_B_NO_MARKET,
                    )):
                result.append(ob)
    return result


def can_cancel_contract(
    contract: Contract,
    round_num: int,
) -> tuple[bool, str | None]:
    """
    全当事者合意による契約解除（§6）の可否を導出する

    永続的な「ロック済み」状態は持たず、義務のフラグと round_num から毎回導出する。
    型B義務は正常履行でも is_fulfilled が立たない（audit_type_b は違反者のみを返す
    仕様のため）。したがって「一度でも履行/監査済み」を捕捉するには
    ob.round_num < round_num（=当該義務の期限Settlementを通過済み）も見る必要がある。

    Negotiationは当該ラウンドのCommit/Settlementより前に走るため、
    round_num == 現在R の義務はまだ未判定であり解除可能。

    Args:
        contract: 対象契約
        round_num: 現在のラウンド番号

    Returns:
        (解除可能か, 不可の場合の理由)
    """
    if contract.status != ContractStatus.ACTIVE:
        return False, f"Contract {contract.contract_id} is not active"
    for ob in contract.obligations:
        if ob.is_fulfilled:
            return False, f"Contract {contract.contract_id} has a fulfilled obligation"
        if ob.is_expired:
            return False, f"Contract {contract.contract_id} has an expired obligation"
        if ob.round_num < round_num:
            return False, (
                f"Contract {contract.contract_id} has an audited obligation "
                f"(R{ob.round_num})"
            )
    return True, None


def request_cancel(
    contract: Contract,
    requester: str,
    alive_parties: set[str],
    round_num: int,
    turn: int,
) -> tuple[Contract, bool]:
    """
    契約解除への同意を1件記録し、生存する全当事者が揃えば CANCELLED へ遷移させる

    可否判定（can_cancel_contract）は呼び出し元（engine/actions.py の validate_action）
    で propose時・最終同意時の両方に対して事前に行う。ここでは同意の集約と、
    最終同意で成立する瞬間に未処理義務を失効させる処理のみを行う。

    Args:
        contract: 対象契約
        requester: 今回同意したプレイヤーID
        alive_parties: 生存しているプレイヤーIDの集合（脱落者の同意は不要）
        round_num: 現在のラウンド番号
        turn: 現在の巡

    Returns:
        (更新された契約, 今回の同意で解除が成立したか)

    Raises:
        ValueError: requesterが当事者でない、または既に同意済みの場合
    """
    if requester not in contract.parties:
        raise ValueError(f"{requester} is not a party of contract {contract.contract_id}")
    if requester in contract.cancel_requested_by:
        raise ValueError(
            f"{requester} already requested cancel of contract {contract.contract_id}"
        )

    new_requested = list(contract.cancel_requested_by) + [requester]
    required = {p for p in contract.parties if p in alive_parties}

    if required and required <= set(new_requested):
        # 生存する全当事者の同意が揃った → 解除成立
        new_obligations = [
            ob if (ob.is_fulfilled or ob.is_expired)
            else ob.model_copy(update={"is_expired": True})
            for ob in contract.obligations
        ]
        return contract.model_copy(update={
            "cancel_requested_by": new_requested,
            "status": ContractStatus.CANCELLED,
            "cancelled_round": round_num,
            "cancelled_turn": turn,
            "obligations": new_obligations,
        }), True

    return contract.model_copy(update={"cancel_requested_by": new_requested}), False
