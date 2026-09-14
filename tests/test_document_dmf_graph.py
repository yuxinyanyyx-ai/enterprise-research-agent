from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.agent import domain_workflows, nodes
from src.schemas.document_dmf import ExtractedDMFQueryBatch
from src.schemas.domain_intent import DocumentIntent
from tests.domain_harness import DomainHarness


def _artifact(tmp_path: Path) -> dict:
    markdown = tmp_path / "document.md"
    markdown.write_text("Ibuprofen", encoding="utf-8")
    return {"document_id": "doc-1", "file_name": "sample.pdf", "source_path": str(tmp_path / "sample.pdf"),
            "markdown_path": str(markdown), "status": "parsed"}


def _documents(tmp_path):
    return {"doc-1": _artifact(tmp_path)}


def _dmf_result(ingredients):
    return {"success": True, "message": "ok", "query": {"ingredients": ingredients}, "query_count": 1,
            "success_count": 1, "total_records": 1, "results": [{"success": True, "message": "ok",
            "query": {"ingredient": ingredients[0]}, "records": [{"dmf_no": "12345", "ingredient": ingredients[0]}]}]}


class FakeDocumentService:
    def __init__(self, calls):
        self.calls = calls
        self.extracted = []

    def extract_query(self, artifact):
        self.extracted.append(artifact["document_id"])
        return ExtractedDMFQueryBatch.model_validate({"ingredients": ["Ibuprofen"]})

    def execute_confirmed_query(self, query):
        self.calls.append(query.model_dump())
        return _dmf_result(query.queries[0].ingredients)


def build_graph(monkeypatch, *, query=False, reuse=False):
    class Model:
        def with_structured_output(self, schema):
            assert schema is DocumentIntent
            return self

        def invoke(self, messages):
            return DocumentIntent(query_document_conditions=query, use_existing_data=reuse)

    monkeypatch.setattr(nodes, "create_apollo_llm", Model)
    return DomainHarness(domain_workflows.build_document_dmf_workflow(checkpointer=InMemorySaver()))


def test_document_review_extracts_without_querying_dmf(tmp_path, monkeypatch):
    calls = []
    service = FakeDocumentService(calls)
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)
    result = build_graph(monkeypatch).invoke({"user_query": "分析文档", "document_artifacts": _documents(tmp_path)},
                                             {"configurable": {"thread_id": "review"}})
    assert "Ibuprofen" in result["final_answer"]
    assert result["workflow_status"] == "completed"
    assert calls == []


@pytest.mark.parametrize("action", ["confirm", "edit", "reject"])
def test_document_query_requires_confirmation_before_single_dmf_call(tmp_path, monkeypatch, action):
    calls = []
    service = FakeDocumentService(calls)
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)
    graph = build_graph(monkeypatch, query=True)
    config = {"configurable": {"thread_id": action}}
    paused = graph.invoke({"user_query": "按文档查询", "document_artifacts": _documents(tmp_path)}, config)
    assert paused["__interrupt__"][0].value["query"]["queries"][0]["ingredients"] == ["Ibuprofen"]
    assert calls == []
    payload = {"action": action}
    if action == "edit":
        payload["query"] = {"queries": [{"ingredients": ["Naproxen"]}]}
    completed = graph.invoke(Command(resume=payload), config)
    assert len(service.extracted) == 1
    if action == "reject":
        assert calls == []
        assert completed["workflow_status"] == "cancelled"
    else:
        assert len(calls) == 1
        assert calls[0]["queries"][0]["ingredients"] == (["Naproxen"] if action == "edit" else ["Ibuprofen"])
        assert completed["dmf_results"]["results"][0]["records"][0]["dmf_no"] == "12345"


def test_multi_document_query_preserves_deduplication_and_provenance(tmp_path, monkeypatch):
    calls = []
    service = FakeDocumentService(calls)
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)
    first = _artifact(tmp_path)
    second = {**first, "document_id": "doc-2", "file_name": "second.xlsx"}
    graph = build_graph(monkeypatch, query=True)
    config = {"configurable": {"thread_id": "multi"}}
    paused = graph.invoke({"user_query": "联合查询文档", "document_artifacts": {"doc-1": first, "doc-2": second}}, config)
    queries = paused["__interrupt__"][0].value["query"]["queries"]
    assert len(queries) == 1
    assert [source["document_id"] for source in queries[0]["sources"]] == ["doc-1", "doc-2"]
    graph.invoke(Command(resume={"action": "confirm"}), config)
    assert len(calls) == 1 and len(calls[0]["queries"]) == 1
    assert service.extracted == ["doc-1", "doc-2"]


def test_multiple_extracted_rows_are_confirmed_as_one_batch(tmp_path, monkeypatch):
    calls = []
    service = FakeDocumentService(calls)
    service.extract_query = lambda artifact: ExtractedDMFQueryBatch.model_validate({"queries": [
        {"dmf_no": "234", "ingredients": ["Ibuprofen"]}, {"dmf_no": "211", "ingredients": ["Naproxen"]},
    ]})
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)
    graph = build_graph(monkeypatch, query=True)
    config = {"configurable": {"thread_id": "batch"}}
    paused = graph.invoke({"user_query": "按文档查询", "document_artifacts": _documents(tmp_path)}, config)
    assert len(paused["__interrupt__"][0].value["query"]["queries"]) == 2
    graph.invoke(Command(resume={"action": "confirm"}), config)
    assert len(calls) == 1 and len(calls[0]["queries"]) == 2


def test_document_review_then_query_reuses_extracted_conditions(tmp_path, monkeypatch):
    calls = []
    service = FakeDocumentService(calls)
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)
    graph = build_graph(monkeypatch)
    config = {"configurable": {"thread_id": "reuse"}}
    reviewed = graph.invoke({"user_query": "分析文档", "document_artifacts": _documents(tmp_path)}, config)
    assert "Ibuprofen" in reviewed["final_answer"]
    monkeypatch.setattr(domain_workflows, "_parse", lambda state, domain: {"query_document_conditions": True, "use_existing_data": True, "needs_clarification": False})
    paused = graph.invoke({"user_query": "查询这些条件"}, config)
    assert paused["__interrupt__"]
    assert service.extracted == ["doc-1"] and calls == []


def test_unknown_document_is_rejected_before_extraction(monkeypatch):
    class Model:
        def with_structured_output(self, schema):
            return self

        def invoke(self, messages):
            return DocumentIntent(document_ids=["missing"], query_document_conditions=True)

    monkeypatch.setattr(nodes, "create_apollo_llm", Model)
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: (_ for _ in ()).throw(AssertionError("No extraction")))
    output = domain_workflows.build_document_dmf_workflow().invoke({"workflow_input": {"user_query": "查询文档"}})["workflow_output"]
    assert output["result"]["status"] == "needs_clarification"