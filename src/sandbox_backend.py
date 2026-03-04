"""
E2B Sandbox backend for DeepAgents using S3.
Implements the BackendProtocol for filesystem operations in an isolated sandbox environment.

ARCHITECTURE OVERVIEW (simple /workspace):
=========================================
- Agent CWD and HOME are /workspace. All tools and commands run there; uv/bun manage packages there.
- S3 thread state is at /mnt/workspace-s3 (rclone FUSE). Restore: rsync S3 -> /workspace (excludes below).
- Persist: after each execute(), rsync /workspace -> S3 (excludes below).
- Skills:
    /.solven/skills  — unified skills directory (S3 FUSE mount).
                       Contains user skills, local escrituras seed, and Anthropic
                       docx/pdf/xlsx/pptx skills installed via `npx skills add`.

Sandbox paths (agent sees /workspace as / via proot):
- /workspace              - Agent root; all project work lives here; persisted to S3.
- /workspace/.solven/skills - Unified skills FUSE mount; excluded from workspace rsync.
- /mnt/workspace-s3       - S3 threads/{thread_id} (rsync target only).
"""
import os
import re
import shlex
import asyncio
from typing import Optional

from e2b import Sandbox, SandboxQuery, SandboxState

from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import WriteResult, EditResult, ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.utils import FileInfo, GrepMatch
from langchain.tools import ToolRuntime
from langgraph.config import get_stream_writer, get_config
from langgraph.graph.state import RunnableConfig
from src.models import AppContext

SANDBOX_TEMPLATE = "solven-sandbox-v1"

# Directories/symlinks excluded from all rsync workspace <-> S3 transfers.
# - .solven/      : unified skills FUSE mount — S3 persistence is handled by the FUSE
#                   mount itself (skills/{user_id} bucket), NOT by rsync. Excluding it
#                   here avoids double-writing the same files into the workspace bucket.
# - .venv/ venv/ env/ : Python virtualenvs (agent recreates on demand)
# - node_modules/ : npm packages (agent recreates on demand)
# - .bun/         : Bun cache (contains symlinks that break S3 FUSE writes)
# - bin sbin lib lib64 : merged-usr symlinks created by _setup_proot() at runtime
_RSYNC_EXCLUDES = (
    ".solven/",
    ".venv/",
    "venv/",
    "env/",
    "npm/",
    "node_modules/",
    ".bun/",
    "bin",
    "sbin",
    "lib",
    "lib64",
)
_RSYNC_EXCLUDE_FLAGS = " ".join(f"--exclude='{p}'" for p in _RSYNC_EXCLUDES)

# Dirs skipped during in-workspace glob / grep searches.
# Merged-usr symlinks (bin/sbin/lib/lib64) would traverse the entire OS if followed;
# the rest are heavy package caches or FUSE/git mounts with no user-authored content.
_WORKSPACE_SEARCH_SKIP_DIRS = frozenset({
    # proot bind-mounted system dirs (visible inside proot but not agent workspace content)
    "usr", "etc", "proc", "dev", "sys", "run", "tmp",
    # merged-usr symlinks at /workspace root pointing into /usr
    "bin", "sbin", "lib", "lib64",
    # package caches (excluded from S3 sync too, agent recreates on demand)
    "node_modules", ".venv", "venv", "env", ".bun",
    # .git dirs (avoid traversing repo metadata when searching .solven)
    ".git",
})


class SandboxBackend(BaseSandbox):
	"""
	E2B Sandbox backend with S3 mounts and simple /workspace sync.

	Paths (agent sees /workspace as / via proot):
	- /workspace              - Agent root; all project work lives here; persisted to S3.
	- /workspace/.solven/skills - Unified skills dir (S3 FUSE mount + npx-installed Anthropic skills); agent reads as /.solven/skills/.
	- /mnt/workspace-s3       - S3 threads/{thread_id} (rsync target only).
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

		# Mount paths
		self._workspace = "/workspace"                       # Agent CWD and HOME; all work here
		self._workspace_s3_mount = "/mnt/workspace-s3"       # S3 mount: threads/{thread_id} (sync only)
		self._workspace_skills_dir = "/workspace/.solven/skills"  # S3 skills/{user_id}; excluded from sync

		self._proot_available = False
		self._initialized = False

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
					self._mount_user_skills()
					self._sync_local_skills()

				# Always install Anthropic skills into .solven/skills.
				self._build_workspace_skills()
				self._setup_proot()
				self._initialized = True
				self._writer("Espacio de trabajo listo")
				print(f"[_ensure_initialized] ✓ Reused existing sandbox", flush=True)
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

		# Step 3: S3 workspace mount (restores prior session files).
		self._mount_s3_buckets()

		# Step 4: User skills FUSE mount + local escrituras seed (non-blocking failures).
		try:
			self._mount_user_skills()
		except Exception as e:
			print(f"[_ensure_initialized] ⚠ User skills mount failed (non-blocking): {e}", flush=True)
		self._sync_local_skills()

		# Step 5: Install Anthropic skills into .solven/skills via npx.
		self._build_workspace_skills()

		# Step 6: proot isolation (merged-usr symlinks in /workspace).
		self._setup_proot()

		self._initialized = True
		self._writer("Espacio de trabajo listo")
		print(f"[_ensure_initialized] ✓ Initialization complete", flush=True)

	def _build_workspace_skills(self) -> None:
		"""Install Anthropic skills into /workspace/.solven/skills via 'npx skills add'.

		Runs from /workspace/.solven with --agent openclaw because OpenClaw's project
		path is 'skills/' (no dot prefix), so skills land directly in .solven/skills/.
		--copy is required because FUSE mounts don't support symlinks.
		-y skips all interactive prompts.

		Skills are only installed when their directory does NOT already exist so that
		user modifications persisted via the S3 FUSE mount are never overwritten.
		The 'system' directory is removed if present (stale from a previous run).
		"""
		parent_dir = "/workspace/.solven"
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
				f"cd {parent_dir} && npx --yes skills add anthropics/skills {skill_flags} --agent openclaw --copy -y",
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
		"""Copy local escrituras/SKILL.md into /.solven/skills/escrituras/ (user S3 mount)."""
		local_skills_dir = os.path.join(os.path.dirname(__file__), "skills")

		if not os.path.exists(local_skills_dir):
			print(f"[_sync_local_skills] Local skills directory not found: {local_skills_dir}", flush=True)
			return

		try:
			escrituras_skill_path = os.path.join(local_skills_dir, "escrituras", "SKILL.md")
			if os.path.exists(escrituras_skill_path):
				with open(escrituras_skill_path, "r", encoding="utf-8") as f:
					skill_content = f.read()
				escrituras_dir = f"{self._workspace_skills_dir}/escrituras"
				# Write to /tmp first (not FUSE), then cp as root into the skills FUSE mount
				tmp_skill = "/tmp/skill_escrituras.md"
				self._sandbox.files.write(tmp_skill, skill_content)
				self._sandbox.commands.run(
					f"mkdir -p {escrituras_dir} && cp {tmp_skill} {escrituras_dir}/SKILL.md",
					timeout=10, user="root",
				)
				print(f"[_sync_local_skills] ✓ Synced escrituras/SKILL.md to {escrituras_dir}/", flush=True)
			else:
				print(f"[_sync_local_skills] escrituras/SKILL.md not found at {escrituras_skill_path}", flush=True)
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

		# Prepare mount dir and workspace
		self._sandbox.commands.run(
			f"mkdir -p {self._workspace_s3_mount} {self._workspace}",
			timeout=30, user="root",
		)
		self._sandbox.commands.run(f"chown -R user:user {self._workspace}", timeout=10, user="root")

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
					self._restore_files_from_s3()
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
			self._restore_files_from_s3()
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
		"""Mount S3 skills/{user_id} at /workspace/.solven/skills (unified skills dir)."""
		bucket = os.getenv("S3_BUCKET_NAME", "solven-testing")
		solven_dir = "/workspace/.solven"
		self._sandbox.commands.run(f"mkdir -p {self._workspace_skills_dir}", timeout=60, user="root")
		try:
			result = self._sandbox.commands.run(
				f'bash /tmp/mount_s3_path.sh "{bucket}" "skills/{self._user_id}" "{self._workspace_skills_dir}" "/tmp/rclone-skills-user.log"',
				timeout=0,  # Disable E2B timeout for long-running rclone mount
				user="root",
			)
			if result.exit_code != 0:
				raise RuntimeError(f"Failed to mount user skills (exit {result.exit_code})")
			import time
			time.sleep(2)
			verify = self._sandbox.commands.run(
				f"ls {self._workspace_skills_dir} >/dev/null 2>&1 && echo 'MOUNT_OK' || echo 'MOUNT_FAILED'",
				timeout=10,
			)
			print(f"[Mount] {self._workspace_skills_dir} mount check: {verify.stdout.strip()}", flush=True)
			# Ensure .solven is writable by the agent (runs as user in proot)
			self._sandbox.commands.run(
				f"chown -R user:user {solven_dir} 2>/dev/null || true",
				timeout=15,
				user="root",
			)
			self._sandbox.commands.run(
				f"chmod -R u+rwX {solven_dir} 2>/dev/null || true",
				timeout=15,
				user="root",
			)
		except Exception as e:
			raise

	def _restore_files_from_s3(self) -> None:
		"""Restore /workspace from S3 mount. Excludes .solven/, .venv/, venv/, env/, node_modules/, .bun/, and merged-usr symlinks."""
		try:
			mount = self._workspace_s3_mount
			self._sandbox.commands.run(
				f"mkdir -p {self._workspace} && rsync -av {_RSYNC_EXCLUDE_FLAGS} {mount}/ {self._workspace}/",
				timeout=180,
				user="root",
			)
			self._sandbox.commands.run(
				f"chown -R user:user {self._workspace} 2>/dev/null || true",
				timeout=30, user="root",
			)
			print(f"[Sync] ✓ S3 -> /workspace (restore)", flush=True)
		except Exception as e:
			print(f"[Sync] ⚠️  S3->workspace restore failed (non-critical): {e}", flush=True)

	def _sync_workspace_to_s3(self) -> None:
		"""Persist /workspace to S3. Excludes .solven/ (FUSE), virtualenvs, node_modules/, .bun/ (cache symlinks), and merged-usr symlinks."""
		try:
			result = self._sandbox.commands.run(
				f"rsync -av {_RSYNC_EXCLUDE_FLAGS} {self._workspace}/ {self._workspace_s3_mount}/",
				timeout=300,
				user="root",
			)
			if result.exit_code == 0:
				print(f"[Sync] ✓ /workspace -> S3", flush=True)
			else:
				print(f"[Sync] ⚠️  persist exit {result.exit_code}", flush=True)
		except Exception as e:
			print(f"[Sync] ⚠️  persist failed (non-critical): {e}", flush=True)

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
		"""Create merged-usr symlinks in /workspace (Ubuntu 22+ merged-usr layout) so proot guest /bin etc. resolve via /usr.
		proot is pre-installed in the sandbox template — no availability check needed.
		"""
		self._sandbox.commands.run(
			"ln -sf usr/bin /workspace/bin 2>/dev/null || true && "
			"ln -sf usr/sbin /workspace/sbin 2>/dev/null || true && "
			"ln -sf usr/lib /workspace/lib 2>/dev/null || true && "
			"ln -sf usr/lib64 /workspace/lib64 2>/dev/null || true",
			timeout=10, user="root",
		)
		self._proot_available = True
		print("[proot] ready", flush=True)

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
		return f"proot -r {ws} {binds} -w / /bin/bash -c {shlex.quote(inner)}"

	def _filter_unwanted_commands(self, command: str) -> Optional[str]:
		"""Block commands the agent must not run."""
		unwanted = {
			r"\bsudo\b": "Error: sudo is not allowed in sandbox environment",
			r"\bapt-get\b": "Error: apt-get is not allowed (system packages pre-installed)",
			r"\bapt\b": "Error: apt is not allowed (system packages pre-installed)",
		}
		for pattern, message in unwanted.items():
			if re.search(pattern, command, re.IGNORECASE):
				return message
		return None

	def execute(self, command: str) -> ExecuteResponse:
		"""Execute a shell command in the sandbox. When proot is available, runs inside proot with /workspace as /; otherwise cd into /workspace."""
		self._ensure_initialized()

		if error_msg := self._filter_unwanted_commands(command):
			return ExecuteResponse(output=error_msg, exit_code=1, truncated=False)

		if self._proot_available:
			run_command = self._build_proot_command(command)
		else:
			ws = self._workspace
			run_command = (
				f"cd {ws} && HOME={ws} UV_PROJECT_ENVIRONMENT={ws}/.venv "
				f"VIRTUAL_ENV={ws}/.venv BUN_INSTALL={ws}/.bun "
				f"PATH={ws}/.venv/bin:{ws}/.local/bin:{ws}/.bun/bin:/usr/local/bin:/usr/bin:/bin "
				f"{command}"
			)
		try:
			result = self._sandbox.commands.run(run_command, timeout=1200)
		except Exception as e:
			return ExecuteResponse(
				output=f"Error executing command: {str(e)}",
				exit_code=1,
				truncated=False,
			)

		try:
			self._sync_workspace_to_s3()
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
		"""Upload multiple files to the sandbox. Agent paths (e.g. / or /foo) are mapped to /workspace."""
		self._ensure_initialized()
		responses = []
		for path, content in files:
			real_path = path if path.startswith("/workspace") else f"/workspace/{path.lstrip('/')}"
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
			self._sync_workspace_to_s3()
		except Exception:
			pass
		return responses

	def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
		"""Download multiple files from the sandbox. Agent paths (e.g. / or /foo) are mapped to /workspace."""
		self._ensure_initialized()
		responses = []
		for path in paths:
			real_path = path if path.startswith("/workspace") else f"/workspace/{path.lstrip('/')}"
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

	async def als_info(self, path: str = "/workspace") -> list[FileInfo]:
		return await asyncio.to_thread(self.ls_info, path)

	async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
		return await asyncio.to_thread(self.read, file_path, offset, limit)

	async def awrite(self, file_path: str, content: str) -> WriteResult:
		return await asyncio.to_thread(self.write, file_path, content)

	async def agrep_raw(self, pattern: str, path: str | None = "/workspace", glob: str | None = None) -> list[GrepMatch] | str:
		return await asyncio.to_thread(self.grep_raw, pattern, path, glob)

	async def aglob_info(self, pattern: str, path: str = "/workspace") -> list[FileInfo]:
		return await asyncio.to_thread(self.glob_info, pattern, path)
