from src.agent_memory.models import UserMemory
from src.agent_memory.repository import MemoryRepository


def make_repository(tmp_path):
    repository = MemoryRepository(f"sqlite:///{tmp_path / 'memory.db'}")
    repository.initialize_schema()
    return repository


def test_explicit_memory_is_persistent_and_scoped(tmp_path):
    repository = make_repository(tmp_path)
    repository.remember(
        tenant_id="tenant-a",
        user_id="user-a",
        memory_key="language",
        content={"value": "zh-CN"},
        idempotency_key="request-1",
    )

    reopened = MemoryRepository(f"sqlite:///{tmp_path / 'memory.db'}")
    assert reopened.list_active(tenant_id="tenant-a", user_id="user-a")[0].content == {
        "value": "zh-CN"
    }
    assert reopened.list_active(tenant_id="tenant-a", user_id="user-b") == []
    assert reopened.list_active(tenant_id="tenant-b", user_id="user-a") == []


def test_remember_updates_and_idempotency_does_not_duplicate(tmp_path):
    repository = make_repository(tmp_path)
    first = repository.remember(
        tenant_id="tenant-a",
        user_id="user-a",
        memory_key="format",
        content={"value": "table"},
        idempotency_key="request-1",
    )
    same_request = repository.remember(
        tenant_id="tenant-a",
        user_id="user-a",
        memory_key="format",
        content={"value": "ignored"},
        idempotency_key="request-1",
    )
    updated = repository.remember(
        tenant_id="tenant-a",
        user_id="user-a",
        memory_key="format",
        content={"value": "bullets"},
        idempotency_key="request-2",
    )

    assert same_request.id == first.id
    assert updated.id == first.id
    assert updated.version == 2
    assert repository.list_active(tenant_id="tenant-a", user_id="user-a")[0].content == {
        "value": "bullets"
    }


def test_idempotency_key_is_scoped_to_user(tmp_path):
    repository = make_repository(tmp_path)
    repository.remember(
        tenant_id="tenant-a",
        user_id="user-a",
        memory_key="language",
        content={"value": "zh-CN"},
        idempotency_key="same-request-id",
    )

    other_user = repository.remember(
        tenant_id="tenant-a",
        user_id="user-b",
        memory_key="language",
        content={"value": "en-US"},
        idempotency_key="same-request-id",
    )

    assert other_user.content == {"value": "en-US"}
    assert repository.list_active(tenant_id="tenant-a", user_id="user-a")[0].content == {
        "value": "zh-CN"
    }


def test_forget_soft_deletes_memory(tmp_path):
    repository = make_repository(tmp_path)
    repository.remember(
        tenant_id="tenant-a",
        user_id="user-a",
        memory_key="name",
        content={"value": "A"},
    )

    assert repository.forget(
        tenant_id="tenant-a", user_id="user-a", memory_key="name"
    ) is True
    assert repository.list_active(tenant_id="tenant-a", user_id="user-a") == []
    with repository.session_factory() as session:
        memory = session.query(UserMemory).one()
        assert memory.status == "deleted"