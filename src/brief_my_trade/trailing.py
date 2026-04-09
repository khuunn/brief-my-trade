"""
trailing.py — 추적손절매(Trailing Stop) 핵심 로직

DB 테이블:
  trailing_stops   : 종목별 최신 trailing stop 현황
  trailing_stop_log: 날짜별 체크 로그
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Optional


# ─── DB 연결 ──────────────────────────────────────────────────

@contextmanager
def _conn(db_path: Path | str):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_trailing_db(db_path: Path | str) -> None:
    """trailing_stops / trailing_stop_log 테이블 생성"""
    with _conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trailing_stops (
                ticker          TEXT NOT NULL,
                market          TEXT NOT NULL,
                high_since_buy  REAL NOT NULL,
                stop_pct        REAL NOT NULL DEFAULT 10.0,
                stop_price      REAL NOT NULL,
                buy_price       REAL NOT NULL,
                buy_stop        REAL NOT NULL,
                last_updated    TEXT NOT NULL,
                PRIMARY KEY (ticker, market)
            );

            CREATE TABLE IF NOT EXISTS trailing_stop_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker          TEXT NOT NULL,
                market          TEXT NOT NULL,
                date            TEXT NOT NULL,
                current_price   REAL NOT NULL,
                high_since_buy  REAL NOT NULL,
                stop_price      REAL NOT NULL,
                buy_stop        REAL NOT NULL,
                gap_pct         REAL NOT NULL,
                triggered       INTEGER NOT NULL DEFAULT 0
            );

            CREATE INDEX IF NOT EXISTS idx_tslog_ticker ON trailing_stop_log(ticker, market);
            CREATE INDEX IF NOT EXISTS idx_tslog_date   ON trailing_stop_log(date);
        """)


def upsert_stop(
    db_path: Path | str,
    ticker: str,
    market: str,
    current_price: float,
    buy_price: float,
    stop_pct: float = 10.0,
) -> dict:
    """
    고점 갱신 + stop_price 재계산 + 로그 저장.

    Returns:
        dict with keys: ticker, market, current_price, high_since_buy,
                        stop_price, buy_stop, gap_pct, triggered, warning
    """
    init_trailing_db(db_path)
    today = date.today().isoformat()

    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM trailing_stops WHERE ticker=? AND market=?",
            (ticker, market),
        ).fetchone()

        if row:
            # 고점 갱신
            high = max(float(row["high_since_buy"]), current_price)
            # buy_price 업데이트 (포지션 추가 매수 등 반영 가능하도록)
            bp = buy_price if buy_price and buy_price > 0 else float(row["buy_price"])
        else:
            high = current_price
            bp = buy_price

        stop_price = high * (1 - stop_pct / 100)
        buy_stop = bp * (1 - stop_pct / 100)

        # gap_pct: 현재가가 가장 가까운 stop선으로부터 얼마나 여유가 있나
        # 양수 = 여유 있음, 음수 = 이미 터치
        gap_from_high = (current_price - stop_price) / stop_price * 100
        gap_from_buy  = (current_price - buy_stop)  / buy_stop  * 100
        gap_pct = min(gap_from_high, gap_from_buy)

        triggered = gap_pct <= 0
        warning   = (not triggered) and (gap_pct <= 3.0)

        # upsert trailing_stops
        conn.execute("""
            INSERT INTO trailing_stops
                (ticker, market, high_since_buy, stop_pct, stop_price, buy_price, buy_stop, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, market) DO UPDATE SET
                high_since_buy = excluded.high_since_buy,
                stop_pct       = excluded.stop_pct,
                stop_price     = excluded.stop_price,
                buy_price      = excluded.buy_price,
                buy_stop       = excluded.buy_stop,
                last_updated   = excluded.last_updated
        """, (ticker, market, high, stop_pct, stop_price, bp, buy_stop, today))

        # 로그 기록 (같은 날 중복 허용 — 여러 번 체크 가능)
        conn.execute("""
            INSERT INTO trailing_stop_log
                (ticker, market, date, current_price, high_since_buy, stop_price, buy_stop, gap_pct, triggered)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (ticker, market, today, current_price, high, stop_price, buy_stop,
              gap_pct, int(triggered)))

    return {
        "ticker": ticker,
        "market": market,
        "current_price": current_price,
        "high_since_buy": high,
        "stop_price": stop_price,
        "buy_price": bp,
        "buy_stop": buy_stop,
        "gap_pct": gap_pct,
        "gap_from_high": gap_from_high,
        "gap_from_buy": gap_from_buy,
        "triggered": triggered,
        "warning": warning,
        "stop_pct": stop_pct,
    }


def get_all_stops(db_path: Path | str) -> list[dict]:
    """전체 trailing stop 현황 조회"""
    init_trailing_db(db_path)
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT * FROM trailing_stops ORDER BY market, ticker"
        ).fetchall()
        return [dict(r) for r in rows]


def get_stop(db_path: Path | str, ticker: str, market: str) -> Optional[dict]:
    """특정 종목 trailing stop 조회"""
    init_trailing_db(db_path)
    with _conn(db_path) as conn:
        row = conn.execute(
            "SELECT * FROM trailing_stops WHERE ticker=? AND market=?",
            (ticker, market),
        ).fetchone()
        return dict(row) if row else None


def check_triggered(db_path: Path | str, ticker: str, market: str, current_price: float) -> bool:
    """현재가 기준으로 stop 터치 여부만 반환"""
    stop = get_stop(db_path, ticker, market)
    if not stop:
        return False
    triggered_high = current_price <= stop["stop_price"]
    triggered_buy  = current_price <= stop["buy_stop"]
    return triggered_high or triggered_buy
