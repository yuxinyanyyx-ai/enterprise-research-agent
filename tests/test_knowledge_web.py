from contextlib import nullcontext
from io import BytesIO
import json
from types import SimpleNamespace
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.pec.knowledge import paths
from src.pec.knowledge.sources import SourceDocument
from src.web.routes import knowledge


def make_client() -> TestClient:
    app = FastAPI()
    app.include_router(knowledge.router)
    return TestClient(app)


def test_sources_reads_active_generation_metadata(tmp_path, monkeypatch) -> None:
    source = tmp_path / "PEC" / "meeting.docx"
    source.parent.mkdir()
    source.write_bytes(b"meeting")
    monkeypatch.setattr(paths, "SOURCES_DIR", tmp_path)

    class Provider:
        def discover(self, scope=""):
            return [SourceDocument(source, "PEC/meeting.docx", "local", "PEC/meeting.docx")]

    monkeypatch.setattr(knowledge, "LocalSourceProvider", Provider)
    monkeypatch.setattr(
        knowledge,
        "get_index_status",
        lambda: {
            "files": {
                "PEC/meeting.docx": {
                    "sha256": knowledge._file_sha256(source),
                    "chunk_count": 4,
                    "indexed_at": "2026-10-01T00:00:00+00:00",
                }
            }
        },
    )

    response = make_client().get("/api/knowledge/sources")

    assert response.status_code == 200
    assert response.json()["sources"][0]["state"] == "indexed"
    assert response.json()["sources"][0]["chunk_count"] == 4
    assert response.json()["scopes"] == ["PEC"]


def test_job_api_uses_persistent_job_service(monkeypatch) -> None:
    calls = []

    class Provider:
        def discover(self, scope=""):
            return [object()]

    monkeypatch.setattr(knowledge, "LocalSourceProvider", Provider)
    monkeypatch.setattr(knowledge, "start_knowledge_job", lambda **kwargs: calls.append(kwargs) or {"id": "job-1", **kwargs})
    monkeypatch.setattr(knowledge, "list_knowledge_jobs", lambda: [{"id": "job-1", "status": "running"}])
    client = make_client()

    rejected = client.post("/api/knowledge/jobs", json={"kind": "index", "scope": "PEC"})
    check = client.post("/api/knowledge/jobs", json={"kind": "check", "scope": "PEC"})
    index = client.post(
        "/api/knowledge/jobs",
        json={"kind": "index", "scope": "PEC", "confirmed": True},
    )
    listed = client.get("/api/knowledge/jobs")
    status = client.get("/api/knowledge/status")

    assert rejected.status_code == 400
    assert check.status_code == index.status_code == 202
    assert calls == [
        {"scope": "PEC", "force": False, "inspect_only": True},
        {"scope": "PEC", "force": False, "inspect_only": False},
    ]
    assert listed.json()["jobs"][0]["id"] == "job-1"
    assert status.json()["updating"] is True


def test_upload_api_saves_valid_source_through_staging_batch(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(knowledge, "WriterLock", lambda: nullcontext())
    monkeypatch.setattr(
        knowledge,
        "get_settings",
        lambda *, require_token=True: SimpleNamespace(max_file_size_bytes=1024),
    )

    response = make_client().post(
        "/api/knowledge/sources",
        data={"folder": "PEC/2026"},
        files={"files": ("meeting.docx", b"document bytes")},
    )

    assert response.status_code == 201
    assert response.json()["uploaded"] == ["PEC/2026/meeting.docx"]
    assert (tmp_path / "sources" / "PEC" / "2026" / "meeting.docx").read_bytes() == b"document bytes"


def test_upload_api_extracts_zip_under_target_folder(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(paths, "SOURCES_DIR", tmp_path / "sources")
    monkeypatch.setattr(knowledge, "WriterLock", lambda: nullcontext())
    monkeypatch.setattr(
        knowledge,
        "get_settings",
        lambda *, require_token=True: SimpleNamespace(max_file_size_bytes=1024),
    )
    archive = BytesIO()
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        output.writestr("2026/meeting.docx", b"meeting")
        output.writestr("2026/slides.pptx", b"slides")

    response = make_client().post(
        "/api/knowledge/sources",
        data={"folder": "PEC"},
        files={"files": ("meeting.zip", archive.getvalue())},
    )

    assert response.status_code == 201
    assert response.json()["uploaded"] == [
        "PEC/2026/meeting.docx",
        "PEC/2026/slides.pptx",
    ]
    assert (tmp_path / "sources" / "PEC" / "2026" / "meeting.docx").read_bytes() == b"meeting"
    assert (tmp_path / "sources" / "PEC" / "2026" / "slides.pptx").read_bytes() == b"slides"


def test_upload_api_rejects_zip_traversal_without_writing_any_files(tmp_path, monkeypatch) -> None:
    root = tmp_path / "sources"
    monkeypatch.setattr(paths, "SOURCES_DIR", root)
    monkeypatch.setattr(knowledge, "WriterLock", lambda: nullcontext())
    monkeypatch.setattr(
        knowledge,
        "get_settings",
        lambda *, require_token=True: SimpleNamespace(max_file_size_bytes=1024),
    )
    archive = BytesIO()
    with ZipFile(archive, "w", ZIP_DEFLATED) as output:
        output.writestr("safe.docx", b"safe")
        output.writestr("../escape.docx", b"escape")

    response = make_client().post(
        "/api/knowledge/sources",
        files={"files": ("meeting.zip", archive.getvalue())},
    )

    assert response.status_code == 400
    assert list(root.rglob("*.docx")) == []


def test_main_application_registers_knowledge_api(monkeypatch) -> None:
    from src.main import app

    monkeypatch.setattr(
        knowledge,
        "get_index_status",
        lambda: {"file_count": 0, "chunk_count": 0, "embeddings_ready": False},
    )
    monkeypatch.setattr(knowledge, "list_knowledge_jobs", lambda: [])

    response = TestClient(app).get("/api/knowledge/status")

    assert response.status_code == 200
    assert response.json()["updating"] is False


def test_topics_api_serves_paged_file_and_meeting_evidence(tmp_path, monkeypatch) -> None:
    generation = tmp_path / "generation"
    generation.mkdir()
    (generation / "topics.json").write_text(
        json.dumps(
            [
                {
                    "topic_id": "topic-1",
                    "source_ref": "PEC/2026/meeting.docx",
                    "evidence_ids": ["chunk-1"],
                    "title": "Decision",
                },
                {
                    "topic_id": "topic-hidden",
                    "source_ref": "PEC/2026/withdrawn.docx",
                    "evidence_ids": ["chunk-hidden"],
                    "title": "Withdrawn",
                },
            ]
        ),
        encoding="utf-8",
    )
    (generation / "meeting_topics.json").write_text(
        json.dumps(
            [
                {
                    "topic_id": "meeting-1",
                    "meeting_scope": "PEC/2026",
                    "source_ref": "PEC/2026/meeting.docx",
                    "source_refs": ["PEC/2026/meeting.docx"],
                    "evidence_ids": ["chunk-1"],
                    "title": "Meeting decision",
                }
            ]
        ),
        encoding="utf-8",
    )
    manifest = {
        "files": {
            "PEC/2026/meeting.docx": {"state": "active"},
            "PEC/2026/withdrawn.docx": {"state": "withdrawn"},
        }
    }
    chunks = [
        {
            "chunk_id": "chunk-1",
            "source_ref": "PEC/2026/meeting.docx",
            "loc": "Page 1",
            "method": "text",
            "text": "The committee approved the plan.",
        },
        {
            "chunk_id": "chunk-hidden",
            "source_ref": "PEC/2026/withdrawn.docx",
            "loc": "Page 1",
            "method": "text",
            "text": "Hidden evidence.",
        },
    ]
    monkeypatch.setattr(knowledge, "active_dir", lambda: generation)
    monkeypatch.setattr(knowledge, "_load_manifest", lambda directory: manifest)
    monkeypatch.setattr(knowledge, "_load_chunks", lambda directory: chunks)

    client = make_client()
    file_response = client.get(
        "/api/knowledge/topics",
        params={"scope": "PEC/2026", "offset": 0, "limit": 1},
    )
    meeting_response = client.get(
        "/api/knowledge/topics",
        params={"level": "meeting", "scope": "PEC/2026"},
    )

    assert file_response.status_code == 200
    assert file_response.json()["total"] == 1
    assert file_response.json()["topics"][0]["evidence"][0]["text"] == "The committee approved the plan."
    assert meeting_response.status_code == 200
    assert meeting_response.json()["topics"][0]["level"] == "meeting"
    assert meeting_response.json()["topics"][0]["evidence"][0]["chunk_id"] == "chunk-1"
