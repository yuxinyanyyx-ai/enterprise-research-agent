import importlib
from pathlib import Path

import pytest

from src.pec.knowledge import paths


def test_default_knowledge_root_preserves_existing_data_directory(monkeypatch) -> None:
    names = ("KNOWLEDGE_ROOT", "PEC_KNOWLEDGE_ROOT")
    original = {name: getattr(paths, name) for name in ("ROOT", "KNOWLEDGE_ROOT", "SOURCES_DIR", "INDEX_DIR", "MANIFEST_FILE", "CHUNKS_FILE", "EMBEDDINGS_FILE")}
    for name in names:
        monkeypatch.delenv(name, raising=False)
    try:
        importlib.reload(paths)
        assert paths.KNOWLEDGE_ROOT == Path(__file__).resolve().parents[1] / "data" / "pec"
    finally:
        for name, value in original.items():
            setattr(paths, name, value)


def _set_root(tmp_path: Path, monkeypatch) -> Path:
    sources = tmp_path / "sources"
    monkeypatch.setattr(paths, "SOURCES_DIR", sources)
    return sources


def test_scope_is_normalized_and_prefix_safe(tmp_path, monkeypatch) -> None:
    sources = _set_root(tmp_path, monkeypatch)
    (sources / "PEC1").mkdir(parents=True)
    normalized, resolved = paths.normalize_source_scope("PEC1")
    assert normalized == "PEC1"
    assert resolved == (sources / "PEC1").resolve()
    assert paths.source_in_scope("PEC1/a.pptx", normalized)
    assert not paths.source_in_scope("PEC10/a.pptx", normalized)


@pytest.mark.parametrize("scope", ["../outside", "C:/outside", "/outside"])
def test_scope_rejects_paths_outside_sources(tmp_path, monkeypatch, scope) -> None:
    _set_root(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="路径越界"):
        paths.normalize_source_scope(scope)


def test_empty_scope_resolves_to_sources(tmp_path, monkeypatch) -> None:
    sources = _set_root(tmp_path, monkeypatch)
    normalized, resolved = paths.normalize_source_scope("")
    assert normalized == ""
    assert resolved == sources.resolve()