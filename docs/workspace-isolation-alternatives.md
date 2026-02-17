# Workspace Isolation Alternatives - Making Thread Workspace Appear as `/`

## Goal

Execute commands where `ls -la /` shows the thread workspace contents, making the workspace appear as the root directory to the agent.

## Best Options

### 1. 🥇 **Bubblewrap (bwrap)** - RECOMMENDED

**What it is:**
- Lightweight user namespace container tool
- Used by Flatpak internally
- Much simpler and more reliable than proot
- Actively maintained by Red Hat/Flatpak team

**Why it's better than proot:**
- ✅ More reliable (fewer bugs)
- ✅ Better maintained
- ✅ Simpler API
- ✅ Uses Linux namespaces properly
- ✅ No ptrace overhead
- ✅ Works with modern kernels

**Installation:**
```bash
# Already installed in E2B sandboxes
bwrap --version
```

**Basic Usage:**
```bash
bwrap \
  --ro-bind /usr /usr \
  --ro-bind /lib /lib \
  --ro-bind /lib64 /lib64 \
  --ro-bind /bin /bin \
  --ro-bind /sbin /sbin \
  --proc /proc \
  --dev /dev \
  --tmpfs /tmp \
  --bind /mnt/r2/bucket/threads/thread_id / \
  --chdir / \
  --unshare-all \
  --share-net \
  --die-with-parent \
  bash -c 'ls -la /'
```

**Result:** Shows contents of thread workspace!

**Example Implementation:**
```python
def execute_with_bwrap(self, command: str) -> ExecuteResponse:
    """Execute command with bubblewrap isolation."""
    
    # Build bwrap command
    bwrap_cmd = [
        "bwrap",
        # System mounts (read-only)
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/bin", "/bin",
        "--ro-bind", "/etc", "/etc",
        "--proc", "/proc",
        "--dev", "/dev",
        "--tmpfs", "/tmp",
        # Workspace as root (read-write)
        "--bind", self._base_path, "/",
        # R2 bucket for symlinks
        "--bind", f"/mnt/r2/{self._bucket_name}", f"/mnt/r2/{self._bucket_name}",
        # Working directory
        "--chdir", "/",
        # Isolation settings
        "--unshare-all",
        "--share-net",  # Keep network access
        "--die-with-parent",
        # Environment
        "--setenv", "HOME", "/",
        "--setenv", "PYTHONUNBUFFERED", "1",
        "--setenv", "MPLBACKEND", "Agg",
        "--setenv", "PATH", "/.venv/bin:/node_modules/.bin:/usr/bin:/bin",
        # Execute command
        "bash", "-c", f"[ -f /.venv/bin/activate ] && source /.venv/bin/activate; {command}"
    ]
    
    result = self._sandbox.commands.run(" ".join(bwrap_cmd), timeout=30000)
    return result
```

**Pros:**
- ✅ Clean, simple API
- ✅ Very reliable
- ✅ Well maintained
- ✅ Proper namespace isolation
- ✅ No ptrace overhead
- ✅ Workspace truly appears as `/`

**Cons:**
- ⚠️ Requires unprivileged user namespaces (usually enabled)
- ⚠️ Slightly more setup than simple cd

---

### 2. 🥈 **unshare** - Built-in Alternative

**What it is:**
- Built into Linux kernel
- Part of util-linux package
- Creates new namespaces

**Basic Usage:**
```bash
unshare -m -r bash -c "
  mount --bind /mnt/r2/bucket/threads/thread_id /mnt/newroot
  cd /mnt/newroot
  pivot_root . old_root
  umount -l old_root
  exec bash
"
```

**Pros:**
- ✅ No installation needed
- ✅ Built into Linux
- ✅ Lightweight

**Cons:**
- ⚠️ More complex setup
- ⚠️ Requires mount namespace management
- ⚠️ Need to handle pivot_root carefully

---

### 3. 🥉 **systemd-nspawn** - Container Alternative

**What it is:**
- Lightweight container manager
- Part of systemd
- Similar to chroot but more powerful

**Basic Usage:**
```bash
systemd-nspawn -D /mnt/r2/bucket/threads/thread_id \
  --bind=/usr \
  --bind=/lib \
  --bind=/lib64 \
  command
```

**Pros:**
- ✅ Full container features
- ✅ Good isolation
- ✅ Well documented

**Cons:**
- ⚠️ Requires systemd
- ⚠️ Heavier than bwrap
- ⚠️ May not be available

---

### 4. **Nix/Devbox/Flox** - Development Environment Managers

**What they are:**
- Environment management tools
- Declarative dependency management
- Reproducible builds

**Use case:**
- NOT for changing root directory
- FOR managing packages and dependencies
- FOR reproducible environments

**Example with Devbox:**
```json
// devbox.json in workspace
{
  "packages": [
    "python@3.12",
    "nodejs@20",
    "pandas",
    "numpy"
  ],
  "shell": {
    "init_hook": ["echo 'Environment ready'"]
  }
}
```

**Pros:**
- ✅ Excellent package management
- ✅ Reproducible
- ✅ Cross-platform

**Cons:**
- ❌ Doesn't change root directory view
- ❌ More complex setup
- ❌ Requires Nix installation

---

## Comparison Table

| Tool | Changes Root View | Complexity | Reliability | Performance | Maintenance |
|------|------------------|------------|-------------|-------------|-------------|
| **Bubblewrap** | ✅ Yes | Low | Excellent | Fast | Active |
| **proot** | ✅ Yes | Medium | Poor | Slow | Declining |
| **unshare** | ✅ Yes | High | Good | Fast | Built-in |
| **systemd-nspawn** | ✅ Yes | Medium | Good | Medium | Active |
| **Nix/Devbox** | ❌ No | Medium | Excellent | Fast | Active |
| **Current (cd)** | ❌ No | Very Low | Excellent | Fastest | N/A |

---

## Recommended Implementation: Bubblewrap

### Why Bubblewrap is Best for Your Use Case

1. **Reliable** - Used in production by Flatpak
2. **Simple** - Clean API, easy to use
3. **Fast** - No ptrace overhead like proot
4. **Maintained** - Active development
5. **Perfect fit** - Exactly what you need

### Implementation Plan

**Step 1: Check if bwrap is available**
```bash
bwrap --version
```

**Step 2: Add to sandbox_backend.py**
```python
def _check_bwrap_available(self) -> bool:
    """Check if bubblewrap is available."""
    try:
        result = self._sandbox.commands.run("which bwrap", timeout=5000)
        return result.exit_code == 0
    except:
        return False

def execute(self, command: str) -> ExecuteResponse:
    """Execute with bubblewrap if available."""
    self._ensure_initialized()
    
    use_bwrap = self._check_bwrap_available()
    
    if use_bwrap:
        return self._execute_with_bwrap(command)
    else:
        return self._execute_simple(command)

def _execute_with_bwrap(self, command: str) -> ExecuteResponse:
    """Execute command with bubblewrap isolation."""
    
    # Prepare activation command
    activation = "[ -f /.venv/bin/activate ] && source /.venv/bin/activate || true"
    full_cmd = f"{activation} && {command}"
    
    # Build bwrap command
    bwrap_args = [
        "bwrap",
        # System binaries (read-only)
        "--ro-bind /usr /usr",
        "--ro-bind /lib /lib",
        "--ro-bind /lib64 /lib64",
        "--ro-bind /bin /bin",
        "--ro-bind /etc /etc",
        # System filesystems
        "--proc /proc",
        "--dev /dev",
        "--tmpfs /tmp",
        # Workspace as root (read-write)
        f"--bind {self._base_path} /",
        # R2 bucket for symlinks
        f"--bind /mnt/r2/{self._bucket_name} /mnt/r2/{self._bucket_name}",
        # Working directory
        "--chdir /",
        # Isolation
        "--unshare-all",
        "--share-net",
        "--die-with-parent",
        # Environment
        "--setenv HOME /",
        "--setenv USER user",
        "--setenv PYTHONUNBUFFERED 1",
        "--setenv PYTHONDONTWRITEBYTECODE 1",
        "--setenv MPLBACKEND Agg",
        "--setenv PATH /.venv/bin:/node_modules/.bin:/usr/bin:/bin",
        # Execute
        "bash -c",
        shlex.quote(full_cmd)
    ]
    
    full_command = " ".join(bwrap_args)
    
    print(f"[SandboxBackend.execute] 🔒 Using bubblewrap isolation", flush=True)
    print(f"[SandboxBackend.execute] Workspace: {self._base_path} → /", flush=True)
    print(f"[SandboxBackend.execute] Command: {command}", flush=True)
    
    result = self._sandbox.commands.run(full_command, timeout=30000)
    
    return ExecuteResponse(
        stdout=result.stdout,
        stderr=result.stderr,
        exit_code=result.exit_code
    )

def _execute_simple(self, command: str) -> ExecuteResponse:
    """Fallback: simple directory-based execution."""
    # Current implementation
    ...
```

**Step 3: Test**
```python
# Test basic commands
result = sandbox.execute("pwd")
# Should output: /

result = sandbox.execute("ls -la /")
# Should show workspace contents

result = sandbox.execute("python -c 'import os; print(os.getcwd())'")
# Should output: /

result = sandbox.execute("echo 'test' > /file.txt && cat /file.txt")
# Should create file in workspace
```

---

## Comparison: Current vs Bubblewrap

### Current Implementation (Simple cd)

```bash
cd /mnt/r2/bucket/threads/thread_id && \
export PYTHONUNBUFFERED=1 && \
... && \
python script.py
```

**Agent sees:**
- `pwd` → `/mnt/r2/bucket/threads/thread_id`
- `ls /` → System root (/)
- Files in `/mnt/r2/bucket/threads/thread_id/`

### With Bubblewrap

```bash
bwrap --bind /mnt/r2/bucket/threads/thread_id / ... \
  python script.py
```

**Agent sees:**
- `pwd` → `/`
- `ls /` → Workspace contents
- Files in `/` (actually workspace)

---

## Example Scenarios

### Scenario 1: Agent creates file in root

**Command:** `echo 'data' > /output.txt`

**Current:**
- Creates `/output.txt` (system root) - ❌ Wrong location!

**With Bubblewrap:**
- Creates `/output.txt` (workspace) - ✅ Correct!

### Scenario 2: Agent lists root

**Command:** `ls -la /`

**Current:**
- Shows system directories (bin, usr, etc) - ❌ Confusing!

**With Bubblewrap:**
- Shows workspace files - ✅ Clean!

### Scenario 3: Agent uses absolute paths

**Command:** `python /script.py`

**Current:**
- Looks for `/script.py` (system root) - ❌ Not found!

**With Bubblewrap:**
- Looks for `/script.py` (workspace) - ✅ Works!

---

## Migration Path

### Phase 1: Add Bubblewrap Support (Recommended)
1. Check if bwrap is available
2. Use bwrap if available, fallback to current
3. Test thoroughly
4. Make bwrap default

### Phase 2: Optional Enhancements
1. Add Devbox for package management
2. Pre-configure common environments
3. Add environment templates

---

## Testing Checklist

- [ ] bwrap available in E2B sandbox
- [ ] Files created in workspace (not system root)
- [ ] `ls /` shows workspace contents
- [ ] Python venv activates correctly
- [ ] Node modules accessible
- [ ] Symlinks (.solven, .ticket) work
- [ ] Network access preserved
- [ ] Performance acceptable
- [ ] Multiple threads don't interfere
- [ ] Error handling works

---

## Summary

**For your specific need ("ls / shows workspace"):**

1. ✅ **Use Bubblewrap** - Best option
   - Reliable, simple, fast
   - Makes workspace appear as `/`
   - Used by Flatpak in production

2. ⚠️ **Consider Devbox** - For package management
   - Excellent for reproducible environments
   - But doesn't change root directory view
   - Could complement bubblewrap

3. ❌ **Don't use proot** - Already removed
   - Unreliable, buggy
   - We made the right call removing it

**Recommendation:**
Implement bubblewrap support. It's exactly what you need - simple, reliable, and makes the workspace appear as `/` to the agent.

```bash
# Agent executes
ls -la /

# With bubblewrap, agent sees:
drwxr-xr-x .solven/
drwxr-xr-x .ticket/
drwxr-xr-x .venv/
drwxr-xr-x node_modules/
-rw-r--r-- pyproject.toml
-rw-r--r-- package.json
# ... user files ...

# Perfect! 🎉
```

