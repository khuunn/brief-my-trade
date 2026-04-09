"""
report_cron.py — 주간 자동 보고서 (cron 실행용)

crontab 예시 (매주 토요일 06:00 KST = 금요일 21:00 UTC):
  0 21 * * 5 cd /path/to/brief-my-trade && source .venv/bin/activate && python -m brief_my_trade.report_cron
"""

import os
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

from .notion import push_weekly_report_page
from .report import format_overview, generate_weekly_report
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

    # Notion 주간 보고서 — 주별 하위 페이지 생성
    try:
        from datetime import date as _date
        today_dt = _date.today()
        monday = today_dt - __import__('datetime').timedelta(days=today_dt.weekday())
        sunday = monday + __import__('datetime').timedelta(days=6)
        week_num = today_dt.isocalendar()[1]
        week_label = f"{today_dt.year}-W{week_num:02d} ({monday.strftime('%m/%d')}~{sunday.strftime('%m/%d')})"
        overview_text = format_overview(store, "week")
        pushed = push_weekly_report_page(overview_text, content, week_label)
        if pushed:
            print(f"Notion 주간 보고서 페이지 생성 완료: {week_label}")
    except Exception as e:
        print(f"Notion 업데이트 실패 (무시): {e}")

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
