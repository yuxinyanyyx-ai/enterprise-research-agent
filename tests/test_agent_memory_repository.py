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
        memory_key=" output_format ",
        content={"value": "table"},
        idempotency_key="request-1",
    )
    same_request = repository.remember(
        tenant_id="tenant-a",
        user_id="user-a",
        memory_key="output_format",
        content={"value": "ignored"},
        idempotency_key="request-1",
    )
    updated = repository.remember(
        tenant_id="tenant-a",
        user_id="user-a",
        memory_key="output_format",
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
        memory_key="salutation",
        content={"value": "A"},
    )

    assert repository.forget(
        tenant_id="tenant-a", user_id="user-a", memory_key="salutation"
    ) is True
    assert repository.list_active(tenant_id="tenant-a", user_id="user-a") == []
    with repository.session_factory() as session:
        memory = session.query(UserMemory).one()
        assert memory.status == "deleted"


def test_memory_key_is_normalized_and_unknown_keys_are_rejected(tmp_path):
    repository = make_repository(tmp_path)

    memory = repository.remember(
        tenant_id="tenant-a",
        user_id="user-a",
        memory_key=" LANGUAGE ",
        content={"value": "zh-CN"},
    )

    assert memory.memory_key == "language"
    for invalid_key in ("", "name", "language.value"):
        try:
            repository.remember(
                tenant_id="tenant-a",
                user_id="user-a",
                memory_key=invalid_key,
                content={"value": "x"},
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid key to fail: {invalid_key!r}")


def test_memory_content_rejects_sensitive_fields_and_excessive_depth(tmp_path):
    repository = make_repository(tmp_path)

    for content in (
        {"password": "do-not-store"},
        {"nested": {"api_key": "do-not-store"}},
        {"nested": {"a": {"b": {"c": {"d": "too-deep"}}}}},
    ):
        try:
            repository.remember(
                tenant_id="tenant-a",
                user_id="user-a",
                memory_key="language",
                content=content,
            )
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected invalid content to fail: {content!r}")