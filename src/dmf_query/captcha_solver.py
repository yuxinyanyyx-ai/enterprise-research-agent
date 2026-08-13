from abc import ABC, abstractmethod
from pathlib import Path


class CaptchaSolver(ABC):

    @abstractmethod
    def solve(self, captcha_path: Path) -> str:
        pass


class ManualCaptchaSolver(CaptchaSolver):

    def solve(self, captcha_path: Path) -> str:
        code = input("请输入图片中的验证码：").strip()

        if not code:
            raise ValueError("验证码不能为空。")

        return code


class AutoCaptchaSolver(CaptchaSolver):

    def __init__(self, ocr):
        self.ocr = ocr

    def solve(self, captcha_path: Path) -> str:
        code = self.ocr.recognize(captcha_path)

        code = code.strip()

        if not code:
            raise ValueError("验证码识别失败。")

        if not code.isdigit():
            raise ValueError(
                f"验证码识别结果格式异常：{code}"
            )

        return code