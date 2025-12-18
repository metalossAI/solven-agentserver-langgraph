from deepagents import CompiledSubAgent, SubAgent
from src.llm import LLM
from langgraph.runtime import Runtime
from src.agent_escrituras_skilled.middleware import (
    EnsureSkillLoadedMiddleware,
)
from src.models import AppContext
from src.agent_escrituras_skilled.tools import list_skills, load_skill
from src.agent_escrituras_skilled.models import SkillsState

async def generate_escrituras_agent(runtime: Runtime[AppContext]) -> CompiledSubAgent:
    
    system_prompt = """Eres un asistente especializado en redacción de ESCRITURAS con acceso a habilidades dinámicas.

IMPORTANTE: Antes de generar cualquier escritura, DEBES seguir este flujo:

1. Si el usuario pide una escritura específica (compraventa, donación, hipoteca, etc.):
   - Primero llama a 'list_skills' para ver las habilidades de escrituras disponibles
   - Luego llama a 'load_skill' con la ruta apropiada (ej: 'escrituras/compraventa')
   - Solo después de cargar la habilidad, genera la escritura siguiendo sus instrucciones

2. Las habilidades contienen:
   - Flujos de trabajo paso a paso
   - Plantillas y estructura de la escritura
   - Restricciones y validaciones legales
   - Ejemplos de uso

Tu dominio es EXCLUSIVAMENTE escrituras. NO generes otros tipos de documentos legales.
NO intentes generar escrituras sin cargar primero la habilidad apropiada.
Si no estás seguro de qué habilidad cargar, usa 'list_skills' primero.
"""
    
    agent = SubAgent(
        name="redactor_escrituras",
        description="Especialista EXCLUSIVO en redacción de ESCRITURAS notariales"
                    "Usa este agente SOLO cuando el usuario solicite crear, redactar o generar ESCRITURAS. "
                    "El agente cargará automáticamente las habilidades especializadas de escrituras necesarias.",
        model=LLM,
        tools=[list_skills, load_skill],
        system_prompt=system_prompt,
        middleware=[
            EnsureSkillLoadedMiddleware(),  # Force skill loading before document generation
        ],
        state_schema=SkillsState,
        context_schema=AppContext,
    )

    return agent