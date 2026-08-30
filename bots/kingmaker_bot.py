"""
Bot 9: Kingmaker — v0.9 free_cash_mode ボット比較用の追加Bot（非標準ロスター）

サイクル9.1（doc/analysis/free_cash_inventory_20260830.md ほか）で判明した
「ルールベースBot 8種は送金・報奨・トレード・契約を一切行わない」ため
free_cash_mode を切り替えても Bot 試合の指標がビット単位で一致してしまう
問題への対応として、承認済み案Bにより追加する送金専用Bot。

仕様（実装承認時ノートより）:
  毎Rの Negotiation 冒頭（turn 1）で「次Rの強制最低返済額（利息込み）＋
  次RのEntry Fee」が現金を超える見込み（＝賞金なしなら来Rで破産する）と
  判定したら、その手番で spendable_cash の全額を、その時点で最も裕福と
  推定される自分以外の生存者へ transfer する。それ以外の手番・条件では
  既存 ConservativeBot と同じ挙動（返済のみ／最低賞金市場+カード均等消化）。

破産予測の簡略化（隠蔽情報の制約）:
  プレイヤーの現在の現金は本来 秘匿情報（§8: 自分の値以外は非公開）である
  ため、他プレイヤーの厳密な現金は参照できない。本Botは「最も裕福な生存者」
  を選ぶために、公開情報（initial_loans・last_round_results の
  prize_per_winner/winners・config.entry_fee）のみから現金を近似推定する
  （返済・利息・送金・型A決済・トレードは反映されない粗い近似）。
  本Botはデフォルトロスター（bots.BOT_REGISTRY の DEFAULT_ROSTER）には
  含めず、scripts/simulate.py 等で明示指定した場合のみ混ぜる。
"""

import math

from engine.models import (
    Action, PassAction, TransferAction, MarketCommitAction,
    PlayerState, Market,
)
from engine.config import GameConfig
from engine import player as player_ops
from bots.base import BotAgent
from bots.constants import CONSERVATIVE_LOAN


class KingmakerBot(BotAgent):
    """
    Kingmaker: 破産直前に全財産を最富裕生存者へ贈与するBot（テスト専用・非標準）

    借入・カード運用はConservativeBotと同一（最低借入・最低賞金市場・
    カード均等消化）。交渉での唯一の能動的行動は「贈与送金」のみ。
    """

    def __init__(self, seed: int = 0) -> None:
        super().__init__("Kingmaker", seed)
        self._cash_estimates: dict[str, int] | None = None
        self._estimates_round: int = 0
        self._has_given_away: bool = False

    def choose_loan(self, config: GameConfig) -> int:
        """最低借入額を選択（ConservativeBotと同一）"""
        self._config = config
        return CONSERVATIVE_LOAN

    # --- 現金推定（公開情報のみ） ---

    def _update_cash_estimates(self, player_state: PlayerState,
                                round_num: int, visible_state: dict) -> None:
        """
        公開情報のみから全プレイヤーの推定現金を更新する。

        初回: initial_loans（R1開始時 現金=借金=借入額なので正確）で初期化。
        以降: 前ラウンドのlast_round_resultsから
              -Entry Fee（生存者全員、公開: config.entry_fee）
              +市場賞金（winners/prize_per_winnerで公開）
        を加減算する。返済・利息・送金・型A決済・トレードは反映されない
        （隠蔽情報のため）近似値。自分の値だけは player_state.cash で正確値に
        上書きする。
        """
        if self._cash_estimates is None:
            self._cash_estimates = dict(visible_state.get("initial_loans", {}))
            self._estimates_round = round_num

        if round_num != self._estimates_round:
            lrr = visible_state.get("last_round_results") or {}
            if lrr.get("round") == round_num - 1:
                entry_fee = self._config.entry_fee if self._config else 0
                for pid in visible_state.get("alive_players", []):
                    if pid in self._cash_estimates:
                        self._cash_estimates[pid] -= entry_fee
                for m in lrr.get("markets", []):
                    prize = m.get("prize_per_winner", 0)
                    for w in m.get("winners", []) or []:
                        if w in self._cash_estimates:
                            self._cash_estimates[w] += prize
            self._estimates_round = round_num

        # 自分の値は正確値で上書き
        self._cash_estimates[player_state.player_id] = player_state.cash

    def _richest_other_survivor(self, player_state: PlayerState,
                                 visible_state: dict) -> str | None:
        """自分以外の生存者のうち推定現金最大の相手を返す（同額は席番号昇順）"""
        pid = player_state.player_id
        alive = [p for p in visible_state.get("alive_players", []) if p != pid]
        if not alive:
            return None
        estimates = self._cash_estimates or {}
        alive.sort(key=lambda p: (-estimates.get(p, 0), p))
        return alive[0]

    def _will_bankrupt_next_round(self, player_state: PlayerState,
                                   round_num: int, config: GameConfig) -> bool:
        """
        「次Rの強制最低返済額（利息込み）＋次RのEntry Fee」が現金を超える
        見込みかどうかを判定する（賞金収入ゼロを仮定した保守的な近似）。
        """
        if round_num >= config.num_rounds:
            return False  # 最終R自動返済のためチェック対象外
        if player_state.debt_balance <= 0:
            return False
        if not config.mandatory_repay_enabled:
            return False

        # 次R Finance時点の借金残高を「利息1回分」で近似
        projected_debt = player_state.debt_balance + math.ceil(
            player_state.debt_balance * config.interest_rate
        )
        next_round = round_num + 1
        remaining_next = config.num_rounds - next_round + 1
        min_repay_next = player_ops.compute_mandatory_repayment(
            projected_debt, remaining_next, config.mandatory_repay_k,
        )
        threshold = min_repay_next + config.entry_fee
        return player_state.cash < threshold

    def negotiate(self, player_state: PlayerState, round_num: int,
                  turn: int, visible_state: dict) -> Action:
        pid = player_state.player_id

        if self._config:
            self._update_cash_estimates(player_state, round_num, visible_state)

        # ラウンドが変わったら贈与済みフラグをリセット
        if round_num != getattr(self, "_gave_this_round", None):
            self._has_given_away = False
            self._gave_this_round = round_num

        if (turn == 1 and self._config and not self._has_given_away
                and self._will_bankrupt_next_round(player_state, round_num, self._config)):
            target = self._richest_other_survivor(player_state, visible_state)
            spendable = player_ops.spendable_cash(
                player_state, self._config, at_settlement=False,
            )
            if target is not None and spendable > 0:
                self._has_given_away = True
                return TransferAction(player_id=pid, to=target, amount=spendable)

        if self._config:
            repay = self._try_repay(player_state, round_num, self._config)
            if repay:
                return repay
        return PassAction(player_id=pid)

    def commit(self, player_state: PlayerState, markets: list[Market],
               round_num: int, visible_state: dict) -> MarketCommitAction:
        """最低賞金市場 + カード均等消化（ConservativeBotと同一）"""
        market = self._cheapest_market(markets)
        sorted_hand = self._get_cards_sorted(player_state.hand, ascending=True)
        card = sorted_hand[0] if sorted_hand else player_state.hand[0]

        return MarketCommitAction(
            player_id=player_state.player_id,
            market_id=market.market_id,
            card_rank=card.rank.name,
        )
