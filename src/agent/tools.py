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
	"""Carga una habilidad específica. Util para ejecutar tareas especializadas donde el uso de herramientas convencionales no es suficiente.
	Por ejemplo, para redactar un documento legal o para seguir un workflow específico.
	
	Args:
		nombre_habilidad: Nombre de la habilidad a cargar (p. ej., 'compraventa-de-viviendas')
	
	Returns:
		El contenido completo de la habilidad en formato markdown
	"""
	backend: SandboxBackend = runtime.context.backend
	if not backend:
		return "Error: No hay backend disponible"
	
	# Stream status update
	runtime.stream_writer(f"🔄 Cargando habilidad '{nombre_habilidad}'...")
	
	# Load the skill content using backend method
	content = await backend.get_skill_content(nombre_habilidad)
	
	if not content:
		return f"Error: No se pudo cargar la habilidad '{nombre_habilidad}'. Verifica que el nombre sea correcto."
	
	runtime.stream_writer(f"✅ Habilidad '{nombre_habilidad}' cargada")
	
	# Return the skill content directly
	return content


