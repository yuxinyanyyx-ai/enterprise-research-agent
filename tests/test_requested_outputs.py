import pytest
from pydantic import ValidationError

from src.agent.nodes import _format_dmf_result_text
from src.schemas.domain_intent import DocumentIntent, WatchlistIntent
from tests.test_react_composition import result_data


def test_formatter_preserves_records_without_llm():
    answer = _format_dmf_result_text(result_data())
    assert "123" in answer and "Ibuprofen" in answer
    assert "DMF 编号" in answer


def test_formatter_explains_each_empty_query():
    result = result_data()
    result["results"][0]["records"] = []
    result["results"].append({"success": True, "message": "ok", "query": {"dmf_no": "234", "ingredient": "Ibuprofen"}, "records": []})
    answer = _format_dmf_result_text(result)
    assert "DMF 编号=234，成分=Ibuprofen" in answer
    assert answer.count("返回 0 条记录") == 2


def test_formatter_explains_failed_query_without_records():
    answer = _format_dmf_result_text({"success": False, "message": "连接超时", "results": []})
    assert answer == "DMF 查询未完成：连接超时"


def test_formatter_distinguishes_unmatched_from_failed_query():
    result = result_data()
    result["results"].extend([
        {"success": True, "query": {"ingredient": "Naproxen"}, "records": []},
        {"success": False, "message": "captcha failed", "query": {"ingredient": "Aspirin"}, "records": []},
    ])
    answer = _format_dmf_result_text(result)
    assert "Naproxen：返回 0 条记录" in answer
    assert "Aspirin：返回 0 条记录" not in answer


def test_formatter_retains_history_without_raw_payload():
    result = result_data()
    result["results"][0]["history"] = {"added_count": 2, "removed_count": 1, "changed_count": 3,
                                      "warnings": ["缺少稳定身份"], "raw_payload": "must-not-render"}
    answer = _format_dmf_result_text(result)
    assert "新增 2 条，消失 1 条，字段变化 3 条" in answer
    assert "缺少稳定身份" in answer and "must-not-render" not in answer


@pytest.mark.parametrize("schema", [DocumentIntent, WatchlistIntent])
def test_domain_intents_do_not_accept_summary_analysis_or_export_orchestration(schema):
    with pytest.raises(ValidationError):
        schema.model_validate({"requested_outputs": ["summary", "analysis", "export"]})