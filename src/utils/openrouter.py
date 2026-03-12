"""
Normalize LangChain/LangGraph message content blocks for OpenRouter API.

OpenRouter expects images as type "image_url" with image_url.url (URL or
data:image/...;base64,...). Tool messages (e.g. from read tool) can contain
type "image" with base64, which causes ValidationError. This module normalizes
content so all messages sent to ChatOpenRouter use the expected schema.

See: https://openrouter.ai/docs/guides/overview/multimodal/images
"""

from __future__ import annotations

import base64 as _b64
from typing import Any

from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.messages.block_translators.openai import (
    convert_to_openai_data_block,
    convert_to_openai_image_block,
)

_DEFAULT_IMAGE_MIME = "image/png"


def _normalize_content_block(block: Any) -> Any:
    """Convert a single content block to OpenRouter format.

    - String or non-dict: return as-is.
    - type "text" or "image_url" (with url): return as-is.
    - type "image": convert to image_url (base64 -> data URI, or use url).
    - Other dict types: try convert_to_openai_data_block; on failure use text placeholder.
    """
    if isinstance(block, str):
        return block
    if not isinstance(block, dict):
        return block

    kind = block.get("type")
    if kind == "text":
        return block
    if kind == "image_url":
        url_val = block.get("image_url")
        if isinstance(url_val, dict) and url_val.get("url"):
            return block
        if isinstance(url_val, str):
            return {"type": "image_url", "image_url": {"url": url_val}}
        return block

    if kind == "image" or (kind and "image" in str(kind).lower()):
        try:
            return convert_to_openai_image_block(block)
        except (ValueError, KeyError):
            pass
        url = block.get("url")
        base64_data = block.get("base64")
        mime = block.get("mime_type") or _DEFAULT_IMAGE_MIME
        if base64_data is not None:
            if isinstance(base64_data, bytes):
                base64_data = _b64.b64encode(base64_data).decode("ascii")
            data_url = f"data:{mime};base64,{base64_data}"
            return {"type": "image_url", "image_url": {"url": data_url}}
        if url:
            return {"type": "image_url", "image_url": {"url": url}}
        return {"type": "text", "text": "[image content not available]"}

    # file, video, or other: try langchain_core converter
    if kind in ("file", "video") or (isinstance(kind, str) and kind):
        try:
            return convert_to_openai_data_block(block)
        except (ValueError, KeyError, TypeError):
            pass
    text = block.get("text") or f"[{kind or 'unknown'} content block]"
    return {"type": "text", "text": text}


def normalize_message_content(content: Any) -> Any:
    """Normalize message content for OpenRouter.

    - If content is a string, return as-is.
    - If content is a list, normalize each element and return the list.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return [_normalize_content_block(item) for item in content]
    return content


def normalize_messages_for_openrouter(messages: list) -> list[BaseMessage]:
    """Return a new list of messages with content normalized for OpenRouter.

    Rebuilds messages so that image blocks use type "image_url" and
    image_url.url (data URI or URL).
    """
    out: list[BaseMessage] = []
    for msg in messages:
        if not isinstance(msg, BaseMessage):
            out.append(msg)
            continue
        content = getattr(msg, "content", None)
        normalized = normalize_message_content(content)
        if normalized is content:
            out.append(msg)
            continue
        kwargs: dict[str, Any] = {"content": normalized}
        if isinstance(msg, ToolMessage):
            kwargs["tool_call_id"] = msg.tool_call_id
        for attr in ("additional_kwargs", "response_metadata", "name", "id"):
            if hasattr(msg, attr):
                val = getattr(msg, attr, None)
                if val is not None and (attr != "additional_kwargs" or val):
                    kwargs[attr] = val
        if isinstance(msg, ToolMessage):
            out.append(ToolMessage(**kwargs))
        elif isinstance(msg, HumanMessage):
            out.append(HumanMessage(**kwargs))
        elif isinstance(msg, AIMessage):
            if hasattr(msg, "tool_calls") and msg.tool_calls:
                kwargs["tool_calls"] = msg.tool_calls
            if hasattr(msg, "invalid_tool_calls") and msg.invalid_tool_calls:
                kwargs["invalid_tool_calls"] = msg.invalid_tool_calls
            out.append(AIMessage(**kwargs))
        elif isinstance(msg, SystemMessage):
            out.append(SystemMessage(**kwargs))
        else:
            try:
                out.append(type(msg)(**kwargs))
            except Exception:
                out.append(msg)
    return out


class OpenRouterContentMiddleware(AgentMiddleware):
    """Middleware that normalizes message content blocks for OpenRouter before the model call."""

    async def awrap_model_call(self, request, handler):
        if not request or not getattr(request, "messages", None):
            return await handler(request)
        messages = getattr(request, "messages", [])
        normalized = normalize_messages_for_openrouter(list(messages))
        if normalized != messages:
            modified = request.override(messages=normalized)
            return await handler(modified)
        return await handler(request)
