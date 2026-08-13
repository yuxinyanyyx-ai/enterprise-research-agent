from __future__ import annotations

import re
import tempfile
from pathlib import Path

import cv2
from rapidocr import RapidOCR


# OCR 引擎只初始化一次
_ocr_engine = RapidOCR()


class CaptchaOcrError(Exception):
    """验证码 OCR 识别异常。"""
    pass


def preprocess_captcha(
    captcha_path: Path,
) -> list[Path]:
    """
    生成多个验证码预处理版本。

    返回：
        [
            放大灰度图,
            二值化图,
        ]
    """

    captcha_path = Path(captcha_path)

    image = cv2.imread(
        str(captcha_path)
    )

    if image is None:
        raise CaptchaOcrError(
            f"无法读取验证码图片：{captcha_path}"
        )

    # ==========================
    # 1. 灰度化
    # ==========================

    gray = cv2.cvtColor(
        image,
        cv2.COLOR_BGR2GRAY,
    )

    # ==========================
    # 2. 放大
    # ==========================

    scale = 5

    enlarged = cv2.resize(
        gray,
        None,
        fx=scale,
        fy=scale,
        interpolation=cv2.INTER_CUBIC,
    )

    # 给四周增加白边
    enlarged = cv2.copyMakeBorder(
        enlarged,
        20,
        20,
        20,
        20,
        cv2.BORDER_CONSTANT,
        value=255,
    )

    # ==========================
    # 3. 二值化
    # ==========================

    _, binary = cv2.threshold(
        enlarged,
        0,
        255,
        cv2.THRESH_BINARY
        + cv2.THRESH_OTSU,
    )


    # ==========================
    # 4. 保存临时图片
    # ==========================

    temp_dir = Path(
        tempfile.gettempdir()
    )

    enlarged_path = (
        temp_dir
        / "dmf_captcha_enlarged.png"
    )

    binary_path = (
        temp_dir
        / "dmf_captcha_binary.png"
    )

    cv2.imwrite(
        str(enlarged_path),
        enlarged,
    )

    cv2.imwrite(
        str(binary_path),
        binary,
    )
    # ==========================
    # 额外方案：提取较深的字符笔画
    # ==========================

    # 灰度化后，保留比较深的像素
    _, dark_mask = cv2.threshold(
        enlarged,
        180,
        255,
        cv2.THRESH_BINARY,
    )

    # 对黑色字符做轻微加粗
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT,
        (2, 2),
    )

    dark_mask = cv2.erode(
        dark_mask,
        kernel,
        iterations=1,
    )

    dark_path = (
            temp_dir
            / "dmf_captcha_dark.png"
    )

    cv2.imwrite(
        str(dark_path),
        dark_mask,
    )
    return [
        enlarged_path,
        binary_path,
        dark_path,
    ]


def extract_digits(
    text: str,
) -> str:
    """
    OCR 结果中只保留数字。
    """

    if not text:
        return ""

    return "".join(
        re.findall(
            r"\d",
            text,
        )
    )


def recognize_one_image(
    image_path: Path,
) -> list[tuple[str, float]]:
    """
    直接执行文字识别。

    关键：
    use_det=False
    跳过文字检测阶段。
    """

    try:
        result = _ocr_engine(
            str(image_path),

            # 重点！！！
            use_det=False,

            # 验证码不需要方向分类
            use_cls=False,

            # 使用文字识别
            use_rec=True,
        )

    except Exception as exc:
        raise CaptchaOcrError(
            f"OCR 执行失败：{exc}"
        ) from exc

    if result is None:
        return []

    texts = getattr(
        result,
        "txts",
        None,
    )

    scores = getattr(
        result,
        "scores",
        None,
    )

    if not texts:
        return []

    if not scores:
        scores = [
            0.0
            for _ in texts
        ]

    candidates = []

    for text, score in zip(
        texts,
        scores,
    ):
        digits = extract_digits(
            str(text)
        )

        if not digits:
            continue

        candidates.append(
            (
                digits,
                float(score),
            )
        )

    return candidates


def recognize_captcha(
    captcha_path: Path,
    *,
    expected_length: int | None = 4,
    min_confidence: float = 0.30,
) -> str:
    """
    自动识别验证码。
    """

    captcha_path = Path(
        captcha_path
    )

    if not captcha_path.exists():
        raise CaptchaOcrError(
            f"验证码图片不存在：{captcha_path}"
        )

    all_candidates = []

    # ==========================
    # 1. 原始图片直接识别
    # ==========================

    original_candidates = (
        recognize_one_image(
            captcha_path
        )
    )

    print(
        "原图 OCR 候选：",
        original_candidates,
    )

    all_candidates.extend(
        original_candidates
    )

    # ==========================
    # 2. 预处理图片识别
    # ==========================

    processed_paths = (
        preprocess_captcha(
            captcha_path
        )
    )

    for path in processed_paths:

        candidates = (
            recognize_one_image(
                path
            )
        )

        print(
            f"{path.name} OCR 候选：",
            candidates,
        )

        all_candidates.extend(
            candidates
        )

    # ==========================
    # 3. 没识别到
    # ==========================

    if not all_candidates:
        raise CaptchaOcrError(
            "OCR 未识别出任何数字。"
        )

    # ==========================
    # 4. 长度过滤
    # ==========================

    if expected_length is not None:

        valid_candidates = [
            item
            for item in all_candidates
            if len(item[0])
            == expected_length
        ]

    else:
        valid_candidates = (
            all_candidates
        )

    if not valid_candidates:

        raw_results = [
            text
            for text, _
            in all_candidates
        ]

        raise CaptchaOcrError(
            "OCR 已识别出数字，"
            f"但长度不符合预期：{raw_results}"
        )

    # ==========================
    # 5. 选择置信度最高的
    # ==========================

    best_code, best_score = max(
        valid_candidates,
        key=lambda item: item[1],
    )

    print(
        f"最佳 OCR 候选："
        f"{best_code} "
        f"(confidence={best_score:.3f})"
    )

    if best_score < min_confidence:
        raise CaptchaOcrError(
            "OCR 置信度过低："
            f"{best_code} "
            f"({best_score:.3f})"
        )

    return best_code


# ==============================
# 单独测试
# ==============================

if __name__ == "__main__":

    captcha_path = Path(
        "outputs/captcha.jpg"
    )

    try:
        code = recognize_captcha(
            captcha_path
        )

        print(
            "\n=========================="
        )
        print(
            "OCR 识别验证码：",
            code,
        )
        print(
            "=========================="
        )

    except CaptchaOcrError as exc:

        print(
            "\nOCR 识别失败：",
            exc,
        )