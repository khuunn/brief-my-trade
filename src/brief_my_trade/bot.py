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

from .parser import ParsedTrade, ParsedHolding, parse_image_bytes, parse_text, parse_portfolio_image_bytes
from .price import get_fx_rate
from .report import (
    fmt_money,
    format_overview,
    format_period_summary,
    format_portfolio,
    format_today_summary,
    format_week_summary,
    generate_weekly_report,
)
from .store import CapitalEvent, Trade, TradeStore

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
    # 파서에서 마켓 명시한 경우 우선
    currency = p.currency
    if currency == "USD":
        market = "US"
    fx = get_fx_rate("USD") if currency == "USD" else 1.0
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
    flag = "🇰🇷" if t.market == "KR" else "🇺🇸"
    side_emoji = "🔵" if t.side == "매수" else "🔴"
    return (
        f"✅ 기록 완료\n"
        f"{side_emoji} #{trade_id} {t.side} {flag} {t.name}\n"
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
    """스크린샷 → Claude Vision 파싱 (체결 or 보유종목 자동 판단)"""
    if not is_allowed(update):
        return

    caption = (update.message.caption or "").strip().lower()
    is_seed = any(kw in caption for kw in ["보유", "잔고", "seed", "시딩", "포트폴리오"])

    msg = await update.message.reply_text("📸 스크린샷 분석 중...")

    photo = update.message.photo[-1]
    file = await ctx.bot.get_file(photo.file_id)
    image_bytes = bytes(await file.download_as_bytearray())

    if is_seed:
        # 보유종목 화면 파싱
        try:
            holdings = parse_portfolio_image_bytes(image_bytes, "image/jpeg")
        except Exception as e:
            await msg.edit_text(f"❌ 파싱 실패: {e}")
            return

        if not holdings:
            await msg.edit_text("❌ 보유종목을 찾지 못했어요.")
            return

        store = get_store()
        results = []
        today = date.today().isoformat()
        for h in holdings:
            name, ticker, market = store.resolve_name(h.name)
            if h.ticker:
                ticker = h.ticker
            if h.market:
                market = h.market
            currency = h.currency
            fx = get_fx_rate("USD") if currency == "USD" else 1.0
            amount = h.qty * h.avg_price
            trade = Trade(
                id=None, date=today, time="00:00",
                market=market, ticker=ticker, name=name,
                side="매수", qty=h.qty, price=h.avg_price,
                amount=amount, currency=currency,
                fx_rate=fx, amount_krw=amount * fx,
                memo="seed",
            )
            trade_id = store.add_trade(trade)
            flag = "🇰🇷" if market == "KR" else "🇺🇸"
            results.append(f"#{trade_id} {flag} {name} {h.qty}주 @ {fmt_money(h.avg_price, currency)}")

        await msg.edit_text(
            f"✅ 보유종목 {len(holdings)}개 시딩 완료\n\n" + "\n".join(results)
            + "\n\n이제 /portfolio 로 미실현손익 확인 가능해요!"
        )
    else:
        # 체결 화면 파싱
        try:
            parsed_list = parse_image_bytes(image_bytes, "image/jpeg")
        except Exception as e:
            await msg.edit_text(f"❌ 파싱 실패: {e}\n\n수동 입력: `매수 종목명 수량 단가`")
            return

        if not parsed_list:
            await msg.edit_text(
                "❓ 체결 화면인가요, 보유종목 화면인가요?\n"
                "• 체결 화면: 그냥 전송\n"
                "• 보유종목 화면: 캡션에 '보유' 써서 전송"
            )
            return

        store = get_store()
        results = []
        for p in parsed_list:
            trade = parsed_to_trade(p, store)
            trade_id = store.add_trade(trade)
            results.append(format_trade_confirm(trade, trade_id))

        await msg.edit_text(
            f"총 {len(parsed_list)}건 파싱 완료\n\n" + "\n\n".join(results)
        )


# ─── 명령어 ───────────────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    help_text = """
📊 *매매기록 봇*

*거래 입력:*
텍스트: `매수 삼전 10 58000`
이미지: 스크린샷 바로 전송

*조회 명령:*
/today — 오늘 거래
/week — 이번 주 실현손익
/portfolio — 보유 포지션 + 미실현손익
/overview — 계좌 현황 (실현+미실현 합산, 기간 선택)
/pnl — 이번달 손익
/pnl 2026-01-01 2026-03-31 — 기간 지정
/report — 주간 마크다운 보고서

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
        capital = store.get_capital()
        lines = ["💰 *자본금 현황*\n"]
        for market, amt in capital.items():
            flag = "🇰🇷" if market == "KR" else "🇺🇸"
            lines.append(f"{flag} {market}: {fmt_money(amt)}")
        total = sum(capital.values())
        lines.append(f"\n합산: {fmt_money(total)}")
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
    보유종목 화면 스크린샷 일괄 등록:
      이미지 캡션에 '보유' 입력 후 전송
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
            "`/seed NVDA 5 135.50 US`\n\n"
            "여러 종목 한번에: 보유종목 화면 스크린샷을 캡션 '보유'로 전송"
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


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if not is_allowed(update):
        return
    store = get_store()
    stats = store.get_period_stats("2000-01-01", date.today().isoformat())
    capital = store.get_capital()
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
    app.add_handler(CallbackQueryHandler(callback_overview, pattern="^overview:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))

    logger.info("봇 시작")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
