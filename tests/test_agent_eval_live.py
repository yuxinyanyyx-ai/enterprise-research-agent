import os
from pathlib import Path

import pytest

from src.llm.apollo import create_apollo_llm, model_name
from tests.agent_eval.assertions import assert_case_result
from tests.agent_eval.fakes import UnexpectedBoundaryCall
from tests.agent_eval.loader import load_cases
from tests.agent_eval.reliability_cases import reliability_cases
from tests.agent_eval.report import write_report, write_summary_report
from tests.agent_eval.runner import AgentEvalRunner, CaseResult


pytestmark = [pytest.mark.agent_eval, pytest.mark.live_llm, pytest.mark.slow]
CASE_DIR = Path(__file__).parent / "agent_eval" / "cases"
LIVE_ENABLED = os.getenv("AGENT_EVAL_LIVE_LLM") == "1"
MAX_CASES = int(os.getenv("AGENT_EVAL_MAX_CASES", "0"))
LIVE_CASES = [
    case
    for case in [*load_cases(CASE_DIR), *reliability_cases()]
    if "live" in case.modes and case.profile == "standard"
]
if MAX_CASES > 0:
    LIVE_CASES = LIVE_CASES[:MAX_CASES]


@pytest.fixture(scope="session")
def live_eval_results():
    results: list[CaseResult] = []
    yield results
    if results:
        write_summary_report(results, model=model_name())


def _failure_reason(error: Exception) -> str:
    if isinstance(error, UnexpectedBoundaryCall):
        return "unexpected_boundary_call"
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, AssertionError):
        return "evaluation_assertion_failure"
    return "execution_error"


@pytest.mark.skipif(not LIVE_ENABLED, reason="set AGENT_EVAL_LIVE_LLM=1")
@pytest.mark.parametrize("case", LIVE_CASES, ids=lambda case: case.id)
def test_agent_eval_live(case, live_eval_results: list[CaseResult]) -> None:
    result: CaseResult | None = None
    try:
        with AgentEvalRunner(mode="live", llm_factory=create_apollo_llm) as runner:
            result = runner.run(case)
        assert_case_result(case, result)
    except Exception as exc:
        if result is None:
            result = CaseResult(case_id=case.id, mode="live")
        result.task_success = False
        result.failure_reason = result.failure_reason or _failure_reason(exc)
        live_eval_results.append(result)
        write_report(result, model=model_name())
        raise
    live_eval_results.append(result)
    write_report(result, model=model_name())
