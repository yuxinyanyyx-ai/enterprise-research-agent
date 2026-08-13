def parse_dmf_result(raw_result: dict) -> dict:
    """
    将 FDA 原始查询结果转换成系统统一格式。
    """

    response_info = raw_result.get("response", {})
    state = response_info.get("state", {})

    state_code = state.get("code")
    message = state.get("msgSubject") or "未知状态"

    page_info = raw_result.get("page", {})

    records = raw_result.get("data") or []

    parsed_records = []

    for item in records:
        parsed_records.append(
            {
                "dmf_no": item.get("dmfNo"),
                "applicant_name": item.get("applicantName"),
                "ingredient": item.get("ingredientsDesc"),
                "valid_date": item.get("validDate"),
            }
        )

    return {
        "success": state_code == 1,
        "message": message,

        "total": page_info.get("totalDatas", 0),
        "total_pages": page_info.get("totalPages", 0),
        "current_page": page_info.get("currentPage", 1),
        "page_size": page_info.get("pageItems", 0),

        "records": parsed_records,
    }