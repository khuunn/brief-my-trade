"""
ta.py — 보유 종목 기술적 분석 (MA, RSI, 거래량, 캔들 패턴)

yfinance 일봉 데이터 기반. 추가 의존성 없음.
"""

from __future__ import annotations

import logging
from typing import Optional

import yfinance as yf

logger = logging.getLogger(__name__)

# yfinance 티커 suffix
_KS = ".KS"
_KQ = ".KQ"


# ─── 데이터 수집 ──────────────────────────────────────────────

def _yf_symbol(ticker: str, market: str) -> str:
    """yfinance 조회용 티커 심볼 변환."""
    if market == "KR":
        code = ticker.replace(_KS, "").replace(_KQ, "")
        return f"{code}{_KS}"
    if market == "JP":
        code = ticker.replace(".T", "")
        return f"{code}.T"
    return ticker


def fetch_ohlc(ticker: str, market: str, period: str = "3mo") -> Optional[list[dict]]:
    """
    yfinance 일봉 OHLC + Volume 데이터 조회.

    Returns:
        [{"open": float, "high": float, "low": float, "close": float, "volume": float}, ...]
        최신 날짜 순 (마지막이 최신). 실패 시 None.
    """
    symbol = _yf_symbol(ticker, market)
    try:
        hist = yf.Ticker(symbol).history(period=period)
        if hist.empty and market == "KR":
            # KOSDAQ fallback
            symbol2 = symbol.replace(_KS, _KQ)
            hist = yf.Ticker(symbol2).history(period=period)
        if hist.empty:
            logger.warning("OHLC 데이터 없음: %s (%s)", ticker, market)
            return None
        return [
            {
                "open":   float(row["Open"]),
                "high":   float(row["High"]),
                "low":    float(row["Low"]),
                "close":  float(row["Close"]),
                "volume": float(row["Volume"]),
            }
            for _, row in hist.iterrows()
        ]
    except Exception as exc:
        logger.warning("OHLC 조회 실패 [%s:%s]: %s", market, ticker, exc)
        return None


# ─── 지표 계산 ────────────────────────────────────────────────

def calc_ma(closes: list[float], period: int) -> Optional[float]:
    """단순 이동평균."""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def calc_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """
    RSI — Wilder's smoothing.
    데이터 부족 (< period + 1) 시 None 반환.
    """
    if len(closes) < period + 1:
        return None

    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains  = [d if d > 0 else 0.0 for d in deltas]
    losses = [-d if d < 0 else 0.0 for d in deltas]

    # 초기 SMA 시드
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder's smoothing
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 1)


def calc_volume_ma(volumes: list[float], period: int = 20) -> Optional[float]:
    """거래량 이동평균."""
    if len(volumes) < period:
        return None
    return sum(volumes[-period:]) / period


# ─── 캔들 패턴 감지 ───────────────────────────────────────────

def _body(c: dict) -> float:
    return abs(c["close"] - c["open"])

def _range(c: dict) -> float:
    return c["high"] - c["low"]

def _upper_wick(c: dict) -> float:
    return c["high"] - max(c["open"], c["close"])

def _lower_wick(c: dict) -> float:
    return min(c["open"], c["close"]) - c["low"]


def detect_candle_pattern(candles: list[dict]) -> Optional[str]:
    """
    최근 1~2봉 기준 캔들 패턴 감지.

    Returns:
        "bullish_engulfing" | "bearish_engulfing"
        | "hammer" | "shooting_star" | "doji" | None
    """
    if len(candles) < 2:
        return None

    curr = candles[-1]
    prev = candles[-2]

    curr_body  = _body(curr)
    prev_body  = _body(prev)
    curr_range = _range(curr)

    # Doji: 바디가 range의 5% 이하
    if curr_range > 0 and curr_body / curr_range < 0.05:
        return "doji"

    # Bullish Engulfing: 전봉 음봉 + 현봉 양봉이 전봉 body를 완전히 감쌈
    if (prev["close"] < prev["open"]                  # prev 음봉
            and curr["close"] > curr["open"]           # curr 양봉
            and curr["open"] <= prev["close"]
            and curr["close"] >= prev["open"]
            and curr_body > prev_body):
        return "bullish_engulfing"

    # Bearish Engulfing: 전봉 양봉 + 현봉 음봉이 전봉 body를 완전히 감쌈
    if (prev["close"] > prev["open"]                  # prev 양봉
            and curr["close"] < curr["open"]           # curr 음봉
            and curr["open"] >= prev["close"]
            and curr["close"] <= prev["open"]
            and curr_body > prev_body):
        return "bearish_engulfing"

    # Hammer: 아래꼬리 >= 2 * body, 위꼬리 < body
    upper = _upper_wick(curr)
    lower = _lower_wick(curr)
    if curr_body > 0:
        if lower >= 2 * curr_body and upper < curr_body:
            return "hammer"
        if upper >= 2 * curr_body and lower < curr_body:
            return "shooting_star"

    return None


# ─── 종합 스냅샷 ──────────────────────────────────────────────

def build_ta_snapshot(ticker: str, market: str) -> Optional[dict]:
    """
    단일 종목 TA 스냅샷 생성.

    Returns:
        {
          "price":          float,          # 최신 종가
          "ma20":           float | None,
          "ma60":           float | None,
          "rsi":            float | None,
          "volume":         float,          # 최신 거래량
          "volume_ma20":    float | None,   # 20일 평균 거래량
          "volume_ratio":   float | None,   # 현재 / 평균 (1.0 = 평균)
          "candle_pattern": str | None,
          "trend":          "bullish" | "bearish" | "neutral",
        }
        실패 시 None.
    """
    candles = fetch_ohlc(ticker, market)
    if not candles or len(candles) < 2:
        return None

    closes  = [c["close"]  for c in candles]
    volumes = [c["volume"] for c in candles]

    ma20 = calc_ma(closes, 20)
    ma60 = calc_ma(closes, 60)
    rsi  = calc_rsi(closes, 14)

    price       = closes[-1]
    volume      = volumes[-1]
    volume_ma20 = calc_volume_ma(volumes, 20)
    volume_ratio = round(volume / volume_ma20, 2) if volume_ma20 and volume_ma20 > 0 else None

    pattern = detect_candle_pattern(candles)

    # 추세: MA20/60 기준
    if ma20 and ma60:
        if price > ma20 > ma60:
            trend = "bullish"
        elif price < ma20 < ma60:
            trend = "bearish"
        else:
            trend = "neutral"
    elif ma20:
        trend = "bullish" if price > ma20 else "bearish"
    else:
        trend = "neutral"

    return {
        "price":          price,
        "ma20":           ma20,
        "ma60":           ma60,
        "rsi":            rsi,
        "volume":         volume,
        "volume_ma20":    volume_ma20,
        "volume_ratio":   volume_ratio,
        "candle_pattern": pattern,
        "trend":          trend,
    }
