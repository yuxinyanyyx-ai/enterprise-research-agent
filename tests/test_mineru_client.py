"""MinerUClient 单元测试,测试只检查本地文件校验逻辑"""

from pathlib import Path
from typing import Any
import pytest

from config import Settings, load_settings
from app.services.mineru_client import (
    FileValidationError,
    MinerUClient, MinerUAPIError,
)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    """为每个测试创建独立配置和临时目录。"""

    return load_settings(
        {
            # 单元测试不会真正请求 MinerU，因此使用假 Token
            "MINERU_TOKEN": "unit-test-token",

            # 测试使用临时目录，不污染项目的 storage 目录
            "UPLOAD_DIR": str(tmp_path / "uploads"),
            "RESULT_DIR": str(tmp_path / "results"),
            "TEMP_DIR": str(tmp_path / "temp"),

            # 为了方便测试，把限制设小一些
            "MAX_FILE_SIZE_MB": "1",
            "MAX_BATCH_SIZE_MB": "2",
            "MAX_BATCH_FILES": "2",
            "MAX_MARKDOWN_SIZE_MB": "1",

            "ALLOWED_EXTENSIONS": ".pdf,.docx",
        }
    )


def test_valid_pdf_passes(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """正常、非空的 PDF 应当通过校验。"""

    pdf_path = tmp_path / "normal.pdf"

    # 这里只测试文件校验，不需要创建真正完整的 PDF
    pdf_path.write_bytes(b"%PDF-1.4 test content")

    with MinerUClient(settings) as client:
        validated = client.validate_files([pdf_path])

    assert validated == (pdf_path.resolve(),)


def test_unsupported_extension_is_rejected(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """不在白名单中的扩展名应当被拒绝。"""

    exe_path = tmp_path / "unsafe.exe"
    exe_path.write_bytes(b"test")

    with MinerUClient(settings) as client:
        with pytest.raises(
            FileValidationError,
            match="不支持的文件格式",
        ):
            client.validate_files([exe_path])


def test_empty_file_is_rejected(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """大小为0的空文件应当被拒绝。"""

    empty_pdf = tmp_path / "empty.pdf"
    empty_pdf.touch()

    with MinerUClient(settings) as client:
        with pytest.raises(
            FileValidationError,
            match="文件为空",
        ):
            client.validate_files([empty_pdf])


def test_too_many_files_are_rejected(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """文件数量超过 MAX_BATCH_FILES 时应当被拒绝。"""

    paths = []

    # 测试配置最多允许2个文件，这里创建3个
    for index in range(3):
        path = tmp_path / f"document_{index}.pdf"
        path.write_bytes(b"%PDF test")
        paths.append(path)

    with MinerUClient(settings) as client:
        with pytest.raises(
            FileValidationError,
            match="每批最多上传",
        ):
            client.validate_files(paths)
class FakeResponse:
    """模拟 requests 返回的 HTTP 响应。"""

    def __init__(
        self,
        body: dict[str, Any],
        status_code: int = 200,
    ) -> None:
        self.body = body
        self.status_code = status_code
        self.text = str(body)

    def raise_for_status(self) -> None:
        """当前测试只模拟HTTP 200，因此不抛异常。"""

    def json(self) -> dict[str, Any]:
        """返回测试预先准备的JSON数据。"""
        return self.body


class FakeSession:
    """模拟 requests.Session，避免真正访问 MinerU。"""

    def __init__(
        self,
        post_response: FakeResponse,
    ) -> None:
        self.post_response = post_response
        self.last_post_url: str | None = None
        self.last_post_kwargs: dict[str, Any] = {}

    def post(self, url: str, **kwargs: Any) -> FakeResponse:
        self.last_post_url = url
        self.last_post_kwargs = kwargs
        return self.post_response

    def put(self, url: str, **kwargs: Any) -> Any:
        raise AssertionError("本测试不应该调用PUT")

    def get(self, url: str, **kwargs: Any) -> Any:
        raise AssertionError("本测试不应该调用GET")

    def close(self) -> None:
        pass


def test_request_upload_urls_succeeds(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """MinerU正常返回时，应正确获得batch_id和上传地址。"""

    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"%PDF test")

    fake_session = FakeSession(
        FakeResponse(
            {
                "code": 0,
                "msg": "ok",
                "data": {
                    "batch_id": "batch-001",
                    "file_urls": [
                        "https://example.com/upload/document"
                    ],
                },
            }
        )
    )

    client = MinerUClient(
        settings,
        session=fake_session,
    )

    submission = client.request_upload_urls(
        [pdf_path]
    )

    assert submission.batch_id == "batch-001"
    assert len(submission.items) == 1

    upload_item = submission.items[0]

    assert upload_item.path == pdf_path.resolve()
    assert upload_item.upload_url == (
        "https://example.com/upload/document"
    )
    assert upload_item.data_id

    # 检查请求地址
    assert fake_session.last_post_url == (
        f"{settings.mineru_base_url}/file-urls/batch"
    )

    # 检查发给MinerU的文件名
    request_json = fake_session.last_post_kwargs["json"]

    assert request_json["files"][0]["name"] == (
        "document.pdf"
    )

    # 检查请求中是否正确携带Token
    request_headers = fake_session.last_post_kwargs[
        "headers"
    ]

    assert request_headers["Authorization"] == (
        "Bearer unit-test-token"
    )


def test_request_upload_urls_rejects_api_error(
    settings: Settings,
    tmp_path: Path,
) -> None:
    """MinerU返回业务错误码时，应抛出MinerUAPIError。"""

    pdf_path = tmp_path / "document.pdf"
    pdf_path.write_bytes(b"%PDF test")

    fake_session = FakeSession(
        FakeResponse(
            {
                "code": -60002,
                "msg": "获取匹配的文件格式失败",
                "data": None,
            }
        )
    )

    client = MinerUClient(
        settings,
        session=fake_session,
    )

    with pytest.raises(
        MinerUAPIError,
        match="申请批量上传地址失败",
    ):
        client.request_upload_urls([pdf_path])