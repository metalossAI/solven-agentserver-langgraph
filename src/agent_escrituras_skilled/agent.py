from deepagents import CompiledSubAgent, SubAgent
from src.llm import LLM
from langgraph.runtime import Runtime
from src.agent_escrituras_skilled.middleware import (
    inject_skill_content,
)
from src.models import AppContext
from src.agent_escrituras_skilled.tools import list_skills, load_skill
from src.agent_escrituras_skilled.models import SkillsState

async def generate_escrituras_agent(runtime: Runtime[AppContext]) -> CompiledSubAgent:
    
    system_prompt = """Eres un asistente especializado en documentos legales con acceso a habilidades dinámicas.

IMPORTANTE: Antes de generar cualquier documento legal, DEBES seguir este flujo:

1. Si el usuario pide un documento específico (escritura, contrato, acta, etc.):
   - Primero llama a 'list_skills' para ver las habilidades disponibles
   - Luego llama a 'load_skill' con la ruta apropiada (ej: 'escrituras/compraventa')
   - Solo después de cargar la habilidad, genera el documento siguiendo sus instrucciones

2. Las habilidades contienen:
   - Flujos de trabajo paso a paso
   - Plantillas y estructura del documento
   - Restricciones y validaciones
   - Ejemplos de uso

NO intentes generar documentos legales sin cargar primero la habilidad apropiada.
Si no estás seguro de qué habilidad cargar, usa 'list_skills' primero.
"""
    
    agent = SubAgent(
        name="redactor_escrituras",
        description="Especialista en redacción de documentos legales (escrituras, contratos, actas). "
                    "Usa este agente cuando el usuario solicite crear, redactar o generar documentos legales. "
                    "El agente cargará automáticamente las habilidades especializadas necesarias.",
        model=LLM,
        tools=[list_skills, load_skill],
        system_prompt=system_prompt,
        middleware=[
            inject_skill_content,
        ],
        state_schema=SkillsState,
        context_schema=AppContext,
    )

    return agent