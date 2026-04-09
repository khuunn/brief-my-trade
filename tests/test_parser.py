"""
test_parser.py — 텍스트 파싱 + 카카오 알림 파싱 단위 테스트
"""
import pytest
from brief_my_trade.parser import parse_text, _parse_kakao_alert


# ── 텍스트 파싱 ───────────────────────────────────────────────

def test_parse_kr_buy():
    result = parse_text("매수 삼전 10 58000")
    assert len(result) == 1
    t = result[0]
    assert t.side == "매수"
    assert t.name == "삼전"
    assert t.qty == 10
    assert t.price == 58000.0
    assert t.currency == "KRW"


def test_parse_kr_sell_with_fees():
    result = parse_text("매도 하닉 5 190000 238 476")
    assert len(result) == 1
    t = result[0]
    assert t.side == "매도"
    assert t.qty == 5
    assert t.price == 190000.0
    assert t.commission == 238.0
    assert t.tax == 476.0


def test_parse_us_by_ticker():
    """영문 대문자 티커 → USD 자동 감지"""
    result = parse_text("매수 NVDA 2 135.50")
    assert len(result) == 1
    t = result[0]
    assert t.currency == "USD"
    assert t.price == 135.50


def test_parse_us_by_decimal_price():
    """소수점 단가 → USD 자동 감지"""
    result = parse_text("매수 퀵로직 52 8.85")
    assert len(result) == 1
    assert result[0].currency == "USD"


def test_parse_multiline():
    text = "매수 삼전 10 58000\n매수 하닉 5 185000"
    result = parse_text(text)
    assert len(result) == 2
    assert result[0].name == "삼전"
    assert result[1].name == "하닉"


def test_parse_comma_in_price():
    result = parse_text("매수 삼전 10 58,000")
    assert result[0].price == 58000.0


def test_parse_english_side():
    result = parse_text("buy AAPL 3 220.50")
    assert result[0].side == "매수"
    result2 = parse_text("sell TSLA 1 350.00")
    assert result2[0].side == "매도"


def test_parse_empty_returns_empty():
    assert parse_text("") == []
    assert parse_text("그냥 대화입니다") == []


# ── 카카오 알림 파싱 ──────────────────────────────────────────

KAKAO_US_ALERT = """[메리츠증권] 해외주식 주문체결 안내
종목명 : 퀵로직(QUIK)
매매구분 : 매수
체결단가 : USD 8.8500
체결수량 : 52주
체결금액 : USD 460.20
체결일자 : 03/05"""

KAKAO_KR_ALERT = """[메리츠증권] 국내주식 주문체결 안내
종목명 : 삼성전자
매매구분 : 매도
체결단가 : 58,000
체결수량 : 10주
체결금액 : 580,000
체결일자 : 03/05"""


def test_kakao_us_buy():
    result = _parse_kakao_alert(KAKAO_US_ALERT)
    assert len(result) == 1
    t = result[0]
    assert t.name == "퀵로직"
    assert t.side == "매수"
    assert t.qty == 52
    assert t.price == pytest.approx(8.85)
    assert t.currency == "USD"
    assert t.trade_date == "2026-03-05"


def test_kakao_kr_sell():
    result = _parse_kakao_alert(KAKAO_KR_ALERT)
    assert len(result) == 1
    t = result[0]
    assert t.name == "삼성전자"
    assert t.side == "매도"
    assert t.qty == 10
    assert t.price == pytest.approx(58000.0)
    assert t.currency == "KRW"


def test_kakao_auto_detect_from_parse_text():
    """parse_text가 카카오 형식 자동 감지하는지"""
    result = parse_text(KAKAO_US_ALERT)
    assert len(result) == 1
    assert result[0].name == "퀵로직"
    assert result[0].currency == "USD"


KAKAO_KR_DOMESTIC = """[메리츠증권] 주문체결 안내
계좌명 : 송*훈
계좌번호 : 3023**04-01
종목 : 삼성전기(009150)
구분 : 매수
체결수량 : 1주
체결단가 : 410,500원
주문일자/번호 : 03/06 No. 55966"""


def test_kakao_kr_domestic_buy():
    """국내주식 카카오 알림 포맷 파싱"""
    result = _parse_kakao_alert(KAKAO_KR_DOMESTIC)
    assert len(result) == 1
    t = result[0]
    assert t.name == "삼성전기"
    assert t.side == "매수"
    assert t.qty == 1
    assert t.price == pytest.approx(410500.0)
    assert t.currency == "KRW"
    assert t.trade_date == "2026-03-06"


def test_kakao_kr_domestic_via_parse_text():
    """parse_text가 국내 알림 자동 감지"""
    result = parse_text(KAKAO_KR_DOMESTIC)
    assert len(result) == 1
    assert result[0].name == "삼성전기"
    assert result[0].currency == "KRW"


def test_kakao_missing_fields_returns_empty():
    result = _parse_kakao_alert("종목명 : 삼성전자\n체결단가 : 58000")
    # 매매구분 없음 → 빈 리스트
    assert result == []


# ── 이미지 파싱 프롬프트 연도 검증 ───────────────────────────

def test_image_parse_prompt_uses_current_year():
    """프롬프트에 하드코딩 연도 없이 현재 연도가 동적으로 주입되는지 확인"""
    from datetime import date
    from brief_my_trade.parser import _build_image_parse_prompt

    year = date.today().year
    prompt = _build_image_parse_prompt()

    assert str(year) in prompt, f"현재 연도 {year}이 프롬프트에 없음"
    assert "2025" not in prompt or year == 2025, "2025가 하드코딩돼 있음"
    assert "2026" not in prompt or year == 2026, "2026이 하드코딩돼 있음"


# ── JP 종목 파싱 ──────────────────────────────────────────────

def test_kakao_jp_buy():
    """카카오 알림톡 일본 주식 매수 파싱"""
    alert = """[메리츠증권] 해외주식 주문체결 안내

계좌명 : 송*훈
계좌번호 : 3023**04-01
종목명 : QD레이저(6613)
매매구분 : 매수
체결단가 : JPY 1,515.0000
주문수량 : 100주
체결수량 : 100주
체결금액 : JPY 151,500.00
체결일자 : 03/18"""
    result = parse_text(alert)
    assert len(result) == 1
    t = result[0]
    assert t.currency == "JPY"
    assert t.ticker == "6613"
    assert t.name == "QD레이저"
    assert t.qty == 100
    assert t.price == 1515.0


def test_kakao_jp_sell():
    """카카오 알림톡 일본 주식 매도 파싱"""
    alert = """[메리츠증권] 해외주식 주문체결 안내
종목명 : QD레이저(6613)
매매구분 : 매도
체결단가 : JPY 1,600.0000
체결수량 : 50주
체결일자 : 03/20"""
    result = parse_text(alert)
    assert len(result) == 1
    assert result[0].side == "매도"
    assert result[0].currency == "JPY"
    assert result[0].qty == 50
    assert result[0].price == 1600.0


def test_manual_jp_dot_t():
    """수동 입력 — .T suffix JP 자동 감지"""
    result = parse_text("매수 7203.T 10 3200")
    assert len(result) == 1
    assert result[0].currency == "JPY"
    assert result[0].name == "7203.T"
    assert result[0].qty == 10
    assert result[0].price == 3200.0


def test_kakao_date_always_current_year():
    """카카오 알림 MM/DD 파싱 시 항상 올해 연도 사용"""
    from datetime import date
    year = date.today().year

    alert = f"""[메리츠증권] 해외주식 주문체결 안내
종목명 : 테슬라(TSLA)
매매구분 : 매수
체결단가 : USD 250.00
체결수량 : 1주
체결일자 : 06/15"""

    result = _parse_kakao_alert(alert)
    assert len(result) == 1
    assert result[0].trade_date == f"{year}-06-15", \
        f"연도 오류: {result[0].trade_date} (기대: {year}-06-15)"
