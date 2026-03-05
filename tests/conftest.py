"""
conftest.py — 공용 pytest 픽스처
"""
import pytest
from pathlib import Path
from brief_my_trade.store import Trade, TradeStore, CapitalEvent


@pytest.fixture
def store(tmp_path: Path) -> TradeStore:
    """테스트용 임시 SQLite DB"""
    return TradeStore(tmp_path / "test.db")


@pytest.fixture
def sample_kr_trade() -> Trade:
    return Trade(
        id=None, date="2026-03-05", time="10:00",
        market="KR", ticker="005930", name="삼성전자",
        side="매수", qty=10, price=58000.0,
        amount=580000.0, currency="KRW",
        fx_rate=1.0, amount_krw=580000.0,
    )


@pytest.fixture
def sample_us_trade() -> Trade:
    return Trade(
        id=None, date="2026-03-05", time="22:00",
        market="US", ticker="NVDA", name="NVIDIA",
        side="매수", qty=5, price=135.50,
        amount=677.50, currency="USD",
        fx_rate=1380.0, amount_krw=934650.0,
    )
