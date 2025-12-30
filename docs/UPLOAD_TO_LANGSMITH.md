# Instrucciones para actualizar el prompt en LangSmith

El prompt del agente principal debe actualizarse en LangSmith con el nombre `solven-main`.

## Archivo del prompt

El prompt optimizado para LangSmith está en: `docs/MAIN_AGENT_SYSTEM_PROMPT_LANGSMITH.txt`

## Variables del prompt

El prompt usa las siguientes variables que se inyectan dinámicamente:

### Variables estáticas (inyectadas en generate_prompt_template):
- `{date}` - Fecha y hora actual
- `{name}` - Nombre del usuario
- `{language}` - Idioma preferido del usuario
- `{profile}` - Perfil del usuario (rol, email, etc.)
- `{initial_context_title}` - Título del thread actual
- `{initial_context_description}` - Descripción del thread actual

### Variable dinámica (inyectada por SkillsPromptMiddleware):
- `{skill}` - Contenido dinámico que cambia según el estado:
  - **Sin skill cargado**: Lista de skills disponibles organizadas por categoría con sus descripciones
  - **Con skill cargado**: Contenido completo del SKILL.md del skill activo

**IMPORTANTE**: El placeholder `{skill}` debe estar presente en el prompt de LangSmith. Este se rellena automáticamente por el middleware.

## Cómo subir a LangSmith

1. Ve a LangSmith: https://smith.langchain.com/
2. Navega a la sección de Prompts
3. Busca el prompt `solven-main` o créalo si no existe
4. Copia el contenido de `docs/MAIN_AGENT_SYSTEM_PROMPT_LANGSMITH.txt`
5. Pégalo en el prompt
6. Asegúrate de que las siguientes variables estén correctamente configuradas:
   - `{date}`
   - `{name}`
   - `{language}`
   - `{profile}`
   - `{initial_context_title}`
   - `{initial_context_description}`
   - `{skill}` ⚠️ **CRÍTICO**: Este placeholder debe estar presente
7. Guarda el prompt

## Estructura del sistema de prompts dinámicos

```
Inicio de cada turn
         │
         ▼
SkillsPromptMiddleware  →  1. Carga prompt base desde LangSmith
                          2. Formatea variables estáticas (date, user, etc.)
                          3. Detecta si hay skill cargado
                               ↓
                          ┌────┴────┐
                          │         │
                   Sin skill    Con skill
                          │         │
                          ↓         ↓
            Lista frontmatter     Contenido SKILL.md
            de todas skills       completo del skill
                          │         │
                          └────┬────┘
                               ↓
                    Inyecta en {skill}
                               ↓
                    Prompt completo al modelo
                               ↓
                    Agente ejecuta
                               ↓
SkillsCleanupMiddleware  →  Limpia current_skill = None
                               ↓
                    Estado limpio para próximo turn
```

**IMPORTANTE**: El sistema limpia `current_skill` después de cada turn. Esto significa:
- El agente siempre empieza viendo la lista completa de skills
- Debe cargar explícitamente el skill que necesita con `cargar_habilidad()`
- Las skills no persisten entre turns - esto es intencional

## Documentación adicional

Para entender en profundidad el sistema, consulta:
- `docs/MAIN_AGENT_PROMPT.md` - Documentación completa del sistema
- `docs/SKILLS_SYSTEM.md` - Sistema de habilidades en detalle
- `docs/SKILLS_CORRECTED.md` - Implementación del sistema de skills

## Cambios realizados

El sistema ha sido actualizado para:
1. ✅ Integrar el sistema de skills directamente en el agente principal
2. ✅ Eliminar el subagente de escrituras
3. ✅ Permitir al agente principal cargar skills y redactar documentos directamente
4. ✅ Mantener subagentes solo para herramientas específicas (email, catastro)
5. ✅ Proporcionar un sistema claro de coordinación entre skills y subagentes

## Ejemplo de flujo dinámico

### Escenario 1: Usuario inicia conversación (sin skill cargado)

**Prompt enviado al modelo**:
```
[... instrucciones generales ...]

SKILL
SKILLS DISPONIBLES:

**ESCRITURAS**
   └─ **compraventa** (`escrituras/compraventa`)
      Generación de escrituras públicas de compraventa de bienes inmuebles

   └─ **hipoteca** (`escrituras/hipoteca`)
      Generación de escrituras de constitución de hipoteca

**CONTRATOS**
   └─ **arrendamiento** (`contratos/arrendamiento`)
      Generación de contratos de arrendamiento urbano

Para usar una skill, llama a: cargar_habilidad("categoria/nombre_skill")
Por ejemplo: cargar_habilidad("escrituras/compraventa")

Una vez cargada, las instrucciones completas aparecerán en esta sección.

[... resto del prompt ...]
```

### Escenario 2: Usuario carga skill de compraventa

**Usuario dice**: "Necesito una escritura de compraventa"

**Agente ejecuta**: `cargar_habilidad("escrituras/compraventa")`

**Prompt enviado al modelo (actualizado)**:
```
[... instrucciones generales ...]

SKILL
SKILL CARGADA: escrituras/compraventa

---
name: "Escritura de Compraventa Inmobiliaria"
description: "Generación de escrituras públicas de compraventa..."
category: "escrituras"
version: "2.1"
---

# Escritura de Compraventa Inmobiliaria

## Descripción General
Esta habilidad te permite generar escrituras públicas de compraventa...

## Cuándo Usar Esta Habilidad
- El usuario solicite redactar una escritura de compraventa
- Se necesite formalizar la venta de una propiedad inmobiliaria
[... contenido completo del SKILL.md ...]

Los recursos de esta skill están disponibles en: /skills/compraventa/

[... resto del prompt ...]
```

### Escenario 3: Usuario cambia de tarea

**Usuario dice**: "Ahora necesito un contrato de arrendamiento"

**Agente ejecuta**: `cargar_habilidad("contratos/arrendamiento")`

**Prompt se actualiza con el nuevo skill** automáticamente.

## Verificación

Después de subir el prompt, verifica que:
- El agente puede cargar skills con `cargar_habilidad("categoria/skill")`
- El placeholder `{skill}` se rellena correctamente con la lista o el contenido
- El agente puede ver las skills disponibles sin necesidad de usar `ls()`
- El agente no intenta delegar redacción de documentos a subagentes
- El agente usa skills para tareas especializadas de redacción
- Los subagentes solo se usan para herramientas específicas
- Al cargar un skill, el contenido completo aparece en el contexto del agente

