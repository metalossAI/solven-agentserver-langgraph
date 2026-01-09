#!/usr/bin/env python3
"""
Test script to verify that rclone mounts are set up correctly by sandbox_backend.py
"""

import os
import asyncio
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

async def test_backend_mounts():
    """Test that sandbox_backend correctly mounts S3 buckets."""
    
    # Import after loading env vars
    from src.models import AppContext, Thread, User, Ticket
    from src.sandbox_backend import SandboxBackend
    
    # Create test context
    thread_id = "test-thread-backend-123"
    user_id = "test-user-456"
    ticket_id = "test-ticket-789"
    
    thread = Thread(id=thread_id, name="Test Thread", userId=user_id)
    user = User(id=user_id, email="test@example.com", name="Test User")
    ticket = Ticket(id=ticket_id, threadId=thread_id, userId=user_id, title="Test Ticket")
    
    context = AppContext(
        thread=thread,
        user=user,
        ticket=ticket
    )
    
    print(f"Creating sandbox backend for thread: {thread_id}")
    backend = SandboxBackend(runtime_context=context)
    
    # Initialize backend (this should create sandbox and mount S3)
    print("\nInitializing backend (this will create sandbox and mount S3)...")
    await backend._ensure_initialized()
    
    print("\n=== Checking rclone processes ===")
    result = await backend._sandbox.commands.run("ps aux | grep rclone | grep -v grep || echo 'No rclone processes'")
    print(result.stdout)
    
    print("\n=== Checking mount points ===")
    mounts_to_check = [
        f"/mnt/r2/threads/{thread_id}",
        "/mnt/r2/skills/system",
        f"/mnt/r2/skills/{user_id}",
        f"/mnt/r2/tickets/{ticket_id}",
    ]
    
    for mount_path in mounts_to_check:
        result = await backend._sandbox.commands.run(f"ls -la {mount_path} 2>&1 | head -5")
        print(f"\n{mount_path}:")
        print(result.stdout)
    
    print("\n=== Checking workspace symlinks ===")
    workspace_path = f"/mnt/r2/threads/{thread_id}"
    
    # Check .solven/skills symlinks
    result = await backend._sandbox.commands.run(f"ls -la {workspace_path}/.solven/skills/ 2>&1")
    print(f"\n.solven/skills/ symlinks:")
    print(result.stdout)
    
    # Check .ticket symlink
    result = await backend._sandbox.commands.run(f"ls -la {workspace_path}/.ticket 2>&1")
    print(f"\n.ticket symlink:")
    print(result.stdout)
    
    # Check if workspace configured marker exists
    result = await backend._sandbox.commands.run(f"ls -la {workspace_path}/.workspace_configured 2>&1")
    print(f"\n.workspace_configured marker:")
    print(result.stdout)
    
    # Check rclone logs
    print("\n=== Checking rclone logs ===")
    log_files = [
        "/tmp/rclone-thread.log",
        "/tmp/rclone-skills-system.log",
        "/tmp/rclone-skills-user.log",
        "/tmp/rclone-ticket.log"
    ]
    
    for log_file in log_files:
        result = await backend._sandbox.commands.run(f"tail -20 {log_file} 2>&1 || echo 'Log not found'")
        print(f"\n{log_file}:")
        print(result.stdout[:500])  # Limit output
    
    # Test file operations through backend
    print("\n=== Testing file operations ===")
    
    # List files in workspace
    files = await backend.als_info("/")
    print(f"\nFiles in workspace root: {len(files)} files")
    for f in files[:5]:
        print(f"  - {f.path}")
    
    # Try to write a test file
    print("\nWriting test file...")
    write_result = await backend.awrite("/test_mount.txt", "Hello from mounted S3!")
    if write_result.error:
        print(f"Write error: {write_result.error}")
    else:
        print(f"✓ File written: {write_result.path}")
        
        # Read it back
        content = await backend.aread("/test_mount.txt")
        print(f"File content:\n{content}")
    
    print(f"\n✓ Test complete!")
    print(f"\nSandbox ID: {backend._sandbox.sandbox_id}")
    print("You can connect to it with: e2b sandbox connect <sandbox_id>")

if __name__ == "__main__":
    asyncio.run(test_backend_mounts())
