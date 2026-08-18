#!/usr/bin/env bash
# デタッチ起動ラッパー: model_matrix.py を親プロセスから完全に切り離して起動する。
# Claude/DevRelay セッションのタイムアウト (SIGALRM) で18モデル走査が巻き込まれて
# 死ぬのを防ぐ（scripts/run_trial.sh と同じ作法）。Phase 3 は18モデルで
# 10〜30分かかるため必須。
#
# 使用例:
#   bash scripts/run_matrix.sh --phase 1 --tier weak,mid,strong --max-cost 0.08
#   bash scripts/run_matrix.sh --phase 3 --tier strong --max-cost 0.45 --max-cost-per-model 0.10
#
# 確認:
#   tail -f logs/model_matrix/run_YYYYMMDD_HHMMSS.log
#   kill -0 $(cat logs/model_matrix/run_YYYYMMDD_HHMMSS.pid) 2>/dev/null && echo running || echo done
set -euo pipefail
cd "$(dirname "$0")/.."

if [ $# -eq 0 ]; then
    echo "Usage: bash scripts/run_matrix.sh [model_matrix.py args]"
    echo "Example: bash scripts/run_matrix.sh --phase 1 --tier weak,mid,strong --max-cost 0.08"
    exit 1
fi

LOG_DIR="logs/model_matrix"
mkdir -p "$LOG_DIR"
TIMESTAMP=$(date '+%Y%m%d_%H%M%S')
LOG_FILE="$LOG_DIR/run_${TIMESTAMP}.log"
PID_FILE="$LOG_DIR/run_${TIMESTAMP}.pid"

echo "[run_matrix] Command: uv run python scripts/model_matrix.py $*"
echo "[run_matrix] Log:     $LOG_FILE"
echo "[run_matrix] PID:     $PID_FILE"

# setsid: 新セッションリーダーとして起動（親の SIGALRM/SIGHUP が届かない）
# nohup:  SIGHUP 無視（二重保険）
# &:      バックグラウンド化
setsid nohup uv run python scripts/model_matrix.py "$@" \
    > "$LOG_FILE" 2>&1 &
CHILD_PID=$!
echo "$CHILD_PID" > "$PID_FILE"

echo "[run_matrix] Started PID=$CHILD_PID (detached). Safe to close this shell."
echo "[run_matrix] Monitor:  tail -f $LOG_FILE"
