import pytest

from src.agent import nodes, routes
from src.schemas.intent import ResearchIntent


def _fake_result() -> dict:
    return {
        "success": True,
        "message": "ok",
        "total_records": 1,
        "query_count": 1,
        "success_count": 1,
        "failed_count": 0,
        "results": [
            {
                "records": [
                    {
                        "dmf_no": "12345",
                        "applicant_name": "Example Pharma",
                        "ingredient": "Ibuprofen",
                        "valid_date": "2026-01-01",
                    }
                ]
            }
        ],
    }


def _document_artifacts() -> dict:
    return {
        "doc-1": {
            "document_id": "doc-1",
            "file_name": "sample.pdf",
            "source_path": "sample.pdf",
            "markdown_path": "trusted/document.md",
            "status": "parsed",
        }
    }


def _merged_query(queries: list[dict]) -> dict:
    return {
        "queries": [
            {
                **query,
                "sources": [{"document_id": "doc-1", "file_name": "sample.pdf"}],
            }
            for query in queries
        ]
    }


def test_intent_supports_multiple_requested_outputs() -> None:
    intent = ResearchIntent(
        data_source="dmf",
        requested_outputs=["result", "summary"],
        ingredients=["Ibuprofen"],
    )

    assert intent.requested_outputs == ["result", "summary"]


def test_normalize_requested_outputs_deduplicates_and_orders() -> None:
    outputs = nodes._normalize_requested_outputs(
        True,
        ["summary", "result", "summary"],
    )

    assert outputs == ["result", "summary"]


def test_dmf_query_defaults_to_result_when_outputs_missing() -> None:
    outputs = nodes._normalize_requested_outputs(True, [])
    assert outputs == ["result"]


@pytest.mark.parametrize(
    ("state", "expected_route"),
    [
        ({"needs_clarification": True}, "clarify"),
        ({"data_source": "none"}, "general_chat"),
        (
            {"data_source": "dmf", "use_existing_data": False},
            "dmf_query",
        ),
        (
            {
                "data_source": "dmf",
                "use_existing_data": True,
                "requested_outputs": ["export"],
            },
            "dmf_export",
        ),
        (
            {"data_source": "dmf", "use_existing_data": True},
            "dmf_result_review",
        ),
        (
            {
                "data_source": "document",
                "use_existing_data": False,
                "query_document_conditions": False,
            },
            "document_review",
        ),
        (
            {
                "data_source": "document",
                "use_existing_data": False,
                "query_document_conditions": True,
            },
            "document_query",
        ),
        (
            {
                "data_source": "document",
                "use_existing_data": True,
                "query_document_conditions": False,
            },
            "document_existing_review",
        ),
        (
            {
                "data_source": "document",
                "use_existing_data": True,
                "query_document_conditions": True,
            },
            "document_followup",
        ),
    ],
)
def test_route_after_intent_uses_structured_fields(state, expected_route) -> None:
    assert routes.route_after_intent(state) == expected_route


def test_empty_request_uses_complete_intent_state_and_clarifies() -> None:
    result = nodes.understand_request({"user_query": ""})

    assert result["data_source"] == "none"
    assert result["use_existing_data"] is False
    assert result["query_document_conditions"] is False
    assert result["requested_outputs"] == []
    assert result["needs_clarification"] is True
    assert routes.route_after_intent(result) == "clarify"


def test_understand_request_uses_fixed_clarification_for_ambiguous_intent(monkeypatch) -> None:
    class StructuredModel:
        def invoke(self, messages):
            return ResearchIntent(
                data_source="dmf",
                needs_clarification=True,
                ingredients=["Ibuprofen"],
            )

    class FakeLlm:
        def with_structured_output(self, schema):
            assert schema is ResearchIntent
            return StructuredModel()

    monkeypatch.setattr(nodes, "create_apollo_llm", FakeLlm)

    result = nodes.understand_request(
        {
            "user_query": "我要查询 BI 相关资料，并和布洛芬做个对比",
        }
    )

    assert result["needs_clarification"] is True
    assert result["clarification_question"] == nodes.AMBIGUOUS_REQUEST_CLARIFICATION


def test_new_query_without_conditions_uses_fixed_clarification() -> None:
    result = nodes._validate_intent(
        ResearchIntent(data_source="dmf", requested_outputs=["result"]),
        {},
    )

    assert "task_type" not in result
    assert result["data_source"] == "dmf"
    assert result["needs_clarification"] is True
    assert result["clarification_question"] == nodes.DEFAULT_CLARIFICATION


def test_document_query_export_also_keeps_result_output() -> None:
    result = nodes._validate_intent(
        ResearchIntent(
            data_source="document",
            query_document_conditions=True,
            requested_outputs=["export"],
        ),
        {"document_artifacts": _document_artifacts()},
    )

    assert result["requested_outputs"] == ["result", "export"]


def test_active_document_prevents_redundant_upload_clarification(monkeypatch) -> None:
    class StructuredModel:
        def invoke(self, messages):
            return ResearchIntent(
                data_source="document",
                query_document_conditions=True,
                requested_outputs=["result"],
            )

    class FakeLlm:
        def with_structured_output(self, schema):
            return StructuredModel()

    monkeypatch.setattr(nodes, "create_apollo_llm", FakeLlm)

    result = nodes.understand_request(
        {
            "user_query": "分析上传文档并查询 DMF",
            "document_artifacts": _document_artifacts(),
        }
    )

    assert "task_type" not in result
    assert routes.route_after_intent(result) == "document_query"
    assert result["needs_clarification"] is False
    assert result["requested_outputs"] == ["result"]


def test_existing_result_analysis_uses_structured_llm(monkeypatch) -> None:
    calls = []

    class StructuredModel:
        def invoke(self, messages):
            calls.append(messages)
            return ResearchIntent(
                data_source="dmf",
                use_existing_data=True,
                requested_outputs=["analysis"],
            )

    class FakeLlm:
        def with_structured_output(self, schema):
            assert schema is ResearchIntent
            return StructuredModel()

    monkeypatch.setattr(nodes, "create_apollo_llm", FakeLlm)

    result = nodes.understand_request(
        {
            "user_query": "分析刚才的结果",
            "dmf_results": _fake_result(),
        }
    )

    assert len(calls) == 1
    assert "task_type" not in result
    assert routes.route_after_intent(result) == "dmf_result_review"
    assert result["requested_outputs"] == ["analysis"]
    assert result["needs_clarification"] is False


def test_extracted_document_query_followup_uses_structured_llm(monkeypatch) -> None:
    calls = []

    class StructuredModel:
        def invoke(self, messages):
            calls.append(messages)
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

    monkeypatch.setattr(nodes, "create_apollo_llm", FakeLlm)

    result = nodes.understand_request(
        {
            "user_query": "把这些逐一查询 DFM",
            "merged_document_query": _merged_query(
                [{"ingredients": ["Ibuprofen"]}]
            ),
        }
    )

    assert len(calls) == 1
    assert "task_type" not in result
    assert routes.route_after_intent(result) == "document_followup"
    assert result["requested_outputs"] == ["result"]
    assert result["needs_clarification"] is False


def test_document_contexts_are_sent_once_with_limited_preview(monkeypatch) -> None:
    calls = []

    class StructuredModel:
        def invoke(self, messages):
            calls.append(messages)
            return ResearchIntent(
                data_source="document",
                use_existing_data=True,
            )

    class FakeLlm:
        def with_structured_output(self, schema):
            return StructuredModel()

    monkeypatch.setattr(nodes, "create_apollo_llm", FakeLlm)
    queries = [
        {"ingredients": [f"Ingredient-{index}"]}
        for index in range(1, 5)
    ]

    nodes.understand_request(
        {
            "user_query": "展示上述条件",
            "document_artifacts": _document_artifacts(),
            "merged_document_query": _merged_query(queries),
        }
    )

    content = calls[0][1].content
    assert content.count("当前上传文档上下文：") == 1
    assert content.count("当前已提取文档条件上下文：") == 1
    assert '"query_count": 4' in content
    assert "Ingredient-3" in content
    assert "Ingredient-4" not in content


@pytest.mark.parametrize(
    ("user_query", "intent", "state", "expected_task"),
    [
        (
            "总结上述数据",
            ResearchIntent(
                data_source="dmf",
                use_existing_data=True,
                requested_outputs=["summary"],
            ),
            {"dmf_results": _fake_result()},
            "dmf_result_review",
        ),
        (
            "给我一个 xlsx",
            ResearchIntent(
                data_source="dmf",
                use_existing_data=True,
                requested_outputs=["export"],
            ),
            {"dmf_results": _fake_result()},
            "dmf_export",
        ),
        (
            "按上述提取条件查询",
            ResearchIntent(
                data_source="document",
                use_existing_data=True,
                query_document_conditions=True,
                requested_outputs=["result"],
            ),
            {"merged_document_query": _merged_query([{"ingredients": ["Ibuprofen"]}])},
            "document_followup",
        ),
        (
            "分析当前文件",
            ResearchIntent(data_source="document"),
            {"document_artifacts": _document_artifacts()},
            "document_review",
        ),
    ],
)
def test_semantic_expressions_use_structured_llm(
    monkeypatch,
    user_query,
    intent,
    state,
    expected_task,
) -> None:
    calls = []

    class StructuredModel:
        def invoke(self, messages):
            calls.append(messages)
            return intent

    class FakeLlm:
        def with_structured_output(self, schema):
            return StructuredModel()

    monkeypatch.setattr(nodes, "create_apollo_llm", FakeLlm)

    result = nodes.understand_request({"user_query": user_query, **state})

    assert len(calls) == 1
    assert user_query in calls[0][1].content
    assert "task_type" not in result
    assert routes.route_after_intent(result) == expected_task
    assert result["needs_clarification"] is False


def test_build_answer_result_only_does_not_call_llm() -> None:
    answer = nodes.build_dmf_answer(
        {
            "requested_outputs": ["result"],
            "dmf_results": _fake_result(),
        }
    )["final_answer"]

    assert "12345" in answer
    assert "Example Pharma" in answer
    assert "## 摘要" not in answer


def test_build_answer_explains_each_successful_empty_query() -> None:
    answer = nodes.build_dmf_answer(
        {
            "requested_outputs": ["result"],
            "dmf_results": {
                "success": True,
                "message": "批量查询完成",
                "results": [
                    {
                        "success": True,
                        "message": "查询成功",
                        "query": {
                            "dmf_no": "234",
                            "applicant_name": "",
                            "ingredient": "Ibuprofen",
                        },
                        "records": [],
                    },
                    {
                        "success": True,
                        "message": "查询成功",
                        "query": {
                            "dmf_no": "",
                            "applicant_name": "",
                            "ingredient": "Ibuprofen",
                        },
                        "records": [],
                    },
                ],
            },
        }
    )["final_answer"]

    assert "DMF 编号=234，成分=Ibuprofen" in answer
    assert "2. 成分=Ibuprofen：查询成功，返回 0 条记录" in answer


def test_build_answer_explains_query_failure_without_nested_results() -> None:
    answer = nodes.build_dmf_answer(
        {
            "requested_outputs": ["result"],
            "dmf_results": {
                "success": False,
                "message": "验证码获取异常：连接超时",
                "results": [],
            },
        }
    )["final_answer"]

    assert answer == "DMF 查询未完成：验证码获取异常：连接超时"


def test_build_answer_lists_unmatched_queries_after_matching_records() -> None:
    result = _fake_result()
    result.update(
        {
            "query_count": 4,
            "success_count": 3,
            "failed_count": 1,
            "results": [
                result["results"][0],
                {
                    "success": True,
                    "message": "查询成功",
                    "query": {
                        "dmf_no": "234",
                        "applicant_name": "",
                        "ingredient": "Ibuprofen",
                    },
                    "records": [],
                },
                {
                    "success": True,
                    "message": "查询成功",
                    "query": {
                        "dmf_no": "",
                        "applicant_name": "",
                        "ingredient": "Ibanez",
                    },
                    "records": [],
                },
                {
                    "success": False,
                    "message": "验证码错误",
                    "query": {
                        "dmf_no": "211",
                        "applicant_name": "",
                        "ingredient": "NOT",
                    },
                    "records": [],
                },
            ],
        }
    )

    answer = nodes.build_dmf_answer(
        {
            "requested_outputs": ["result"],
            "dmf_results": result,
        }
    )["final_answer"]

    assert "12345" in answer
    assert "未命中查询：" in answer
    assert "1. DMF 编号=234，成分=Ibuprofen：返回 0 条记录" in answer
    assert "2. 成分=Ibanez：返回 0 条记录" in answer
    assert "DMF 编号=211，成分=NOT：返回 0 条记录" not in answer
    assert "部分查询未完成：成功 3，失败 1" in answer


def test_build_answer_combines_result_and_summary(monkeypatch) -> None:
    monkeypatch.setattr(
        nodes,
        "_summarize_dmf_text",
        lambda result: "这是测试摘要。",
    )

    answer = nodes.build_dmf_answer(
        {
            "user_query": "查询 Ibuprofen 并总结",
            "requested_outputs": ["result", "summary"],
            "dmf_results": _fake_result(),
        }
    )["final_answer"]

    assert "## 查询结果" in answer
    assert "12345" in answer
    assert "## 摘要" in answer
    assert "这是测试摘要。" in answer
