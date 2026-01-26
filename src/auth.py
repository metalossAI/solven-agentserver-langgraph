import os
import httpx
from dotenv import load_dotenv
from langgraph.graph.state import RunnableConfig
from supabase import create_async_client
from langgraph_sdk import Auth

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ["SUPABASE_SECRET_KEY"]

auth = Auth()

# Load from .env file

@auth.authenticate
async def authenticate(headers: dict) -> Auth.types.MinimalUserDict:
    """Validate JWT tokens and extract user information."""
    supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    def _get_header(name: str):
        return headers.get(name) or headers.get(name.lower()) or headers.get(name.upper()) or headers.get(name.encode())

    def _normalize_bearer(value):
        """Extract token from Bearer header"""
        if value is None:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8")
        if not isinstance(value, str):
            value = str(value)
        value = value.strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        return value

    # Try to get authentication token from either x-api-key or Authorization header
    x_api_key = _get_header("x-api-key")
    authorization = _get_header("authorization")
    
    # Determine the token to validate
    token = None
    
    if x_api_key:
        if isinstance(x_api_key, bytes):
            x_api_key = x_api_key.decode("utf-8")
        x_api_key = x_api_key.strip()
        
        # Check if it's the system API key
        if os.getenv("LANGGRAPH_API_KEY") == x_api_key:
            user_data = {
                "id": "system",
                "email": "system@metaloss.es",
                "name": "System",
                "role": "system",
                "company_id": "system",
                "is_active": True,
                "is_creator": True,
            }
            return {
                "identity": "system",
                "is_authenticated": True,
                "user_data": user_data,
            }
        
        # Not the system key, treat as user token (from CopilotKit via langsmithApiKey)
        token = x_api_key
    elif authorization:
        # Extract Bearer token from Authorization header
        token = _normalize_bearer(authorization)
    
    if not token:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Missing authentication header")
    
    try:
        # Verify token with Supabase (works for both x-api-key and Authorization header)
        auth_user = await supabase.auth.get_user(token)
        
        if not auth_user or not auth_user.user:
            raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")
        
        # Extract user data from Supabase user metadata
        user = auth_user.user
        metadata = user.user_metadata or {}
        
        user_data = {
            "id": user.id,
            "email": user.email,
            "name": metadata.get("name", user.email.split("@")[0] if user.email else "User"),
            "role": metadata.get("role", "oficial"),
            "company_id": metadata.get("company_id"),
            "is_active": metadata.get("is_active", True),
            "is_creator": metadata.get("is_creator", False),
        }        
        return {
            "identity": user.id,
            "is_authenticated": True,
            "user_data": user_data,
        }
    except Auth.exceptions.HTTPException:
        raise  # Re-raise auth exceptions
    except Exception as e:
        raise Auth.exceptions.HTTPException(status_code=401, detail=str(e))

def get_user_from_config(config : RunnableConfig):
    """Extract user information from the runtime for ticket creation."""
    # Get context from config
    user_config = config["configurable"].get("langgraph_auth_user")
    user_data = user_config.get("user_data", {})
    conversation_id = config.get("metadata", {}).get("thread_id")
    
    # Return user information for ticket creation
    return {
        "id": user_data.get("id"),
        "name": user_data.get("name", "Unknown User"),
        "email": user_data.get("email"),
        "company_id": user_data.get("company_id"),
        "conversation_id": conversation_id
    }
    