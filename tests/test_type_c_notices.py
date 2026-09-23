"""
v0.10 型C（条件付き金銭契約）の通知（notice）テスト

`engine/game.py`:
- `_queue_type_c_notices()`: Settlement中に呼ばれ、型C評価結果（発火/不成立）を
  当事者（obligor/counterparty）のみへ次ラウンド通知として積む
- `_phase_negotiation()` 冒頭のリセット直後で `_pending_contract_notices` を
  drainし、その回の `_contract_notices` へ繰り越す

検証観点:
- fired/not_fired の結果種別が kind="type_c_fired"/"type_c_not_met" に正しく写像される
- obligor・counterparty の両方に届き、第三者には届かない
- Settlement中に積まれた通知は即時ではなく次のNegotiation Phase開始時に配送される
  （`_pending_contract_notices` → `_contract_notices` のドレイン）
- type_c_records が空なら何もしない（S1 / type_c_enabled=False相当のno-op回帰）
"""

from engine.config import GameConfig
from engine.events import EventLogger
from engine.game import Game
from engine.negotiation import StubAgent


def _make_game(num_players: int = 4) -> Game:
    config = GameConfig.baseline_v1_s2(num_players)
    agents = {f"P{i+1:02d}": StubAgent() for i in range(num_players)}
    game = Game(config=config, agents=agents, seed=42, logger=EventLogger())
    game._setup()
    return game


def _fired_record(contract_id: str = "C1", obligor: str = "P01",
                   counterparty: str = "P02") -> dict:
    return {
        "contract_id": contract_id, "obligation_id": f"{contract_id}_OB01",
        "condition_type": "market_winner", "result": "fired", "reason": "matched",
        "obligor": obligor, "counterparty": counterparty, "amount": 300_000,
        "condition": {"market_id": "M01", "target_player": obligor},
    }


def _not_fired_record(contract_id: str = "C2", obligor: str = "P01",
                       counterparty: str = "P02") -> dict:
    return {
        "contract_id": contract_id, "obligation_id": f"{contract_id}_OB01",
        "condition_type": "eliminated", "result": "not_fired", "reason": "no match",
        "obligor": obligor, "counterparty": counterparty, "amount": 500_000,
        "condition": {"target_player": "P09"},
    }


class TestQueueTypeCNoticesKindMapping:
    """_queue_type_c_notices() の result -> kind 写像"""

    def test_fired_maps_to_type_c_fired(self):
        game = _make_game()
        game._queue_type_c_notices([_fired_record()])
        kinds = [n["kind"] for n in game._pending_contract_notices["P01"]]
        assert kinds == ["type_c_fired"]

    def test_not_fired_maps_to_type_c_not_met(self):
        game = _make_game()
        game._queue_type_c_notices([_not_fired_record()])
        kinds = [n["kind"] for n in game._pending_contract_notices["P01"]]
        assert kinds == ["type_c_not_met"]

    def test_empty_records_is_noop(self):
        """type_c_records=[]（S1 / type_c_enabled=False相当）は何もしない"""
        game = _make_game()
        game._queue_type_c_notices([])
        assert game._pending_contract_notices == {}


class TestQueueTypeCNoticesDelivery:
    """当事者（obligor/counterparty）にのみ届く"""

    def test_delivered_to_both_obligor_and_counterparty(self):
        game = _make_game()
        game._queue_type_c_notices([_fired_record(obligor="P01", counterparty="P02")])
        assert "P01" in game._pending_contract_notices
        assert "P02" in game._pending_contract_notices

    def test_not_delivered_to_third_party(self):
        game = _make_game()
        game._queue_type_c_notices([_fired_record(obligor="P01", counterparty="P02")])
        assert "P03" not in game._pending_contract_notices

    def test_notice_fields_include_contract_and_obligation_id(self):
        game = _make_game()
        game._queue_type_c_notices([_fired_record()])
        notice = game._pending_contract_notices["P01"][0]
        assert notice["contract_id"] == "C1"
        assert notice["obligation_id"] == "C1_OB01"
        assert notice["condition_type"] == "market_winner"
        assert notice["amount"] == 300_000


class TestPendingNoticesDrainedAtNextNegotiationPhase:
    """Settlement中に積んだ通知は次のNegotiation Phase開始時に配送される"""

    def test_drained_into_contract_notices_on_next_negotiation_phase(self):
        game = _make_game()
        game._queue_type_c_notices([_fired_record(obligor="P01", counterparty="P02")])
        assert game._pending_contract_notices  # まだdrainされていない
        game._phase_negotiation(2)
        # drain後はpendingが空になっている
        assert game._pending_contract_notices == {}
        state_p01 = game._build_visible_state(2, for_player_id="P01")
        kinds_p01 = [n["kind"] for n in state_p01["my_contract_notices"]]
        assert "type_c_fired" in kinds_p01
        state_p02 = game._build_visible_state(2, for_player_id="P02")
        kinds_p02 = [n["kind"] for n in state_p02["my_contract_notices"]]
        assert "type_c_fired" in kinds_p02

    def test_third_party_receives_nothing(self):
        game = _make_game()
        game._queue_type_c_notices([_fired_record(obligor="P01", counterparty="P02")])
        game._phase_negotiation(2)
        state_p03 = game._build_visible_state(2, for_player_id="P03")
        assert state_p03["my_contract_notices"] == []

    def test_no_pending_notices_is_noop_regression(self):
        """pendingが空（既存挙動 / S1相当）ならNegotiation Phase開始は何も変わらない"""
        game = _make_game()
        game._phase_negotiation(2)
        for pid in game.players:
            state = game._build_visible_state(2, for_player_id=pid)
            assert state["my_contract_notices"] == []
