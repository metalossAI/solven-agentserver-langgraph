from langchain_core.tools import tool, InjectedToolArg
from langchain_core.messages import ToolMessage
from langchain.tools import ToolRuntime
from src.models import AppContext
from src.sandbox_backend import SandboxBackend
from typing import Dict, Any, Optional, Annotated

@tool
async def cargar_habilidad(
    nombre_habilidad: str, 
    runtime: Annotated[ToolRuntime[AppContext], InjectedToolArg] = None
) -> str:
	"""
	Carga un skill especifico.
	
	Args:
		nombre_habilidad: Nombre del skill a cargar (p. ej., 'escrituras/compraventa')
	
	Returns:
		El contenido completo del skill en formato markdown
	"""
	backend: SandboxBackend = SandboxBackend(runtime)
	
	# Stream status update
	runtime.stream_writer(f"Cargando instrucciones '{nombre_habilidad}'...")
	
	# Load the skill content using backend method
	content = await backend.get_skill_content(nombre_habilidad)
	
	if not content:
		return f"Error: No se pudo cargar la habilidad '{nombre_habilidad}'. Verifica que el nombre sea correcto."
	
	runtime.stream_writer(f"Instrucciones cargadas")
	
	# Return the skill content directly
	return content


