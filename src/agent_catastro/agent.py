from deepagents import SubAgent
from src.agent_catastro.tools import (
	buscar_inmueble_localizacion, 
	buscar_inmueble_rc,
	obtener_municipios,
	obtener_provincias,
	obtener_numeros_via,
	obtener_vias,
)
from src.llm import LLM as llm
from src.models import SolvenState

subagent = SubAgent(
	name="asistente_busqueda_catastro",
	description="agente para gestionar busquedas en el catastro",
	system_prompt="Eres un asistente de busqueda de datos del catastro de España.",
	model=llm,
	tools=[
		buscar_inmueble_localizacion, 
		buscar_inmueble_rc,
		obtener_municipios,
		obtener_provincias,
		obtener_numeros_via,
		obtener_vias
	],
	state_schema=SolvenState,
)