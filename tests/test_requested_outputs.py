from src.agent import nodes
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


def test_intent_supports_multiple_requested_outputs() -> None:
    intent = ResearchIntent(
        task_type="dmf_query",
        requested_outputs=["result", "summary"],
        ingredients=["Ibuprofen"],
    )

    assert intent.requested_outputs == ["result", "summary"]


def test_normalize_requested_outputs_deduplicates_and_orders() -> None:
    outputs = nodes._normalize_requested_outputs(
        "dmf_query",
        ["summary", "result", "summary"],
    )

    assert outputs == ["result", "summary"]


def test_dmf_query_defaults_to_result_when_outputs_missing() -> None:
    outputs = nodes._normalize_requested_outputs("dmf_query", [])
    assert outputs == ["result"]


def test_incomplete_compare_clarification_uses_fallback() -> None:
    answer = nodes.ask_clarification(
        {
            "task_type": "dmf_compare",
            "clarification_question": "您提到要查询",
        }
    )["final_answer"]

    assert "比较的成分、DMF 或申请商" in answer
    assert "比较的具体维度" in answer
    assert answer.endswith("？")


def test_incomplete_query_clarification_uses_default() -> None:
    answer = nodes.ask_clarification(
        {
            "task_type": "dmf_query",
            "clarification_question": "请提供",
        }
    )["final_answer"]

    assert answer == nodes.DEFAULT_CLARIFICATION


def test_valid_statement_clarification_is_preserved() -> None:
    question = "当前会话没有可导出的 DMF 查询结果，请先执行查询。"

    answer = nodes.ask_clarification(
        {
            "task_type": "dmf_post_process",
            "clarification_question": question,
        }
    )["final_answer"]

    assert answer == question


def test_understand_request_normalizes_incomplete_clarification(monkeypatch) -> None:
    class StructuredModel:
        def invoke(self, messages):
            return ResearchIntent(
                task_type="dmf_compare",
                needs_clarification=True,
                clarification_question="您提到要查询",
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
    assert "比较的具体维度" in result["clarification_question"]
    assert result["clarification_question"].endswith("？")


def test_active_document_prevents_redundant_upload_clarification(monkeypatch) -> None:
    class StructuredModel:
        def invoke(self, messages):
            return ResearchIntent(
                task_type="dmf_document_compare",
                needs_clarification=True,
                clarification_question="请提供文档。",
            )

    class FakeLlm:
        def with_structured_output(self, schema):
            return StructuredModel()

    monkeypatch.setattr(nodes, "create_apollo_llm", FakeLlm)

    result = nodes.understand_request(
        {
            "user_query": "分析上传文档并查询 DMF",
            "document_artifact": {
                "file_name": "sample.pdf",
                "markdown_path": "trusted/document.md",
                "status": "parsed",
            },
        }
    )

    assert result["task_type"] == "dmf_document_compare"
    assert result["needs_clarification"] is False
    assert result["requested_outputs"] == ["result"]


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
