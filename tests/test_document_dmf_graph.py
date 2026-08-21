from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.agent import graph as graph_module
from src.agent import nodes
from src.schemas.document_dmf import ExtractedDMFQuery


def _artifact(tmp_path: Path) -> dict:
    markdown = tmp_path / "results" / "documents" / "doc-1" / "document.md"
    markdown.parent.mkdir(parents=True)
    markdown.write_text("原料 Ibuprofen", encoding="utf-8")
    return {
        "document_id": "doc-1",
        "file_name": "sample.pdf",
        "source_path": str(tmp_path / "sample.pdf"),
        "markdown_path": str(markdown),
        "status": "parsed",
    }


def _intent(task_type: str):
    def understand(state):
        return {
            "task_type": task_type,
            "requested_outputs": ["result"] if task_type == "dmf_document_compare" else [],
            "needs_clarification": False,
            "dmf_no": "",
            "applicant_name": "",
            "ingredients": [],
            "messages": [HumanMessage(content=state["user_query"])],
            "tool_rounds": 0,
            "max_tool_rounds": 8,
            "last_tool_batch": [],
            "tool_stop_reason": "",
            "tool_artifacts": [],
        }

    return understand


def _dmf_result(ingredients: list[str]) -> dict:
    return {
        "success": True,
        "message": "查询完成",
        "query_count": 1,
        "success_count": 1,
        "failed_count": 0,
        "total_records": 1,
        "results": [
            {
                "success": True,
                "message": "查询完成",
                "query": {"ingredient": ingredients[0]},
                "records": [
                    {
                        "dmf_no": "12345",
                        "applicant_name": "Example Pharma",
                        "ingredient": ingredients[0],
                        "valid_date": "2027-01-01",
                    }
                ],
            }
        ],
    }


class FakeDocumentService:
    def __init__(self, calls: list[dict]) -> None:
        self.calls = calls

    def extract_query(self, artifact) -> ExtractedDMFQuery:
        assert artifact["file_name"] == "sample.pdf"
        return ExtractedDMFQuery(ingredients=["Ibuprofen"])

    def execute_confirmed_query(self, query: ExtractedDMFQuery) -> dict:
        self.calls.append(query.model_dump())
        return _dmf_result(query.ingredients)


def test_document_review_extracts_without_querying_dmf(tmp_path, monkeypatch) -> None:
    calls: list[dict] = []
    service = FakeDocumentService(calls)
    monkeypatch.setattr(graph_module, "understand_request", _intent("document_review"))
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)

    graph = graph_module.build_research_graph()
    result = graph.invoke(
        {
            "user_query": "分析上传的文档",
            "document_artifact": _artifact(tmp_path),
            "warnings": [],
        }
    )

    assert "Ibuprofen" in result["final_answer"]
    assert calls == []


@pytest.mark.parametrize(
    ("resume", "expected_ingredients", "expected_answer"),
    [
        (
            {"action": "confirm", "query": {"ingredients": ["Ibuprofen"]}},
            ["Ibuprofen"],
            "12345",
        ),
        (
            {"action": "edit", "query": {"ingredients": ["Naproxen"]}},
            ["Naproxen"],
            "12345",
        ),
        ({"action": "reject"}, None, "已取消"),
    ],
)
def test_document_query_requires_confirmation_before_single_dmf_call(
    tmp_path,
    monkeypatch,
    resume,
    expected_ingredients,
    expected_answer,
) -> None:
    calls: list[dict] = []
    service = FakeDocumentService(calls)
    monkeypatch.setattr(
        graph_module,
        "understand_request",
        _intent("dmf_document_compare"),
    )
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)

    graph = graph_module.build_research_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": f"document-{resume['action']}"}}
    paused = graph.invoke(
        {
            "user_query": "分析上传文档并查询 DMF",
            "document_artifact": _artifact(tmp_path),
            "warnings": [],
        },
        config=config,
    )

    assert paused["__interrupt__"][0].value["type"] == "document_query_confirmation"
    assert paused["__interrupt__"][0].value["query"]["ingredients"] == ["Ibuprofen"]
    assert calls == []

    completed = graph.invoke(Command(resume=resume), config=config)

    assert expected_answer in completed["final_answer"]
    if expected_ingredients is None:
        assert calls == []
    else:
        assert [call["ingredients"] for call in calls] == [expected_ingredients]