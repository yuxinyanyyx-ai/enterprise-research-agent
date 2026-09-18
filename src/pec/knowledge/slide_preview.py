"""Web 读取索引阶段预生成的页面预览。"""

from __future__ import annotations

import json
import re
from pathlib import Path

from src.pec.knowledge.chunk_ingest import file_sha256
from src.pec.knowledge.paths import resolve_source_path
from src.pec.knowledge.preview_build import get_preview_image, preview_dir
from src.pec.knowledge.source_reader import normalize_excerpt_loc


def parse_slide_number(loc: str) -> int | None:
    m = re.search(r"Slide\s+(\d+)", normalize_excerpt_loc(loc) or "", re.I)
    return int(m.group(1)) if m else None


def parse_page_number(loc: str) -> int | None:
    m = re.search(r"Page\s+(\d+)", normalize_excerpt_loc(loc) or "", re.I)
    return int(m.group(1)) if m else None


def _digest_for_source(source_ref: str) -> str:
    return file_sha256(resolve_source_path(source_ref))


def _render_mode_for_page(digest: str, page_no: int) -> str:
    meta_file = preview_dir(digest) / "meta.json"
    if not meta_file.is_file():
        return ""
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    for item in meta.get("pages", []):
        if item.get("no") == page_no:
            return item.get("mode", "")
    return ""


def export_page_png(source_ref: str, page_no: int) -> tuple[Path | None, str]:
    """读取索引预生成的页面 PNG（slide/page 均用页码）。"""
    digest = _digest_for_source(source_ref)
    loc = f"Slide {page_no}"  # get_preview_image 同时认 Slide/Page
    png, mode = get_preview_image(source_ref, loc, digest=digest)
    if not png:
        png, mode = get_preview_image(source_ref, f"Page {page_no}", digest=digest)
    if png:
        return png, mode or "indexed"
    return None, "missing"


def export_pptx_slide_png(source_ref: str, slide_no: int) -> tuple[Path | None, str]:
    return export_page_png(source_ref, slide_no)


def preview_available(source_ref: str, loc: str) -> dict:
    path = resolve_source_path(source_ref)
    suffix = path.suffix.lower()
    slide = parse_slide_number(loc)
    page = parse_page_number(loc)
    digest = _digest_for_source(source_ref)
    has_meta = (preview_dir(digest) / "meta.json").is_file()

    if suffix == ".pptx" and slide:
        mode = _render_mode_for_page(digest, slide)
        return {
            "type": "pptx_slide",
            "slide": slide,
            "image_url": f"/api/source/slide-image?source_ref={source_ref}&slide={slide}",
            "render_mode": mode or ("indexed" if has_meta else "pending"),
            "file_url": f"/api/source/file?source_ref={source_ref}",
        }

    if suffix == ".pdf":
        page_no = page or 1
        mode = _render_mode_for_page(digest, page_no)
        return {
            "type": "pdf_page",
            "page": page_no,
            "image_url": f"/api/source/slide-image?source_ref={source_ref}&slide={page_no}",
            "render_mode": mode or ("indexed" if has_meta else "pending"),
            "file_url": f"/api/source/file?source_ref={source_ref}",
        }

    if suffix == ".docx":
        page_no = page or 1
        mode = _render_mode_for_page(digest, page_no)
        return {
            "type": "docx_page",
            "page": page_no,
            "image_url": f"/api/source/slide-image?source_ref={source_ref}&slide={page_no}",
            "render_mode": mode or ("indexed" if has_meta else "pending"),
            "file_url": f"/api/source/file?source_ref={source_ref}",
        }

    return {
        "type": "download_only",
        "file_url": f"/api/source/file?source_ref={source_ref}",
    }
