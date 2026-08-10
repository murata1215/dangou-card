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
