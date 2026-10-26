from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_web_starts_without_provider_credentials_and_rejects_unconfigured_parser(tmp_path):
    env = {
        key: value for key, value in os.environ.items()
        if not key.startswith(("APOLLO_", "MINERU_", "DMF_", "AGENT_"))
    }
    env.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "PYTHONIOENCODING": "utf-8",
        "DATABASE_URL": f"sqlite:///{(tmp_path / 'sessions.db').as_posix()}",
        "UPLOAD_DIR": str(tmp_path / "uploads"),
        "RESULT_DIR": str(tmp_path / "results"),
        "TEMP_DIR": str(tmp_path / "temp"),
        "KNOWLEDGE_ROOT": str(tmp_path / "knowledge"),
        "AGENT_CHECKPOINT_ENABLED": "false",
        "AGENT_MEMORY_ENABLED": "false",
        "AGENT_TRUST_PROXY_IDENTITY": "false",
        "DMF_HISTORY_ENABLED": "false",
        "DMF_WATCHLIST_ENABLED": "false",
        "DMF_NOTIFICATION_ENABLED": "false",
    })
    code = """
import socket
from unittest.mock import patch
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from pathlib import Path
from src.observability import tracing
from src.agent import audit_log
import os
tracing.TRACE_DIR = Path(os.environ["TEMP_DIR"]) / "traces"
audit_log.LOG_DIR = Path(os.environ["TEMP_DIR"]) / "audit"
original_connect = socket.socket.connect
def local_connect(sock, address):
    if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
        return original_connect(sock, address)
    raise AssertionError("Unexpected external network request")
with patch.object(socket.socket, "connect", local_connect):
    command.upgrade(Config("alembic.ini"), "head")
    from src.main import app
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        session = client.post("/api/agent/sessions")
        assert session.status_code == 201
        response = client.post(
            "/api/agent/sessions/" + session.json()["session_id"] + "/messages",
            json={"message": "Hello"},
        )
        assert response.status_code == 503, response.text
        assert "尚未配置" in response.json()["detail"]
        response = client.post("/api/tasks", files={"files": ("sample.pdf", b"%PDF-test", "application/pdf")})
        assert response.status_code == 503, response.text
        assert "MINERU_TOKEN" in response.json()["detail"]
        response = client.get("/api/knowledge/status")
        assert response.status_code == 200, response.text
    from src.settings import ConfigurationError, get_settings
    try:
        get_settings()
    except ConfigurationError:
        pass
    else:
        raise AssertionError("Default settings must still require a real MinerU token")
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list((tmp_path / "uploads").glob("*"))


def test_compose_preserves_data_and_only_publishes_authenticated_proxy():
    config = yaml.safe_load((ROOT / "compose.yaml").read_text(encoding="utf-8"))
    services = config["services"]
    assert set(services) == {"app", "db", "proxy"}
    assert services["app"]["image"] == "enterprise-research-agent-app:latest"
    assert services["app"]["build"]["args"]["PYTHON_IMAGE"] == "${PYTHON_IMAGE:-python:3.12-slim-bookworm}"
    assert services["db"]["image"] == "${POSTGRES_IMAGE:-postgres:17-alpine}"
    assert services["proxy"]["image"] == "${CADDY_IMAGE:-caddy:2-alpine}"
    assert all("ports" not in services[name] for name in ("app", "db"))
    assert services["app"]["environment"]["AGENT_CHECKPOINT_ENABLED"] == "true"
    assert services["app"]["environment"]["AGENT_TRUST_PROXY_IDENTITY"] == "false"
    assert services["app"]["depends_on"]["db"]["condition"] == "service_healthy"
    assert services["proxy"]["depends_on"]["app"]["condition"] == "service_healthy"
    targets = {mount.split(":", 1)[1] for mount in services["app"]["volumes"]}
    assert targets == {"/app/src/storage", "/app/data", "/app/outputs", "/app/logs", "/app/PDF"}
    assert services["db"]["volumes"] == ["postgres_data:/var/lib/postgresql/data"]
    assert all("127.0.0.1" in port for port in services["proxy"]["ports"])
    caddy = (ROOT / "docker" / "Caddyfile").read_text(encoding="utf-8")
    assert "basic_auth {" in caddy
    assert "{$APP_PASSWORD_HASH}" in caddy
    assert "reverse_proxy app:8000" in caddy
    for header in ("Authorization", "X-Authenticated-User", "X-Authenticated-Tenant"):
        assert f"header_up -{header}" in caddy
    entrypoint = (ROOT / "docker" / "web-entrypoint.sh").read_text(encoding="utf-8")
    assert "set -eu" in entrypoint
    assert entrypoint.index("alembic upgrade head") < entrypoint.index("uvicorn src.main:app")
    assert "--workers 1" in entrypoint


def test_image_does_not_copy_private_runtime_data():
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert ignore[0] == "**"
    assert "src/storage/" in ignore
    assert "**/.env" in ignore
    allowed = {line for line in ignore if line.startswith("!")}
    assert not any(line.startswith(("!data", "!outputs", "!logs", "!.env", "!.git")) for line in allowed)
    dockerfile = (ROOT / "docker" / "Dockerfile.web").read_text(encoding="utf-8")
    assert "USER app" in dockerfile
    assert "COPY . " not in dockerfile
    assert "libreoffice-impress" in dockerfile


@pytest.mark.parametrize("configured", [False, True])
def test_docker_compose_config_validates_required_secrets(tmp_path, configured):
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker CLI is not installed")
    env_file = tmp_path / "compose.env"
    password_hash = "$2a$14$offline-test-hash"
    env_file.write_text(
        "POSTGRES_PASSWORD=" + ("a" * 48 if configured else "") + "\n"
        "APP_PASSWORD_HASH='" + (password_hash if configured else "") + "'\n",
        encoding="utf-8",
    )
    env = dict(os.environ)
    for key in ("POSTGRES_PASSWORD", "APP_PASSWORD_HASH"):
        env.pop(key, None)
    result = subprocess.run(
        [
            docker, "compose", "--env-file", str(env_file), "-f", str(ROOT / "compose.yaml"),
            "config", "--format", "json",
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
    )
    if configured:
        assert result.returncode == 0, result.stderr
        config = json.loads(result.stdout)
        # Canonical Compose output escapes dollars for reuse as a Compose file.
        assert config["services"]["proxy"]["environment"]["APP_PASSWORD_HASH"] == password_hash.replace("$", "$$")
        interpolation = subprocess.run(
            [
                docker, "compose", "--env-file", str(env_file), "-f", str(ROOT / "compose.yaml"),
                "config", "--environment",
            ],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30,
        )
        assert interpolation.returncode == 0, interpolation.stderr
        assert f"APP_PASSWORD_HASH={password_hash}" in interpolation.stdout.splitlines()
    else:
        assert result.returncode != 0
        assert "POSTGRES_PASSWORD" in result.stderr
