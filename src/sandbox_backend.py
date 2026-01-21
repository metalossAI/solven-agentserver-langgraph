"""
E2B Sandbox backend for DeepAgents using Cloudflare R2.
Implements the BackendProtocol for filesystem operations in an isolated sandbox environment.

ARCHITECTURE OVERVIEW:
======================

Simple Mount Approach:
- **Thread workspace**: Mounted from `threads/{thread_id}` to `/home/user` (writable)
- **Skills**: Mounted from `skills/{user_id}` to `/mnt/skills` (writable)
- **Ticket**: Mounted from `threads/{ticket_id}` to `/mnt/.ticket` (read-only, if ticket exists)
"""
import os
import re
import shlex
import asyncio
from typing import Optional
from datetime import datetime

from e2b import Sandbox, CommandResult, SandboxQuery, SandboxState

from deepagents.backends.protocol import SandboxBackendProtocol, WriteResult, EditResult, ExecuteResponse, FileDownloadResponse, FileUploadResponse
from deepagents.backends.utils import FileInfo, GrepMatch
from langchain.tools import ToolRuntime
from langgraph.config import get_stream_writer, get_config
from langgraph.graph.state import RunnableConfig
from src.models import AppContext


def _parse_skillmd_frontmatter(skillmd: str) -> str:
	"""
	Parse and extract the frontmatter from a skillmd file.
	
	Extracts YAML frontmatter from the beginning of a file in the format:
	---
	name: compraventa-escrituras
	description: Redacta escrituras de compraventa...
	---
	
	Args:
		skillmd: The content of the skillmd file as a string
		
	Returns:
		The frontmatter string (content between --- delimiters), or empty string if not found
	"""
	frontmatter_pattern = r'^---\s*\n(.*?)\n---\s*\n'
	match = re.match(frontmatter_pattern, skillmd, re.DOTALL)
	
	if not match:
		return ""
	
	return match.group(1)


SANDBOX_TEMPLATE = "solven-sandbox-v1"


class SandboxBackend(SandboxBackendProtocol):
	"""
	E2B Sandbox backend with simple mount approach.
	
	Mount structure:
	- Workspace: /workspace - Mounted from threads/{thread_id} (writable)
	- Skills: /mnt/skills - Mounted from skills/{user_id} (writable)
	- Ticket: /.ticket - Mounted from threads/{ticket_id} (read-only, if exists)
	"""
	
	def __init__(self, runtime: ToolRuntime[AppContext]):
		self._sandbox: Optional[Sandbox] = None
		self._writer = get_stream_writer()  # Use LangGraph's get_stream_writer() function
		
		# Extract IDs from config instead of runtime context
		config: RunnableConfig = get_config()
		
		# Thread ID comes from configurable (set by LangGraph SDK)
		thread_id = config["configurable"].get("thread_id")
		if not thread_id:
			raise RuntimeError("Cannot initialize SandboxBackend: thread_id not found in config")
		self._thread_id = thread_id
		
		# Extract user data from auth
		user_config = config["configurable"].get("langgraph_auth_user")
		user_data = user_config.get("user_data") if user_config else {}
		user_id = user_data.get("id")
		if not user_id:
			raise RuntimeError("Cannot initialize SandboxBackend: user_id not found in config")
		self._user_id = user_id
		
		# Extract ticket_id from metadata
		metadata = config.get("metadata", {})
		self._ticket_id = metadata.get("ticket_id")
		
		# Paths
		self._workspace = "/workspace"  # Main workspace (mounted from threads/{thread_id})
		self._skills_mount = "/mnt/skills"  # Skills mount (from skills/{user_id})
		self._ticket_mount = "/.ticket"  # Ticket mount (from threads/{ticket_id})
		
		# Directories to show (everything else is filtered)
		self._allowed_dirs = ["workspace", "mnt", ".ticket"]
		
		# Only show these directories at root level - filter everything else
		# This ensures we only see workspace, mnt, and .ticket
		self._allowed_root_dirs = ["workspace", "mnt", ".ticket"]
		
		# State
		self._initialized = False
	
	def _ensure_initialized(self) -> None:
		"""Ensure sandbox is initialized (idempotent)."""
		if self._sandbox is not None:
			return
		
		print(f"[_ensure_initialized] Starting initialization for thread_id={self._thread_id}, user_id={self._user_id}", flush=True)
		self._writer("🔧 Preparando espacio de trabajo...")
		
		# Step 1: Try to find existing sandbox
		try:
			existing_sandboxes = Sandbox.list(
				query=SandboxQuery(
					metadata={"threadId": self._thread_id, "userId": str(self._user_id)},
					state=[SandboxState.RUNNING, SandboxState.PAUSED]
				)
			)
			
			if existing_sandboxes and len(existing_sandboxes) > 0:
				existing_sandbox = existing_sandboxes[0]
				sandbox_id = existing_sandbox.sandbox_id
				
				if sandbox_id:
					try:
						self._sandbox = Sandbox.connect(sandbox_id)
						print(f"[_ensure_initialized] ✓ Connected to existing sandbox: {sandbox_id}", flush=True)
						
						# Verify mount is accessible - if not, remount
						try:
							check_result = self._sandbox.commands.run("test -d /mnt/skills && echo 'MOUNT_OK' || echo 'MOUNT_MISSING'", timeout=5)
							mount_status = check_result.stdout.strip()
							print(f"[_ensure_initialized] Mount check: {mount_status}", flush=True)
							
							if "MOUNT_MISSING" in mount_status:
								print(f"[_ensure_initialized] Mounts missing, remounting...", flush=True)
								self._mount_r2_buckets()
						except Exception as e:
							print(f"[_ensure_initialized] Error checking mounts: {e}, attempting to mount...", flush=True)
							self._mount_r2_buckets()
						
						self._initialized = True
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
		
		self._initialized = True
		self._writer("Espacio de trabajo listo")
		print(f"[_ensure_initialized] ✓ Initialization complete", flush=True)
	
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
						"tail -100 /tmp/rclone-thread.log 2>&1 || echo 'No log file'",
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
					"tail -100 /tmp/rclone-thread.log 2>&1 || echo 'No log file'",
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
					"tail -100 /tmp/rclone-thread.log 2>&1 || echo 'No log file'",
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
		"""Execute command directly in sandbox. Pass command as-is without any path manipulation."""
		self._ensure_initialized()
		
		# Filter unwanted commands
		if error_msg := self._filter_unwanted_commands(command):
			return ExecuteResponse(
				output=error_msg,
				exit_code=1,
				truncated=False
			)
		
		# Run command directly - pass as-is to sandbox
		try:
			result = self._sandbox.commands.run(command, timeout=500)
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
	
	def read(self, path: str, offset: int = 0, limit: int = 2000) -> str:
		"""Read file using sandbox.files API. Pass path directly to sandbox."""
		self._ensure_initialized()
		file_type = path.split('.')[-1]
		disallowed_file_types = ["pdf", "docx", "xlsx", "pptx"]
		if file_type in disallowed_file_types:
			return f"To read this file, please use the corresponsing skill"
		
		# Pass path directly to sandbox without normalization
		try:
			# Check if file exists
			if not self._sandbox.files.exists(path):
				return f"Error reading {path}: File not found"
			
			# Read file using files API
			content_bytes = self._sandbox.files.read(path)
			content = content_bytes.decode('utf-8') if isinstance(content_bytes, bytes) else content_bytes
			return content
		except Exception as e:
			return f"Error reading {path}: {str(e)}"
	
	async def aread(self, path: str, offset: int = 0, limit: int = 2000) -> str:
		"""Async version of read."""
		return await asyncio.to_thread(self.read, path, offset, limit)
	
	def write(self, path: str, content: str) -> WriteResult:
		"""Write file using sandbox.files API. Pass path directly to sandbox."""
		self._ensure_initialized()
		
		# Pass path directly to sandbox without normalization
		# Check if ticket (read-only)
		if path.startswith("/.ticket/"):
			if not self._ticket_id:
				return WriteResult(
					error=f"Cannot write to ticket directory (no ticket_id): {path}",
					path=None,
					files_update=None
				)
			return WriteResult(
				error=f"Cannot write to ticket directory (read-only): {path}",
				path=None,
				files_update=None
			)
		
		# Check if file exists (to prevent overwriting)
		try:
			if self._sandbox.files.exists(path):
				return WriteResult(
					error=f"File '{path}' already exists. Use edit to modify existing files.",
					path=None,
					files_update=None
				)
		except Exception as e:
			pass  # Continue even if check fails
		
		# Write file using files API (auto-creates parent directories)
		try:
			self._sandbox.files.write(path, content)
			return WriteResult(error=None, path=path, files_update=None)
		except Exception as e:
			return WriteResult(
				error=f"Write failed: {str(e)}",
				path=None,
				files_update=None
			)
	
	async def awrite(self, path: str, content: str) -> WriteResult:
		"""Async version of write."""
		return await asyncio.to_thread(self.write, path, content)
	
	def edit(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
		"""Edit file using sandbox.files API. Pass path directly to sandbox."""
		self._ensure_initialized()
		
		# Pass path directly to sandbox without normalization
		# Check if ticket (read-only)
		if path.startswith("/.ticket/"):
			return EditResult(
				error=f"Cannot edit ticket directory (read-only): {path}",
				path=None,
				files_update=None,
				occurrences=0
			)
		
		# Check if file exists
		try:
			if not self._sandbox.files.exists(path):
				return EditResult(
					error=f"File not found: {path}",
					path=None,
					files_update=None,
					occurrences=0
				)
		except Exception as e:
			return EditResult(
				error=f"File check failed: {str(e)}",
				path=None,
				files_update=None,
				occurrences=0
			)
		
		# Read file content
		try:
			content_bytes = self._sandbox.files.read(path)
			content = content_bytes.decode('utf-8') if isinstance(content_bytes, bytes) else content_bytes
		except Exception as e:
			return EditResult(
				error=f"Failed to read file: {str(e)}",
				path=None,
				files_update=None,
				occurrences=0
			)
		
		# Count occurrences
		occurrences = content.count(old_string)
		
		if occurrences == 0:
			return EditResult(
				error=f"String not found in file",
				path=None,
				files_update=None,
				occurrences=0
			)
		
		if occurrences > 1 and not replace_all:
			return EditResult(
				error=f"String appears {occurrences} times. Use replace_all=True",
				path=None,
				files_update=None,
				occurrences=occurrences
			)
		
		# Perform replacement in Python
		if replace_all:
			new_content = content.replace(old_string, new_string)
		else:
			# Replace first occurrence only
			new_content = content.replace(old_string, new_string, 1)
		
		# Write back using files API
		try:
			self._sandbox.files.write(path, new_content)
			return EditResult(error=None, path=path, files_update=None, occurrences=occurrences)
		except Exception as e:
			return EditResult(
				error=f"Edit failed: {str(e)}",
				path=None,
				files_update=None,
				occurrences=0
			)
	
	async def aedit(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
		"""Async version of edit."""
		return await asyncio.to_thread(self.edit, path, old_string, new_string, replace_all)
	
	def ls_info(self, path: str = "/") -> list[FileInfo]:
		"""List directory contents using sandbox.files.list() API. Pass path directly to sandbox."""
		self._ensure_initialized()
		
		# Normalize path - handle trailing slashes
		normalized_path = path.rstrip('/') if path != "/" else "/"
		
		print(f"[ls_info] Listing path: '{path}' (normalized: '{normalized_path}')", flush=True)
		
		# For mounted directories (like /mnt/skills), use command-based listing
		# as E2B's files.list() may not work reliably with FUSE mounts
		# Check both original and normalized path to catch /mnt/skills/ and /mnt/skills
		if path.startswith("/mnt/") or normalized_path.startswith("/mnt/") or path.startswith("/.ticket") or normalized_path.startswith("/.ticket"):
			target_path = normalized_path if normalized_path != "/" else path
			print(f"[ls_info] Using command-based listing for mounted path: {target_path}", flush=True)
			return self._ls_info_command(target_path)
		
		# For regular paths, use E2B files.list() API
		depth = len(path.split('/'))
		try:
			entries : list[str] = self._sandbox.files.list(path, depth=depth)
			print(f"[ls_info] files.list() returned {len(entries)} entries for {path}", flush=True)
		except Exception as e:
			print(f"[ls_info] files.list() failed for {path}: {e}, trying command-based approach", flush=True)
			return self._ls_info_command(path)
		
		files = []
		for entry in entries:
			# E2B files.list() returns strings (paths) or objects with name/path
			if isinstance(entry, str):
				# Entry is a string path - extract filename
				entry_path = entry.rstrip('/')
				filename = entry_path.split('/')[-1] if entry_path else entry_path
			else:
				# Entry is an object - try to get name or path
				filename = getattr(entry, 'name', None)
				entry_path = getattr(entry, 'path', None)
				if not filename and entry_path:
					filename = entry_path.rstrip('/').split('/')[-1]
				if not entry_path and filename:
					# Construct path from path and filename
					entry_path = f"{path.rstrip('/')}/{filename}"
			
			# Skip empty filenames, ".", and ".."
			if not filename or filename == "." or filename == "..":
				continue
			
			# At root level, only show allowed directories
			if path == "/":
				if filename not in self._allowed_root_dirs:
					continue
			
			# Construct full path
			if path == "/":
				full_path = f"/{filename}"
			elif isinstance(entry_path, str) and entry_path.startswith('/'):
				full_path = entry_path
			else:
				base_path = path.rstrip('/')
				fname = filename.lstrip('/')
				full_path = f"{base_path}/{fname}".replace("//", "/")
			
			# Get size and modified time from entry if available
			size = 0
			modified = None
			is_dir = False
			
			# Check if it's a directory - check if path ends with / or entry indicates directory
			if isinstance(entry, str) and entry.endswith('/'):
				is_dir = True
			elif isinstance(entry_path, str) and entry_path.endswith('/'):
				is_dir = True
			elif not isinstance(entry, str):
				# Check entry object for directory indicator
				if hasattr(entry, 'is_dir'):
					is_dir = entry.is_dir
				elif hasattr(entry, 'type') and entry.type == 'directory':
					is_dir = True
			
			if not isinstance(entry, str):
				# Try to get metadata from entry object
				if hasattr(entry, 'size'):
					size = entry.size or 0
				if hasattr(entry, 'modified') or hasattr(entry, 'mtime'):
					modified = getattr(entry, 'modified', None) or getattr(entry, 'mtime', None)
				elif hasattr(entry, 'stat'):
					stat = entry.stat
					size = getattr(stat, 'st_size', 0) if stat else 0
					modified = getattr(stat, 'st_mtime', None) if stat else None
			
			# Format modified time as ISO string if it's a datetime object
			modified_at = None
			if modified:
				if hasattr(modified, 'isoformat'):
					modified_at = modified.isoformat()
				elif isinstance(modified, (int, float)):
					# Unix timestamp
					from datetime import datetime
					modified_at = datetime.fromtimestamp(modified).isoformat()
				else:
					modified_at = str(modified)
			
			files.append(FileInfo(
				path=full_path,
				is_dir=is_dir,
				size=size,
				modified_at=modified_at
			))

		return files
	
	def _ls_info_command(self, path: str) -> list[FileInfo]:
		"""List directory contents using shell commands (for FUSE mounts)."""
		import shlex
		try:
			# Normalize path - remove trailing slash for ls command
			normalized_path = path.rstrip('/')
			if not normalized_path:
				normalized_path = "/"
			
			print(f"[_ls_info_command] Listing {normalized_path} (original: {path})", flush=True)
			
			# First verify the directory exists
			check_cmd = f"test -d {shlex.quote(normalized_path)} && echo 'EXISTS' || echo 'NOT_EXISTS'"
			check_result = self._sandbox.commands.run(check_cmd, timeout=5)
			print(f"[_ls_info_command] Directory check: {check_result.stdout.strip()}", flush=True)
			
			if "NOT_EXISTS" in check_result.stdout:
				print(f"[_ls_info_command] Directory {normalized_path} does not exist", flush=True)
				return []
			
			# Use ls command to list directory contents
			ls_cmd = f"ls -la {shlex.quote(normalized_path)} 2>&1 | tail -n +2"
			result = self._sandbox.commands.run(ls_cmd, timeout=10)
			
			print(f"[_ls_info_command] ls exit code: {result.exit_code}, stdout length: {len(result.stdout)}, stderr: {result.stderr[:200]}", flush=True)
			
			if result.exit_code != 0:
				print(f"[_ls_info_command] ls failed for {normalized_path}: {result.stderr}", flush=True)
				return []
			
			files = []
			for line in result.stdout.strip().split('\n'):
				if not line.strip():
					continue
				
				# Parse ls -la output: permissions links owner group size date time name
				# Example: drwxr-xr-x 2 root root 4096 Jan 15 10:30 docx
				parts = line.split(None, 8)
				if len(parts) < 9:
					continue
				
				permissions = parts[0]
				name = parts[8]
				
				# Skip . and ..
				if name in [".", ".."]:
					continue
				
				# Check if it's a directory (first char of permissions is 'd')
				is_dir = permissions.startswith('d')
				
				# Get size
				try:
					size = int(parts[4]) if parts[4].isdigit() else 0
				except:
					size = 0
				
				# Construct full path
				if path == "/":
					full_path = f"/{name}"
				else:
					base_path = path.rstrip('/')
					full_path = f"{base_path}/{name}".replace("//", "/")
				
				# Get modified time from parts[5], parts[6], parts[7]
				# Format: "Jan 15 10:30" or "Jan 15 2024"
				modified_at = None
				if len(parts) >= 8:
					try:
						date_parts = parts[5:8]
						# Try to parse date - this is approximate
						modified_at = " ".join(date_parts)
					except:
						pass
				
				files.append(FileInfo(
					path=full_path,
					is_dir=is_dir,
					size=size,
					modified_at=modified_at
				))
			
			print(f"[_ls_info_command] Found {len(files)} entries in {path}", flush=True)
			return files
		except Exception as e:
			print(f"[_ls_info_command] Exception listing {path}: {e}", flush=True)
			return []
	
	async def als_info(self, path: str = "/") -> list[FileInfo]:
		"""Async version of ls_info."""
		return await asyncio.to_thread(self.ls_info, path)
	
	def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
		"""Find files matching glob pattern using bash globstar. Pass paths directly to sandbox."""
		self._ensure_initialized()
		import shlex
		
		# Build filtering: at root, only search in allowed directories
		# In subdirectories, search everything
		if path == "/":
			# At root: only search in allowed directories using absolute paths
			allowed_globs = " ".join([f"/{allowed}/**/{pattern}" for allowed in self._allowed_root_dirs])
			glob_pattern = f"({allowed_globs})"
			exclusion_checks = "true"  # No exclusion needed, we're already filtering by allowed dirs
		else:
			# In subdirectories: use absolute path with pattern
			base_path = path.rstrip('/')
			glob_pattern = f"{base_path}/**/{pattern}"
			exclude_checks_parts = []
			# Exclude common system directories
			system_dirs = ["bin", "sbin", "dev", "etc", "lib", "lib32", "lib64", "libx32", "proc", "sys", "run", "tmp", "var", "boot", "root", "srv", "opt", "mnt", "media", "home", "lost+found"]
			for exc in system_dirs:
				exclude_checks_parts.append(f'[[ "$file" != *{shlex.quote("/" + exc + "/")}* ]]')
			exclusion_checks = " && ".join(exclude_checks_parts) if exclude_checks_parts else "true"
		
		# Use bash globstar - pass paths directly, no cd needed
		glob_cmd = f"""
shopt -s globstar nullglob
for file in {glob_pattern}; do
	if [ -e "$file" ] && {exclusion_checks}; then
		stat -c '%n|%s|%Y' "$file" 2>/dev/null || true
    fi
done
"""
		try:
			result = self._sandbox.commands.run(glob_cmd, timeout=500)
		except Exception as e:
			return []
		
		# Parse output (format: filename|size|timestamp)
		files = []
		for line in result.stdout.strip().split('\n'):
			if not line or '|' not in line:
				continue
			parts = line.split('|', 2)
			if len(parts) >= 2:
				filename = parts[0]
			# At root level, only show files from allowed directories
			if path == "/":
				# Check if file is in an allowed directory
				path_parts = filename.split('/')
				if path_parts and path_parts[0] not in self._allowed_root_dirs:
					continue
			# In subdirectories, filter out system directories
			else:
				path_parts = filename.split('/')
				system_dirs = ["bin", "sbin", "dev", "etc", "lib", "lib32", "lib64", "libx32", "proc", "sys", "run", "tmp", "var", "boot", "root", "srv", "opt", "mnt", "media", "home", "lost+found"]
				if any(part in system_dirs for part in path_parts if part):
					continue
			
			# Make path absolute
			if not filename.startswith('/'):
				if path == "/":
					full_path = f"/{filename}"
				else:
					base_path = path.rstrip('/')
					fname = filename.lstrip('/')
					full_path = f"{base_path}/{fname}".replace("//", "/")
			else:
				# If absolute, use as-is
				full_path = filename
			
			files.append(FileInfo(
				path=full_path,
				size=int(parts[1]) if parts[1].isdigit() else 0,
				modified_at=parts[2] if len(parts) > 2 else None
			))
			
		return files
	
	async def aglob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
		"""Async version of glob_info."""
		return await asyncio.to_thread(self.glob_info, pattern, path)
	
	def grep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list[GrepMatch] | str:
		"""Search for pattern using ripgrep. Pass path directly to sandbox."""
		self._ensure_initialized()
		
		self._writer(f"Buscando '{pattern}'...")
		
		import shlex
			
		# Pass path directly to sandbox without normalization
		search_path = path if path else "/"
		
		# Build ripgrep command with directory filtering
		# rg is much faster than grep and has native directory exclusion
		rg_parts = ["rg", "--no-config", "--no-heading", "--with-filename", "--line-number"]
		
		# At root level, only search in allowed directories
		if search_path == "/":
			# Only search in workspace, mnt, and .ticket
			for allowed_dir in self._allowed_root_dirs:
				rg_parts.extend(["-g", f"{allowed_dir}/**"])
		else:
			# In subdirectories, exclude system directories
			system_dirs = ["bin", "sbin", "dev", "etc", "lib", "lib32", "lib64", "libx32", "proc", "sys", "run", "tmp", "var", "boot", "root", "srv", "opt", "mnt", "media", "home", "lost+found"]
			for exc_dir in system_dirs:
				rg_parts.extend(["-g", f"!{exc_dir}/**"])
				rg_parts.extend(["-g", f"!**/{exc_dir}/**"])
		
		# Add glob filter if provided (e.g., -g '*.txt')
		if glob:
			rg_parts.extend(["-g", glob])
		
		# Add pattern and search path
		rg_parts.extend([shlex.quote(pattern), shlex.quote(search_path)])
		
		rg_cmd = " ".join(rg_parts) + " 2>/dev/null || true"
		
		try:
			result = self._sandbox.commands.run(rg_cmd, timeout=500)  # rg is much faster
		except Exception as e:
			return []
		
		# Parse rg output (format: file:line:content)
		matches = []
		for line in result.stdout.strip().split('\n'):
			if not line:
				continue
		
			# Split only on first two colons to preserve colons in content
			parts = line.split(':', 2)
			if len(parts) >= 3:
				file_path = parts[0]
				# Ensure path starts with /
				if not file_path.startswith('/'):
					file_path = f"/{file_path}"
				try:
					line_num = int(parts[1])
				except ValueError:
					continue
				content = parts[2]
				
				matches.append(GrepMatch(
					path=file_path,
					line=line_num,
					text=content
				))
	
		return matches
	
	async def agrep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list[GrepMatch] | str:
		"""Async version of grep_raw."""
		return await asyncio.to_thread(self.grep_raw, pattern, path, glob)
	
	def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
		"""Download multiple files from the sandbox."""
		self._ensure_initialized()
		responses = []
		for path in paths:
			try:
				if not self._sandbox.files.exists(path):
					responses.append(FileDownloadResponse(path=path, content=None, error="file_not_found"))
					continue
				
				content_bytes = self._sandbox.files.read(path)
				if isinstance(content_bytes, bytes):
					responses.append(FileDownloadResponse(path=path, content=content_bytes, error=None))
				else:
					# If it's already a string, encode it
					responses.append(FileDownloadResponse(path=path, content=content_bytes.encode('utf-8'), error=None))
			except Exception as e:
				responses.append(FileDownloadResponse(path=path, content=None, error="permission_denied"))
		
		return responses
	
	async def adownload_files(self, paths: list[str]) -> list[FileDownloadResponse]:
		"""Async version of download_files."""
		return await asyncio.to_thread(self.download_files, paths)
	
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
						# Binary file - write as bytes
						self._sandbox.files.write(path, content)
				else:
					self._sandbox.files.write(path, str(content))
				responses.append(FileUploadResponse(path=path, error=None))
			except Exception as e:
				responses.append(FileUploadResponse(path=path, error="permission_denied"))
		
		return responses
	
	async def aupload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
		"""Async version of upload_files."""
		return await asyncio.to_thread(self.upload_files, files)

	@property
	def id(self) -> str:
		"""Unique identifier for the sandbox backend instance."""
		if self._sandbox:
			return self._sandbox.sandbox_id
		return "uninitialized"

