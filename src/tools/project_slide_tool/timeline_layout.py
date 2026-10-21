from __future__ import annotations

import re
from datetime import datetime

from .constants import GRID_COLS, GRID_HEIGHT, GRID_LEFT, GRID_TOP, GRID_WIDTH, QUARTER_LABELS
from .models import TrackData
from .pptx_utils import normalize_text, set_cell


def parse_date(text: str) -> datetime | None:
    text = text.strip()
    if not text or text in ("—", "-", "TBD"):
        return None
    for fmt in ("%d %b %Y", "%d %B %Y", "%b %Y", "%B %Y"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    m = re.match(r"(\d{1,2})\s+([A-Za-z]{3,9})\s+(\d{4})", text)
    if m:
        for fmt in ("%d %b %Y", "%d %B %Y"):
            try:
                return datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", fmt)
            except ValueError:
                continue
    return None


def read_grid_geometry(slide) -> dict:
    for shape in slide.shapes:
        if shape.has_table and len(shape.table.columns) >= 20:
            return {
                "left": shape.left,
                "top": shape.top,
                "width": shape.width,
                "height": shape.height,
                "cols": len(shape.table.columns),
                "shape": shape,
            }
    return {"left": GRID_LEFT, "top": GRID_TOP, "width": GRID_WIDTH, "height": GRID_HEIGHT, "cols": GRID_COLS, "shape": None}


def compute_year_range(upper: TrackData, lower: TrackData) -> tuple[int, int]:
    """First/last calendar year covering all milestone dates (no extra leading year)."""
    dates: list[datetime] = []
    for td in (upper, lower):
        for ev in td.events.values():
            dt = parse_date(ev.date)
            if dt:
                dates.append(dt)
    if not dates:
        return 2025, 2029
    lo = min(d.year for d in dates)
    hi = max(d.year for d in dates)
    start = lo
    # 20-column grid = 5 year labels; ensure enough span for the axis.
    end = max(hi + 1, start + 4)
    return start, end


def update_quarter_grid(slide, start_year: int, end_year: int, collector=None) -> None:
    """Update year row on the 20-column quarter grid (5 years x 4 quarters)."""
    quarters = ("Q1", "Q2", "Q3", "Q4")
    for shape in slide.shapes:
        if not shape.has_table or len(shape.table.columns) < 20:
            continue
        t = shape.table
        cols = len(t.columns)
        span = 4
        n_years = cols // span
        for i in range(n_years):
            year = start_year + i
            base = i * span
            year_text = str(year) if year <= end_year else ""
            if collector:
                collector.set_table_cell(shape, 0, base, year_text)
            else:
                set_cell(t.cell(0, base), year_text, font_size=9)
            for j in range(1, span):
                if base + j < cols:
                    if collector:
                        collector.set_table_cell(shape, 0, base + j, "")
                    else:
                        set_cell(t.cell(0, base + j), "", font_size=9)
            for j in range(span):
                if base + j < cols:
                    if collector:
                        collector.set_table_cell(shape, 1, base + j, quarters[j])
                    else:
                        set_cell(t.cell(1, base + j), quarters[j], font_size=9)
        return


def _quarters_for_range(start_year: int, end_year: int, cols: int) -> list[tuple[int, str, str]]:
    """Build quarter column labels from year range (independent of in-memory table)."""
    quarter_names = ("Q1", "Q2", "Q3", "Q4")
    labels: list[tuple[int, str, str]] = []
    span = 4
    n_years = cols // span
    for i in range(n_years):
        year = start_year + i
        year_text = str(year) if year <= end_year else ""
        base = i * span
        for j in range(span):
            col = base + j
            if col >= cols:
                break
            labels.append((col, year_text if j == 0 else "", quarter_names[j]))
    return labels


def _build_quarter_labels(slide) -> list[tuple[int, str, str]]:
    labels: list[tuple[int, str, str]] = []
    for shape in slide.shapes:
        if not shape.has_table or len(shape.table.columns) < 20:
            continue
        t = shape.table
        for c in range(len(t.columns)):
            year = normalize_text(t.cell(0, c).text)
            quarter = normalize_text(t.cell(1, c).text)
            labels.append((c, year, quarter))
        return labels
    for i, q in enumerate(QUARTER_LABELS):
        labels.append((i, "", q))
    return labels


def date_to_col(dt, quarters: list[tuple[int, str, str]]) -> float:
    if not quarters:
        return 0.0
    target = dt.year + (dt.month - 0.5) / 12.0
    years = [int(q[1][:4]) for q in quarters if q[1].strip().isdigit()]
    if not years:
        return 0.0
    first_year = years[0]
    last_year = years[-1] + 1
    col_span = len(quarters)
    frac = (target - first_year) / max(1, last_year - first_year)
    return max(0.0, min(col_span - 0.01, frac * col_span))


def col_center_x(grid: dict, col: float) -> int:
    col_w = grid["width"] / grid["cols"]
    return int(grid["left"] + col * col_w + col_w / 2)


def col_to_x(grid: dict, col: float, width: int = 400_000) -> int:
    col_w = grid["width"] / grid["cols"]
    center = grid["left"] + col * col_w
    return int(center - width / 2) if width > 0 else int(center)


def display_label(name: str) -> str:
    return name
