# Complete Workspace Structure with bwrap Isolation

## Overview

Each agent workspace is fully isolated using bwrap, with access to shared skills and ticket data via symlinks.

## Workspace Structure

```
/mnt/r2/{bucket}/threads/{thread_id}/    ← Workspace (mounted as / in bwrap)
├── .solven/                              ← Skills access
│   └── skills/
│       ├── system -> /mnt/r2/{bucket}/skills/system
│       └── user -> /mnt/r2/{bucket}/skills/{user_id}
├── .ticket -> /mnt/r2/{bucket}/threads/{ticket_id}  (if ticket exists)
├── .venv/                                ← Isolated Python environment
│   ├── bin/python
│   └── lib/python3.12/site-packages/
├── node_modules/                         ← Isolated Node environment
│   └── .bin/
├── .cache/ (tmpfs)                       ← Fresh cache per execution
├── .workspace_configured                 ← Configuration marker
└── [agent files]                         ← Scripts, data, outputs
```

## Inside bwrap Container

When bwrap mounts the workspace as `/`, it becomes:

```
/                                         ← Workspace root
├── .solven/                              ← Accessible via symlink
│   └── skills/
│       ├── system/                       ← Read-only system skills
│       │   ├── skill1.md
│       │   └── skill2.md
│       └── user/                         ← Read-only user skills
│           ├── custom1.md
│           └── custom2.md
├── .ticket/                              ← Accessible via symlink (if exists)
│   ├── context.json
│   └── files/
├── .venv/                                ← Workspace Python
│   ├── bin/python
│   └── lib/python3.12/site-packages/
├── node_modules/                         ← Workspace Node
│   └── .bin/
├── plot.png                              ← Agent-generated file
└── script.py                             ← Agent-generated script

System directories (read-only):
├── /usr/
├── /lib/
├── /bin/
├── /etc/
├── /proc/
└── /dev/
```

## bwrap Mount Configuration

```python
bwrap_cmd = [
    "bwrap",
    
    # === WORKSPACE (Writable) ===
    "--bind", workspace_path, "/",
    
    # === R2 MOUNTS (Read-Only) for symlinks ===
    "--ro-bind", "/mnt/r2/{bucket}/skills", "/mnt/r2/{bucket}/skills",
    "--ro-bind", "/mnt/r2/{bucket}/threads/{ticket_id}", "/mnt/r2/{bucket}/threads/{ticket_id}",  # if ticket exists
    
    # === SYSTEM (Read-Only) ===
    "--ro-bind", "/usr", "/usr",
    "--ro-bind", "/lib", "/lib",
    "--ro-bind", "/bin", "/bin",
    "--ro-bind", "/etc", "/etc",
    
    # === SYSTEM RESOURCES ===
    "--proc", "/proc",
    "--dev", "/dev",
    "--tmpfs", "/tmp",
    "--tmpfs", "/.cache",
    
    # ... environment variables and command ...
]
```

## Symlink Creation

### 1. `.solven/skills/system` (System Skills)

```python
# Target: /mnt/r2/{bucket}/skills/system
# Link: workspace/.solven/skills/system

ln -sf /mnt/r2/{bucket}/skills/system workspace/.solven/skills/system
```

**Inside bwrap:**
```
/.solven/skills/system/ → /mnt/r2/{bucket}/skills/system/
```

**Agent can access:**
```python
# Read system skill
with open('/.solven/skills/system/data-analysis.md', 'r') as f:
    skill = f.read()
```

### 2. `.solven/skills/user` (User-Specific Skills)

```python
# Target: /mnt/r2/{bucket}/skills/{user_id}
# Link: workspace/.solven/skills/user

ln -sf /mnt/r2/{bucket}/skills/{user_id} workspace/.solven/skills/user
```

**Inside bwrap:**
```
/.solven/skills/user/ → /mnt/r2/{bucket}/skills/{user_id}/
```

**Agent can access:**
```python
# Read user's custom skill
with open('/.solven/skills/user/my-custom-skill.md', 'r') as f:
    skill = f.read()
```

### 3. `.ticket` (Ticket Context)

```python
# Target: /mnt/r2/{bucket}/threads/{ticket_id}
# Link: workspace/.ticket

ln -sf /mnt/r2/{bucket}/threads/{ticket_id} workspace/.ticket
```

**Inside bwrap (if ticket exists):**
```
/.ticket/ → /mnt/r2/{bucket}/threads/{ticket_id}/
```

**Agent can access:**
```python
# Read ticket context
with open('/.ticket/context.json', 'r') as f:
    ticket_data = json.load(f)

# Access ticket files
files = os.listdir('/.ticket/files/')
```

## Access Patterns

### From Agent Code

```python
# System skills (read-only)
import os
system_skills = os.listdir('/.solven/skills/system/')
# ['data-analysis.md', 'web-scraping.md', 'pdf-processing.md']

# User skills (read-only)
user_skills = os.listdir('/.solven/skills/user/')
# ['my-custom-skill.md', 'company-specific.md']

# Ticket data (read-only, if exists)
if os.path.exists('/.ticket'):
    with open('/.ticket/context.json', 'r') as f:
        ticket = json.load(f)

# Workspace files (read-write)
with open('/output.txt', 'w') as f:
    f.write('Result')
```

### From Bash Commands

```bash
# List system skills
ls /.solven/skills/system/

# Read user skill
cat /.solven/skills/user/my-skill.md

# Check ticket
test -d /.ticket && echo "Ticket exists" || echo "No ticket"

# Create workspace file
echo "data" > /result.txt
```

## Security & Isolation

### Read-Only Access

**Skills and ticket are read-only:**
```python
# ✅ Can read
content = open('/.solven/skills/system/skill.md', 'r').read()

# ❌ Cannot write (permission denied)
open('/.solven/skills/system/skill.md', 'w').write('hack')
```

**Enforced by:**
- `--ro-bind` flag in bwrap
- Filesystem permissions in R2 mount

### Workspace Isolation

**Each workspace is completely independent:**

```
Thread A:
  /mnt/r2/bucket/threads/thread-a/
  ├── .venv/ → pandas==1.0
  ├── .solven/ → shared skills (read-only)
  └── files/

Thread B:
  /mnt/r2/bucket/threads/thread-b/
  ├── .venv/ → pandas==2.0
  ├── .solven/ → shared skills (read-only)
  └── files/

No conflicts! Different Python environments, same skills.
```

### Network Access

**Shared with host but filtered by E2B:**
- Package managers: ✅ pypi.org, npmjs.org
- Git repos: ✅ github.com, gitlab.com
- APIs: Depends on E2B configuration
- Local network: ❌ Blocked by E2B

## Use Cases

### 1. Agent Uses System Skill

```python
# Agent reads skill to understand capability
with open('/.solven/skills/system/pdf-processing.md', 'r') as f:
    skill_doc = f.read()
    # Parse skill to learn about PDF functions

# Agent uses the skill
import pypdf
# ... process PDF ...
```

### 2. Agent Uses Custom User Skill

```python
# User created a custom skill for their domain
with open('/.solven/skills/user/company-data-format.md', 'r') as f:
    format_spec = f.read()
    # Agent learns company-specific data format

# Agent processes data according to spec
# ... parse company data ...
```

### 3. Agent Accesses Ticket Context

```python
# Check if working on a ticket
import os
import json

if os.path.exists('/.ticket'):
    # Load ticket context
    with open('/.ticket/context.json', 'r') as f:
        ticket = json.load(f)
    
    print(f"Working on ticket: {ticket['title']}")
    
    # Access ticket files
    for file in os.listdir('/.ticket/files/'):
        print(f"Ticket has file: {file}")
else:
    print("No ticket context")
```

### 4. Multi-Workspace Scenario

```python
# Workspace A (Thread focused on data science)
backend_a = SandboxBackend(context_a)
backend_a.ensure_python_init()
backend_a.execute("uv pip install pandas numpy")

# Reads system skills
backend_a.execute("cat /.solven/skills/system/data-analysis.md")

# Workspace B (Thread focused on web scraping)
backend_b = SandboxBackend(context_b)
backend_b.ensure_python_init()
backend_b.execute("uv pip install beautifulsoup4 requests")

# Reads same skills, different environment
backend_b.execute("cat /.solven/skills/system/web-scraping.md")

# Both share skills, but have isolated Python environments
```

## Configuration Setup

### Workspace Initialization

```python
def _setup_workspace_symlinks(self):
    """Create .solven and .ticket symlinks."""
    
    # Create .solven/skills/ structure
    solven_skills_path = f"{workspace}/.solven/skills"
    mkdir -p {solven_skills_path}
    
    # Link system skills
    ln -sf /mnt/r2/{bucket}/skills/system {solven_skills_path}/system
    
    # Link user skills
    mkdir -p /mnt/r2/{bucket}/skills/{user_id}  # Ensure exists
    ln -sf /mnt/r2/{bucket}/skills/{user_id} {solven_skills_path}/user
    
    # Link ticket (if exists)
    if ticket_id:
        ln -sf /mnt/r2/{bucket}/threads/{ticket_id} {workspace}/.ticket
```

### bwrap Mount Setup

```python
def _run_bwrap_direct(self, bash_command):
    """Run command with bwrap mounting workspace as /."""
    
    bwrap_cmd = [
        "bwrap",
        "--bind", workspace, "/",
        "--ro-bind", skills_path, skills_path,  # For symlinks to work
        "--ro-bind", ticket_path, ticket_path,  # If ticket exists
        # ... rest of mounts ...
    ]
```

## Benefits

### 1. **Efficient Skill Sharing**
- ✅ All workspaces share same skills (no duplication)
- ✅ Skills are read-only (no accidental modification)
- ✅ Skills update globally (update once, all workspaces see it)

### 2. **User-Specific Skills**
- ✅ Each user has their own skill directory
- ✅ Custom skills per user/organization
- ✅ Isolated from other users

### 3. **Ticket Context**
- ✅ Agent can access ticket information
- ✅ Ticket files available (attachments, context)
- ✅ Read-only to prevent modification

### 4. **Complete Isolation**
- ✅ Workspace files are isolated
- ✅ Python/Node environments are isolated
- ✅ Only skills and ticket are shared (read-only)
- ✅ No conflicts between workspaces

## Testing

### Test Symlink Creation

```bash
# Check .solven structure
ls -la workspace/.solven/skills/
# drwxr-xr-x system -> /mnt/r2/bucket/skills/system
# drwxr-xr-x user -> /mnt/r2/bucket/skills/user_id

# Check .ticket (if exists)
ls -la workspace/.ticket
# lrwxrwxrwx .ticket -> /mnt/r2/bucket/threads/ticket_id
```

### Test Agent Access

```python
# Test system skill access
result = backend.execute("cat /.solven/skills/system/data-analysis.md")
assert result.exit_code == 0
assert "data analysis" in result.output.lower()

# Test user skill access
result = backend.execute("cat /.solven/skills/user/custom.md")
assert result.exit_code == 0

# Test ticket access (if exists)
result = backend.execute("test -d /.ticket && echo 'exists' || echo 'missing'")
if has_ticket:
    assert "exists" in result.output
else:
    assert "missing" in result.output
```

### Test Read-Only Enforcement

```python
# Try to write to system skill (should fail)
result = backend.execute("echo 'hack' > /.solven/skills/system/test.md")
assert result.exit_code != 0
assert "read-only" in result.output.lower() or "permission denied" in result.output.lower()

# Write to workspace (should succeed)
result = backend.execute("echo 'data' > /output.txt")
assert result.exit_code == 0
```

## Summary

**Complete workspace structure with bwrap:**

- ✅ Workspace mounted as `/`
- ✅ Skills accessible via `/.solven/skills/{system,user}`
- ✅ Ticket accessible via `/.ticket` (if exists)
- ✅ Python environment at `/.venv/`
- ✅ Node environment at `/node_modules/`
- ✅ All symlinks work correctly
- ✅ Read-only skills (shared)
- ✅ Read-write workspace (isolated)
- ✅ No conflicts between threads

**Agent perspective:**
```
/ = My workspace
/.solven/skills/system/ = System skills (read-only)
/.solven/skills/user/ = My custom skills (read-only)
/.ticket/ = Current ticket context (read-only)
/myfile.txt = My files (read-write)
```

**Simple, secure, efficient!** 🎉

