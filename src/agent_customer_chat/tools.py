"""
Customer chat tools — all ticket operations are scoped to the current thread.

Invariant: ticket_id == thread_id
Every tool that touches a ticket reads or writes the ticket whose ID equals
the LangGraph thread_id from the run config. This means:
  - One thread  <=>  one solicitud
  - No tool ever accepts a ticket_id argument (except listar_solicitudes_cliente
    which lists, never touches a specific ticket)
  - crear_solicitud is idempotent: safe to call multiple times on the same thread
"""

from typing import Optional, List
from langchain.tools import tool, ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.documents import Document
from pydantic import BaseModel, Field

from datetime import datetime, timezone
import os

from src.embeddings import embeddings
from supabase import create_async_client
from langchain_postgres import PGVectorStore
from src.utils.vector_store import get_pg_engine
from src.models import AppContext
from src.utils.config import get_user, get_thread_id

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SECRET_KEY")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_pg_vector_store():
    pg_engine = await get_pg_engine()
    return await PGVectorStore.create(
        engine=pg_engine,
        table_name="documents",
        embedding_service=embeddings,
        id_column="id",
        content_column="content",
        embedding_column="embedding",
        metadata_json_column="metadata",
    )


# ---------------------------------------------------------------------------
# Tool 1: listar_solicitudes_cliente
# ---------------------------------------------------------------------------

@tool
async def listar_solicitudes_cliente(runtime: ToolRuntime[AppContext]) -> ToolMessage:
    """
    Lista todas las solicitudes del cliente autenticado.
    Devuelve los tickets cuyo customer_email coincide con el email del usuario
    en sesión. Excluye solicitudes descartadas.
    No requiere ningún argumento.
    """
    try:
        user = get_user()
        customer_email = user.email
        company_id = user.company_id

        if not customer_email:
            return ToolMessage(
                content="Error: No se encontró el email del usuario en la configuración.",
                status="error",
                tool_call_id=runtime.tool_call_id,
                name="listar_solicitudes_cliente",
            )
        if not company_id:
            return ToolMessage(
                content="Error: No se encontró el ID de la compañía.",
                status="error",
                tool_call_id=runtime.tool_call_id,
                name="listar_solicitudes_cliente",
            )

        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

        # Prefer filtering by customer_id (auth user ID) for chat-originated tickets;
        # fall back to customer_email for email-originated tickets
        query = (
            supabase.table("tickets")
            .select("id, title, status, priority, created_at, updated_at")
            .eq("company_id", company_id)
            .neq("status", "discarded")
            .order("updated_at", desc=True)
        )
        if user.id:
            query = query.eq("customer_id", user.id)
        else:
            query = query.eq("customer_email", customer_email)

        resp = await query.execute()

        tickets = resp.data or []
        if not tickets:
            return ToolMessage(
                content="No tienes solicitudes registradas todavía.",
                tool_call_id=runtime.tool_call_id,
                name="listar_solicitudes_cliente",
            )

        lines = [f"Tienes {len(tickets)} solicitud(es):\n"]
        for t in tickets:
            updated = t.get("updated_at") or t.get("created_at") or "?"
            lines.append(
                f"- [{t.get('status', '?').upper()}] {t.get('title', 'Sin título')} "
                f"(ID: {t['id']}, Prioridad: {t.get('priority', '?')}, "
                f"Última actualización: {updated})"
            )

        return ToolMessage(content="\n".join(lines), tool_call_id=runtime.tool_call_id, name="listar_solicitudes_cliente")

    except Exception as e:
        import traceback; traceback.print_exc()
        return ToolMessage(
            content=f"Error al listar solicitudes: {str(e)}",
            status="error",
            tool_call_id=runtime.tool_call_id,
            name="listar_solicitudes_cliente",
        )


# ---------------------------------------------------------------------------
# Tool 2: crear_solicitud
# ---------------------------------------------------------------------------

class CrearSolicitudInput(BaseModel):
    titulo: str = Field(description="Título descriptivo de la solicitud o trámite")
    descripcion: str = Field(
        description="Descripción detallada de lo que el cliente necesita realizar"
    )


@tool(args_schema=CrearSolicitudInput)
async def crear_solicitud(
    titulo: str,
    descripcion: str,
    runtime: ToolRuntime[AppContext] = None,
) -> ToolMessage:
    """
    Crea la solicitud vinculada al hilo actual. Una sola solicitud por hilo.

    El ID del ticket es SIEMPRE el thread_id de LangGraph (ticket_id == thread_id).
    Si ya existe un ticket para este thread, devuelve un resumen sin duplicar.
    Se usa upsert en la BD para garantizar un único ticket por thread_id incluso
    si se invoca en paralelo.

    Args:
    - titulo: título descriptivo del trámite
    - descripcion: descripción detallada de lo que el cliente necesita
    """
    try:
        thread_id = get_thread_id()
        if not thread_id:
            return ToolMessage(
                content="Error: No se encontró el thread_id en la configuración.",
                status="error",
                tool_call_id=runtime.tool_call_id,
                name="crear_solicitud",
            )

        user = get_user()
        customer_email = user.email
        customer_id = user.id
        company_id = user.company_id

        if not all([company_id, customer_email, customer_id]):
            return ToolMessage(
                content="Error: Faltan datos del usuario (email, id o company_id) en la configuración.",
                status="error",
                tool_call_id=runtime.tool_call_id,
                name="crear_solicitud",
            )

        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

        # Single unique ticket per thread: id == thread_id always.
        # Idempotency: if ticket already exists, return without duplicating.
        existing = (
            await supabase.table("tickets")
            .select("id, title, status")
            .eq("id", thread_id)
            .execute()
        )
        if existing.data:
            t = existing.data[0]
            print(f"[customer_chat] Ticket already exists — thread: {thread_id}", flush=True)
            return ToolMessage(
                content=(
                    f"La solicitud ya existe: [{t.get('status', '?').upper()}] "
                    f"'{t.get('title', 'Sin título')}' (ID: {t['id']}). "
                    f"Usa actualizar_solicitud si deseas modificarla."
                ),
                tool_call_id=runtime.tool_call_id,
                name="crear_solicitud",
            )

        now = datetime.now(timezone.utc).isoformat()

        # Create document with embeddings (document id == thread_id)
        doc_metadata = {
            "ticket_id": thread_id,
            "company_id": company_id,
            "customer_email": customer_email,
            "customer_id": customer_id,
            "title": titulo,
            "priority": "medium",
            "type": "ticket_description",
            "created_at": now,
        }
        doc = Document(id=thread_id, page_content=descripcion, metadata=doc_metadata)
        vector_store = await _get_pg_vector_store()
        try:
            await vector_store.aadd_documents([doc])
        except Exception as e:
            print(f"[ERROR] crear_solicitud: embedding failed: {e}", flush=True)
            return ToolMessage(
                content=f"Error al crear el documento con embeddings: {str(e)}",
                status="error",
                tool_call_id=runtime.tool_call_id,
                name="crear_solicitud",
            )

        # Insert ticket with id == thread_id (one ticket per thread; use upsert to avoid duplicates on race)
        ticket_data = {
            "id": thread_id,
            "company_id": company_id,
            "customer_id": customer_id,
            "customer_email": customer_email,
            "channel": "chat",
            "priority": "medium",
            "title": titulo,
            "document_id": thread_id,
            "status": "open",
            "related_threads": [thread_id],
        }
        ticket_resp = await (
            supabase.table("tickets")
            .upsert(ticket_data, on_conflict="id")
            .execute()
        )

        if not ticket_resp.data:
            # Rollback document
            await supabase.table("documents").delete().eq("id", thread_id).execute()
            return ToolMessage(
                content="Error al crear la solicitud en la base de datos.",
                status="error",
                tool_call_id=runtime.tool_call_id,
                name="crear_solicitud",
            )

        print(f"[customer_chat] Ticket created — single ticket id (thread_id): {thread_id}", flush=True)
        return ToolMessage(
            content=(
                f"Solicitud '{titulo}' creada con éxito (ID: {thread_id}). "
                f"La notaría ha sido notificada y podrá atenderte en breve."
            ),
            tool_call_id=runtime.tool_call_id,
            name="crear_solicitud",
        )

    except Exception as e:
        import traceback; traceback.print_exc()
        return ToolMessage(
            content=f"Error al crear solicitud: {str(e)}",
            status="error",
            tool_call_id=runtime.tool_call_id,
            name="crear_solicitud",
        )


# ---------------------------------------------------------------------------
# Tool 3: leer_solicitud
# ---------------------------------------------------------------------------

@tool
async def leer_solicitud(runtime: ToolRuntime[AppContext]) -> ToolMessage:
    """
    Lee el estado y contenido de la solicitud vinculada al hilo actual.
    No requiere ningún argumento: el ticket_id es siempre el thread_id del hilo.
    """
    try:
        thread_id = get_thread_id()
        company_id = get_user().company_id

        if not thread_id or not company_id:
            return ToolMessage(
                content="Error: No se encontró thread_id o company_id en la configuración.",
                status="error",
                tool_call_id=runtime.tool_call_id,
                name="leer_solicitud",
            )

        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

        ticket_resp = (
            await supabase.table("tickets")
            .select("*")
            .eq("id", thread_id)
            .eq("company_id", company_id)
            .execute()
        )

        if not ticket_resp.data:
            return ToolMessage(
                content=(
                    "No existe una solicitud para este hilo todavía. "
                    "Usa crear_solicitud para iniciar tu trámite."
                ),
                tool_call_id=runtime.tool_call_id,
                name="leer_solicitud",
            )

        ticket = ticket_resp.data[0]
        doc_id = ticket.get("document_id")

        # Fetch document content
        description_text = ""
        if doc_id:
            doc_resp = (
                await supabase.table("documents")
                .select("content")
                .eq("id", doc_id)
                .execute()
            )
            if doc_resp.data:
                description_text = doc_resp.data[0].get("content", "")

        # Fetch actions
        actions_resp = (
            await supabase.table("actions")
            .select("title, description, status, created_by")
            .eq("ticket_id", thread_id)
            .order("created_at")
            .execute()
        )
        actions = actions_resp.data or []

        actions_text = "\nDocumentos / acciones requeridas: Ninguna"
        if actions:
            actions_text = f"\nDocumentos / acciones requeridas ({len(actions)}):\n"
            for i, a in enumerate(actions, 1):
                actions_text += (
                    f"  {i}. [{a.get('status', '?').upper()}] {a.get('title', '?')}"
                )
                if a.get("description"):
                    actions_text += f" — {a['description']}"
                actions_text += "\n"

        response = (
            f"Solicitud ID: {thread_id}\n"
            f"Título: {ticket.get('title', 'Sin título')}\n"
            f"Estado: {ticket.get('status', '?')}\n"
            f"Prioridad: {ticket.get('priority', '?')}\n"
            f"Creada: {ticket.get('created_at', '?')}\n"
            f"Actualizada: {ticket.get('updated_at', '?')}\n"
            f"\nDescripción:\n{description_text or '(sin descripción)'}"
            f"{actions_text}"
        )

        return ToolMessage(content=response.strip(), tool_call_id=runtime.tool_call_id, name="leer_solicitud")

    except Exception as e:
        import traceback; traceback.print_exc()
        return ToolMessage(
            content=f"Error al leer solicitud: {str(e)}",
            status="error",
            tool_call_id=runtime.tool_call_id,
            name="leer_solicitud",
        )


# ---------------------------------------------------------------------------
# Tool 4: actualizar_solicitud
# ---------------------------------------------------------------------------

class ActualizarSolicitudInput(BaseModel):
    titulo: Optional[str] = Field(None, description="Nuevo título (opcional)")
    descripcion: Optional[str] = Field(
        None,
        description="Nueva descripción que reemplaza la anterior (opcional)",
    )


@tool(args_schema=ActualizarSolicitudInput)
async def actualizar_solicitud(
    titulo: Optional[str] = None,
    descripcion: Optional[str] = None,
    runtime: ToolRuntime[AppContext] = None,
) -> ToolMessage:
    """
    Actualiza el título o la descripción de la solicitud del hilo actual.
    No acepta ticket_id: siempre opera sobre el ticket del thread actual.

    Args:
    - titulo: nuevo título (opcional)
    - descripcion: nueva descripción que reemplaza la anterior (opcional)
    """
    try:
        if not titulo and not descripcion:
            return ToolMessage(
                content="Debes proporcionar al menos título o descripción para actualizar.",
                status="error",
                tool_call_id=runtime.tool_call_id,
                name="actualizar_solicitud",
            )

        thread_id = get_thread_id()
        company_id = get_user().company_id

        if not thread_id or not company_id:
            return ToolMessage(
                content="Error: No se encontró thread_id o company_id en la configuración.",
                status="error",
                tool_call_id=runtime.tool_call_id,
                name="actualizar_solicitud",
            )

        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

        check = (
            await supabase.table("tickets")
            .select("id, document_id")
            .eq("id", thread_id)
            .eq("company_id", company_id)
            .execute()
        )
        if not check.data:
            return ToolMessage(
                content="No existe una solicitud para este hilo. Usa crear_solicitud primero.",
                status="error",
                tool_call_id=runtime.tool_call_id,
                name="actualizar_solicitud",
            )

        document_id = check.data[0].get("document_id")
        now = datetime.now(timezone.utc).isoformat()

        ticket_update: dict = {"updated_at": now}
        if titulo:
            ticket_update["title"] = titulo

        await supabase.table("tickets").update(ticket_update).eq("id", thread_id).execute()

        # Update document / embeddings when description changes
        if descripcion and document_id:
            doc_resp = (
                await supabase.table("documents")
                .select("metadata")
                .eq("id", document_id)
                .execute()
            )
            existing_meta = (
                doc_resp.data[0].get("metadata", {}) if doc_resp.data else {}
            )
            updated_meta = {**existing_meta, "updated_at": now, "modified": True}
            if titulo:
                updated_meta["title"] = titulo

            vector_store = await _get_pg_vector_store()
            updated_doc = Document(
                id=document_id, page_content=descripcion, metadata=updated_meta
            )
            await vector_store.aadd_documents([updated_doc])

        parts = []
        if titulo:
            parts.append(f"título → '{titulo}'")
        if descripcion:
            parts.append("descripción actualizada")

        return ToolMessage(
            content=f"Solicitud actualizada: {', '.join(parts)}.",
            tool_call_id=runtime.tool_call_id,
            name="actualizar_solicitud",
        )

    except Exception as e:
        import traceback; traceback.print_exc()
        return ToolMessage(
            content=f"Error al actualizar solicitud: {str(e)}",
            status="error",
            tool_call_id=runtime.tool_call_id,
            name="actualizar_solicitud",
        )


# ---------------------------------------------------------------------------
# Tool 5: solicitar_archivo
# ---------------------------------------------------------------------------

class SolicitarArchivoInput(BaseModel):
    nombre_documento: str = Field(
        description=(
            "Nombre o tipo del documento requerido "
            "(ej. 'DNI', 'Escritura de compraventa', 'Certificado catastral')"
        )
    )
    instrucciones: Optional[str] = Field(
        None,
        description="Instrucciones adicionales sobre el documento (formato, condiciones, fecha límite, etc.)",
    )


@tool(args_schema=SolicitarArchivoInput)
async def solicitar_archivo(
    nombre_documento: str,
    instrucciones: Optional[str] = None,
    runtime: ToolRuntime[AppContext] = None,
) -> ToolMessage:
    """
    Registra en la solicitud actual que el cliente debe aportar un documento
    específico. Crea una acción de tipo 'documento_requerido' en el ticket
    del hilo actual.
    Úsalo cuando el trámite exija que el cliente suba o envíe un archivo.

    Args:
    - nombre_documento: nombre o tipo del documento (DNI, escritura, etc.)
    - instrucciones: indicaciones extra sobre el documento (opcional)
    """
    try:
        thread_id = get_thread_id()
        company_id = get_user().company_id

        if not thread_id or not company_id:
            return ToolMessage(
                content="Error: No se encontró thread_id o company_id en la configuración.",
                status="error",
                tool_call_id=runtime.tool_call_id,
                name="solicitar_archivo",
            )

        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

        check = (
            await supabase.table("tickets")
            .select("id")
            .eq("id", thread_id)
            .eq("company_id", company_id)
            .execute()
        )
        if not check.data:
            return ToolMessage(
                content=(
                    "No existe una solicitud para este hilo. "
                    "Usa crear_solicitud primero."
                ),
                status="error",
                tool_call_id=runtime.tool_call_id,
                name="solicitar_archivo",
            )

        action_data = {
            "ticket_id": thread_id,
            "title": f"Documento requerido: {nombre_documento}",
            "description": instrucciones or f"El cliente debe aportar: {nombre_documento}",
            "status": "pending",
            "created_by": "customer_chat",
            "metadata": {
                "type": "documento_requerido",
                "nombre_documento": nombre_documento,
            },
        }
        await supabase.table("actions").insert(action_data).execute()

        msg = f"Se ha registrado que necesitas aportar: **{nombre_documento}**."
        if instrucciones:
            msg += f" {instrucciones}"

        return ToolMessage(content=msg, tool_call_id=runtime.tool_call_id, name="solicitar_archivo")

    except Exception as e:
        import traceback; traceback.print_exc()
        return ToolMessage(
            content=f"Error al registrar solicitud de archivo: {str(e)}",
            status="error",
            tool_call_id=runtime.tool_call_id,
            name="solicitar_archivo",
        )
