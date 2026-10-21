import json
from pathlib import Path

import pytest

from tests.agent_eval.report import write_report, write_summary_report
from tests.agent_eval.runner import CaseResult, TurnResult


@pytest.mark.agent_eval
def test_report_redacts_secrets_and_omits_raw_call_log(tmp_path: Path) -> None:
    result = CaseResult(
        case_id="REPORT-001",
        mode="live",
        turns=[
            TurnResult(
                index=0,
                state={},
                executed_nodes=["prepare_react_request", "react_agent", "finalize"],
                pending_nodes=[],
                interrupted=False,
                answer=(
                    "Authorization: Bearer secret-token Cookie=session-secret "
                    "captcha=1234 DMF answer"
                ),
                call_deltas={"llm": 2, "dmf": 0, "document_extract": 0, "export": 0},
                call_log=[{"raw_payload": "must-not-be-written"}],
            )
        ],
    )

    output_dir = write_report(result, model="test-model", output_root=tmp_path)
    json_text = (output_dir / "report.json").read_text(encoding="utf-8")
    markdown_text = (output_dir / "report.md").read_text(encoding="utf-8")
    payload = json.loads(json_text)

    assert payload["case_id"] == "REPORT-001"
    assert "secret-token" not in json_text + markdown_text
    assert "session-secret" not in json_text + markdown_text
    assert "1234" not in json_text + markdown_text
    assert "must-not-be-written" not in json_text + markdown_text
    assert "DMF answer" in json_text + markdown_text


@pytest.mark.agent_eval
def test_summary_reports_success_selection_latency_tokens_and_failures(tmp_path: Path) -> None:
    successful = CaseResult(
        case_id="METRIC-001",
        mode="live",
        task_success=True,
        turns=[TurnResult(
            index=0,
            state={},
            executed_nodes=["react_agent", "finalize"],
            pending_nodes=[],
            interrupted=False,
            answer="ok",
            call_deltas={"llm": 2, "dmf": 1, "document_extract": 0, "export": 0, "watchlist": 0},
            call_log=[{"prompt": "private prompt"}],
            duration_ms=100,
            expected_tool_decisions=[["search_dmf"], []],
            selected_tool_decisions=[["search_dmf"], []],
            llm_metrics=[{
                "duration_ms": 20,
                "input_tokens": 30,
                "output_tokens": 8,
                "token_source": "actual",
            }],
        )],
    )
    failed = CaseResult(
        case_id="METRIC-002",
        mode="live",
        task_success=False,
        failure_reason="evaluation_assertion_failure",
        turns=[TurnResult(
            index=0,
            state={},
            executed_nodes=["react_agent", "finalize"],
            pending_nodes=[],
            interrupted=False,
            answer="failed",
            call_deltas={"llm": 1, "dmf": 0, "document_extract": 0, "export": 0, "watchlist": 0},
            call_log=[],
            duration_ms=200,
            expected_tool_decisions=[["run_watchlist_workflow"]],
            selected_tool_decisions=[["search_dmf"]],
            llm_metrics=[{
                "duration_ms": 40,
                "input_tokens": 10,
                "output_tokens": 5,
                "token_source": "estimated",
            }],
        )],
    )

    output_dir = write_summary_report(
        [successful, failed], model="test-model", output_root=tmp_path
    )
    json_text = (output_dir / "metrics.json").read_text(encoding="utf-8")
    markdown_text = (output_dir / "metrics.md").read_text(encoding="utf-8")
    payload = json.loads(json_text)

    assert payload["metrics"]["task_success_rate"] == 0.5
    assert payload["metrics"]["tool_selection"]["correct_decisions"] == 2
    assert payload["metrics"]["tool_selection"]["total_decisions"] == 3
    assert payload["metrics"]["latency_ms"] == {
        "request_p50": 100,
        "request_p95": 200,
        "llm_p50": 20,
        "llm_p95": 40,
    }
    assert payload["metrics"]["token_consumption"]["total"] == 53
    assert payload["metrics"]["tool_call_counts"]["dmf"] == 1
    assert payload["metrics"]["failure_reasons"] == {
        "evaluation_assertion_failure": 1
    }
    assert "private prompt" not in json_text + markdown_text
