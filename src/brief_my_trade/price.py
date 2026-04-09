"""
price.py — 현재가 + 환율 실시간 조회 (yfinance 기반)
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import requests
import yfinance as yf

logger = logging.getLogger(__name__)

# 국내 종목 티커 suffix
KR_SUFFIX = ".KS"   # KRX (KOSPI/KOSDAQ)
KQ_SUFFIX = ".KQ"   # KOSDAQ fallback
JP_SUFFIX = ".T"    # 도쿄증권거래소

_price_cache: dict[str, tuple[float, float]] = {}  # ticker → (price, timestamp)
_CACHE_TTL = 300  # 5분 캐시


def get_current_price(ticker: str, market: str) -> Optional[float]:
    """
    현재가 조회.
    - KR: ticker 숫자 6자리 → '{ticker}.KS' 시도, 실패 시 '.KQ'
    - US: ticker 그대로 (AAPL, NVDA 등)
    - JP: ticker 숫자 4자리 → '{ticker}.T'
    """
    cache_key = f"{market}:{ticker}"
    if cache_key in _price_cache:
        price, ts = _price_cache[cache_key]
        if time.time() - ts < _CACHE_TTL:
            return price

    try:
        if market == "KR":
            price = _fetch_kr_price(ticker)
        elif market == "JP":
            price = _fetch_jp_price(ticker)
        else:
            price = _fetch_us_price(ticker)

        if price and price > 0:
            _price_cache[cache_key] = (price, time.time())
            return price
    except Exception as e:
        logger.warning("현재가 조회 실패 [%s:%s]: %s", market, ticker, e)
    return None


def _fetch_kr_price_naver(ticker: str) -> Optional[float]:
    """네이버 금융 API로 국내 종목 현재가 조회 (6자리 코드 필요)"""
    code = ticker.replace(".KS", "").replace(".KQ", "")
    if len(code) != 6:  # 숫자 6자리 또는 0148J0 형태 허용
        return None
    try:
        url = f"https://m.stock.naver.com/api/stock/{code}/basic"
        r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=5)
        if r.status_code != 200:
            return None
        data = r.json()
        price_str = data.get("closePrice", "") or data.get("currentPrice", "")
        if price_str:
            return float(price_str.replace(",", ""))
    except Exception:
        pass
    return None


def _fetch_kr_price(ticker: str) -> Optional[float]:
    # 네이버 금융 우선 시도
    naver_price = _fetch_kr_price_naver(ticker)
    if naver_price:
        return naver_price
    # yfinance fallback
    symbol = f"{ticker}{KR_SUFFIX}" if ticker.isdigit() else ticker
    t = yf.Ticker(symbol)
    info = t.fast_info
    price = getattr(info, "last_price", None)
    if price and price > 0:
        return price
    # KOSDAQ fallback
    if KR_SUFFIX in symbol:
        symbol2 = symbol.replace(KR_SUFFIX, KQ_SUFFIX)
        t2 = yf.Ticker(symbol2)
        info2 = t2.fast_info
        price2 = getattr(info2, "last_price", None)
        if price2 and price2 > 0:
            return price2
    return None


def _fetch_jp_price(ticker: str) -> Optional[float]:
    """도쿄증권거래소 종목 현재가 (JPY). 숫자 코드에 .T suffix 붙임."""
    code = ticker.replace(JP_SUFFIX, "")
    symbol = f"{code}{JP_SUFFIX}"
    try:
        t = yf.Ticker(symbol)
        info = t.fast_info
        price = getattr(info, "last_price", None)
        if price and price > 0:
            return price
    except Exception as exc:
        logger.warning("JP 현재가 조회 실패 [%s]: %s", symbol, exc)
    return None


def _fetch_us_price(ticker: str) -> Optional[float]:
    t = yf.Ticker(ticker)
    info = t.fast_info
    return getattr(info, "last_price", None)


# ── 환율 ──────────────────────────────────────────────────────

_fx_cache: dict[str, tuple[float, float]] = {}  # currency → (rate, timestamp)
_FX_CACHE_TTL = 300  # 5분


def get_fx_rate(currency: str) -> float:
    """
    1 {currency} = ? KRW
    USD/KRW, JPY/KRW 등
    """
    if currency == "KRW":
        return 1.0

    cache_key = currency
    if cache_key in _fx_cache:
        rate, ts = _fx_cache[cache_key]
        if time.time() - ts < _FX_CACHE_TTL:
            return rate

    try:
        # yfinance로 환율 조회 (예: USDKRW=X)
        symbol = f"{currency}KRW=X"
        ticker = yf.Ticker(symbol)
        info = ticker.fast_info
        rate = getattr(info, "last_price", None)
        if rate and rate > 0:
            _fx_cache[cache_key] = (rate, time.time())
            return rate
    except Exception as e:
        logger.warning("환율 조회 실패 [%s]: %s", currency, e)

    # fallback: 대략적인 고정값 (실패 시)
    fallback = {"USD": 1380.0, "JPY": 9.2, "EUR": 1500.0, "HKD": 177.0}
    fallback_val = fallback.get(currency, 1.0)
    logger.warning("환율 fallback 사용 [%s]: %.1f", currency, fallback_val)
    return fallback_val


def get_unrealized_pnl(
    name: str,
    ticker: str,
    market: str,
    currency: str,
    net_qty: int,
    avg_buy_price: float,
) -> Optional[dict]:
    """
    미실현손익 계산.
    - KR 종목: yfinance KRW 가격 → KRW 비교
    - US 종목 (currency=KRW): yfinance USD 가격 → KRW 환산 후 비교
    - US 종목 (currency=USD): yfinance USD 가격 → KRW 환산
    - JP 종목 (currency=JPY or KRW): yfinance JPY 가격 → KRW 환산
    Returns: {current_price, current_price_krw, unrealized_pnl_krw, fx_rate, return_pct}
    """
    if net_qty <= 0 or not ticker:
        return None

    current_price = get_current_price(ticker, market)
    if current_price is None:
        return None

    if market == "US":
        fx = get_fx_rate("USD")
        current_price_krw = current_price * fx
        if currency == "KRW":
            unrealized_krw = (current_price_krw - avg_buy_price) * net_qty
            cost_krw = avg_buy_price * net_qty
        else:
            avg_buy_price_krw = avg_buy_price * fx
            unrealized_krw = (current_price_krw - avg_buy_price_krw) * net_qty
            cost_krw = avg_buy_price_krw * net_qty
    elif market == "JP":
        fx = get_fx_rate("JPY")
        current_price_krw = current_price * fx
        if currency == "KRW":
            # avg_buy_price가 KRW로 저장된 경우 (seed 입력 등)
            unrealized_krw = (current_price_krw - avg_buy_price) * net_qty
            cost_krw = avg_buy_price * net_qty
        else:
            # avg_buy_price가 JPY로 저장된 경우 (알림톡 정상 입력)
            avg_buy_price_krw = avg_buy_price * fx
            unrealized_krw = (current_price_krw - avg_buy_price_krw) * net_qty
            cost_krw = avg_buy_price_krw * net_qty
    else:
        # KR: 모두 KRW
        fx = 1.0
        current_price_krw = current_price
        unrealized_krw = (current_price_krw - avg_buy_price) * net_qty
        cost_krw = avg_buy_price * net_qty

    return_pct = (unrealized_krw / cost_krw * 100) if cost_krw > 0 else 0.0

    return {
        "current_price": current_price,
        "current_price_krw": current_price_krw,
        "unrealized_pnl_krw": unrealized_krw,
        "fx_rate": fx,
        "return_pct": return_pct,
    }
