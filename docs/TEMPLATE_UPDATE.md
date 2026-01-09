# E2B Template Update - Bubblewrap Installation

## What Changed

Added **bubblewrap** to the E2B sandbox template for reliable workspace isolation.

## Changes Made

**File:** `src/e2b_sandbox/template.py`

```python
# Added bubblewrap
.apt_install([
    "bubblewrap",
])
```

## Why Bubblewrap?

- ✅ **Simpler** than proot
- ✅ **More reliable** - fewer bugs
- ✅ **Better maintained** - used by Flatpak
- ✅ **Makes workspace appear as /** - agent can use any path
- ✅ **Faster** - no ptrace overhead

## Rebuild Template

### Step 1: Set E2B API Key

```bash
export E2B_API_KEY=your_key_here
```

### Step 2: Build Template

```bash
cd /home/ramon/Github/metaloss/solven-agentserver-langgraph/src/e2b_sandbox

# Build new template
python template.py
```

This will:
1. Install bubblewrap in the sandbox
2. Create a new template version
3. Output the template ID

### Step 3: Update Environment Variable

Update your `.env` file with the new template ID:

```bash
E2B_TEMPLATE_ID=new_template_id_here
```

### Step 4: Restart Services

```bash
# Restart the agent server to use new template
# Existing sandboxes will continue using old template
# New sandboxes will use new template with bubblewrap
```

## Verification

After rebuilding, verify bubblewrap is available:

```python
from e2b_code_interpreter import Sandbox

sandbox = Sandbox(template="your_new_template_id")
result = sandbox.commands.run("which bwrap")
print(f"Bubblewrap available: {result.exit_code == 0}")
print(f"Path: {result.stdout}")
# Should output: /usr/bin/bwrap
```

## Benefits After Update

### Before (Without Bubblewrap)
```python
# Agent command
execute("node /grafico_node.js")

# Error: Cannot find module '/grafico_node.js'
# (looks in system root, not workspace)
```

### After (With Bubblewrap)
```python
# Agent command
execute("node /grafico_node.js")

# Success! ✅
# (workspace appears as /, so /grafico_node.js = workspace/grafico_node.js)
```

## Example Usage

Once template is rebuilt:

```python
# All these work naturally:
execute("ls /")                  # Shows workspace contents
execute("python /script.py")     # Runs from workspace
execute("node /app.js")          # Runs from workspace
execute("cat /file.txt")         # Reads from workspace
execute("echo 'data' > /out.txt") # Writes to workspace
```

## Testing Checklist

After template rebuild:

- [ ] Bubblewrap is available (`which bwrap`)
- [ ] Commands with absolute paths work (`node /script.js`)
- [ ] `ls /` shows workspace contents
- [ ] Python venv activates
- [ ] Node modules accessible
- [ ] Files create in workspace
- [ ] Symlinks work (.solven, .ticket)

## Rollback

If issues arise, revert to old template:

```bash
# In .env
E2B_TEMPLATE_ID=old_template_id
```

Restart services to use old template.

## Timeline

1. **Build new template** - ~10-15 minutes
2. **Deploy to staging** - Test with staging template
3. **Verify functionality** - Run test suite
4. **Deploy to production** - Update production template ID

## Summary

✅ **Added bubblewrap to E2B template**
✅ **Enables workspace-as-root isolation**
✅ **Makes all paths work naturally**
✅ **Fixes issues with absolute paths**

**Next Step:** Rebuild template with `python template.py`

