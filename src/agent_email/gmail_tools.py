"""Gmail tool wrappers using Composio direct execution."""

import asyncio
import json
import base64
import os
from typing import Any, Optional, List, Dict
from src.sandbox_backend import SandboxBackend
from src.s3_client import S3Client
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from langgraph.types import interrupt
from src.composio.types.gmail import GMAIL
from src.composio.client import composio_client, execute_composio_tool, upload_file_to_composio
from src.models import AppContext

# Message Operations
@tool(
    GMAIL.tools.FETCH_EMAILS,
)
async def gmail_fetch_emails(
    runtime: ToolRuntime[AppContext],
    query: Optional[str] = None,
    max_results: int = 1,
    label_ids: Optional[List[str]] = None,
    include_payload: bool = True,
    include_spam_trash: bool = False,
    ids_only: bool = False,
    verbose: bool = True,
    page_token: Optional[str] = None,
    user_id: str = "me",
) -> str:
    """Fetches a list of email messages from a Gmail account."""
    arguments = {
        "query": query,
        "max_results": max_results,
        "label_ids": label_ids,
        "include_payload": include_payload,
        "include_spam_trash": include_spam_trash,
        "ids_only": ids_only,
        "verbose": verbose,
        "page_token": page_token,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    #TODO: revisar!!
    def compact_email_modifier(data: Dict[str, Any]) -> Dict[str, Any]:
        """Extract only compact email information."""
        messages = data.get("messages", [])
        compact_messages = []
        
        for msg in messages:
            compact_msg = {
                "messageId": msg.get("messageId"),
                "from": msg.get("sender"),
                "subject": msg.get("subject"),
                "attachmentList": msg.get("attachmentList"),
                "preview": msg.get("preview"),
            }
            compact_messages.append(compact_msg)
        
        return {
            "messages": compact_messages,
            "nextPageToken": data.get("nextPageToken"),
            "resultSizeEstimate": data.get("resultSizeEstimate"),
            "composio_execution_message": data.get("composio_execution_message"),
        }
    
    return await execute_composio_tool(GMAIL.tools.FETCH_EMAILS, arguments, runtime, compact_email_modifier)


@tool(
    GMAIL.tools.SEND_EMAIL,
)
async def gmail_send_email(
    runtime: ToolRuntime[AppContext],
    recipient_email: Optional[str] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    extra_recipients: Optional[List[str]] = None,
    is_html: bool = False,
    attachment_path: Optional[str] = None,
    user_id: str = "me",
) -> str:
    """
    Send an email via Gmail with optional attachment.
    
    Args:
        recipient_email: Primary recipient email address
        subject: Email subject line
        body: Email body content
        cc: List of CC email addresses
        bcc: List of BCC email addresses
        extra_recipients: Additional recipient email addresses
        is_html: Whether the body is HTML formatted
        attachment_path: Single file path from workspace (e.g., "/workspace/adjuntos/file.pdf")
        user_id: Gmail user ID (default: "me")
        
    Returns:
        JSON string with success status and message details
    """
    
    composio_attachment = None
    temp_file_path = None
    
    if attachment_path:
        backend = SandboxBackend(runtime)
        
        try:
            # Download file from sandbox
            download_responses = await backend.adownload_files([attachment_path])
            download_response = download_responses[0] if download_responses else None
            
            if not download_response or download_response.error:
                error_msg = download_response.error if download_response else "unknown error"
                return json.dumps({
                    "success": False,
                    "message": f"Failed to download attachment: {error_msg}"
                }, indent=2)
            
            file_content = download_response.content
            file_name = os.path.basename(attachment_path)
            
            if not isinstance(file_content, bytes):
                return json.dumps({
                    "success": False,
                    "message": f"Invalid file content type: {type(file_content).__name__}"
                }, indent=2)
            
            # Write to temp file - Composio SDK will handle upload
            import tempfile
            temp_dir = tempfile.gettempdir()
            temp_file_path = os.path.join(temp_dir, file_name)
            
            with open(temp_file_path, 'wb') as f:
                f.write(file_content)
            
            # Verify temp file integrity
            temp_size = os.path.getsize(temp_file_path)
            
            # Read back and verify it matches
            with open(temp_file_path, 'rb') as f:
                verify_content = f.read()
            
            if len(verify_content) != len(file_content):
                return json.dumps({
                    "success": False,
                    "message": f"Temp file size mismatch: written {len(file_content)}, read {len(verify_content)}"
                }, indent=2)
            
            if verify_content != file_content:
                return json.dumps({
                    "success": False,
                    "message": "Temp file content mismatch - file corrupted during write"
                }, indent=2)
            
            # Check DOCX signature (PK zip header)
            if file_name.endswith('.docx') and not verify_content.startswith(b'PK'):
                return json.dumps({
                    "success": False,
                    "message": f"Invalid DOCX file - missing PK header. First bytes: {verify_content[:10].hex()}"
                }, indent=2)
            
            composio_attachment = temp_file_path
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "message": f"Error processing attachment: {str(e)}"
            }, indent=2)
    
    arguments = {
        "recipient_email": recipient_email,
        "subject": subject,
        "body": body,
        "cc": cc or [],
        "bcc": bcc or [],
        "extra_recipients": extra_recipients or [],
        "is_html": is_html,
        "user_id": user_id,
    }
    
    if composio_attachment:
        arguments["attachment"] = composio_attachment
    
    arguments = {k: v for k, v in arguments.items() if v is not None}
    
    # # Request user confirmation before sending
    # confirmation_payload = {
    #     "action": "confirm_email_send",
    #     "provider": "gmail",
    #     "recipient": recipient_email,
    #     "subject": subject,
    #     "body": body,
    #     "has_attachment": attachment_path is not None,
    #     "attachment_name": os.path.basename(attachment_path) if attachment_path else None,
    # }
    # 
    # confirmation = interrupt(confirmation_payload)
    # 
    # # If user didn't confirm, return cancelled message
    # if not confirmation or confirmation.get("confirmed") != True:
    #     # Clean up temp file if exists
    #     if temp_file_path and os.path.exists(temp_file_path):
    #         try:
    #             os.remove(temp_file_path)
    #         except Exception:
    #             pass
    #     return json.dumps({
    #         "success": False,
    #         "message": "Email sending cancelled by user"
    #     }, indent=2)
    
    try:
        result = await execute_composio_tool(GMAIL.tools.SEND_EMAIL, arguments, runtime)
        return result
    finally:
        # Clean up temp file
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception:
                pass


@tool(
    GMAIL.tools.FETCH_MESSAGE_BY_MESSAGE_ID,
)
async def gmail_fetch_message_by_message_id(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    format: str = "full",
    user_id: str = "me",
) -> str:
    """Fetches a specific email message by its ID."""
    arguments = {
        "message_id": message_id,
        "format": format,
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.FETCH_MESSAGE_BY_MESSAGE_ID, arguments, runtime)


@tool(
    GMAIL.tools.FETCH_MESSAGE_BY_THREAD_ID,
)
async def gmail_fetch_message_by_thread_id(
    runtime: ToolRuntime[AppContext],
    thread_id: str,
    page_token: str = "",
    user_id: str = "me",
) -> str:
    """Retrieves messages from a Gmail thread using its thread_id."""
    arguments = {
        "thread_id": thread_id,
        "page_token": page_token,
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.FETCH_MESSAGE_BY_THREAD_ID, arguments, runtime)


@tool(
    GMAIL.tools.REPLY_TO_THREAD,
)
async def gmail_reply_to_thread(
    runtime: ToolRuntime[AppContext],
    thread_id: str,
    message_body: str = "",
    recipient_email: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    extra_recipients: Optional[List[str]] = None,
    is_html: bool = False,
    attachment_path: Optional[str] = None,
    user_id: str = "me",
) -> str:
    """Sends a reply within a specific Gmail thread with optional attachment."""
    
    # Process attachment if provided
    composio_attachment = None
    
    if attachment_path:
        backend = SandboxBackend(runtime)
        
        try:
            download_responses = await backend.adownload_files([attachment_path])
            download_response = download_responses[0] if download_responses else None
            
            if not download_response or download_response.error:
                error_msg = download_response.error if download_response else "unknown error"
                return json.dumps({
                    "success": False,
                    "message": f"Failed to download attachment '{attachment_path}': {error_msg}"
                }, indent=2)
            
            file_content = download_response.content
            file_name = os.path.basename(attachment_path)
            
            # Validate file content is bytes
            if not isinstance(file_content, bytes):
                return json.dumps({
                    "success": False,
                    "message": f"Invalid file content type: expected bytes, got {type(file_content).__name__}"
                }, indent=2)
            
            # Upload to Composio S3 backend
            upload_result = await upload_file_to_composio(
                file_content=file_content,
                file_name=file_name,
                app_slug="gmail",
                action_slug="GMAIL_REPLY_TO_THREAD",
                runtime=runtime,
                custom_path=attachment_path
            )
            
            if not upload_result.get('success'):
                return json.dumps({
                    "success": False,
                    "message": f"Failed to upload attachment '{file_name}' to Composio: {upload_result.get('error')}"
                }, indent=2)
            
            # Build attachment object with s3key format
            storage_key = upload_result.get('storage_location') or upload_result.get('key')
            composio_attachment = {
                "s3key": storage_key,
                "name": file_name,
                "mimetype": upload_result.get('mime_type', 'application/octet-stream')
            }
            
        except Exception as e:
            return json.dumps({
                "success": False,
                "message": f"Error processing attachment '{attachment_path}': {str(e)}"
            }, indent=2)
    
    arguments = {
        "thread_id": thread_id,
        "message_body": message_body,
        "recipient_email": recipient_email,
        "cc": cc or [],
        "bcc": bcc or [],
        "extra_recipients": extra_recipients or [],
        "is_html": is_html,
        "user_id": user_id,
    }
    
    if composio_attachment:
        arguments["attachment"] = composio_attachment
    
    arguments = {k: v for k, v in arguments.items() if v is not None}
    
    return await execute_composio_tool(GMAIL.tools.REPLY_TO_THREAD, arguments, runtime)


@tool(
    GMAIL.tools.FORWARD_MESSAGE,
)
async def gmail_forward_message(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    recipient_email: str,
    additional_text: Optional[str] = None,
    user_id: str = "me",
) -> str:
    """Forward an existing Gmail message to specified recipients."""
    arguments = {
        "message_id": message_id,
        "recipient_email": recipient_email,
        "additional_text": additional_text,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(GMAIL.tools.FORWARD_MESSAGE, arguments, runtime)


@tool(
    GMAIL.tools.MOVE_TO_TRASH,
)
async def gmail_move_to_trash(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    user_id: str = "me",
) -> str:
    """Moves an existing email message to the trash."""
    arguments = {
        "message_id": message_id,
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.MOVE_TO_TRASH, arguments, runtime)


@tool(
    GMAIL.tools.DELETE_MESSAGE,
)
async def gmail_delete_message(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    user_id: str = "me",
) -> str:
    """Permanently deletes a specific email message by its ID."""
    arguments = {
        "message_id": message_id,
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.DELETE_MESSAGE, arguments, runtime)


# Draft Operations
@tool(
    GMAIL.tools.CREATE_EMAIL_DRAFT,
)
async def gmail_create_email_draft(
    runtime: ToolRuntime[AppContext],
    recipient_email: Optional[str] = None,
    subject: Optional[str] = None,
    body: Optional[str] = None,
    cc: Optional[List[str]] = None,
    bcc: Optional[List[str]] = None,
    extra_recipients: Optional[List[str]] = None,
    is_html: bool = False,
    thread_id: Optional[str] = None,
    attachment: Optional[Dict[str, Any]] = None,
    user_id: str = "me",
) -> str:
    """Creates a Gmail email draft."""
    arguments = {
        "recipient_email": recipient_email,
        "subject": subject,
        "body": body,
        "cc": cc or [],
        "bcc": bcc or [],
        "extra_recipients": extra_recipients or [],
        "is_html": is_html,
        "thread_id": thread_id,
        "attachment": attachment,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(GMAIL.tools.CREATE_EMAIL_DRAFT, arguments, runtime)


@tool(
    GMAIL.tools.SEND_DRAFT,
)
async def gmail_send_draft(
    runtime: ToolRuntime[AppContext],
    draft_id: str,
    user_id: str = "me",
) -> str:
    """Sends the specified existing draft to the recipients."""
    arguments = {
        "draft_id": draft_id,
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.SEND_DRAFT, arguments, runtime)


@tool(
    GMAIL.tools.LIST_DRAFTS,
)
async def gmail_list_drafts(
    runtime: ToolRuntime[AppContext],
    max_results: int = 1,
    page_token: str = "",
    verbose: bool = False,
    user_id: str = "me",
) -> str:
    """Retrieves a paginated list of email drafts from a user's Gmail account."""
    arguments = {
        "max_results": max_results,
        "page_token": page_token,
        "verbose": verbose,
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.LIST_DRAFTS, arguments, runtime)


@tool(
    GMAIL.tools.DELETE_DRAFT,
)
async def gmail_delete_draft(
    runtime: ToolRuntime[AppContext],
    draft_id: str,
    user_id: str = "me",
) -> str:
    """Permanently deletes a specific Gmail draft using its ID."""
    arguments = {
        "draft_id": draft_id,
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.DELETE_DRAFT, arguments, runtime)


# Label Operations
@tool(
    GMAIL.tools.ADD_LABEL_TO_EMAIL,
)
async def gmail_add_label_to_email(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    add_label_ids: Optional[List[str]] = None,
    remove_label_ids: Optional[List[str]] = None,
    user_id: str = "me",
) -> str:
    """Adds and/or removes specified Gmail labels for a message."""
    arguments = {
        "message_id": message_id,
        "add_label_ids": add_label_ids or [],
        "remove_label_ids": remove_label_ids or [],
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.ADD_LABEL_TO_EMAIL, arguments, runtime)


@tool(
    GMAIL.tools.LIST_LABELS,
)
async def gmail_list_labels(
    runtime: ToolRuntime[AppContext],
    user_id: str = "me",
) -> str:
    """Retrieves a list of all system and user-created labels for the specified Gmail account."""
    arguments = {
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.LIST_LABELS, arguments, runtime)


@tool(
    GMAIL.tools.CREATE_LABEL,
)
async def gmail_create_label(
    runtime: ToolRuntime[AppContext],
    label_name: str,
    background_color: Optional[str] = None,
    text_color: Optional[str] = None,
    label_list_visibility: str = "labelShow",
    message_list_visibility: str = "show",
    user_id: str = "me",
) -> str:
    """Creates a new label with a unique name in the specified user's Gmail account."""
    arguments = {
        "label_name": label_name,
        "background_color": background_color,
        "text_color": text_color,
        "label_list_visibility": label_list_visibility,
        "message_list_visibility": message_list_visibility,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(GMAIL.tools.CREATE_LABEL, arguments, runtime)


@tool(
    GMAIL.tools.DELETE_LABEL,
)
async def gmail_delete_label(
    runtime: ToolRuntime[AppContext],
    label_id: str,
    user_id: str = "me",
) -> str:
    """Permanently DELETES a user-created Gmail label from the account."""
    arguments = {
        "label_id": label_id,
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.DELETE_LABEL, arguments, runtime)


@tool(
    GMAIL.tools.PATCH_LABEL,
)
async def gmail_patch_label(
    runtime: ToolRuntime[AppContext],
    userId: str,
    id: str,
    name: Optional[str] = None,
    messageListVisibility: Optional[str] = None,
    labelListVisibility: Optional[str] = None,
    color: Optional[Dict[str, str]] = None,
) -> str:
    """Patches the specified label."""
    arguments = {
        "userId": userId,
        "id": id,
        "name": name,
        "messageListVisibility": messageListVisibility,
        "labelListVisibility": labelListVisibility,
        "color": color,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(GMAIL.tools.PATCH_LABEL, arguments, runtime)


# Thread Operations
@tool(
    GMAIL.tools.LIST_THREADS,
)
async def gmail_list_threads(
    runtime: ToolRuntime[AppContext],
    max_results: int = 10,
    page_token: str = "",
    query: str = "",
    verbose: bool = False,
    user_id: str = "me",
) -> str:
    """Retrieves a list of email threads from a Gmail account."""
    arguments = {
        "max_results": max_results,
        "page_token": page_token,
        "query": query,
        "verbose": verbose,
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.LIST_THREADS, arguments, runtime)


@tool(
    GMAIL.tools.MODIFY_THREAD_LABELS,
)
async def gmail_modify_thread_labels(
    runtime: ToolRuntime[AppContext],
    thread_id: str,
    add_label_ids: Optional[List[str]] = None,
    remove_label_ids: Optional[List[str]] = None,
    user_id: str = "me",
) -> str:
    """Adds or removes specified existing label IDs from a Gmail thread."""
    arguments = {
        "thread_id": thread_id,
        "add_label_ids": add_label_ids,
        "remove_label_ids": remove_label_ids,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(GMAIL.tools.MODIFY_THREAD_LABELS, arguments, runtime)


# Attachment Operations
@tool(
    GMAIL.tools.GET_ATTACHMENT,
)
async def gmail_get_attachment(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    attachment_id: str,
    file_name: str,
    user_id: str = "me",
) -> str:
    """
    Retrieves a specific attachment by ID from a message in a user's Gmail mailbox.
    Returns the workspace-relative path where the agent can access the file.

    Args:
        message_id (str): The ID of the message containing the attachment.
        attachment_id (str): The ID of the attachment to retrieve.
        file_name (str): The name of the file to save the attachment as.
        user_id (str, optional): The ID of the user's Gmail account. Defaults to "me".
        
    Returns:
        str: JSON string with success status and workspace path (e.g., /workspace/attachments/file.pdf)
    """
    arguments = {
        "message_id": message_id,
        "attachment_id": attachment_id,
        "file_name": file_name,
        "user_id": user_id,
    }

    result = await execute_composio_tool(GMAIL.tools.GET_ATTACHMENT, arguments, runtime)
    
    try:
        result_data = json.loads(result)
        
        attachment_bytes = None
        
        if isinstance(result_data, dict):
            if "data" in result_data:
                attachment_data_b64 = result_data["data"]
                attachment_bytes = base64.b64decode(attachment_data_b64)
                source_type = "base64"
            elif "file" in result_data:
                import os
                local_file_path = result_data["file"]
                
                if os.path.exists(local_file_path):
                    file_size = os.path.getsize(local_file_path)
                    with open(local_file_path, "rb") as f:
                        attachment_bytes = f.read()
                    source_type = "local_file"
                else:
                    return json.dumps({
                        "success": False,
                        "message": f"Local file not found: {local_file_path}",
                        "raw_result": result,
                    }, indent=2)
        
        if attachment_bytes:
            from datetime import datetime
            
            thread_id = runtime.context.thread.id
            
            s3_client = S3Client(prefix=f"threads/{thread_id}")
            
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_filename = file_name.replace(" ", "_").replace("/", "_")
            file_path = f"adjuntos/{timestamp}_{safe_filename}"
            
            upload_result = await asyncio.to_thread(
                s3_client.upload_file,
                file_path=file_path,
                content=attachment_bytes,
                metadata={
                    "message_id": message_id,
                    "attachment_id": attachment_id,
                    "source": "gmail",
                    "thread_id": thread_id,
                    "original_filename": file_name,
                    "uploaded_at": datetime.now().isoformat(),
                }
            )
            
            if upload_result["success"]:
                workspace_path = f"/workspace/{file_path}"
                
                return json.dumps({
                    "success": True,
                    "message": f"File saved to: {workspace_path}",
                    "path": workspace_path,
                    "file_name": file_name,
                    "message_id": message_id,
                    "size_bytes": len(attachment_bytes),
                }, indent=2)
            else:
                return json.dumps({
                    "success": False,
                    "message": f"S3 upload failed: {upload_result.get('error')}",
                    "file_name": file_name,
                    "upload_error": upload_result.get('error'),
                }, indent=2)
        else:
            return json.dumps({
                "success": False,
                "message": "Attachment data not found in response (expected 'data' or 'file' field)",
                "raw_result": result,
            }, indent=2)
            
    except json.JSONDecodeError:
        return json.dumps({
            "success": False,
            "message": "Failed to parse attachment response",
            "raw_result": result,
        }, indent=2)
        
    except Exception as e:
        return json.dumps({
            "success": False,
            "message": f"Error processing attachment: {str(e)}",
            "file_name": file_name,
        }, indent=2)
    


# Batch Operations
@tool(
    GMAIL.tools.BATCH_DELETE_MESSAGES,
)
async def gmail_batch_delete_messages(
    runtime: ToolRuntime[AppContext],
    messageIds: List[str],
    userId: str = "me",
) -> str:
    """Permanently delete multiple Gmail messages in bulk."""
    arguments = {
        "messageIds": messageIds,
        "userId": userId,
    }
    return await execute_composio_tool(GMAIL.tools.BATCH_DELETE_MESSAGES, arguments, runtime)


@tool(
    GMAIL.tools.BATCH_MODIFY_MESSAGES,
)
async def gmail_batch_modify_messages(
    runtime: ToolRuntime[AppContext],
    messageIds: List[str],
    addLabelIds: Optional[List[str]] = None,
    removeLabelIds: Optional[List[str]] = None,
    userId: str = "me",
) -> str:
    """Modify labels on multiple Gmail messages in one efficient API call."""
    arguments = {
        "messageIds": messageIds,
        "addLabelIds": addLabelIds,
        "removeLabelIds": removeLabelIds,
        "userId": userId,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(GMAIL.tools.BATCH_MODIFY_MESSAGES, arguments, runtime)


# Profile and Settings
@tool(
    GMAIL.tools.GET_PROFILE,
)
async def gmail_get_profile(
    runtime: ToolRuntime[AppContext],
    user_id: str = "me",
) -> str:
    """Retrieves key Gmail profile information for a user."""
    arguments = {
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.GET_PROFILE, arguments, runtime)


@tool(
    GMAIL.tools.GET_CONTACTS,
    description="Fetches contacts (connections) for the authenticated Google account.",
)
async def gmail_get_contacts(
    runtime: ToolRuntime[AppContext],
    resource_name: str = "people/me",
    person_fields: str = "emailAddresses,names,birthdays,genders",
    include_other_contacts: bool = True,
    page_token: Optional[str] = None,
) -> str:
    """Fetches contacts (connections) for the authenticated Google account."""
    arguments = {
        "resource_name": resource_name,
        "person_fields": person_fields,
        "include_other_contacts": include_other_contacts,
        "page_token": page_token,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(GMAIL.tools.GET_CONTACTS, arguments, runtime)


@tool(
    GMAIL.tools.SEARCH_PEOPLE,
)
async def gmail_search_people(
    runtime: ToolRuntime[AppContext],
    query: str,
    person_fields: str = "emailAddresses,metadata,names,phoneNumbers",
    other_contacts: bool = True,
    pageSize: int = 10,
) -> str:
    """Searches contacts by matching the query against names, nicknames, emails, phone numbers, and organizations."""
    arguments = {
        "query": query,
        "person_fields": person_fields,
        "other_contacts": other_contacts,
        "pageSize": pageSize,
    }
    return await execute_composio_tool(GMAIL.tools.SEARCH_PEOPLE, arguments, runtime)


@tool(
    GMAIL.tools.GET_PEOPLE,
)
async def gmail_get_people(
    runtime: ToolRuntime[AppContext],
    resource_name: str = "people/me",
    person_fields: str = "emailAddresses,names,birthdays,genders",
    other_contacts: bool = False,
    page_size: int = 10,
    page_token: str = "",
    sync_token: str = "",
) -> str:
    """Retrieves either a specific person's details or lists 'Other Contacts'."""
    arguments = {
        "resource_name": resource_name,
        "person_fields": person_fields,
        "other_contacts": other_contacts,
        "page_size": page_size,
        "page_token": page_token,
        "sync_token": sync_token,
    }
    return await execute_composio_tool(GMAIL.tools.GET_PEOPLE, arguments, runtime)


@tool(
    GMAIL.tools.GET_VACATION_SETTINGS,
)
async def gmail_get_vacation_settings(
    runtime: ToolRuntime[AppContext],
    user_id: str = "me",
) -> str:
    """Retrieves vacation responder settings for a Gmail user."""
    arguments = {
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.GET_VACATION_SETTINGS, arguments, runtime)


@tool(
    GMAIL.tools.GET_AUTO_FORWARDING,
)
async def gmail_get_auto_forwarding(
    runtime: ToolRuntime[AppContext],
    user_id: str = "me",
) -> str:
    """Gets the auto-forwarding setting for the specified account."""
    arguments = {
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.GET_AUTO_FORWARDING, arguments, runtime)


@tool(
    GMAIL.tools.GET_LANGUAGE_SETTINGS,
)
async def gmail_get_language_settings(
    runtime: ToolRuntime[AppContext],
    user_id: str = "me",
) -> str:
    """Retrieves the language settings for a Gmail user."""
    arguments = {
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.GET_LANGUAGE_SETTINGS, arguments, runtime)


@tool(
    GMAIL.tools.LIST_SEND_AS,
)
async def gmail_list_send_as(
    runtime: ToolRuntime[AppContext],
    user_id: str = "me",
) -> str:
    """Lists the send-as aliases for a Gmail account."""
    arguments = {
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.LIST_SEND_AS, arguments, runtime)


@tool(
    GMAIL.tools.SETTINGS_SEND_AS_GET,
)
async def gmail_settings_send_as_get(
    runtime: ToolRuntime[AppContext],
    send_as_email: str,
    user_id: str = "me",
) -> str:
    """Retrieves a specific send-as alias configuration for a Gmail user."""
    arguments = {
        "send_as_email": send_as_email,
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.SETTINGS_SEND_AS_GET, arguments, runtime)


@tool(
    GMAIL.tools.SETTINGS_GET_IMAP,
)
async def gmail_settings_get_imap(
    runtime: ToolRuntime[AppContext],
    user_id: str = "me",
) -> str:
    """Retrieves the IMAP settings for a Gmail user account."""
    arguments = {
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.SETTINGS_GET_IMAP, arguments, runtime)


@tool(
    GMAIL.tools.SETTINGS_GET_POP,
)
async def gmail_settings_get_pop(
    runtime: ToolRuntime[AppContext],
    user_id: str = "me",
) -> str:
    """Retrieves POP settings for a Gmail account."""
    arguments = {
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.SETTINGS_GET_POP, arguments, runtime)


@tool(
    GMAIL.tools.LIST_HISTORY,
)
async def gmail_list_history(
    runtime: ToolRuntime[AppContext],
    start_history_id: str,
    history_types: Optional[List[str]] = None,
    label_id: Optional[str] = None,
    max_results: int = 100,
    page_token: Optional[str] = None,
    user_id: str = "me",
) -> str:
    """Lists Gmail mailbox change history since a known startHistoryId."""
    arguments = {
        "start_history_id": start_history_id,
        "history_types": history_types,
        "label_id": label_id,
        "max_results": max_results,
        "page_token": page_token,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(GMAIL.tools.LIST_HISTORY, arguments, runtime)


@tool(
    GMAIL.tools.LIST_CSE_IDENTITIES,
)
async def gmail_list_cse_identities(
    runtime: ToolRuntime[AppContext],
    user_id: str = "me",
    page_size: Optional[int] = None,
    page_token: Optional[str] = None,
) -> str:
    """Lists client-side encrypted identities for an authenticated user."""
    arguments = {
        "user_id": user_id,
        "page_size": page_size,
        "page_token": page_token,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(GMAIL.tools.LIST_CSE_IDENTITIES, arguments, runtime)


@tool(
    GMAIL.tools.LIST_CSE_KEYPAIRS,
)
async def gmail_list_cse_keypairs(
    runtime: ToolRuntime[AppContext],
    user_id: str = "me",
    page_size: Optional[int] = None,
    page_token: Optional[str] = None,
) -> str:
    """Lists client-side encryption key pairs for an authenticated user."""
    arguments = {
        "user_id": user_id,
        "page_size": page_size,
        "page_token": page_token,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(GMAIL.tools.LIST_CSE_KEYPAIRS, arguments, runtime)


@tool(
    GMAIL.tools.LIST_SMIME_INFO,
)
async def gmail_list_smime_info(
    runtime: ToolRuntime[AppContext],
    send_as_email: str,
    user_id: str = "me",
) -> str:
    """Lists S/MIME configs for the specified send-as alias."""
    arguments = {
        "send_as_email": send_as_email,
        "user_id": user_id,
    }
    return await execute_composio_tool(GMAIL.tools.LIST_SMIME_INFO, arguments, runtime)


# Export all Gmail tools
gmail_tools = [
    # Message operations
    gmail_fetch_emails,
    gmail_send_email,
    gmail_fetch_message_by_message_id,
    gmail_fetch_message_by_thread_id,
    gmail_reply_to_thread,
    gmail_forward_message,
    # gmail_move_to_trash,
    # gmail_delete_message,
    # __________________________________________________________
    # Draft operations
    gmail_create_email_draft,
    gmail_send_draft,
    gmail_list_drafts,
    gmail_delete_draft,
    # __________________________________________________________
    # Label operations
    # gmail_add_label_to_email,
    # gmail_list_labels,
    # gmail_create_label,
    # gmail_delete_label,
    # gmail_patch_label,
    # __________________________________________________________
    # Thread operations
    gmail_list_threads,
    # gmail_modify_thread_labels,
    # __________________________________________________________
    # Attachment operations
    gmail_get_attachment,
    # __________________________________________________________
    # Batch operations
    # gmail_batch_delete_messages,
    # gmail_batch_modify_messages,
    # __________________________________________________________
    # Profile and settings
    # gmail_get_profile,
    # gmail_get_contacts,
    # gmail_search_people,
    # gmail_get_people,
    # gmail_get_vacation_settings,
    # gmail_get_auto_forwarding,
    # gmail_get_language_settings,
    # gmail_list_send_as,
    # gmail_settings_send_as_get,
    # gmail_settings_get_imap,
    # gmail_settings_get_pop,
    gmail_list_history,
    # gmail_list_cse_identities,
    # gmail_list_cse_keypairs,
    # gmail_list_smime_info,
]
