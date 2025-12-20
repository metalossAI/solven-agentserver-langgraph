from langchain_core.tools import tool
from langchain_core.messages import ToolMessage
from langchain.tools import ToolRuntime
from langgraph.types import Command
from src.models import AppContext
from src.backend import S3Backend

@tool
async def list_skills(runtime: ToolRuntime[AppContext]) -> str:
    """Lista las habilidades disponibles del dominio "escrituras".

    Returns:
        Cadena formateada con las habilidades de escrituras
    """
    backend : S3Backend = runtime.context.backend
    if not backend:
        return "Error: No hay backend disponible"
    
    # Filter to only show escrituras skills
    return await backend.load_all_skills_formatted(category='escrituras')

@tool
async def load_skill(runtime: ToolRuntime[AppContext], skill_path: str) -> Command:
    """Carga una habilidad específica para usar en la tarea actual.

    Esto inyectará las instrucciones de la habilidad en el prompt del sistema.

    Args:
        skill_path: Ruta de la habilidad en formato 'categoria/nombre_habilidad' (p. ej., 'escrituras/compraventa')

    Returns:
        Objeto Command que actualiza el estado y devuelve un mensaje
    """
    print(f"[load_skill] Herramienta llamada con skill_path: {skill_path}")
    
    backend : S3Backend = runtime.context.backend
    if not backend:
        print(f"[load_skill] ❌ No hay backend disponible")
        return Command(
            update={
                "messages": [ToolMessage(
                    content="Error: No hay backend disponible",
                    tool_call_id=runtime.tool_call_id
                )]
            }
        )
    
    # Load the skill content using backend method
    print(f"[load_skill] Cargando contenido de la habilidad desde S3...")
    content = await backend.load_skill_content(skill_path)
    
    if not content:
        return Command(
            update={
                "messages": [ToolMessage(
                    content=f"Error: No se pudo cargar la habilidad '{skill_path}'",
                    tool_call_id=runtime.tool_call_id
                )]
            }
        )
    
    # Return Command to update state and inject skill instructions into conversation
    return Command(
        update={
            "messages": [ToolMessage(
                content=content,
                tool_call_id=runtime.tool_call_id
            )],
            "current_skill": skill_path
        }
    )