from .dmf_client import search_dmf_page
from .result_parser import parse_dmf_result
from .dmf_errors import DmfClientError
from datetime import datetime, timezone
import logging

logger = logging.getLogger(__name__)


def _collection_times(started_at: datetime) -> dict[str, str]:
    ended_at = datetime.now(timezone.utc)
    return {
        "started_at": started_at.isoformat(),
        "ended_at": ended_at.isoformat(),
        "queried_at": ended_at.isoformat(),
    }


def search_all_dmf(
    session,
    *,
    captcha_code: str,
    verify_code: str,
    dmf_no: str = "",
    applicant_name: str = "",
    ingredient: str = "",
    page_size: int = 10,
):
    """
    查询满足条件的所有 DMF 数据，并自动处理分页。
    """

    started_at = datetime.now(timezone.utc)

    # ==========================
    # 1. 输入校验
    # ==========================

    dmf_no = dmf_no.strip()
    applicant_name = applicant_name.strip()
    ingredient = ingredient.strip()
    query = {
        "dmf_no": dmf_no,
        "applicant_name": applicant_name,
        "ingredient": ingredient,
    }

    if not any([
        dmf_no,
        applicant_name,
        ingredient,
    ]):
        return {
            "success": False,
            "message": "DMF编号、申请商名称、成分至少填写一个。",
            "query": query,
            "collection_status": "FAILED",
            "total": 0,
            "total_pages": 0,
            "successful_pages": 0,
            "failed_page": None,
            "records": [],
            "raw_pages": [],
            **_collection_times(started_at),
        }

    if not captcha_code.strip():
        return {
            "success": False,
            "message": "验证码不能为空。",
            "query": query,
            "collection_status": "FAILED",
            "total": 0,
            "total_pages": 0,
            "successful_pages": 0,
            "failed_page": None,
            "records": [],
            "raw_pages": [],
            **_collection_times(started_at),
        }

    # ==========================
    # 2. 查询第一页
    # ==========================

    try:
        first_raw = search_dmf_page(
            session=session,
            captcha_code=captcha_code,
            verify_code=verify_code,
            dmf_no=dmf_no,
            applicant_name=applicant_name,
            ingredient=ingredient,
            page=1,
            page_size=page_size,
        )

    except DmfClientError as exc:
        return {
            "success": False,
            "message": str(exc),
            "query": query,
            "collection_status": "FAILED",
            "total": 0,
            "total_pages": 0,
            "successful_pages": 0,
            "failed_page": 1,
            "records": [],
            "raw_pages": [],
            **_collection_times(started_at),
        }

    first_result = parse_dmf_result(first_raw)

    if not first_result["success"]:
        return {
            **first_result,
            "query": query,
            "collection_status": "FAILED",
            "successful_pages": 0,
            "failed_page": 1,
            "raw_pages": [first_raw],
            **_collection_times(started_at),
        }

    total_pages = first_result["total_pages"]
    all_records = list(first_result["records"])
    raw_pages = [first_raw]
    logger.info(
        "DMF 查询成功，总记录数：%s，总页数：%s",
        first_result["total"],
        total_pages,
    )
    # ==========================
    # 3. 查询剩余分页
    # ==========================

    for page_number in range(2, total_pages + 1):

        logger.info(
            "正在获取第 %s/%s 页",
            page_number,
            total_pages,
        )

        try:
            raw_result = search_dmf_page(
                session=session,
                captcha_code=captcha_code,
                verify_code=verify_code,
                dmf_no=dmf_no,
                applicant_name=applicant_name,
                ingredient=ingredient,
                page=page_number,
                page_size=page_size,
            )

        except DmfClientError as exc:
            return {
                "success": False,
                "message": (
                    f"第 {page_number} 页查询失败：{exc}"
                ),
                "query": query,
                "collection_status": "PARTIAL",
                "total": first_result["total"],
                "total_pages": total_pages,
                "successful_pages": page_number - 1,
                "failed_page": page_number,
                "records": all_records,
                "raw_pages": raw_pages,
                **_collection_times(started_at),
            }

        parsed_result = parse_dmf_result(raw_result)
        raw_pages.append(raw_result)

        if not parsed_result["success"]:
            return {
                "success": False,
                "message": (
                    f"第 {page_number} 页查询失败："
                    f"{parsed_result['message']}"
                ),
                "query": query,
                "collection_status": "PARTIAL",
                "total": first_result["total"],
                "total_pages": total_pages,
                "successful_pages": page_number - 1,
                "failed_page": page_number,
                "records": all_records,
                "raw_pages": raw_pages,
                **_collection_times(started_at),
            }

        all_records.extend(
            parsed_result["records"]
        )
    logger.info(
        "DMF 全部分页获取完成，实际获取：%s 条",
        len(all_records),
    )
    # ==========================
    # 4. 最终统一结果
    # ==========================

    return {
        "success": True,
        "message": first_result["message"],
        "query": query,
        "collection_status": (
            "SUCCESS_NONEMPTY" if all_records else "SUCCESS_EMPTY"
        ),
        "total": first_result["total"],
        "total_pages": total_pages,
        "successful_pages": total_pages,
        "failed_page": None,
        "records": all_records,
        "raw_pages": raw_pages,
        **_collection_times(started_at),
    }
