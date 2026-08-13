from __future__ import annotations

import re
from pathlib import Path

from dotenv import load_dotenv

from src.settings import get_settings
from src.mineru.services.mineru_client import (
    MinerUClient,
    MinerUError,
)


# 加载项目根目录 .env
PROJECT_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(PROJECT_ROOT / ".env")


class MinerUCaptchaError(RuntimeError):
    """MinerU 验证码识别异常。"""


def recognize_captcha_with_mineru(
    captcha_path: Path,
) -> str:
    """
    使用 MinerU 识别4位数字验证码。
    """

    captcha_path = Path(captcha_path).resolve()

    if not captcha_path.is_file():
        raise MinerUCaptchaError(
            f"验证码图片不存在：{captcha_path}"
        )

    try:
        settings = get_settings()

        with MinerUClient(settings) as client:
            batch_result = client.parse_files(
                [captcha_path]
            )

    except MinerUError as exc:
        raise MinerUCaptchaError(
            f"MinerU 调用失败：{exc}"
        ) from exc

    except Exception as exc:
        raise MinerUCaptchaError(
            f"验证码识别异常：{exc}"
        ) from exc

    if not batch_result.files:
        raise MinerUCaptchaError(
            "MinerU 没有返回解析结果"
        )

    file_result = batch_result.files[0]

    if not file_result.succeeded:
        raise MinerUCaptchaError(
            f"MinerU 解析失败：{file_result.error}"
        )

    markdown = (
        file_result.markdown
        or ""
    ).strip()

    # 先直接找独立4位数字
    matches = re.findall(
        r"(?<!\d)\d{4}(?!\d)",
        markdown,
    )

    if matches:
        return matches[0]

    # 兼容 MinerU 输出：
    # 8 1 0 7
    digits = re.sub(
        r"\D",
        "",
        markdown,
    )

    if len(digits) == 4:
        return digits

    raise MinerUCaptchaError(
        f"MinerU 未识别出完整4位验证码，原始结果：{markdown!r}"
    )