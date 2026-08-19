"""使用 MinerU 测试 DMF 验证码识别。

用法（在仓库根目录执行）：
    python -m src.dmf_query.mineru_captcha_test

也可以指定图片：


    python -m src.dmf_query.mineru_captcha_test src/dmf_query/outputs/captcha_auto_test/captcha_011.jpg
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from dotenv import load_dotenv


# ==========================================
# 项目根目录
# src/dmf_query/mineru_captcha_test.py
# 往上两级就是项目根目录
# ==========================================

PROJECT_ROOT = Path(__file__).resolve().parents[2]

ENV_PATH = PROJECT_ROOT / ".env"

load_dotenv(
    dotenv_path=ENV_PATH,
    override=False,
)

print("加载 .env：", ENV_PATH)
print(".env 是否存在：", ENV_PATH.exists())


# 必须在 load_dotenv 之后 import settings
import src.settings as settings_module

from src.mineru.services.mineru_client import (
    MinerUClient,
    MinerUError,
)


DEFAULT_CAPTCHA_PATH = Path(
    "src/dmf_query/outputs/captcha_auto_test/captcha_011.jpg"
)


def build_settings():
    """兼容当前项目中常见的 Settings 获取方式。"""
    get_settings = getattr(settings_module, "get_settings", None)
    if callable(get_settings):
        return get_settings()

    settings_cls = getattr(settings_module, "Settings", None)
    if settings_cls is None:
        raise RuntimeError("src.settings 中未找到 Settings 或 get_settings")

    return settings_cls()


def extract_four_digits(text: str) -> str:
    """从 MinerU Markdown 结果中提取一个 4 位数字验证码。"""
    if not text:
        raise ValueError("MinerU 没有返回文本")

    # 优先匹配独立的 4 位数字，避免从更长数字中截取。
    matches = re.findall(r"(?<!\d)\d{4}(?!\d)", text)

    if not matches:
        # 兼容 Markdown 中可能出现的空格，例如 1 7 7 6
        compact_digits = re.sub(r"\D", "", text)
        if len(compact_digits) == 4:
            return compact_digits

        raise ValueError(
            f"没有找到完整的4位数字，MinerU原始结果：{text!r}"
        )

    return matches[0]


def resolve_captcha_path() -> Path:
    if len(sys.argv) >= 2:
        return Path(sys.argv[1]).expanduser().resolve()

    return DEFAULT_CAPTCHA_PATH.resolve()


def main() -> None:
    captcha_path = resolve_captcha_path()

    print("=" * 60)
    print("MinerU 验证码识别测试")
    print("=" * 60)
    print("测试图片：", captcha_path)

    if not captcha_path.is_file():
        print("\n❌ 验证码文件不存在")
        print("请检查路径，或运行：")
        print(
            "python -m src.dmf_query.mineru_captcha_test "
            "<验证码图片路径>"
        )
        return

    try:
        settings = build_settings()
    except Exception as exc:
        print("\n❌ Settings 初始化失败：", exc)
        return

    try:
        with MinerUClient(settings) as client:
            batch_result = client.parse_files([captcha_path])
    except MinerUError as exc:
        print("\n❌ MinerU 调用失败：", exc)
        return
    except Exception as exc:
        print("\n❌ 调用过程中发生异常：", exc)
        return

    if not batch_result.files:
        print("\n❌ MinerU 没有返回文件结果")
        return

    file_result = batch_result.files[0]

    print("\nBatch ID：", batch_result.batch_id)
    print("解析状态：", file_result.state)

    if not file_result.succeeded:
        print("❌ MinerU 解析失败：", file_result.error)
        return

    markdown = file_result.markdown or ""

    print("\n" + "=" * 60)
    print("MinerU 原始识别结果")
    print("=" * 60)
    print(repr(markdown))

    try:
        code = extract_four_digits(markdown)
    except ValueError as exc:
        print("\n❌ 验证码提取失败：", exc)
        return

    print("\n" + "=" * 60)
    print("最终验证码")
    print("=" * 60)
    print(code)


if __name__ == "__main__":
    main()
