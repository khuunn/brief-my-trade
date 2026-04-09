"""
parser.py — 매매기록 파싱
  1. 텍스트 파싱 (수동 입력)
  2. 이미지 파싱 (메리츠 mPOP 스크린샷 → Claude Vision)
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

# ─── 파싱 결과 ────────────────────────────────────────────────

@dataclass
class ParsedTrade:
    side: str            # '매수' | '매도'
    name: str            # 종목명 (줄임말 가능)
    qty: int
    price: float
    commission: float = 0.0
    tax: float = 0.0
    trade_date: str = ""  # YYYY-MM-DD (없으면 오늘)
    trade_time: str = ""  # HH:MM
    currency: str = "KRW"
    ticker: str = ""      # 종목코드 (카카오 알림톡에서 추출된 경우)
    raw_text: str = ""    # 원본 텍스트 (디버그용)


# ─── 텍스트 파싱 ──────────────────────────────────────────────

TRADE_PATTERN = re.compile(
    r"^(매수|매도|buy|sell)\s+"    # side
    r"(\S+)\s+"                     # 종목명/줄임말
    r"(\d+)\s+"                     # 수량
    r"([\d,.]+)"                    # 단가 (소수점 지원: 135.50, 8.85)
    r"(?:\s+([\d,.]+))?"            # 수수료 (선택)
    r"(?:\s+([\d,.]+))?$",          # 세금 (선택)
    re.IGNORECASE,
)

SIDE_MAP = {"buy": "매수", "sell": "매도"}

# 카카오 알림톡 필드 패턴
_KV_PATTERN = re.compile(r"^(.+?)\s*:\s*(.+)$")
_KAKAO_NAME_PATTERN = re.compile(r"^(.+?)\(([A-Z0-9.]+)\)$")  # 퀵로직(QUIK)
_CURRENCY_PRICE_PATTERN = re.compile(r"^(USD|KRW|JPY|EUR|HKD)?\s*([\d,.]+)$", re.IGNORECASE)
_DATE_MDSLASH = re.compile(r"^(\d{1,2})/(\d{1,2})$")       # 03/05 (exact match)
_DATE_MDSLASH_SEARCH = re.compile(r"(\d{1,2})/(\d{1,2})")  # 03/06 No. 55966 (partial)


def _parse_kakao_alert(text: str) -> list[ParsedTrade]:
    """
    메리츠증권 카카오 알림톡 텍스트 파싱.
    해외주식: 종목명 / 매매구분 / 체결단가(USD X.XX) / 체결일자
    국내주식: 종목     / 구분     / 체결단가(X원)     / 주문일자/번호
    """
    lines = text.strip().splitlines()
    fields: dict[str, str] = {}
    for line in lines:
        m = _KV_PATTERN.match(line.strip())
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()

    # 필드 alias: 국내(종목/구분) ↔ 해외(종목명/매매구분) 통일
    if "종목명" not in fields and "종목" in fields:
        fields["종목명"] = fields["종목"]
    if "매매구분" not in fields and "구분" in fields:
        fields["매매구분"] = fields["구분"]

    # 필수 필드 확인
    if "종목명" not in fields or "매매구분" not in fields or "체결단가" not in fields:
        return []

    # 종목명 + 티커 분리 (삼성전기(009150) or 퀵로직(QUIK))
    raw_name = fields["종목명"]
    nm = _KAKAO_NAME_PATTERN.match(raw_name)
    name = nm.group(1).strip() if nm else raw_name
    ticker = nm.group(2).strip() if nm else ""

    # side
    side_raw = fields.get("매매구분", "매수")
    side = "매수" if "매수" in side_raw else "매도"

    # 체결단가 + 통화
    # 해외: "USD 8.8500" / 국내: "410,500원" or "410500"
    price_raw = fields.get("체결단가", "0").replace(",", "").replace("원", "").strip()
    pm = _CURRENCY_PRICE_PATTERN.match(price_raw)
    currency = "KRW"
    price = 0.0
    if pm:
        cur_str = (pm.group(1) or "").upper()
        currency = cur_str if cur_str in ("USD", "JPY", "EUR", "HKD") else "KRW"
        price = float(pm.group(2))

    # 수량 (체결수량 우선, 없으면 주문수량)
    qty_raw = fields.get("체결수량") or fields.get("주문수량", "0")
    qty = int(re.sub(r"[^\d]", "", qty_raw) or "0")

    # 날짜 — 체결일자(해외) or 주문일자/번호(국내: "03/06 No. 55966")
    date_raw = fields.get("체결일자") or fields.get("주문일자/번호", "")
    # MM/DD 부분 추출 (문자열 중간에 있어도 탐색)
    date_match = _DATE_MDSLASH_SEARCH.search(date_raw.strip()) if date_raw else None
    trade_date = ""
    if date_match:
        year = date.today().year
        trade_date = f"{year}-{int(date_match.group(1)):02d}-{int(date_match.group(2)):02d}"
    elif re.match(r"\d{4}-\d{2}-\d{2}", date_raw):
        trade_date = date_raw[:10]

    market = "US" if currency in ("USD", "JPY", "EUR", "HKD") else "KR"

    return [ParsedTrade(
        side=side, name=name, qty=qty, price=price,
        currency=currency, trade_date=trade_date,
        ticker=ticker,
        raw_text=text,
    )]


def parse_text(text: str) -> list[ParsedTrade]:
    """
    텍스트에서 매매 내역 파싱.
    한 줄 또는 여러 줄 입력 지원.

    예시:
      매수 삼전 10 58000
      매도 하닉 5 190000 238 238
      매수 NVDA 2 135.50         ← 해외 (USD 자동 감지)
    """
    # 카카오 알림톡 형식 먼저 시도
    if "[메리츠증권]" in text or "체결단가" in text or "매매구분" in text:
        kakao = _parse_kakao_alert(text)
        if kakao:
            return kakao

    results = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        m = TRADE_PATTERN.match(line)
        if not m:
            continue

        side_raw, name, qty_str, price_str, comm_str, tax_str = m.groups()
        side = SIDE_MAP.get(side_raw.lower(), side_raw)
        price_clean = float(price_str.replace(",", ""))

        # 해외 종목 자동 감지
        currency = "KRW"
        if name.upper().endswith(".T") or (name.isdigit() and len(name) == 4):
            # 일본 종목: 7203.T 또는 숫자 4자리 (6613 등)
            # 단, 숫자 4자리는 JPY 명시 없으면 KRW 오판 가능 → .T suffix 있을 때만 JP 자동 감지
            if name.upper().endswith(".T"):
                currency = "JPY"
        elif name.isupper() and name.isalpha() and len(name) <= 5:
            currency = "USD"
        elif "." in price_str:
            currency = "USD"

        results.append(ParsedTrade(
            side=side,
            name=name,
            qty=int(qty_str),
            price=price_clean,
            commission=float(comm_str.replace(",", "")) if comm_str else 0.0,
            tax=float(tax_str.replace(",", "")) if tax_str else 0.0,
            currency=currency,
            raw_text=line,
        ))
    return results




