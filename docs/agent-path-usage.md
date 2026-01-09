# Agent Path Usage Guide

## Current Setup: Simple Directory-Based Execution

The workspace uses **simple directory-based execution** where each thread has its own workspace directory.

## Workspace Structure

```
/mnt/r2/{bucket}/threads/{thread_id}/  ← Working directory
├── .venv/               → Python environment
├── .solven/skills/      → Skills (symlinked)
├── .ticket/             → Ticket files (symlinked)  
├── node_modules/        → Node packages
├── pyproject.toml       → Python config
├── package.json         → Node config
└── (user files)         → Agent's files
```

## ✅ Correct Path Usage

### Use Relative Paths (Recommended)

```python
# Python scripts
execute("python script.py")              # ✅
execute("python data/process.py")        # ✅

# Node scripts
execute("node app.js")                   # ✅
execute("bun run script.ts")             # ✅

# File operations
execute("cat file.txt")                  # ✅
execute("echo 'data' > output.txt")      # ✅
execute("ls -la")                        # ✅ Shows workspace
execute("mkdir data")                    # ✅
execute("cp source.txt dest.txt")        # ✅
```

### Use Current Directory (.)

```python
# List workspace
execute("ls -la .")                      # ✅

# Find files
execute("find . -name '*.py'")           # ✅

# Work in subdirectories
execute("cd subdir && python script.py") # ✅
```

## ❌ Avoid Absolute Paths

Absolute paths reference the system root, not the workspace:

```python
# These look in system root, NOT workspace
execute("python /script.py")             # ❌ Looks in system /
execute("cat /file.txt")                 # ❌ Looks in system /
execute("ls /")                          # ❌ Shows system root

# Instead use relative paths:
execute("python script.py")              # ✅ Workspace
execute("cat file.txt")                  # ✅ Workspace
execute("ls .")                          # ✅ Workspace
```

## Special Directories

### Skills (.solven/)
```python
# Access system skills
execute("ls .solven/skills/system")      # ✅
execute("cat .solven/skills/system/pdf_skill.py")  # ✅

# Access user skills
execute("ls .solven/skills/user")        # ✅
```

### Ticket Files (.ticket/)
```python
# Access ticket files
execute("ls .ticket")                    # ✅
execute("cat .ticket/document.pdf")      # ✅
```

### Python Environment (.venv/)
```python
# Venv is auto-activated, just use python
execute("python --version")              # ✅
execute("pip list")                      # ✅ (or uv pip list)
```

## Full Path When Necessary

If you absolutely need to reference workspace files with full paths:

```python
import os
workspace = os.environ.get('HOME')  # Points to workspace
script_path = f"{workspace}/script.py"
execute(f"python {script_path}")
```

But **relative paths are simpler and recommended**.

## Environment Variables

The following are automatically set:

```bash
HOME=/mnt/r2/{bucket}/threads/{thread_id}  # Workspace root
PWD=/mnt/r2/{bucket}/threads/{thread_id}   # Current directory
PATH=.venv/bin:node_modules/.bin:$PATH     # Includes venv and node_modules
PYTHONUNBUFFERED=1                         # Immediate output
MPLBACKEND=Agg                             # Matplotlib headless
```

## Examples

### Creating and Running a Script

```python
# Create script (relative path)
execute("cat > hello.py << 'EOF'\nprint('Hello World')\nEOF")

# Run it (relative path)
result = execute("python hello.py")
print(result.output)  # "Hello World"
```

### Working with Data Files

```python
# Create data directory
execute("mkdir -p data")

# Create data file
execute("echo 'name,age\nJohn,30' > data/input.csv")

# Process with Python
execute("""
python -c "
import pandas as pd
df = pd.read_csv('data/input.csv')
df['age'] = df['age'] + 1
df.to_csv('data/output.csv', index=False)
print('Done!')
"
""")

# Read result
result = execute("cat data/output.csv")
print(result.output)
```

### Using Skills

```python
# List available skills
execute("ls .solven/skills/system")

# Use a skill (if it's executable or a library)
execute("python .solven/skills/system/pdf_skill.py input.pdf")
```

## Troubleshooting

### Error: "No such file or directory"

**Problem:** Using absolute path that doesn't exist in workspace

```python
execute("python /script.py")  # ❌ /script.py doesn't exist
```

**Solution:** Use relative path

```python
execute("python script.py")   # ✅ Looks in workspace
```

### Error: "Module not found"

**Problem:** Python package not installed

**Solution:** Install with uv

```python
execute("uv pip install pandas")
execute("python script.py")  # Now works
```

### Files Not Found After Creation

**Problem:** Created file in system root instead of workspace

```python
execute("echo 'test' > /file.txt")  # Created in system /
execute("cat file.txt")             # Not found (looking in workspace)
```

**Solution:** Use relative paths consistently

```python
execute("echo 'test' > file.txt")   # Created in workspace
execute("cat file.txt")             # Found!
```

## Best Practices

1. **Always use relative paths** - `script.py` not `/script.py`
2. **Use `.` for current directory** - `ls .` shows workspace
3. **Install packages on-demand** - `uv pip install <package>`
4. **Check working directory** - `pwd` shows workspace path
5. **List files to verify** - `ls -la` after creating files

## Summary

✅ **Do:**
- Use relative paths: `python script.py`
- Use current directory: `ls .`
- Use subdirectories: `cd data && python process.py`

❌ **Don't:**
- Use absolute paths for workspace files: `/script.py`
- Assume system paths: `/usr/local/bin/script.py`
- Mix absolute and relative inconsistently

**Simple rule: If it's your file, use a relative path!** 🎯

