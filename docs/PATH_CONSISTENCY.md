# Path Consistency: Execute vs File Operations

## Overview

With proot isolation, it's critical that all operations present a consistent view of the filesystem to the agent. This document explains how consistency is maintained.

## The Virtual Filesystem View

**From the agent's perspective:**
- `/` is the thread workspace root (not the actual system root)
- `/file.txt` is a file in the thread workspace
- `/.solven/` contains skills
- `/.ticket/` contains ticket workspace files (if ticket exists)

## Consistency Guarantee

### Path Mapping System

All operations use two core methods:

1. **`_key(virtual_path)` → actual_path**
   - Converts agent's virtual paths to actual filesystem paths
   - Example: `"/"` → `"/mnt/r2/solven-testing/threads/thread-123/"`
   - Example: `"/file.txt"` → `"/mnt/r2/solven-testing/threads/thread-123/file.txt"`

2. **`_path_from_key(actual_path)` → virtual_path**
   - Converts actual filesystem paths back to agent's virtual paths
   - Example: `"/mnt/r2/solven-testing/threads/thread-123/file.txt"` → `"/file.txt"`

### Method Consistency Table

| Operation | What Agent Does | What Backend Does | Consistency |
|-----------|----------------|-------------------|-------------|
| **execute("ls /")** | Runs `ls /` in proot | proot shows base_path as `/` | ✅ Via proot |
| **ls_info("/")** | Calls ls_info API | Uses `_key("/")` → base_path | ✅ Via _key |
| **execute("cat /file.txt")** | Runs `cat /file.txt` in proot | proot reads base_path/file.txt | ✅ Via proot |
| **read("/file.txt")** | Calls read API | Uses `_key("/file.txt")` → base_path/file.txt | ✅ Via _key |
| **execute("python /script.py")** | Runs python in proot | proot runs base_path/script.py | ✅ Via proot |
| **write("/script.py", code)** | Calls write API | Uses `_key("/script.py")` → base_path/script.py | ✅ Via _key |

## Examples of Consistent Behavior

### Example 1: Listing Root Directory

**Via execute():**
```python
agent.execute("ls -la /")
# Output: .venv, package.json, .solven, .ticket, myfile.txt
```

**Via ls_info():**
```python
files = agent.ls_info("/")
# Returns: [FileInfo(path="/.venv", ...), FileInfo(path="/package.json", ...), 
#           FileInfo(path="/.solven", ...), FileInfo(path="/myfile.txt", ...)]
```

**Result:** ✅ Both show the same files

### Example 2: Reading a File

**Via execute():**
```python
agent.execute("cat /data.json")
# Output: {"key": "value"}
```

**Via read():**
```python
content = agent.read("/data.json")
# Returns: {"key": "value"}
```

**Result:** ✅ Both return the same content

### Example 3: Writing a File

**Via execute():**
```python
agent.execute("echo 'hello' > /test.txt")
# Creates file at base_path/test.txt
```

**Via write():**
```python
agent.write("/test.txt", "hello")
# Creates file at base_path/test.txt (via _key mapping)
```

**Result:** ✅ Both create the file in the same location

### Example 4: Working with Skills

**Via execute():**
```python
agent.execute("ls /.solven/skills/system/")
# Shows system skills (via symlink or proot path resolution)
```

**Via ls_info():**
```python
files = agent.ls_info("/.solven/skills/system/")
# Returns list of system skills (via _key resolution to skills path)
```

**Result:** ✅ Both show the same skills

## Implementation Details

### In execute() method:

```python
# With proot (preferred)
proot -r /mnt/r2/solven-testing/threads/thread-123 -w / /bin/bash -c "command"
# Inside proot: "/" = base_path
# Agent's command runs with base_path as root

# Without proot (fallback)
cd /mnt/r2/solven-testing/threads/thread-123 && (sanitized_command)
# Agent's paths are sanitized: "/" → ".", "/file" → "./file"
```

### In file operation methods:

```python
def ls_info(self, path: str):
    key = self._key(path)  # Convert "/" → base_path
    # List files at actual filesystem location
    # Convert paths back via _path_from_key()
    return file_infos

def read(self, path: str):
    key = self._key(path)  # Convert "/file.txt" → base_path/file.txt
    # Read from actual filesystem location
    return content

def write(self, path: str, content: str):
    key = self._key(path)  # Convert "/file.txt" → base_path/file.txt
    # Write to actual filesystem location
    return result
```

## Validation

### How to Test Consistency

```python
# Test 1: List files both ways
execute_output = agent.execute("ls /")
ls_info_files = [f.path for f in agent.ls_info("/")]
# Should contain the same files

# Test 2: Write via execute, read via API
agent.execute("echo 'test data' > /test.txt")
content = agent.read("/test.txt")
# Should contain "test data"

# Test 3: Write via API, read via execute
agent.write("/api-test.txt", "api data")
result = agent.execute("cat /api-test.txt")
# Should contain "api data"

# Test 4: Check isolation
agent.execute("ls /mnt/r2/")
# Should fail or show nothing (isolated by proot)

agent.ls_info("/mnt/r2/")  
# Should fail (rejected by _key security check)
```

## Security Notes

1. **Path Security Check in _key():**
   - All paths are validated to be within allowed directories
   - Prevents access to paths outside base_path, skills, or ticket workspace
   - `_key()` raises `ValueError` for invalid paths

2. **Proot Isolation (Preferred):**
   - Agent literally cannot see paths outside base_path
   - No path sanitization needed
   - Kernel-level enforcement (via ptrace)

3. **Sanitization Fallback:**
   - When proot unavailable, uses regex-based path sanitization
   - Converts absolute paths to relative paths
   - Less secure but functional

## Troubleshooting

### Agent sees different files in execute() vs ls_info()

**Cause:** Inconsistent path mapping
**Fix:** Verify `_key()` mapping is correct for the path

### Agent can access files outside workspace

**Cause:** Proot not available or path validation issue
**Fix:** 
1. Check `_check_proot_available()` returns True
2. Verify `_key()` security check is working
3. Rebuild E2B template with proot installed

### Symlinks not working

**Cause:** Symlinks might not be created or followed
**Fix:**
1. Check frontend workspace setup creates symlinks
2. Verify rclone is mounted with `--vfs-links` and `--copy-links`
3. Use direct path resolution in `_key()` as fallback

## Summary

✅ **All operations are consistent** because:
1. `execute()` uses proot to make base_path appear as "/"
2. File operations use `_key()` to map "/" to base_path
3. Both approaches present the same virtual filesystem view to the agent
4. Security checks ensure agent cannot escape workspace

The agent sees a **single consistent filesystem** regardless of which method it uses!

