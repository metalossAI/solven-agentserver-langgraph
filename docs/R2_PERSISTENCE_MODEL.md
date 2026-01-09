# R2 Persistence Model

## Clear Mental Model

### Architecture Overview
Each thread has a workspace that persists directly to Cloudflare R2. All files created by agents are immediately written to R2 and will appear in your Cloudflare dashboard.

```
Cloudflare R2 Bucket: solven-{env}
├── skills/
│   ├── system/                     ← System-wide skills
│   └── {user_id}/                  ← User-specific skills
└── threads/
    ├── {thread_id_1}/              ← Thread workspace (base_path)
    │   ├── .solven/skills/
    │   │   ├── system → /mnt/r2/{bucket}/skills/system
    │   │   └── {user_id} → /mnt/r2/{bucket}/skills/{user_id}
    │   ├── .ticket → /mnt/r2/{bucket}/threads/{ticket_id}
    │   ├── .venv/                  ← Python environment (persisted)
    │   ├── .bashrc                 ← Shell config (auto-activates venv)
    │   ├── .workspace_configured   ← Marker file
    │   ├── package.json            ← Node.js config
    │   ├── node_modules/           ← Node dependencies
    │   └── ...files...             ← All user files persist here
    └── {thread_id_2}/
        └── ...
```

### E2B Sandbox View
```
E2B Sandbox Filesystem:
/mnt/r2/solven-testing/            ← R2 bucket mounted via rclone
├── skills/
│   ├── system/
│   └── {user_id}/
└── threads/
    └── {thread_id}/               ← All operations happen here
```

### Backend Path Mapping
```python
# In sandbox_backend.py
_bucket_name = "solven-testing"
_r2_base_mount = "/mnt/r2/solven-testing"
_base_path = "/mnt/r2/solven-testing/threads/{thread_id}"  # Thread workspace root

# All file operations map to base_path (thread root):
write("/file.txt", ...) → /mnt/r2/solven-testing/threads/{thread_id}/file.txt
read("/file.txt")       → /mnt/r2/solven-testing/threads/{thread_id}/file.txt
execute("touch /f.txt") → Creates at /mnt/r2/solven-testing/threads/{thread_id}/f.txt
```

### Command Execution with Proot
Commands execute with proot for isolation while maintaining R2 persistence:

**Key Insight**: proot doesn't copy files or create overlays. It only intercepts system calls and translates paths. When proot root points to an R2 mount, all file operations still go to R2.

```bash
# Proot command structure:
proot -r /mnt/r2/solven-testing/threads/{thread_id} \
  -b /bin:/bin -b /usr:/usr ... \
  -b /mnt/r2/solven-testing:/mnt/r2/solven-testing \
  -w / \
  /bin/bash --login -c 'export HOME=/ USER=user && {command}'

# What happens:
# 1. Agent sees: / (which is the thread workspace)
# 2. Agent writes: /file.txt
# 3. Proot translates: /mnt/r2/solven-testing/threads/{thread_id}/file.txt
# 4. File persists to R2 ✅
# 5. Symlinks (.solven, .ticket) are accessible via bind-mounted /mnt/r2/
```

**Isolation + Persistence**:
- Agent sees only their files in `/` (via proot path translation)
- All writes go through to actual R2 mount (no overlays)
- Files persist to Cloudflare R2 immediately
- Agent cannot see or access other threads

### Persistence Guarantee
- ✅ All files written to `/mnt/r2/` persist to Cloudflare R2
- ✅ Files survive sandbox restarts
- ✅ Files are immediately visible in Cloudflare dashboard
- ✅ No overlays or temporary storage
- ✅ Direct writes to R2 mount

### Isolation Strategy
Each thread is isolated by:
1. **Proot Filesystem View**: Agent's `/` maps to `threads/{thread_id}/home/user/` on R2
2. **Path Translation**: proot intercepts syscalls and translates paths (no copies)
3. **Python**: Isolated `.venv` per thread
4. **Node.js**: Isolated `node_modules` per thread
5. **Environment**: HOME=/ (which maps to thread's directory)
6. **Bind Mounts**: System binaries available, but agent can't escape to sandbox root

**How Proot Maintains Persistence**:
- proot uses `ptrace` to intercept system calls
- When agent opens `/file.txt`, proot translates to actual R2 path
- No files are copied, no overlays are created
- All I/O operations go directly to R2 mount
- Files persist immediately to Cloudflare R2

**Isolation Guarantee**:
```bash
# Agent runs: ls /
# Proot translates to: ls /mnt/r2/solven-testing/threads/{thread_id}/home/user/
# Agent sees: Only their files ✅
# Cannot see: Other threads, sandbox files, system files
```

### Verification
You can verify files are persisting to R2:
1. Check Cloudflare R2 dashboard
2. Navigate to bucket: `solven-testing` (or your env)
3. Look for: `threads/{thread_id}/home/user/`
4. All files should be visible there

### Example Flow
```
1. Agent: write("/analysis.py", "...")
   → Creates: /mnt/r2/solven-testing/threads/abc123/analysis.py
   → Persists to R2 immediately

2. Agent: execute("python /analysis.py")
   → Proot translates to: python /mnt/r2/solven-testing/threads/abc123/analysis.py
   → Working directory is / (which is the thread workspace)

3. Agent: execute("touch /output.txt")
   → Creates: /mnt/r2/solven-testing/threads/abc123/output.txt
   → Persists to R2 immediately

4. Agent: ls /.solven/skills/system/
   → Symlink resolves to: /mnt/r2/solven-testing/skills/system/
   → Can access shared skills ✅

5. Check Cloudflare dashboard:
   → See: threads/abc123/analysis.py
   → See: threads/abc123/output.txt
   → See: threads/abc123/.solven/ (symlink)
```

## Key Design Principles

1. **Proot for Isolation, Not Overlays**
   - proot -r points directly to R2 mount location
   - No file copies, no overlays, no temporary storage
   - Only path translation via ptrace syscall interception
   
2. **R2 as Source of Truth**
   - All operations happen on `/mnt/r2/` mount
   - Files persist immediately to Cloudflare
   - Survives sandbox restarts
   
3. **Clean Agent View**
   - Agent sees `/` as their workspace
   - `ls /` shows only their files
   - Cannot access other threads or sandbox internals
   
4. **System Binary Access**
   - proot bind mounts `/bin`, `/usr`, `/lib`, etc.
   - Agent can use python, bun, and system tools
   - Tools execute normally, but filesystem is isolated

