"""Browse a configured SharePoint site and copy supported files into sources.

Credentials stay in environment variables. Parsing and indexing still see only
local files under the knowledge sources directory.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
import time
import uuid
from pathlib import Path
from urllib.parse import quote, urlparse

import requests

from src.pec.knowledge.paths import SUPPORTED_SUFFIXES

GRAPH_BASE = "https://graph.microsoft.com/v1.0"
# v1.0 /content redirects to sharepoint.com. That hop is reset inside the cluster.
# Beta contentStream returns the bytes from graph.microsoft.com itself.
GRAPH_BETA_BASE = "https://graph.microsoft.com/beta"
SCOPE = "https://graph.microsoft.com/.default"
REQUEST_TIMEOUT = (30, 300)
MAX_FILES = 5000
MAX_BYTES = 2 * 1024 * 1024 * 1024
_INVALID_NAME_CHARS = set('<>:"|?*')
_ENV_KEYS = (
    "SHAREPOINT_TENANT_ID",
    "SHAREPOINT_CLIENT_ID",
    "SHAREPOINT_CLIENT_SECRET",
    "SHAREPOINT_SITE_URL",
)


class SharePointError(RuntimeError):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


def public_error(exc: BaseException) -> str:
    if isinstance(exc, (SharePointError, ValueError, RuntimeError)):
        return str(exc)
    if isinstance(exc, requests.RequestException):
        return "无法连接 SharePoint，请检查网络后重试。"
    if isinstance(exc, OSError):
        return f"保存文件失败：{exc.strerror or exc}"
    return str(exc)[:300]


def _response_json(response: requests.Response) -> dict:
    try:
        data = response.json()
    except ValueError as exc:
        raise SharePointError("SharePoint 返回了无法识别的内容") from exc
    if not isinstance(data, dict):
        raise SharePointError("SharePoint 返回了无法识别的内容")
    return data


def configured() -> bool:
    return all(os.getenv(key, "").strip() for key in _ENV_KEYS)


def site_label() -> str:
    raw = os.getenv("SHAREPOINT_SITE_URL", "").strip()
    parsed = urlparse(raw)
    if not parsed.netloc:
        return ""
    return f"{parsed.netloc}{parsed.path}".rstrip("/")


def normalize_remote(path: str) -> str:
    path = (path or "").strip().replace("\\", "/").strip("/")
    parts = [part for part in path.split("/") if part]
    if any(part in {".", ".."} for part in parts):
        raise ValueError("文件夹路径无效")
    for part in parts:
        if not _safe_name(part):
            raise ValueError("文件夹路径包含不能保存到本地的名称")
    return "/".join(parts)


def _safe_name(name: str) -> bool:
    if not name or name in {".", ".."} or name.startswith("~$") or name.startswith("."):
        return False
    return not any(char in _INVALID_NAME_CHARS or ord(char) < 32 for char in name)


def _encode_drive_path(path: str) -> str:
    return quote(path.strip("/"), safe="/")


def _parse_site_url(site_url: str) -> tuple[str, str]:
    parsed = urlparse(site_url.rstrip("/"))
    if not parsed.netloc or not parsed.path:
        raise SharePointError("SHAREPOINT_SITE_URL 无效", 500)
    return parsed.netloc, parsed.path.lstrip("/")


def _win_long_path(path: Path) -> str:
    resolved = str(path.resolve())
    if os.name != "nt" or resolved.startswith("\\\\?\\"):
        return resolved
    if resolved.startswith("\\\\"):
        return "\\\\?\\UNC\\" + resolved.lstrip("\\")
    return "\\\\?\\" + resolved


class SharePointClient:
    def __init__(self, tenant_id: str, client_id: str, client_secret: str, site_url: str):
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.client_secret = client_secret
        self.site_host, self.site_path = _parse_site_url(site_url)
        self._token = ""
        self._obtained_at = 0.0
        self._site_id = ""
        self._lock = threading.Lock()

    @classmethod
    def from_env(cls) -> "SharePointClient":
        if not configured():
            raise SharePointError("SharePoint 尚未配置，无法浏览或导入", 503)
        return cls(*(os.environ[key].strip() for key in _ENV_KEYS))

    def _token_value(self, *, force: bool = False) -> str:
        with self._lock:
            if force or not self._token or (time.time() - self._obtained_at) > 45 * 60:
                try:
                    response = requests.post(
                        f"https://login.microsoftonline.com/{self.tenant_id}/oauth2/v2.0/token",
                        data={
                            "client_id": self.client_id,
                            "client_secret": self.client_secret,
                            "scope": SCOPE,
                            "grant_type": "client_credentials",
                        },
                        timeout=(30, 60),
                    )
                except requests.RequestException as exc:
                    raise SharePointError("无法连接 SharePoint，请检查网络后重试。") from exc
                if response.status_code != 200:
                    raise SharePointError("SharePoint 登录失败，请检查应用凭据是否仍然有效")
                token = _response_json(response).get("access_token")
                if not token:
                    raise SharePointError("SharePoint 登录没有返回访问令牌")
                self._token = token
                self._obtained_at = time.time()
            return self._token

    def _get(
        self,
        url: str,
        *,
        stream: bool = False,
        allow_redirects: bool = True,
        auth: bool = True,
    ) -> requests.Response:
        def fetch(token: str) -> requests.Response:
            headers = {"Authorization": f"Bearer {token}"} if auth else {}
            try:
                return requests.get(
                    url,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT,
                    stream=stream,
                    allow_redirects=allow_redirects,
                )
            except requests.RequestException as exc:
                raise SharePointError("无法连接 SharePoint，请检查网络后重试。") from exc

        token = self._token_value() if auth else ""
        response = fetch(token)
        if auth and response.status_code == 401:
            response.close()
            response = fetch(self._token_value(force=True))
        return response

    def site_id(self) -> str:
        if self._site_id:
            return self._site_id
        response = self._get(f"{GRAPH_BASE}/sites/{self.site_host}:/{self.site_path}:")
        if response.status_code != 200:
            raise SharePointError(f"无法打开 SharePoint 站点（{response.status_code}）")
        self._site_id = _response_json(response).get("id") or ""
        if not self._site_id:
            raise SharePointError("无法打开 SharePoint 站点")
        return self._site_id

    def list_children(self, folder_path: str) -> list[dict]:
        folder_path = normalize_remote(folder_path)
        site_id = self.site_id()
        if folder_path:
            url = f"{GRAPH_BASE}/sites/{site_id}/drive/root:/{_encode_drive_path(folder_path)}:/children"
        else:
            url = f"{GRAPH_BASE}/sites/{site_id}/drive/root/children"
        items: list[dict] = []
        while url:
            response = self._get(url)
            if response.status_code == 404:
                raise SharePointError("SharePoint 文件夹不存在", 404)
            if response.status_code != 200:
                raise SharePointError(f"列出 SharePoint 文件夹失败（{response.status_code}）")
            data = _response_json(response)
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
        return items

    def browse(self, folder_path: str = "") -> dict:
        folder_path = normalize_remote(folder_path)
        folders = []
        files = []
        for item in self.list_children(folder_path):
            name = item.get("name") or ""
            if not _safe_name(name):
                continue
            child = f"{folder_path}/{name}".strip("/")
            if "folder" in item:
                folders.append({"name": name, "path": child})
            elif "file" in item:
                files.append({
                    "name": name,
                    "size": int(item.get("size") or 0),
                    "supported": Path(name).suffix.lower() in SUPPORTED_SUFFIXES,
                })
        folders.sort(key=lambda item: item["name"].casefold())
        files.sort(key=lambda item: item["name"].casefold())
        return {"path": folder_path, "folders": folders, "files": files}

    def import_folder(self, folder_path: str, sources_dir: Path, progress=None) -> dict:
        folder_path = normalize_remote(folder_path)
        if not folder_path:
            raise ValueError("请先选择一个 SharePoint 文件夹")
        sources_dir = Path(sources_dir)
        sources_dir.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix=".sharepoint-", dir=sources_dir))
        stats = {"downloaded": 0, "skipped": 0, "failed": [], "bytes": 0}
        try:
            self._collect(folder_path, staging, sources_dir, stats, progress)
            if stats["downloaded"] == 0 and stats["skipped"] == 0 and not stats["failed"]:
                raise ValueError("该文件夹没有可入库的 PPTX、DOCX、PDF 或 XLSX")
            if stats["downloaded"]:
                from src.pec.knowledge.storage import WriterLock
                lock = WriterLock()
                try:
                    self._publish(staging, sources_dir)
                finally:
                    lock.close()
        finally:
            shutil.rmtree(_win_long_path(staging), ignore_errors=True)
        return {"ok": True, "folder": folder_path, **stats}

    def _collect(self, remote_folder: str, staging: Path, sources_dir: Path, stats: dict, progress=None) -> None:
        for item in self.list_children(remote_folder):
            name = item.get("name") or ""
            if not _safe_name(name):
                continue
            remote_child = f"{remote_folder}/{name}".strip("/")
            if "folder" in item:
                self._collect(remote_child, staging, sources_dir, stats, progress)
                continue
            if "file" not in item or Path(name).suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            self._download_one(item, remote_child, staging, sources_dir, stats)
            if progress:
                progress(remote_child, stats)

    def _download_one(self, item: dict, remote_child: str, staging: Path, sources_dir: Path, stats: dict) -> None:
        size = int(item.get("size") or 0)
        if stats["downloaded"] + stats["skipped"] >= MAX_FILES or stats["bytes"] + size > MAX_BYTES:
            raise ValueError("所选文件夹超过 5000 个文件或 2 GB，请改选更小的文件夹")
        local = sources_dir / remote_child
        if _existing_size(local) == size and size > 0:
            stats["skipped"] += 1
            return
        target = staging / remote_child
        try:
            self._download(remote_child, target)
        except SharePointError as exc:
            stats["failed"].append(remote_child)
            if len(stats["failed"]) > 50:
                raise SharePointError(f"下载失败过多，已停止：{exc}") from exc
            return
        stats["downloaded"] += 1
        stats["bytes"] += size

    def _download(self, remote_path: str, local_path: Path) -> None:
        encoded = _encode_drive_path(remote_path)
        site = self.site_id()
        stream_url = f"{GRAPH_BETA_BASE}/sites/{site}/drive/root:/{encoded}:/contentStream"
        content_url = f"{GRAPH_BASE}/sites/{site}/drive/root:/{encoded}:/content"
        parent = Path(_win_long_path(local_path.parent))
        parent.mkdir(parents=True, exist_ok=True)
        temporary = Path(_win_long_path(local_path.with_suffix(local_path.suffix + ".part")))
        response = self._get(stream_url, stream=True, allow_redirects=False)
        try:
            if response.status_code in (404, 405):
                response.close()
                response = self._open_redirected_content(content_url, remote_path)
            self._write_response(response, remote_path, temporary, local_path)
        finally:
            response.close()
            Path(str(temporary)).unlink(missing_ok=True)

    def _open_redirected_content(self, content_url: str, remote_path: str) -> requests.Response:
        response = self._get(content_url, stream=True, allow_redirects=False)
        if response.status_code in (301, 302, 303, 307, 308):
            location = response.headers.get("Location")
            response.close()
            if not location:
                raise SharePointError(f"下载失败 {remote_path}：没有跳转地址")
            return self._get(location, stream=True, auth=False)
        return response

    def _write_response(
        self,
        response: requests.Response,
        remote_path: str,
        temporary: Path,
        local_path: Path,
    ) -> None:
        try:
            if response.status_code != 200:
                raise SharePointError(f"下载失败 {remote_path}（{response.status_code}）")
            with open(temporary, "wb") as handle:
                for chunk in response.iter_content(chunk_size=1024 * 256):
                    if chunk:
                        handle.write(chunk)
            os.replace(str(temporary), _win_long_path(local_path))
        except requests.RequestException as exc:
            raise SharePointError(f"下载中断 {remote_path}") from exc
        except OSError as exc:
            raise SharePointError(f"保存文件失败 {remote_path}：{exc.strerror or exc}") from exc

    def _publish(self, staging: Path, sources_dir: Path) -> None:
        root = _win_long_path(staging)
        sources_root = sources_dir.resolve()
        for dirpath, _, filenames in os.walk(root):
            for name in filenames:
                source = os.path.join(dirpath, name)
                relative = os.path.relpath(source, root).replace("\\", "/")
                target = sources_dir / relative
                if not target.resolve().is_relative_to(sources_root):
                    raise ValueError("下载目标越出资料目录")
                os.makedirs(_win_long_path(target.parent), exist_ok=True)
                os.replace(source, _win_long_path(target))


def _existing_size(path: Path) -> int | None:
    try:
        candidate = Path(_win_long_path(path))
        if candidate.is_file():
            return candidate.stat().st_size
    except OSError:
        return None
    return None


def provider_status() -> dict:
    if not configured():
        return {
            "id": "sharepoint",
            "title": "SharePoint",
            "status": "entry_only",
            "can_import": False,
            "message": "入口已预留，尚未连接 SharePoint；当前不会读取或导入任何内容。",
        }
    return {
        "id": "sharepoint",
        "title": "SharePoint",
        "status": "available",
        "can_import": True,
        "site": site_label(),
        "message": "可以选择站点文件夹，下载到本地资料目录后再入库。",
    }


_IMPORT_GUARD = threading.Lock()
_IMPORT_ACTIVE = False


def _job_file(job_id: str) -> Path:
    from src.pec.knowledge.paths import INDEX_DIR
    return INDEX_DIR / "sharepoint-jobs" / f"{job_id}.json"


def _save_job(job: dict) -> None:
    path = _job_file(job["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, path)


def _reconcile_imports() -> None:
    root = _job_file("0" * 32).parent
    if not root.is_dir():
        return
    for path in root.glob("*.json"):
        try:
            job = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(job, dict) and job.get("status") == "running" and job.get("id") == path.stem:
            job.update(status="interrupted", message="服务已重启，导入中断；可重新导入，已下载的文件会跳过")
            _save_job(job)


def get_import(job_id: str) -> dict:
    if len(job_id) != 32 or any(char not in "0123456789abcdef" for char in job_id):
        raise ValueError("导入任务不存在")
    path = _job_file(job_id)
    if not path.is_file():
        raise ValueError("导入任务不存在")
    job = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(job, dict) or job.get("id") != job_id:
        raise ValueError("导入任务不存在")
    return job


def start_import(folder_path: str, sources_dir: Path) -> dict:
    global _IMPORT_ACTIVE
    folder_path = normalize_remote(folder_path)
    if not folder_path:
        raise ValueError("请先选择一个 SharePoint 文件夹")
    with _IMPORT_GUARD:
        if _IMPORT_ACTIVE:
            raise RuntimeError("已有 SharePoint 导入在进行，请等待完成")
        _reconcile_imports()
        _IMPORT_ACTIVE = True
        job = {
            "id": uuid.uuid4().hex,
            "status": "running",
            "folder": folder_path,
            "message": "正在连接 SharePoint",
            "downloaded": 0,
            "skipped": 0,
            "failed": [],
        }
        try:
            _save_job(job)
        except OSError:
            _IMPORT_ACTIVE = False
            raise
    threading.Thread(target=_run_import, args=(job, Path(sources_dir)), daemon=True).start()
    return job


def _run_import(job: dict, sources_dir: Path) -> None:
    global _IMPORT_ACTIVE
    try:
        def progress(remote_child, stats):
            job.update(
                message=f"正在下载 {remote_child}",
                downloaded=stats["downloaded"],
                skipped=stats["skipped"],
                failed=list(stats["failed"][:20]),
            )
            _save_job(job)

        result = SharePointClient.from_env().import_folder(job["folder"], sources_dir, progress)
        failed = result["failed"]
        job.update(
            status="succeeded" if not failed else "succeeded",
            message=f"已下载 {result['downloaded']} 个文件，跳过 {result['skipped']} 个" + (f"，{len(failed)} 个失败" if failed else ""),
            downloaded=result["downloaded"],
            skipped=result["skipped"],
            failed=failed[:20],
        )
    except Exception as exc:
        job.update(status="failed", message=public_error(exc))
    finally:
        try:
            _save_job(job)
        finally:
            with _IMPORT_GUARD:
                _IMPORT_ACTIVE = False
