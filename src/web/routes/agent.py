"""HTTP API for browser-based DMF Agent sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, HTTPException, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import BaseModel, Field

from src.agent.graph import build_research_graph
from src.mineru.routes.convert import save_uploaded_files
from src.services.document_dmf_service import DocumentDMFError, DocumentDMFService


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)


class ResumeRequest(BaseModel):
    action: str = ""
    query: dict[str, Any] | None = None


@dataclass
class WebSession:
    session_id: str
    active_document: dict[str, Any] = field(default_factory=dict)
    awaiting_resume: bool = False
    files: dict[str, Path] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock)


class AgentWebService:
    """Own process-local browser sessions and one checkpointed Agent graph."""

    def __init__(self) -> None:
        self.graph = build_research_graph(checkpointer=InMemorySaver())
        self.sessions: dict[str, WebSession] = {}
        self.lock = RLock()

    def create_session(self) -> WebSession:
        session = WebSession(session_id=uuid4().hex)
        with self.lock:
            self.sessions[session.session_id] = session
        return session

    def get_session(self, session_id: str) -> WebSession:
        with self.lock:
            session = self.sessions.get(session_id)
        if session is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Agent 会话不存在或已失效",
            )
        return session

    def invoke(self, session: WebSession, message: str) -> dict[str, Any]:
        with session.lock:
            if session.awaiting_resume:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="当前操作正在等待确认，请先确认或拒绝",
                )
            result = self.graph.invoke(
                {
                    "user_query": message.strip(),
                    "document_artifact": session.active_document,
                    "document_status": session.active_document.get("status", ""),
                    "warnings": [],
                },
                config=self._config(session),
            )
            return self._response(session, result)

    def resume(self, session: WebSession, request: ResumeRequest) -> dict[str, Any]:
        with session.lock:
            if not session.awaiting_resume:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="当前会话没有等待确认的操作",
                )
            payload: dict[str, Any] = {"action": request.action}
            if request.query is not None:
                payload["query"] = request.query
            result = self.graph.invoke(
                Command(resume=payload),
                config=self._config(session),
            )
            return self._response(session, result)

    def _response(self, session: WebSession, result: dict[str, Any]) -> dict[str, Any]:
        interrupts = result.get("__interrupt__") or []
        interrupt_value = interrupts[0].value if interrupts else None
        session.awaiting_resume = interrupt_value is not None
        downloads = self._register_downloads(session, result)
        return {
            "status": "awaiting_confirmation" if interrupt_value else "completed",
            "answer": result.get("final_answer", ""),
            "interrupt": interrupt_value,
            "downloads": downloads,
        }

    @staticmethod
    def _config(session: WebSession) -> dict[str, dict[str, str]]:
        return {"configurable": {"thread_id": session.session_id}}

    @staticmethod
    def _register_downloads(
        session: WebSession,
        result: dict[str, Any],
    ) -> list[dict[str, str]]:
        downloads: list[dict[str, str]] = []
        for artifact in result.get("tool_artifacts") or []:
            if artifact.get("tool_name") != "export_dmf_excel":
                continue
            export_result = artifact.get("result") or {}
            if not export_result.get("success"):
                continue
            path = Path(export_result.get("file_path", "")).resolve()
            if not path.is_file():
                continue
            existing_file_id = next(
                (
                    registered_id
                    for registered_id, registered_path in session.files.items()
                    if registered_path == path
                ),
                "",
            )
            if existing_file_id:
                continue
            file_id = uuid4().hex
            session.files[file_id] = path
            downloads.append(
                {
                    "file_id": file_id,
                    "file_name": path.name,
                    "url": f"/api/agent/sessions/{session.session_id}/files/{file_id}",
                }
            )
        return downloads


router = APIRouter(prefix="/api/agent", tags=["DMF Agent"])
agent_web_service = AgentWebService()


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session() -> dict[str, str]:
    session = agent_web_service.create_session()
    return {"session_id": session.session_id}


@router.post("/sessions/{session_id}/messages")
def send_message(session_id: str, request: MessageRequest) -> dict[str, Any]:
    session = agent_web_service.get_session(session_id)
    return agent_web_service.invoke(session, request.message)


@router.post("/sessions/{session_id}/resume")
def resume_session(session_id: str, request: ResumeRequest) -> dict[str, Any]:
    session = agent_web_service.get_session(session_id)
    return agent_web_service.resume(session, request)


@router.post("/sessions/{session_id}/document")
async def upload_document(session_id: str, file: UploadFile) -> dict[str, Any]:
    session = agent_web_service.get_session(session_id)
    if session.awaiting_resume:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前操作正在等待确认，暂时不能替换文档",
        )
    saved_paths = await save_uploaded_files([file])
    try:
        artifact = await run_in_threadpool(
            DocumentDMFService().parse_document,
            saved_paths[0],
        )
    except DocumentDMFError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    with session.lock:
        session.active_document = artifact.model_dump()
    return {
        "status": "parsed",
        "document": {
            "document_id": artifact.document_id,
            "file_name": artifact.file_name,
        },
    }


@router.delete("/sessions/{session_id}/document")
def clear_document(session_id: str) -> dict[str, str]:
    session = agent_web_service.get_session(session_id)
    if session.awaiting_resume:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前操作正在等待确认，暂时不能清除文档",
        )
    with session.lock:
        session.active_document = {}
    return {"status": "cleared"}


@router.get("/sessions/{session_id}/files/{file_id}")
def download_file(session_id: str, file_id: str) -> FileResponse:
    session = agent_web_service.get_session(session_id)
    path = session.files.get(file_id)
    if path is None or not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="导出文件不存在或已失效",
        )
    return FileResponse(
        path=path,
        filename=path.name,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )