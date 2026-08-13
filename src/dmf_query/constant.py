from pathlib import Path


BASE_URL = "https://lmspiq.fda.gov.tw"

DMF_PAGE_URL = f"{BASE_URL}/web/DRPIQ/DRPIQ7000"

CAPTCHA_URL = f"{BASE_URL}/api/auth/imageCode"

SEARCH_URL = f"{BASE_URL}/api/public/dr/piq/7000/search"


# HTTP 请求超时时间
REQUEST_TIMEOUT = 20


PROJECT_ROOT = Path(__file__).resolve().parents[2]

OUTPUT_DIR = (
    PROJECT_ROOT
    / "outputs"
    / "dmf_query"
)

LOG_DIR = (
    PROJECT_ROOT
    / "logs"
    / "dmf_query"
)