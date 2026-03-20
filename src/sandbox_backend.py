"""
E2B Sandbox backend for DeepAgents using S3.
Implements the BackendProtocol for filesystem operations in an isolated sandbox environment.

ARCHITECTURE:
"""

import base64
import logging
import os
import re
import shlex
import asyncio
import time as _time
from pathlib import Path
from typing import Optional

# Process-level cache: one sandbox_id per user_id so we always reuse the same sandbox (avoids duplicates from dev restarts/sync).
_user_sandbox_cache: dict[str, str] = {}

from e2b import Sandbox, SandboxQuery, SandboxState
from e2b.sandbox.commands.command_handle import CommandExitException

from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import WriteResult, EditResult, ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.utils import FileInfo, GrepMatch
from langchain.tools import ToolRuntime
from src.models import AppContext
from src.backend import _parse_skillmd_frontmatter
from src.utils.config import get_user
# Workspace and user models (S3 mount at /mnt/user) via rclone in-sandbox; no s3_utils for tar/manifest.

SANDBOX_TEMPLATE = "solven-sandbox-v1"
SKILLS_REPO_URL = "https://github.com/metalossAI/solven-skills.git"

SOLVEN_LOCKS = "/var/lib/solven/locks"  # Per-thread persist locks (flock)
OPT_SOLVEN_SKILLS = "/opt/solven/skills"
OPT_SOLVEN_USER_MODELS = "/opt/solven/user-models"
OPT_SOLVEN_USER_MODELS_NORMALIZED = "/opt/solven/user-models/templates_normalized"  # Writable; syncs to S3 templates/normalized/
RCLONE_CACHE_BASE = "/tmp/rclone-cache"

# Dirs skipped during in-workspace glob / grep searches (caches, mounts, system).
_WORKSPACE_SEARCH_SKIP_DIRS = frozenset({
    "usr", "etc", "proc", "dev", "sys", "run", "tmp", "cache",
    "bin", "sbin", "lib", "lib64",
    "node_modules", ".venv", "venv", "env", ".bun", ".git", "mnt",
})

# Top-level names to hide from agent in ls_info/glob_info/grep_raw (bwrap exposes /usr, /etc, etc.).
_AGENT_HIDDEN_TOPLEVEL = frozenset({
    "mnt", "usr", "etc", "proc", "dev", "sys", "run", "lib", "lib64", "bin", "sbin", "tmp", "cache",
})

# Document extensions that require Modal/Docling conversion (documents, not code/text).
# Images are intentionally excluded so the model can handle them directly.
_READ_AS_DOCUMENT_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".doc",
    ".xlsx", ".xls",
    ".pptx", ".ppt",
    ".odt", ".ods", ".odp",
    ".rtf",
})

# Image extensions: do NOT convert to markdown. Instead embed as a markdown image data-url.
_READ_AS_IMAGE_EXTENSIONS = frozenset({
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".tiff", ".tif", ".bmp",
})

_IMAGE_MIME_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".tiff": "image/tiff",
    ".tif": "image/tiff",
    ".bmp": "image/bmp",
}

# Marker file written inside E2B workspace after full init; any new SandboxBackend instance can skip full setup if it exists.
_WORKSPACE_READY_MARKER = ".workspace_ready"

# Min seconds between S3→local rclone syncs on the fast init path (avoid hammering on every tool call).
_HYDRATE_FROM_S3_THROTTLE_SEC = 45.0


class SandboxBackend(BaseSandbox):
	"""
	E2B Sandbox backend with bwrap isolation: /workspace is bound to / inside the container.
	Paths are never rewritten; only result filtering (ls_info, glob_info, grep_raw) hides system dirs.
	Init and sync run outside bwrap; execute (and thus read/write/edit/ls_info/glob_info/grep_raw) run inside bwrap.
	Initialization is idempotent: workspace marker file and per-user E2B cache ensure new instances
	skip full setup when the workspace is already ready. Call ensure_ready() to init; use is_available() to check.
	"""

	def __init__(self, runtime: ToolRuntime[AppContext]):
		self._runtime = runtime
		self._sandbox: Optional[Sandbox] = None

		from src.utils.config import get_user, get_workspace_id

		workspace_id = get_workspace_id(runtime)
		if not workspace_id:
			raise RuntimeError("Cannot initialize SandboxBackend: thread_id not found in config")
		self._thread_id = workspace_id

		user = get_user()  # raises RuntimeError if missing
		self._user_id = user.id
		if not user.company_id:
			raise RuntimeError("Cannot initialize SandboxBackend: user company_id (tenant) not found in config")
		self._tenant_id = user.company_id

		self._workspace = f"/{self._tenant_id}/threads/{self._thread_id}"
		# workspace IS the agent root; /.solven is bwrap-virtual, backed by /opt/solven/skills + user-models
		self._venv = f"{self._workspace}/.venv"
		self._node_modules = f"{self._workspace}/node_modules"

		self._workspace_ready = False
		self._initialized = False
		self._bwrap_available: Optional[bool] = None
		self._dirty = False
		self.runtime = runtime

	def _s3_envs(self) -> dict[str, str]:
		"""S3-related env vars from host for sandbox.commands.run(..., envs=). Ensures rclone/config see credentials."""
		out: dict[str, str] = {}
		for name in ("S3_ACCESS_KEY_ID", "S3_BUCKET_NAME", "S3_ENDPOINT_URL", "S3_REGION"):
			val = (os.getenv(name) or "").strip()
			if val:
				out[name] = val
		secret = (os.getenv("S3_ACCESS_SECRET") or os.getenv("AWS_SECRET_ACCESS_KEY") or "").strip()
		if secret:
			out["S3_ACCESS_SECRET"] = secret
		return out

	# Attachments are always stored under this subfolder in the workspace.
	ADJUNTOS_DIR = "adjuntos"

	def _normalize_upload_path_to_adjuntos(self, path: str) -> str:
		"""Ensure upload path is under /adjuntos/ so files never land at workspace root."""
		if not path or not path.strip():
			return f"/{self.ADJUNTOS_DIR}/attachment"
		p = path.strip().replace("\\", "/").lstrip("/")
		if p.startswith(f"{self.ADJUNTOS_DIR}/") or p == self.ADJUNTOS_DIR:
			return f"/{p}" if not path.startswith("/") else path
		# Single segment or path not under adjuntos -> put under adjuntos
		leaf = p.split("/")[-1] if "/" in p else p
		return f"/{self.ADJUNTOS_DIR}/{leaf}"

	def _normalize_agent_path(self, path: str) -> str:
		"""
		Map paths that mirror S3/host layout or legacy /workspaces to bwrap root paths.

		Inside bwrap the thread workspace is mounted at /. Agent paths must be like ``/foo``.
		If callers pass ``/{tenant_id}/threads/{thread_id}/foo`` (S3 key shape) or
		``/workspaces/...`` (old layout), ``ls_info``/``scandir`` would look for nested dirs
		that do not exist and return empty. This strips those prefixes.
		"""
		if not path or not path.strip():
			return "/"
		p = path.strip().replace("\\", "/")
		if p in (".", "./"):
			return "/"
		if not p.startswith("/"):
			p = "/" + p
		p = p.rstrip("/") or "/"
		prefix = f"/{self._tenant_id}/threads/{self._thread_id}"
		if p == prefix or p.startswith(prefix + "/"):
			rest = p[len(prefix) :].lstrip("/")
			return f"/{rest}" if rest else "/"
		# Legacy: /workspaces[/thread_id][/...]
		if p == "/workspaces" or p.startswith("/workspaces/"):
			rest = p[len("/workspaces") :].lstrip("/")
			if rest.startswith(self._thread_id + "/"):
				rest = rest[len(self._thread_id) + 1 :]
			elif rest == self._thread_id:
				rest = ""
			return f"/{rest}" if rest else "/"
		return p

	def _resolve_workspace_path(self, path: str) -> str:
		"""
		Resolve an agent path to the real E2B filesystem path.

		Agent `/` = ``self._workspace`` = ``/{tenant_id}/threads/{thread_id}`` on disk.
		Always call ``_normalize_agent_path`` first so S3-mirror-shaped paths are stripped.
		"""
		path = self._normalize_agent_path(path)
		if not path or path.strip() in ("", "/"):
			return self._workspace
		p = path.strip().replace("\\", "/").lstrip("/")
		ws = self._workspace.rstrip("/")
		resolved = os.path.normpath(f"{ws}/{p}")
		if resolved != ws and not resolved.startswith(ws + os.sep):
			return self._workspace
		return resolved

	def _to_agent_path(self, real_path: str) -> str:
		"""Convert real path under workspace to agent-visible path (where / = workspace root)."""
		if real_path.startswith(self._workspace):
			suffix = real_path[len(self._workspace):].lstrip("/")
			return f"/{suffix}" if suffix else "/"
		return real_path

	def _is_workspace_path(self, agent_path: str) -> bool:
		"""True if path is workspace-only (not under system toplevel like /usr, /etc). Used to filter results."""
		if not agent_path or not agent_path.strip():
			return True
		p = agent_path.strip().rstrip("/")
		if p == "/" or not p.startswith("/"):
			return True
		first = p.lstrip("/").split("/")[0]
		return first not in _AGENT_HIDDEN_TOPLEVEL

	@property
	def id(self) -> str:
		"""Unique identifier for the sandbox backend instance."""
		if self._sandbox:
			return self._sandbox.sandbox_id
		return f"sandbox-{self._thread_id}"

	def _get_or_create_user_sandbox(self) -> None:
		"""Get existing sandbox for this user (RUNNING or PAUSED) or create one. Reuses same sandbox via process cache and deterministic pick."""
		user_key = str(self._user_id)

		def try_connect(sandbox_id: str) -> bool:
			try:
				self._sandbox = Sandbox.connect(sandbox_id)
				_user_sandbox_cache[user_key] = sandbox_id
				return True
			except Exception:
				self._sandbox = None
				return False

		cached_id = _user_sandbox_cache.get(user_key)
		if cached_id and try_connect(cached_id):
			return
		if cached_id:
			_user_sandbox_cache.pop(user_key, None)

		existing_sandboxes = []
		try:
			paginator = Sandbox.list(
				query=SandboxQuery(
					metadata={"userId": user_key},
					state=[SandboxState.RUNNING, SandboxState.PAUSED],
				)
			)
			existing_sandboxes = paginator.next_items()
		except Exception:
			pass

		ids_sorted = sorted(sb.sandbox_id for sb in existing_sandboxes if sb.sandbox_id)
		if cached_id and cached_id in ids_sorted:
			ids_to_try = [cached_id] + [i for i in ids_sorted if i != cached_id]
		else:
			ids_to_try = ids_sorted
		for sandbox_id in ids_to_try:
			if try_connect(sandbox_id):
				return

		if existing_sandboxes:
			_time.sleep(2)
			for sandbox_id in ids_to_try:
				if try_connect(sandbox_id):
					return

		env_vars = {"THREAD_ID": self._thread_id, "USER_ID": user_key}
		for key, env in [
			("S3_BUCKET_NAME", "S3_BUCKET_NAME"),
			("S3_ACCESS_KEY_ID", "S3_ACCESS_KEY_ID"),
			("S3_ACCESS_SECRET", "S3_ACCESS_SECRET"),
			("S3_ENDPOINT_URL", "S3_ENDPOINT_URL"),
			("S3_REGION", "S3_REGION"),
		]:
			if val := os.getenv(env):
				env_vars[key] = val

		timeout_sec = int(os.getenv("E2B_SANDBOX_TIMEOUT", "300"))
		self._sandbox = Sandbox.create(
			template=SANDBOX_TEMPLATE,
			envs=env_vars,
			timeout=timeout_sec,
			lifecycle={"on_timeout": "pause", "auto_resume": True},
			metadata={"userId": user_key},
		)
		_user_sandbox_cache[user_key] = self._sandbox.sandbox_id

	def _ensure_boot_dirs(self) -> None:
		"""Create layout (idempotent): workspace, .venv, node_modules, /opt/solven/skills, user-models, locks, rclone cache."""
		cache_dir = f"{RCLONE_CACHE_BASE}/{self._thread_id}"
		self._sandbox.commands.run(
			f"mkdir -p {shlex.quote(self._workspace)} {shlex.quote(self._venv)} {shlex.quote(self._node_modules)} "
			f"{shlex.quote(OPT_SOLVEN_SKILLS)} "
			f"{OPT_SOLVEN_USER_MODELS}/templates {OPT_SOLVEN_USER_MODELS}/references "
			# templates/normalized: stub inside the ro-bind source so bwrap can overlay it without mkdir-ing into ro fs
			f"{OPT_SOLVEN_USER_MODELS}/templates/normalized {shlex.quote(OPT_SOLVEN_USER_MODELS_NORMALIZED)} "
			f"{shlex.quote(SOLVEN_LOCKS)} {shlex.quote(RCLONE_CACHE_BASE)} {shlex.quote(cache_dir)} "
			f"/root/.config/rclone",
			timeout=10, user="root",
		)

	def _hydrate_from_s3(self) -> None:
		"""Pull latest thread workspace from S3 into workspace/. Idempotent rclone sync. Excludes .solven, .venv, node_modules (local-only; populated separately)."""
		bucket = (os.getenv("S3_BUCKET_NAME") or "solven-testing").strip()
		remote = f"s3remote:{bucket}/{self._tenant_id}/threads/{self._thread_id}"
		excludes = "--exclude '.solven/**' --exclude '.venv/**' --exclude 'node_modules/**' --exclude '.bun/**' --exclude '.git/**'"
		cmd = (
			f"rclone sync {shlex.quote(remote)}/ {shlex.quote(self._workspace)}/ "
			f"--config /root/.config/rclone/rclone.conf "
			f"--fast-list --transfers 4 --no-update-modtime {excludes} "
			f"2>/dev/null || true"
		)
		try:
			self._sandbox.commands.run(cmd, timeout=120, user="root", envs=self._s3_envs())
		except Exception as e:
			logging.warning("_hydrate_from_s3: %s", e)

	def _maybe_hydrate_from_s3_throttled(self) -> None:
		"""On repeated tool runs, periodically pull S3 so uploads from the app (cold storage) appear in the sandbox."""
		if not self._sandbox or not getattr(self, "_workspace_ready", False):
			return
		now = _time.monotonic()
		last = getattr(self, "_last_hydrate_from_s3_monotonic", 0.0)
		if now - last < _HYDRATE_FROM_S3_THROTTLE_SEC:
			return
		self._last_hydrate_from_s3_monotonic = now
		try:
			self._hydrate_from_s3()
		except Exception as e:
			logging.warning("_maybe_hydrate_from_s3_throttled: %s", e)

	def _ensure_skills_mount_dirs(self) -> None:
		"""Ensure bwrap mount-point dirs exist inside /opt/solven/skills (for user-models sub-binds)."""
		try:
			self._sandbox.commands.run(
				f"mkdir -p {OPT_SOLVEN_SKILLS}/escrituras/assets/templates "
				f"{OPT_SOLVEN_SKILLS}/escrituras/references 2>/dev/null || true",
				timeout=10, user="root",
			)
		except Exception as e:
			logging.warning("_ensure_skills_mount_dirs: %s", e)

	def _ensure_skills_repo(self) -> None:
		"""Ensure /opt/solven/skills has the skills git clone. Update in-place (pull or fetch+reset) when repo exists. Fresh clone directly into path (no temp dir)."""
		self._sandbox.commands.run(
			f"git config --global --add safe.directory {shlex.quote(OPT_SOLVEN_SKILLS)} 2>/dev/null || true",
			timeout=5, user="root",
		)
		if self._sandbox.files.exists(f"{OPT_SOLVEN_SKILLS}/.git/HEAD"):
			# Repo exists. Try to update in-place.
			try:
				self._sandbox.git.pull(
					path=OPT_SOLVEN_SKILLS,
					username=os.getenv("GIT_USERNAME"),
					password=os.getenv("GIT_TOKEN"),
					user="root",
					timeout=60,
				)
				self._ensure_skills_mount_dirs()
				return
			except Exception as e:
				logging.warning("_ensure_skills_repo git pull: %s", e)
			try:
				token = os.getenv("GIT_TOKEN") or ""
				username_git = os.getenv("GIT_USERNAME") or ""
				auth_url = SKILLS_REPO_URL.replace("https://", f"https://{username_git}:{token}@") if token else SKILLS_REPO_URL
				r = self._sandbox.commands.run(
					f"git -C {shlex.quote(OPT_SOLVEN_SKILLS)} remote set-url origin {shlex.quote(auth_url)} && "
					f"git -C {shlex.quote(OPT_SOLVEN_SKILLS)} fetch --depth=1 origin main && "
					f"git -C {shlex.quote(OPT_SOLVEN_SKILLS)} reset --hard origin/main",
					timeout=60, user="root",
				)
				if r.exit_code == 0:
					self._ensure_skills_mount_dirs()
					return
			except Exception as e:
				logging.warning("_ensure_skills_repo fetch+reset: %s", e)
			return

		# No repo: remove existing dir and clone directly into OPT_SOLVEN_SKILLS (no temp dir).
		self._sandbox.commands.run(
			f"rm -rf {shlex.quote(OPT_SOLVEN_SKILLS)}",
			timeout=15, user="root",
		)
		self._sandbox.commands.run(
			f"mkdir -p {shlex.quote(OPT_SOLVEN_SKILLS)} {shlex.quote(os.path.dirname(OPT_SOLVEN_SKILLS))}",
			timeout=5, user="root",
		)
		try:
			self._sandbox.git.clone(
				SKILLS_REPO_URL,
				path=OPT_SOLVEN_SKILLS,
				username=os.getenv("GIT_USERNAME"),
				password=os.getenv("GIT_TOKEN"),
				depth=1,
				user="root",
				timeout=120,
			)
		except Exception as e:
			msg = getattr(e, "stderr", None) or getattr(e, "stdout", None) or str(e)
			# Don't raise: keep the empty dir so bwrap can still bind /.solven/skills.
			logging.warning("_ensure_skills_repo clone failed (skills unavailable): %s", msg)
			return
		self._ensure_skills_mount_dirs()

	def _pull_user_models(self) -> None:
		"""Copy user-specific templates, templates/normalized, and references from S3:users/{id}/models/ into /opt/solven/user-models. Fast; idempotent."""
		bucket = (os.getenv("S3_BUCKET_NAME") or "solven-testing").strip()
		s3_user_base = f"s3remote:{bucket}/{self._tenant_id}/users/{self._user_id}/models"
		templates_dst = f"{OPT_SOLVEN_USER_MODELS}/templates"
		references_dst = f"{OPT_SOLVEN_USER_MODELS}/references"
		cfg = "--config /root/.config/rclone/rclone.conf --fast-list --transfers 4 --no-update-modtime"
		try:
			self._sandbox.commands.run(
				# templates/normalized stub must live inside templates/ so bwrap can overlay it without touching the ro-bind filesystem
				f"mkdir -p {shlex.quote(templates_dst)} {shlex.quote(references_dst)} "
				f"{OPT_SOLVEN_USER_MODELS}/templates/normalized {shlex.quote(OPT_SOLVEN_USER_MODELS_NORMALIZED)}",
				timeout=10, user="root",
			)
		except Exception as e:
			logging.warning("_pull_user_models mkdir: %s", e)
		for src, dst in [
			(f"{s3_user_base}/templates", templates_dst),
			(f"{s3_user_base}/references", references_dst),
			(f"{s3_user_base}/templates/normalized", OPT_SOLVEN_USER_MODELS_NORMALIZED),
		]:
			try:
				self._sandbox.commands.run(
					f"rclone copy {shlex.quote(src)}/ {shlex.quote(dst)}/ {cfg} 2>/dev/null || true",
					timeout=60, user="root", envs=self._s3_envs(),
				)
			except Exception as e:
				logging.warning("_pull_user_models copy %s: %s", src, e)

	def _ensure_thread_env(self) -> None:
		"""Create .venv and node_modules inside workspace only when missing. Excluded from rclone sync."""
		venv_python = f"{self._venv}/bin/python"
		resources_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2b_sandbox", "resources")
		pkg_path = f"{self._workspace}/package.json"
		py_path = f"{self._workspace}/pyproject.toml"
		# Skip if env already exists
		try:
			if self._sandbox.files.exists(venv_python):
				return
		except Exception as e:
			logging.warning("_ensure_thread_env exists check: %s", e)
		# Write default manifests only if not present
		if not self._sandbox.files.exists(pkg_path):
			with open(os.path.join(resources_dir, "package.json"), "r") as f:
				pkg_content = f.read()
			b64 = base64.b64encode(pkg_content.encode("utf-8")).decode("ascii")
			self._sandbox.commands.run(
				f"echo {shlex.quote(b64)} | base64 -d > {shlex.quote(pkg_path)}",
				timeout=15, user="root",
			)
		if not self._sandbox.files.exists(py_path):
			with open(os.path.join(resources_dir, "pyproject.toml"), "r") as f:
				py_content = f.read()
			b64 = base64.b64encode(py_content.encode("utf-8")).decode("ascii")
			self._sandbox.commands.run(
				f"echo {shlex.quote(b64)} | base64 -d > {shlex.quote(py_path)}",
				timeout=15, user="root",
			)
		try:
			self._sandbox.commands.run(
				f"cd {shlex.quote(self._workspace)} && uv sync",
				timeout=300, user="root",
			)
		except Exception as e:
			logging.warning("_ensure_thread_env uv sync: %s", e)
		try:
			self._sandbox.commands.run(
				f"cd {shlex.quote(self._workspace)} && bun install",
				timeout=120, user="root",
			)
		except Exception as e:
			logging.warning("_ensure_thread_env bun install: %s", e)

	def _ensure_workspace_ready(self) -> None:
		"""Health check called on every tool invocation when already initialized.
		Repairs skills dir if it disappeared (e.g. sandbox resumed from stale snapshot).
		"""
		if not self._sandbox or not getattr(self, "_workspace_ready", False):
			return
		try:
			if not self._sandbox.files.exists(f"{OPT_SOLVEN_SKILLS}/.git/HEAD"):
				logging.warning("_ensure_workspace_ready: skills dir missing, repairing")
				self._ensure_boot_dirs()
				self._ensure_skills_repo()
		except Exception as e:
			logging.warning("_ensure_workspace_ready: %s", e)

	def _ensure_initialized(self) -> None:
		"""Ensure sandbox and thread workspace are initialized (idempotent).

		Fast path: if _initialized is True, does a lightweight workspace health check and returns.
		When a cached instance is reused across tool calls this is the path taken — no E2B API calls.

		If the sandbox connection becomes stale (sandbox died while the instance was cached), the
		health check will notice and set _initialized=False so the next call does a full reconnect.
		"""
		if self._initialized:
			# Verify the live sandbox connection is still usable; reset if stale.
			try:
				self._ensure_workspace_ready()
			except Exception as e:
				logging.warning("[ensure_initialized] sandbox health check failed, will reconnect: %s", e)
				self._initialized = False
				self._workspace_ready = False
				self._sandbox = None
			else:
				self._maybe_hydrate_from_s3_throttled()
				return

		# Connect or resume the per-user E2B sandbox (fast: ~1s API call)
		self._get_or_create_user_sandbox()

		# Fast path: workspace was already set up in a previous run (marker lives inside E2B sandbox)
		marker = f"{self._workspace}/{_WORKSPACE_READY_MARKER}"
		try:
			if self._sandbox.files.exists(marker):
				self._pull_user_models()
				self._workspace_ready = True
				self._initialized = True
				self._maybe_hydrate_from_s3_throttled()
				return
		except Exception:
			pass

		# Full first-time setup
		logging.info("Preparando espacio de trabajo...")
		self._ensure_boot_dirs()
		self._configure_rclone()
		self._hydrate_from_s3()
		self._ensure_skills_repo()
		self._pull_user_models()
		try:
			self._ensure_thread_env()
		except Exception as e:
			logging.warning("_ensure_thread_env at init: %s", e)
		self._sandbox.commands.run(f"chown -R user:user {shlex.quote(self._workspace)}", timeout=60, user="root")
		try:
			if not self._sandbox.files.exists(f"{OPT_SOLVEN_SKILLS}/escrituras/SKILL.md"):
				logging.warning("%s/escrituras/SKILL.md not found after init — skills may be unavailable", OPT_SOLVEN_SKILLS)
		except Exception as e:
			logging.warning("skills marker check after init: %s", e)
		self._workspace_ready = True
		self._ensure_bwrap()
		self._initialized = True
		try:
			self._sandbox.files.write(marker, "ready")
		except Exception as e:
			logging.warning("Could not write workspace marker: %s", e)
		logging.info("Espacio de trabajo listo")

	def ensure_ready(self) -> None:
		"""Public entrypoint to ensure sandbox and workspace are initialized (idempotent). Use from middleware."""
		self._ensure_initialized()

	def is_available(self) -> bool:
		"""Return True when the backend has a connected E2B sandbox and has completed initialization."""
		return self._sandbox is not None and self._initialized

	def _persist_shell_cmd(self) -> str:
		"""Build persist as single shell command (serialized per thread via flock):
		1. rclone sync workspace/ -> S3:{tenant_id}/threads/{id}/ (excluding .solven/.venv/node_modules/etc.)
		2. rclone copy user templates -> S3:users/{id}/models/templates/
		3. rclone copy user templates_normalized -> S3:users/{id}/models/templates/normalized/
		4. rclone copy user references -> S3:users/{id}/models/references/
		"""
		persist_lock = f"{SOLVEN_LOCKS}/{self._thread_id}.persist.lock"
		bucket = (os.getenv("S3_BUCKET_NAME") or "solven-testing").strip()
		threads_remote = f"s3remote:{bucket}/{self._tenant_id}/threads/{self._thread_id}"
		s3_user_base = f"s3remote:{bucket}/{self._tenant_id}/users/{self._user_id}/models"
		excludes = "--exclude '.solven/**' --exclude '.venv/**' --exclude 'node_modules/**' --exclude '.bun/**' --exclude '.git/**'"
		cfg = "--config /root/.config/rclone/rclone.conf --fast-list --transfers 4 --no-update-modtime"

		templates_src = f"{OPT_SOLVEN_USER_MODELS}/templates"
		references_src = f"{OPT_SOLVEN_USER_MODELS}/references"

		sync_project = f"rclone sync {cfg} {excludes} {shlex.quote(self._workspace)}/ {shlex.quote(threads_remote)}/ 2>/dev/null || true"
		copy_templates = f"rclone copy {cfg} {shlex.quote(templates_src)}/ {shlex.quote(s3_user_base + '/templates')}/ 2>/dev/null || true"
		copy_normalized = f"rclone copy {cfg} {shlex.quote(OPT_SOLVEN_USER_MODELS_NORMALIZED)}/ {shlex.quote(s3_user_base + '/templates/normalized')}/ 2>/dev/null || true"
		copy_references = f"rclone copy {cfg} {shlex.quote(references_src)}/ {shlex.quote(s3_user_base + '/references')}/ 2>/dev/null || true"

		inner = f"{sync_project} && {copy_templates} && {copy_normalized} && {copy_references}"
		return f"flock -n {shlex.quote(persist_lock)} -c {shlex.quote(inner)}"

	def persist_workspace(self, background: bool = True) -> None:
		"""Persist local project/ to S3 (rclone sync). User models auto-sync via rclone VFS mount. Skips when not dirty."""
		if not self._sandbox or not self._workspace_ready:
			return
		if not self._dirty:
			return
		if background:
			try:
				self._sandbox.commands.run(
					self._persist_shell_cmd(),
					timeout=300,
					user="root",
					envs=self._s3_envs(),
					background=True,
				)
				self._dirty = False
			except Exception as e:
				logging.warning("persist_workspace background run: %s", e)
			return
		try:
			self._sandbox.commands.run(
				self._persist_shell_cmd(),
				timeout=300,
				user="root",
				envs=self._s3_envs(),
			)
			self._dirty = False
		except Exception as e:
			logging.warning("persist_workspace: %s", e)

	def _configure_rclone(self) -> None:
		"""Upload and run create_rclone_config.sh to create /root/.config/rclone/rclone.conf. Required for rclone sync/copy in hydrate and persist."""
		src_dir = os.path.dirname(os.path.abspath(__file__))
		script_path = os.path.join(src_dir, "e2b_sandbox", "scripts", "create_rclone_config.sh")
		with open(script_path, "r") as f:
			config_script = f.read()
		b64 = base64.b64encode(config_script.encode("utf-8")).decode("ascii")
		result = self._sandbox.commands.run(
			f"echo {shlex.quote(b64)} | base64 -d > /root/create_rclone_config.sh",
			timeout=30, user="root",
		)
		if result.exit_code != 0:
			raise RuntimeError(f"Failed to write create_rclone_config.sh: {result.stderr or result.stdout}")
		result = self._sandbox.commands.run(
			"cp /root/create_rclone_config.sh /tmp/create_rclone_config.sh && chmod +x /tmp/create_rclone_config.sh",
			timeout=10, user="root",
		)
		if result.exit_code != 0:
			raise RuntimeError(f"Failed to cp/chmod create_rclone_config.sh: {result.stderr or result.stdout}")
		rclone_envs = self._s3_envs()
		if not rclone_envs.get("S3_ACCESS_KEY_ID") or not rclone_envs.get("S3_ACCESS_SECRET"):
			raise RuntimeError("S3_ACCESS_KEY_ID and S3_ACCESS_SECRET required for rclone config")
		run_result = self._sandbox.commands.run(
			"/tmp/create_rclone_config.sh",
			timeout=30, user="root", envs=rclone_envs,
		)
		if run_result.exit_code != 0:
			raise RuntimeError(f"create_rclone_config.sh failed: {run_result.stderr or run_result.stdout}")

	def _execute_env(self) -> dict[str, str]:
		"""Environment for commands run in thread workspace (bwrap binds it as /)."""
		return {
			"HOME": "/",
			"TMPDIR": "/tmp",
			"PATH": "/.venv/bin:/.local/bin:/.bun/bin:/usr/local/bin:/usr/bin:/bin",
			"UV_PROJECT_ENVIRONMENT": "/.venv",
			"VIRTUAL_ENV": "/.venv",
			"BUN_INSTALL": "/.bun",
		}

	def _ensure_bwrap(self) -> None:
		"""Verify bwrap is available; cache result."""
		if self._bwrap_available is not None:
			return
		try:
			result = self._sandbox.commands.run("which bwrap", timeout=10, user="root")
			self._bwrap_available = result.exit_code == 0 and bool((result.stdout or "").strip())
		except Exception:
			self._bwrap_available = False
		if not self._bwrap_available:
			return

	def _filter_unwanted_commands(self, command: str) -> Optional[str]:
		"""Block install commands so deps use uv (Python) and bun (Node). Allow pip/npm/npx for non-install (e.g. pip list, npm run)."""
		unwanted = {
			r"\bsudo\b": "Not allowed: sudo is not allowed in sandbox environment.",
			r"\bapt-get\s+(install|update)\b": "Not allowed: apt-get is not allowed (system packages pre-installed).",
			r"\bapt\s+(install|update)\b": "Not allowed: apt is not allowed (system packages pre-installed).",
			r"pip\s+install": "Not allowed: use uv for Python dependencies (e.g. uv add <pkg> or uv sync).",
			r"npm\s+install\b": "Not allowed: use bun for Node dependencies (e.g. bun add <pkg> or bun install).",
			r"npm\s+i\s": "Not allowed: use bun for Node dependencies (e.g. bun add <pkg> or bun install).",
		}
		for pattern, message in unwanted.items():
			if re.search(pattern, command, re.IGNORECASE):
				return message
		return None

	def _build_bwrap_command(self, command: str) -> str:
		"""Wrap command in bwrap: workspace → /; --dir /.solven; /opt/solven/skills and user-models bound into /.solven/skills."""
		path_env = "/.venv/bin:/.local/bin:/.bun/bin:/node_modules/.bin:/usr/local/bin:/usr/bin:/bin"
		args = [
			"bwrap",
			"--bind", self._workspace, "/",
			"--dir", "/.solven",
			"--bind", OPT_SOLVEN_SKILLS, "/.solven/skills",
			"--ro-bind", f"{OPT_SOLVEN_USER_MODELS}/templates", "/.solven/skills/escrituras/assets/templates",
			"--bind", OPT_SOLVEN_USER_MODELS_NORMALIZED, "/.solven/skills/escrituras/assets/templates/normalized",
			"--bind", f"{OPT_SOLVEN_USER_MODELS}/references", "/.solven/skills/escrituras/references",
			"--bind", self._venv, "/.venv",
			"--bind", self._node_modules, "/node_modules",
			"--ro-bind", "/usr", "/usr",
			"--ro-bind", "/lib", "/lib",
			"--ro-bind", "/lib64", "/lib64",
			"--ro-bind", "/bin", "/bin",
			"--ro-bind", "/sbin", "/sbin",
			"--ro-bind", "/etc", "/etc",
			"--proc", "/proc",
			"--dev", "/dev",
			"--tmpfs", "/tmp",
			"--chdir", "/",
			"--setenv", "HOME", "/",
			"--setenv", "PWD", "/",
			"--setenv", "TMPDIR", "/tmp",
			"--setenv", "PATH", path_env,
			"--setenv", "UV_PROJECT_ENVIRONMENT", "/.venv",
			"--setenv", "VIRTUAL_ENV", "/.venv",
			"--setenv", "BUN_INSTALL", "/.bun",
			"--setenv", "NODE_PATH", "/node_modules",
			"--",
			"/bin/bash", "-c", command,
		]
		return " ".join(shlex.quote(str(a)) for a in args)

	def execute(self, command: str) -> ExecuteResponse:
		"""Execute a shell command inside bwrap (workspace bound as /). No path rewriting; run command as-is."""
		self._ensure_initialized()
		if not self._workspace_ready:
			return ExecuteResponse(
				output="Error: workspace not ready (sandbox init did not complete).",
				exit_code=1,
				truncated=False,
			)
		if self._bwrap_available is False:
			return ExecuteResponse(
				output="Error: bwrap not available (required for command execution). Ensure bubblewrap is installed in the E2B template.",
				exit_code=1,
				truncated=False,
			)

		if error_msg := self._filter_unwanted_commands(command):
			return ExecuteResponse(output=error_msg, exit_code=1, truncated=False)

		full_cmd = self._build_bwrap_command(command)
		try:
			result = self._sandbox.commands.run(
				full_cmd,
				timeout=1200,
				user="root",
			)
		except Exception as e:
			return ExecuteResponse(
				output=f"Error executing command: {str(e)}",
				exit_code=1,
				truncated=False,
			)
		self._dirty = True
		try:
			self.persist_workspace(background=True)
		except Exception:
			pass

		return ExecuteResponse(
			output=(result.stdout or "") + (result.stderr or ""),
			exit_code=result.exit_code,
			truncated=False,
		)
	
	async def aexecute(self, command: str) -> ExecuteResponse:
		"""Async version of execute."""
		return await asyncio.to_thread(self.execute, command)

	def ls_info(self, path: str) -> list[FileInfo]:
		"""
		List files under path. Pass agent path to base so that inside bwrap (where / = thread
		workspace) scandir sees the correct dir. E.g. /.solven/skills/ -> list under thread workspace.
		"""
		self._ensure_initialized()
		path = self._normalize_agent_path(path)
		result = super().ls_info(path)
		out = [p for p in result if self._is_workspace_path(p["path"])]
		return out

	def glob_info(self, pattern: str, path: str = "/") -> list["FileInfo"]:
		"""List files matching pattern via find -iname (case-insensitive) so e.g. **/acta* matches ACTA JUNTA UNIVERSAL."""
		self._ensure_initialized()
		path = self._normalize_agent_path(path)
		search_path = path.rstrip("/") or "/"
		# Basename part for -iname: **/acta* -> acta*, **/*.pdf -> *.pdf
		basename_pattern = pattern.split("/")[-1] if "/" in pattern else pattern
		if not basename_pattern or basename_pattern == "**":
			basename_pattern = "*"
		# Prune skip dirs only ( -type d -name d1 -o -type d -name d2 ... ) -prune -o -type f -iname 'pattern' -print
		prune_expr = " -o ".join(f"-type d -name {shlex.quote(d)}" for d in _WORKSPACE_SEARCH_SKIP_DIRS)
		cmd = (
			f"find {shlex.quote(search_path)} "
			f"\\( {prune_expr} \\) -prune -o -type f -iname {shlex.quote(basename_pattern)} -print 2>/dev/null"
		)
		result = self.execute(cmd)
		file_infos: list = []
		for line in (result.output or "").splitlines():
			line = line.strip()
			if line:
				file_infos.append({"path": line, "is_dir": False})
		return file_infos

	def grep_raw(
		self,
		pattern: str,
		path: str | None = None,
		glob: str | None = None,	
	) -> "list[GrepMatch] | str":
		"""Same as base (grep -rHnF via execute) but with --exclude-dir so root search is fast."""
		self._ensure_initialized()
		norm = self._normalize_agent_path(path or "/")
		search_path = shlex.quote(norm.rstrip("/") or "/")
		skip_dirs = set(_WORKSPACE_SEARCH_SKIP_DIRS)
		exclude_dirs = " ".join(f"--exclude-dir={d}" for d in skip_dirs)
		glob_pattern = f"--include={shlex.quote(glob)}" if glob else ""
		cmd = (
			f"grep -rHnF {exclude_dirs} {glob_pattern} "
			f"-e {shlex.quote(pattern)} {search_path} 2>/dev/null || true"
		)
		result = self.execute(cmd)
		output = (result.output or "").rstrip()
		if not output:
			return []
		matches: list = []
		for line in output.split("\n"):
			parts = line.split(":", 2)
			if len(parts) >= 3:
				try:
					matches.append({"path": parts[0], "line": int(parts[1]), "text": parts[2]})
				except ValueError:
					continue
		return matches

	def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
		self._ensure_initialized()
		responses = []

		for path, content in files:
			try:
				path = self._normalize_upload_path_to_adjuntos(path)
				real_path = self._resolve_workspace_path(path)

				parent = os.path.dirname(real_path)
				self._sandbox.commands.run(f"mkdir -p {shlex.quote(parent)}", timeout=10)

				if isinstance(content, bytes):
					try:
						self._sandbox.files.write(real_path, content.decode("utf-8"))
					except UnicodeDecodeError:
						self._sandbox.files.write(real_path, content)
				else:
					self._sandbox.files.write(real_path, str(content))

				responses.append(FileUploadResponse(path=path, error=None))

			except Exception:
				responses.append(FileUploadResponse(path=path, error="permission_denied"))

		self._dirty = True
		try:
			self.persist_workspace(background=True)
		except Exception:
			pass

		return responses

	def _convert_to_markdown(self, content: bytes, filename: str) -> str:
		"""
		Convert document bytes (PDF/DOCX/etc) to markdown.

		Used only for document types (not code/text/images). Prefer Modal GPU (Docling VLM) when
		configured; fall back to local Docling on failure.
		"""
		use_modal = bool((os.getenv("MODAL_TOKEN_ID") or os.getenv("USE_MODAL_DOCLING") or "").strip())
		if use_modal:
			try:
				import modal
				fn = modal.Function.from_name("solven-docling-converter", "convert_to_markdown")
				return fn.remote(content, filename)
			except Exception:
				pass
		# Fallback: local Docling (CPU, no VLM)
		import tempfile
		ext = (Path(filename).suffix or "").lower()
		suffix = ext or ".bin"
		with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
			tmp.write(content)
			tmp.flush()
			tmp_path = tmp.name
		try:
			from docling.document_converter import DocumentConverter
			converter = DocumentConverter()
			result = converter.convert(tmp_path)
			return result.document.export_to_markdown()
		finally:
			try:
				os.unlink(tmp_path)
			except OSError:
				pass

	def read(self, file_path: str, offset: int = 0, limit: int = 2000, allow_non_markdown: bool = False) -> str:
		"""
		Read file content with line numbers.

		Only document-like formats (PDF/DOCX/XLSX/PPTX/ODT/ODS/ODP/RTF) are converted to markdown.
		Everything else (plain text/code/images/etc.) must defer to the parent implementation
		so existing image handling / multimodal tool serialization stays consistent.
		"""
		self._ensure_initialized()
		file_path = self._normalize_agent_path(file_path)

		ext = (Path(file_path).suffix or "").lower()
		# Block access to internal instructions.md files (consistent with parent semantics).
		if file_path.endswith("/instructions.md") or file_path.endswith("instructions.md"):
			return "Error: File '{}' not found".format(file_path)

		# Delegate all non-doc formats to deepagents BaseSandbox implementation.
		# This avoids custom image embedding that can break OpenRouter multimodal schemas.
		if ext not in _READ_AS_DOCUMENT_EXTENSIONS:
			return super().read(file_path, offset, limit)

		real_path = self._resolve_workspace_path(file_path)
		if not self._sandbox.files.exists(real_path):
			return f"Error: File '{file_path}' not found"

		# Ensure it's not a directory (best-effort).
		try:
			info = self._sandbox.files.get_info(real_path)
			type_val = getattr(info, "type", None)
			is_dir = getattr(info, "is_dir", False) or (str(type_val).lower().find("dir") >= 0)
			if is_dir:
				return f"Error: File '{file_path}' is a directory"
		except Exception:
			pass

		download_url = self._sandbox.download_url(real_path)
		import requests

		response = requests.get(download_url, timeout=30)
		response.raise_for_status()
		content = response.content

		basename = os.path.basename(file_path)

		def _format_numbered_lines(text: str) -> str:
			lines = text.splitlines()
			start = offset
			end = min(offset + limit, len(lines))
			selected_lines = lines[start:end]
			return "\n".join(
				f"{i}\t{line}"
				for i, line in enumerate(selected_lines, start=start + 1)
			)

		# Documents: convert to markdown (Word/PDF/etc).
		try:
			markdown = self._convert_to_markdown(content, file_path)
		except Exception as e:
			return f"Error converting '{file_path}' to markdown: {str(e)}"
		return _format_numbered_lines(markdown)

	def write(self, file_path: str, content: str) -> WriteResult:
		self._ensure_initialized()
		return super().write(self._normalize_agent_path(file_path), content)

	def edit(
		self,
		file_path: str,
		old_string: str,
		new_string: str,
		replace_all: bool = False,
	) -> EditResult:
		self._ensure_initialized()
		return super().edit(
			self._normalize_agent_path(file_path),
			old_string,
			new_string,
			replace_all=replace_all,
		)

	def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
		"""
		Download files from the sandbox. Paths are agent-visible (e.g. /file.docx or /.solven/skills/...);
		resolved to real sandbox paths under {tenant_id}/threads/{thread_id} via _resolve_workspace_path.
		"""
		self._ensure_initialized()
		responses = []
		for path in paths:
			try:
				real_path = self._resolve_workspace_path(path)

				if not self._sandbox.files.exists(real_path):
					responses.append(
						FileDownloadResponse(path=path, content=None, error="file_not_found")
					)
					continue
				# E2B download_url is for files only; skip directories
				try:
					info = self._sandbox.files.get_info(real_path)
					type_val = getattr(info, "type", None)
					is_dir = getattr(info, "is_dir", False) or (str(type_val).lower().find("dir") >= 0)
					if is_dir:
						responses.append(
							FileDownloadResponse(path=path, content=None, error="is_directory")
						)
						continue
				except Exception:
					pass

				download_url = self._sandbox.download_url(real_path)
				import requests
				response = requests.get(download_url, timeout=30)
				response.raise_for_status()

				responses.append(
					FileDownloadResponse(path=path, content=response.content, error=None)
				)

			except Exception as e:
				responses.append(
					FileDownloadResponse(path=path, content=None, error=f"download_error: {str(e)}")
				)

		return responses

	async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
		"""Async version of upload_files."""
		return await asyncio.to_thread(self.upload_files, files)

	async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
		"""Async version of download_files."""
		return await asyncio.to_thread(self.download_files, paths)

	async def als_info(self, path: str) -> list[FileInfo]:
		return await asyncio.to_thread(self.ls_info, path)

	async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
		norm = self._normalize_agent_path(file_path)
		ext = (Path(norm).suffix or "").lower()
		if ext not in _READ_AS_DOCUMENT_EXTENSIONS:
			# Bypass our document override; use BaseSandbox read (images / multimodal).
			return await asyncio.to_thread(super().read, norm, offset, limit)
		return await asyncio.to_thread(self.read, file_path, offset, limit)

	async def awrite(self, file_path: str, content: str) -> WriteResult:
		return await asyncio.to_thread(self.write, file_path, content)

	async def agrep_raw(self, pattern: str, path: str | None, glob: str | None = None) -> list[GrepMatch] | str:
		return await asyncio.to_thread(self.grep_raw, pattern, path, glob)

	async def aglob_info(self, pattern: str, path: str) -> list[FileInfo]:
		return await asyncio.to_thread(self.glob_info, pattern, path)
