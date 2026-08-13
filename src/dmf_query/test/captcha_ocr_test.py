import csv
import shutil
import time
from pathlib import Path

import requests

from captcha_client import get_captcha
from capcha_ocr import recognize_captcha, CaptchaOcrError
from dmf_client import search_dmf
from dmf_errors import DmfClientError


TEST_COUNT = 100  # 先测30，稳定后再改100

TEST_DIR = Path("../outputs/captcha_auto_test")
RESULT_CSV = TEST_DIR / "captcha_auto_test_results.csv"


def main():
    TEST_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    session = requests.Session()

    correct_count = 0
    wrong_count = 0
    ocr_failed_count = 0
    request_failed_count = 0

    results = []

    print(
        f"\n准备自动测试 {TEST_COUNT} 张验证码"
    )

    for index in range(1, TEST_COUNT + 1):

        print("\n" + "=" * 50)
        print(f"测试 {index}/{TEST_COUNT}")

        # =========================
        # 1. 获取新的验证码
        # =========================

        try:
            captcha = get_captcha(session)

        except Exception as exc:
            print("获取验证码失败：", exc)

            request_failed_count += 1

            results.append({
                "index": index,
                "predicted": "",
                "status": "验证码获取失败",
                "message": str(exc),
            })

            continue

        captcha_path = Path(
            captcha["captcha_path"]
        )

        # 保存每一张测试图片
        saved_path = (
            TEST_DIR
            / f"captcha_{index:03d}.jpg"
        )

        shutil.copy2(
            captcha_path,
            saved_path,
        )

        # =========================
        # 2. OCR 自动识别
        # =========================

        try:
            predicted_code = recognize_captcha(
                saved_path,
                expected_length=4,
            )

            print(
                "OCR预测：",
                predicted_code,
            )

        except CaptchaOcrError as exc:
            print(
                "OCR失败：",
                exc,
            )

            ocr_failed_count += 1

            results.append({
                "index": index,
                "predicted": "",
                "status": "OCR失败",
                "message": str(exc),
            })

            # OCR连4位都没得到，就没必要提交
            continue

        # =========================
        # 3. 让服务器验证 OCR 答案
        # =========================

        try:
            search_dmf(
                session=session,
                captcha_code=predicted_code,
                verify_code=captcha["verify_code"],

                # 随便用一个稳定存在的查询条件
                ingredient="Ibuprofen",

                page=1,
                page_size=1,
                debug=False,
            )

            # 查询成功 = 验证码正确
            correct_count += 1

            print(
                "服务器验证：✅ 正确"
            )

            results.append({
                "index": index,
                "predicted": predicted_code,
                "status": "正确",
                "message": "",
            })

        except DmfClientError as exc:

            error_message = str(exc)

            # =====================
            # 验证码不相符
            # =====================

            if (
                "驗證碼" in error_message
                or "验证码" in error_message
            ):
                wrong_count += 1

                print(
                    "服务器验证：❌ OCR识别错误"
                )

                results.append({
                    "index": index,
                    "predicted": predicted_code,
                    "status": "识别错误",
                    "message": error_message,
                })

            # =====================
            # 其他网络/API错误
            # =====================

            else:
                request_failed_count += 1

                print(
                    "查询接口异常：",
                    error_message,
                )

                results.append({
                    "index": index,
                    "predicted": predicted_code,
                    "status": "接口异常",
                    "message": error_message,
                })

        # 不要请求太快
        time.sleep(1)

    # =============================
    # 4. 保存 CSV
    # =============================

    with open(
        RESULT_CSV,
        "w",
        newline="",
        encoding="utf-8-sig",
    ) as file:

        writer = csv.DictWriter(
            file,
            fieldnames=[
                "index",
                "predicted",
                "status",
                "message",
            ],
        )

        writer.writeheader()
        writer.writerows(results)

    # =============================
    # 5. 最终统计
    # =============================

    total = len(results)

    if total > 0:
        success_rate = (
            correct_count
            / total
            * 100
        )
    else:
        success_rate = 0.0

    submitted = (
        correct_count
        + wrong_count
    )

    if submitted > 0:
        submitted_accuracy = (
            correct_count
            / submitted
            * 100
        )
    else:
        submitted_accuracy = 0.0

    print("\n" + "=" * 50)
    print("验证码自动测试完成")
    print("=" * 50)

    print("测试总数：", total)
    print("服务器验证正确：", correct_count)
    print("服务器验证错误：", wrong_count)
    print("OCR直接失败：", ocr_failed_count)
    print("其他接口异常：", request_failed_count)

    print(
        f"已提交验证码准确率："
        f"{submitted_accuracy:.2f}%"
    )

    print(
        f"端到端成功率："
        f"{success_rate:.2f}%"
    )

    print(
        "\n结果文件：",
        RESULT_CSV.resolve(),
    )


if __name__ == "__main__":
    main()