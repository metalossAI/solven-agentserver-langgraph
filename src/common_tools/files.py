from langchain_core.tools import tool

@tool
async def upload_file(runtime: ToolRuntime):
    """
    Herramienta para solicitar al usuario que suba un archivo
    """
    return ""