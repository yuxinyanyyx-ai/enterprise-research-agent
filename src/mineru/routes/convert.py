"""文档批量解析 Web API"""

import shutil
import uuid
from pathlib import Path

from fastapi import (
    APIRouter,
    BackgroundTasks,
    HTTPException,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse, Response


from src.settings import get_settings
from src.mineru.services.task_manager import (
    ResultNotReadyError,
    TaskManager,
    TaskNotFoundError,
)

# 读取项目配置
settings = get_settings()

# 创建任务管理器
task_manager = TaskManager(settings)

# 创建FastAPI路由
router = APIRouter(
    prefix="/api",
    tags=["文档解析"],
)


async def save_uploaded_files(
    files: list[UploadFile],
) -> tuple[Path, ...]:
    """把用户上传的多个文件保存到本地。

    保存过程中检查：
    1. 是否上传文件；
    2. 文件数量；
    3. 文件名；
    4. 文件扩展名；
    5. 是否存在同名文件；
    6. 单文件大小；
    7. 批次总大小；
    8. 是否为空文件。
    """

    if not files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="请至少上传一个文件",
        )

    if len(files) > settings.max_batch_files:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"每批最多上传"
                f"{settings.max_batch_files}个文件"
            ),
        )

    # 每一次上传使用独立目录，避免不同批次的文件互相覆盖
    upload_group_id = uuid.uuid4().hex

    upload_group_dir = (
        settings.upload_dir / upload_group_id
    )

    upload_group_dir.mkdir(
        parents=True,
        exist_ok=False,
    )

    saved_paths: list[Path] = []

    # 使用casefold避免Windows下A.pdf和a.pdf发生冲突
    used_names: set[str] = set()

    total_size = 0

    try:
        for upload_file in files:
            # 只保留文件名，去掉用户文件名中可能携带的目录
            safe_name = Path(
                upload_file.filename or ""
            ).name.strip()

            if not safe_name:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="上传文件缺少文件名",
                )

            name_key = safe_name.casefold()

            if name_key in used_names:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"同一批次不能包含同名文件："
                        f"{safe_name}"
                    ),
                )

            used_names.add(name_key)

            suffix = Path(safe_name).suffix.lower()

            if suffix not in settings.allowed_extensions:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=(
                        f"不支持的文件格式：{safe_name}"
                    ),
                )

            destination = (
                upload_group_dir / safe_name
            )

            file_size = 0

            # 分块保存文件，避免大文件一次性加载到内存
            with destination.open("wb") as output:
                while True:
                    chunk = await upload_file.read(
                        1024 * 1024
                    )

                    if not chunk:
                        break

                    file_size += len(chunk)
                    total_size += len(chunk)

                    if (
                        file_size
                        > settings.max_file_size_bytes
                    ):
                        raise HTTPException(
                            status_code=(
                                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                            ),
                            detail=(
                                f"文件超过大小限制："
                                f"{safe_name}"
                            ),
                        )

                    if (
                        total_size
                        > settings.max_batch_size_bytes
                    ):
                        raise HTTPException(
                            status_code=(
                                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE
                            ),
                            detail="本批次文件总大小超过限制",
                        )

                    output.write(chunk)

            if file_size == 0:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"文件为空：{safe_name}",
                )

            saved_paths.append(destination)

        return tuple(saved_paths)

    except Exception:
        # 任意一个文件保存失败，就删除整个上传批次
        shutil.rmtree(
            upload_group_dir,
            ignore_errors=True,
        )
        raise

    finally:
        # 释放FastAPI上传文件占用的临时资源
        for upload_file in files:
            await upload_file.close()


@router.post(
    "/tasks",
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_parse_task(
    background_tasks: BackgroundTasks,
    files: list[UploadFile],
) -> dict[str, object]:
    """上传多个文件并创建后台解析任务。

    接口不会等待MinerU解析完成，
    而是立即返回网站自己的task_id。
    """

    saved_paths = await save_uploaded_files(
        files
    )

    try:
        task_id = task_manager.create_task(
            saved_paths
        )

    except ValueError as exc:
        # 任务创建失败时删除已经保存的上传文件
        if saved_paths:
            shutil.rmtree(
                saved_paths[0].parent,
                ignore_errors=True,
            )

        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc

    # run_task是同步阻塞函数。
    # FastAPI会在响应返回后，通过后台线程执行它。
    background_tasks.add_task(
        task_manager.run_task,
        task_id,
        saved_paths,
    )

    return {
        "task_id": task_id,
        "state": "queued",
        "message": "文件上传完成，解析任务已创建",
        "file_count": len(saved_paths),
    }


@router.get("/tasks/{task_id}")
def get_parse_task(
    task_id: str,
) -> dict[str, object]:
    """查询批量任务及每个文件的解析状态。"""

    try:
        return task_manager.get_task_snapshot(
            task_id
        )

    except TaskNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get(
    "/tasks/{task_id}/files/{data_id}/markdown"
)
def preview_markdown(
    task_id: str,
    data_id: str,
) -> Response:
    """获取某个文件的Markdown文本，用于网页预览。"""

    try:
        markdown_path = (
            task_manager.get_markdown_path(
                task_id,
                data_id,
            )
        )

        markdown = markdown_path.read_text(
            encoding="utf-8-sig"
        )

        return Response(
            content=markdown,
            media_type="text/markdown",
        )

    except TaskNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except ResultNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc

    except OSError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_500_INTERNAL_SERVER_ERROR
            ),
            detail=f"读取Markdown失败：{exc}",
        ) from exc


@router.get(
    "/tasks/{task_id}/files/{data_id}/download"
)
def download_markdown(
    task_id: str,
    data_id: str,
) -> FileResponse:
    """下载某个文件对应的Markdown文件。"""

    try:
        markdown_path = (
            task_manager.get_markdown_path(
                task_id,
                data_id,
            )
        )

        snapshot = (
            task_manager.get_task_snapshot(
                task_id
            )
        )

        file_info = next(
            (
                item
                for item in snapshot["files"]
                if item["data_id"] == data_id
            ),
            None,
        )

        if file_info is None:
            raise TaskNotFoundError(
                f"任务中不存在文件：{data_id}"
            )

        original_name = str(
            file_info["file_name"]
        )

        download_name = (
            f"{Path(original_name).stem}.md"
        )

        return FileResponse(
            path=markdown_path,
            media_type="text/markdown",
            filename=download_name,
        )

    except TaskNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc

    except ResultNotReadyError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc