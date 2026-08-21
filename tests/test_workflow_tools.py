from __future__ import annotations

from typing import Any
from types import SimpleNamespace

from src.Apollo import workflow_client
from src.tools import workflow_tools


class FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_workflow_client_posts_markdown_and_returns_structured_output(
    monkeypatch,
) -> None:
    monkeypatch.setenv("APOLLO_STUDIO_BASE_URL", "https://studio.example.test/v1/")
    monkeypatch.setenv("APOLLO_STUDIO_WORKFLOW_API_KEY", "test-key")
    expected = {
        "dmf_no": "",
        "applicant_name": "",
        "ingredients": ["Ibuprofen"],
    }

    def fake_post(url, *, headers, json, timeout, verify):
        assert url == "https://studio.example.test/v1/workflows/run"
        assert headers["Authorization"] == "Bearer test-key"
        assert json == {
            "inputs": {"markdown": "原料 Ibuprofen"},
            "response_mode": "blocking",
            "user": "dmf-agent-local",
        }
        assert timeout == 60
        assert verify is False
        return FakeResponse(
            {
                "data": {
                    "status": "succeeded",
                    "outputs": {"structured_output": expected},
                }
            }
        )

    monkeypatch.setattr(workflow_client.requests, "post", fake_post)

    assert workflow_client.extract_dmf_query_params("原料 Ibuprofen") == expected


def test_registered_workflow_tool_delegates_to_client(monkeypatch) -> None:
    expected = {"ingredients": ["Ibuprofen"]}
    artifact = {
        "document_id": "doc-1",
        "file_name": "sample.md",
        "source_path": "sample.md",
        "markdown_path": "trusted/sample.md",
        "status": "parsed",
    }
    seen = []
    monkeypatch.setattr(
        workflow_tools,
        "DocumentDMFService",
        lambda: SimpleNamespace(
            extract_query=lambda value: (
                seen.append(value) or SimpleNamespace(model_dump=lambda: expected)
            )
        ),
    )

    result = workflow_tools.extract_document_dmf_params.invoke(
        {"document_artifact": artifact}
    )

    assert result == expected
    assert seen == [artifact]