"""
E2B Sandbox backend for DeepAgents using S3.
Implements the BackendProtocol for filesystem operations in an isolated sandbox environment.

ARCHITECTURE OVERVIEW (OverlayFS + S3):
======================================
- OverlayFS: lower=/sandbox/lower (read-only merged-usr), upper=/sandbox/upper/<tid>, work=/sandbox/work/<tid>.
  Merge mounted at /workspace. Agent sees /workspace as / via proot -0 -r /workspace.
- S3 thread workspace at /mnt/workspace-s3 (rclone FUSE). Optional one-time preload: S3 -> upper (non-blocking).
  Persist: rsync upper -> S3 in background only (no sync in critical path).
- Skills: S3 skills/{user_id} mounted at /mnt/user-skills, bind-mounted to /workspace/.solven/skills. Excluded from sync.

Sandbox paths (agent sees /workspace as / via proot):
- /workspace           - Overlay merge (lower + upper); agent root.
- /workspace/.solven/skills - Bind mount of /mnt/user-skills (S3 skills); excluded from workspace sync.
- /mnt/workspace-s3     - S3 threads/{thread_id} (persist target only).
"""
import os
import re
import shlex
import asyncio
import time as _time
from typing import Optional

# #region agent log
_DEBUG_LOG_PATH = "/home/ramon/Github/metaloss/solven-app-vercel/.cursor/debug-b65ae0.log"
def _debug_log(message: str, hypothesis_id: str, data: dict | None = None) -> None:
	try:
		with open(_DEBUG_LOG_PATH, "a") as f:
			f.write(__import__("json").dumps({"sessionId": "b65ae0", "hypothesisId": hypothesis_id, "location": "sandbox_backend", "message": message, "data": data or {}, "timestamp": int(_time.time() * 1000)}) + "\n")
	except Exception:
		pass
# #endregion

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

# Directories excluded from workspace (upper) -> S3 sync. .solven/ (includes skills) is bind-mounted from S3; never synced.
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

# Dirs skipped during in-workspace glob / grep searches.
# bin/sbin/lib/lib64 come from overlay lower (merged-usr); rest are caches or mounts.
_WORKSPACE_SEARCH_SKIP_DIRS = frozenset({
    "usr", "etc", "proc", "dev", "sys", "run", "tmp",
    "bin", "sbin", "lib", "lib64",
    "node_modules", ".venv", "venv", "env", ".bun",
    ".git", ".solven",
})


class SandboxBackend(BaseSandbox):
	"""
	E2B Sandbox backend with OverlayFS and S3. Per-thread upper/work; skills bind-mounted at /workspace/.solven/skills.

	Paths (agent sees /workspace as / via proot -0 -r /workspace):
	- /workspace              - Overlay merge (lower + upper/<tid>); agent root; persisted via upper -> S3.
	- /workspace/.solven/skills - Bind mount of /mnt/user-skills (S3 skills/{user_id}); excluded from sync.
	- /mnt/workspace-s3     - S3 threads/{thread_id} (persist target only).
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

		# Paths
		self._workspace = "/workspace"
		self._workspace_s3_mount = "/mnt/workspace-s3"
		self._user_skills_mount = "/mnt/user-skills"   # S3 skills FUSE mount
		self._workspace_skills_dir = "/workspace/.solven/skills"  # Bind of _user_skills_mount; excluded from sync

		self._proot_available = False
		self._initialized = False

	def _upper_dir(self) -> str:
		return f"/sandbox/upper/{self._thread_id}"

	def _work_dir(self) -> str:
		return f"/sandbox/work/{self._thread_id}"

	def _normalize_path(self, path: str) -> str:
		"""Normalize to a path under /workspace (backend root). / and agent paths become /workspace/..."""
		if not path or path.strip() == "" or path.strip() == "/":
			return self._workspace
		p = path.strip().rstrip("/") or path.strip()
		if p.startswith(self._workspace + "/") or p == self._workspace:
			return p
		return f"{self._workspace}/{p.lstrip('/')}"

	def _to_agent_path(self, real_path: str) -> str:
		"""Convert real path under /workspace to path as seen inside proot (where / = /workspace)."""
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
		"""Ensure sandbox is initialized (idempotent). Uses self._initialized as guard."""
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

				# Check if the S3 workspace mount is still alive.
				try:
					check = self._sandbox.commands.run(
						f"mountpoint -q {self._workspace_s3_mount} && echo OK || echo MISSING",
						timeout=10,
					)
					mount_ok = check.stdout.strip() == "OK"
				except Exception:
					mount_ok = False

				if not mount_ok:
					print(f"[_ensure_initialized] S3 mount missing, remounting...", flush=True)
					self._mount_s3_buckets()
					self._setup_overlay()
					self._mount_user_skills()
					self._sync_local_skills()
				else:
					# Ensure overlay and skills bind are still present
					overlay_ok = False
					skills_ok = False
					try:
						co = self._sandbox.commands.run("mountpoint -q /workspace && echo OK || true", timeout=5)
						overlay_ok = "OK" in (co.stdout or "")
						cs = self._sandbox.commands.run("mountpoint -q /workspace/.solven/skills && echo OK || true", timeout=5)
						skills_ok = "OK" in (cs.stdout or "")
					except Exception:
						pass
					if not overlay_ok:
						print(f"[_ensure_initialized] Overlay missing, re-running _setup_overlay", flush=True)
						self._setup_overlay()
					if not skills_ok:
						print(f"[_ensure_initialized] Skills bind missing, re-running _mount_user_skills", flush=True)
						self._mount_user_skills()

				self._build_workspace_skills()
				self._setup_proot()
				self._initialized = True
				print(f"[_ensure_initialized] ✓ Reused existing sandbox", flush=True)
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

		# Step 4: OverlayFS at /workspace (lower + upper/<tid>)
		self._setup_overlay()

		# Step 5: Optional preload S3 -> upper (background, non-blocking)
		try:
			self._sandbox.commands.run(
				f"rsync -av {_RSYNC_ONE_FS} {_RSYNC_EXCLUDE_FLAGS} "
				f"{self._workspace_s3_mount}/ {self._workspace}/ && chown -R user:user {self._workspace} 2>/dev/null || true",
				timeout=180, user="root", background=True,
			)
		except Exception as e:
			print(f"[_ensure_initialized] ⚠ Preload start failed (non-critical): {e}", flush=True)

		# Step 6: User skills at /mnt/user-skills, bind to /workspace/.solven/skills
		try:
			self._mount_user_skills()
		except Exception as e:
			print(f"[_ensure_initialized] ⚠ User skills mount failed (non-blocking): {e}", flush=True)
		self._sync_local_skills()

		# Step 7: Install Anthropic skills into .solven/skills via npx
		self._build_workspace_skills()

		# Step 8: chown only upper layer (not full /workspace merge) to avoid traversing read-only lower layer (huge /usr tree) and timeout
		upper = self._upper_dir()
		_debug_log("chown_upper_start", "H1", {"upper_dir": upper, "timeout_sec": 60})
		self._sandbox.commands.run(f"chown -R user:user {upper}", timeout=60, user="root")
		_debug_log("chown_upper_done", "H1", {"upper_dir": upper})
		self._setup_proot()

		# Step 9: Mark ready, start background sync (upper -> S3 only)
		self._initialized = True
		self._writer("Espacio de trabajo listo")
		print(f"[_ensure_initialized] ✓ Initialization complete", flush=True)
		self._start_background_syncs()

	def _build_workspace_skills(self) -> None:
		"""Install Anthropic skills into /workspace/.solven/skills via 'npx skills add'.

		Runs from /workspace with --path .solven/skills. --copy for FUSE; -y skips prompts.
		Skills only installed when directory does NOT exist. 'system' dir removed if stale.
		"""
		parent_dir = "/workspace"
		skills_dir = self._workspace_skills_dir  # /workspace/.solven/skills
		skills_to_install = ["docx", "pdf", "xlsx", "pptx"]

		# Remove stale 'system' directory and check which skills are missing — use
		# shell commands (not files.exists) to avoid REST API timeouts on FUSE paths.
		try:
			check = self._sandbox.commands.run(
				f"test -d {skills_dir}/system && rm -rf {skills_dir}/system && echo REMOVED_SYSTEM || true ; "
				+ " ; ".join(f"test -d {skills_dir}/{s} && echo '{s}:ok' || echo '{s}:missing'" for s in skills_to_install),
				timeout=20,
				user="root",
			)
			output = check.stdout
			if "REMOVED_SYSTEM" in output:
				print(f"[_build_workspace_skills] 🗑 Removed stale system dir", flush=True)
			missing = [s for s in skills_to_install if f"{s}:missing" in output]
		except Exception as e:
			print(f"[_build_workspace_skills] ⚠ Could not check skill dirs: {e} — assuming all missing", flush=True)
			missing = list(skills_to_install)

		if not missing:
			print(f"[_build_workspace_skills] ↩ All skills already present, skipping", flush=True)
			return

		skill_flags = " ".join(f"--skill {s}" for s in missing)
		try:
			result = self._sandbox.commands.run(
				f"cd {parent_dir} && npx --yes skills add anthropics/skills {skill_flags} --agent openclaw --copy -y --path .solven/skills",
				timeout=120,
			)
			print(f"[_build_workspace_skills] ✓ Installed: {', '.join(missing)}", flush=True)
			if result.stderr:
				print(f"[_build_workspace_skills] stderr: {result.stderr[:400]}", flush=True)
		except Exception as e:
			print(f"[_build_workspace_skills] ✗ Failed: {e}", flush=True)
			import traceback
			print(traceback.format_exc(), flush=True)
		print(f"[_build_workspace_skills] ✓ Done — {skills_dir}", flush=True)

	def _sync_local_skills(self) -> None:
		"""Copy local escrituras (SKILL.md + required scripts) into /workspace/.solven/skills/escrituras/ (user S3 bind)."""
		local_skills_dir = os.path.join(os.path.dirname(__file__), "skills")
		escrituras_src = os.path.join(local_skills_dir, "escrituras")

		if not os.path.exists(local_skills_dir):
			print(f"[_sync_local_skills] Local skills directory not found: {local_skills_dir}", flush=True)
			return

		# Paths to sync: SKILL.md and base analysis scripts for escrituras (fill scripts go in scripts/fill, not synced)
		escrituras_sync_paths = [
			"SKILL.md",
			"scripts/analyze_template.py",
			"scripts/analyze_docx_placeholders.py",
		]
		escrituras_dir = f"{self._workspace_skills_dir}/escrituras"

		try:
			synced = []
			for rel_path in escrituras_sync_paths:
				src_path = os.path.join(escrituras_src, rel_path)
				if not os.path.exists(src_path):
					continue
				with open(src_path, "r", encoding="utf-8") as f:
					content = f.read()
				tmp_name = f"/tmp/escrituras_{rel_path.replace('/', '_')}"
				self._sandbox.files.write(tmp_name, content)
				self._sandbox.commands.run(
					f"mkdir -p {escrituras_dir}/scripts && cp {tmp_name} {escrituras_dir}/{rel_path}",
					timeout=10, user="root",
				)
				synced.append(rel_path)
			if synced:
				print(f"[_sync_local_skills] ✓ Synced escrituras: {', '.join(synced)}", flush=True)
			else:
				print(f"[_sync_local_skills] No escrituras files found under {escrituras_src}", flush=True)
		except Exception as e:
			print(f"[_sync_local_skills] ✗ Error syncing skills: {e}", flush=True)

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

		# Prepare mount dir and /sandbox for overlay (no /workspace yet; overlay creates it)
		self._sandbox.commands.run(
			f"mkdir -p {self._workspace_s3_mount} /sandbox",
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

		mount_cmd = f'bash /tmp/mount_s3_path.sh "{bucket}" "threads/{self._thread_id}" "{self._workspace_s3_mount}" "/tmp/rclone-thread.log"'
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

	def _mount_user_skills(self) -> None:
		"""Mount S3 skills/{user_id} at /mnt/user-skills, then bind to /workspace/.solven/skills.
		Uses --vfs-write-back 0 (immediate). .solven/ is excluded from workspace sync.
		"""
		bucket = os.getenv("S3_BUCKET_NAME", "solven-testing")
		# Unmount stale mounts
		self._sandbox.commands.run(
			f"mountpoint -q {self._workspace_skills_dir} 2>/dev/null && umount {self._workspace_skills_dir} 2>/dev/null || true; "
			f"mountpoint -q {self._user_skills_mount} 2>/dev/null && umount {self._user_skills_mount} 2>/dev/null || true",
			timeout=15, user="root",
		)
		self._sandbox.commands.run(f"mkdir -p {self._user_skills_mount} {self._workspace_skills_dir}", timeout=60, user="root")
		try:
			result = self._sandbox.commands.run(
				f'bash /tmp/mount_s3_path.sh "{bucket}" "skills/{self._user_id}" "{self._user_skills_mount}" "/tmp/rclone-skills-user.log" immediate',
				timeout=0,
				user="root",
			)
			if result.exit_code != 0:
				raise RuntimeError(f"Failed to mount user skills (exit {result.exit_code})")
			import time
			time.sleep(2)
			verify = self._sandbox.commands.run(
				f"ls {self._user_skills_mount} >/dev/null 2>&1 && echo 'MOUNT_OK' || echo 'MOUNT_FAILED'",
				timeout=10,
			)
			print(f"[Mount] {self._user_skills_mount} check: {verify.stdout.strip()}", flush=True)
			if "MOUNT_OK" not in verify.stdout:
				raise RuntimeError("User skills S3 mount not accessible")
			# Bind into workspace so agent sees /.solven/skills
			self._sandbox.commands.run(
				f"mount --bind {self._user_skills_mount} {self._workspace_skills_dir}",
				timeout=10, user="root",
			)
			self._sandbox.commands.run(
				f"chown -R user:user {self._workspace_skills_dir} 2>/dev/null || true",
				timeout=15, user="root",
			)
			self._sandbox.commands.run(
				f"chmod -R u+rwX {self._workspace_skills_dir} 2>/dev/null || true",
				timeout=15, user="root",
			)
		except Exception as e:
			raise

	def _setup_overlay(self) -> None:
		"""Create OverlayFS: lower (merged-usr), upper/<tid>, work/<tid>; mount merge at /workspace."""
		upper = self._upper_dir()
		work = self._work_dir()
		self._sandbox.commands.run(
			f"mkdir -p /sandbox/lower {upper} {work}",
			timeout=10, user="root",
		)
		self._sandbox.commands.run(
			"ln -sf /usr/bin /sandbox/lower/bin 2>/dev/null || true && "
			"ln -sf /usr/sbin /sandbox/lower/sbin 2>/dev/null || true && "
			"ln -sf /usr/lib /sandbox/lower/lib 2>/dev/null || true && "
			"ln -sf /usr/lib64 /sandbox/lower/lib64 2>/dev/null || true",
			timeout=10, user="root",
		)
		self._sandbox.commands.run(
			"mountpoint -q /workspace && umount /workspace 2>/dev/null || true; mkdir -p /workspace",
			timeout=15, user="root",
		)
		self._sandbox.commands.run(
			f"mount -t overlay overlay "
			f"-o lowerdir=/sandbox/lower,upperdir={upper},workdir={work} /workspace",
			timeout=15, user="root",
		)
		print("[Overlay] ✓ /workspace = lower + upper", flush=True)

	def _preload_upper_from_s3(self) -> None:
		"""Optionally preload upper from S3 (one-time). Non-blocking; run in background to avoid blocking agent startup."""
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
			print(f"[Sync] ✓ S3 -> upper (preload)", flush=True)
		except Exception as e:
			print(f"[Sync] ⚠️  S3->upper preload failed (non-critical): {e}", flush=True)

	def _sync_workspace_to_s3(self) -> None:
		"""Persist upper layer to S3 (excludes .solven/, virtualenvs, node_modules/, etc.). Blocking; prefer _start_background_syncs()."""
		try:
			upper = self._upper_dir()
			result = self._sandbox.commands.run(
				f"rsync -av {_RSYNC_ONE_FS} {_RSYNC_EXCLUDE_FLAGS} {upper}/ {self._workspace_s3_mount}/",
				timeout=300,
				user="root",
			)
			if result.exit_code == 0:
				print(f"[Sync] ✓ upper -> S3", flush=True)
			else:
				print(f"[Sync] ⚠️  persist exit {result.exit_code}", flush=True)
		except Exception as e:
			print(f"[Sync] ⚠️  persist failed (non-critical): {e}", flush=True)

	def _start_background_syncs(self) -> None:
		"""Start upper -> S3 sync in background. No sync in critical path. .solven/ excluded (bind mount)."""
		upper = self._upper_dir()
		workspace_cmd = (
			f"rsync -av {_RSYNC_ONE_FS} {_RSYNC_EXCLUDE_FLAGS} "
			f"{upper}/ {self._workspace_s3_mount}/"
		)
		try:
			self._sandbox.commands.run(workspace_cmd, background=True, user="root")
			print(f"[Sync] started background: upper -> /mnt/workspace-s3", flush=True)
		except Exception as e:
			print(f"[Sync] ⚠️  background sync start failed (non-critical): {e}", flush=True)

	async def load_skills_frontmatter(self, category: Optional[str] = None) -> str:
		"""Load SKILL.md frontmatter for prompt injection. Tries sandbox first, falls back to local src/skills/."""
		# Try sandbox (requires init)
		try:
			self._ensure_initialized()
			skills_dir = self._workspace_skills_dir
			result = self._sandbox.commands.run(
				f"ls -1 {skills_dir} 2>/dev/null || true",
				timeout=15,
				user="root",
			)
			dirs = [d.strip() for d in (result.stdout or "").splitlines() if d.strip() and not d.startswith(".")]
			frontmatter_blocks: list[str] = []
			for skill_name in dirs:
				skill_md = f"{skills_dir}/{skill_name}/SKILL.md"
				try:
					read_result = self._sandbox.commands.run(
						f"cat {shlex.quote(skill_md)} 2>/dev/null || true",
						timeout=10,
						user="root",
					)
					content = (read_result.stdout or "").strip()
					if content:
						fm = _parse_skillmd_frontmatter(content)
						if fm:
							frontmatter_blocks.append(f"---\n{fm}\n---")
				except Exception:
					continue
			if frontmatter_blocks:
				return "\n".join(frontmatter_blocks)
		except Exception as e:
			print(f"[load_skills_frontmatter] sandbox: {e}", flush=True)
		# Fallback: read from local src/skills/ (docx, escrituras, etc.)
		local_skills = os.path.join(os.path.dirname(__file__), "skills")
		if os.path.isdir(local_skills):
			blocks: list[str] = []
			for name in sorted(os.listdir(local_skills)):
				if name.startswith("."):
					continue
				skill_md = os.path.join(local_skills, name, "SKILL.md")
				if os.path.isfile(skill_md):
					try:
						with open(skill_md, "r", encoding="utf-8") as f:
							content = f.read()
						fm = _parse_skillmd_frontmatter(content)
						if fm:
							blocks.append(f"---\n{fm}\n---")
					except Exception:
						pass
			if blocks:
				print(f"[load_skills_frontmatter] using local fallback ({len(blocks)} skills)", flush=True)
				return "\n".join(blocks)
		return ""

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

	def _setup_proot(self) -> None:
		"""Verify proot works with -r /workspace (agent sees /workspace as /), then mark ready."""
		check = self._build_proot_command("pwd && ls / 2>/dev/null | head -8")
		try:
			result = self._sandbox.commands.run(check, timeout=30)
			out = (result.stdout or "").strip()
			first_line = out.split("\n")[0].strip() if out else ""
			if result.exit_code != 0 or first_line != "/":
				print(f"[proot] verification output: {out!r}", flush=True)
				raise RuntimeError("proot verification failed: pwd should be / inside proot")
		except Exception as e:
			print(f"[proot] ✗ {e}", flush=True)
			raise
		self._proot_available = True
		print("[proot] ready (agent sees /workspace as /)", flush=True)

	def _build_proot_command(self, bash_command: str) -> str:
		"""Wrap command in proot with /workspace as root; bind system dirs and set workspace env."""
		ws = self._workspace
		inner = (
			"HOME=/ TMPDIR=/tmp "
			"UV_PROJECT_ENVIRONMENT=/.venv VIRTUAL_ENV=/.venv BUN_INSTALL=/.bun "
			"PATH=/.venv/bin:/.local/bin:/.bun/bin:/usr/local/bin:/usr/bin:/bin "
			f"{bash_command}"
		)
		binds = (
			"-b /proc:/proc -b /dev:/dev -b /sys:/sys "
			"-b /usr:/usr "
			"-b /etc:/etc "
		)
		return f"proot -0 -r {ws} {binds} -w / /bin/bash -c {shlex.quote(inner)}"

	def _filter_unwanted_commands(self, command: str) -> Optional[str]:
		"""Block install commands so deps use uv (Python) and bun (Node). Allow pip/npm/npx for non-install (e.g. pip list, npm run)."""
		unwanted = {
			r"\bnpm\s+(install|i)\b": "Not allowed: Use bun for Node (e.g. bun install, bun add <pkg>).",
			r"\bnpx\s+(install|add)\b": "Not allowed: Use bun for Node (e.g. bun add <pkg>, bun x <pkg>).",
			r"\bnode\b": "Not allowed: Use bun for Node (e.g. bun <script.js> or bun run <script>).",
			r"\bpip\s+install\b": "Not allowed: Use uv for Python (e.g. uv pip install ..., uv add ..., uv run ...).",
			r"\bsudo\b": "Not allowed: sudo is not allowed in sandbox environment.",
			r"\bapt-get\s+(install|update)\b": "Not allowed: apt-get is not allowed (system packages pre-installed).",
			r"\bapt\s+(install|update)\b": "Not allowed: apt is not allowed (system packages pre-installed).",
		}
		for pattern, message in unwanted.items():
			if re.search(pattern, command, re.IGNORECASE):
				return message
		return None

	def execute(self, command: str) -> ExecuteResponse:
		"""Execute a shell command in the sandbox. Always runs inside proot so the agent sees /workspace as /."""
		self._ensure_initialized()
		if not self._proot_available:
			return ExecuteResponse(
				output="Error: proot not ready (sandbox init did not complete).",
				exit_code=1,
				truncated=False,
			)

		if error_msg := self._filter_unwanted_commands(command):
			return ExecuteResponse(output=error_msg, exit_code=1, truncated=False)

		run_command = self._build_proot_command(command)
		try:
			result = self._sandbox.commands.run(run_command, timeout=1200)
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
			output=result.stdout + result.stderr,
			exit_code=result.exit_code,
			truncated=False,
		)

	async def aexecute(self, command: str) -> ExecuteResponse:
		"""Async version of execute."""
		return await asyncio.to_thread(self.execute, command)

	def glob_info(self, pattern: str, path: str = "/") -> list["FileInfo"]:
		"""List files matching pattern via find -iname (case-insensitive) so e.g. **/acta* matches ACTA JUNTA UNIVERSAL."""
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
		"""Upload files to the sandbox. All paths are normalized to /workspace as root. Uses E2B files API."""
		self._ensure_initialized()
		responses = []
		for path, content in files:
			real_path = self._normalize_path(path)
			try:
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
		"""Download files from the sandbox. All paths are normalized to /workspace as root. Uses E2B files API."""
		self._ensure_initialized()
		responses = []
		for path in paths:
			real_path = self._normalize_path(path)
			try:
				if not self._sandbox.files.exists(real_path):
					responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
					continue
				download_url = self._sandbox.download_url(real_path)
				import requests
				response = requests.get(download_url, timeout=30)
				response.raise_for_status()
				responses.append(FileDownloadResponse(path=path, content=response.content, error=None))
			except Exception as e:
				responses.append(FileDownloadResponse(path=path, content=None, error=f"download_error: {str(e)}"))
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
