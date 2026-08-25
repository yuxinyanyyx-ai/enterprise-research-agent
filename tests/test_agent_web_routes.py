from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from src.main import app
from src.web.routes import agent as web_routes


class FakeInterrupt:
    def __init__(self, value: dict) -> None:
        self.value = value


class FakeGraph:
    def __init__(self, results: list[dict]) -> None:
        self.results = list(results)
        self.calls: list[tuple[object, dict]] = []

    def invoke(self, value, *, config):
        self.calls.append((value, config))
        return self.results.pop(0)


def _new_client(monkeypatch, results: list[dict]) -> tuple[TestClient, FakeGraph]:
    fake_graph = FakeGraph(results)
    monkeypatch.setattr(web_routes.agent_web_service, "graph", fake_graph)
    web_routes.agent_web_service.sessions.clear()
    return TestClient(app), fake_graph


def test_agent_session_message_interrupt_and_resume(monkeypatch) -> None:
    interrupt = {
        "type": "document_query_confirmation",
        "message": "请确认查询条件",
        "query": {"queries": [{"ingredients": ["Ibuprofen"]}]},
    }
    client, graph = _new_client(
        monkeypatch,
        [
            {"__interrupt__": [FakeInterrupt(interrupt)]},
            {"final_answer": "查询完成", "tool_artifacts": []},
        ],
    )
    session_id = client.post("/api/agent/sessions").json()["session_id"]

    paused = client.post(
        f"/api/agent/sessions/{session_id}/messages",
        json={"message": "分析文档并查询 DMF"},
    )

    assert paused.status_code == 200
    assert paused.json() == {
        "status": "awaiting_confirmation",
        "answer": "",
        "interrupt": interrupt,
        "downloads": [],
    }
    conflict = client.post(
        f"/api/agent/sessions/{session_id}/messages",
        json={"message": "另一条消息"},
    )
    assert conflict.status_code == 409

    completed = client.post(
        f"/api/agent/sessions/{session_id}/resume",
        json={"action": "confirm", "query": interrupt["query"]},
    )

    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["answer"] == "查询完成"
    assert graph.calls[0][1]["configurable"]["thread_id"] == session_id
    assert graph.calls[1][1]["configurable"]["thread_id"] == session_id


def test_agent_document_upload_uses_multipart_and_enters_graph_state(
    tmp_path: Path,
    monkeypatch,
) -> None:
    saved_path = tmp_path / "uploads" / "sample.xlsx"
    saved_path.parent.mkdir(parents=True)
    saved_path.write_bytes(b"xlsx")
    artifact = SimpleNamespace(
        document_id="doc-1",
        file_name="sample.xlsx",
        model_dump=lambda: {
            "document_id": "doc-1",
            "file_name": "sample.xlsx",
            "source_path": str(saved_path),
            "markdown_path": str(tmp_path / "results" / "document.md"),
            "status": "parsed",
        },
    )

    async def fake_save(files):
        assert files[0].filename == "sample.xlsx"
        return (saved_path,)

    class FakeDocumentService:
        def parse_document(self, path):
            assert path == saved_path
            return artifact

    monkeypatch.setattr(web_routes, "save_uploaded_files", fake_save)
    monkeypatch.setattr(web_routes, "DocumentDMFService", FakeDocumentService)
    client, graph = _new_client(
        monkeypatch,
        [{"final_answer": "已分析文档", "tool_artifacts": []}],
    )
    session_id = client.post("/api/agent/sessions").json()["session_id"]

    uploaded = client.post(
        f"/api/agent/sessions/{session_id}/document",
        files={
            "file": (
                "sample.xlsx",
                b"xlsx-content",
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )

    assert uploaded.status_code == 200
    assert uploaded.json()["document"]["file_name"] == "sample.xlsx"

    response = client.post(
        f"/api/agent/sessions/{session_id}/messages",
        json={"message": "分析当前文档"},
    )

    assert response.status_code == 200
    graph_input = graph.calls[0][0]
    assert graph_input["document_artifact"]["document_id"] == "doc-1"