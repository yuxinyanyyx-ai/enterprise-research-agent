import pytest


@pytest.fixture(autouse=True)
def isolated_agent_audit_log(tmp_path, monkeypatch):
    from src.agent import audit_log
    monkeypatch.setattr(audit_log, "LOG_DIR", tmp_path / "agent-logs")
    yield
    with audit_log._lock:
        if audit_log._handler is not None:
            audit_log._handler.close()
        audit_log._handler = None
        audit_log._handler_path = None