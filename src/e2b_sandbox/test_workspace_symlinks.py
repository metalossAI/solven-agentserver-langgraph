#!/usr/bin/env python3
"""
Test script to verify workspace symlinks (.solven and .ticket) are created correctly
after sandbox creation.
"""

import os
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def test_workspace_symlinks():
    """Test workspace symlink creation in E2B sandbox."""
    from e2b import AsyncSandbox
    
    # Get environment variables (filter out None values)
    thread_id = "test-thread-symlinks-123"
    user_id = "test-user-456"
    ticket_id = "test-ticket-789"
    
    env_vars = {
        "THREAD_ID": thread_id,
        "USER_ID": user_id,
        "TICKET_ID": ticket_id,
        "S3_BUCKET_NAME": os.getenv("S3_BUCKET_NAME", "solven-testing"),
        "S3_REGION": os.getenv("S3_REGION", "eu-central-1"),
    }
    
    # Add credentials only if they exist
    if os.getenv("S3_ENDPOINT_URL"):
        env_vars["S3_ENDPOINT_URL"] = os.getenv("S3_ENDPOINT_URL")
    if os.getenv("S3_ACCESS_KEY_ID"):
        env_vars["S3_ACCESS_KEY_ID"] = os.getenv("S3_ACCESS_KEY_ID")
    if os.getenv("S3_ACCESS_SECRET"):
        env_vars["S3_ACCESS_SECRET"] = os.getenv("S3_ACCESS_SECRET")
    
    # Filter out None values
    env_vars = {k: v for k, v in env_vars.items() if v is not None}
    
    print("Creating E2B sandbox...")
    print(f"Environment variables: {list(env_vars.keys())}")
    
    try:
        # Create sandbox
        sandbox = await AsyncSandbox.create(
            template=os.getenv("E2B_SANDBOX_TEMPLATE", "solven-sandbox-v1"),
            envs=env_vars,
            timeout=60 * 1000,
        )
        
        print(f"✓ Sandbox created: {sandbox.sandbox_id}")
        
        # Wait for start command to complete (mounts)
        print("\nWaiting 10 seconds for start_cmd to complete (rclone mounts)...")
        await asyncio.sleep(10)
        
        # Check if rclone processes are running
        print("\n--- Checking rclone processes ---")
        result = await sandbox.commands.run("ps aux | grep rclone | grep -v grep || echo 'No rclone processes'")
        print(result.stdout)
        if result.stderr:
            print(f"stderr: {result.stderr}")
        
        # Check mounts
        print("\n--- Checking mounts ---")
        mounts_to_check = [
            f"/mnt/r2/threads/{thread_id}",
            "/mnt/r2/skills/system",
            f"/mnt/r2/skills/{user_id}",
            f"/mnt/r2/tickets/{ticket_id}",
        ]
        
        for mount_path in mounts_to_check:
            result = await sandbox.commands.run(f"ls -la {mount_path} 2>&1 | head -5")
            print(f"\n{mount_path}:")
            print(result.stdout)
        
        # Check workspace structure (where symlinks should be)
        workspace_path = f"/mnt/r2/threads/{thread_id}"
        
        print(f"\n--- Checking workspace structure at {workspace_path} ---")
        result = await sandbox.commands.run(f"ls -la {workspace_path}")
        print(result.stdout)
        if result.stderr:
            print(f"stderr: {result.stderr}")
        
        # Check .solven symlink
        print("\n--- Checking .solven/skills/ symlinks ---")
        solven_path = f"{workspace_path}/.solven/skills"
        result = await sandbox.commands.run(f"ls -la {solven_path} 2>&1")
        print(result.stdout)
        if result.stderr:
            print(f"stderr: {result.stderr}")
        
        # Check .ticket symlink
        print("\n--- Checking .ticket symlink ---")
        ticket_symlink = f"{workspace_path}/.ticket"
        result = await sandbox.commands.run(f"ls -la {ticket_symlink} 2>&1")
        print(result.stdout)
        if result.stderr:
            print(f"stderr: {result.stderr}")
        
        # Try to access files through symlinks
        print("\n--- Testing symlink access ---")
        result = await sandbox.commands.run(f"ls {workspace_path}/.solven/skills/system 2>&1 | head -5")
        print("System skills via .solven/skills/system:")
        print(result.stdout)
        
        result = await sandbox.commands.run(f"ls {workspace_path}/.solven/skills/user 2>&1 | head -5")
        print("\nUser skills via .solven/skills/user:")
        print(result.stdout)
        
        # Check if workspace marker file exists
        print("\n--- Checking workspace configuration marker ---")
        marker_path = f"{workspace_path}/.workspace_configured"
        result = await sandbox.commands.run(f"ls -la {marker_path} 2>&1")
        print(result.stdout)
        if result.stderr:
            print(f"stderr: {result.stderr}")
        
        print(f"\n✓ Test complete!")
        print(f"\nSandbox ID: {sandbox.sandbox_id}")
        print("You can connect to it with: e2b sandbox connect <sandbox_id>")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(test_workspace_symlinks())

