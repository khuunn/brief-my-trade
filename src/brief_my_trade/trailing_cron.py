"""
trailing_cron.py — 추적손절매 크론 실행 엔트리포인트

실행:
  python -m brief_my_trade.trailing_cron --market KR
  python -m brief_my_trade.trailing_cron --market US
"""

from __future__ import annotations

import argparse
import logging
import os
from datetime import date
from pathlib import Path

import requests
from dotenv import load_dotenv

# .env 경로 명시
_ENV_PATH = Path(__file__).parent.parent.parent / ".env"
load_dotenv(_ENV_PATH, override=True)

from .price import get_current_price
from .store import TradeStore
from .trailing import init_trailing_db, upsert_stop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DB_PATH", "./trades.db"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
STOP_PCT = float(os.environ.get("TRAILING_STOP_PCT", "10.0"))
WARNING_PCT = float(os.environ.get("TRAILING_WARNING_PCT", "3.0"))


# ─── 텔레그램 전송 ────────────────────────────────────────────

def send_message(text: str, chat_id: str = None) -> None:
    cid = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not cid:
        print(text)
        return
    for c in cid.split(","):
        c = c.strip()
        if not c:
            continue
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        try:
            requests.post(url, json={"chat_id": c, "text": text, "parse_mode": "HTML"}, timeout=10)
        except Exception as e:
            logger.warning("텔레그램 전송 실패: %s", e)


# ─── 포맷 헬퍼 ────────────────────────────────────────────────

def _fmt_price(price: float, market: str) -> str:
    if market == "US":
        return f"${price:,.2f}"
    return f"{price:,.0f}"


def _status_emoji(result: dict) -> str:
    if result["triggered"]:
        return "🚨"
    if result["warning"]:
        return "⚠️"
    return "✅"


def format_stop_line(name: str, result: dict) -> str:
    """종목 trailing stop 상태 포맷 (고점/매수가 손절선 분리)"""
    mkt = result["market"]
    cur = result["current_price"]
    high = result["high_since_buy"]
    sp   = result["stop_price"]    # 고점 기준 손절선
    bs   = result["buy_stop"]      # 매수가 기준 손절선
    gh   = result.get("gap_from_high", result["gap_pct"])
    gb   = result.get("gap_from_buy",  result["gap_pct"])
    emoji = _status_emoji(result)

    def _gap(g: float) -> str:
        return f"+{g:.1f}%" if g > 0 else f"{g:.1f}%"

    return (
        f"{name} {emoji}\n"
        f"  현재 {_fmt_price(cur, mkt)}\n"
        f"  고점손절  고점 {_fmt_price(high, mkt)} → 손절선 {_fmt_price(sp, mkt)} | 여유 {_gap(gh)}\n"
        f"  매수손절  손절선 {_fmt_price(bs, mkt)} | 여유 {_gap(gb)}"
    )


# ─── 메인 ────────────────────────────────────────────────────

def run(market: str) -> None:
    init_trailing_db(DB_PATH)
    store = TradeStore(DB_PATH)
    portfolio = store.get_portfolio(market)

    if not portfolio:
        logger.info("[%s] 보유 종목 없음", market)
        send_message(f"📊 추적손절매 체크 ({market})\n보유 종목이 없습니다.")
        return

    today = date.today().isoformat()
    results: list[tuple[str, dict]] = []
    failed: list[str] = []

    for name, summary in portfolio.items():
        ticker = summary.ticker
        if not ticker:
            logger.warning("티커 없음: %s — 건너뜀", name)
            failed.append(name)
            continue

        current_price = get_current_price(ticker, market)
        if current_price is None:
            logger.warning("현재가 조회 실패: %s (%s)", name, ticker)
            failed.append(name)
            continue

        # avg_buy_price는 원화(KRW) 기준이므로, US 종목은 환율 변환 필요
        buy_price_raw = summary.avg_buy_price  # KRW 기준
        if market == "US":
            # current_price는 USD, buy_price_raw는 KRW → 동일 단위로 맞춤
            from .price import get_fx_rate
            fx = get_fx_rate("USD")
            buy_price = buy_price_raw / fx if fx > 0 else buy_price_raw
        else:
            buy_price = buy_price_raw

        result = upsert_stop(
            db_path=DB_PATH,
            ticker=ticker,
            market=market,
            current_price=current_price,
            buy_price=buy_price,
            stop_pct=STOP_PCT,
        )
        result["name"] = name
        results.append((name, result))

    if not results:
        send_message(f"📊 추적손절매 체크 ({market})\n현재가 조회 실패로 체크 불가.")
        return

    # ── 리포트 구성 ─────────────────────────────────────────

    flag = "🇰🇷 국내" if market == "KR" else "🇺🇸 미국"
    lines = [f"📊 추적손절매 현황 ({today})\n\n{flag}"]

    triggered_names: list[str] = []
    for name, r in results:
        lines.append(format_stop_line(name, r))
        if r["triggered"]:
            triggered_names.append(name)

    if failed:
        lines.append(f"\n⚠️ 조회 실패: {', '.join(failed)}")

    report_text = "\n".join(lines)
    send_message(report_text)

    # ── stop 터치 종목 별도 긴급 알림 ─────────────────────────
    for name, r in results:
        if r["triggered"]:
            mkt = r["market"]
            cur = r["current_price"]
            sp = r["stop_price"]
            bp_stop = r["buy_stop"]
            gap = r["gap_pct"]
            alert = (
                f"🚨 손절선 터치!\n\n"
                f"종목: {name} ({r['ticker']})\n"
                f"현재가: {_fmt_price(cur, mkt)}\n"
                f"고점기반 손절선: {_fmt_price(sp, mkt)}\n"
                f"매수가기반 손절선: {_fmt_price(bp_stop, mkt)}\n"
                f"여유: {gap:.1f}%\n\n"
                f"⚡ 매도 검토 필요"
            )
            send_message(alert)

    logger.info("[%s] 완료: %d종목 체크, %d종목 stop 터치", market, len(results), len(triggered_names))


def main():
    parser = argparse.ArgumentParser(description="trailing stop cron")
    parser.add_argument("--market", choices=["KR", "US"], required=True)
    args = parser.parse_args()

    run(args.market)


if __name__ == "__main__":
    main()
