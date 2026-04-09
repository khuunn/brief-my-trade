"""
test_ta.py — TA 계산 함수 단위 테스트 (네트워크 없음)
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from brief_my_trade.ta import (
    calc_ma,
    calc_rsi,
    calc_volume_ma,
    detect_candle_pattern,
    build_ta_snapshot,
)


# ─── calc_ma ─────────────────────────────────────────────────

def test_calc_ma_basic():
    prices = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert calc_ma(prices, 3) == pytest.approx(40.0)   # (30+40+50)/3


def test_calc_ma_exact_period():
    prices = [10.0, 20.0, 30.0]
    assert calc_ma(prices, 3) == pytest.approx(20.0)


def test_calc_ma_insufficient_data():
    assert calc_ma([10.0, 20.0], 5) is None


def test_calc_ma_single():
    assert calc_ma([42.0], 1) == pytest.approx(42.0)


# ─── calc_rsi ────────────────────────────────────────────────

def _make_prices(n: int, up_pct: float = 1.0) -> list[float]:
    """단조 상승 or 하락 가격 시리즈 생성."""
    price = 100.0
    result = [price]
    for _ in range(n - 1):
        price *= (1 + up_pct / 100)
        result.append(price)
    return result


def test_rsi_all_up_near_100():
    """모두 상승 → RSI ≈ 100"""
    prices = _make_prices(30, up_pct=1.0)
    rsi = calc_rsi(prices, 14)
    assert rsi is not None
    assert rsi > 90.0


def test_rsi_all_down_near_0():
    """모두 하락 → RSI ≈ 0"""
    prices = _make_prices(30, up_pct=-1.0)
    rsi = calc_rsi(prices, 14)
    assert rsi is not None
    assert rsi < 10.0


def test_rsi_insufficient_data():
    """데이터 부족 → None"""
    assert calc_rsi([100.0, 101.0, 102.0], 14) is None


def test_rsi_range():
    """RSI 항상 0~100 범위"""
    import random
    random.seed(42)
    prices = [100.0 + random.uniform(-5, 5) for _ in range(50)]
    rsi = calc_rsi(prices, 14)
    assert rsi is not None
    assert 0.0 <= rsi <= 100.0


# ─── calc_volume_ma ──────────────────────────────────────────

def test_volume_ma_basic():
    vols = [100.0] * 20
    assert calc_volume_ma(vols, 20) == pytest.approx(100.0)


def test_volume_ma_insufficient():
    assert calc_volume_ma([100.0] * 5, 20) is None


# ─── detect_candle_pattern ────────────────────────────────────

def _candle(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c}


def test_bullish_engulfing():
    prev = _candle(110, 112, 98, 100)   # 음봉 (110→100)
    curr = _candle(98,  115, 97, 112)   # 양봉이 prev body 완전히 감쌈 (98→112)
    assert detect_candle_pattern([prev, curr]) == "bullish_engulfing"


def test_bearish_engulfing():
    prev = _candle(90, 105, 88, 102)    # 양봉 (90→102)
    curr = _candle(104, 106, 87, 89)    # 음봉이 prev body 완전히 감쌈 (104→89)
    assert detect_candle_pattern([prev, curr]) == "bearish_engulfing"


def test_hammer():
    # open=100, close=103, high=104, low=90
    # body=3, range=14, body/range=21% (not doji)
    # lower_wick=10 >= 2*3=6 ✓ / upper_wick=1 < 3 ✓
    c = _candle(100, 104, 90, 103)
    prev = _candle(105, 106, 103, 104)
    assert detect_candle_pattern([prev, c]) == "hammer"


def test_shooting_star():
    # open=100, close=97, high=110, low=96
    # body=3, range=14, body/range=21% (not doji)
    # upper_wick=10 >= 2*3=6 ✓ / lower_wick=1 < 3 ✓
    c = _candle(100, 110, 96, 97)
    prev = _candle(95, 96, 94, 95)
    assert detect_candle_pattern([prev, c]) == "shooting_star"


def test_doji():
    c = _candle(100, 105, 95, 100.2)  # body=0.2, range=10 → body/range=2% < 5%
    prev = _candle(99, 100, 98, 99.5)
    assert detect_candle_pattern([prev, c]) == "doji"


def test_no_pattern():
    prev = _candle(100, 102, 99, 101)
    curr = _candle(101, 103, 100, 102)
    assert detect_candle_pattern([prev, curr]) is None


def test_insufficient_candles():
    assert detect_candle_pattern([]) is None
    assert detect_candle_pattern([_candle(100, 102, 99, 101)]) is None


# ─── build_ta_snapshot ────────────────────────────────────────

def _make_candles(n: int = 70) -> list[dict]:
    """단조 상승 캔들 시리즈."""
    candles = []
    price = 100.0
    for i in range(n):
        o = price
        c = price * 1.005
        candles.append({
            "open": o, "high": c * 1.002,
            "low": o * 0.998, "close": c,
            "volume": 1_000_000.0,
        })
        price = c
    return candles


def test_build_ta_snapshot_success():
    with patch("brief_my_trade.ta.fetch_ohlc", return_value=_make_candles(70)):
        snap = build_ta_snapshot("005930", "KR")
    assert snap is not None
    assert snap["ma20"] is not None
    assert snap["ma60"] is not None
    assert snap["rsi"] is not None
    assert snap["volume_ratio"] is not None
    assert snap["trend"] == "bullish"


def test_build_ta_snapshot_fetch_failure():
    with patch("brief_my_trade.ta.fetch_ohlc", return_value=None):
        assert build_ta_snapshot("005930", "KR") is None


def test_build_ta_snapshot_too_few_candles():
    with patch("brief_my_trade.ta.fetch_ohlc", return_value=_make_candles(5)):
        snap = build_ta_snapshot("005930", "KR")
    # 5봉 → MA20/60/RSI None, 하지만 snapshot 자체는 반환
    assert snap is not None
    assert snap["ma20"] is None
    assert snap["ma60"] is None
    assert snap["rsi"] is None
