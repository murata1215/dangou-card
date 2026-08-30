"""
ルールベースBot 8種パッケージ

LLMを使わない決定論的Botを提供する。
全BotはPlayerAgentを継承し、構造化インテントプロトコルで交渉する。
"""

from bots.random_bot import RandomBot
from bots.conservative import ConservativeBot
from bots.strong_card_save import StrongCardSaveBot
from bots.high_prize_hunter import HighPrizeHunterBot
from bots.empty_market_hunter import EmptyMarketHunterBot
from bots.collusion import CollusionBot
from bots.betrayal import BetrayalBot
from bots.honey_pot import HoneyPotBot
from bots.kingmaker_bot import KingmakerBot

# Bot名 → クラスのレジストリ（simulate.pyのrosterオプション用）
BOT_REGISTRY: dict[str, type] = {
    "Random": RandomBot,
    "Conservative": ConservativeBot,
    "StrongCardSave": StrongCardSaveBot,
    "HighPrizeHunter": HighPrizeHunterBot,
    "EmptyMarketHunter": EmptyMarketHunterBot,
    "Collusion": CollusionBot,
    "Betrayal": BetrayalBot,
    "HoneyPot": HoneyPotBot,
}

# デフォルトロスター: 8種×1体
DEFAULT_ROSTER: list[str] = list(BOT_REGISTRY.keys())

# v0.9 サイクル9.1: 送金専用の追加Bot（doc/analysis/free_cash_inventory_20260830.md）。
# 既定ロスター8種は送金・報奨・トレード・契約を一切行わないため、
# free_cash_mode比較のボット試合指標がモード間で完全一致してしまう。
# KingmakerBotはDEFAULT_ROSTERに含めず、--roster等で明示指定時のみ使う。
BOT_REGISTRY["Kingmaker"] = KingmakerBot
