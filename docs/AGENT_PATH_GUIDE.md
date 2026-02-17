# Agent Path Usage - IMPORTANT

## TL;DR

**Agent should use RELATIVE paths, not absolute paths!**

```python
✅ CORRECT:
write("script.py", code)
execute("python script.py")
read("output.txt")
ls_info(".")

❌ WRONG:
write("/script.py", code)        # Don't use absolute paths!
execute("python /script.py")     # File won't be found!
read("/output.txt")              # Wrong location!
```

## Why Relative Paths?

The workspace is at `/mnt/r2/{bucket}/threads/{thread_id}/`. When commands execute:
- Working directory = workspace
- Files created with relative paths = in workspace ✅
- Files created with absolute paths = in system root ❌

## Correct Usage

### Creating Files

```python
# ✅ CORRECT - Relative path
write("script.py", """
print('Hello World')
""")

# ❌ WRONG - Absolute path
write("/script.py", code)  # Creates in system root, not workspace!
```

### Executing Scripts

```python
# ✅ CORRECT - Relative path
execute("python script.py")
execute("python data/process.py")
execute("node app.js")

# ❌ WRONG - Absolute path
execute("python /script.py")  # File not found!
```

### Reading Files

```python
# ✅ CORRECT - Relative path
content = read("output.txt")
content = read("data/results.csv")

# ❌ WRONG - Absolute path  
content = read("/output.txt")  # File not found!
```

### Listing Files

```python
# ✅ CORRECT - Current directory or subdirectory
files = ls_info(".")
files = ls_info("data")

# ❌ WRONG - Absolute path
files = ls_info("/")  # Shows system root, not workspace!
```

## Special Directories

These DO use absolute-style paths because they're symlinks:

```python
# ✅ Skills (use .solven)
files = ls_info(".solven/skills/system")
execute("python .solven/skills/system/pdf_skill.py input.pdf")

# ✅ Ticket files (use .ticket)
files = ls_info(".ticket")
content = read(".ticket/document.pdf")
```

## Working Directory

All commands execute in the workspace:

```bash
pwd  # Shows: /mnt/r2/{bucket}/threads/{thread_id}
ls   # Shows workspace files
```

## Complete Example

```python
# 1. Create script (relative path)
write("hello.py", """
print('Hello from workspace!')
import os
print(f'Working dir: {os.getcwd()}')
with open('output.txt', 'w') as f:
    f.write('Success!')
""")

# 2. Execute script (relative path)
result = execute("python hello.py")
print(result.output)
# Output:
# Hello from workspace!
# Working dir: /mnt/r2/.../thread_id

# 3. Read output (relative path)
content = read("output.txt")
print(content)  # "Success!"

# 4. List files (current directory)
files = ls_info(".")
# Shows: ['hello.py', 'output.txt', '.venv', '.solven', ...]
```

## Agent System Prompt Addition

Add this to your agent's system prompt:

```markdown
## File System Rules

**CRITICAL: Always use RELATIVE paths, never absolute paths!**

Working directory: Your workspace (thread-specific directory)

✅ CORRECT Usage:
- Create file: `write("script.py", code)`
- Execute: `execute("python script.py")`
- Read: `read("output.txt")`
- List: `ls_info(".")` or `ls_info("subdir")`

❌ WRONG Usage (DO NOT DO THIS):
- `write("/script.py", code)` ← File goes to wrong location!
- `execute("python /script.py")` ← File not found!
- `read("/output.txt")` ← Wrong location!

Special directories (these use dot-prefix):
- Skills: `.solven/skills/system/`
- Ticket files: `.ticket/`

Examples:
```python
# Create and run a script
write("process.py", "print('Processing...')")
execute("python process.py")

# Work with subdirectories
execute("mkdir data")
write("data/input.csv", csv_data)
execute("python process.py data/input.csv")
result = read("data/output.csv")
```

Remember: Current directory IS your workspace. Use relative paths!
```

## Troubleshooting

### Error: "No such file or directory"

**Symptom:**
```
python3: can't open file '/script.py': [Errno 2] No such file or directory
```

**Cause:** Using absolute path `/script.py` instead of relative `script.py`

**Solution:**
```python
# Instead of:
write("/script.py", code)
execute("python /script.py")

# Use:
write("script.py", code)
execute("python script.py")
```

### Files Created But Not Found

**Symptom:** Agent creates file, but execute/read can't find it

**Cause:** Mixed absolute/relative paths

**Solution:** Use relative paths consistently:
```python
write("script.py", code)      # Creates in workspace
execute("python script.py")   # Finds in workspace ✅
```

### Empty Directory Listing

**Symptom:** `ls_info("/")` returns `[]`

**Cause:** Listing system root `/` instead of workspace

**Solution:**
```python
# Instead of:
files = ls_info("/")

# Use:
files = ls_info(".")  # Current directory (workspace)
```

## Summary

🎯 **Golden Rule: Use relative paths like you're in a normal terminal!**

- ✅ `script.py` - YES!
- ✅ `data/file.csv` - YES!
- ✅ `.` or `./file.txt` - YES!
- ❌ `/script.py` - NO!
- ❌ `/absolute/path` - NO!

**Think of it like working in a normal project directory - you don't use absolute paths, you use relative ones!**

