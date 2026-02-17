# Fast Workspace Configuration

## Overview

Workspace configuration is now **ultra-fast** (~5 seconds) by initializing environments without installing packages. Packages are installed **on-demand** when needed.

## What Changed

### Before (Slow - 1-2 minutes)
```
✗ Blocked other threads
✗ Installed all packages upfront
✗ 30-60 seconds for Python packages
✗ 10-30 seconds for Node packages
```

### After (Fast - ~5 seconds)
```
✓ Non-blocking
✓ Just initialize uv and bun
✓ ~3 seconds for Python init
✓ ~2 seconds for Node init
✓ Packages installed on first use
```

## Configuration Steps

### 1. Directory Structure (~1s)
```bash
mkdir -p /mnt/r2/.../threads/{thread_id}/.solven/skills
```

### 2. Symlinks (~1s)
```bash
ln -sfn /mnt/r2/.../skills/system /.solven/skills/system
ln -sfn /mnt/r2/.../skills/{user_id} /.solven/skills/user
```

### 3. Python Init (~3s)
```bash
uv init --python 3.12
# Creates:
# - pyproject.toml (empty dependencies)
# - .venv/ (Python 3.12 venv)
# - hello.py (sample file)
```

### 4. Node Init (~2s)
```bash
bun init -y
# Creates:
# - package.json
# - index.ts
# - tsconfig.json
```

### 5. Config Files (~1s)
```bash
# .bashrc, .gitignore, .workspace_configured
```

**Total: ~5-8 seconds** ⚡

## Installing Packages On-Demand

### Python Packages

**Option 1: Let the agent install**
```python
# Agent code can use uv directly
import subprocess
subprocess.run(['uv', 'pip', 'install', 'pandas'])

import pandas as pd
```

**Option 2: Pre-install common packages**
Add to agent instructions:
```
Before processing data, ensure pandas is installed:
uv pip install pandas numpy matplotlib
```

**Option 3: Use helper function**
```bash
# In bash
py-ensure pandas  # Auto-installs if missing
python -c "import pandas"
```

### Node Packages

```bash
# Install when needed
bun add docx

# Then use
bun run script.js
```

## Common Package Installation Commands

### Python (uv)

```bash
# Data science stack
uv pip install pandas numpy matplotlib seaborn

# PDF processing
uv pip install pypdf pdfplumber reportlab

# Excel processing
uv pip install openpyxl xlrd

# Image processing
uv pip install Pillow pytesseract pdf2image

# Web scraping
uv pip install beautifulsoup4 lxml requests

# All at once (comprehensive)
uv pip install pandas numpy matplotlib seaborn \
  pypdf pdfplumber reportlab pdf2image \
  openpyxl xlrd Pillow pytesseract \
  beautifulsoup4 lxml requests httpx \
  defusedxml python-dateutil pytz
```

### Node (bun)

```bash
# Document processing
bun add docx

# PDF generation
bun add pdfkit

# Excel processing
bun add xlsx
```

## Agent Instructions

Add to system prompt:

```markdown
## Package Management

**Python packages:**
- Use `uv pip install <package>` to install packages
- Common packages: pandas, numpy, matplotlib, openpyxl, pypdf

**Node packages:**
- Use `bun add <package>` to install packages
- Common packages: docx, xlsx, pdfkit

**Example:**
```python
# Install if needed
import subprocess
subprocess.run(['uv', 'pip', 'install', 'pandas'], check=True)

# Then use
import pandas as pd
```
```

## Performance Comparison

| Metric | Before | After | Improvement |
|--------|--------|-------|-------------|
| **Initial config** | 90-120s | 5-8s | **15-20x faster** |
| **Blocking time** | 90-120s | 5-8s | **15-20x faster** |
| **First pandas use** | 0s (pre-installed) | 5-10s (install) | Delayed |
| **Subsequent use** | 0s | 0s | Same |
| **Multi-thread** | Blocked | Parallel | ✅ Huge win |

## Trade-offs

### Pros ✅
- **15-20x faster initial configuration**
- **Non-blocking** - other threads can start immediately
- **Lazy loading** - only install what's actually used
- **Flexible** - agent can install any package on-demand

### Cons ⚠️
- **First-use delay** - Package installation on first import
- **Agent awareness** - Agent needs to know to install packages
- **Multiple installs** - Each package installed when first needed

## Best Practices

### 1. Pre-install Common Packages

For frequently used packages, add a setup script:

```python
# /.solven/setup_common.py
import subprocess

common_packages = [
    'pandas', 'numpy', 'matplotlib',
    'openpyxl', 'pypdf', 'Pillow'
]

print("Installing common packages...")
subprocess.run(['uv', 'pip', 'install'] + common_packages)
print("✓ Common packages installed")
```

Run once: `python /.solven/setup_common.py`

### 2. Install Before Use

```python
def ensure_package(package_name, import_name=None):
    """Ensure a package is installed before importing."""
    if import_name is None:
        import_name = package_name
    
    try:
        __import__(import_name)
    except ImportError:
        import subprocess
        subprocess.run(['uv', 'pip', 'install', package_name], check=True)
        __import__(import_name)

# Usage
ensure_package('pandas')
import pandas as pd
```

### 3. Batch Installation

```python
# Install multiple packages at once (faster)
subprocess.run([
    'uv', 'pip', 'install',
    'pandas', 'numpy', 'matplotlib'
], check=True)
```

## Monitoring

### Check Installation Status

```bash
# Python packages
uv pip list

# Node packages
bun pm ls

# Package locations
ls -la /.venv/lib/python3.12/site-packages/
ls -la /node_modules/
```

### Installation Time

```python
import time
start = time.time()

# Install package
subprocess.run(['uv', 'pip', 'install', 'pandas'])

print(f"Installed in {time.time() - start:.1f}s")
# Typical: 5-10 seconds for pandas
```

## Future Optimizations

### Option 1: Pre-built Cache
- Cache common package installations in R2
- Copy from cache instead of downloading
- Reduce install time to <1 second

### Option 2: Predictive Installation
- Detect common imports in code
- Auto-install before execution
- Transparent to agent

### Option 3: Package Templates
- Pre-configured environments for different tasks
- "Data Science" template with pandas, numpy, matplotlib
- "Document Processing" template with pypdf, docx
- "Web Scraping" template with requests, beautifulsoup4

## Troubleshooting

### Package Not Found

**Symptom:** `ModuleNotFoundError: No module named 'pandas'`

**Solution:** Install the package:
```bash
uv pip install pandas
```

### Slow Installation

**Symptom:** Package installation takes >30 seconds

**Possible causes:**
- Large package (e.g., scipy, torch)
- Slow internet connection
- Package has many dependencies

**Solutions:**
- Use smaller alternatives
- Pre-install large packages
- Check network connectivity

### Installation Fails

**Symptom:** `uv pip install` returns error

**Check:**
```bash
# Is uv working?
uv --version

# Is venv activated?
which python

# Disk space?
df -h /
```

## Summary

✅ **Workspace configuration is now 15-20x faster**
✅ **Non-blocking - other threads start immediately**
✅ **Packages install on-demand when needed**
✅ **Agent has full control over package management**

**Result:** Fast startup + flexible package management = better user experience!

