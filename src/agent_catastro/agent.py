from deepagents import SubAgent
from src.agent_catastro.tools import busqueda_catastro
from src.llm import LLM as llm
from src.models import SolvenState

subagent = SubAgent(
	name="asistente_busqueda_catastro",
	description="agente para gestionar busquedas en el catastro",
	system_prompt="Eres un asistente de busqueda de datos del catastro de España.",
	model=llm,
	tools=[busqueda_catastro],
	state_schema=SolvenState,
)