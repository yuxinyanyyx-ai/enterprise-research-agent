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
