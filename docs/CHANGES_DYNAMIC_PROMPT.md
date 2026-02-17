# Cambios: Sistema de Prompt Dinámico

**Fecha**: Diciembre 29, 2025  
**Objetivo**: Implementar sistema de prompt completamente dinámico con auto-limpieza de skills

## Resumen

El sistema ahora carga el prompt base completo desde LangSmith y lo enriquece dinámicamente con contenido de skills. Después de cada ejecución del agente, el sistema limpia automáticamente el skill cargado, asegurando que cada turn empiece con la lista completa de skills disponibles.

## Cambios Realizados

### 1. `src/agent/prompt.py` - Simplificado

**Antes**: Cargaba el prompt de LangSmith y lo formateaba con placeholder `{skill}`

**Ahora**: 
- Nueva función `get_prompt_variables()` que solo retorna diccionario con variables
- `generate_prompt_template()` ahora retorna string vacío (legacy compatibility)
- El middleware maneja toda la carga y formateo del prompt

```python
# Antes
def generate_prompt_template(...):
    main_prompt = client.pull_prompt("solven-main")
    return main_prompt.format(..., skill="{skill}")

# Ahora
def get_prompt_variables(...) -> dict:
    return {
        "date": datetime.now()...
        "name": name.capitalize(),
        ...
    }

def generate_prompt_template(...) -> str:
    return ""  # El middleware construye todo
```

### 2. `src/agent/middleware.py` - Completamente Rediseñado

**Nuevo**: `SkillsPromptMiddleware` ahora:
1. Carga el prompt base desde LangSmith
2. Formatea todas las variables (no solo skill)
3. Genera contenido dinámico para la sección SKILL
4. Retorna prompt completo ensamblado

```python
@dynamic_prompt
async def SkillsPromptMiddleware(request: ModelRequest) -> str:
    # Cargar prompt base desde LangSmith
    client = Client()
    base_prompt = client.pull_prompt("solven-main")
    
    # Generar variables
    prompt_vars = get_prompt_variables(...)
    
    # Generar sección SKILL dinámicamente
    if current_skill:
        skill_section = await backend.load_skill_content(current_skill)
    else:
        skill_section = await generate_skills_list(backend)
    
    # Formatear y retornar prompt completo
    return base_prompt.format(**prompt_vars, skill=skill_section)
```

**Nuevo**: `SkillsCleanupMiddleware` - Limpieza automática

```python
class SkillsCleanupMiddleware(AgentMiddleware[AppContext]):
    async def awrap_agent_call(self, request: ModelRequest, handler):
        response = await handler(request)
        
        # Limpiar después de cada ejecución
        if hasattr(request.state, 'current_skill'):
            request.state.current_skill = None
        
        return response
```

### 3. `src/agent/graph.py` - Actualizado

**Cambios**:
- `main_prompt = ""` - Ya no se genera aquí
- Añadido `SkillsCleanupMiddleware()` a la lista de middlewares
- Añadidas `gmail_tools` al agente principal

```python
# Antes
main_prompt = generate_prompt_template(...)
main_agent = create_deep_agent(
    system_prompt=main_prompt,
    tools=[cargar_habilidad],
    middleware=[SkillsPromptMiddleware],
    ...
)

# Ahora
main_prompt = ""  # El middleware lo construye
main_agent = create_deep_agent(
    system_prompt=main_prompt,
    tools=gmail_tools + [cargar_habilidad],
    middleware=[
        SkillsPromptMiddleware,      # Construye prompt
        SkillsCleanupMiddleware(),   # Limpia después
    ],
    ...
)
```

### 4. Prompt de LangSmith - Sin cambios en estructura

El prompt en LangSmith mantiene el placeholder `{skill}`:

```
...
---

{skill}

---

TU OBJETIVO
...
```

**IMPORTANTE**: El placeholder `{skill}` DEBE estar presente para que el middleware lo llene.

## Nuevo Flujo de Ejecución

```
┌─────────────────────────────────────────┐
│         Usuario envía mensaje           │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│   SkillsPromptMiddleware se ejecuta     │
│                                          │
│  1. Carga "solven-main" desde LangSmith │
│  2. Formatea variables estáticas        │
│  3. Si current_skill == None:           │
│     → Lista de skills frontmatter       │
│     Si current_skill != None:           │
│     → Contenido SKILL.md completo       │
│  4. Inyecta en {skill}                  │
│  5. Retorna prompt completo             │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│     Agente ejecuta con prompt full      │
│                                          │
│  - Ve instrucciones generales           │
│  - Ve lista de skills o skill cargado   │
│  - Puede llamar cargar_habilidad()      │
│  - Genera respuesta                     │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│   SkillsCleanupMiddleware se ejecuta    │
│                                          │
│  1. Agente terminó                      │
│  2. current_skill = None                │
│  3. Retorna respuesta                   │
└───────────────┬─────────────────────────┘
                │
                ▼
┌─────────────────────────────────────────┐
│      Usuario recibe respuesta           │
│                                          │
│  Estado para próximo turn:              │
│  - current_skill = None ✓               │
│  - Verá lista de skills nuevamente      │
└─────────────────────────────────────────┘
```

## Ventajas del Nuevo Sistema

### 1. **Fresh Start Cada Turn**
- El agente siempre empieza viendo todas las skills disponibles
- No hay "residuos" de skills cargadas en turns anteriores
- Más predecible y fácil de debuggear

### 2. **Carga Explícita de Skills**
- El agente debe cargar la skill que necesita en cada conversación
- Esto lo hace más consciente de sus acciones
- Evita confusión de qué skill está activa

### 3. **Prompt Centralizado**
- Todo el prompt está en LangSmith
- Fácil de actualizar sin tocar código
- El middleware solo inyecta contenido dinámico

### 4. **Código Más Limpio**
- `prompt.py` es más simple
- La lógica está en el middleware donde corresponde
- Separación clara de responsabilidades

### 5. **Auto-Cleanup**
- No necesitas preocuparte de limpiar el estado
- El sistema se encarga automáticamente
- Menos errores potenciales

## Comportamiento del Agente

### Escenario 1: Primera conversación

```
Turn 1:
Usuario: "Hola, necesito ayuda con una escritura"

Agente ve:
- Prompt completo con instrucciones
- SKILL: Lista de todas las skills disponibles
- Puede decidir cargar la skill apropiada

Agente responde: "Entiendo que necesitas una escritura..."
[carga cargar_habilidad("escrituras/compraventa")]

Turn 2:
Estado inicial: current_skill = None (limpiado automáticamente)
Agente ve: Lista de skills nuevamente
Si necesita la skill otra vez, debe cargarla de nuevo
```

### Escenario 2: Cambio de tarea

```
Turn 1:
Usuario: "Necesito una escritura de compraventa"
Agente: [carga skill compraventa, responde]

Turn 2:
Usuario: "Ahora necesito un contrato de arrendamiento"
Estado: current_skill = None (auto-limpiado)
Agente ve: Lista completa de skills
Agente: [carga skill arrendamiento, responde]

✅ Cambio de skill sin confusión
```

### Escenario 3: Conversación larga con misma skill

```
Turn 1:
Usuario: "Necesito escritura de compraventa"
Agente: [carga skill, pide datos]

Turn 2:
Usuario: "El vendedor es Juan Pérez..."
Estado: current_skill = None (limpiado)
Agente: [debe cargar skill otra vez si la necesita]

Turn 3:
Usuario: "El precio es 200,000€"
Estado: current_skill = None
Agente: [carga skill, genera documento]

⚠️ Nota: El agente debe cargar la skill en cada turn que la necesite
Esto es intencional - mantiene el context fresh
```

## Testing

### Test Manual 1: Verificar limpieza

1. Hacer request que cargue una skill
2. Verificar en logs: `current_skill = "escrituras/compraventa"`
3. Esperar respuesta del agente
4. Verificar en logs: `current_skill = None` (después de cleanup)
5. Hacer otro request
6. Verificar que el agente ve lista de skills

### Test Manual 2: Verificar prompt completo

1. Añadir logging en `SkillsPromptMiddleware`:
   ```python
   formatted_prompt = base_prompt.format(...)
   print(f"[DEBUG] Prompt length: {len(formatted_prompt)}")
   print(f"[DEBUG] Has skills list: {'SKILLS DISPONIBLES' in formatted_prompt}")
   ```
2. Hacer request sin skill cargada
3. Verificar logs muestran prompt con lista de skills

### Test Manual 3: Cambio de skill

1. Request: "Necesito una escritura"
2. Verificar agente carga skill escrituras
3. Request: "Ahora un contrato"
4. Verificar agente puede cambiar a skill contratos sin problemas

## Deployment Checklist

- [x] Código actualizado
  - [x] `src/agent/prompt.py` - Simplificado
  - [x] `src/agent/middleware.py` - Rediseñado
  - [x] `src/agent/graph.py` - Actualizado
- [x] Documentación
  - [x] `DYNAMIC_PROMPT_SYSTEM.md` - Sistema explicado
  - [x] `CHANGES_DYNAMIC_PROMPT.md` - Este documento
  - [x] `UPLOAD_TO_LANGSMITH.md` - Actualizado
- [ ] Prompt en LangSmith
  - [ ] Subir `MAIN_AGENT_SYSTEM_PROMPT_LANGSMITH.txt`
  - [ ] Verificar placeholder `{skill}` presente
  - [ ] Verificar todas las variables
- [ ] Testing
  - [ ] Test de limpieza automática
  - [ ] Test de carga de skills
  - [ ] Test de cambio de skills
  - [ ] Test de conversación larga

## Rollback Plan

Si hay problemas, el rollback es simple:

1. Revertir `src/agent/prompt.py` a versión anterior
2. Revertir `src/agent/middleware.py` a versión anterior
3. Revertir `src/agent/graph.py` para usar prompt antiguo
4. El prompt en LangSmith puede quedarse igual (es compatible)

## Próximos Pasos

1. **Subir prompt a LangSmith** usando `MAIN_AGENT_SYSTEM_PROMPT_LANGSMITH.txt`
2. **Deploy código** a entorno de staging
3. **Probar manualmente** los escenarios descritos arriba
4. **Monitorear logs** para verificar limpieza automática
5. **Deploy a producción** después de validación

## Notas Adicionales

### Sobre la persistencia de skills

**¿Por qué limpiar el skill después de cada turn?**

1. **Claridad**: El agente siempre sabe qué tiene disponible
2. **Flexibilidad**: Puede cambiar de skill sin confusión
3. **Debugging**: Es más fácil entender el estado del sistema
4. **Fresh context**: El agente no se queda "atascado" en una skill

**¿No es ineficiente cargar la skill cada vez?**

- La carga es rápida (solo lectura de S3)
- El beneficio en claridad supera el pequeño overhead
- El agente solo carga la skill cuando realmente la necesita
- En conversaciones cortas, se carga 1-2 veces máximo

### Sobre el prompt vacío en graph.py

`main_prompt = ""` no significa que el agente no tenga prompt. El middleware `SkillsPromptMiddleware` construye el prompt completo antes de cada llamada al modelo. Este approach:

- Mantiene la lógica del prompt en el middleware
- Facilita debugging (puedes logguear el prompt completo)
- Permite cambios sin modificar graph.py

## Conclusión

Este sistema proporciona un flujo más limpio y predecible:
- ✅ Prompt completamente dinámico
- ✅ Auto-limpieza de estado
- ✅ Fresh context en cada turn
- ✅ Fácil de mantener y debuggear
- ✅ Sin placeholders complejos
- ✅ Prompt centralizado en LangSmith

El agente ahora es más consciente de sus capacidades y debe tomar decisiones explícitas sobre qué skills cargar, lo que resulta en un comportamiento más predecible y confiable.

