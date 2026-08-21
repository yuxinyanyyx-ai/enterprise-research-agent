import os
from pathlib import Path

import requests
from dotenv import load_dotenv


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


def load_workflow_config() -> tuple[str, str]:
    """加载 Apollo Studio Workflow 配置，不回显 API key。"""
    if not ENV_PATH.is_file():
        raise RuntimeError(f"环境配置文件不存在: {ENV_PATH}")

    load_dotenv(ENV_PATH)
    base_url = os.getenv("APOLLO_STUDIO_BASE_URL", "").strip().rstrip("/")
    api_key = os.getenv("APOLLO_STUDIO_WORKFLOW_API_KEY", "").strip()

    missing = [
        name
        for name, value in (
            ("APOLLO_STUDIO_BASE_URL", base_url),
            ("APOLLO_STUDIO_WORKFLOW_API_KEY", api_key),
        )
        if not value
    ]
    if missing:
        existing_apollo_hint = (
            " APOLLO_BASE_URL 是 LLM 接口配置，不能替代 Studio Workflow 地址。"
            if os.getenv("APOLLO_BASE_URL")
            else ""
        )
        raise RuntimeError(
            f"{ENV_PATH} 缺少 Workflow 配置: {', '.join(missing)}。"
            "请从 Apollo Studio 应用的“访问 API”页面获取地址和 API Key。"
            f"{existing_apollo_hint}"
        )

    return base_url, api_key


def main() -> None:
    base_url, api_key = load_workflow_config()
    url = f"{base_url}/workflows/run"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "inputs": {
            "markdown": "原料Ibuprofen",
        },
        "response_mode": "blocking",
        "user": "dmf-agent-local",
    }

    response = requests.post(
    url,
    headers=headers,
    json=payload,
    timeout=60,
    verify=False,
)
    response.raise_for_status()
    print(response.json())


if __name__ == "__main__":
    main()