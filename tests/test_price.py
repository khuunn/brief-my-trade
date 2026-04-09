"""
test_price.py — get_unrealized_pnl 계산 로직 단위 테스트
네트워크 없이 price/fx를 mock하여 계산 로직만 검증
"""
import pytest
from unittest.mock import patch
from brief_my_trade.price import get_unrealized_pnl


def _mock_pnl(ticker, market, currency, net_qty, avg_buy_price,
              current_price_usd=None, current_price_krw=None, fx=1450.0):
    """
    get_unrealized_pnl을 mock price/fx로 호출하는 헬퍼.
    - KR 종목: current_price_krw 지정
    - US 종목: current_price_usd 지정 (USD 단가)
    - JP 종목: current_price_usd 지정 (JPY 단가 — 파라미터명 재사용)
    """
    price = current_price_usd if market in ("US", "JP") else current_price_krw
    with patch("brief_my_trade.price.get_current_price", return_value=price), \
         patch("brief_my_trade.price.get_fx_rate", return_value=fx):
        return get_unrealized_pnl("테스트", ticker, market, currency, net_qty, avg_buy_price)


# ── KR 종목 ───────────────────────────────────────────────────

def test_kr_unrealized_profit():
    """KR 종목 수익 케이스"""
    result = _mock_pnl("005930", "KR", "KRW", 10, 58000.0, current_price_krw=65000.0)
    assert result is not None
    assert result["unrealized_pnl_krw"] == pytest.approx(70000.0)  # (65000-58000)*10
    assert result["return_pct"] == pytest.approx(12.069, rel=1e-2)


def test_kr_unrealized_loss():
    """KR 종목 손실 케이스"""
    result = _mock_pnl("005930", "KR", "KRW", 10, 58000.0, current_price_krw=50000.0)
    assert result["unrealized_pnl_krw"] == pytest.approx(-80000.0)
    assert result["return_pct"] < 0


def test_kr_breakeven():
    """KR 종목 본전"""
    result = _mock_pnl("005930", "KR", "KRW", 10, 58000.0, current_price_krw=58000.0)
    assert result["unrealized_pnl_krw"] == pytest.approx(0.0)
    assert result["return_pct"] == pytest.approx(0.0)


# ── US 종목 (평균단가 KRW로 저장된 경우) ─────────────────────

def test_us_krw_stored_profit():
    """
    US 종목, 평균단가 KRW로 저장 (메리츠 원화환산가 방식).
    현재가 USD → KRW 환산 후 비교.
    """
    # avg_buy_price=663775원, 현재 TSLA=$220, fx=1450 → 현재가 319000원
    result = _mock_pnl("TSLA", "US", "KRW", 20, 663775.0,
                       current_price_usd=220.0, fx=1450.0)
    assert result is not None
    current_krw = 220.0 * 1450.0  # = 319000
    expected_pnl = (current_krw - 663775.0) * 20
    assert result["unrealized_pnl_krw"] == pytest.approx(expected_pnl)
    assert result["current_price_krw"] == pytest.approx(319000.0)


def test_us_usd_stored_profit():
    """
    US 종목, 평균단가 USD로 저장.
    모두 USD 기준으로 계산 후 KRW 환산.
    """
    # avg=135.5 USD, 현재=180 USD, fx=1450
    result = _mock_pnl("NVDA", "US", "USD", 5, 135.5,
                       current_price_usd=180.0, fx=1450.0)
    assert result is not None
    expected_pnl_krw = (180.0 - 135.5) * 5 * 1450.0
    assert result["unrealized_pnl_krw"] == pytest.approx(expected_pnl_krw)


# ── 예외 케이스 ───────────────────────────────────────────────

# ── JP 종목 ───────────────────────────────────────────────────

def test_jp_jpy_stored_profit():
    """JP 종목, 평균단가 JPY로 저장. 현재가 JPY → KRW 환산."""
    # avg=1500 JPY, 현재=1600 JPY, fx=9.2
    result = _mock_pnl("6613", "JP", "JPY", 100, 1500.0,
                       current_price_usd=1600.0, fx=9.2)
    assert result is not None
    expected_pnl_krw = (1600.0 - 1500.0) * 100 * 9.2  # = 92000
    assert result["unrealized_pnl_krw"] == pytest.approx(expected_pnl_krw)
    assert result["current_price_krw"] == pytest.approx(1600.0 * 9.2)


def test_jp_krw_stored():
    """JP 종목, 평균단가 KRW로 저장 (seed 입력 등). KRW 직접 비교."""
    # avg=13800원, 현재=1600JPY=14720원, fx=9.2
    result = _mock_pnl("6613", "JP", "KRW", 100, 13800.0,
                       current_price_usd=1600.0, fx=9.2)
    assert result is not None
    current_krw = 1600.0 * 9.2  # 14720
    expected_pnl = (current_krw - 13800.0) * 100
    assert result["unrealized_pnl_krw"] == pytest.approx(expected_pnl)


def test_jp_loss():
    """JP 종목 손실 케이스"""
    result = _mock_pnl("7203", "JP", "JPY", 50, 3000.0,
                       current_price_usd=2800.0, fx=9.2)
    assert result is not None
    assert result["unrealized_pnl_krw"] < 0
    assert result["return_pct"] < 0


def test_zero_qty_returns_none():
    """보유 수량 0이면 None 반환"""
    result = _mock_pnl("005930", "KR", "KRW", 0, 58000.0, current_price_krw=65000.0)
    assert result is None


def test_empty_ticker_returns_none():
    """티커 없으면 None 반환"""
    with patch("brief_my_trade.price.get_current_price", return_value=65000.0), \
         patch("brief_my_trade.price.get_fx_rate", return_value=1450.0):
        result = get_unrealized_pnl("삼성전자", "", "KR", "KRW", 10, 58000.0)
    assert result is None


def test_price_fetch_failure_returns_none():
    """가격 조회 실패 시 None 반환"""
    result = _mock_pnl("005930", "KR", "KRW", 10, 58000.0, current_price_krw=None)
    assert result is None
