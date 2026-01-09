from langchain_core.tools import tool
from langchain_core.messages import ToolMessage
from langchain.tools import ToolRuntime
from langgraph.types import Command
from src.models import AppContext
from src.sandbox_backend import SandboxBackend
@tool
async def cargar_habilidad(runtime: ToolRuntime[AppContext], nombre_habilidad: str) -> Command:
	"""Carga una habilidad específica. Util para ejecutar tareas especializadas donde el uso de herramientas convencionales no es suficiente.
	Por ejemplo, para redactar un documento legal o para seguir un workflow específico.
	
	Args:
		nombre_habilidad: Nombre de la habilidad a cargar (p. ej., 'compraventa-de-viviendas')
	"""
	backend: SandboxBackend = runtime.context.backend
	if not backend:
		return Command(
			update={
				"messages": [ToolMessage(
					content="Error: No hay backend disponible",
					tool_call_id=runtime.tool_call_id
				)]
			}
		)
	
	# Load the skill content using backend method
	content = await backend.get_skill_content(nombre_habilidad)
	
	if not content:
		return Command(
			update={
				"messages": [ToolMessage(
					content=f"Error: No se pudo cargar la habilidad '{nombre_habilidad}'. Verifica que el nombre sea correcto.",
					tool_call_id=runtime.tool_call_id
				)]
			}
		)
	
	# Register skill with backend to make it accessible in /skills virtual path
	await backend.load_skills([nombre_habilidad])
	
	# Return Command with ToolMessage containing the full SKILL.md content
	return Command(
		update={
			"messages": [ToolMessage(
				content=content,
				tool_call_id=runtime.tool_call_id
			)]
		}
	)

