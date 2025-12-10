import os
import httpx
from dotenv import load_dotenv
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
    