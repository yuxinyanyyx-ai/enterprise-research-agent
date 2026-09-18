"""DMF Research Agent 的集中配置
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Mapping
from urllib.parse import urlparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from email.headerregistry import Address

from dotenv import load_dotenv


SOURCE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = SOURCE_ROOT.parent

# 读取项目根目录中的.env
load_dotenv(PROJECT_ROOT / ".env")

MIB = 1024 * 1024


class ConfigurationError(RuntimeError):
	"""配置缺失或配置值不合法。"""


def _get_bool(env: Mapping[str, str], name: str, default: bool) -> bool:
	value = env.get(name)
	if value is None:
		return default
	normalized = value.strip().lower()
	if normalized in {"1", "true", "yes", "on"}:
		return True
	if normalized in {"0", "false", "no", "off"}:
		return False
	raise ConfigurationError(f"{name} 必须是 true/false 或 1/0")


def _get_int(
	env: Mapping[str, str], name: str, default: int, *, minimum: int = 1
) -> int:
	raw_value = env.get(name, str(default)).strip()
	try:
		value = int(raw_value)
	except ValueError as exc:
		raise ConfigurationError(f"{name} 必须是整数") from exc
	if value < minimum:
		raise ConfigurationError(f"{name} 必须大于等于 {minimum}")
	return value


def _get_float(
	env: Mapping[str, str], name: str, default: float, *, minimum: float = 0.1
) -> float:
	raw_value = env.get(name, str(default)).strip()
	try:
		value = float(raw_value)
	except ValueError as exc:
		raise ConfigurationError(f"{name} 必须是数字") from exc
	if value < minimum:
		raise ConfigurationError(f"{name} 必须大于等于 {minimum}")
	return value


def _get_path(env: Mapping[str, str], name: str, default: Path) -> Path:
	raw_value = env.get(name)
	path = Path(raw_value).expanduser() if raw_value else default
	if not path.is_absolute():
		path = PROJECT_ROOT / path
	return path.resolve()


def _get_extensions(env: Mapping[str, str]) -> frozenset[str]:
	raw_value = env.get(
		"ALLOWED_EXTENSIONS",
		".pdf,.doc,.docx,.ppt,.pptx,.png,.jpg,.jpeg,.xlsx,.xls,.md,.txt",
	)
	extensions = {
		extension.strip().lower()
		for extension in raw_value.split(",")
		if extension.strip()
	}
	extensions = {
		extension if extension.startswith(".") else f".{extension}"
		for extension in extensions
	}
	if not extensions:
		raise ConfigurationError("ALLOWED_EXTENSIONS 不能为空")
	return frozenset(extensions)


@dataclass(frozen=True, slots=True)
class Settings:
	"""应用运行配置；Token 不参与 repr，避免意外输出到日志。"""

	mineru_token: str = field(repr=False)
	mineru_base_url: str
	mineru_language: str
	mineru_model_version: str
	mineru_enable_ocr: bool
	mineru_enable_table: bool
	mineru_enable_formula: bool

	connect_timeout_seconds: int
	api_timeout_seconds: int
	upload_timeout_seconds: int
	download_timeout_seconds: int
	poll_interval_seconds: float
	max_wait_seconds: int

	max_file_size_bytes: int
	max_batch_size_bytes: int
	max_batch_files: int
	max_markdown_size_bytes: int
	allowed_extensions: frozenset[str]

	template_dir: Path
	static_dir: Path
	upload_dir: Path
	result_dir: Path
	temp_dir: Path
	result_retention_hours: int
	keep_uploaded_files: bool
	dmf_history_enabled: bool = False
	agent_memory_enabled: bool = False
	agent_memory_max_items: int = 8
	agent_checkpoint_enabled: bool = False
	agent_trust_proxy_identity: bool = False
	agent_identity_user_header: str = "X-Authenticated-User"
	agent_identity_tenant_header: str = "X-Authenticated-Tenant"
	dmf_watchlist_enabled: bool = False
	dmf_watchlist_poll_seconds: int = 60
	database_url: str = f"sqlite:///{(SOURCE_ROOT / 'storage' / 'dmf_history.db').as_posix()}"
	notification_enabled: bool = False
	smtp_host: str = ""
	smtp_port: int = 587
	smtp_user: str = ""
	smtp_password: str = field(default="", repr=False)
	smtp_from_email: str = ""
	smtp_security: str = "starttls"
	smtp_timeout_seconds: int = 30
	notification_weekly_digest_day: int = 0
	notification_weekly_digest_hour: int = 9
	notification_timezone: str = "Asia/Taipei"
	notification_max_retries: int = 3
	notification_retry_seconds: int = 60

	@property
	def authorization_headers(self) -> dict[str, str]:
		"""返回仅用于 MinerU API 的鉴权请求头。"""
		return {"Authorization": f"Bearer {self.mineru_token}"}

	def ensure_directories(self) -> None:
		"""创建运行时需要的目录。"""
		for directory in (self.upload_dir, self.result_dir, self.temp_dir):
			directory.mkdir(parents=True, exist_ok=True)


def load_settings(
	env: Mapping[str, str] | None = None, *, require_token: bool = True
) -> Settings:
	"""从环境变量加载并校验配置。

	``env`` 参数用于测试时注入独立配置；生产环境默认读取 ``os.environ``。
	"""
	source = os.environ if env is None else env
	token = source.get("MINERU_TOKEN", "").strip()
	if require_token and not token:
		raise ConfigurationError("未设置环境变量 MINERU_TOKEN")

	base_url = source.get("MINERU_BASE_URL", "https://mineru.net/api/v4").rstrip("/")
	parsed_url = urlparse(base_url)
	if parsed_url.scheme != "https" or not parsed_url.netloc:
		raise ConfigurationError("MINERU_BASE_URL 必须是有效的 HTTPS 地址")

	max_file_mb = _get_int(source, "MAX_FILE_SIZE_MB", 200)
	max_batch_mb = _get_int(source, "MAX_BATCH_SIZE_MB", 500)
	if max_batch_mb < max_file_mb:
		raise ConfigurationError("MAX_BATCH_SIZE_MB 不能小于 MAX_FILE_SIZE_MB")

	language = source.get("MINERU_LANGUAGE", "ch").strip()
	model_version = source.get("MINERU_MODEL_VERSION", "pipeline").strip()
	if not language:
		raise ConfigurationError("MINERU_LANGUAGE 不能为空")
	if not model_version:
		raise ConfigurationError("MINERU_MODEL_VERSION 不能为空")

	dmf_history_enabled = _get_bool(source, "DMF_HISTORY_ENABLED", False)
	agent_memory_enabled = _get_bool(source, "AGENT_MEMORY_ENABLED", False)
	agent_memory_max_items = _get_int(source, "AGENT_MEMORY_MAX_ITEMS", 8)
	agent_checkpoint_enabled = _get_bool(source, "AGENT_CHECKPOINT_ENABLED", False)
	agent_trust_proxy_identity = _get_bool(source, "AGENT_TRUST_PROXY_IDENTITY", False)
	agent_identity_user_header = source.get(
		"AGENT_IDENTITY_USER_HEADER", "X-Authenticated-User"
	).strip()
	agent_identity_tenant_header = source.get(
		"AGENT_IDENTITY_TENANT_HEADER", "X-Authenticated-Tenant"
	).strip()
	if agent_trust_proxy_identity and (not agent_identity_user_header or not agent_identity_tenant_header):
		raise ConfigurationError("代理身份 header 名称不能为空")
	dmf_watchlist_enabled = _get_bool(source, "DMF_WATCHLIST_ENABLED", False)
	if dmf_watchlist_enabled and not dmf_history_enabled:
		raise ConfigurationError("启用 DMF Watchlist 时必须同时启用 DMF 历史功能")
	database_url = source.get(
		"DATABASE_URL",
		f"sqlite:///{(SOURCE_ROOT / 'storage' / 'dmf_history.db').as_posix()}",
	).strip()
	if (dmf_history_enabled or agent_memory_enabled or agent_checkpoint_enabled) and not database_url:
		raise ConfigurationError(
		"启用 DMF 历史、Agent 记忆或持久化 checkpoint 时 DATABASE_URL 不能为空"
	)

	notification_enabled = _get_bool(source, "DMF_NOTIFICATION_ENABLED", False)
	smtp_host = source.get("SMTP_HOST", "").strip()
	smtp_from_email = source.get("SMTP_FROM_EMAIL", "").strip()
	smtp_user = source.get("SMTP_USER", "").strip()
	smtp_password = source.get("SMTP_PASSWORD", "")
	smtp_security = source.get("SMTP_SECURITY", "starttls").strip().lower()
	if smtp_security not in {"starttls", "ssl"}:
		raise ConfigurationError("SMTP_SECURITY must be starttls or ssl")
	smtp_port = _get_int(source, "SMTP_PORT", 465 if smtp_security == "ssl" else 587)
	weekly_day = _get_int(source, "DMF_NOTIFICATION_WEEKLY_DAY", 0, minimum=0)
	weekly_hour = _get_int(source, "DMF_NOTIFICATION_WEEKLY_HOUR", 9, minimum=0)
	if smtp_port > 65535 or weekly_day > 6 or weekly_hour > 23:
		raise ConfigurationError("Invalid SMTP port or weekly schedule")
	notification_timezone = source.get("DMF_NOTIFICATION_TIMEZONE", "Asia/Taipei")
	try:
		ZoneInfo(notification_timezone)
	except (ZoneInfoNotFoundError, ValueError) as exc:
		raise ConfigurationError("Invalid DMF_NOTIFICATION_TIMEZONE") from exc
	if notification_enabled:
		if not dmf_watchlist_enabled or not smtp_host or not smtp_from_email:
			raise ConfigurationError("Notifications require Watchlist, SMTP_HOST and SMTP_FROM_EMAIL")
		try:
			address = Address(addr_spec=smtp_from_email)
			if not address.username or not address.domain:
				raise ValueError("Incomplete sender")
		except ValueError as exc:
			raise ConfigurationError("Invalid SMTP_FROM_EMAIL") from exc
		if bool(smtp_user) != bool(smtp_password):
			raise ConfigurationError("SMTP_USER and SMTP_PASSWORD must be configured together")

	return Settings(
		mineru_token=token,
		mineru_base_url=base_url,
		mineru_language=language,
		mineru_model_version=model_version,
		mineru_enable_ocr=_get_bool(source, "MINERU_ENABLE_OCR", False),
		mineru_enable_table=_get_bool(source, "MINERU_ENABLE_TABLE", True),
		mineru_enable_formula=_get_bool(source, "MINERU_ENABLE_FORMULA", True),
		connect_timeout_seconds=_get_int(source, "CONNECT_TIMEOUT_SECONDS", 10),
		api_timeout_seconds=_get_int(source, "API_TIMEOUT_SECONDS", 30),
		upload_timeout_seconds=_get_int(source, "UPLOAD_TIMEOUT_SECONDS", 600),
		download_timeout_seconds=_get_int(source, "DOWNLOAD_TIMEOUT_SECONDS", 600),
		poll_interval_seconds=_get_float(source, "POLL_INTERVAL_SECONDS", 3.0),
		max_wait_seconds=_get_int(source, "MAX_WAIT_SECONDS", 1200),
		max_file_size_bytes=max_file_mb * MIB,
		max_batch_size_bytes=max_batch_mb * MIB,
		max_batch_files=_get_int(source, "MAX_BATCH_FILES", 20),
		max_markdown_size_bytes=_get_int(source, "MAX_MARKDOWN_SIZE_MB", 100) * MIB,
		allowed_extensions=_get_extensions(source),
		template_dir=_get_path(source, "TEMPLATE_DIR", SOURCE_ROOT / "web" / "templates"),
		static_dir=_get_path(source, "STATIC_DIR", SOURCE_ROOT / "web" / "static"),
		upload_dir=_get_path(source, "UPLOAD_DIR", SOURCE_ROOT / "storage" / "uploads"),
		result_dir=_get_path(source, "RESULT_DIR", SOURCE_ROOT / "storage" / "results"),
		temp_dir=_get_path(source, "TEMP_DIR", SOURCE_ROOT / "storage" / "temp"),
		result_retention_hours=_get_int(source, "RESULT_RETENTION_HOURS", 24),
		keep_uploaded_files=_get_bool(source, "KEEP_UPLOADED_FILES", False),
		dmf_history_enabled=dmf_history_enabled,
		agent_memory_enabled=agent_memory_enabled,
		agent_memory_max_items=agent_memory_max_items,
		agent_checkpoint_enabled=agent_checkpoint_enabled,
		agent_trust_proxy_identity=agent_trust_proxy_identity,
		agent_identity_user_header=agent_identity_user_header,
		agent_identity_tenant_header=agent_identity_tenant_header,
		dmf_watchlist_enabled=dmf_watchlist_enabled,
		dmf_watchlist_poll_seconds=_get_int(
			source, "DMF_WATCHLIST_POLL_SECONDS", 60
		),
		database_url=database_url,
		notification_enabled=notification_enabled,
		smtp_host=smtp_host,
		smtp_port=smtp_port,
		smtp_user=smtp_user,
		smtp_password=smtp_password,
		smtp_from_email=smtp_from_email,
		smtp_security=smtp_security,
		smtp_timeout_seconds=_get_int(source, "SMTP_TIMEOUT_SECONDS", 30),
		notification_weekly_digest_day=weekly_day,
		notification_weekly_digest_hour=weekly_hour,
		notification_timezone=notification_timezone,
		notification_max_retries=_get_int(source, "DMF_NOTIFICATION_MAX_RETRIES", 3, minimum=0),
		notification_retry_seconds=_get_int(source, "DMF_NOTIFICATION_RETRY_SECONDS", 60),
	)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
	"""返回进程内缓存的配置对象。"""
	return load_settings()

