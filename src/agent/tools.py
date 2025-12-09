
from typing import Callable, List
import os
import tempfile
import requests
import asyncio
from composio import Composio, before_execute, after_execute, schema_modifier
from composio.types import ToolExecuteParams, ToolExecutionResponse
from composio_langchain import LangchainProvider
from docling.document_converter import DocumentConverter
from src.backend import get_user_backend_sync

def get_composio_gmail_tools(user_id, thread_id):
    """Synchronous version for running in thread pool"""
    # Create modifier factory with captured context
    save_attachment_modifier = create_save_attachment_modifier(user_id, thread_id)
    
    composio = Composio(provider=LangchainProvider())
    tools = composio.tools.get(
        user_id=user_id,
        toolkits=["GMAIL"],
        modifiers=[
            before_execute_modifier_gmail_fetch,
            save_attachment_modifier
        ]
    )
    return tools

def get_composio_outlook_tools(user_id, thread_id):
    """Synchronous version for running in thread pool"""
    
    # Create modifier factory with captured context
    save_attachment_modifier = create_save_attachment_modifier(user_id, thread_id)
    
    composio = Composio(provider=LangchainProvider())
    tools = composio.tools.get(
        user_id=user_id,
        toolkits=["OUTLOOK"],
        modifiers=[
            before_execute_modifier_outlook_fetch,
            save_attachment_modifier
        ]
    )
    return tools

def create_save_attachment_modifier(user_id: str, thread_id: str):
    """Factory function that creates a modifier with captured user_id and thread_id"""
    
    @after_execute(tools=["GMAIL_GET_ATTACHMENT", "OUTLOOK_DOWNLOAD_OUTLOOK_ATTACHMENT"])
    def save_attachment_modifier(
        tool: str,
        toolkit: str,
        response: ToolExecutionResponse
    ) -> ToolExecutionResponse:
        return process_attachment(
            user_id=user_id,
            thread_id=thread_id,
            tool=tool,
            toolkit=toolkit,
            response=response
        )
    
    return save_attachment_modifier

@before_execute(tools=["GMAIL_FETCH_EMAILS"])
def before_execute_modifier_gmail_fetch(
    tool: str,
    toolkit: str,
    params: ToolExecuteParams,
) -> ToolExecuteParams:
    params["arguments"]["max_results"] = 3
    params["arguments"]["verbose"] = False
    return params

@before_execute(tools=["OUTLOOK_QUERY_EMAILS"])
def before_execute_modifier_outlook_fetch(
    tool: str,
    toolkit: str,
    params: ToolExecuteParams,
) -> ToolExecuteParams:
    params["arguments"]["top"] = 3
    return params

def process_attachment(
    user_id: str,
    thread_id: str,
    tool: str,
    toolkit: str,
    response: ToolExecutionResponse
) -> ToolExecutionResponse:
    """
    Get local file path from Composio, convert to markdown using Docling,
    then upload both original + markdown to user's S3 conversation folder.
    """
    try:
        data = response.get("data", {})
        local_path = data.get("file")
        print("[after_execute_modifier_save_attachment] Local file path:", local_path)
        
        if not local_path or not os.path.exists(local_path):
            return response
        
        filename = os.path.basename(local_path)

        s3_backend = get_user_backend_sync(user_id, thread_id)

        # --- (2) Convert to markdown via Docling
        print(f"[after_execute_modifier_save_attachment] Converting to markdown with Docling")
        converter = DocumentConverter()
        conversion_result = converter.convert(local_path)
        markdown_content = conversion_result.document.export_to_markdown()

        # Save converted markdown
        base_name = os.path.splitext(filename)[0]
        markdown_filename = f"{base_name}.md"
        print(f"[after_execute_modifier_save_attachment] Saving markdown to S3: {markdown_filename}")
        md_write = s3_backend.write(markdown_filename, markdown_content)

        # --- Optional: cleanup local file
        os.remove(local_path)
        print("[after_execute_modifier_save_attachment] Cleaned up local file")

    except Exception as e:
        response["data"]["error"] = str(e)

    return response
