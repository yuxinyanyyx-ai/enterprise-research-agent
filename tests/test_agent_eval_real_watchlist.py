import logging
from pathlib import Path

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from src.settings import PROJECT_ROOT
from tests.agent_eval.real_watchlist import RealWatchlistEvalRunner


@pytest.mark.agent_eval
def test_real_runner_migrates_and_removes_isolated_database(monkeypatch, caplog) -> None:
    monkeypatch.setenv("AGENT_EVAL_WATCHLIST_REAL", "1")
    runner = RealWatchlistEvalRunner(dmf_no="DMF-TEST", llm_factory=lambda: None)
    sandbox = runner.sandbox
    database_path = runner.database_path
    head = ScriptDirectory.from_config(Config(str(PROJECT_ROOT / "alembic.ini"))).get_current_head()
    assert head is not None
    loggers = (logging.getLogger(), logging.getLogger("src.agent.nodes"))
    logging_states = [(logger.disabled, logger.level, logger.propagate, tuple(logger.handlers)) for logger in loggers]
    assert caplog.handler in loggers[0].handlers

    with runner:
        assert database_path.is_file()
        assert runner._database_revision() == head
        assert database_path.is_relative_to(sandbox)
        assert [(logger.disabled, logger.level, logger.propagate, tuple(logger.handlers)) for logger in loggers] == logging_states

    assert not sandbox.exists()
    assert not database_path.exists()
    assert [(logger.disabled, logger.level, logger.propagate, tuple(logger.handlers)) for logger in loggers] == logging_states


@pytest.mark.agent_eval
def test_real_runner_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_EVAL_WATCHLIST_REAL", raising=False)
    runner = RealWatchlistEvalRunner(dmf_no="DMF-TEST", llm_factory=lambda: None)
    sandbox = Path(runner.sandbox)

    with pytest.raises(RuntimeError, match="AGENT_EVAL_WATCHLIST_REAL"):
        runner.__enter__()

    runner._temporary_directory.cleanup()
    assert not sandbox.exists()