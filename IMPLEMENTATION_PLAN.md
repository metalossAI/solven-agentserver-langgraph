# E2B S3 Mounts - Implementation Complete

## Issue Identified
E2B's `set_start_cmd()` runs only during template BUILD, not for each sandbox.  
Environment variables passed at sandbox creation don't trigger mount scripts to re-run.

## Solution Implemented
1. ✅ Removed S3 credentials from template env vars  
2. ✅ Template creates placeholder directories during build
3. ⚠️ **NEXT STEP**: Backend must run mount script after sandbox creation

## Required Changes

### File: `src/sandbox_backend.py` (Line ~186)

After this line:
```python
new_sandbox = await AsyncSandbox.create(
    template=sandbox_template,
    envs=envs,
    timeout=sandbox_timeout,
)
```

Add:
```python
# Mount S3 buckets after sandbox creation
try:
    print(f"[SandboxBackend] Mounting S3 buckets for thread {thread_id}...", flush=True)
    mount_script = f"""#!/bin/bash
set -e
sudo mkdir -p /root/.config/rclone

# Create rclone config
if [ -n "${{S3_ENDPOINT_URL}}" ]; then
  cat << EOF | sudo tee /root/.config/rclone/rclone.conf > /dev/null
[s3remote]
type = s3
provider = Other
access_key_id = ${{S3_ACCESS_KEY_ID}}
secret_access_key = ${{S3_ACCESS_SECRET}}
endpoint = ${{S3_ENDPOINT_URL}}
acl = private
EOF
else
  cat << EOF | sudo tee /root/.config/rclone/rclone.conf > /dev/null
[s3remote]
type = s3
provider = AWS
access_key_id = ${{S3_ACCESS_KEY_ID}}
secret_access_key = ${{S3_ACCESS_SECRET}}
region = ${{S3_REGION:-us-east-1}}
acl = private
EOF
fi

# Mount thread workspace
sudo mkdir -p /mnt/r2/threads/${{THREAD_ID}}
nohup sudo rclone --config /root/.config/rclone/rclone.conf mount s3remote:${{S3_BUCKET_NAME}}/threads/${{THREAD_ID}} /mnt/r2/threads/${{THREAD_ID}} --allow-other --vfs-cache-mode full --vfs-write-back 1s --daemon >/dev/null 2>&1 &
sleep 2

# Mount system skills
sudo mkdir -p /mnt/r2/skills/system
nohup sudo rclone --config /root/.config/rclone/rclone.conf mount s3remote:${{S3_BUCKET_NAME}}/skills/system /mnt/r2/skills/system --allow-other --vfs-cache-mode full --vfs-write-back 1s --daemon >/dev/null 2>&1 &
sleep 2

# Mount user skills  
sudo mkdir -p /mnt/r2/skills/${{USER_ID}}
nohup sudo rclone --config /root/.config/rclone/rclone.conf mount s3remote:${{S3_BUCKET_NAME}}/skills/${{USER_ID}} /mnt/r2/skills/${{USER_ID}} --allow-other --vfs-cache-mode full --vfs-write-back 1s --daemon >/dev/null 2>&1 &

sudo chown -R user:user /mnt/r2
echo "[rclone] Mounts complete"
"""
    
    mount_result = await new_sandbox.commands.run(mount_script)
    print(f"[SandboxBackend] Mount output: {mount_result.stdout}", flush=True)
    if mount_result.stderr:
        print(f"[SandboxBackend] Mount errors: {mount_result.stderr}", flush=True)
except Exception as e:
    print(f"[SandboxBackend] Warning: Failed to mount S3 buckets: {e}", flush=True)
```

This will execute the mount script for each sandbox with the correct THREAD_ID/USER_ID values.

## Summary
- ✅ Template builds without credentials
- ✅ Credentials passed at sandbox creation
- ⚠️ **ACTION NEEDED**: Add mount script execution to `sandbox_backend.py`

