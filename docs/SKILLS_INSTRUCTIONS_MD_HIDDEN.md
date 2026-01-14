# Hiding instructions.md from Agents

## Issue

When creating assistants/skills from the frontend (`/app/asistentes`), users provide instructions in a text field. This gets saved as `instructions.md` in the skill directory:

```
{user_id}/skills/{category}/{skill_name}/
├── SKILL.md              # Generated skill file (visible to agent)
├── instructions.md       # User's original instructions (should be hidden)
├── disclosure_*.md       # Progressive disclosure (visible to agent)
└── resources/            # Resources folder (visible to agent)
```

The `instructions.md` file contains the user's raw input used to generate the `SKILL.md`. It should **NOT** be visible to agents because:
- It's internal configuration/reference material
- Agents should only see the structured `SKILL.md` output
- It could confuse the agent with duplicate/unstructured information

## Solution

Added filtering in the S3Backend to hide `instructions.md` files from agents.

### Changes Made

#### 1. Filter from Listings (`ls_info`)

```python
# In src/backend.py, ls_info method
for obj in page.get('Contents', []):
    file_path = self._path_from_key(obj['Key'])
    
    # Only include .md files
    if not file_path.endswith('.md'):
        continue
    
    # Skip instructions.md files (internal configuration, not for agents)
    if file_path.endswith('/instructions.md') or file_path.endswith('instructions.md'):
        continue
    
    result.append({...})
```

#### 2. Block Reading (`read`)

```python
# In src/backend.py, read method
# Block access to instructions.md files (internal configuration)
if file_path.endswith('/instructions.md') or file_path.endswith('instructions.md'):
    return f"Error: File '{file_path}' not found"
```

## How It Works

### Before (Incorrect)
```python
# Agent could see instructions.md
backend.ls("/skills/compraventa")
# Returns: [
#   "/skills/compraventa/SKILL.md",
#   "/skills/compraventa/instructions.md",  # ❌ Visible
#   "/skills/compraventa/resources"
# ]

# Agent could read it
content = backend.read("/skills/compraventa/instructions.md")
# Returns: "..." # ❌ Actual content
```

### After (Correct)
```python
# Agent cannot see instructions.md
backend.ls("/skills/compraventa")
# Returns: [
#   "/skills/compraventa/SKILL.md",
#   # instructions.md is filtered out ✅
#   "/skills/compraventa/resources"
# ]

# Agent cannot read it even if they try
content = backend.read("/skills/compraventa/instructions.md")
# Returns: "Error: File '/skills/compraventa/instructions.md' not found" ✅
```

## File Storage Location

The file is still uploaded to S3 but hidden from agents:

```typescript
// In /api/assistants/[assistantId]/config/route.ts
// Line 407
const textKey = `${basePath}/${fieldId}.md`;
// If fieldId === 'instructions', creates: instructions.md
```

**Why keep it in S3?**
- Auditing: Can review what instructions were used
- Regeneration: Can regenerate SKILL.md if needed
- Reference: Developers can see original user input
- No breaking changes: Existing upload flow unchanged

## Agent Behavior

1. **Discovery**: Agent loads skill via `cargar_habilidad("categoria/skill")`
2. **Exploration**: Agent uses `ls("/skills/skill")` to see available files
3. **Result**: Only sees `SKILL.md`, progressive disclosure files, and resources
4. **Attempted Access**: If agent tries to read `instructions.md`, gets "file not found"

## Implementation Details

### Pattern Matching

Both patterns are checked to handle different path formats:
```python
if file_path.endswith('/instructions.md') or file_path.endswith('instructions.md'):
```

This catches:
- `/skills/compraventa/instructions.md` ✅
- `/skills/categoria/skill/instructions.md` ✅
- `instructions.md` (edge case) ✅

### Return Value

For `read()`, returns the same error as non-existent files:
```python
return f"Error: File '{file_path}' not found"
```

This prevents agents from knowing the file exists but is hidden.

## Testing

### Test Cases

```python
# 1. List skill directory - instructions.md should not appear
files = backend.ls("/skills/compraventa")
assert "instructions.md" not in [f['path'] for f in files]

# 2. Try to read instructions.md - should fail
content = backend.read("/skills/compraventa/instructions.md")
assert "Error: File" in content
assert "not found" in content

# 3. Other .md files should work
content = backend.read("/skills/compraventa/SKILL.md")
assert "Error" not in content  # Should succeed

# 4. Progressive disclosure should work
content = backend.read("/skills/compraventa/disclosure_1.md")
assert "Error" not in content  # Should succeed
```

## What Agents See

```
/skills/compraventa/
├── SKILL.md ✅               # Visible and readable
├── disclosure_clausulas.md ✅ # Visible and readable
├── disclosure_proceso.md ✅   # Visible and readable
└── resources/ ✅              # Visible
    ├── plantilla.pdf ✅       # Visible (but binary, not readable)
    └── ejemplo.md ✅          # Visible and readable

# Hidden from agents:
├── instructions.md ❌         # Filtered from listings and reads
```

## Related Files

- **Backend**: `/home/ramon/Github/metaloss/solven-agentserver-langgraph/src/backend.py`
  - `ls_info()` - Lines filtering instructions.md from directory listings
  - `read()` - Lines blocking read access to instructions.md

- **Frontend Upload**: `/home/ramon/Github/metaloss/solven-app-vercel/src/app/api/assistants/[assistantId]/config/route.ts`
  - Line 407: Where instructions.md is created from text field

## Benefits

1. ✅ **Clean separation**: User instructions vs agent knowledge
2. ✅ **No confusion**: Agent only sees structured SKILL.md
3. ✅ **Security**: Internal config not exposed to agent
4. ✅ **Backward compatible**: Existing uploads still work
5. ✅ **Transparent**: Agent doesn't know file exists
6. ✅ **Auditable**: File still stored for reference

## Future Considerations

If you want to prevent uploading `instructions.md` entirely:

```typescript
// In src/app/api/assistants/[assistantId]/config/route.ts
else if (typeof value === 'string' && value.trim()) {
  // Capture instructions text for skills.md
  if (fieldId === 'instructions') {
    instructionsText = value;
    // Skip uploading - only use for SKILL.md generation
    continue;  // Don't upload instructions.md
  }
  
  // Upload other text fields as .md files
  const textKey = `${basePath}/${fieldId}.md`;
  // ... rest of upload code
}
```

But the current approach (upload but hide) is better for auditability.

## Conclusion

The `instructions.md` file is now completely hidden from agents:
- ❌ Not shown in directory listings
- ❌ Cannot be read even if agent knows the path
- ✅ Still stored in S3 for reference
- ✅ No breaking changes to existing system
























