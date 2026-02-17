# Single-File Workspace Configuration

## Overview

A simpler approach inspired by Nix, mise, and devenv - define your entire development environment in a single `workspace.toml` file.

## Comparison: Nix vs Our Approach

| Feature | Nix | Our System | Winner |
|---------|-----|------------|--------|
| Single file config | ✅ shell.nix | ✅ workspace.toml | Tie |
| Simple syntax | ❌ Complex | ✅ Simple TOML | Us |
| Reproducible | ✅✅ Perfect | ✅ Good | Nix |
| Fast setup | ✅ Cached | ✅ Fast | Tie |
| No extra deps | ❌ Requires Nix | ✅ Built-in | Us |
| Package ecosystem | ✅✅ Huge | ✅ Standard | Nix |
| Learning curve | ❌ Steep | ✅ Easy | Us |
| Isolation | ✅ Good | ✅ Excellent (bwrap) | Us |

**Verdict:** Our system is **simpler** and **easier** to use, Nix is more **powerful** and **reproducible**.

## The Single File: `workspace.toml`

### Complete Example

```toml
[workspace]
name = "my-project"
version = "1.0"
description = "My agent workspace"

# Python setup
[python]
version = "3.12"
package_manager = "uv"

[python.packages]
pandas = ">=2.1.0"
matplotlib = ">=3.8.0"
requests = ">=2.31.0"

# Node.js setup
[nodejs]
version = "20"
package_manager = "bun"

[nodejs.packages]
axios = "^1.6.0"
docx = "^8.5.0"

# Environment
[env]
PYTHONUNBUFFERED = "1"
MY_API_KEY = "${SECRET_API_KEY}"  # From context

# Security
[security]
network_enabled = true

[security.limits]
max_disk = "1GB"
max_memory = "512MB"

# Custom tasks
[tasks]
setup = "echo 'Setting up workspace...'"
test = "pytest tests/"
```

## How It Works

### 1. Drop `workspace.toml` in R2

```bash
# Upload to R2
rclone copy workspace.toml r2:solven-testing/workspace-configs/
```

### 2. System Reads and Applies

```python
class WorkspaceConfig:
    """Single-file workspace configuration manager."""
    
    @staticmethod
    def from_toml(toml_path: str) -> dict:
        """Load workspace config from TOML file."""
        import tomli
        
        with open(toml_path, 'rb') as f:
            config = tomli.load(f)
        
        return config
    
    @staticmethod
    def apply(workspace_path: str, config: dict):
        """Apply configuration to workspace."""
        # 1. Set up Python
        python_config = config.get('python', {})
        version = python_config.get('version', '3.12')
        packages = python_config.get('packages', {})
        
        # Create pyproject.toml
        deps = [f"{pkg}{ver}" for pkg, ver in packages.items()]
        create_pyproject(workspace_path, deps, version)
        
        # Install
        run_command(f"cd {workspace_path} && uv init --python {version}")
        run_command(f"cd {workspace_path} && uv pip install .")
        
        # 2. Set up Node.js
        nodejs_config = config.get('nodejs', {})
        node_packages = nodejs_config.get('packages', {})
        
        # Create package.json
        create_package_json(workspace_path, node_packages)
        
        # Install
        run_command(f"cd {workspace_path} && bun install")
        
        # 3. Set environment variables
        env_vars = config.get('env', {})
        create_env_file(workspace_path, env_vars)
        
        # 4. Create task scripts
        tasks = config.get('tasks', {})
        create_task_scripts(workspace_path, tasks)
```

### 3. Agent Uses Ready Environment

```python
# Agent just runs code - environment is ready!
agent.execute("python script.py")
agent.execute("bun run index.js")
agent.execute("workspace-task setup")  # Run custom task
```

## Alternative: Using Nix (If We Want Maximum Reproducibility)

### Option A: Add Nix to E2B Template

```python
# In template.py
class E2BSandboxTemplate:
    def build(self):
        return (
            E2BTemplate()
            # ... existing setup ...
            
            # Add Nix
            .run_cmd("curl -L https://nixos.org/nix/install | sh -s -- --daemon", user="root")
            .run_cmd("echo 'source /nix/var/nix/profiles/default/etc/profile.d/nix-daemon.sh' >> /home/user/.bashrc")
        )
```

### Option B: Use `shell.nix` in Workspace

```nix
# shell.nix in workspace
{ pkgs ? import <nixpkgs> {} }:

pkgs.mkShell {
  buildInputs = with pkgs; [
    python312
    python312Packages.pandas
    python312Packages.matplotlib
    nodejs_20
    bun
  ];
  
  shellHook = ''
    export PYTHONUNBUFFERED=1
    export MPLBACKEND=Agg
    
    # Auto-activate
    if [ -f .venv/bin/activate ]; then
      source .venv/bin/activate
    fi
  '';
}
```

**Then in bwrap command:**
```python
# Instead of activating venv manually, use nix-shell
bwrap_cmd = [
    "bwrap",
    "--bind", self._base_path, "/",
    # ... other bindings ...
    "/bin/bash", "-c",
    f"nix-shell --run '{bash_command}'"
]
```

## Alternative: Using mise (Simpler than Nix)

### Add mise to E2B Template

```python
# In template.py
.run_cmd("curl https://mise.run | sh", user="root")
.run_cmd("echo 'eval \"$(~/.local/bin/mise activate bash)\"' >> /home/user/.bashrc")
```

### Use `.mise.toml` in Workspace

```toml
# .mise.toml
[tools]
python = "3.12"
node = "20"
bun = "latest"

[env]
PYTHONUNBUFFERED = "1"
MPLBACKEND = "Agg"

[tasks.install]
run = ["uv pip install -r requirements.txt", "bun install"]
```

**Then commands just work:**
```bash
mise exec -- python script.py  # Automatically uses correct Python version
mise run install  # Run custom task
```

## Recommendation: Hybrid Approach 🎯

**Best of both worlds:**

1. **Keep our TOML-based template system** (it works!)
2. **Add optional Nix support** (for users who want it)
3. **Add optional mise support** (simpler than Nix)

### Implementation

```python
class SandboxBackend:
    def __init__(self, runtime_context: AppContext, workspace_mode: str = "template"):
        """
        Args:
            workspace_mode: "template" | "nix" | "mise"
        """
        self._workspace_mode = workspace_mode
        
    def _ensure_workspace_configured(self):
        if self._workspace_mode == "nix":
            self._configure_with_nix()
        elif self._workspace_mode == "mise":
            self._configure_with_mise()
        else:
            self._configure_with_template()  # Default
    
    def _configure_with_nix(self):
        """Use shell.nix if present, fall back to template."""
        shell_nix = f"{self._base_path}/shell.nix"
        
        if self._sandbox.files.exists(shell_nix):
            # Use Nix environment
            print("[Workspace] Using Nix environment (shell.nix)")
            self._run_command("nix-shell --run 'echo Nix environment ready'")
        else:
            # Fall back to template
            print("[Workspace] No shell.nix found, using template")
            self._configure_with_template()
    
    def _configure_with_mise(self):
        """Use .mise.toml if present."""
        mise_toml = f"{self._base_path}/.mise.toml"
        
        if self._sandbox.files.exists(mise_toml):
            print("[Workspace] Using mise environment (.mise.toml)")
            self._run_command("mise install")
        else:
            print("[Workspace] No .mise.toml found, using template")
            self._configure_with_template()
    
    def execute(self, command: str) -> ExecuteResponse:
        """Execute command using appropriate environment manager."""
        if self._workspace_mode == "nix":
            # Wrap with nix-shell
            wrapped_command = f"nix-shell --run '{command}'"
            return self._execute_simple(wrapped_command)
        
        elif self._workspace_mode == "mise":
            # Wrap with mise exec
            wrapped_command = f"mise exec -- {command}"
            return self._execute_simple(wrapped_command)
        
        else:
            # Use our bwrap setup
            return self._execute_simple(command)
```

## Pros/Cons of Each Approach

### Our Template System (Current)
**Best for:** General use, simplicity, quick start
- ✅ Simple TOML format
- ✅ No extra dependencies
- ✅ Fast initialization
- ✅ Works now
- ❌ Less reproducible than Nix

### + Nix
**Best for:** Maximum reproducibility, complex deps
- ✅ Perfect reproducibility
- ✅ Huge package ecosystem
- ✅ Industry standard
- ❌ Complex syntax
- ❌ Requires Nix installation
- ❌ Larger template size

### + mise
**Best for:** Simple reproducibility, easy to learn
- ✅ Simple TOML format
- ✅ Fast and lightweight
- ✅ Easy to learn
- ✅ Good enough reproducibility
- ❌ Smaller ecosystem than Nix

## My Recommendation

**Start with what we have, add mise later:**

1. **Phase 1 (Now)**: Use our template system
   - Already working
   - Simple and fast
   - Good enough for most cases

2. **Phase 2 (Next)**: Add support for `workspace.toml` drop-in
   - Single file for custom workspaces
   - Still uses our system underneath
   - Easy migration path

3. **Phase 3 (Future)**: Optional mise support
   - For users who want it
   - Better version management
   - Task runner built-in
   - Falls back to templates if not present

4. **Phase 4 (Optional)**: Optional Nix support
   - For power users
   - Maximum reproducibility
   - Falls back to templates if not present

## Quick Start: Single-File Workspace

**Create `workspace.toml` in your workspace:**

```toml
[workspace]
name = "my-project"

[python.packages]
pandas = ">=2.1.0"
requests = ">=2.31.0"

[nodejs.packages]
axios = "^1.6.0"

[tasks]
setup = "echo 'Ready!'"
```

**System automatically detects and applies it!**

Simple, fast, and it just works! 🚀

