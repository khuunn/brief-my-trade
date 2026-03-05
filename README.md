# brief-my-trade 🦜

메리츠증권 mPOP 매매기록 → 텔레그램 봇으로 자동 기록 + 손익 분석

## 기능

- 📸 **스크린샷 파싱** — 체결확인 화면 이미지 전송 → Claude Vision으로 자동 추출
- ✍️ **텍스트 입력** — `매수 삼전 10 58000` 형식 직접 입력
- 🇰🇷🇺🇸 **국내/해외 분리** — 메리츠 국내주식 + 해외주식 모두 지원
- 💱 **환율 자동 조회** — USD/KRW 실시간 (yfinance)
- 📈 **미실현손익** — 현재가 실시간 조회 후 포지션 평가
- 💰 **기간별 수익률** — 실현/미실현/합산, 수익률 % 계산
- 📊 **주간 보고서** — 마크다운 파일 자동 생성 + 전송

## 설치

```bash
cd brief-my-trade
python -m venv .venv
source .venv/bin/activate
pip install -e .

cp .env.example .env
# .env 편집: TELEGRAM_BOT_TOKEN, ANTHROPIC_API_KEY 설정
```

## 실행

```bash
python -m brief_my_trade.bot
```

## 텔레그램 명령어

| 명령어 | 설명 |
|--------|------|
| `매수 삼전 10 58000` | 거래 직접 입력 |
| 📸 이미지 전송 | 체결확인 스크린샷 파싱 |
| `/today` | 오늘 거래 요약 |
| `/week` | 이번 주 요약 |
| `/portfolio` | 보유 포지션 + 미실현손익 |
| `/pnl` | 이번달 손익 (실현/미실현/합산) |
| `/pnl 2026-01-01 2026-03-31` | 기간 지정 손익 |
| `/pnl week` | 이번 주 손익 |
| `/pnl ytd` | 연초부터 손익 |
| `/report` | 주간 마크다운 보고서 |
| `/capital` | 자본금 현황 |
| `/capital set KR 5000000` | 국내 초기 자본 설정 |
| `/capital set US 3000 USD` | 해외 초기 자본 (달러) |
| `/capital add KR 1000000` | 추가 입금 |
| `/capital withdraw KR 500000` | 출금 |
| `/undo` | 마지막 거래 취소 |
| `/export` | CSV 내보내기 |
| `/alias 삼전 삼성전자 005930 KR` | 별칭 등록 |
| `/stats` | 전체 누적 통계 |

## 거래 입력 예시

```
매수 삼전 10 58000
매도 하닉 5 190000 238 238     ← 수수료 238, 세금 238
매수 NVDA 2 135.50             ← 해외 USD 자동 감지
매수 005930 10 58000           ← 종목코드 직접 입력
```

여러 줄 입력도 지원:
```
매수 삼전 10 58000
매수 하닉 5 185000
```

## 자동 보고서 (cron)

```bash
# 매주 금요일 18:00 KST (09:00 UTC)
0 9 * * 5 cd /path/to/brief-my-trade && python -m brief_my_trade.report_cron
```

## 환경변수

| 변수 | 필수 | 설명 |
|------|------|------|
| `TELEGRAM_BOT_TOKEN` | ✅ | BotFather 발급 토큰 |
| `ANTHROPIC_API_KEY` | ✅ | 스크린샷 파싱용 |
| `TELEGRAM_CHAT_ID` | 선택 | 허용 채팅 ID (콤마 구분) |
| `DB_PATH` | 선택 | DB 파일 경로 (기본: ./trades.db) |
| `REPORT_DIR` | 선택 | 보고서 저장 경로 (기본: ./reports) |
