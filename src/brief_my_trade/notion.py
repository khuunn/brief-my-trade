"""
notion.py — Notion 거래 기록 동기화
"""

from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING
from datetime import date, timedelta

import requests

if TYPE_CHECKING:
    from .store import Trade

logger = logging.getLogger(__name__)

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"


# ─── 설정 ─────────────────────────────────────────────────────

def _get_config() -> tuple[str, str] | None:
    """(token, db_id) 반환. 미설정 시 None."""
    token = os.environ.get("NOTION_TOKEN", "")
    db_id = os.environ.get("NOTION_TRADES_DB_ID", "")
    if not token or not db_id:
        return None
    return token, db_id


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


def _week_label(date_str: str) -> str:
    """날짜 → '2026-W10 (03/02~03/08)' 형식"""
    d = date.fromisoformat(date_str)
    monday = d - timedelta(days=d.weekday())
    sunday = monday + timedelta(days=6)
    week_num = d.isocalendar()[1]
    return f"{d.year}-W{week_num:02d} ({monday.strftime('%m/%d')}~{sunday.strftime('%m/%d')})"


# ─── 마크다운 → Notion 블록 변환 ──────────────────────────────

def _rich_text(content: str) -> list[dict]:
    """단순 텍스트 → Notion rich_text"""
    if not content:
        return [{"type": "text", "text": {"content": ""}}]
    # 2000자 제한 (Notion API)
    return [{"type": "text", "text": {"content": content[:2000]}}]


def _heading_block(level: int, text: str) -> dict:
    t = f"heading_{level}"
    return {
        "object": "block",
        "type": t,
        t: {"rich_text": _rich_text(text.strip())},
    }


def _para_block(text: str) -> dict:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": _rich_text(text.strip())},
    }


def _callout_block(text: str, emoji: str = "📊", color: str = "blue_background") -> dict:
    return {
        "object": "block",
        "type": "callout",
        "callout": {
            "rich_text": _rich_text(text),
            "icon": {"emoji": emoji},
            "color": color,
        },
    }


def _divider_block() -> dict:
    return {"object": "block", "type": "divider", "divider": {}}


def _table_block(rows: list[list[str]]) -> dict | None:
    """마크다운 테이블 rows → Notion table 블록"""
    if not rows:
        return None
    width = max(len(r) for r in rows)
    table_rows = []
    for row in rows:
        cells = []
        for i in range(width):
            cell_text = row[i].strip() if i < len(row) else ""
            cells.append(_rich_text(cell_text))
        table_rows.append({
            "object": "block",
            "type": "table_row",
            "table_row": {"cells": cells},
        })
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": True,
            "has_row_header": False,
            "children": table_rows,
        },
    }


def _parse_table_line(line: str) -> list[str]:
    """'| col1 | col2 |' → ['col1', 'col2']"""
    parts = line.strip().strip("|").split("|")
    return [p.strip() for p in parts]


_SEPARATOR_RE = re.compile(r"^\|[\s\-|]+\|$")


def markdown_to_notion_blocks(text: str) -> list[dict]:
    """마크다운 텍스트 → Notion 블록 리스트"""
    blocks: list[dict] = []
    lines = text.split("\n")
    i = 0

    while i < len(lines):
        line = lines[i]

        # 제목
        if line.startswith("### "):
            blocks.append(_heading_block(3, line[4:]))
        elif line.startswith("## "):
            blocks.append(_heading_block(2, line[3:]))
        elif line.startswith("# "):
            blocks.append(_heading_block(1, line[2:]))

        # 구분선 (─── 또는 ---)
        elif re.match(r"^[-─]{3,}$", line.strip()):
            blocks.append(_divider_block())

        # 인용 (> text)
        elif line.startswith("> "):
            blocks.append(_callout_block(line[2:], emoji="💬", color="gray_background"))

        # 테이블
        elif line.startswith("|"):
            table_lines: list[list[str]] = []
            while i < len(lines) and lines[i].startswith("|"):
                if not _SEPARATOR_RE.match(lines[i]):
                    table_lines.append(_parse_table_line(lines[i]))
                i += 1
            tb = _table_block(table_lines)
            if tb:
                blocks.append(tb)
            continue

        # 빈 줄
        elif line.strip() == "":
            pass

        # 일반 단락
        else:
            # 긴 텍스트는 1900자씩 분할
            content = line.strip()
            while len(content) > 1900:
                blocks.append(_para_block(content[:1900]))
                content = content[1900:]
            if content:
                blocks.append(_para_block(content))

        i += 1

    return blocks


# ─── 거래 동기화 ───────────────────────────────────────────────

def push_trade(trade: "Trade") -> str | None:
    """거래를 Notion DB에 추가. 성공 시 페이지 ID 반환."""
    cfg = _get_config()
    if not cfg:
        return None
    token, db_id = cfg

    properties = {
        "종목": {"title": [{"text": {"content": trade.name}}]},
        "날짜": {"date": {"start": trade.date}},
        "방향": {"select": {"name": trade.side}},
        "수량": {"number": trade.qty},
        "단가": {"number": round(trade.price, 4)},
        "총금액": {"number": round(trade.amount, 2)},
        "수수료": {"number": round(trade.commission, 2)},
        "세금": {"number": round(trade.tax, 2)},
        "시장": {"select": {"name": trade.market}},
        "통화": {"select": {"name": trade.currency}},
        "주차": {"select": {"name": _week_label(trade.date)}},
    }
    if trade.ticker:
        properties["종목코드"] = {"rich_text": [{"text": {"content": trade.ticker}}]}
    if trade.memo:
        properties["메모"] = {"rich_text": [{"text": {"content": trade.memo}}]}

    try:
        resp = requests.post(
            f"{NOTION_API_BASE}/pages",
            headers=_headers(token),
            json={"parent": {"database_id": db_id}, "properties": properties},
            timeout=10,
        )
        resp.raise_for_status()
        page_id = resp.json().get("id", "")
        logger.info(f"Notion 동기화 완료: {trade.name} ({page_id})")
        return page_id
    except Exception as e:
        logger.warning(f"Notion 동기화 실패 (무시): {e}")
        return None


# ─── 주간 보고서: 주별 하위 페이지 생성 ──────────────────────

def push_weekly_report_page(overview_text: str, report_text: str, week_label: str) -> bool:
    """
    주간 보고서 페이지 하위에 주별 페이지 생성.
    마크다운을 Notion 네이티브 블록으로 변환.
    """
    token = os.environ.get("NOTION_TOKEN", "")
    parent_page_id = os.environ.get("NOTION_WEEKLY_PAGE_ID", "")
    if not token or not parent_page_id:
        logger.warning("NOTION_WEEKLY_PAGE_ID 미설정 — 주간 보고서 동기화 스킵")
        return False

    headers = _headers(token)

    # 블록 생성: Overview 먼저, 구분선, 주간 보고서
    blocks: list[dict] = []
    blocks.append(_callout_block(f"🤖 자동 생성 — {week_label}", emoji="📊", color="blue_background"))
    blocks += markdown_to_notion_blocks(overview_text)
    blocks.append(_divider_block())
    blocks += markdown_to_notion_blocks(report_text)

    # Notion API: 한 번에 최대 100블록
    BATCH = 100
    try:
        # 하위 페이지 생성 (첫 번째 배치 포함)
        resp = requests.post(
            f"{NOTION_API_BASE}/pages",
            headers=headers,
            json={
                "parent": {"page_id": parent_page_id},
                "properties": {
                    "title": {"title": [{"text": {"content": f"📊 {week_label}"}}]}
                },
                "children": blocks[:BATCH],
            },
            timeout=15,
        )
        resp.raise_for_status()
        new_page_id = resp.json()["id"]

        # 100블록 초과 시 추가 배치
        for start in range(BATCH, len(blocks), BATCH):
            batch = blocks[start:start + BATCH]
            r = requests.patch(
                f"{NOTION_API_BASE}/blocks/{new_page_id}/children",
                headers=headers,
                json={"children": batch},
                timeout=15,
            )
            r.raise_for_status()

        logger.info(f"주간 보고서 Notion 페이지 생성 완료: {week_label} ({new_page_id})")
        return True
    except Exception as e:
        logger.warning(f"주간 보고서 Notion 생성 실패: {e}")
        return False


# ─── 레거시 호환 (사용 안 함) ─────────────────────────────────

def delete_page(page_id: str) -> bool:
    """Notion 페이지 삭제 (archive). undo 시 사용."""
    token = os.environ.get("NOTION_TOKEN", "")
    if not token or not page_id:
        return False
    try:
        resp = requests.patch(
            f"{NOTION_API_BASE}/pages/{page_id}",
            headers=_headers(token),
            json={"archived": True},
            timeout=10,
        )
        resp.raise_for_status()
        return True
    except Exception as e:
        logger.warning(f"Notion 페이지 삭제 실패: {e}")
        return False
