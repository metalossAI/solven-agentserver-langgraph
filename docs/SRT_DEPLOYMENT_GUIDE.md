# SRT (Anthropic Sandbox Runtime) Deployment Guide

## What Changed

We've integrated [Anthropic's Sandbox Runtime (srt)](https://github.com/anthropic-experimental/sandbox-runtime) to provide **complete workspace isolation** for each thread using bubblewrap on Linux.

### Key Changes

1. **E2B Template** (`src/e2b_sandbox/template.py`):
   - Added `npm install -g @anthropic-ai/sandbox-runtime`

2. **Sandbox Backend** (`src/sandbox_backend.py`):
   - Added `_create_srt_config()` method to generate `.srt-settings.json`
   - Updated `_execute_simple()` to wrap commands with `srt`
   - Added step in `_ensure_workspace_configured()` to create srt config

3. **Security Configuration**:
   - Filesystem isolation (allow writes only to thread workspace)
   - Network isolation (allowlist for package managers and APIs)
   - Unix socket blocking (prevent unauthorized IPC)

## Deployment Steps

### Step 1: Rebuild E2B Template

**⚠️ REQUIRED**: The E2B template must be rebuilt to install `srt`.

```bash
cd /home/ramon/Github/metaloss/solven-agentserver-langgraph
uv run python src/e2b_sandbox/template.py
```

**Expected output**:
```
Building template...
Installing dependencies...
✓ npm install -g @anthropic-ai/sandbox-runtime
✓ Template built successfully
Template ID: solven-agentserver-xxx
```

**Time**: ~10-15 minutes

### Step 2: Update Environment Variables

No changes needed - uses existing E2B credentials.

### Step 3: Deploy Backend

```bash
# No code changes needed on your end
# The backend automatically uses srt after template rebuild
git pull  # Get latest changes
# Restart your backend service
```

### Step 4: Verify Installation

Test that `srt` is installed in the E2B environment:

```python
from e2b import Sandbox

sandbox = Sandbox()

# Check if srt is available
result = sandbox.commands.run("which srt", timeout=5000)
print(f"✓ srt installed at: {result.stdout.strip()}")

# Check srt version
result = sandbox.commands.run("srt --version", timeout=5000)
print(f"✓ srt version: {result.stdout.strip()}")

sandbox.close()
```

**Expected output**:
```
✓ srt installed at: /usr/local/bin/srt
✓ srt version: 0.x.x
```

### Step 5: Test Workspace Isolation

Create a new thread and verify srt configuration:

1. **Check Configuration File Created**:
```python
# In backend logs, look for:
[Workspace] Creating srt sandbox configuration...
[Workspace] ✓ SRT config created at /mnt/r2/.../threads/{thread_id}/.srt-settings.json
```

2. **Verify Command Execution**:
```python
# In backend logs, look for:
[SandboxBackend.execute] 🔒 SRT isolated execution
[SandboxBackend.execute] Workspace: /mnt/r2/.../threads/{thread_id}/
```

3. **Test Filesystem Restrictions**:
```python
# These should FAIL (blocked by srt):
execute("cat ~/.ssh/id_rsa")              # Reading SSH keys
execute("echo 'test' > /etc/test.txt")    # Writing to system dir
execute("echo 'bad' >> ~/.bashrc")        # Tampering with shell config

# This should SUCCEED:
execute("echo 'Hello' > /test.txt")       # Writing to workspace
```

4. **Test Network Restrictions**:
```python
# Should SUCCEED (allowed domains):
execute("curl -I https://pypi.org")
execute("curl -I https://github.com")

# Should FAIL (unauthorized domain):
execute("curl -I https://unauthorized-site.com")
```

## What You'll See

### Before (without srt):
```
[SandboxBackend.execute] 📂 Simple mode: /mnt/r2/.../threads/{thread_id}/
[SandboxBackend.execute] Command: ls -la
```

### After (with srt):
```
[SandboxBackend.execute] 🔒 SRT isolated execution
[SandboxBackend.execute] Workspace: /mnt/r2/.../threads/{thread_id}/
[SandboxBackend.execute] Command: ls -la
```

## Security Benefits

✅ **Filesystem Isolation**: Agent can only write to its own workspace  
✅ **Network Control**: Agent can only access approved domains  
✅ **No Privilege Escalation**: Cannot modify shell configs or system files  
✅ **Unix Socket Blocking**: Cannot create unauthorized IPC channels  
✅ **Per-Thread Isolation**: Each thread has its own sandbox config  
✅ **Violation Monitoring**: Unauthorized access attempts are logged  

## Rollback Plan

If issues arise, you can temporarily disable srt:

```python
# In sandbox_backend.py, comment out srt wrapping:
def _execute_simple(self, command: str) -> ExecuteResponse:
    # Temporarily disable srt for debugging
    # sandboxed_command = f"srt --settings {srt_settings_path} bash -c {command}"
    # Use direct execution instead:
    result = self._sandbox.commands.run(f"{env_setup} && ({command})", timeout=60000)
```

## Troubleshooting

### `srt: command not found`

**Solution**: Rebuild E2B template (Step 1)

### Commands are slower

**Normal**: srt has initial overhead (~500ms) for proxy setup. Subsequent commands are faster.

### Network access blocked unexpectedly

**Solution**: Add domain to allowlist in `_create_srt_config()`:
```python
"allowedDomains": [
    "pypi.org",
    "your-domain.com",  # Add here
    ...
]
```

### Cannot write files in workspace

**Check**: Verify `self._base_path` is in `allowWrite` list (should be by default)

## Monitoring

### View Sandbox Violations (Linux)

```bash
# In E2B sandbox, trace violations:
strace -f srt <command> 2>&1 | grep EPERM
```

### Backend Logs

All srt operations are logged with `[SandboxBackend.execute]` prefix.

## Next Steps

After successful deployment:

1. ✅ Monitor backend logs for srt execution messages
2. ✅ Test filesystem and network restrictions
3. ✅ Review violation logs (if any)
4. ✅ Adjust allowlists if needed based on use cases

## Documentation

- [Full Integration Guide](./docs/srt-sandbox-integration.md)
- [Anthropic srt GitHub](https://github.com/anthropic-experimental/sandbox-runtime)
- [Claude Code Sandboxing](https://docs.claude.com/en/docs/claude-code/sandboxing)

## Summary

🔒 **Security**: Complete workspace isolation with filesystem and network controls  
⚡ **Performance**: Native OS primitives, no container overhead  
🎯 **Simplicity**: Automatic per-thread configuration  
📊 **Monitoring**: Built-in violation detection and logging  

**Action Required**: Rebuild E2B template to install srt  
**Downtime**: None (new threads use srt, existing threads continue normally)  
**Risk**: Low (can be disabled if issues arise)  

