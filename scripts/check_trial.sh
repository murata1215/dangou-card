#!/usr/bin/env bash
# トライアル進行確認: 最新の trial_* ディレクトリのラウンド・完了状態を表示する。
#
# 使用例:
#   bash scripts/check_trial.sh                    # 最新トライアル
#   bash scripts/check_trial.sh logs/llm/trial_C_20260815_221824  # 指定ディレクトリ
set -euo pipefail
cd "$(dirname "$0")/.."

# 引数があれば指定ディレクトリ、なければ最新の trial_C_*
if [ $# -ge 1 ]; then
    TRIAL_DIR="$1"
else
    TRIAL_DIR=$(ls -d logs/llm/trial_C_* 2>/dev/null | sort | tail -1)
fi

if [ -z "$TRIAL_DIR" ] || [ ! -d "$TRIAL_DIR" ]; then
    echo "No trial directory found."
    exit 1
fi

echo "Trial: $TRIAL_DIR"

# PID チェック（最新の run_*.pid）
PID_FILE=$(ls logs/llm/run_*.pid 2>/dev/null | sort | tail -1 || true)
if [ -n "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo "Process: RUNNING (PID=$PID, file=$PID_FILE)"
    else
        echo "Process: FINISHED (PID=$PID was, file=$PID_FILE)"
    fi
else
    echo "Process: No PID file found"
fi

# イベントファイルからラウンド・完了判定
EVENTS="$TRIAL_DIR/game01_events.jsonl"
if [ ! -f "$EVENTS" ]; then
    echo "No events file yet."
    exit 0
fi

python3 -c "
import json, os
with open('$EVENTS') as f:
    lr=0; ge=False; cnt=0; last_ts=''
    for l in f:
        cnt+=1
        ev=json.loads(l)
        r=ev.get('round_num',0)
        if r>lr: lr=r
        if ev.get('event_type')=='GAME_END':
            ge=True
            data=ev.get('data',{})
            survivors=data.get('survivors',[])
            eliminated=data.get('eliminated',[])
            print(f'Round: {lr}/12, Events: {cnt}, Completed: YES')
            print(f'Survivors: {len(survivors)}, Eliminated: {len(eliminated)}')
            for s in survivors:
                print(f'  {s[\"player_id\"]}: cash={s[\"cash\"]:,}')
        last_ts=ev.get('timestamp','')
    if not ge:
        print(f'Round: {lr}/12, Events: {cnt}, Completed: NO (in progress or stopped)')
    print(f'Last event: {last_ts[:19]}')
"

# 座席マップがあれば表示
SEAT_MAP="$TRIAL_DIR/game01_seat_map.json"
if [ -f "$SEAT_MAP" ]; then
    echo "Seat map:"
    python3 -c "
import json
sm=json.load(open('$SEAT_MAP'))
for pid,model in sorted(sm.items()):
    print(f'  {pid}: {model}')
"
fi

# 最新の run.log があれば末尾を表示
LOG_FILE=$(ls logs/llm/run_*.log 2>/dev/null | sort | tail -1 || true)
if [ -n "$LOG_FILE" ]; then
    echo ""
    echo "Latest log tail ($LOG_FILE):"
    tail -5 "$LOG_FILE"
fi
