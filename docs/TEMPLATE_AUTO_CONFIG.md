# Template Auto-Configuration: Workspace Ready on Startup

## Overview

The template now **auto-configures the entire workspace** during sandbox startup, eliminating the need for backend-driven configuration. When an agent connects, the workspace is already mounted, symlinked, and ready with Python/Node environments.

## Key Benefits

1. **Real-time R2 sync** with `--vfs-write-back 1s` (instant writes)
2. **Zero-wait agent connection** - workspace is pre-configured
3. **Simplified backend** - just verifies configuration, doesn't create it
4. **Consistent environment** - all sandboxes start identically
5. **Workspace persistence** - everything lives in `/mnt/r2/threads/{THREAD_ID}`

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│ Sandbox Startup Flow (Template start_cmd)                     │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│ 1. Create rclone config from env vars                          │
│    └─> /root/.config/rclone/rclone.conf                        │
│                                                                 │
│ 2. Mount R2 paths with REAL-TIME SYNC                          │
│    ├─> r2:{BUCKET}/threads/{THREAD}  → /mnt/r2/threads/{THREAD}│
│    │   (--vfs-write-back 1s, --poll-interval 5s)               │
│    ├─> r2:{BUCKET}/skills/system     → /mnt/r2/skills/system   │
│    │   (read-only, --poll-interval 30s)                        │
│    ├─> r2:{BUCKET}/skills/{USER}     → /mnt/r2/skills/{USER}   │
│    │   (read-only, --poll-interval 30s)                        │
│    └─> r2:{BUCKET}/threads/{TICKET}  → /mnt/r2/tickets/{TICKET}│
│        (if TICKET_ID set, --vfs-write-back 1s)                 │
│                                                                 │
│ 3. Configure workspace at /mnt/r2/threads/{THREAD}             │
│    ├─> Create symlinks:                                        │
│    │   ├─> .solven   → /mnt/r2/skills/{USER}                   │
│    │   ├─> .ticket   → /mnt/r2/tickets/{TICKET} (if exists)    │
│    │   └─> tmp/      (for isolated /tmp in bwrap)              │
│    ├─> Initialize Python: uv venv .venv                        │
│    ├─> Initialize Node: bun init (package.json)                │
│    └─> Create marker: .workspace_configured                    │
│                                                                 │
│ 4. Keep sandbox alive: tail -f /dev/null                       │
│                                                                 │
└────────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────────┐
│ Backend Connection Flow (SandboxBackend.__init__)              │
├────────────────────────────────────────────────────────────────┤
│                                                                 │
│ 1. Connect to E2B sandbox                                      │
│                                                                 │
│ 2. Check for .workspace_configured marker                      │
│    ├─> Found: ✅ Workspace ready                                │
│    └─> Not found: ⏳ Wait up to 30s                             │
│        └─> Still not found: ⚠️  Manual fallback configuration   │
│                                                                 │
│ 3. Agent can immediately use workspace                         │
│    └─> All commands run in bwrap with workspace mounted as /   │
│                                                                 │
└────────────────────────────────────────────────────────────────┘
```

## Real-Time Sync Configuration

### Thread & Ticket Workspaces (Writable)
```bash
--vfs-write-back 1s      # Sync writes to R2 after 1 second
--poll-interval 5s       # Check for external changes every 5 seconds
--dir-cache-time 5s      # Cache directory listings for 5 seconds
```

**Behavior:** Changes made by agents are visible in R2 within 1-2 seconds.

### Skills (Read-Only)
```bash
--read-only              # No writes allowed
--poll-interval 30s      # Check for updates every 30 seconds
--dir-cache-time 30s     # Cache directory listings for 30 seconds
```

**Behavior:** Skills are cached longer since they change infrequently.

## Workspace Structure

```
/mnt/r2/threads/{THREAD_ID}/     ← Thread workspace (mounted as / in bwrap)
├── .workspace_configured         ← Configuration marker
├── .solven/                      ← Symlink to /mnt/r2/skills/{USER_ID}
│   └── (user skills)
├── .ticket/                      ← Symlink to /mnt/r2/tickets/{TICKET_ID}
│   └── (ticket files)
├── tmp/                          ← Isolated /tmp for bwrap
├── .venv/                        ← Python virtual environment
│   ├── bin/python
│   └── lib/python3.x/
├── package.json                  ← Node/Bun configuration
├── node_modules/                 ← Node packages (installed on-demand)
└── (agent-generated files)       ← All agent work lives here
```

## Environment Variables (Set by Frontend)

When creating a sandbox, the frontend must pass:

```typescript
{
  envs: {
    S3_ENDPOINT_URL: process.env.R2_ENDPOINT_URL,
    S3_ACCESS_KEY: process.env.R2_ACCESS_KEY,
    S3_ACCESS_SECRET: process.env.S3_ACCESS_SECRET,
    R2_BUCKET_NAME: "solven-testing",  // or production/staging
    THREAD_ID: "uuid-here",
    USER_ID: "uuid-here",
    TICKET_ID: "uuid-here"  // optional
  }
}
```

**Important:** `THREAD_ID`, `USER_ID`, and `R2_BUCKET_NAME` are required. The template skips mounting if these are not set (template build mode).

## Deployment Steps

### 1. Rebuild the Template

```bash
cd solven-agentserver-langgraph
python src/e2b_sandbox/build.py
```

This pushes the new template to E2B. **Existing sandboxes** will continue using the old template.

### 2. Create New Sandboxes

Delete old sandboxes or let them expire. New sandboxes will use the updated template with auto-configuration.

### 3. Verify Configuration

Check logs for:
```
🚀 Starting Solven Sandbox...
✅ Thread workspace mounted at /mnt/r2/threads/{THREAD_ID}
✅ System skills mounted
✅ User skills mounted
✅ Python venv created
✅ Bun environment initialized
✅ Workspace configured at /mnt/r2/threads/{THREAD_ID}
✅ Solven Sandbox ready!
```

Backend should show:
```
[Workspace] ✅ Ready (auto-configured by template)
```

## Troubleshooting

### Template Configuration Timeout

If backend shows `⏳ Waiting for template configuration...` for 30+ seconds:

1. **Check R2 credentials** - ensure env vars are set correctly
2. **Check `.keep` file** - ensure frontend creates it on thread creation
3. **Check template logs:**
   ```bash
   cat /tmp/rclone-thread.log
   cat /tmp/rclone-skills-system.log
   cat /tmp/rclone-skills-user.log
   ```
4. **Check rclone processes:**
   ```bash
   ps aux | grep rclone
   ```
5. **Manual mount test:**
   ```bash
   rclone mount r2:solven-testing/threads/{THREAD_ID} /tmp/test \
     --allow-other --vfs-cache-mode full --vfs-write-back 1s -vv
   ```

### Workspace Not Syncing to R2

If changes aren't appearing in R2:

1. **Check mount status:**
   ```bash
   mount | grep rclone
   # Should show: r2:bucket/threads/xxx on /mnt/r2/threads/xxx
   ```

2. **Check VFS cache:**
   ```bash
   # Force flush cache
   pkill -HUP -f 'rclone.*threads/{THREAD_ID}'
   ```

3. **Verify write-back setting:**
   ```bash
   ps aux | grep rclone
   # Should show: --vfs-write-back 1s
   ```

4. **Test write:**
   ```bash
   echo "test" > /mnt/r2/threads/{THREAD_ID}/test.txt
   sleep 2
   rclone ls r2:solven-testing/threads/{THREAD_ID}/
   # Should show: test.txt
   ```

### Python/Node Environment Issues

If `.venv` or `node_modules` aren't created:

1. **Check workspace path:**
   ```bash
   ls -la /mnt/r2/threads/{THREAD_ID}/
   ```

2. **Check uv/bun availability:**
   ```bash
   which uv
   which bun
   ```

3. **Manual setup:**
   ```bash
   cd /mnt/r2/threads/{THREAD_ID}
   uv venv .venv
   bun init -y
   ```

## Performance Characteristics

### Mount Initialization Time
- **Thread workspace:** ~10 seconds (with retries)
- **Skills (system + user):** ~5 seconds each
- **Ticket workspace:** ~5 seconds
- **Total startup:** ~20-25 seconds

### Sync Latency
- **Agent write → R2:** 1-2 seconds
- **R2 → Agent read:** 5-10 seconds (poll interval)

### Cache Behavior
- **Directory listings:** Cached for 5s (workspaces) or 30s (skills)
- **File contents:** Cached until modified (VFS cache mode: full)

## Comparison: Old vs New

| Aspect | Old Approach | New Approach |
|--------|--------------|--------------|
| **Configuration** | Backend on first connection | Template on startup |
| **Agent Wait Time** | 30-60 seconds | 0 seconds (pre-configured) |
| **R2 Sync** | `--poll-interval 10s` only | `--vfs-write-back 1s` (real-time) |
| **Mount Strategy** | Entire buckets | Specific paths only |
| **Environment Setup** | Backend calls `uv`, `bun` | Template pre-initializes |
| **Symlinks** | Backend creates | Template creates |
| **Verification** | Backend manual checks | Template + mount verification |

## Files Modified

1. ✅ `src/e2b_sandbox/template.py` - Complete rewrite with auto-configuration
2. ✅ `src/sandbox_backend.py` - Simplified to wait for template configuration
3. ✅ `src/app/api/lg/threads/route.ts` - Creates `.keep` file on thread creation

## Next Steps

1. **Rebuild template:** `python src/e2b_sandbox/build.py`
2. **Test with new thread:** Create thread, verify `.keep` file, connect agent
3. **Monitor logs:** Check for "✅ Solven Sandbox ready!"
4. **Verify R2 sync:** Make changes, check R2 after 2 seconds


