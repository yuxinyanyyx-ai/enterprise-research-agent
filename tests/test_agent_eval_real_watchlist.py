from pathlib import Path

import pytest

from tests.agent_eval.real_watchlist import RealWatchlistEvalRunner


@pytest.mark.agent_eval
def test_real_runner_migrates_and_removes_isolated_database(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_EVAL_WATCHLIST_REAL", "1")
    runner = RealWatchlistEvalRunner(dmf_no="DMF-TEST", llm_factory=lambda: None)
    sandbox = runner.sandbox
    database_path = runner.database_path

    with runner:
        assert database_path.is_file()
        assert runner._database_revision() == "e91a26b7c804"
        assert database_path.is_relative_to(sandbox)

    assert not sandbox.exists()
    assert not database_path.exists()


@pytest.mark.agent_eval
def test_real_runner_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_EVAL_WATCHLIST_REAL", raising=False)
    runner = RealWatchlistEvalRunner(dmf_no="DMF-TEST", llm_factory=lambda: None)
    sandbox = Path(runner.sandbox)

    with pytest.raises(RuntimeError, match="AGENT_EVAL_WATCHLIST_REAL"):
        runner.__enter__()

    runner._temporary_directory.cleanup()
    assert not sandbox.exists()