
from typing import Callable, List
from composio import Composio, before_execute, schema_modifier
from composio.types import ToolExecuteParams
from composio_langchain import LangchainProvider

def get_composio_gmail_tools(user_id):
    """Synchronous version for running in thread pool"""
    composio = Composio(provider=LangchainProvider())
    tools = composio.tools.get(
        user_id=user_id,
        toolkits=["GMAIL"],
        modifiers=[
            before_execute_modifier_gmail_fetch # avoid cluttering context with max 3 emails
        ]
    )
    return tools

def get_composio_outlook_tools(user_id):
    """Synchronous version for running in thread pool"""
    composio = Composio(provider=LangchainProvider())
    tools = composio.tools.get(
        user_id=user_id,
        toolkits=["OUTLOOK"],
        modifiers=[
        ]
    )
    return tools

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
