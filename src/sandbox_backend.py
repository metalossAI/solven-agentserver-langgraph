"""
E2B Sandbox backend for DeepAgents using S3.
Implements the BackendProtocol for filesystem operations in an isolated sandbox environment.

ARCHITECTURE OVERVIEW (plain /workspace + S3):
==============================================
- /workspace is a plain directory; commands run with cwd=/workspace and path token rewriting so all paths stay under /workspace.
- S3 thread workspace at /mnt/workspace-s3 (rclone FUSE). Optional one-time preload: S3 -> /workspace (non-blocking).
  Persist: rsync /workspace -> S3 in background.
- Skills: .solven/skills = copy of the solven-skills submodule. User models from S3 bind into escrituras/assets/templates, .../references, .../scripts/fill. Excluded from sync.

Sandbox paths:
- /workspace           - Agent root; persisted via rsync /workspace -> S3.
- /workspace/.solven/skills - Copy of project solven-skills; escrituras/assets/templates, references, scripts/fill are bind mounts from S3 user models.
- /mnt/workspace-s3     - S3 {tenant_id}/threads/{thread_id} (persist target only).
- /mnt/user-models      - S3 {tenant_id}/users/{user_id}/models (bind into escrituras/assets/templates, references, scripts/fill).
"""
import os
import re
import shlex
import asyncio
import time as _time
from typing import Optional

from e2b import Sandbox, SandboxQuery, SandboxState

from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import WriteResult, EditResult, ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.utils import FileInfo, GrepMatch
from langchain.tools import ToolRuntime
from langgraph.config import get_stream_writer, get_config
from langgraph.graph.state import RunnableConfig
from src.models import AppContext
from src.backend import _parse_skillmd_frontmatter

SANDBOX_TEMPLATE = "solven-sandbox-v1"

# Directories excluded from /workspace -> S3 sync. .solven/ (includes skills) excluded from sync.
_RSYNC_EXCLUDES = (
    ".solven/",
    ".venv/",
    "venv/",
    "env/",
    "npm/",
    "node_modules/",
    ".bun/",
)
_RSYNC_EXCLUDE_FLAGS = " ".join(f"--exclude='{p}'" for p in _RSYNC_EXCLUDES)
_RSYNC_ONE_FS = "--one-file-system"

# Dirs skipped during in-workspace glob / grep searches (caches, mounts, system).
_WORKSPACE_SEARCH_SKIP_DIRS = frozenset({
    "usr", "etc", "proc", "dev", "sys", "run", "tmp",
    "bin", "sbin", "lib", "lib64",
    "node_modules", ".venv", "venv", "env", ".bun",
    ".git", ".solven",
})


class SandboxBackend(BaseSandbox):
	"""
	E2B Sandbox backend with plain /workspace and S3. No overlay or chroot; cwd=/workspace and path resolution keep all operations under /workspace.

	Paths:
	- /workspace              - Agent root; persisted via rsync /workspace -> S3.
	- /workspace/.solven/skills - Copy of project solven-skills; escrituras/assets/templates, references, scripts/fill are bind mounts from S3 user models.
	- /mnt/workspace-s3     - S3 thread workspace (persist target only).
	- /mnt/user-models      - S3 user models; bound into .solven/skills/escrituras/assets/templates, .../references, .../scripts/fill.
	"""

	def __init__(self, runtime: ToolRuntime[AppContext]):
		self._sandbox: Optional[Sandbox] = None
		self._writer = get_stream_writer()

		from src.utils.config import get_user, get_thread_id

		thread_id = get_thread_id()
		if not thread_id:
			raise RuntimeError("Cannot initialize SandboxBackend: thread_id not found in config")
		self._thread_id = thread_id

		user = get_user()  # raises RuntimeError if missing
		self._user_id = user.id
		if not user.company_id:
			raise RuntimeError("Cannot initialize SandboxBackend: user company_id (tenant) not found in config")
		self._tenant_id = user.company_id

		# Paths
		self._workspace = "/workspace"
		self._workspace_s3_mount = "/mnt/workspace-s3"
		self._user_models_mount = "/mnt/user-models"  # S3 {tenant_id}/users/{user_id}/models FUSE mount; bound into escrituras/assets and references
		self._workspace_skills_dir = "/workspace/.solven/skills"  # Copy of project solven-skills; excluded from sync

		self._workspace_ready = False
		self._initialized = False

	def _resolve_workspace_path(self, path: str) -> str:
		"""
		Convert agent-visible path (where / = /workspace) to real sandbox path.
		"""

		if not path or path.strip() in ("", "/"):
			return self._workspace

		p = path.strip()

		# already real path
		if p.startswith(self._workspace):
			return p

		return f"{self._workspace}/{p.lstrip('/')}"

	def _to_agent_path(self, real_path: str) -> str:
		"""Convert real path under /workspace to agent-visible path (where / = /workspace)."""
		if not real_path.startswith(self._workspace):
			return real_path
		suffix = real_path[len(self._workspace):].lstrip("/")
		return f"/{suffix}" if suffix else "/"

	@property
	def id(self) -> str:
		"""Unique identifier for the sandbox backend instance."""
		if self._sandbox:
			return self._sandbox.sandbox_id
		return f"sandbox-{self._thread_id}"

	def _ensure_initialized(self) -> None:
		"""Ensure sandbox is initialized (idempotent). Uses self._initialized as guard.
		On first run: create sandbox, mount S3, create /workspace, copy skills, mount user models.
		When reusing an existing sandbox: skip all configuration; only run a minimal liveness check.
		"""
		if self._initialized:
			return

		# Step 1: Try to connect to an existing RUNNING sandbox for this thread.
		# We only query RUNNING — connecting to PAUSED sandboxes triggers an E2B resume
		# that can hang indefinitely.
		print(f"[_ensure_initialized] Searching for sandbox threadId={self._thread_id}", flush=True)
		existing_sandboxes = []
		try:
			paginator = Sandbox.list(
				query=SandboxQuery(
					metadata={"threadId": self._thread_id},
					state=[SandboxState.RUNNING],
				)
			)
			existing_sandboxes = paginator.next_items()
			print(f"[_ensure_initialized] Found {len(existing_sandboxes)} running sandbox(es)", flush=True)
		except Exception as e:
			print(f"[_ensure_initialized] ✗ Error listing sandboxes: {e}", flush=True)

		for sb_info in existing_sandboxes:
			sandbox_id = sb_info.sandbox_id
			if not sandbox_id:
				continue
			try:
				print(f"[_ensure_initialized] Connecting to {sandbox_id}...", flush=True)
				self._sandbox = Sandbox.connect(sandbox_id)
				print(f"[_ensure_initialized] ✓ Connected to existing sandbox: {sandbox_id}", flush=True)

				# Sandbox was configured on first run; skip all re-configuration when reusing.
				# Only verify it is responsive and /workspace exists (minimal liveness check).
				try:
					check = self._sandbox.commands.run(
						"test -d /workspace && echo OK || echo MISSING",
						timeout=10,
					)
					alive = (check.stdout or "").strip() == "OK"
				except Exception:
					alive = False

				if not alive:
					print(f"[_ensure_initialized] Reused sandbox not ready (/workspace missing), will create new", flush=True)
					self._sandbox = None
					continue

				self._workspace_ready = True
				self._initialized = True
				print(f"[_ensure_initialized] ✓ Reused existing sandbox (config skipped)", flush=True)
				self._start_background_syncs()
				return
			except Exception as e:
				print(f"[_ensure_initialized] ✗ Failed to use sandbox {sandbox_id}: {e}", flush=True)
				self._sandbox = None

		# Step 2: No usable existing sandbox — create a new one.
		print(f"[_ensure_initialized] Creating new sandbox...", flush=True)
		env_vars = {"THREAD_ID": self._thread_id, "USER_ID": str(self._user_id)}
		for key, env in [
			("S3_BUCKET_NAME", "S3_BUCKET_NAME"),
			("S3_ACCESS_KEY_ID", "S3_ACCESS_KEY_ID"),
			("S3_ACCESS_SECRET", "S3_ACCESS_SECRET"),
			("S3_ENDPOINT_URL", "S3_ENDPOINT_URL"),
			("S3_REGION", "S3_REGION"),
		]:
			if val := os.getenv(env):
				env_vars[key] = val

		self._sandbox = Sandbox.create(
			template=SANDBOX_TEMPLATE,
			envs=env_vars,
			timeout=300,
			metadata={"threadId": self._thread_id, "userId": str(self._user_id)},
		)
		print(f"[_ensure_initialized] ✓ Created new sandbox: {self._sandbox.sandbox_id}", flush=True)
		self._writer("Preparando espacio de trabajo...")

		# Step 3: S3 workspace mount
		self._mount_s3_buckets()

		# Step 4: Create plain /workspace (no overlay)
		self._sandbox.commands.run(
			f"mkdir -p {self._workspace} {self._workspace}/tmp",
			timeout=10, user="root",
		)

		# Step 5: Optional preload S3 -> /workspace (background, non-blocking)
		try:
			self._sandbox.commands.run(
				f"rsync -av {_RSYNC_ONE_FS} {_RSYNC_EXCLUDE_FLAGS} "
				f"{self._workspace_s3_mount}/ {self._workspace}/ && chown -R user:user {self._workspace} 2>/dev/null || true",
				timeout=180, user="root", background=True,
			)
		except Exception as e:
			print(f"[_ensure_initialized] ⚠ Preload start failed (non-critical): {e}", flush=True)

		# Step 6: Copy .solven/skills from project solven-skills submodule
		try:
			self._copy_skills_from_submodule()
		except Exception as e:
			print(f"[_ensure_initialized] ⚠ Copy skills from submodule failed (non-blocking): {e}", flush=True)
		# Fallback: if .solven/skills still missing and S3 template prefix set, sync from S3
		if not self._check_skills_dir() and os.getenv("SKILL_S3_TEMPLATE_PREFIX", "").strip():
			try:
				self._ensure_skills_from_s3()
			except Exception as e:
				print(f"[_ensure_initialized] ⚠ S3 skills fallback failed: {e}", flush=True)
		try:
			self._mount_user_models()
		except Exception as e:
			print(f"[_ensure_initialized] ⚠ User models mount failed (non-blocking): {e}", flush=True)

		# Step 7: chown /workspace
		self._sandbox.commands.run(f"chown -R user:user {self._workspace}", timeout=60, user="root")

		# Step 8: Mark ready, start background sync (/workspace -> S3)
		self._workspace_ready = True
		self._initialized = True
		self._writer("Espacio de trabajo listo")
		print(f"[_ensure_initialized] ✓ Initialization complete", flush=True)
		self._start_background_syncs()

	def _check_skills_dir(self) -> bool:
		"""True if .solven/skills exists and contains at least one skill (e.g. escrituras)."""
		try:
			if not self._sandbox.files.exists(self._workspace_skills_dir):
				return False
			# Must have at least one skill dir (e.g. escrituras with SKILL.md)
			entries = self._sandbox.files.list(self._workspace_skills_dir) or []
			for e in entries:
				if e.name in ("escrituras", "docx", "pdf") or (e.is_dir and self._sandbox.files.exists(f"{self._workspace_skills_dir}/{e.name}/SKILL.md")):
					return True
			return False
		except Exception:
			return False

	def _ensure_skills_from_s3(self) -> None:
		"""Populate .solven/skills from S3 template prefix when SKILL_S3_TEMPLATE_PREFIX is set.
		Syncs s3://bucket/{SKILL_S3_TEMPLATE_PREFIX}/ to /workspace/.solven/skills.
		"""
		prefix = (os.getenv("SKILL_S3_TEMPLATE_PREFIX") or "").strip().rstrip("/")
		if not prefix:
			return
		bucket = os.getenv("S3_BUCKET_NAME", "solven-testing")
		s3_prefix = prefix
		self._sandbox.commands.run("mkdir -p /workspace/.solven", timeout=10, user="root")
		# rclone copy s3:bucket/skills-template/ /workspace/.solven/skills/ (requires rclone in sandbox and S3 mounted or rclone config)
		# Sandbox already has /tmp/mount_s3_path.sh and S3 credentials; mount the template path and rsync
		template_mount = "/mnt/skills-template"
		self._sandbox.commands.run(f"mkdir -p {template_mount}", timeout=5, user="root")
		try:
			result = self._sandbox.commands.run(
				f'bash /tmp/mount_s3_path.sh "{bucket}" "{s3_prefix}" "{template_mount}" "/tmp/rclone-skills-tpl.log" immediate',
				timeout=120, user="root",
			)
			if result.exit_code != 0:
				raise RuntimeError(f"S3 skills template mount failed (exit {result.exit_code})")
			_time.sleep(1)
			self._sandbox.commands.run(
				f"rsync -a {template_mount}/ {self._workspace_skills_dir}/ && chown -R user:user {self._workspace_skills_dir}",
				timeout=60, user="root",
			)
			self._sandbox.commands.run(f"umount {template_mount} 2>/dev/null || true", timeout=5, user="root")
			print(f"[_ensure_skills_from_s3] ✓ Synced {s3_prefix} -> .solven/skills", flush=True)
		finally:
			self._sandbox.commands.run(f"umount {template_mount} 2>/dev/null || true", timeout=5, user="root")

	def _get_skills_source_path(self) -> Optional[str]:
		"""Path to solven-skills submodule (project dir). Prefer SKILLS_SOURCE_PATH env, else repo/solven-skills."""
		env_path = os.getenv("SKILLS_SOURCE_PATH", "").strip()
		if env_path:
			path = os.path.abspath(os.path.expanduser(env_path))
			return path if os.path.isdir(path) else None
		# Default: solven-skills next to this package (repo root = parent of src)
		root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
		path = os.path.join(root, "solven-skills")
		return path if os.path.isdir(path) else None

	def _copy_skills_from_submodule(self) -> None:
		"""Copy project solven-skills submodule into /workspace/.solven/skills. Skips .git, __pycache__."""
		source = self._get_skills_source_path()
		if not source:
			print("[_copy_skills_from_submodule] solven-skills not found (set SKILLS_SOURCE_PATH or add submodule)", flush=True)
			return
		skills_dir = self._workspace_skills_dir
		self._sandbox.commands.run("mkdir -p /workspace/.solven", timeout=10, user="root")
		try:
			if self._sandbox.files.exists(skills_dir):
				self._sandbox.commands.run(f"rm -rf {skills_dir}", timeout=30, user="root")
		except Exception as e:
			print(f"[_copy_skills_from_submodule] ⚠ Clear failed: {e}", flush=True)
		skip_dirs = {".git", "__pycache__", ".venv", "venv"}
		dirs_to_create = []
		files_to_write = []
		for dirpath, dirnames, filenames in os.walk(source, topdown=True):
			dirnames[:] = [d for d in dirnames if d not in skip_dirs]
			rel_dir = os.path.relpath(dirpath, source)
			if rel_dir != ".":
				dirs_to_create.append(rel_dir)
			for name in filenames:
				local_path = os.path.join(dirpath, name)
				rel_path = os.path.relpath(local_path, source)
				files_to_write.append((rel_path, local_path))
		# Create dirs in sandbox
		for rel_dir in sorted(dirs_to_create):
			remote_dir = f"{skills_dir}/{rel_dir}"
			self._sandbox.commands.run(f"mkdir -p {remote_dir}", timeout=10, user="root")
		# Write files
		for rel_path, local_path in files_to_write:
			remote_path = f"{skills_dir}/{rel_path}"
			with open(local_path, "rb") as f:
				data = f.read()
			self._sandbox.files.write(remote_path, data)
		self._sandbox.commands.run(f"chown -R user:user {skills_dir}", timeout=30, user="root")
		print(f"[_copy_skills_from_submodule] ✓ Copied {source} -> {skills_dir}", flush=True)

	def _mount_user_models(self) -> None:
		"""Mount S3 {tenant_id}/users/{user_id}/models at /mnt/user-models, then bind:
		- templates -> escrituras/assets/templates (all user models templates inside assets/)
		- references -> escrituras/references
		- fill_scripts -> escrituras/scripts/fill
		"""
		bucket = os.getenv("S3_BUCKET_NAME", "solven-testing")
		escrituras_assets_templates = f"{self._workspace_skills_dir}/escrituras/assets/templates"
		escrituras_references = f"{self._workspace_skills_dir}/escrituras/references"
		escrituras_scripts_fill = f"{self._workspace_skills_dir}/escrituras/scripts/fill"
		s3_models_prefix = f"{self._tenant_id}/users/{self._user_id}/models"
		# Unmount stale binds and FUSE
		self._sandbox.commands.run(
			f"mountpoint -q {escrituras_scripts_fill} 2>/dev/null && umount {escrituras_scripts_fill} 2>/dev/null || true; "
			f"mountpoint -q {escrituras_references} 2>/dev/null && umount {escrituras_references} 2>/dev/null || true; "
			f"mountpoint -q {escrituras_assets_templates} 2>/dev/null && umount {escrituras_assets_templates} 2>/dev/null || true; "
			f"mountpoint -q {self._user_models_mount} 2>/dev/null && umount {self._user_models_mount} 2>/dev/null || true",
			timeout=15, user="root",
		)
		self._sandbox.commands.run(
			f"mkdir -p {self._user_models_mount} {escrituras_assets_templates} {escrituras_references} {escrituras_scripts_fill}",
			timeout=30, user="root",
		)
		try:
			result = self._sandbox.commands.run(
				f'bash /tmp/mount_s3_path.sh "{bucket}" "{s3_models_prefix}" "{self._user_models_mount}" "/tmp/rclone-models-user.log" immediate',
				timeout=600, user="root",
			)
			if result.exit_code != 0:
				raise RuntimeError(f"Failed to mount user models (exit {result.exit_code})")
			import time
			time.sleep(2)
			verify = self._sandbox.commands.run(
				f"ls {self._user_models_mount} >/dev/null 2>&1 && echo 'MOUNT_OK' || echo 'MOUNT_FAILED'",
				timeout=10,
			)
			if "MOUNT_OK" not in verify.stdout:
				raise RuntimeError("User models S3 mount not accessible")
			# Ensure S3 subdirs exist for new users
			self._sandbox.commands.run(
				f"mkdir -p {self._user_models_mount}/templates {self._user_models_mount}/references {self._user_models_mount}/fill_scripts && "
				f"touch {self._user_models_mount}/templates/.keep {self._user_models_mount}/references/.keep {self._user_models_mount}/fill_scripts/.keep",
				timeout=10, user="root",
			)
			# Bind user models templates into assets/templates; references and fill_scripts into their paths
			self._sandbox.commands.run(
				f"mount --bind {self._user_models_mount}/templates {escrituras_assets_templates}",
				timeout=10, user="root",
			)
			self._sandbox.commands.run(
				f"mount --bind {self._user_models_mount}/references {escrituras_references}",
				timeout=10, user="root",
			)
			self._sandbox.commands.run(
				f"mount --bind {self._user_models_mount}/fill_scripts {escrituras_scripts_fill}",
				timeout=10, user="root",
			)
			self._sandbox.commands.run(
				f"chown -R user:user {escrituras_assets_templates} {escrituras_references} {escrituras_scripts_fill} 2>/dev/null || true",
				timeout=15, user="root",
			)
			self._sandbox.commands.run(
				f"chmod -R u+rwX {escrituras_assets_templates} {escrituras_references} {escrituras_scripts_fill} 2>/dev/null || true",
				timeout=15, user="root",
			)
			print(f"[_mount_user_models] ✓ templates->escrituras/assets/templates, references, fill_scripts->scripts/fill", flush=True)
		except Exception as e:
			raise

	def _mount_s3_buckets(self) -> None:
		"""Mount S3 buckets using rclone. All privileged operations use user='root'."""

		bucket = os.getenv("S3_BUCKET_NAME", "solven-testing")
		access_key = os.getenv("S3_ACCESS_KEY_ID")
		secret = os.getenv("S3_ACCESS_SECRET")
		endpoint = os.getenv("S3_ENDPOINT_URL", "")
		region = os.getenv("S3_REGION", "auto")

		if not access_key or not secret:
			print("[Mount] ✗ S3 credentials missing, skipping mounts", flush=True)
			return

		self._upload_mount_scripts()

		try:
			env_vars = f"S3_ENDPOINT_URL='{endpoint}' S3_ACCESS_KEY_ID='{access_key}' S3_ACCESS_SECRET='{secret}' S3_REGION='{region}'"
			result = self._sandbox.commands.run(
				f"{env_vars} bash /tmp/create_rclone_config.sh",
				timeout=180,
				user="root",
			)
			if result.exit_code != 0:
				raise RuntimeError(f"Failed to create rclone config (exit {result.exit_code}): {result.stderr or result.stdout}")
		except Exception as e:
			raise

		# Prepare mount dir
		self._sandbox.commands.run(
			f"mkdir -p {self._workspace_s3_mount}",
			timeout=30, user="root",
		)

		try:
			check_mount = self._sandbox.commands.run(
				f"mountpoint -q {self._workspace_s3_mount} 2>/dev/null && echo 'ALREADY_MOUNTED' || echo 'NOT_MOUNTED'",
				timeout=10,
			)
			if "ALREADY_MOUNTED" in check_mount.stdout:
				print(f"[Mount] {self._workspace_s3_mount} already mounted", flush=True)
				verify = self._sandbox.commands.run(
					f"ls {self._workspace_s3_mount} >/dev/null 2>&1 && echo 'MOUNT_OK' || echo 'MOUNT_FAILED'",
					timeout=10,
				)
				if "MOUNT_OK" in verify.stdout:
					return
				print(f"[Mount] Existing mount not accessible, remounting", flush=True)
				self._sandbox.commands.run(
					f"umount {self._workspace_s3_mount} 2>/dev/null || true",
					timeout=30,
					user="root",
				)
		except Exception as e:
			print(f"[Mount] Could not check existing mount: {e}", flush=True)

		s3_thread_prefix = f"{self._tenant_id}/threads/{self._thread_id}"
		mount_cmd = f'bash /tmp/mount_s3_path.sh "{bucket}" "{s3_thread_prefix}" "{self._workspace_s3_mount}" "/tmp/rclone-thread.log"'
		print(f"[Mount] Starting: {mount_cmd}", flush=True)
		try:
			result = self._sandbox.commands.run(mount_cmd, timeout=600, user="root")
			if result.exit_code != 0:
				try:
					log = self._sandbox.commands.run(
						"tail -100 /tmp/rclone-thread.log 2>&1 || echo 'No log file'",
						timeout=10, user="root",
					)
					log_output = log.stdout if log else "No log available"
				except Exception:
					log_output = "Could not read log file"
				raise RuntimeError(
					f"Failed to mount thread workspace (exit {result.exit_code}): "
					f"{result.stderr or result.stdout}\n\nLog:\n{log_output}"
				)

			import time
			time.sleep(2)
			verify = self._sandbox.commands.run(
				f"ls {self._workspace_s3_mount} >/dev/null 2>&1 && echo 'MOUNT_OK' || echo 'MOUNT_FAILED'",
				timeout=30,
			)
			if "MOUNT_OK" not in verify.stdout:
				ps = self._sandbox.commands.run(
					"ps aux | grep 'rclone.*mount.*threads' | grep -v grep || echo 'NO_PROCESS'",
					timeout=10,
				)
				raise RuntimeError(
					f"Mount verification failed.\nRclone: {ps.stdout}\nCheck: /tmp/rclone-thread.log"
				)
		except RuntimeError:
			raise
		except Exception as e:
			try:
				log = self._sandbox.commands.run(
					"tail -100 /tmp/rclone-thread.log 2>&1 || echo 'No log file'",
					timeout=10, user="root",
				)
				log_output = log.stdout if log else "No log available"
			except Exception:
				log_output = "Could not read log file"
			is_timeout = isinstance(e, TimeoutError) or "timeout" in str(e).lower()
			raise RuntimeError(
				f"Thread mount {'timed out' if is_timeout else 'failed'}: {e}\nLog:\n{log_output}"
			)

	def _preload_upper_from_s3(self) -> None:
		"""Optionally preload /workspace from S3 (one-time). Non-blocking; run in background to avoid blocking agent startup."""
		try:
			self._sandbox.commands.run(
				f"rsync -av {_RSYNC_ONE_FS} {_RSYNC_EXCLUDE_FLAGS} "
				f"{self._workspace_s3_mount}/ {self._workspace}/",
				timeout=180,
				user="root",
			)
			self._sandbox.commands.run(
				f"chown -R user:user {self._workspace} 2>/dev/null || true",
				timeout=30, user="root",
			)
			print(f"[Sync] ✓ S3 -> /workspace (preload)", flush=True)
		except Exception as e:
			print(f"[Sync] ⚠️  S3->/workspace preload failed (non-critical): {e}", flush=True)

	def _sync_workspace_to_s3(self) -> None:
		"""Persist /workspace to S3 (excludes .solven/, virtualenvs, node_modules/, etc.). Blocking; prefer _start_background_syncs()."""
		try:
			result = self._sandbox.commands.run(
				f"rsync -av {_RSYNC_ONE_FS} {_RSYNC_EXCLUDE_FLAGS} {self._workspace}/ {self._workspace_s3_mount}/",
				timeout=300,
				user="root",
			)
			if result.exit_code == 0:
				print(f"[Sync] ✓ /workspace -> S3", flush=True)
			else:
				print(f"[Sync] ⚠️  persist exit {result.exit_code}", flush=True)
		except Exception as e:
			print(f"[Sync] ⚠️  persist failed (non-critical): {e}", flush=True)

	def _start_background_syncs(self) -> None:
		"""Start /workspace -> S3 sync in background. .solven/ excluded from sync."""
		workspace_cmd = (
			f"rsync -av {_RSYNC_ONE_FS} {_RSYNC_EXCLUDE_FLAGS} "
			f"{self._workspace}/ {self._workspace_s3_mount}/"
		)
		try:
			self._sandbox.commands.run(workspace_cmd, background=True, user="root")
			print(f"[Sync] started background: /workspace -> /mnt/workspace-s3", flush=True)
		except Exception as e:
			print(f"[Sync] ⚠️  background sync start failed (non-critical): {e}", flush=True)

	def _upload_mount_scripts(self) -> None:
		"""Upload rclone mount scripts to sandbox from local files."""
		src_dir = os.path.dirname(os.path.abspath(__file__))
		script_dir = os.path.join(src_dir, "e2b_sandbox", "scripts")

		with open(os.path.join(script_dir, "create_rclone_config.sh"), "r") as f:
			config_script = f.read()
		with open(os.path.join(script_dir, "mount_s3_path.sh"), "r") as f:
			mount_script = f.read()

		self._sandbox.files.write("/tmp/create_rclone_config.sh", config_script)
		self._sandbox.files.write("/tmp/mount_s3_path.sh", mount_script)

		chmod = self._sandbox.commands.run(
			"chmod +x /tmp/create_rclone_config.sh /tmp/mount_s3_path.sh",
			timeout=30,
		)
		if chmod.exit_code != 0:
			raise RuntimeError(f"Failed to make scripts executable: {chmod.stderr}")

	def _execute_env(self) -> dict[str, str]:
		"""Environment for commands run in /workspace (cwd=/workspace, HOME=/workspace)."""
		return {
			"HOME": self._workspace,
			"TMPDIR": f"{self._workspace}/tmp",
			"PATH": f"{self._workspace}/.venv/bin:{self._workspace}/.local/bin:{self._workspace}/.bun/bin:/usr/local/bin:/usr/bin:/bin",
			"UV_PROJECT_ENVIRONMENT": f"{self._workspace}/.venv",
			"VIRTUAL_ENV": f"{self._workspace}/.venv",
			"BUN_INSTALL": f"{self._workspace}/.bun",
		}

	def _filter_unwanted_commands(self, command: str) -> Optional[str]:
		"""Block install commands so deps use uv (Python) and bun (Node). Allow pip/npm/npx for non-install (e.g. pip list, npm run)."""
		unwanted = {
			r"\bsudo\b": "Not allowed: sudo is not allowed in sandbox environment.",
			r"\bapt-get\s+(install|update)\b": "Not allowed: apt-get is not allowed (system packages pre-installed).",
			r"\bapt\s+(install|update)\b": "Not allowed: apt is not allowed (system packages pre-installed).",
		}
		for pattern, message in unwanted.items():
			if re.search(pattern, command, re.IGNORECASE):
				return message
		return None

	def execute(self, command: str) -> ExecuteResponse:
		"""Execute a shell command in the sandbox with all paths prefixed to /workspace. Uses shlex tokenization and cwd=/workspace."""
		self._ensure_initialized()
		if not self._workspace_ready:
			return ExecuteResponse(
				output="Error: workspace not ready (sandbox init did not complete).",
				exit_code=1,
				truncated=False,
			)

		if error_msg := self._filter_unwanted_commands(command):
			return ExecuteResponse(output=error_msg, exit_code=1, truncated=False)

		try:
			tokens = shlex.split(command, posix=True)
		except ValueError:
			return ExecuteResponse(
				output=f"Error parsing command: invalid quoting or shell syntax.",
				exit_code=1,
				truncated=False,
			)
		prefixed_tokens = []
		for tok in tokens:
			if tok.startswith("/") or tok.startswith("."):
				tok = self._resolve_workspace_path(tok)
			prefixed_tokens.append(tok)
		run_command = " ".join(shlex.quote(t) for t in prefixed_tokens)

		try:
			result = self._sandbox.commands.run(
				run_command,
				cwd=self._workspace,
				envs=self._execute_env(),
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
			self._start_background_syncs()
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

	def read(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
		"""Read file; path is resolved to /workspace before calling base."""
		real = self._resolve_workspace_path(file_path)
		return super().read(real, offset, limit)

	def write(self, file_path: str, content: str) -> WriteResult:
		"""Write file; path is resolved to /workspace before calling base."""
		real = self._resolve_workspace_path(file_path)
		return super().write(real, content)

	def edit(
		self,
		file_path: str,
		old_string: str,
		new_string: str,
		replace_all: bool = False,
	) -> EditResult:
		"""Edit file; path is resolved to /workspace before calling base."""
		real = self._resolve_workspace_path(file_path)
		return super().edit(real, old_string, new_string, replace_all)

	def ls_info(self, path: str) -> list[FileInfo]:
		"""List files; path is resolved to /workspace. Returned paths are converted to agent-visible."""
		real = self._resolve_workspace_path(path)
		result = super().ls_info(real)
		return [FileInfo(path=self._to_agent_path(p["path"]), is_dir=p["is_dir"]) for p in result]

	def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
		"""List files matching pattern; path is resolved to /workspace. Returned paths are agent-visible."""
		real = self._resolve_workspace_path(path.rstrip("/") or "/")
		result = super().glob_info(pattern, real)
		return [FileInfo(path=self._to_agent_path(p["path"]), is_dir=p["is_dir"]) for p in result]

	def grep_raw(
		self,
		pattern: str,
		path: str | None = None,
		glob: str | None = None,
	) -> list[GrepMatch] | str:
		"""Search in path; path is resolved to /workspace. Returned paths are agent-visible."""
		real_path = self._resolve_workspace_path((path or ".").rstrip("/") or "/")
		result = super().grep_raw(pattern, real_path, glob)
		if isinstance(result, str):
			return result
		return [
			{"path": self._to_agent_path(m["path"]), "line": m["line"], "text": m["text"]}
			for m in result
		]

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
			self._start_background_syncs()
		except Exception:
			pass

		return responses

	def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
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

				download_url = self._sandbox.download_url(real_path)
				print(f"DOWNLOADING: {download_url}", flush=True)

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
