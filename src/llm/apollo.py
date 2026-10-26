"""Apollo LLM 统一接入层。"""

from __future__ import annotations

import base64
import math
import os
import ssl
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import requests
import urllib3
from dotenv import load_dotenv
from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APIStatusError

from src.observability.tracing import TraceSpan, trace_span


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
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
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
    max_tokens: int | None = None,
    streaming: bool = False,
    timeout: float | tuple[float, float] | None = None,
    max_retries: int | None = None,
) -> ChatOpenAI:
    """创建可供 LangChain / LangGraph 使用的 Apollo LLM。"""

    token = access_token or get_access_token()

    base_url = _required_env("APOLLO_BASE_URL")
    verify = _httpx_verify(_ssl_verify_setting())

    options: dict[str, Any] = {
        "base_url": base_url,
        "api_key": token,
        "model": model or model_name(),
        "temperature": 0.1,
        "streaming": streaming,
        "http_client": httpx.Client(verify=verify),
        "http_async_client": httpx.AsyncClient(verify=verify),
    }
    if max_tokens is not None:
        options["max_tokens"] = max_tokens
    if timeout is not None:
        options["timeout"] = timeout
    options["max_retries"] = 0 if max_retries is None else max_retries
    return ChatOpenAI(
        **options,
    )


def ingest_llm_timeout() -> float:
    """Return the bounded timeout for long-running knowledge-ingestion calls."""
    raw_timeout = os.getenv("APOLLO_INGEST_TIMEOUT_SECONDS", "300")
    try:
        timeout = float(raw_timeout)
    except ValueError as exc:
        raise ApolloConfigurationError("APOLLO_INGEST_TIMEOUT_SECONDS 必须是正数") from exc
    if not math.isfinite(timeout) or timeout <= 0:
        raise ApolloConfigurationError("APOLLO_INGEST_TIMEOUT_SECONDS 必须是正数")
    return timeout


def invoke_with_gateway_retries(
    client: Any,
    messages: Any,
    *,
    retries: int = 2,
    on_retry: Callable[[int, int, Exception], None] | None = None,
    operation_name: str = "llm.invoke",
    component: str = "apollo.llm",
    trace_model_name: str | None = None,
    request_id: str | None = None,
    trace_id: str | None = None,
    parent_span_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Any:
    """Retry only transient gateway, connection, and timeout failures."""
    if isinstance(retries, bool) or not isinstance(retries, int) or retries < 0:
        raise ValueError("retries 必须是非负整数")
    resolved_model = trace_model_name or getattr(client, "model_name", None)
    if not resolved_model:
        resolved_model = getattr(client, "model", None) or model_name()
    with trace_span(
        operation_name,
        component,
        request_id=request_id,
        trace_id=trace_id,
        parent_span_id=parent_span_id,
        model_name=str(resolved_model),
        metadata=metadata,
    ) as span:
        for attempt in range(retries + 1):
            span.metadata["attempt_count"] = attempt + 1
            try:
                response = client.invoke(messages)
                _record_llm_usage(span, response)
                return response
            except (APIConnectionError, APIStatusError) as exc:
                if isinstance(exc, APIStatusError):
                    span.metadata["http_status"] = exc.status_code
                    span.metadata.pop("provider_error_code", None)
                    if isinstance(exc.code, str) and exc.code in {
                        "context_length_exceeded", "invalid_request_error",
                        "invalid_parameter", "invalid_value", "unsupported_parameter",
                        "invalid_tool_call", "tool_use_failed", "rate_limit_exceeded",
                    }:
                        span.metadata["provider_error_code"] = exc.code
                retryable = isinstance(exc, APIConnectionError) or (
                    exc.status_code in {408, 409, 429} or exc.status_code >= 500
                )
                if not retryable or attempt == retries:
                    raise
                span.retry_count += 1
                if on_retry is not None:
                    on_retry(attempt + 1, retries, exc)
                time.sleep(min(2**attempt, 8))
        raise RuntimeError("Apollo 调用重试状态异常")


def _usage_number(source: Any, *keys: str) -> int | None:
    if not isinstance(source, dict):
        return None
    for key in keys:
        value = source.get(key)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return None


def _record_llm_usage(span: TraceSpan, response: Any) -> None:
    raw_response = response.get("raw") if isinstance(response, dict) else response
    usage = getattr(raw_response, "usage_metadata", None)
    if not isinstance(usage, dict):
        response_metadata = getattr(raw_response, "response_metadata", None)
        usage = (
            response_metadata.get("token_usage", response_metadata.get("usage"))
            if isinstance(response_metadata, dict)
            else None
        )
    input_tokens = _usage_number(usage, "input_tokens", "prompt_tokens")
    output_tokens = _usage_number(usage, "output_tokens", "completion_tokens")
    total_tokens = _usage_number(usage, "total_tokens")
    if (
        output_tokens is None
        and total_tokens is not None
        and input_tokens is not None
        and total_tokens >= input_tokens
    ):
        output_tokens = total_tokens - input_tokens
    span.set_usage(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        total_tokens=total_tokens,
        source="actual",
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
    model = _required_env("APOLLO_EMBEDDING_MODEL")
    with trace_span(
        "embedding.embed",
        "apollo.embedding",
        model_name=model,
        metadata={"input_count": len(texts)},
    ) as span:
        token = access_token or get_access_token()
        payload = _post_json(
            _endpoint(_required_env("APOLLO_BASE_URL"), "embeddings"),
            {"model": model, "input": texts},
            token,
        )
        _record_embedding_usage(span, payload)
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
        span.metadata["vector_dimension"] = dimension
        return [vector for vector in ordered if vector is not None]


def _record_embedding_usage(span: TraceSpan, payload: dict[str, Any]) -> None:
    usage = payload.get("usage")
    input_tokens = _usage_number(usage, "input_tokens", "prompt_tokens")
    total_tokens = _usage_number(usage, "total_tokens")
    span.set_usage(input_tokens=input_tokens, total_tokens=total_tokens, source="actual")


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
    model = _required_env("APOLLO_RERANK_MODEL")
    with trace_span(
        "rerank.documents",
        "apollo.rerank",
        model_name=model,
        metadata={"input_count": len(documents), "top_n": min(top_n, len(documents))},
    ) as span:
        token = access_token or get_access_token()
        rerank_url = (os.getenv("APOLLO_RERANK_URL") or "").strip() or _endpoint(
            _required_env("APOLLO_BASE_URL"),
            "rerank",
        )
        payload = _post_json(
            rerank_url,
            {
                "model": model,
                "query": query,
                "documents": documents,
                "top_n": min(top_n, len(documents)),
            },
            token,
        )
        _record_embedding_usage(span, payload)
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
        span.metadata["result_count"] = len(normalized)
        return normalized[: min(top_n, len(documents))]


def describe_image(
    image: bytes,
    *,
    mime_type: str,
    access_token: str | None = None,
) -> str:
    """Extract searchable content from one uploaded image."""
    if not image:
        raise ValueError("image 不能为空")
    if not mime_type.startswith("image/"):
        raise ValueError("mime_type 必须是 image/*")
    data_url = f"data:{mime_type};base64,{base64.b64encode(image).decode('ascii')}"
    llm = create_apollo_llm(
        access_token,
        model=(
            os.getenv("APOLLO_VISION_MODEL")
            or model_name()
        ).strip(),
    )
    response = invoke_with_gateway_retries(llm, [
        HumanMessage(content=[
            {
                "type": "text",
                "text": (
                   "你的任务是为用户上传的单张图片建立可检索内容，不要概括或遗漏可读信息。\n"
                   "输出图片内文字和视觉关系；两类信息分别列出。\n"

                   "图片内文字：\n"
                   "- 转录图片、截图、扫描件、图片化表格和图表中的可读文字。\n"
                   "- 图片化表格必须保留表头与字段对应关系，每行单独转录为“表头：值”。\n"
                   "- 无法辨认的文字标记为[无法辨认]，不要猜测或补全。\n"

                   "视觉关系：\n"
                   "- 时间轴、pipeline、roadmap中的阶段关系、先后顺序和关键节点位置；\n"
                   "- Global/China、多泳道、多区域之间的对应关系；\n"
                   "- 流程图中的箭头方向、步骤关系和依赖关系；\n"
                   "- 图表中的趋势、比较关系和视觉编码；\n"
                   "- 颜色、标记、图标仅在承载业务含义时描述。\n"

                   "对于普通标题、页码、Logo、页脚、装饰性元素、状态灯等无业务含义内容不要描述。\n"

                   "如果没有可提取的图片文字或视觉关系，只返回：无视觉增量信息。"
                ),
            },
            {"type": "image_url", "image_url": {"url": data_url}},
        ], retries=2, operation_name="vision.describe_image", component="apollo.vision",
           trace_model_name=(os.getenv("APOLLO_VISION_MODEL") or model_name()).strip())
    ])
    content = response.content
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = [item.get("text", "") for item in content if isinstance(item, dict)]
        return "\n".join(part for part in parts if part).strip()
    raise RuntimeError("Apollo Vision 返回无法解析的内容")