"""
report_cron.py — 주간 자동 보고서 (cron 실행용)

crontab 예시:
  0 18 * * 5 cd /path/to/brief-my-trade && python -m brief_my_trade.report_cron
"""

import os
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

from .report import generate_weekly_report
from .store import TradeStore

load_dotenv()

DB_PATH = Path(os.environ.get("DB_PATH", "./trades.db"))
REPORT_DIR = Path(os.environ.get("REPORT_DIR", "./reports"))
REPORT_DIR.mkdir(exist_ok=True)
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")


def send_file(chat_id: str, file_path: Path, caption: str = ""):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument"
    with open(file_path, "rb") as f:
        requests.post(url, data={"chat_id": chat_id, "caption": caption}, files={"document": f})


def main():
    store = TradeStore(DB_PATH)
    today = date.today()
    content = generate_weekly_report(store)

    # 파일 저장
    filename = f"report_{today}.md"
    report_path = REPORT_DIR / filename
    report_path.write_text(content, encoding="utf-8")

    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print(f"보고서 생성 완료: {report_path}")
        return

    # 텔레그램 전송
    for chat_id in TELEGRAM_CHAT_ID.split(","):
        chat_id = chat_id.strip()
        if chat_id:
            send_file(chat_id, report_path, caption=f"📊 주간 보고서 ({today})")
    print(f"보고서 전송 완료: {filename}")


if __name__ == "__main__":
    main()
