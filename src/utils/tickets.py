from dotenv import load_dotenv
import os
from typing import Optional
from datetime import datetime

from supabase import create_async_client
from src.models import Ticket

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET = os.getenv("SUPABASE_SECRET_KEY")

async def get_ticket(ticket_id: str) -> Optional[Ticket]:
    """
    Retrieve a ticket from Supabase by ID.
    
    Args:
        ticket_id: The UUID of the ticket to retrieve
        
    Returns:
        Ticket object if found, None otherwise
    """
    try:
        if not ticket_id:
            return None
            
        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SECRET)
        
        # Fetch ticket from database
        response = await supabase.table("tickets").select("*").eq("id", ticket_id).execute()
        
        if not response.data or len(response.data) == 0:
            return None
        
        ticket_data = response.data[0]
        
        # Build Ticket model from database data
        ticket = Ticket(
            id=ticket_data.get("id"),
            title=ticket_data.get("title", ""),
            description=ticket_data.get("description", ""),
            related_threads=ticket_data.get("related_threads", []),
            status=ticket_data.get("status", "open"),
            updated_at=datetime.fromisoformat(ticket_data.get("updated_at")) if ticket_data.get("updated_at") else datetime.now()
        )
        
        return ticket
        
    except Exception:
        return None
