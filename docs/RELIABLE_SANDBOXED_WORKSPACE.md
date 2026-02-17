# Reliable Sandboxed Workspace Design

## Overview

A production-ready workspace system that provides agents with a secure, isolated environment for executing Python and Node.js code with reliable dependency management and fast initialization.

## Current Architecture ✅

### Storage Layer
```
R2 Bucket: solven-{env}
├── threads/
│   └── {thread_id}/              # Per-thread workspace
│       ├── .workspace_configured  # Marker file (atomic operation)
│       ├── .srt-settings.json    # Sandbox config (if using SRT)
│       ├── pyproject.toml        # Python dependencies (UV)
│       ├── .venv/                # Python virtual environment
│       ├── package.json          # Node dependencies (Bun)
│       ├── node_modules/         # Node packages
│       ├── .solven/              # Skills (symlinked)
│       │   └── skills/
│       │       ├── system/       # System-wide skills
│       │       └── {user_id}/    # User-specific skills
│       ├── .ticket/              # Ticket workspace (symlinked, optional)
│       ├── .gitignore            # Clean repo
│       └── (agent files)         # All agent-created files
├── skills/
│   ├── system/                   # Shared skills
│   └── users/{user_id}/          # User skills
└── tickets/{ticket_id}/          # Ticket workspaces
```

### Isolation Layer (Bubblewrap)
```
Inside Sandbox (agent's view):
/                    → {workspace} (bind mount)
/usr, /lib, /bin     → System (read-only)
/tmp                 → tmpfs (writable)
/.cache              → tmpfs (writable, for UV/npm)
/proc, /dev          → System resources

Environment:
HOME=/
PWD=/
PATH=/.venv/bin:/node_modules/.bin:/usr/local/bin:/usr/bin:/bin
UV_CACHE_DIR=/.cache/uv
```

## Improved Design 🚀

### 1. Template-Based Workspace Initialization

#### Template Structure
```yaml
# workspace-template.yaml
version: "1.0"
name: "default"
description: "Standard Python + Node.js workspace"

python:
  version: "3.12"
  dependencies:
    - matplotlib>=3.8.0
    - pandas>=2.1.0
    - numpy>=1.26.0
    - requests>=2.31.0
    - python-dotenv>=1.0.0
    - pillow>=10.0.0
    
nodejs:
  version: "20"
  runtime: "bun"
  dependencies:
    docx: "^8.5.0"
    axios: "^1.6.0"
    dotenv: "^16.3.0"

system:
  shell: "/bin/bash"
  editor: "nano"
  
security:
  network_enabled: true
  filesystem_readonly: ["/usr", "/lib", "/bin", "/sbin"]
  filesystem_writable: ["/", "/tmp", "/.cache"]
```

#### Template Management

**Storage:**
```
R2:
└── workspace-templates/
    ├── default.yaml              # Standard template
    ├── data-science.yaml         # Heavy data libs
    ├── web-dev.yaml              # Web-focused
    └── minimal.yaml              # Lightweight
```

**Code:**
```python
class WorkspaceTemplate:
    """Manages workspace templates for quick initialization."""
    
    @staticmethod
    def load_template(name: str = "default") -> dict:
        """Load template from R2."""
        template_path = f"workspace-templates/{name}.yaml"
        content = r2_client.get_object(template_path)
        return yaml.safe_load(content)
    
    @staticmethod
    def apply_template(workspace_path: str, template: dict):
        """Apply template to workspace."""
        # Create pyproject.toml from template
        pyproject = {
            "project": {
                "name": "workspace",
                "version": "0.1.0",
                "requires-python": f">={template['python']['version']}",
                "dependencies": template['python']['dependencies']
            }
        }
        
        # Create package.json from template
        package_json = {
            "name": "workspace",
            "type": "module",
            "dependencies": template['nodejs']['dependencies']
        }
        
        # Write configuration files
        write_file(f"{workspace_path}/pyproject.toml", toml.dumps(pyproject))
        write_file(f"{workspace_path}/package.json", json.dumps(package_json, indent=2))
```

### 2. Fast Workspace Sync System

#### Snapshot-Based Initialization

Instead of installing packages every time, use pre-built snapshots:

```python
class WorkspaceSnapshots:
    """Manages pre-built workspace snapshots for instant initialization."""
    
    SNAPSHOT_PATH = "workspace-snapshots/default/"
    
    @staticmethod
    async def create_snapshot():
        """Create a workspace snapshot with all dependencies installed."""
        temp_workspace = "/tmp/workspace-snapshot"
        
        # 1. Initialize workspace
        run_command(f"uv init --python 3.12 {temp_workspace}")
        run_command(f"cd {temp_workspace} && bun init -y")
        
        # 2. Install all default dependencies
        pyproject = load_template("default")['python']
        for dep in pyproject['dependencies']:
            run_command(f"cd {temp_workspace} && uv pip install {dep}")
        
        package_json = load_template("default")['nodejs']
        for dep, version in package_json['dependencies'].items():
            run_command(f"cd {temp_workspace} && bun add {dep}@{version}")
        
        # 3. Upload snapshot to R2
        upload_directory_to_r2(temp_workspace, SNAPSHOT_PATH)
        
    @staticmethod
    async def restore_snapshot(workspace_path: str):
        """Restore workspace from snapshot (instant!)."""
        # Copy .venv/ and node_modules/ from snapshot
        copy_from_r2(f"{SNAPSHOT_PATH}/.venv/", f"{workspace_path}/.venv/")
        copy_from_r2(f"{SNAPSHOT_PATH}/node_modules/", f"{workspace_path}/node_modules/")
        copy_from_r2(f"{SNAPSHOT_PATH}/pyproject.toml", f"{workspace_path}/pyproject.toml")
        copy_from_r2(f"{SNAPSHOT_PATH}/package.json", f"{workspace_path}/package.json")
```

**Benefits:**
- ✅ Workspace ready in ~2-3 seconds (vs ~30 seconds with fresh install)
- ✅ Consistent environment across all threads
- ✅ Versioned snapshots (can rollback if needed)
- ✅ No network dependency after initial snapshot creation

### 3. Incremental Dependency Management

Allow agents to install additional packages on-demand while persisting them:

```python
def install_python_package(self, package: str, persist: bool = True):
    """Install Python package and optionally persist to pyproject.toml."""
    # Install in current workspace
    result = self._run_bwrap_command(f"uv pip install {package}", timeout=60000)
    
    if result.exit_code == 0 and persist:
        # Update pyproject.toml to persist across sessions
        pyproject_path = f"{self._base_path}/pyproject.toml"
        pyproject = toml.load(pyproject_path)
        
        if 'dependencies' not in pyproject['project']:
            pyproject['project']['dependencies'] = []
        
        # Add package if not already there
        if package not in pyproject['project']['dependencies']:
            pyproject['project']['dependencies'].append(package)
            
        # Save updated pyproject.toml
        with open(pyproject_path, 'w') as f:
            toml.dump(pyproject, f)
        
        print(f"✅ {package} installed and persisted")
    
    return result

def install_node_package(self, package: str, persist: bool = True):
    """Install Node package with Bun."""
    # Bun automatically updates package.json
    result = self._run_bwrap_command(f"bun add {package}", timeout=60000)
    return result
```

### 4. Workspace Health Checks

Ensure workspace integrity:

```python
class WorkspaceHealth:
    """Monitors and repairs workspace health."""
    
    @staticmethod
    def check_health(workspace_path: str) -> dict:
        """Check workspace health and return status."""
        health = {
            "configured": False,
            "python_venv": False,
            "python_deps_installed": False,
            "node_modules": False,
            "node_deps_installed": False,
            "disk_usage": 0,
            "issues": []
        }
        
        # Check marker file
        if os.path.exists(f"{workspace_path}/.workspace_configured"):
            health["configured"] = True
        else:
            health["issues"].append("Workspace not configured")
        
        # Check Python environment
        if os.path.exists(f"{workspace_path}/.venv"):
            health["python_venv"] = True
            # Check if dependencies are actually installed
            activate_venv = f"source {workspace_path}/.venv/bin/activate"
            result = run_command(f"{activate_venv} && python -c 'import matplotlib'")
            health["python_deps_installed"] = (result.exit_code == 0)
        else:
            health["issues"].append("Python venv missing")
        
        # Check Node environment
        if os.path.exists(f"{workspace_path}/node_modules"):
            health["node_modules"] = True
            node_count = len(os.listdir(f"{workspace_path}/node_modules"))
            health["node_deps_installed"] = (node_count > 0)
        else:
            health["issues"].append("node_modules missing")
        
        # Check disk usage
        health["disk_usage"] = get_directory_size(workspace_path)
        
        return health
    
    @staticmethod
    def repair_workspace(workspace_path: str, health: dict):
        """Attempt to repair workspace issues."""
        if not health["python_venv"]:
            print("🔧 Repairing Python environment...")
            run_command(f"cd {workspace_path} && uv init --python 3.12")
            
        if not health["python_deps_installed"] and os.path.exists(f"{workspace_path}/pyproject.toml"):
            print("🔧 Reinstalling Python dependencies...")
            run_command(f"cd {workspace_path} && uv pip install -r pyproject.toml")
        
        if not health["node_modules"] and os.path.exists(f"{workspace_path}/package.json"):
            print("🔧 Reinstalling Node dependencies...")
            run_command(f"cd {workspace_path} && bun install")
```

### 5. Workspace Lifecycle Management

```python
class WorkspaceLifecycle:
    """Manages workspace lifecycle: create, suspend, resume, destroy."""
    
    @staticmethod
    async def create_workspace(thread_id: str, template: str = "default"):
        """Create a new workspace from template or snapshot."""
        workspace_path = f"/mnt/r2/{bucket}/threads/{thread_id}"
        
        # Option 1: Fast - Restore from snapshot (~2-3s)
        if ENABLE_SNAPSHOTS:
            await WorkspaceSnapshots.restore_snapshot(workspace_path)
        
        # Option 2: Slow - Fresh install (~30s)
        else:
            template_config = WorkspaceTemplate.load_template(template)
            WorkspaceTemplate.apply_template(workspace_path, template_config)
            
            # Install dependencies
            run_command(f"cd {workspace_path} && uv pip install .")
            run_command(f"cd {workspace_path} && bun install")
        
        # Create configuration marker
        create_marker(f"{workspace_path}/.workspace_configured")
        
        return workspace_path
    
    @staticmethod
    async def suspend_workspace(thread_id: str):
        """Suspend workspace (keep in R2, remove from local cache if any)."""
        # R2 persists automatically, just clean local cache
        pass
    
    @staticmethod
    async def resume_workspace(thread_id: str):
        """Resume workspace (mount from R2)."""
        workspace_path = f"/mnt/r2/{bucket}/threads/{thread_id}"
        
        # Check health
        health = WorkspaceHealth.check_health(workspace_path)
        
        if not health["configured"] or health["issues"]:
            print("⚠️  Workspace issues detected, repairing...")
            WorkspaceHealth.repair_workspace(workspace_path, health)
        
        return workspace_path
    
    @staticmethod
    async def destroy_workspace(thread_id: str):
        """Destroy workspace (archive and delete)."""
        workspace_path = f"/mnt/r2/{bucket}/threads/{thread_id}"
        
        # Optional: Archive before deletion
        archive_path = f"/mnt/r2/{bucket}/archives/{thread_id}-{timestamp}.tar.gz"
        create_archive(workspace_path, archive_path)
        
        # Delete workspace
        shutil.rmtree(workspace_path)
```

### 6. Security Enhancements

#### Network Filtering
```python
# Only allow specific domains
ALLOWED_DOMAINS = [
    "pypi.org", "files.pythonhosted.org",  # Python packages
    "registry.npmjs.org", "bun.sh",         # Node packages
    "github.com", "githubusercontent.com",   # Git repos
]

# Block everything else
DENIED_DOMAINS = ["*"]  # Default deny

# Implement in bwrap or use srt with proper network config
```

#### Resource Limits
```python
RESOURCE_LIMITS = {
    "max_disk_usage": "1GB",       # Per workspace
    "max_memory": "512MB",         # Per command
    "max_cpu_time": "30s",         # Per command
    "max_processes": 10,           # Concurrent processes
    "max_file_descriptors": 100,   # Open files
}
```

## Implementation Phases

### Phase 1: Enhanced Dependency Management ✅ (Current)
- [x] UV for Python (fast package management)
- [x] Bun for Node.js (fast runtime)
- [x] pyproject.toml and package.json
- [x] On-demand package installation

### Phase 2: Template System 🔄 (Implement Next)
- [ ] Create workspace template YAML format
- [ ] Store templates in R2
- [ ] Apply templates during workspace creation
- [ ] Support multiple templates (default, data-science, web-dev)

### Phase 3: Snapshot System 🔄
- [ ] Create snapshot builder
- [ ] Store pre-built snapshots in R2
- [ ] Implement fast restore from snapshots
- [ ] Version snapshots and allow rollback

### Phase 4: Health & Lifecycle 🔄
- [ ] Implement workspace health checks
- [ ] Auto-repair broken workspaces
- [ ] Workspace lifecycle management
- [ ] Monitoring and metrics

### Phase 5: Advanced Security 🔄
- [ ] Network filtering (domain allowlist)
- [ ] Resource limits (disk, memory, CPU)
- [ ] Audit logging
- [ ] Workspace isolation verification

## Benefits

### For Agents
✅ **Reliable**: Consistent environment every time  
✅ **Fast**: 2-3s initialization with snapshots  
✅ **Flexible**: Install packages on-demand  
✅ **Predictable**: Known set of available tools  

### For System
✅ **Secure**: Complete isolation with bwrap  
✅ **Efficient**: Shared snapshots reduce storage  
✅ **Maintainable**: Template-based configuration  
✅ **Observable**: Health checks and monitoring  

### For Users
✅ **Consistent**: Same results every time  
✅ **Fast**: Quick response times  
✅ **Powerful**: Full Python + Node.js stack  
✅ **Safe**: Sandboxed execution  

## Next Steps

1. **Implement Template System** (Priority: High)
   - Create default template with common packages
   - Store in R2
   - Update workspace initialization to use templates

2. **Build Snapshot System** (Priority: Medium)
   - Create snapshot builder job
   - Implement fast restore
   - Set up snapshot versioning

3. **Add Health Monitoring** (Priority: Medium)
   - Implement health checks
   - Add auto-repair logic
   - Create monitoring dashboard

4. **Enhance Security** (Priority: High)
   - Implement network filtering
   - Add resource limits
   - Set up audit logging

## Configuration Example

```python
# config.py
WORKSPACE_CONFIG = {
    "template": "default",  # or "data-science", "web-dev", "minimal"
    "use_snapshots": True,   # Fast initialization
    "auto_repair": True,     # Auto-fix issues
    "isolation": "bwrap",    # or "srt" for more features
    "network": {
        "enabled": True,
        "allowed_domains": [
            "pypi.org", "npmjs.org", "github.com"
        ]
    },
    "resources": {
        "max_disk": "1GB",
        "max_memory": "512MB",
        "max_cpu_time": "30s"
    }
}
```

This design provides a production-ready, secure, and efficient workspace system for AI agents! 🚀

