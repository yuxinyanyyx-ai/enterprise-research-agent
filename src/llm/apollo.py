"""Apollo LLM 统一接入层。"""

from __future__ import annotations

import os
import ssl
from pathlib import Path

import httpx
import requests
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI


# src/llm/apollo.py
# parents[0] = llm
# parents[1] = src
# parents[2] = 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


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
) -> ChatOpenAI:
    """创建可供 LangChain / LangGraph 使用的 Apollo LLM。"""

    token = access_token or get_access_token()

    base_url = _required_env("APOLLO_BASE_URL")
    verify = _httpx_verify(_ssl_verify_setting())

    return ChatOpenAI(
        base_url=base_url,
        api_key=token,
        model=model_name(),
        temperature=0.1,
        http_client=httpx.Client(verify=verify),
        http_async_client=httpx.AsyncClient(verify=verify),
    )