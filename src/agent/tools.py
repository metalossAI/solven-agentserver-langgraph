from deepagents.backends.protocol import FileDownloadResponse
from langchain_core.tools import tool, InjectedToolArg
from langchain_core.messages import ToolMessage
from langchain.tools import ToolRuntime
from src.models import AppContext
from src.sandbox_backend import SandboxBackend
from typing import Dict, Any, Optional, Annotated

@tool
async def cargar_habilidad(
    path: Annotated[str, "La ruta del skill a cargar"],
    runtime: Annotated[ToolRuntime[AppContext], InjectedToolArg] = None
) -> str:
	"""
	Carga un skill especifico.

	Args:
		path: La ruta del skill a cargar
	"""
	backend: SandboxBackend = SandboxBackend(runtime)
	responses : list[FileDownloadResponse] = await backend.download_files([path])
	if responses:
		print(f"Cargada la habilidad {path}")
		return responses[0].content
	else:
		return "Error: No se pudo cargar la habilidad"


