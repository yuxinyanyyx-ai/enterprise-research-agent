from __future__ import annotations

from pathlib import Path

import pytest

from src.llm import apollo


class FakeResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"access_token": "token"}


class PayloadResponse(FakeResponse):
    def __init__(self, payload) -> None:
        self.payload = payload

    def json(self):
        return self.payload


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


def test_embed_texts_uses_openai_contract_and_restores_order(monkeypatch) -> None:
    monkeypatch.setenv("APOLLO_BASE_URL", "https://apollo.example/v1/")
    monkeypatch.setenv("APOLLO_EMBEDDING_MODEL", "embedding-model")
    monkeypatch.setenv("APOLLO_VERIFY_SSL", "false")
    seen = {}

    def fake_post(url, **kwargs):
        seen.update({"url": url, **kwargs})
        return PayloadResponse({"data": [
            {"index": 1, "embedding": [3, 4]},
            {"index": 0, "embedding": [1, 2]},
        ]})

    monkeypatch.setattr(apollo.requests, "post", fake_post)
    result = apollo.embed_texts(["first", "second"], access_token="token")
    assert result == [[1.0, 2.0], [3.0, 4.0]]
    assert seen["url"] == "https://apollo.example/v1/embeddings"
    assert seen["json"] == {"model": "embedding-model", "input": ["first", "second"]}
    assert seen["headers"]["Authorization"] == "Bearer token"
    assert seen["verify"] is False


def test_embed_texts_rejects_inconsistent_dimensions(monkeypatch) -> None:
    monkeypatch.setenv("APOLLO_BASE_URL", "https://apollo.example/v1")
    monkeypatch.setenv("APOLLO_EMBEDDING_MODEL", "embedding-model")
    monkeypatch.setattr(apollo.requests, "post", lambda *args, **kwargs: PayloadResponse({"data": [
        {"index": 0, "embedding": [1]}, {"index": 1, "embedding": [2, 3]},
    ]}))
    with pytest.raises(RuntimeError, match="维度不一致"):
        apollo.embed_texts(["first", "second"], access_token="token")


def test_rerank_documents_normalizes_and_sorts_results(monkeypatch) -> None:
    monkeypatch.delenv("APOLLO_RERANK_URL", raising=False)
    monkeypatch.setenv("APOLLO_BASE_URL", "https://apollo.example/v1/")
    monkeypatch.setenv("APOLLO_RERANK_MODEL", "rerank-model")
    seen = {}

    def fake_post(url, **kwargs):
        seen.update({"url": url, **kwargs})
        return PayloadResponse({"results": [
            {"index": 0, "relevance_score": 0.2},
            {"index": 1, "score": 0.9},
        ]})

    monkeypatch.setattr(apollo.requests, "post", fake_post)
    result = apollo.rerank_documents("query", ["a", "b"], top_n=2, access_token="token")
    assert result == [
        {"index": 1, "relevance_score": 0.9},
        {"index": 0, "relevance_score": 0.2},
    ]
    assert seen["json"]["model"] == "rerank-model"
    assert seen["url"] == "https://apollo.example/v1/rerank"


def test_rerank_url_can_override_shared_base(monkeypatch) -> None:
    monkeypatch.setenv("APOLLO_BASE_URL", "https://apollo.example/v1")
    monkeypatch.setenv("APOLLO_RERANK_URL", "https://rerank.example/custom")
    monkeypatch.setenv("APOLLO_RERANK_MODEL", "rerank-model")
    seen = {}

    def fake_post(url, **kwargs):
        seen["url"] = url
        return PayloadResponse({"results": []})

    monkeypatch.setattr(apollo.requests, "post", fake_post)
    apollo.rerank_documents("query", ["a"], top_n=1, access_token="token")
    assert seen["url"] == "https://rerank.example/custom"


def test_rerank_documents_rejects_out_of_range_index(monkeypatch) -> None:
    monkeypatch.delenv("APOLLO_RERANK_URL", raising=False)
    monkeypatch.setenv("APOLLO_BASE_URL", "https://apollo.example/v1")
    monkeypatch.setenv("APOLLO_RERANK_MODEL", "rerank-model")
    monkeypatch.setattr(apollo.requests, "post", lambda *args, **kwargs: PayloadResponse({
        "results": [{"index": 2, "relevance_score": 1}],
    }))
    with pytest.raises(RuntimeError, match="非法 index"):
        apollo.rerank_documents("query", ["only"], top_n=1, access_token="token")


def test_describe_image_uses_vision_model_and_data_url(monkeypatch) -> None:
    monkeypatch.setenv("APOLLO_VISION_MODEL", "vision-model")
    seen = {}

    class FakeLlm:
        def invoke(self, messages):
            seen["messages"] = messages
            return type("Response", (), {"content": "  visible content  "})()

    def fake_create(token, *, model=None):
        seen.update({"token": token, "model": model})
        return FakeLlm()

    monkeypatch.setattr(apollo, "create_apollo_llm", fake_create)
    result = apollo.describe_image(b"image", mime_type="image/png", access_token="token")
    assert result == "visible content"
    assert seen["model"] == "vision-model"
    image_url = seen["messages"][0].content[1]["image_url"]["url"]
    assert image_url.startswith("data:image/png;base64,")


def test_describe_image_prefers_pec_vision_model(monkeypatch) -> None:
    monkeypatch.setenv("APOLLO_PEC_VISION_MODEL", "pec-vision-model")
    monkeypatch.setenv("APOLLO_VISION_MODEL", "vision-model")
    seen = {}

    class FakeLlm:
        def invoke(self, messages):
            return type("Response", (), {"content": "content"})()

    monkeypatch.setattr(
        apollo,
        "create_apollo_llm",
        lambda token, *, model=None: seen.update(model=model) or FakeLlm(),
    )

    apollo.describe_image(b"image", mime_type="image/png", access_token="token")

    assert seen["model"] == "pec-vision-model"