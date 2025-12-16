from langchain.tools import tool, ToolRuntime
from src.utils import build_context_from_config
from src.agent_triage.models import Ticket
import uuid
from datetime import datetime

@tool
def crear_ticket(titulo: str , description : str, runtime: ToolRuntime) -> str:
    """
    Crea un ticket con titulo y descripción.

    Args:
    - titulo: titulo del ticket
    - description: descripción detallada del ticket
    """

    user_id = runtime.context.user_id
    ticket_id = str(uuid.uuid4())
    
    ticket_data = {
        "id": ticket_id,
        "title": titulo,
        "description": description,
        "assigned_to": str(user_id),
        "status": "open",
        "related_threads": [],
        "updated_at": datetime.now().isoformat(),
    }
    
    # Store ticket as individual item with namespace (user_id, "tickets")
    namespace = (str(user_id), "tickets")
    runtime.store.put(namespace, ticket_id, ticket_data)
    
    print(f"Ticket created with ID {ticket_id} for user {user_id}", flush=True)
    
    # Verify storage by searching
    tickets = runtime.store.search(namespace)
    print(f"Total tickets for user {user_id}: {len(list(tickets))}", flush=True)

    return f"Ticket creado con id {ticket_id} asignado al usuario {user_id}"

@tool
def patch_ticket(runtime : ToolRuntime) -> str :
    """
    Actualiza un ticket existente.

    Args:
    - runtime: contexto de ejecución con configuración del usuario
    """
    user_id = runtime.context.user_id
    # Here you would implement the actual ticket patching logic
    # using the runtime to access the document store
    # For now, just return a placeholder message
    # TODO: Implement actual ticket patching logic
    return "Ticket patched - placeholder implementation"


