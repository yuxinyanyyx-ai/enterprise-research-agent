from src.agent_session.repository import AgentSessionRepository


def make_repository(tmp_path):
    repository = AgentSessionRepository(f"sqlite:///{tmp_path / 'agent.db'}")
    repository.initialize_schema()
    return repository


def test_session_metadata_is_persistent_and_scoped(tmp_path):
    repository = make_repository(tmp_path)
    repository.create(
        session_id="session-a",
        tenant_id="tenant-a",
        user_id="user-a",
    )

    reopened = AgentSessionRepository(f"sqlite:///{tmp_path / 'agent.db'}")
    session = reopened.get(
        "session-a",
        tenant_id="tenant-a",
        user_id="user-a",
    )

    assert session is not None
    assert session.awaiting_resume is False
    assert reopened.get("session-a", tenant_id="tenant-a", user_id="user-b") is None
    assert reopened.get("session-a", tenant_id="tenant-b", user_id="user-a") is None


def test_session_resume_flag_is_updated_without_graph_state_fields(tmp_path):
    repository = make_repository(tmp_path)
    repository.create(session_id="session-a")

    updated = repository.update_awaiting_resume(
        "session-a", awaiting_resume=True
    )

    assert updated is not None
    assert updated.awaiting_resume is True
    assert updated.updated_at != updated.created_at


def test_session_requires_an_id(tmp_path):
    repository = make_repository(tmp_path)

    try:
        repository.create(session_id="")
    except ValueError as exc:
        assert str(exc) == "session_id is required"
    else:
        raise AssertionError("expected empty session_id to fail")
