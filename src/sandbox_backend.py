"""
E2B Sandbox backend for DeepAgents using S3.
Implements the BackendProtocol for filesystem operations in an isolated sandbox environment.

ARCHITECTURE OVERVIEW:
======================
Sandbox paths:
- /home/user              - LOCAL workspace (agent works here; symlink support for npm). Synced to/from S3 (excluding .solven).
- /home/user/.solven/skills - S3 skills/{user_id} mounted here; Anthropic docx/xlsx/pptx/pdf copied in. Excluded from workspace sync.
- /mnt/workspace-s3       - S3 mount threads/{thread_id} (rclone). Used only for sync.
- /ticket                 - S3 mount: threads/{ticket_id} (optional, read-only)

rclone mounts do NOT support symlinks (EIO). We use local /home/user and sync to/from S3; .solven is excluded.
"""
import json
import os
import re
import shlex
import asyncio
from typing import Optional
from datetime import datetime

from e2b import Sandbox, CommandResult, SandboxQuery, SandboxState

from deepagents.backends.sandbox import BaseSandbox
from deepagents.backends.protocol import WriteResult, EditResult, ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.utils import FileInfo, GrepMatch
from langchain.tools import ToolRuntime
from langgraph.config import get_stream_writer, get_config
from langgraph.graph.state import RunnableConfig
from src.models import AppContext

SANDBOX_TEMPLATE = "solven-sandbox-v1"


class SandboxBackend(BaseSandbox):
	"""
	E2B Sandbox backend with S3 mounts and local workspace.

	Paths:
	- /home/user              - LOCAL workspace (agent works here). Synced to/from S3; .solven excluded.
	- /home/user/.solven/skills - S3 skills/{user_id} mount + Anthropic docx/xlsx/pptx/pdf (escrituras, docx, pptx, pdf, xlsx). Use for glob/ls.
	- /mnt/workspace-s3       - S3 mount threads/{thread_id} (sync only)
	- /ticket                 - S3 mount threads/{ticket_id} (optional, read-only)
	"""

	def __init__(self, runtime: ToolRuntime[AppContext]):
		self._sandbox: Optional[Sandbox] = None
		self._writer = get_stream_writer()

		from src.utils.config import get_user_id_from_config, get_thread_id_from_config

		thread_id = get_thread_id_from_config()
		if not thread_id:
			raise RuntimeError("Cannot initialize SandboxBackend: thread_id not found in config")
		self._thread_id = thread_id

		user_id = get_user_id_from_config()
		if not user_id:
			raise RuntimeError("Cannot initialize SandboxBackend: user_id not found in config")
		self._user_id = user_id

		config: RunnableConfig = get_config()
		metadata = config.get("metadata", {})
		self._ticket_id = metadata.get("ticket_id")

		# Mount paths
		self._workspace = "/home/user"           # LOCAL workspace (agent works here; scripts run here)
		self._workspace_s3_mount = "/mnt/workspace-s3"  # S3 mount: threads/{thread_id} (sync only)
		self._workspace_skills_dir = "/home/user/.solven/skills"  # S3 skills/{user_id} mounted here; anthropic skills copied in
		self._ticket_mount = "/ticket"           # S3: threads/{ticket_id} (optional)

		self._initialized = False

	ANTHROPIC_SKILLS_SUBDIRS = ("docx", "xlsx", "pptx", "pdf")
	TMP_ANTHROPIC_CLONE = "/tmp/anthropic-skills"

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
			for idx, sb in enumerate(existing_sandboxes):
				print(f"[_ensure_initialized]   [{idx}] id={sb.sandbox_id} metadata={getattr(sb, 'metadata', {})}", flush=True)
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

				# Verify workspace S3 mount is accessible (agent works in /home/user)
				try:
					check = self._sandbox.commands.run(
						f"mountpoint -q {self._workspace_s3_mount} && echo 'MOUNT_OK' || echo 'MOUNT_MISSING'",
						timeout=10,
					)
					mount_status = check.stdout.strip()
				except Exception as check_err:
					print(f"[_ensure_initialized] Mount check error: {check_err}", flush=True)
					mount_status = "MOUNT_MISSING"

				if "MOUNT_MISSING" in mount_status:
					print(f"[_ensure_initialized] Mounts missing, remounting...", flush=True)
					self._mount_s3_buckets()
					self._mount_user_skills()
					self._sync_local_skills()
					self._build_workspace_skills()

				self._initialized = True
				self._writer("Espacio de trabajo listo")
				print(f"[_ensure_initialized] ✓ Reused existing sandbox", flush=True)
				return
			except Exception as e:
				print(f"[_ensure_initialized] ✗ Failed to use sandbox {sandbox_id}: {e}", flush=True)
				self._sandbox = None  # reset so guard doesn't fire on next attempt

		# Step 2: No usable existing sandbox — create a new one
		print(f"[_ensure_initialized] Creating new sandbox...", flush=True)
		env_vars = {
			"THREAD_ID": self._thread_id,
			"USER_ID": str(self._user_id),
		}
		if bucket := os.getenv("S3_BUCKET_NAME"):
			env_vars["S3_BUCKET_NAME"] = bucket
		if access_key := os.getenv("S3_ACCESS_KEY_ID"):
			env_vars["S3_ACCESS_KEY_ID"] = access_key
		if secret := os.getenv("S3_ACCESS_SECRET"):
			env_vars["S3_ACCESS_SECRET"] = secret
		if endpoint := os.getenv("S3_ENDPOINT_URL"):
			env_vars["S3_ENDPOINT_URL"] = endpoint
		if region := os.getenv("S3_REGION"):
			env_vars["S3_REGION"] = region

		self._sandbox = Sandbox.create(
			template=SANDBOX_TEMPLATE,
			envs=env_vars,
			timeout=3600,
			metadata={
				"threadId": self._thread_id,
				"userId": str(self._user_id),
				"ticketId": str(self._ticket_id) if self._ticket_id else "",
			},
		)
		print(f"[_ensure_initialized] ✓ Created new sandbox: {self._sandbox.sandbox_id}", flush=True)

		self._writer("Preparando espacio de trabajo...")
		# Step 3: Mount workspace-s3 + optional ticket
		self._mount_s3_buckets()

		# Step 4: Mount user skills at /home/user/.solven/skills; seed escrituras and copy Anthropic skills into the mount
		self._mount_user_skills()
		self._sync_local_skills()
		self._build_workspace_skills()

		self._initialized = True
		self._writer("Espacio de trabajo listo")
		print(f"[_ensure_initialized] ✓ Initialization complete", flush=True)

	ANTHROPIC_SKILLS_SUBDIRS = ("docx", "xlsx", "pptx", "pdf")
	TMP_ANTHROPIC_CLONE = "/tmp/anthropic-skills"

	def _build_workspace_skills(self) -> None:
		"""
		Copy Anthropic skills (docx, xlsx, pptx, pdf) from repo in /tmp into /home/user/.solven/skills (the user S3 mount).
		Runs after _mount_user_skills. End result: .solven/skills has escrituras (from S3) + docx, pptx, pdf, xlsx.
		"""
		try:
			solven_skills = self._workspace_skills_dir
			# Clone anthropics/skills to temp, copy only docx, xlsx, pptx, pdf into the mount
			repo_url = "https://github.com/anthropics/skills.git"
			clone_dir = self.TMP_ANTHROPIC_CLONE
			self._sandbox.commands.run(f"mkdir -p {clone_dir}", timeout=10, user="root")
			self._sandbox.commands.run(f"git config --global --add safe.directory {clone_dir}", timeout=10)

			repo_check = self._sandbox.commands.run(
				f"test -d {clone_dir}/.git && echo EXISTS || echo NOT_FOUND",
				timeout=10,
			)
			import time as _time
			max_retries = 2
			for attempt in range(max_retries + 1):
				try:
					if "EXISTS" in repo_check.stdout:
						self._sandbox.commands.run(
							f"cd {clone_dir} && git pull origin main",
							timeout=90,
						)
						break
					else:
						clone_result = self._sandbox.git.clone(
							repo_url,
							path=clone_dir,
							depth=1,
							user="root",
						)
						if clone_result.exit_code != 0:
							raise Exception(clone_result.stderr or "Clone failed")
						break
				except Exception as repo_err:
					if attempt >= max_retries:
						raise
					_time.sleep(2)

			for subdir in self.ANTHROPIC_SKILLS_SUBDIRS:
				src = f"{clone_dir}/skills/{subdir}"
				self._sandbox.commands.run(
					f"test -d {src} && cp -a {src} {solven_skills}/ 2>/dev/null || true",
					timeout=30,
					user="root",
				)

			self._sandbox.commands.run(f"chown -R user:user {solven_skills}", timeout=15, user="root")
			print(f"[_build_workspace_skills] ✓ {solven_skills} ready", flush=True)
		except Exception as e:
			print(f"[_build_workspace_skills] ✗ Error: {e}", flush=True)
			import traceback
			print(traceback.format_exc(), flush=True)

	def _sync_local_skills(self) -> None:
		"""Copy local escrituras/SKILL.md into /home/user/.solven/skills/escrituras/ (user S3 mount)."""
		local_skills_dir = os.path.join(os.path.dirname(__file__), "skills")

		if not os.path.exists(local_skills_dir):
			print(f"[_sync_local_skills] Local skills directory not found: {local_skills_dir}", flush=True)
			return

		try:
			escrituras_skill_path = os.path.join(local_skills_dir, "escrituras", "SKILL.md")
			if os.path.exists(escrituras_skill_path):
				with open(escrituras_skill_path, "r", encoding="utf-8") as f:
					skill_content = f.read()
				# .solven/skills is the user S3 mount; copy SKILL.md into escrituras there
				escrituras_dir = f"{self._workspace_skills_dir}/escrituras"
				self._sandbox.commands.run(f"mkdir -p {escrituras_dir}", timeout=10)
				self._sandbox.files.write(f"{escrituras_dir}/SKILL.md", skill_content)
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

		# Mount thread workspace to /mnt/workspace-s3 (rclone does not support symlinks; agent uses local /home/user)
		self._sandbox.commands.run(f"mkdir -p {self._workspace_s3_mount} {self._workspace}", timeout=30, user="root")
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
					self._sync_workspace_from_s3()
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
						timeout=10,
						user="root",
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
			self._sync_workspace_from_s3()
		except RuntimeError:
			raise
		except Exception as e:
			try:
				log = self._sandbox.commands.run(
					"tail -100 /tmp/rclone-thread.log 2>&1 || echo 'No log file'",
					timeout=10,
					user="root",
				)
				log_output = log.stdout if log else "No log available"
			except Exception:
				log_output = "Could not read log file"
			is_timeout = isinstance(e, TimeoutError) or "timeout" in str(e).lower()
			raise RuntimeError(
				f"Thread mount {'timed out' if is_timeout else 'failed'}: {e}\nLog:\n{log_output}"
			)

		# Mount ticket (optional, read-only)
		if self._ticket_id:
			try:
				self._sandbox.commands.run(f"mkdir -p {self._ticket_mount}", timeout=30, user="root")
				result = self._sandbox.commands.run(
					f'bash /tmp/mount_s3_path.sh "{bucket}" "threads/{self._ticket_id}" "{self._ticket_mount}" "/tmp/rclone-ticket.log" "read-only"',
					timeout=500,
					user="root",
				)
				if result.exit_code != 0:
					raise RuntimeError(f"Failed to mount ticket (exit {result.exit_code})")
				import time
				time.sleep(2)
				verify = self._sandbox.commands.run(
					f"ls {self._ticket_mount} >/dev/null 2>&1 && echo 'MOUNT_OK' || echo 'MOUNT_FAILED'",
					timeout=10,
				)
				print(f"[Mount] {self._ticket_mount} mount check: {verify.stdout.strip()}", flush=True)
			except Exception as e:
				print(f"[Mount] ⚠️  Ticket mount failed (non-critical): {e}", flush=True)

	def _mount_user_skills(self) -> None:
		"""Mount S3 skills/{user_id} at /home/user/.solven/skills. Anthropic skills are then copied in by _build_workspace_skills."""
		bucket = os.getenv("S3_BUCKET_NAME", "solven-testing")
		# Create parent dir so we can mount at .solven/skills
		self._sandbox.commands.run(f"mkdir -p {self._workspace_skills_dir}", timeout=30, user="root")
		try:
			result = self._sandbox.commands.run(
				f'bash /tmp/mount_s3_path.sh "{bucket}" "skills/{self._user_id}" "{self._workspace_skills_dir}" "/tmp/rclone-skills-user.log"',
				timeout=500,
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
		except Exception as e:
			raise

	def _sync_workspace_from_s3(self) -> None:
		"""Sync S3 mount contents to local /home/user (supports symlinks for npm). Mirrors S3 state."""
		try:
			result = self._sandbox.commands.run(
				f"rsync -a --delete {self._workspace_s3_mount}/ {self._workspace}/ 2>/dev/null || true",
				timeout=120,
				user="root",
			)
			self._sandbox.commands.run(f"chown -R user:user {self._workspace}", timeout=10, user="root")
			print(f"[Sync] ✓ S3 -> {self._workspace}", flush=True)
		except Exception as e:
			print(f"[Sync] ⚠️  S3->workspace failed (non-critical): {e}", flush=True)

	def _sync_workspace_to_s3(self) -> None:
		"""Sync local /home/user to S3 mount. Excludes node_modules, .venv, .solven."""
		try:
			result = self._sandbox.commands.run(
				f"rsync -a --delete --exclude='node_modules' --exclude='.venv' --exclude='__pycache__' --exclude='.solven' {self._workspace}/ {self._workspace_s3_mount}/",
				timeout=180,
				user="root",
			)
			if result.exit_code == 0:
				print(f"[Sync] ✓ {self._workspace} -> S3", flush=True)
			else:
				print(f"[Sync] ⚠️  workspace->S3 exit {result.exit_code}", flush=True)
		except Exception as e:
			print(f"[Sync] ⚠️  workspace->S3 failed (non-critical): {e}", flush=True)

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
		"""Execute a shell command in the sandbox; workspace is /home/user."""
		self._ensure_initialized()

		if error_msg := self._filter_unwanted_commands(command):
			return ExecuteResponse(output=error_msg, exit_code=1, truncated=False)

		run_command = f"cd {self._workspace} && {command}"
		try:
			result = self._sandbox.commands.run(run_command, timeout=1200)
		except Exception as e:
			return ExecuteResponse(
				output=f"Error executing command: {str(e)}",
				exit_code=1,
				truncated=False,
			)

		try:
			self._sandbox.commands.run("sync", timeout=500)
		except Exception:
			pass

		# Persist workspace to S3 (local /home/user supports npm symlinks; rclone mount does not)
		try:
			self._sync_workspace_to_s3()
		except Exception:
			pass
		# User skills are mounted at .solven/skills (S3) so edits persist automatically; no sync needed

		return ExecuteResponse(
			output=result.stdout + result.stderr,
			exit_code=result.exit_code,
			truncated=False,
		)

	async def aexecute(self, command: str) -> ExecuteResponse:
		"""Async version of execute."""
		return await asyncio.to_thread(self.execute, command)

	def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
		"""Upload multiple files to the sandbox."""
		self._ensure_initialized()
		responses = []
		for path, content in files:
			try:
				if isinstance(content, bytes):
					try:
						self._sandbox.files.write(path, content.decode("utf-8"))
					except UnicodeDecodeError:
						self._sandbox.files.write(path, content)
				else:
					self._sandbox.files.write(path, str(content))
				responses.append(FileUploadResponse(path=path, error=None))
			except Exception as e:
				responses.append(FileUploadResponse(path=path, error="permission_denied"))
		return responses

	def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
		"""Download multiple files from the sandbox."""
		self._ensure_initialized()
		responses = []
		for path in paths:
			try:
				if not self._sandbox.files.exists(path):
					responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
					continue

				download_url = self._sandbox.download_url(path)
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

	async def als_info(self, path: str = "/home/user") -> list[FileInfo]:
		return await asyncio.to_thread(self.ls_info, path)

	async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
		return await asyncio.to_thread(self.read, file_path, offset, limit)

	async def awrite(self, file_path: str, content: str) -> WriteResult:
		return await asyncio.to_thread(self.write, file_path, content)

	async def agrep_raw(self, pattern: str, path: str | None = "/home/user", glob: str | None = None) -> list[GrepMatch] | str:
		return await asyncio.to_thread(self.grep_raw, pattern, path, glob)

	async def aglob_info(self, pattern: str, path: str = "/home/user") -> list[FileInfo]:
		return await asyncio.to_thread(self.glob_info, pattern, path)
