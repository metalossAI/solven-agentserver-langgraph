# Skills System - Corrected Implementation

## Overview

The skills system has been corrected to work within the constraints of the `BackendProtocol` tool interface. The key change: **`/skills` only shows loaded skills**, not the entire skills library.

## Key Principle

The agent only has access to these tools from `BackendProtocol`:
- `ls_info(path)` - List files and directories
- `read(file_path, offset, limit)` - Read file content
- `grep_raw(pattern, path, glob)` - Search for patterns
- `glob_info(pattern, path)` - Find files matching glob pattern
- `write(file_path, content)` - Write new files
- `edit(file_path, old_string, new_string, replace_all)` - Edit existing files

**There is NO `exists()` tool** - agents must use `ls` to discover what's available.

## How It Works

### 1. Initial State (No Skills Loaded)

When the backend is first created:
```python
backend.loaded_skills = set()  # Empty - no skills loaded yet
```

Agent listing root:
```python
backend.ls("/")
# Returns: ["/workspace", "/ticket"]
# Note: /skills is NOT shown because no skills are loaded
```

### 2. Loading a Skill

When the agent calls `cargar_habilidad("escrituras/compraventa")`:

```python
@tool
async def cargar_habilidad(runtime: ToolRuntime[AppContext], skill_path: str):
    backend = runtime.context.backend
    backend.load_skill(skill_path)  # Registers the skill
    return Command(update={"current_skill": skill_path})
```

This does two things:
1. **Registers skill with backend**: `backend.loaded_skills.add("escrituras/compraventa")`
2. **Updates state**: `current_skill = "escrituras/compraventa"`

The middleware then loads `SKILL.md` into the prompt.

### 3. After Loading (Skills Visible)

Now the agent can see and access the skill:

```python
backend.ls("/")
# Returns: ["/workspace", "/ticket", "/skills"]
# Note: /skills NOW appears because a skill is loaded

backend.ls("/skills")
# Returns: ["/skills/compraventa"]
# Note: Only shows "compraventa" (the loaded skill)

backend.ls("/skills/compraventa")
# Returns: [
#   "/skills/compraventa/SKILL.md",
#   "/skills/compraventa/disclosure_clausulas.md",
#   "/skills/compraventa/resources"
# ]

backend.ls("/skills/compraventa/resources")
# Returns: [
#   "/skills/compraventa/resources/plantilla.pdf",
#   "/skills/compraventa/resources/ejemplo.md"
# ]

backend.read("/skills/compraventa/SKILL.md")
# Returns the skill content (same as what middleware loaded)

backend.read("/skills/compraventa/disclosure_clausulas.md")
# Returns progressive disclosure content

backend.read("/skills/compraventa/resources/ejemplo.md")
# Returns example content
```

### 4. Path Resolution

When the agent accesses `/skills/compraventa/file.md`:

1. Backend sees path starts with `/skills/`
2. Extracts skill name: `compraventa`
3. Checks `loaded_skills` for a match: finds `escrituras/compraventa`
4. Resolves to S3: `{user_id}/skills/escrituras/compraventa/file.md`
5. Reads from S3 and returns content

If the skill is NOT loaded:
```python
backend.read("/skills/other_skill/file.md")
# Resolves to: {user_id}/skills/__not_loaded__/other_skill/file.md
# Returns: "Error: File not found"
```

## Storage Structure (Unchanged)

Skills are still stored in S3 at:
```
{user_id}/skills/
├── escrituras/
│   ├── compraventa/
│   │   ├── SKILL.md
│   │   ├── disclosure_clausulas.md
│   │   └── resources/
│   │       ├── plantilla.pdf
│   │       └── ejemplo.md
│   └── hipoteca/
│       └── SKILL.md
└── contratos/
    └── arrendamiento/
        └── SKILL.md
```

But the agent accesses them via:
```
/skills/compraventa/SKILL.md
/skills/compraventa/resources/plantilla.pdf
```

## Agent Workflow

### Discovery

The agent CANNOT browse all skills. It must use the middleware which lists available skills:

```python
@dynamic_prompt
async def SkillsPromptMiddleware(request: ModelRequest) -> str:
    current_skill = getattr(request.state, 'current_skill', None)
    
    if not current_skill:
        # Load skills frontmatter for discovery
        frontmatter = await backend.load_skills_frontmatter(category="escrituras")
        # Returns: YAML frontmatter from all skills in category
        # Agent sees: name, description, category for each skill
```

The middleware (not the agent) calls `load_skills_frontmatter()` which directly accesses S3 to list all skills. This info is injected into the prompt, so the agent knows what skills exist.

### Loading

Once the agent knows a skill exists (from the injected info), it loads it:

```python
# Agent decides to use "compraventa" skill
await cargar_habilidad("escrituras/compraventa")

# Now the skill is:
# 1. Registered in backend.loaded_skills
# 2. Set in state as current_skill
# 3. SKILL.md content injected into prompt by middleware
# 4. Visible in /skills/ directory
```

### Accessing Resources

After loading, the agent can explore and read:

```python
# List what's in the skill
files = backend.ls("/skills/compraventa")

# Read progressive disclosure
details = backend.read("/skills/compraventa/disclosure_clausulas.md")

# List resources
resources = backend.ls("/skills/compraventa/resources")

# Read resource (if text)
example = backend.read("/skills/compraventa/resources/ejemplo.md")

# Try to read binary (graceful error)
pdf = backend.read("/skills/compraventa/resources/plantilla.pdf")
# Returns: "Error: File is a binary file and cannot be read as text..."
# But the agent now knows the file exists at that path
```

## Complete Example

```python
# 1. Agent starts - no skills loaded
backend.ls("/")  
# → ["/workspace", "/ticket"]

# 2. Middleware injects skills frontmatter
# Agent sees available skills in prompt, including "compraventa"

# 3. Agent decides to load compraventa
await cargar_habilidad("escrituras/compraventa")
# → backend.loaded_skills = {"escrituras/compraventa"}
# → state.current_skill = "escrituras/compraventa"  
# → Middleware injects SKILL.md into prompt

# 4. Agent can now access the skill
backend.ls("/")
# → ["/workspace", "/ticket", "/skills"]

backend.ls("/skills")
# → ["/skills/compraventa"]

backend.ls("/skills/compraventa")
# → ["/skills/compraventa/SKILL.md", 
#     "/skills/compraventa/disclosure_clausulas.md",
#     "/skills/compraventa/resources"]

# 5. Agent explores resources
backend.ls("/skills/compraventa/resources")
# → ["/skills/compraventa/resources/plantilla.pdf",
#     "/skills/compraventa/resources/ejemplo.md"]

# 6. Agent reads progressive disclosure
details = backend.read("/skills/compraventa/disclosure_clausulas.md")
# → Returns full content

# 7. Agent reads example
example = backend.read("/skills/compraventa/resources/ejemplo.md")
# → Returns example content

# 8. Agent tries to read PDF
pdf = backend.read("/skills/compraventa/resources/plantilla.pdf")
# → Returns: "Error: File is a binary file..."
# But agent now knows there's a template at that path
```

## Benefits of This Approach

### 1. Security & Performance
- ✅ Agent only sees skills that have been explicitly loaded
- ✅ Prevents accidental access to irrelevant skills
- ✅ Reduces cognitive load - agent doesn't see hundreds of skills

### 2. Clean Tool Interface
- ✅ Works within standard BackendProtocol tools
- ✅ No custom tools needed
- ✅ Agent uses familiar `ls` and `read` operations

### 3. Progressive Loading
- ✅ Skills are loaded on-demand
- ✅ Only loaded skills consume context/memory
- ✅ Multiple skills can be loaded if needed

### 4. Clear Mental Model
- ✅ Empty `/skills` = no skills loaded yet
- ✅ `/skills/skill_name` appears after loading
- ✅ Agent discovers by listing, not by guessing paths

## Comparison: Before vs After

### Before (Incorrect)
```python
# Agent could list ALL skills at any time
backend.ls("/skills")
# → ["/skills/escrituras", "/skills/contratos", ...]

backend.ls("/skills/escrituras")
# → ["/skills/escrituras/compraventa", "/skills/escrituras/hipoteca", ...]

# Agent could access ANY skill without loading it
backend.read("/skills/escrituras/random_skill/SKILL.md")
# → Would work even if skill not loaded
```

**Problems:**
- Violates separation of concerns (middleware handles discovery)
- Agent could access unloaded skills
- Exposed entire skills library

### After (Correct)
```python
# Agent sees NOTHING initially
backend.ls("/")
# → ["/workspace", "/ticket"]
# No /skills directory

# After loading a skill
await cargar_habilidad("escrituras/compraventa")

backend.ls("/")
# → ["/workspace", "/ticket", "/skills"]

backend.ls("/skills")
# → ["/skills/compraventa"]  # ONLY the loaded skill

# Agent can only access loaded skills
backend.read("/skills/compraventa/SKILL.md")  # ✅ Works

backend.read("/skills/hipoteca/SKILL.md")  # ❌ Fails - not loaded
# → "Error: File not found"
```

**Benefits:**
- Clear separation: middleware = discovery, agent = usage
- Agent only sees what it has explicitly loaded
- Prevents unintended access

## Technical Implementation

### Backend State
```python
class S3Backend:
    def __init__(self, ...):
        self.loaded_skills: set[str] = set()  # Tracks loaded skills
        # No permanent /skills mount
        
    def load_skill(self, skill_path: str):
        """Register skill as loaded"""
        self.loaded_skills.add(skill_path)
    
    def _resolve_path(self, path: str) -> str:
        """Resolve /skills paths to actual S3 keys"""
        if path.startswith("/skills/"):
            skill_name = path[8:].split('/')[0]
            # Find matching skill in loaded_skills
            for loaded_skill in self.loaded_skills:
                if loaded_skill.endswith(skill_name):
                    return f"{self.user_id}/skills/{loaded_skill}/..."
            # Not loaded → return invalid path
            return f"{self.user_id}/skills/__not_loaded__/..."
    
    def ls_info(self, path: str) -> list[FileInfo]:
        """List files/directories"""
        if path == "/":
            # Add /skills only if skills are loaded
            if self.loaded_skills:
                result.append({'/skills': is_dir=True})
        
        if path == "/skills":
            # Show only loaded skills
            for skill_path in self.loaded_skills:
                skill_name = skill_path.split('/')[-1]
                result.append({f'/skills/{skill_name}': is_dir=True})
```

### Tool
```python
@tool
async def cargar_habilidad(runtime, skill_path: str):
    backend = runtime.context.backend
    backend.load_skill(skill_path)  # Register with backend
    return Command(update={"current_skill": skill_path})  # Update state
```

### Middleware
```python
@dynamic_prompt
async def SkillsPromptMiddleware(request):
    current_skill = request.state.current_skill
    backend = request.runtime.context.backend
    
    if current_skill:
        # Load SKILL.md content
        content = await backend.load_skill_content(current_skill)
        return content
    else:
        # Load skills frontmatter for discovery
        return await backend.load_skills_frontmatter(category="escrituras")
```

## Migration from Previous Version

If you have code using the old approach:

### Old (Won't Work)
```python
# Agent trying to browse all skills
backend.ls("/skills")  # Would show all categories

# Agent trying to check if file exists
backend.exists("/skills/escrituras/compraventa/resource.pdf")  # No such method
```

### New (Correct)
```python
# Agent loads skill first
await cargar_habilidad("escrituras/compraventa")

# Then browses loaded skill
backend.ls("/skills")  # Shows only "compraventa"
backend.ls("/skills/compraventa/resources")  # Shows resources

# Check if file exists by listing
resources = backend.ls("/skills/compraventa/resources")
has_template = any("plantilla.pdf" in r['path'] for r in resources)
```

## Conclusion

The corrected implementation:
- ✅ Works within BackendProtocol constraints
- ✅ Only exposes loaded skills to agents
- ✅ Uses standard `ls` and `read` tools
- ✅ Maintains clean separation between discovery (middleware) and usage (agent)
- ✅ Provides intuitive mental model for agents

Agents now follow this pattern:
1. See available skills (via middleware injection)
2. Load a skill explicitly (`cargar_habilidad`)
3. Explore skill resources (`ls`)
4. Read skill content (`read`)


















































