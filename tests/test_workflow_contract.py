import pytest
from pydantic import ValidationError

from src.schemas.workflow import WorkflowResult


@pytest.mark.parametrize("status", ["completed", "needs_clarification", "cancelled", "failed", "rejected"])
def test_only_completed_workflow_can_continue(status):
    result = WorkflowResult(workflow_name="document", status=status)
    assert result.can_continue is (status == "completed")


def test_workflow_status_is_not_an_arbitrary_success_string():
    with pytest.raises(ValidationError):
        WorkflowResult(workflow_name="watchlist", status="returned")


def test_workflow_data_is_not_shared_between_instances():
    first = WorkflowResult(workflow_name="document", status="completed")
    second = WorkflowResult(workflow_name="document", status="completed")
    first.data["query"] = "example"
    assert second.data == {}


def test_document_subgraph_projects_only_output(monkeypatch):
    from src.agent import domain_workflows
    monkeypatch.setattr(domain_workflows, "_parse", lambda state, domain: {
        "needs_clarification": True, "clarification_question": "upload document",
    })
    result = domain_workflows.build_document_dmf_workflow().invoke({"workflow_input": {
        "user_query": "document", "react_tool_rounds": 99, "confirmed_dmf_query": {"injected": True},
    }})
    assert set(result) == {"workflow_output"}
    output = result["workflow_output"]
    assert output["result"]["status"] == "needs_clarification"
    assert "confirmed_dmf_query" not in output["updates"]
    assert "messages" not in output["updates"]


def test_child_initialization_does_not_reuse_missing_bridge_fields():
    from src.agent.domain_workflows import _initialize
    result = _initialize({"workflow_input": {"user_query": "new request"},
                          "pending_intent": {"dmf_no": "old"}, "operation_ledger": {"old": True},
                          "document_artifacts": {"old": {}}}, "watchlist")
    assert result["pending_intent"] == {}
    assert result["operation_ledger"] == {}
    assert result["document_artifacts"] == {}