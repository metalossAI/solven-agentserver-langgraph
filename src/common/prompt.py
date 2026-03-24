from typing import Callable, Awaitable
from langchain_core.messages import SystemMessage
from langchain.agents.middleware import ModelRequest, dynamic_prompt
from src.agent.middleware import build_prompt_template


def create_prompt_middleware(
    prompt_id: str,
    get_variables: Callable[[ModelRequest], Awaitable[dict]],
) -> Callable[[ModelRequest], Awaitable[SystemMessage]]:
    """
    Returns a @dynamic_prompt middleware that builds the system message for the given prompt_id.
    The returned function can be passed at runtime (e.g. to create_agent(middleware=[...])).
    get_variables(request) is called to obtain the format variables; the formatted template
    is prepended before the existing ``request.system_message`` content_blocks so earlier
    middleware additions are preserved.
    """
    @dynamic_prompt
    async def middleware(request: ModelRequest) -> SystemMessage:
        variables = await get_variables(request)
        initial_prompt = await build_prompt_template(prompt_id, variables)
        system_prompt = request.system_message
        prior_blocks = list(system_prompt.content_blocks) if system_prompt is not None else []
        new_content = [
            {"type": "text", "text": f"{initial_prompt}\n\n"},
            *prior_blocks,
        ]
        return SystemMessage(content=new_content)
    return middleware