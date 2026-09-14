from pathlib import Path

import pytest

from tests.agent_eval.assertions import assert_case_result
from tests.agent_eval.fakes import CallLog, ScriptedLLMProvider, UnexpectedBoundaryCall
from tests.agent_eval.loader import load_cases
from tests.agent_eval.runner import AgentEvalRunner


CASE_DIR = Path(__file__).parent / "agent_eval" / "cases"
CASES = [case for case in load_cases(CASE_DIR) if "deterministic" in case.modes]


@pytest.mark.agent_eval
@pytest.mark.parametrize("case", CASES, ids=lambda case: case.id)
def test_agent_eval_case(case) -> None:
    with AgentEvalRunner() as runner:
        result = runner.run(case)

    assert_case_result(case, result)


@pytest.mark.agent_eval
def test_runner_executes_real_core_nodes() -> None:
    case = next(case for case in CASES if case.id == "DMF-001")

    with AgentEvalRunner() as runner:
        result = runner.run(case)

    assert result.turns[0].executed_nodes == [
        "prepare_react_request", "react_agent", "execute_function_tools", "react_agent", "finalize",
    ]


@pytest.mark.agent_eval
def test_scripted_llm_rejects_undeclared_calls() -> None:
    provider = ScriptedLLMProvider(CallLog())

    with pytest.raises(UnexpectedBoundaryCall, match="undeclared LLM text call"):
        provider.invoke([])


@pytest.mark.agent_eval
def test_repeated_case_runs_do_not_share_checkpoint_state() -> None:
    case = next(case for case in CASES if case.id == "DMF-001")

    with AgentEvalRunner() as runner:
        first = runner.run(case)
        second = runner.run(case)

    first_messages = first.turns[0].state["react_messages"]
    second_messages = second.turns[0].state["react_messages"]
    assert len(first_messages) == len(second_messages) == 4
