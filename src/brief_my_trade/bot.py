"""
bot.py — 텔레그램 봇 메인
"""

from __future__ import annotations

import logging
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update

# .env 경로 명시 (이 파일 기준 3단계 위 = brief-my-trade/)
_ENV_PATH = Path(__file__).parent.parent.parent / ".env"
load_dotenv(_ENV_PATH, override=True)
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .parser import ParsedTrade, parse_text
from .price import get_current_price, get_fx_rate
from .report import (
    fmt_money,
    format_overview,
    format_period_summary,
    format_portfolio,
    format_ta_report,
    format_today_summary,
    format_week_summary,
    generate_weekly_report,
)
from .store import CapitalEvent, Trade, TradeStore
from .trailing import get_all_stops, get_stop, init_trailing_db, upsert_stop

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DB_PATH = Path(os.environ.get("DB_PATH", "./trades.db"))
REPORT_DIR = Path(os.environ.get("REPORT_DIR", "./reports"))
REPORT_DIR.mkdir(exist_ok=True)

ALLOWED_CHAT_IDS: set[int] = set()
_raw = os.environ.get("TELEGRAM_CHAT_ID", "")
if _raw:
    for _cid in _raw.split(","):
        _cid = _cid.strip()
        if _cid.lstrip("-").isdigit():
            ALLOWED_CHAT_IDS.add(int(_cid))


# ─── 헬퍼 ────────────────────────────────────────────────────

def get_store() -> TradeStore:
    return TradeStore(DB_PATH)


def is_allowed(update: Update) -> bool:
    if not ALLOWED_CHAT_IDS:
        return True
    return update.effective_chat.id in ALLOWED_CHAT_IDS


def parsed_to_trade(p: ParsedTrade, store: TradeStore) -> Trade:
    name, ticker, market = store.resolve_name(p.name)
    # 카카오 알림톡 파서가 추출한 ticker/market 우선 적용
    if p.ticker:
        ticker = p.ticker
    currency = p.currency
    if currency == "USD":
        market = "US"
    elif currency == "JPY":
        market = "JP"
    fx = get_fx_rate(currency) if currency in ("USD", "JPY") else 1.0
    amount = p.qty * p.price
    amount_krw = amount * fx
    trade_date = p.trade_date or date.today().isoformat()
    trade_time = p.trade_time or datetime.now().strftime("%H:%M")
    return Trade(
        id=None,
        date=trade_date, time=trade_time,
        market=market, ticker=ticker, name=name,
        side=p.side, qty=p.qty, price=p.price,
        amount=amount, currency=currency,
        fx_rate=fx, amount_krw=amount_krw,
        commission=p.commission, tax=p.tax,
    )


def format_trade_confirm(t: Trade, trade_id: int) -> str:
    flag = {"KR": "🇰🇷", "US": "🇺🇸", "JP": "🇯🇵"}.get(t.market, "🌐")
    side_emoji = "🔵" if t.side == "매수" else "🔴"
    ticker_str = f" ({t.ticker})" if t.ticker else ""
    return (
        f"✅ 기록 완료\n"
        f"{side_emoji} #{trade_id} {t.side} {flag} {t.name}{ticker_str}\n"
        f"{t.qty}주 × {fmt_money(t.price, t.currency)} = {fmt_money(t.amount, t.currency)}"
        + (f"\n💱 원화환산: {fmt_money(t.amount_krw)}" if t.currency != "KRW" else "")
        + (f"\n수수료: {fmt_money(t.commission, t.currency)} / 세금: {fmt_money(t.tax, t.currency)}"
           if t.commission or t.tax else "")
    )


# ─── 핸들러 ───────────────────────────────────────────────────

async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    text = update.message.text or ""
    store = get_store()

    parsed = parse_text(text)
    if not parsed:
        return  # 일반 대화 → 무시

    results = []
    for p in parsed:
        trade = parsed_to_trade(p, store)
        trade_id = store.add_trade(trade)
        results.append(format_trade_confirm(trade, trade_id))

    await update.message.reply_text("\n\n".join(results))


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    await update.message.reply_text("📝 알림톡 텍스트를 복붙해서 보내주세요.")


# ─── 명령어 ───────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    help_text = """
📊 *매매기록 봇*

*거래 입력:*
`매수 삼전 10 58000`
알림톡 텍스트 복붙 (종목명(코드) 자동 파싱)

*조회 명령:*
/today — 오늘 거래
/week — 이번 주 실현손익
/portfolio — 보유 포지션 + 미실현손익
/overview — 계좌 현황 (실현+미실현 합산, 기간 선택)
/pnl — 이번달 손익
/pnl 2026-01-01 2026-03-31 — 기간 지정
/report — 주간 마크다운 보고서
/trailing — 추적손절매 현황
/trailing TICKER — 특정 종목 현황
/ta — 기술적 분석 (MA, RSI, 거래량, 캔들)

*자본금:*
/capital — 자본금 현황
/capital set KR 5000000 — 국내 초기 자본 설정
/capital set US 3000 USD — 해외 초기 자본 (달러)
/capital add KR 1000000 — 추가 입금
/capital withdraw KR 500000 — 출금

*기타:*
/undo — 마지막 거래 취소
/export — CSV 내보내기
/alias 삼전 삼성전자 005930 KR — 별칭 등록
/stats — 전체 통계
"""
    await update.message.reply_text(help_text, parse_mode=ParseMode.MARKDOWN)


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    store = get_store()
    await update.message.reply_text(format_today_summary(store), parse_mode=ParseMode.MARKDOWN)


async def cmd_week(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    store = get_store()
    await update.message.reply_text(format_week_summary(store), parse_mode=ParseMode.MARKDOWN)


async def cmd_portfolio(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    store = get_store()
    msg = await update.message.reply_text("📡 현재가 조회 중...")
    text = format_portfolio(store)
    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_ta(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /ta — 보유 종목 기술적 분석 스냅샷
    MA20/60, RSI(14), 거래량, 캔들 패턴 (일봉 기준)
    """
    if not is_allowed(update):
        return
    store = get_store()
    msg = await update.message.reply_text("📡 TA 데이터 조회 중...")
    text = format_ta_report(store)
    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_pnl(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /pnl                          → 이번달
    /pnl 2026-01-01 2026-03-31   → 기간 지정
    /pnl week                     → 이번 주
    /pnl ytd                      → 연초부터
    """
    if not is_allowed(update):
        return

    args = ctx.args or []
    today = date.today()

    if len(args) == 2:
        start, end = args[0], args[1]
    elif len(args) == 1 and args[0] == "week":
        monday = today - timedelta(days=today.weekday())
        start, end = monday.isoformat(), (monday + timedelta(days=6)).isoformat()
    elif len(args) == 1 and args[0] == "ytd":
        start, end = f"{today.year}-01-01", today.isoformat()
    else:
        # 이번달
        start = today.replace(day=1).isoformat()
        end = today.isoformat()

    store = get_store()
    msg = await update.message.reply_text("📡 현재가 조회 중...")
    text = format_period_summary(store, start, end)
    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_report(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    store = get_store()
    ref = ctx.args[0] if ctx.args else None
    content = generate_weekly_report(store, ref)

    today = date.today()
    filename = f"report_{today}.md"
    report_path = REPORT_DIR / filename
    report_path.write_text(content, encoding="utf-8")

    with open(report_path, "rb") as f:
        await update.message.reply_document(document=f, filename=filename)


async def cmd_capital(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /capital                         → 현황 조회
    /capital set KR 5000000          → 초기 자본 설정 (KRW)
    /capital set US 3000 USD         → 해외 초기 자본 (외화)
    /capital add KR 1000000          → 추가 입금
    /capital withdraw KR 500000      → 출금
    """
    if not is_allowed(update):
        return
    store = get_store()
    args = ctx.args or []

    if not args:
        # 현황 조회
        capital = store.get_capital()   # 순수 자본금 (입출금만)
        cash = store.get_cash()         # 예수금 (자본금 - 매수 + 매도)
        markets = sorted(set(list(capital.keys()) + list(cash.keys())))
        lines = ["💰 *자본금 현황*\n"]
        for market in markets:
            flag = "🇰🇷" if market == "KR" else "🇺🇸"
            cap = capital.get(market, 0)
            avail = cash.get(market, 0)
            lines.append(f"{flag} {market}: 투입 {fmt_money(cap)} | 예수금 {fmt_money(avail)}")
        total_cap = sum(capital.values())
        total_cash = sum(cash.values())
        lines.append(f"\n투입 합산: {fmt_money(total_cap)}")
        lines.append(f"예수금 합산: {fmt_money(total_cash)}")
        await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
        return

    action = args[0].lower()
    if action not in ("set", "add", "withdraw") or len(args) < 3:
        await update.message.reply_text("사용법: /capital set KR 5000000")
        return

    market = args[1].upper()
    amount_raw = float(args[2].replace(",", ""))
    currency = args[3].upper() if len(args) > 3 else "KRW"

    fx = get_fx_rate(currency) if currency != "KRW" else 1.0
    amount_krw = amount_raw * fx

    type_map = {"set": "initial", "add": "deposit", "withdraw": "withdraw"}
    event = CapitalEvent(
        id=None,
        date=date.today().isoformat(),
        market=market,
        type=type_map[action],
        amount_krw=amount_krw,
        currency=currency,
        fx_rate=fx,
    )
    store.add_capital_event(event)

    label = {"set": "초기 자본 설정", "add": "입금", "withdraw": "출금"}[action]
    flag = "🇰🇷" if market == "KR" else "🇺🇸"
    await update.message.reply_text(
        f"✅ {flag} {market} {label}: {fmt_money(amount_raw, currency)}"
        + (f" (≈ {fmt_money(amount_krw)})" if currency != "KRW" else "")
    )


async def cmd_undo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    store = get_store()
    trade = store.undo_last()
    if not trade:
        await update.message.reply_text("취소할 거래 없음")
        return
    await update.message.reply_text(
        f"↩️ 취소됨: #{trade.id} {trade.side} {trade.name} {trade.qty}주"
    )


async def cmd_export(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    store = get_store()
    args = ctx.args or []
    start = args[0] if len(args) > 0 else None
    end = args[1] if len(args) > 1 else None
    csv_text = store.export_csv(start, end)

    filename = f"trades_{date.today()}.csv"
    with tempfile.NamedTemporaryFile(mode="w", suffix=".csv", delete=False, encoding="utf-8-sig") as f:
        f.write(csv_text)
        tmp_path = f.name

    with open(tmp_path, "rb") as f:
        await update.message.reply_document(document=f, filename=filename)
    Path(tmp_path).unlink(missing_ok=True)


async def cmd_seed(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    기존 보유종목 단건 등록:
      /seed 삼성전자 10 58000
      /seed NVDA 5 135.50 US
    """
    if not is_allowed(update):
        return
    args = ctx.args or []
    if len(args) < 3:
        await update.message.reply_text(
            "사용법:\n"
            "`/seed 종목명 수량 평균단가 [KR|US]`\n\n"
            "예시:\n"
            "`/seed 삼성전자 10 58000`\n"
            "`/seed NVDA 5 135.50 US`"
        )
        return

    store = get_store()
    name_raw, qty_str, price_str = args[0], args[1], args[2]
    market_arg = args[3].upper() if len(args) > 3 else None

    name, ticker, market = store.resolve_name(name_raw)
    if market_arg:
        market = market_arg

    currency = "USD" if market == "US" else "KRW"
    qty = int(qty_str)
    price = float(price_str.replace(",", ""))
    fx = get_fx_rate("USD") if currency == "USD" else 1.0
    amount = qty * price

    trade = Trade(
        id=None, date=date.today().isoformat(), time="00:00",
        market=market, ticker=ticker, name=name,
        side="매수", qty=qty, price=price,
        amount=amount, currency=currency,
        fx_rate=fx, amount_krw=amount * fx,
        memo="seed",
    )
    trade_id = store.add_trade(trade)
    flag = "🇰🇷" if market == "KR" else "🇺🇸"
    await update.message.reply_text(
        f"✅ #{trade_id} {flag} {name} {qty}주 @ {fmt_money(price, currency)} 등록 완료"
    )


async def cmd_alias(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /alias 삼전 삼성전자 005930 KR
    /alias NVDA NVIDIA NVDA US
    """
    if not is_allowed(update):
        return
    args = ctx.args or []
    if len(args) < 2:
        await update.message.reply_text("사용법: /alias 줄임말 종목명 [티커] [KR|US]")
        return
    alias = args[0]
    name = args[1]
    ticker = args[2] if len(args) > 2 else ""
    market = args[3].upper() if len(args) > 3 else "KR"
    store = get_store()
    store.add_alias(alias, name, ticker, market)
    await update.message.reply_text(f"✅ 별칭 등록: {alias} → {name} ({ticker}, {market})")


OVERVIEW_PERIODS = [
    ("오늘",     "overview:today"),
    ("이번 주",  "overview:week"),
    ("이번 달",  "overview:month"),
    ("3개월",    "overview:3m"),
    ("6개월",    "overview:6m"),
    ("올해(YTD)","overview:1y"),
    ("전체기간", "overview:all"),
]

def build_overview_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        InlineKeyboardButton(label, callback_data=data)
        for label, data in OVERVIEW_PERIODS
    ]
    # 3열 배치
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    return InlineKeyboardMarkup(rows)


async def cmd_overview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /overview              → 기간 선택 버튼
    /overview week         → 이번 주
    /overview month        → 이번 달
    /overview 3m / 6m / 1y
    /overview all          → 전체기간
    /overview 2026-01-01 2026-03-05
    """
    if not is_allowed(update):
        return

    args = ctx.args or []

    if not args:
        await update.message.reply_text(
            "📊 *계좌 현황 — 기간 선택*",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_overview_keyboard(),
        )
        return

    period_key = " ".join(args)
    store = get_store()
    msg = await update.message.reply_text("📡 현재가 조회 중...")
    text = format_overview(store, period_key)
    await msg.edit_text(text, parse_mode=ParseMode.MARKDOWN)


async def callback_overview(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """인라인 버튼 클릭 처리"""
    query = update.callback_query
    await query.answer()

    if not query.data.startswith("overview:"):
        return

    period_key = query.data.split(":", 1)[1]
    store = get_store()

    # 기존 메시지를 "조회 중"으로 업데이트
    await query.edit_message_text("📡 현재가 조회 중...", parse_mode=ParseMode.MARKDOWN)
    text = format_overview(store, period_key)
    # 결과 + 버튼 다시 표시 (다른 기간으로 전환 가능)
    await query.edit_message_text(
        text,
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_overview_keyboard(),
    )


async def cmd_trailing(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    /trailing          → 전체 보유 종목 trailing stop 현황
    /trailing TICKER   → 특정 종목 현황
    """
    if not is_allowed(update):
        return

    args = ctx.args or []
    store = get_store()
    init_trailing_db(DB_PATH)

    today = date.today().isoformat()
    STOP_PCT = 10.0

    # 특정 종목 조회
    if args:
        raw = args[0]
        name, ticker, market = store.resolve_name(raw)
        ticker = ticker or raw.upper()

        # DB에 기록이 있으면 조회
        stop = get_stop(DB_PATH, ticker, market)
        if not stop:
            # 실시간으로 계산
            portfolio = store.get_portfolio()
            summary = portfolio.get(name) or next(
                (v for v in portfolio.values() if v.ticker.upper() == ticker.upper()), None
            )
            if not summary:
                await update.message.reply_text(f"❌ '{raw}' — 보유 포지션 없음")
                return

            msg = await update.message.reply_text("📡 현재가 조회 중...")
            current_price = get_current_price(summary.ticker, summary.market)
            if not current_price:
                await msg.edit_text(f"❌ 현재가 조회 실패: {summary.ticker}")
                return

            buy_price = summary.avg_buy_price
            if summary.market == "US":
                fx = get_fx_rate("USD")
                buy_price = buy_price / fx if fx > 0 else buy_price

            result = upsert_stop(DB_PATH, summary.ticker, summary.market,
                                 current_price, buy_price, STOP_PCT)
            stop_name = summary.name
        else:
            msg = await update.message.reply_text("📡 현재가 조회 중...")
            current_price = get_current_price(ticker, market)
            if not current_price:
                await msg.edit_text(f"❌ 현재가 조회 실패: {ticker}")
                return
            portfolio = store.get_portfolio()
            summary = next(
                (v for v in portfolio.values() if v.ticker.upper() == ticker.upper()), None
            )
            buy_price = summary.avg_buy_price if summary else stop["buy_price"]
            if market == "US":
                fx = get_fx_rate("USD")
                buy_price = buy_price / fx if fx > 0 else buy_price

            result = upsert_stop(DB_PATH, ticker, market, current_price, buy_price, STOP_PCT)
            stop_name = summary.name if summary else name

        flag = "🇰🇷" if market == "KR" else "🇺🇸"
        gap = result["gap_pct"]
        gap_str = f"+{gap:.1f}%" if gap > 0 else f"{gap:.1f}%"
        emoji = "🚨" if result["triggered"] else ("⚠️" if result["warning"] else "✅")

        def fp(p):
            return f"${p:,.2f}" if market == "US" else f"{p:,.0f}"

        text = (
            f"📊 추적손절매 현황 ({today})\n\n"
            f"{flag} {stop_name} ({ticker})\n"
            f"현재가: {fp(result['current_price'])}\n"
            f"고점: {fp(result['high_since_buy'])}\n"
            f"고점기반 손절선: {fp(result['stop_price'])} "
            f"(여유 {'+' if result['gap_from_high']>0 else ''}{result['gap_from_high']:.1f}%)\n"
            f"매수가기반 손절선: {fp(result['buy_stop'])} "
            f"(여유 {'+' if result['gap_from_buy']>0 else ''}{result['gap_from_buy']:.1f}%)\n"
            f"상태: {emoji} {gap_str}"
        )
        await msg.edit_text(text)
        return

    # 전체 현황
    msg = await update.message.reply_text("📡 현재가 조회 중...")
    portfolio = store.get_portfolio()

    if not portfolio:
        await msg.edit_text("보유 포지션이 없습니다.")
        return

    kr_lines: list[str] = []
    us_lines: list[str] = []
    errors: list[str] = []

    for name, summary in portfolio.items():
        if not summary.ticker:
            errors.append(f"{name} (티커 없음)")
            continue

        current_price = get_current_price(summary.ticker, summary.market)
        if not current_price:
            errors.append(f"{name} (조회 실패)")
            continue

        buy_price = summary.avg_buy_price
        if summary.market == "US":
            fx = get_fx_rate("USD")
            buy_price = buy_price / fx if fx > 0 else buy_price

        result = upsert_stop(DB_PATH, summary.ticker, summary.market,
                             current_price, buy_price, STOP_PCT)

        def fp(p, mkt=summary.market):
            return f"${p:,.2f}" if mkt == "US" else f"{p:,.0f}"

        gh = result.get("gap_from_high", result["gap_pct"])
        gb = result.get("gap_from_buy",  result["gap_pct"])
        emoji = "🚨" if result["triggered"] else ("⚠️" if result["warning"] else "✅")
        def _gap(g): return f"+{g:.1f}%" if g > 0 else f"{g:.1f}%"
        line = (
            f"{name} {emoji}\n"
            f"  현재 {fp(current_price)}\n"
            f"  고점손절  고점 {fp(result['high_since_buy'])} → 손절선 {fp(result['stop_price'])} | 여유 {_gap(gh)}\n"
            f"  매수손절  손절선 {fp(result['buy_stop'])} | 여유 {_gap(gb)}"
        )
        if summary.market == "KR":
            kr_lines.append(line)
        else:
            us_lines.append(line)

    lines = [f"📊 추적손절매 현황 ({today})"]
    if kr_lines:
        lines.append("\n🇰🇷 국내")
        lines.extend(kr_lines)
    if us_lines:
        lines.append("\n🇺🇸 미국")
        lines.extend(us_lines)
    if errors:
        lines.append(f"\n⚠️ 조회 실패: {', '.join(errors)}")

    await msg.edit_text("\n".join(lines))


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    store = get_store()
    stats = store.get_period_stats("2000-01-01", date.today().isoformat())
    capital = store.get_capital()   # 투입 자본 기준으로 수익률 계산
    total_capital = sum(capital.values())

    lines = [
        "📊 *전체 통계*\n",
        f"총 거래: {stats['trade_count']}건",
        f"총 매수: {fmt_money(stats['total_buy_krw'])}",
        f"총 매도: {fmt_money(stats['total_sell_krw'])}",
    ]
    for cur, pnl in stats["realized_by_currency"].items():
        lines.append(f"실현손익 ({cur}): {fmt_money(pnl, cur)}")
    if total_capital > 0:
        total_realized = sum(stats["realized_by_currency"].values())
        lines.append(f"투입 자본: {fmt_money(total_capital)}")
        lines.append(f"수익률 (실현): {total_realized / total_capital * 100:.2f}%")

    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


# ─── 앱 실행 ──────────────────────────────────────────────────

def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN 환경변수 없음")

    app = Application.builder().token(token).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("week", cmd_week))
    app.add_handler(CommandHandler("portfolio", cmd_portfolio))
    app.add_handler(CommandHandler("pnl", cmd_pnl))
    app.add_handler(CommandHandler("report", cmd_report))
    app.add_handler(CommandHandler("capital", cmd_capital))
    app.add_handler(CommandHandler("seed", cmd_seed))
    app.add_handler(CommandHandler("undo", cmd_undo))
    app.add_handler(CommandHandler("export", cmd_export))
    app.add_handler(CommandHandler("alias", cmd_alias))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("overview", cmd_overview))
    app.add_handler(CommandHandler("trailing", cmd_trailing))
    app.add_handler(CommandHandler("ta", cmd_ta))
    app.add_handler(CallbackQueryHandler(callback_overview, pattern="^overview:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("봇 시작")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
