# SRT Implementation - Final (Follows SRT Documentation)

## How It Works (Per SRT Docs)

According to the [Anthropic Sandbox Runtime documentation](https://github.com/anthropic-experimental/sandbox-runtime), the recommended pattern is:

1. **CD into workspace directory**
2. **Use `allowWrite: ["."]`** for current directory
3. **Use relative paths** in commands

## Our Implementation

### 1. SRT Configuration
```python
{
    "filesystem": {
        "denyRead": ["~/.ssh", "~/.aws", "/etc/shadow", ...],
        "allowWrite": ["."],  # Current directory only
        "denyWrite": [".srt-settings.json", ".workspace_configured"]
    },
    "network": {
        "allowedDomains": ["pypi.org", "github.com", ...]
    },
    "allowAllUnixSockets": False
}
```

### 2. Command Execution Flow
```bash
# Step 1: CD into workspace
cd /mnt/r2/{bucket}/threads/{thread_id}/

# Step 2: Run command with SRT
srt --settings .srt-settings.json bash -c "command"
```

### 3. Path Conversion

We convert agent's "absolute" paths to workspace-relative paths:

```python
def _to_workspace_path(self, agent_path: str) -> str:
    """
    Agent's "/" means workspace root, not system root.
    - "/file.txt" → "file.txt"
    - "file.txt" → "file.txt"
    - "/" → "."
    """
    if not agent_path:
        return "."
    path = agent_path.lstrip("/")
    return path if path else "."
```

## Examples

### Agent writes to "/prueba.txt"
```
Agent: write("/prueba.txt", "Hello")
  ↓
_to_workspace_path("/prueba.txt") = "prueba.txt"
  ↓
Command: echo {base64} | base64 -d > prueba.txt
  ↓
Current dir: /mnt/r2/.../threads/{id}/
SRT checks: Is "prueba.txt" under "."? ✅ YES
  ↓
Write succeeds at: /mnt/r2/.../threads/{id}/prueba.txt
```

### Agent lists "/"
```
Agent: ls_info("/")
  ↓
_to_workspace_path("/") = "."
  ↓
Command: find . -maxdepth 1 ...
  ↓
Lists files in: /mnt/r2/.../threads/{id}/
```

### Agent uses nested path "/subdir/file.txt"
```
Agent: write("/subdir/file.txt", "Hello")
  ↓
_to_workspace_path("/subdir/file.txt") = "subdir/file.txt"
  ↓
Command: mkdir -p subdir && echo {base64} | base64 -d > subdir/file.txt
  ↓
SRT checks: Is "subdir/file.txt" under "."? ✅ YES
  ↓
Write succeeds
```

### Agent tries system path (blocked)
```
Agent tries to escape: write("../../etc/passwd", "Bad")
  ↓
_to_workspace_path("../../etc/passwd") = "../../etc/passwd"
  ↓
Command: echo {base64} | base64 -d > ../../etc/passwd
  ↓
SRT checks: Does "../../etc/passwd" escape "."? ❌ YES (blocked)
  ↓
❌ Operation not permitted
```

## Why This Works

### From Agent's Perspective
- Agent thinks of workspace as "/"
- Agent uses paths like "/file.txt", "/subdir/data.json"
- Agent doesn't need to know about `/mnt/r2/{bucket}/threads/{id}/`

### From SRT's Perspective
- We CD into workspace first
- All paths are relative to current directory
- `allowWrite: ["."]` = allow writes to current directory only
- SRT blocks any attempts to escape with `../`

### Security
✅ Agent can't write outside workspace (SRT blocks `../`)  
✅ Agent can't modify sensitive files (denyWrite)  
✅ Agent can't access SSH keys (denyRead)  
✅ Network is restricted to allowed domains  
✅ Unix sockets are blocked  

## All Methods Use Path Conversion

Every file operation converts agent paths to workspace-relative:

- `ls_info("/")` → `find . ...`
- `read("/file.txt")` → `cat file.txt`
- `write("/file.txt", ...)` → `echo ... > file.txt`
- `edit("/file.txt", ...)` → `cat file.txt` then `echo ... > file.txt`
- `grep_raw(pattern, "/")` → `grep -rn pattern .`
- `glob_info("*.py", "/")` → `find . -name "*.py" ...`

## Key Points

✅ **Follows SRT documentation** - Uses recommended `allowWrite: ["."]` pattern  
✅ **Clean isolation** - Workspace appears as "/" to agent  
✅ **Simple & secure** - SRT handles all security  
✅ **No agent changes needed** - Agent uses paths naturally  

## Installation Requirements

E2B template includes:
- ✅ `bubblewrap` - Filesystem isolation (Linux)
- ✅ `ripgrep` - Fast file search (used by SRT)
- ✅ `socat` - Network socket relay (used by SRT)
- ✅ `@anthropic-ai/sandbox-runtime` - SRT package

## Next Step

Rebuild E2B template to install SRT and dependencies:
```bash
cd /home/ramon/Github/metaloss/solven-agentserver-langgraph
uv run python src/e2b_sandbox/template.py
```

**Clean, simple, secure - exactly as SRT documentation recommends!** 🎉

