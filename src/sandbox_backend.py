"""
E2B Sandbox backend for DeepAgents using Cloudflare R2.
Implements the BackendProtocol for filesystem operations in an isolated sandbox environment.

ARCHITECTURE OVERVIEW:
======================

Hybrid Venv Approach:
- **Dependency files on R2** (pyproject.toml, package.json, lockfiles) - Persisted, tracked
- **Venvs on local filesystem** (/tmp/workspace/.venv, /tmp/workspace/node_modules) - Fast, no FUSE issues 
- **On startup**: Read dep files from R2, create venvs locally, install deps

Benefits:
- No FUSE symlink/atomic write issues
- Fast venv operations
- Deps persist across sandboxes
- Clean separation: user files on R2, runtime artifacts local
"""
import os
import re
import shlex
import asyncio
from typing import Optional
from datetime import datetime

from e2b import AsyncSandbox, SandboxQuery, SandboxState
from e2b.sandbox.commands.command_handle import CommandExitException

from deepagents.backends.protocol import SandboxBackendProtocol, WriteResult, EditResult, ExecuteResponse
from deepagents.backends.utils import FileInfo, GrepMatch
from langchain.tools import ToolRuntime
from langgraph.config import get_stream_writer
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
	E2B Sandbox backend with hybrid venv approach.
	
	Paths:
	- R2 workspace: /mnt/r2/threads/{thread_id} - User files + dep files
	- Local workspace: /tmp/workspace - Venvs (.venv, node_modules)
	"""
	
	def __init__(self, runtime: ToolRuntime[AppContext]):
		self._sandbox: Optional[AsyncSandbox] = None
		self._runtime = runtime
		self._writer = get_stream_writer()  # Use LangGraph's get_stream_writer() function
		
		# Handle case where context might not be fully initialized yet
		if runtime.context.thread is None:
			raise RuntimeError("Cannot initialize SandboxBackend: runtime.context.thread is None")
		
		self._thread_id = runtime.context.thread.id
		self._user_id = runtime.context.user.id
		self._ticket_id = runtime.context.ticket.id if runtime.context.ticket else None
		
		# Paths
		self._r2_workspace = f"/mnt/r2/threads/{self._thread_id}"  # R2 FUSE mount
		self._local_workspace = "/tmp/workspace"  # Local fast storage
		
		# System directories to exclude from search/list operations
		# These are bind mounts, system paths, and caches that shouldn't be searched
		self._exclude_dirs = [".cache", ".local", ".venv", "node_modules", "bin", "dev", 
		                      "etc", "lib", "lib64", "proc", "usr", "sys", "run", "tmp"]
		
		# State
		self._initialized = False
		self._init_lock = asyncio.Lock()
	
	async def _ensure_initialized(self) -> None:
		"""Ensure sandbox is initialized (idempotent)."""
		if self._initialized:
			return
		
		async with self._init_lock:
			if self._initialized:
				return
			
			self._writer("🔧 Preparando espacio de trabajo...")
			
		# Step 1: Create or connect to sandbox
		self._sandbox, is_existing = await self._create_or_connect_sandbox()
		
		# Step 2-4: Run setup (always needed, even for existing sandboxes)
		# Venvs are in /tmp/workspace which is ephemeral, so they need to be recreated
		if not is_existing:
			# Mount R2 buckets (only for new sandboxes)
			await self._mount_r2_buckets()
		
		# ALWAYS initialize workspace (venvs are ephemeral in /tmp)
		self._writer("📦 Instalando dependencias...")
		await self._initialize_workspace()
		
		# ALWAYS ensure R2 skill directories exist (bwrap will bind-mount them)
		self._writer("🔧 Comprobando que todo funcione...")
		await self._setup_skill_directories()
		
		self._initialized = True
		self._writer("✅ Espacio de trabajo listo")
	
	async def _create_or_connect_sandbox(self) -> tuple[AsyncSandbox, bool]:
		"""
		Get existing sandbox or create new E2B sandbox with environment variables.
		
		Returns:
			Tuple of (sandbox, is_existing) where is_existing is True if connected to existing sandbox
		"""
		try:
			paginator = AsyncSandbox.list(
				query=SandboxQuery(
					metadata={"threadId": self._thread_id},
					state=[SandboxState.RUNNING, SandboxState.PAUSED]
				)
			)
			existing_sandboxes = await paginator.next_items()
			
			if existing_sandboxes and len(existing_sandboxes) > 0:
				existing_sandbox = existing_sandboxes[0]
				sandbox_id = existing_sandbox.sandbox_id
				
				if sandbox_id:
					try:

						sandbox = await AsyncSandbox.connect(sandbox_id)
						return sandbox, True  # Existing sandbox
					except Exception as e:
						print(f"[Sandbox] ✗ Failed to connect to existing sandbox {sandbox_id}: {e}", flush=True)
						# Continue to create new sandbox below
		except Exception as e:
			pass
		env_vars = {
			"THREAD_ID": self._thread_id,
			"USER_ID": str(self._user_id),
		}
		
		# Add R2 credentials if present (using correct env var names)
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
		else:
			env_vars["S3_REGION"] = "auto"
		
		if self._ticket_id:
			env_vars["TICKET_ID"] = str(self._ticket_id)
		
		sandbox = await AsyncSandbox.create(
			template=SANDBOX_TEMPLATE,
			envs=env_vars,
			timeout=180,  # 3 minutes in s
				metadata={
				"threadId": self._thread_id,
				"userId": str(self._user_id),
				"ticketId": str(self._ticket_id) if self._ticket_id else "",
			},
		)
		
		return sandbox, False  # New sandbox
	
	async def _mount_r2_buckets(self) -> None:
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
		await self._upload_mount_scripts()
		
		try:
			env_vars = f"S3_ENDPOINT_URL='{endpoint}' S3_ACCESS_KEY_ID='{access_key}' S3_ACCESS_SECRET='{secret}' S3_REGION='{region}'"
			result = await self._sandbox.commands.run(
				f"{env_vars} sudo -E bash /tmp/create_rclone_config.sh",
				timeout=180
			)
			if result.exit_code != 0:
				raise RuntimeError(f"Failed to create rclone config (exit {result.exit_code}): {result.stderr or result.stdout}")
		except Exception as e:
			raise
		
		# Mount thread workspace (critical - must succeed)
		mount_cmd = f'bash /tmp/mount_s3_path.sh "{bucket}" "threads/{self._thread_id}" "{self._r2_workspace}" "/tmp/rclone-thread.log"'
		try:
			result = await self._sandbox.commands.run(mount_cmd, timeout=300)
			if result.exit_code != 0:
				# Show rclone log if available
				log_result = await self._sandbox.commands.run(
					"tail -50 /tmp/rclone-thread.log 2>&1 || echo 'No log file'",
					timeout=500
				)
				raise RuntimeError(f"Failed to mount thread workspace (exit {result.exit_code}): {result.stderr or result.stdout}")
		except Exception as e:
			try:
				log_result = await self._sandbox.commands.run(
					"tail -50 /tmp/rclone-thread.log 2>&1 || echo 'No log file'",
					timeout=500
				)
			except:
				pass
			raise RuntimeError(f"Thread mount timed out - check rclone configuration and S3 connectivity")
		
		try:
			result = await self._sandbox.commands.run(
				f'bash /tmp/mount_s3_path.sh "{bucket}" "skills/system" "/mnt/r2/skills/system" "/tmp/rclone-skills-system.log"',
					timeout=500
			)
			if result.exit_code != 0:
				raise RuntimeError(f"Failed to mount system skills (exit {result.exit_code})")
		except Exception as e:
			raise
		try:
			result = await self._sandbox.commands.run(
				f'bash /tmp/mount_s3_path.sh "{bucket}" "skills/{self._user_id}" "/mnt/r2/skills/{self._user_id}" "/tmp/rclone-skills-user.log"',
					timeout=500
			)
			if result.exit_code != 0:
				raise RuntimeError(f"Failed to mount user skills (exit {result.exit_code})")
		except Exception as e:
			raise
		
		# Mount ticket if exists (optional)
		if self._ticket_id:
			try:
				result = await self._sandbox.commands.run(
					f'bash /tmp/mount_s3_path.sh "{bucket}" "threads/{self._ticket_id}" "/mnt/r2/threads/{self._ticket_id}" "/tmp/rclone-ticket.log"',
						timeout=500
				)
				if result.exit_code != 0:
					raise RuntimeError(f"Failed to mount ticket workspace (exit {result.exit_code})")
			except Exception as e:
				pass
	
	async def _upload_mount_scripts(self) -> None:
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
			await self._sandbox.files.write("/tmp/create_rclone_config.sh", config_script)
			
			await self._sandbox.files.write("/tmp/mount_s3_path.sh", mount_script)
		except Exception as e:
			raise
		
		# Verify files exist
		verify_result = await self._sandbox.commands.run("ls -la /tmp/*.sh", timeout=500)
		
		# Make scripts executable
		chmod_result = await self._sandbox.commands.run("chmod +x /tmp/create_rclone_config.sh /tmp/mount_s3_path.sh", timeout=500)
		if chmod_result.exit_code != 0:
			raise RuntimeError(f"Failed to make scripts executable: {chmod_result.stderr}")
		
	
	async def _initialize_workspace(self) -> None:
		"""Set up local workspace with venvs from R2 dependency files."""
		
		# Ensure local workspace directory exists
		await self._sandbox.commands.run(f"mkdir -p {self._local_workspace}", timeout=500)
		
		# Python setup
		pyproject_r2 = f"{self._r2_workspace}/pyproject.toml"
		uvlock_r2 = f"{self._r2_workspace}/uv.lock"
		pyproject_exists = await self._sandbox.files.exists(pyproject_r2)
		
		if not pyproject_exists:
			# Load pyproject.toml template from resources
			resources_dir = os.path.join(os.path.dirname(__file__), "e2b_sandbox", "resources")
			pyproject_template_path = os.path.join(resources_dir, "pyproject.toml")
			
			with open(pyproject_template_path, "r") as f:
				pyproject_content = f.read()
			
			await self._sandbox.files.write(pyproject_r2, pyproject_content)
		else:
			pass
		# ALWAYS copy pyproject.toml from R2 to local workspace
		copy_cmd = f"cp {pyproject_r2} {self._local_workspace}/pyproject.toml"
		
		# Also copy uv.lock if it exists
		uvlock_exists = await self._sandbox.files.exists(uvlock_r2)
		if uvlock_exists:
			copy_cmd += f" && cp {uvlock_r2} {self._local_workspace}/uv.lock"
		
		# Execute the copy command first
		result = await self._sandbox.commands.run(copy_cmd, timeout=500)
		if result.exit_code != 0:
			raise RuntimeError(f"Failed to copy Python dependency files: {result.stderr}")
		
		# Now validate uv.lock if it was copied (files are now in place)
		if uvlock_exists:
			validate_result = await self._sandbox.commands.run(
				f"cd {self._local_workspace} && uv sync --dry-run 2>&1",
				timeout=500
			)
			if validate_result.exit_code != 0 or "Failed to parse" in validate_result.stderr or "TOML parse error" in validate_result.stderr:
				# Remove corrupted lockfile from both local and R2
				await self._sandbox.commands.run(
					f"rm -f {self._local_workspace}/uv.lock {uvlock_r2}",
					timeout=500
				)
				uvlock_exists = False  # Treat as if it doesn't exist

		# Create Python venv locally and sync (don't install workspace as package)
		result = await self._sandbox.commands.run(
			f"cd {self._local_workspace} && uv venv && uv sync --no-install-project",
			timeout=900
		)
		if result.exit_code != 0:
			raise RuntimeError(f"Python venv setup failed: {result.stderr}")
		
		# Node setup
		package_r2 = f"{self._r2_workspace}/package.json"
		bunlock_r2 = f"{self._r2_workspace}/bun.lockb"
		package_exists = await self._sandbox.files.exists(package_r2)
		
		if not package_exists:
			# Load package.json template from resources
			resources_dir = os.path.join(os.path.dirname(__file__), "e2b_sandbox", "resources")
			package_template_path = os.path.join(resources_dir, "package.json")
			
			with open(package_template_path, "r") as f:
				package_content = f.read()
			await self._sandbox.files.write(package_r2, package_content)
		else:
			pass
			# ALWAYS copy dependency files from R2 to local (they may have been updated by previous sandbox)
		copy_cmd = f"cp {package_r2} {self._local_workspace}/package.json"
		
		# Also copy bun.lockb if it exists
		bunlock_exists = await self._sandbox.files.exists(bunlock_r2)
		if bunlock_exists:
			copy_cmd += f" && cp {bunlock_r2} {self._local_workspace}/bun.lockb"
			print(f"[Bun] ✓ Found existing bun.lockb on R2", flush=True)
		
		result = await self._sandbox.commands.run(copy_cmd, timeout=500)
		if result.exit_code != 0:
			raise RuntimeError(f"Failed to copy Node dependency files: {result.stderr}")

		result = await self._sandbox.commands.run(
			f"cd {self._local_workspace} && bun install",
			timeout=300
		)
		if result.exit_code != 0:
			raise RuntimeError(f"Bun install failed: {result.stderr}")
	
	async def _setup_skill_directories(self) -> None:
		"""Ensure R2 skill source directories exist (bwrap will handle bind-mounts).
		
		We only ensure the SOURCE directories on R2 exist:
		- /mnt/r2/skills/system (should already exist from mount)
		- /mnt/r2/skills/{user_id} (may need creation)
		
		Bwrap will automatically create /.solven/skills/ structure when it bind-mounts:
		- --ro-bind /mnt/r2/skills/system /.solven/skills/system
		- --bind /mnt/r2/skills/{user_id} /.solven/skills/user
		"""
		
		# Ensure user skills directory exists on R2 (for when agent creates new skills)
		user_skills_path = f"/mnt/r2/skills/{self._user_id}"
		user_skills_exists = await self._sandbox.files.exists(user_skills_path)
		
		if not user_skills_exists:
			await self._sandbox.commands.run(f"mkdir -p {user_skills_path}", timeout=500)
		else:
			pass
		# Verify R2 skill mounts have content
		system_check = await self._sandbox.commands.run(
			f"ls /mnt/r2/skills/system 2>&1 | head -5 || echo 'Empty or not found'",
			timeout=500
		)
		user_check = await self._sandbox.commands.run(
			f"ls {user_skills_path} 2>&1 | head -5 || echo 'Empty or not found'",
			timeout=500
		)
		
		system_count = len([l for l in system_check.stdout.strip().split('\n') if l and 'Empty' not in l])
		user_count = len([l for l in user_check.stdout.strip().split('\n') if l and 'Empty' not in l])

		# Ensure ticket directory exists on R2 if ticket exists
		if self._ticket_id:
			ticket_path = f"/mnt/r2/threads/{self._ticket_id}"
			ticket_exists = await self._sandbox.files.exists(ticket_path)
			if not ticket_exists:
				await self._sandbox.commands.run(f"mkdir -p {ticket_path}", timeout=500)
			else:
				pass
	
	async def _run_isolated(self, command: str, timeout: int = 30000):
		"""Run command with bwrap isolation.
		
		Bind-mounts R2 skills directly to /.solven/skills/ so:
		- Agent can read skills from /.solven/skills/system/ and /.solven/skills/user/
		- Agent modifications to user skills persist to /mnt/r2/skills/{user_id}
		- User skills are shared across all workspaces for the same user
		"""
		import shlex
		
		# Build bwrap command
		bwrap_cmd = [
			"bwrap",
			
			# Mount R2 workspace as / (writable)
			"--bind", self._r2_workspace, "/",
			
			# Mount local venvs into workspace (writable)
			"--bind", f"{self._local_workspace}/.venv", "/.venv",
			"--bind", f"{self._local_workspace}/node_modules", "/node_modules",
			
			# Bind-mount R2 skills directly to /.solven/skills/
			# System skills (read-only)
			"--ro-bind", "/mnt/r2/skills/system", "/.solven/skills/system",
			# User skills (writable - modifications persist to R2!)
			"--bind", f"/mnt/r2/skills/{self._user_id}", "/.solven/skills/user",
		]
		
		# Ticket (read-only) if exists
		if self._ticket_id:
			bwrap_cmd.extend([
				"--ro-bind", f"/mnt/r2/threads/{self._ticket_id}", "/.ticket",
			])
		
		# Continue with system binds
		bwrap_cmd.extend([
			# System binds (read-only)
			"--ro-bind", "/usr", "/usr",
			"--ro-bind", "/lib", "/lib",
			"--ro-bind", "/lib64", "/lib64",
			"--ro-bind", "/bin", "/bin",
			"--ro-bind", "/etc", "/etc",
			
			# System resources
			"--proc", "/proc",
			"--dev", "/dev",
			
			# Cache directories (tmpfs to avoid FUSE issues)
			"--tmpfs", "/.cache",
			"--tmpfs", "/.local",
			
			# Working directory
			"--chdir", "/",
			
			# Environment
			"--setenv", "HOME", "/",
			"--setenv", "PATH", "/.venv/bin:/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
			"--setenv", "PYTHONUNBUFFERED", "1",
			"--setenv", "PYTHONDONTWRITEBYTECODE", "1",
			"--setenv", "NODE_ENV", "development",
			"--setenv", "UV_CACHE_DIR", "/.cache/uv",
			"--setenv", "BUN_INSTALL_CACHE_DIR", "/.cache/bun",
			
			# Command
			"/bin/bash", "-c", command
		])
		
		cmd_str = " ".join(shlex.quote(arg) for arg in bwrap_cmd)
		return await self._sandbox.commands.run(cmd_str, timeout=timeout)
	
	async def _filter_unwanted_commands(self, command: str) -> Optional[str]:
		"""Block dangerous commands."""
		unwanted = {
			r"\buv\s+init\b": "Error: Use 'uv add <package>' to add packages (project already initialized)",
			r"\bbun\s+init\b": "Error: Use 'bun add <package>' to add packages (project already initialized)",
			r"\bsudo\b": "Error: sudo is not allowed in isolated environment",
			r"\bapt-get\b": "Error: apt-get is not allowed (system packages pre-installed)",
			r"\bapt\b": "Error: apt is not allowed (system packages pre-installed)",
			r"\bpip\b": "Error: Usa 'uv add <package>' en lugar de pip",
			r"\bnpm\b": "Error: Usa 'bun add <package>' en lugar de npm",
		}
		
		for pattern, message in unwanted.items():
			if re.search(pattern, command, re.IGNORECASE):
				return message
		return None
	
	async def aexecute(self, command: str) -> ExecuteResponse:
		"""Execute command and sync deps back to R2 if changed."""
		await self._ensure_initialized()
		
		# Filter unwanted commands
		if error_msg := await self._filter_unwanted_commands(command):
			return ExecuteResponse(
				output=error_msg,
				exit_code=1,
				truncated=False
			)
		
		# Stream status for installation/execution commands
		if "uv add" in command or "bun add" in command:
			self._writer("📦 Instalando dependencia...")
		elif "uv run" in command or "bun run" in command or "python" in command:
			self._writer("▶️ Ejecutando comando...")
		
		# Run command in isolated environment
		try:
			result = await self._run_isolated(command, timeout=500)
		except Exception as e:
			return ExecuteResponse(
				output=f"Error executing command: {str(e)}",
				exit_code=1,
				truncated=False
			)
		
		# Flush filesystem changes to ensure FUSE mounts see them
		try:
			await self._sandbox.commands.run("sync", timeout=500)
		except:
			pass  # Sync failures are not critical
		
		# If command modified dependencies, sync back to R2
		if "uv add" in command or "uv remove" in command:
			try:
				await self._sandbox.commands.run(
					f"cp {self._local_workspace}/pyproject.toml {self._r2_workspace}/pyproject.toml && "
					f"cp {self._local_workspace}/uv.lock {self._r2_workspace}/uv.lock 2>/dev/null || true",
					timeout=500
				)
			except:
				pass  # Sync failures are not critical
		
		if "bun add" in command or "bun remove" in command:
			try:
				await self._sandbox.commands.run(
					f"cp {self._local_workspace}/package.json {self._r2_workspace}/package.json && "
					f"cp {self._local_workspace}/bun.lockb {self._r2_workspace}/bun.lockb 2>/dev/null || true",
					timeout=500
				)
			except:
				pass  # Sync failures are not critical
		
		return ExecuteResponse(
			output=result.stdout + result.stderr,
			exit_code=result.exit_code,
			truncated=False
		)
	
	async def aread(self, path: str, offset: int = 0, limit: int = 2000) -> str:
		"""Read file directly from R2 workspace with line numbers."""
		await self._ensure_initialized()
		
		# Construct full R2 path
		full_path = f"{self._r2_workspace}/{path.lstrip('/')}"
		
		# Read file
		try:
			content_bytes = await self._sandbox.files.read(full_path)
			content = content_bytes.decode('utf-8') if isinstance(content_bytes, bytes) else content_bytes
		except Exception as e:
			return f"Error reading {path}: {str(e)}"
		
		# Apply pagination and add line numbers
		lines = content.split('\n')
		
		if offset > 0 or (limit > 0 and limit != 2000):
			start = offset
			end = offset + limit if limit > 0 else len(lines)
			lines = lines[start:end]
		elif limit == 2000 and len(lines) > 2000:
			lines = lines[offset:offset + 2000]
		
		# Add line numbers
		numbered = [f"{i+offset+1:6d}|{line}" for i, line in enumerate(lines)]
		return '\n'.join(numbered)
	
	async def awrite(self, path: str, content: str) -> WriteResult:
		"""Write file directly to R2 workspace (auto-creates parent directories)."""
		await self._ensure_initialized()
		
		# Construct full R2 path
		full_path = f"{self._r2_workspace}/{path.lstrip('/')}"
		
		# Check if file exists (to prevent overwriting)
		file_exists = await self._sandbox.files.exists(full_path)
		if file_exists:
			return WriteResult(
				error=f"File '{path}' already exists. Use edit to modify existing files.",
				path=None,
				files_update=None
			)
		
		# Write file (automatically creates parent directories)
		try:
			await self._sandbox.files.write(full_path, content)
			return WriteResult(error=None, path=path, files_update=None)
		except Exception as e:
			return WriteResult(
				error=f"Write failed: {str(e)}",
				path=None,
				files_update=None
			)
	
	async def upload_file(self, destination_path: str, source_file_path: str) -> WriteResult:
		"""
		Upload a file from the local filesystem to the sandbox workspace.
		Uses bwrap path resolution to handle special paths like /.solven/skills/user/
		
		Args:
			destination_path: Destination path in workspace (e.g., "/documents/file.pdf" or "/.solven/skills/user/my_skill.py")
			source_file_path: Local file path to upload (e.g., "/tmp/upload_abc123.pdf")
		
		Returns:
			WriteResult with success/error status
		"""
		await self._ensure_initialized()
		
		self._writer(f"📤 Subiendo archivo a {destination_path}...")
		
		# Normalize the destination path
		dest_path_normalized = f"/{destination_path.lstrip('/')}"
		
		# Resolve bwrap path mappings to actual R2 paths
		# Check if path is in a special bwrap-bound location
		if dest_path_normalized.startswith("/.solven/skills/system/"):
			# System skills are read-only, cannot upload here
			error_msg = "Cannot upload to system skills directory (read-only)"
			return WriteResult(
				error=error_msg,
				path=None,
				files_update=None
			)
		elif dest_path_normalized.startswith("/.solven/skills/user/"):
			# User skills map to /mnt/r2/skills/{user_id}/
			relative_path = dest_path_normalized.replace("/.solven/skills/user/", "")
			full_dest_path = f"/mnt/r2/skills/{self._user_id}/{relative_path}"
		elif dest_path_normalized.startswith("/.ticket/"):
			# Ticket files are read-only
			error_msg = "Cannot upload to ticket directory (read-only)"
			return WriteResult(
				error=error_msg,
				path=None,
				files_update=None
			)
		else:
			# Regular workspace files map to R2 workspace
			full_dest_path = f"{self._r2_workspace}{dest_path_normalized}"
		
		try:
			# Read the local file
			with open(source_file_path, 'rb') as f:
				file_content = f.read()
			
			# Write to sandbox (respects bwrap path mappings)
			await self._sandbox.files.write(full_dest_path, file_content)
			
			self._writer(f"✅ Archivo subido exitosamente")
			
			return WriteResult(error=None, path=destination_path, files_update=None)
			
		except FileNotFoundError:
			error_msg = f"Source file not found: {source_file_path}"
			return WriteResult(
				error=error_msg,
				path=None,
				files_update=None
			)
		except Exception as e:
			error_msg = f"Upload failed: {str(e)}"
			return WriteResult(
				error=error_msg,
				path=None,
				files_update=None
			)
	
	async def aedit(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
		"""Edit file in R2 workspace."""
		await self._ensure_initialized()
		
		# Construct full R2 path
		full_path = f"{self._r2_workspace}/{path.lstrip('/')}"
		
		# Check if file exists
		file_exists = await self._sandbox.files.exists(full_path)
		if not file_exists:
			return EditResult(
				error=f"File not found: {path}",
				path=None,
				files_update=None,
				occurrences=0
			)
		
		# Read current content
		try:
			content_bytes = await self._sandbox.files.read(full_path)
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
		
		# Replace
		if replace_all:
			new_content = content.replace(old_string, new_string)
		else:
			new_content = content.replace(old_string, new_string, 1)
		
		# Write back
		try:
			await self._sandbox.files.write(full_path, new_content)
			return EditResult(error=None, path=path, files_update=None, occurrences=occurrences)
		except Exception as e:
			return EditResult(
				error=f"Write failed: {str(e)}",
				path=None,
				files_update=None,
				occurrences=0
			)
	
	async def als_info(self, path: str = "/") -> list[FileInfo]:
		"""List directory contents using bwrap."""
		await self._ensure_initialized()
		
		self._writer(f"📂 Listando archivos...")
		
		import shlex
		normalized_path = f"/{path.lstrip('/')}"
		
		# List directory contents - don't exit with error if directory doesn't exist
		list_cmd = f"""
if [ ! -d {shlex.quote(normalized_path)} ]; then
	echo "__DIR_NOT_EXIST__"
	exit 0
fi
cd {shlex.quote(normalized_path)} || exit 0
ls -A1 | while IFS= read -r file; do
	stat -c '%n|%s|%Y' "$file" 2>/dev/null || true
done
"""
			
		try:
			result = await self._run_isolated(list_cmd, timeout=500)
		except Exception as e:
			return []
		
		# Check if directory doesn't exist (normal case, not an error)
		if "__DIR_NOT_EXIST__" in result.stdout:
			return []
		
		# Parse output (format: filename|size|timestamp)
		files = []
		for line in result.stdout.strip().split('\n'):
			if not line or '|' not in line:
				continue
			parts = line.split('|', 2)
			if len(parts) >= 2:
				filename = parts[0]
			
			# Skip system directories when listing root
			if normalized_path == "/" and filename in self._exclude_dirs:
				continue
			
			if normalized_path == "/":
					full_path = f"/{filename}"
			else:
				full_path = f"{normalized_path}/{filename}".replace("//", "/")
				
			files.append(FileInfo(
					path=full_path,
					size=int(parts[1]) if parts[1].isdigit() else 0,
					modified=parts[2] if len(parts) > 2 else None
				))
			
		return files
	
	async def aglob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
		"""Find files matching glob pattern using bash globstar in bwrap."""
		await self._ensure_initialized()
		import shlex
		normalized_path = f"/{path.lstrip('/')}"
		
		# Build exclusion pattern for bash
		exclusion_checks = " && ".join([f'[[ "$file" != {exc}/* ]]' for exc in self._exclude_dirs])
		
		# Use bash globstar - fastest and most idiomatic
		# Supports **, *, ?, character classes, etc.
		glob_cmd = f"""
cd {shlex.quote(normalized_path)} 2>/dev/null || exit 0
shopt -s globstar nullglob
for file in {pattern}; do
	if [ -e "$file" ] && {exclusion_checks}; then
		stat -c '%n|%s|%Y' "$file" 2>/dev/null || true
    fi
done
"""
			
		try:
			result = await self._run_isolated(glob_cmd, timeout=500)
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
			# Make path absolute
			if not filename.startswith('/'):
				if normalized_path == "/":
					full_path = f"/{filename}"
				else:
					full_path = f"{normalized_path}/{filename}".replace("//", "/")
			else:
				full_path = filename
			
			files.append(FileInfo(
				path=full_path,
					size=int(parts[1]) if parts[1].isdigit() else 0,
					modified=parts[2] if len(parts) > 2 else None
				))
			
		return files
	
	async def agrep_raw(self, pattern: str, path: str | None = None, glob: str | None = None) -> list[GrepMatch] | str:
		"""Search for pattern using ripgrep in bwrap."""
		await self._ensure_initialized()
		
		self._writer(f"🔍 Buscando '{pattern}'...")
		
		import shlex
			
		# Normalize search path
		search_path = f"/{path.lstrip('/')}" if path else "/"
		
		# Build ripgrep command with directory exclusions
		# rg is much faster than grep and has native directory exclusion
		rg_parts = ["rg", "--no-config", "--no-heading", "--with-filename", "--line-number"]
		
		# Add directory exclusions using negated globs (very fast, applied during traversal)
		for exc_dir in self._exclude_dirs:
			rg_parts.extend(["-g", f"!{exc_dir}/**"])
		
		# Add glob filter if provided (e.g., -g '*.txt')
		if glob:
			rg_parts.extend(["-g", glob])
		
		# Add pattern and search path
		rg_parts.extend([shlex.quote(pattern), shlex.quote(search_path)])
		
		rg_cmd = " ".join(rg_parts) + " 2>/dev/null || true"
		
		try:
			result = await self._run_isolated(rg_cmd, timeout=500)  # rg is much faster
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
		
		print(f"[agrep_raw] Found {len(matches)} matches", flush=True)
		return matches
	
	async def load_skills_frontmatter(self) -> str:
		"""Load all skills frontmatter (system + user)."""
		await self._ensure_initialized()
		
		print(f"[load_skills_frontmatter] Starting skill loading for user {self._user_id}", flush=True)
		frontmatters = []
		
		# System skills - read from the actual R2 mount point
		system_skills_path = "/mnt/r2/skills/system"
		system_skills_exists = await self._sandbox.files.exists(system_skills_path)
		
		if system_skills_exists:
			result = await self._sandbox.commands.run(
				f"find {system_skills_path} -name '*.md' -type f",
				timeout=500
			)
			if result.exit_code == 0:
				skill_files = [f for f in result.stdout.strip().split('\n') if f]
				for skill_file in skill_files:

					content = await self._sandbox.files.read(skill_file)
					content_str = content.decode('utf-8') if isinstance(content, bytes) else content
					fm = _parse_skillmd_frontmatter(content_str)
					if fm:
						frontmatters.append(fm)
		
		# User skills - read from the actual R2 mount point
		user_skills_path = f"/mnt/r2/skills/{self._user_id}"
		user_skills_exists = await self._sandbox.files.exists(user_skills_path)
		
		if user_skills_exists:
			result = await self._sandbox.commands.run(
				f"find {user_skills_path} -name '*.md' -type f",
				timeout=500
			)
			if result.exit_code == 0:
				skill_files = [f for f in result.stdout.strip().split('\n') if f]
				for skill_file in skill_files:
					content = await self._sandbox.files.read(skill_file)
					content_str = content.decode('utf-8') if isinstance(content, bytes) else content
					fm = _parse_skillmd_frontmatter(content_str)
					if fm:
						frontmatters.append(fm)
		
		return "\n\n".join(frontmatters)
	
	async def get_skill_content(self, skill_name: str) -> Optional[str]:
		"""
		Get the SKILL.md content for a skill using bwrap isolation.
		
		This ensures the skill is accessible from within the isolated environment
		where agent commands actually run, and that all resources are available.
		
		Reads from .solven/skills/ directory which contains symlinks:
		- /.solven/skills/system/ → system-wide skills from R2
		- /.solven/skills/user/ → user-specific skills from R2
		
		Args:
			skill_name: Name of the skill (e.g., 'compraventa-de-viviendas')
			
		Returns:
			SKILL.md content as string if skill exists and is accessible, None otherwise
		"""
		await self._ensure_initialized()
		
		# Check system skills first, then user skills
		# Use paths as they appear in bwrap (/ = R2 workspace root)
		skill_paths = [
			("system", f"/.solven/skills/system/{skill_name}/SKILL.md"),
			("user", f"/.solven/skills/user/{skill_name}/SKILL.md")
		]
		
		for source, skill_path in skill_paths:
			try:
				import shlex
				# Use bwrap to read the skill, ensuring it's accessible in the isolated environment
				read_cmd = f"cat {shlex.quote(skill_path)} 2>/dev/null"
				result = await self._run_isolated(read_cmd, timeout=500)
				
				if result.exit_code != 0 or not result.stdout.strip():
					# Skill not found in this location, try next
					continue
				
				content = result.stdout
				return content
				
			except Exception as e:
				# Skill not found or not accessible in this location, try next
				continue
		
		return None
	
	async def aclose(self) -> None:
		"""Close sandbox and cleanup."""
		if self._sandbox:
			try:
				await self._sandbox.kill()
			except Exception as e:
				pass

