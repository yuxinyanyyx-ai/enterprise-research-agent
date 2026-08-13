from dmf_client import search_dmf
from result_parser import parse_dmf_result
from dmf_errors import DmfClientError
import logging

logger = logging.getLogger(__name__)


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

    # ==========================
    # 1. 输入校验
    # ==========================

    dmf_no = dmf_no.strip()
    applicant_name = applicant_name.strip()
    ingredient = ingredient.strip()

    if not any([
        dmf_no,
        applicant_name,
        ingredient,
    ]):
        return {
            "success": False,
            "message": "DMF编号、申请商名称、成分至少填写一个。",
            "total": 0,
            "total_pages": 0,
            "records": [],
        }

    if not captcha_code.strip():
        return {
            "success": False,
            "message": "验证码不能为空。",
            "total": 0,
            "total_pages": 0,
            "records": [],
        }

    # ==========================
    # 2. 查询第一页
    # ==========================

    try:
        first_raw = search_dmf(
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
            "total": 0,
            "total_pages": 0,
            "records": [],
        }

    first_result = parse_dmf_result(first_raw)

    if not first_result["success"]:
        return first_result

    total_pages = first_result["total_pages"]
    all_records = list(first_result["records"])
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
            raw_result = search_dmf(
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
                "total": first_result["total"],
                "total_pages": total_pages,
                "records": all_records,
            }

        parsed_result = parse_dmf_result(raw_result)

        if not parsed_result["success"]:
            return {
                "success": False,
                "message": (
                    f"第 {page_number} 页查询失败："
                    f"{parsed_result['message']}"
                ),
                "total": first_result["total"],
                "total_pages": total_pages,
                "records": all_records,
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

        "query": {
            "dmf_no": dmf_no,
            "applicant_name": applicant_name,
            "ingredient": ingredient,
        },

        "total": first_result["total"],
        "total_pages": total_pages,
        "records": all_records,
    }
