import logging

from dmf_service import search_all_dmf


logger = logging.getLogger(__name__)


def search_multiple_ingredients(
    session,
    *,
    ingredients: list[str],
    captcha_code: str,
    verify_code: str,
) -> dict:
    """
    批量查询多个成分。

    如果验证码错误，则立即终止整个批量任务，
    避免后续查询全部重复失败。
    """

    # ==========================
    # 1. 清理输入
    # ==========================

    cleaned_ingredients = []

    for ingredient in ingredients:
        ingredient = ingredient.strip()

        if ingredient:
            cleaned_ingredients.append(ingredient)

    # 去重，同时保持原来的顺序
    cleaned_ingredients = list(
        dict.fromkeys(cleaned_ingredients)
    )

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

    # ==========================
    # 2. 初始化统计
    # ==========================

    results = []

    success_count = 0
    failed_count = 0
    total_records = 0

    total_queries = len(cleaned_ingredients)

    # ==========================
    # 3. 依次查询
    # ==========================

    for index, ingredient in enumerate(
        cleaned_ingredients,
        start=1,
    ):
        logger.info(
            "正在查询 %s/%s：%s",
            index,
            total_queries,
            ingredient,
        )

        result = search_all_dmf(
            session=session,
            captcha_code=captcha_code,
            verify_code=verify_code,
            ingredient=ingredient,
        )

        # --------------------------------
        # 无论成功还是失败，都保存查询条件
        # 解决“--- 未知 ---”问题
        # --------------------------------

        result["query"] = {
            "dmf_no": "",
            "applicant_name": "",
            "ingredient": ingredient,
        }

        results.append(result)

        # ==============================
        # 4. 查询成功
        # ==============================

        if result["success"]:
            success_count += 1

            record_count = len(
                result.get("records", [])
            )

            total_records += record_count

            logger.info(
                "%s 查询完成，共 %s 条",
                ingredient,
                record_count,
            )

            continue

        # ==============================
        # 5. 查询失败
        # ==============================

        failed_count += 1

        error_message = (
            result.get("message") or ""
        )

        logger.warning(
            "%s 查询失败：%s",
            ingredient,
            error_message,
        )

        # ==============================
        # 6. 验证码错误：立即终止
        # ==============================

        if (
            "驗證碼" in error_message
            or "验证码" in error_message
        ):
            logger.error(
                "检测到验证码错误，停止后续批量查询。"
            )

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

    # ==========================
    # 7. 所有查询执行完成
    # ==========================

    return {
        "success": failed_count == 0,
        "captcha_error": False,
        "message": (
            "全部查询完成"
            if failed_count == 0
            else "部分查询失败"
        ),
        "query_count": total_queries,
        "success_count": success_count,
        "failed_count": failed_count,
        "total_records": total_records,
        "results": results,
    }