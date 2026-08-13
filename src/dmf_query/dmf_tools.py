"""DMF Agent Tools.


"""

from __future__ import annotations

import requests
from langchain_core.tools import tool

from .captcha_client import get_captcha
from .captcha_solver import MinerUCaptchaSolver
from .dmf_errors import CaptchaError
from .dmf_service import search_all_dmf


def _clean_ingredients(
    ingredients: list[str] | None,
) -> list[str]:
    """清洗成分列表：去空值、去首尾空格、去重并保持顺序。"""

    cleaned = [
        item.strip()
        for item in (ingredients or [])
        if item and item.strip()
    ]

    return list(dict.fromkeys(cleaned))


def _build_error_result(
    *,
    message: str,
    dmf_no: str,
    applicant_name: str,
    ingredients: list[str],
) -> dict:
    """构造统一失败返回格式。"""

    return {
        "success": False,
        "message": message,
        "query": {
            "dmf_no": dmf_no,
            "applicant_name": applicant_name,
            "ingredients": ingredients,
        },
        "query_count": 0,
        "success_count": 0,
        "failed_count": 0,
        "total_records": 0,
        "results": [],
    }


@tool(
    "search_dmf",
    description=(
        "查询 DMF（Drug Master File）数据。"
        "支持按 DMF 编号、申请商名称、一个或多个成分查询。"
        "工具会自动处理验证码、MinerU 识别和分页查询，"
        "并返回完整结构化 DMF 记录。"
    ),
)
def search_dmf(
    dmf_no: str = "",
    applicant_name: str = "",
    ingredients: list[str] | None = None,
) -> dict:


    dmf_no = (dmf_no or "").strip()
    applicant_name = (applicant_name or "").strip()
    ingredients = _clean_ingredients(ingredients)


    if not any(
        [
            dmf_no,
            applicant_name,
            ingredients,
        ]
    ):
        return _build_error_result(
            message=(
                "DMF 编号、申请商名称、成分至少需要提供一个查询条件"
            ),
            dmf_no=dmf_no,
            applicant_name=applicant_name,
            ingredients=ingredients,
        )

    # 没有成分时，也要执行一次 DMF 编号/申请商查询。
    query_ingredients = ingredients if ingredients else [""]

    session = requests.Session()

    try:

        try:
            captcha = get_captcha(session)
        except CaptchaError as exc:
            return _build_error_result(
                message=f"验证码获取失败：{exc}",
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredients=ingredients,
            )
        except Exception as exc:
            return _build_error_result(
                message=f"验证码获取异常：{exc}",
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredients=ingredients,
            )


        solver = MinerUCaptchaSolver()

        try:
            captcha_code = solver.solve(
                captcha["captcha_path"]
            )
        except Exception as exc:
            return _build_error_result(
                message=f"验证码识别失败：{exc}",
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredients=ingredients,
            )

        verify_code = captcha["verify_code"]


        results: list[dict] = []
        success_count = 0
        failed_count = 0
        total_records = 0

        for ingredient in query_ingredients:
            try:
                result = search_all_dmf(
                    session=session,
                    captcha_code=captcha_code,
                    verify_code=verify_code,
                    dmf_no=dmf_no,
                    applicant_name=applicant_name,
                    ingredient=ingredient,
                )
            except Exception as exc:
                result = {
                    "success": False,
                    "message": f"DMF 查询异常：{exc}",
                    "total": 0,
                    "total_pages": 0,
                    "records": [],
                }

            # 统一记录每次实际使用的查询条件
            result["query"] = {
                "dmf_no": dmf_no,
                "applicant_name": applicant_name,
                "ingredient": ingredient,
            }

            results.append(result)

            if result.get("success"):
                success_count += 1
                total_records += int(
                    result.get("total", 0) or 0
                )
            else:
                failed_count += 1

                # 验证码错误后，继续查询其他成分没有意义。
                message = str(
                    result.get("message", "")
                )

                if (
                    "驗證碼" in message
                    or "验证码" in message
                ):
                    break

        query_count = len(results)

        if failed_count == 0:
            success = True
            message = "全部查询完成"
        elif success_count > 0:
            success = False
            message = "部分查询失败"
        else:
            success = False
            message = "查询失败"

        return {
            "success": success,
            "message": message,
            "query": {
                "dmf_no": dmf_no,
                "applicant_name": applicant_name,
                "ingredients": ingredients,
            },
            "query_count": query_count,
            "success_count": success_count,
            "failed_count": failed_count,
            "total_records": total_records,
            "results": results,
        }

    finally:
        session.close()
