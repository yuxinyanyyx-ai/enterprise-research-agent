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
        self.updates: list[tuple[dict, dict]] = []
        self.state: dict = {}

    def invoke(self, value, *, config):
        self.calls.append((value, config))
        return self.results.pop(0)

    def update_state(self, config, values):
        self.updates.append((config, values))
        self.state.update(values)

    def get_state(self, config):
        return SimpleNamespace(values=self.state)


def _new_client(monkeypatch, results: list[dict]) -> tuple[TestClient, FakeGraph]:
    fake_graph = FakeGraph(results)
    monkeypatch.setattr(web_routes.agent_web_service, "graph", fake_graph)
    web_routes.agent_web_service.session_repository.initialize_schema()
    web_routes.agent_web_service.sessions.clear()
    return TestClient(app), fake_graph


def test_agent_session_reuses_thread_across_multiple_messages(monkeypatch) -> None:
    client, graph = _new_client(
        monkeypatch,
        [
            {"final_answer": "第一轮", "tool_artifacts": []},
            {"final_answer": "第二轮", "tool_artifacts": []},
            {"final_answer": "第三轮", "tool_artifacts": []},
        ],
    )
    session_id = client.post("/api/agent/sessions").json()["session_id"]

    for index in range(1, 4):
        response = client.post(
            f"/api/agent/sessions/{session_id}/messages",
            json={"message": f"第 {index} 轮"},
        )
        assert response.status_code == 200
        assert response.json()["answer"] == f"第{'一二三'[index - 1]}轮"

    assert [
        config["configurable"]["thread_id"]
        for _, config in graph.calls
    ] == [session_id, session_id, session_id]
    request_ids = [value["request_id"] for value, _ in graph.calls]
    assert all(request_ids)
    assert len(set(request_ids)) == 3


def test_agent_sessions_use_isolated_thread_ids(monkeypatch) -> None:
    client, graph = _new_client(
        monkeypatch,
        [
            {"final_answer": "会话 A", "tool_artifacts": []},
            {"final_answer": "会话 B", "tool_artifacts": []},
        ],
    )
    session_a = client.post("/api/agent/sessions").json()["session_id"]
    session_b = client.post("/api/agent/sessions").json()["session_id"]

    assert session_a != session_b
    assert client.post(
        f"/api/agent/sessions/{session_a}/messages",
        json={"message": "A"},
    ).status_code == 200
    assert client.post(
        f"/api/agent/sessions/{session_b}/messages",
        json={"message": "B"},
    ).status_code == 200
    assert [
        config["configurable"]["thread_id"]
        for _, config in graph.calls
    ] == [session_a, session_b]


def test_agent_session_is_recovered_from_repository_after_cache_miss(monkeypatch) -> None:
    client, graph = _new_client(
        monkeypatch,
        [{"final_answer": "恢复成功", "tool_artifacts": []}],
    )
    session_id = client.post("/api/agent/sessions").json()["session_id"]
    web_routes.agent_web_service.sessions.clear()

    response = client.post(
        f"/api/agent/sessions/{session_id}/messages",
        json={"message": "继续对话"},
    )

    assert response.status_code == 200
    assert response.json()["answer"] == "恢复成功"
    assert graph.calls[0][1]["configurable"]["thread_id"] == session_id


def test_export_download_is_scoped_to_session_and_directory(tmp_path, monkeypatch):
    output_dir = tmp_path / "exports"
    output_dir.mkdir()
    target = output_dir / "result.xlsx"
    target.write_bytes(b"workbook")
    outside = tmp_path / "outside.xlsx"
    outside.write_bytes(b"private")
    monkeypatch.setattr(web_routes, "OUTPUT_DIR", output_dir)
    artifact = {"tool_name": "export_dmf_excel", "result": {"success": True, "file_path": str(target)}}
    client, graph = _new_client(monkeypatch, [{"tool_artifacts": [artifact, {
        "tool_name": "export_dmf_excel", "result": {"success": True, "file_path": str(outside)},
    }]}, {"tool_artifacts": [artifact]}])
    owner = client.post("/api/agent/sessions").json()["session_id"]
    other = client.post("/api/agent/sessions").json()["session_id"]
    result = client.post(f"/api/agent/sessions/{owner}/messages", json={"message": "导出"}).json()
    assert len(result["downloads"]) == 1
    download = result["downloads"][0]
    assert client.get(download["url"]).content == b"workbook"
    assert client.get(f"/api/agent/sessions/{other}/files/{download['file_id']}").status_code == 404
    repeated = client.post(f"/api/agent/sessions/{owner}/messages", json={"message": "导出"}).json()
    assert repeated["downloads"] == []


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
    assert graph_input["document_artifacts"]["doc-1"]["document_id"] == "doc-1"


def test_agent_documents_append_list_delete_and_clear(
    tmp_path: Path,
    monkeypatch,
) -> None:
    saved_paths = [tmp_path / "a.xlsx", tmp_path / "b.xlsx"]
    for path in saved_paths:
        path.write_bytes(b"xlsx")

    async def fake_save(files):
        return tuple(saved_paths[:len(files)])

    class FakeDocumentService:
        def parse_document(self, path):
            document_id = f"doc-{path.stem}"
            return SimpleNamespace(
                model_dump=lambda: {
                    "document_id": document_id,
                    "file_name": path.name,
                    "source_path": str(path),
                    "markdown_path": str(tmp_path / f"{path.stem}.md"),
                    "status": "parsed",
                },
            )

    monkeypatch.setattr(web_routes, "save_uploaded_files", fake_save)
    monkeypatch.setattr(web_routes, "DocumentDMFService", FakeDocumentService)
    client, graph = _new_client(monkeypatch, [])
    session_id = client.post("/api/agent/sessions").json()["session_id"]

    uploaded = client.post(
        f"/api/agent/sessions/{session_id}/documents",
        files=[
            ("files", ("a.xlsx", b"a", "application/octet-stream")),
            ("files", ("b.xlsx", b"b", "application/octet-stream")),
        ],
    )

    assert uploaded.status_code == 200
    assert [item["document_id"] for item in uploaded.json()["documents"]] == [
        "doc-a",
        "doc-b",
    ]
    listed = client.get(f"/api/agent/sessions/{session_id}/documents")
    assert listed.json() == {"documents": uploaded.json()["documents"]}

    graph.state["document_extractions"] = {
        "doc-a": {"queries": [{"ingredients": ["A"]}]},
        "doc-b": {"queries": [{"ingredients": ["B"]}]},
    }

    deleted = client.delete(
        f"/api/agent/sessions/{session_id}/documents/doc-a"
    )
    assert [item["document_id"] for item in deleted.json()["documents"]] == ["doc-b"]
    assert graph.updates[-1][1]["document_extractions"] == {
        "doc-b": {"queries": [{"ingredients": ["B"]}]}
    }
    assert graph.updates[-1][1]["document_artifacts"] == {
        "doc-b": web_routes.agent_web_service.get_session(session_id).documents["doc-b"]
    }

    cleared = client.delete(f"/api/agent/sessions/{session_id}/documents")
    assert cleared.json() == {"status": "cleared", "documents": []}
    assert graph.updates[-1][1]["document_artifacts"] == {}


def test_agent_document_changes_are_blocked_while_awaiting_confirmation(
    monkeypatch,
) -> None:
    interrupt = {"type": "document_query_confirmation", "query": {"queries": []}}
    client, _ = _new_client(
        monkeypatch,
        [{"__interrupt__": [FakeInterrupt(interrupt)]}],
    )
    session_id = client.post("/api/agent/sessions").json()["session_id"]
    client.post(
        f"/api/agent/sessions/{session_id}/messages",
        json={"message": "查询文档"},
    )

    upload = client.post(
        f"/api/agent/sessions/{session_id}/documents",
        files={"files": ("a.xlsx", b"a", "application/octet-stream")},
    )
    clear = client.delete(f"/api/agent/sessions/{session_id}/documents")

    assert upload.status_code == 409
    assert clear.status_code == 409