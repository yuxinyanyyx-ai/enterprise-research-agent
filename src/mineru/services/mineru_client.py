"""MinerU v4 Token 批量 API 客户端"""

from __future__ import annotations

import shutil
import stat
import tempfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Iterable, Protocol
from urllib.parse import urlparse

import requests

from src.settings import Settings


DONE_STATES = frozenset({"done", "success", "completed"})
FAILED_STATES = frozenset({"failed", "error"})
TERMINAL_STATES = DONE_STATES | FAILED_STATES


class MinerUError(RuntimeError):
	"""MinerU 客户端基础异常。"""


class FileValidationError(MinerUError):
	"""上传文件不满足本地限制。"""


class MinerUAPIError(MinerUError):
	"""MinerU HTTP 或业务响应异常。"""


class MinerUTimeoutError(MinerUError):
    """批量解析等待超时，同时保留已经获取到的部分结果。"""

    def __init__(
        self,
        message: str,
        partial_results: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(message)
        self.partial_results = partial_results or {}



class ResultArchiveError(MinerUError):
	"""MinerU 结果压缩包不合法。"""


@dataclass(frozen=True, slots=True)
class UploadItem:
	data_id: str
	path: Path
	upload_url: str


@dataclass(frozen=True, slots=True)
class BatchSubmission:
	batch_id: str
	items: tuple[UploadItem, ...]


@dataclass(frozen=True, slots=True)
class ProgressEvent:
	"""供上层记录或推送任务进度。"""

	stage: str
	message: str
	batch_id: str | None = None
	data_id: str | None = None
	file_name: str | None = None
	state: str | None = None


@dataclass(frozen=True, slots=True)
class ParseResult:
	"""一个源文件的最终结果。"""

	data_id: str
	original_name: str
	state: str
	markdown: str | None = None
	markdown_path: Path | None = None
	result_dir: Path | None = None
	error: str | None = None

	@property
	def succeeded(self) -> bool:
		return self.state in DONE_STATES and self.markdown_path is not None


@dataclass(frozen=True, slots=True)
class BatchParseResult:
	"""一次单文件或多文件解析的结果。"""

	batch_id: str
	files: tuple[ParseResult, ...]

	@property
	def succeeded(self) -> tuple[ParseResult, ...]:
		return tuple(item for item in self.files if item.succeeded)

	@property
	def failed(self) -> tuple[ParseResult, ...]:
		return tuple(item for item in self.files if not item.succeeded)


ProgressCallback = Callable[[ProgressEvent], None]


class HTTPSession(Protocol):
	"""客户端所需的最小 HTTP Session 接口，便于注入离线测试替身。"""

	def post(self, url: str, **kwargs: Any) -> Any: ...

	def put(self, url: str, **kwargs: Any) -> Any: ...

	def get(self, url: str, **kwargs: Any) -> Any: ...

	def close(self) -> None: ...


class MinerUClient:
	"""使用 Bearer Token 调用 MinerU v4 批量 API。"""

	def __init__(
		self,
		settings: Settings,
		*,
		session: HTTPSession | None = None,
		progress_callback: ProgressCallback | None = None,
	) -> None:
		self.settings = settings
		self._owns_session = session is None
		self.session = session or requests.Session()
		self.progress_callback = progress_callback
		self.settings.ensure_directories()

	def __enter__(self) -> "MinerUClient":
		return self

	def __exit__(self, exc_type: Any, exc_value: Any, traceback: Any) -> None:
		self.close()

	def close(self) -> None:
		if self._owns_session:
			self.session.close()

	def _emit(
		self,
		stage: str,
		message: str,
		*,
		batch_id: str | None = None,
		item: UploadItem | None = None,
		state: str | None = None,
	) -> None:
		if self.progress_callback is None:
			return
		self.progress_callback(
			ProgressEvent(
				stage=stage,
				message=message,
				batch_id=batch_id,
				data_id=item.data_id if item else None,
				file_name=item.path.name if item else None,
				state=state,
			)
		)

	@staticmethod
	def _https_url(value: Any, field: str) -> str:
		if not isinstance(value, str):
			raise MinerUAPIError(f"MinerU 响应中的 {field} 不是字符串")
		parsed = urlparse(value)
		if parsed.scheme != "https" or not parsed.netloc:
			raise MinerUAPIError(f"MinerU 响应中的 {field} 不是有效 HTTPS 地址")
		return value

	@staticmethod
	def _response_json(response: requests.Response, action: str) -> dict[str, Any]:
		try:
			response.raise_for_status()
		except requests.HTTPError as exc:
			raise MinerUAPIError(
				f"{action}失败：HTTP {response.status_code}，{response.text[:500]}"
			) from exc
		try:
			body = response.json()
		except ValueError as exc:
			raise MinerUAPIError(f"{action}失败：响应不是有效 JSON") from exc
		if not isinstance(body, dict) or body.get("code") != 0:
			raise MinerUAPIError(f"{action}失败：{body}")
		return body

	def validate_files(self, paths: Iterable[Path]) -> tuple[Path, ...]:
		"""校验数量、格式、单文件大小和批次总大小。"""
		files = tuple(Path(path).expanduser().resolve() for path in paths)
		if not files:
			raise FileValidationError("请至少上传一个文件")
		if len(files) > self.settings.max_batch_files:
			raise FileValidationError(
				f"每批最多上传 {self.settings.max_batch_files} 个文件"
			)

		total_size = 0
		for path in files:
			if not path.is_file():
				raise FileValidationError(f"文件不存在：{path}")
			if path.suffix.lower() not in self.settings.allowed_extensions:
				raise FileValidationError(f"不支持的文件格式：{path.name}")
			size = path.stat().st_size
			if size == 0:
				raise FileValidationError(f"文件为空：{path.name}")
			if size > self.settings.max_file_size_bytes:
				raise FileValidationError(f"文件超过大小限制：{path.name}")
			total_size += size
		if total_size > self.settings.max_batch_size_bytes:
			raise FileValidationError("本批次文件总大小超过限制")
		return files

	def request_upload_urls(self, files: Iterable[Path]) -> BatchSubmission:
		"""创建批次并获取每个文件的预签名上传 URL。"""
		validated = self.validate_files(files)
		data_ids = tuple(uuid.uuid4().hex for _ in validated)
		payload = {
			"files": [
				{
					"name": path.name,
					"is_ocr": self.settings.mineru_enable_ocr,
					"data_id": data_id,
				}
				for path, data_id in zip(validated, data_ids)
			],
			"model_version": self.settings.mineru_model_version,
			"enable_formula": self.settings.mineru_enable_formula,
			"enable_table": self.settings.mineru_enable_table,
			"language": self.settings.mineru_language,
		}
		self._emit("creating", f"正在创建包含 {len(validated)} 个文件的批次")
		response = self.session.post(
			f"{self.settings.mineru_base_url}/file-urls/batch",
			headers=self.settings.authorization_headers,
			json=payload,
			timeout=(
				self.settings.connect_timeout_seconds,
				self.settings.api_timeout_seconds,
			),
		)
		body = self._response_json(response, "申请批量上传地址")
		data = body.get("data")
		if not isinstance(data, dict):
			raise MinerUAPIError("MinerU 响应缺少 data 对象")
		batch_id = data.get("batch_id")
		raw_urls = data.get("file_urls")
		if not isinstance(batch_id, str) or not batch_id:
			raise MinerUAPIError("MinerU 响应缺少 batch_id")
		if not isinstance(raw_urls, list) or len(raw_urls) != len(validated):
			raise MinerUAPIError("MinerU 返回的上传地址数量与文件数量不一致")

		upload_urls: list[str] = []
		for raw_url in raw_urls:
			value = (
				raw_url.get("url") or raw_url.get("file_url")
				if isinstance(raw_url, dict)
				else raw_url
			)
			upload_urls.append(self._https_url(value, "file_url"))
		items = tuple(
			UploadItem(data_id=data_id, path=path, upload_url=url)
			for data_id, path, url in zip(data_ids, validated, upload_urls)
		)
		self._emit("created", "MinerU 批次创建成功", batch_id=batch_id)
		return BatchSubmission(batch_id=batch_id, items=items)

	def upload_files(self, submission: BatchSubmission) -> dict[str, str]:
		"""逐个上传文件，返回以 data_id 为键的失败信息。"""
		failures: dict[str, str] = {}
		for item in submission.items:
			self._emit(
				"uploading", f"正在上传 {item.path.name}",
				batch_id=submission.batch_id, item=item,
			)
			try:
				with item.path.open("rb") as stream:
					# 预签名 URL 不能携带 MinerU Authorization 请求头。
					response = self.session.put(
						item.upload_url,
						data=stream,
						timeout=(
							self.settings.connect_timeout_seconds,
							self.settings.upload_timeout_seconds,
						),
					)
				response.raise_for_status()
			except (OSError, requests.RequestException) as exc:
				failures[item.data_id] = f"上传失败：{exc}"
				self._emit(
					"upload_failed", failures[item.data_id],
					batch_id=submission.batch_id, item=item, state="failed",
				)
			else:
				self._emit(
					"uploaded", f"{item.path.name} 上传完成",
					batch_id=submission.batch_id, item=item,
				)
		return failures

	def get_batch_results(self, batch_id: str) -> list[dict[str, Any]]:
		"""查询一次批次状态。"""
		response = self.session.get(
			f"{self.settings.mineru_base_url}/extract-results/batch/{batch_id}",
			headers=self.settings.authorization_headers,
			timeout=(
				self.settings.connect_timeout_seconds,
				self.settings.api_timeout_seconds,
			),
		)
		body = self._response_json(response, "查询批量解析结果")
		data = body.get("data")
		if not isinstance(data, dict):
			raise MinerUAPIError("MinerU 响应缺少 data 对象")
		results = data.get("extract_result")
		if results is None:
			results = data.get("extract_results")
		if not isinstance(results, list) or not all(
			isinstance(item, dict) for item in results
		):
			raise MinerUAPIError("MinerU 响应缺少 extract_result 列表")
		return results

	@staticmethod
	def _index_results(
		results: list[dict[str, Any]], items: tuple[UploadItem, ...]
	) -> dict[str, dict[str, Any]]:
		"""优先按 data_id，必要时按唯一文件名关联响应。"""
		indexed: dict[str, dict[str, Any]] = {}
		by_name: dict[str, list[UploadItem]] = {}
		for item in items:
			by_name.setdefault(item.path.name, []).append(item)
		for result in results:
			data_id = result.get("data_id")
			if isinstance(data_id, str) and data_id:
				indexed[data_id] = result
				continue
			name = str(result.get("file_name") or result.get("name") or "")
			matches = by_name.get(name, [])
			if len(matches) == 1:
				indexed[matches[0].data_id] = result
		return indexed

	def wait_for_batch(
		self, submission: BatchSubmission, active_data_ids: set[str]
	) -> dict[str, dict[str, Any]]:
		"""等待已上传文件全部进入成功或失败终态。"""
		if not active_data_ids:
			return {}
		deadline = time.monotonic() + self.settings.max_wait_seconds
		latest: dict[str, dict[str, Any]] = {}
		last_states: dict[str, str] = {}
		while time.monotonic() < deadline:
			latest.update(
				self._index_results(
					self.get_batch_results(submission.batch_id), submission.items
				)
			)
			for item in submission.items:
				remote = latest.get(item.data_id)
				if item.data_id not in active_data_ids or remote is None:
					continue
				state = str(remote.get("state", "unknown")).lower()
				if last_states.get(item.data_id) != state:
					self._emit(
						"parsing", f"{item.path.name}：{state}",
						batch_id=submission.batch_id, item=item, state=state,
					)
					last_states[item.data_id] = state
			if all(
				data_id in latest
				and str(latest[data_id].get("state", "unknown")).lower()
				in TERMINAL_STATES
				for data_id in active_data_ids
			):
				return latest
			time.sleep(self.settings.poll_interval_seconds)
		raise MinerUTimeoutError(
			(
				f"批次解析等待超过 {self.settings.max_wait_seconds} 秒，"
				f"batch_id={submission.batch_id}"
			),
			partial_results=latest,
		)
	@staticmethod
	def _safe_member_path(output_dir: Path, member_name: str) -> Path:
		"""拒绝 ZIP 绝对路径、父目录跳转和 Windows 盘符。"""
		member = PurePosixPath(member_name.replace("\\", "/"))
		if (
			member.is_absolute()
			or not member.parts
			or ".." in member.parts
			or ":" in member.parts[0]
		):
			raise ResultArchiveError(f"结果 ZIP 包含不安全路径：{member_name}")
		return output_dir.joinpath(*member.parts)

	def _extract_result_zip(self, zip_path: Path, output_dir: Path) -> Path:
		"""安全解压并返回主要 Markdown 路径。"""
		try:
			archive = zipfile.ZipFile(zip_path)
		except zipfile.BadZipFile as exc:
			raise ResultArchiveError("MinerU 返回的结果不是有效 ZIP") from exc
		with archive:
			members = archive.infolist()
			total_size = sum(item.file_size for item in members if not item.is_dir())
			if total_size > self.settings.max_batch_size_bytes:
				raise ResultArchiveError("结果 ZIP 解压后总大小超过限制")
			markdown_members: list[zipfile.ZipInfo] = []
			for member in members:
				if member.flag_bits & 0x1:
					raise ResultArchiveError("结果 ZIP 包含加密文件")
				if stat.S_ISLNK(member.external_attr >> 16):
					raise ResultArchiveError("结果 ZIP 包含符号链接")
				target = self._safe_member_path(output_dir, member.filename)
				if member.is_dir():
					target.mkdir(parents=True, exist_ok=True)
					continue
				if Path(member.filename).suffix.lower() == ".md":
					if member.file_size > self.settings.max_markdown_size_bytes:
						raise ResultArchiveError("结果 Markdown 超过大小限制")
					markdown_members.append(member)
				target.parent.mkdir(parents=True, exist_ok=True)
				with archive.open(member) as source, target.open("wb") as destination:
					while chunk := source.read(1024 * 1024):
						destination.write(chunk)
			if not markdown_members:
				raise ResultArchiveError("MinerU 结果中没有 Markdown 文件")
			preferred = next(
				(
					member for member in markdown_members
					if Path(member.filename).name.lower() == "full.md"
				),
				max(markdown_members, key=lambda member: member.file_size),
			)
			return self._safe_member_path(output_dir, preferred.filename)

	def download_and_extract_result(
		self,
		batch_id: str,
		item: UploadItem,
		remote_result: dict[str, Any],
	) -> ParseResult:
		"""下载一个成功结果，保留 Markdown 和相对引用资源。"""
		try:
			zip_url = self._https_url(remote_result.get("full_zip_url"), "full_zip_url")
		except MinerUError as exc:
			return self._failed_result(item, f"结果下载或解压失败：{exc}")
		result_dir = self.settings.result_dir / batch_id / item.data_id
		temp_path: Path | None = None
		result = self._failed_result(item, "结果下载或解压失败：未知错误")
		try:
			result_dir.mkdir(parents=True, exist_ok=False)
			self._emit(
				"downloading", f"正在下载 {item.path.name} 的解析结果",
				batch_id=batch_id, item=item,
			)
			with self.session.get(
				zip_url,
				stream=True,
				timeout=(
					self.settings.connect_timeout_seconds,
					self.settings.download_timeout_seconds,
				),
			) as response:
				response.raise_for_status()
				with tempfile.NamedTemporaryFile(
					dir=self.settings.temp_dir, suffix=".zip", delete=False
				) as temp_file:
					temp_path = Path(temp_file.name)
					downloaded = 0
					for chunk in response.iter_content(chunk_size=1024 * 1024):
						if not chunk:
							continue
						downloaded += len(chunk)
						if downloaded > self.settings.max_batch_size_bytes:
							raise ResultArchiveError("结果 ZIP 下载大小超过限制")
						temp_file.write(chunk)
			if temp_path is None:
				raise ResultArchiveError("结果 ZIP 临时文件创建失败")
			markdown_path = self._extract_result_zip(temp_path, result_dir)
			try:
				markdown = markdown_path.read_text(encoding="utf-8-sig")
			except UnicodeDecodeError as exc:
				raise ResultArchiveError("结果 Markdown 不是 UTF-8 编码") from exc
			self._emit(
				"done", f"{item.path.name} 解析完成",
				batch_id=batch_id, item=item, state="done",
			)
			result = ParseResult(
				data_id=item.data_id,
				original_name=item.path.name,
				state="done",
				markdown=markdown,
				markdown_path=markdown_path,
				result_dir=result_dir,
			)
		except (OSError, requests.RequestException, MinerUError) as exc:
			shutil.rmtree(result_dir, ignore_errors=True)
			result = self._failed_result(item, f"结果下载或解压失败：{exc}")
		finally:
			if temp_path is not None:
				temp_path.unlink(missing_ok=True)
		return result

	@staticmethod
	def _failed_result(item: UploadItem, error: str, state: str = "failed") -> ParseResult:
		return ParseResult(
			data_id=item.data_id,
			original_name=item.path.name,
			state=state,
			error=error,
		)

	def parse_files(self, paths: Iterable[Path]) -> BatchParseResult:
		"""执行一个或多个文件的完整批量解析流程。"""
		submission = self.request_upload_urls(paths)
		upload_failures = self.upload_files(submission)
		active_ids = {
			item.data_id for item in submission.items
			if item.data_id not in upload_failures
		}
		try:
			remote_results = self.wait_for_batch(submission, active_ids)
			timeout_message = ""
		except MinerUTimeoutError as exc:
		# 保留超时之前已经成功完成的文件
			remote_results = exc.partial_results
			timeout_message = str(exc)

		results: list[ParseResult] = []

		for item in submission.items:
			if item.data_id in upload_failures:
				results.append(
					self._failed_result(
						item,
						upload_failures[item.data_id],
					)
				)
				continue

			remote = remote_results.get(
				item.data_id
			)

			if remote is None:
				results.append(
					self._failed_result(
						item,
						(
								timeout_message
								or "MinerU未返回该文件的解析结果"
						),
						(
							"timeout"
							if timeout_message
							else "failed"
						),
					)
				)
				continue

			state = str(
				remote.get("state", "unknown")
			).lower()

			if state in FAILED_STATES:
				results.append(
					self._failed_result(
						item,
						str(
							remote.get("err_msg")
							or "MinerU解析失败"
						),
					)
				)

			elif state in DONE_STATES:
				results.append(
					self.download_and_extract_result(
						submission.batch_id,
						item,
						remote,
					)
				)

			else:
				# 批次超时时，已经完成的文件正常返回；
				# 尚未完成的文件单独标记为timeout。
				if timeout_message:
					results.append(
						self._failed_result(
							item,
							timeout_message,
							"timeout",
						)
					)
				else:
					results.append(
						self._failed_result(
							item,
							f"未知结束状态：{state}",
							state,
						)
					)

		# 注意：return必须在for循环外面
		return BatchParseResult(
			batch_id=submission.batch_id,
			files=tuple(results),
		)




