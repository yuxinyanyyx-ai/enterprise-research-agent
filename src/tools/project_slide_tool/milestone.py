from __future__ import annotations

from .constants import (
    CHINA_DATE_COL,
    CHINA_NOTE_COL,
    GLOBAL_DATE_COL,
    GLOBAL_NOTE_COL,
    MILESTONE_ALIASES,
    MILESTONE_ORDER,
)
from .models import MilestoneEvent, TrackData
from .pptx_utils import find_slide_title_shape, normalize_text, read_table


def _canonical_milestone(name: str) -> str | None:
    n = normalize_text(name)
    for canonical, aliases in MILESTONE_ALIASES.items():
        for alias in aliases:
            if n.lower() == alias.lower():
                return canonical
    return None


def _study_label(table, col: int) -> str:
    if len(table) < 3 or col >= len(table[2]):
        return ""
    raw = normalize_text(table[2][col])
    if " " in raw and "\n" not in raw:
        parts = raw.replace("Dareon-5", "\nDareon-5").replace("1438-0005", "1438-0005\n").split("\n")
        return "\n".join(p.strip() for p in parts if p.strip())
    return raw


def parse_milestone_slide(slide) -> tuple[TrackData, TrackData]:
    table = None
    for shape in slide.shapes:
        if shape.has_table and len(shape.table.rows) >= 4 and len(shape.table.columns) >= 9:
            table = read_table(shape)
            break
    if not table:
        raise ValueError("第 3 页未找到 9 列 Milestone 表")

    upper = TrackData(study_label=_study_label(table, GLOBAL_DATE_COL))
    lower = TrackData(study_label=_study_label(table, CHINA_DATE_COL))

    for row in table[3:]:
        if not row or not row[0].strip():
            continue
        ms = _canonical_milestone(row[0])
        if not ms:
            continue

        g_date = row[GLOBAL_DATE_COL].strip() if GLOBAL_DATE_COL < len(row) else ""
        g_note = row[GLOBAL_NOTE_COL].strip() if GLOBAL_NOTE_COL < len(row) else ""
        c_date = row[CHINA_DATE_COL].strip() if CHINA_DATE_COL < len(row) else ""
        c_note = row[CHINA_NOTE_COL].strip() if CHINA_NOTE_COL < len(row) else ""

        if g_date or g_note:
            if ms not in upper.events:
                upper.event_order.append(ms)
            upper.events[ms] = MilestoneEvent(date=g_date, note=g_note)
        if c_date or c_note:
            if ms not in lower.events:
                lower.event_order.append(ms)
            lower.events[ms] = MilestoneEvent(date=c_date, note=c_note)

    return upper, lower


def extract_slide_title(slide) -> str:
    shape = find_slide_title_shape(slide)
    if shape and shape.has_text_frame:
        return normalize_text(shape.text_frame.text)
    return ""


def format_milestone_markdown(table: list[list[str]]) -> str:
    lines = ["# Milestone Table", ""]
    for row in table[:12]:
        lines.append("| " + " | ".join(c.replace("\n", " ") for c in row) + " |")
    return "\n".join(lines)
