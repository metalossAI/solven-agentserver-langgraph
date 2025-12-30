# Skill Naming Consistency - Implementation Summary

## Changes Made

### 1. Frontend: `src/app/api/assistants/[assistantId]/config/route.ts`

#### Change 1: Hyphen Convention (Line 289, 775)
```typescript
// BEFORE: const skillName = assistantName.toLowerCase().replace(/\s+/g, '_');
// AFTER:
const skillName = assistantName.toLowerCase().replace(/\s+/g, '-');
```
**Impact**: Folder names now use hyphens (e.g., `compraventa-vivienda`) instead of underscores.

#### Change 2: Pass skillName to Agent (Lines 93-99)
```typescript
async function generateSkillMDWithAgent(params: {
  assistantName: string;
  skillName: string;        // ← NEW PARAMETER
  categoryId: string;
  instructions: string;
  formFields: Record<string, string>;
  resourceFiles: string[];
  userId: string;
})
```

#### Change 3: Explicit Instruction to Agent (Lines 122-125)
```typescript
const contextParts: string[] = [
  `Genera un archivo SKILL.md para el siguiente asistente:\n`,
  `**Nombre del asistente (para mostrar):** ${assistantName}`,
  `**Nombre técnico (para frontmatter):** ${skillName}`,      // ← NEW
  `**Categoría:** ${categoryId}`,
  `\n⚠️ IMPORTANTE: El campo "name" en el frontmatter YAML debe ser exactamente: ${skillName}`,  // ← NEW
];
```

#### Change 4: Pass skillName at Call Site (Line 543)
```typescript
const skillsMarkdown = await generateSkillMDWithAgent({
  assistantName,
  skillName,    // ← NOW PASSED
  categoryId,
  instructions: instructionsForAgent,
  formFields: formFieldsForAgent,
  resourceFiles: resourceFilesList,
  userId: user.id,
});
```

### 2. Backend: Already Correct!

**`src/backend.py`** (Line 1189):
```python
skill_md_path = f"{self.user_id}/skills/{skill_path}/SKILL.md"
```

Where `skill_path = "escrituras/compraventa-vivienda"` results in:
```
user_id/skills/escrituras/compraventa-vivienda/SKILL.md
```

**`src/agent/tools.py`** (Line 23):
```python
backend.load_skill(skill_path)  # "escrituras/compraventa-vivienda"
```

**`src/agent/middleware.py`** (Line 88):
```python
skill_content = await backend.load_skill_content(current_skill)
# current_skill = "escrituras/compraventa-vivienda"
```

## Complete Workflow

### Step 1: User Creates Skill
- User enters: **"Compraventa Vivienda"**
- Frontend generates: `skillName = "compraventa-vivienda"`

### Step 2: S3 Storage
```
b0e6c9dc-fd5f-4f21-ab98-77ac434fbbd6/
  skills/
    escrituras/
      compraventa-vivienda/          ← skillName
        SKILL.md                     ← Contains: name: compraventa-vivienda
        resources/
          file1.docx
```

### Step 3: Agent Generation
LLM receives:
```
**Nombre técnico (para frontmatter):** compraventa-vivienda
⚠️ IMPORTANTE: El campo "name" en el frontmatter YAML debe ser exactamente: compraventa-vivienda
```

Generates:
```yaml
---
name: compraventa-vivienda
description: ...
---
```

### Step 4: Agent Usage
```python
# Agent calls:
cargar_habilidad("escrituras/compraventa-vivienda")

# Backend loads from:
s3://bucket/user_id/skills/escrituras/compraventa-vivienda/SKILL.md

# Agent sees virtual path:
/skills/compraventa-vivienda/
```

### Step 5: Middleware
```python
current_skill = "escrituras/compraventa-vivienda"
skill_content = await backend.load_skill_content(current_skill)
# Returns full SKILL.md as system prompt
```

## Verification

✅ Folder name: `compraventa-vivienda`  
✅ Frontmatter: `name: compraventa-vivienda`  
✅ Virtual path: `/skills/compraventa-vivienda/`  
✅ Load path: `escrituras/compraventa-vivienda`  
✅ S3 path: `user_id/skills/escrituras/compraventa-vivienda/`

## Result

**All paths are now consistent from creation to loading!**

The folder name, frontmatter `name` field, virtual path, and S3 storage path all use the exact same `skillName` value with hyphens.

