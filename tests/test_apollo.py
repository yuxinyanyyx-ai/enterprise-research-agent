from __future__ import annotations

from pathlib import Path

import pytest

from src.llm import apollo


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"access_token": "token"}


def test_access_token_uses_configured_ssl_verification(monkeypatch) -> None:
    monkeypatch.setenv("APOLLO_CLIENT_ID", "client")
    monkeypatch.setenv("APOLLO_CLIENT_SECRET", "secret")
    monkeypatch.setenv("APOLLO_TOKEN_URL", "https://apollo.example/token")
    monkeypatch.setenv("APOLLO_VERIFY_SSL", "false")
    request_options = {}

    def fake_post(url, **kwargs):
        request_options.update(kwargs)
        return FakeResponse()

    monkeypatch.setattr(apollo.requests, "post", fake_post)

    assert apollo.get_access_token() == "token"
    assert request_options["verify"] is False


def test_create_llm_uses_same_ssl_setting_for_http_clients(monkeypatch) -> None:
    monkeypatch.setenv("APOLLO_BASE_URL", "https://apollo.example/llm")
    monkeypatch.setenv("APOLLO_VERIFY_SSL", "false")
    client_settings = []
    model_settings = {}

    monkeypatch.setattr(
        apollo.httpx,
        "Client",
        lambda **kwargs: client_settings.append(kwargs) or object(),
    )
    monkeypatch.setattr(
        apollo.httpx,
        "AsyncClient",
        lambda **kwargs: client_settings.append(kwargs) or object(),
    )
    monkeypatch.setattr(
        apollo,
        "ChatOpenAI",
        lambda **kwargs: model_settings.update(kwargs) or object(),
    )

    apollo.create_apollo_llm(access_token="token")

    assert client_settings == [{"verify": False}, {"verify": False}]
    assert model_settings["http_client"] is not None
    assert model_settings["http_async_client"] is not None


def test_ssl_verification_accepts_ca_bundle_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    ca_bundle = tmp_path / "company-ca.pem"
    ca_bundle.write_text("certificate", encoding="utf-8")
    monkeypatch.setenv("APOLLO_VERIFY_SSL", str(ca_bundle))

    assert apollo._ssl_verify_setting() == str(ca_bundle.resolve())


def test_ssl_verification_rejects_missing_ca_bundle(monkeypatch) -> None:
    monkeypatch.setenv("APOLLO_VERIFY_SSL", "missing-company-ca.pem")

    with pytest.raises(apollo.ApolloConfigurationError, match="CA 文件不存在"):
        apollo._ssl_verify_setting()