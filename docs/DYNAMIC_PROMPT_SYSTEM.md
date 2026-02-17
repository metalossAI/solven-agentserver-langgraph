# Sistema de Prompt Dinámico - Solven

## Overview

El sistema de prompt dinámico permite que el agente principal siempre vea la lista completa de skills disponibles al inicio de cada conversación, y cargue skills específicas solo cuando las necesita.

## Arquitectura

```
┌─────────────────────────────────────────────────────────────┐
│                    INICIO DE CADA TURN                       │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│         SkillsPromptMiddleware (dynamic_prompt)             │
│                                                              │
│  1. Carga prompt base desde LangSmith ("solven-main")       │
│  2. Formatea variables estáticas (user, date, context)      │
│  3. Verifica si hay current_skill en el state:              │
│                                                              │
│     ┌───────────────┬──────────────────┐                    │
│     │               │                  │                    │
│     ▼               ▼                  ▼                    │
│  No skill     current_skill      current_skill              │
│  cargado      != None            == None                    │
│     │               │                  │                    │
│     │               ▼                  │                    │
│     │     Carga SKILL.md completo      │                    │
│     │     del skill específico          │                    │
│     │               │                  │                    │
│     └───────────────┴──────────────────┘                    │
│                     │                                        │
│                     ▼                                        │
│     4. Inyecta contenido en sección {skill}                 │
│     5. Retorna prompt completo formateado                   │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│              AGENTE EJECUTA CON PROMPT COMPLETO             │
│                                                              │
│  - Ve instrucciones generales                               │
│  - Ve lista de skills O contenido del skill cargado         │
│  - Puede llamar herramientas (cargar_habilidad, etc.)       │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│         SkillsCleanupMiddleware (awrap_agent_call)          │
│                                                              │
│  1. Agente termina su ejecución                             │
│  2. Limpia current_skill = None                             │
│  3. Retorna respuesta al usuario                            │
└────────────────────────┬────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────┐
│                      FIN DEL TURN                            │
│                                                              │
│  Estado limpio para el próximo turn:                        │
│  - current_skill = None                                     │
│  - En el próximo turn verá lista de skills nuevamente       │
└─────────────────────────────────────────────────────────────┘
```

## Componentes

### 1. `src/agent/prompt.py`

**Función `get_prompt_variables()`**
- Genera las variables estáticas del prompt
- Retorna diccionario con: date, name, language, profile, context_title, context_description

**Función `generate_prompt_template()`**
- Legacy function mantenida por compatibilidad
- Ahora retorna string vacío
- El middleware construye el prompt completo

### 2. `src/agent/middleware.py`

**`SkillsPromptMiddleware` (dynamic_prompt)**

```python
@dynamic_prompt
async def SkillsPromptMiddleware(request: ModelRequest) -> str:
    # 1. Obtener contexto
    backend = request.runtime.context.backend
    user = request.runtime.context.user
    current_skill = getattr(request.state, 'current_skill', None)
    
    # 2. Cargar prompt base desde LangSmith
    client = Client()
    base_prompt = client.pull_prompt("solven-main")
    
    # 3. Generar variables
    prompt_vars = get_prompt_variables(...)
    
    # 4. Generar sección SKILL
    if current_skill:
        skill_section = await backend.load_skill_content(current_skill)
    else:
        skill_section = await generate_skills_list(backend)
    
    # 5. Formatear y retornar prompt completo
    return base_prompt.format(**prompt_vars, skill=skill_section)
```

**Cuándo se ejecuta**: Antes de cada llamada al modelo

**Qué hace**:
1. Carga el prompt base desde LangSmith
2. Formatea variables de usuario, fecha, contexto
3. Genera contenido dinámico para la sección SKILL:
   - Si NO hay skill cargado → Lista de frontmatters de skills disponibles
   - Si hay skill cargado → Contenido completo del SKILL.md
4. Retorna prompt completo ensamblado

**`SkillsCleanupMiddleware` (awrap_agent_call)**

```python
class SkillsCleanupMiddleware(AgentMiddleware[AppContext]):
    async def awrap_agent_call(self, request: ModelRequest, handler):
        # Ejecutar el agente
        response = await handler(request)
        
        # Limpiar después
        if hasattr(request.state, 'current_skill'):
            request.state.current_skill = None
        
        return response
```

**Cuándo se ejecuta**: Después de cada ejecución del agente

**Qué hace**:
1. Espera que el agente termine
2. Limpia `current_skill` del state
3. Retorna la respuesta

**Por qué**: Asegura que en el próximo turn, el agente siempre empiece viendo la lista de skills disponibles

### 3. `src/agent/graph.py`

```python
main_agent = create_deep_agent(
    model=llm,
    system_prompt="",  # ← Vacío, el middleware construye el prompt
    tools=gmail_tools + [cargar_habilidad],
    subagents=[...],
    middleware=[
        SkillsPromptMiddleware,      # ← Construye prompt dinámico
        SkillsCleanupMiddleware(),   # ← Limpia después
    ],
    ...
)
```

## Flujo de Ejemplo

### Turn 1: Usuario solicita escritura

```
Usuario: "Necesito una escritura de compraventa"

┌─ SkillsPromptMiddleware ──────────────────────────┐
│ current_skill = None                               │
│ → Carga prompt base desde LangSmith               │
│ → Formatea variables (user, date, etc.)           │
│ → Genera lista de skills disponibles:             │
│                                                    │
│   SKILLS DISPONIBLES:                             │
│   **ESCRITURAS**                                   │
│      └─ compraventa: Generación de escrituras...  │
│      └─ hipoteca: Escrituras de hipoteca...       │
│   **CONTRATOS**                                    │
│      └─ arrendamiento: Contratos de...            │
│                                                    │
│   Para usar: cargar_habilidad("escrituras/...")   │
│ → Inyecta en {skill}                              │
│ → Retorna prompt completo                         │
└────────────────────────────────────────────────────┘
                     ▼
┌─ Agente procesa ─────────────────────────────────┐
│ Ve lista de skills                                │
│ Decide cargar: cargar_habilidad("escrituras/...") │
│ Tool call registrado                              │
└────────────────────────────────────────────────────┘
                     ▼
┌─ Tool ejecuta ───────────────────────────────────┐
│ backend.load_skill("escrituras/compraventa")      │
│ state.current_skill = "escrituras/compraventa"    │
│ Retorna confirmación                              │
└────────────────────────────────────────────────────┘
                     ▼
┌─ SkillsCleanupMiddleware ────────────────────────┐
│ Agente termina                                    │
│ state.current_skill = None  ← LIMPIA             │
│ Retorna respuesta                                 │
└────────────────────────────────────────────────────┘
```

### Turn 2: Agente pide información

```
Agente: "Perfecto, necesito los siguientes datos..."

┌─ SkillsPromptMiddleware ──────────────────────────┐
│ current_skill = None  ← LIMPIO del turn anterior  │
│ → Carga prompt base                               │
│ → Genera lista de skills NUEVAMENTE              │
│ → El agente ve la lista completa otra vez        │
└────────────────────────────────────────────────────┘
                     ▼
┌─ Agente procesa ─────────────────────────────────┐
│ Ve lista de skills (no el skill específico)       │
│ Si necesita el skill, debe cargarlo otra vez      │
└────────────────────────────────────────────────────┘
```

## Ventajas del Sistema

### 1. **Siempre Fresh Context**
- El agente siempre empieza viendo todas las skills disponibles
- No se queda "atascado" en una skill antigua
- Puede cambiar de skill fácilmente

### 2. **Carga Explícita**
- El agente debe cargar explícitamente la skill que necesita
- Esto es más claro y predecible
- El agente toma decisiones conscientes

### 3. **Prompt Base Centralizado**
- El prompt está en LangSmith, fácil de actualizar
- No hay placeholders complicados
- El middleware maneja toda la lógica

### 4. **Estado Limpio**
- Cada turn empieza limpio
- No hay "memory leaks" de skills cargadas
- Más fácil de debuggear

### 5. **Flexibilidad**
- El agente puede cargar diferentes skills en diferentes turns
- Puede cambiar de tarea sin confusión
- El sistema se adapta automáticamente

## Prompt en LangSmith

El prompt en LangSmith debe:

1. **Incluir el placeholder `{skill}`** donde se inyectará el contenido dinámico
2. **NO definir qué va en `{skill}`** - eso lo hace el middleware
3. **Explicar cómo cargar skills** con `cargar_habilidad()`
4. **Mencionar que las skills se resetean** después de cada respuesta

### Variables del prompt:

```
{date}                       - Generada por get_prompt_variables()
{name}                       - Generada por get_prompt_variables()
{language}                   - Generada por get_prompt_variables()
{profile}                    - Generada por get_prompt_variables()
{initial_context_title}      - Generada por get_prompt_variables()
{initial_context_description}- Generada por get_prompt_variables()
{skill}                      - Generada por SkillsPromptMiddleware
```

## Debugging

### Ver qué skill está cargado

```python
# En el middleware o herramienta
current_skill = getattr(request.state, 'current_skill', None)
print(f"[DEBUG] Current skill: {current_skill}")
```

### Ver el prompt completo que recibe el agente

```python
# En SkillsPromptMiddleware
formatted_prompt = base_prompt.format(...)
print(f"[DEBUG] Prompt length: {len(formatted_prompt)}")
print(f"[DEBUG] Prompt preview: {formatted_prompt[:500]}")
```

### Verificar cleanup

```python
# En SkillsCleanupMiddleware
print(f"[DEBUG] Before cleanup: current_skill={request.state.current_skill}")
request.state.current_skill = None
print(f"[DEBUG] After cleanup: current_skill={request.state.current_skill}")
```

## Testing

### Test 1: Lista de skills inicial

```python
# Crear state sin skill cargado
state = SolvenState(current_skill=None)

# Ejecutar middleware
prompt = await SkillsPromptMiddleware(request)

# Verificar que contiene lista de skills
assert "SKILLS DISPONIBLES" in prompt
assert "cargar_habilidad" in prompt
```

### Test 2: Skill cargado

```python
# Crear state con skill
state = SolvenState(current_skill="escrituras/compraventa")

# Ejecutar middleware
prompt = await SkillsPromptMiddleware(request)

# Verificar que contiene contenido del skill
assert "SKILL CARGADA: escrituras/compraventa" in prompt
assert "Escritura de Compraventa" in prompt
```

### Test 3: Cleanup

```python
# Crear state con skill
state = SolvenState(current_skill="escrituras/compraventa")

# Ejecutar cleanup
await SkillsCleanupMiddleware().awrap_agent_call(request, handler)

# Verificar que se limpió
assert state.current_skill is None
```

## Deployment

1. Sube el prompt a LangSmith con nombre `solven-main`
2. Asegúrate de incluir el placeholder `{skill}`
3. Verifica que todas las variables están presentes
4. Deploy el código con los middlewares actualizados
5. Verifica logs para confirmar que funciona correctamente

## Troubleshooting

### Problema: El agente no ve la lista de skills

**Causa**: El middleware no se está ejecutando o hay un error

**Solución**:
- Verifica que `SkillsPromptMiddleware` está en la lista de middlewares
- Revisa logs para errores en `generate_skills_list()`
- Verifica que el backend tiene acceso a S3

### Problema: El skill no se limpia

**Causa**: `SkillsCleanupMiddleware` no está configurado

**Solución**:
- Verifica que `SkillsCleanupMiddleware()` está en la lista de middlewares
- Asegúrate de que está DESPUÉS de `SkillsPromptMiddleware`
- Revisa que el state tiene el campo `current_skill`

### Problema: Error al formatear el prompt

**Causa**: Falta alguna variable en el prompt de LangSmith

**Solución**:
- Verifica que todas las variables están presentes: {date}, {name}, {language}, {profile}, {initial_context_title}, {initial_context_description}, {skill}
- Revisa que los nombres coinciden exactamente
- Verifica que no hay typos

## Conclusión

Este sistema de prompt dinámico proporciona:
- ✅ Context fresco en cada turn
- ✅ Carga explícita de skills
- ✅ Prompt centralizado en LangSmith
- ✅ Estado limpio y predecible
- ✅ Fácil de mantener y debuggear

