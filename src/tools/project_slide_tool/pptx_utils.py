from __future__ import annotations

import re
import uuid
from copy import deepcopy

from lxml import etree
from pptx.util import Pt

# Office Timeline default ~10.7pt on Example.pptx
DEFAULT_OTL_FONT_SIZE = Pt(10)


def normalize_text(text: str) -> str:
    return text.replace("\x0b", "\n").replace("\r\n", "\n").strip()


def read_table(shape) -> list[list[str]]:
    table = shape.table
    return [[normalize_text(table.cell(r, c).text) for c in range(len(table.columns))] for r in range(len(table.rows))]


def detect_track_threshold(slide) -> int:
    candidates: list[int] = []
    for shape in slide.shapes:
        name = shape.name or ""
        if "Connector" in name and getattr(shape, "height", 1) == 0:
            if 1_500_000 < shape.top < 4_500_000:
                candidates.append(shape.top)
    if candidates:
        return sorted(candidates)[0] + 200_000
    return 2_700_000


def track_of(shape, threshold: int) -> str:
    return "upper" if shape.top < threshold else "lower"


def looks_like_date(text: str) -> bool:
    t = text.split("\n")[0].strip()
    if t in ("", "—", "-", "TBD"):
        return True
    return bool(re.match(r"(\d{1,2}\s+)?[A-Za-z]{3,9}\s+\d{4}", t)) or bool(re.match(r"^Q[1-4]\s+\d{4}", t, re.I))


def is_study_label(text: str) -> bool:
    t = text.lower()
    return t.startswith("ph") and "-" in t


def _capture_run_font(run) -> None:
    pass


def _apply_run_font(run, *, size=None, name=None) -> None:
    if size is not None:
        run.font.size = size
    if name:
        run.font.name = name


def _set_paragraph_text(p, line: str, *, font_size=None) -> None:
    if p.runs:
        p.runs[0].text = line
        if font_size is not None:
            _apply_run_font(p.runs[0], size=font_size)
        for run in p.runs[1:]:
            run.text = ""
        return
    run = p.add_run()
    run.text = line
    if font_size is not None:
        _apply_run_font(run, size=font_size)


def _shape_font_size(shape) -> int | None:
    if not shape or not shape.has_text_frame:
        return None
    for p in shape.text_frame.paragraphs:
        for r in p.runs:
            if r.font.size:
                return int(r.font.size)
    return None


def set_shape_text(shape, text: str, *, force_small: bool = False, font_size=None) -> None:
    """Update text in-place; preserve OTL run formatting when possible."""
    if not shape or not shape.has_text_frame:
        return
    text = normalize_text(text)
    if font_size is not None:
        ref_size = font_size
    elif force_small:
        ref_size = DEFAULT_OTL_FONT_SIZE
    else:
        ref_size = _shape_font_size(shape)
        if ref_size and ref_size > 127_000:
            ref_size = DEFAULT_OTL_FONT_SIZE
    tf = shape.text_frame
    lines = text.split("\n") if text else [""]
    paragraphs = list(tf.paragraphs)
    for i, line in enumerate(lines):
        p = paragraphs[i] if i < len(paragraphs) else tf.add_paragraph()
        _set_paragraph_text(p, line, font_size=ref_size)
    for p in paragraphs[len(lines) :]:
        _set_paragraph_text(p, "")


def set_cell(cell, text: str, *, font_size: int = 10, bullet: bool = False) -> None:
    text = normalize_text(text.replace("<br>", "\n"))
    tf = cell.text_frame
    tf.word_wrap = True
    if not text:
        for p in tf.paragraphs:
            _set_paragraph_text(p, "")
        return
    lines = [line.lstrip("• ").strip() for line in text.split("\n") if line.strip()]
    paragraphs = list(tf.paragraphs)
    for i, line in enumerate(lines):
        p = paragraphs[i] if i < len(paragraphs) else tf.add_paragraph()
        _set_paragraph_text(p, f"• {line}" if bullet else line)
        for run in p.runs:
            run.font.name = "Calibri"
            run.font.size = Pt(font_size)


def bullets(items: list[str]) -> str:
    return "\n".join(f"• {i.strip().lstrip('•').strip()}" for i in items if i.strip())


def _max_cnvpr_id(slide) -> int:
    max_id = 0
    for el in slide.shapes._spTree.iter():
        if not el.tag.endswith("}cNvPr"):
            continue
        try:
            max_id = max(max_id, int(el.get("id", 0)))
        except ValueError:
            pass
    return max_id


def hide_shape(shape) -> None:
    """Hide a shape without moving it off-slide (negative coords break OTL)."""
    if not shape:
        return
    try:
        for el in shape.element.iter():
            if el.tag.endswith("}cNvPr"):
                el.set("hidden", "1")
                return
    except Exception:
        pass


def _extract_otl_guid(name: str) -> str | None:
    m = re.search(r"OTLSHAPE_[MT]_([0-9a-f]{32})", name or "", flags=re.I)
    return m.group(1) if m else None


def clone_shape_with_new_id(slide, source):
    """Clone a shape with unique cNvPr id and (for OTL markers) a fresh OTL guid."""
    ids_before = {id(s.element) for s in slide.shapes}
    new_el = deepcopy(source.element)
    old_guid = _extract_otl_guid(source.name or "")
    new_guid = uuid.uuid4().hex if old_guid else None
    if old_guid:
        xml = etree.tostring(new_el, encoding="unicode")
        xml = xml.replace(old_guid, new_guid)
        new_el = etree.fromstring(xml.encode("utf-8"))
    new_id = _max_cnvpr_id(slide) + 1
    for el in new_el.iter():
        if not el.tag.endswith("}cNvPr"):
            continue
        el.set("id", str(new_id))
        name = el.get("name")
        if name and new_guid and old_guid:
            el.set("name", name.replace(old_guid, new_guid))
        elif name:
            el.set("name", f"{name}2")
        break
    slide.shapes._spTree.insert_element_before(new_el, "p:extLst")
    for shape in slide.shapes:
        if id(shape.element) not in ids_before:
            return shape
    return None


def shape_cnv_id(shape) -> str | None:
    if not shape:
        return None
    for el in shape.element.iter():
        if el.tag.endswith("}cNvPr"):
            return el.get("id")
    return None


def find_slide_title_shape(slide):
    """Top wide textbox used as slide title (same role as Example slide 1 / 3)."""
    best = None
    best_score = 0
    for shape in slide.shapes:
        if not shape.has_text_frame:
            continue
        if shape.top > 900_000:
            continue
        if shape.width < 3_000_000:
            continue
        score = shape.width - shape.top
        if score > best_score:
            best_score = score
            best = shape
    return best
