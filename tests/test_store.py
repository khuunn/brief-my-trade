"""
test_store.py — TradeStore 단위 테스트
"""
import pytest
from datetime import date
from brief_my_trade.store import Trade, TradeStore, CapitalEvent


# ── CRUD ─────────────────────────────────────────────────────

def test_add_and_get_trade(store, sample_kr_trade):
    trade_id = store.add_trade(sample_kr_trade)
    assert trade_id == 1

    trades = store.get_today_trades()
    # 오늘 날짜와 다를 수 있으니 날짜 범위로 조회
    trades = store.get_trades_by_date_range("2026-03-05", "2026-03-05")
    assert len(trades) == 1
    assert trades[0].name == "삼성전자"
    assert trades[0].qty == 10
    assert trades[0].price == 58000.0


def test_undo_last(store, sample_kr_trade):
    store.add_trade(sample_kr_trade)
    undone = store.undo_last()
    assert undone is not None
    assert undone.name == "삼성전자"

    trades = store.get_trades_by_date_range("2026-03-05", "2026-03-05")
    assert len(trades) == 0


def test_undo_empty(store):
    result = store.undo_last()
    assert result is None


# ── summarize_trades ─────────────────────────────────────────

def test_summarize_buy_only(store, sample_kr_trade):
    store.add_trade(sample_kr_trade)
    trades = store.get_trades_by_date_range("2026-03-05", "2026-03-05")
    summaries = store.summarize_trades(trades)

    assert "삼성전자" in summaries
    s = summaries["삼성전자"]
    assert s.buy_qty == 10
    assert s.buy_amount == 580000.0
    assert s.sell_qty == 0
    assert s.net_qty == 10
    assert s.avg_buy_price == 58000.0


def test_summarize_realized_pnl(store):
    """매수 후 매도 → 실현손익 계산"""
    buy = Trade(
        id=None, date="2026-03-01", time="09:00",
        market="KR", ticker="005930", name="삼성전자",
        side="매수", qty=10, price=50000.0,
        amount=500000.0, currency="KRW",
        fx_rate=1.0, amount_krw=500000.0,
    )
    sell = Trade(
        id=None, date="2026-03-05", time="14:00",
        market="KR", ticker="005930", name="삼성전자",
        side="매도", qty=10, price=60000.0,
        amount=600000.0, currency="KRW",
        fx_rate=1.0, amount_krw=600000.0,
        commission=500.0, tax=200.0,
    )
    store.add_trade(buy)
    store.add_trade(sell)
    trades = store.get_trades_by_date_range("2026-03-01", "2026-03-05")
    summaries = store.summarize_trades(trades)

    s = summaries["삼성전자"]
    assert s.net_qty == 0
    # 실현손익 = 매도금액 - 매수단가*매도수량 - 수수료 - 세금
    # = 600000 - 500000 - 500 - 200 = 99300
    assert s.realized_pnl == pytest.approx(99300.0)


# ── get_portfolio ─────────────────────────────────────────────

def test_portfolio_excludes_fully_sold(store):
    buy = Trade(
        id=None, date="2026-03-01", time="09:00",
        market="KR", ticker="005930", name="삼성전자",
        side="매수", qty=5, price=50000.0,
        amount=250000.0, currency="KRW", fx_rate=1.0, amount_krw=250000.0,
    )
    sell = Trade(
        id=None, date="2026-03-05", time="14:00",
        market="KR", ticker="005930", name="삼성전자",
        side="매도", qty=5, price=60000.0,
        amount=300000.0, currency="KRW", fx_rate=1.0, amount_krw=300000.0,
    )
    store.add_trade(buy)
    store.add_trade(sell)
    portfolio = store.get_portfolio("KR")
    assert "삼성전자" not in portfolio


def test_portfolio_includes_partial_position(store, sample_kr_trade):
    store.add_trade(sample_kr_trade)
    sell = Trade(
        id=None, date="2026-03-05", time="14:00",
        market="KR", ticker="005930", name="삼성전자",
        side="매도", qty=3, price=60000.0,
        amount=180000.0, currency="KRW", fx_rate=1.0, amount_krw=180000.0,
    )
    store.add_trade(sell)
    portfolio = store.get_portfolio("KR")
    assert "삼성전자" in portfolio
    assert portfolio["삼성전자"].net_qty == 7


# ── get_period_stats ──────────────────────────────────────────

def test_period_stats_excludes_seed(store):
    """seed 거래는 real_trade_count에서 제외"""
    seed = Trade(
        id=None, date="2026-03-05", time="00:00",
        market="KR", ticker="005930", name="삼성전자",
        side="매수", qty=10, price=58000.0,
        amount=580000.0, currency="KRW", fx_rate=1.0, amount_krw=580000.0,
        memo="seed",
    )
    real = Trade(
        id=None, date="2026-03-05", time="10:00",
        market="KR", ticker="000660", name="SK하이닉스",
        side="매수", qty=5, price=180000.0,
        amount=900000.0, currency="KRW", fx_rate=1.0, amount_krw=900000.0,
    )
    store.add_trade(seed)
    store.add_trade(real)

    stats = store.get_period_stats("2026-03-05", "2026-03-05")
    assert stats["trade_count"] == 2       # 전체 (seed 포함)
    assert stats["real_trade_count"] == 1  # seed 제외


# ── alias / resolve ───────────────────────────────────────────

def test_resolve_builtin_alias(store):
    name, ticker, market = store.resolve_name("삼전")
    assert name == "삼성전자"
    assert ticker == "005930"
    assert market == "KR"


def test_resolve_kr_numeric_code(store):
    name, ticker, market = store.resolve_name("005930")
    assert ticker == "005930"
    assert market == "KR"


def test_resolve_us_ticker(store):
    name, ticker, market = store.resolve_name("AAPL")
    assert ticker == "AAPL"
    assert market == "US"


def test_add_custom_alias(store):
    store.add_alias("하닉", "SK하이닉스", "000660", "KR")
    name, ticker, market = store.resolve_name("하닉")
    assert name == "SK하이닉스"
    assert ticker == "000660"


# ── export_csv ────────────────────────────────────────────────

def test_export_csv_comma_in_name(store):
    """종목명에 콤마 있어도 CSV가 깨지지 않아야 함"""
    trade = Trade(
        id=None, date="2026-03-05", time="10:00",
        market="KR", ticker="TEST", name="테스트,종목",
        side="매수", qty=1, price=1000.0,
        amount=1000.0, currency="KRW", fx_rate=1.0, amount_krw=1000.0,
    )
    store.add_trade(trade)
    csv_out = store.export_csv()
    lines = csv_out.strip().splitlines()
    assert len(lines) == 2  # 헤더 + 1건
    # csv 모듈이 콤마 포함 필드를 따옴표로 감쌌는지 확인
    assert '"테스트,종목"' in lines[1]


# ── 혼재 통화 (US 종목 seed=KRW + 체결=USD) ───────────────────

def test_mixed_currency_avg_price(store):
    """
    US 종목: seed(KRW) + 신규체결(USD) 혼재 시 평균단가가 KRW로 정확히 계산돼야 함.
    ADBE seed 2주 @413,292원 + 체결 1주 @USD 283.22 (fx=1450 → 410,669원)
    기대 avg_buy_price = (826,584 + 410,669) / 3 = 412,418원
    """
    seed = Trade(
        id=None, date="2026-03-01", time="00:00",
        market="US", ticker="ADBE", name="어도비",
        side="매수", qty=2, price=413292.0,
        amount=826584.0, currency="KRW",
        fx_rate=1.0, amount_krw=826584.0,
        memo="seed",
    )
    new_buy = Trade(
        id=None, date="2026-03-05", time="22:00",
        market="US", ticker="ADBE", name="어도비",
        side="매수", qty=1, price=283.22,
        amount=283.22, currency="USD",
        fx_rate=1450.0, amount_krw=283.22 * 1450.0,
    )
    store.add_trade(seed)
    store.add_trade(new_buy)
    trades = store.get_trades_by_date_range("2026-01-01", "2026-12-31")
    summaries = store.summarize_trades(trades)

    s = summaries["어도비"]
    assert s.buy_qty == 3
    expected_avg = (826584.0 + 283.22 * 1450.0) / 3
    assert s.avg_buy_price == pytest.approx(expected_avg, rel=1e-3)


def test_mixed_currency_realized_pnl(store):
    """
    혼재 통화 종목 매도 시 실현손익도 KRW 기준으로 정확해야 함.
    seed 2주 @413,292원 + 체결 1주 @USD 283.22 (fx=1450)
    전량 매도 3주 @USD 300 (fx=1450 → 435,000원)
    실현손익 = 매도금액(KRW) - 평균매수단가(KRW)*수량
    """
    seed = Trade(
        id=None, date="2026-03-01", time="00:00",
        market="US", ticker="ADBE", name="어도비",
        side="매수", qty=2, price=413292.0,
        amount=826584.0, currency="KRW",
        fx_rate=1.0, amount_krw=826584.0, memo="seed",
    )
    buy = Trade(
        id=None, date="2026-03-05", time="10:00",
        market="US", ticker="ADBE", name="어도비",
        side="매수", qty=1, price=283.22,
        amount=283.22, currency="USD",
        fx_rate=1450.0, amount_krw=283.22 * 1450.0,
    )
    sell = Trade(
        id=None, date="2026-03-05", time="22:00",
        market="US", ticker="ADBE", name="어도비",
        side="매도", qty=3, price=300.0,
        amount=900.0, currency="USD",
        fx_rate=1450.0, amount_krw=900.0 * 1450.0,
    )
    store.add_trade(seed)
    store.add_trade(buy)
    store.add_trade(sell)
    trades = store.get_trades_by_date_range("2026-01-01", "2026-12-31")
    summaries = store.summarize_trades(trades)

    s = summaries["어도비"]
    total_buy_krw = 826584.0 + 283.22 * 1450.0
    sell_krw = 900.0 * 1450.0
    expected_pnl = sell_krw - total_buy_krw
    assert s.realized_pnl == pytest.approx(expected_pnl, rel=1e-3)


# ── capital ───────────────────────────────────────────────────

def test_get_cash_after_buy(store):
    """초기자본 - 매수금액 = 예수금"""
    store.add_capital_event(CapitalEvent(
        id=None, date="2026-01-01", market="KR",
        type="initial", amount_krw=10_000_000.0,
    ))
    buy = Trade(
        id=None, date="2026-03-05", time="10:00",
        market="KR", ticker="005930", name="삼성전자",
        side="매수", qty=10, price=100_000.0,
        amount=1_000_000.0, currency="KRW",
        fx_rate=1.0, amount_krw=1_000_000.0,
    )
    store.add_trade(buy)
    cash = store.get_cash("KR")
    assert cash["KR"] == pytest.approx(9_000_000.0)


def test_get_cash_after_sell(store):
    """매수 후 매도 → 예수금 반영"""
    store.add_capital_event(CapitalEvent(
        id=None, date="2026-01-01", market="KR",
        type="initial", amount_krw=10_000_000.0,
    ))
    buy = Trade(
        id=None, date="2026-03-01", time="10:00",
        market="KR", ticker="005930", name="삼성전자",
        side="매수", qty=10, price=100_000.0,
        amount=1_000_000.0, currency="KRW",
        fx_rate=1.0, amount_krw=1_000_000.0,
    )
    sell = Trade(
        id=None, date="2026-03-05", time="14:00",
        market="KR", ticker="005930", name="삼성전자",
        side="매도", qty=10, price=120_000.0,
        amount=1_200_000.0, currency="KRW",
        fx_rate=1.0, amount_krw=1_200_000.0,
    )
    store.add_trade(buy)
    store.add_trade(sell)
    cash = store.get_cash("KR")
    # 10M - 1M (매수) + 1.2M (매도) = 10.2M
    assert cash["KR"] == pytest.approx(10_200_000.0)


def test_get_cash_excludes_seed(store):
    """seed 거래는 예수금 계산에서 제외"""
    store.add_capital_event(CapitalEvent(
        id=None, date="2026-01-01", market="KR",
        type="initial", amount_krw=10_000_000.0,
    ))
    seed = Trade(
        id=None, date="2026-03-01", time="00:00",
        market="KR", ticker="005930", name="삼성전자",
        side="매수", qty=10, price=100_000.0,
        amount=1_000_000.0, currency="KRW",
        fx_rate=1.0, amount_krw=1_000_000.0,
        memo="seed",
    )
    store.add_trade(seed)
    cash = store.get_cash("KR")
    # seed 제외 → 자본금 그대로
    assert cash["KR"] == pytest.approx(10_000_000.0)


def test_capital_deposit_and_withdraw(store):
    store.add_capital_event(CapitalEvent(
        id=None, date="2026-01-01", market="KR",
        type="initial", amount_krw=10_000_000.0,
    ))
    store.add_capital_event(CapitalEvent(
        id=None, date="2026-02-01", market="KR",
        type="withdraw", amount_krw=1_000_000.0,
    ))
    capital = store.get_capital("KR")
    assert capital["KR"] == pytest.approx(9_000_000.0)
