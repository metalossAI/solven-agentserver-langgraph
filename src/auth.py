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
    """Validate JWT tokens and extract user information.
    
    Supports two authentication modes:
    1. System API key (x-api-token or x-api-key) - reads user data from headers
    2. User access token (x-api-key or Authorization) - validates with Supabase
    """
    supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    def _get_header(name: str):
        """Get header value and ensure it's a string (decode if bytes)"""
        value = headers.get(name) or headers.get(name.lower()) or headers.get(name.upper()) or headers.get(name.encode())
        if value is None:
            return None
        # Decode bytes to string if necessary
        if isinstance(value, bytes):
            try:
                return value.decode('utf-8')
            except UnicodeDecodeError:
                # If UTF-8 fails, try latin-1 (which can decode any byte sequence)
                # then encode back to bytes and decode as utf-8 with error handling
                try:
                    return value.decode('latin-1')
                except (UnicodeDecodeError, AttributeError):
                    # Last resort: decode with error replacement
                    return value.decode('utf-8', errors='replace')
        return value

    def _normalize_bearer(value):
        """Extract token from Bearer header"""
        if value is None:
            return None
        if isinstance(value, bytes):
            try:
                value = value.decode("utf-8")
            except UnicodeDecodeError:
                value = value.decode("utf-8", errors="replace")
        if not isinstance(value, str):
            value = str(value)
        value = value.strip()
        if value.lower().startswith("bearer "):
            value = value[7:].strip()
        return value

    # Try to get authentication token from x-api-token, x-api-key, or Authorization header
    x_api_token = _get_header("x-api-token")
    x_api_key = _get_header("x-api-key")
    authorization = _get_header("authorization")
    
    # Determine the token to validate
    token = None
    is_system_auth = False
    
    # Check for system API key first (x-api-token or x-api-key matching system key)
    system_api_key = os.getenv("LANGGRAPH_API_KEY")
    
    if x_api_token:
        if isinstance(x_api_token, bytes):
            try:
                x_api_token = x_api_token.decode("utf-8")
            except UnicodeDecodeError:
                x_api_token = x_api_token.decode("utf-8", errors="replace")
        x_api_token = x_api_token.strip()
        if system_api_key == x_api_token:
            is_system_auth = True
    elif x_api_key:
        if isinstance(x_api_key, bytes):
            try:
                x_api_key = x_api_key.decode("utf-8")
            except UnicodeDecodeError:
                x_api_key = x_api_key.decode("utf-8", errors="replace")
        x_api_key = x_api_key.strip()
        if system_api_key == x_api_key:
            is_system_auth = True
        else:
            # Not the system key, treat as user token
            token = x_api_key
    elif authorization:
        # Extract Bearer token from Authorization header
        token = _normalize_bearer(authorization)
    
    # If using system auth, read user data from headers
    if is_system_auth:
        # Extract user data from headers
        user_id = _get_header("x-user-id")
        company_id = _get_header("x-company-id")
        user_name = _get_header("x-user-name")
        user_email = _get_header("x-user-email")
        user_role = _get_header("x-user-role")
        
        # Debug logging (optional - uncomment if needed)
        # print(f"[AUTH] System auth - Headers received:")
        # print(f"  user_id: {user_id}")
        # print(f"  company_id: {company_id!r}")
        # print(f"  user_name: {user_name}")
        # print(f"  user_email: {user_email}")
        # print(f"  user_role: {user_role}")
        
        # If user headers are present, use them; otherwise fall back to system user
        if user_id:
            # Normalize company_id: treat empty string as None
            normalized_company_id = company_id if company_id else None
            
            user_data = {
                "id": user_id,
                "email": user_email or f"{user_id}@metaloss.es",
                "name": user_name or "User",
                "role": user_role or "oficial",
                "company_id": normalized_company_id,
                "is_active": True,
                "is_creator": False,
            }
            
            # Return with user_data nested - LangGraph stores this in langgraph_auth_user
            return {
                "identity": user_id,
                "is_authenticated": True,
                "user_data": user_data,
            }
        else:
            # System user (no user headers provided)
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
    
    # If no token and not system auth, fail
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
    # Get context from config - extract user_data from langgraph_auth_user (ProxyUser object)
    user_config = config["configurable"].get("langgraph_auth_user")
    user_data = {}
    
    if user_config:
        # Handle ProxyUser object - try attribute access first
        if hasattr(user_config, 'user_data'):
            user_data = user_config.user_data
        elif isinstance(user_config, dict):
            user_data = user_config.get("user_data", {})
        else:
            try:
                user_data = user_config["user_data"]
            except (KeyError, TypeError):
                user_data = {}
    
    conversation_id = config.get("metadata", {}).get("thread_id")
    
    # Return user information for ticket creation
    return {
        "id": user_data.get("id") if user_data else None,
        "name": user_data.get("name", "Unknown User") if user_data else "Unknown User",
        "email": user_data.get("email") if user_data else None,
        "company_id": user_data.get("company_id") if user_data else None,
        "conversation_id": conversation_id
    }
    