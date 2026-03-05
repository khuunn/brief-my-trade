"""
report.py — 요약 텍스트 + 마크다운 보고서 생성
"""

from __future__ import annotations

from datetime import date, timedelta

from .store import StockSummary, TradeStore
from .price import get_fx_rate, get_unrealized_pnl


# ─── 포맷 유틸 ────────────────────────────────────────────────

def fmt_money(val: float, currency: str = "KRW") -> str:
    if currency == "KRW":
        return f"{val:,.0f}원"
    return f"{val:,.2f} {currency}"


def fmt_pnl(val: float, currency: str = "KRW") -> str:
    sym = "원" if currency == "KRW" else f" {currency}"
    if val > 0:
        return f"🟢 +{val:,.0f}{sym}"
    if val < 0:
        return f"🔴 {val:,.0f}{sym}"
    return f"⚪ 0{sym}"


def fmt_pct(val: float) -> str:
    if val > 0:
        return f"+{val:.2f}%"
    return f"{val:.2f}%"


# ─── 텔레그램 짧은 요약 ───────────────────────────────────────

def format_today_summary(store: TradeStore) -> str:
    trades = store.get_today_trades()
    if not trades:
        return "오늘 거래 없음"

    summaries = store.summarize_trades(trades)
    lines = [f"📅 *오늘 거래 — {date.today().isoformat()}*\n"]
    for s in summaries.values():
        lines.append(f"• {s.name} ({s.market})")
        if s.buy_qty:
            lines.append(f"  매수 {s.buy_qty}주 × {fmt_money(s.avg_buy_price, s.currency)}")
        if s.sell_qty:
            lines.append(f"  매도 {s.sell_qty}주 × {fmt_money(s.avg_sell_price, s.currency)}")
            lines.append(f"  실현손익: {fmt_pnl(s.realized_pnl, s.currency)}")
    lines.append(f"\n총 {len(trades)}건")
    return "\n".join(lines)


def format_portfolio(store: TradeStore) -> str:
    lines = ["📊 *보유 포지션*\n"]
    has_any = False

    for market_label, market_code in [("🇰🇷 국내", "KR"), ("🇺🇸 해외", "US")]:
        portfolio = store.get_portfolio(market_code)
        if not portfolio:
            continue
        has_any = True
        lines.append(f"*{market_label}*")
        for s in portfolio.values():
            unr = get_unrealized_pnl(
                s.name, s.ticker, s.market, s.currency,
                s.net_qty, s.avg_buy_price,
            )
            price_str = ""
            pnl_str = ""
            if unr:
                cur_price_krw = unr.get("current_price_krw", unr.get("current_price", 0))
                price_str = f" | 현재가 {fmt_money(cur_price_krw)}"
                pnl_str = f"\n    미실현: {fmt_pnl(unr['unrealized_pnl_krw'])} ({fmt_pct(unr['return_pct'])})"
            lines.append(
                f"• {s.name} {s.net_qty}주 "
                f"(평균 {fmt_money(s.avg_buy_price, s.currency)}){price_str}{pnl_str}"
            )
        lines.append("")

    if not has_any:
        return "보유 포지션 없음"
    return "\n".join(lines)


def format_period_summary(store: TradeStore, start: str, end: str) -> str:
    """
    기간 손익 요약.
    - 실현손익: 기간 내 매도로 확정된 손익
    - 미실현손익: /portfolio에서 확인 (기간 리포트에선 제외)
    - seed 거래는 거래수에서 제외
    """
    lines = [f"📈 *기간 손익 — {start} ~ {end}*\n"]

    total_realized_krw = 0.0
    total_trade_count = 0

    for market_label, market_code in [("🇰🇷 국내", "KR"), ("🇺🇸 해외", "US")]:
        stats = store.get_period_stats(start, end, market_code)
        # seed 거래 제외 후 실제 거래 수
        real_count = stats.get("real_trade_count", stats["trade_count"])
        if real_count == 0:
            continue

        realized_krw = sum(
            pnl * get_fx_rate(cur) if cur != "KRW" else pnl
            for cur, pnl in stats["realized_by_currency"].items()
        )
        cap = store.get_capital(market_code).get(market_code, 0)
        ret_pct = (realized_krw / cap * 100) if cap > 0 else 0.0

        total_realized_krw += realized_krw
        total_trade_count += real_count

        lines.append(f"*{market_label}* ({real_count}건)")
        lines.append(f"  실현손익: {fmt_pnl(realized_krw)}")
        if cap > 0:
            lines.append(f"  수익률:   {fmt_pct(ret_pct)}")
        lines.append("")

    if total_trade_count == 0:
        lines.append("기간 내 거래 없음")
        lines.append("_(미실현손익은 /portfolio 에서 확인)_")
        return "\n".join(lines)

    lines.append("*📊 합산*")
    lines.append(f"  실현손익: {fmt_pnl(total_realized_krw)}")
    lines.append("")
    lines.append("_(미실현손익은 /portfolio 에서 확인)_")

    return "\n".join(lines)


def format_week_summary(store: TradeStore) -> str:
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    friday = monday + timedelta(days=4)
    return format_period_summary(store, monday.isoformat(), friday.isoformat())


# ─── Overview (실현 + 미실현 합산) ───────────────────────────

def parse_overview_period(key: str) -> tuple[str | None, str, str]:
    """
    key → (start, end, label)
    start=None이면 전체기간 실현손익 (all)
    """
    today = date.today()

    if key in ("all", "전체"):
        return None, today.isoformat(), "전체기간"
    if key in ("today", "오늘"):
        return today.isoformat(), today.isoformat(), "오늘"
    if key in ("week", "이번주"):
        monday = today - timedelta(days=today.weekday())
        return monday.isoformat(), today.isoformat(), "이번 주"
    if key in ("month", "이번달"):
        return today.replace(day=1).isoformat(), today.isoformat(), "이번 달"
    if key == "3m":
        return (today - timedelta(days=90)).isoformat(), today.isoformat(), "최근 3개월"
    if key == "6m":
        return (today - timedelta(days=180)).isoformat(), today.isoformat(), "최근 6개월"
    if key in ("1y", "ytd"):
        return f"{today.year}-01-01", today.isoformat(), "올해 (YTD)"
    # 날짜 직접 지정: "2026-01-01 2026-03-05"
    parts = key.split()
    if len(parts) == 2:
        return parts[0], parts[1], f"{parts[0]} ~ {parts[1]}"
    return None, today.isoformat(), "전체기간"


def format_overview(store: TradeStore, period_key: str = "all") -> str:
    start, end, label = parse_overview_period(period_key)

    lines = [f"📊 *계좌 현황 — {label}*\n"]
    lines.append(f"_(미실현: 현재 기준 / 실현: {label} 기준)_\n")

    total_cost_krw = 0.0
    total_eval_krw = 0.0
    total_unrealized_krw = 0.0
    total_realized_krw = 0.0

    for market_label, market_code in [("🇰🇷 국내", "KR"), ("🇺🇸 해외", "US")]:
        portfolio = store.get_portfolio(market_code)

        # 투자원금 + 현재평가 + 미실현손익
        cost_krw = 0.0
        eval_krw = 0.0
        unrealized_krw = 0.0
        for s in portfolio.values():
            pos_cost = s.avg_buy_price * s.net_qty  # 원화 기준
            cost_krw += pos_cost
            unr = get_unrealized_pnl(
                s.name, s.ticker, s.market, s.currency,
                s.net_qty, s.avg_buy_price,
            )
            if unr:
                cur_price_krw = unr.get("current_price_krw", unr.get("current_price", 0))
                pos_eval = cur_price_krw * s.net_qty
                eval_krw += pos_eval
                unrealized_krw += unr.get("unrealized_pnl_krw", 0)
            else:
                eval_krw += pos_cost  # 가격 미조회시 원가 유지

        # 실현손익 (기간 내)
        if start:
            stats = store.get_period_stats(start, end, market_code)
        else:
            stats = store.get_period_stats("2000-01-01", end, market_code)

        realized_krw = sum(
            pnl * get_fx_rate(cur) if cur != "KRW" else pnl
            for cur, pnl in stats["realized_by_currency"].items()
        )

        if cost_krw == 0 and realized_krw == 0:
            continue

        unr_pct = (unrealized_krw / cost_krw * 100) if cost_krw > 0 else 0.0
        total_pnl = unrealized_krw + realized_krw
        total_pnl_pct = (total_pnl / cost_krw * 100) if cost_krw > 0 else 0.0

        lines.append(f"*{market_label}*")
        if cost_krw > 0:
            lines.append(f"  투자원금    {fmt_money(cost_krw)}")
            lines.append(f"  현재평가    {fmt_money(eval_krw)}")
        lines.append(f"  미실현손익  {fmt_pnl(unrealized_krw)} ({fmt_pct(unr_pct)})")
        lines.append(f"  기간실현손익 {fmt_pnl(realized_krw)}")
        lines.append(f"  합산손익    {fmt_pnl(total_pnl)} ({fmt_pct(total_pnl_pct)})")
        lines.append("")

        total_cost_krw += cost_krw
        total_eval_krw += eval_krw
        total_unrealized_krw += unrealized_krw
        total_realized_krw += realized_krw

    if total_cost_krw == 0 and total_realized_krw == 0:
        lines.append("데이터 없음")
        return "\n".join(lines)

    total_pnl = total_unrealized_krw + total_realized_krw
    total_ret_pct = (total_pnl / total_cost_krw * 100) if total_cost_krw > 0 else 0.0

    lines.append("*📊 합산*")
    lines.append(f"  총 투자원금  {fmt_money(total_cost_krw)}")
    lines.append(f"  총 현재평가  {fmt_money(total_eval_krw)}")
    lines.append(f"  미실현손익   {fmt_pnl(total_unrealized_krw)}")
    lines.append(f"  기간실현손익  {fmt_pnl(total_realized_krw)}")
    lines.append(f"  ─────────────────")
    lines.append(f"  총 손익     {fmt_pnl(total_pnl)} ({fmt_pct(total_ret_pct)})")

    return "\n".join(lines)


# ─── 마크다운 보고서 ──────────────────────────────────────────

def generate_weekly_report(store: TradeStore, ref_date: str = None) -> str:
    ref = date.fromisoformat(ref_date) if ref_date else date.today()
    monday = ref - timedelta(days=ref.weekday())
    friday = monday + timedelta(days=4)
    trades = store.get_trades_by_date_range(monday.isoformat(), friday.isoformat())

    lines = [
        f"# 주간 매매 보고서",
        f"> 기간: {monday} ~ {friday}",
        f"> 생성: {date.today()}",
        "",
    ]

    # 시장별 섹션
    for market_label, market_code, currency in [
        ("🇰🇷 국내 (KRW)", "KR", "KRW"),
        ("🇺🇸 해외 (USD)", "US", "USD"),
    ]:
        mkt_trades = [t for t in trades if t.market == market_code]
        if not mkt_trades:
            continue

        summaries = store.summarize_trades(mkt_trades)
        total_buy = sum(t.amount for t in mkt_trades if t.side == "매수")
        total_sell = sum(t.amount for t in mkt_trades if t.side == "매도")
        total_comm = sum(t.commission for t in mkt_trades)
        total_tax = sum(t.tax for t in mkt_trades)
        realized = sum(s.realized_pnl for s in summaries.values())
        winners = sum(1 for s in summaries.values() if s.realized_pnl > 0)
        losers = sum(1 for s in summaries.values() if s.realized_pnl < 0)
        wr = winners / (winners + losers) * 100 if (winners + losers) > 0 else 0

        lines += [
            f"## {market_label}",
            "",
            "### 주간 요약",
            "| 항목 | 값 |",
            "|---|---|",
            f"| 총 거래 | {len(mkt_trades)}건 |",
            f"| 매수 금액 | {fmt_money(total_buy, currency)} |",
            f"| 매도 금액 | {fmt_money(total_sell, currency)} |",
            f"| 실현 손익 | {fmt_pnl(realized, currency)} |",
            f"| 수수료 | {fmt_money(total_comm, currency)} |",
            f"| 세금 | {fmt_money(total_tax, currency)} |",
            f"| 승률 | {wr:.1f}% ({winners}W / {losers}L) |",
            "",
            "### 종목별 상세",
            "| 종목명 | 매수 | 매도 | 실현손익 | 거래 |",
            "|---|---|---|---|---|",
        ]
        for s in summaries.values():
            buy_str = f"{s.buy_qty}주 / {fmt_money(s.buy_amount, currency)}" if s.buy_qty else "—"
            sell_str = f"{s.sell_qty}주 / {fmt_money(s.sell_amount, currency)}" if s.sell_qty else "—"
            lines.append(
                f"| {s.name} | {buy_str} | {sell_str} | {fmt_pnl(s.realized_pnl, currency)} | {s.trade_count}건 |"
            )
        lines += ["", "### 일별 거래 로그", "| 날짜 | 시간 | 종목 | 구분 | 수량 | 단가 | 금액 |", "|---|---|---|---|---|---|---|"]
        for t in mkt_trades:
            lines.append(
                f"| {t.date} | {t.time} | {t.name} | {t.side} | {t.qty} | {fmt_money(t.price, currency)} | {fmt_money(t.amount, currency)} |"
            )
        lines.append("")

    return "\n".join(lines)
