# Two-State Prompt System

## Overview

The main agent now operates with **two distinct prompt states** depending on whether a skill is loaded or not.

## State 1: No Skill Loaded (Initial State)

### Behavior
- Uses the **base prompt** from LangSmith (`solven-main-skills`)
- Injects **frontmatter YAML blocks** from all available skills into the `{skill}` placeholder
- Agent sees a summary of all available skills with their metadata

### Prompt Structure
```
[Base Prompt from LangSmith]
...
SKILL SECTION:
---
name: compraventa
description: Redacción de escrituras de compraventa...
category: escrituras
---
---
name: hipoteca
description: Redacción de escrituras de hipoteca...
category: escrituras
---
...
```

### Purpose
- Agent can see what skills are available
- Agent can decide which skill to load based on user request
- Maintains context about the overall system

## State 2: Skill Loaded

### Behavior
- **COMPLETELY REPLACES** the base prompt
- Uses the **entire SKILL.md file** as the system prompt
- No base prompt, no frontmatter list - just the skill content

### Prompt Structure
```
[Entire SKILL.md content]
---
name: compraventa
description: Redacción de escrituras de compraventa...
---

# Compraventa de Vivienda

## Overview
...

## When to Use
...

## Workflow
...

[All skill content]
```

### Purpose
- Agent becomes **specialized** for that specific task
- Has access to detailed instructions, examples, constraints
- Can access skill resources via `/skills/{skill_name}/`

## Implementation

### Middleware Logic

```python
@dynamic_prompt
async def SkillsPromptMiddleware(request: ModelRequest) -> str:
    backend = request.runtime.context.backend
    current_skill = getattr(request.state, 'current_skill', None)
    
    if current_skill:
        # STATE 2: Return SKILL.md as complete system prompt
        skill_content = await backend.load_skill_content(current_skill)
        return skill_content  # Direct return, no base prompt
    else:
        # STATE 1: Use base prompt with frontmatter
        base_prompt = client.pull_prompt("solven-main-skills")
        frontmatter = await backend.load_skills_frontmatter()
        return base_prompt.format(skill=frontmatter, ...)
```

### State Transitions

```
┌─────────────────────────────────────────┐
│  Initial State: No Skill Loaded         │
│  Prompt: Base + Frontmatter List        │
└─────────────────┬───────────────────────┘
                  │
                  │ cargar_habilidad("escrituras/compraventa")
                  ↓
┌─────────────────────────────────────────┐
│  Skill Loaded: escrituras/compraventa   │
│  Prompt: SKILL.md (Complete)            │
└─────────────────┬───────────────────────┘
                  │
                  │ Agent run completes
                  │ SkillsCleanupMiddleware runs
                  ↓
┌─────────────────────────────────────────┐
│  Reset: No Skill Loaded                 │
│  Prompt: Base + Frontmatter List        │
└─────────────────────────────────────────┘
```

## Benefits

1. **Clear Separation**: Base system knowledge vs. specialized skill knowledge
2. **Full Context**: When a skill is loaded, agent has complete instructions without base prompt interference
3. **Automatic Reset**: Skills are cleared after each turn, forcing explicit loading
4. **Flexible**: Agent can switch between general mode and specialized mode as needed

## Logging

### State 1 Logs
```
[SkillsPromptMiddleware] No skill loaded, injecting frontmatter list into base prompt...
[SkillsMiddleware] Generating skills frontmatter list, user_id=xxx
[S3Backend] load_skills_frontmatter: Found 1 categories
[S3Backend] load_skills_frontmatter: Loaded frontmatter for compraventa
[SkillsPromptMiddleware] ✅ Base prompt generated: 5500 chars total
```

### State 2 Logs
```
[SkillsPromptMiddleware] Skill loaded: escrituras/compraventa, loading FULL skill content...
[SkillsPromptMiddleware] ✅ Skill content loaded (3500 chars) - Using as FULL system prompt
[SkillsCleanupMiddleware] current_skill reset to None.
```

## Testing

To test the two states:

1. **Test State 1** (No skill):
   - Start a new conversation
   - Agent should see frontmatter list
   - Ask: "What skills are available?"

2. **Test State 2** (Skill loaded):
   - Say: "Load the compraventa skill"
   - Agent calls `cargar_habilidad("escrituras/compraventa")`
   - Next message uses SKILL.md as complete prompt
   - Agent should follow skill instructions exactly

3. **Test Reset**:
   - After skill is used, send another message
   - Agent should be back in State 1 with frontmatter list

