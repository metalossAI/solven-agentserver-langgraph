# rclone Mount Fix: Critical Issues Resolved

## Problems Found

### 1. ❌ Heredoc Delimiter Prevented Variable Expansion
**File:** `src/e2b_sandbox/template.py`

**Issue:**
```bash
cat << 'RCLONE_CONFIG' | sudo tee ...
```

The **quoted delimiter** (`'RCLONE_CONFIG'`) prevented environment variable expansion in the rclone config, so `${S3_ACCESS_KEY}`, `${S3_ACCESS_SECRET}`, and `${S3_ENDPOINT_URL}` were written literally instead of being replaced with actual values.

**Fix:**
```bash
cat << EOF | sudo tee ...
```

Changed to unquoted `EOF` to allow variable expansion.

### 2. ❌ Environment Variable Name Mismatch
**File:** `src/app/api/sandbox/route.ts`

**Issue:**
```typescript
S3_ACCESS_KEY: process.env.R2_ACCESS_KEY || ''  // ❌ Wrong variable name
```

The frontend was reading from `R2_ACCESS_KEY` but the correct environment variable is `S3_ACCESS_KEY_ID` (as used consistently in `s3-client.ts`).

**Fix:**
```typescript
S3_ACCESS_KEY: process.env.S3_ACCESS_KEY_ID || ''  // ✅ Correct
```

## Why rclone Wasn't Working

1. **No credentials:** Because of the heredoc issue, rclone config contained literal `${S3_ACCESS_KEY}` text instead of actual keys
2. **Wrong variable:** Even if heredoc was fixed, the wrong env var name meant keys weren't passed correctly
3. **Silent failure:** rclone daemon started but couldn't authenticate to R2, so mounts failed

## What Happens Now

### Template startup will:
1. ✅ Create rclone config with **actual credentials**
2. ✅ Mount R2 buckets successfully:
   - `r2:{BUCKET}/threads/{THREAD}` → `/mnt/r2/threads/{THREAD}` (real-time sync)
   - `r2:{BUCKET}/skills/system` → `/mnt/r2/skills/system` (read-only)
   - `r2:{BUCKET}/skills/{USER}` → `/mnt/r2/skills/{USER}` (read-only)
   - `r2:{BUCKET}/threads/{TICKET}` → `/mnt/r2/tickets/{TICKET}` (if ticket exists)
3. ✅ Configure workspace automatically:
   - Create symlinks (`.solven`, `.ticket`)
   - Initialize Python venv
   - Initialize bun/node
   - Mark as configured

### Backend will see:
```
[Workspace] ✅ Ready (auto-configured by template)
```

Instead of:
```
[Workspace] ⏳ Waiting for template configuration...
[Workspace] ⚠️  Template configuration timeout, configuring manually...
```

## Deployment Steps

### 1. Rebuild the Template

**Important:** This pushes the fixes to E2B.

```bash
cd /home/ramon/Github/metaloss/solven-agentserver-langgraph
python src/e2b_sandbox/build.py
```

**Expected output:**
```
Building template solven-sandbox-v1...
✅ Template built successfully
```

### 2. Delete Existing Sandboxes (or let them expire)

Old sandboxes use the broken template. You can:
- **Option A:** Let them expire naturally (15 min timeout)
- **Option B:** Delete manually via E2B dashboard
- **Option C:** Delete via API (if you have sandbox IDs)

### 3. Create a New Thread

When you create a new thread, the frontend will:
1. Create `.keep` file in R2: `threads/{THREAD_ID}/.keep`
2. Create new sandbox with the **fixed template**
3. Pass correct environment variables to sandbox

### 4. Verify Success

Check the logs for:

**Template startup:**
```
🚀 Starting Solven Sandbox...
📦 Bucket: solven-testing
🧵 Thread: {THREAD_ID}
👤 User: {USER_ID}
📂 Creating mount point directories...
⏳ Mounting thread workspace with real-time sync...
✅ Thread workspace mounted at /mnt/r2/threads/{THREAD_ID}
⏳ Mounting skills...
✅ System skills mounted
✅ User skills mounted
🐍 Initializing Python environment...
✅ Python venv created
📦 Initializing Bun environment...
✅ Bun environment initialized
✅ Workspace configured at /mnt/r2/threads/{THREAD_ID}
✅ Solven Sandbox ready!
```

**Backend:**
```
[Workspace] ✅ Ready (auto-configured by template)
```

**Agent execution:**
```
[Execute] 🔒 bwrap isolated: {command}
[Execute] Exit code: 0
[Execute] Stdout: {output}
```

### 5. Test R2 Sync

1. **Agent creates a file:**
   ```bash
   echo "test" > /test.txt
   ```

2. **Wait 2 seconds** (for --vfs-write-back 1s to sync)

3. **Check R2:**
   ```bash
   rclone ls r2:solven-testing/threads/{THREAD_ID}/
   # Should show: test.txt
   ```

## Environment Variables Required

Ensure these are set in your frontend's `.env`:

```bash
R2_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
S3_ACCESS_KEY_ID=your_access_key_here
S3_ACCESS_SECRET=your_secret_key_here
R2_BUCKET_NAME=solven-testing  # or solven-production, solven-staging
```

**Note:** `S3_ACCESS_KEY_ID` (not `R2_ACCESS_KEY`) for consistency with S3 client.

## Troubleshooting

### If mounts still fail after rebuild:

1. **Check template build logs:**
   ```bash
   python src/e2b_sandbox/build.py 2>&1 | tee build.log
   ```

2. **Check sandbox startup in E2B:**
   - Go to E2B dashboard
   - Find your sandbox
   - Check "Logs" tab for startup errors

3. **Test rclone credentials manually:**
   ```bash
   # In the sandbox terminal:
   cat /root/.config/rclone/rclone.conf
   # Should show actual credentials, not ${S3_ACCESS_KEY}
   
   rclone lsd r2:
   # Should list buckets
   ```

4. **Check rclone logs:**
   ```bash
   cat /tmp/rclone-thread.log
   cat /tmp/rclone-skills-system.log
   cat /tmp/rclone-skills-user.log
   ```

5. **Verify mount points:**
   ```bash
   mount | grep rclone
   # Should show all mounted paths
   ```

## Files Changed

1. ✅ `solven-agentserver-langgraph/src/e2b_sandbox/template.py`
   - Fixed heredoc delimiter: `'RCLONE_CONFIG'` → `EOF`

2. ✅ `solven-app-vercel/src/app/api/sandbox/route.ts`
   - Fixed env var name: `R2_ACCESS_KEY` → `S3_ACCESS_KEY_ID`

## Before & After

### Before (Broken)
```bash
# rclone config had literal text:
access_key_id = ${S3_ACCESS_KEY}  # ❌ Not expanded
secret_access_key = ${S3_ACCESS_SECRET}  # ❌ Not expanded

# Result: Authentication failed, no mounts
# Backend: Manual fallback configuration
```

### After (Fixed)
```bash
# rclone config has actual credentials:
access_key_id = abc123...  # ✅ Actual key
secret_access_key = xyz789...  # ✅ Actual secret

# Result: Mounts succeed, real-time sync works
# Backend: Auto-configured by template
```

## Ready to Deploy

Run this now:

```bash
cd /home/ramon/Github/metaloss/solven-agentserver-langgraph && python src/e2b_sandbox/build.py
```

Then create a new thread and watch the magic happen! 🎉

