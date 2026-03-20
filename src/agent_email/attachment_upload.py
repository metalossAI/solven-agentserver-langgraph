"""Shared attachment upload: parse Composio response and upload via backend (sandbox or S3)."""

from __future__ import annotations

import base64
import json
import os
from datetime import datetime
from typing import Any, Dict, Optional, Tuple


def parse_composio_attachment_response(result: str) -> Tuple[Optional[bytes], Optional[str]]:
    """Extract attachment bytes from Composio tool result (Gmail/Outlook get attachment).

    Returns:
        (bytes, None) on success, (None, error_message) on failure.
    """
    if isinstance(result, str) and result.strip().startswith("Error"):
        return None, result.strip()
    try:
        result_data = json.loads(result)
    except json.JSONDecodeError as e:
        preview = (result or "")[:200].replace("\n", " ")
        return None, f"Failed to parse attachment response as JSON: {e}. Preview: {preview!r}"

    attachment_bytes = None
    if isinstance(result_data, str):
        try:
            attachment_bytes = base64.b64decode(result_data)
        except Exception:
            pass
    elif isinstance(result_data, dict):
        raw = result_data.get("data") or result_data.get("body") or result_data.get("content")
        if isinstance(raw, dict):
            raw = raw.get("data") or raw.get("dataBase64")
        if isinstance(raw, str):
            try:
                attachment_bytes = base64.b64decode(raw)
            except Exception:
                pass
        if attachment_bytes is None:
            raw = result_data.get("file") or result_data.get("filePath")
            if isinstance(raw, str) and os.path.exists(raw):
                with open(raw, "rb") as f:
                    attachment_bytes = f.read()
            elif isinstance(raw, str):
                return None, f"Local file not found: {raw}"

    if attachment_bytes:
        return attachment_bytes, None
    return None, "Attachment data not found in response (expected 'data' or 'file' field)"


async def upload_attachment_to_backend(
    backend: Any,
    file_name: str,
    content: bytes,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    """Upload attachment bytes via ``backend.aupload_files``.

    Uses a single logical path ``/adjuntos/...``; SandboxBackend and SolvenS3Backend resolve it
    internally (workspace root vs ``/workspace`` mount).

    metadata is echoed in the result dict (message_id, attachment_id, etc.).
    """
    message_id = metadata.get("message_id", "")
    attachment_id = metadata.get("attachment_id", "")
    name_for_path = (file_name or "").strip().lstrip("/")
    safe_filename = name_for_path.replace(" ", "_").replace("/", "_") or "attachment"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    upload_path = f"/adjuntos/{timestamp}_{safe_filename}"

    responses = await backend.aupload_files([(upload_path, content)])
    resp = responses[0] if responses else None
    if resp and resp.error is None:
        return {
            "success": True,
            "path": upload_path,
            "file_name": file_name,
            "message_id": message_id,
            "attachment_id": attachment_id,
            "size_bytes": len(content),
            # Keep user-facing output free of local/absolute filesystem locations.
            "message": "Attachment downloaded successfully",
        }
    upload_error = resp.error if resp else "no response"
    return {
        "success": False,
        "file_name": file_name,
        "message_id": message_id,
        "attachment_id": attachment_id,
        "message": f"Upload failed: {upload_error}",
        "upload_error": upload_error,
    }
