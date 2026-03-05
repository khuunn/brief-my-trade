"""
parser.py — 매매기록 파싱
  1. 텍스트 파싱 (수동 입력)
  2. 이미지 파싱 (메리츠 mPOP 스크린샷 → Claude Vision)
"""

from __future__ import annotations

import base64
import os
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
    raw_text: str = ""    # 원본 텍스트 (디버그용)


# ─── 텍스트 파싱 ──────────────────────────────────────────────

TRADE_PATTERN = re.compile(
    r"^(매수|매도|buy|sell)\s+"    # side
    r"(\S+)\s+"                     # 종목명/줄임말
    r"(\d+)\s+"                     # 수량
    r"([\d,]+)"                     # 단가
    r"(?:\s+([\d,]+))?"             # 수수료 (선택)
    r"(?:\s+([\d,]+))?$",           # 세금 (선택)
    re.IGNORECASE,
)

SIDE_MAP = {"buy": "매수", "sell": "매도"}

# 카카오 알림톡 필드 패턴
_KV_PATTERN = re.compile(r"^(.+?)\s*:\s*(.+)$")
_KAKAO_NAME_PATTERN = re.compile(r"^(.+?)\(([A-Z0-9.]+)\)$")  # 퀵로직(QUIK)
_CURRENCY_PRICE_PATTERN = re.compile(r"^(USD|KRW|JPY|EUR|HKD)?\s*([\d,.]+)$", re.IGNORECASE)
_DATE_MDSLASH = re.compile(r"^(\d{1,2})/(\d{1,2})$")  # 03/05


def _parse_kakao_alert(text: str) -> list[ParsedTrade]:
    """
    메리츠증권 카카오 알림톡 텍스트 파싱.
    [메리츠증권] 해외주식/국내주식 주문체결 안내 포함한 구조화 텍스트.
    """
    lines = text.strip().splitlines()
    fields: dict[str, str] = {}
    for line in lines:
        m = _KV_PATTERN.match(line.strip())
        if m:
            fields[m.group(1).strip()] = m.group(2).strip()

    # 필수 필드 확인
    if "종목명" not in fields or "매매구분" not in fields or "체결단가" not in fields:
        return []

    # 종목명 + 티커 분리
    raw_name = fields["종목명"]
    nm = _KAKAO_NAME_PATTERN.match(raw_name)
    name = nm.group(1).strip() if nm else raw_name
    ticker = nm.group(2).strip() if nm else ""

    # side
    side_raw = fields.get("매매구분", "매수")
    side = "매수" if "매수" in side_raw else "매도"

    # 체결단가 + 통화
    price_raw = fields.get("체결단가", "0")
    pm = _CURRENCY_PRICE_PATTERN.match(price_raw.replace(",", ""))
    currency = "KRW"
    price = 0.0
    if pm:
        cur_str = (pm.group(1) or "").upper()
        currency = cur_str if cur_str in ("USD", "JPY", "EUR", "HKD") else "KRW"
        price = float(pm.group(2))

    # 수량 (체결수량 우선, 없으면 주문수량)
    qty_raw = fields.get("체결수량") or fields.get("주문수량", "0")
    qty = int(re.sub(r"[^\d]", "", qty_raw) or "0")

    # 날짜
    date_raw = fields.get("체결일자", "")
    trade_date = ""
    dm = _DATE_MDSLASH.match(date_raw.strip()) if date_raw else None
    if dm:
        import datetime
        year = datetime.date.today().year
        trade_date = f"{year}-{int(dm.group(1)):02d}-{int(dm.group(2)):02d}"
    elif re.match(r"\d{4}-\d{2}-\d{2}", date_raw):
        trade_date = date_raw[:10]

    market = "US" if currency in ("USD", "JPY", "EUR", "HKD") else "KR"

    return [ParsedTrade(
        side=side, name=name, qty=qty, price=price,
        currency=currency, trade_date=trade_date,
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

        # 해외 종목 자동 감지: 영문 대문자 or 소수점 포함
        currency = "KRW"
        if name.isupper() and name.isalpha() and len(name) <= 5:
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


# ─── 이미지 파싱 (Claude Vision) ──────────────────────────────

PORTFOLIO_PARSE_PROMPT = """
메리츠증권 mPOP 앱의 보유종목/잔고 화면 스크린샷에서 현재 보유 종목 목록을 추출해줘.

아래 JSON 배열 형식으로만 응답해:
[
  {
    "name": "종목명",
    "ticker": "종목코드 (있으면, 없으면 빈 문자열)",
    "qty": 보유수량(정수),
    "avg_price": 평균매입단가(숫자),
    "currency": "KRW 또는 USD",
    "market": "KR 또는 US"
  }
]

규칙:
- 예수금/현금은 제외, 종목만 추출
- 해외주식이면 currency=USD, market=US
- JSON 외 다른 텍스트 절대 출력 금지
"""


IMAGE_PARSE_PROMPT = """
메리츠증권 카카오 알림톡 또는 mPOP 체결 화면 스크린샷에서 매매 내역을 추출해줘.

카카오 알림톡 형식 예시:
  종목명 : 퀵로직(QUIK)
  매매구분 : 매수
  체결단가 : USD 8.8500
  체결수량 : 2주
  체결금액 : USD 17.70
  체결일자 : 03/05

아래 JSON 배열 형식으로만 응답해:
[
  {
    "side": "매수 또는 매도",
    "name": "종목명 (한글명, 예: 퀵로직)",
    "ticker": "티커 (괄호 안 영문, 예: QUIK. 없으면 빈 문자열)",
    "qty": 체결수량(정수),
    "price": 체결단가(숫자만, 통화기호 제외),
    "commission": 수수료(숫자, 없으면 0),
    "tax": 세금(숫자, 없으면 0),
    "date": "YYYY-MM-DD (MM/DD면 올해 연도 붙이기, 예: 03/05 → 2026-03-05)",
    "time": "HH:MM (있으면, 없으면 빈 문자열)",
    "currency": "KRW 또는 USD (체결단가에 USD 있으면 USD, 없으면 KRW)",
    "market": "KR 또는 US (USD면 US, KRW면 KR)"
  }
]

규칙:
- 화면에 여러 건이면 모두 추출
- 종목명(QUIK) 처럼 괄호 안 영문은 ticker로 추출
- 날짜가 MM/DD면 현재 연도(2026) 붙여서 YYYY-MM-DD로
- JSON 외 다른 텍스트 절대 출력 금지
"""


def _call_vision(image_b64: str, mime_type: str) -> list[dict]:
    """
    OpenRouter를 통해 Claude Vision 호출.
    OPENROUTER_API_KEY 환경변수 사용.
    """
    import json as _json
    from openai import OpenAI

    api_key = os.environ.get("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError("OPENROUTER_API_KEY 환경변수 없음")

    client = OpenAI(
        api_key=api_key,
        base_url="https://openrouter.ai/api/v1",
    )

    response = client.chat.completions.create(
        model="anthropic/claude-opus-4-5",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime_type};base64,{image_b64}"},
                },
                {"type": "text", "text": IMAGE_PARSE_PROMPT},
            ],
        }],
    )

    raw = response.choices[0].message.content.strip()
    try:
        return _json.loads(raw)
    except _json.JSONDecodeError:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        if m:
            return _json.loads(m.group())
        raise RuntimeError(f"이미지 파싱 실패: {raw[:200]}")


def parse_image(image_path: str | Path) -> list[ParsedTrade]:
    """이미지 파일 경로로 파싱"""
    image_data = Path(image_path).read_bytes()
    ext = Path(image_path).suffix.lower()
    mime = {".jpg": "image/jpeg", ".jpeg": "image/jpeg",
            ".png": "image/png", ".webp": "image/webp"}.get(ext, "image/jpeg")
    return parse_image_bytes(image_data, mime)


@dataclass
class ParsedHolding:
    name: str
    ticker: str
    qty: int
    avg_price: float
    currency: str = "KRW"
    market: str = "KR"


def parse_portfolio_image_bytes(image_bytes: bytes, mime_type: str = "image/jpeg") -> list[ParsedHolding]:
    """보유종목 화면 스크린샷 → ParsedHolding 목록"""
    import json as _json
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    from openai import OpenAI
    import os as _os
    api_key = _os.environ.get("OPENROUTER_API_KEY")
    client = OpenAI(api_key=api_key, base_url="https://openrouter.ai/api/v1")
    response = client.chat.completions.create(
        model="anthropic/claude-opus-4-5",
        max_tokens=1024,
        messages=[{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{b64}"}},
                {"type": "text", "text": PORTFOLIO_PARSE_PROMPT},
            ],
        }],
    )
    raw = response.choices[0].message.content.strip()
    try:
        items = _json.loads(raw)
    except _json.JSONDecodeError:
        m = re.search(r"\[.*\]", raw, re.DOTALL)
        items = _json.loads(m.group()) if m else []

    return [
        ParsedHolding(
            name=i.get("name", ""),
            ticker=i.get("ticker", ""),
            qty=int(i.get("qty", 0)),
            avg_price=float(i.get("avg_price", 0)),
            currency=i.get("currency", "KRW"),
            market=i.get("market", "KR"),
        )
        for i in items if i.get("qty", 0) > 0
    ]


def parse_image_bytes(image_bytes: bytes, mime_type: str = "image/jpeg") -> list[ParsedTrade]:
    """
    바이트 데이터로 직접 파싱 (텔레그램 파일 다운로드 후 사용)
    OpenRouter를 통해 Claude Vision 호출.
    """
    b64 = base64.standard_b64encode(image_bytes).decode("utf-8")
    items = _call_vision(b64, mime_type)

    return [
        ParsedTrade(
            side=i.get("side", "매수"),
            name=i.get("name", ""),
            qty=int(i.get("qty", 0)),
            price=float(i.get("price", 0)),
            commission=float(i.get("commission", 0)),
            tax=float(i.get("tax", 0)),
            trade_date=i.get("date", ""),
            trade_time=i.get("time", ""),
            currency=i.get("currency", "KRW"),
            raw_text=str(i),
        )
        for i in items
    ]
