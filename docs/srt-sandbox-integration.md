# Anthropic Sandbox Runtime (srt) Integration

## Overview

We've integrated [Anthropic's Sandbox Runtime (srt)](https://github.com/anthropic-experimental/sandbox-runtime) to provide **complete workspace isolation** for each thread. This ensures that agent operations are confined to their designated workspace with controlled filesystem and network access.

## What is srt?

`srt` is a lightweight sandboxing tool that enforces filesystem and network restrictions at the OS level without requiring a container. On Linux, it uses **bubblewrap** (the same tool used by Flatpak) to provide:

- **Filesystem isolation**: Control read/write access to files and directories
- **Network isolation**: Control which domains can be accessed via HTTP/HTTPS
- **Unix socket restrictions**: Block unauthorized IPC channel creation
- **Violation monitoring**: Track and log unauthorized access attempts

## Architecture

```
┌─────────────────────────────────────────────┐
│  Agent Command Request                       │
└─────────────────┬───────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│  sandbox_backend.py                          │
│  ├─ _ensure_workspace_configured()          │
│  │   └─ _create_srt_config()                │
│  │       └─ Creates .srt-settings.json      │
│  └─ execute()                                │
│      └─ Wraps command with srt               │
└─────────────────┬───────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│  srt (Anthropic Sandbox Runtime)            │
│  ├─ Loads .srt-settings.json                │
│  ├─ Starts HTTP/SOCKS5 proxies              │
│  └─ Wraps command with bubblewrap           │
└─────────────────┬───────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│  bubblewrap (Linux kernel namespaces)       │
│  ├─ Filesystem isolation                    │
│  ├─ Network namespace + proxy routing       │
│  └─ Seccomp BPF (Unix socket blocking)     │
└─────────────────┬───────────────────────────┘
                  │
                  ▼
┌─────────────────────────────────────────────┐
│  Isolated Command Execution                 │
│  └─ Runs within sandbox restrictions        │
└─────────────────────────────────────────────┘
```

## Security Model

### Filesystem Isolation

**Read Access (deny-only pattern)**:
- Default: Allow reading from everywhere
- Explicitly deny sensitive paths:
  - `~/.ssh` (SSH keys)
  - `~/.aws`, `~/.gcp` (Cloud credentials)
  - `~/.config` (User configurations with potential secrets)
  - `/etc/shadow`, `/etc/sudoers` (System security files)
  - `/root` (Root home directory)

**Write Access (allow-only pattern)**:
- Default: Deny writing everywhere
- Explicitly allow only:
  - Thread workspace directory (`/mnt/r2/{bucket}/threads/{thread_id}/`)
- Additional protections:
  - `.srt-settings.json` (prevent tampering)
  - `.workspace_configured` (prevent tampering)

**Mandatory Denials** (srt built-in):
Even if workspace is allowed for writes, these are always blocked:
- Shell configs: `.bashrc`, `.zshrc`, `.profile`
- Git hooks: `.git/hooks/`, `.git/config`
- IDE configs: `.vscode/`, `.idea/`
- MCP config: `.mcp.json`

### Network Isolation

**Network Access (allow-only pattern)**:
- Default: Deny all network access
- Explicitly allow:
  - **Package managers**: `pypi.org`, `registry.npmjs.org`, `bun.sh`
  - **Git repositories**: `github.com`, `gitlab.com`
  - **APIs**: `api.openai.com` (configurable)
  - **CDNs**: `cdn.jsdelivr.net`, `unpkg.com`
  - **Localhost**: `127.0.0.1`, `localhost` (for development servers)

**How it works**:
1. srt removes the network namespace entirely
2. Starts HTTP and SOCKS5 proxies on the host
3. Routes all traffic through these proxies via Unix sockets
4. Proxies enforce domain allowlists/denylists

### Unix Socket Restrictions (Linux)

- Uses **seccomp BPF** to block `socket(AF_UNIX, ...)` syscalls
- Prevents processes from creating new Unix domain sockets
- Blocks unauthorized IPC channels
- Pre-built static binaries for x64 and arm64

## Implementation Details

### 1. Installation (E2B Template)

```python
# src/e2b_sandbox/template.py
.run_cmd("npm install -g @anthropic-ai/sandbox-runtime", user="root")
```

### 2. Configuration File Generation

During workspace initialization (`_ensure_workspace_configured`), we create `.srt-settings.json`:

```python
def _create_srt_config(self) -> None:
    srt_config = {
        "filesystem": {
            "denyRead": ["~/.ssh", "~/.aws", ...],
            "allowWrite": [self._base_path],
            "denyWrite": [".srt-settings.json", ".workspace_configured"]
        },
        "network": {
            "allowedDomains": ["pypi.org", "github.com", ...],
            "deniedDomains": []
        },
        "allowAllUnixSockets": False
    }
    self._sandbox.files.write(f"{self._base_path}/.srt-settings.json", ...)
```

### 3. Command Execution

All commands are wrapped with `srt`:

```python
def _execute_simple(self, command: str) -> ExecuteResponse:
    srt_settings_path = f"{self._base_path}/.srt-settings.json"
    sandboxed_command = f"srt --settings {srt_settings_path} bash -c {command}"
    result = self._sandbox.commands.run(sandboxed_command, timeout=60000)
```

## Configuration Per Thread

Each thread workspace has its own `.srt-settings.json` file:

```
/mnt/r2/{bucket}/threads/{thread_id}/
├── .srt-settings.json          # Sandbox configuration (THIS THREAD)
├── .workspace_configured        # Configuration marker
├── .venv/                       # Python environment
├── node_modules/                # Node.js dependencies
├── pyproject.toml              # Python project config
├── package.json                # Node.js project config
├── .solven/                    # Symlink to skills
└── .ticket/                    # Symlink to ticket workspace (if applicable)
```

## Violation Detection

### Automatic Monitoring (Linux)

Use `strace` to trace sandbox violations:

```bash
# Trace all denied operations
strace -f srt <command> 2>&1 | grep EPERM

# Trace specific file operations
strace -f -e trace=open,openat,stat,access srt <command> 2>&1 | grep EPERM

# Trace network operations
strace -f -e trace=network srt <command> 2>&1 | grep EPERM
```

### Example Violations

**Filesystem**:
```bash
# Attempt to read SSH keys (blocked)
$ srt "cat ~/.ssh/id_rsa"
cat: /home/user/.ssh/id_rsa: Operation not permitted

# Attempt to write to .bashrc (blocked by mandatory denial)
$ srt "echo 'malicious' >> .bashrc"
/bin/bash: .bashrc: Operation not permitted
```

**Network**:
```bash
# Attempt to access unauthorized domain (blocked)
$ srt "curl https://unauthorized-domain.com"
Connection blocked by network allowlist
```

## Benefits

1. **Defense in Depth**: Multiple layers of isolation (bubblewrap + seccomp + proxies)
2. **Secure by Default**: Minimal access by default, explicit allowlisting required
3. **Per-Thread Isolation**: Each thread has its own sandbox configuration
4. **No Container Overhead**: Uses native OS primitives, not Docker
5. **Network Control**: Fine-grained domain-based filtering
6. **Violation Monitoring**: Track and log unauthorized access attempts
7. **Proven Technology**: Same tool used by Claude Code for agent safety

## Deployment Checklist

### 1. Rebuild E2B Template

```bash
cd /home/ramon/Github/metaloss/solven-agentserver-langgraph
uv run python src/e2b_sandbox/template.py
```

Wait for template to build (~10-15 minutes). This installs `srt` globally.

### 2. Test srt Installation

After template builds, test in a new sandbox:

```python
from e2b import Sandbox

sandbox = Sandbox()
result = sandbox.commands.run("which srt", timeout=5000)
print(f"srt path: {result.stdout}")  # Should show: /usr/local/bin/srt

result = sandbox.commands.run("srt --version", timeout=5000)
print(f"srt version: {result.stdout}")
sandbox.close()
```

### 3. Verify Workspace Configuration

Start a new thread and check that `.srt-settings.json` is created:

```python
# In agent server logs, look for:
[Workspace] Creating srt sandbox configuration...
[Workspace] ✓ SRT config created at /mnt/r2/.../threads/{thread_id}/.srt-settings.json
```

### 4. Test Command Execution

Send a simple command and verify srt wrapping:

```python
# In agent server logs, look for:
[SandboxBackend.execute] 🔒 SRT isolated execution
[SandboxBackend.execute] Workspace: /mnt/r2/.../threads/{thread_id}/
[SandboxBackend.execute] Command: echo "Hello World"
```

### 5. Test Filesystem Restrictions

```python
# Should fail (reading SSH keys)
execute("cat ~/.ssh/id_rsa")

# Should fail (writing to system directory)
execute("echo 'test' > /etc/test.txt")

# Should succeed (writing to workspace)
execute("echo 'test' > /test.txt")
```

### 6. Test Network Restrictions

```python
# Should succeed (allowed domain)
execute("curl -I https://pypi.org")

# Should fail (unauthorized domain)
execute("curl -I https://unauthorized-domain.com")
```

## Troubleshooting

### Issue: `srt: command not found`

**Cause**: E2B template not rebuilt with srt installation

**Solution**:
```bash
cd /home/ramon/Github/metaloss/solven-agentserver-langgraph
uv run python src/e2b_sandbox/template.py
```

### Issue: `bubblewrap: not found`

**Cause**: bubblewrap not installed in E2B template

**Solution**: Already included in template:
```python
.apt_install(["bubblewrap"])
```

### Issue: Commands slow to execute

**Cause**: srt proxy initialization overhead on first run

**Mitigation**: srt caches proxy instances, subsequent commands are faster

### Issue: Network access blocked unexpectedly

**Cause**: Domain not in allowlist

**Solution**: Add domain to `.srt-settings.json`:
```python
# In _create_srt_config():
"allowedDomains": [
    "pypi.org",
    "new-domain.com",  # Add here
    ...
]
```

### Issue: Cannot write to workspace

**Cause**: Workspace path not in allowWrite

**Solution**: Verify `self._base_path` is in `allowWrite` list (already configured)

## Security Limitations

Per srt documentation:

1. **Network filtering is domain-based**: Does not inspect packet contents
   - Allowing broad domains like `github.com` may allow data exfiltration
   - Consider using custom MITM proxy for finer control

2. **Unix socket allowlist can escalate privileges**: 
   - Allowing `/var/run/docker.sock` would grant host access
   - Current config: `allowAllUnixSockets: false` (secure)

3. **Filesystem permission escalation**:
   - Overly broad write permissions can enable attacks
   - Current config: Only allow thread workspace (secure)

4. **Domain fronting bypass**:
   - Some CDNs allow fronting to bypass domain restrictions
   - Mitigation: Limit CDN access, use custom proxy for inspection

## Future Enhancements

1. **Custom MITM Proxy**: Integrate mitmproxy for traffic inspection
2. **Violation Alerting**: Real-time alerts on security violations
3. **Dynamic Allowlists**: Per-task domain allowlists based on ticket context
4. **Resource Limits**: CPU, memory, and disk quotas via cgroups
5. **Audit Logging**: Comprehensive logging of all sandbox operations

## References

- [Anthropic Sandbox Runtime GitHub](https://github.com/anthropic-experimental/sandbox-runtime)
- [Claude Code Sandboxing Documentation](https://docs.claude.com/en/docs/claude-code/sandboxing)
- [Beyond Permission Prompts: Making Claude Code More Secure](https://www.anthropic.com/engineering/claude-code-sandboxing)
- [Bubblewrap Documentation](https://github.com/containers/bubblewrap)
- [ArchWiki: Bubblewrap](https://wiki.archlinux.org/title/Bubblewrap)

