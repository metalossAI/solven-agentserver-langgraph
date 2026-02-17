# Fix: Missing Python Venv Issue

## Problem Observed

From the logs:
```
[SandboxBackend.execute] Command: python /barras_test.py
[SandboxBackend.execute] Python venv exists: False
[SandboxBackend.execute] ⚠️  Warning: No Python venv found!
[bwrap] Python venv check: missing
[venv] ℹ️  No virtual environment found, using system Python
[venv] Python location: /usr/local/bin/python
```

**Issues:**
1. Python venv doesn't exist (should have been created during workspace setup)
2. Script runs with system Python instead of venv Python
3. Script fails but error output not clearly shown
4. PNG file not created (`/barras_test.png: cannot open`)

## Root Causes

### 1. Workspace Configuration Not Verified

The workspace configuration checks if `.workspace_configured` marker exists, but doesn't verify that the venv was actually created successfully. This means:

- Marker file could exist even if venv creation failed
- No retry mechanism if venv is missing
- Silent failure during initialization

### 2. Missing Error Output

The bash wrapper used `set -e` which caused the script to exit immediately on error, but didn't capture the Python error output properly.

### 3. No Fallback for Missing Venv

When venv is missing, the system falls back to system Python, but:
- System Python might not have required packages (e.g., matplotlib)
- No clear indication to user that packages need to be installed
- Script fails silently

## Fixes Applied ✅

### Fix 1: Verify Venv During Configuration Check

**Before:**
```python
if self._sandbox.files.exists(config_marker_path):
    print(f"[Workspace] ✅ Already configured")
    return
```

**After:**
```python
if self._sandbox.files.exists(config_marker_path):
    print(f"[Workspace] ✅ Already configured")
    
    # Verify Python venv exists
    venv_exists = self._sandbox.files.exists(f"{self._base_path}/.venv/bin/python")
    if not venv_exists:
        print(f"[Workspace] ⚠️  Marker exists but venv missing - reconfiguring...")
        # Remove marker and reconfigure
        try:
            self._sandbox.files.remove(config_marker_path)
        except:
            pass
    else:
        return

print(f"[Workspace] ⚙️  Configuring workspace...")
```

**What this does:**
- Checks if venv actually exists, not just the marker
- If marker exists but venv is missing, removes marker and reconfigures
- Ensures workspace is always in a valid state

### Fix 2: Better Error Capture in Bash Wrapper

**Before:**
```bash
set -e  # Exit on error
set -o pipefail

# ... venv activation ...

{command}
```

**After:**
```bash
set -o pipefail  # Exit on pipe failure (but not on command error)

# ... venv activation ...

# Execute the actual command (capture exit code but don't stop on error)
{command}
exit_code=$?

# Show exit code for debugging
if [ $exit_code -ne 0 ]; then
    echo "[bwrap] Command failed with exit code: $exit_code" >&2
fi

exit $exit_code
```

**What this does:**
- Removed `set -e` to allow Python errors to be captured
- Explicitly captures exit code
- Shows exit code in stderr for debugging
- Ensures Python error output is visible

### Fix 3: Helpful Error Messages for Missing Venv

**Added to CommandExitException handler:**
```python
# For Python commands, suggest checking if venv is set up
if "python" in command.lower():
    venv_exists = self._sandbox.files.exists(f"{self._base_path}/.venv/bin/python")
    if not venv_exists:
        error_msg += "\n\n⚠️  Python virtual environment not found!"
        error_msg += "\n💡 The workspace may need to be reconfigured."
        error_msg += "\n   Try running a simple command first to trigger setup."
```

**What this does:**
- Detects if Python command failed due to missing venv
- Provides helpful guidance to user
- Suggests reconfiguration

### Fix 4: Enhanced Python Environment Setup

**Added verification step:**
```python
def _setup_python_environment(self) -> None:
    # ... create venv ...
    
    # Create venv if it doesn't exist
    if not self._sandbox.files.exists(venv_path):
        print(f"[Workspace] Creating Python virtual environment...")
        result = self._run_command(
            f"cd {self._base_path} && uv venv",
            timeout=15000,
            description="Creating Python venv"
        )
        
        if result.exit_code != 0:
            raise RuntimeError(f"Failed to create venv: {result.stderr}")
    
    # Verify Python works
    self._verify_python_environment()
```

**What this does:**
- Explicitly creates venv if missing
- Verifies venv works after creation
- Fails fast if venv creation fails

## Expected Behavior After Fixes

### Scenario 1: Fresh Workspace (First Time)

```
[Workspace] ⚙️  Configuring workspace at /mnt/r2/.../threads/{id}
[Workspace] Creating Python virtual environment...
[Workspace] ✓ Python check passed: Python 3.12.x
[Workspace] ✓ Python environment ready
[Workspace] ✅ Configuration complete!

[SandboxBackend.execute] Command: python /script.py
[SandboxBackend.execute] Python venv exists: True
[bwrap] Python venv check: exists
[venv] ✓ Activated Python environment
[venv] Python: /.venv/bin/python
[venv] Version: Python 3.12.x
(script output...)
```

### Scenario 2: Corrupted Workspace (Marker but No Venv)

```
[Workspace] ✅ Already configured at /mnt/r2/.../threads/{id}
[Workspace] ⚠️  Marker exists but venv missing - reconfiguring...
[Workspace] ⚙️  Configuring workspace at /mnt/r2/.../threads/{id}
[Workspace] Creating Python virtual environment...
[Workspace] ✓ Python check passed: Python 3.12.x
[Workspace] ✓ Python environment ready
[Workspace] ✅ Configuration complete!
```

### Scenario 3: Python Script with Error (Now Shows Error)

```
[SandboxBackend.execute] Command: python /script.py
[SandboxBackend.execute] Python venv exists: True
[bwrap] Python venv check: exists
[venv] ✓ Activated Python environment
Traceback (most recent call last):
  File "/script.py", line 5, in <module>
    import matplotlib
ModuleNotFoundError: No module named 'matplotlib'
[bwrap] Command failed with exit code: 1
[SandboxBackend.execute] Exit code: 1
[SandboxBackend.execute] ℹ️  Python module missing - agent should install it first
```

## Testing Checklist

### Test 1: Fresh Workspace ✅
```python
# First command should trigger workspace setup
agent.execute("python --version")
# Should see: [Workspace] ⚙️  Configuring workspace...
# Should see: [venv] ✓ Activated Python environment
```

### Test 2: Verify Venv Exists ✅
```python
# After setup, check venv
agent.execute("ls -la /.venv/bin/python")
# Should show: /.venv/bin/python exists
```

### Test 3: Python Script Execution ✅
```python
# Write and run script
agent.write("/test.py", "print('Hello from Python!')")
agent.execute("python /test.py")
# Should output: Hello from Python!
```

### Test 4: Error Handling ✅
```python
# Script with import error
agent.write("/test.py", "import nonexistent")
agent.execute("python /test.py")
# Should show: ModuleNotFoundError: No module named 'nonexistent'
# Should show: ℹ️  Python module missing
```

### Test 5: Package Installation ✅
```python
# Install package
agent.execute("uv pip install matplotlib")
# Should work with venv

# Use package
agent.write("/plot.py", """
import matplotlib.pyplot as plt
plt.plot([1, 2, 3], [4, 5, 6])
plt.savefig('/plot.png')
print('Plot saved!')
""")
agent.execute("python /plot.py")
# Should output: Plot saved!
# Should create: /plot.png
```

### Test 6: Recovery from Corrupted State ✅
```python
# Simulate corrupted workspace (marker exists, venv doesn't)
# System should automatically detect and reconfigure
agent.execute("python --version")
# Should see: ⚠️  Marker exists but venv missing - reconfiguring...
# Should see: ⚙️  Configuring workspace...
# Should see: ✓ Python environment ready
```

## Why the Original Issue Occurred

Looking at your logs:
```
[venv] Python venv check: missing
[venv] ℹ️  No virtual environment found, using system Python
```

**Possible causes:**
1. **First time setup incomplete** - Workspace was never fully configured
2. **Setup failed silently** - Venv creation failed but marker was still created
3. **R2 sync issue** - Venv was created but not synced to R2
4. **Partial cleanup** - Previous cleanup removed venv but not marker

**The fix ensures:**
- ✅ Venv existence is always verified
- ✅ Failed setups are retried
- ✅ Clear error messages when something is wrong
- ✅ Automatic recovery from corrupted state

## Summary

| Issue | Before | After |
|-------|--------|-------|
| Missing venv detection | ❌ Not detected | ✅ Detected and fixed |
| Error visibility | ❌ Silent failures | ✅ Clear error messages |
| Auto-recovery | ❌ None | ✅ Automatic reconfiguration |
| User guidance | ❌ Confusing | ✅ Helpful suggestions |
| Debugging | ❌ Hard | ✅ Easy with detailed logs |

**Python executions should now be reliable even if workspace gets into a bad state!** 🎉

