"""MinerU批量文档解析Web服务入口。"""

from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from src.mineru.routes.convert import router as convert_router
from src.settings import get_settings

# 读取项目配置
settings = get_settings()


# StaticFiles在挂载时要求目录已经存在
settings.static_dir.mkdir(
    parents=True,
    exist_ok=True,
)

settings.template_dir.mkdir(
    parents=True,
    exist_ok=True,
)


@asynccontextmanager
async def lifespan(
    app: FastAPI,
) -> AsyncIterator[None]:
    """管理FastAPI应用的启动和关闭。"""

    # 创建上传、结果和临时目录
    settings.ensure_directories()

    yield


app = FastAPI(
    title="MinerU批量文档解析服务",
    description=(
        "支持一次上传多个文档，"
        "调用MinerU精准解析API生成Markdown。"
    ),
    version="1.0.0",
    lifespan=lifespan,
)


# 挂载CSS和JavaScript静态资源
app.mount(
    "/static",
    StaticFiles(
        directory=str(settings.static_dir)
    ),
    name="static",
)


# 注册文档解析API
app.include_router(convert_router)


@app.get(
    "/",
    include_in_schema=False,
)
def index() -> FileResponse:
    """返回用户操作首页。"""

    return FileResponse(
        path=settings.template_dir / "index.html",
        media_type="text/html",
    )


@app.get(
    "/health",
    tags=["系统"],
)
def health_check() -> dict[str, str]:
    """服务健康检查。"""

    return {
        "status": "ok",
        "service": "mineru-demo",
    }