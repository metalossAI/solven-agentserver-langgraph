"""
E2B Sandbox backend for DeepAgents using Cloudflare R2.
Implements the BackendProtocol for filesystem operations in an isolated sandbox environment.

ARCHITECTURE OVERVIEW:
======================

Virtual Environment Strategy:
- **System-wide venvs**: /opt/solven/{python,node} on E2B sandbox (local filesystem)
- **Workspace**: /mnt/r2/threads/{id} on R2 FUSE mount (user files only)

Benefits:
- Fast, reliable (no FUSE issues with venvs)
- Reproducible (fresh each sandbox)
- Agents can add/remove packages with `uv add` and `bun add`
- No venv state mixed with user files on R2

Agent commands like `python`, `bun`, `uv`, etc. use the system venvs automatically
via PATH configuration in bwrap.
"""
import os
import re
import time
from datetime import datetime
from typing import Optional
from langgraph.config import get_stream_writer
from e2b import AsyncSandbox, SandboxQuery, SandboxState
from e2b.sandbox.commands.command_handle import CommandExitException
from deepagents.backends.protocol import SandboxBackendProtocol, WriteResult, EditResult, ExecuteResponse
from deepagents.backends.utils import FileInfo, GrepMatch
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

SANDBOX_TEMPLATE="solven-sandbox-v1"

class SandboxBackend(SandboxBackendProtocol):
	"""
	E2B Sandbox backend that provides a filesystem interface for the agent.
	
	Thread Workspace Structure:
	Base path (THREAD_ROOT): /mnt/r2/{BUCKET_NAME}/threads/{thread_id}
	
	Each thread workspace is automatically configured and contains:
	- pyproject.toml     # uv project file with Python dependencies
	- .venv/             # uv-managed Python virtual environment
	- package.json       # bun project configuration
	- node_modules/      # bun-installed Node.js packages
	- .solven/           # Skills directory structure
	  └── skills/
		  ├── system/     # Symlink to system-wide skills
		  └── user/       # Symlink to user-specific skills
	- .ticket/           # Symlink to ticket workspace (if ticket exists)
	- .bashrc            # Auto-activation script for proot
	- .gitignore         # Clean repository configuration
	- .workspace_configured  # Marker file indicating successful setup
	- (all agent files)  # Agent works directly in base_path
	
	Package Management:
	- Python: uv (ultra-fast package manager, ~6x faster than pip)
	  * Uses pyproject.toml for dependency tracking
	  * Creates reproducible environments
	  * Manages venv and packages together
	- Node.js: Bun (ultra-fast runtime and package manager, ~10x faster than npm)
	  * Uses package.json for dependency tracking
	  * Fast installs and runtime execution
	
	Isolation:
	- All operations are scoped to base_path
	- Commands execute directly in base_path (same as file operations)
	- Python venv is auto-activated for all commands
	- Environment variables (HOME, PWD) set to base_path
	- Python and Node environments are isolated per thread
	- Symlinks provide efficient access to shared resources
	
	IMPORTANT - Path Consistency:
	- execute("/") runs commands in base_path/
	- ls_info("/") lists files in base_path/
	- read("/file.txt") reads from base_path/file.txt
	- All operations use base_path/ as root for consistency
	
	Workspace Configuration:
	- Configured automatically on first message (takes ~30 seconds)
	- Uses .workspace_configured marker for atomic operation
	- Subsequent messages are instant (no configuration needed)
	"""
	def __init__(self, runtime_context: AppContext):
		self._runtime_context = runtime_context
		
		# New mount structure: specific paths mounted directly
		# Base workspace path - thread root, all agent operations happen here
		self._base_path = f"/mnt/r2/threads/{self._runtime_context.thread.id}"
		# Skills path in R2 (for symlink targets)
		self._r2_skills_path = "/mnt/r2/skills"
		# Ticket path (if ticket exists) - points to ticket thread root
		self._r2_ticket_path = None
		if self._runtime_context.ticket and self._runtime_context.ticket.id:
			self._r2_ticket_path = f"/mnt/r2/tickets/{self._runtime_context.ticket.id}"

		# Lazy initialization - no blocking operations in __init__
		self._sandbox: Optional[AsyncSandbox] = None
		self._has_bwrap: bool | None = None  # Cached bubblewrap availability check
		self._user_skills_exists: bool | None = None  # Cached user skills mount check


		self._solven_shadow_dir: Optional[str] = None  # Shadow directory with symlinks for .solven
		self._initialized = False
	
	async def _get_or_create_sandbox(self) -> AsyncSandbox:
		"""
		Get existing sandbox for this thread or create a new one.
		Handles the complete sandbox lifecycle including creation with proper env vars.
		
		Returns:
			Connected AsyncSandbox instance
			
		Raises:
			RuntimeError: If sandbox creation or connection fails
		"""
		thread_id = self._runtime_context.thread.id
		user_id = self._runtime_context.user.id
		ticket_id = self._runtime_context.ticket.id if self._runtime_context.ticket else None
		
		# Check for existing sandbox for this thread
		try:
			paginator = AsyncSandbox.list(
				query=SandboxQuery(
					metadata={"threadId": thread_id},
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
						return sandbox
					except Exception as e:
						print(f"[SandboxBackend] ❌ Failed to connect to existing sandbox {sandbox_id}: {e}", flush=True)
						# Continue to create new sandbox below
			
			# No existing sandbox found, create a new one
			print(f"[SandboxBackend] No existing sandbox found, creating new sandbox for thread {thread_id}", flush=True)
			
			# Get R2 credentials from environment variables
			r2_bucket = os.getenv("S3_BUCKET_NAME", "testing")
			s3_endpoint_url = os.getenv("S3_ENDPOINT_URL")
			s3_access_key = os.getenv("S3_ACCESS_KEY_ID")
			s3_access_secret = os.getenv("S3_ACCESS_SECRET")
			s3_region = os.getenv("S3_REGION", "eu-central-1")
			
			# Build environment variables for sandbox creation
			envs = {
				"THREAD_ID": thread_id,
				"USER_ID": user_id,
				"TICKET_ID": ticket_id or "",
				"S3_BUCKET_NAME": r2_bucket,
				"S3_REGION": s3_region,
			}
			
			# Add R2 credentials only if they exist (to avoid None values)
			if s3_endpoint_url:
				envs["S3_ENDPOINT_URL"] = s3_endpoint_url
			if s3_access_key:
				envs["S3_ACCESS_KEY_ID"] = s3_access_key
			if s3_access_secret:
				envs["S3_ACCESS_SECRET"] = s3_access_secret
			
			# Create new sandbox with thread-specific environment variables
			# Using the same template name as the frontend
			sandbox_template = os.getenv("E2B_SANDBOX_TEMPLATE", "solven-sandbox-v1")
			
			print(f"[SandboxBackend] Creating sandbox with template: {sandbox_template}", flush=True)
			print(f"[SandboxBackend] Env vars: THREAD_ID={thread_id}, USER_ID={user_id}, TICKET_ID={ticket_id or ''}, S3_BUCKET_NAME={r2_bucket}", flush=True)
			
			new_sandbox = await AsyncSandbox.create(
				template=sandbox_template,
				envs=envs,
				timeout=60 * 1 * 1000,  # 1 minutes timeout
				metadata={
					"threadId": thread_id,
					"userId": user_id,
					"ticketId": ticket_id or "",
				},
			)
			
			# Note: S3 bucket mounting is handled by the template's set_start_cmd
			# which runs automatically for each sandbox instance with the provided envs
			
			sandbox_id = new_sandbox.sandbox_id
			print(f"[SandboxBackend] ✅ Created new sandbox: {sandbox_id} for thread {thread_id}", flush=True)
			
			# Small delay to ensure sandbox is fully initialized and indexed
			# Delay handled by sandbox initialization
			
			# Verify sandbox is accessible
			try:
				# Try to connect to verify it's ready
				await new_sandbox.connect()
				print(f"[SandboxBackend] ✅ Sandbox verified and ready: {sandbox_id}", flush=True)
			except Exception as verify_err:
				print(f"[SandboxBackend] ⚠️  Sandbox verification warning (non-fatal): {verify_err}", flush=True)
				# Don't fail - sandbox exists, just might need more time to be indexed
			
			# Setup rclone mounts for this sandbox
			print(f"[SandboxBackend] Setting up S3 mounts...", flush=True)
			# Temporarily set the sandbox so we can use mounting methods
			old_sandbox = self._sandbox
			self._sandbox = new_sandbox
			try:
				await self._setup_rclone_mounts()
				print(f"[SandboxBackend] ✓ S3 mounts configured", flush=True)
				
				# Configure workspace immediately (create .solven and .ticket symlinks)
				print(f"[SandboxBackend] Configuring workspace and symlinks...", flush=True)
				await self._ensure_workspace_configured()
				print(f"[SandboxBackend] ✓ Workspace configured with .solven and .ticket symlinks", flush=True)
				
				# Keep the new sandbox (don't restore old_sandbox)
				# self._sandbox is already set to new_sandbox, so we're good
			except Exception as setup_error:
				print(f"[SandboxBackend] Warning: Setup failed: {setup_error}", flush=True)
				import traceback
				print(f"[SandboxBackend] Traceback: {traceback.format_exc()}", flush=True)
				# Restore old sandbox on failure
				self._sandbox = old_sandbox
				# Re-raise to prevent using a partially configured sandbox
				raise
			
			return new_sandbox
			
		except Exception as e:
			print(f"[SandboxBackend] Error in sandbox lifecycle: {e}", flush=True)
			import traceback
			print(f"[SandboxBackend] Traceback: {traceback.format_exc()}", flush=True)
			raise RuntimeError(f"Failed to get or create sandbox for thread {thread_id}: {e}")

	def _load_system_skills(self):
		"""
		DEPRECATED: Skills are now accessed via .solven/ symlink.
		This method is kept for backward compatibility but does nothing.
		"""
		print(f"[_load_system_skills] Skills are accessed via .solven/ symlink, no explicit loading needed", flush=True)
		pass


	async def _ensure_initialized(self):
		"""Ensure the backend is initialized before operations (async, non-blocking)."""
		if self._initialized and self._sandbox:
			return
		
		# Initialize sandbox connection
		if not self._sandbox:
			self._sandbox = await self._get_or_create_sandbox()
		
		# Ensure workspace is configured before use
		if not self._initialized:
			await self._ensure_workspace_configured()
			self._initialized = True
	
	
	async def _run_command(self, command: str, timeout: int = 5000, description: str = "") -> None:
		"""Execute a command in the sandbox with error handling (async, non-blocking)."""
		try:
			result = await self._sandbox.commands.run(command, timeout=timeout)
			if result.exit_code != 0:
				error_msg = f"{description} failed" if description else "Command failed"
				raise RuntimeError(f"{error_msg}: {result.stderr or result.stdout}")
		except Exception as e:
			if description:
				raise RuntimeError(f"{description} failed: {e}")
			raise
	
	async def _setup_workspace_structure(self) -> None:
		"""Create workspace directory and .solven structure."""
		print(f"[Workspace] Creating directory structure...", flush=True)
		await self._run_command(
			f"mkdir -p {self._base_path}/.solven/skills",
			description="Creating workspace directories"
		)
	
	async def _setup_symlinks(self) -> None:
		"""Create symlinks for skills and ticket."""
		print(f"[Workspace] Setting up symlinks...", flush=True)
		
		# System skills symlink
		system_skills_path = f"{self._r2_skills_path}/system"
		if await self._sandbox.files.exists(system_skills_path):
			await self._run_command(
				f"ln -sfn {system_skills_path} {self._base_path}/.solven/skills/system",
				description="Creating system skills symlink"
			)
			print(f"[Workspace] ✓ System skills linked", flush=True)
		else:
			print(f"[Workspace] ⚠ System skills not found at {system_skills_path}", flush=True)
		
		# User skills symlink (create directory if needed)
		user_skills_path = f"{self._r2_skills_path}/{self._runtime_context.user.id}"
		await self._run_command(f"mkdir -p {user_skills_path}", description="Creating user skills directory")
		await self._run_command(
			f"ln -sfn {user_skills_path} {self._base_path}/.solven/skills/user",
			description="Creating user skills symlink"
		)
		print(f"[Workspace] ✓ User skills linked", flush=True)
		
		# Ticket symlink (if applicable)
		if self._r2_ticket_path:
			if not await self._sandbox.files.exists(self._r2_ticket_path):
				await self._run_command(f"mkdir -p {self._r2_ticket_path}", description="Creating ticket directory")
			
			await self._run_command(
				f"ln -sfn {self._r2_ticket_path} {self._base_path}/.ticket",
				description="Creating ticket symlink"
			)
			print(f"[Workspace] ✓ Ticket linked", flush=True)
	
	async def _verify_system_environments(self) -> None:
		"""Verify system-wide Python and Node environments exist (created in template)."""
		print(f"[System] Verifying environments...", flush=True)
		
		# Check Python venv
		python_check = await self._sandbox.commands.run(
			"[ -f /opt/solven/python/.venv/bin/python ] && echo 'OK' || echo 'MISSING'",
			timeout=5000
		)
		if 'OK' not in python_check.stdout:
			raise RuntimeError("System Python venv not found at /opt/solven/python/.venv - check template")
		
		# Check Node environment
		node_check = await self._sandbox.commands.run(
			"[ -d /opt/solven/node/node_modules ] && echo 'OK' || echo 'MISSING'",
			timeout=5000
		)
		if 'OK' not in node_check.stdout:
			raise RuntimeError("System Node environment not found at /opt/solven/node/node_modules - check template")
		
		print(f"[System] ✓ Python and Node ready", flush=True)
	
	async def _verify_python_environment(self) -> None:
		"""Verify Python environment is working correctly (runs inside bwrap)."""
		try:
			# Test Python execution inside bwrap
			result = await self._run_isolated("/.venv/bin/python --version", timeout=5000)
			
			if result.exit_code == 0:
				python_version = result.stdout.strip()
				print(f"[Workspace] ✓ Python check passed: {python_version}", flush=True)
			else:
				print(f"[Workspace] ⚠️  Python check failed: {result.stderr}", flush=True)
		
		except Exception as e:
			print(f"[Workspace] ⚠️  Python verification error: {e}", flush=True)
	
	def _create_workspace_files(self) -> None:
		"""Create configuration files (.bashrc, .gitignore)."""
		print(f"[Workspace] Creating configuration files...", flush=True)
		
		# .bashrc for complete environment setup in proot
		bashrc_content = """# Solven Workspace Environment Configuration
# This file is automatically sourced in proot environment

# === Environment Variables ===
export HOME=/
export USER=user
export LOGNAME=user
export PWD=/

# === Python Configuration ===
export PYTHONUNBUFFERED=1           # Immediate output, no buffering
export PYTHONDONTWRITEBYTECODE=1    # Don't create .pyc files
export MPLBACKEND=Agg               # Matplotlib headless mode
export PYTHON_VERSION=3.12
export UV_PROJECT_ENVIRONMENT=/.venv  # Tell uv where venv is

# === Node.js Configuration ===
export NODE_ENV=development

# === PATH Configuration ===
# Add venv binaries and node_modules binaries to PATH
export PATH="/.venv/bin:/node_modules/.bin:$PATH"

# === Auto-activation ===
# Activate Python virtual environment if it exists
if [ -f /.venv/bin/activate ]; then
  source /.venv/bin/activate
fi

# === Working Directory ===
# Set working directory to root (proot maps workspace to /)
cd /

# === Helper Functions ===
# Auto-install Python package if missing (use sparingly, prefer pre-installation)
py-ensure() {
  python -c "import $1" 2>/dev/null || {
    echo "📦 Installing $1..."
    uv pip install "$1"
  }
}

# === Welcome Message (only in interactive shells) ===
if [ -t 0 ]; then
  echo "Solven Workspace Environment"
  echo "Python: $(python --version 2>/dev/null || echo 'Not available')"
  echo "Node: $(node --version 2>/dev/null || echo 'Not available')"
  echo "Working directory: $(pwd)"
  echo ""
  echo "💡 Tip: Use 'uv pip install <package>' to add Python packages"
  echo "💡 Tip: Use 'bun add <package>' to add Node packages"
fi
"""
		self._sandbox.files.write(f"{self._base_path}/.bashrc", bashrc_content)
		
		# .gitignore for clean repository
		gitignore_content = """# Python
.venv/
__pycache__/
*.py[cod]
*$py.class
*.so

# Node
node_modules/
*.log

# Environment
.env
.env.local

# IDE
.vscode/
.idea/

# OS
.DS_Store
"""
		self._sandbox.files.write(f"{self._base_path}/.gitignore", gitignore_content)
		
		print(f"[Workspace] ✓ Configuration files created", flush=True)
	
	def _create_configuration_marker(self) -> None:
		"""Create .workspace_configured marker file as final step."""
		import json
		from datetime import datetime
		
		config_data = {
			"configured_at": datetime.utcnow().isoformat() + "Z",
			"thread_id": self._runtime_context.thread.id,
			"user_id": self._runtime_context.user.id,
			"ticket_id": self._runtime_context.ticket.id if self._runtime_context.ticket else None,
			"structure": "per-thread-sandbox",
			"workspace_path": self._base_path,
			"persistence": "cloudflare-r2",
			"python": {
				"manager": "uv",
				"version": "3.12",
				"venv_path": ".venv",
				"project_file": "pyproject.toml"
			},
			"node": {
				"manager": "bun",
				"package_json": "package.json"
			}
		}
		
		config_marker_path = f"{self._base_path}/.workspace_configured"
		self._sandbox.files.write(config_marker_path, json.dumps(config_data, indent=2))
		print(f"[Workspace] ✓ Configuration marker created", flush=True)
	
	
	def _flush_rclone_cache(self) -> None:
		"""
		Manually flush rclone VFS cache to ensure R2 sync.
		
		rclone with --vfs-cache-mode full caches writes.
		This sends a SIGHUP to force flush to R2.
		
		Note: Only needed if experiencing sync delays.
		"""
		try:
			# Find rclone process and send HUP to flush cache
			result = self._sandbox.commands.run(
				"pkill -HUP -f 'rclone.*mount' && echo 'flushed' || echo 'no rclone process'",
				timeout=5000
			)
			print(f"[rclone] Cache flush: {result.stdout.strip()}", flush=True)
		except Exception as e:
			print(f"[rclone] Cache flush failed: {e}", flush=True)
	
	async def _setup_rclone_mounts(self) -> None:
		"""
		Configure and mount S3 buckets using rclone for this specific sandbox.
		
		Mounts:
		- /mnt/r2/threads/{thread_id} -> s3://bucket/threads/{thread_id}
		- /mnt/r2/skills/system -> s3://bucket/skills/system
		- /mnt/r2/skills/{user_id} -> s3://bucket/skills/{user_id}
		- /mnt/r2/tickets/{ticket_id} -> s3://bucket/tickets/{ticket_id} (if ticket exists)
		
		This runs after sandbox creation with the correct environment variables.
		Uses external script files for cleaner code.
		"""
		thread_id = self._runtime_context.thread.id
		user_id = self._runtime_context.user.id
		ticket_id = self._runtime_context.ticket.id if self._runtime_context.ticket else None
		
		s3_bucket = os.getenv("S3_BUCKET_NAME", "solven-testing")
		s3_endpoint_url = os.getenv("S3_ENDPOINT_URL", "")
		s3_access_key_id = os.getenv("S3_ACCESS_KEY_ID", "")
		s3_access_secret = os.getenv("S3_ACCESS_SECRET", "")
		s3_region = os.getenv("S3_REGION", "eu-central-1")
		
		if not s3_access_key_id or not s3_access_secret:
			print(f"[rclone] ⚠️  S3 credentials not available, skipping mounts", flush=True)
			return
		
		print(f"[rclone] === Setting up S3 mounts ===", flush=True)
		print(f"[rclone] Bucket: {s3_bucket}", flush=True)
		print(f"[rclone] Endpoint: {s3_endpoint_url or 'AWS S3 (default)'}", flush=True)
		
		# Get script directory path (sandbox_backend.py is in src/, scripts are in src/e2b_sandbox/scripts/)
		src_dir = os.path.dirname(os.path.abspath(__file__))
		script_dir = os.path.join(src_dir, "e2b_sandbox", "scripts")
		
		print(f"[rclone] Reading scripts from: {script_dir}", flush=True)
		
		# Read script files
		config_script_path = os.path.join(script_dir, "create_rclone_config.sh")
		mount_script_path = os.path.join(script_dir, "mount_s3_path.sh")
		
		with open(config_script_path, "r") as f:
			config_script = f.read()
		
		with open(mount_script_path, "r") as f:
			mount_script = f.read()
		
		print(f"[rclone] Uploading scripts to sandbox...", flush=True)
		
		# Upload scripts to sandbox (async methods)
		try:
			await self._sandbox.files.write("/tmp/create_rclone_config.sh", config_script)
			print(f"[rclone]   ✓ Uploaded create_rclone_config.sh ({len(config_script)} bytes)", flush=True)
			
			await self._sandbox.files.write("/tmp/mount_s3_path.sh", mount_script)
			print(f"[rclone]   ✓ Uploaded mount_s3_path.sh ({len(mount_script)} bytes)", flush=True)
		except Exception as e:
			print(f"[rclone] ✗ Failed to upload scripts: {e}", flush=True)
			raise
		
		# Verify files exist
		verify_result = await self._sandbox.commands.run("ls -la /tmp/*.sh", timeout=5000)
		print(f"[rclone] Files in /tmp: {verify_result.stdout}", flush=True)
		
		# Make scripts executable
		chmod_result = await self._sandbox.commands.run("chmod +x /tmp/create_rclone_config.sh /tmp/mount_s3_path.sh", timeout=5000)
		if chmod_result.exit_code != 0:
			print(f"[rclone] ✗ chmod failed: {chmod_result.stderr}", flush=True)
			raise RuntimeError(f"Failed to make scripts executable: {chmod_result.stderr}")
		
		print(f"[rclone] ✓ Scripts uploaded and ready", flush=True)
		
		# Create rclone config
		print(f"[rclone] Creating rclone configuration...", flush=True)
		try:
			env_vars = f"S3_ENDPOINT_URL='{s3_endpoint_url}' S3_ACCESS_KEY_ID='{s3_access_key_id}' S3_ACCESS_SECRET='{s3_access_secret}' S3_REGION='{s3_region}'"
			result = await self._sandbox.commands.run(f"{env_vars} sudo -E bash /tmp/create_rclone_config.sh", timeout=10000)
			print(f"[rclone] Config script stdout: {result.stdout}", flush=True)
			if result.stderr:
				print(f"[rclone] Config script stderr: {result.stderr}", flush=True)
			if result.exit_code != 0:
				# Debug: check directory and file
				debug_result = await self._sandbox.commands.run("sudo ls -la /root/.config/rclone/ 2>&1 || echo 'Directory does not exist'", timeout=5000)
				print(f"[rclone] Debug - rclone dir contents: {debug_result.stdout}", flush=True)
				raise RuntimeError(f"Failed to create rclone config (exit {result.exit_code}): {result.stderr or result.stdout}")
		except Exception as e:
			print(f"[rclone] ✗ Config creation failed: {e}", flush=True)
			raise
		
		# Mount thread workspace (critical - must succeed)
		print(f"[rclone] Mounting thread workspace: {s3_bucket}/threads/{thread_id}", flush=True)
		mount_cmd = f'bash /tmp/mount_s3_path.sh "{s3_bucket}" "threads/{thread_id}" "/mnt/r2/threads/{thread_id}" "/tmp/rclone-thread.log"'
		print(f"[rclone] Running command: {mount_cmd}", flush=True)
		try:
			result = await self._sandbox.commands.run(mount_cmd, timeout=30000)
			print(f"[rclone] Thread mount exit code: {result.exit_code}", flush=True)
			print(f"[rclone] Thread mount stdout: {result.stdout}", flush=True)
			if result.stderr:
				print(f"[rclone] Thread mount stderr: {result.stderr}", flush=True)
			if result.exit_code != 0:
				# Show rclone log if available
				log_result = await self._sandbox.commands.run("tail -50 /tmp/rclone-thread.log 2>&1 || echo 'No log file'", timeout=5000)
				print(f"[rclone] rclone log tail: {log_result.stdout}", flush=True)
				raise RuntimeError(f"Failed to mount thread workspace (exit {result.exit_code}): {result.stderr or result.stdout}")
		except Exception as e:
			if "timeout" in str(e).lower():
				print(f"[rclone] ✗ Thread mount timed out", flush=True)
				# Try to get log
				try:
					log_result = await self._sandbox.commands.run("tail -50 /tmp/rclone-thread.log 2>&1 || echo 'No log file'", timeout=5000)
					print(f"[rclone] rclone log tail: {log_result.stdout}", flush=True)
				except:
					pass
				raise RuntimeError(f"Thread mount timed out - check rclone configuration and S3 connectivity")
			raise
		
		# Mount system skills
		print(f"[rclone] Mounting system skills: {s3_bucket}/skills/system", flush=True)
		try:
			result = await self._sandbox.commands.run(
				f'bash /tmp/mount_s3_path.sh "{s3_bucket}" "skills/system" "/mnt/r2/skills/system" "/tmp/rclone-skills-system.log"',
				timeout=30000
			)
			print(f"[rclone] System skills mount: {result.stdout}", flush=True)
			if result.exit_code != 0:
				print(f"[rclone] System skills stderr: {result.stderr}", flush=True)
				raise RuntimeError(f"Failed to mount system skills (exit {result.exit_code})")
		except Exception as e:
			if "timeout" in str(e).lower():
				print(f"[rclone] ✗ System skills mount timed out", flush=True)
				raise RuntimeError(f"System skills mount timed out")
			raise
		
		# Mount user skills
		print(f"[rclone] Mounting user skills: {s3_bucket}/skills/{user_id}", flush=True)
		try:
			result = await self._sandbox.commands.run(
				f'bash /tmp/mount_s3_path.sh "{s3_bucket}" "skills/{user_id}" "/mnt/r2/skills/{user_id}" "/tmp/rclone-skills-user.log"',
				timeout=30000
			)
			print(f"[rclone] User skills mount: {result.stdout}", flush=True)
			if result.exit_code != 0:
				print(f"[rclone] User skills stderr: {result.stderr}", flush=True)
				raise RuntimeError(f"Failed to mount user skills (exit {result.exit_code})")
		except Exception as e:
			if "timeout" in str(e).lower():
				print(f"[rclone] ✗ User skills mount timed out", flush=True)
				raise RuntimeError(f"User skills mount timed out")
			raise
		
		# Mount ticket workspace (optional)
		if ticket_id:
			print(f"[rclone] Mounting ticket workspace: {s3_bucket}/tickets/{ticket_id}", flush=True)
			try:
				result = await self._sandbox.commands.run(
					f'bash /tmp/mount_s3_path.sh "{s3_bucket}" "tickets/{ticket_id}" "/mnt/r2/tickets/{ticket_id}" "/tmp/rclone-ticket.log"',
					timeout=30000
				)
				print(f"[rclone] Ticket mount: {result.stdout}", flush=True)
				if result.exit_code != 0:
					print(f"[rclone] Ticket stderr: {result.stderr}", flush=True)
					raise RuntimeError(f"Failed to mount ticket workspace (exit {result.exit_code})")
			except Exception as e:
				if "timeout" in str(e).lower():
					print(f"[rclone] ✗ Ticket mount timed out", flush=True)
					raise RuntimeError(f"Ticket mount timed out")
				raise
		
		# Set permissions
		await self._sandbox.commands.run("sudo chown -R user:user /mnt/r2", timeout=5000)
		
		# Verify mounts
		print(f"[rclone] Verifying mounts...", flush=True)
		# FUSE mounts initialization handled by mount scripts
		
		verify_cmd = f"""
ps aux | grep rclone | grep -v grep || echo "[rclone] Warning: No rclone processes"
ls /mnt/r2/threads/{thread_id}/ >/dev/null 2>&1 && echo "[rclone] ✓ Thread mount OK" || echo "[rclone] ⚠ Thread mount issue"
ls /mnt/r2/skills/system/ >/dev/null 2>&1 && echo "[rclone] ✓ System skills mount OK" || echo "[rclone] ⚠ System skills issue"
"""
		result = await self._sandbox.commands.run(verify_cmd, timeout=10000)
		print(result.stdout, flush=True)
		
		print(f"[rclone] ✓ Mounts complete", flush=True)
		print(f"[rclone] Logs available at: /tmp/rclone-*.log", flush=True)
	
	async def _check_user_skills_mount(self) -> None:
		"""
		Check if user skills mount exists and cache the result (async, non-blocking).
		This avoids checking on every command execution, which can cause timeouts.
		"""
		if self._user_skills_exists is not None:
			return  # Already checked
		
		user_skills_path = f"{self._r2_skills_path}/{self._runtime_context.user.id}"
		try:
			check_user_skills = await self._sandbox.commands.run(f"test -d {user_skills_path} && echo 'exists' || echo 'missing'", timeout=5000)
			self._user_skills_exists = "exists" in check_user_skills.stdout
			if self._user_skills_exists:
				print(f"[Workspace] ✓ User skills mount found: {user_skills_path}", flush=True)
			else:
				print(f"[Workspace] ⚠️  User skills mount not found (user has no skills yet): {user_skills_path}", flush=True)
		except Exception as e:
			# If check fails (e.g., sandbox timeout), assume it doesn't exist to be safe
			print(f"[Workspace] ⚠️  Could not check user skills mount, assuming missing (non-fatal): {e}", flush=True)
			self._user_skills_exists = False
	
	async def _setup_workspace_symlinks(self) -> None:
		"""
		Create .solven and .ticket directory structure in workspace.
		
		IMPORTANT: rclone FUSE mounts don't support symlinks (S3 has no symlink concept).
		Instead of symlinks, we create empty directories and use bwrap bind-mounts
		to overlay the actual skills/ticket paths at runtime.
		
		Structure created on disk:
		  .solven/
		    skills/
		      system/    (empty directory, bwrap will bind-mount /mnt/r2/skills/system here)
		      user/      (empty directory, bwrap will bind-mount /mnt/r2/skills/{user_id} here)
		  .ticket/         (empty directory, bwrap will bind-mount /mnt/r2/tickets/{ticket_id} here)
		  tmp/             (workspace temp directory that persists between commands)
		
		At runtime, bwrap overlays the actual content:
		  bwrap --ro-bind /mnt/r2/skills/system /.solven/skills/system
		  bwrap --ro-bind /mnt/r2/skills/{user_id} /.solven/skills/user
		  bwrap --ro-bind /mnt/r2/tickets/{ticket_id} /.ticket
		"""
		print(f"[Workspace] Creating .solven, .ticket, and tmp directories...", flush=True)
		print(f"[Workspace] Base path: {self._base_path}", flush=True)
		
		# Verify base path exists and is accessible
		check_result = await self._sandbox.commands.run(f"ls -la {self._base_path}", timeout=5000)
		print(f"[Workspace] Base path contents: {check_result.stdout[:500]}", flush=True)
		
		# Create tmp directory in workspace (so /tmp inside bwrap persists) (non-blocking)
		tmp_path = f"{self._base_path}/tmp"
		tmp_result = await self._sandbox.commands.run(f"mkdir -p {tmp_path}", timeout=5000)
		if tmp_result.exit_code != 0:
			print(f"[Workspace] ✗ Failed to create tmp directory: {tmp_result.stderr}", flush=True)
			raise RuntimeError(f"Failed to create tmp directory")
		print(f"[Workspace] ✓ Created tmp directory: {tmp_path}", flush=True)
		
		# Create empty .solven directory structure
		# bwrap will bind-mount the actual skills directly here
		solven_skills_dir = f"{self._base_path}/.solven/skills"
		mkdir_result = await self._sandbox.commands.run(f"mkdir -p {solven_skills_dir}", timeout=5000)
		if mkdir_result.exit_code != 0:
			print(f"[Workspace] ✗ Failed to create .solven/skills: {mkdir_result.stderr}", flush=True)
			raise RuntimeError(f"Failed to create .solven/skills")
		print(f"[Workspace] ✓ Created .solven/skills (bwrap will bind-mount skills here)", flush=True)
		
		# Verify directories were created
		verify_result = await self._sandbox.commands.run(f"ls -la {self._base_path}/ | head -20", timeout=5000)
		print(f"[Workspace] Directory structure verification:\n{verify_result.stdout}", flush=True)
		
		# Check specifically for .solven
		solven_check = await self._sandbox.commands.run(f"test -d {self._base_path}/.solven/skills && echo 'exists' || echo 'missing'", timeout=5000)
		print(f"[Workspace] .solven/skills directory: {solven_check.stdout.strip()}", flush=True)
		
		# CRITICAL: Verify the SOURCE directories have content (outside bwrap)
		print(f"[Workspace] Verifying source mount points have content...", flush=True)
		system_skills_check = await self._sandbox.commands.run(f"ls -la /mnt/r2/skills/system/ | head -10", timeout=5000)
		print(f"[Workspace] System skills content (/mnt/r2/skills/system):\n{system_skills_check.stdout}", flush=True)
		
		if self._user_skills_exists:
			user_skills_check = await self._sandbox.commands.run(f"ls -la /mnt/r2/skills/{self._runtime_context.user.id}/ | head -10", timeout=5000)
			print(f"[Workspace] User skills content (/mnt/r2/skills/{self._runtime_context.user.id}):\n{user_skills_check.stdout}", flush=True)
	
	async def _ensure_workspace_configured(self) -> None:
		"""
		Workspace configuration for bwrap isolation (async, non-blocking).
		
		Creates:
		1. .solven/skills/ symlinks to shared skills
		2. .ticket/ symlink (if ticket exists)
		3. Python environment (/.venv with uv)
		4. Node environment (/package.json with bun)
		5. Configuration marker
		
		After configuration, workspace is ready for immediate use.
		
		Important: rclone + bwrap interaction
		---------------------------------------
		- rclone FUSE-mounts specific R2 paths (per-thread sandboxes):
		  * /mnt/r2/threads/{thread_id} -> r2:bucket/threads/{thread_id}
		  * /mnt/r2/skills/system -> r2:bucket/skills/system
		  * /mnt/r2/skills/{user_id} -> r2:bucket/skills/{user_id}
		  * /mnt/r2/tickets/{ticket_id} -> r2:bucket/tickets/{ticket_id} (if exists)
		- bwrap bind-mounts workspace as / inside sandbox
		- Changes inside bwrap write through to FUSE mount
		- rclone syncs to R2 with --vfs-write-back 1s delay
		- All setup operations MUST happen OUTSIDE bwrap (directly on FUSE mount)
		  to ensure proper directory structure creation on R2
		
		Filesystem Access Audit:
		-------------------------
		This method and related setup methods use self._sandbox.files.* directly
		because setup operations must happen outside bwrap context to ensure proper
		directory structure creation on R2. All user-facing file operations (ls_info,
		read, write, edit, glob_info, grep_raw) use bwrap via _run_isolated() for
		consistency. The only exception was load_skill() which has been refactored
		to use bwrap for consistency.
		"""
		config_marker_path = f"{self._base_path}/.workspace_configured"
		
		# Use async file check (non-blocking)
		marker_exists = await self._sandbox.files.exists(config_marker_path)
		if marker_exists:
			print(f"[Workspace] ✓ Already configured", flush=True)
			return
		
		print(f"[Workspace] First run, configuring...", flush=True)
		
		try:
			# Verify bwrap is installed (non-blocking)
			bwrap_check = await self._sandbox.commands.run("which bwrap", timeout=5000)
			if bwrap_check.exit_code != 0:
				raise RuntimeError("bwrap not found! Ensure it's installed in E2B template.")
			else:
				print(f"[Workspace] ✓ bwrap found at: {bwrap_check.stdout.strip()}", flush=True)
			
			# Verify rclone mount is accessible (non-blocking)
			mount_check = await self._sandbox.commands.run(f"mountpoint -q {self._base_path} && echo 'mounted' || echo 'not mounted'", timeout=5000)
			print(f"[Workspace] R2 mount status at {self._base_path}: {mount_check.stdout.strip()}", flush=True)
			
			# Ensure workspace directory exists on R2 mount (non-blocking)
			# This MUST happen outside bwrap, directly on the FUSE mount
			await self._sandbox.commands.run(f"mkdir -p {self._base_path}", timeout=5000)
			print(f"[Workspace] ✓ Base directory created: {self._base_path}", flush=True)
			
			# Check if workspace directory is writable (non-blocking)
			test_file = f"{self._base_path}/.write_test_{int(time.time())}"
			write_test = await self._sandbox.commands.run(f"echo 'test' > {test_file} && rm {test_file} && echo 'OK' || echo 'FAIL'", timeout=5000)
			print(f"[Workspace] Write test: {write_test.stdout.strip()}", flush=True)
			if "FAIL" in write_test.stdout:
				raise RuntimeError(f"Workspace directory {self._base_path} is not writable!")
			
			# Check if user skills mount exists (cache result to avoid checking on every command)
			await self._check_user_skills_mount()
			
			# Setup .solven and .ticket symlinks (non-blocking)
			await self._setup_workspace_symlinks()
			
			# Verify system-wide environments exist (created in template)
			await self._verify_system_environments()
			
			# Create marker (non-blocking)
			await self._sandbox.files.write(config_marker_path, f"Configured at {datetime.now().isoformat()}")
			
			print(f"[Workspace] ✓ Ready - system Python+Node configured", flush=True)
			
		except Exception as e:
			print(f"[Workspace] ✗ Failed: {e}", flush=True)
			raise RuntimeError(f"Workspace configuration failed: {e}")
	
	def _key(self, path: str) -> str:
		"""
		Convert virtual path to actual filesystem path.
		
		This ensures consistency between execute() (with proot) and file operations.
		All operations treat base_path as the root "/", matching what the agent sees in proot.
		
		Path mappings (consistent with proot view):
		- "/" -> base_path (thread workspace root)
		- "." -> base_path (current directory = workspace root)
		- "" -> base_path (empty = workspace root)
		- "/file.txt" -> base_path/file.txt
		- "file.txt" -> base_path/file.txt
		- "/.solven/skills/system/" -> skills/system/ (via symlink or direct resolution)
		- "/.ticket/file.txt" -> ticket workspace/file.txt (via symlink or direct resolution)
		
		Example consistency:
		- Agent executes: "ls /" via proot → sees files in base_path
		- Agent calls: ls_info("/") → lists files in base_path (via _key mapping)
		- Agent executes: "cat /file.txt" via proot → reads base_path/file.txt
		- Agent calls: read("/file.txt") → reads base_path/file.txt (via _key mapping)
		
		Args:
			path: Virtual path from agent's perspective (where "/" = thread workspace root)
			
		Returns:
			Actual filesystem path that corresponds to the virtual path
		"""
		# Handle current directory notation
		if path == ".":
			return self._base_path
		
		# Remove leading slash and normalize
		path = path.lstrip("/")
		
		# Handle virtual paths - resolve directly if symlinks don't work
		if path.startswith(".solven/skills/"):
			# Resolve .solven/skills/ to actual skills path
			relative_path = path[len(".solven/skills/"):]
			if relative_path:
				key = f"{self._r2_skills_path}/{relative_path}"
			else:
				key = self._r2_skills_path
		elif path.startswith(".ticket/"):
			# Resolve .ticket/ to actual ticket path
			if not self._r2_ticket_path:
				raise ValueError(f"Ticket path requested but no ticket ID available in context")
			relative_path = path[len(".ticket/"):]
			if relative_path:
				key = f"{self._r2_ticket_path}/{relative_path}"
			else:
				key = self._r2_ticket_path
		elif path.startswith(".solven/"):
			# Handle .solven/ without /skills/ (shouldn't happen, but handle gracefully)
			relative_path = path[len(".solven/"):]
			if relative_path:
				key = f"{self._base_path}/.solven/{relative_path}"
			else:
				key = f"{self._base_path}/.solven"
		else:
			# Regular path under base_path
			if not path:
				key = self._base_path
			else:
				key = f"{self._base_path}/{path}"
		
		# Security check: ensure path is within allowed directories
		allowed_prefixes = [self._base_path, self._r2_skills_path]
		if self._r2_ticket_path:
			allowed_prefixes.append(self._r2_ticket_path)
		
		if not any(key.startswith(prefix) for prefix in allowed_prefixes):
			raise ValueError(f"Path {path} maps outside allowed directories")
		
		return key
	
	def _path_from_key(self, key: str) -> str:
		"""
		Convert filesystem path back to virtual path.
		
		This is the inverse of _key() - it converts actual filesystem paths back to
		the virtual paths that the agent sees (where base_path appears as "/").
		
		This ensures that when file operations return paths (like ls_info), they return
		paths consistent with what the agent would see in proot.
		
		Maps actual paths back to virtual paths:
		- base_path/test -> "/test"
		- base_path/file.txt -> "/file.txt"
		- base_path/.solven/skills/system/skill -> "/.solven/skills/system/skill"
		- base_path/.ticket/file.txt -> "/.ticket/file.txt"
		
		Example consistency:
		- Agent executes: "ls /" via proot → sees "/file.txt"
		- ls_info("/") returns FileInfo with path="/file.txt" (via _path_from_key)
		- Both operations show the same view to the agent
		
		Args:
			key: Actual filesystem path
			
		Returns:
			Virtual path from agent's perspective (where "/" = thread workspace root)
		"""
		# All paths should be under base_path (symlinks are within base_path)
		if key.startswith(self._base_path):
			relative_path = key[len(self._base_path):].lstrip("/")
			if not relative_path:
				return "/"
			return f"/{relative_path}"
		
		# Return as-is if not under base_path (shouldn't happen, but handle gracefully)
		return key
	
	# BackendProtocol implementation

	async def _filter_unwanted_commands(self, command: str) -> str:
		"""
		Filter out unwanted commands from the command.
		"""
		import re
		
		# Normalize command: strip leading whitespace and handle compound commands
		normalized = command.strip()
		
		# Check for uv init (with any flags or arguments)
		if re.search(r'\buv\s+init\b', normalized):
			return "Error: Python environment is system-wide at /opt/solven/python. Use 'uv add <package>' to install packages. Avoid 'uv init'."
		
		# Check for bun init (with any flags or arguments)
		if re.search(r'\bbun\s+init\b', normalized):
			return "Error: Node environment is system-wide at /opt/solven/node. Use 'bun add <package>' to install packages. Avoid 'bun init'."
		
		# Check for other unwanted commands (exact start match)
		unwanted_commands = {
			"sudo": "Not allowed",
			"apt-get": "Not allowed",
			"apt-cache": "Not allowed",
			"apt-key": "Not allowed",
			"node": "Please use bun add <package> instead",
			"npm": "Please use bun add <package> instead",
			"pip": "Please use uv add <package> instead",
		}
		
		for unwanted_command in unwanted_commands.keys():
			if normalized.startswith(unwanted_command):
				return unwanted_commands[unwanted_command]
		
		return None
	
	async def aexecute(self, command: str) -> ExecuteResponse:
		"""
		Execute a command in the thread's workspace.
		
		Simple approach:
		- cd to workspace directory
		- activate Python venv
		- execute command
		
		All file operations use _key() to resolve paths relative to workspace.
		"""
		await self._ensure_initialized()
		# Filter out unwanted commands
		if message := await self._filter_unwanted_commands(command):
			return ExecuteResponse(
				output=message,
				exit_code=1,
				truncated=False
			)
		return await self._execute_simple(command)
	
	
	
	def _workspace_path(self, agent_path: str) -> str:
		"""
		Convert agent path to bubblewrap path.
		
		With proper bwrap setup, workspace IS /, so paths map directly:
		- "/" -> "/"
		- "/file.txt" -> "/file.txt"
		- "file.txt" -> "/file.txt" (relative, works in cwd)
		"""
		if not agent_path:
			return "/"
		
		# Handle relative paths
		if not agent_path.startswith('/'):
			return f"/{agent_path}"
		
		# Absolute paths work as-is
		return agent_path

	
	def _normalize_path(self, agent_path: str) -> str:
		"""
		Normalize agent path for bwrap usage.
		
		With bwrap mounting workspace as /, paths work naturally:
		- "/" → "/" (workspace root inside bwrap)
		- "/file.txt" → "/file.txt" (workspace/file.txt inside bwrap)
		- "file.txt" → "/file.txt" (normalize to absolute)
		
		Just ensure paths are absolute for consistency.
		"""
		if not agent_path or agent_path == "/":
			return "/"
		
		# Ensure path starts with /
		if not agent_path.startswith("/"):
			return f"/{agent_path}"
		
		return agent_path
	
	def _normalize_return_path(self, path: str, base_path: str = "/") -> str:
		"""
		Normalize a path returned from bwrap commands to be workspace-relative.
		
		Args:
			path: Path from command output (may be absolute or relative)
			base_path: Base path used in command (usually "/")
		
		Returns:
			Workspace-relative path (e.g., "/file.txt", "/dir/file.txt")
		"""
		if not path:
			return "/"
		
		# Remove base_path prefix if present
		if base_path and path.startswith(base_path):
			path = path[len(base_path):]
		
		# Remove any leading ./ or ./
		path = path.lstrip("./")
		
		# Ensure starts with /
		if not path.startswith("/"):
			path = f"/{path}"
		
		return path
	
	async def _run_bwrap_direct(self, bash_command: str, timeout: int = 10000):
		"""
		Run a bash command with bwrap mounting workspace as root (/).
		
		Complete isolation:
		1. Filesystem: Workspace becomes / (no path conversion needed!)
		2. Python: Workspace /.venv is the ONLY Python environment
		3. Node: Workspace /node_modules is the ONLY Node environment
		4. Cache: Fresh /.cache per execution (tmpfs)
		5. Network: Shared with host (filtered by E2B)
		
		Each workspace is fully independent:
		- workspace A: /.venv with pandas 1.0
		- workspace B: /.venv with pandas 2.0
		- No conflicts!
		
		Args:
			bash_command: The bash command to run
			timeout: Command timeout in milliseconds
			
		Returns:
			CommandResult from E2B with stdout, stderr, exit_code
		"""
		import shlex
		
		# System venvs are already in PATH via bwrap, no activation needed
		wrapped_command = f"""
# Run command with system venvs in PATH
{bash_command}
"""
		
		# Build bwrap command that mounts workspace as /
		bwrap_cmd = [
			"bwrap",
			
			# === WORKSPACE (Writable) ===
			# This becomes / inside the sandbox
			# User files only (no venvs)
			"--bind", self._base_path, "/",
			
			# === SYSTEM VENVS (Writable!) ===
			# System-wide Python and Node environments
			# Agents CAN modify these (uv add, bun add, etc.)
			"--bind", "/opt/solven", "/opt/solven",
			
			# === SKILLS & TICKETS ===
			# Bind-mount skills directly to /.solven paths
			# System skills (read-only)
			"--ro-bind", "/mnt/r2/skills/system", "/.solven/skills/system",
		]
		
		# User skills (writable)
		user_skills_path = f"/mnt/r2/skills/{self._runtime_context.user.id}"
		if self._user_skills_exists:
			bwrap_cmd.extend([
				"--bind", user_skills_path, "/.solven/skills/user",
			])
		
		# Ticket (read-only) - bind directly to /.ticket
		if self._r2_ticket_path:
			bwrap_cmd.extend([
				"--ro-bind", self._r2_ticket_path, "/.ticket",
			])
		
		# Continue with system binds
		bwrap_cmd.extend([
			# === SYSTEM (Read-Only) ===
			# Only bind essential system directories
			# System Python/Node exist but won't be used (PATH prioritizes workspace)
			"--ro-bind", "/usr", "/usr",
			"--ro-bind", "/lib", "/lib",
			"--ro-bind", "/lib64", "/lib64",
			"--ro-bind", "/bin", "/bin",
			"--ro-bind", "/sbin", "/sbin",
			"--ro-bind", "/etc", "/etc",  # DNS resolution + system config
			
			# === SYSTEM RESOURCES ===
			"--proc", "/proc",        # Process info
			"--dev", "/dev",          # Devices (needed for /dev/null, /dev/random, etc.)
			"--dev-bind", "/dev/fuse", "/dev/fuse",  # FUSE device (for rclone mounts)
			"--tmpfs", "/.cache",     # Fresh cache per execution (packages, etc.)
			
			# === WORKING DIRECTORY ===
			"--chdir", "/",           # Start in workspace root
			
			# === ENVIRONMENT: Core ===
			"--setenv", "HOME", "/",
			"--setenv", "PWD", "/",
			"--setenv", "LANG", "C.UTF-8",
			"--setenv", "LC_ALL", "C.UTF-8",
			
			# === ENVIRONMENT: Python Settings ===
			# General Python settings (non-path related)
			"--setenv", "PYTHONUNBUFFERED", "1",
			"--setenv", "PYTHONDONTWRITEBYTECODE", "1",
			"--setenv", "PYTHONHASHSEED", "0",
			"--setenv", "PYTHONIOENCODING", "utf-8",
			"--setenv", "MPLBACKEND", "Agg",
			# Note: Don't set PYTHONPATH, PYTHONHOME, or PYTHONUSERBASE here
			# The venv activation script handles those correctly
			
			# === ENVIRONMENT: Node/Bun Isolation ===
			# Point to system-wide Node environment
			"--setenv", "NODE_PATH", "/opt/solven/node/node_modules",
			"--setenv", "npm_config_prefix", "/opt/solven/node",
			"--setenv", "npm_config_cache", "/.cache/npm",
			"--setenv", "BUN_INSTALL", "/opt/solven/node",
			
			# === ENVIRONMENT: Package Manager Isolation ===
			"--setenv", "UV_PROJECT_ENVIRONMENT", "/opt/solven/python/.venv",
			"--setenv", "UV_NO_SYNC", "1",  # Don't sync with system Python
			"--setenv", "pip_no_warn_script_location", "1",
			
			# === ENVIRONMENT: Temp ===
			"--setenv", "TMPDIR", "/tmp",
			"--setenv", "TEMP", "/tmp",
			"--setenv", "TMP", "/tmp",
			
			# === ENVIRONMENT: PATH (System Venvs!) ===
			# System venv binaries take precedence
			"--setenv", "PATH", "/opt/solven/python/.venv/bin:/opt/solven/node/node_modules/.bin:/usr/local/bin:/usr/bin:/bin",
			
			# === COMMAND ===
			"/bin/bash", "-c", wrapped_command
		])
		
		# Join command for execution
		full_command = " ".join(shlex.quote(arg) for arg in bwrap_cmd)
		
		print(f"[bwrap] Isolated (workspace at /, system venvs at /opt/solven)", flush=True)
		
		# AsyncSandbox.commands.run is async, so await it directly
		return await self._sandbox.commands.run(full_command, timeout=timeout)
	
	async def ensure_python_init(self) -> bool:
		"""
		Verify system-wide Python environment exists.
		
		With the new architecture, Python lives at /opt/solven/python/.venv
		on the E2B sandbox (not per-workspace). This method just checks it exists.
		
		Returns True if system venv exists.
		"""
		try:
			# Check system venv exists (outside bwrap, on E2B filesystem)
			venv_check = await self._sandbox.commands.run(
				"[ -f /opt/solven/python/.venv/bin/python ] && echo 'exists' || echo 'missing'",
				timeout=5000
			)
			
			if 'exists' in venv_check.stdout:
				print("[Python] ✓ System Python ready", flush=True)
				return True
			else:
				print("[Python] ⚠️  System Python not configured", flush=True)
				return False
			
		except Exception as e:
			print(f"[Python] Error: {e}", flush=True)
			return False
	
	async def ensure_node_init(self) -> bool:
		"""
		Verify system-wide Node environment exists.
		
		With the new architecture, Node lives at /opt/solven/node/node_modules
		on the E2B sandbox (not per-workspace). This method just checks it exists.
		
		Returns True if system node_modules exists.
		"""
		try:
			# Check system node_modules exists (outside bwrap, on E2B filesystem)
			nm_check = await self._sandbox.commands.run(
				"[ -d /opt/solven/node/node_modules ] && echo 'exists' || echo 'missing'",
				timeout=5000
			)
			
			if 'exists' in nm_check.stdout:
				print("[Bun] ✓ System Node ready", flush=True)
				return True
			else:
				print("[Bun] ⚠️  System Node not configured", flush=True)
				return False
			
		except Exception as e:
			print(f"[Bun] Error: {e}", flush=True)
			return False
	
	def _convert_paths_to_relative_UNUSED(self, command: str) -> str:
		"""
		Convert absolute workspace paths to relative paths for SRT.
		
		This allows agents to naturally use /file.txt (meaning workspace root)
		without needing to know about the underlying workspace structure.
		
		Handles:
		- Python: img.save('/file.png') → img.save('file.png')
		- Shell: ls /dir → ls dir
		- Any: /path/to/file → path/to/file
		
		Preserves:
		- URLs: http://domain.com/path
		- System paths in allowed tools: /usr/bin/python
		"""
		import re
		
		# Skip if no absolute paths
		if '/' not in command or command.count('/') == command.count('://'):
			return command
		
		# Handle root directory references: / → .
		command = re.sub(r'(\s|^)(ls|cd|rm|mv|cp|find)\s+/($|\s|\||&|;)', r'\1\2 .\3', command)
		
		# Convert quoted paths: '/file' or "/file" → 'file'
		# But skip URLs and system paths
		def replace_quoted(match):
			path = match.group(2)
			# Keep system paths like /usr/, /bin/, /etc/
			if path.startswith(('usr/', 'bin/', 'sbin/', 'lib/', 'etc/', 'var/', 'tmp/', 'sys/', 'proc/', 'dev/')):
				return match.group(0)
			return f"{match.group(1)}{path}{match.group(3)}"
		
		command = re.sub(r"(['\"])/([\w/._ -]+)(['\"])", replace_quoted, command)
		
		# Convert function arguments: save(/file) → save(file)
		command = re.sub(r'(\(|,\s*)/([\w/._-]+)(\)|,)', r'\1\2\3', command)
		
		# Convert bare paths after whitespace
		command = re.sub(r'(\s)/([\w/._-]+)(\s|$|\||&|;)', r'\1\2\3', command)
		
		return command
	
	async def _run_isolated(self, bash_command: str, timeout: int = 10000):
		"""
		Run a bash command with bwrap isolation (async, non-blocking).
		
		Uses bwrap to mount workspace as /, so:
		- Agent writes to /file.png → workspace/file.png
		- Agent writes to /dir/file.txt → workspace/dir/file.txt
		- No path conversion needed!
		
		Args:
			bash_command: The bash command to run
			timeout: Command timeout in milliseconds
			
		Returns:
			CommandResult from E2B with stdout, stderr, exit_code
		"""
		return await self._run_bwrap_direct(bash_command, timeout)
	
	async def _execute_simple(self, command: str) -> ExecuteResponse:
		"""Execute command with bwrap isolation (workspace mounted as /)."""
		try:
			print(f"[Execute] 🔒 bwrap isolated: {command}", flush=True)
			
			# No path conversion needed! bwrap mounts workspace as /
			# So /file.txt → workspace/file.txt automatically
			result = await self._run_isolated(command, timeout=60000)
			
			print(f"[Execute] Exit code: {result.exit_code}", flush=True)
			
			# Log output for debugging
			if result.stdout:
				stdout_preview = result.stdout[:500] if len(result.stdout) > 500 else result.stdout
				print(f"[Execute] Stdout: {stdout_preview}", flush=True)
			if result.stderr:
				stderr_preview = result.stderr[:500] if len(result.stderr) > 500 else result.stderr
				print(f"[Execute] Stderr: {stderr_preview}", flush=True)
			
			# Combine stdout and stderr
			output = result.stdout
			if result.stderr:
				output = f"{output}\n\n{result.stderr}" if output else result.stderr
			
			return ExecuteResponse(
				output=output,
				exit_code=result.exit_code,
				truncated=False
			)
			
		except CommandExitException as e:
			# Extract all available info from exception
			exit_code = getattr(e, 'exit_code', 1)
			error_msg = getattr(e, 'error', str(e))
			
			print(f"[Execute] CommandExitException: exit_code={exit_code}", flush=True)
			print(f"[Execute] Error: {error_msg}", flush=True)
			
			# Check if exception has stdout/stderr
			if hasattr(e, 'stdout'):
				print(f"[Execute] Exception stdout: {e.stdout[:500]}", flush=True)
			if hasattr(e, 'stderr'):
				print(f"[Execute] Exception stderr: {e.stderr[:500]}", flush=True)
			
			# Build comprehensive error message
			output_parts = [f"Command failed with exit code {exit_code}"]
			if error_msg:
				output_parts.append(f"\nError: {error_msg}")
			if hasattr(e, 'stdout') and e.stdout:
				output_parts.append(f"\nOutput:\n{e.stdout}")
			if hasattr(e, 'stderr') and e.stderr:
				output_parts.append(f"\nError output:\n{e.stderr}")
			
			return ExecuteResponse(
				output='\n'.join(output_parts),
				exit_code=exit_code,
				truncated=False
			)
			
		except Exception as e:
			print(f"[SandboxBackend.execute] Unexpected error: {e}", flush=True)
			import traceback
			traceback.print_exc()
			
			return ExecuteResponse(
				output=f"Error executing command: {str(e)}\n\n[Command failed with exit code 1]",
				exit_code=1,
				truncated=False
			)
	
	async def als_info(self, path: str) -> list[FileInfo]:
		"""List directory contents (bwrap-isolated, workspace mounted as /)."""
		await self._ensure_initialized()
		
		try:
			import shlex
			
			# Resolve to absolute path
			abs_path = self._normalize_path(path)
			print(f"[als_info] Listing path: {path} -> {abs_path}", flush=True)
			print(f"[als_info] Base path: {self._base_path}", flush=True)
			
			
			# List files using ls -A (includes hidden files) and stat each one
			# Show errors directly so agent can learn from mistakes
			list_and_stat_cmd = f"""
if [ ! -d {shlex.quote(abs_path)} ]; then
    echo "ERROR: Directory {shlex.quote(abs_path)} does not exist" >&2
    exit 1
fi
cd {shlex.quote(abs_path)} || exit 1
count=$(ls -A1 2>/dev/null | wc -l)
if [ "$count" -eq 0 ]; then
    echo "EMPTY_DIR" >&2
fi
ls -A1 | while IFS= read -r file; do
    stat -c '%n|%s|%y' "$file" || echo "ERROR: Cannot stat $file" >&2
done
"""
			
			result = await self._run_isolated(list_and_stat_cmd, timeout=10000)
			
			# Show both stdout and stderr
			output_info = result.stdout[:500] if result.stdout else '(no files)'
			if result.stderr:
				output_info += f"\nSTDERR: {result.stderr[:200]}"
			if result.exit_code != 0:
				output_info += f"\nExit code: {result.exit_code}"
			print(f"[als_info] Listing {abs_path}: {output_info}", flush=True)
			
			# Parse output (format: filename|size|modified)
			file_infos = []
			for line in result.stdout.strip().split('\n'):
				if not line or '|' not in line:
					continue
				parts = line.split('|', 2)
				if len(parts) >= 2:
					# Parts[0] is just the filename, not full path
					# Construct the full path
					filename = parts[0]
					if abs_path == "/":
						full_path = f"/{filename}"
					else:
						full_path = f"{abs_path}/{filename}".replace("//", "/")
					
					file_infos.append(FileInfo(
						path=full_path,
						size=int(parts[1]) if parts[1].isdigit() else 0,
						modified=parts[2] if len(parts) > 2 else None
					))
			
			print(f"[als_info] Returning {len(file_infos)} files", flush=True)
			return file_infos
		except Exception as e:
			return []

	async def aread(self, path: str, offset: int = 0, limit: int = 2000) -> str:
		"""Read file content with pagination (bwrap-isolated, workspace mounted as /)."""
		await self._ensure_initialized()
		
		try:
			import shlex
			
			# Resolve to absolute path
			abs_path = self._normalize_path(path)
			
			# Read lines with sed for pagination (show errors)
			if offset > 0 or limit > 0:
				end = offset + limit if limit > 0 else ""
				read_cmd = f"sed -n '{offset+1},{end}p' {shlex.quote(abs_path)}"
			else:
				read_cmd = f"cat {shlex.quote(abs_path)}"
			
			result = await self._run_isolated(read_cmd, timeout=10000)
			
			# Show errors if any
			if result.exit_code != 0 or result.stderr:
				error_msg = f"Error reading {abs_path}\n"
				if result.stderr:
					error_msg += f"STDERR: {result.stderr}\n"
				if result.exit_code != 0:
					error_msg += f"Exit code: {result.exit_code}\n"
				return error_msg
			
			# Number lines
			lines = result.stdout.split('\n')
			numbered = [f"{i+offset+1:6d}|{line}" for i, line in enumerate(lines)]
			
			return '\n'.join(numbered)
		except Exception as e:
			return f"Exception: {str(e)}"
	
	async def aglob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
		"""Find files matching glob pattern (bwrap-isolated, workspace mounted as /).
		
		Supports glob patterns:
		- *.docx - files ending with .docx in current directory
		- **/*.docx - files ending with .docx in any subdirectory (recursive)
		- dir/*.txt - files ending with .txt in specific directory
		"""
		await self._ensure_initialized()
		
		try:
			import shlex
			
			# Resolve to absolute path
			abs_path = self._normalize_path(path)

			# DON'T quote the pattern - let bash expand it!
			bash_glob_cmd = f"""
shopt -s globstar nullglob dotglob
cd {shlex.quote(abs_path)} || exit 1
for file in {pattern}; do
    if [ -e "$file" ]; then
        stat -c '%n|%s|%y' "$file" 2>&1
    fi
done
"""
			
			result = await self._run_isolated(bash_glob_cmd, timeout=30000)
			
			# Log what we're searching
			print(f"[glob_info] Pattern: '{pattern}' in path: '{abs_path}'", flush=True)
			if result.exit_code != 0:
				print(f"[glob_info] Exit code: {result.exit_code}", flush=True)
			if result.stderr:
				print(f"[glob_info] Stderr: {result.stderr[:200]}", flush=True)
			if not result.stdout.strip():
				print(f"[glob_info] No matches found", flush=True)
			
			# Parse output (format: path|size|modified)
			file_infos = []
			for line in result.stdout.strip().split('\n'):
				if not line or '|' not in line:
					continue
				parts = line.split('|', 2)
				if len(parts) >= 2:
					# Normalize path to be workspace-relative
					# Paths from stat are relative to abs_path (where we cd'd)
					file_path = parts[0]
					# If path is relative, make it absolute relative to abs_path
					if not file_path.startswith('/'):
						file_path = f"{abs_path}/{file_path}" if abs_path != '/' else f"/{file_path}"
					normalized_path = self._normalize_return_path(file_path, abs_path)
					file_infos.append(FileInfo(
						path=normalized_path,
						size=int(parts[1]) if parts[1].isdigit() else 0,
						modified=parts[2] if len(parts) > 2 else None
					))
			
			print(f"[glob_info] Returning {len(file_infos)} file(s)", flush=True)
			return file_infos
		except Exception as e:
			return []
	
	async def agrep_raw(self, pattern: str, path: Optional[str] = None, glob: Optional[str] = None) -> list[GrepMatch] | str:
		"""Search for pattern in files using grep (bwrap-isolated, workspace mounted as /).
		
		Uses simple grep -rn command for all searches. The glob parameter is ignored.
		"""
		await self._ensure_initialized()
		
		try:
			import shlex
			
			# Normalize path (inside bwrap, / is workspace root)
			abs_path = self._normalize_path(path) if path else "/"
			
			# Always use simple grep command
			grep_cmd = f"grep -rn {shlex.quote(pattern)} {shlex.quote(abs_path)} 2>/dev/null || true"
			
			# Use longer timeout for recursive searches on FUSE mounts
			result = await self._run_isolated(grep_cmd, timeout=60000)
			
			# Parse grep output (format: file:line:text)
			matches = []
			for line in result.stdout.strip().split('\n'):
				if not line or ':' not in line:
					continue
				parts = line.split(':', 2)
				if len(parts) >= 3:
					# Normalize path to be workspace-relative
					normalized_path = self._normalize_return_path(parts[0], abs_path)
					matches.append(GrepMatch(
						path=normalized_path,
						line=int(parts[1]) if parts[1].isdigit() else 0,
						text=parts[2]
					))
			
			return matches
		except Exception as e:
			return f"Error: {str(e)}"
	
	async def awrite(self, file_path: str, content: str) -> WriteResult:
		"""Write content to a file (bwrap-isolated, workspace mounted as /)."""
		await self._ensure_initialized()
		try:
			import shlex
			import base64
			# Resolve to absolute path
			abs_path = self._normalize_path(file_path)
			
			# Create parent directories if needed (inside bwrap, / is workspace root)
			parent = os.path.dirname(abs_path)
			if parent and parent != "/":
				await self._run_isolated(f"mkdir -p {shlex.quote(parent)}", timeout=5000)
			
			# Check if file exists
			check = await self._run_isolated(f"[ -f {shlex.quote(abs_path)} ] && echo 'exists' || echo 'new'", timeout=5000)
			if 'exists' in check.stdout:
				raise RuntimeError(f"File '{file_path}' already exists. Use edit to modify.")
			
			# Write using base64 to handle all characters safely
			content_b64 = base64.b64encode(content.encode('utf-8')).decode('ascii')
			write_cmd = f"echo {shlex.quote(content_b64)} | base64 -d > {shlex.quote(abs_path)}"
			result = await self._run_isolated(write_cmd, timeout=10000)
			
			if result.exit_code != 0:
				raise RuntimeError(f"Write failed: exit code {result.exit_code}")
			
			return WriteResult(error=None, path=file_path, files_update=None)
		except Exception as e:
			return WriteResult(error=f"Error: {str(e)}", path=None, files_update=None)
	
	async def aedit(self, path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:
		"""Edit file by replacing old_string with new_string (bwrap-isolated, workspace mounted as /)."""
		await self._ensure_initialized()
		
		try:
			import shlex
			import base64
			
			# Resolve to absolute path
			abs_path = self._normalize_path(path)
			
			# Check if file exists
			check = await self._run_isolated(f"[ -f {shlex.quote(abs_path)} ] && echo 'exists' || echo 'missing'", timeout=5000)
			if 'missing' in check.stdout:
				raise FileNotFoundError(f"File not found: {path}")
			
			# Read file
			read_result = await self._run_isolated(f"cat {shlex.quote(abs_path)}", timeout=10000)
			if read_result.exit_code != 0:
				raise RuntimeError(f"Failed to read file")
			
			content = read_result.stdout
			
			# Count and replace
			occurrences = content.count(old_string)
			if occurrences == 0:
				return EditResult(error=f"String not found", path=None, files_update=None, occurrences=0)
			if not replace_all and occurrences > 1:
				return EditResult(error=f"String appears {occurrences} times. Use replace_all=True", path=None, files_update=None, occurrences=occurrences)
			
			new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
			
			# Write back
			content_b64 = base64.b64encode(new_content.encode('utf-8')).decode('ascii')
			write_result = await self._run_isolated(f"echo {shlex.quote(content_b64)} | base64 -d > {shlex.quote(abs_path)}", timeout=10000)
			
			if write_result.exit_code != 0:
				raise RuntimeError(f"Write failed")
			
			return EditResult(error=None, path=path, files_update=None, occurrences=occurrences)
		except Exception as e:
			return EditResult(error=f"Error: {str(e)}", path=None, files_update=None, occurrences=0)
	
	# Skill management methods
	
	async def get_skill_content(self, skill_name: str) -> Optional[str]:
		"""
		Get the SKILL.md content for a skill from the sandbox workspace.
		
		Reads from .solven/skills/ directory in the workspace, which contains symlinks to:
		- .solven/skills/system/ → /mnt/r2/skills/system (system-wide skills)
		- .solven/skills/user/ → /mnt/r2/skills/{user_id} (user-specific skills)
		
		This ensures we read from the same filesystem the agent sees in bwrap.
		
		Args:
			skill_name: Name of the skill to retrieve (e.g., 'compraventa-de-viviendas')
			
		Returns:
			SKILL.md content as string if skill exists, None otherwise
		"""
		await self._ensure_initialized()
		
		# Check system skills first (via .solven/skills/system symlink)
		# Then user skills (via .solven/skills/user symlink)
		# Note: We use 'user' here (not user_id) because the symlink target is named 'user'
		skill_paths = [
			("system", f"/.solven/skills/system/{skill_name}/SKILL.md"),
			("user", f"/.solven/skills/user/{skill_name}/SKILL.md")
		]
		
		for source, virtual_path in skill_paths:
			try:
				# Normalize path for bwrap (virtual path works directly in bwrap)
				normalized_path = self._normalize_path(virtual_path)
				
				# Read using bwrap for consistency with other file operations
				import shlex
				read_cmd = f"cat {shlex.quote(normalized_path)} 2>/dev/null || true"
				read_result = await self._run_isolated(read_cmd, timeout=5000)
				
				if read_result.exit_code != 0 or not read_result.stdout.strip():
					# Skill not found in this location, try next
					continue
				
				content = read_result.stdout
				
				print(f"[Skills] ✓ Loaded '{skill_name}' from {source} skills", flush=True)
				return content
				
			except Exception as e:
				# Skill not found in this location, try next
				continue
		
		# Skill not found in any location
		print(f"[Skills] ✗ Skill '{skill_name}' not found in system or user skills", flush=True)
		return None
	
	async def load_skills_frontmatter(self, category: Optional[str] = None) -> str:
		"""
		Load all skills frontmatter as concatenated YAML blocks.
		Loads from .solven/skills/ directory in the workspace, which contains symlinks to:
		- .solven/skills/system/ → /mnt/r2/skills/system (system-wide skills)
		- .solven/skills/user/ → /mnt/r2/skills/{user_id} (user-specific skills)
		
		This ensures we read from the same filesystem the agent sees in bwrap.
		
		Args:
			category: Deprecated - kept for backward compatibility, ignored.
		
		Returns:
			Concatenated YAML frontmatter blocks from all skills, each wrapped in ---
		"""
		await self._ensure_initialized()
		if not self._runtime_context.user or not self._runtime_context.user.id:
			return ""
		
		all_frontmatter_blocks = []
		
		# Load skills from .solven/ symlinks (both system and user)
		# Note: We use 'user' here (not user_id) because the symlink target is named 'user'
		# Pass virtual paths directly - bwrap will resolve symlinks correctly
		system_skills_path = "/.solven/skills/system"
		user_skills_path = "/.solven/skills/user"
		
		system_frontmatter = await self._load_skills_from_path(system_skills_path)
		all_frontmatter_blocks.extend(system_frontmatter)
		
		user_frontmatter = await self._load_skills_from_path(user_skills_path)
		all_frontmatter_blocks.extend(user_frontmatter)
		
		# Return concatenated frontmatter blocks
		return "\n".join(all_frontmatter_blocks)
	
	async def _load_skills_from_path(self, skills_path: str) -> list[str]:
		"""
		Load skill frontmatter from a given path using bwrap for consistency.
		
		Args:
			skills_path: Path to skills directory (e.g., /.solven/skills/system or /.solven/skills/user)
		
		Returns:
			List of frontmatter blocks (each wrapped in ---)
		"""
		await self._ensure_initialized()
		
		try:
			import shlex
			
			# Normalize path to workspace-relative (should be via symlink like /.solven/skills/system)
			normalized_skills_path = self._normalize_path(skills_path)
			
			# List all skill directories using bwrap (find directories only)
			find_dirs_cmd = f"find {shlex.quote(normalized_skills_path)} -maxdepth 1 -mindepth 1 -type d 2>/dev/null || true"
			result = await self._run_isolated(find_dirs_cmd, timeout=10000)
			
			if not result.stdout.strip():
				return []
			
			skill_dirs = []
			for line in result.stdout.strip().split('\n'):
				if line:
					# Normalize the path returned from find
					normalized_dir = self._normalize_return_path(line, normalized_skills_path)
					skill_dirs.append(normalized_dir)
			
			frontmatter_blocks = []
			
			# For each skill directory, try to read SKILL.md using bwrap
			for skill_dir in skill_dirs:
				# Ensure path is normalized for bwrap
				skill_md_path = f"{skill_dir}/SKILL.md"
				
				try:
					# Read SKILL.md file using bwrap
					read_cmd = f"cat {shlex.quote(skill_md_path)} 2>/dev/null || true"
					read_result = await self._run_isolated(read_cmd, timeout=5000)
					
					if read_result.exit_code != 0 or not read_result.stdout.strip():
						# Skip if file doesn't exist or is empty
						continue
					
					content = read_result.stdout
					
					# Extract frontmatter
					frontmatter = _parse_skillmd_frontmatter(content)
					
					if frontmatter:
						frontmatter_blocks.append(f"---\n{frontmatter}\n---")
				except Exception:
					# Skip skills that can't be read (file doesn't exist, etc.)
					continue
			
			return frontmatter_blocks
			
		except Exception:
			# Return empty list if path doesn't exist or can't be accessed
			return []
	
	async def load_skills(self, skills: list[str]):
		"""
		DEPRECATED: Skills are now accessed via .solven/ symlink.
		This method is kept for backward compatibility but does nothing.
		Skills are automatically available through the .solven/ symlink.
		
		Args:
			skills: List of skill names (ignored, kept for compatibility)
		"""
		print(f"[load_skills] Skills are accessed via .solven/ symlink, no explicit loading needed", flush=True)
		pass

