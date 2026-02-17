#!/bin/bash
# Create rclone configuration for S3/Supabase storage
# Environment variables expected:
# - S3_ENDPOINT_URL (optional, for Supabase/custom S3)
# - S3_ACCESS_KEY_ID
# - S3_ACCESS_SECRET
# - S3_REGION (default: eu-central-1)

set -e

echo "[config] Creating rclone configuration..."

# Verify required environment variables
if [ -z "${S3_ACCESS_KEY_ID}" ] || [ -z "${S3_ACCESS_SECRET}" ]; then
    echo "ERROR: S3_ACCESS_KEY_ID and S3_ACCESS_SECRET must be set" >&2
    exit 1
fi

# Create rclone config directory
echo "[config] Creating config directory..."
if ! sudo mkdir -p /root/.config/rclone; then
    echo "ERROR: Failed to create rclone config directory" >&2
    exit 1
fi

# Build rclone config based on whether using Supabase or AWS S3
echo "[config] Writing rclone configuration..."
if [ -n "${S3_ENDPOINT_URL}" ]; then
  # Supabase S3 or custom endpoint
  echo "[config] Using custom endpoint: ${S3_ENDPOINT_URL}"
  cat << RCLONE_EOF | sudo tee /root/.config/rclone/rclone.conf > /dev/null
[s3remote]
type = s3
provider = Other
access_key_id = ${S3_ACCESS_KEY_ID}
secret_access_key = ${S3_ACCESS_SECRET}
endpoint = ${S3_ENDPOINT_URL}
acl = private
RCLONE_EOF
else
  # Standard AWS S3
  echo "[config] Using AWS S3 (region: ${S3_REGION:-eu-central-1})"
  cat << RCLONE_EOF | sudo tee /root/.config/rclone/rclone.conf > /dev/null
[s3remote]
type = s3
provider = AWS
access_key_id = ${S3_ACCESS_KEY_ID}
secret_access_key = ${S3_ACCESS_SECRET}
region = ${S3_REGION:-eu-central-1}
acl = private
RCLONE_EOF
fi

# Verify config was created
if [ ! -f /root/.config/rclone/rclone.conf ]; then
    echo "ERROR: rclone config file was not created" >&2
    exit 1
fi

echo "[rclone] ✓ Config created"
exit 0

