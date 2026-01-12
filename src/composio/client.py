import asyncio
import json
from typing import Any, Dict
from langchain.tools import ToolRuntime

from src.models import AppContext

from composio import Composio
from composio_langchain import LangchainProvider

# Shared Composio client instance
composio_client = Composio(
    toolkit_versions={
        "gmail": "20260110_00",
        "outlook": "20260110_00"
    },
    provider=LangchainProvider()
)

async def execute_composio_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    runtime: ToolRuntime[AppContext]
) -> str:
    """Execute a Composio tool with user context."""
    if not runtime:
        return "Error: Runtime context not available."
    
    user_id = runtime.context.user.id if runtime.context.user else None
    if not user_id:
        return "Error: User ID not found in runtime context."
    
    try:
        def _execute_tool():
            return composio_client.tools.execute(
                slug=tool_name,
                user_id=user_id,
                arguments=arguments,
            )
        
        result = await asyncio.to_thread(_execute_tool)
        
        # Handle None result
        if result is None:
            return "Error: Composio tool execution returned None. Please check tool configuration and user permissions."
        
        # Ensure result is a dictionary
        if not isinstance(result, dict):
            return f"Error: Composio tool returned unexpected type: {type(result)}. Expected dict."
        
        # Check if execution was successful
        if not result.get("successful", False):
            error_msg = result.get("error", "Unknown error from Composio tool.")
            return f"Error: {error_msg}"
        
        # Extract data and convert to JSON string (tools must return strings)
        data = result.get("data", "")
        if data is None:
            return "Success: Tool executed successfully but returned no data."
        
        # Convert to JSON string for better structure preservation
        try:
            return json.dumps(data, indent=2, ensure_ascii=False)
        except (TypeError, ValueError):
            # Fall back to string if not JSON serializable
            return str(data)
    
    except AttributeError as e:
        return f"Error executing Composio tool '{tool_name}': AttributeError - {str(e)}. Result may be None or not a dict."
    except Exception as e:
        import traceback
        return f"Error executing Composio tool '{tool_name}': {str(e)}\n\n{traceback.format_exc()}"
 