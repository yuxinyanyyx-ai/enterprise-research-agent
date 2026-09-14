import os

import pytest

from src.llm.apollo import create_apollo_llm
from tests.agent_eval.real_watchlist import RealWatchlistEvalRunner


pytestmark = [
    pytest.mark.agent_eval,
    pytest.mark.live_llm,
    pytest.mark.live_dmf,
    pytest.mark.real_watchlist,
    pytest.mark.slow,
]
REAL_ENABLED = all(
    os.getenv(name) == "1"
    for name in (
        "AGENT_EVAL_WATCHLIST_REAL",
        "AGENT_EVAL_LIVE_LLM",
        "AGENT_EVAL_LIVE_DMF",
    )
)


@pytest.mark.skipif(
    not REAL_ENABLED,
    reason=(
        "set AGENT_EVAL_WATCHLIST_REAL=1, AGENT_EVAL_LIVE_LLM=1, "
        "and AGENT_EVAL_LIVE_DMF=1"
    ),
)
def test_real_watchlist_integration() -> None:
    dmf_no = os.getenv("AGENT_EVAL_WATCHLIST_DMF_NO", "").strip()
    if not dmf_no:
        pytest.fail("set AGENT_EVAL_WATCHLIST_DMF_NO to one real, exact DMF number")

    with RealWatchlistEvalRunner(
        dmf_no=dmf_no,
        llm_factory=create_apollo_llm,
    ) as runner:
        result = runner.run()

    report_path = result.write_report()
    print(f"Real Watchlist Eval report: {report_path}")
    assert result.database_revision == "c42f81a6d903"
    assert len(result.turns) == 7