"""HTTP API for browser-based DMF Agent sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from langgraph.types import Command
from pydantic import BaseModel, Field

from src.agent.react_graph import build_react_graph
from src.agent.checkpoint import AgentCheckpoint
from src.dmf_query.constant import OUTPUT_DIR
from src.mineru.routes.convert import save_uploaded_files
from src.services.document_dmf_service import DocumentDMFError, DocumentDMFService
from src.agent_memory.repository import MemoryRepository
from src.agent_session.repository import AgentSessionRepository
from src.settings import load_settings


class MessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=10_000)


class ResumeRequest(BaseModel):
    action: str = ""
    query: dict[str, Any] | None = None


class MemoryRequest(BaseModel):
    content: dict[str, Any]
    idempotency_key: str | None = None


@dataclass
class WebSession:
    session_id: str
    user_id: str = ""
    tenant_id: str = ""
    documents: dict[str, dict[str, Any]] = field(default_factory=dict)
    awaiting_resume: bool = False
    files: dict[str, Path] = field(default_factory=dict)
    lock: RLock = field(default_factory=RLock)


class AgentWebService:
    """Own process-local browser sessions and one checkpointed Agent graph."""

    def __init__(self) -> None:
        self.settings = load_settings(require_token=False)
        self._checkpoint_context = AgentCheckpoint(self.settings)
        memory_repository = (
            MemoryRepository(self.settings.database_url)
            if self.settings.agent_memory_enabled
            else None
        )
        self.memory_repository = memory_repository
        self.session_repository = AgentSessionRepository(self.settings.database_url)
        self.graph = build_react_graph(
            checkpointer=self._checkpoint_context.__enter__(),
            memory_repository=memory_repository,
            memory_limit=self.settings.agent_memory_max_items,
        )
        self.sessions: dict[str, WebSession] = {}
        self.lock = RLock()

    def create_session(self, *, user_id: str = "", tenant_id: str = "") -> WebSession:
        if self.settings.agent_trust_proxy_identity and (not user_id or not tenant_id):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="缺少已认证的代理用户身份",
            )
        if not self.settings.agent_trust_proxy_identity:
            user_id = ""
            tenant_id = ""
        session = WebSession(
            session_id=uuid4().hex, user_id=user_id, tenant_id=tenant_id
        )
        with self.lock:
            self.sessions[session.session_id] = session
        try:
            self.session_repository.create(
                session_id=session.session_id,
                user_id=session.user_id,
                tenant_id=session.tenant_id,
            )
        except Exception:
            with self.lock:
                self.sessions.pop(session.session_id, None)
            raise
        return session

    def get_session(
        self, session_id: str, *, user_id: str = "", tenant_id: str = ""
    ) -> WebSession:
        with self.lock:
            session = self.sessions.get(session_id)
        if session is None:
            persisted = self.session_repository.get(
                session_id,
                user_id=user_id,
                tenant_id=tenant_id,
            )
            if persisted is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Agent 会话不存在或已失效",
                )
            session = WebSession(
                session_id=persisted.session_id,
                user_id=persisted.user_id,
                tenant_id=persisted.tenant_id,
                awaiting_resume=persisted.awaiting_resume,
            )
            state = self.graph.get_state(self._config(session)).values
            session.documents = dict(state.get("document_artifacts") or {})
            with self.lock:
                self.sessions[session.session_id] = session
        if session.user_id and (
            session.user_id != user_id or session.tenant_id != tenant_id
        ):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="无权访问其他用户的 Agent 会话",
            )
        return session

    def identity_from_headers(self, headers: Any) -> tuple[str, str]:
        if not self.settings.agent_trust_proxy_identity:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="未启用可信代理身份，不能使用跨会话记忆",
            )
        user_id = str(headers.get(self.settings.agent_identity_user_header, "")).strip()
        tenant_id = str(headers.get(self.settings.agent_identity_tenant_header, "")).strip()
        if not user_id or not tenant_id:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="缺少已认证的代理用户身份",
            )
        return user_id, tenant_id

    def require_memory_repository(self) -> MemoryRepository:
        if self.memory_repository is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="长期记忆功能未启用",
            )
        return self.memory_repository

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
                    "request_id": uuid4().hex,
                    "document_artifacts": dict(session.documents),
                    "warnings": [],
                    "memory_scope": {
                        "user_id": session.user_id,
                        "tenant_id": session.tenant_id,
                    },
                },
                config=self._config(session),
            )
            return self._response(session, result)

    def invalidate_document_state(self, session: WebSession) -> None:
        current = self.graph.get_state(self._config(session)).values
        active_ids = set(session.documents)
        self.graph.update_state(
            self._config(session),
            {
                "document_artifacts": dict(session.documents),
                "document_extractions": {
                    document_id: extraction
                    for document_id, extraction in (
                        current.get("document_extractions") or {}
                    ).items()
                    if document_id in active_ids
                },
                "document_errors": {
                    document_id: message
                    for document_id, message in (
                        current.get("document_errors") or {}
                    ).items()
                    if document_id in active_ids
                },
                "merged_document_query": {},
                "domain_pending": {key: value for key, value in (current.get("domain_pending") or {}).items() if key != "document"},
                **({"dmf_results": {}, "result_id": "", "result_source": ""} if current.get("result_source") == "document" else {}),
            },
        )

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
        self.session_repository.update_awaiting_resume(
            session.session_id,
            awaiting_resume=session.awaiting_resume,
            user_id=session.user_id,
            tenant_id=session.tenant_id,
        )
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
            if not path.is_file() or not path.is_relative_to(OUTPUT_DIR.resolve()):
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


def _document_summary(artifact: dict[str, Any]) -> dict[str, str]:
    return {
        "document_id": str(artifact.get("document_id", "")),
        "file_name": str(artifact.get("file_name", "")),
    }


def _list_documents(session: WebSession) -> list[dict[str, str]]:
    return [_document_summary(artifact) for artifact in session.documents.values()]


def _ensure_documents_mutable(session: WebSession) -> None:
    if session.awaiting_resume:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="当前操作正在等待确认，暂时不能增删文档",
        )


async def _append_documents(
    session: WebSession,
    files: list[UploadFile],
) -> dict[str, Any]:
    _ensure_documents_mutable(session)
    saved_paths = await save_uploaded_files(files)
    parsed: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    service = DocumentDMFService()
    for file, saved_path in zip(files, saved_paths, strict=True):
        try:
            artifact = await run_in_threadpool(service.parse_document, saved_path)
        except DocumentDMFError as exc:
            errors.append({"file_name": file.filename or saved_path.name, "message": str(exc)})
            continue
        parsed.append(artifact.model_dump())

    with session.lock:
        _ensure_documents_mutable(session)
        for artifact in parsed:
            session.documents[artifact["document_id"]] = artifact
        if parsed:
            agent_web_service.graph.update_state(
                agent_web_service._config(session),
                {"document_artifacts": dict(session.documents)},
            )
        documents = _list_documents(session)
    return {"status": "parsed", "documents": documents, "errors": errors}


@router.post("/sessions", status_code=status.HTTP_201_CREATED)
def create_session(request: Request) -> dict[str, str]:
    authenticated_user, authenticated_tenant = agent_web_service.identity_from_headers(
        request.headers
    ) if agent_web_service.settings.agent_trust_proxy_identity else ("", "")
    session = agent_web_service.create_session(
        user_id=authenticated_user or "", tenant_id=authenticated_tenant or ""
    )
    return {"session_id": session.session_id}


@router.get("/memory")
def list_memory(request: Request) -> dict[str, Any]:
    user_id, tenant_id = agent_web_service.identity_from_headers(request.headers)
    repository = agent_web_service.require_memory_repository()
    return {
        "memories": [
            {
                "memory_key": memory.memory_key,
                "memory_type": memory.memory_type,
                "content": memory.content,
                "version": memory.version,
                "updated_at": memory.updated_at.isoformat(),
            }
            for memory in repository.list_active(
                tenant_id=tenant_id,
                user_id=user_id,
                limit=agent_web_service.settings.agent_memory_max_items,
            )
        ]
    }


@router.put("/memory/{memory_key}")
def remember(
    memory_key: str, request: MemoryRequest, http_request: Request
) -> dict[str, Any]:
    user_id, tenant_id = agent_web_service.identity_from_headers(http_request.headers)
    try:
        memory = agent_web_service.require_memory_repository().remember(
            tenant_id=tenant_id,
            user_id=user_id,
            memory_key=memory_key,
            content=request.content,
            idempotency_key=request.idempotency_key,
            audit_note="explicit_user_api",
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    return {
        "memory_key": memory.memory_key,
        "content": memory.content,
        "version": memory.version,
    }


@router.delete("/memory/{memory_key}")
def forget(memory_key: str, request: Request) -> dict[str, Any]:
    user_id, tenant_id = agent_web_service.identity_from_headers(request.headers)
    try:
        deleted = agent_web_service.require_memory_repository().forget(
            tenant_id=tenant_id, user_id=user_id, memory_key=memory_key
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="记忆不存在"
        )
    return {"status": "deleted", "memory_key": memory_key}


@router.post("/sessions/{session_id}/messages")
def send_message(
    session_id: str,
    request: MessageRequest,
    http_request: Request,
) -> dict[str, Any]:
    authenticated_user, authenticated_tenant = (
        agent_web_service.identity_from_headers(http_request.headers)
        if agent_web_service.settings.agent_trust_proxy_identity
        else ("", "")
    )
    session = agent_web_service.get_session(
        session_id, user_id=authenticated_user or "", tenant_id=authenticated_tenant or ""
    )
    return agent_web_service.invoke(session, request.message)


@router.post("/sessions/{session_id}/resume")
def resume_session(
    session_id: str,
    request: ResumeRequest,
    http_request: Request,
) -> dict[str, Any]:
    authenticated_user, authenticated_tenant = (
        agent_web_service.identity_from_headers(http_request.headers)
        if agent_web_service.settings.agent_trust_proxy_identity
        else ("", "")
    )
    session = agent_web_service.get_session(
        session_id, user_id=authenticated_user or "", tenant_id=authenticated_tenant or ""
    )
    return agent_web_service.resume(session, request)


@router.get("/sessions/{session_id}/documents")
def list_documents(session_id: str) -> dict[str, Any]:
    session = agent_web_service.get_session(session_id)
    with session.lock:
        return {"documents": _list_documents(session)}


@router.post("/sessions/{session_id}/documents")
async def upload_documents(
    session_id: str,
    files: list[UploadFile] = File(...),
) -> dict[str, Any]:
    session = agent_web_service.get_session(session_id)
    return await _append_documents(session, files)


@router.post("/sessions/{session_id}/document")
async def upload_document(session_id: str, file: UploadFile) -> dict[str, Any]:
    session = agent_web_service.get_session(session_id)
    result = await _append_documents(session, [file])
    if result["errors"]:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=result["errors"][0]["message"],
        )
    return {**result, "document": result["documents"][-1]}


@router.delete("/sessions/{session_id}/documents/{document_id}")
def delete_document(session_id: str, document_id: str) -> dict[str, Any]:
    session = agent_web_service.get_session(session_id)
    with session.lock:
        _ensure_documents_mutable(session)
        if document_id not in session.documents:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="文档不存在或已被删除",
            )
        del session.documents[document_id]
        agent_web_service.invalidate_document_state(session)
        return {"status": "deleted", "documents": _list_documents(session)}


@router.delete("/sessions/{session_id}/documents")
def clear_documents(session_id: str) -> dict[str, Any]:
    session = agent_web_service.get_session(session_id)
    with session.lock:
        _ensure_documents_mutable(session)
        session.documents.clear()
        agent_web_service.invalidate_document_state(session)
        return {"status": "cleared", "documents": []}


@router.delete("/sessions/{session_id}/document")
def clear_document(session_id: str) -> dict[str, str]:
    session = agent_web_service.get_session(session_id)
    with session.lock:
        _ensure_documents_mutable(session)
        session.documents.clear()
        agent_web_service.invalidate_document_state(session)
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