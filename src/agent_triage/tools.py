from langchain.tools import tool, ToolRuntime
from src.utils import build_context_from_config
from src.agent_triage.models import Ticket
import uuid
from datetime import datetime
import os
from supabase import create_async_client

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SECRET_KEY")

@tool
async def crear_ticket(titulo: str, description: str, runtime: ToolRuntime) -> str:
    """
    Crea un ticket con titulo y descripción.

    Args:
    - titulo: titulo del ticket
    - description: descripción detallada del ticket
    """
    try:
        company_id = runtime.context.tenant_id
        user_id = runtime.context.user_id
        
        print(f"[crear_ticket] Company ID: {company_id}", flush=True)
        print(f"[crear_ticket] User ID: {user_id}", flush=True)
        print(f"[crear_ticket] Title: {titulo}", flush=True)
        
        if not company_id:
            print(f"[crear_ticket] ERROR: No company_id", flush=True)
            return "Error: No se encontró el ID de la compañía"
        
        if not user_id:
            print(f"[crear_ticket] ERROR: No user_id", flush=True)
            return "Error: No se encontró el ID del usuario"
        
        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        print(f"[crear_ticket] Supabase client created", flush=True)
        
        # Create ticket in database
        ticket_data = {
            "company_id": company_id,
            "assigned_to": user_id,
            "assigned_by": "AI",
            "title": titulo,
            "description": description,
            "status": "open",
            "related_threads": [],
        }
        
        print(f"[crear_ticket] Inserting ticket data: {ticket_data}", flush=True)
        ticket_response = await supabase.table("tickets").insert(ticket_data).execute()
        print(f"[crear_ticket] Response: {ticket_response}", flush=True)
        
        if not ticket_response.data or len(ticket_response.data) == 0:
            print(f"[crear_ticket] ERROR: No data in response", flush=True)
            return "Error al crear ticket en la base de datos"
        
        ticket = ticket_response.data[0]
        ticket_id = ticket["id"]
        
        print(f"[crear_ticket] SUCCESS: Ticket created with ID {ticket_id}", flush=True)
        
        return f"Ticket creado con id {ticket_id} asignado al usuario {user_id}"
    except Exception as e:
        print(f"Error creating ticket: {str(e)}", flush=True)
        return f"Error al crear ticket: {str(e)}"

@tool
async def patch_ticket(ticket_id: str, status: str, rejection_reason: str = None, runtime: ToolRuntime = None) -> str:
    """
    Actualiza el estado de un ticket existente.

    Args:
    - ticket_id: ID del ticket a actualizar
    - status: nuevo estado del ticket ('open' o 'closed')
    - rejection_reason: razón del rechazo (opcional, requerido si se rechaza)
    - runtime: contexto de ejecución con configuración del usuario
    """
    try:
        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        if status not in ["open", "closed"]:
            return f"Estado inválido: {status}. Debe ser 'open' o 'closed'"
        
        company_id = runtime.context.tenant_id
        
        if not company_id:
            return "Error: Usuario sin compañía asignada"
        
        # Verify ticket belongs to user's company
        ticket_check = await supabase.table("tickets").select("id").eq("id", ticket_id).eq("company_id", company_id).execute()
        
        if not ticket_check.data or len(ticket_check.data) == 0:
            return f"Error: Ticket {ticket_id} no encontrado o no pertenece a tu compañía"
        
        # Prepare update data
        update_data = {
            "status": status,
            "updated_at": datetime.now().isoformat()
        }
        
        # Add rejection reason if provided
        if rejection_reason:
            update_data["rejection_reason"] = rejection_reason
        
        # Update ticket
        update_response = await supabase.table("tickets").update(update_data).eq("id", ticket_id).execute()
        
        if not update_response.data:
            return "Error al actualizar ticket"
        
        print(f"Ticket {ticket_id} updated to status {status}", flush=True)
        if rejection_reason:
            print(f"Rejection reason: {rejection_reason}", flush=True)
        
        return f"Ticket {ticket_id} actualizado correctamente con estado {status}" + (f" - Razón: {rejection_reason}" if rejection_reason else "")
    except Exception as e:
        print(f"Error updating ticket: {str(e)}", flush=True)
        return f"Error al actualizar ticket: {str(e)}"

@tool
async def listar_tickets(status: str = None, runtime: ToolRuntime = None) -> str:
    """
    Lista todos los tickets de la compañía del usuario.

    Args:
    - status: filtro opcional por estado ('open' o 'closed')
    - runtime: contexto de ejecución con configuración del usuario
    """
    try:
        company_id = runtime.context.tenant_id
        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        if not company_id:
            return "Error: Usuario sin compañía asignada"
        
        # Query tickets for the company
        query = supabase.table("tickets").select("*").eq("company_id", company_id)
        
        if status:
            query = query.eq("status", status)
        
        tickets_response = await query.execute()
        
        tickets = tickets_response.data if tickets_response.data else []
        total = len(tickets)
        
        if total == 0:
            return "No se encontraron tickets"
        
        tickets_summary = f"Se encontraron {total} tickets:\n\n"
        for ticket in tickets:
            tickets_summary += f"- ID: {ticket.get('id')}\n"
            tickets_summary += f"  Título: {ticket.get('title')}\n"
            tickets_summary += f"  Estado: {ticket.get('status')}\n"
            description = ticket.get('description', '')
            tickets_summary += f"  Descripción: {description[:100]}...\n\n" if len(description) > 100 else f"  Descripción: {description}\n\n"
        
        return tickets_summary
    except Exception as e:
        print(f"Error listing tickets: {str(e)}", flush=True)
        return f"Error al listar tickets: {str(e)}"


