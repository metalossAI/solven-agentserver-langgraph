#!/bin/bash
# Mount a single S3 path using rclone
# Arguments:
#   $1: S3 bucket name
#   $2: S3 path (e.g., "threads/thread-123")
#   $3: Local mount point (e.g., "/mnt/r2/threads/thread-123")
#   $4: Log file path (e.g., "/tmp/rclone-thread.log")

set -e

if [ $# -ne 4 ]; then
    echo "ERROR: Usage: $0 <bucket> <s3_path> <mount_point> <log_file>" >&2
    exit 1
fi

BUCKET="$1"
S3_PATH="$2"
MOUNT_POINT="$3"
LOG_FILE="$4"

echo "[mount] Creating mount point: ${MOUNT_POINT}"
# Create mount point
if ! sudo mkdir -p "${MOUNT_POINT}"; then
    echo "ERROR: Failed to create mount point ${MOUNT_POINT}" >&2
    exit 1
fi

echo "[mount] Mounting s3remote:${BUCKET}/${S3_PATH} to ${MOUNT_POINT}"
# Mount with rclone in background using nohup (more reliable than --daemon)
nohup sudo rclone --config /root/.config/rclone/rclone.conf mount \
  "s3remote:${BUCKET}/${S3_PATH}" \
  "${MOUNT_POINT}" \
  --allow-other \
  --vfs-cache-mode full \
  --vfs-write-back 1s \
  --poll-interval 2s \
  --dir-cache-time 2s \
  --log-file "${LOG_FILE}" \
  --log-level INFO > /dev/null 2>&1 &

# Store PID for verification
RCLONE_PID=$!
echo "[mount] rclone started with PID: ${RCLONE_PID}"

# Wait for mount to initialize
echo "[mount] Waiting for mount to initialize..."
sleep 3

# Check if rclone process is still running
if ! ps -p ${RCLONE_PID} > /dev/null 2>&1; then
    echo "ERROR: rclone process died immediately" >&2
    echo "Check log file: ${LOG_FILE}" >&2
    sudo tail -20 "${LOG_FILE}" >&2 2>/dev/null || echo "No log available" >&2
    exit 1
fi

# Verify mount is accessible (with retry). Use sudo so we run as same user that owns the mount.
echo "[mount] Verifying mount accessibility..."
for i in 1 2 3 4 5; do
    if sudo ls "${MOUNT_POINT}" >/dev/null 2>&1; then
        echo "[mount] Mount verified on attempt $i"
        break
    fi
    if [ $i -eq 5 ]; then
        echo "ERROR: Mount point ${MOUNT_POINT} is not accessible after 5 attempts" >&2
        echo "Check log file: ${LOG_FILE}" >&2
        sudo tail -20 "${LOG_FILE}" >&2 2>/dev/null || echo "No log available" >&2
        exit 1
    fi
    echo "[mount] Attempt $i failed, retrying..."
    sleep 1
done

echo "[rclone] ✓ Mounted ${BUCKET}/${S3_PATH} to ${MOUNT_POINT}"
exit 0

