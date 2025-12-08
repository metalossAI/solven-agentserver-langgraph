from composio import Composio, after_execute
from composio_langchain import LangchainProvider

def get_composio_tools_sync(user_id, toolkit):
    """Synchronous version for running in thread pool"""
    composio = Composio(provider=LangchainProvider())
    tools = composio.tools.get(
        user_id=user_id,
        toolkits=[toolkit],
        modifiers=[after_execute]
    )
    return tools

async def get_composio_tools(user_id, toolkit):
    """Async wrapper that runs sync version in thread pool"""
    import asyncio
    return await asyncio.to_thread(get_composio_tools_sync, user_id, toolkit)