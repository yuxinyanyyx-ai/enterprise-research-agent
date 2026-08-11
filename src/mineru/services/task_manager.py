"""批量文档解析任务管理。"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


from src.settings import Settings
from src.mineru.services.mineru_client import (
    MinerUClient,
    MinerUError,
    ParseResult,
    ProgressEvent,
)


class TaskNotFoundError(RuntimeError):
    """任务不存在。"""


class ResultNotReadyError(RuntimeError):
    """文件解析结果尚未准备好。"""


@dataclass(slots=True)
class FileTaskState:
    """一个文件在批量任务中的状态。"""

    file_name: str
    state: str = "queued"
    message: str = "等待处理"

    data_id: str | None = None
    markdown_path: Path | None = None
    error: str | None = None


@dataclass(slots=True)
class ParseTask:
    """一次用户提交的批量解析任务。"""

    task_id: str
    state: str = "queued"
    message: str = "任务已创建"

    batch_id: str | None = None

    files: dict[str, FileTaskState] = field(
        default_factory=dict
    )


class TaskManager:
    """管理 Web 层批量解析任务。"""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

        # 当前进程中的任务表
        self._tasks: dict[str, ParseTask] = {}

        # 防止后台线程与 HTTP 请求同时修改任务状态
        self._lock = threading.RLock()

    def create_task(
        self,
        paths: Iterable[Path],
    ) -> str:
        """创建一个新的批量解析任务。"""

        files = tuple(paths)

        if not files:
            raise ValueError("至少需要一个文件")

        names = [path.name for path in files]

        # 当前版本不允许一次上传两个完全同名文件
        if len(names) != len(set(names)):
            raise ValueError("同一批次中不能存在同名文件")

        task_id = uuid.uuid4().hex

        task = ParseTask(
            task_id=task_id,
            files={
                path.name: FileTaskState(
                    file_name=path.name
                )
                for path in files
            },
        )

        with self._lock:
            self._tasks[task_id] = task

        return task_id

    def run_task(
        self,
        task_id: str,
        paths: Iterable[Path],
    ) -> None:
        """执行批量解析。

        这是同步函数，后续由 FastAPI 放到后台线程运行。
        """

        files = tuple(paths)

        self._update_task(
            task_id,
            state="running",
            message="正在提交 MinerU 解析",
        )

        def on_progress(event: ProgressEvent) -> None:
            """接收 MinerUClient 发出的进度事件。"""

            with self._lock:
                task = self._get_task(task_id)

                if event.batch_id:
                    task.batch_id = event.batch_id

                task.message = event.message

                if event.file_name:
                    file_state = task.files.get(
                        event.file_name
                    )

                    if file_state is not None:
                        file_state.message = event.message

                        if event.data_id:
                            file_state.data_id = event.data_id

                        # MinerU 有明确状态时优先使用
                        file_state.state = (
                            event.state or event.stage
                        )

        try:
            with MinerUClient(
                self.settings,
                progress_callback=on_progress,
            ) as client:

                result = client.parse_files(files)

            # 保存 MinerU batch_id
            with self._lock:
                task = self._get_task(task_id)
                task.batch_id = result.batch_id

            # 保存每一个文件的最终结果
            for parse_result in result.files:
                self._save_file_result(
                    task_id,
                    parse_result,
                )

            # 计算整个批次的最终状态
            success_count = sum(
                1
                for item in result.files
                if item.succeeded
            )

            if success_count == len(result.files):
                final_state = "done"
                message = "全部文件解析完成"

            elif success_count > 0:
                final_state = "partial_failed"
                message = "部分文件解析失败"

            else:
                final_state = "failed"
                message = "全部文件解析失败"

            self._update_task(
                task_id,
                state=final_state,
                message=message,
            )

        except MinerUError as exc:
            self._update_task(
                task_id,
                state="failed",
                message=str(exc),
            )

        except Exception as exc:
            # 防止后台任务异常后一直显示 running
            self._update_task(
                task_id,
                state="failed",
                message=f"任务执行失败：{exc}",
            )

    def _save_file_result(
        self,
        task_id: str,
        result: ParseResult,
    ) -> None:
        """保存单个文件的最终解析结果。"""

        with self._lock:
            task = self._get_task(task_id)

            file_state = task.files.get(
                result.original_name
            )

            if file_state is None:
                return

            file_state.data_id = result.data_id
            file_state.state = result.state
            file_state.markdown_path = result.markdown_path
            file_state.error = result.error

            if result.succeeded:
                file_state.message = "解析完成"
            else:
                file_state.message = (
                    result.error or "解析失败"
                )

    def _update_task(
        self,
        task_id: str,
        *,
        state: str,
        message: str,
    ) -> None:
        """更新任务整体状态。"""

        with self._lock:
            task = self._get_task(task_id)
            task.state = state
            task.message = message

    def _get_task(
        self,
        task_id: str,
    ) -> ParseTask:
        """获取内部任务对象。"""

        task = self._tasks.get(task_id)

        if task is None:
            raise TaskNotFoundError(
                f"任务不存在：{task_id}"
            )

        return task

    def get_task_snapshot(
        self,
        task_id: str,
    ) -> dict[str, Any]:
        """返回适合 FastAPI 输出给前端的任务状态。"""

        with self._lock:
            task = self._get_task(task_id)

            return {
                "task_id": task.task_id,
                "batch_id": task.batch_id,
                "state": task.state,
                "message": task.message,
                "files": [
                    {
                        "file_name": item.file_name,
                        "data_id": item.data_id,
                        "state": item.state,
                        "message": item.message,
                        "error": item.error,
                        "has_markdown": (
                            item.markdown_path is not None
                        ),
                    }
                    for item in task.files.values()
                ],
            }

    def get_markdown_path(
        self,
        task_id: str,
        data_id: str,
    ) -> Path:
        """取得某个文件最终 Markdown 的本地路径。"""

        with self._lock:
            task = self._get_task(task_id)

            for item in task.files.values():
                if item.data_id != data_id:
                    continue

                if item.markdown_path is None:
                    raise ResultNotReadyError(
                        "该文件的 Markdown 尚未生成"
                    )

                return item.markdown_path

        raise TaskNotFoundError(
            f"任务中不存在文件：{data_id}"
        )