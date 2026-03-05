#!/bin/bash
# setup.sh — brief-my-trade 초기 설치
set -euo pipefail

DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"

echo "📦 venv 생성 중..."
python3 -m venv .venv

echo "📦 패키지 설치 중..."
.venv/bin/pip install -e . -q

echo "✅ 설치 완료!"
echo ""
echo "실행: bash start.sh"
