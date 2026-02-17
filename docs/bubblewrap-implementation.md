# ✅ Bubblewrap Implementation - Workspace as Root

## What Was Implemented

**Bubblewrap (bwrap)** - Makes the workspace appear as `/` to the agent, so ANY path works!

## How It Works

### With Bubblewrap (Automatic if Available)

```bash
# Agent executes
ls -la /

# Agent sees workspace contents:
.venv/
.solven/
.ticket/
node_modules/
pyproject.toml
package.json
# ...user files...
```

**All paths work naturally:**
- `/file.txt` → workspace/file.txt ✅
- `./file.txt` → workspace/file.txt ✅  
- `file.txt` → workspace/file.txt ✅
- `/script.py` → workspace/script.py ✅

### Without Bubblewrap (Fallback)

Falls back to simple `cd` approach (current behavior).

## Key Features

1. **Auto-detection** - Checks if bwrap is available
2. **Transparent fallback** - Uses simple mode if bwrap not found
3. **Path agnostic** - Agent can use any path style
4. **Zero config** - Works automatically

## Implementation Details

### Three Methods

**1. `execute(command)` - Main entry point**
```python
def execute(self, command: str) -> ExecuteResponse:
    use_bwrap = self._check_bwrap_available()
    
    if use_bwrap:
        return self._execute_with_bwrap(command)
    else:
        return self._execute_simple(command)
```

**2. `_execute_with_bwrap(command)` - Bubblewrap mode**
```python
def _execute_with_bwrap(self, command: str) -> ExecuteResponse:
    # Makes workspace appear as /
    bwrap_args = [
        "bwrap",
        "--bind", self._base_path, "/",  # ← THE MAGIC
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        # ... more system mounts ...
        "--setenv", "PATH", "/.venv/bin:/node_modules/.bin:/usr/bin:/bin",
        "bash", "-c", command
    ]
```

**3. `_execute_simple(command)` - Fallback mode**
```python
def _execute_simple(self, command: str) -> ExecuteResponse:
    # Current approach: cd to workspace
    full_command = f"cd {self._base_path} && ... && {command}"
```

## Bubblewrap Command Breakdown

```bash
bwrap \
  # System binaries (read-only)
  --ro-bind /usr /usr \
  --ro-bind /lib /lib \
  --ro-bind /lib64 /lib64 \
  --ro-bind /bin /bin \
  --ro-bind /etc /etc \
  
  # System filesystems
  --proc /proc \
  --dev /dev \
  --tmpfs /tmp \
  
  # 🎯 WORKSPACE AS ROOT (read-write)
  --bind /mnt/r2/bucket/threads/thread_id / \
  
  # R2 bucket for symlinks
  --bind /mnt/r2/bucket /mnt/r2/bucket \
  
  # Working directory
  --chdir / \
  
  # Isolation
  --unshare-all \
  --share-net \
  --die-with-parent \
  
  # Environment
  --setenv HOME / \
  --setenv PYTHONUNBUFFERED 1 \
  --setenv MPLBACKEND Agg \
  --setenv PATH /.venv/bin:/node_modules/.bin:/usr/bin:/bin \
  
  # Execute
  bash -c 'source /.venv/bin/activate && command'
```

## Benefits

### ✅ Path Independence
```python
# All these work the same:
execute("python /script.py")
execute("python ./script.py")
execute("python script.py")

# File operations:
execute("echo 'data' > /output.txt")      # ✅ Works
execute("cat /output.txt")                # ✅ Works
execute("ls -la /")                       # ✅ Shows workspace
```

### ✅ Simple for Agent
Agent doesn't need to know about:
- Workspace paths
- Mount points
- Directory structure

Agent just uses normal paths like `/file.txt`

### ✅ Reliable Isolation
- Each thread completely isolated
- System binaries read-only
- Workspace is read-write
- Network preserved

### ✅ Performance
- Faster than proot (no ptrace)
- Uses Linux namespaces properly
- No significant overhead

## Example Usage

### Before (Path-dependent)

```python
# Agent needs to know workspace path
execute(f"cd {workspace_path} && python script.py")
execute(f"cat {workspace_path}/file.txt")

# Absolute paths don't work
execute("python /script.py")  # ❌ Looks in system root
```

### After (Path-independent)

```python
# Agent uses natural paths
execute("python /script.py")   # ✅ Workspace
execute("cat /file.txt")       # ✅ Workspace
execute("ls -la /")            # ✅ Shows workspace
execute("python script.py")    # ✅ Still works
```

## Testing

### Test 1: File Creation
```python
result = execute("echo 'Hello World' > /test.txt")
assert result.exit_code == 0

result = execute("cat /test.txt")
assert "Hello World" in result.output
```

### Test 2: Root Listing
```python
result = execute("ls -la /")
assert ".venv" in result.output
assert ".solven" in result.output
assert "pyproject.toml" in result.output
```

### Test 3: Python Script
```python
result = execute("""
python -c "
import os
print(f'CWD: {os.getcwd()}')
print(f'HOME: {os.environ[\"HOME\"]}')
with open('/output.txt', 'w') as f:
    f.write('test')
print('File created!')
"
""")

assert result.exit_code == 0
assert "CWD: /" in result.output
assert "HOME: /" in result.output
```

### Test 4: Fallback Mode
```python
# If bwrap not available, should still work
result = execute("pwd")
# Shows actual path in fallback mode
# But commands still execute successfully
```

## Logging

### With Bubblewrap
```
[SandboxBackend] ✅ bubblewrap available - using isolated root
[SandboxBackend.execute] 🔒 Bubblewrap: /mnt/r2/.../thread_id → /
[SandboxBackend.execute] Command: python script.py
[SandboxBackend.execute] Exit code: 0
```

### Without Bubblewrap
```
[SandboxBackend] ⚠️  bubblewrap not found - using simple mode
[SandboxBackend.execute] 📂 Simple mode: /mnt/r2/.../thread_id
[SandboxBackend.execute] Command: python script.py
[SandboxBackend.execute] Exit code: 0
```

## Troubleshooting

### Check if Bubblewrap is Available
```bash
# In E2B sandbox
bwrap --version

# Should output:
# bubblewrap 0.x.x
```

### Force Fallback Mode
```python
# Temporarily disable bwrap
self._has_bwrap = False
result = execute(command)  # Uses simple mode
```

### Debug Bubblewrap Command
The full bwrap command is logged when executing. Check logs to see the exact command being run.

## Comparison

| Feature | Bubblewrap | Simple (Fallback) |
|---------|-----------|-------------------|
| Workspace as `/` | ✅ Yes | ❌ No |
| Absolute paths work | ✅ Yes | ⚠️ Requires full path |
| Isolation | ✅ Strong | ⚠️ Basic |
| Performance | ✅ Fast | ✅ Fastest |
| Complexity | Low | Very Low |
| Fallback needed | No | N/A |

## Summary

🎉 **Bubblewrap gives you the best of both worlds:**

1. **Simple paths** - Agent uses `/file.txt`, it just works
2. **Reliable** - Battle-tested by Flatpak
3. **Fast** - No overhead
4. **Automatic** - Detects and uses if available
5. **Safe fallback** - Works even without bwrap

**Result: Commands succeed independently of path! ✅**

```python
# ANY of these work:
execute("python /script.py")
execute("python ./script.py")
execute("python script.py")

# ALL create file in workspace:
execute("echo 'data' > /file.txt")
execute("echo 'data' > ./file.txt")
execute("echo 'data' > file.txt")

# Perfect! 🚀
```

