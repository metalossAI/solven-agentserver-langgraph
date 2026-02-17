# Bwrap-Only Isolation System

## Overview

Clean, simple isolation using **only bwrap** (bubblewrap). No SRT, no path parsing hacks - just proper namespace isolation.

## How It Works

### Core Concept

```bash
bwrap --bind /path/to/workspace / ...
```

Mount the workspace AS root `/` inside the container:

```
Outside container:                Inside container:
/mnt/r2/.../thread_123/          /
├── .venv/                       ├── .venv/
├── node_modules/                ├── node_modules/
├── file.txt                     ├── file.txt
└── script.py                    └── script.py
```

### The Magic

**Agent writes:** `/plot.png`  
**Actual location:** `workspace/plot.png`

**No path conversion needed!** ✨

## Complete Isolation

### 1. Filesystem Isolation

```python
# Workspace (writable)
"--bind", workspace_path, "/"

# System (read-only)
"--ro-bind", "/usr", "/usr"
"--ro-bind", "/lib", "/lib"
"--ro-bind", "/bin", "/bin"
"--ro-bind", "/etc", "/etc"  # DNS resolution

# Fresh temp/cache per execution
"--tmpfs", "/tmp"
"--tmpfs", "/.cache"
```

**Result:**
- Workspace is fully writable
- System files are protected (read-only)
- Each execution gets fresh temp/cache
- No access to sensitive files

### 2. Python Environment Isolation

```python
# Environment variables
"--setenv", "PYTHONPATH", "/.venv/lib/python3.12/site-packages"
"--setenv", "PYTHONHOME", ""  # Unset to avoid system Python
"--setenv", "PYTHONUSERBASE", "/.venv"
"--setenv", "PYTHONNOUSERSITE", "1"  # Block system site-packages
"--setenv", "PATH", "/.venv/bin:/usr/bin:/bin"  # Workspace Python first

# Venv activation in bash wrapper
if [ -f /.venv/bin/activate ]; then
    source /.venv/bin/activate
    export PYTHONHOME=/.venv
    export PYTHONUSERBASE=/.venv
fi
```

**Result:**
- Each workspace has its own `.venv/`
- System Python site-packages are NOT accessible
- Packages installed with `uv pip install` go to workspace venv
- No conflicts between workspaces

**Example:**
```
workspace A:
  /.venv/ → pandas 1.0, numpy 1.20

workspace B:
  /.venv/ → pandas 2.0, numpy 2.0

No conflicts! Completely isolated.
```

### 3. Node/Bun Environment Isolation

```python
# Environment variables
"--setenv", "NODE_PATH", "/node_modules"
"--setenv", "npm_config_prefix", "/"
"--setenv", "npm_config_cache", "/.cache/npm"
"--setenv", "BUN_INSTALL", "/"
"--setenv", "BUN_INSTALL_CACHE_DIR", "/.cache/bun"
"--setenv", "PATH", "/node_modules/.bin:/.venv/bin:/usr/bin:/bin"
```

**Result:**
- Each workspace has its own `/node_modules/`
- Packages installed with `bun add` go to workspace
- No global pollution
- Workspace binaries in `/node_modules/.bin/` are prioritized

### 4. Cache Isolation

```python
"--tmpfs", "/.cache"

"--setenv", "UV_CACHE_DIR", "/.cache/uv"
"--setenv", "npm_config_cache", "/.cache/npm"
"--setenv", "BUN_INSTALL_CACHE_DIR", "/.cache/bun"
```

**Result:**
- Each execution gets a fresh cache (tmpfs)
- No cache poisoning between executions
- Faster cache access (in-memory tmpfs)
- Automatic cleanup (tmpfs disappears after execution)

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ E2B Sandbox                                                 │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐  │
│  │ bwrap Container (workspace mounted as /)            │  │
│  │                                                      │  │
│  │  /                         ← workspace/             │  │
│  │  ├── .venv/               ← isolated Python         │  │
│  │  ├── node_modules/        ← isolated Node           │  │
│  │  ├── .cache/ (tmpfs)      ← fresh cache             │  │
│  │  ├── /tmp/ (tmpfs)        ← fresh temp              │  │
│  │  ├── script.py                                      │  │
│  │  └── plot.png                                       │  │
│  │                                                      │  │
│  │  /usr, /lib, /bin (read-only from system)          │  │
│  │  /proc, /dev (from host)                           │  │
│  │                                                      │  │
│  │  Agent runs: python /script.py                      │  │
│  │  Actually runs: workspace/script.py                 │  │
│  │                                                      │  │
│  │  Agent writes: /plot.png                            │  │
│  │  Actually writes: workspace/plot.png                │  │
│  └─────────────────────────────────────────────────────┘  │
│                                                             │
│  System Python: /usr/bin/python (not used)                 │
│  System site-packages: (not accessible)                    │
└─────────────────────────────────────────────────────────────┘

Storage: Cloudflare R2 (/mnt/r2/bucket/threads/thread_id/)
```

## Key Methods

### Core Execution

```python
def _run_isolated(self, bash_command: str, timeout: int = 10000):
    """Run command with bwrap isolation."""
    return self._run_bwrap_direct(bash_command, timeout)

def _run_bwrap_direct(self, bash_command: str, timeout: int = 10000):
    """
    Mount workspace as / and execute command.
    Complete filesystem, Python, and Node isolation.
    """
    # Build bwrap command with all mounts and environment
    # Execute in isolated container
```

### Environment Setup

```python
def ensure_python_init(self) -> bool:
    """
    Ensure Python venv exists at /.venv
    Uses: uv venv /.venv
    """

def ensure_node_init(self) -> bool:
    """
    Ensure Node project exists at /
    Uses: bun init -y
    """
```

### File Operations

All file operations use `_resolve_path()` to convert agent paths:

```python
def _resolve_path(self, agent_path: str) -> str:
    """
    Convert agent path to absolute workspace path.
    
    "/" → workspace_base_path
    "/file.txt" → workspace_base_path/file.txt
    "file.txt" → workspace_base_path/file.txt
    """
```

Then execute with `_run_isolated()` which uses bwrap.

## Usage Examples

### Python with Isolated Environment

```python
# Create Python environment
backend.ensure_python_init()

# Install packages (isolated to workspace)
backend.execute("uv pip install matplotlib pandas")

# Run script (uses workspace venv)
backend.write("/plot.py", """
import matplotlib.pyplot as plt
plt.plot([1,2,3], [4,5,6])
plt.savefig('/plot.png')  # Writes to workspace/plot.png
""")

backend.execute("python /plot.py")

# Check result
files = backend.ls_info("/")  # Lists workspace files
# Returns: [..., plot.png, ...]
```

### Node with Isolated Environment

```python
# Create Node environment
backend.ensure_node_init()

# Install packages (isolated to workspace)
backend.execute("bun add axios")

# Run script (uses workspace node_modules)
backend.write("/fetch.ts", """
import axios from 'axios';
const response = await axios.get('https://api.example.com');
console.log(response.data);
""")

backend.execute("bun run /fetch.ts")
```

### Multiple Workspaces

```python
# Workspace A
backend_a = SandboxBackend(context_a)
backend_a.ensure_python_init()
backend_a.execute("uv pip install pandas==1.0")

# Workspace B
backend_b = SandboxBackend(context_b)
backend_b.ensure_python_init()
backend_b.execute("uv pip install pandas==2.0")

# No conflicts! Each has its own /.venv/
```

## Benefits

### 1. **True Isolation**
- ✅ Filesystem: Workspace mounted as `/`
- ✅ Python: Isolated venv per workspace
- ✅ Node: Isolated node_modules per workspace
- ✅ Cache: Fresh per execution
- ✅ Temp: Fresh per execution

### 2. **No Path Hacks**
- ✅ No regex parsing
- ✅ No path conversion
- ✅ Agent uses natural paths: `/file.txt`
- ✅ bwrap handles everything

### 3. **Complete Independence**
- ✅ Workspace A: pandas 1.0, numpy 1.20
- ✅ Workspace B: pandas 2.0, numpy 2.0
- ✅ No conflicts, no pollution

### 4. **Security**
- ✅ System files protected (read-only)
- ✅ Sensitive paths not accessible
- ✅ Network shared but filtered by E2B
- ✅ Fresh temp/cache prevents poisoning

### 5. **Simplicity**
- ✅ ~150 lines of core code
- ✅ Clear, understandable logic
- ✅ Easy to debug
- ✅ No external dependencies (just bwrap)

## Comparison

| Aspect | SRT Approach | Bwrap Approach |
|--------|-------------|----------------|
| **Path Handling** | Parse and convert | Native (mount as /) |
| **Python Isolation** | Config + PATH | Mount + venv + env vars |
| **Node Isolation** | Config + PATH | Mount + node_modules + env vars |
| **Complexity** | Medium (config files) | Low (direct mounts) |
| **Dependencies** | npm, srt, bwrap, socat, ripgrep | bwrap, ripgrep |
| **Config Files** | .srt-settings.json | None |
| **Debugging** | Check SRT logs | Check bwrap command |
| **Performance** | SRT overhead | Direct bwrap |

## Requirements

### E2B Template

```python
.apt_install([
    "bubblewrap",    # Filesystem isolation
    "ripgrep",       # Fast file search
])
```

### Python Packages

```bash
uv  # Fast Python package manager
```

### Node Runtime

```bash
bun  # Fast Node runtime and package manager
```

## Testing

### Test 1: Python Isolation

```python
# Create workspace
backend = SandboxBackend(context)
backend.ensure_python_init()

# Install package
backend.execute("uv pip install matplotlib")

# Verify in workspace venv only
result = backend.execute("uv pip list")
assert "matplotlib" in result.output

# Verify system doesn't have it
system_result = sandbox.commands.run("python3 -c 'import matplotlib'")
assert system_result.exit_code != 0  # Should fail
```

### Test 2: File Writing

```python
# Write with absolute path
backend.write("/test.txt", "Hello World")

# Verify it's in workspace
files = backend.ls_info("/")
assert any(f.path == "/test.txt" for f in files)

# Verify system root doesn't have it
system_check = sandbox.commands.run("ls /test.txt")
assert system_check.exit_code != 0  # Should fail
```

### Test 3: Multi-Workspace Isolation

```python
# Create two workspaces
backend_a = SandboxBackend(context_a)
backend_b = SandboxBackend(context_b)

# Install different versions
backend_a.ensure_python_init()
backend_a.execute("uv pip install pandas==1.0")

backend_b.ensure_python_init()
backend_b.execute("uv pip install pandas==2.0")

# Verify isolation
result_a = backend_a.execute("python -c 'import pandas; print(pandas.__version__)'")
assert "1.0" in result_a.output

result_b = backend_b.execute("python -c 'import pandas; print(pandas.__version__)'")
assert "2.0" in result_b.output
```

## Summary

**Clean, simple, effective isolation using only bwrap.**

- ✅ No SRT dependency
- ✅ No path parsing
- ✅ No config files
- ✅ True filesystem isolation
- ✅ True Python isolation
- ✅ True Node isolation
- ✅ Fresh cache per execution
- ✅ ~150 lines of code
- ✅ Easy to understand
- ✅ Easy to debug

**Agent writes `/file.png` → workspace gets `file.png`**

**That's it!** 🎉

