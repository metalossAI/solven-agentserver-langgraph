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
    # Here you would implement the actual ticket creation logic
    # using the runtime to access the document store
    context = build_context_from_config(runtime.config)
    print("context", context)
    # Here you would implement the actual ticket creation logic
    # using the runtime to access the document store
    context = build_context_from_config(runtime.config)
    print("context", context)

    user_id = context.user_id
    ticket: Ticket = Ticket(
        id = str(uuid.uuid4()),
        title=title,
        description=description,
        status="open",
        related_threads=[],
        updated_at=datetime.now().isoformat(),
    )

    runtime.store.put((user_id, "tickets"), "all", ticket)

    return f"Ticket created with ID {ticket.id} for user {context.get('user_id', 'unknown')}"

@tool
def patch_ticket(runtime : ToolRuntime) -> str :
    """
    Actualiza un ticket existente.

    Args:
    - runtime: contexto de ejecución con configuración del usuario
    """
    context = build_context_from_config(runtime.config)
    print("patch context", context)
    # Here you would implement the actual ticket patching logic
    # using the runtime to access the document store
    # For now, just return a placeholder message
    # TODO: Implement actual ticket patching logic
    return "Ticket patched - placeholder implementation"


