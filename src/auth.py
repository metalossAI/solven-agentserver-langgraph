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
    print(f"[DEBUG auth.authenticate] ========== START authenticate ==========")
    print(f"[DEBUG auth.authenticate] Headers keys: {list(headers.keys())}")
    
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

    # CHECK 1: Try to get authentication token from either x-api-key or Authorization header
    x_api_key = _get_header("x-api-key")
    authorization = _get_header("authorization")
    
    print(f"[DEBUG auth.authenticate] x-api-key present: {x_api_key is not None}")
    print(f"[DEBUG auth.authenticate] x-api-key length: {len(x_api_key) if x_api_key else 0}")
    print(f"[DEBUG auth.authenticate] authorization present: {authorization is not None}")
    
    # Determine the token to validate
    token = None
    
    if x_api_key:
        if isinstance(x_api_key, bytes):
            x_api_key = x_api_key.decode("utf-8")
        x_api_key = x_api_key.strip()
        
        # CHECK 2: Check if it's the system API key
        system_api_key = os.getenv("LANGGRAPH_API_KEY")
        is_system_key = system_api_key == x_api_key
        print(f"[DEBUG auth.authenticate] Is system API key: {is_system_key}")
        print(f"[DEBUG auth.authenticate] System API key set: {system_api_key is not None}")
        
        if is_system_key:
            user_data = {
                "id": "system",
                "email": "system@metaloss.es",
                "name": "System",
                "role": "system",
                "company_id": "system",
                "is_active": True,
                "is_creator": True,
            }
            result = {
                "identity": "system",
                "is_authenticated": True,
                "user_data": user_data,
            }
            print(f"[DEBUG auth.authenticate] ✅ Returning system user_data: {user_data}")
            print(f"[DEBUG auth.authenticate] ========== END authenticate (SYSTEM) ==========")
            return result
        
        # Not the system key, treat as user token (from CopilotKit via langsmithApiKey)
        token = x_api_key
        print(f"[DEBUG auth.authenticate] Using x-api-key as user token")
    elif authorization:
        # Extract Bearer token from Authorization header
        token = _normalize_bearer(authorization)
        print(f"[DEBUG auth.authenticate] Using Authorization header as token")
    
    # CHECK 3: Verify token exists
    if not token:
        print(f"[ERROR auth.authenticate] No token found in headers!")
        print(f"[DEBUG auth.authenticate] ========== END authenticate (NO TOKEN) ==========")
        raise Auth.exceptions.HTTPException(status_code=401, detail="Missing authentication header")
    
    print(f"[DEBUG auth.authenticate] Token length: {len(token)}")
    print(f"[DEBUG auth.authenticate] Token prefix: {token[:20]}..." if len(token) > 20 else f"[DEBUG auth.authenticate] Token: {token}")
    
    try:
        # CHECK 4: Verify token with Supabase (works for both x-api-key and Authorization header)
        print(f"[DEBUG auth.authenticate] Calling supabase.auth.get_user(token)...")
        auth_user = await supabase.auth.get_user(token)
        
        if not auth_user or not auth_user.user:
            print(f"[ERROR auth.authenticate] Invalid token - auth_user or auth_user.user is None")
            print(f"[DEBUG auth.authenticate] ========== END authenticate (INVALID TOKEN) ==========")
            raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")
        
        # CHECK 5: Extract user data from Supabase user metadata
        user = auth_user.user
        metadata = user.user_metadata or {}
        
        print(f"[DEBUG auth.authenticate] Supabase user.id: {user.id}")
        print(f"[DEBUG auth.authenticate] Supabase user.email: {user.email}")
        print(f"[DEBUG auth.authenticate] Supabase user.user_metadata: {metadata}")
        
        user_data = {
            "id": user.id,
            "email": user.email,
            "name": metadata.get("name", user.email.split("@")[0] if user.email else "User"),
            "role": metadata.get("role", "oficial"),
            "company_id": metadata.get("company_id"),
            "is_active": metadata.get("is_active", True),
            "is_creator": metadata.get("is_creator", False),
        }
        
        print(f"[DEBUG auth.authenticate] ✅ Extracted user_data: {user_data}")
        print(f"[DEBUG auth.authenticate] user_data.get('id'): {user_data.get('id')}")
        print(f"[DEBUG auth.authenticate] user_data.get('company_id'): {user_data.get('company_id')}")
        
        result = {
            "identity": user.id,
            "is_authenticated": True,
            "user_data": user_data,
        }
        
        print(f"[DEBUG auth.authenticate] ✅ Returning result: identity={result['identity']}, is_authenticated={result['is_authenticated']}")
        print(f"[DEBUG auth.authenticate] ========== END authenticate (SUCCESS) ==========")
        return result
    except Auth.exceptions.HTTPException as e:
        print(f"[ERROR auth.authenticate] Auth HTTPException: {e}")
        print(f"[DEBUG auth.authenticate] ========== END authenticate (AUTH ERROR) ==========")
        raise  # Re-raise auth exceptions
    except Exception as e:
        print(f"[ERROR auth.authenticate] Unexpected exception: {type(e).__name__}: {str(e)}")
        import traceback
        print(f"[ERROR auth.authenticate] Traceback: {traceback.format_exc()}")
        print(f"[DEBUG auth.authenticate] ========== END authenticate (EXCEPTION) ==========")
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
    