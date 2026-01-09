# Complete proot Environment Setup

## Overview

The workspace uses **proot** for complete process isolation with a fully configured Python and Node.js environment. This ensures reliable, independent execution of agent commands.

## Architecture

### proot Isolation

```
Host System
├── /mnt/r2/{bucket}/threads/{thread_id}/  (Real workspace on R2)
│
└── proot -r /mnt/r2/.../threads/{thread_id}/
    │
    ├── / (appears as root inside proot)
    │   ├── .venv/           → Python virtual environment
    │   ├── pyproject.toml   → Python dependencies
    │   ├── package.json     → Node dependencies
    │   ├── node_modules/    → Node packages
    │   ├── .solven/skills/  → Skills symlinks
    │   ├── .ticket/         → Ticket symlink
    │   ├── .bashrc          → Environment configuration
    │   └── (user files)
    │
    └── Bind Mounts (from host):
        ├── /bin, /usr, /lib, /lib64  → System binaries and libraries
        ├── /etc                      → System configuration
        ├── /dev, /proc, /sys         → System devices and info
        ├── /tmp, /var                → Temporary and variable data
        └── /mnt/r2/{bucket}          → For symlink resolution
```

## Essential Bind Mounts

### Why Each Mount is Needed

| Mount | Purpose | Required For |
|-------|---------|--------------|
| `/bin` | System binaries | bash, sh, ls, cat, etc. |
| `/usr` | User binaries & libraries | python, node, pip, npm, gcc |
| `/lib`, `/lib64` | System libraries | Shared libraries for all programs |
| `/etc` | System config | DNS, users, timezone, locales |
| `/dev` | Device files | /dev/null, /dev/zero, /dev/urandom |
| `/proc` | Process info | CPU info, memory, process list |
| `/sys` | System info | Hardware information |
| `/tmp` | Temporary files | Temporary storage for processes |
| `/var` | Variable data | Logs, caches, package manager data |
| `/mnt/r2/{bucket}` | R2 access | Symlink resolution for skills/ticket |

### Critical Devices

```bash
-b /dev/null:/dev/null       # Required by many programs
-b /dev/zero:/dev/zero       # Memory operations
-b /dev/urandom:/dev/urandom # Random number generation
```

## Environment Variables

### Set Inside proot

```bash
# === User Environment ===
export HOME=/                # Home directory at root
export USER=user             # User name
export LOGNAME=user          # Login name
export PWD=/                 # Current directory

# === Python Configuration ===
export PYTHONUNBUFFERED=1           # No output buffering (see results immediately)
export PYTHONDONTWRITEBYTECODE=1    # No .pyc files (cleaner workspace)
export MPLBACKEND=Agg               # Matplotlib headless mode (no GUI)
export PYTHON_VERSION=3.12          # Python version marker

# === Node.js Configuration ===
export NODE_ENV=development   # Development mode

# === PATH Configuration ===
export PATH="/.venv/bin:/node_modules/.bin:$PATH"
# /.venv/bin          → Python binaries (python, pip, uv)
# /node_modules/.bin  → Node binaries (bun, installed packages)
# $PATH               → System binaries
```

## Python Environment (uv)

### Setup Process

1. **Initialize uv project:**
   ```bash
   uv init --python 3.12
   ```
   - Creates `pyproject.toml`
   - Creates `.venv/` directory
   - Installs Python 3.12

2. **Add packages:**
   ```bash
   uv add pandas numpy matplotlib ...
   ```
   - Updates `pyproject.toml`
   - Installs to `.venv/`
   - ~6x faster than pip

3. **Auto-activation:**
   - `.bashrc` activates venv automatically
   - PATH includes `/.venv/bin`
   - All Python commands use venv

### Installed Packages

**Data Manipulation:**
- pandas - Data frames and analysis
- numpy - Numerical computing

**Excel Processing:**
- openpyxl - Read/write Excel 2010+ (.xlsx)
- xlrd - Read older Excel files (.xls)

**PDF Processing:**
- pypdf - PDF manipulation
- pdfplumber - PDF text extraction
- reportlab - PDF generation
- pdf2image - Convert PDF to images

**Image Processing:**
- Pillow - Image manipulation
- pytesseract - OCR (text from images)

**Plotting:**
- matplotlib - Create charts and graphs
- seaborn - Statistical visualizations

**Web/XML:**
- defusedxml - Safe XML parsing
- beautifulsoup4 - HTML/XML parsing
- lxml - Fast XML/HTML processing

**HTTP:**
- requests - HTTP client
- httpx - Async HTTP client

**Utilities:**
- python-dateutil - Date/time parsing
- pytz - Timezone handling

## Node.js Environment (Bun)

### Setup Process

1. **Initialize Bun project:**
   ```bash
   bun init -y
   ```
   - Creates `package.json`
   - Sets up Bun project structure

2. **Add packages:**
   ```bash
   bun add docx
   ```
   - Updates `package.json`
   - Installs to `node_modules/`
   - ~10x faster than npm

3. **PATH Configuration:**
   - `/node_modules/.bin` in PATH
   - Can run installed binaries directly

### Installed Packages

- **docx** - Word document manipulation

## Configuration Files

### .bashrc (Auto-sourced)

Complete environment setup:
```bash
# User environment
export HOME=/
export USER=user

# Python configuration
export PYTHONUNBUFFERED=1
export MPLBACKEND=Agg

# PATH with venv and node_modules
export PATH="/.venv/bin:/node_modules/.bin:$PATH"

# Auto-activate venv
[ -f /.venv/bin/activate ] && source /.venv/bin/activate

# Working directory
cd /
```

### pyproject.toml (Python Dependencies)

```toml
[project]
name = "workspace"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = [
    "pandas",
    "numpy",
    "matplotlib",
    # ... all packages ...
]
```

### package.json (Node Dependencies)

```json
{
  "name": "workspace",
  "module": "index.ts",
  "type": "module",
  "dependencies": {
    "docx": "^8.0.0"
  }
}
```

## Execution Flow

### When Command is Executed

1. **proot starts:**
   ```bash
   proot -r /mnt/r2/.../threads/{thread_id}/ \
         -b /bin:/bin -b /usr:/usr ... \
         -w / \
         /bin/bash --login -c 'command'
   ```

2. **Bash loads `.bashrc`:**
   - Sets environment variables
   - Activates Python venv
   - Configures PATH
   - Changes to `/` directory

3. **Command executes:**
   - Python: Uses venv interpreter
   - Node: Uses Bun runtime
   - Files: Created in `/` (mapped to workspace)
   - Output: Unbuffered (immediate)

4. **Files persist:**
   - All changes go to R2
   - Workspace is persistent
   - No overlay needed

## Example Commands

### Python Script

```python
# Inside proot: /script.py
import pandas as pd
import matplotlib.pyplot as plt

# Create data
df = pd.DataFrame({'x': [1,2,3], 'y': [4,5,6]})

# Create plot
plt.plot(df['x'], df['y'])
plt.savefig('/chart.png')  # Saves to workspace root

print("Chart saved!")  # Immediate output (PYTHONUNBUFFERED)
```

**Execute:**
```bash
python /script.py
```

**What happens:**
1. proot isolates environment
2. .bashrc activates venv
3. python uses venv interpreter
4. matplotlib uses Agg backend
5. File saves to `/chart.png` (workspace root)
6. Output appears immediately

### Node.js Script

```javascript
// Inside proot: /script.js
const { Document, Packer, Paragraph } = require('docx');
const fs = require('fs');

// Create document
const doc = new Document({
  sections: [{
    children: [
      new Paragraph("Hello World"),
    ],
  }],
});

// Save document
Packer.toBuffer(doc).then(buffer => {
  fs.writeFileSync('/document.docx', buffer);
  console.log('Document saved!');
});
```

**Execute:**
```bash
bun /script.js
```

**What happens:**
1. proot isolates environment
2. bun runtime executes
3. docx package from node_modules
4. File saves to `/document.docx`
5. Output appears immediately

## Troubleshooting

### Python Issues

**Issue:** `python: command not found`
- **Check:** Is venv activated? `which python`
- **Check:** Is PATH set? `echo $PATH`
- **Fix:** Ensure `.bashrc` is sourced

**Issue:** Package not found
- **Check:** Is it in `pyproject.toml`?
- **Fix:** Re-run workspace configuration

**Issue:** Files not created
- **Check:** Exit code: `echo $?`
- **Check:** Permissions: `ls -la /`
- **Check:** PYTHONUNBUFFERED is set

### Node.js Issues

**Issue:** `bun: command not found`
- **Check:** Is bun in PATH? `which bun`
- **Fix:** Ensure PATH includes system binaries

**Issue:** Package not found
- **Check:** Is it in `package.json`?
- **Check:** Does `node_modules/` exist?

### proot Issues

**Issue:** Permission denied
- **Check:** Are bind mounts correct?
- **Check:** Does `/dev/null` exist?
- **Fix:** Verify all essential mounts

**Issue:** Command fails with exit 0 but no output
- **Check:** Is PYTHONUNBUFFERED set?
- **Check:** Is output being redirected?
- **Fix:** Add explicit print statements

## Benefits

### Reliability
- ✅ Complete system isolation
- ✅ All dependencies pre-installed
- ✅ Reproducible environment
- ✅ No conflicts with host

### Independence
- ✅ Self-contained workspace
- ✅ Own Python environment
- ✅ Own Node environment
- ✅ Own package installations

### Performance
- ✅ uv (~6x faster than pip)
- ✅ Bun (~10x faster than npm)
- ✅ No overhead from overlays
- ✅ Direct R2 persistence

### Maintainability
- ✅ Clear environment setup
- ✅ Well-documented configuration
- ✅ Easy to debug
- ✅ Standard tools (uv, Bun)

## Verification Commands

### Check Environment

```bash
# Inside proot workspace:
printenv | grep -E 'HOME|USER|PATH|PYTHON|NODE'
which python
which bun
python --version
bun --version
```

### Check Packages

```bash
# Python:
uv pip list

# Node:
bun pm ls
```

### Check Files

```bash
# List workspace:
ls -la /

# Check specific files:
cat /pyproject.toml
cat /package.json
cat /.bashrc
```

## Summary

The proot environment provides:

1. **Complete Isolation**: Separate process namespace
2. **System Access**: Essential system resources via bind mounts
3. **Python Stack**: uv-managed with 20+ packages
4. **Node Stack**: Bun-managed with fast runtime
5. **Proper Configuration**: Environment variables, PATH, auto-activation
6. **Reliable Execution**: Unbuffered output, proper backends
7. **Persistent Storage**: Direct R2 backing

This setup ensures that Python and Node.js commands work reliably and independently in a complete, isolated environment.

