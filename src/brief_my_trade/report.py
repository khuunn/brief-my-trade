"""
report.py — 요약 텍스트 + 마크다운 보고서 생성
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .store import StockSummary, TradeStore
from .price import get_fx_rate, get_unrealized_pnl
from .ta import build_ta_snapshot

_KST = timezone(timedelta(hours=9))


# ─── 포맷 유틸 ────────────────────────────────────────────────

def fmt_money(val: float, currency: str = "KRW") -> str:
    if currency == "KRW":
        return f"{val:,.0f}원"
    if currency == "JPY":
        return f"¥{val:,.0f}"
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
    today = date.today().isoformat()
    trades = store.get_today_trades()
    if not trades:
        return "오늘 거래 없음"

    # 전체 이력으로 이동평균단가 추적 후 오늘 매도분만 realized_pnl 반영
    all_trades = store.get_trades_by_date_range("2000-01-01", today)
    summaries = store.summarize_trades(all_trades, pnl_after=today)

    # 오늘 거래가 있는 종목만 표시
    today_names = {t.name for t in trades}
    lines = [f"📅 *오늘 거래 — {today}*\n"]
    for s in summaries.values():
        if s.name not in today_names:
            continue
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

    for market_label, market_code in [("🇰🇷 국내", "KR"), ("🇺🇸 해외", "US"), ("🇯🇵 일본", "JP")]:
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

    for market_label, market_code in [("🇰🇷 국내", "KR"), ("🇺🇸 해외", "US"), ("🇯🇵 일본", "JP")]:
        stats = store.get_period_stats(start, end, market_code)
        # seed 거래 제외 후 실제 거래 수
        real_count = stats.get("real_trade_count", stats["trade_count"])
        if real_count == 0:
            continue

        realized_krw = sum(
            pnl * get_fx_rate(cur)
            for cur, pnl in stats["realized_by_currency"].items()
        )

        total_realized_krw += realized_krw
        total_trade_count += real_count

        lines.append(f"*{market_label}* ({real_count}건)")
        lines.append(f"  실현손익: {fmt_pnl(realized_krw)}")
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

    for market_label, market_code in [("🇰🇷 국내", "KR"), ("🇺🇸 해외", "US"), ("🇯🇵 일본", "JP")]:
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
            pnl * get_fx_rate(cur)
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
        lines.append("---")
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
    today_str = date.today().isoformat()
    trades = store.get_trades_by_date_range(monday.isoformat(), friday.isoformat())

    lines = [
        f"# 주간 매매 보고서",
        f"> 기간: {monday} ~ {friday}",
        f"> 생성: {date.today()}",
        "",
    ]

    # ── 1) YTD 누적 성과 ─────────────────────────────────────
    ytd_start = f"{ref.year}-01-01"
    ytd_realized_krw = 0.0
    week_realized_krw = 0.0

    for market_code in ["KR", "US"]:
        ytd_stats = store.get_period_stats(ytd_start, today_str, market_code)
        week_stats = store.get_period_stats(monday.isoformat(), friday.isoformat(), market_code)
        fx = get_fx_rate("USD") if market_code == "US" else 1.0
        ytd_realized_krw += sum(
            pnl * (get_fx_rate(cur) if cur != "KRW" else 1.0)
            for cur, pnl in ytd_stats["realized_by_currency"].items()
        )
        week_realized_krw += sum(
            pnl * (get_fx_rate(cur) if cur != "KRW" else 1.0)
            for cur, pnl in week_stats["realized_by_currency"].items()
        )

    lines += [
        "## 📈 누적 성과 (YTD)",
        "| 항목 | 금액 |",
        "|---|---|",
        f"| 이번 주 실현손익 | {fmt_pnl(week_realized_krw)} |",
        f"| 연초 누적 실현손익 ({ref.year}년) | {fmt_pnl(ytd_realized_krw)} |",
        "",
    ]

    # ── 2) 이번 주 Best / Worst 종목 ──────────────────────────
    all_summaries = store.summarize_trades(trades)
    traded_summaries = {k: v for k, v in all_summaries.items() if v.realized_pnl != 0}

    if traded_summaries:
        best = max(traded_summaries.values(), key=lambda s: s.realized_pnl)
        worst = min(traded_summaries.values(), key=lambda s: s.realized_pnl)
        lines += [
            "## 🏆 이번 주 Best / Worst",
            "| 구분 | 종목 | 실현손익 |",
            "|---|---|---|",
            f"| 🥇 Best | {best.name} | {fmt_pnl(best.realized_pnl)} |",
            f"| 💀 Worst | {worst.name} | {fmt_pnl(worst.realized_pnl)} |",
            "",
        ]

    # ── 시장별 섹션 ───────────────────────────────────────────
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

    # ── 3) 다음 주 보유 포지션 ────────────────────────────────
    lines += ["## 📦 다음 주 보유 포지션", ""]
    has_position = False

    for market_label, market_code, currency in [
        ("🇰🇷 국내", "KR", "KRW"),
        ("🇺🇸 해외", "US", "USD"),
    ]:
        portfolio = store.get_portfolio(market_code)
        if not portfolio:
            continue
        has_position = True
        lines.append(f"### {market_label}")
        lines += [
            "| 종목 | 보유수량 | 평균단가 | 현재가 | 미실현손익 | 수익률 |",
            "|---|---|---|---|---|---|",
        ]
        for s in portfolio.values():
            unr = get_unrealized_pnl(
                s.name, s.ticker, s.market, s.currency,
                s.net_qty, s.avg_buy_price,
            )
            if unr:
                cur_price = unr.get("current_price_krw", unr.get("current_price", 0))
                pnl_str = fmt_pnl(unr["unrealized_pnl_krw"])
                pct_str = fmt_pct(unr["return_pct"])
                price_str = fmt_money(cur_price)
            else:
                price_str = "—"
                pnl_str = "—"
                pct_str = "—"
            lines.append(
                f"| {s.name} | {s.net_qty}주 | {fmt_money(s.avg_buy_price, currency)}"
                f" | {price_str} | {pnl_str} | {pct_str} |"
            )
        lines.append("")

    if not has_position:
        lines.append("_보유 포지션 없음 — 다음 주 현금 100%_")
        lines.append("")

    return "\n".join(lines)


# ─── TA 분석 ─────────────────────────────────────────────────

_PATTERN_LABEL: dict[str, str] = {
    "bullish_engulfing": "🟢 Bullish Engulfing (상승 장악)",
    "bearish_engulfing": "🔴 Bearish Engulfing (하락 장악)",
    "hammer":            "🔨 Hammer (망치 — 반등 신호)",
    "shooting_star":     "⭐ Shooting Star (유성 — 반락 신호)",
    "doji":              "➕ Doji (추세 불확실)",
}

_TREND_LABEL: dict[str, str] = {
    "bullish": "📈 강세 (MA20 > MA60 위)",
    "bearish": "📉 약세 (MA20 < MA60 아래)",
    "neutral": "➡️ 중립",
}


def _fmt_price(val: float, currency: str) -> str:
    if currency == "USD":
        return f"${val:,.2f}"
    return f"{val:,.0f}원"


def _fmt_volume(vol: float) -> str:
    if vol >= 1_000_000:
        return f"{vol / 1_000_000:.1f}M"
    if vol >= 1_000:
        return f"{vol / 1_000:.0f}K"
    return f"{vol:.0f}"


def _rsi_label(rsi: float) -> str:
    if rsi >= 70:
        return f"{rsi} ⚠️ 과매수"
    if rsi <= 30:
        return f"{rsi} ⚠️ 과매도"
    return f"{rsi} 중립"


def format_ta_report(store: TradeStore) -> str:
    """
    보유 종목 전체 기술적 분석 스냅샷.
    KR → US 순으로 출력. 조회 실패 종목은 스킵 표시.
    """
    lines = ["📡 *기술적 분석 스냅샷*\n"]
    has_any = False

    for market_code, flag in [("KR", "🇰🇷"), ("US", "🇺🇸"), ("JP", "🇯🇵")]:
        portfolio = store.get_portfolio(market_code)
        if not portfolio:
            continue

        for s in portfolio.values():
            has_any = True
            currency = "USD" if market_code == "US" else "KRW"
            ticker_label = f"({s.ticker})" if s.ticker else ""

            snap = build_ta_snapshot(s.ticker, market_code) if s.ticker else None

            if snap is None:
                lines.append(f"{flag} *{s.name}* {ticker_label} — 데이터 조회 실패\n")
                continue

            price_str = _fmt_price(snap["price"], currency)
            ma20_str  = _fmt_price(snap["ma20"],  currency) if snap["ma20"]  else "—"
            ma60_str  = _fmt_price(snap["ma60"],  currency) if snap["ma60"]  else "—"
            rsi_str   = _rsi_label(snap["rsi"])              if snap["rsi"]   is not None else "—"
            trend_str = _TREND_LABEL.get(snap["trend"], "—")

            # 거래량
            vol_str = _fmt_volume(snap["volume"])
            if snap["volume_ratio"] is not None:
                ratio = snap["volume_ratio"]
                diff_pct = (ratio - 1.0) * 100
                sign = "+" if diff_pct >= 0 else ""
                intensity = " 🔥" if ratio >= 1.5 else (" 🔻" if ratio <= 0.5 else "")
                vol_str += f" (평균 대비 {sign}{diff_pct:.0f}%{intensity})"

            # 캔들 패턴
            pattern_str = _PATTERN_LABEL.get(snap["candle_pattern"], "") if snap["candle_pattern"] else ""

            block = [f"{flag} *{s.name}* {ticker_label}"]
            block.append(f"  현재가: {price_str}")
            block.append(f"  MA20: {ma20_str} | MA60: {ma60_str}")
            block.append(f"  추세: {trend_str}")
            block.append(f"  RSI(14): {rsi_str}")
            block.append(f"  거래량: {vol_str}")
            if pattern_str:
                block.append(f"  캔들: {pattern_str}")
            lines.append("\n".join(block))
            lines.append("")

    if not has_any:
        return "보유 포지션 없음"

    now_kst = datetime.now(_KST)
    lines.append(f"_조회: {now_kst:%Y-%m-%d %H:%M} KST (일봉 기준)_")
    return "\n".join(lines)
