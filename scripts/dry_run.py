"""
ドライランスクリプト

12人×12ラウンドのゲームを1試合実行し、JSONLログを logs/ に出力する。
全プレイヤーはStubAgent（最低借入・交渉pass・最低カード最低市場）。

使用方法:
    uv run python scripts/dry_run.py
    uv run python scripts/dry_run.py --seed 12345
    uv run python scripts/dry_run.py --players 20
"""

import argparse
import sys
from pathlib import Path

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.config import GameConfig
from engine.events import EventLogger
from engine.negotiation import StubAgent
from engine.game import Game


def main() -> None:
    """ドライランのメインエントリーポイント"""
    parser = argparse.ArgumentParser(description="談合カード ドライラン")
    parser.add_argument("--seed", type=int, default=42, help="乱数シード（デフォルト: 42）")
    parser.add_argument("--players", type=int, default=12, help="プレイヤー数（12 or 20、デフォルト: 12）")
    parser.add_argument("--output", type=str, default=None, help="JSONLログ出力先パス")
    args = parser.parse_args()

    # --- 設定選択 ---
    if args.players == 12:
        config = GameConfig.default_12()
    elif args.players == 20:
        config = GameConfig.default_20()
    else:
        print(f"プレイヤー数は12または20を指定してください（指定値: {args.players}）")
        sys.exit(1)

    # --- エージェント生成 ---
    agents: dict[str, StubAgent] = {}
    for i in range(1, config.num_players + 1):
        pid = f"P{i:02d}"
        agents[pid] = StubAgent()

    # --- ロガー ---
    logger = EventLogger()

    # --- ゲーム実行 ---
    print(f"=== 談合カード ドライラン ===")
    print(f"プレイヤー数: {config.num_players}")
    print(f"ラウンド数: {config.num_rounds}")
    print(f"総賞金: {config.total_prize:,}円")
    print(f"生還条件: 借金0 + 現金{config.survival_cash:,}円以上")
    print(f"シード: {args.seed}")
    print(f"---")

    game = Game(config=config, agents=agents, seed=args.seed, logger=logger)
    result = game.run()

    # --- 結果表示 ---
    print(f"\n=== 結果 ===")
    print(f"生還者: {len(result.survivors)}人")
    for p in result.survivors:
        print(f"  {p.player_id}: Cash={p.cash:,}円, Debt={p.debt_balance:,}円")

    print(f"\n脱落者: {len(result.eliminated)}人")
    for p in result.eliminated:
        print(f"  {p.player_id}: 理由={p.elimination_reason}, R{p.elimination_round}")

    # --- JSONLログ出力 ---
    output_path = args.output or f"logs/dry_run_seed{args.seed}_{config.num_players}p.jsonl"
    logger.save_jsonl(output_path)
    print(f"\nJSONLログ出力: {output_path}")
    print(f"イベント数: {len(logger.events)}")


if __name__ == "__main__":
    main()
