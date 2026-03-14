"""
E2B Sandbox backend for DeepAgents using S3.
Implements the BackendProtocol for filesystem operations in an isolated sandbox environment.

ARCHITECTURE (reliability-first: two-tier storage, explicit sync boundaries):
===========================================================================
- One E2B sandbox per user (metadata userId); lifecycle: on_timeout=pause, auto_resume=true.
- Thread store: /var/lib/solven/threads/{thread_id} — hydrated from S3 (rclone sync). Authoritative local copy.
- Runtime: /var/lib/solven/runtime/{thread_id} — copy of thread for execution (rsync from thread store).
- /workspaces/{thread_id} — symlink to runtime for compatibility; bwrap binds runtime as /.
- /opt/solven/skills — shared skills (git clone at boot); rsync into runtime .solven at thread init.
- /mnt/user — rclone mount of S3 {tenant}/users/{user_id}; templates/references from here into runtime .solven. If mount fails, fallback: rclone sync -> /var/lib/solven/users/{user_id}.
- Persist: rsync runtime -> thread store (exclude .venv, node_modules, .bun, .git), then rclone sync thread store -> S3.
- Crash recovery: on sandbox start, rm -rf /var/lib/solven/runtime/* (runtime is ephemeral).
"""

import base64
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

from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import WriteResult, EditResult, ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.utils import FileInfo, GrepMatch
from langchain.tools import ToolRuntime
from langgraph.config import get_stream_writer, get_config
from langgraph.graph.state import RunnableConfig
from src.models import AppContext
from src.backend import _parse_skillmd_frontmatter
from src.utils.config import get_user
# Workspace and user models (S3 mount at /mnt/user) via rclone in-sandbox; no s3_utils for tar/manifest.

SANDBOX_TEMPLATE = "solven-sandbox-v1"
SKILLS_REPO_URL = "https://github.com/metalossAI/solven-skills.git"

# Reliability-first layout (see plan).
SOLVEN_THREADS = "/var/lib/solven/threads"
SOLVEN_RUNTIME = "/var/lib/solven/runtime"
SOLVEN_USERS = "/var/lib/solven/users"  # Local sync of S3 tenant/users/{user_id}; no FUSE mount
OPT_SOLVEN_SKILLS = "/opt/solven/skills"
MNT_USER = "/mnt/user"
WORKSPACES = "/workspaces"

# Dirs skipped during in-workspace glob / grep searches (caches, mounts, system).
_WORKSPACE_SEARCH_SKIP_DIRS = frozenset({
    "usr", "etc", "proc", "dev", "sys", "run", "tmp",
    "bin", "sbin", "lib", "lib64",
    "node_modules", ".venv", "venv", "env", ".bun",
    ".git",
})

# Top-level names to hide from agent in ls_info/glob_info/grep_raw (bwrap exposes /usr, /etc, etc.).
_AGENT_HIDDEN_TOPLEVEL = frozenset({
    "mnt", "usr", "etc", "proc", "dev", "sys", "run", "lib", "lib64", "bin", "sbin", "tmp", "cache",
})


class SandboxBackend(BaseSandbox):
	"""
	E2B Sandbox backend with bwrap isolation: /workspace is bound to / inside the container.
	Paths are never rewritten; only result filtering (ls_info, glob_info, grep_raw) hides system dirs.
	Init and sync run outside bwrap; execute (and thus read/write/edit/ls_info/glob_info/grep_raw) run inside bwrap.
	"""

	def __init__(self, runtime: ToolRuntime[AppContext]):
		self._sandbox: Optional[Sandbox] = None
		self._writer = get_stream_writer()

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

		# Two-tier layout: thread store (hydrated from S3), runtime (execution copy), symlink for compatibility.
		self._thread_store = f"{SOLVEN_THREADS}/{self._thread_id}"
		self._runtime_workspace = f"{SOLVEN_RUNTIME}/{self._thread_id}"
		self._thread_workspace = self._runtime_workspace  # Path used for resolution and bwrap (agent sees this as /)
		self._workspaces_symlink = f"{WORKSPACES}/{self._thread_id}"
		self._user_mount = MNT_USER  # rclone mount of S3 {tenant}/users/{user_id}; must be mounted and populated
		self._user_local = f"{SOLVEN_USERS}/{self._user_id}"  # fallback: sync copy if mount unavailable
		self._workspace_skills_dir = f"{self._thread_workspace}/.solven/skills"

		self._workspace_ready = False
		self._initialized = False
		self._bwrap_available: Optional[bool] = None

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
		Resolve agent-visible path to this thread's runtime workspace path (under SOLVEN_RUNTIME).
		Download/upload scope is strictly the runtime workspace (bwrap root).
		Agent paths (e.g. /.solven/skills/docx/SKILL.md or /file.docx) are converted to
		real paths under that root. Normalizes and constrains so '..' cannot escape.
		"""
		if not path or path.strip() in ("", "/"):
			return self._thread_workspace
		p = path.strip().replace("\\", "/")
		if p.startswith(self._thread_workspace):
			resolved = os.path.normpath(p)
		else:
			# Agent path: / or relative -> under thread workspace
			rel = p.lstrip("/")
			resolved = os.path.normpath(f"{self._thread_workspace}/{rel}")
		# Must remain under thread workspace (prevent .. escape)
		if not resolved.startswith(self._thread_workspace):
			return self._thread_workspace
		return resolved

	def _to_agent_path(self, real_path: str) -> str:
		"""Convert real path under thread workspace to agent-visible path (where / = thread root)."""
		if not real_path.startswith(self._thread_workspace):
			return real_path
		suffix = real_path[len(self._thread_workspace):].lstrip("/")
		return f"/{suffix}" if suffix else "/"

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
		"""Create reliability-first layout (idempotent). Matches template start_cmd; run if backend init runs before start_cmd."""
		self._sandbox.commands.run(
			f"mkdir -p {shlex.quote(SOLVEN_THREADS)} {shlex.quote(SOLVEN_RUNTIME)} {shlex.quote(SOLVEN_USERS)} "
			f"{shlex.quote(OPT_SOLVEN_SKILLS)} {shlex.quote(MNT_USER)} {shlex.quote(WORKSPACES)}",
			timeout=10, user="root",
		)

	def _ensure_skills_repo(self) -> None:
		"""Ensure /opt/solven/skills is a valid git clone. If repo exists, pull to update; if pull fails or repo missing, re-clone."""
		if self._sandbox.files.exists(f"{OPT_SOLVEN_SKILLS}/.git/HEAD"):
			try:
				self._sandbox.git.pull(
					path=OPT_SOLVEN_SKILLS,
					username=os.getenv("GIT_USERNAME"),
					password=os.getenv("GIT_TOKEN"),
					user="root",
					timeout=60,
				)
				return
			except Exception:
				pass  # pull failed (e.g. corrupt or detached), fall through to re-clone
		# Remove existing dir if present (rm -rf exits 0 even when path is missing)
		self._sandbox.commands.run(
			f"rm -rf {shlex.quote(OPT_SOLVEN_SKILLS)}",
			timeout=15, user="root",
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
			raise RuntimeError(f"Failed to clone skills repo into {OPT_SOLVEN_SKILLS}: {msg}") from e

	def _hydrate_thread_store(self) -> None:
		"""Ensure thread store exists: rclone sync S3 -> /var/lib/solven/threads/{thread_id}. Only when necessary."""
		bucket = (os.getenv("S3_BUCKET_NAME") or "solven-testing").strip()
		remote = f"s3remote:{bucket}/{self._tenant_id}/threads/{self._thread_id}"
		cmd = (
			f"rclone sync --config /root/.config/rclone/rclone.conf "
			f"{shlex.quote(remote)}/ {shlex.quote(self._thread_store)}/"
		)
		try:
			self._sandbox.commands.run(cmd, timeout=300, user="root", envs=self._s3_envs())
		except Exception:
			pass

	def _create_runtime_workspace(self) -> None:
		"""Copy thread store to runtime; create compatibility symlink /workspaces/{thread_id} -> runtime."""
		self._sandbox.commands.run(
			f"rsync -a {shlex.quote(self._thread_store)}/ {shlex.quote(self._runtime_workspace)}/",
			timeout=300, user="root",
		)
		self._sandbox.commands.run(
			f"ln -sfn {shlex.quote(self._runtime_workspace)} {shlex.quote(self._workspaces_symlink)}",
			timeout=10, user="root",
		)

	def _build_solven_in_runtime(self) -> None:
		"""Materialize .solven in runtime: skills from /opt/solven/skills, user templates/references from /mnt/user (mount) or synced fallback."""
		dst = self._runtime_workspace
		solven_skills = f"{dst}/.solven/skills"
		self._sandbox.commands.run(f"mkdir -p {shlex.quote(solven_skills)}", timeout=10, user="root")
		# Skills from shared clone (creates escrituras/, docx/, etc.)
		try:
			self._sandbox.commands.run(
				f"rsync -a {shlex.quote(OPT_SOLVEN_SKILLS)}/ {shlex.quote(solven_skills)}/",
				timeout=120, user="root",
			)
		except Exception:
			pass
		escrituras_assets = f"{solven_skills}/escrituras/assets"
		escrituras_references = f"{solven_skills}/escrituras/references"
		self._sandbox.commands.run(f"mkdir -p {shlex.quote(escrituras_assets)} {shlex.quote(escrituras_references)}", timeout=10, user="root")
		# User templates/references: use synced _user_local (fresh rclone sync before this step); mount VFS can be stale/empty
		src_base = self._user_local
		for src_sub, dst_sub in [
			(f"{src_base}/models/templates", escrituras_assets),
			(f"{src_base}/models/references", escrituras_references),
		]:
			try:
				self._sandbox.commands.run(
					f"rsync -a {shlex.quote(src_sub)}/ {shlex.quote(dst_sub)}/ 2>/dev/null || true",
					timeout=60, user="root",
				)
			except Exception:
				pass
		self._sandbox.commands.run(f"chown -R user:user {shlex.quote(dst)}/.solven 2>/dev/null || true", timeout=30, user="root")

	def _ensure_thread_env(self) -> None:
		"""Create .venv and node_modules inside workspace only when missing. Excluded from rclone sync."""
		venv_python = f"{self._thread_workspace}/.venv/bin/python"
		resources_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "e2b_sandbox", "resources")
		pkg_path = f"{self._thread_workspace}/package.json"
		py_path = f"{self._thread_workspace}/pyproject.toml"
		# Skip if env already exists
		try:
			if self._sandbox.files.exists(venv_python):
				return
		except Exception:
			pass
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
				f"cd {shlex.quote(self._thread_workspace)} && uv sync",
				timeout=300, user="root",
			)
		except Exception:
			pass
		try:
			self._sandbox.commands.run(
				f"cd {shlex.quote(self._thread_workspace)} && bun install",
				timeout=120, user="root",
			)
		except Exception:
			pass

	def _ensure_initialized(self) -> None:
		"""Ensure sandbox and thread workspace are initialized (idempotent). Reliability-first: boot dirs -> hydrate -> runtime -> .solven -> env."""
		if self._initialized:
			return

		# 1. Per-user sandbox (connect or create with lifecycle)
		self._get_or_create_user_sandbox()
		self._writer("Preparando espacio de trabajo...")

		# 2. Boot layout (dirs); rclone config; mount user folder at /mnt/user (S3 -> FUSE mount)
		self._ensure_boot_dirs()
		self._upload_mount_scripts()
		try:
			self._mount_user()
		except Exception:
			try:
				self._sync_user_folder()
			except Exception:
				pass

		# 3. Thread hydration: S3 -> thread store
		self._hydrate_thread_store()

		# 4. Runtime workspace: rsync thread store -> runtime, symlink /workspaces/{id} -> runtime
		self._create_runtime_workspace()

		# 5. Fresh sync of user folder S3 -> local so we have latest content (mount VFS can be stale)
		try:
			self._sync_user_folder()
		except Exception:
			pass

		# 6. Ensure shared skills repo exists so every workspace has .solven/skills (clone if missing/broken)
		self._ensure_skills_repo()

		# 7. Build .solven in runtime (skills from /opt/solven/skills, user templates/references from synced _user_local)
		self._build_solven_in_runtime()

		# 8. Per-thread env (.venv, node_modules inside runtime; excluded from persist)
		try:
			self._ensure_thread_env()
		except Exception:
			pass

		# 9. chown runtime workspace
		self._sandbox.commands.run(f"chown -R user:user {shlex.quote(self._runtime_workspace)}", timeout=60, user="root")

		# 10. Liveness: runtime exists
		try:
			alive = self._sandbox.files.exists(self._runtime_workspace)
		except Exception:
			alive = False
		if not alive:
			raise RuntimeError("Sandbox runtime workspace missing after init")

		self._workspace_ready = True
		self._ensure_bwrap()
		self._initialized = True
		self._writer("Espacio de trabajo listo")

	def persist_workspace(self, background: bool = True) -> None:
		"""Two-step persist: rsync runtime -> thread store (exclude env dirs), then rclone sync thread store -> S3."""
		if not self._sandbox or not self._workspace_ready:
			return
		excludes = " ".join(
			f"--exclude {shlex.quote(d)}" for d in (".venv", "node_modules", ".bun", ".git")
		)
		try:
			self._sandbox.commands.run(
				f"rsync -a --delete {excludes} "
				f"{shlex.quote(self._runtime_workspace)}/ {shlex.quote(self._thread_store)}/",
				timeout=300, user="root",
			)
		except Exception:
			return
		bucket = (os.getenv("S3_BUCKET_NAME") or "solven-testing").strip()
		remote = f"s3remote:{bucket}/{self._tenant_id}/threads/{self._thread_id}"
		try:
			self._sandbox.commands.run(
				f"rclone sync --config /root/.config/rclone/rclone.conf "
				f"{shlex.quote(self._thread_store)}/ {shlex.quote(remote)}/",
				timeout=300, user="root", envs=self._s3_envs(),
			)
		except Exception:
			pass

	def _upload_mount_scripts(self) -> None:
		"""Upload rclone mount scripts: write to /root then copy to /tmp (avoids E2B/shell redirect issues with /tmp)."""
		src_dir = os.path.dirname(os.path.abspath(__file__))
		script_dir = os.path.join(src_dir, "e2b_sandbox", "scripts")

		with open(os.path.join(script_dir, "create_rclone_config.sh"), "r") as f:
			config_script = f.read()
		with open(os.path.join(script_dir, "mount_s3_path.sh"), "r") as f:
			mount_script = f.read()

		for dest_name, content in [
			("create_rclone_config.sh", config_script),
			("mount_s3_path.sh", mount_script),
		]:
			b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
			result = self._sandbox.commands.run(
				f"echo {shlex.quote(b64)} | base64 -d > /root/{shlex.quote(dest_name)}",
				timeout=30,
				user="root",
			)
			if result.exit_code != 0:
				raise RuntimeError(f"Failed to write /root/{dest_name}: {result.stderr or result.stdout}")
			result = self._sandbox.commands.run(
				f"cp /root/{shlex.quote(dest_name)} /tmp/{shlex.quote(dest_name)}",
				timeout=10,
				user="root",
			)
			if result.exit_code != 0:
				raise RuntimeError(f"Failed to cp /root/{dest_name} to /tmp: {result.stderr or result.stdout}")

		chmod = self._sandbox.commands.run(
			"chmod +x /tmp/create_rclone_config.sh /tmp/mount_s3_path.sh",
			timeout=30,
			user="root",
		)
		if chmod.exit_code != 0:
			raise RuntimeError(f"Failed to make scripts executable: {chmod.stderr}")
		# Run create_rclone_config.sh with S3 env from host (pass via envs= so credentials are not in shell)
		rclone_envs = self._s3_envs()
		if rclone_envs.get("S3_ACCESS_KEY_ID") and rclone_envs.get("S3_ACCESS_SECRET"):
			run_result = self._sandbox.commands.run(
				"/tmp/create_rclone_config.sh",
				timeout=30,
				user="root",
				envs=rclone_envs,
			)
			if run_result.exit_code != 0:
				pass
		else:
			pass

	def _mount_user(self) -> None:
		"""Mount S3 at /mnt/user. S3 path = bucket/company_id/users/user_id (tenant_id = company_id). Idempotent. Envs passed to script."""
		bucket = (os.getenv("S3_BUCKET_NAME") or "solven-testing").strip()
		user_id_str = str(self._user_id).strip()
		# S3 path: company_id/users/user_id (tenant_id is company_id)
		s3_path = f"{self._tenant_id}/users/{user_id_str}".replace("//", "/")
		envs = self._s3_envs()

		check = self._sandbox.commands.run(
			f"mountpoint -q {shlex.quote(self._user_mount)} 2>/dev/null && echo yes || echo no",
			timeout=5, user="root",
		)
		if (check.stdout or "").strip() != "yes":
			log_file = "/tmp/rclone-user.log"
			cmd = f"/tmp/mount_s3_path.sh {shlex.quote(bucket)} {shlex.quote(s3_path)} {shlex.quote(self._user_mount)} {shlex.quote(log_file)} user"
			result = self._sandbox.commands.run(cmd, timeout=120, user="root", envs=envs)
			if result.exit_code != 0:
				err = (result.stderr or "").strip() or (result.stdout or "").strip()
				raise RuntimeError(f"rclone mount /mnt/user failed: {err}")

		try:
			self._sandbox.commands.run(
				f"mkdir -p {shlex.quote(self._user_mount)}/models/templates {shlex.quote(self._user_mount)}/models/references",
				timeout=15, user="root", envs=envs,
			)
		except Exception:
			pass

		try:
			for sub in ("", "/models", "/models/templates", "/models/references"):
				path = f"{self._user_mount}{sub}"
				self._sandbox.commands.run(
					f"ls -la {shlex.quote(path)} 2>&1 || true",
					timeout=15, user="root", envs=envs,
				)
				if sub != "/models/references":
					try:
						self._sandbox.commands.run("sleep 1", timeout=5, user="root")
					except Exception:
						pass
		except Exception:
			pass

	def _sync_user_folder(self) -> None:
		"""Sync S3 {tenant}/users/{user_id} -> local /var/lib/solven/users/{user_id}. No FUSE; guarantees local copy. Envs passed to rclone."""
		bucket = (os.getenv("S3_BUCKET_NAME") or "solven-testing").strip()
		user_id_str = str(self._user_id).strip()
		s3_path = f"{self._tenant_id}/users/{user_id_str}".replace("//", "/")
		remote = f"s3remote:{bucket}/{s3_path}"
		self._sandbox.commands.run(
			f"mkdir -p {shlex.quote(self._user_local)}",
			timeout=10, user="root",
		)
		cmd = (
			f"rclone sync --config /root/.config/rclone/rclone.conf "
			f"--s3-list-chunk 1000 "
			f"{shlex.quote(remote)}/ {shlex.quote(self._user_local)}/"
		)
		self._sandbox.commands.run(cmd, timeout=300, user="root", envs=self._s3_envs())
		# Ensure subdirs exist so _build_solven_in_runtime rsync always has valid sources
		self._sandbox.commands.run(
			f"mkdir -p {shlex.quote(self._user_local)}/models/templates {shlex.quote(self._user_local)}/models/references",
			timeout=10, user="root",
		)

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
		"""Wrap command in bwrap; bind thread workspace (local dir) to /. .venv and node_modules live in workspace and are excluded from rclone sync."""
		# PATH: thread's .venv, .local, bun, node_modules/.bin (all at /), then system
		path_env = "/.venv/bin:/.local/bin:/.bun/bin:/node_modules/.bin:/usr/local/bin:/usr/bin:/bin"
		args = [
			"bwrap",
			"--bind", self._thread_workspace, "/",
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

		try:
			self.persist_workspace(background=True)
		except Exception:
			pass

		return responses

	def _convert_to_markdown(self, content: bytes, filename: str) -> str:
		"""
		Convert file bytes to markdown. Prefer Modal GPU function (Docling VLM) when configured;
		fall back to local Docling (no VLM) on failure or when Modal is not configured.
		Raises on conversion error.
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

	def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
		"""
		Read a file from the sandbox: download via E2B, then either decode as text (plain-text
		extensions) or convert to markdown via Modal Docling (GPU) or local Docling fallback.
		Saves the markdown version alongside the original (e.g. doc.pdf -> doc.md) for reuse.
		Returns content with line-based pagination (offset/limit). Error string on failure.
		"""
		self._ensure_initialized()
		import requests

		real_path = self._resolve_workspace_path(file_path)
		if not self._sandbox.files.exists(real_path):
			return f"Error: File '{file_path}' not found"
		try:
			info = self._sandbox.files.get_info(real_path)
			type_val = getattr(info, "type", None)
			is_dir = getattr(info, "is_dir", False) or (str(type_val).lower().find("dir") >= 0)
			if is_dir:
				return f"Error: '{file_path}' is a directory"
		except Exception:
			pass

		ext = (Path(file_path).suffix or "").lower()
		plain_extensions = frozenset({
			".md", ".markdown", ".txt", ".text", ".csv", ".json", ".yaml", ".yml",
			".xml", ".log", ".rst", ".adoc", ".asciidoc", ".tex",
		})
		needs_conversion = ext not in plain_extensions and bool(ext)
		md_agent_path = str(Path(file_path).with_suffix(".md")) if "." in file_path else f"{file_path.rstrip('/')}.md"

		if needs_conversion:
			md_real_path = self._resolve_workspace_path(md_agent_path)
			if self._sandbox.files.exists(md_real_path):
				return self.read(md_agent_path, offset=offset, limit=limit)

		download_url = self._sandbox.download_url(real_path)
		if not needs_conversion:
			try:
				response = requests.get(download_url, timeout=60)
				response.raise_for_status()
				full_text = response.content.decode("utf-8", errors="replace")
			except Exception as e:
				return f"Error reading '{file_path}': {str(e)}"
		else:
			try:
				response = requests.get(download_url, timeout=60)
				response.raise_for_status()
				content = response.content
			except Exception as e:
				return f"Error downloading '{file_path}': {str(e)}"
			try:
				full_text = self._convert_to_markdown(content, Path(real_path).name)
			except Exception as e:
				return f"Error converting '{file_path}' to markdown: {str(e)}"
			try:
				self.write(md_agent_path, full_text)
			except Exception:
				pass

		lines = full_text.split("\n")
		total_lines = len(lines)
		start = max(0, offset)
		end = min(start + limit, total_lines)
		selected = lines[start:end]
		out = "\n".join(selected)
		if total_lines > limit:
			out += f"\n\n--- Lines {start + 1}-{end} of {total_lines} (offset={offset}, limit={limit}). Request next page with offset={end} ---"
		return out

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
