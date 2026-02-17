#!/bin/bash
# setup_rclone_mounts.sh
# Script to configure and mount S3 buckets using rclone
# This script is executed at sandbox startup
# Environment variables are passed at runtime when sandboxes are created

# Don't exit on error - handle gracefully
set +e

BUCKET="${S3_BUCKET_NAME}"

echo "[rclone] === Starting S3 Mount Setup ==="
echo "[rclone] Bucket: ${BUCKET}"
echo "[rclone] Thread ID: ${THREAD_ID}"
echo "[rclone] User ID: ${USER_ID}"
echo "[rclone] Endpoint: ${S3_ENDPOINT_URL:-'AWS S3 (default)'}"

# Check if required credentials are available
# During template build, these may not be set, so we'll skip mount setup
if [ -z "${S3_ACCESS_KEY_ID}" ] || [ -z "${S3_ACCESS_SECRET}" ]; then
  echo "[rclone] ⚠️  S3 credentials not available (this is normal during template build)"
  echo "[rclone] Mount setup will be skipped. Credentials will be provided at sandbox creation time."
  # Create empty directories as placeholders
  sudo mkdir -p /mnt/s3/threads /mnt/s3/skills /mnt/s3/tickets
  sudo chown -R user:user /mnt/s3
  sudo ln -sf /mnt/s3 /mnt/r2 2>/dev/null || true
  echo "[rclone] ✓ Placeholder directories created"
  exit 0
fi

# Create rclone config directory
sudo mkdir -p /root/.config/rclone

# Create rclone config for S3
echo "[rclone] Creating rclone configuration..."

# Build config content based on endpoint
if [ -n "${S3_ENDPOINT_URL}" ] && echo "${S3_ENDPOINT_URL}" | grep -q "supabase.co"; then
  # Supabase S3 requires path-style addressing
  echo "[rclone] Using Supabase S3 configuration (path-style)"
  CONFIG_CONTENT="[s3]
type = s3
provider = AWS
access_key_id = ${S3_ACCESS_KEY_ID}
secret_access_key = ${S3_ACCESS_SECRET}
endpoint = ${S3_ENDPOINT_URL}
region = ${S3_REGION:-us-east-1}
force_path_style = true"
elif [ -n "${S3_ENDPOINT_URL}" ]; then
  # Other S3-compatible storage (MinIO, etc.)
  echo "[rclone] Using S3-compatible storage configuration"
  CONFIG_CONTENT="[s3]
type = s3
provider = AWS
access_key_id = ${S3_ACCESS_KEY_ID}
secret_access_key = ${S3_ACCESS_SECRET}
endpoint = ${S3_ENDPOINT_URL}
region = ${S3_REGION:-us-east-1}"
else
  # Standard AWS S3 (no endpoint needed)
  echo "[rclone] Using standard AWS S3 configuration"
  CONFIG_CONTENT="[s3]
type = s3
provider = AWS
access_key_id = ${S3_ACCESS_KEY_ID}
secret_access_key = ${S3_ACCESS_SECRET}
region = ${S3_REGION:-us-east-1}"
fi

# Write config file
echo "${CONFIG_CONTENT}" | sudo tee /root/.config/rclone/rclone.conf > /dev/null

# Verify config was created
if [ ! -f /root/.config/rclone/rclone.conf ]; then
  echo "[rclone] ❌ ERROR: Failed to create rclone config file!"
  exit 1
fi

echo "[rclone] ✓ Config file created successfully"

# Test basic rclone connectivity (list bucket root)
echo "[rclone] Testing S3 connectivity..."
if sudo rclone --config /root/.config/rclone/rclone.conf lsd s3:${BUCKET} > /tmp/rclone-test.log 2>&1; then
  echo "[rclone] ✓ S3 connectivity OK"
else
  echo "[rclone] ⚠️  S3 connectivity test failed!"
  echo "[rclone] Error log:"
  cat /tmp/rclone-test.log
  echo "[rclone] Config file (sanitized):"
  sudo sed 's/secret_access_key = .*/secret_access_key = ***HIDDEN***/' /root/.config/rclone/rclone.conf
  echo "[rclone] Continuing anyway (mount may still work)..."
fi

# Function to mount and verify
mount_and_verify() {
  local remote_path=$1
  local mount_point=$2
  local log_file=$3
  local name=$4
  
  echo "[rclone] Mounting ${name}: ${remote_path} -> ${mount_point}"
  sudo mkdir -p ${mount_point}
  
  # Test rclone connectivity first
  echo "[rclone] Testing connectivity to s3:${BUCKET}${remote_path}..."
  if sudo rclone --config /root/.config/rclone/rclone.conf lsd s3:${BUCKET}${remote_path} > ${log_file}.test 2>&1; then
    echo "[rclone] ✓ Connectivity test passed"
  else
    echo "[rclone] ⚠️  Connectivity test failed (path may not exist yet, continuing...)"
    cat ${log_file}.test
  fi
  
  # Start mount with logging
  nohup sudo rclone --config /root/.config/rclone/rclone.conf mount s3:${BUCKET}${remote_path} ${mount_point} \
    --allow-other \
    --vfs-cache-mode full \
    --vfs-links \
    --copy-links \
    --vfs-write-back 1s \
    --poll-interval 2s \
    --dir-cache-time 2s \
    --daemon > ${log_file} 2>&1
  
  sleep 3
  
  # Verify mount is active
  if mountpoint -q ${mount_point} 2>/dev/null || mount | grep -q "${mount_point}"; then
    echo "[rclone] ✓ ${name} mounted successfully"
    return 0
  else
    echo "[rclone] ❌ ${name} mount verification failed"
    echo "[rclone] Checking rclone process..."
    ps aux | grep "rclone.*${mount_point}" | grep -v grep || true
    echo "[rclone] Mount log:"
    cat ${log_file}
    return 1
  fi
}

# Create log directory
sudo mkdir -p /tmp/rclone-logs

# Mount thread workspace (critical - must succeed)
if mount_and_verify "/threads/${THREAD_ID}" "/mnt/s3/threads/${THREAD_ID}" "/tmp/rclone-logs/thread.log" "Thread workspace"; then
  echo "[rclone] ✓ Thread workspace mounted"
else
  echo "[rclone] ❌ CRITICAL: Thread workspace mount failed!"
  echo "[rclone] Attempting to create empty directory as fallback..."
  sudo mkdir -p /mnt/s3/threads/${THREAD_ID}
fi

# Mount system skills (non-critical)
if mount_and_verify "/skills/system" "/mnt/s3/skills/system" "/tmp/rclone-logs/skills-system.log" "System skills"; then
  echo "[rclone] ✓ System skills mounted"
else
  echo "[rclone] ⚠️  System skills mount failed (creating empty directory)"
  sudo mkdir -p /mnt/s3/skills/system
fi

# Mount user skills (non-critical)
if mount_and_verify "/skills/${USER_ID}" "/mnt/s3/skills/${USER_ID}" "/tmp/rclone-logs/skills-user.log" "User skills"; then
  echo "[rclone] ✓ User skills mounted"
else
  echo "[rclone] ⚠️  User skills mount failed (creating empty directory)"
  sudo mkdir -p /mnt/s3/skills/${USER_ID}
fi

# Mount ticket workspace (optional, non-critical)
if [ -n "${TICKET_ID}" ]; then
  if mount_and_verify "/tickets/${TICKET_ID}" "/mnt/s3/tickets/${TICKET_ID}" "/tmp/rclone-logs/ticket.log" "Ticket workspace"; then
    echo "[rclone] ✓ Ticket workspace mounted"
  else
    echo "[rclone] ⚠️  Ticket workspace mount failed (creating empty directory)"
    sudo mkdir -p /mnt/s3/tickets/${TICKET_ID}
  fi
fi

# Final verification and setup
echo "[rclone] All mounts complete, verifying..."
sleep 2

echo "[rclone] Active mounts:"
mount | grep rclone || echo "[rclone] No rclone mounts found"

echo "[rclone] Rclone processes:"
ps aux | grep rclone | grep -v grep || echo "[rclone] No rclone processes found"

ls -la /mnt/s3/threads/${THREAD_ID}/ || echo "[rclone] Thread workspace is empty or not accessible"

echo "[rclone] Creating workspace marker"
sudo touch /mnt/s3/threads/${THREAD_ID}/.workspace_mounted 2>/dev/null || true

sudo chown -R user:user /mnt/s3

# Ensure backward compatibility: create symlink from /mnt/r2 to /mnt/s3
sudo ln -sf /mnt/s3 /mnt/r2 2>/dev/null || true

echo "[rclone] ✓ Sandbox initialization complete"

