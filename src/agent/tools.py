from deepagents.backends.protocol import FileDownloadResponse
from langchain_core.tools import tool, InjectedToolArg
from langchain_core.messages import ToolMessage
from langchain.tools import ToolRuntime
from src.models import AppContext
from src.sandbox_backend import get_backend
from typing import Dict, Any, Optional, Annotated

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
	responses : list[FileDownloadResponse] = await backend.adownload_files([path])
	if responses:
		print(f"Cargada la habilidad {path}")
		return ToolMessage(
			content=responses[0].content.decode("utf-8"),
			status="success",
			tool_call_id=runtime.tool_call_id,
			name="load_skill",
		)
	else:
		return ToolMessage(content="Error: No se pudo cargar la habilidad", status="error", tool_call_id=runtime.tool_call_id)


