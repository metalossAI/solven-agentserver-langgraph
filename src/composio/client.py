import asyncio
import json
import hashlib
import mimetypes
import os
from typing import Any, Callable, Dict, Optional, Union
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
    provider=LangchainProvider(),
)

async def execute_composio_tool(
    tool_name: str,
    arguments: Dict[str, Any],
    runtime: ToolRuntime[AppContext],
    modifer : Optional[Callable] = None,
) -> str:
    """Execute a Composio tool with user context."""
    if not runtime:
        return "Error: Runtime context not available."
    
    # Debug: Check what's in the runtime context
    print(f"[DEBUG] Runtime context: {runtime.context}")
    print(f"[DEBUG] Runtime context user: {runtime.context.user if hasattr(runtime.context, 'user') else 'NO USER ATTR'}")
    
    user_id = runtime.context.user.id if runtime.context.user else None
    if not user_id:
        error_msg = f"Error: User ID not found in runtime context. Context: {runtime.context}"
        print(f"[ERROR] {error_msg}")
        return error_msg
    
    print(f"[DEBUG] Executing Composio tool '{tool_name}' with user_id: {user_id}")
    
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
        
        # Apply modifier if provided
        if modifer is not None:
            data = modifer(data)
        
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


async def upload_file_to_composio(
    file_content: Union[bytes, str],
    file_name: str,
    app_slug: str,
    action_slug: str,
    runtime: ToolRuntime[AppContext],
    mime_type: Optional[str] = None,
    custom_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Upload a file to Composio's backend for use in actions like GMAIL_SEND_EMAIL.
    
    Args:
        file_content: File content as bytes or file path string
        file_name: Original file name (e.g., "document.pdf")
        app_slug: App slug (e.g., "gmail", "slack", "outlook")
        action_slug: Action slug (e.g., "GMAIL_SEND_EMAIL", "SLACK_UPLOAD_FILE")
        runtime: ToolRuntime with user context
        mime_type: MIME type (auto-detected if not provided)
        custom_path: Custom path to maintain consistency with your S3 structure
                     (e.g., "threads/thread_id/adjuntos/file.pdf")
        
    Returns:
        Dict with 'success', 'file_id', 'storage_location', 'custom_path', and optional 'error' fields
        
    Example:
        result = await upload_file_to_composio(
            file_content=pdf_bytes,
            file_name="report.pdf",
            app_slug="gmail",
            action_slug="GMAIL_SEND_EMAIL",
            runtime=runtime,
            custom_path="threads/abc123/adjuntos/report.pdf"
        )
        # Use result['file_id'] in the send_email attachments parameter
        # Use result['custom_path'] to reference the file in your backend
    """
    try:
        import aiohttp
        
        # Read file content if path is provided
        if isinstance(file_content, str):
            if os.path.exists(file_content):
                with open(file_content, "rb") as f:
                    file_bytes = f.read()
            else:
                return {
                    'success': False,
                    'error': f"File not found: {file_content}"
                }
        else:
            file_bytes = file_content
        
        # Auto-detect MIME type if not provided
        if mime_type is None:
            # Get file extension
            ext = os.path.splitext(file_name)[1].lower()
            
            # Common MIME types mapping (especially for Microsoft Office files)
            mime_type_map = {
                # Microsoft Office
                '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                '.doc': 'application/msword',
                '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                '.xls': 'application/vnd.ms-excel',
                '.pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
                '.ppt': 'application/vnd.ms-powerpoint',
                # PDF
                '.pdf': 'application/pdf',
                # Images
                '.jpg': 'image/jpeg',
                '.jpeg': 'image/jpeg',
                '.png': 'image/png',
                '.gif': 'image/gif',
                '.webp': 'image/webp',
                '.svg': 'image/svg+xml',
                # Text
                '.txt': 'text/plain',
                '.csv': 'text/csv',
                '.html': 'text/html',
                '.htm': 'text/html',
                # Archives
                '.zip': 'application/zip',
                '.rar': 'application/x-rar-compressed',
                '.7z': 'application/x-7z-compressed',
            }
            
            # Try custom mapping first
            mime_type = mime_type_map.get(ext)
            
            # Fall back to mimetypes library
            if mime_type is None:
                mime_type, _ = mimetypes.guess_type(file_name)
            
            # Final fallback
            if mime_type is None:
                mime_type = "application/octet-stream"
        
        # Calculate MD5 hash for deduplication
        md5_hash = hashlib.md5(file_bytes).hexdigest()
        
        # Get Composio API key
        composio_api_key = os.getenv("COMPOSIO_API_KEY")
        if not composio_api_key:
            return {
                'success': False,
                'error': "COMPOSIO_API_KEY not found in environment"
            }
        
        # Step 1: Request presigned URL
        async def _request_presigned_url():
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    "https://backend.composio.dev/api/v3/files/upload/request",
                    headers={
                        "x-api-key": composio_api_key,
                        "Content-Type": "application/json"
                    },
                    json={
                        "toolkit_slug": app_slug,
                        "tool_slug": action_slug,
                        "filename": file_name,
                        "mimetype": mime_type,
                        "md5": md5_hash
                    }
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        return {
                            'success': False,
                            'error': f"Failed to get presigned URL: {response.status} - {error_text}"
                        }
                    return await response.json()
        
        presigned_response = await _request_presigned_url()
        
        if not presigned_response.get('success', True):
            return presigned_response
        
        # v3 API returns: id, key, new_presigned_url (or newPresignedUrl)
        file_id = presigned_response.get("id")
        presigned_url = presigned_response.get("new_presigned_url") or presigned_response.get("newPresignedUrl")
        storage_location = presigned_response.get("key")
        
        if not presigned_url:
            return {
                'success': False,
                'error': "No presigned URL returned from Composio"
            }
        
        # Step 2: Upload file to presigned URL
        async def _upload_to_presigned_url():
            async with aiohttp.ClientSession() as session:
                async with session.put(
                    presigned_url,
                    data=file_bytes,
                    headers={
                        "Content-Type": mime_type
                    }
                ) as response:
                    if response.status not in (200, 201, 204):
                        error_text = await response.text()
                        return {
                            'success': False,
                            'error': f"Failed to upload file: {response.status} - {error_text}"
                        }
                    return {'success': True}
        
        upload_result = await _upload_to_presigned_url()
        
        if not upload_result.get('success'):
            return upload_result
        
        return {
            'success': True,
            'file_id': file_id,
            'storage_location': storage_location,
            'custom_path': custom_path,
            'file_name': file_name,
            'mime_type': mime_type,
            'size_bytes': len(file_bytes)
        }
        
    except Exception as e:
        import traceback
        return {
            'success': False,
            'error': f"Error uploading file to Composio: {str(e)}\n{traceback.format_exc()}"
        }


