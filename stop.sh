#!/bin/bash
DIR="$(cd "$(dirname "$0")" && pwd)"
PID_FILE="$DIR/logs/bot.pid"

if [ ! -f "$PID_FILE" ]; then
    echo "PID 파일 없음"
    exit 0
fi

PID=$(cat "$PID_FILE")
if kill -0 "$PID" 2>/dev/null; then
    kill "$PID"
    rm -f "$PID_FILE"
    echo "✅ 봇 종료 (PID $PID)"
else
    echo "이미 종료됨"
    rm -f "$PID_FILE"
fi
