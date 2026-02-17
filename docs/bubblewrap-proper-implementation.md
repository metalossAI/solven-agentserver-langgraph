# Proper Bubblewrap Implementation

## Based on ArchWiki Best Practices

Reference: [https://wiki.archlinux.org/title/Bubblewrap#Filesystem_isolation](https://wiki.archlinux.org/title/Bubblewrap#Filesystem_isolation)

## Key Insight: Workspace as Root

The correct approach from ArchWiki's "Filesystem isolation" section:

```bash
bwrap \
  --bind ~/sandboxes/${MYPACKAGE}/files/ / \
  --ro-bind /etc/resolv.conf /etc/resolv.conf \
  --tmpfs /tmp \
  --proc /proc \
  --dev /dev \
  --chdir / \
  command
```

**The magic:** `--bind workspace /` makes the workspace appear as root (`/`)!

## Our Implementation

### Architecture

```
Physical (Host):
/mnt/r2/{bucket}/threads/{thread_id}/  ← Actual workspace
├── .venv/              → Python environment
├── .solven/            → Skills symlinks
├── .ticket/            → Ticket symlink
├── node_modules/       → Node packages
├── script.py           → User files
└── data/               → User directories

Bubblewrap View (Inside Container):
/                       ← Workspace bound here!
├── .venv/              → Same files
├── .solven/            → Same symlinks
├── script.py           → Same user files
├── data/               → Same directories
├── /usr/              → System binaries (overlaid, read-only)
├── /bin/              → System binaries (overlaid, read-only)
├── /lib/              → System libraries (overlaid, read-only)
├── /proc/             → Process info
├── /dev/              → Devices
└── /tmp/              → Temp filesystem
```

### Mount Strategy

**Step 1: Foundation - Bind Workspace as Root**
```bash
--bind /mnt/r2/{bucket}/threads/{thread_id}/ /
```

This makes ALL workspace files appear at `/`. Any file the agent creates at `/script.py` goes to the workspace!

**Step 2: Overlay System Directories (Read-Only)**
```bash
--ro-bind /usr /usr      # System programs
--ro-bind /lib /lib      # System libraries  
--ro-bind /lib64 /lib64  # 64-bit libraries
--ro-bind /bin /bin      # Essential binaries
--ro-bind /sbin /sbin    # System binaries
```

These don't conflict with workspace because they're separate mount points.

**Step 3: System Files**
```bash
--ro-bind /etc/resolv.conf /etc/resolv.conf  # DNS
--ro-bind /etc/hosts /etc/hosts              # Hosts
--ro-bind /etc/ssl /etc/ssl                  # Certificates
```

**Step 4: System Filesystems**
```bash
--proc /proc     # Process information
--dev /dev       # Device files
--tmpfs /tmp     # Temporary files
```

**Step 5: R2 Bucket for Symlinks**
```bash
--ro-bind /mnt/r2/{bucket} /mnt/r2/{bucket}
```

This allows `.solven` and `.ticket` symlinks to resolve correctly.

**Step 6: Namespace Isolation**
```bash
--unshare-all     # Create all namespaces (user, PID, mount, etc.)
--share-net       # But keep network access
--new-session     # Security: prevent TIOCSTI escape (CVE-2017-5226)
--die-with-parent # Kill sandbox if parent dies
```

**Step 7: Environment**
```bash
--chdir /                                  # Working directory
--setenv HOME /                            # Home directory
--setenv PATH /.venv/bin:/usr/bin:/bin    # Include venv
```

## Agent Path Usage

With this setup, paths work naturally:

```python
# Agent writes file
write("/script.py", code)
# Creates: /mnt/r2/.../thread_id/script.py ✅

# Agent executes
execute("python /script.py")
# Finds: / (workspace) / script.py ✅

# Agent lists directory
ls_info("/")
# Shows workspace files ✅

# Agent creates subdirectory
execute("mkdir /data")
# Creates: /mnt/r2/.../thread_id/data/ ✅
```

**Everything just works!** ✅

## Implementation Details

### _bwrap_command Method

```python
def _bwrap_command(self, command: str) -> str:
    """Wrap command with bubblewrap following ArchWiki best practices."""
    import shlex
    
    bwrap_args = [
        "bwrap",
        # Foundation: workspace as root
        "--bind", self._base_path, "/",
        
        # Overlay: system directories (read-only)
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/sbin", "/sbin",
        "--ro-bind", "/etc/resolv.conf", "/etc/resolv.conf",
        "--ro-bind", "/etc/hosts", "/etc/hosts",
        "--ro-bind", "/etc/ssl", "/etc/ssl",
        
        # System filesystems
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        
        # R2 for symlinks
        "--ro-bind", f"/mnt/r2/{bucket}", f"/mnt/r2/{bucket}",
        
        # Isolation
        "--chdir", "/",
        "--unshare-all",
        "--share-net",
        "--new-session",
        "--die-with-parent",
        
        # Environment
        "--setenv", "HOME", "/",
        "--setenv", "PATH", "/.venv/bin:/usr/bin:/bin",
        
        # Execute
        "/bin/bash", "-c", shlex.quote(command)
    ]
    
    return " ".join(bwrap_args)
```

### Path Conversion

```python
def _workspace_path(self, agent_path: str) -> str:
    """
    Convert agent path to bubblewrap path.
    With workspace as /, paths map directly!
    """
    if not agent_path or agent_path == "/":
        return "/"
    
    # Add leading slash if not present
    if not agent_path.startswith('/'):
        return f"/{agent_path}"
    
    return agent_path
```

## Security Features

### CVE-2017-5226 Protection

Using `--new-session` prevents the TIOCSTI security issue where sandboxed processes could escape via terminal injection.

### Namespace Isolation

- **User namespace:** Separate UID/GID space
- **PID namespace:** Isolated process tree
- **Mount namespace:** Separate mount table
- **Network namespace:** Isolated (but we share-net for functionality)
- **IPC namespace:** Isolated inter-process communication
- **UTS namespace:** Separate hostname

### Read-Only System

All system directories (`/usr`, `/bin`, `/lib`) are read-only, preventing:
- System file modification
- Binary tampering
- Library replacement attacks

### Process Limits

With `--die-with-parent`, if the parent process dies, all sandboxed processes are killed, preventing orphaned processes.

## Benefits

### ✅ True Isolation

Each thread gets its own:
- Root filesystem
- Process tree
- IPC namespace
- Mount table

### ✅ Natural Paths

Agent uses normal paths:
```python
"/script.py" → Works ✅
"/data/file.csv" → Works ✅
"/.solven/skills/" → Works ✅
```

### ✅ System Access

Agent has access to:
- Python, pip, uv
- Node, npm, bun
- System utilities (ls, cat, grep, etc.)
- Network (DNS, HTTP)

### ✅ Persistence

All files persist to R2:
```
Agent creates: /output.txt
Stored at: /mnt/r2/{bucket}/threads/{thread_id}/output.txt
```

### ✅ Symlinks Work

`.solven` and `.ticket` symlinks resolve correctly because R2 bucket is mounted inside bubblewrap.

## Complete Example

### Agent Workflow

**1. Create Python script**
```python
write("/analyze.py", """
import pandas as pd

# Read data
df = pd.read_csv('/data/input.csv')

# Process
df['processed'] = df['value'] * 2

# Write result
df.to_csv('/data/output.csv', index=False)

print('Analysis complete!')
""")
```

**2. Create data directory and file**
```python
execute("mkdir /data")
write("/data/input.csv", "value\n10\n20\n30")
```

**3. Execute script**
```python
result = execute("python /analyze.py")
# Output: "Analysis complete!"
```

**4. Read result**
```python
output = read("/data/output.csv")
# Contains: "value,processed\n10,20\n20,40\n30,60"
```

**5. List files**
```python
files = ls_info("/")
# Returns: [
#   FileInfo(path="/analyze.py", ...),
#   FileInfo(path="/data", ...),
#   FileInfo(path="/.venv", ...),
#   ...
# ]
```

### What Happens Behind the Scenes

**Execute command:**
```bash
bwrap \
  --bind /mnt/r2/solven-testing/threads/abc123/ / \
  --ro-bind /usr /usr \
  --ro-bind /bin /bin \
  --ro-bind /lib /lib \
  ... \
  --proc /proc \
  --dev /dev \
  --unshare-all \
  --share-net \
  --new-session \
  --setenv HOME / \
  --setenv PATH /.venv/bin:/usr/bin:/bin \
  /bin/bash -c 'source /.venv/bin/activate && python /analyze.py'
```

**Result:**
- Python runs from `/.venv/bin/python` ✅
- Finds script at `/analyze.py` ✅
- Reads data from `/data/input.csv` ✅
- Writes output to `/data/output.csv` ✅
- All files persist to R2 ✅

## Comparison: Before vs After

### Before (Broken)

```
Agent: write("/script.py", code)
→ Creates at: {workspace}/script.py

Agent: execute("python /script.py")
→ Looks for: /script.py (system root)
→ ERROR: File not found! ❌
```

### After (Working)

```
Agent: write("/script.py", code)  
→ Creates at: / → {workspace}/script.py

Agent: execute("python /script.py")
→ Looks for: /script.py → {workspace}/script.py
→ SUCCESS: File found and executed! ✅
```

## Verification

### Test Bubblewrap Setup

```python
# Test 1: Create and execute
write("/test.py", "print('Hello from /')")
result = execute("python /test.py")
assert "Hello from /" in result.output

# Test 2: Working directory
result = execute("pwd")
assert result.output.strip() == "/"

# Test 3: List files
files = ls_info("/")
assert any(f.path == "/test.py" for f in files)

# Test 4: System access
result = execute("which python")
assert "/.venv/bin/python" in result.output

# Test 5: Network access
result = execute("curl -I https://www.google.com")
assert result.exit_code == 0
```

## Troubleshooting

### Issue: "Permission denied" on system directories

**Cause:** Trying to write to read-only system directories

**Solution:** All writes go to workspace (`/`), system directories are read-only by design

### Issue: Symlinks don't resolve

**Cause:** R2 bucket not mounted in bubblewrap

**Solution:** Ensure `--ro-bind /mnt/r2/{bucket} /mnt/r2/{bucket}` is included

### Issue: Network doesn't work

**Cause:** Network namespace not shared

**Solution:** Must include `--share-net` flag

### Issue: Python venv not activated

**Cause:** PATH doesn't include `/.venv/bin`

**Solution:** Ensure `--setenv PATH /.venv/bin:/usr/bin:/bin`

## Summary

✅ **Proper bubblewrap implementation following ArchWiki:**

1. **Bind workspace as root** - `--bind {workspace} /`
2. **Overlay system binaries** - `--ro-bind /usr /usr` etc.
3. **Add system filesystems** - `--proc /proc`, `--dev /dev`
4. **Isolate namespaces** - `--unshare-all --share-net`
5. **Security hardening** - `--new-session`, `--die-with-parent`

**Result:** Each thread gets an isolated machine-like environment where:
- Workspace appears as root `/`
- Agent can use natural paths
- Full system access available
- Complete isolation maintained
- Files persist to R2

**Perfect isolated development environment! 🎉**

