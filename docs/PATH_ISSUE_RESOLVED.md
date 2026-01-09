# Path Issue - Root Cause and Solution

## The Problem

**Agent's behavior:**
```python
# Agent writes file:
write("/mi_script_v2.py", code)  # ✅ Works - creates in workspace

# Agent executes:
execute("python /mi_script_v2.py")  # ❌ FAILS - File not found!
```

## Root Cause

### Two Different Path Systems

**1. File Operations (write, read, ls_info):**
```python
def _key(self, path: str) -> str:
    path = path.lstrip("/")  # Strips leading slash!
    return f"{base_path}/{path}"

# Result:
"/mi_script.py" → "{base_path}/mi_script.py"  ✅
```

**2. Execute Operations:**
```bash
execute("python /mi_script.py")
# Executes as-is in bash:
# python /mi_script.py  # Looks in system root!
```

### The Mismatch

```
Agent writes to: "/mi_script.py"
↓ _key() converts to
File created at: /mnt/r2/.../thread_id/mi_script.py ✅

Agent executes: "python /mi_script.py"
↓ Bash interprets literally
Looks for file at: /mi_script.py (system root) ❌
```

## Why Bubblewrap Didn't Help

Bubblewrap can't make workspace appear as `/` because:
1. Would overwrite `/bin`, `/usr`, `/lib` (system directories)
2. Complex workarounds (overlayfs, symlinks) add fragility
3. Doesn't solve core issue: agent needs consistent path usage

## The Solution

**Use relative paths consistently!**

### Correct Pattern

```python
# ✅ Create file (relative path)
write("script.py", code)
# Creates at: {workspace}/script.py

# ✅ Execute (relative path)
execute("python script.py")
# Looks for: {workspace}/script.py (working dir is workspace)
# FOUND! ✅
```

### Why This Works

```bash
# When execute() runs:
cd {workspace}              # Change to workspace
source .venv/bin/activate   # Activate venv
python script.py            # Relative path - finds file!
```

## Agent Instructions

### Update Agent System Prompt

```markdown
## Critical: Path Usage Rules

**Always use RELATIVE paths, never absolute paths starting with /**

✅ CORRECT:
- `write("script.py", code)` then `execute("python script.py")`
- `write("data/file.csv", data)` then `read("data/file.csv")`
- `ls_info(".")` to list current directory

❌ WRONG (DO NOT USE):
- `write("/script.py", code)` ← Creates in workspace BUT...
- `execute("python /script.py")` ← Looks in system root, NOT FOUND!

Think of it like working in a normal project directory:
- You're already IN your project folder (workspace)
- Use paths relative to where you are
- Don't use absolute paths unless accessing system files

Examples:
```python
# Create and run Python script
write("process_data.py", "print('Processing...')")
execute("python process_data.py")

# Work with subdirectories
execute("mkdir analysis")
write("analysis/results.py", code)
execute("python analysis/results.py")

# Read output
output = read("output.txt")
```

Special directories (use dot-prefix):
- Skills: `.solven/skills/system/`
- Ticket: `.ticket/`
```

## Implementation Status

### ✅ What Works Now

1. **File operations** - All handle paths correctly:
   - `write("file.txt", data)` ✅
   - `read("file.txt")` ✅
   - `ls_info(".")` ✅

2. **Execute operations** - Work with relative paths:
   - `execute("python script.py")` ✅
   - `execute("ls -la")` ✅
   - `execute("cat file.txt")` ✅

3. **Fallback mode** - Bubblewrap disabled, using simple reliable approach

### 📝 What Needs to Change

**Agent behavior** - Must use relative paths:
```python
# Current (broken):
write("/script.py", code)
execute("python /script.py")  # ❌

# Should be:
write("script.py", code)
execute("python script.py")  # ✅
```

## Testing

### Test Case 1: Create and Execute Script

```python
# ✅ CORRECT way
write("test.py", "print('Hello World')")
result = execute("python test.py")
assert "Hello World" in result.output

# ❌ WRONG way (will fail)
write("/test.py", "print('Hello World')")
result = execute("python /test.py")
# Error: can't open file '/test.py': No such file or directory
```

### Test Case 2: Multi-Step Workflow

```python
# Create directory and files
execute("mkdir data")
write("data/input.txt", "test data")

# Process
write("process.py", """
with open('data/input.txt') as f:
    data = f.read()
    
with open('data/output.txt', 'w') as f:
    f.write(data.upper())
""")
execute("python process.py")

# Read result
result = read("data/output.txt")
assert "TEST DATA" in result
```

## Summary

🎯 **Root cause:** Mismatch between file operation paths (stripped `/`) and execute paths (literal)

✅ **Solution:** Agent uses relative paths consistently

📋 **Action required:** Update agent system prompt with path usage rules

🚀 **Result:** Simple, reliable, works like normal development workflow!

```python
# The Way Forward:
write("script.py", code)     # ✅ Simple
execute("python script.py")  # ✅ Works
read("output.txt")           # ✅ Clean

# Perfect! 🎉
```

