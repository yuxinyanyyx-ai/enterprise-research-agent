from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4


class ArtifactNotFound(FileNotFoundError):
    """The requested tool artifact does not exist."""


class ArtifactAccessError(PermissionError):
    """The artifact does not belong to the requested request."""


def _artifact_root() -> Path:
    configured = os.getenv("AGENT_ARTIFACT_DIR")
    root = Path(configured) if configured else Path(__file__).resolve().parents[2] / "outputs" / "agent" / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def _safe_id(value: str) -> str:
    if not value or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for character in value):
        raise ValueError("Invalid artifact id")
    return value


def store_artifact(result: Any, *, request_id: str, tool_name: str, tool_call_id: str) -> dict[str, Any]:
    artifact_id = uuid4().hex
    payload = json.dumps(
        {"__artifact_request_id": request_id, "value": result},
        ensure_ascii=False,
        default=str,
        separators=(",", ":"),
    )
    root = _artifact_root()
    path = root / f"{artifact_id}.json"
    path.write_text(payload, encoding="utf-8")
    return {
        "artifact_id": artifact_id,
        "request_id": request_id,
        "tool_name": tool_name,
        "tool_call_id": tool_call_id,
        "size_bytes": len(payload.encode("utf-8")),
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "storage": "filesystem",
    }


def load_artifact(artifact_id: str, *, request_id: str) -> Any:
    artifact_id = _safe_id(artifact_id)
    root = _artifact_root()
    path = (root / f"{artifact_id}.json").resolve()
    if path.parent != root:
        raise ArtifactAccessError("Invalid artifact path")
    if not path.is_file():
        raise ArtifactNotFound(artifact_id)
    # The request binding is stored alongside the payload to prevent cross-request reads.
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and payload.get("__artifact_request_id") == request_id:
        return payload.get("value")
    raise ArtifactAccessError("Artifact is not available for this request")
