"""
E2B Sandbox backend for DeepAgents using S3.
Implements the BackendProtocol for filesystem operations in an isolated sandbox environment.

ARCHITECTURE (rclone mount workspace):
=====================================
- One E2B sandbox per user; lifecycle: on_timeout=pause, auto_resume=true.
- /workspaces/{thread_id}/project/ — rclone mount of S3 tenant/threads/{thread_id}/ (VFS write-back, auto-sync).
- /workspaces/{thread_id}/.solven/skills/ — local copy (rsync from /opt/solven/skills) + user models (rclone copy from S3).
- /workspaces/{thread_id}/.venv/, node_modules/ — local (uv sync, bun install); set up once, survive pause.
- bwrap binds project/ -> /, .solven -> /.solven, .venv -> /.venv, node_modules -> /node_modules.
- Persist: user models only (rclone copy .solven/escrituras -> S3); project files auto-sync via rclone VFS.
- Init is idempotent: check-before-do so resume is fast (mountpoint -q, SKILL.md, .venv/bin/python).
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
from langgraph.config import get_config
from langgraph.graph.state import RunnableConfig
from src.models import AppContext
from src.backend import _parse_skillmd_frontmatter
from src.utils.config import get_user
# Workspace and user models (S3 mount at /mnt/user) via rclone in-sandbox; no s3_utils for tar/manifest.

SANDBOX_TEMPLATE = "solven-sandbox-v1"
SKILLS_REPO_URL = "https://github.com/metalossAI/solven-skills.git"

SOLVEN_LOCKS = "/var/lib/solven/locks"  # Per-thread persist locks (flock)
OPT_SOLVEN_SKILLS = "/opt/solven/skills"
WORKSPACES = "/workspaces"
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

# Extensions that require Modal/Docling conversion (documents, not code). All other files (code, .md, .txt, etc.) are read as UTF-8.
_READ_AS_DOCUMENT_EXTENSIONS = frozenset({
    ".pdf", ".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt",
    ".odt", ".ods", ".odp", ".rtf",
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".tiff", ".tif", ".bmp",
})


def _context_backend(ctx) -> Optional["SandboxBackend"]:
	"""Get backend from runtime context (dict or object)."""
	if ctx is None:
		return None
	if isinstance(ctx, dict):
		return ctx.get("backend")
	return getattr(ctx, "backend", None)


def _context_set_backend(ctx, backend: "SandboxBackend") -> None:
	"""Store backend in runtime context (dict or object)."""
	if ctx is None:
		return
	if isinstance(ctx, dict):
		ctx["backend"] = backend
	else:
		setattr(ctx, "backend", backend)


def get_backend(runtime: ToolRuntime[AppContext]) -> "SandboxBackend":
	"""Return the backend for this run; reuses instance from context when already configured."""
	return SandboxBackend(runtime)


class SandboxBackend(BaseSandbox):
	"""
	E2B Sandbox backend with bwrap isolation: /workspace is bound to / inside the container.
	Paths are never rewritten; only result filtering (ls_info, glob_info, grep_raw) hides system dirs.
	Init and sync run outside bwrap; execute (and thus read/write/edit/ls_info/glob_info/grep_raw) run inside bwrap.
	Reuses one instance per run: runtime.context stores the backend; __new__ returns it so tool calls are fast.
	"""

	def __new__(cls, runtime: ToolRuntime[AppContext], *args, **kwargs):
		ctx = getattr(runtime, "context", None)
		existing = _context_backend(ctx)
		if existing is not None and isinstance(existing, cls):
			return existing
		return super().__new__(cls)

	def __init__(self, runtime: ToolRuntime[AppContext]):
		# Reused instance from __new__: skip full init
		if getattr(self, "_initialized", False):
			return
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

		self._workspace = f"{WORKSPACES}/{self._thread_id}"
		self._project = f"{self._workspace}/project"
		self._solven = f"{self._workspace}/.solven"
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

	def _resolve_workspace_path(self, path: str) -> str:
		"""
		Resolve agent-visible path to real path. Agent / = project (S3 mount); /.solven/* = workspace .solven.
		Normalizes and constrains so '..' cannot escape.
		"""
		if not path or path.strip() in ("", "/"):
			return self._project
		p = path.strip().replace("\\", "/").lstrip("/")
		if p.startswith(".solven/"):
			base = self._workspace
			resolved = os.path.normpath(f"{base}/{p}")
			if not resolved.startswith(self._solven):
				return self._project
		else:
			base = self._project
			resolved = os.path.normpath(f"{base}/{p}")
			if not resolved.startswith(self._project):
				return self._project
		return resolved

	def _to_agent_path(self, real_path: str) -> str:
		"""Convert real path under project or .solven to agent-visible path (where / = project root)."""
		if real_path.startswith(self._project):
			suffix = real_path[len(self._project):].lstrip("/")
			return f"/{suffix}" if suffix else "/"
		if real_path.startswith(self._solven):
			suffix = real_path[len(self._solven):].lstrip("/")
			return f"/.solven/{suffix}" if suffix else "/.solven"
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
		"""Create layout (idempotent): workspace, project, .solven, .venv, node_modules, locks, skills, rclone cache."""
		cache_dir = f"{RCLONE_CACHE_BASE}/{self._thread_id}"
		self._sandbox.commands.run(
			f"mkdir -p {shlex.quote(self._workspace)} {shlex.quote(self._project)} {shlex.quote(self._solven)} "
			f"{shlex.quote(self._venv)} {shlex.quote(self._node_modules)} "
			f"{shlex.quote(SOLVEN_LOCKS)} {shlex.quote(OPT_SOLVEN_SKILLS)} {shlex.quote(RCLONE_CACHE_BASE)} {shlex.quote(cache_dir)} "
			f"/root/.config/rclone",
			timeout=10, user="root",
		)

	def _cleanup_ghost_mounts(self) -> None:
		"""Lazy-unmount any leftover bind mounts from old overlayfs sessions (transition safety)."""
		try:
			self._sandbox.commands.run(
				"awk '$2 ~ \"^/var/lib/solven/runtime\" {print $2}' /proc/mounts | sort -r | xargs -r -I{} umount -l {} 2>/dev/null || true",
				timeout=15, user="root",
			)
			self._sandbox.commands.run(
				"mountpoint -q /opt/solven/skills && umount -l /opt/solven/skills 2>/dev/null || true",
				timeout=5, user="root",
			)
		except Exception as e:
			logging.warning("_cleanup_ghost_mounts: %s", e)

	def _mount_project(self) -> None:
		"""Mount S3 thread path at project/. Idempotent: skip if already mounted and responsive. If broken, lazy-umount then mount."""
		bucket = (os.getenv("S3_BUCKET_NAME") or "solven-testing").strip()
		remote = f"s3remote:{bucket}/{self._tenant_id}/threads/{self._thread_id}"
		proj = shlex.quote(self._project)
		cache_dir = shlex.quote(f"{RCLONE_CACHE_BASE}/{self._thread_id}")
		# Already mounted and responsive -> skip (E2B raises on non-zero, so catch and treat as "not mounted")
		try:
			r = self._sandbox.commands.run(
				f"mountpoint -q {proj}/ && ls {proj}/ >/dev/null 2>&1",
				timeout=5, user="root",
			)
			if r.exit_code == 0:
				return
		except CommandExitException:
			pass  # not mounted or ls failed (e.g. exit 32) -> continue to umount and mount
		# Broken or not mounted: lazy-umount if it was a mount point (ignore non-zero)
		try:
			self._sandbox.commands.run(f"umount -l {proj}/ 2>/dev/null || true", timeout=10, user="root")
		except CommandExitException:
			pass
		# rclone mount requires an empty directory (exit 32 if not). When not a mount point, wipe and recreate.
		# E2B can raise CommandExitException for mountpoint -q (e.g. exit 32); treat as "not a mount point".
		try:
			r_mountpoint = self._sandbox.commands.run(f"mountpoint -q {proj}/", timeout=5, user="root")
			is_mount = r_mountpoint.exit_code == 0
		except CommandExitException:
			is_mount = False
		if not is_mount:
			try:
				self._sandbox.commands.run(f"rm -rf {proj}", timeout=15, user="root")
				self._sandbox.commands.run(f"mkdir -p {proj}", timeout=5, user="root")
			except CommandExitException as e:
				logging.warning("_mount_project ensure empty dir: %s", e)
		# Mount with VFS full cache and write-back; dir-cache-time 0 so listings always hit S3 (no RC in E2B daemon)
		cmd = (
			f"rclone mount {remote}/ {proj}/ "
			f"--config /root/.config/rclone/rclone.conf "
			f"--vfs-cache-mode full --vfs-write-back 5s --vfs-cache-max-size 2G "
			f"--dir-cache-time 0 --poll-interval 5s "
			f"--cache-dir {cache_dir} --daemon --allow-other --no-checksum --no-update-modtime"
		)
		try:
			self._sandbox.commands.run(cmd, timeout=30, user="root", envs=self._s3_envs())
		except CommandExitException as e:
			# exit 32 = directory not empty; force empty and retry once
			if getattr(e, "exit_code", None) == 32:
				self._sandbox.commands.run(f"umount -l {proj}/ 2>/dev/null || true", timeout=10, user="root")
				self._sandbox.commands.run(f"rm -rf {proj}", timeout=15, user="root")
				self._sandbox.commands.run(f"mkdir -p {proj}", timeout=5, user="root")
				self._sandbox.commands.run(cmd, timeout=30, user="root", envs=self._s3_envs())
			else:
				raise
		# Wait up to 10s for mount to become responsive
		for _ in range(10):
			_time.sleep(1)
			try:
				r = self._sandbox.commands.run(f"ls {proj}/ >/dev/null 2>&1", timeout=5, user="root")
				if r.exit_code == 0:
					return
			except CommandExitException:
				pass
		raise RuntimeError("rclone mount did not become responsive within 10s")

	def _ensure_skills_repo(self) -> None:
		"""Ensure /opt/solven/skills has the skills git clone. Update in-place (pull or fetch+reset) when the repo already exists — never delete it while it may be bind-mounted. Fresh clone only when .git/HEAD is absent."""
		# Avoid "dubious ownership" when repo dir is owned by different user (e.g. template vs root)
		self._sandbox.commands.run(
			f"git config --global --add safe.directory {shlex.quote(OPT_SOLVEN_SKILLS)} 2>/dev/null || true",
			timeout=5, user="root",
		)
		if self._sandbox.files.exists(f"{OPT_SOLVEN_SKILLS}/.git/HEAD"):
			# Repo exists (survives sandbox pause/resume). Try to update in-place; never rm -rf while bind-mounted.
			try:
				self._sandbox.git.pull(
					path=OPT_SOLVEN_SKILLS,
					username=os.getenv("GIT_USERNAME"),
					password=os.getenv("GIT_TOKEN"),
					user="root",
					timeout=60,
				)
				return
			except Exception as e:
				logging.warning("_ensure_skills_repo git pull: %s", e)
			# pull failed (detached HEAD, diverged, etc.): fetch + hard reset in-place
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
					return
			except Exception as e:
				logging.warning("_ensure_skills_repo fetch+reset: %s", e)
			# Repo exists but can't update — leave as-is (stale content is still usable)
			return

		# No repo at all: fresh clone into a temp dir then move
		clone_dest = f"{OPT_SOLVEN_SKILLS}.clone"
		self._sandbox.commands.run(
			f"rm -rf {shlex.quote(clone_dest)}",
			timeout=15, user="root",
		)
		self._sandbox.commands.run(f"mkdir -p {shlex.quote(os.path.dirname(OPT_SOLVEN_SKILLS))}", timeout=5, user="root")
		try:
			self._sandbox.git.clone(
				SKILLS_REPO_URL,
				path=clone_dest,
				username=os.getenv("GIT_USERNAME"),
				password=os.getenv("GIT_TOKEN"),
				depth=1,
				user="root",
				timeout=120,
			)
		except Exception as e:
			msg = getattr(e, "stderr", None) or getattr(e, "stdout", None) or str(e)
			raise RuntimeError(f"Failed to clone skills repo into {OPT_SOLVEN_SKILLS}: {msg}") from e
		self._sandbox.commands.run(
			f"mv {shlex.quote(clone_dest)} {shlex.quote(OPT_SOLVEN_SKILLS)}",
			timeout=10, user="root",
		)

	def _sync_user_models_down(self) -> None:
		"""Copy user models from S3 into workspace .solven/skills/escrituras (templates, references)."""
		bucket = (os.getenv("S3_BUCKET_NAME") or "solven-testing").strip()
		user_id_str = str(self._user_id).strip()
		s3_base = f"s3remote:{bucket}/{self._tenant_id}/users/{user_id_str}"
		escrituras = f"{self._solven}/skills/escrituras"
		templates_dst = f"{escrituras}/assets/templates"
		references_dst = f"{escrituras}/references"
		self._sandbox.commands.run(
			f"mkdir -p {shlex.quote(templates_dst)} {shlex.quote(references_dst)}",
			timeout=10, user="root",
		)
		rclone = "rclone copy --config /root/.config/rclone/rclone.conf --fast-list --transfers 4 --no-update-modtime"
		for remote_sub, local_dst in [
			(f"{s3_base}/models/templates", templates_dst),
			(f"{s3_base}/models/references", references_dst),
		]:
			try:
				self._sandbox.commands.run(
					f"{rclone} {shlex.quote(remote_sub)}/ {shlex.quote(local_dst)}/ 2>/dev/null || true",
					timeout=120, user="root", envs=self._s3_envs(),
				)
			except Exception as e:
				logging.warning("_sync_user_models_down %s -> %s: %s", remote_sub, local_dst, e)

	def _populate_solven(self) -> None:
		"""Materialize .solven/skills: rsync from /opt/solven/skills; user models via rclone copy from S3. Skip rsync if SKILL.md present (resume)."""
		solven_skills = f"{self._solven}/skills"
		self._sandbox.commands.run(f"mkdir -p {shlex.quote(solven_skills)}", timeout=10, user="root")
		skill_marker = f"{solven_skills}/escrituras/SKILL.md"
		try:
			if self._sandbox.files.exists(skill_marker):
				self._sync_user_models_down()
				self._sandbox.commands.run(f"chown -R user:user {shlex.quote(self._solven)} 2>/dev/null || true", timeout=30, user="root")
				return
		except Exception as e:
			logging.warning("_populate_solven skip-check: %s", e)
		try:
			r = self._sandbox.commands.run(
				f"rsync -a {shlex.quote(OPT_SOLVEN_SKILLS)}/ {shlex.quote(solven_skills)}/",
				timeout=120, user="root",
			)
			if r.exit_code != 0:
				logging.warning("_populate_solven skills rsync exit %s: %s", r.exit_code, (r.stderr or r.stdout or "")[:200])
		except Exception as e:
			logging.warning("_populate_solven skills rsync: %s", e)
		self._sync_user_models_down()
		self._sandbox.commands.run(f"chown -R user:user {shlex.quote(self._solven)} 2>/dev/null || true", timeout=30, user="root")

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
		"""Lightweight health check: if project mount dead, remount; if SKILL.md absent, repopulate .solven."""
		if not self._sandbox or not getattr(self, "_workspace_ready", False):
			return
		try:
			r = self._sandbox.commands.run(
				f"mountpoint -q {shlex.quote(self._project)}/ && ls {shlex.quote(self._project)}/ >/dev/null 2>&1",
				timeout=5, user="root",
			)
			if r.exit_code != 0:
				self._mount_project()
		except (CommandExitException, Exception) as e:
			logging.warning("_ensure_workspace_ready mount check: %s", e)
		try:
			skill_marker = f"{self._solven}/skills/escrituras/SKILL.md"
			if not self._sandbox.files.exists(skill_marker):
				self._populate_solven()
		except Exception as e:
			logging.warning("_ensure_workspace_ready skills repopulate: %s", e)

	def _ensure_initialized(self) -> None:
		"""Ensure sandbox and thread workspace are initialized (idempotent). Steps: ghost cleanup -> sandbox -> mkdir -> rclone -> mount -> skills -> .solven -> env -> chown -> liveness."""
		if self._initialized:
			self._ensure_workspace_ready()
			return

		# 1. Per-user sandbox (connect or create with lifecycle)
		self._get_or_create_user_sandbox()
		logging.info("Preparando espacio de trabajo...")

		# 2. Lazy-unmount legacy bind mounts (transition safety; requires _sandbox)
		self._cleanup_ghost_mounts()

		# 3. Boot layout (dirs); rclone config
		self._ensure_boot_dirs()
		self._configure_rclone()

		# 4. Mount project (S3 -> project/ via rclone mount; idempotent)
		self._mount_project()

		# 5. Ensure shared skills repo (clone if missing/broken)
		self._ensure_skills_repo()

		# 6. Populate .solven (skills + user models; idempotent)
		self._populate_solven()

		# 7. Per-thread env (.venv, node_modules)
		try:
			self._ensure_thread_env()
		except Exception as e:
			logging.warning("_ensure_thread_env at init: %s", e)

		# 8. chown workspace
		self._sandbox.commands.run(f"chown -R user:user {shlex.quote(self._workspace)}", timeout=60, user="root")

		# 9. Liveness: project mount responsive
		try:
			r = self._sandbox.commands.run(
				f"mountpoint -q {shlex.quote(self._project)}/ && ls {shlex.quote(self._project)}/ >/dev/null 2>&1",
				timeout=5, user="root",
			)
			alive = r.exit_code == 0
		except Exception as e:
			logging.warning("liveness check mount: %s", e)
			alive = False
		if not alive:
			raise RuntimeError("Sandbox project mount missing or unresponsive after init")

		# 10. Verify skills populated; warn if empty
		try:
			skill_marker = f"{self._solven}/skills/escrituras/SKILL.md"
			if not self._sandbox.files.exists(skill_marker):
				logging.warning(".solven/skills/escrituras/SKILL.md not found after init — skills may be unavailable")
		except Exception as e:
			logging.warning("skills marker check after init: %s", e)

		self._workspace_ready = True
		self._ensure_bwrap()
		self._initialized = True
		ctx = getattr(getattr(self, "runtime", None) or getattr(self, "_runtime", None), "context", None)
		_context_set_backend(ctx, self)
		logging.info("Espacio de trabajo listo")

	def _persist_shell_cmd(self) -> str:
		"""Build persist as single shell command: user models only (rclone copy .solven/escrituras -> S3). Project files auto-sync via rclone VFS. Serialized per thread via flock."""
		persist_lock = f"{SOLVEN_LOCKS}/{self._thread_id}.persist.lock"
		bucket = (os.getenv("S3_BUCKET_NAME") or "solven-testing").strip()
		user_id_str = str(self._user_id).strip()
		remote_templates = f"s3remote:{bucket}/{self._tenant_id}/users/{user_id_str}/models/templates"
		remote_references = f"s3remote:{bucket}/{self._tenant_id}/users/{user_id_str}/models/references"
		escrituras = f"{self._solven}/skills/escrituras"
		rclone = "rclone copy --config /root/.config/rclone/rclone.conf --fast-list --transfers 4 --no-update-modtime"
		inner = (
			f"{rclone} {shlex.quote(escrituras)}/assets/templates/ {shlex.quote(remote_templates)}/ 2>/dev/null || true && "
			f"{rclone} {shlex.quote(escrituras)}/references/ {shlex.quote(remote_references)}/ 2>/dev/null || true"
		)
		return f"flock -n {shlex.quote(persist_lock)} -c {shlex.quote(inner)}"

	def persist_workspace(self, background: bool = True) -> None:
		"""Persist user models to S3 (project files auto-sync via rclone VFS). Skips when not dirty."""
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
		"""Wrap command in bwrap; bind project/ -> /, .solven -> /.solven, .venv -> /.venv, node_modules -> /node_modules."""
		path_env = "/.venv/bin:/.local/bin:/.bun/bin:/node_modules/.bin:/usr/local/bin:/usr/bin:/bin"
		args = [
			"bwrap",
			"--bind", self._project, "/",
			"--bind", self._solven, "/.solven",
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
		# Base runs execute() in bwrap with thread workspace bound to /; use agent path as-is
		result = super().ls_info(path)
		out = [p for p in result if self._is_workspace_path(p["path"])]
		return out

	def glob_info(self, pattern: str, path: str = "/") -> list["FileInfo"]:
		"""List files matching pattern via find -iname (case-insensitive) so e.g. **/acta* matches ACTA JUNTA UNIVERSAL."""
		self._ensure_initialized()
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
		search_path = shlex.quote((path or ".").rstrip("/") or "/")
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
		Convert document bytes (PDF, DOCX, images, etc.) to markdown. Used only for document types,
		not for code or plain text. Prefer Modal GPU (Docling VLM) when configured; fall back to
		local Docling on failure or when Modal is not configured. Raises on conversion error.
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

	def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
		"""
		Download files from the sandbox. Paths are agent-visible (e.g. /file.docx or /.solven/skills/...);
		resolved to real sandbox paths under /workspaces/{thread_id} via _resolve_workspace_path.
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
		return await asyncio.to_thread(self.read, file_path, offset, limit)

	async def awrite(self, file_path: str, content: str) -> WriteResult:
		return await asyncio.to_thread(self.write, file_path, content)

	async def agrep_raw(self, pattern: str, path: str | None, glob: str | None = None) -> list[GrepMatch] | str:
		return await asyncio.to_thread(self.grep_raw, pattern, path, glob)

	async def aglob_info(self, pattern: str, path: str) -> list[FileInfo]:
		return await asyncio.to_thread(self.glob_info, pattern, path)
