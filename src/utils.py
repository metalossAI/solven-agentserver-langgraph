
from typing import Dict, Any

from langgraph.graph.state import RunnableConfig
from src.models import AppContext

def build_context_from_config(config: RunnableConfig) -> AppContext:
    """Build context from runtime configuration."""
    user_config = config["configurable"].get("langgraph_auth_user")
    user_id = user_config.get("user_data").get("id")
    tenant_id = user_config.get("user_data").get("company_id")
    thread_id = config.get("metadata").get("thread_id")
    
    return AppContext(
        thread_id=thread_id,
        user_id=user_id,
        tenant_id=tenant_id,
    )