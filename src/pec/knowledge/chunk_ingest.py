"""从 sources 下的 Office/PDF 文件抽取 chunk。"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from src.llm.apollo import describe_image, get_access_token
from src.pec.knowledge.paths import (
    INDEX_DIR,
    SUPPORTED_SUFFIXES,
    normalize_source_scope,
    resolve_source_path,
    source_relative,
)


@dataclass
class ChunkDraft:
    chunk_id: str
    text: str
    source_ref: str
    folder: str
    loc: str
    method: str  # text | vision | vision_page | table


def _min_slide_text() -> int:
    return int(os.getenv("MIN_SLIDE_TEXT_CHARS", "80"))


def _vision_mode() -> str:
    mode = (os.getenv("PEC_PPT_VISION_MODE", "smart") or "smart").strip().lower()
    return mode if mode in {"smart", "all", "none"} else "smart"


def _vision_max_slides() -> int:
    return max(0, int(os.getenv("PEC_PPT_VISION_MAX_SLIDES", "0")))


def _vision_cache_dir() -> Path:
    return INDEX_DIR / "vision_cache"


def _vision_model_name() -> str:
    return (
        os.getenv("APOLLO_PEC_VISION_MODEL")
        or os.getenv("APOLLO_VISION_MODEL")
        or os.getenv("APOLLO_MODEL")
        or ""
    ).strip()


def _slide_number(loc: str) -> int:
    match = re.match(r"Slide\s+(\d+)", loc, re.I)
    return int(match.group(1)) if match else 0


def _vision_cache_file(source_digest: str, slide_no: int) -> Path:
    version = os.getenv("PEC_PPT_VISION_PROMPT_VERSION", "1").strip()
    key = hashlib.sha256(
        f"{source_digest}|{slide_no}|{_vision_model_name()}|{version}".encode()
    ).hexdigest()
    return _vision_cache_dir() / f"{key}.json"


def _load_cached_vision(source_digest: str, slide_no: int) -> str | None:
    path = _vision_cache_file(source_digest, slide_no)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        text = str(payload.get("text") or "").strip()
        return text or None
    except (OSError, ValueError, TypeError):
        return None


def _save_cached_vision(source_digest: str, slide_no: int, text: str) -> None:
    path = _vision_cache_file(source_digest, slide_no)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {"source_digest": source_digest, "slide": slide_no, "text": text},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _missing_dependency(package: str, file_type: str, exc: ImportError) -> RuntimeError:
    return RuntimeError(
        f"索引 {file_type} 文件需要安装 {package}，请更新项目依赖后重试。"
    )


def _make_chunk_id(source_ref: str, loc: str, text: str) -> str:
    digest = hashlib.sha1(f"{source_ref}|{loc}|{text[:120]}".encode()).hexdigest()[:12]
    return f"chunk-{digest}"


def _folder_name(source_ref: str) -> str:
    parts = source_ref.replace("\\", "/").split("/")
    return parts[0] if len(parts) > 1 else ""


def _serialize_table(headers: list[str], row: list[str]) -> str:
    pairs = []
    for i, value in enumerate(row):
        if not (value or "").strip():
            continue
        key = headers[i] if i < len(headers) and headers[i].strip() else f"col_{i+1}"
        pairs.append(f"{key}: {value.strip()}")
    return " | ".join(pairs)


def _slide_needs_vision(slide, body: str, mode: str) -> bool:
    if mode == "all":
        return True
    if mode == "none":
        return False
    try:
        from pptx.enum.shapes import MSO_SHAPE_TYPE
    except ImportError as exc:
        raise _missing_dependency("python-pptx", "PPTX", exc) from exc
    shape_types = {shape.shape_type for shape in slide.shapes}
    complex_types = {
        MSO_SHAPE_TYPE.PICTURE,
        MSO_SHAPE_TYPE.GROUP,
        MSO_SHAPE_TYPE.CHART,
        MSO_SHAPE_TYPE.TABLE,
    }
    has_complex_shape = bool(shape_types & complex_types)
    return not body or len(body) < _min_slide_text() or has_complex_shape


def _rendered_slide_image(path: Path, source_ref: str, slide_no: int) -> Path | None:
    # preview_build imports this module for file_sha256; import lazily to avoid a cycle.
    from src.pec.knowledge.preview_build import get_preview_image

    image, _ = get_preview_image(source_ref, f"Slide {slide_no}")
    return image


def _vision_slide_page(
    path: Path,
    source_ref: str,
    slide_no: int,
    source_digest: str,
    access_token: str | None,
) -> str | None:
    cached = _load_cached_vision(source_digest, slide_no)
    if cached:
        return cached
    image_path = _rendered_slide_image(path, source_ref, slide_no)
    if image_path is None:
        return None
    token = access_token or get_access_token()
    text = describe_image(
        image_path.read_bytes(),
        mime_type="image/png",
        access_token=token,
    ).strip()
    if text:
        _save_cached_vision(source_digest, slide_no, text)
        return text
    return None

# PPT 是怎么切 Chunk
# 有两种 Chunk：
#
# 一页 PPT
#   │
#   ├── 普通文本 → Slide Chunk
#   │
#   └── 表格 → 每一行单独一个 Table Chunk
def _extract_pptx_chunks(
    path: Path,
    source_ref: str,
    access_token: str | None = None,
) -> list[ChunkDraft]:
    try:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE
    except ImportError as exc:
        raise _missing_dependency("python-pptx", "PPTX", exc) from exc
    prs = Presentation(str(path))
    min_text = _min_slide_text()
    vision_mode = _vision_mode()
    source_digest = file_sha256(path)
    vision_count = 0
    chunks: list[ChunkDraft] = []

    for slide_no, slide in enumerate(prs.slides, start=1):
        loc = f"Slide {slide_no}"
        texts: list[str] = []
        for shape in slide.shapes:
            if hasattr(shape, "text") and shape.text.strip():
                texts.append(shape.text.strip())
            if shape.shape_type == MSO_SHAPE_TYPE.TABLE:
                table = shape.table
                headers = [cell.text.strip() for cell in table.rows[0].cells]
                for row_idx in range(1, len(table.rows)):
                    row = table.rows[row_idx]
                    cells = [cell.text.strip() for cell in row.cells]
                    row_text = _serialize_table(headers, cells)
                    if row_text:
                        row_loc = f"{loc} Table row {row_idx + 1}"
                        chunks.append(
                            ChunkDraft(
                                chunk_id=_make_chunk_id(source_ref, row_loc, row_text),
                                text=row_text,
                                source_ref=source_ref,
                                folder=_folder_name(source_ref),
                                loc=row_loc,
                                method="table",
                            )
                        )

        body = "\n".join(texts).strip()
        method = "text"
        if _slide_needs_vision(slide, body, vision_mode):
            max_slides = _vision_max_slides()
            if max_slides == 0 or vision_count < max_slides:
                try:
                    vision_text = _vision_slide_page(
                        resolved_path := path,
                        source_ref,
                        slide_no,
                        source_digest,
                        access_token,
                    )
                except Exception:
                    vision_text = None
                if vision_text:
                    body = vision_text if not body else f"{body}\n\n[Vision]\n{vision_text}"
                    method = "vision_page"
                    vision_count += 1

        if not body.strip():
            continue
        if len(body) >= min_text or method in {"vision", "text+vision"}:
            chunks.append(
                ChunkDraft(
                    chunk_id=_make_chunk_id(source_ref, loc, body),
                    text=body,
                    source_ref=source_ref,
                    folder=_folder_name(source_ref),
                    loc=loc,
                    method=method,
                )
            )
    return chunks


def _extract_docx_chunks(path: Path, source_ref: str) -> list[ChunkDraft]:
    try:
        from docx import Document
    except ImportError as exc:
        raise _missing_dependency("python-docx", "DOCX", exc) from exc
    doc = Document(str(path))
    chunks: list[ChunkDraft] = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if len(text) < 40:
            continue
        loc = "Paragraph"
        chunks.append(
            ChunkDraft(
                chunk_id=_make_chunk_id(source_ref, loc + text[:40], text),
                text=text,
                source_ref=source_ref,
                folder=_folder_name(source_ref),
                loc=loc,
                method="text",
            )
        )

    for table_idx, table in enumerate(doc.tables, start=1):
        if not table.rows:
            continue
        headers = [cell.text.strip() for cell in table.rows[0].cells]
        for row_idx in range(1, len(table.rows)):
            row = table.rows[row_idx]
            cells = [cell.text.strip() for cell in row.cells]
            row_text = _serialize_table(headers, cells)
            if not row_text:
                continue
            loc = f"Table {table_idx} row {row_idx + 1}"
            chunks.append(
                ChunkDraft(
                    chunk_id=_make_chunk_id(source_ref, loc, row_text),
                    text=row_text,
                    source_ref=source_ref,
                    folder=_folder_name(source_ref),
                    loc=loc,
                    method="table",
                )
            )
    return chunks


def _extract_pdf_chunks(path: Path, source_ref: str) -> list[ChunkDraft]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:
        raise _missing_dependency("pypdf", "PDF", exc) from exc
    reader = PdfReader(str(path))
    min_text = _min_slide_text()
    chunks: list[ChunkDraft] = []

    for page_no, page in enumerate(reader.pages, start=1):
        loc = f"Page {page_no}"
        text = (page.extract_text() or "").strip()
        if len(text) >= min_text:
            chunks.append(
                ChunkDraft(
                    chunk_id=_make_chunk_id(source_ref, loc, text),
                    text=text,
                    source_ref=source_ref,
                    folder=_folder_name(source_ref),
                    loc=loc,
                    method="text",
                )
            )
    return chunks


def _extract_xlsx_chunks(path: Path, source_ref: str) -> list[ChunkDraft]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        return [
            ChunkDraft(
                chunk_id=_make_chunk_id(source_ref, "xlsx", "missing openpyxl"),
                text="[xlsx 未安装 openpyxl，跳过]",
                source_ref=source_ref,
                folder=_folder_name(source_ref),
                loc="Sheet",
                method="text",
            )
        ]

    wb = load_workbook(path, read_only=True, data_only=True)
    chunks: list[ChunkDraft] = []
    try:
        for sheet in wb.worksheets:
            rows: list[str] = []
            for row in sheet.iter_rows(values_only=True):
                cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
                if cells:
                    rows.append(" | ".join(cells))
            if not rows:
                continue
            header = rows[0]
            for i, row in enumerate(rows[1:], start=2):
                loc = f"Sheet {sheet.title} row {i}"
                text = f"Headers: {header}\nRecord: {row}"
                chunks.append(
                    ChunkDraft(
                        chunk_id=_make_chunk_id(source_ref, loc, text),
                        text=text,
                        source_ref=source_ref,
                        folder=_folder_name(source_ref),
                        loc=loc,
                        method="table",
                    )
                )
    finally:
        wb.close()
    return chunks


def ingest_file(path: Path, *, access_token: str | None = None) -> list[ChunkDraft]:
    source_ref = source_relative(path)
    resolved = resolve_source_path(source_ref)
    suffix = resolved.suffix.lower()

    if suffix == ".pptx":
        return _extract_pptx_chunks(resolved, source_ref, access_token)
    if suffix == ".docx":
        return _extract_docx_chunks(resolved, source_ref)
    if suffix == ".pdf":
        return _extract_pdf_chunks(resolved, source_ref)
    if suffix == ".xlsx":
        return _extract_xlsx_chunks(resolved, source_ref)
    return []


def scan_source_files(scope: str = "") -> list[Path]:
    _, root = normalize_source_scope(scope)
    if not root.exists():
        return []
    if root.is_file():
        return [root] if root.suffix.lower() in SUPPORTED_SUFFIXES else []
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() in SUPPORTED_SUFFIXES:
            files.append(path)
    return files


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def chunk_draft_to_dict(chunk: ChunkDraft) -> dict:
    return asdict(chunk)
