# Virtual Filesystem Architecture

## Overview

The S3Backend implements a **virtual filesystem** that abstracts the underlying S3 storage structure. Agents interact with a consistent virtual path structure regardless of how files are actually stored in S3.

## Core Principle

**Agents NEVER see absolute S3 paths - only virtual mount paths.**

This ensures:
- ✅ Consistent agent experience across different storage backends
- ✅ Security through path abstraction
- ✅ Easy migration and refactoring of storage structure
- ✅ Clear separation between physical storage and logical organization

## Virtual Mount Points

The backend provides three virtual mount points:

### 1. `/workspace` - Thread Workspace
- **Virtual Path**: `/workspace/*`
- **S3 Storage**: `threads/{thread_id}/*`
- **Purpose**: Shared workspace for the current conversation thread
- **File Types**: Only `.md` files visible (markdown documents)
- **Example**:
  - Agent sees: `/workspace/notes.md`
  - S3 stores: `threads/abc-123/notes.md`

### 2. `/skills` - User Skills Library
- **Virtual Path**: `/skills/{skill_name}/*`
- **S3 Storage**: `{user_id}/skills/{category}/{skill_name}/*`
- **Purpose**: Access to loaded skill resources and documentation
- **File Types**: ALL files visible (`.md`, `.pdf`, `.docx`, etc.)
- **Loading**: Skills must be explicitly loaded via `cargar_habilidad(skill_path)` tool
- **Example**:
  - Agent sees: `/skills/compraventa/SKILL.md`
  - S3 stores: `user-123/skills/escrituras/compraventa/SKILL.md`
  - Agent sees: `/skills/compraventa/resources/plantilla.docx`
  - S3 stores: `user-123/skills/escrituras/compraventa/resources/plantilla.docx`

### 3. `/ticket` - Ticket Context Files
- **Virtual Path**: `/ticket/*`
- **S3 Storage**: `tickets/{ticket_id}/*`
- **Purpose**: Access to ticket-related context and files
- **File Types**: Only `.md` files visible
- **Example**:
  - Agent sees: `/ticket/requirements.md`
  - S3 stores: `tickets/ticket-456/requirements.md`

## Skills Path Resolution

Skills have a special two-level path structure:

1. **Loading**: Skills are loaded with full path including category
   ```python
   cargar_habilidad("escrituras/compraventa")
   ```

2. **Access**: Skills are accessed via short name (without category)
   ```python
   read("/skills/compraventa/SKILL.md")
   ls("/skills/compraventa/resources")
   ```

3. **Storage**: Files are stored with full category path
   ```
   S3: {user_id}/skills/escrituras/compraventa/SKILL.md
   ```

### Why This Design?

- **Simplicity**: Agents don't need to remember categories when accessing files
- **Uniqueness**: Skill names are unique within a user's library
- **Organization**: Categories help organize skills in storage and UI
- **Flexibility**: Categories can be changed without breaking agent code

## Path Resolution Flow

### Virtual → S3 (via `_resolve_path`)

```
Agent Request: /skills/compraventa/SKILL.md
       ↓
Check loaded_skills for match: "escrituras/compraventa"
       ↓
Resolve to S3: user-123/skills/escrituras/compraventa/SKILL.md
```

### S3 → Virtual (via `_path_from_key`)

```
S3 Key: user-123/skills/escrituras/compraventa/resources/doc.pdf
       ↓
Extract: category=escrituras, skill=compraventa, rest=resources/doc.pdf
       ↓
Check if loaded: "escrituras/compraventa" in loaded_skills?
       ↓
Convert to virtual: /skills/compraventa/resources/doc.pdf
```

## File Visibility Rules

### Workspace & Ticket Mounts
- ✅ Show: `.md` files only
- ❌ Hide: `.editor.json`, `instructions.md`, non-markdown files

### Skills Mount
- ✅ Show: ALL file types (`.md`, `.pdf`, `.docx`, `.txt`, etc.)
- ❌ Hide: `.editor.json`, `instructions.md` (internal config files)

## Implementation Details

### Key Methods

1. **`_resolve_path(virtual_path) -> s3_key`**
   - Converts virtual paths to S3 keys
   - Handles skills path expansion (short name → full path)
   - Used for: read, write, delete operations

2. **`_path_from_key(s3_key) -> virtual_path`**
   - Converts S3 keys back to virtual paths
   - Handles skills path contraction (full path → short name)
   - Used for: ls_info, search results

3. **`load_skill(skill_path)`**
   - Registers a skill as loaded
   - Makes skill accessible via `/skills/{skill_name}/`
   - Called by `cargar_habilidad` tool

### Debug Logging

Both methods include debug logging:
```python
print(f"[S3Backend] _key: path='{path}' -> resolved='{resolved}'")
print(f"[S3Backend] _path_from_key: key='{key}' -> '{virtual_path}'")
```

This helps verify path resolution is working correctly.

## Frontend Integration

### Uploading Files

When uploading skill resources from the frontend:

```typescript
// Frontend uploads to:
const s3Key = `${userId}/skills/${categoryId}/${skillName}/resources/${fileName}`;

// Agent accesses via:
const virtualPath = `/skills/${skillName}/resources/${fileName}`;
```

### Configuration Storage

The `assistantConfigurations` table stores:
- `formFields`: User-provided form data
- `resourceFiles`: Array of uploaded file names (not full paths)

Example:
```json
{
  "formFields": {
    "instructions": "{...skill JSON...}"
  },
  "resourceFiles": [
    "Escritura Compraventa Vivienda.docx",
    "plantilla.pdf"
  ]
}
```

## Testing Path Resolution

Use the test script to verify paths are correctly resolved:

```bash
cd /home/ramon/Github/metaloss/solven-agentserver-langgraph
python test_skills_path.py
```

Expected output:
```
✓ Loaded skill: escrituras/compraventa
✓ Virtual to S3:
  Virtual: /skills/compraventa/SKILL.md
  S3 Key:  user-123/skills/escrituras/compraventa/SKILL.md
✓ S3 to Virtual:
  S3 Key:  user-123/skills/escrituras/compraventa/SKILL.md
  Virtual: /skills/compraventa/SKILL.md
✅ All tests passed!
```

## Common Issues & Solutions

### Issue: Agent sees absolute S3 paths

**Symptom**: Agent receives paths like `/user-123/skills/escrituras/compraventa/SKILL.md`

**Cause**: `_path_from_key` not properly converting S3 keys

**Solution**: Ensure skill is loaded and `_path_from_key` handles the conversion

### Issue: File not found when reading

**Symptom**: `Error: File '/skills/escrituras/compraventa/SKILL.md' not found`

**Cause**: Agent using full path instead of short name

**Solution**: Use `/skills/compraventa/SKILL.md` (without category)

### Issue: Resources not visible in ls

**Symptom**: `ls("/skills/compraventa/resources")` returns empty

**Cause**: File filtering too strict for skills paths

**Solution**: Skills paths show ALL file types (already fixed)

## Best Practices

1. **Always use virtual paths in agent code**
   - ✅ `read("/skills/compraventa/SKILL.md")`
   - ❌ `read("user-123/skills/escrituras/compraventa/SKILL.md")`

2. **Load skills before accessing**
   ```python
   cargar_habilidad("escrituras/compraventa")  # Load first
   read("/skills/compraventa/SKILL.md")        # Then access
   ```

3. **Use short names for skills access**
   - ✅ `/skills/compraventa/`
   - ❌ `/skills/escrituras/compraventa/`

4. **Check debug logs for path issues**
   - Look for `[S3Backend]` log messages
   - Verify virtual ↔ S3 conversions are correct

