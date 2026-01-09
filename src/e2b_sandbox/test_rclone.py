#!/usr/bin/env python3
"""
Quick test to verify rclone mounts work in the new template
"""
import os
import sys
from dotenv import load_dotenv
from e2b import Sandbox

load_dotenv()

TEMPLATE_ID = os.getenv("E2B_TEMPLATE_ID")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL")
S3_ACCESS_KEY_ID = os.getenv("S3_ACCESS_KEY_ID")
S3_ACCESS_SECRET = os.getenv("S3_ACCESS_SECRET")
S3_BUCKET_NAME = os.getenv("S3_BUCKET_NAME")
S3_REGION = os.getenv("S3_REGION", "us-east-1")

THREAD_ID = "test-thread-123"
USER_ID = "test-user-456"

print("=" * 80)
print("E2B Sandbox Rclone Mount Test")
print("=" * 80)
print(f"Template: {TEMPLATE_ID}")
print(f"Bucket: {S3_BUCKET_NAME}")
print(f"Endpoint: {S3_ENDPOINT_URL}")
print("=" * 80)

# Create sandbox
print("\n🚀 Creating sandbox with credentials...")
envs = {
    "THREAD_ID": THREAD_ID,
    "USER_ID": USER_ID,
    "S3_BUCKET_NAME": S3_BUCKET_NAME,
    "S3_ACCESS_KEY_ID": S3_ACCESS_KEY_ID,
    "S3_ACCESS_SECRET": S3_ACCESS_SECRET,
    "S3_ENDPOINT_URL": S3_ENDPOINT_URL,
    "S3_REGION": S3_REGION,
}

sandbox = Sandbox.create(
    template=TEMPLATE_ID,
    envs=envs,
    timeout=60 * 1 * 1000,
)
print(f"✓ Sandbox created: {sandbox.sandbox_id}")

print("\n⏳ Waiting for mounts to initialize (10 seconds)...")
import time
time.sleep(10)

# Check startup logs first
print("\n" + "=" * 80)
print("Startup Logs")
print("=" * 80)
result = sandbox.commands.run("journalctl --no-pager | grep -E '\\[rclone\\]|rclone' | tail -30")
print(result.stdout if result.stdout else "No rclone logs found")

# Check if start command is even running
print("\n" + "=" * 80)
print("Check start command process")
print("=" * 80)
result = sandbox.commands.run("ps aux | grep 'tail -f /dev/null' | grep -v grep")
print(result.stdout if result.stdout else "No tail process found")

# Test 1: Check rclone processes
print("\n" + "=" * 80)
print("Test 1: Check rclone processes")
print("=" * 80)
try:
    result = sandbox.commands.run("ps aux | grep rclone | grep -v grep")
    print(f"Rclone processes:\n{result.stdout}")
except Exception:
    print("No rclone processes found (or grep returned no matches)")

# Test 2: Check mounts
print("\n" + "=" * 80)
print("Test 2: Check mounted directories")
print("=" * 80)
for path in [
    f"/mnt/r2/threads/{THREAD_ID}",
    "/mnt/r2/skills/system",
    f"/mnt/r2/skills/{USER_ID}",
]:
    result = sandbox.commands.run(f"mountpoint -q {path} && echo 'MOUNTED' || echo 'NOT MOUNTED'")
    status = result.stdout.strip()
    print(f"{path}: {status}")

# Test 3: List thread workspace contents
print("\n" + "=" * 80)
print("Test 3: List thread workspace contents")
print("=" * 80)
result = sandbox.commands.run(f"ls -la /mnt/r2/threads/{THREAD_ID}/ | head -10")
print(result.stdout)

# Test 4: Try to create a file
print("\n" + "=" * 80)
print("Test 4: Create test file")
print("=" * 80)
result = sandbox.commands.run(f"""
echo "Test file created at $(date)" > /mnt/r2/threads/{THREAD_ID}/test-from-rclone.txt
cat /mnt/r2/threads/{THREAD_ID}/test-from-rclone.txt
""")
print(result.stdout)

print("\n" + "=" * 80)
print("✓ All tests complete!")
print("=" * 80)
print(f"Sandbox ID: {sandbox.sandbox_id}")
print("Closing sandbox...")
sandbox.kill()
print("✓ Done")

