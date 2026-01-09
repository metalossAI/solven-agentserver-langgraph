# rclone Mount Fix: Ensuring R2 Paths Exist for New Threads

## Problem

When creating a new thread, the corresponding R2 path (`threads/{THREAD_ID}`) doesn't exist yet because no files have been uploaded. This causes `rclone mount` to fail during sandbox initialization, as rclone cannot mount a non-existent S3 path.

**Symptoms:**
- Mount verification shows "not active" even though rclone process is running
- Template startup logs show mount failures
- Workspace writes may not sync to R2

## Solution

### 1. Create `.keep` File on Thread Creation ✅

When a new thread is created, immediately create a `.keep` file in R2 to ensure the path exists:

**File:** `solven-app-vercel/src/app/api/lg/threads/route.ts`

```typescript
// After thread creation
const s3Client = getS3Client();
const keepFilePath = `threads/${thread.thread_id}/.keep`;
await s3Client.uploadFile(keepFilePath, '', {
  'created-at': new Date().toISOString(),
  'purpose': 'Ensure thread directory exists in R2 for rclone mount'
});
```

This guarantees that when the sandbox starts and attempts to mount `r2:${BUCKET}/threads/${THREAD_ID}`, the path exists in R2.

### 2. Improved Mount Verification with Retries ✅

**Template (`src/e2b_sandbox/template.py`):**
- Increased initial sleep from 3s to 5s after starting rclone
- Added retry loop (3 attempts with 2s between each)
- Better logging with emojis (⏳, ✓, ❌) for mount status
- Displays rclone logs on failure

**Backend (`src/sandbox_backend.py`):**
- `_verify_rclone_mounts()` now retries up to 5 times with 2s delay
- Logs mount readiness with attempt counter
- Shows rclone process status and logs on final failure
- Allows time for rclone to initialize before declaring failure

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ Thread Creation Flow                                        │
├─────────────────────────────────────────────────────────────┤
│                                                              │
│ 1. Frontend: POST /api/lg/threads                           │
│    └─> Create thread in LangGraph                           │
│    └─> Create .keep file in R2: threads/{THREAD_ID}/.keep   │
│                                                              │
│ 2. Frontend: GET /api/sandbox?threadId=xxx                  │
│    └─> Create E2B sandbox with env vars:                    │
│        - R2_BUCKET_NAME                                      │
│        - THREAD_ID                                           │
│        - USER_ID                                             │
│        - TICKET_ID (optional)                                │
│                                                              │
│ 3. E2B Template start_cmd:                                  │
│    └─> Mount r2:${BUCKET}/threads/${THREAD} (exists now!)   │
│    └─> Mount r2:${BUCKET}/skills/system                     │
│    └─> Mount r2:${BUCKET}/skills/${USER}                    │
│    └─> Mount r2:${BUCKET}/threads/${TICKET} (if set)        │
│                                                              │
│ 4. Backend SandboxBackend.__init__:                         │
│    └─> Verify mounts with retries                           │
│    └─> Configure workspace (symlinks, venv, bun)            │
│                                                              │
└─────────────────────────────────────────────────────────────┘
```

## Mount Settings (Unchanged)

Thread and ticket mounts use optimized settings for fast sync:
```bash
--allow-other \
--vfs-cache-mode full \
--vfs-links \
--copy-links \
--poll-interval 10s \
--dir-cache-time 10s \
--daemon
```

Skills mounts use the same settings (read-only).

## Improved Mount Verification (v2)

After initial implementation, we found that `mountpoint -q` doesn't always detect FUSE mounts correctly. Enhanced verification now:

**Template:**
- Increased initial wait from 5s to 8s
- Increased retries from 3 to 5 with 3s delays (up to 23s total wait)
- Checks both `mountpoint` AND `mount | grep`
- Shows detailed diagnostics on failure (process status, mount list, rclone logs)
- **No longer fails sandbox creation** - warns and continues to let backend handle

**Backend:**
- Increased retries from 5 to 8 with 3s delays
- Uses both `mountpoint` and `mount | grep` for verification
- Shows full rclone process info (`pgrep -af`)
- Displays complete rclone logs on failure
- Shows all FUSE/rclone mounts in system

## Rebuilding the Template

**Important:** Existing sandboxes use the old template. You must rebuild:

```bash
cd solven-agentserver-langgraph
python src/e2b_sandbox/build.py
```

This will:
1. Build the new template with improved mount verification
2. Update the E2B template in the cloud
3. New sandboxes will use the updated template

**Note:** Existing sandboxes will continue to use the old template until you delete them and create new ones.

## Testing

1. **Rebuild the template (REQUIRED):**
   ```bash
   cd solven-agentserver-langgraph
   python src/e2b_sandbox/build.py
   ```

2. **Create a new thread:**
   ```bash
   curl -X POST /api/lg/threads \
     -H "Content-Type: application/json" \
     -d '{"user_id": "...", "company_id": "..."}'
   ```

3. **Verify `.keep` file exists in R2:**
   ```bash
   rclone ls r2:solven-testing/threads/{THREAD_ID}/
   # Should show: 0 .keep
   ```

4. **Check sandbox mount logs:**
   - Look for "✓ Thread workspace mounted successfully"
   - Backend should show "[rclone] ✓ Workspace mount active"
   - If mount verification fails, you'll see detailed diagnostics

## Debugging Mount Issues

If mounts still fail after rebuild:

1. **Check template startup logs:**
   ```bash
   # In E2B terminal or via sandbox.commands.run():
   cat /tmp/rclone-thread.log
   ```

2. **Check if mount exists:**
   ```bash
   mount | grep rclone
   # Should show: r2:bucket/threads/xxx on /mnt/r2/threads/xxx
   ```

3. **Check rclone process:**
   ```bash
   ps aux | grep rclone
   # Should show running rclone mount processes
   ```

4. **Test R2 connectivity:**
   ```bash
   rclone ls r2:solven-testing/threads/{THREAD_ID}/
   # Should list .keep file
   ```

5. **Manual mount test:**
   ```bash
   # Kill existing mount
   pkill rclone
   
   # Try manual mount (foreground for debugging)
   rclone mount r2:solven-testing/threads/{THREAD_ID} /mnt/test \
     --allow-other \
     --vfs-cache-mode full \
     -vv  # Verbose logging
   ```

## Fallback Behavior

If mounts fail (skills/tickets), the system creates empty local directories as fallbacks:
- System skills: continues with empty directory (non-critical)
- User skills: continues with empty directory (non-critical)
- Ticket: continues with empty directory (non-critical)
- **Thread workspace: FATAL** - exits with error (critical for operation)

## Files Modified

1. ✅ `solven-app-vercel/src/app/api/lg/threads/route.ts` - Create `.keep` file
2. ✅ `solven-agentserver-langgraph/src/e2b_sandbox/template.py` - Improved mount verification
3. ✅ `solven-agentserver-langgraph/src/sandbox_backend.py` - Retry logic for mount verification

## Next Steps

If mount issues persist:
1. Check R2 credentials and endpoint configuration
2. Verify `.keep` file is created (check S3 browser or `rclone ls`)
3. Review template startup logs: `cat /tmp/rclone-thread.log`
4. Ensure E2B template has correct environment variables
5. Test with `rclone mount` directly in E2B terminal to isolate issues

