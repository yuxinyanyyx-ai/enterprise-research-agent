import os
from pathlib import Path

import pytest

from src.llm.apollo import create_apollo_llm, model_name
from tests.agent_eval.assertions import assert_case_result
from tests.agent_eval.loader import load_cases
from tests.agent_eval.report import write_report
from tests.agent_eval.runner import AgentEvalRunner


pytestmark = [pytest.mark.agent_eval, pytest.mark.live_llm, pytest.mark.slow]
CASE_DIR = Path(__file__).parent / "agent_eval" / "cases"
LIVE_ENABLED = os.getenv("AGENT_EVAL_LIVE_LLM") == "1"
MAX_CASES = int(os.getenv("AGENT_EVAL_MAX_CASES", "0"))
LIVE_CASES = [case for case in load_cases(CASE_DIR) if "live" in case.modes]
if MAX_CASES > 0:
    LIVE_CASES = LIVE_CASES[:MAX_CASES]


@pytest.mark.skipif(not LIVE_ENABLED, reason="set AGENT_EVAL_LIVE_LLM=1")
@pytest.mark.parametrize("case", LIVE_CASES, ids=lambda case: case.id)
def test_agent_eval_live(case) -> None:
    with AgentEvalRunner(mode="live", llm_factory=create_apollo_llm) as runner:
        result = runner.run(case)

    assert_case_result(case, result)
    write_report(result, model=model_name())
