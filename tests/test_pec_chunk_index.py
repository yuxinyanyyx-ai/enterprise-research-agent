import json
from pathlib import Path

import numpy as np
import pytest

from src.pec.knowledge import chunk_index, chunk_ingest, paths
from src.pec.knowledge.chunk_ingest import ChunkDraft


def _configure_index(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
    sources = tmp_path / "sources"
    index = tmp_path / "index"
    sources.mkdir()
    index.mkdir()
    monkeypatch.setenv("APOLLO_EMBEDDING_MODEL", "test-embedding")
    monkeypatch.setenv("PREVIEW_ON_INDEX", "0")
    monkeypatch.setattr(paths, "SOURCES_DIR", sources)
    monkeypatch.setattr(paths, "INDEX_DIR", index)
    monkeypatch.setattr(chunk_ingest, "SOURCES_DIR", sources, raising=False)
    monkeypatch.setattr(chunk_index, "SOURCES_DIR", sources)
    monkeypatch.setattr(chunk_index, "MANIFEST_FILE", index / "manifest.json")
    monkeypatch.setattr(chunk_index, "CHUNKS_FILE", index / "chunks.jsonl")
    monkeypatch.setattr(chunk_index, "EMBEDDINGS_FILE", index / "embeddings.npy")
    return sources, index


def _draft(path: Path) -> ChunkDraft:
    text = path.read_text(encoding="utf-8")
    source_ref = paths.source_relative(path)
    return ChunkDraft(
        chunk_id=f"chunk-{source_ref}", text=text, source_ref=source_ref,
        folder=source_ref.split("/")[0] if "/" in source_ref else "",
        loc="Page 1", method="text",
    )


def test_ingest_pdf_does_not_request_access_token(tmp_path, monkeypatch) -> None:
    sources, _ = _configure_index(tmp_path, monkeypatch)
    pdf = sources / "PEC1" / "minutes.pdf"
    pdf.parent.mkdir()
    pdf.write_bytes(b"pdf")
    seen = {}

    monkeypatch.setattr(
        chunk_ingest,
        "get_access_token",
        lambda: pytest.fail("PDF text extraction must not request an Apollo token"),
    )

    def extract(path, source_ref):
        seen["path"] = path
        seen["source_ref"] = source_ref
        return []

    monkeypatch.setattr(chunk_ingest, "_extract_pdf_chunks", extract)

    assert chunk_ingest.ingest_file(pdf) == []
    assert seen == {"path": pdf.resolve(), "source_ref": "PEC1/minutes.pdf"}


def test_incremental_index_embeds_only_new_content(tmp_path, monkeypatch) -> None:
    sources, index = _configure_index(tmp_path, monkeypatch)
    first = sources / "PEC1" / "first.pdf"
    first.parent.mkdir()
    first.write_text("first content", encoding="utf-8")
    monkeypatch.setattr(chunk_index, "ingest_file", lambda path: [_draft(path)])
    embedded: list[list[str]] = []

    def embed(texts, batch_size=16):
        embedded.append(list(texts))
        return [[float(len(text)), 1.0] for text in texts]

    monkeypatch.setattr(chunk_index, "_batch_embed", embed)
    initial = chunk_index.update_knowledge_index()
    assert initial["embedded_chunks"] == 1
    assert embedded == [["first content"]]

    unchanged = chunk_index.update_knowledge_index()
    assert unchanged["embedded_chunks"] == 0
    assert embedded == [["first content"]]

    second = sources / "PEC1" / "second.pdf"
    second.write_text("second content", encoding="utf-8")
    added = chunk_index.update_knowledge_index("PEC1")
    assert added["embedded_chunks"] == 1
    assert embedded[-1] == ["second content"]
    matrix = np.load(index / "embeddings.npy")
    assert matrix.shape == (2, 2)
    manifest = json.loads((index / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["chunk_count"] == 2
    assert manifest["embeddings_ready"] is True
    assert manifest["generation"]


def test_changed_file_reuses_identical_chunk_and_removal_drops_vector(tmp_path, monkeypatch) -> None:
    sources, index = _configure_index(tmp_path, monkeypatch)
    path = sources / "PEC1" / "meeting.pdf"
    path.parent.mkdir()
    path.write_text("stable content", encoding="utf-8")
    monkeypatch.setattr(chunk_index, "ingest_file", lambda source: [_draft(source)])
    calls = []
    monkeypatch.setattr(
        chunk_index, "_batch_embed",
        lambda texts, batch_size=16: calls.append(list(texts)) or [[1.0, 2.0] for _ in texts],
    )
    chunk_index.update_knowledge_index()
    original = path.read_text(encoding="utf-8")
    path.write_text(original, encoding="utf-8")
    forced = chunk_index.update_knowledge_index("PEC1", force=True)
    assert forced["embedded_chunks"] == 0
    assert len(calls) == 1

    path.unlink()
    removed = chunk_index.update_knowledge_index("PEC1")
    assert removed["removed"] == ["PEC1/meeting.pdf"]
    assert np.load(index / "embeddings.npy").shape == (0, 0)


def test_embedding_failure_keeps_previous_snapshot(tmp_path, monkeypatch) -> None:
    sources, index = _configure_index(tmp_path, monkeypatch)
    first = sources / "first.pdf"
    first.write_text("first", encoding="utf-8")
    monkeypatch.setattr(chunk_index, "ingest_file", lambda path: [_draft(path)])
    monkeypatch.setattr(chunk_index, "_batch_embed", lambda texts, batch_size=16: [[1.0, 2.0]])
    chunk_index.update_knowledge_index()
    before = {name: (index / name).read_bytes() for name in ("manifest.json", "chunks.jsonl", "embeddings.npy")}

    second = sources / "second.pdf"
    second.write_text("second", encoding="utf-8")
    monkeypatch.setattr(chunk_index, "_batch_embed", lambda texts, batch_size=16: (_ for _ in ()).throw(RuntimeError("down")))
    with pytest.raises(RuntimeError, match="down"):
        chunk_index.update_knowledge_index()
    after = {name: (index / name).read_bytes() for name in before}
    assert after == before


def test_version_two_manifest_detects_content_mismatch() -> None:
    chunks = [{"chunk_id": "a", "text": "current"}]
    matrix = np.array([[1.0, 2.0]], dtype=np.float32)
    manifest = {
        "version": 2,
        "embedding_model": "test-embedding",
        "embeddings_ready": True,
        "chunks_sha256": chunk_index._chunks_digest([{"chunk_id": "a", "text": "old"}]),
        "embeddings_sha256": chunk_index._matrix_digest(matrix),
    }
    assert chunk_index._embeddings_ready(manifest, chunks, matrix) is False


def test_single_file_scope_does_not_remove_sibling(tmp_path, monkeypatch) -> None:
    sources, _ = _configure_index(tmp_path, monkeypatch)
    folder = sources / "PEC1"
    folder.mkdir()
    first = folder / "first.pdf"
    second = folder / "second.pdf"
    first.write_text("first", encoding="utf-8")
    second.write_text("second", encoding="utf-8")
    monkeypatch.setattr(chunk_index, "ingest_file", lambda path: [_draft(path)])
    monkeypatch.setattr(
        chunk_index, "_batch_embed",
        lambda texts, batch_size=16: [[float(index), 1.0] for index, _ in enumerate(texts, 1)],
    )
    chunk_index.update_knowledge_index()
    first.write_text("first changed", encoding="utf-8")
    result = chunk_index.update_knowledge_index("PEC1/first.pdf")
    assert result["updated"] == ["PEC1/first.pdf"]
    assert "PEC1/second.pdf" not in result["removed"]


def test_search_chunks_maps_cosine_candidates_through_rerank(monkeypatch) -> None:
    chunks = [
        {"chunk_id": "a", "text": "alpha", "source_ref": "PEC1/a.pdf", "folder": "PEC1", "loc": "Page 1", "method": "text"},
        {"chunk_id": "b", "text": "beta", "source_ref": "PEC2/b.pdf", "folder": "PEC2", "loc": "Page 2", "method": "text"},
    ]
    matrix = np.array([[1.0, 0.0], [0.0, 1.0]], dtype=np.float32)
    monkeypatch.setenv("APOLLO_EMBEDDING_MODEL", "test-embedding")
    monkeypatch.setenv("RERANK_MIN_SCORE", "0.4")
    monkeypatch.setattr(chunk_index, "_load_chunks", lambda: chunks)
    monkeypatch.setattr(chunk_index, "_load_embeddings", lambda: matrix)
    monkeypatch.setattr(chunk_index, "_load_manifest", lambda: {
        "embedding_model": "test-embedding", "embeddings_ready": True,
    })
    monkeypatch.setattr(chunk_index, "get_access_token", lambda: "token")
    monkeypatch.setattr(chunk_index, "embed_texts", lambda texts, access_token=None: [[0.9, 0.1]])
    seen = {}

    def rerank(query, documents, top_n, access_token=None):
        seen["documents"] = documents
        return [
            {"index": 1, "relevance_score": 0.8},
            {"index": 0, "relevance_score": 0.3},
        ]

    monkeypatch.setattr(chunk_index, "rerank_documents", rerank)
    hits = chunk_index.search_chunks("query", top_n=2, recall_top_k=2)
    assert seen["documents"] == ["alpha", "beta"]
    assert [hit.chunk_id for hit in hits] == ["b"]
    assert hits[0].embed_score == pytest.approx(0.1104315)


def test_search_chunks_rejects_embedding_model_mismatch(monkeypatch) -> None:
    monkeypatch.setenv("APOLLO_EMBEDDING_MODEL", "new-model")
    monkeypatch.setattr(chunk_index, "_load_chunks", lambda: [{"chunk_id": "a"}])
    monkeypatch.setattr(chunk_index, "_load_embeddings", lambda: np.array([[1.0]], dtype=np.float32))
    monkeypatch.setattr(chunk_index, "_load_manifest", lambda: {
        "embedding_model": "old-model", "embeddings_ready": True,
    })
    with pytest.raises(RuntimeError, match="向量索引未完成"):
        chunk_index.search_chunks("query")