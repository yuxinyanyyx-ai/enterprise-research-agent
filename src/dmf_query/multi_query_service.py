"""High-level DMF search orchestration used by Agent tools and APIs."""

from __future__ import annotations

import logging

import requests

from src.schemas.dmf import (
    DMFQuery,
    DMFQueryResult,
    DMFSearchResult,
    DMFSingleQuery,
)

from .captcha_client import get_captcha
from .captcha_solver import MinerUCaptchaSolver
from .dmf_errors import CaptchaError
from .dmf_service import search_all_dmf

logger = logging.getLogger(__name__)


def clean_ingredients(ingredients: list[str] | None) -> list[str]:
    """Trim, remove blanks, and deduplicate ingredients while preserving order."""

    cleaned = [
        item.strip()
        for item in (ingredients or [])
        if item and item.strip()
    ]
    return list(dict.fromkeys(cleaned))


def _empty_search_result(
    *,
    message: str,
    dmf_no: str,
    applicant_name: str,
    ingredients: list[str],
) -> DMFSearchResult:
    return DMFSearchResult(
        success=False,
        message=message,
        query=DMFQuery(
            dmf_no=dmf_no,
            applicant_name=applicant_name,
            ingredients=ingredients,
        ),
    )


def search_dmf_queries(
    *,
    dmf_no: str = "",
    applicant_name: str = "",
    ingredients: list[str] | None = None,
) -> dict:
    """Execute a complete DMF search task and return a stable structured result.

    This is the business-service boundary used by Agent/API adapters. It owns
    captcha acquisition/recognition, session lifetime, multi-ingredient search,
    and aggregation. The Agent tool itself should remain a thin wrapper.
    """

    dmf_no = (dmf_no or "").strip()
    applicant_name = (applicant_name or "").strip()
    cleaned_ingredients = clean_ingredients(ingredients)

    if not any([dmf_no, applicant_name, cleaned_ingredients]):
        return _empty_search_result(
            message="DMF 编号、申请商名称、成分至少需要提供一个查询条件",
            dmf_no=dmf_no,
            applicant_name=applicant_name,
            ingredients=cleaned_ingredients,
        ).model_dump()

    # An empty ingredient still represents one valid DMF number/applicant query.
    query_ingredients = cleaned_ingredients or [""]
    session = requests.Session()

    try:
        try:
            captcha = get_captcha(session)
        except CaptchaError as exc:
            return _empty_search_result(
                message=f"验证码获取失败：{exc}",
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredients=cleaned_ingredients,
            ).model_dump()
        except Exception as exc:
            return _empty_search_result(
                message=f"验证码获取异常：{exc}",
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredients=cleaned_ingredients,
            ).model_dump()

        solver = MinerUCaptchaSolver()
        try:
            captcha_code = solver.solve(captcha["captcha_path"])
        except Exception as exc:
            return _empty_search_result(
                message=f"验证码识别失败：{exc}",
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredients=cleaned_ingredients,
            ).model_dump()

        verify_code = captcha["verify_code"]
        results: list[DMFQueryResult] = []
        success_count = 0
        failed_count = 0
        total_records = 0

        for ingredient in query_ingredients:
            query = DMFSingleQuery(
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredient=ingredient,
            )

            try:
                raw_result = search_all_dmf(
                    session=session,
                    captcha_code=captcha_code,
                    verify_code=verify_code,
                    dmf_no=dmf_no,
                    applicant_name=applicant_name,
                    ingredient=ingredient,
                )
            except Exception as exc:
                raw_result = {
                    "success": False,
                    "message": f"DMF 查询异常：{exc}",
                    "total": 0,
                    "total_pages": 0,
                    "records": [],
                }

            raw_result["query"] = query.model_dump()
            result = DMFQueryResult.model_validate(raw_result)
            results.append(result)

            if result.success:
                success_count += 1
                total_records += result.total
            else:
                failed_count += 1
                if "驗證碼" in result.message or "验证码" in result.message:
                    break

        if failed_count == 0:
            success = True
            message = "全部查询完成"
        elif success_count > 0:
            success = False
            message = "部分查询失败"
        else:
            success = False
            message = "查询失败"

        return DMFSearchResult(
            success=success,
            message=message,
            query=DMFQuery(
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredients=cleaned_ingredients,
            ),
            query_count=len(results),
            success_count=success_count,
            failed_count=failed_count,
            total_records=total_records,
            results=results,
        ).model_dump()
    finally:
        session.close()


def search_multiple_ingredients(
    session,
    *,
    ingredients: list[str],
    captcha_code: str,
    verify_code: str,
) -> dict:
    """Backward-compatible batch search with caller-managed captcha/session."""

    cleaned_ingredients = clean_ingredients(ingredients)
    if not cleaned_ingredients:
        return {
            "success": False,
            "captcha_error": False,
            "message": "至少需要提供一个成分。",
            "query_count": 0,
            "success_count": 0,
            "failed_count": 0,
            "total_records": 0,
            "results": [],
        }

    results = []
    success_count = 0
    failed_count = 0
    total_records = 0
    total_queries = len(cleaned_ingredients)

    for index, ingredient in enumerate(cleaned_ingredients, start=1):
        logger.info("正在查询 %s/%s：%s", index, total_queries, ingredient)
        result = search_all_dmf(
            session=session,
            captcha_code=captcha_code,
            verify_code=verify_code,
            ingredient=ingredient,
        )
        result["query"] = {
            "dmf_no": "",
            "applicant_name": "",
            "ingredient": ingredient,
        }
        results.append(result)

        if result["success"]:
            success_count += 1
            record_count = len(result.get("records", []))
            total_records += record_count
            logger.info("%s 查询完成，共 %s 条", ingredient, record_count)
            continue

        failed_count += 1
        error_message = result.get("message") or ""
        logger.warning("%s 查询失败：%s", ingredient, error_message)

        if "驗證碼" in error_message or "验证码" in error_message:
            logger.error("检测到验证码错误，停止后续批量查询。")
            return {
                "success": False,
                "captcha_error": True,
                "message": "验证码错误，请重新获取验证码后再试。",
                "query_count": total_queries,
                "success_count": success_count,
                "failed_count": failed_count,
                "total_records": total_records,
                "results": results,
            }

    return {
        "success": failed_count == 0,
        "captcha_error": False,
        "message": "全部查询完成" if failed_count == 0 else "部分查询失败",
        "query_count": total_queries,
        "success_count": success_count,
        "failed_count": failed_count,
        "total_records": total_records,
        "results": results,
    }