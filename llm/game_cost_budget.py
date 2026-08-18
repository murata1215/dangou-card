"""本戦1ゲーム用の共有LLMコスト予算。"""

from dataclasses import dataclass
from typing import Any


class BudgetBlockedError(Exception):
    """事前予約がcapを超え、APIを呼ばずに停止したことを表す。"""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class Reservation:
    player_id: str
    amount_usd: float


class GameCostBudget:
    """player単位とgame全体の実績・予約を一元管理する。"""

    def __init__(self, per_player_cap_usd: float, game_cap_usd: float, event_logger: Any) -> None:
        self.per_player_cap_usd = per_player_cap_usd
        self.game_cap_usd = game_cap_usd
        self._event_logger = event_logger
        self.player_spent_usd: dict[str, float] = {}
        self.player_pending_usd: dict[str, float] = {}
        self.game_spent_usd = 0.0
        self.game_pending_usd = 0.0
        self.blocks: list[dict[str, Any]] = []

    def reserve(self, player_id: str, amount_usd: float, *, round_num: int,
                phase: str, turn: int | None = None) -> Reservation:
        player_total = self.player_spent_usd.get(player_id, 0.0) + self.player_pending_usd.get(player_id, 0.0) + amount_usd
        game_total = self.game_spent_usd + self.game_pending_usd + amount_usd
        reason = ""
        if player_total > self.per_player_cap_usd:
            reason = "per_player_cap"
        elif game_total > self.game_cap_usd:
            reason = "game_cap"
        if reason:
            block = {
                "player_id": player_id, "reason": reason, "round_num": round_num,
                "phase": phase, "turn": turn,
                "player_actual_spent_usd": self.player_spent_usd.get(player_id, 0.0),
                "game_actual_spent_usd": self.game_spent_usd,
                "next_reserved_cost_usd": amount_usd,
                "per_player_game_cost_cap_usd": self.per_player_cap_usd,
                "game_cost_cap_usd": self.game_cap_usd, "api_called": False,
            }
            self.blocks.append(block)
            self._event_logger.log("LLM_BUDGET_BLOCKED", round_num, phase, data=block)
            raise BudgetBlockedError(reason)
        self.player_pending_usd[player_id] = self.player_pending_usd.get(player_id, 0.0) + amount_usd
        self.game_pending_usd += amount_usd
        return Reservation(player_id, amount_usd)

    def settle(self, reservation: Reservation, actual_cost_usd: float) -> None:
        self._release(reservation)
        self.player_spent_usd[reservation.player_id] = self.player_spent_usd.get(reservation.player_id, 0.0) + actual_cost_usd
        self.game_spent_usd += actual_cost_usd

    def release(self, reservation: Reservation) -> None:
        self._release(reservation)

    def _release(self, reservation: Reservation) -> None:
        self.player_pending_usd[reservation.player_id] = max(0.0, self.player_pending_usd.get(reservation.player_id, 0.0) - reservation.amount_usd)
        self.game_pending_usd = max(0.0, self.game_pending_usd - reservation.amount_usd)

    def snapshot(self) -> dict[str, Any]:
        return {
            "game_total_actual_cost_usd": self.game_spent_usd,
            "player_actual_cost_usd": dict(self.player_spent_usd),
            "budget_block_count": len(self.blocks),
            "first_budget_block": self.blocks[0] if self.blocks else None,
        }
