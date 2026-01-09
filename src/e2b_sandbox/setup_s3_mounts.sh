#!/bin/bash
# setup_s3_mounts.sh
# Script to mount S3 buckets using Mountpoint for S3
# This script is executed at sandbox startup
# Environment variables are passed at runtime when sandboxes are created

# Don't exit on error - handle gracefully
set +e

BUCKET="${S3_BUCKET_NAME}"

echo "[mountpoint] === Starting S3 Mount Setup ==="
echo "[mountpoint] Bucket: ${BUCKET}"
echo "[mountpoint] Thread ID: ${THREAD_ID}"
echo "[mountpoint] User ID: ${USER_ID}"
echo "[mountpoint] Endpoint: ${S3_ENDPOINT_URL:-'AWS S3 (default)'}"

# Check if required credentials are available
# During template build, these may not be set, so we'll skip mount setup
if [ -z "${S3_ACCESS_KEY_ID}" ] || [ -z "${S3_ACCESS_SECRET}" ]; then
  echo "[mountpoint] ⚠️  S3 credentials not available (this is normal during template build)"
  echo "[mountpoint] Mount setup will be skipped. Credentials will be provided at sandbox creation time."
  # Create empty directories as placeholders
  sudo mkdir -p /mnt/s3/threads /mnt/s3/skills /mnt/s3/tickets
  sudo chown -R user:user /mnt/s3
  sudo ln -sf /mnt/s3 /mnt/r2 2>/dev/null || true
  echo "[mountpoint] ✓ Placeholder directories created"
  exit 0
fi

# Set up AWS credentials for Mountpoint
export AWS_ACCESS_KEY_ID="${S3_ACCESS_KEY_ID}"
export AWS_SECRET_ACCESS_KEY="${S3_ACCESS_SECRET}"
export AWS_REGION="${S3_REGION:-us-east-1}"

# Set endpoint if provided (for Supabase S3 or other S3-compatible storage)
if [ -n "${S3_ENDPOINT_URL}" ]; then
  export AWS_ENDPOINT_URL="${S3_ENDPOINT_URL}"
  echo "[mountpoint] Using custom S3 endpoint"
fi

# Function to mount and verify
mount_and_verify() {
  local remote_path=$1
  local mount_point=$2
  local name=$3
  
  echo "[mountpoint] Mounting ${name}: ${BUCKET}${remote_path} -> ${mount_point}"
  sudo mkdir -p ${mount_point}
  
  # Mount using Mountpoint for S3
  # Run in background with nohup so it doesn't block
  if [ -n "${S3_ENDPOINT_URL}" ]; then
    # With custom endpoint
    nohup sudo -E mount-s3 \
      --endpoint-url "${S3_ENDPOINT_URL}" \
      --allow-other \
      --file-mode 0666 \
      --dir-mode 0777 \
      "${BUCKET}${remote_path}" \
      "${mount_point}" > /tmp/mountpoint-logs/${name}.log 2>&1 &
  else
    # Standard AWS S3
    nohup sudo -E mount-s3 \
      --allow-other \
      --file-mode 0666 \
      --dir-mode 0777 \
      "${BUCKET}${remote_path}" \
      "${mount_point}" > /tmp/mountpoint-logs/${name}.log 2>&1 &
  fi
  
  # Wait a moment for mount to initialize
  sleep 2
  
  # Verify mount is active
  if mountpoint -q ${mount_point} 2>/dev/null || mount | grep -q "${mount_point}"; then
    echo "[mountpoint] ✓ ${name} mounted successfully"
    return 0
  else
    echo "[mountpoint] ❌ ${name} mount verification failed"
    echo "[mountpoint] Checking mount processes..."
    ps aux | grep "mount-s3.*${mount_point}" | grep -v grep || true
    echo "[mountpoint] Mount log:"
    cat /tmp/mountpoint-logs/${name}.log 2>/dev/null || echo "No log available"
    # Create empty directory as fallback
    sudo mkdir -p ${mount_point}
    return 1
  fi
}

# Create log directory
sudo mkdir -p /tmp/mountpoint-logs

echo "[mountpoint] === Mounting S3 Buckets ==="

# Mount thread workspace (critical)
if mount_and_verify "/threads/${THREAD_ID}" "/mnt/s3/threads/${THREAD_ID}" "thread"; then
  echo "[mountpoint] ✓ Thread workspace mounted"
else
  echo "[mountpoint] ⚠️  Thread workspace mount failed (using local directory as fallback)"
  sudo mkdir -p /mnt/s3/threads/${THREAD_ID}
fi

# Mount system skills (non-critical, read-only workload)
if mount_and_verify "/skills/system" "/mnt/s3/skills/system" "skills-system"; then
  echo "[mountpoint] ✓ System skills mounted"
else
  echo "[mountpoint] ⚠️  System skills mount failed (using local directory as fallback)"
  sudo mkdir -p /mnt/s3/skills/system
fi

# Mount user skills (non-critical, read-only workload)
if mount_and_verify "/skills/${USER_ID}" "/mnt/s3/skills/${USER_ID}" "skills-user"; then
  echo "[mountpoint] ✓ User skills mounted"
else
  echo "[mountpoint] ⚠️  User skills mount failed (using local directory as fallback)"
  sudo mkdir -p /mnt/s3/skills/${USER_ID}
fi

# Mount ticket workspace (optional, non-critical)
if [ -n "${TICKET_ID}" ]; then
  if mount_and_verify "/tickets/${TICKET_ID}" "/mnt/s3/tickets/${TICKET_ID}" "ticket"; then
    echo "[mountpoint] ✓ Ticket workspace mounted"
  else
    echo "[mountpoint] ⚠️  Ticket workspace mount failed (using local directory as fallback)"
    sudo mkdir -p /mnt/s3/tickets/${TICKET_ID}
  fi
fi

# Final verification and setup
echo "[mountpoint] === Mount Verification ==="
sleep 1

echo "[mountpoint] Active mounts:"
mount | grep mount-s3 || echo "[mountpoint] No mountpoint mounts found in mount table"

echo "[mountpoint] Mountpoint processes:"
ps aux | grep "[m]ount-s3" || echo "[mountpoint] No mount-s3 processes found"

echo "[mountpoint] Thread workspace contents:"
ls -la /mnt/s3/threads/${THREAD_ID}/ 2>/dev/null || echo "[mountpoint] Thread workspace is empty or not accessible"

echo "[mountpoint] Creating workspace marker"
sudo touch /mnt/s3/threads/${THREAD_ID}/.workspace_mounted 2>/dev/null || true

sudo chown -R user:user /mnt/s3

# Ensure backward compatibility: create symlink from /mnt/r2 to /mnt/s3
sudo ln -sf /mnt/s3 /mnt/r2 2>/dev/null || true

echo "[mountpoint] ✓ Sandbox initialization complete"

