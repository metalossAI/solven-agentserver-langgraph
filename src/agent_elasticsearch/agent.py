import os
from dotenv import load_dotenv
load_dotenv()
 
from deepagents.middleware.subagents import SubAgent
from src.models import SolvenState

from src.llm import LLM as llm
from src.agent_elasticsearch.tools import buscar_documentos

doc_search_agent = SubAgent(
        name="busqueda_documentos",
        description="Asistente capaz de buscar documentos en la base de datos de documentos del usuario.",
        system_prompt="Eres un asistente encargado de realizar busquedas para encontrar referencias/evidencias en una base de datos de documentos proporcionada por el usuario. Utiliza las herramientas disponibles para realizar consultas precisas y devolver resultados relevantes basados en las solicitudes del usuario.",
        model=llm,
        tools=[buscar_documentos],
        state_schema=SolvenState,
    )
    