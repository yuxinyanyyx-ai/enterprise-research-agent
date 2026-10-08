import base64
from pathlib import Path

import pytest
from pptx import Presentation
from pptx.util import Inches

from src.pec.knowledge import chunk_ingest, paths


def _make_pptx(path: Path) -> None:
    presentation = Presentation()
    first = presentation.slides.add_slide(presentation.slide_layouts[0])
    first.shapes.title.text = "A complex portfolio page"
    first.shapes.add_textbox(Inches(1), Inches(1), Inches(4), Inches(1)).text = "status and timeline"
    image = path.with_suffix(".png")
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+5Q4R"
            "AAAAAElFTkSuQmCC"
        )
    )
    first.shapes.add_picture(str(image), Inches(6), Inches(1), width=Inches(1))
    presentation.slides.add_slide(presentation.slide_layouts[6])
    presentation.save(path)


def test_page_vision_is_called_once_per_slide_and_cached(tmp_path, monkeypatch) -> None:
    sources = tmp_path / "sources"
    index = tmp_path / "index"
    source = sources / "PEC1" / "meeting.pptx"
    source.parent.mkdir(parents=True)
    index.mkdir()
    _make_pptx(source)
    monkeypatch.setattr(paths, "SOURCES_DIR", sources)
    monkeypatch.setattr(paths, "INDEX_DIR", index)
    monkeypatch.setattr(chunk_ingest, "INDEX_DIR", index)
    monkeypatch.setenv("PEC_PPT_VISION_MODE", "all")

    image = tmp_path / "slide.png"
    image.write_bytes(b"png")
    monkeypatch.setattr(
        chunk_ingest,
        "_rendered_slide_image",
        lambda source_ref, slide_no: image,
    )
    calls = []
    monkeypatch.setattr(
        chunk_ingest,
        "describe_image",
        lambda blob, **kwargs: calls.append((blob, kwargs)) or f"visual slide {len(calls)}",
    )

    first = chunk_ingest.ingest_file(source, access_token="token")
    second = chunk_ingest.ingest_file(source, access_token="token")

    assert [chunk.method for chunk in first if chunk.method == "vision_page"] == [
        "vision_page",
        "vision_page",
    ]
    assert len(calls) == 2
    cached_text = [chunk.text for chunk in second if chunk.method == "vision_page"]
    assert len(cached_text) == 2
    assert all(f"visual slide {index}" in text for index, text in enumerate(cached_text, 1))


def test_smart_and_none_modes_control_page_vision(tmp_path, monkeypatch) -> None:
    sources = tmp_path / "sources"
    index = tmp_path / "index"
    source = sources / "PEC1" / "meeting.pptx"
    source.parent.mkdir(parents=True)
    index.mkdir()
    _make_pptx(source)
    monkeypatch.setattr(paths, "SOURCES_DIR", sources)
    monkeypatch.setattr(paths, "INDEX_DIR", index)
    monkeypatch.setattr(chunk_ingest, "INDEX_DIR", index)
    monkeypatch.setattr(chunk_ingest, "_rendered_slide_image", lambda *args: image)

    image = tmp_path / "slide.png"
    image.write_bytes(b"png")
    calls = []
    monkeypatch.setattr(
        chunk_ingest,
        "describe_image",
        lambda blob, **kwargs: calls.append(blob) or "visual",
    )

    monkeypatch.setenv("PEC_PPT_VISION_MODE", "none")
    assert not [chunk for chunk in chunk_ingest.ingest_file(source) if chunk.method == "vision_page"]

    monkeypatch.setenv("PEC_PPT_VISION_MODE", "smart")
    assert [chunk for chunk in chunk_ingest.ingest_file(source) if chunk.method == "vision_page"]
    assert calls


def test_smart_skips_text_only_and_blank_slides(tmp_path, monkeypatch) -> None:
    sources = tmp_path / "sources"
    source = sources / "PEC1" / "text-only.pptx"
    source.parent.mkdir(parents=True)
    monkeypatch.setattr(paths, "SOURCES_DIR", sources)
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(5), Inches(1)).text = "A short title"
    presentation.slides.add_slide(presentation.slide_layouts[6])
    presentation.save(source)

    monkeypatch.setenv("PEC_PPT_VISION_MODE", "smart")
    monkeypatch.setattr(chunk_ingest, "_rendered_slide_image", lambda *args: pytest.fail("not called"))

    chunks = chunk_ingest.ingest_file(source)

    assert not [chunk for chunk in chunks if chunk.method == "vision_page"]


def test_vision_failure_keeps_native_chunks(tmp_path, monkeypatch) -> None:
    sources = tmp_path / "sources"
    source = sources / "PEC1" / "visual.pptx"
    source.parent.mkdir(parents=True)
    presentation = Presentation()
    slide = presentation.slides.add_slide(presentation.slide_layouts[6])
    slide.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(2)).text = (
        "Native meeting content remains searchable when visual enrichment fails. "
        "This sentence is intentionally long enough to produce a native text chunk."
    )
    image = tmp_path / "visual.png"
    image.write_bytes(
        base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M/wHwAF/gL+5Q4R"
            "AAAAAElFTkSuQmCC"
        )
    )
    slide.shapes.add_picture(str(image), Inches(6), Inches(1), width=Inches(1))
    presentation.save(source)
    monkeypatch.setattr(paths, "SOURCES_DIR", sources)
    monkeypatch.setenv("PEC_PPT_VISION_MODE", "smart")
    monkeypatch.setattr(chunk_ingest, "_rendered_slide_image", lambda *args: tmp_path / "slide.png")
    monkeypatch.setattr(chunk_ingest, "describe_image", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("down")))

    chunks = chunk_ingest.ingest_file(source, access_token="token")

    assert any(chunk.method == "text" for chunk in chunks)
    assert not any(chunk.method == "vision_page" for chunk in chunks)


def test_cache_key_uses_same_prompt_version_as_metadata(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(chunk_ingest, "INDEX_DIR", tmp_path / "index")
    monkeypatch.delenv("PEC_PPT_VISION_PROMPT_VERSION", raising=False)

    default_path = chunk_ingest._vision_cache_file("digest", 1, "powerpoint")
    monkeypatch.setenv("PEC_PPT_VISION_PROMPT_VERSION", "3")
    custom_path = chunk_ingest._vision_cache_file("digest", 1, "powerpoint")

    assert chunk_ingest._vision_prompt_version() == "3"
    assert default_path != custom_path