

import requests

from .captcha_client import get_captcha
import os
from .excel_exporter import export_multi_query_result
from .dmf_errors import CaptchaError
from .logger import setup_logging
from .multi_query_service import search_multiple_ingredients
from .captcha_solver import MinerUCaptchaSolver


def main():
    setup_logging()
    # 1. 创建同一个 Session
    session = requests.Session()

    # 2. 获取验证码
    try:
        captcha = get_captcha(session)

    except CaptchaError as exc:
        print(f"\n验证码获取失败：{exc}")
        return

    # 自动打开验证码图片
    os.startfile(captcha["captcha_path"].resolve())

    print("\n验证码图片已经打开")
    print("图片位置：", captcha["captcha_path"].resolve())
    print("过期时间：", captcha["expire_time"])
    #print("当前 Session Cookies：", session.cookies.get_dict())
    # 3.
    #solver = ManualCaptchaSolver()
   # solver = AutoCaptchaSolver(ocr)
    solver = MinerUCaptchaSolver()
    captcha_code = solver.solve(
        captcha["captcha_path"]
    )
    ingredients = [
        "Ibuprofen",
        "Paracetamol",
        "Metformin",
    ]

    result = search_multiple_ingredients(
        session=session,
        ingredients=ingredients,
        captcha_code=captcha_code,
        verify_code=captcha["verify_code"],
    )
    print("\n========== 多药物查询结果 ==========")

    print("总体状态：", result["message"])
    print("查询药物数：", result["query_count"])
    print("成功：", result["success_count"])
    print("失败：", result["failed_count"])
    print("总记录数：", result["total_records"])


    # ==============================
    # 验证码错误处理
    # ==============================

    if result.get("captcha_error"):
        print("\n========== 查询已终止 ==========")
        print(result["message"])
        print("请重新运行程序并输入新的验证码。")
        return

    # ==============================
    # 导出 Excel
    # ==============================

    if result["total_records"] > 0:
        try:
            excel_path = export_multi_query_result(
                result
            )

            print(
                "\nExcel 导出成功：",
                excel_path.resolve(),
            )

        except Exception as exc:
            print(
                "\nExcel 导出失败：",
                exc,
            )

    else:
        print("\n没有查询到可导出的 DMF 数据。")
    for item in result["results"]:
        query = item.get("query", {})
        ingredient = query.get("ingredient", "未知")

        print(f"\n--- {ingredient} ---")

        if not item["success"]:
            print("查询失败：", item["message"])
            continue

        print("记录数：", len(item["records"]))
    # 4. 输入查询条件
    # print("\n请输入 DMF 查询条件")
    # print("不需要的字段直接按 Enter 留空即可\n")
    #
    # dmf_no = input("DMF 编号：").strip()
    # applicant_name = input("申请商名称：").strip()
    # ingredient = input("成分：").strip()
    #
    # # 5. 调用查询接口
    # result = search_all_dmf(
    #     session=session,
    #     captcha_code=captcha_code,
    #     verify_code=captcha["verify_code"],
    #     dmf_no=dmf_no,
    #     applicant_name=applicant_name,
    #     ingredient=ingredient,
    # )
    # print("\n========== 最终查询结果 ==========")
    # if not result["success"]:
    #     print("查询失败：", result["message"])
    #     return
    # print("查询状态：", result["message"])
    # print("总记录数：", result["total"])
    # print("实际获取：", len(result["records"]))
    #
    # if result["total"] == 0:
    #     print("\n未查询到符合条件的 DMF 数据。")
    #     return
    #
    # for index, item in enumerate(
    #         result["records"],
    #         start=1,
    # ):
    #     print(f"\n记录 {index}")
    #     print("DMF编号：", item["dmf_no"])
    #     print("申请商：", item["applicant_name"])
    #     print("成分：", item["ingredient"])
    #     print(
    #         "有效日期：",
    #         item["valid_date"] or "暂无"
    #     )
    #
    # print("\n========== 测试第 2 页 ==========")
    #
    # raw_page_2 = search_dmf(
    #     session=session,
    #     captcha_code=captcha_code,
    #     verify_code=captcha["verify_code"],
    #     dmf_no=dmf_no,
    #     applicant_name=applicant_name,
    #     ingredient=ingredient,
    #     page=2,
    #     page_size=10,
    # )
    #
    # page_2_result = parse_dmf_result(raw_page_2)
    #
    # print("查询状态：", page_2_result["message"])
    # print("当前页：", page_2_result["current_page"])
    # print("当前返回：", len(page_2_result["records"]))
    #
    # for index, item in enumerate(page_2_result["records"], start=1):
    #     print(
    #         index,
    #         item["dmf_no"],
    #         item["applicant_name"],
    #         item["ingredient"],
    #         item["valid_date"],
    #     )
    # 6. 打印服务器返回结果
    # print("\n========== 查询结果 ==========")
    #
    # print(
    #     json.dumps(
    #         result,
    #         ensure_ascii=False,
    #         indent=2,
    #     )
    # )


if __name__ == "__main__":
    main()