import os
from dotenv import load_dotenv
load_dotenv()
 
from deepagents.middleware.subagents import SubAgent
from src.models import SolvenState

from src.llm import LLM as llm
from src.agent_elasticsearch.tools import buscar_documentos


document_search_subagent = SubAgent(
    name="busqueda_documentos",
    description="Asistente capaz de buscar documentos en la base de datos de documentos del usuario.",
    system_prompt="Eres un asistente encargado de realizar busquedas complejas para encontrar referencias/evidencias asi como reconstruir documentos usando las herramientas de busqueda.",
    model=llm,
    tools=[buscar_documentos],
    state_schema=SolvenState,
)
    