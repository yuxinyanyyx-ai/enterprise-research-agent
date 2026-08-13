from pathlib import Path


BASE_URL = "https://lmspiq.fda.gov.tw"

DMF_PAGE_URL = f"{BASE_URL}/web/DRPIQ/DRPIQ7000"

CAPTCHA_URL = f"{BASE_URL}/api/auth/imageCode"

SEARCH_URL = f"{BASE_URL}/api/public/dr/piq/7000/search"


# HTTP 请求超时时间
REQUEST_TIMEOUT = 20


# 项目根目录
BASE_DIR = Path(__file__).resolve().parent

# 验证码图片输出目录
OUTPUT_DIR = BASE_DIR / "outputs"

# 日志目录
LOG_DIR = BASE_DIR / "logs"