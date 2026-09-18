"""Apollo LLM 统一接入层。"""

from __future__ import annotations

import base64
import math
import os
import ssl
from pathlib import Path
from typing import Any

import httpx
import requests
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI


# src/llm/apollo.py
# parents[0] = llm
# parents[1] = src
# parents[2] = 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]

def load_env() -> None:
    """Load local Apollo settings without overriding deployment variables."""
    load_dotenv(PROJECT_ROOT / ".env", override=False)


load_env()


class ApolloConfigurationError(RuntimeError):
    """Apollo 配置缺失或不合法。"""


def _required_env(name: str) -> str:
    """读取必需环境变量。"""

    value = (os.getenv(name) or "").strip()

    if not value:
        raise ApolloConfigurationError(
            f"未配置环境变量 {name}"
        )

    return value


def _ssl_verify_setting() -> bool | str:
    """返回 Apollo HTTPS 校验配置。"""

    raw_value = (os.getenv("APOLLO_VERIFY_SSL") or "true").strip()
    normalized = raw_value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False

    ca_bundle = Path(raw_value).expanduser()
    if not ca_bundle.is_absolute():
        ca_bundle = PROJECT_ROOT / ca_bundle
    ca_bundle = ca_bundle.resolve()
    if not ca_bundle.is_file():
        raise ApolloConfigurationError(
            f"APOLLO_VERIFY_SSL 指定的 CA 文件不存在: {ca_bundle}"
        )
    return str(ca_bundle)


def _httpx_verify(verify: bool | str) -> bool | ssl.SSLContext:
    if isinstance(verify, bool):
        return verify
    return ssl.create_default_context(cafile=verify)


def get_access_token() -> str:
    """通过 OAuth2 client_credentials 获取 Apollo access token。"""

    client_id = _required_env("APOLLO_CLIENT_ID")
    client_secret = _required_env("APOLLO_CLIENT_SECRET")
    token_url = _required_env("APOLLO_TOKEN_URL")

    response = requests.post(
        token_url,
        data={
            "grant_type": "client_credentials",
            "client_id": client_id,
            "client_secret": client_secret,
        },
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        timeout=30,
        verify=_ssl_verify_setting(),
    )

    response.raise_for_status()

    payload = response.json()

    access_token = payload.get("access_token")

    if not access_token:
        raise RuntimeError(
            f"Apollo Token 响应中不存在 access_token: {payload}"
        )

    return str(access_token)


def model_name() -> str:
    """返回当前 Apollo LLM 模型名称。"""

    return (
        os.getenv("APOLLO_MODEL")
        or "claude_4_6_sonnet"
    ).strip()


def create_apollo_llm(
    access_token: str | None = None,
    *,
    model: str | None = None,
) -> ChatOpenAI:
    """创建可供 LangChain / LangGraph 使用的 Apollo LLM。"""

    token = access_token or get_access_token()

    base_url = _required_env("APOLLO_BASE_URL")
    verify = _httpx_verify(_ssl_verify_setting())

    return ChatOpenAI(
        base_url=base_url,
        api_key=token,
        model=model or model_name(),
        temperature=0.1,
        http_client=httpx.Client(verify=verify),
        http_async_client=httpx.AsyncClient(verify=verify),
    )


def _bearer_headers(access_token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _post_json(url: str, payload: dict[str, Any], access_token: str) -> dict[str, Any]:
    response = requests.post(
        url,
        headers=_bearer_headers(access_token),
        json=payload,
        timeout=60,
        verify=_ssl_verify_setting(),
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("Apollo 响应必须是 JSON 对象")
    return data


def _endpoint(base_url: str, path: str) -> str:
    normalized = base_url.rstrip("/")
    suffix = f"/{path.lstrip('/')}"
    return normalized if normalized.endswith(suffix) else normalized + suffix


def embed_texts(
    texts: list[str],
    access_token: str | None = None,
) -> list[list[float]]:
    """Embed text through Apollo's OpenAI-compatible endpoint."""
    if not texts:
        return []
    if any(not isinstance(text, str) or not text.strip() for text in texts):
        raise ValueError("embedding 输入必须是非空字符串")
    token = access_token or get_access_token()
    payload = _post_json(
        _endpoint(_required_env("APOLLO_BASE_URL"), "embeddings"),
        {"model": _required_env("APOLLO_EMBEDDING_MODEL"), "input": texts},
        token,
    )
    rows = payload.get("data")
    if not isinstance(rows, list) or len(rows) != len(texts):
        raise RuntimeError("Apollo embedding 返回数量与输入不一致")
    ordered: list[list[float] | None] = [None] * len(texts)
    dimension: int | None = None
    for position, row in enumerate(rows):
        if not isinstance(row, dict):
            raise RuntimeError("Apollo embedding 返回项格式错误")
        index = row.get("index", position)
        vector = row.get("embedding")
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(texts):
            raise RuntimeError("Apollo embedding 返回非法 index")
        if ordered[index] is not None or not isinstance(vector, list) or not vector:
            raise RuntimeError("Apollo embedding 返回重复 index 或空向量")
        try:
            normalized = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Apollo embedding 向量包含非数值") from exc
        if any(not math.isfinite(value) for value in normalized):
            raise RuntimeError("Apollo embedding 向量包含非有限值")
        dimension = dimension or len(normalized)
        if len(normalized) != dimension:
            raise RuntimeError("Apollo embedding 向量维度不一致")
        ordered[index] = normalized
    if any(vector is None for vector in ordered):
        raise RuntimeError("Apollo embedding 返回缺失 index")
    return [vector for vector in ordered if vector is not None]


def rerank_documents(
    query: str,
    documents: list[str],
    *,
    top_n: int,
    access_token: str | None = None,
) -> list[dict[str, float | int]]:
    """Rerank documents through the configured Apollo rerank endpoint."""
    if not documents:
        return []
    if not query.strip():
        raise ValueError("rerank query 不能为空")
    if top_n < 1:
        raise ValueError("top_n 必须为正整数")
    token = access_token or get_access_token()
    rerank_url = (os.getenv("APOLLO_RERANK_URL") or "").strip() or _endpoint(
        _required_env("APOLLO_BASE_URL"),
        "rerank",
    )
    payload = _post_json(
        rerank_url,
        {
            "model": _required_env("APOLLO_RERANK_MODEL"),
            "query": query,
            "documents": documents,
            "top_n": min(top_n, len(documents)),
        },
        token,
    )
    rows = payload.get("results", payload.get("data"))
    if not isinstance(rows, list):
        raise RuntimeError("Apollo rerank 响应缺少 results")
    normalized: list[dict[str, float | int]] = []
    seen: set[int] = set()
    for row in rows:
        if not isinstance(row, dict):
            raise RuntimeError("Apollo rerank 返回项格式错误")
        index = row.get("index")
        score = row.get("relevance_score", row.get("score"))
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < len(documents):
            raise RuntimeError("Apollo rerank 返回非法 index")
        if index in seen:
            raise RuntimeError("Apollo rerank 返回重复 index")
        try:
            relevance = float(score)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("Apollo rerank 返回非法分数") from exc
        if not math.isfinite(relevance):
            raise RuntimeError("Apollo rerank 返回非有限分数")
        seen.add(index)
        normalized.append({"index": index, "relevance_score": relevance})
    normalized.sort(key=lambda item: float(item["relevance_score"]), reverse=True)
    return normalized[: min(top_n, len(documents))]


def describe_image(
    image: bytes,
    *,
    mime_type: str,
    access_token: str | None = None,
) -> str:
    """Extract searchable meeting content from one image."""
    if not image:
        raise ValueError("image 不能为空")
    if not mime_type.startswith("image/"):
        raise ValueError("mime_type 必须是 image/*")
    data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
    llm = create_apollo_llm(
        access_token,
        model=(
            os.getenv("APOLLO_PEC_VISION_MODEL")
            or os.getenv("APOLLO_VISION_MODEL")
            or model_name()
        ).strip(),
    )
    response = llm.invoke([
        HumanMessage(content=[
            {
                "type": "text",
                "text": "提取该 PEC 会议页面中的文字、表格、决定和行动项。只描述可见内容，使用简洁中文。",
            },
            {"type": "image_url", "image_url": {"url": data_url}},
        ])
    ])
    content = response.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        return "\n".join(part for part in parts if part).strip()
    raise RuntimeError("Apollo Vision 返回无法解析的内容")