# Unified Bubblewrap Operations

## Overview

ALL file operations (execute, read, write, ls_info, edit) now use **bubblewrap** for consistent path handling. This ensures that paths work the same way across all operations.

## Architecture

### Bubblewrap Workspace Mapping

```
Physical (Host):
/mnt/r2/{bucket}/threads/{thread_id}/  ← Actual workspace on R2
├── .venv/
├── .solven/
├── node_modules/
└── (files)

Bubblewrap View (Inside Container):
/workspace/  ← Workspace appears here
├── .venv/
├── .solven/
├── node_modules/
└── (files)

/bin, /usr, /lib  ← System binaries (read-only)
```

### Path Conversion

Agent paths are converted to workspace paths:

```python
"/" → "/workspace"
"/file.txt" → "/workspace/file.txt"
"file.txt" → "/workspace/file.txt"
"/subdir/file.txt" → "/workspace/subdir/file.txt"
```

## Unified Operations

### 1. Execute Commands

```python
execute("python script.py")
execute("ls -la")
execute("cat file.txt")
```

**How it works:**
- Wraps command with bubblewrap
- Working directory: `/workspace`
- Python venv auto-activated
- All files in `/workspace`

### 2. Read Files

```python
read("/file.txt")        # Reads /workspace/file.txt
read("/data/input.csv")  # Reads /workspace/data/input.csv
```

**How it works:**
- Converts agent path → `/workspace/...`
- Uses `cat` command via bubblewrap
- Returns file content

### 3. Write Files

```python
write("/output.txt", "data")  # Writes to /workspace/output.txt
write("/data/result.csv", csv_data)  # Writes to /workspace/data/result.csv
```

**How it works:**
- Converts agent path → `/workspace/...`
- Creates parent directories if needed
- Uses heredoc to write content via bubblewrap
- Checks if file exists first

### 4. List Directory

```python
ls_info("/")             # Lists /workspace
ls_info("/data")         # Lists /workspace/data
```

**How it works:**
- Converts agent path → `/workspace/...`
- Uses `ls -la` via bubblewrap
- Parses output into `FileInfo` objects

### 5. Edit Files

```python
edit("/script.py", "old_code", "new_code")
```

**How it works:**
- Reads file via bubblewrap
- Performs replacement
- Writes back via bubblewrap

## Implementation Details

### Core Methods

#### `_bwrap_command(command: str) -> str`

Wraps any command with bubblewrap isolation:

```python
def _bwrap_command(self, command: str) -> str:
    """Wrap command with bubblewrap."""
    return f"bwrap --bind {self._base_path} /workspace ... {command}"
```

**Usage:**
```python
cmd = "ls -la /workspace"
full_cmd = self._bwrap_command(cmd)
result = self._sandbox.commands.run(full_cmd)
```

#### `_workspace_path(agent_path: str) -> str`

Converts agent paths to workspace paths:

```python
def _workspace_path(self, agent_path: str) -> str:
    """Convert agent path to workspace path."""
    clean_path = agent_path.lstrip('/')
    return f"/workspace/{clean_path}" if clean_path else "/workspace"
```

**Examples:**
```python
_workspace_path("/") → "/workspace"
_workspace_path("/file.txt") → "/workspace/file.txt"
_workspace_path("file.txt") → "/workspace/file.txt"
```

### Fallback Mode

If bubblewrap is not available, all operations fall back to direct file access:

```python
if self._check_bwrap_available():
    # Use bubblewrap
    workspace_path = self._workspace_path(path)
    cmd = self._bwrap_command(f"cat {workspace_path}")
    result = self._sandbox.commands.run(cmd)
else:
    # Fallback to direct access
    key = self._key(path)
    content = self._sandbox.files.read(key)
```

## Benefits

### ✅ Consistent Path Handling

All operations use the same path system:

```python
# All these work with the same path format
execute("python /script.py")  # Runs /workspace/script.py
write("/script.py", code)     # Writes to /workspace/script.py
read("/script.py")            # Reads /workspace/script.py
ls_info("/")                  # Lists /workspace
```

### ✅ Agent Path Independence

Agent can use any path style - all work:

```python
# Absolute paths
execute("python /script.py")
write("/file.txt", "data")

# Relative paths
execute("python script.py")
write("file.txt", "data")

# Subdirectories
execute("python /data/process.py")
write("/data/output.csv", csv)
```

### ✅ Isolation

- System binaries read-only
- Workspace read-write
- Each thread completely isolated
- Network preserved

### ✅ Reliability

- All operations go through same path
- Consistent behavior
- No path confusion
- Proper error handling

## Agent Usage Guide

### File Paths

Agent should use `/workspace` as the root:

```python
# Create file
write("/output.txt", "data")

# Read file
content = read("/output.txt")

# Execute script
execute("python /script.py")

# List directory
files = ls_info("/")
```

### Subdirectories

```python
# Create in subdirectory
write("/data/file.txt", "content")

# List subdirectory
files = ls_info("/data")

# Execute from subdirectory
execute("cd /workspace/data && python process.py")
```

### Skills and Ticket

```python
# Access skills
files = ls_info("/.solven/skills/system")
execute("python /.solven/skills/system/pdf_skill.py")

# Access ticket files
files = ls_info("/.ticket")
content = read("/.ticket/document.pdf")
```

## Examples

### Example 1: Create and Run Python Script

```python
# Write script
write("/hello.py", """
print('Hello from /workspace!')

import os
print(f'Working dir: {os.getcwd()}')
print(f'Files: {os.listdir("/")}')
""")

# Execute it
result = execute("python /hello.py")
print(result.output)
# Output:
# Hello from /workspace!
# Working dir: /workspace
# Files: ['.venv', '.solven', 'hello.py', ...]
```

### Example 2: Data Processing Workflow

```python
# Create data directory and file
execute("mkdir -p /data")
write("/data/input.csv", "name,age\nJohn,30\nJane,25")

# Process with Python
execute("""
python -c "
import pandas as pd
df = pd.read_csv('/workspace/data/input.csv')
df['age'] = df['age'] + 1
df.to_csv('/workspace/data/output.csv', index=False)
"
""")

# Read result
result = read("/data/output.csv")
print(result)
# name,age
# John,31
# Jane,26
```

### Example 3: Multi-File Project

```python
# Create project structure
execute("mkdir -p /project/src /project/data")

# Write main script
write("/project/src/main.py", """
from utils import process_data

if __name__ == '__main__':
    process_data()
""")

# Write utility module
write("/project/src/utils.py", """
def process_data():
    print('Processing data...')
""")

# Run project
execute("cd /workspace/project && python src/main.py")
```

## Troubleshooting

### Issue: "File not found"

**Cause:** Path not using `/workspace` prefix in bubblewrap

**Solution:** Ensure `_workspace_path()` is used for all paths

```python
# Wrong
cmd = f"cat {path}"  # Missing /workspace prefix

# Correct
workspace_path = self._workspace_path(path)
cmd = f"cat {workspace_path}"
```

### Issue: "Permission denied"

**Cause:** Trying to write to system directories

**Solution:** Ensure workspace is bound correctly

```python
"--bind", self._base_path, "/workspace"  # ✅ Correct
```

### Issue: Bubblewrap not available

**Check:**
```python
result = execute("which bwrap")
print(result.output)  # Should show /usr/bin/bwrap
```

**Fix:** Rebuild E2B template with bubblewrap installed

## Summary

🎉 **Unified bubblewrap operations provide:**

1. **Consistent paths** - All operations use `/workspace`
2. **Agent flexibility** - Any path style works
3. **Complete isolation** - Each thread separate
4. **Reliable execution** - All ops through same system
5. **Proper fallback** - Works without bubblewrap too

**Result: Simple, consistent, reliable file operations! ✅**

```python
# Everything just works:
write("/script.py", code)        # ✅
execute("python /script.py")     # ✅
result = read("/output.txt")     # ✅
files = ls_info("/")             # ✅

# Perfect! 🚀
```

