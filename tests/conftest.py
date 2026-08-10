"""
テスト用共通フィクスチャ

ゲーム状態・プレイヤー・契約の生成ヘルパーを提供する。
受け入れテスト・ユニットテストの両方で使用。
"""

import pytest
from engine.config import GameConfig
from engine.models import (
    PlayerState, Card, CardRank, Market, Contract, ContractStatus,
    Obligation, ObligationType, MarketCommit,
)
from engine.cards import create_deck
from engine.events import EventLogger


@pytest.fixture
def config_20() -> GameConfig:
    """20人版デフォルト設定"""
    return GameConfig.default_20()


@pytest.fixture
def config_12() -> GameConfig:
    """12人版テスト設定"""
    return GameConfig.default_12()


@pytest.fixture
def logger() -> EventLogger:
    """テスト用イベントロガー（固定タイムスタンプ）"""
    return EventLogger(fixed_timestamp="2026-08-09T00:00:00Z")


def make_player(
    player_id: str,
    cash: int,
    debt: int = 0,
    loan: int | None = None,
    hand: list[Card] | None = None,
) -> PlayerState:
    """
    テスト用プレイヤーを生成するヘルパー

    Args:
        player_id: プレイヤーID
        cash: 現金
        debt: 借金残高
        loan: 初期借入額（Noneならdebtと同値）
        hand: 手札（Noneなら12枚フルデッキ）
    """
    return PlayerState(
        player_id=player_id,
        cash=cash,
        debt_balance=debt,
        initial_loan=loan if loan is not None else debt,
        hand=hand if hand is not None else create_deck(),
    )


def make_contract(
    contract_id: str,
    proposer: str,
    parties: list[str],
    obligations: list[Obligation],
    round_created: int = 1,
    status: ContractStatus = ContractStatus.ACTIVE,
) -> Contract:
    """
    テスト用契約を生成するヘルパー

    Args:
        contract_id: 契約ID
        proposer: 提案者
        parties: 当事者
        obligations: 義務リスト
        round_created: 作成ラウンド
        status: 契約ステータス
    """
    return Contract(
        contract_id=contract_id,
        proposer=proposer,
        parties=parties,
        signed_by=parties,
        obligations=obligations,
        round_created=round_created,
        status=status,
    )


def make_obligation(
    obligation_id: str,
    contract_id: str,
    obligor: str,
    counterparty: str,
    ob_type: ObligationType,
    round_num: int,
    details: dict | None = None,
) -> Obligation:
    """
    テスト用義務を生成するヘルパー

    Args:
        obligation_id: 義務ID
        contract_id: 契約ID
        obligor: 義務者
        counterparty: 相手方
        ob_type: 義務種別
        round_num: 対象ラウンド
        details: 詳細パラメータ
    """
    return Obligation(
        obligation_id=obligation_id,
        contract_id=contract_id,
        obligor=obligor,
        counterparty=counterparty,
        ob_type=ob_type,
        round_num=round_num,
        details=details or {},
    )


def make_market(market_id: str, base_prize: int, carryover: int = 0) -> Market:
    """テスト用市場を生成するヘルパー"""
    return Market(market_id=market_id, base_prize=base_prize, carryover=carryover)
