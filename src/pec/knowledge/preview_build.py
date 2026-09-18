"""索引阶段预生成页面预览图（PPTX/PDF/DOCX），供 Web 稳定读取。"""

from __future__ import annotations

import json
import importlib
import logging
import os
import platform
import re
import shutil
import subprocess
import tempfile
import threading
from io import BytesIO
from pathlib import Path

from src.pec.knowledge.chunk_ingest import file_sha256
from src.pec.knowledge.paths import INDEX_DIR, resolve_source_path
from src.pec.knowledge.source_reader import normalize_excerpt_loc

logger = logging.getLogger(__name__)

SLIDE_W = 1280
SLIDE_H = 720
_ppt_lock = threading.Lock()

PREVIEWS_ROOT = INDEX_DIR / "previews"


def previews_enabled() -> bool:
    return os.getenv("PREVIEW_ON_INDEX", "1").strip().lower() not in {"0", "false", "no"}


def preview_dir(digest: str) -> Path:
    return PREVIEWS_ROOT / digest


def remove_file_previews(digest: str) -> None:
    root = preview_dir(digest)
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


def _parse_page_index(loc: str) -> int | None:
    loc = normalize_excerpt_loc(loc)
    m = re.match(r"Slide\s+(\d+)", loc, re.I)
    if m:
        return int(m.group(1))
    m = re.match(r"Page\s+(\d+)", loc, re.I)
    if m:
        return int(m.group(1))
    return None


def get_preview_image(source_ref: str, loc: str, *, digest: str | None = None) -> tuple[Path | None, str]:
    """读取索引时预生成的 PNG。返回 (路径, render_mode)。"""
    if digest is None:
        path = resolve_source_path(source_ref)
        digest = file_sha256(path)
    page_no = _parse_page_index(loc)
    if not page_no:
        return None, ""

    root = preview_dir(digest)
    meta_file = root / "meta.json"
    if not meta_file.is_file():
        return None, ""

    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    for item in meta.get("pages", []):
        if item.get("no") == page_no:
            rel = item.get("file")
            mode = item.get("mode", "")
            if rel:
                candidate = root / rel
                if candidate.is_file():
                    return candidate, mode
    return None, ""


def build_file_previews(path: Path, source_ref: str, digest: str) -> dict:
    """为单个文件预生成全部页面预览，写入 index/previews/{sha256}/。"""
    if not previews_enabled():
        return {"enabled": False, "pages": []}

    suffix = path.suffix.lower()
    root = preview_dir(digest)
    if root.is_dir():
        shutil.rmtree(root)
    root.mkdir(parents=True, exist_ok=True)

    pages: list[dict] = []
    if suffix == ".pptx":
        pages = _build_pptx_previews(path, root)
    elif suffix == ".pdf":
        pages = _build_pdf_previews(path, root)
    elif suffix == ".docx":
        pages = _build_docx_previews(path, root)
    else:
        pages = []

    meta = {
        "source_ref": source_ref,
        "digest": digest,
        "file_type": suffix.lstrip("."),
        "page_count": len(pages),
        "pages": pages,
    }
    (root / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    ppt_modes = [p["mode"] for p in pages if p.get("mode") == "powerpoint"]
    logger.info(
        "预览已生成 %s: %s 页 (powerpoint %s)",
        source_ref,
        len(pages),
        len(ppt_modes),
    )
    return meta


def _pptx_preview_engine() -> str:
    """powerpoint | libreoffice | pillow | auto（默认：Linux 用 libreoffice，Windows 用 powerpoint）。"""
    raw = os.getenv("PREVIEW_PPTX_ENGINE", "auto").strip().lower()
    if raw in {"powerpoint", "libreoffice", "pillow", "none", "off"}:
        return raw
    if os.getenv("PREVIEW_PREFER_LIBREOFFICE", "").strip().lower() in {"1", "true", "yes"}:
        return "libreoffice"
    if platform.system() != "Windows":
        return "libreoffice"
    return "powerpoint"


def _build_pptx_previews(path: Path, root: Path) -> list[dict]:
    try:
        Presentation = importlib.import_module("pptx").Presentation
    except ImportError as exc:
        raise RuntimeError("PPTX 预览需要安装 python-pptx。") from exc

    engine = _pptx_preview_engine()
    if engine in {"none", "off"}:
        print(f"    跳过 PPTX 预览（PREVIEW_PPTX_ENGINE={engine}）: {path.name}")
        return []

    prs = Presentation(str(path))
    total = len(prs.slides)
    print(f"    预览 PPTX: {path.name} ({total} slides, engine={engine})")

    if engine == "libreoffice":
        pages = _build_pptx_previews_via_pdf(path, root)
        if pages:
            return pages
        print("    LibreOffice 不可用，回退 pillow 简易预览")
        return _build_pptx_previews_pillow(path, root, total)

    if engine == "powerpoint":
        pages = _build_pptx_previews_powerpoint(path, root, total)
        if pages and any(p.get("mode") == "powerpoint" for p in pages):
            return pages
        print("    PowerPoint 导出失败，尝试 LibreOffice …")
        pages = _build_pptx_previews_via_pdf(path, root)
        if pages:
            return pages
        print("    回退 pillow 简易预览")
        return _build_pptx_previews_pillow(path, root, total)

    if engine == "pillow":
        return _build_pptx_previews_pillow(path, root, total)

    # auto
    pages = _build_pptx_previews_powerpoint(path, root, total)
    if pages and any(p.get("mode") == "powerpoint" for p in pages):
        return pages
    pages = _build_pptx_previews_via_pdf(path, root)
    if pages:
        return pages
    return _build_pptx_previews_pillow(path, root, total)


def _build_pptx_previews_via_pdf(path: Path, root: Path) -> list[dict]:
    """PPTX → PDF（LibreOffice 无头）→ pymupdf 按页出图，适合 Linux 服务器。"""
    pdf_path = _pptx_to_pdf(path)
    if not pdf_path or not pdf_path.is_file():
        return []
    try:
        pages = _build_pdf_previews(pdf_path, root)
        for p in pages:
            p["mode"] = "libreoffice"
            if "loc" in p and p["loc"].startswith("Page "):
                p["loc"] = p["loc"].replace("Page ", "Slide ", 1)
            fname = p.get("file", "")
            if fname.startswith("page_"):
                slide_name = "slide_" + fname[5:]
                old = root / fname
                new = root / slide_name
                if old.is_file():
                    old.rename(new)
                p["file"] = slide_name
        return pages
    finally:
        if pdf_path.parent.name.startswith("pec_pptx_"):
            shutil.rmtree(pdf_path.parent, ignore_errors=True)


def _build_pptx_previews_powerpoint(path: Path, root: Path, total: int) -> list[dict]:
    pages: list[dict] = []
    for slide_no in range(1, total + 1):
        filename = f"slide_{slide_no:03d}.png"
        out = root / filename
        mode = ""
        if _export_pptx_slide(path, slide_no, out):
            mode = "powerpoint"
            pages.append({"no": slide_no, "file": filename, "mode": mode, "loc": f"Slide {slide_no}"})
        if slide_no % 10 == 0 or slide_no == total:
            print(f"      slide {slide_no}/{total} ({mode or 'skip'})")
    return pages


def _build_pptx_previews_pillow(path: Path, root: Path, total: int) -> list[dict]:
    pages: list[dict] = []
    for slide_no in range(1, total + 1):
        filename = f"slide_{slide_no:03d}.png"
        out = root / filename
        mode = "pillow" if _export_pillow_slide(path, slide_no, out) else "failed"
        if mode == "pillow":
            pages.append({"no": slide_no, "file": filename, "mode": mode, "loc": f"Slide {slide_no}"})
        if slide_no % 10 == 0 or slide_no == total:
            print(f"      slide {slide_no}/{total} ({mode})")
    return pages


def _build_pdf_previews(path: Path, root: Path) -> list[dict]:
    try:
        import fitz  # pymupdf
    except ImportError:
        print(f"    跳过 PDF 预览（未安装 pymupdf）: {path.name}")
        return []

    pages: list[dict] = []
    doc = fitz.open(str(path))
    total = len(doc)
    print(f"    预览 PDF: {path.name} ({total} pages)")
    zoom = fitz.Matrix(2, 2)
    for i in range(total):
        page_no = i + 1
        filename = f"page_{page_no:03d}.png"
        out = root / filename
        doc[i].get_pixmap(matrix=zoom, alpha=False).save(str(out))
        pages.append({"no": page_no, "file": filename, "mode": "pymupdf", "loc": f"Page {page_no}"})
    doc.close()
    return pages


def _build_docx_previews(path: Path, root: Path) -> list[dict]:
    """DOCX → 临时 PDF → pymupdf 按页出图（Word 或 LibreOffice 转 PDF，无需 PowerPoint）。"""
    engine = _docx_preview_engine()
    if engine in {"none", "off"}:
        print(f"    跳过 DOCX 预览（PREVIEW_DOCX_ENGINE={engine}）: {path.name}")
        return []

    pdf_path, conv_mode = _docx_to_pdf_with_mode(path)
    if not pdf_path or not pdf_path.is_file():
        print(f"    DOCX 无预览（需 Word 或 LibreOffice 转 PDF）: {path.name}")
        return []
    try:
        pages = _build_pdf_previews(pdf_path, root)
        for p in pages:
            p["mode"] = conv_mode or "docx_via_pdf"
        return pages
    finally:
        if pdf_path.parent.name.startswith("pec_docx_"):
            shutil.rmtree(pdf_path.parent, ignore_errors=True)


def _docx_preview_engine() -> str:
    """word | libreoffice | auto（Linux 默认 libreoffice；Windows auto 先试 Word）。"""
    raw = os.getenv("PREVIEW_DOCX_ENGINE", "auto").strip().lower()
    if raw in {"word", "libreoffice", "none", "off"}:
        return raw
    if os.getenv("PREVIEW_PREFER_LIBREOFFICE", "").strip().lower() in {"1", "true", "yes"}:
        return "libreoffice"
    if platform.system() != "Windows":
        return "libreoffice"
    return "word"


def _docx_to_pdf_with_mode(path: Path) -> tuple[Path | None, str]:
    engine = _docx_preview_engine()
    tmp = Path(tempfile.mkdtemp(prefix="pec_docx_"))
    pdf = tmp / f"{path.stem}.pdf"

    if engine == "libreoffice":
        if _office_to_pdf_libreoffice(path, pdf):
            return pdf, "libreoffice"
    elif engine == "word":
        if _docx_to_pdf_word(path, pdf):
            return pdf, "word"
    else:
        if _docx_to_pdf_word(path, pdf):
            return pdf, "word"
        if _office_to_pdf_libreoffice(path, pdf):
            return pdf, "libreoffice"

    shutil.rmtree(tmp, ignore_errors=True)
    return None, ""


def _pptx_to_pdf(path: Path) -> Path | None:
    tmp = Path(tempfile.mkdtemp(prefix="pec_pptx_"))
    pdf = tmp / f"{path.stem}.pdf"
    if _office_to_pdf_libreoffice(path, pdf):
        return pdf
    shutil.rmtree(tmp, ignore_errors=True)
    return None


def _docx_to_pdf(path: Path) -> Path | None:
    pdf_path, _ = _docx_to_pdf_with_mode(path)
    return pdf_path


def _docx_to_pdf_word(path: Path, pdf: Path) -> bool:
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError:
        return False
    with _ppt_lock:
        pythoncom.CoInitialize()
        word = None
        doc = None
        try:
            word = win32com.client.Dispatch("Word.Application")
            word.Visible = 0
            doc = word.Documents.Open(str(path.resolve()), ReadOnly=True)
            doc.SaveAs(str(pdf.resolve()), FileFormat=17)
            return pdf.is_file()
        except Exception as exc:
            logger.warning("Word 转 PDF 失败: %s", exc)
            return False
        finally:
            if doc is not None:
                try:
                    doc.Close(False)
                except Exception:
                    pass
            if word is not None:
                try:
                    word.Quit()
                except Exception:
                    pass
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass


def _office_to_pdf_libreoffice(path: Path, pdf: Path) -> bool:
    """LibreOffice 无头转 PDF（pptx/docx/ppt/doc），Linux 服务器常用。"""
    for cmd in ("soffice", "libreoffice"):
        try:
            subprocess.run(
                [
                    cmd,
                    "--headless",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(pdf.parent),
                    str(path.resolve()),
                ],
                check=True,
                timeout=300,
                capture_output=True,
            )
            candidate = pdf.parent / f"{path.stem}.pdf"
            if candidate.is_file() and candidate != pdf:
                candidate.rename(pdf)
            return pdf.is_file()
        except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            continue
    return False


def _export_pptx_slide(path: Path, slide_no: int, out: Path) -> bool:
    try:
        import pythoncom  # type: ignore
        import win32com.client  # type: ignore
    except ImportError:
        return False
    tmp = Path(tempfile.gettempdir()) / f"pec_{out.name}"
    with _ppt_lock:
        pythoncom.CoInitialize()
        app = None
        presentation = None
        try:
            app = win32com.client.Dispatch("PowerPoint.Application")
            try:
                app.Visible = 0
            except Exception:
                pass
            presentation = app.Presentations.Open(
                str(path.resolve()), WithWindow=False, ReadOnly=True
            )
            if slide_no > presentation.Slides.Count:
                return False
            presentation.Slides(slide_no).Export(str(tmp), "PNG")
            if tmp.is_file():
                out.write_bytes(tmp.read_bytes())
                return True
        except Exception:
            return False
        finally:
            if presentation is not None:
                try:
                    presentation.Close()
                except Exception:
                    pass
            if app is not None:
                try:
                    app.Quit()
                except Exception:
                    pass
            if tmp.is_file():
                tmp.unlink(missing_ok=True)
            try:
                pythoncom.CoUninitialize()
            except Exception:
                pass
    return False


def _export_pillow_slide(path: Path, slide_no: int, out: Path) -> bool:
    try:
        from PIL import Image, ImageDraw
        Presentation = importlib.import_module("pptx").Presentation
        MSO_SHAPE_TYPE = importlib.import_module("pptx.enum.shapes").MSO_SHAPE_TYPE
    except ImportError:
        return False
    try:
        prs = Presentation(str(path))
        if slide_no < 1 or slide_no > len(prs.slides):
            return False
        slide = prs.slides[slide_no - 1]
        sw = int(prs.slide_width) or 9144000
        sh = int(prs.slide_height) or 6858000
        canvas = Image.new("RGB", (SLIDE_W, SLIDE_H), (255, 255, 255))
        draw = ImageDraw.Draw(canvas)
        for shape in slide.shapes:
            if not hasattr(shape, "left"):
                continue
            left = int(int(shape.left) * SLIDE_W / sw)
            top = int(int(shape.top) * SLIDE_H / sh)
            w = max(1, int(int(shape.width) * SLIDE_W / sw))
            h = max(1, int(int(shape.height) * SLIDE_H / sh))
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                try:
                    img = Image.open(BytesIO(shape.image.blob)).convert("RGB")
                    img.thumbnail((w, h), Image.Resampling.LANCZOS)
                    canvas.paste(img, (left, top))
                except Exception:
                    pass
            elif hasattr(shape, "text") and shape.text.strip():
                t = shape.text.strip().replace("\n", " ")[:200]
                draw.rectangle([left, top, left + w, top + h], fill=(248, 250, 252))
                draw.text((left + 4, top + 4), t, fill=(15, 23, 42))
        canvas.save(out, format="PNG")
        return True
    except Exception:
        return False
