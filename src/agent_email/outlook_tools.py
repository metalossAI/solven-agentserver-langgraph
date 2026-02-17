"""Outlook tool wrappers using Composio direct execution."""

import asyncio
import json
import base64
import os
from typing import Any, Optional, List, Dict
from src.sandbox_backend import SandboxBackend
from langchain_core.tools import tool
from langchain.tools import ToolRuntime
from langgraph.types import interrupt
from src.composio.types.outlook import OUTLOOK
from src.composio.client import composio_client, execute_composio_tool
from src.models import AppContext

@tool(
    OUTLOOK.tools.ADD_EVENT_ATTACHMENT,
)
async def outlook_add_event_attachment(
    runtime: ToolRuntime[AppContext],
    event_id: str,
    name: str,
    odata_type: str,
    content_bytes: Optional[str] = None,
    item: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Adds an attachment to a specific Outlook calendar event. Use when you need to attach a file or nested item to an existing event."""
    arguments = {
        "event_id": event_id,
        "name": name,
        "odata_type": odata_type,
        "contentBytes": content_bytes,
        "item": item,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.ADD_EVENT_ATTACHMENT, arguments, runtime)


@tool(
    OUTLOOK.tools.ADD_MAIL_ATTACHMENT,
)
async def outlook_add_mail_attachment(
    runtime: ToolRuntime[AppContext],
    content_bytes: str,
    message_id: str,
    name: str,
    odata_type: str,
    content_id: Optional[str] = None,
    content_location: Optional[str] = None,
    content_type: Optional[str] = None,
    is_inline: Optional[bool] = None,
    item: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to add an attachment to an email message. Use when you have a message ID and need to attach a small (<3 MB) file or reference."""
    arguments = {
        "contentBytes": content_bytes,
        "message_id": message_id,
        "name": name,
        "odata_type": odata_type,
        "contentId": content_id,
        "contentLocation": content_location,
        "contentType": content_type,
        "isInline": is_inline,
        "item": item,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.ADD_MAIL_ATTACHMENT, arguments, runtime)


@tool(
    OUTLOOK.tools.CALENDAR_CREATE_EVENT,
)
async def outlook_calendar_create_event(
    runtime: ToolRuntime[AppContext],
    end_datetime: str,
    start_datetime: str,
    subject: str,
    time_zone: str,
    attendees_info: Optional[List[Any]] = [],
    body: Optional[str] = "",
    categories: Optional[List[str]] = [],
    is_html: bool = False,
    is_online_meeting: bool = False,
    location: Optional[str] = "",
    online_meeting_provider: Optional[str] = None,
    show_as: Optional[str] = "busy",
    user_id: Optional[str] = "me",
) -> str:
    """Creates a new Outlook calendar event, ensuring `start_datetime` is chronologically before `end_datetime`."""
    arguments = {
        "end_datetime": end_datetime,
        "start_datetime": start_datetime,
        "subject": subject,
        "time_zone": time_zone,
        "attendees_info": attendees_info or [],
        "body": body,
        "categories": categories or [],
        "is_html": is_html,
        "is_online_meeting": is_online_meeting,
        "location": location,
        "online_meeting_provider": online_meeting_provider,
        "show_as": show_as,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.CALENDAR_CREATE_EVENT, arguments, runtime)


@tool(
    OUTLOOK.tools.CREATE_ATTACHMENT_UPLOAD_SESSION,
)
async def outlook_create_attachment_upload_session(
    runtime: ToolRuntime[AppContext],
    attachment_item: Dict[str, Any],
    message_id: str,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to create an upload session for large (>3 MB) message attachments. Use when you need to upload attachments in chunks."""
    arguments = {
        "attachmentItem": attachment_item,
        "message_id": message_id,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.CREATE_ATTACHMENT_UPLOAD_SESSION, arguments, runtime)


@tool(
    OUTLOOK.tools.CREATE_CALENDAR,
)
async def outlook_create_calendar(
    runtime: ToolRuntime[AppContext],
    name: str,
    color: Optional[str] = None,
    hex_color: Optional[str] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to create a new calendar in the signed-in user's mailbox. Use when organizing events into a separate calendar."""
    arguments = {
        "name": name,
        "color": color,
        "hexColor": hex_color,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.CREATE_CALENDAR, arguments, runtime)


@tool(
    OUTLOOK.tools.CREATE_CONTACT,
)
async def outlook_create_contact(
    runtime: ToolRuntime[AppContext],
    birthday: Optional[str] = "",
    business_phones: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    company_name: Optional[str] = "",
    department: Optional[str] = "",
    display_name: Optional[str] = "",
    email_addresses: Optional[List[Any]] = None,
    given_name: Optional[str] = "",
    home_phone: Optional[str] = "",
    job_title: Optional[str] = "",
    mobile_phone: Optional[str] = "",
    notes: Optional[str] = "",
    office_location: Optional[str] = "",
    surname: Optional[str] = "",
    user_id: Optional[str] = "me",
) -> str:
    """Creates a new contact in a Microsoft Outlook user's contacts folder."""
    arguments = {
        "birthday": birthday,
        "businessPhones": business_phones,
        "categories": categories,
        "companyName": company_name,
        "department": department,
        "displayName": display_name,
        "emailAddresses": email_addresses,
        "givenName": given_name,
        "homePhone": home_phone,
        "jobTitle": job_title,
        "mobilePhone": mobile_phone,
        "notes": notes,
        "officeLocation": office_location,
        "surname": surname,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.CREATE_CONTACT, arguments, runtime)


@tool(
    OUTLOOK.tools.CREATE_CONTACT_FOLDER,
)
async def outlook_create_contact_folder(
    runtime: ToolRuntime[AppContext],
    display_name: str,
    parent_folder_id: Optional[str] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to create a new contact folder in the user's mailbox. Use when needing to organize contacts into custom folders."""
    arguments = {
        "displayName": display_name,
        "parentFolderId": parent_folder_id,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.CREATE_CONTACT_FOLDER, arguments, runtime)


@tool(
    OUTLOOK.tools.CREATE_DRAFT,
)
async def outlook_create_draft(
    runtime: ToolRuntime[AppContext],
    body: str,
    subject: str,
    attachment: Optional[Dict[str, Any]] = None,
    bcc_recipients: Optional[List[str]] = [],
    cc_recipients: Optional[List[str]] = [],
    is_html: bool = False,
    to_recipients: Optional[List[str]] = [],
) -> str:
    """Creates a new Outlook email draft with subject, body, recipients, and an optional attachment. This action creates a standalone draft for new conversations. To create a draft reply to an existing conversation/message, use the CREATE_DRAFT_REPLY action instead."""
    arguments = {
        "body": body,
        "subject": subject,
        "attachment": attachment,
        "bcc_recipients": bcc_recipients or [],
        "cc_recipients": cc_recipients or [],
        "is_html": is_html,
        "to_recipients": to_recipients or [],
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.CREATE_DRAFT, arguments, runtime)


@tool(
    OUTLOOK.tools.CREATE_DRAFT_REPLY,
)
async def outlook_create_draft_reply(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    bcc_emails: Optional[List[str]] = [],
    cc_emails: Optional[List[str]] = [],
    comment: Optional[str] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Creates a draft reply in the specified user's Outlook mailbox to an existing message (identified by a valid `message_id`), optionally including a `comment` and CC/BCC recipients."""
    arguments = {
        "message_id": message_id,
        "bcc_emails": bcc_emails or [],
        "cc_emails": cc_emails or [],
        "comment": comment,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.CREATE_DRAFT_REPLY, arguments, runtime)


@tool(
    OUTLOOK.tools.CREATE_EMAIL_RULE,
)
async def outlook_create_email_rule(
    runtime: ToolRuntime[AppContext],
    actions: Dict[str, Any],
    conditions: Dict[str, Any],
    display_name: str,
    is_enabled: bool = True,
    sequence: Optional[int] = 1,
) -> str:
    """Create email rule filter with conditions and actions"""
    arguments = {
        "actions": actions,
        "conditions": conditions,
        "displayName": display_name,
        "isEnabled": is_enabled,
        "sequence": sequence,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.CREATE_EMAIL_RULE, arguments, runtime)


@tool(
    OUTLOOK.tools.CREATE_MAIL_FOLDER,
)
async def outlook_create_mail_folder(
    runtime: ToolRuntime[AppContext],
    display_name: str,
    is_hidden: bool = False,
    return_existing_if_exists: bool = False,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to create a new mail folder. Use when you need to organize email into a new folder."""
    arguments = {
        "displayName": display_name,
        "isHidden": is_hidden,
        "return_existing_if_exists": return_existing_if_exists,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.CREATE_MAIL_FOLDER, arguments, runtime)


@tool(
    OUTLOOK.tools.CREATE_MASTER_CATEGORY,
)
async def outlook_create_master_category(
    runtime: ToolRuntime[AppContext],
    display_name: str,
    color: Optional[str] = None,
) -> str:
    """Tool to create a new category in the user's master category list. Use after selecting a unique display name."""
    arguments = {
        "displayName": display_name,
        "color": color,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.CREATE_MASTER_CATEGORY, arguments, runtime)


@tool(
    OUTLOOK.tools.DECLINE_EVENT,
)
async def outlook_decline_event(
    runtime: ToolRuntime[AppContext],
    event_id: str,
    comment: Optional[str] = None,
    proposed_new_time: Optional[Dict[str, Any]] = None,
    send_response: bool = True,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to decline an invitation to a calendar event. Use when the user wants to decline a meeting or event invitation. The API returns 202 Accepted with no content on success."""
    arguments = {
        "event_id": event_id,
        "comment": comment,
        "proposedNewTime": proposed_new_time,
        "sendResponse": send_response,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.DECLINE_EVENT, arguments, runtime)


@tool(
    OUTLOOK.tools.DELETE_CONTACT,
)
async def outlook_delete_contact(
    runtime: ToolRuntime[AppContext],
    contact_id: str,
    user_id: Optional[str] = "me",
) -> str:
    """Permanently deletes an existing contact, using its `contact_id` (obtainable via 'List User Contacts' or 'Get Contact'), from the Outlook contacts of the user specified by `user_id`."""
    arguments = {
        "contact_id": contact_id,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.DELETE_CONTACT, arguments, runtime)


@tool(
    OUTLOOK.tools.DELETE_EMAIL_RULE,
)
async def outlook_delete_email_rule(
    runtime: ToolRuntime[AppContext],
    rule_id: str,
) -> str:
    """Delete an email rule"""
    arguments = {
        "ruleId": rule_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.DELETE_EMAIL_RULE, arguments, runtime)


@tool(
    OUTLOOK.tools.DELETE_EVENT,
)
async def outlook_delete_event(
    runtime: ToolRuntime[AppContext],
    event_id: str,
    send_notifications: bool = True,
    user_id: Optional[str] = "me",
) -> str:
    """Deletes an existing calendar event, identified by its unique `event_id`, from a specified user's Microsoft Outlook calendar, with an option to send cancellation notifications to attendees."""
    arguments = {
        "event_id": event_id,
        "send_notifications": send_notifications,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.DELETE_EVENT, arguments, runtime)


@tool(
    OUTLOOK.tools.DELETE_MAIL_FOLDER,
)
async def outlook_delete_mail_folder(
    runtime: ToolRuntime[AppContext],
    folder_id: str,
    user_id: Optional[str] = "me",
) -> str:
    """Delete a mail folder from the user's mailbox. Use when you need to remove an existing mail folder."""
    arguments = {
        "folder_id": folder_id,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.DELETE_MAIL_FOLDER, arguments, runtime)


@tool(
    OUTLOOK.tools.DELETE_MESSAGE,
)
async def outlook_delete_message(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to permanently delete an Outlook email message by its message_id. Use when removing unwanted messages, cleaning up drafts, or performing mailbox maintenance."""
    arguments = {
        "message_id": message_id,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.DELETE_MESSAGE, arguments, runtime)


@tool(
    OUTLOOK.tools.DOWNLOAD_OUTLOOK_ATTACHMENT,
)
async def outlook_download_attachment(
    runtime: ToolRuntime[AppContext],
    attachment_id: str,
    file_name: str,
    message_id: str,
    user_id: Optional[str] = "me",
) -> str:
    """
    Downloads a specific file attachment from an email message in Outlook and uploads it to S3.
    Returns the workspace-relative path where the agent can access the file.
    """
    arguments = {
        "attachment_id": attachment_id,
        "file_name": file_name,
        "message_id": message_id,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    result = await execute_composio_tool(OUTLOOK.tools.DOWNLOAD_OUTLOOK_ATTACHMENT, arguments, runtime)
    
    try:
        result_data = json.loads(result)
        
        attachment_bytes = None
        
        if isinstance(result_data, dict):
            if "data" in result_data:
                attachment_data_b64 = result_data["data"]
                attachment_bytes = base64.b64decode(attachment_data_b64)
            elif "file" in result_data:
                local_file_path = result_data["file"]
                
                if os.path.exists(local_file_path):
                    with open(local_file_path, "rb") as f:
                        attachment_bytes = f.read()
                else:
                    return json.dumps({
                        "success": False,
                        "message": f"Local file not found: {local_file_path}",
                        "raw_result": result,
                    }, indent=2)
        
        if attachment_bytes:
            from datetime import datetime
            from src.s3_client import S3Client
            
            from src.utils.config import get_thread_id_from_config
            thread_id = get_thread_id_from_config()
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
                    "source": "outlook",
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


@tool(
    OUTLOOK.tools.FIND_MEETING_TIMES,
)
async def outlook_find_meeting_times(
    runtime: ToolRuntime[AppContext],
    attendees: Optional[List[str]] = None,
    is_organizer_optional: Optional[bool] = None,
    location_constraint: Optional[Dict[str, Any]] = None,
    max_candidates: Optional[int] = None,
    meeting_duration: Optional[str] = None,
    minimum_attendee_percentage: Optional[str] = None,
    prefer_timezone: Optional[str] = None,
    return_suggestion_reasons: Optional[bool] = None,
    time_constraint: Optional[Dict[str, Any]] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Suggests meeting times based on organizer and attendee availability, time constraints, and duration requirements. Use when you need to find optimal meeting slots across multiple participants' schedules."""
    arguments = {
        "attendees": attendees,
        "isOrganizerOptional": is_organizer_optional,
        "locationConstraint": location_constraint,
        "maxCandidates": max_candidates,
        "meetingDuration": meeting_duration,
        "minimumAttendeePercentage": minimum_attendee_percentage,
        "prefer_timezone": prefer_timezone,
        "returnSuggestionReasons": return_suggestion_reasons,
        "timeConstraint": time_constraint,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.FIND_MEETING_TIMES, arguments, runtime)


@tool(
    OUTLOOK.tools.FORWARD_MESSAGE,
)
async def outlook_forward_message(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    to_recipients: List[str],
    comment: Optional[str] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to forward a message. Use when you need to send an existing email to new recipients."""
    arguments = {
        "message_id": message_id,
        "to_recipients": to_recipients,
        "comment": comment,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.FORWARD_MESSAGE, arguments, runtime)


@tool(
    OUTLOOK.tools.GET_CALENDAR_VIEW,
)
async def outlook_get_calendar_view(
    runtime: ToolRuntime[AppContext],
    end_datetime: str,
    start_datetime: str,
    calendar_id: Optional[str] = None,
    select: Optional[List[str]] = None,
    timezone: Optional[str] = "UTC",
    top: int = 100,
    user_id: Optional[str] = "me",
) -> str:
    """Get events ACTIVE during a time window (includes multi-day events). Use for \"what's on my calendar today/this week\" or availability checks. Returns events overlapping the time range. For keyword search or filters by category, use LIST_EVENTS instead."""
    arguments = {
        "end_datetime": end_datetime,
        "start_datetime": start_datetime,
        "calendar_id": calendar_id,
        "select": select,
        "timezone": timezone,
        "top": top,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_CALENDAR_VIEW, arguments, runtime)


@tool(
    OUTLOOK.tools.GET_CONTACT,
)
async def outlook_get_contact(
    runtime: ToolRuntime[AppContext],
    contact_id: str,
    user_id: Optional[str] = "me",
) -> str:
    """Retrieves a specific Outlook contact by its `contact_id` from the contacts of a specified `user_id` (defaults to 'me' for the authenticated user)."""
    arguments = {
        "contact_id": contact_id,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_CONTACT, arguments, runtime)


@tool(
    OUTLOOK.tools.GET_CONTACT_FOLDERS,
)
async def outlook_get_contact_folders(
    runtime: ToolRuntime[AppContext],
    expand: Optional[List[str]] = None,
    filter: Optional[str] = None,
    orderby: Optional[List[str]] = None,
    select: Optional[List[str]] = None,
    skip: Optional[int] = None,
    top: Optional[int] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to retrieve a list of contact folders in the signed-in user's mailbox. Use after authentication when you need to browse or select among contact folders."""
    arguments = {
        "expand": expand,
        "filter": filter,
        "orderby": orderby,
        "select": select,
        "skip": skip,
        "top": top,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_CONTACT_FOLDERS, arguments, runtime)


@tool(
    OUTLOOK.tools.GET_EVENT,
)
async def outlook_get_event(
    runtime: ToolRuntime[AppContext],
    event_id: str,
    user_id: Optional[str] = "me",
) -> str:
    """Retrieves the full details of a specific calendar event by its ID from a user's Outlook calendar, provided the event exists."""
    arguments = {
        "event_id": event_id,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_EVENT, arguments, runtime)


@tool(
    OUTLOOK.tools.GET_MAILBOX_SETTINGS,
)
async def outlook_get_mailbox_settings(
    runtime: ToolRuntime[AppContext],
    expand: Optional[List[str]] = None,
    select: Optional[List[str]] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to retrieve mailbox settings. Use when you need to view settings such as automatic replies, time zone, and working hours for the signed-in or specified user."""
    arguments = {
        "expand": expand,
        "select": select,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_MAILBOX_SETTINGS, arguments, runtime)


@tool(
    OUTLOOK.tools.GET_MAIL_DELTA,
)
async def outlook_get_mail_delta(
    runtime: ToolRuntime[AppContext],
    delta_token: Optional[str] = None,
    expand: Optional[List[str]] = None,
    folder_id: Optional[str] = None,
    select: Optional[List[str]] = None,
    skip_token: Optional[str] = None,
    top: Optional[int] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Retrieve incremental changes (delta) of messages in a mailbox. FIRST RUN: Returns ALL messages in folder (use top=50 to limit). Response has @odata.deltaLink. SUBSEQUENT: Pass stored deltaLink to get only NEW/UPDATED/DELETED messages since last sync. Properties available: id, subject, from, receivedDateTime, isRead, etc. NOT available: internetMessageHeaders, full body, attachment content (response size limits)."""
    arguments = {
        "delta_token": delta_token,
        "expand": expand,
        "folder_id": folder_id,
        "select": select,
        "skip_token": skip_token,
        "top": top,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_MAIL_DELTA, arguments, runtime)


@tool(
    OUTLOOK.tools.GET_MAIL_TIPS,
)
async def outlook_get_mail_tips(
    runtime: ToolRuntime[AppContext],
    email_addresses: List[str],
    mail_tips_options: List[str],
    user_id: Optional[str] = "me",
) -> str:
    """Tool to retrieve mail tips such as automatic replies and mailbox full status. Use when you need to check recipient status before sending mail."""
    arguments = {
        "EmailAddresses": email_addresses,
        "MailTipsOptions": mail_tips_options,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_MAIL_TIPS, arguments, runtime)


@tool(
    OUTLOOK.tools.GET_MASTER_CATEGORIES,
)
async def outlook_get_master_categories(
    runtime: ToolRuntime[AppContext],
    filter: Optional[str] = None,
    orderby: Optional[List[str]] = None,
    select: Optional[List[str]] = None,
    skip: Optional[int] = None,
    top: Optional[int] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to retrieve the user's master category list. Use when you need to get all categories defined for the user."""
    arguments = {
        "filter": filter,
        "orderby": orderby,
        "select": select,
        "skip": skip,
        "top": top,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_MASTER_CATEGORIES, arguments, runtime)


@tool(
    OUTLOOK.tools.GET_MESSAGE,
)
async def outlook_get_message(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    select: Optional[List[str]] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Retrieves a specific email message by its ID from the specified user's Outlook mailbox. Use the 'select' parameter to include specific fields like 'internetMessageHeaders' for filtering automated emails."""
    arguments = {
        "message_id": message_id,
        "select": select,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_MESSAGE, arguments, runtime)

@tool(
    OUTLOOK.tools.GET_PROFILE,
)
async def outlook_get_profile(
    runtime: ToolRuntime[AppContext],
    user_id: Optional[str] = "me",
) -> str:
    """Retrieves the Microsoft Outlook profile for a specified user."""
    arguments = {
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_PROFILE, arguments, runtime)


@tool(
    OUTLOOK.tools.GET_SCHEDULE,
)
async def outlook_get_schedule(
    runtime: ToolRuntime[AppContext],
    end_time: Dict[str, Any],
    schedules: List[str],
    start_time: Dict[str, Any],
    availability_view_interval: Optional[str] = "30",
) -> str:
    """Retrieves free/busy schedule information for specified email addresses within a defined time window."""
    arguments = {
        "EndTime": end_time,
        "Schedules": schedules,
        "StartTime": start_time,
        "availabilityViewInterval": availability_view_interval,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_SCHEDULE, arguments, runtime)


@tool(
    OUTLOOK.tools.GET_SUPPORTED_LANGUAGES,
)
async def outlook_get_supported_languages(
    runtime: ToolRuntime[AppContext],
    user_id: Optional[str] = "me",
) -> str:
    """Tool to retrieve supported languages in the user's mailbox. Use when you need to display or select from available mailbox languages."""
    arguments = {
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_SUPPORTED_LANGUAGES, arguments, runtime)


@tool(
    OUTLOOK.tools.GET_SUPPORTED_TIME_ZONES,
)
async def outlook_get_supported_time_zones(
    runtime: ToolRuntime[AppContext],
    time_zone_standard: Optional[str] = None,
) -> str:
    """Tool to retrieve supported time zones in the user's mailbox. Use when you need a list of time zones to display or choose from for event scheduling."""
    arguments = {
        "timeZoneStandard": time_zone_standard,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.GET_SUPPORTED_TIME_ZONES, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_CALENDARS,
)
async def outlook_list_calendars(
    runtime: ToolRuntime[AppContext],
    filter: Optional[str] = None,
    orderby: Optional[List[str]] = None,
    select: Optional[List[str]] = None,
    skip: Optional[int] = None,
    top: Optional[int] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to list calendars in the signed-in user's mailbox. Use when you need to retrieve calendars with optional OData queries."""
    arguments = {
        "filter": filter,
        "orderby": orderby,
        "select": select,
        "skip": skip,
        "top": top,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_CALENDARS, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_CHAT_MESSAGES,
)
async def outlook_list_chat_messages(
    runtime: ToolRuntime[AppContext],
    chat_id: str,
    filter: Optional[str] = None,
    orderby: Optional[str] = None,
    top: Optional[int] = None,
) -> str:
    """Tool to list messages in a Teams chat. Use when you need message IDs to select a specific message for further actions."""
    arguments = {
        "chat_id": chat_id,
        "filter": filter,
        "orderby": orderby,
        "top": top,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_CHAT_MESSAGES, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_CHATS,
)
async def outlook_list_chats(
    runtime: ToolRuntime[AppContext],
    expand: Optional[str] = None,
    filter: Optional[str] = None,
    orderby: Optional[str] = None,
    top: Optional[int] = None,
) -> str:
    """Tool to list Teams chats. Use when you need chat IDs and topics to select a chat for further actions."""
    arguments = {
        "expand": expand,
        "filter": filter,
        "orderby": orderby,
        "top": top,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_CHATS, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_CHILD_MAIL_FOLDERS,
)
async def outlook_list_child_mail_folders(
    runtime: ToolRuntime[AppContext],
    parent_folder_id: str,
    filter: Optional[str] = None,
    include_hidden_folders: bool = False,
    select: Optional[List[str]] = None,
    top: Optional[int] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to list subfolders (childFolders) under a specified Outlook mail folder. Use when navigating nested folder hierarchies or checking if a folder has subfolders."""
    arguments = {
        "parent_folder_id": parent_folder_id,
        "filter": filter,
        "include_hidden_folders": include_hidden_folders,
        "select": select,
        "top": top,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_CHILD_MAIL_FOLDERS, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_CONTACTS,
)
async def outlook_list_contacts(
    runtime: ToolRuntime[AppContext],
    contact_folder_id: Optional[str] = None,
    filter: Optional[str] = None,
    orderby: Optional[List[str]] = [],
    select: Optional[List[str]] = [],
    top: int = 10,
    user_id: Optional[str] = "me",
) -> str:
    """Retrieves a user's Microsoft Outlook contacts, from the default or a specified contact folder."""
    arguments = {
        "contact_folder_id": contact_folder_id,
        "filter": filter,
        "orderby": orderby or [],
        "select": select or [],
        "top": top,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_CONTACTS, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_EMAIL_RULES,
)
async def outlook_list_email_rules(
    runtime: ToolRuntime[AppContext],
    top: Optional[int] = None,
) -> str:
    """List all email rules from inbox"""
    arguments = {
        "top": top,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_EMAIL_RULES, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_EVENT_ATTACHMENTS,
)
async def outlook_list_event_attachments(
    runtime: ToolRuntime[AppContext],
    event_id: str,
    filter: Optional[str] = None,
    orderby: Optional[List[str]] = None,
    select: Optional[List[str]] = None,
    skip: Optional[int] = None,
    top: Optional[int] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to list attachments for a specific Outlook calendar event. Use when you have an event ID and need to view its attachments."""
    arguments = {
        "event_id": event_id,
        "filter": filter,
        "orderby": orderby,
        "select": select,
        "skip": skip,
        "top": top,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_EVENT_ATTACHMENTS, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_EVENTS,
)
async def outlook_list_events(
    runtime: ToolRuntime[AppContext],
    calendar_id: Optional[str] = None,
    expand_recurring_events: bool = False,
    filter: Optional[str] = "",
    orderby: Optional[List[str]] = None,
    select: Optional[List[str]] = None,
    skip: int = 0,
    timezone: Optional[str] = "UTC",
    top: int = 10,
    user_id: Optional[str] = "me",
) -> str:
    """Retrieves events from a user's Outlook calendar via Microsoft Graph API. Supports primary/secondary/shared calendars, pagination, filtering, property selection, sorting, and timezone specification. Use calendar_id to access non-primary calendars."""
    arguments = {
        "calendar_id": calendar_id,
        "expand_recurring_events": expand_recurring_events,
        "filter": filter,
        "orderby": orderby,
        "select": select,
        "skip": skip,
        "timezone": timezone,
        "top": top,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_EVENTS, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_MAIL_FOLDERS,
)
async def outlook_list_mail_folders(
    runtime: ToolRuntime[AppContext],
    include_hidden_folders: bool = False,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to list a user's top-level mail folders. Use when you need folders like Inbox, Drafts, Sent Items; set include_hidden_folders=True to include hidden folders."""
    arguments = {
        "include_hidden_folders": include_hidden_folders,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_MAIL_FOLDERS, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_MESSAGES,
)
async def outlook_list_messages(
    runtime: ToolRuntime[AppContext],
    categories: Optional[List[str]] = None,
    conversation_id: Optional[str] = None,
    folder: Optional[str] = "inbox",
    from_address: Optional[str] = None,
    has_attachments: Optional[bool] = None,
    importance: Optional[str] = None,
    is_read: Optional[bool] = None,
    orderby: Optional[List[str]] = [],
    received_date_time_ge: Optional[str] = None,
    received_date_time_gt: Optional[str] = None,
    received_date_time_le: Optional[str] = None,
    received_date_time_lt: Optional[str] = None,
    select: Optional[List[str]] = None,
    sent_date_time_gt: Optional[str] = None,
    sent_date_time_lt: Optional[str] = None,
    skip: int = 0,
    subject: Optional[str] = None,
    subject_contains: Optional[str] = None,
    subject_endswith: Optional[str] = None,
    subject_startswith: Optional[str] = None,
    top: int = 10,
    user_id: Optional[str] = "me",
) -> str:
    """Retrieves a list of email messages from a specified mail folder in an Outlook mailbox, with options for filtering (including by conversationId to get all messages in a thread), pagination, and sorting; ensure 'user_id' and 'folder' are valid, and all date/time strings are in ISO 8601 format."""
    arguments = {
        "categories": categories,
        "conversationId": conversation_id,
        "folder": folder,
        "from_address": from_address,
        "has_attachments": has_attachments,
        "importance": importance,
        "is_read": is_read,
        "orderby": orderby,
        "received_date_time_ge": received_date_time_ge,
        "received_date_time_gt": received_date_time_gt,
        "received_date_time_le": received_date_time_le,
        "received_date_time_lt": received_date_time_lt,
        "select": select,
        "sent_date_time_gt": sent_date_time_gt,
        "sent_date_time_lt": sent_date_time_lt,
        "skip": skip,
        "subject": subject,
        "subject_contains": subject_contains,
        "subject_endswith": subject_endswith,
        "subject_startswith": subject_startswith,
        "top": top,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_MESSAGES, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_OUTLOOK_ATTACHMENTS,
)
async def outlook_list_attachments(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    user_id: Optional[str] = "me",
) -> str:
    """Lists metadata (like name, size, and type, but not `contentBytes`) for all attachments of a specified Outlook email message."""
    arguments = {
        "message_id": message_id,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_OUTLOOK_ATTACHMENTS, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_REMINDERS,
)
async def outlook_list_reminders(
    runtime: ToolRuntime[AppContext],
    end_date_time: str,
    start_date_time: str,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to retrieve reminders for events occurring within a specified time range. Use when you need to see upcoming reminders between two datetimes."""
    arguments = {
        "endDateTime": end_date_time,
        "startDateTime": start_date_time,
        "userId": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_REMINDERS, arguments, runtime)


@tool(
    OUTLOOK.tools.LIST_USERS,
)
async def outlook_list_users(
    runtime: ToolRuntime[AppContext],
    filter: Optional[str] = None,
    select: Optional[List[str]] = None,
    skip: Optional[int] = None,
    top: Optional[int] = None,
) -> str:
    """Tool to list users in Microsoft Entra ID. Use when you need to retrieve a paginated list of users, optionally filtering or selecting specific properties."""
    arguments = {
        "filter": filter,
        "select": select,
        "skip": skip,
        "top": top,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.LIST_USERS, arguments, runtime)


@tool(
    OUTLOOK.tools.MOVE_MESSAGE,
)
async def outlook_move_message(
    runtime: ToolRuntime[AppContext],
    destination_id: str,
    message_id: str,
    user_id: Optional[str] = "me",
) -> str:
    """Move a message to another folder within the specified user's mailbox. This creates a new copy of the message in the destination folder and removes the original message."""
    arguments = {
        "destination_id": destination_id,
        "message_id": message_id,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.MOVE_MESSAGE, arguments, runtime)


@tool(
    OUTLOOK.tools.PERMANENT_DELETE_MESSAGE,
)
async def outlook_permanent_delete_message(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    mail_folder_id: Optional[str] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Permanently deletes an Outlook message by moving it to the Purges folder in the dumpster. Unlike standard DELETE, this action makes the message unrecoverable by the user. IMPORTANT: This is NOT the same as DELETE - permanentDelete is irreversible and availability differs by national cloud deployments (not available in US Government L4, L5 (DOD), or China (21Vianet))."""
    arguments = {
        "message_id": message_id,
        "mail_folder_id": mail_folder_id,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.PERMANENT_DELETE_MESSAGE, arguments, runtime)


@tool(
    OUTLOOK.tools.PIN_MESSAGE,
)
async def outlook_pin_message(
    runtime: ToolRuntime[AppContext],
    chat_id: str,
    message_url: str,
) -> str:
    """Tool to pin a message in an Outlook chat. Use when you want to mark an important message for quick access."""
    arguments = {
        "chat_id": chat_id,
        "message_url": message_url,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.PIN_MESSAGE, arguments, runtime)


@tool(
    OUTLOOK.tools.QUERY_EMAILS,
)
async def outlook_query_emails(
    runtime: ToolRuntime[AppContext],
    filter: Optional[str] = None,
    folder: Optional[str] = "inbox",
    orderby: Optional[str] = "receivedDateTime desc",
    select: Optional[List[str]] = None,
    skip: int = 0,
    top: int = 100,
    user_id: Optional[str] = "me",
) -> str:
    """Primary tool for querying Outlook emails with custom OData filters. Build precise server-side filters for dates, read status, importance, subjects, attachments, and conversations. Best for structured queries on message metadata. Returns up to 100 messages per request with pagination support. • Server-side filters: dates, importance, isRead, hasAttachments, subjects, conversationId • CRITICAL: Always check response['@odata.nextLink'] for pagination • Limitations: Recipient/sender/category filters require SEARCH_MESSAGES • For keyword/body search: Use SEARCH_MESSAGES (KQL syntax)"""
    arguments = {
        "filter": filter,
        "folder": folder,
        "orderby": orderby,
        "select": select,
        "skip": skip,
        "top": top,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.QUERY_EMAILS, arguments, runtime)


@tool(
    OUTLOOK.tools.REPLY_EMAIL,
)
async def outlook_reply_email(
    runtime: ToolRuntime[AppContext],
    comment: str,
    message_id: str,
    bcc_emails: Optional[List[str]] = [],
    cc_emails: Optional[List[str]] = [],
    user_id: Optional[str] = "me",
) -> str:
    """Sends a plain text reply to an Outlook email message, identified by `message_id`, allowing optional CC and BCC recipients."""
    arguments = {
        "comment": comment,
        "message_id": message_id,
        "bcc_emails": bcc_emails or [],
        "cc_emails": cc_emails or [],
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.REPLY_EMAIL, arguments, runtime)


@tool(
    OUTLOOK.tools.SEARCH_MESSAGES,
)
async def outlook_search_messages(
    runtime: ToolRuntime[AppContext],
    enable_top_results: bool = False,
    from_email: Optional[str] = None,
    from_index: int = 0,
    has_attachments: Optional[bool] = None,
    query: Optional[str] = "",
    size: int = 25,
    subject: Optional[str] = None,
) -> str:
    """Search Outlook messages using powerful KQL syntax. Supports sender (from:), recipient (to:, cc:), subject, date filters (received:, sent:), attachments, and boolean logic. Only works with Microsoft 365/Enterprise accounts (no @hotmail.com/@outlook.com). Examples: 'from:user@example.com AND received>=2025-10-01', 'to:info@jcdn.nl AND subject:invoice', 'received>today-30 AND hasattachment:yes'"""
    arguments = {
        "enable_top_results": enable_top_results,
        "fromEmail": from_email,
        "from_index": from_index,
        "hasAttachments": has_attachments,
        "query": query,
        "size": size,
        "subject": subject,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.SEARCH_MESSAGES, arguments, runtime)


@tool(
    OUTLOOK.tools.SEND_DRAFT,
)
async def outlook_send_draft(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    user_id: Optional[str] = "me",
) -> str:
    """Tool to send an existing draft message. Use after creating a draft when you want to deliver it to recipients immediately. Example: Send a draft message with ID 'AAMkAG…'."""
    arguments = {
        "message_id": message_id,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.SEND_DRAFT, arguments, runtime)


@tool(
    OUTLOOK.tools.SEND_EMAIL,
)
async def outlook_send_email(
    runtime: ToolRuntime[AppContext],
    body: str,
    subject: str,
    to: str,
    attachment_path: Optional[str] = None,
    bcc_emails: Optional[List[str]] = [],
    cc_emails: Optional[List[str]] = [],
    from_address: Optional[str] = None,
    is_html: bool = False,
    save_to_sent_items: bool = True,
    to_name: Optional[str] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Sends an email with subject, body, recipients, and an optional attachment via Microsoft Graph API."""
    
    # Process attachment if provided
    composio_attachment = None
    temp_file_path = None
    
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
            
            # Write to temp file preserving the original filename and extension
            import tempfile
            temp_dir = tempfile.gettempdir()
            temp_file_path = os.path.join(temp_dir, file_name)
            
            # Write binary content to temp file
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
                "message": f"Error processing attachment '{attachment_path}': {str(e)}"
            }, indent=2)
    
    arguments = {
        "body": body,
        "subject": subject,
        "to": to,
        "bcc_emails": bcc_emails or [],
        "cc_emails": cc_emails or [],
        "from_address": from_address,
        "is_html": is_html,
        "save_to_sent_items": save_to_sent_items,
        "to_name": to_name,
        "user_id": user_id,
    }
    
    if composio_attachment:
        arguments["attachment"] = composio_attachment
    
    arguments = {k: v for k, v in arguments.items() if v is not None}
    
    # # Request user confirmation before sending
    # confirmation_payload = {
    #     "action": "confirm_email_send",
    #     "provider": "outlook",
    #     "recipient": to,
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
        result = await execute_composio_tool(OUTLOOK.tools.SEND_EMAIL, arguments, runtime)
        return result
    finally:
        # Clean up temp file
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
            except Exception:
                pass


@tool(
    OUTLOOK.tools.UPDATE_CALENDAR_EVENT,
)
async def outlook_update_calendar_event(
    runtime: ToolRuntime[AppContext],
    event_id: str,
    attendees: Optional[List[str]] = None,
    body: Optional[Dict[str, Any]] = None,
    categories: Optional[List[str]] = None,
    end_datetime: Optional[str] = None,
    location: Optional[str] = None,
    show_as: Optional[str] = None,
    start_datetime: Optional[str] = None,
    subject: Optional[str] = None,
    time_zone: Optional[str] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Updates specified fields of an existing Outlook calendar event. Implementation note: To avoid unintentionally clearing properties, the action first fetches the existing event, merges only the provided fields, and then PATCHes the merged updates. Unspecified fields remain unchanged."""
    arguments = {
        "event_id": event_id,
        "attendees": attendees,
        "body": body,
        "categories": categories,
        "end_datetime": end_datetime,
        "location": location,
        "show_as": show_as,
        "start_datetime": start_datetime,
        "subject": subject,
        "time_zone": time_zone,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.UPDATE_CALENDAR_EVENT, arguments, runtime)


@tool(
    OUTLOOK.tools.UPDATE_CONTACT,
)
async def outlook_update_contact(
    runtime: ToolRuntime[AppContext],
    contact_id: str,
    birthday: Optional[str] = "",
    business_phones: Optional[List[str]] = None,
    categories: Optional[List[str]] = None,
    company_name: Optional[str] = "",
    department: Optional[str] = "",
    display_name: Optional[str] = "",
    email_addresses: Optional[List[Any]] = None,
    given_name: Optional[str] = "",
    home_phones: Optional[List[str]] = None,
    job_title: Optional[str] = "",
    mobile_phone: Optional[str] = "",
    notes: Optional[str] = "",
    office_location: Optional[str] = "",
    surname: Optional[str] = "",
    user_id: Optional[str] = "me",
) -> str:
    """Updates an existing Outlook contact, identified by `contact_id` for the specified `user_id`, requiring at least one other field to be modified."""
    arguments = {
        "contact_id": contact_id,
        "birthday": birthday,
        "businessPhones": business_phones,
        "categories": categories,
        "companyName": company_name,
        "department": department,
        "displayName": display_name,
        "emailAddresses": email_addresses,
        "givenName": given_name,
        "homePhones": home_phones,
        "jobTitle": job_title,
        "mobilePhone": mobile_phone,
        "notes": notes,
        "officeLocation": office_location,
        "surname": surname,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.UPDATE_CONTACT, arguments, runtime)


@tool(
    OUTLOOK.tools.UPDATE_EMAIL,
)
async def outlook_update_email(
    runtime: ToolRuntime[AppContext],
    message_id: str,
    bcc_recipients: Optional[List[Any]] = None,
    body: Optional[Dict[str, Any]] = None,
    cc_recipients: Optional[List[Any]] = None,
    importance: Optional[str] = "normal",
    is_read: Optional[bool] = None,
    subject: Optional[str] = None,
    to_recipients: Optional[List[Any]] = None,
    user_id: Optional[str] = "me",
) -> str:
    """Updates specified properties of an existing email message; `message_id` must identify a valid message within the specified `user_id`'s mailbox."""
    arguments = {
        "message_id": message_id,
        "bcc_recipients": bcc_recipients,
        "body": body,
        "cc_recipients": cc_recipients,
        "importance": importance,
        "is_read": is_read,
        "subject": subject,
        "to_recipients": to_recipients,
        "user_id": user_id,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.UPDATE_EMAIL, arguments, runtime)


@tool(
    OUTLOOK.tools.UPDATE_EMAIL_RULE,
)
async def outlook_update_email_rule(
    runtime: ToolRuntime[AppContext],
    rule_id: str,
    actions: Optional[Dict[str, Any]] = None,
    conditions: Optional[Dict[str, Any]] = None,
    display_name: Optional[str] = None,
    is_enabled: Optional[bool] = None,
    sequence: Optional[int] = None,
) -> str:
    """Update an existing email rule"""
    arguments = {
        "ruleId": rule_id,
        "actions": actions,
        "conditions": conditions,
        "displayName": display_name,
        "isEnabled": is_enabled,
        "sequence": sequence,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.UPDATE_EMAIL_RULE, arguments, runtime)


@tool(
    OUTLOOK.tools.UPDATE_MAILBOX_SETTINGS,
)
async def outlook_update_mailbox_settings(
    runtime: ToolRuntime[AppContext],
    automatic_replies_setting: Optional[Dict[str, Any]] = None,
    language: Optional[Dict[str, Any]] = None,
    time_zone: Optional[str] = None,
    working_hours: Optional[Dict[str, Any]] = None,
) -> str:
    """Tool to update mailbox settings for the signed-in user. Use when you need to configure automatic replies, default time zone, language, or working hours. Example: schedule automatic replies for vacation."""
    arguments = {
        "automaticRepliesSetting": automatic_replies_setting,
        "language": language,
        "timeZone": time_zone,
        "workingHours": working_hours,
    }
    arguments = {k: v for k, v in arguments.items() if v is not None}
    return await execute_composio_tool(OUTLOOK.tools.UPDATE_MAILBOX_SETTINGS, arguments, runtime)



# Export all Outlook tools
outlook_tools = [
    # Message operations
    outlook_query_emails,
    outlook_list_messages,
    outlook_get_message,
    outlook_search_messages,
    outlook_send_email,
    outlook_reply_email,
    outlook_forward_message,
    # outlook_delete_message,
    # outlook_permanent_delete_message,
    # outlook_move_message,
    # outlook_pin_message,
    # __________________________________________________________
    # Draft operations
    outlook_create_draft,
    outlook_create_draft_reply,
    outlook_send_draft,
    # __________________________________________________________
    # Attachment operations
    outlook_list_attachments,
    outlook_download_attachment,
    outlook_add_mail_attachment,
    outlook_create_attachment_upload_session,
    # __________________________________________________________
    # Mail delta (for syncing/changes)
    outlook_get_mail_delta,
    # __________________________________________________________
    # Calendar operations (commented out - not email-related)
    # outlook_add_event_attachment,
    # outlook_calendar_create_event,
    # outlook_create_calendar,
    # outlook_decline_event,
    # outlook_get_calendar_view,
    # outlook_get_event,
    # outlook_get_schedule,
    # outlook_list_calendars,
    # outlook_list_event_attachments,
    # outlook_list_events,
    # outlook_update_calendar_event,
    # outlook_find_meeting_times,
    # __________________________________________________________
    # Contact operations (commented out - not email-related)
    # outlook_create_contact,
    # outlook_create_contact_folder,
    # outlook_delete_contact,
    # outlook_get_contact,
    # outlook_get_contact_folders,
    # outlook_list_contacts,
    # outlook_update_contact,
    # __________________________________________________________
    # Folder operations (commented out - organization)
    # outlook_create_mail_folder,
    # outlook_delete_mail_folder,
    # outlook_list_mail_folders,
    # outlook_list_child_mail_folders,
    # __________________________________________________________
    # Email rules (commented out - organization)
    # outlook_create_email_rule,
    # outlook_delete_email_rule,
    # outlook_list_email_rules,
    # outlook_update_email_rule,
    # __________________________________________________________
    # Categories (commented out - organization)
    # outlook_create_master_category,
    # outlook_get_master_categories,
    # __________________________________________________________
    # Settings and profile (commented out - not email operations)
    # outlook_get_mailbox_settings,
    # outlook_get_mail_tips,
    # outlook_get_profile,
    # outlook_get_supported_languages,
    # outlook_get_supported_time_zones,
    # outlook_update_mailbox_settings,
    # __________________________________________________________
    # Chat operations (commented out - not email)
    # outlook_list_chat_messages,
    # outlook_list_chats,
    # __________________________________________________________
    # Other operations (commented out)
    # outlook_list_reminders,
    # outlook_list_users,
    # outlook_update_email,
]
