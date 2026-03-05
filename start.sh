#!/bin/bash
# start.sh — brief-my-trade 봇 시작
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

LOG="$DIR/logs/bot.log"
PID_FILE="$DIR/logs/bot.pid"
mkdir -p "$DIR/logs" "$DIR/reports"

# venv 우선순위: 로컬 → roly-suite 공유
VENV="$DIR/.venv/bin/python"
if [ ! -f "$VENV" ]; then
    VENV="/home/node/.openclaw/workspace/roly-suite/.venv/bin/python"
fi
if [ ! -f "$VENV" ]; then
    echo "❌ Python venv 없음"
    exit 1
fi

if [ ! -f "$DIR/.env" ]; then
    echo "❌ .env 없음"
    exit 1
fi

# 이미 실행 중인지 확인
if [ -f "$PID_FILE" ]; then
    OLD_PID=$(cat "$PID_FILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "⚠️  이미 실행 중 (PID $OLD_PID)"
        exit 0
    fi
fi

echo "🚀 brief-my-trade 봇 시작..."
nohup "$VENV" -m brief_my_trade.bot >> "$LOG" 2>&1 &
BOT_PID=$!
echo "$BOT_PID" > "$PID_FILE"
echo "✅ 시작됨 (PID $BOT_PID)"
echo "📄 로그: tail -f $LOG"
