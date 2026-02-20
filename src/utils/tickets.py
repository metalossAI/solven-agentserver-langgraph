from dotenv import load_dotenv
import os
from typing import Optional
from datetime import datetime, timezone

from supabase import create_async_client
from src.models import Ticket

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SECRET = os.getenv("SUPABASE_SECRET_KEY")

async def get_ticket(thread_id: str) -> Optional[Ticket]:
    """
    Retrieve a ticket from Supabase by thread_id (which is the ticket ID).
    Also loads the document content from the documents table using the ticket's documentId.
    
    Args:
        thread_id: The UUID of the ticket/thread to retrieve
        
    Returns:
        Ticket object with document content injected into description if found, None otherwise
    """
    try:
        if not thread_id:
            print(f"[get_ticket] No thread_id provided", flush=True)
            return None
        
        print(f"[get_ticket] Loading ticket for thread_id: {thread_id}", flush=True)
        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SECRET)
        
        # Fetch ticket from database using thread_id (which is the ticket ID)
        response = await supabase.table("tickets").select("*").eq("id", thread_id).execute()
        
        if not response.data or len(response.data) == 0:
            print(f"[get_ticket] No ticket found for thread_id: {thread_id}", flush=True)
            return None
        
        ticket_data = response.data[0]
        print(f"[get_ticket] Ticket found: {ticket_data.get('id')} - {ticket_data.get('title', 'No title')}", flush=True)
        
        # Get documentId from ticket
        document_id = ticket_data.get("document_id") or ticket_data.get("documentId")
        
        # Load document content from documents table if documentId exists
        description = ticket_data.get("description", "")
        if document_id:
            print(f"[get_ticket] Loading document for documentId: {document_id}", flush=True)
            try:
                doc_response = await supabase.table("documents").select("content").eq("id", document_id).execute()
                if doc_response.data and len(doc_response.data) > 0:
                    document_content = doc_response.data[0].get("content", "")
                    if document_content:
                        print(f"[get_ticket] Document content loaded successfully ({len(document_content)} chars), injecting into ticket description", flush=True)
                        # Inject document content into description
                        description = document_content
                    else:
                        print(f"[get_ticket] Document found but content is empty, using ticket description", flush=True)
                else:
                    print(f"[get_ticket] No document found for documentId: {document_id}, using ticket description", flush=True)
            except Exception as e:
                print(f"[get_ticket] Warning: Failed to load document {document_id}: {e}", flush=True)
                # Continue with original description if document load fails
        else:
            print(f"[get_ticket] No documentId found in ticket, using ticket description", flush=True)
        
        # Build Ticket model from database data
        ticket = Ticket(
            id=ticket_data.get("id"),
            title=ticket_data.get("title", ""),
            description=description,  # Use document content if available, otherwise use ticket description
            related_threads=ticket_data.get("related_threads", []),
            status=ticket_data.get("status", "open"),
            updated_at=datetime.fromisoformat(ticket_data.get("updated_at")) if ticket_data.get("updated_at") else datetime.now(timezone.utc)
        )
        
        print(f"[get_ticket] Ticket loaded successfully: {ticket.id} - Description length: {len(ticket.description)} chars", flush=True)
        return ticket
        
    except Exception as e:
        print(f"[get_ticket] Error loading ticket for thread_id {thread_id}: {e}", flush=True)
        import traceback
        print(f"[get_ticket] Traceback: {traceback.format_exc()}", flush=True)
        return None
