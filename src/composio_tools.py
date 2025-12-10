from composio import Composio, after_execute
from composio_langchain import LangchainProvider

def check_if_connection_exists(auth_config : str, user_id : str):
    composio = Composio(provider=LangchainProvider())
    accounts = composio.connected_accounts.list(
        auth_config_ids=[auth_config],
        user_ids=[user_id],
        statuses=["ACTIVE"]
    )
    return accounts

def authenticate_elastic_search_toolkit(user_id: str, auth_config_id: str, es_api_key: str):
    composio = Composio(provider=LangchainProvider())
    if composio.connected_accounts.get(user_id):
        return True
    try:
        composio.connected_accounts.initiate(
            user_id=user_id,
            auth_config_id=auth_config_id,
            config={"auth_scheme": "API_KEY", "val": {"generic_api_key": es_api_key}}
        )    
        return True
    except Exception as e:
        print(f"Error connecting to Elasticsearch: {e}")
        return False

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