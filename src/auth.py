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
    # Get the API key and decode if it's bytes
    api_key = headers.get(b"x-api-key")
    
    if not api_key:
        raise Auth.exceptions.HTTPException(status_code=401, detail="Missing API key header")
    
    # Decode bytes to string
    if isinstance(api_key, bytes):
        api_key = api_key.decode('utf-8')
    
    try:
        # Verify token with Supabase
        auth_user = await supabase.auth.get_user(api_key)
        
        if not auth_user or not auth_user.user:
            raise Auth.exceptions.HTTPException(status_code=401, detail="Invalid token")
        
        # Try to fetch user data from users table (optional)
        user_data = None
        try:
            user_response = await supabase.table("users").select("*").eq("supabase_id", auth_user.user.id).execute()
            if user_response.data and len(user_response.data) > 0:
                user_data = user_response.data[0]
        except Exception as db_error:
            pass
        
        return {
            "identity": auth_user.user.id,
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
    user_data = user_config.get("user_data")
    user_id = user_config.get("user_data").get("id")
    tenant_id = user_config.get("user_data").get("company_id")
    conversation_id = config.get("metadata").get("thread_id")
    
    # Return user information for ticket creation
    return {
        "id": user_id,
        "name": user_data.get("name") if user_data else "Unknown User",
        "email": user_data.get("email") if user_data else None,
        "company_id": tenant_id,
        "conversation_id": conversation_id
    }
    