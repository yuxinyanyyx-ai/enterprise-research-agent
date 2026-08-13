import base64
import os
from pathlib import Path

import requests


CAPTCHA_URL = "https://lmspiq.fda.gov.tw/api/auth/imageCode"


def get_captcha(session: requests.Session):
    """
    请求验证码接口。

    返回：
    - captcha_path: 验证码图片保存路径
    - verify_code: 查询接口后续需要使用的验证 token
    - expire_time: 验证码过期时间
    """

    response = session.get(
        CAPTCHA_URL,
        timeout=15
    )

    # 如果 HTTP 请求失败，例如 404、500，会直接抛异常
    response.raise_for_status()

    result = response.json()

    # 接口真正的数据在 data 中
    data = result["data"]

    image_data = data["image"]
    verify_code = data["code"]
    expire_time = data["expireTime"]

    # image_data 类似：
    # data:image/jpeg;base64,/9j/4AAQSk...
    _, base64_data = image_data.split(",", 1)

    # Base64 -> 图片二进制
    image_bytes = base64.b64decode(base64_data)

    # 创建输出目录
    output_dir = Path("outputs")
    output_dir.mkdir(exist_ok=True)

    captcha_path = output_dir / "captcha.jpg"

    # 保存验证码图片
    captcha_path.write_bytes(image_bytes)

    return {
        "captcha_path": captcha_path,
        "verify_code": verify_code,
        "expire_time": expire_time
    }


if __name__ == "__main__":

    # 用 Session 是因为后面查询接口也继续使用同一个 Session
    session = requests.Session()

    captcha = get_captcha(session)

    print("验证码获取成功")
    print("图片位置：", captcha["captcha_path"].resolve())
    print("过期时间：", captcha["expire_time"])

    # verifyCode 不建议完整打印，只看前几个字符确认拿到了
    print(
        "verifyCode：",
        captcha["verify_code"][:10] + "..."
    )

    # Windows 下自动打开验证码图片
    os.startfile(captcha["captcha_path"].resolve())