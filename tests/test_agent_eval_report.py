import json
from pathlib import Path

import pytest

from tests.agent_eval.report import write_report
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
