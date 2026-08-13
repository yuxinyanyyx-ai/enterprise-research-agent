from abc import ABC, abstractmethod
from pathlib import Path

from .mineru_captcha_solver import (
    recognize_captcha_with_mineru,
    MinerUCaptchaError,
)


class CaptchaSolver(ABC):
    """验证码识别器抽象接口。"""

    @abstractmethod
    def solve(
        self,
        captcha_path: Path,
    ) -> str:
        pass


class ManualCaptchaSolver(CaptchaSolver):
    """人工输入验证码。"""

    def solve(
        self,
        captcha_path: Path,
    ) -> str:

        print(
            f"验证码图片：{captcha_path}"
        )

        code = input(
            "请输入图片中的验证码："
        ).strip()

        if not code:
            raise ValueError(
                "验证码不能为空"
            )

        return code


class MinerUCaptchaSolver(CaptchaSolver):
    """使用 MinerU 自动识别验证码。"""

    def solve(
        self,
        captcha_path: Path,
    ) -> str:

        try:
            code = recognize_captcha_with_mineru(
                captcha_path
            )

        except MinerUCaptchaError as exc:
            raise ValueError(
                f"MinerU 验证码识别失败：{exc}"
            ) from exc

        return code