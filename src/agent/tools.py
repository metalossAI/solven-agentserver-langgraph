from deepagents.backends.protocol import FileDownloadResponse
from langchain_core.tools import tool, InjectedToolArg
from langchain.tools import ToolRuntime
from src.models import AppContext
from src.utils.backend import get_backend
from typing import Annotated

@tool
async def load_skill(
    path: Annotated[str, "La ruta del skill a cargar"],
    runtime: Annotated[ToolRuntime[AppContext], InjectedToolArg] = None
) -> str:
	"""
	Carga y leer un skill especifico.

	Args:
		path: La ruta del skill
	"""
	backend = get_backend(runtime)
	responses: list[FileDownloadResponse] = await backend.adownload_files([path])
	if responses and responses[0].content is not None:
		return responses[0].content.decode("utf-8")
	reason = "archivo vacío o no se pudo leer" if responses else "archivo no encontrado"
	return f"Error: No se pudo cargar la habilidad ({reason}): {path}"

