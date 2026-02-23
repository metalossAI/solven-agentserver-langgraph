"""
E2B Sandbox backend for DeepAgents using Cloudflare R2.
Implements the BackendProtocol for filesystem operations in an isolated sandbox environment.

ARCHITECTURE OVERVIEW:
======================
Direct R2 Mounts:
- **R2 Mounts**:
  - threads/{thread_id} -> /workspace
  - skills/{user_id} -> /skills
  - threads/{ticket_id} -> /ticket (optional, read-only)

- **Agent View**:
  - Works from /workspace directory
  - Can access /skills and /ticket directly
  - System directories available at root for python/node
"""
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
	E2B Sandbox backend with direct R2 mounts.
	
	R2 Mounts:
	- /workspace - threads/{thread_id}
	- /skills - skills/{user_id}
	- /ticket - threads/{ticket_id} (optional, read-only)
	
	Agent works from /workspace directory and can access:
	- /workspace - user workspace files
	- /skills - user skills
	- /ticket - ticket files (read-only)
	"""
	
	def __init__(self, runtime: ToolRuntime[AppContext]):
		self._sandbox: Optional[Sandbox] = None
		self._writer = get_stream_writer()  # Use LangGraph's get_stream_writer() function
		
		# Extract IDs from config instead of runtime context
		from src.utils.config import get_user_id_from_config, get_thread_id_from_config
		
		# Thread ID comes from configurable (set by LangGraph SDK)
		thread_id = get_thread_id_from_config()
		if not thread_id:
			raise RuntimeError("Cannot initialize SandboxBackend: thread_id not found in config")
		self._thread_id = thread_id
		
		# Extract user_id using config helper (handles both langgraph_auth_user and user_data)
		user_id = get_user_id_from_config()
		if not user_id:
			raise RuntimeError("Cannot initialize SandboxBackend: user_id not found in config")
		self._user_id = user_id
		
		# Extract ticket_id from metadata
		config: RunnableConfig = get_config()
		metadata = config.get("metadata", {})
		self._ticket_id = metadata.get("ticket_id")
		
		# R2 mount paths
		self._workspace = "/workspace"  # threads/{thread_id}
		self._skills_mount = "/skills"  # skills/{user_id}
		self._ticket_mount = "/ticket"  # threads/{ticket_id}
		
		# State
		self._initialized = False
		
		# Note: Sandbox initialization is deferred to first use (lazy initialization)
		# This avoids blocking calls during __init__ which runs in async context
		# All methods that need the sandbox call _ensure_initialized() first
	
	@property
	def id(self) -> str:
		"""Unique identifier for the sandbox backend instance."""
		if self._sandbox:
			return self._sandbox.sandbox_id
		return f"sandbox-{self._thread_id}"
	
	def _ensure_initialized(self) -> None:
		"""Ensure sandbox is initialized (idempotent)."""
		if self._sandbox is not None:
			return
		self._writer("Preparando espacio de trabajo...")
		
		# Step 1: Try to find existing sandbox (query by threadId only)
		print(f"[_ensure_initialized] Searching for sandboxes with threadId={self._thread_id}", flush=True)
		print(f"[_ensure_initialized] threadId type: {type(self._thread_id)}, value: '{self._thread_id}'", flush=True)
		try:
			sandbox_paginator = Sandbox.list(
				query=SandboxQuery(
					metadata={"threadId": self._thread_id},
					state=[SandboxState.RUNNING, SandboxState.PAUSED]
				)
			)
			existing_sandboxes = sandbox_paginator.next_items()
			print(f"[_ensure_initialized] Found {len(existing_sandboxes)} existing sandboxes", flush=True)
			if len(existing_sandboxes) > 0:
				for idx, sb in enumerate(existing_sandboxes):
					print(f"[_ensure_initialized] Sandbox {idx}: id={sb.sandbox_id}, metadata={getattr(sb, 'metadata', 'N/A')}", flush=True)
			
			if existing_sandboxes and len(existing_sandboxes) > 0:
				existing_sandbox = existing_sandboxes[0]
				sandbox_id = existing_sandbox.sandbox_id
				
				if sandbox_id:
					try:
						self._sandbox = Sandbox.connect(sandbox_id)
						print(f"[_ensure_initialized] ✓ Connected to existing sandbox: {sandbox_id}", flush=True)
						
						# Verify mounts are accessible
						try:
							check_result = self._sandbox.commands.run("test -d /skills && echo 'MOUNT_OK' || echo 'MOUNT_MISSING'", timeout=5)
							mount_status = check_result.stdout.strip()
							if "MOUNT_MISSING" in mount_status:
								print(f"[_ensure_initialized] Mounts missing, remounting...", flush=True)
								self._mount_r2_buckets()
						except Exception as e:
							print(f"[_ensure_initialized] Error checking mounts: {e}, attempting to mount...", flush=True)
							self._mount_r2_buckets()
						
						self._initialized = True
						print(f"[_ensure_initialized] ✓ Reused existing sandbox", flush=True)
						return
					except Exception as e:
						print(f"[_ensure_initialized] ✗ Failed to connect to existing sandbox {sandbox_id}: {e}", flush=True)
		except Exception as e:
			print(f"[_ensure_initialized] ✗ Error listing sandboxes: {e}", flush=True)
		
		# Step 2: Create new sandbox if not found
		env_vars = {
			"THREAD_ID": self._thread_id,
			"USER_ID": str(self._user_id),
		}
		
		# Add R2 credentials if present
		if bucket := os.getenv("R2_BUCKET_NAME"):
			env_vars["S3_BUCKET_NAME"] = bucket
		if access_key := os.getenv("R2_ACCESS_KEY_ID"):
			env_vars["S3_ACCESS_KEY_ID"] = access_key
		if secret := os.getenv("R2_SECRET_ACCESS_KEY"):
			env_vars["S3_ACCESS_SECRET"] = secret
		if endpoint := os.getenv("R2_ENDPOINT_URL"):
			env_vars["S3_ENDPOINT_URL"] = endpoint
		if region := os.getenv("R2_REGION"):
			env_vars["S3_REGION"] = region
		
		self._sandbox = Sandbox.create(
			template=SANDBOX_TEMPLATE,
			envs=env_vars,
			timeout=180,
			metadata={
				"threadId": self._thread_id,
				"userId": str(self._user_id),
				"ticketId": str(self._ticket_id) if self._ticket_id else "",
			},
		)
		
		print(f"[_ensure_initialized] ✓ Created new sandbox: {self._sandbox.sandbox_id}", flush=True)
		
		# Step 3: Mount R2 buckets for new sandbox
		self._mount_r2_buckets()
		
		# Step 4: Update system skills from official repo
		self._update_system_skills()
		
		# Step 5: Sync local skills to sandbox
		self._sync_local_skills()
		
		self._initialized = True
		self._writer("Espacio de trabajo listo")
		print(f"[_ensure_initialized] ✓ Initialization complete", flush=True)
	
	def _update_system_skills(self) -> None:
		"""
		Clone Anthropic skills repo to /anthropic/ and rsync selected skills into /skills/.

		Clones (or pulls) the Anthropic skills repo at /anthropic/, then copies only the
		docx, xlsx, pdf and pptx skills into /skills/ so the agent can find them alongside
		user skills.

		/skills/ is an R2-backed rclone FUSE mount. Writes go over the network, so we:
		  - Skip skills that already exist in /skills/ (R2 persists across sandbox restarts
		    for the same user, so they only need to be uploaded once).
		  - Batch all four rsyncs into a single shell command to minimise round-trips.
		  - Use a generous single timeout (300 s) for the whole operation.

		Structure after sync:
		- /anthropic/              (Anthropic repo - git-managed, staging area)
		  └── skills/
		      ├── docx/
		      ├── pdf/
		      ├── xlsx/
		      └── pptx/
		- /skills/                 (R2-mounted user skills bucket)
		  ├── docx/                ← rsynced from /anthropic/skills/docx/
		  ├── xlsx/                ← rsynced from /anthropic/skills/xlsx/
		  ├── pdf/                 ← rsynced from /anthropic/skills/pdf/
		  ├── pptx/                ← rsynced from /anthropic/skills/pptx/
		  └── escrituras/          (user custom skill)
		"""
		SKILLS_TO_SYNC = ["docx", "xlsx", "pdf", "pptx"]
		ALLOWED_SKILLS = SKILLS_TO_SYNC + ["escrituras"]

		try:
			repo_url = "https://github.com/anthropics/skills.git"
			repo_dir = "/anthropic"

			# ── 1. Ensure staging directory exists ───────────────────────────
			mkdir_result = self._sandbox.commands.run(
				f"sudo mkdir -p {repo_dir} && sudo chown -R user:user {repo_dir}",
				timeout=30
			)
			if mkdir_result.exit_code != 0:
				print(f"[_update_system_skills] ⚠️  mkdir failed: {mkdir_result.stderr}", flush=True)
			else:
				print(f"[_update_system_skills] ✓ Staging dir ready: {repo_dir}", flush=True)

			self._sandbox.commands.run(
				f"git config --global --add safe.directory {repo_dir}",
				timeout=10
			)

			# ── 2. Clone or pull ─────────────────────────────────────────────
			repo_check = self._sandbox.commands.run(
				f"test -d {repo_dir}/.git && echo EXISTS || echo NOT_FOUND",
				timeout=10
			)

			if "EXISTS" in repo_check.stdout:
				print(f"[_update_system_skills] Pulling latest Anthropic skills", flush=True)
				pull_result = self._sandbox.commands.run(
					f"cd {repo_dir} && git pull origin main",
					timeout=90
				)
				if pull_result.exit_code == 0:
					print(f"[_update_system_skills] ✓ Repo updated", flush=True)
				else:
					print(f"[_update_system_skills] ⚠️  git pull failed, using cached version", flush=True)
			else:
				print(f"[_update_system_skills] Cloning Anthropic skills repo (first time)", flush=True)
				clone_result = self._sandbox.git.clone(
					repo_url,
					path=repo_dir,
					depth=1
				)
				if clone_result.exit_code == 0:
					print(f"[_update_system_skills] ✓ Cloned to {repo_dir}", flush=True)
				else:
					print(f"[_update_system_skills] ✗ Clone failed: {clone_result.stderr}", flush=True)
					raise Exception(f"Failed to clone Anthropic skills: {clone_result.stderr}")

			# ── 3. Rsync all skills in one batched shell command ─────────────
			# /skills/ is an rclone FUSE mount (R2). Writes are slow because each
			# file goes over the network. To keep things fast on subsequent runs we
			# skip any skill whose destination directory is already populated — R2
			# persists across sandbox restarts for the same user.
			#
			# All four rsyncs run in a single commands.run call so we only pay the
			# round-trip overhead once and use a single, generous timeout.
			batch_script = ""
			for skill in SKILLS_TO_SYNC:
				src = f"{repo_dir}/skills/{skill}/"
				dst = f"/skills/{skill}/"
				batch_script += (
					f"if [ -d {dst} ] && [ \"$(ls -A {dst} 2>/dev/null)\" ]; then "
					f"  echo 'SKIP:{skill} already in /skills/'; "
					f"else "
					f"  mkdir -p {dst} && "
					f"  rsync -a {src} {dst} && echo 'OK:{skill}' || echo 'FAIL:{skill}'; "
					f"fi; "
				)

			print(f"[_update_system_skills] Syncing skills {SKILLS_TO_SYNC} → /skills/", flush=True)
			sync_result = self._sandbox.commands.run(
				f"bash -c '{batch_script}'",
				timeout=300  # generous: writing to R2 via FUSE can be slow on first run
			)

			for line in sync_result.stdout.splitlines():
				line = line.strip()
				if line.startswith("OK:"):
					print(f"[_update_system_skills] ✓ Synced {line[3:]}", flush=True)
				elif line.startswith("SKIP:"):
					print(f"[_update_system_skills] ↩ {line[5:]}", flush=True)
				elif line.startswith("FAIL:"):
					print(f"[_update_system_skills] ⚠️  rsync failed for {line[5:]}", flush=True)

			if sync_result.exit_code != 0:
				print(f"[_update_system_skills] ⚠️  Batch sync stderr: {sync_result.stderr}", flush=True)

			# ── 4. Purge any unexpected skill directories from /skills/ ──────
			# /skills/ must contain ONLY the skills in ALLOWED_SKILLS. Anything
			# else (stale uploads, leftover dirs, etc.) is removed so the agent's
			# skill context stays clean and predictable.
			allowed_list = " ".join(ALLOWED_SKILLS)
			purge_script = (
				f"ALLOWED='{allowed_list}'; "
				"for dir in /skills/*/; do "
				"  [ -d \"$dir\" ] || continue; "
				"  name=$(basename \"$dir\"); "
				"  if ! echo \"$ALLOWED\" | grep -qw \"$name\"; then "
				"    rm -rf \"$dir\" && echo \"REMOVED:$name\" || echo \"REMOVE_FAIL:$name\"; "
				"  fi; "
				"done"
			)
			print(f"[_update_system_skills] Purging unexpected dirs from /skills/ (allowed: {ALLOWED_SKILLS})", flush=True)
			purge_result = self._sandbox.commands.run(
				f"bash -c '{purge_script}'",
				timeout=60
			)
			for line in purge_result.stdout.splitlines():
				line = line.strip()
				if line.startswith("REMOVED:"):
					print(f"[_update_system_skills] 🗑️  Removed unexpected skill: {line[8:]}", flush=True)
				elif line.startswith("REMOVE_FAIL:"):
					print(f"[_update_system_skills] ⚠️  Failed to remove: {line[12:]}", flush=True)
			if not purge_result.stdout.strip():
				print(f"[_update_system_skills] ✓ No unexpected dirs found", flush=True)

			print(f"[_update_system_skills] ✓ System skills ready at /skills/", flush=True)

		except Exception as e:
			print(f"[_update_system_skills] ✗ Error updating system skills: {e}", flush=True)
			import traceback
			print(f"[_update_system_skills] Traceback:\n{traceback.format_exc()}", flush=True)
	
	def _sync_local_skills(self) -> None:		
		# Path to local skills directory
		local_skills_dir = os.path.join(os.path.dirname(__file__), "skills")
		
		if not os.path.exists(local_skills_dir):
			print(f"[_sync_local_skills] Local skills directory not found: {local_skills_dir}", flush=True)
			return
		
		try:
			# Sync escrituras skill
			escrituras_skill_path = os.path.join(local_skills_dir, "escrituras", "SKILL.md")
			if os.path.exists(escrituras_skill_path):
				with open(escrituras_skill_path, 'r', encoding='utf-8') as f:
					skill_content = f.read()
				
				# Ensure /skills/escrituras directory exists
				self._sandbox.commands.run("mkdir -p /skills/escrituras", timeout=10)
				
				# Write skill file to sandbox
				self._sandbox.files.write("/skills/escrituras/SKILL.md", skill_content)
				print(f"[_sync_local_skills] ✓ Synced escrituras/SKILL.md", flush=True)
			else:
				print(f"[_sync_local_skills] escrituras/SKILL.md not found at {escrituras_skill_path}", flush=True)
			
			# TODO: Add more skills here as needed
			
		except Exception as e:
			print(f"[_sync_local_skills] ✗ Error syncing skills: {e}", flush=True)
	
	def _mount_r2_buckets(self) -> None:
		"""Mount R2 buckets using rclone."""
		
		# Get credentials from environment (support both R2_ and S3_ prefixes)
		bucket = os.getenv("R2_BUCKET_NAME") or os.getenv("S3_BUCKET_NAME", "solven-testing")
		access_key = os.getenv("R2_ACCESS_KEY_ID") or os.getenv("S3_ACCESS_KEY_ID")
		secret = os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("S3_ACCESS_SECRET")
		endpoint = os.getenv("R2_ENDPOINT_URL") or os.getenv("S3_ENDPOINT_URL", "")
		region = os.getenv("R2_REGION") or os.getenv("S3_REGION", "auto")
		
		if not access_key or not secret:
			return
	
		# Upload mount scripts from files
		self._upload_mount_scripts()
		
		try:
			env_vars = f"S3_ENDPOINT_URL='{endpoint}' S3_ACCESS_KEY_ID='{access_key}' S3_ACCESS_SECRET='{secret}' S3_REGION='{region}'"
			result = self._sandbox.commands.run(
				f"{env_vars} sudo -E bash /tmp/create_rclone_config.sh",
				timeout=180
			)
			if result.exit_code != 0:
				raise RuntimeError(f"Failed to create rclone config (exit {result.exit_code}): {result.stderr or result.stdout}")
		except Exception as e:
			raise
		
		# Mount thread workspace to /workspace - critical, must succeed
		# First ensure workspace directory exists
		self._sandbox.commands.run(f"sudo mkdir -p {self._workspace}", timeout=30)
		
		# Check if workspace is already mounted
		try:
			check_mount = self._sandbox.commands.run(
				f"mountpoint -q {self._workspace} 2>/dev/null && echo 'ALREADY_MOUNTED' || echo 'NOT_MOUNTED'",
				timeout=10
			)
			if "ALREADY_MOUNTED" in check_mount.stdout:
				print(f"[Mount] {self._workspace} is already mounted, skipping mount", flush=True)
				# Verify it's accessible
				verify_result = self._sandbox.commands.run(
					f"test -d {self._workspace} && ls {self._workspace} >/dev/null 2>&1 && echo 'MOUNT_OK' || echo 'MOUNT_FAILED'",
					timeout=10
				)
				if "MOUNT_OK" in verify_result.stdout:
					return  # Already mounted and working
				# Mount exists but not accessible, try to unmount and remount
				print(f"[Mount] Existing mount not accessible, attempting to unmount and remount", flush=True)
				self._sandbox.commands.run(
					f"sudo umount {self._workspace} 2>/dev/null || true",
					timeout=30
				)
		except Exception as e:
			# If check fails, continue with mount attempt
			print(f"[Mount] Could not check existing mount: {e}", flush=True)
		
		mount_cmd = f'bash /tmp/mount_s3_path.sh "{bucket}" "threads/{self._thread_id}" "{self._workspace}" "/tmp/rclone-thread.log"'
		try:
			# Run mount command with longer timeout (rclone mounts can take time)
			print(f"[Mount] Starting mount command: {mount_cmd}", flush=True)
			result = self._sandbox.commands.run(mount_cmd, timeout=600)
			if result.exit_code != 0:
				# Show rclone log if available
				try:
					log_result = self._sandbox.commands.run(
						"sudo tail -100 /tmp/rclone-thread.log 2>&1 || echo 'No log file'",
						timeout=10
					)
					log_output = log_result.stdout if log_result else "No log available"
				except:
					log_output = "Could not read log file"
				raise RuntimeError(
					f"Failed to mount thread workspace (exit {result.exit_code}): "
					f"{result.stderr or result.stdout}\n\nLog output:\n{log_output}"
				)
			
			# Verify mount is actually accessible (wait a bit for mount to stabilize)
			import time
			time.sleep(2)
			verify_result = self._sandbox.commands.run(
				f"test -d {self._workspace} && ls {self._workspace} >/dev/null 2>&1 && echo 'MOUNT_OK' || echo 'MOUNT_FAILED'",
				timeout=30
			)
			if "MOUNT_OK" not in verify_result.stdout:
				# Check if rclone process is still running
				ps_result = self._sandbox.commands.run(
					"ps aux | grep 'rclone.*mount.*threads' | grep -v grep || echo 'NO_PROCESS'",
					timeout=10
				)
				raise RuntimeError(
					f"Mount verification failed. Workspace mount may not be accessible.\n"
					f"Rclone process status: {ps_result.stdout}\n"
					f"Check log: /tmp/rclone-thread.log"
				)
		except Exception as timeout_exc:
			# Timeout or other exception occurred - try to get diagnostic info
			is_timeout = isinstance(timeout_exc, TimeoutError) or "timeout" in str(timeout_exc).lower()
			
			try:
				log_result = self._sandbox.commands.run(
					"sudo tail -100 /tmp/rclone-thread.log 2>&1 || echo 'No log file'",
					timeout=10
				)
				ps_result = self._sandbox.commands.run(
					"ps aux | grep rclone | grep -v grep || echo 'No rclone processes'",
					timeout=10
				)
				mount_result = self._sandbox.commands.run(
					f"mountpoint {self._workspace} 2>&1 || mount | grep {self._workspace} || echo 'Not mounted'",
					timeout=10
				)
				log_output = log_result.stdout if log_result else "No log available"
				ps_output = ps_result.stdout if ps_result else "Could not check processes"
				mount_output = mount_result.stdout if mount_result else "Could not check mount status"
			except:
				log_output = "Could not read diagnostics"
				ps_output = "Could not check processes"
				mount_output = "Could not check mount status"
			
			error_msg = (
				f"Thread mount {'timed out after 600 seconds' if is_timeout else 'failed'}.\n"
				f"Exception: {str(timeout_exc)}\n"
				f"Rclone processes: {ps_output}\n"
				f"Mount status: {mount_output}\n"
				f"Log output:\n{log_output}\n"
				f"Check rclone configuration and S3 connectivity."
			)
			raise RuntimeError(error_msg)
		except RuntimeError:
			# Re-raise RuntimeErrors as-is
			raise
		except Exception as e:
			# Catch any other exceptions and provide context
			try:
				log_result = self._sandbox.commands.run(
					"sudo tail -100 /tmp/rclone-thread.log 2>&1 || echo 'No log file'",
					timeout=10
				)
				log_output = log_result.stdout if log_result else "No log available"
			except:
				log_output = "Could not read log file"
			
			raise RuntimeError(
				f"Thread mount failed with exception: {str(e)}\n"
				f"Log output:\n{log_output}"
			)
		
		# Mount user skills directory to /mnt/skills
		try:
			# Ensure parent directory exists and is visible
			self._sandbox.commands.run("sudo mkdir -p /mnt", timeout=30)
			# Verify mnt directory exists and is accessible
			verify_mnt = self._sandbox.commands.run(
				"test -d /mnt && ls -la /mnt >/dev/null 2>&1 && echo 'MNT_OK' || echo 'MNT_FAILED'",
				timeout=10
			)
			print(f"[Mount] /mnt directory check: {verify_mnt.stdout}", flush=True)
			
			result = self._sandbox.commands.run(
				f'bash /tmp/mount_s3_path.sh "{bucket}" "skills/{self._user_id}" "{self._skills_mount}" "/tmp/rclone-skills-user.log"',
				timeout=500
			)
			if result.exit_code != 0:
				raise RuntimeError(f"Failed to mount user skills (exit {result.exit_code})")
			
			# Verify mount is accessible
			import time
			time.sleep(2)
			verify_mount = self._sandbox.commands.run(
				"test -d /mnt/skills && ls /mnt/skills >/dev/null 2>&1 && echo 'MOUNT_OK' || echo 'MOUNT_FAILED'",
				timeout=10
			)
			print(f"[Mount] /mnt/skills mount check: {verify_mount.stdout}", flush=True)
		except Exception as e:
			raise
		
		# Mount ticket if exists (optional) to /.ticket
		if self._ticket_id:
			try:
				# Ensure parent directory exists and is visible
				self._sandbox.commands.run("sudo mkdir -p /.ticket", timeout=30)
				# Verify .ticket directory exists
				verify_ticket = self._sandbox.commands.run(
					"test -d /.ticket && ls -la /.ticket >/dev/null 2>&1 && echo 'TICKET_OK' || echo 'TICKET_FAILED'",
					timeout=10
				)
				print(f"[Mount] .ticket directory check: {verify_ticket.stdout}", flush=True)
				
				result = self._sandbox.commands.run(
					f'bash /tmp/mount_s3_path.sh "{bucket}" "threads/{self._ticket_id}" "{self._ticket_mount}" "/tmp/rclone-ticket.log"',
					timeout=500
				)
				if result.exit_code != 0:
					raise RuntimeError(f"Failed to mount ticket workspace (exit {result.exit_code})")
				
				# Verify mount is accessible
				import time
				time.sleep(2)
				verify_mount = self._sandbox.commands.run(
					"test -d /.ticket && ls /.ticket >/dev/null 2>&1 && echo 'MOUNT_OK' || echo 'MOUNT_FAILED'",
					timeout=10
				)
				print(f"[Mount] .ticket mount check: {verify_mount.stdout}", flush=True)
			except Exception as e:
				pass
	
	def _upload_mount_scripts(self) -> None:
		"""Upload rclone mount scripts to sandbox from files."""
		# Get script directory path
		src_dir = os.path.dirname(os.path.abspath(__file__))
		script_dir = os.path.join(src_dir, "e2b_sandbox", "scripts")
		
		# Read script files
		config_script_path = os.path.join(script_dir, "create_rclone_config.sh")
		mount_script_path = os.path.join(script_dir, "mount_s3_path.sh")
		
		with open(config_script_path, "r") as f:
			config_script = f.read()
		
		with open(mount_script_path, "r") as f:
			mount_script = f.read()

		
		# Upload scripts to sandbox
		try:
			self._sandbox.files.write("/tmp/create_rclone_config.sh", config_script)
			
			self._sandbox.files.write("/tmp/mount_s3_path.sh", mount_script)
		except Exception as e:
			raise
		
		# Verify files exist
		verify_result = self._sandbox.commands.run("ls -la /tmp/*.sh", timeout=500)
		
		# Make scripts executable
		chmod_result = self._sandbox.commands.run("chmod +x /tmp/create_rclone_config.sh /tmp/mount_s3_path.sh", timeout=500)
		if chmod_result.exit_code != 0:
			raise RuntimeError(f"Failed to make scripts executable: {chmod_result.stderr}")
	
	def _filter_unwanted_commands(self, command: str) -> Optional[str]:
		"""Block dangerous commands."""
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
		"""Execute command directly in sandbox.
		
		Commands run from /workspace directory where:
		- /workspace contains user workspace files
		- /skills contains user skills
		- /ticket contains ticket files (read-only)
		"""
		self._ensure_initialized()
		
		# Filter unwanted commands
		if error_msg := self._filter_unwanted_commands(command):
			return ExecuteResponse(
				output=error_msg,
				exit_code=1,
				truncated=False
			)
		wrapped_command = f"cd {shlex.quote(self._workspace)} && {command}"
		try:
			result = self._sandbox.commands.run(wrapped_command, timeout=500)
		except Exception as e:
			return ExecuteResponse(
				output=f"Error executing command: {str(e)}",
				exit_code=1,
				truncated=False
			)
		
		# Flush filesystem changes to ensure FUSE mounts see them
		try:
			self._sandbox.commands.run("sync", timeout=500)
		except:
			pass  # Sync failures are not critical
		
		return ExecuteResponse(
			output=result.stdout + result.stderr,
			exit_code=result.exit_code,
			truncated=False
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
				# Decode bytes to string if it's text content
				if isinstance(content, bytes):
					try:
						content_str = content.decode('utf-8')
						self._sandbox.files.write(path, content_str)
					except UnicodeDecodeError:
						self._sandbox.files.write(path, content)
				else:
					self._sandbox.files.write(path, str(content))
				responses.append(FileUploadResponse(path=path, error=None))
			except Exception as e:
				responses.append(FileUploadResponse(path=path, error="permission_denied"))
		
		return responses
	
	def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
		"""Download multiple files from the sandbox using download_url for raw bytes."""
		self._ensure_initialized()
		responses = []
		for path in paths:
			try:
				if not self._sandbox.files.exists(path):
					responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
					continue
				
				# Get download URL from E2B
				download_url = self._sandbox.download_url(path)
				
				# Fetch file content directly via HTTP to get raw bytes
				import requests
				response = requests.get(download_url, timeout=30)
				response.raise_for_status()
				
				content_bytes = response.content  # Raw bytes, no encoding
				responses.append(FileDownloadResponse(path=path, content=content_bytes, error=None))
				
			except Exception as e:
				responses.append(FileDownloadResponse(path=path, content=None, error=f"download_error: {str(e)}"))
		
		return responses
	
	async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
		"""Async version of upload_files."""
		return await asyncio.to_thread(self.upload_files, files)
	
	async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
		"""Async version of download_files."""
		return await asyncio.to_thread(self.download_files, paths)
	
	async def aread(self, file_path: str, offset: int = 0, limit: int = 2000) -> str:
		return await asyncio.to_thread(self.read, file_path, offset, limit)
	
	async def awrite(self, file_path: str, content: str) -> WriteResult:
		return await asyncio.to_thread(self.write, file_path, content)
	
	# ── Search-path guard ────────────────────────────────────────────────────
	# Tools like glob_info / grep_raw operate recursively. Allowing path="/"
	# (the base-class default) causes them to traverse the entire filesystem
	# (including /anthropic/, /usr/, FUSE mounts, etc.) which is both slow and
	# unsafe. All searches are restricted to /workspace and /skills.

	_ALLOWED_SEARCH_ROOTS = ("/workspace", "/skills")

	def _sanitize_search_path(self, path: str | None) -> str:
		"""Clamp a search path to /workspace or /skills.

		- None / empty / "." / "/" → /workspace (safe default)
		- Already inside /workspace or /skills → returned as-is
		- Anything else → /workspace (with a warning log)
		"""
		if not path or path in ("/", "."):
			return self._workspace
		for root in self._ALLOWED_SEARCH_ROOTS:
			if path == root or path.startswith(root + "/"):
				return path
		print(
			f"[SandboxBackend] ⚠️  Search path '{path}' is outside allowed roots "
			f"{self._ALLOWED_SEARCH_ROOTS}, redirecting to /workspace",
			flush=True,
		)
		return self._workspace

	def glob_info(self, pattern: str, path: str = "/workspace") -> list[FileInfo]:
		"""Glob within /workspace or /skills only (never the full filesystem)."""
		return super().glob_info(pattern, self._sanitize_search_path(path))

	def grep_raw(
		self, pattern: str, path: str | None = None, glob: str | None = None
	) -> list[GrepMatch] | str:
		"""Grep within /workspace or /skills only (never the full filesystem)."""
		return super().grep_raw(pattern, self._sanitize_search_path(path), glob)

	async def agrep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list[GrepMatch] | str:
		"""
		Structured search results or error string for invalid input.
		
		Performs a recursive text search using grep with structured output (filename, line number, matching text).
		This tool is designed for PRECISE searches, not for listing directory contents or broad exploratory searches.
		
		Search is restricted to /workspace and /skills. Any other path (including '/')
		is silently redirected to /workspace.
		
		IMPORTANT: Avoid generic patterns that would match too many files:
		- DO NOT use patterns like '*' or '**/*' in the glob parameter
		- DO NOT use overly broad search patterns that would return thousands of results
		- Use specific file extensions in glob (e.g., '*.py', '*.tsx') when needed
		- Use specific search terms in the pattern parameter
		- Prefer searching in specific directories rather than the entire filesystem
		
		For listing directory contents, use list_files() or read_file() instead.
		"""
		return await asyncio.to_thread(self.grep_raw, pattern, path, glob)
	
	async def aglob_info(self, pattern: str, path: str = "/workspace") -> list[FileInfo]:
		"""
		Structured glob matching returning FileInfo dicts.
		
		Finds files and directories matching a glob pattern with structured output (path, is_dir).
		This tool is designed for PRECISE file matching, not for listing entire directory trees or broad searches.
		
		Search is restricted to /workspace and /skills. Any other path (including '/')
		is silently redirected to /workspace.
		
		IMPORTANT: Avoid generic patterns that would match too many files:
		- DO NOT use patterns like '**/*' or '*' that would return thousands of files
		- DO NOT use this tool to list all contents of directories
		- Use specific file extensions (e.g., '*.py', '*.tsx', '*.md')
		- Use specific filename patterns (e.g., 'test_*.py', '*.config.js')
		- Prefer searching in specific subdirectories rather than the root '/'
		- Limit the scope of your search to relevant directories
		
		For listing directory contents, use list_files() instead.
		For broad exploratory searches, consider using more specific tools or narrowing your search criteria first.
		"""
		return await asyncio.to_thread(self.glob_info, pattern, path)

	@property
	def id(self) -> str:
		"""Unique identifier for the sandbox backend instance."""
		if self._sandbox:
			return self._sandbox.sandbox_id
		return "uninitialized"

