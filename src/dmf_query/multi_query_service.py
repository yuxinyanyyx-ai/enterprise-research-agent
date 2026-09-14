"""High-level DMF search orchestration used by Agent tools and APIs."""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import requests

from src.dmf_history import DMFHistoryRepository, get_configured_history_repository
from src.dmf_history.repository import (
    PUBLIC_HISTORY_ERROR_CODE,
    PUBLIC_HISTORY_ERROR_MESSAGE,
)
from src.schemas.dmf import (
    DMFCollectionStatus,
    DMFHistoryResult,
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


def _preflight_failure_result(
    *,
    message: str,
    dmf_no: str,
    applicant_name: str,
    ingredients: list[str],
    query_ingredients: list[str],
    history_repository: DMFHistoryRepository | None,
) -> DMFSearchResult:
    results = []
    for ingredient in query_ingredients:
        result = DMFQueryResult(
            success=False,
            message=message,
            query=DMFSingleQuery(
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredient=ingredient,
            ),
            collection_status=DMFCollectionStatus.NOT_EXECUTED,
        )
        if history_repository is not None:
            try:
                result.history = history_repository.record_query(result)
            except Exception as exc:
                logger.exception("DMF 前置失败 monitor run 写入失败")
                result.history = DMFHistoryResult(
                    history_status="monitor_run_failed",
                    comparison_status="failed",
                    history_error_code=PUBLIC_HISTORY_ERROR_CODE,
                    history_error=PUBLIC_HISTORY_ERROR_MESSAGE,
                )
        results.append(result)
    return DMFSearchResult(
        success=False,
        message=message,
        query=DMFQuery(
            dmf_no=dmf_no,
            applicant_name=applicant_name,
            ingredients=ingredients,
        ),
        query_count=len(results),
        not_executed_count=len(results),
        results=results,
    )


def search_dmf_queries(
    *,
    dmf_no: str = "",
    applicant_name: str = "",
    ingredients: list[str] | None = None,
    history_repository: DMFHistoryRepository | None = None,
) -> dict:
    """Execute a complete DMF search task and return a stable structured result.

    This is the business-service boundary used by Agent/API adapters. It owns
    captcha acquisition/recognition, session lifetime, multi-ingredient search,
    and aggregation. The Agent tool itself should remain a thin wrapper.
    """

    dmf_no = (dmf_no or "").strip()
    applicant_name = (applicant_name or "").strip()
    cleaned_ingredients = clean_ingredients(ingredients)
    if history_repository is None:
        history_repository = get_configured_history_repository()

    if not any([dmf_no, applicant_name, cleaned_ingredients]):
        return _empty_search_result(
            message="DMF 编号、申请商名称、成分至少需要提供一个查询条件",
            dmf_no=dmf_no,
            applicant_name=applicant_name,
            ingredients=cleaned_ingredients,
        ).model_dump(mode="json")

    # An empty ingredient still represents one valid DMF number/applicant query.
    query_ingredients = cleaned_ingredients or [""]
    session = requests.Session()

    try:
        try:
            captcha = get_captcha(session)
        except CaptchaError as exc:
            return _preflight_failure_result(
                message=f"验证码获取失败：{exc}",
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredients=cleaned_ingredients,
                query_ingredients=query_ingredients,
                history_repository=history_repository,
            ).model_dump(mode="json")
        except Exception as exc:
            return _preflight_failure_result(
                message=f"验证码获取异常：{exc}",
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredients=cleaned_ingredients,
                query_ingredients=query_ingredients,
                history_repository=history_repository,
            ).model_dump(mode="json")

        solver = MinerUCaptchaSolver()
        try:
            captcha_code = solver.solve(captcha["captcha_path"])
        except Exception as exc:
            return _preflight_failure_result(
                message=f"验证码识别失败：{exc}",
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredients=cleaned_ingredients,
                query_ingredients=query_ingredients,
                history_repository=history_repository,
            ).model_dump(mode="json")

        verify_code = captcha["verify_code"]
        results: list[DMFQueryResult] = []
        success_count = 0
        failed_count = 0
        not_executed_count = 0
        total_records = 0

        for query_index, ingredient in enumerate(query_ingredients):
            query = DMFSingleQuery(
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredient=ingredient,
            )

            query_started_at = datetime.now(timezone.utc)
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
            query_ended_at = datetime.now(timezone.utc)
            raw_result.setdefault("started_at", query_started_at.isoformat())
            raw_result.setdefault("ended_at", query_ended_at.isoformat())
            raw_result.setdefault("queried_at", query_ended_at.isoformat())

            raw_result["query"] = query.model_dump(mode="json")
            if not raw_result.get("collection_status"):
                raw_result["collection_status"] = (
                    DMFCollectionStatus.SUCCESS_NONEMPTY
                    if raw_result.get("success") and raw_result.get("records")
                    else DMFCollectionStatus.SUCCESS_EMPTY
                    if raw_result.get("success")
                    else DMFCollectionStatus.FAILED
                )
            result = DMFQueryResult.model_validate(raw_result)
            if history_repository is not None:
                try:
                    queried_at = raw_result.get("queried_at")
                    result.history = history_repository.record_query(
                        result,
                        raw_pages=raw_result.get("raw_pages") or [],
                        queried_at=(
                            datetime.fromisoformat(queried_at)
                            if isinstance(queried_at, str)
                            else queried_at
                        ),
                    )
                except Exception as exc:
                    logger.exception("DMF 历史 monitor run 写入失败")
                    result.history = DMFHistoryResult(
                        history_status="monitor_run_failed",
                        comparison_status="failed",
                        history_error_code=PUBLIC_HISTORY_ERROR_CODE,
                        history_error=PUBLIC_HISTORY_ERROR_MESSAGE,
                    )
            results.append(result)

            if result.success:
                success_count += 1
                total_records += result.total
            else:
                failed_count += 1
                if "驗證碼" in result.message or "验证码" in result.message:
                    for remaining_ingredient in query_ingredients[query_index + 1 :]:
                        skipped_result = DMFQueryResult(
                            success=False,
                            message="前序查询验证码失效，本项未执行",
                            query=DMFSingleQuery(
                                dmf_no=dmf_no,
                                applicant_name=applicant_name,
                                ingredient=remaining_ingredient,
                            ),
                            collection_status=DMFCollectionStatus.NOT_EXECUTED,
                        )
                        if history_repository is not None:
                            try:
                                skipped_result.history = history_repository.record_query(
                                    skipped_result
                                )
                            except Exception:
                                logger.exception("DMF 未执行项 monitor run 写入失败")
                                skipped_result.history = DMFHistoryResult(
                                    history_status="monitor_run_failed",
                                    comparison_status="failed",
                                    history_error_code=PUBLIC_HISTORY_ERROR_CODE,
                                    history_error=PUBLIC_HISTORY_ERROR_MESSAGE,
                                )
                        results.append(skipped_result)
                        not_executed_count += 1
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
            not_executed_count=not_executed_count,
            total_records=total_records,
            results=results,
        ).model_dump(mode="json")
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