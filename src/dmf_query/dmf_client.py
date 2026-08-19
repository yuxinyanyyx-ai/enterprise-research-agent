import json

import requests

from .constant import SEARCH_URL, REQUEST_TIMEOUT
from .dmf_errors import DmfClientError


def search_dmf_page(
    session: requests.Session,
    *,
    captcha_code: str,
    verify_code: str,
    dmf_no: str = "",
    applicant_name: str = "",
    ingredient: str = "",
    page: int = 1,
    page_size: int = 10,
    debug: bool = False,
):
    """
    调用台湾 FDA DMF 查询接口，查询指定分页。

    参数：
    - captcha_code: 用户输入的图片验证码
    - verify_code: imageCode 接口返回的验证 token
    - dmf_no: DMF 编号
    - applicant_name: 申请商名称
    - ingredient: 成分
    - page: 当前页
    - page_size: 每页数量
    """

    payload = {
        "data": {
            "dmfNo": dmf_no,
            "applicantName": applicant_name,
            "ingredientsDesc": ingredient,
            "code": {
                "code": captcha_code,
                "verifyCode": verify_code,
                "pageChange": True,
            },
        },
        "page": {
            "page": page,
            "pageSize": page_size,
        },
    }

    # 调试模式下可以查看请求结构
    # 验证码和 verifyCode 不直接输出
    if debug:
        safe_payload = {
            "data": {
                "dmfNo": dmf_no,
                "applicantName": applicant_name,
                "ingredientsDesc": ingredient,
                "code": {
                    "code": "***",
                    "verifyCode": "***",
                    "pageChange": True,
                },
            },
            "page": {
                "page": page,
                "pageSize": page_size,
            },
        }

        print("\n========== Request ==========")
        print(
            json.dumps(
                safe_payload,
                ensure_ascii=False,
                indent=2,
            )
        )

    try:
        response = session.post(
            SEARCH_URL,
            json=payload,
            timeout=REQUEST_TIMEOUT,
        )

    except requests.Timeout as exc:
        raise DmfClientError(
            "DMF 查询接口请求超时。"
        ) from exc

    except requests.ConnectionError as exc:
        raise DmfClientError(
            "无法连接 DMF 查询网站。"
        ) from exc

    except requests.RequestException as exc:
        raise DmfClientError(
            f"DMF 查询请求发生异常：{exc}"
        ) from exc

    if debug:
        print(
            "HTTP 状态码：",
            response.status_code,
        )

    # HTTP 状态异常
    if not response.ok:
        try:
            error_data = response.json()
            message = error_data.get("message")
        except ValueError:
            message = response.text

        raise DmfClientError(
            f"DMF 查询失败（HTTP {response.status_code}）："
            f"{message or '服务器未返回错误说明'}"
        )

    # JSON 解析
    try:
        return response.json()

    except ValueError as exc:
        raise DmfClientError(
            "DMF 网站返回的数据不是有效 JSON。"
        ) from exc