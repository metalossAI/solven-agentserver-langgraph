# SRT - Correct Implementation ✅

## Core Principle

**Don't modify paths - let the agent use whatever it wants, let SRT handle security**

## SRT Configuration

```python
srt_config = {
    "filesystem": {
        "denyRead": ["~/.ssh", "~/.aws", "/etc/shadow"],
        "allowWrite": [
            ".",                # Relative paths (workspace after cd)
            self._base_path,    # Absolute workspace path
        ],
        "denyWrite": [
            f"{self._base_path}/.srt-settings.json",
            f"{self._base_path}/.workspace_configured",
        ]
    },
    "network": {
        "allowedDomains": ["pypi.org", "registry.npmjs.org", "github.com", ...]
    },
    "allowAllUnixSockets": False
}
```

## Command Execution

```bash
# Step 1: CD into workspace
cd /mnt/r2/{bucket}/threads/{thread_id}/

# Step 2: Run with SRT  
srt --settings .srt-settings.json bash -c "agent_command"
```

## All Methods - No Path Modification

```python
def ls_info(self, path: str) -> list[FileInfo]:
    # Use path AS-IS
    cmd = f"find {shlex.quote(path or '.')} -maxdepth 1 ..."
    return self._run_srt_command(cmd)

def read(self, path: str, ...) -> str:
    # Use path AS-IS
    cmd = f"cat {shlex.quote(path)}"
    return self._run_srt_command(cmd)

def write(self, file_path: str, content: str) -> WriteResult:
    # Use path AS-IS
    cmd = f"echo {content_b64} | base64 -d > {shlex.quote(file_path)}"
    return self._run_srt_command(cmd)

def edit(self, path: str, ...) -> EditResult:
    # Use path AS-IS
    cmd = f"cat {shlex.quote(path)}"
    # ... edit logic ...
    cmd = f"echo {content_b64} | base64 -d > {shlex.quote(path)}"
    return self._run_srt_command(cmd)

def grep_raw(self, pattern: str, path: Optional[str] = None, ...) -> list[GrepMatch]:
    # Use path AS-IS
    search_path = path or "."
    cmd = f"grep -rn {shlex.quote(pattern)} {shlex.quote(search_path)}"
    return self._run_srt_command(cmd)

def glob_info(self, pattern: str, path: str = "/") -> list[FileInfo]:
    # Use path AS-IS
    cmd = f"find {shlex.quote(path or '.')} -name {shlex.quote(pattern)} ..."
    return self._run_srt_command(cmd)
```

## How It Works

### Agent uses relative path
```
Agent: write("prueba.txt", "Hello")
↓
Command: echo {base64} | base64 -d > prueba.txt
↓
SRT: Is "prueba.txt" under "."? ✅ YES
↓
Write succeeds at: /mnt/r2/.../threads/{id}/prueba.txt
```

### Agent uses absolute path (workspace)
```
Agent: write("/mnt/r2/.../threads/{id}/file.txt", "Hello")
↓
Command: echo {base64} | base64 -d > /mnt/r2/.../threads/{id}/file.txt
↓
SRT: Is "/mnt/r2/.../threads/{id}/file.txt" under allowWrite paths? ✅ YES
↓
Write succeeds
```

### Agent uses absolute path (outside workspace)
```
Agent: write("/etc/passwd", "Bad")
↓
Command: echo {base64} | base64 -d > /etc/passwd
↓
SRT: Is "/etc/passwd" under allowWrite paths? ❌ NO
↓
❌ Operation not permitted
```

## Key Points

✅ **No path stripping** - Use paths exactly as provided  
✅ **No path normalization** - Let agent use whatever it wants  
✅ **SRT handles security** - Allow workspace paths, block everything else  
✅ **Both relative and absolute** - Both work correctly  

## What We Don't Do

❌ `path.lstrip("/")`  
❌ Path resolution to absolute  
❌ Path normalization  
❌ Any path transformation  

## What SRT Does

✅ Checks if path is under `allowWrite` directories  
✅ Blocks writes outside allowed paths  
✅ Allows reads everywhere except `denyRead`  
✅ Enforces network restrictions  

**Simple, clean, secure!** 🎉

