# Skills System Architecture

## Overview

The Skills system provides agents with specialized knowledge and resources organized in a structured, reusable format. Skills use **progressive disclosure** - starting with a high-level overview and allowing agents to access deeper detail as needed.

## Storage Structure

Skills are stored per-user in S3 with the following hierarchy:

```
S3 Bucket: {bucket_name}
└── {user_id}/
    └── skills/
        ├── categoria_1/
        │   ├── skill_a/
        │   │   ├── SKILL.md              # Main skill description (loaded into prompt)
        │   │   ├── disclosure_1.md       # Progressive disclosure files
        │   │   ├── disclosure_2.md
        │   │   └── resources/
        │   │       ├── plantilla.pdf     # Resource files (templates, examples)
        │   │       ├── ejemplo.md
        │   │       └── schema.json
        │   └── skill_b/
        │       └── SKILL.md
        └── categoria_2/
            └── skill_c/
                └── SKILL.md
```

## Virtual Mount Points

The S3Backend provides three virtual mount points for agents:

| Mount Point | S3 Location | Purpose |
|-------------|-------------|---------|
| `/workspace` | `threads/{thread_id}/` | Thread-specific workspace (read/write) |
| `/ticket` | `tickets/{ticket_id}/` | Ticket context files (if ticket exists) |
| `/skills` | `{user_id}/skills/` | User's skills library (read-only) |

## How Skills Work

### 1. Skill Discovery

Agents can list available skills using the backend:

```python
# List all categories
categories = await backend.ls("/skills")
# Returns: ['/skills/escrituras', '/skills/contratos', ...]

# List skills in a category
skills = await backend.ls("/skills/escrituras")
# Returns: ['/skills/escrituras/compraventa', '/skills/escrituras/hipoteca', ...]
```

Or use specialized tools:

```python
# Get formatted list of skills in a category
skills_info = await backend.load_all_skills_formatted(category="escrituras")
# Returns formatted string with skill names and descriptions
```

### 2. Loading a Skill

When an agent loads a skill, the middleware:

1. Sets `current_skill` in the state (e.g., `"escrituras/compraventa"`)
2. Loads the skill's `SKILL.md` content
3. Injects it into the system prompt

Example using the `cargar_habilidad` tool:

```python
@tool
async def cargar_habilidad(runtime: ToolRuntime[AppContext], skill_path: str):
    """Load a skill (e.g., 'escrituras/compraventa')"""
    return Command(update={"current_skill": skill_path})
```

The middleware then loads the skill:

```python
@dynamic_prompt
async def SkillsPromptMiddleware(request: ModelRequest) -> str:
    current_skill = getattr(request.state, 'current_skill', None)
    backend = request.runtime.context.backend
    
    if current_skill:
        # Load the specific skill's SKILL.md
        content = await backend.load_skill_content(current_skill)
        return f"---\n{content}\n---"
    
    # Otherwise, load all skills frontmatter
    skills = await backend.load_skills_frontmatter(category="escrituras")
    return prompt.format(skills=skills)
```

### 3. Accessing Skill Resources

Once a skill is loaded, the agent can access its resources using full paths:

```python
# Read the main skill file
skill_content = backend.read("/skills/escrituras/compraventa/SKILL.md")

# Read progressive disclosure files
details = backend.read("/skills/escrituras/compraventa/disclosure_1.md")

# Check if a resource exists
has_template = backend.exists("/skills/escrituras/compraventa/resources/plantilla.pdf")

# List resources in a skill
resources = backend.ls("/skills/escrituras/compraventa/resources")
```

### 4. SKILL.md Format

Each `SKILL.md` file should follow this structure:

```markdown
---
name: "Escritura de Compraventa"
description: "Generación de escrituras de compraventa inmobiliaria"
category: "escrituras"
version: "1.0"
---

# Escritura de Compraventa

## Descripción
[High-level description of what this skill does]

## Cuándo Usar Esta Habilidad
- When the user requests X
- For tasks involving Y

## Recursos Disponibles
- `/skills/escrituras/compraventa/resources/plantilla.pdf` - Template base
- `/skills/escrituras/compraventa/disclosure_clausulas.md` - Detailed clauses

## Proceso
1. [Step 1]
2. For detailed information, read: `/skills/escrituras/compraventa/disclosure_proceso.md`
3. [Step 3]

## Validaciones
[Important validations to perform]

## Ejemplos
See: `/skills/escrituras/compraventa/resources/ejemplo.md`
```

## File Access Patterns

### Reading Markdown Files

```python
# From workspace
content = backend.read("/workspace/document.md")

# From skills (markdown)
skill = backend.read("/skills/escrituras/compraventa/SKILL.md")
disclosure = backend.read("/skills/escrituras/compraventa/disclosure_1.md")

# Auto-adds .md extension if not present
content = backend.read("/workspace/notes")  # Reads notes.md
```

### Reading Non-Markdown Files in Skills

```python
# Skills paths automatically allow non-markdown files
# But binary files (PDFs) cannot be read as text
result = backend.read("/skills/escrituras/compraventa/resources/plantilla.pdf")
# Returns: "Error: File is a binary file and cannot be read as text..."

# Instead, check existence and reference the path
if backend.exists("/skills/escrituras/compraventa/resources/plantilla.pdf"):
    # Tell user about the template location
    response = "There's a template at /skills/escrituras/compraventa/resources/plantilla.pdf"
```

### Listing Files

```python
# List workspace files
files = backend.ls("/workspace")

# List skill categories
categories = backend.ls("/skills")

# List skills in a category
skills = backend.ls("/skills/escrituras")

# List resources in a skill
resources = backend.ls("/skills/escrituras/compraventa/resources")
```

## Backend Methods for Skills

### `load_skills(category: str = "all") -> list[str]`
Returns list of skill paths like `["escrituras/compraventa", "escrituras/hipoteca"]`

### `load_skills_frontmatter(category: Optional[str] = None) -> str`
Returns concatenated YAML frontmatter from all skills in a category.

### `load_all_skills_formatted(category: Optional[str] = None) -> str`
Returns a formatted string with skill names and descriptions, organized by category.

### `load_skill_content(skill_path: str) -> Optional[str]`
Loads the complete `SKILL.md` content for a specific skill.

## Agent Workflow Example

1. **User Request**: "Necesito una escritura de compraventa"

2. **Agent Discovery**:
   ```python
   # Middleware automatically loads skills frontmatter
   # Agent sees available skills in its prompt
   ```

3. **Load Specific Skill**:
   ```python
   # Agent calls: cargar_habilidad("escrituras/compraventa")
   # Middleware injects SKILL.md into prompt
   ```

4. **Access Resources**:
   ```python
   # Read progressive disclosure
   details = backend.read("/skills/escrituras/compraventa/disclosure_clausulas.md")
   
   # Check for templates
   if backend.exists("/skills/escrituras/compraventa/resources/plantilla.pdf"):
       # Reference the template in response
   ```

5. **Use Skill Knowledge**:
   - Follow the process outlined in SKILL.md
   - Apply validations
   - Reference examples and resources

## Best Practices

### For Skill Authors

1. **Keep SKILL.md Concise**: High-level overview only
2. **Use Progressive Disclosure**: Detailed info in separate files
3. **Document Resources**: List all available resources with descriptions
4. **Provide Examples**: Include example files in resources/
5. **Version Skills**: Update version in frontmatter when making changes

### For Agents

1. **Load Before Using**: Always load a skill before using its knowledge
2. **Reference Resources**: Tell users about available templates and examples
3. **Follow the Process**: Adhere to the workflow defined in SKILL.md
4. **Progressive Loading**: Only load disclosure files when detailed info is needed
5. **Check Existence**: Verify resources exist before referencing them

## Path Resolution Examples

| Agent Path | Resolves To | Purpose |
|------------|-------------|---------|
| `/workspace/notes.md` | `threads/{thread_id}/notes.md` | Thread workspace file |
| `/ticket/requirements.md` | `tickets/{ticket_id}/requirements.md` | Ticket context file |
| `/skills/escrituras/compraventa/SKILL.md` | `{user_id}/skills/escrituras/compraventa/SKILL.md` | Main skill file |
| `/skills/escrituras/compraventa/disclosure_1.md` | `{user_id}/skills/escrituras/compraventa/disclosure_1.md` | Progressive disclosure |
| `/skills/escrituras/compraventa/resources/template.pdf` | `{user_id}/skills/escrituras/compraventa/resources/template.pdf` | Skill resource |

## Implementation Details

### Mount Initialization (backend.py)

```python
# Mount points are set up during S3Backend initialization
if ticket_id:
    self.mounts["/ticket"] = f"tickets/{ticket_id}"

if thread_id:
    self.mounts["/workspace"] = f"threads/{thread_id}"

if user_id:
    self.mounts["/skills"] = f"{user_id}/skills"
```

### Path Resolution

```python
def _resolve_path(self, path: str) -> str:
    """Resolve virtual path to actual S3 key"""
    for mount, s3_prefix in self.mounts.items():
        if path.startswith(mount):
            relative = path[len(mount):].lstrip("/")
            return f"{s3_prefix}/{relative}" if relative else s3_prefix
    # Fallback to workspace or legacy behavior
    ...
```

### Middleware Integration

```python
@dynamic_prompt
async def SkillsPromptMiddleware(request: ModelRequest) -> str:
    current_skill = getattr(request.state, 'current_skill', None)
    backend = request.runtime.context.backend
    
    if current_skill:
        # Load specific skill content
        content = await backend.load_skill_content(current_skill)
        return f"---\n{content}\n---"
    
    # Load all skills frontmatter for discovery
    skills = await backend.load_skills_frontmatter(category="escrituras")
    return base_prompt.format(skills=skills)
```

## Troubleshooting

### "File not found" errors
- Verify the skill exists: `backend.exists("/skills/category/skill/SKILL.md")`
- Check the path format: must start with `/skills/`
- Ensure user_id is set in backend initialization

### "Binary file cannot be read" errors
- This is expected for PDFs, images, etc.
- Use `exists()` to verify the file is there
- Reference the path in responses to users
- Consider adding markdown summaries of binary files

### Skill not loading in prompt
- Verify `current_skill` is set in state
- Check middleware is properly registered
- Ensure `load_skill_content()` returns content

### Resources not accessible
- Verify mount points: check `backend.mounts`
- Test path resolution: add debug logging to `_resolve_path()`
- Check S3 permissions and bucket configuration

## Future Enhancements

1. **Skill Versioning**: Support multiple versions of the same skill
2. **Skill Dependencies**: Allow skills to reference other skills
3. **Binary Processing**: Add PDF-to-markdown conversion for resources
4. **Skill Analytics**: Track which skills are used most frequently
5. **Skill Templates**: Provide templates for creating new skills
6. **Cross-User Skills**: Shared skill libraries at the company level

































