from langchain.tools import ToolRuntime
from langchain_core.tools import tool, InjectedToolArg
from langgraph.types import interrupt
from src.sandbox_backend import SandboxBackend
from src.models import AppContext
from src.utils.config import get_user_id_from_config, get_thread_id_from_config
from typing import Annotated

@tool
async def solicitar_recurso_de_skill(
    path: str,
    runtime: Annotated[ToolRuntime[AppContext], InjectedToolArg]
):
    """
    Solicita al usuario que suba un archivo como recursos de la skill.
    
    Args:
        path: ./skill-name/resource-name
    
    Returns:
        Información del archivo subido: nombre, tamaño, tipo, y ruta en S3
    """
    print(f"[solicitar_archivo] TOOL CALLED - path: {path}", flush=True)
    
    user_id = get_user_id_from_config()
    thread_id = get_thread_id_from_config()
    
    if not user_id:
        return "Error: No se encontró el ID del usuario"
    
    if not thread_id:
        return "Error: No se encontró el ID del hilo de conversación"
    
    # Create interrupt payload to request file upload
    interrupt_payload = {
        "action": "file_upload_request",
        "path": path,
        "thread_id": thread_id,
        "user_id": user_id,
    }
    
    print(f"[solicitar_archivo] CALLING interrupt() - about to pause", flush=True)
    uploaded_file_info = interrupt(interrupt_payload)
    print(f"[solicitar_archivo] RESUMED from interrupt - received: {uploaded_file_info}", flush=True)
    
    # When resumed, uploaded_file_info will contain the file information
    if isinstance(uploaded_file_info, dict):
        filename = uploaded_file_info.get("filename", "archivo")
        local_path = uploaded_file_info.get("local_path", "")  # Path where file was temporarily saved

        try:
            backend : SandboxBackend = SandboxBackend(runtime)
            runtime.stream_writer(f"Subiendo {filename} al espacio de trabajo...")
            
            # Upload file to workspace using the new upload_file method
            result = await backend.upload_file(path, local_path)
            
            if result.error:
                runtime.stream_writer(f"❌ Error: {result.error}")
                return f"Error al subir el archivo: {result.error}"
            
            runtime.stream_writer(f"✅ Archivo {filename} subido exitosamente a {path}")
            
            # Return simple string response - the framework will handle creating the ToolMessage
            return f"Archivo {filename} subido exitosamente a {path}. El archivo está disponible en el workspace."

        except Exception as e:
            error_msg = f"Error al subir el archivo: {str(e)}"
            runtime.stream_writer(f"❌ {error_msg}")
            return error_msg

    else:
        runtime.stream_writer(f"❌ Error: Información de archivo inválida")
        return "Error: Información de archivo inválida recibida"