# bwrap Simplification - No More Path Resolution

## Problem

We were still using `_resolve_path()` to convert agent paths to absolute filesystem paths like `/mnt/r2/bucket/threads/thread_id/file.txt`. This was unnecessary complexity because **bwrap mounts the workspace AS `/`**.

## Solution

**Simplified all file operations to work with bwrap-native paths.**

### Before (Complex)

```python
def _resolve_path(self, agent_path: str) -> str:
    """Convert agent path to absolute filesystem path."""
    if not agent_path or agent_path == "/":
        return self._base_path  # /mnt/r2/bucket/threads/thread_id
    
    clean_path = agent_path.lstrip("/")
    return f"{self._base_path}/{clean_path}"

def ls_info(self, path: str):
    abs_path = self._resolve_path(path)  # /mnt/r2/.../threads/id/file.txt
    ls_cmd = f"find {abs_path} ..."
```

**Problem:**
- Commands run inside bwrap where `/` = workspace
- But we're passing `/mnt/r2/...` paths
- Doesn't work! bwrap doesn't see that path

### After (Simple)

```python
def _normalize_path(self, agent_path: str) -> str:
    """Ensure path is absolute for bwrap."""
    if not agent_path or agent_path == "/":
        return "/"
    
    # Just ensure it starts with /
    if not agent_path.startswith("/"):
        return f"/{agent_path}"
    
    return agent_path

def ls_info(self, path: str):
    abs_path = self._normalize_path(path)  # /file.txt or /
    ls_cmd = f"find {abs_path} ..."  # Runs inside bwrap where / = workspace
```

**Why this works:**
- Commands run inside bwrap
- Inside bwrap, `/` = workspace
- So `/file.txt` naturally resolves to workspace/file.txt
- No path conversion needed!

## Changes Made

### 1. Renamed `_resolve_path()` → `_normalize_path()`

**Old behavior:**
```python
_resolve_path("/file.txt")
# Returns: /mnt/r2/bucket/threads/thread_id/file.txt
```

**New behavior:**
```python
_normalize_path("/file.txt")
# Returns: /file.txt (used inside bwrap where / = workspace)
```

### 2. Fixed `grep_raw()` Default Path

**Before:**
```python
abs_path = self._normalize_path(path) if path else self._base_path
```

**After:**
```python
abs_path = self._normalize_path(path) if path else "/"
```

**Why:** Inside bwrap, workspace root is `/`, not `self._base_path`

### 3. Fixed `write()` Parent Directory Check

**Before:**
```python
parent = os.path.dirname(abs_path)
if parent and parent != self._base_path:
    mkdir -p parent
```

**After:**
```python
parent = os.path.dirname(abs_path)
if parent and parent != "/":
    mkdir -p parent
```

**Why:** Inside bwrap, root is `/`, not `self._base_path`

## File Operations Simplified

All file operations now use simple bwrap-native paths:

### `ls_info()`

```python
def ls_info(self, path: str):
    abs_path = self._normalize_path(path)  # "/" or "/file.txt"
    result = self._run_isolated(f"find {abs_path} ...")
    # Inside bwrap: find / (workspace root)
```

### `read()`

```python
def read(self, path: str):
    abs_path = self._normalize_path(path)  # "/file.txt"
    result = self._run_isolated(f"cat {abs_path}")
    # Inside bwrap: cat /file.txt (workspace/file.txt)
```

### `write()`

```python
def write(self, file_path: str, content: str):
    abs_path = self._normalize_path(file_path)  # "/file.txt"
    result = self._run_isolated(f"echo ... > {abs_path}")
    # Inside bwrap: writes to /file.txt (workspace/file.txt)
```

### `grep_raw()`

```python
def grep_raw(self, pattern: str, path: Optional[str] = None):
    abs_path = self._normalize_path(path) if path else "/"
    result = self._run_isolated(f"grep -rn {pattern} {abs_path}")
    # Inside bwrap: grep in / (entire workspace)
```

### `glob_info()`

```python
def glob_info(self, pattern: str, path: str = "/"):
    abs_path = self._normalize_path(path)  # "/"
    result = self._run_isolated(f"find {abs_path} -name {pattern}")
    # Inside bwrap: find / -name '*.txt' (workspace root)
```

## How bwrap Makes This Work

```python
bwrap_cmd = [
    "bwrap",
    "--bind", "/mnt/r2/bucket/threads/thread_id", "/",  # ← Magic!
    # ... other mounts ...
    "/bin/bash", "-c", "ls /file.txt"
]
```

**Inside bwrap:**
```
/ = /mnt/r2/bucket/threads/thread_id  (workspace)
/file.txt = /mnt/r2/bucket/threads/thread_id/file.txt
/.solven/ = /mnt/r2/bucket/threads/thread_id/.solven/
/.venv/ = /mnt/r2/bucket/threads/thread_id/.venv/
```

**Agent perspective:**
```
/ = my workspace root
/file.txt = my file
/.solven/skills/system/ = system skills
```

## Benefits

### 1. **Simpler Code**

**Before:**
```python
def ls_info(self, path):
    # Convert agent path to filesystem path
    abs_path = self._resolve_path(path)
    # abs_path = /mnt/r2/.../threads/id/file.txt
    
    # Run command (doesn't work in bwrap!)
    result = self._run_isolated(f"ls {abs_path}")
```

**After:**
```python
def ls_info(self, path):
    # Just normalize to absolute
    abs_path = self._normalize_path(path)
    # abs_path = /file.txt
    
    # Run command (works perfectly in bwrap!)
    result = self._run_isolated(f"ls {abs_path}")
```

### 2. **No Path Confusion**

**Before:**
- Agent uses: `/file.txt`
- We convert to: `/mnt/r2/.../file.txt`
- bwrap sees: `/` as workspace
- Result: Path confusion!

**After:**
- Agent uses: `/file.txt`
- We normalize: `/file.txt`
- bwrap maps: `/` → workspace
- Result: `/file.txt` works naturally!

### 3. **Consistent Behavior**

All paths work the same way:

```python
# Agent writes
backend.write("/plot.png", data)
# Inside bwrap: writes to /plot.png → workspace/plot.png ✓

# Agent reads
backend.read("/plot.png")
# Inside bwrap: reads from /plot.png → workspace/plot.png ✓

# Agent lists
backend.ls_info("/")
# Inside bwrap: lists / → workspace root ✓

# Agent searches
backend.grep_raw("pattern", "/")
# Inside bwrap: searches / → entire workspace ✓
```

### 4. **Natural for Agents**

Agents can think of their workspace as a real filesystem:

```python
# Agent code
import matplotlib.pyplot as plt

plt.plot([1, 2, 3], [4, 5, 6])
plt.savefig('/plot.png')  # Just use / naturally!

# Check it worked
import os
print(os.listdir('/'))  # Lists workspace root
# Output: ['plot.png', '.venv', '.solven', 'script.py']
```

## Testing

### Test 1: File Operations

```python
# Write file
backend.write("/test.txt", "Hello")
# Inside bwrap: /test.txt = workspace/test.txt ✓

# Read file
content = backend.read("/test.txt")
# Inside bwrap: reads /test.txt ✓
assert content == "Hello"

# List files
files = backend.ls_info("/")
# Inside bwrap: lists / ✓
assert any(f.path == "/test.txt" for f in files)
```

### Test 2: Nested Directories

```python
# Write nested file
backend.write("/data/results.json", '{"status": "ok"}')
# Inside bwrap: mkdir -p /data, write /data/results.json ✓

# Read nested file
content = backend.read("/data/results.json")
# Inside bwrap: reads /data/results.json ✓
assert "ok" in content
```

### Test 3: Skills Access

```python
# List skills
files = backend.ls_info("/.solven/skills/system/")
# Inside bwrap: lists /.solven/skills/system/ → symlink to /mnt/r2/.../skills/system ✓

# Read skill
skill = backend.read("/.solven/skills/system/data-analysis.md")
# Inside bwrap: reads via symlink ✓
assert "data analysis" in skill.lower()
```

### Test 4: Search Operations

```python
# Search workspace
results = backend.grep_raw("pattern")
# Inside bwrap: grep -rn pattern / ✓

# Search with glob
results = backend.grep_raw("pattern", glob="*.py")
# Inside bwrap: find / -name '*.py' -exec grep pattern {} + ✓
```

## Summary

| Aspect | Before | After |
|--------|--------|-------|
| **Path conversion** | Complex (`_resolve_path`) | Simple (`_normalize_path`) |
| **Paths used** | Filesystem paths | bwrap-native paths |
| **Agent writes `/file.txt`** | Converts to `/mnt/r2/.../file.txt` | Uses `/file.txt` directly |
| **Inside bwrap** | Path mismatch | Path works naturally |
| **Code complexity** | High | Low |
| **Debugging** | Hard (path confusion) | Easy (paths match agent view) |
| **Lines of code** | More | Less |

**Result: Clean, simple, and works correctly with bwrap!** ✅

## Key Takeaway

**With bwrap mounting workspace as `/`, we don't need path conversion:**

```python
# Agent perspective
/file.txt = my file

# bwrap magic
bwrap --bind /mnt/r2/.../workspace /

# Inside bwrap
/file.txt = workspace/file.txt

# No conversion needed! Just use / naturally.
```

**This is the right way to use bwrap.** 🎯

