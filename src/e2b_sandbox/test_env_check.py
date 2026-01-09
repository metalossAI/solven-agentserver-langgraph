#!/usr/bin/env python3
"""Quick test to check what env vars are in the sandbox"""
import os
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

print(f"Creating sandbox with:")
envs = {
    "THREAD_ID": THREAD_ID,
    "USER_ID": USER_ID,
    "S3_BUCKET_NAME": S3_BUCKET_NAME,
    "S3_ACCESS_KEY_ID": S3_ACCESS_KEY_ID,
    "S3_ACCESS_SECRET": S3_ACCESS_SECRET,
    "S3_ENDPOINT_URL": S3_ENDPOINT_URL,
    "S3_REGION": S3_REGION,
}
for k, v in envs.items():
    print(f"  {k}: {v[:20] if v and len(v) > 20 else v}...")

sandbox = Sandbox.create(template=TEMPLATE_ID, envs=envs)
print(f"✓ Sandbox: {sandbox.sandbox_id}\n")

import time
time.sleep(5)

print("Checking environment variables in sandbox:")
result = sandbox.commands.run("env | grep -E 'S3_|THREAD_|USER_' | sort")
print(result.stdout)

print("\nChecking start command execution:")
result = sandbox.commands.run("journalctl --no-pager | grep 'rclone' | tail -20")
print(result.stdout if result.stdout else "No rclone logs")

print("\nChecking for processes:")
result = sandbox.commands.run("ps aux | head -20")
print(result.stdout)

sandbox.kill()

