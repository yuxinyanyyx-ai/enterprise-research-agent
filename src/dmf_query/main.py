from __future__ import annotations

import requests

from .captcha_client import get_captcha
from .captcha_solver import MinerUCaptchaSolver
from .dmf_errors import CaptchaError
from .dmf_service import search_all_dmf
from .logger import setup_logging


def get_query_conditions():
    """从命令行获取 DMF 查询条件。"""

    print("\n" + "=" * 60)
    print("DMF 查询")
    print("=" * 60)

    print("\n请输入 DMF 查询条件")
    print("不需要的字段直接按 Enter 留空")
    print("多个成分请使用英文逗号 , 分隔\n")

    dmf_no = input("DMF 编号：").strip()
    applicant_name = input("申请商名称：").strip()
    ingredient_input = input("成分：").strip()

    ingredients = [
        item.strip()
        for item in ingredient_input.split(",")
        if item.strip()
    ]

    ingredients = list(dict.fromkeys(ingredients))

    if not any([dmf_no, applicant_name, ingredients]):
        raise ValueError(
            "DMF 编号、申请商名称、成分至少填写一个"
        )

    return dmf_no, applicant_name, ingredients


def print_query_conditions(
    *,
    dmf_no: str,
    applicant_name: str,
    ingredients: list[str],
):
    """打印本次查询条件。"""

    print("\n" + "=" * 60)
    print("本次查询条件")
    print("=" * 60)

    if dmf_no:
        print("DMF 编号：", dmf_no)

    if applicant_name:
        print("申请商名称：", applicant_name)

    if ingredients:
        print("成分：", ", ".join(ingredients))


def print_single_query_result(
    result: dict,
    *,
    title: str | None = None,
):
    """打印一次 DMF 查询的详细结果。"""

    if title:
        print("\n" + "=" * 60)
        print(title)
        print("=" * 60)

    if not result.get("success"):
        print("查询状态：失败")
        print(
            "失败原因：",
            result.get("message", "未知错误"),
        )
        return

    print("查询状态：成功")
    print("查询结果：", result.get("message", ""))
    print("总记录数：", result.get("total", 0))
    print("总页数：", result.get("total_pages", 0))

    records = result.get("records", [])

    if not records:
        print("\n没有查询到符合条件的 DMF 记录。")
        return

    print("\n" + "-" * 60)
    print("DMF 详细记录")
    print("-" * 60)

    for index, record in enumerate(records, start=1):
        print(f"\n【记录 {index}】")
        print("DMF 编号：", record.get("dmf_no") or "-")
        print(
            "申请商名称：",
            record.get("applicant_name") or "-",
        )
        print("成分：", record.get("ingredient") or "-")
        print("有效日期：", record.get("valid_date") or "-")
        print("-" * 60)


def run_dmf_queries(
    *,
    session: requests.Session,
    captcha_code: str,
    verify_code: str,
    dmf_no: str,
    applicant_name: str,
    ingredients: list[str],
):
    """执行单条件、组合条件或多成分 DMF 查询。"""

    if not ingredients:
        result = search_all_dmf(
            session=session,
            captcha_code=captcha_code,
            verify_code=verify_code,
            dmf_no=dmf_no,
            applicant_name=applicant_name,
            ingredient="",
        )

        print_single_query_result(
            result,
            title="DMF 查询结果",
        )
        return

    if len(ingredients) == 1:
        ingredient = ingredients[0]

        result = search_all_dmf(
            session=session,
            captcha_code=captcha_code,
            verify_code=verify_code,
            dmf_no=dmf_no,
            applicant_name=applicant_name,
            ingredient=ingredient,
        )

        print_single_query_result(
            result,
            title=f"DMF 查询结果：{ingredient}",
        )
        return

    print("\n" + "=" * 60)
    print(f"开始批量查询，共 {len(ingredients)} 个成分")
    print("=" * 60)

    success_count = 0
    failed_count = 0
    total_records = 0

    for index, ingredient in enumerate(
        ingredients,
        start=1,
    ):
        print(
            f"\n正在查询 "
            f"{index}/{len(ingredients)}："
            f"{ingredient}"
        )

        result = search_all_dmf(
            session=session,
            captcha_code=captcha_code,
            verify_code=verify_code,
            dmf_no=dmf_no,
            applicant_name=applicant_name,
            ingredient=ingredient,
        )

        if result.get("success"):
            success_count += 1
            total_records += result.get("total", 0)
        else:
            failed_count += 1

        print_single_query_result(
            result,
            title=f"查询结果：{ingredient}",
        )

    print("\n" + "=" * 60)
    print("批量查询汇总")
    print("=" * 60)
    print("查询成分数：", len(ingredients))
    print("成功：", success_count)
    print("失败：", failed_count)
    print("总记录数：", total_records)


def main():
    """DMF 查询程序入口。"""

    setup_logging()

    try:
        dmf_no, applicant_name, ingredients = (
            get_query_conditions()
        )
    except ValueError as exc:
        print(f"\n输入错误：{exc}")
        return

    print_query_conditions(
        dmf_no=dmf_no,
        applicant_name=applicant_name,
        ingredients=ingredients,
    )

    session = requests.Session()

    try:
        try:
            captcha = get_captcha(session)
        except CaptchaError as exc:
            print(f"\n验证码获取失败：{exc}")
            return

        print(
            "\n验证码图片位置：",
            captcha["captcha_path"],
        )
        print(
            "验证码过期时间：",
            captcha["expire_time"],
        )

        solver = MinerUCaptchaSolver()

        try:
            captcha_code = solver.solve(
                captcha["captcha_path"]
            )
        except ValueError as exc:
            print(f"\n验证码识别失败：{exc}")
            return

        print("\n验证码识别完成")

        run_dmf_queries(
            session=session,
            captcha_code=captcha_code,
            verify_code=captcha["verify_code"],
            dmf_no=dmf_no,
            applicant_name=applicant_name,
            ingredients=ingredients,
        )

    finally:
        session.close()


if __name__ == "__main__":
    main()
