from __future__ import annotations

from pathlib import Path

import pytest
from langchain_core.messages import HumanMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

from src.agent import graph as graph_module
from src.agent import nodes
from src.schemas.document_dmf import ExtractedDMFQueryBatch
from src.schemas.intent import ResearchIntent


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


def _documents(tmp_path: Path) -> dict[str, dict]:
    artifact = _artifact(tmp_path)
    return {artifact["document_id"]: artifact}


def _intent(
    *,
    use_existing_data: bool = False,
    query: bool = False,
    requested_outputs: list[str] | None = None,
):
    def understand(state):
        return {
            "data_source": "document",
            "use_existing_data": use_existing_data,
            "query_document_conditions": query,
            "requested_outputs": (
                requested_outputs
                if requested_outputs is not None
                else (["result"] if query else [])
            ),
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

    def extract_query(self, artifact) -> ExtractedDMFQueryBatch:
        assert artifact["file_name"] == "sample.pdf"
        return ExtractedDMFQueryBatch.model_validate(
            {"ingredients": ["Ibuprofen"]}
        )

    def execute_confirmed_query(self, query: ExtractedDMFQueryBatch) -> dict:
        self.calls.append(query.model_dump())
        return _dmf_result(query.queries[0].ingredients)


def test_document_review_extracts_without_querying_dmf(tmp_path, monkeypatch) -> None:
    calls: list[dict] = []
    service = FakeDocumentService(calls)
    monkeypatch.setattr(graph_module, "understand_request", _intent())
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)

    graph = graph_module.build_research_graph()
    result = graph.invoke(
        {
            "user_query": "分析上传的文档",
            "document_artifacts": _documents(tmp_path),
            "warnings": [],
        }
    )

    assert "Ibuprofen" in result["final_answer"]
    assert calls == []


@pytest.mark.parametrize(
    ("resume", "expected_ingredients", "expected_answer"),
    [
        (
            {
                "action": "confirm",
                "query": {"queries": [{"ingredients": ["Ibuprofen"]}]},
            },
            ["Ibuprofen"],
            "12345",
        ),
        (
            {
                "action": "edit",
                "query": {"queries": [{"ingredients": ["Naproxen"]}]},
            },
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
        _intent(query=True),
    )
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)

    graph = graph_module.build_research_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": f"document-{resume['action']}"}}
    paused = graph.invoke(
        {
            "user_query": "分析上传文档并查询 DMF",
                "document_artifacts": _documents(tmp_path),
            "warnings": [],
        },
        config=config,
    )

    assert paused["__interrupt__"][0].value["type"] == "document_query_confirmation"
    assert paused["__interrupt__"][0].value["query"]["queries"][0]["ingredients"] == ["Ibuprofen"]
    assert calls == []

    completed = graph.invoke(Command(resume=resume), config=config)

    assert expected_answer in completed["final_answer"]
    if expected_ingredients is None:
        assert calls == []
    else:
        assert [call["queries"][0]["ingredients"] for call in calls] == [expected_ingredients]


def test_document_query_confirms_all_extracted_rows_as_one_batch(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[dict] = []

    class BatchDocumentService(FakeDocumentService):
        def extract_query(self, artifact) -> ExtractedDMFQueryBatch:
            return ExtractedDMFQueryBatch.model_validate(
                {
                    "queries": [
                        {"dmf_no": "234", "ingredients": ["Ibuprofen"]},
                        {"dmf_no": "211", "ingredients": ["NOT"]},
                        {"ingredients": ["Ibanez"]},
                    ]
                }
            )

    service = BatchDocumentService(calls)
    monkeypatch.setattr(
        graph_module,
        "understand_request",
        _intent(query=True),
    )
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)
    graph = graph_module.build_research_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "document-batch"}}

    paused = graph.invoke(
        {
            "user_query": "分析上传文档并查询 DMF",
            "document_artifacts": _documents(tmp_path),
            "warnings": [],
        },
        config=config,
    )
    queries = paused["__interrupt__"][0].value["query"]["queries"]

    assert [query["dmf_no"] for query in queries] == ["234", "211", ""]
    assert calls == []

    completed = graph.invoke(
        Command(resume={"action": "confirm", "query": {"queries": queries}}),
        config=config,
    )

    assert completed["dmf_results"]["success"] is True
    assert len(calls) == 1
    assert len(calls[0]["queries"]) == 3


def test_multi_document_query_extracts_independently_and_executes_deduplicated_batch(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[dict] = []
    extracted: list[str] = []
    first = _artifact(tmp_path)
    second = {
        **first,
        "document_id": "doc-2",
        "file_name": "second.xlsx",
    }

    class MultiDocumentService(FakeDocumentService):
        def extract_query(self, artifact) -> ExtractedDMFQueryBatch:
            extracted.append(artifact["document_id"])
            ingredients = (
                ["Ibuprofen", "Aspirin"]
                if artifact["document_id"] == "doc-1"
                else ["aspirin", "IBUPROFEN"]
            )
            return ExtractedDMFQueryBatch.model_validate(
                {"dmf_no": "123", "ingredients": ingredients}
            )

    service = MultiDocumentService(calls)
    monkeypatch.setattr(graph_module, "understand_request", _intent(query=True))
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)
    graph = graph_module.build_research_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "multi-document"}}

    paused = graph.invoke(
        {
            "user_query": "联合查询这两个文档",
            "document_artifacts": {"doc-1": first, "doc-2": second},
            "warnings": [],
        },
        config=config,
    )

    query = paused["__interrupt__"][0].value["query"]["queries"][0]
    assert extracted == ["doc-1", "doc-2"]
    assert len(paused["__interrupt__"][0].value["query"]["queries"]) == 1
    assert [source["document_id"] for source in query["sources"]] == [
        "doc-1",
        "doc-2",
    ]

    completed = graph.invoke(
        Command(
            resume={
                "action": "confirm",
                "query": {
                    "queries": [{
                        "dmf_no": query["dmf_no"],
                        "applicant_name": query["applicant_name"],
                        "ingredients": query["ingredients"],
                    }]
                },
            }
        ),
        config=config,
    )

    assert completed["dmf_results"]["success"] is True
    assert len(calls) == 1
    assert len(calls[0]["queries"]) == 1


def test_document_review_then_query_these_reuses_extracted_conditions(
    tmp_path,
    monkeypatch,
) -> None:
    calls: list[dict] = []
    extraction_calls = 0
    intent_messages = []

    class StructuredModel:
        def invoke(self, messages):
            intent_messages.append(messages)
            return ResearchIntent(
                data_source="document",
                use_existing_data=True,
                query_document_conditions=True,
                requested_outputs=["result"],
            )

    class FakeLlm:
        def with_structured_output(self, schema):
            assert schema is ResearchIntent
            return StructuredModel()

    class CountingDocumentService(FakeDocumentService):
        def extract_query(self, artifact) -> ExtractedDMFQueryBatch:
            nonlocal extraction_calls
            extraction_calls += 1
            return super().extract_query(artifact)

    service = CountingDocumentService(calls)

    def understand(state):
        if state["user_query"] == "分析上传的文档":
            return _intent()(state)
        return nodes.understand_request(state)

    monkeypatch.setattr(graph_module, "understand_request", understand)
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)
    monkeypatch.setattr(nodes, "create_apollo_llm", FakeLlm)
    graph = graph_module.build_research_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "document-followup"}}

    reviewed = graph.invoke(
        {
            "user_query": "分析上传的文档",
            "document_artifacts": _documents(tmp_path),
            "warnings": [],
        },
        config=config,
    )
    assert "Ibuprofen" in reviewed["final_answer"]

    paused = graph.invoke(
        {"user_query": "把这些逐一查询 DFM", "warnings": []},
        config=config,
    )

    assert paused["__interrupt__"][0].value["type"] == "document_query_confirmation"
    assert paused["__interrupt__"][0].value["query"]["queries"][0]["ingredients"] == [
        "Ibuprofen"
    ]
    assert extraction_calls == 1
    assert calls == []
    assert len(intent_messages) == 1
    assert '"available": true' in intent_messages[0][1].content
    assert '"ingredients": ["Ibuprofen"]' in intent_messages[0][1].content


def test_existing_document_conditions_are_reviewed_without_reextracting(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        graph_module,
        "understand_request",
        _intent(use_existing_data=True),
    )
    monkeypatch.setattr(
        nodes,
        "DocumentDMFService",
        lambda: (_ for _ in ()).throw(AssertionError("must not reextract")),
    )
    graph = graph_module.build_research_graph()

    result = graph.invoke(
        {
            "user_query": "展示上述条件",
            "merged_document_query": {
                "queries": [{
                    "ingredients": ["Ibuprofen"],
                    "sources": [{"document_id": "doc-1", "file_name": "sample.pdf"}],
                }],
            },
            "warnings": [],
        }
    )

    assert "Ibuprofen" in result["final_answer"]


def test_document_query_preserves_analysis_output(tmp_path, monkeypatch) -> None:
    calls: list[dict] = []
    service = FakeDocumentService(calls)
    monkeypatch.setattr(
        graph_module,
        "understand_request",
        _intent(query=True, requested_outputs=["analysis"]),
    )
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)
    monkeypatch.setattr(
        nodes,
        "_analyze_dmf_text",
        lambda state, result: "文档条件分析结果",
    )
    graph = graph_module.build_research_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "document-analysis"}}

    graph.invoke(
        {
            "user_query": "按文档条件查询并分析",
            "document_artifacts": _documents(tmp_path),
            "warnings": [],
        },
        config=config,
    )
    completed = graph.invoke(
        Command(
            resume={
                "action": "confirm",
                "query": {"queries": [{"ingredients": ["Ibuprofen"]}]},
            }
        ),
        config=config,
    )

    assert completed["requested_outputs"] == ["analysis"]
    assert completed["final_answer"] == "文档条件分析结果"
    assert len(calls) == 1


def test_document_query_preserves_export_output(tmp_path, monkeypatch) -> None:
    calls: list[dict] = []
    service = FakeDocumentService(calls)
    output_path = tmp_path / "document-query.xlsx"

    def fake_export(result, *, filename=None, output_dir=None):
        output_path.write_text("workbook", encoding="utf-8")
        return output_path

    monkeypatch.setattr(
        graph_module,
        "understand_request",
        _intent(query=True, requested_outputs=["result", "export"]),
    )
    monkeypatch.setattr(nodes, "DocumentDMFService", lambda: service)
    monkeypatch.setattr(nodes, "export_multi_query_result", fake_export)
    graph = graph_module.build_research_graph(checkpointer=InMemorySaver())
    config = {"configurable": {"thread_id": "document-export"}}

    graph.invoke(
        {
            "user_query": "按文档条件查询并导出",
            "document_artifacts": _documents(tmp_path),
            "warnings": [],
        },
        config=config,
    )
    completed = graph.invoke(
        Command(
            resume={
                "action": "confirm",
                "query": {"queries": [{"ingredients": ["Ibuprofen"]}]},
            }
        ),
        config=config,
    )

    assert completed["requested_outputs"] == ["result", "export"]
    assert output_path.exists()
    assert str(output_path) in completed["final_answer"]
    assert len(calls) == 1