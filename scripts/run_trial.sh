#!/usr/bin/env bash
# デタッチ起動ラッパー: llm_trial.py を親プロセスから完全に切り離して起動する。
# Claude/DevRelay セッションのタイムアウト (SIGALRM) で試合が巻き込まれて死ぬのを防ぐ。
#
# 使用例:
#   bash scripts/run_trial.sh --phase C --ruleset S2 --roster "M3,M3,M3,L2,L2,L2,M6,M6,M6" --games 1 --seed 504
#
# 確認:
#   bash scripts/check_trial.sh          # 最新トライアルの進行状況
#   tail -f logs/llm/run_YYYYMMDD_HHMMSS.log  # リアルタイムログ
#   kill -0 $(cat logs/llm/run_YYYYMMDD_HHMMSS.pid) 2>/dev/null && echo running || echo done
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -eq 0 ]; then
    echo "Usage: bash scripts/run_trial.sh [llm_trial.py args]"
    echo "Example: bash scripts/run_trial.sh --phase C --ruleset S2 --roster \"M3,M3,M3,L2,L2,L2,M6,M6,M6\" --games 1 --seed 504"
    exit 1
fi

LOG_DIR="logs/llm"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
LOG_FILE="$LOG_DIR/run_${TIMESTAMP}.log"
PID_FILE="$LOG_DIR/run_${TIMESTAMP}.pid"

echo "[run_trial] Command: uv run python scripts/llm_trial.py $*"
echo "[run_trial] Log:     $LOG_FILE"
echo "[run_trial] PID:     $PID_FILE"

# setsid: 新セッションリーダーとして起動（親の SIGALRM/SIGHUP が届かない）
# nohup:  SIGHUP 無視（二重保険）
# &:      バックグラウンド化
setsid nohup uv run python scripts/llm_trial.py "$@" \
    > "$LOG_FILE" 2>&1 &
CHILD_PID=$!
echo "$CHILD_PID" > "$PID_FILE"

echo "[run_trial] Started PID=$CHILD_PID (detached). Safe to close this shell."
echo "[run_trial] Monitor:  tail -f $LOG_FILE"
echo "[run_trial] Check:    bash scripts/check_trial.sh"
