"""Apollo Studio Workflow API 调用层。"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


# src/apollo_studio/workflow_client.py
# parents[0] = apollo_studio
# parents[1] = src
# parents[2] = 项目根目录
PROJECT_ROOT = Path(__file__).resolve().parents[2]

load_dotenv(PROJECT_ROOT / ".env")


class ApolloStudioWorkflowError(RuntimeError):
    """Apollo Studio Workflow 调用失败。"""


def _required_env(name: str) -> str:
    """读取必需环境变量。"""

    value = (os.getenv(name) or "").strip()

    if not value:
        raise ApolloStudioWorkflowError(
            f"未配置环境变量 {name}"
        )

    return value


def extract_dmf_query_params(
    markdown: str,
) -> dict[str, Any]:
    """
    调用 Apollo Studio Workflow，
    从 Markdown 中提取 DMF 查询条件。
    """

    if not markdown.strip():
        raise ValueError("markdown 不能为空")

    base_url = _required_env(
        "APOLLO_STUDIO_BASE_URL"
    ).rstrip("/")

    api_key = _required_env(
        "APOLLO_STUDIO_WORKFLOW_API_KEY"
    )

    response = requests.post(
        f"{base_url}/workflows/run",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "inputs": {
                "markdown": markdown,
            },
            "response_mode": "blocking",
            "user": "dmf-agent-local",
        },
        timeout=60,

        # 当前公司环境证书链暂时无法被 requests 验证。
        # 仅用于本地联调，部署前改为公司 CA 证书。
        verify=False,
    )

    response.raise_for_status()

    payload = response.json()

    data = payload.get("data")

    if not isinstance(data, dict):
        raise ApolloStudioWorkflowError(
            "Workflow 响应缺少 data"
        )

    if data.get("status") != "succeeded":
        raise ApolloStudioWorkflowError(
            str(
                data.get("error")
                or "Apollo Studio Workflow 执行失败"
            )
        )

    outputs = data.get("outputs")

    if not isinstance(outputs, dict):
        raise ApolloStudioWorkflowError(
            "Workflow 响应缺少 outputs"
        )

    structured_output = outputs.get(
        "structured_output"
    )

    if not isinstance(structured_output, dict):
        raise ApolloStudioWorkflowError(
            "Workflow 响应缺少 structured_output"
        )

    return structured_output