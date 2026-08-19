"""Apollo LLM 统一接入层。"""

from __future__ import annotations

import os
from pathlib import Path

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

    return ChatOpenAI(
        base_url=base_url,
        api_key=token,
        model=model_name(),
        temperature=0.1,
    )