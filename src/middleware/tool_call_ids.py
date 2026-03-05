"""
Middleware to assign globally unique tool call IDs in agent responses.

LLMs (OpenAI, etc.) emit tool call IDs that reset per turn (e.g. functions.execute:0,
functions.execute:1). Multi-step agent runs produce multiple AI messages, each with
tool calls that reuse the same IDs. The assistant-ui frontend expects globally unique
IDs and crashes with "Duplicate key … in tapResources" when it sees duplicates.

This middleware rewrites tool call IDs at the source so the frontend receives
unique IDs without any client-side normalization or separator hacks.
"""

from typing import Callable, Awaitable, Any

from langchain.agents.middleware import AgentMiddleware, ModelRequest
from langchain.agents.middleware.types import ModelResponse
from langchain_core.messages import AIMessage, BaseMessage


def _count_existing_tool_calls(messages: list) -> int:
    """Count total tool calls in all AI messages so far (for unique ID base)."""
    n = 0
    for msg in messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            n += len(msg.tool_calls)
    return n


def _rewrite_tool_call_ids(msg: AIMessage, base: int) -> AIMessage:
    """Replace tool call IDs with globally unique tc-{base+i} format."""
    tool_calls = getattr(msg, "tool_calls", None) or []
    if not tool_calls:
        return msg

    new_tool_calls = []
    for i, tc in enumerate(tool_calls):
        tc_dict = dict(tc) if hasattr(tc, "items") else {"name": getattr(tc, "name", ""), "args": getattr(tc, "args", {}) or {}, "id": getattr(tc, "id", "")}
        new_id = f"tc-{base + i}"
        new_tc = {**tc_dict, "id": new_id}
        new_tool_calls.append(new_tc)

    return msg.model_copy(update={"tool_calls": new_tool_calls})


class UniqueToolCallIdsMiddleware(AgentMiddleware):
    """
    Rewrites tool call IDs on AIMessages to be globally unique per run.

    Uses tc-{n} format where n is the cumulative count of tool calls in the
    conversation. This prevents assistant-ui from crashing on duplicate keys
    when multi-step runs reuse IDs like functions.execute:1 across steps.
    """

    async def awrap_model_call(
        self,
        request: ModelRequest,
        handler: Callable[[ModelRequest], Awaitable[Any]],
    ) -> Any:
        response = await handler(request)

        # Handle ModelResponse (result is list of BaseMessage)
        if isinstance(response, ModelResponse):
            result = getattr(response, "result", None)
            if not result or not isinstance(result, list):
                return response

            base = _count_existing_tool_calls(list(request.messages))
            new_result: list[BaseMessage] = []

            for msg in result:
                if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
                    new_result.append(_rewrite_tool_call_ids(msg, base))
                    base += len(msg.tool_calls)
                else:
                    new_result.append(msg)

            return ModelResponse(result=new_result, structured_response=getattr(response, "structured_response", None))

        # Handle bare AIMessage (some runtimes return this)
        if isinstance(response, AIMessage) and getattr(response, "tool_calls", None):
            base = _count_existing_tool_calls(list(request.messages))
            return _rewrite_tool_call_ids(response, base)

        return response
