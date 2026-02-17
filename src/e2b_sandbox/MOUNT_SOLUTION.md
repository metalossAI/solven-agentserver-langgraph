# S3 Mount Solution for E2B Sandboxes

## Problem
E2B's `set_start_cmd()` only runs during template BUILD, not when individual sandboxes are created. This means:
- Mounts configured in `set_start_cmd` happen once during build with empty THREAD_ID/USER_ID
- Environment variables passed at sandbox creation don't trigger the start command to re-run
- Individual sandboxes don't get their specific mounts

## Solution
1. **Template**: Create a mount script at `/usr/local/bin/mount-s3-buckets.sh` during template build
2. **Backend**: After creating each sandbox in `sandbox_backend.py`, execute the mount script with `sandbox.commands.run()`

## Implementation Steps

### 1. Update Template (`template.py`)
- Install rclone
- Create `/usr/local/bin/mount-s3-buckets.sh` script
- Set a simple start command that just keeps template alive
- DO NOT include S3 credentials in template env vars

### 2. Update Backend (`sandbox_backend.py`)
After `new_sandbox = await AsyncSandbox.create()`, add:
```python
# Mount S3 buckets
mount_result = await new_sandbox.commands.run("/usr/local/bin/mount-s3-buckets.sh")
print(f"[SandboxBackend] Mount script output: {mount_result.stdout}")
```

### 3. The Mount Script
The script at `/usr/local/bin/mount-s3-buckets.sh` should:
- Read env vars: S3_BUCKET_NAME, S3_ACCESS_KEY_ID, S3_ACCESS_SECRET, S3_ENDPOINT_URL, THREAD_ID, USER_ID
- Create rclone config
- Mount: threads/${THREAD_ID}, skills/system, skills/${USER_ID}, tickets/${TICKET_ID}
- Run rclone mounts in background with `--daemon`

## Why This Works
- Template build: Creates mount script, no credentials needed
- Sandbox creation: Backend passes credentials as env vars
- Post-creation: Backend runs mount script which reads env vars and performs mounts
- Each sandbox gets its own mounts with correct THREAD_ID/USER_ID

## Files to Modify
1. `src/e2b_sandbox/template.py` - Create mount script, simple start command
2. `src/sandbox_backend.py` - Add mount script execution after sandbox creation (line ~186)

