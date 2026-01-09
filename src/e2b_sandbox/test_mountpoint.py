#!/usr/bin/env python3
"""
Test script to connect to E2B sandbox and verify Mountpoint for S3 setup
"""
import os
import sys
from dotenv import load_dotenv
from e2b import Sandbox

# Load environment variables
load_dotenv()

# Get E2B template ID
TEMPLATE_ID = os.getenv("E2B_TEMPLATE_ID")
if not TEMPLATE_ID:
    print("❌ E2B_TEMPLATE_ID not found in environment")
    sys.exit(1)

# Get S3 credentials
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID")
S3_ACCESS_SECRET = os.getenv("S3_ACCESS_SECRET")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_REGION = os.getenv("S3_REGION", "us-east-1")

# Test values
THREAD_ID = "test-thread-123"
USER_ID = "test-user-456"

print("=" * 80)
print("E2B Sandbox Mountpoint Test")
print("=" * 80)
print(f"Template ID: {TEMPLATE_ID}")
print(f"S3 Bucket: {S3_BUCKET_NAME}")
print(f"S3 Endpoint: {S3_ENDPOINT_URL or 'AWS S3 (default)'}")
print(f"S3 Region: {S3_REGION}")
print(f"Thread ID: {THREAD_ID}")
print(f"User ID: {USER_ID}")
print("=" * 80)

# Create sandbox with environment variables (only non-empty values)
print("\n🚀 Creating sandbox...")

# Build env vars dict, only including non-empty values (E2B doesn't accept null)
envs = {
    "THREAD_ID": THREAD_ID,
    "USER_ID": USER_ID,
}

if S3_BUCKET_NAME:
    envs["S3_BUCKET_NAME"] = S3_BUCKET_NAME
if S3_ACCESS_KEY_ID:
    envs["S3_ACCESS_KEY_ID"] = S3_ACCESS_KEY_ID
if S3_ACCESS_SECRET:
    envs["S3_ACCESS_SECRET"] = S3_ACCESS_SECRET
if S3_ENDPOINT_URL:
    envs["S3_ENDPOINT_URL"] = S3_ENDPOINT_URL
if S3_REGION:
    envs["S3_REGION"] = S3_REGION

print(f"Environment variables being passed: {list(envs.keys())}")

try:
    sandbox = Sandbox.create(
        template=TEMPLATE_ID,
        envs=envs,
        timeout=60 * 1 * 1000,  # 1 minutes timeout
    )
    print(f"✓ Sandbox created: {sandbox.sandbox_id}")
except Exception as e:
    print(f"❌ Failed to create sandbox: {e}")
    sys.exit(1)

# Wait for startup
print("\n⏳ Waiting for sandbox to start...")
import time
time.sleep(10)

# Test 1: Check if mountpoint is installed
print("\n" + "=" * 80)
print("Test 1: Check mountpoint installation")
print("=" * 80)
result = sandbox.commands.run("which mount-s3")
print(f"Exit code: {result.exit_code}")
print(f"Output: {result.stdout or result.stderr}")

# Test 2: Check AWS credentials are set
print("\n" + "=" * 80)
print("Test 2: Check AWS environment variables")
print("=" * 80)
try:
    result = sandbox.commands.run("env | grep AWS")
    print(f"Output:\n{result.stdout}")
except Exception as e:
    print(f"No AWS environment variables found (this is the problem!)")

# Test 3: Check S3 environment variables
print("\n" + "=" * 80)
print("Test 3: Check S3 environment variables")
print("=" * 80)
try:
    result = sandbox.commands.run("env | grep S3")
    print(f"Output:\n{result.stdout}")
except Exception as e:
    print(f"Error: {e}")

# Test 4: Check mount points
print("\n" + "=" * 80)
print("Test 4: Check current mounts")
print("=" * 80)
try:
    result = sandbox.commands.run("mount | grep mount-s3")
    print(f"Exit code: {result.exit_code}")
    print(f"Output: {result.stdout or result.stderr or 'No mountpoint mounts found'}")
except Exception as e:
    print("No mountpoint mounts found")

# Test 5: Check directory structure
print("\n" + "=" * 80)
print("Test 5: Check /mnt/r2 directory structure")
print("=" * 80)
result = sandbox.commands.run("ls -laR /mnt/r2/")
print(f"Output:\n{result.stdout}")

# Test 6: Check mount status
print("\n" + "=" * 80)
print("Test 6: Check if directories are mountpoints")
print("=" * 80)
for path in [
    f"/mnt/r2/threads/{THREAD_ID}",
    "/mnt/r2/skills/system",
    f"/mnt/r2/skills/{USER_ID}",
]:
    result = sandbox.commands.run(f"mountpoint -q {path} && echo 'YES' || echo 'NO'")
    print(f"{path}: {result.stdout.strip()}")

# Test 7: Check mount-s3 processes
print("\n" + "=" * 80)
print("Test 7: Check mount-s3 processes")
print("=" * 80)
try:
    result = sandbox.commands.run("ps aux | grep mount-s3 | grep -v grep")
    print(f"Exit code: {result.exit_code}")
    print(f"Output: {result.stdout or 'No mount-s3 processes found'}")
except Exception:
    print("No mount-s3 processes found")

# Test 7.5: Test AWS CLI connectivity
print("\n" + "=" * 80)
print("Test 7.5: Test S3 API connectivity with AWS CLI")
print("=" * 80)
try:
    aws_test_cmd = f"""
export AWS_ACCESS_KEY_ID="{S3_ACCESS_KEY_ID}"
export AWS_SECRET_ACCESS_KEY="{S3_ACCESS_SECRET}"
export AWS_REGION="{S3_REGION}"
{f'export AWS_ENDPOINT_URL="{S3_ENDPOINT_URL}"' if S3_ENDPOINT_URL else ''}

# Install AWS CLI if not present
which aws || (curl "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o "/tmp/awscliv2.zip" && unzip -q /tmp/awscliv2.zip -d /tmp && sudo /tmp/aws/install)

# Test listing bucket
echo "Testing: aws s3 ls s3://{S3_BUCKET_NAME}/threads/ --recursive | head -5"
aws s3 ls s3://{S3_BUCKET_NAME}/threads/ --recursive 2>&1 | head -10
"""
    result = sandbox.commands.run(aws_test_cmd)
    print(f"Output:\n{result.stdout}")
    if result.stderr:
        print(f"Errors:\n{result.stderr}")
except Exception as e:
    print(f"AWS CLI test failed: {e}")

# Test 8: Manual mount test with diagnostics
print("\n" + "=" * 80)
print("Test 8: Try manual mount with diagnostics")
print("=" * 80)
test_mount_cmd = f"""
export AWS_ACCESS_KEY_ID="{S3_ACCESS_KEY_ID}"
export AWS_SECRET_ACCESS_KEY="{S3_ACCESS_SECRET}"
export AWS_REGION="{S3_REGION}"
{f'export AWS_ENDPOINT_URL="{S3_ENDPOINT_URL}"' if S3_ENDPOINT_URL else ''}

echo "=== Connectivity Test ==="
echo "Testing connection to S3 endpoint..."
{f'curl -I "{S3_ENDPOINT_URL}" -m 5 2>&1 | head -5' if S3_ENDPOINT_URL else 'echo "Using AWS S3 (no custom endpoint)"'}

echo ""
echo "=== Mount Test ==="
echo "Bucket: {S3_BUCKET_NAME}"
echo "Prefix: threads/{THREAD_ID}/"
echo "Endpoint: {S3_ENDPOINT_URL or 'AWS S3 default'}"
echo "Mount point: /mnt/test"

sudo mkdir -p /mnt/test

echo "Running mount-s3 command..."
sudo -E mount-s3 \\
  {f'--endpoint-url "{S3_ENDPOINT_URL}"' if S3_ENDPOINT_URL else ''} \\
  --prefix "threads/{THREAD_ID}/" \\
  --allow-other \\
  --file-mode 0666 \\
  --dir-mode 0777 \\
  --debug \\
  "{S3_BUCKET_NAME}" \\
  /mnt/test 2>&1 | head -50 &

MOUNT_PID=$!
echo "Mount process PID: $MOUNT_PID"

sleep 5
ps aux | grep -E "mount-s3|$MOUNT_PID" | grep -v grep || echo "No mount-s3 process found"

if mountpoint -q /mnt/test; then
  echo "✓ Mount successful!"
  ls -la /mnt/test/ | head -10
else
  echo "❌ Mount failed"
  echo "Checking for errors..."
  journalctl -n 20 2>/dev/null || dmesg | tail -10
fi
"""
result = sandbox.commands.run(test_mount_cmd)
print(f"Output:\n{result.stdout}")
if result.stderr:
    print(f"Errors:\n{result.stderr}")

# Test 9: Check system logs
print("\n" + "=" * 80)
print("Test 9: Check for any mountpoint logs")
print("=" * 80)
try:
    result = sandbox.commands.run("dmesg | grep -i mount | tail -20")
    print(f"Output:\n{result.stdout}")
except Exception as e:
    print(f"Could not read dmesg: {e}")

# Keep sandbox open for manual inspection
print("\n" + "=" * 80)
print("Sandbox is ready for manual inspection")
print("=" * 80)
print(f"Sandbox ID: {sandbox.sandbox_id}")
print("Press Enter to close the sandbox (or Ctrl+C to keep it open)...")

try:
    input()
except KeyboardInterrupt:
    print("\n\nKeeping sandbox open. Close it manually when done.")
    print(f"Sandbox URL: https://e2b.dev/dashboard?sandbox={sandbox.sandbox_id}")
    sys.exit(0)

# Cleanup
print("\n🧹 Closing sandbox...")
sandbox.kill()
print("✓ Done")

