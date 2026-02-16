"""Helper utilities for extracting data from LangGraph config."""

from typing import Dict, Optional
from langgraph.config import get_config
from langgraph.graph.state import RunnableConfig


def get_user_data_from_config() -> Dict:
    """Extract user_data dict from config.
    
    Returns:
        Dict with user data (id, name, email, role, company_id, etc.)
        Empty dict if not found.
    """
    try:
        config: RunnableConfig = get_config()
        configurable = config.get("configurable", {})
        
        # Try langgraph_auth_user first (for authenticated user calls)
        user_config = configurable.get("langgraph_auth_user", {})
        if user_config:
            user_data = user_config.get("user_data", {}) if isinstance(user_config, dict) else {}
            if user_data and user_data.get("id"):
                return user_data
        
        # Fallback: try user_data directly in configurable (for Composio triggers)
        user_data = configurable.get("user_data", {})
        if user_data and user_data.get("id"):
            return user_data
        
        return {}
    except Exception:
        return {}


def get_user_id_from_config() -> Optional[str]:
    """Extract user_id from config.
    
    Tries multiple sources:
    1. user_data.id from langgraph_auth_user.user_data
    2. user_data.id from configurable.user_data (Composio triggers)
    3. user_id from metadata (fallback)
    4. user_id from configurable (fallback)
    
    Returns:
        User ID string or None if not found.
    """
    try:
        config: RunnableConfig = get_config()
        configurable = config.get("configurable", {})
        metadata = config.get("metadata", {})
        
        # Try user_data first
        user_data = get_user_data_from_config()
        user_id = user_data.get("id")
        if user_id:
            return user_id
        
        # Fallback: try metadata.user_id
        user_id = metadata.get("user_id")
        if user_id:
            return user_id
        
        # Fallback: try configurable.user_id
        user_id = configurable.get("user_id")
        if user_id:
            return user_id
        
        return None
    except Exception:
        return None


def get_company_id_from_config() -> Optional[str]:
    """Extract company_id from config.
    
    Tries user_data.company_id first, then configurable.company_id.
    
    Returns:
        Company ID string or None if not found.
    """
    try:
        config: RunnableConfig = get_config()
        configurable = config.get("configurable", {})
        
        # Try user_data.company_id first
        user_data = get_user_data_from_config()
        company_id = user_data.get("company_id")
        if company_id:
            return company_id
        
        # Fallback: try configurable.company_id
        company_id = configurable.get("company_id")
        if company_id:
            return company_id
        
        return None
    except Exception:
        return None


def get_thread_id_from_config() -> Optional[str]:
    """Extract thread_id from config.
    
    Returns:
        Thread ID string or None if not found.
    """
    try:
        config: RunnableConfig = get_config()
        configurable = config.get("configurable", {})
        return configurable.get("thread_id")
    except Exception:
        return None


def get_event_message_from_config() -> Optional[str]:
    """Extract event_message from config (for triage agent).
    
    Returns:
        Event message string or None if not found.
    """
    try:
        config: RunnableConfig = get_config()
        configurable = config.get("configurable", {})
        metadata = config.get("metadata", {})
        
        # Try configurable first, then metadata
        event_message = configurable.get("event_message") or metadata.get("event_message")
        return event_message if event_message else None
    except Exception:
        return None

