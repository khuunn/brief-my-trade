"""
store.py — SQLite 저장소 (거래 + 자본금 + 환율 캐시)
"""

from __future__ import annotations

import csv
import io
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

DB_PATH = Path("./trades.db")
EPOCH_DATE = "2000-01-01"  # 전체기간 조회 시 시작일


# ─── 데이터 클래스 ────────────────────────────────────────────

@dataclass
class Trade:
    id: Optional[int]
    date: str                  # YYYY-MM-DD
    time: str                  # HH:MM
    market: str                # 'KR' | 'US'
    ticker: str                # 종목코드
    name: str                  # 종목명
    side: str                  # '매수' | '매도'
    qty: int
    price: float               # 체결 단가 (원화 or 외화)
    amount: float              # qty × price (원화 or 외화)
    currency: str              # 'KRW' | 'USD'
    fx_rate: float             # 체결 당시 환율 (KRW/USD), KRW=1.0
    amount_krw: float          # 원화 환산 금액
    commission: float = 0.0
    tax: float = 0.0
    memo: str = ""
    created_at: str = ""


@dataclass
class CapitalEvent:
    id: Optional[int]
    date: str                  # YYYY-MM-DD
    market: str                # 'KR' | 'US'
    type: str                  # 'initial' | 'deposit' | 'withdraw'
    amount_krw: float          # 원화 기준 금액
    currency: str = "KRW"
    fx_rate: float = 1.0
    memo: str = ""


@dataclass
class StockSummary:
    name: str
    ticker: str
    market: str
    currency: str
    buy_qty: int = 0
    buy_amount: float = 0.0
    sell_qty: int = 0
    sell_amount: float = 0.0
    commission: float = 0.0
    tax: float = 0.0
    trade_count: int = 0

    @property
    def net_qty(self) -> int:
        return self.buy_qty - self.sell_qty

    @property
    def avg_buy_price(self) -> float:
        return self.buy_amount / self.buy_qty if self.buy_qty > 0 else 0.0

    @property
    def avg_sell_price(self) -> float:
        return self.sell_amount / self.sell_qty if self.sell_qty > 0 else 0.0

    @property
    def realized_pnl(self) -> float:
        if self.sell_qty == 0 or self.buy_qty == 0:
            return 0.0
        cost = self.avg_buy_price * self.sell_qty
        return self.sell_amount - cost - self.commission - self.tax


# ─── TradeStore ───────────────────────────────────────────────

DEFAULT_ALIASES = {
    # 국내
    "삼전":   ("삼성전자", "005930", "KR"),
    "하닉":   ("SK하이닉스", "000660", "KR"),
    "네이버": ("NAVER", "035420", "KR"),
    "카카오": ("카카오", "035720", "KR"),
    "현차":   ("현대차", "005380", "KR"),
    "기아":   ("기아", "000270", "KR"),
    "엘화":   ("LG화학", "051910", "KR"),
    "삼SDI":  ("삼성SDI", "006400", "KR"),
    "셀트":   ("셀트리온", "068270", "KR"),
    "포스코": ("POSCO홀딩스", "005490", "KR"),
    "KB":     ("KB금융", "105560", "KR"),
    "신한":   ("신한지주", "055550", "KR"),
    # 해외
    "엔비":   ("NVIDIA", "NVDA", "US"),
    "애플":   ("Apple", "AAPL", "US"),
    "테슬":   ("Tesla", "TSLA", "US"),
    "마소":   ("Microsoft", "MSFT", "US"),
    "구글":   ("Alphabet", "GOOGL", "US"),
    "아마존": ("Amazon", "AMZN", "US"),
    "메타":   ("Meta", "META", "US"),
}


class TradeStore:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS trades (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    date        TEXT NOT NULL,
                    time        TEXT DEFAULT '00:00',
                    market      TEXT DEFAULT 'KR',
                    ticker      TEXT DEFAULT '',
                    name        TEXT NOT NULL,
                    side        TEXT NOT NULL,
                    qty         INTEGER NOT NULL,
                    price       REAL NOT NULL,
                    amount      REAL NOT NULL,
                    currency    TEXT DEFAULT 'KRW',
                    fx_rate     REAL DEFAULT 1.0,
                    amount_krw  REAL NOT NULL,
                    commission  REAL DEFAULT 0,
                    tax         REAL DEFAULT 0,
                    memo        TEXT DEFAULT '',
                    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
                );

                CREATE INDEX IF NOT EXISTS idx_trades_date   ON trades(date);
                CREATE INDEX IF NOT EXISTS idx_trades_name   ON trades(name);
                CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market);

                CREATE TABLE IF NOT EXISTS capital_events (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    date        TEXT NOT NULL,
                    market      TEXT NOT NULL,
                    type        TEXT NOT NULL,
                    amount_krw  REAL NOT NULL,
                    currency    TEXT DEFAULT 'KRW',
                    fx_rate     REAL DEFAULT 1.0,
                    memo        TEXT DEFAULT '',
                    created_at  TEXT DEFAULT (datetime('now', 'localtime'))
                );

                CREATE TABLE IF NOT EXISTS stock_aliases (
                    alias   TEXT PRIMARY KEY,
                    name    TEXT NOT NULL,
                    ticker  TEXT DEFAULT '',
                    market  TEXT DEFAULT 'KR'
                );

                CREATE TABLE IF NOT EXISTS fx_rates (
                    date     TEXT NOT NULL,
                    currency TEXT NOT NULL,
                    rate     REAL NOT NULL,
                    PRIMARY KEY (date, currency)
                );
            """)
            # 기본 별칭 삽입
            for alias, (name, ticker, market) in DEFAULT_ALIASES.items():
                conn.execute(
                    "INSERT OR IGNORE INTO stock_aliases (alias, name, ticker, market) VALUES (?, ?, ?, ?)",
                    (alias, name, ticker, market),
                )

    # ── 거래 CRUD ────────────────────────────────────────────

    def add_trade(self, trade: Trade) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO trades
                   (date, time, market, ticker, name, side, qty, price, amount,
                    currency, fx_rate, amount_krw, commission, tax, memo)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (trade.date, trade.time, trade.market, trade.ticker, trade.name,
                 trade.side, trade.qty, trade.price, trade.amount,
                 trade.currency, trade.fx_rate, trade.amount_krw,
                 trade.commission, trade.tax, trade.memo),
            )
            return cur.lastrowid

    def delete_trade(self, trade_id: int) -> bool:
        with self._conn() as conn:
            cur = conn.execute("DELETE FROM trades WHERE id=?", (trade_id,))
            return cur.rowcount > 0

    def undo_last(self) -> Optional[Trade]:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM trades ORDER BY created_at DESC LIMIT 1"
            ).fetchone()
            if not row:
                return None
            trade = self._row_to_trade(row)
            conn.execute("DELETE FROM trades WHERE id=?", (trade.id,))
            return trade

    def get_trades_by_date_range(self, start: str, end: str, market: str = None) -> list[Trade]:
        with self._conn() as conn:
            if market:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE date >= ? AND date <= ? AND market=? ORDER BY date, time",
                    (start, end, market),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM trades WHERE date >= ? AND date <= ? ORDER BY date, time",
                    (start, end),
                ).fetchall()
            return [self._row_to_trade(r) for r in rows]

    def get_today_trades(self) -> list[Trade]:
        today = date.today().isoformat()
        return self.get_trades_by_date_range(today, today)

    def get_week_trades(self, ref: str = None) -> list[Trade]:
        ref_date = date.fromisoformat(ref) if ref else date.today()
        monday = ref_date - timedelta(days=ref_date.weekday())
        friday = monday + timedelta(days=4)
        return self.get_trades_by_date_range(monday.isoformat(), friday.isoformat())

    def _row_to_trade(self, row) -> Trade:
        return Trade(
            id=row["id"], date=row["date"], time=row["time"],
            market=row["market"], ticker=row["ticker"], name=row["name"],
            side=row["side"], qty=row["qty"], price=row["price"],
            amount=row["amount"], currency=row["currency"],
            fx_rate=row["fx_rate"], amount_krw=row["amount_krw"],
            commission=row["commission"], tax=row["tax"],
            memo=row["memo"], created_at=row["created_at"],
        )

    # ── 집계 ─────────────────────────────────────────────────

    def summarize_trades(self, trades: list[Trade]) -> dict[str, StockSummary]:
        summaries: dict[str, StockSummary] = {}
        for t in trades:
            key = t.name
            if key not in summaries:
                summaries[key] = StockSummary(
                    name=t.name, ticker=t.ticker,
                    market=t.market, currency=t.currency,
                )
            s = summaries[key]
            s.trade_count += 1
            s.commission += t.commission
            s.tax += t.tax
            if t.side == "매수":
                s.buy_qty += t.qty
                s.buy_amount += t.amount
            else:
                s.sell_qty += t.qty
                s.sell_amount += t.amount
        return summaries

    def get_portfolio(self, market: str = None) -> dict[str, StockSummary]:
        """미청산 포지션 (net_qty > 0)"""
        end = date.today().isoformat()
        trades = self.get_trades_by_date_range(EPOCH_DATE, end, market)
        summaries = self.summarize_trades(trades)
        return {k: v for k, v in summaries.items() if v.net_qty > 0}

    def get_period_stats(self, start: str, end: str, market: str = None) -> dict:
        trades = self.get_trades_by_date_range(start, end, market)
        real_trades = [t for t in trades if t.memo != "seed"]
        total_buy_krw = sum(t.amount_krw for t in real_trades if t.side == "매수")
        total_sell_krw = sum(t.amount_krw for t in real_trades if t.side == "매도")
        summaries = self.summarize_trades(real_trades)
        realized_by_currency: dict[str, float] = {}
        for s in summaries.values():
            cur = s.currency
            realized_by_currency[cur] = realized_by_currency.get(cur, 0) + s.realized_pnl
        return {
            "trade_count": len(trades),
            "real_trade_count": len(real_trades),
            "total_buy_krw": total_buy_krw,
            "total_sell_krw": total_sell_krw,
            "realized_by_currency": realized_by_currency,
            "summaries": summaries,
        }

    # ── 자본금 ────────────────────────────────────────────────

    def add_capital_event(self, event: CapitalEvent) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO capital_events (date, market, type, amount_krw, currency, fx_rate, memo)
                   VALUES (?,?,?,?,?,?,?)""",
                (event.date, event.market, event.type,
                 event.amount_krw, event.currency, event.fx_rate, event.memo),
            )
            return cur.lastrowid

    def get_capital(self, market: str = None) -> dict[str, float]:
        """시장별 누적 자본금 (원화 기준)"""
        with self._conn() as conn:
            if market:
                rows = conn.execute(
                    "SELECT market, type, amount_krw FROM capital_events WHERE market=?",
                    (market,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT market, type, amount_krw FROM capital_events"
                ).fetchall()
        result: dict[str, float] = {}
        for row in rows:
            m = row["market"]
            amt = row["amount_krw"]
            if row["type"] == "withdraw":
                amt = -amt
            result[m] = result.get(m, 0) + amt
        return result

    # ── 환율 캐시 ─────────────────────────────────────────────

    def cache_fx_rate(self, currency: str, rate: float, on_date: str = None):
        d = on_date or date.today().isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO fx_rates (date, currency, rate) VALUES (?,?,?)",
                (d, currency, rate),
            )

    def get_cached_fx_rate(self, currency: str, on_date: str = None) -> Optional[float]:
        d = on_date or date.today().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                "SELECT rate FROM fx_rates WHERE date=? AND currency=?", (d, currency)
            ).fetchone()
            return row["rate"] if row else None

    # ── 별칭 ─────────────────────────────────────────────────

    def resolve_name(self, raw: str) -> tuple[str, str, str]:
        """줄임말 → (정식명, 티커, 마켓)"""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT name, ticker, market FROM stock_aliases WHERE alias=?", (raw,)
            ).fetchone()
            if row:
                return row["name"], row["ticker"], row["market"]
        # 숫자 6자리면 국내 종목코드로 간주
        if raw.isdigit() and len(raw) == 6:
            return raw, raw, "KR"
        # 영문 대문자면 해외
        if raw.isupper() and raw.isalpha():
            return raw, raw, "US"
        return raw, "", "KR"

    def add_alias(self, alias: str, name: str, ticker: str = "", market: str = "KR"):
        with self._conn() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO stock_aliases (alias, name, ticker, market) VALUES (?,?,?,?)",
                (alias, name, ticker, market),
            )

    def export_csv(self, start: str = None, end: str = None) -> str:
        s = start or EPOCH_DATE
        e = end or date.today().isoformat()
        trades = self.get_trades_by_date_range(s, e)
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["날짜","시간","시장","종목명","티커","구분","수량","단가","금액","통화","환율","원화금액","수수료","세금","메모"])
        for t in trades:
            writer.writerow([
                t.date, t.time, t.market, t.name, t.ticker,
                t.side, t.qty, t.price, t.amount,
                t.currency, t.fx_rate, t.amount_krw,
                t.commission, t.tax, t.memo,
            ])
        return buf.getvalue()
