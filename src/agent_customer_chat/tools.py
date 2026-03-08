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
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from datetime import datetime, timezone
import json
import os

from src.embeddings import embeddings
from supabase import create_async_client
from langchain_postgres import PGVectorStore
from src.utils.vector_store import get_pg_engine
from src.models import AppContext
from src.utils.config import get_user, get_thread_id
from src.agent_customer_chat.models import Accion

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
    """Campos actualizables de la solicitud (todos opcionales; al menos uno requerido)."""
    titulo: Optional[str] = Field(None, description="Nuevo título de la solicitud (opcional)")
    descripcion: Optional[str] = Field(
        None,
        description="Nueva descripción que reemplaza la anterior (opcional)",
    )
    prioridad: Optional[str] = Field(
        None,
        description="Nueva prioridad: 'low', 'medium', 'high', 'urgent' (opcional)",
    )
    status: Optional[str] = Field(
        None,
        description="Nuevo estado del ticket: 'open', 'ongoing', 'closed' (opcional)",
    )
    acciones: Optional[List[Accion]] = Field(
        None,
        description="Lista de documentos/acciones requeridas; reemplaza las actuales (opcional)",
    )


@tool(args_schema=ActualizarSolicitudInput)
async def actualizar_solicitud(
    titulo: Optional[str] = None,
    descripcion: Optional[str] = None,
    prioridad: Optional[str] = None,
    status: Optional[str] = None,
    acciones: Optional[List[Accion]] = None,
    runtime: ToolRuntime[AppContext] = None,
) -> ToolMessage:
    """
    Actualiza la solicitud del hilo actual. Permite modificar título, descripción,
    prioridad, estado y lista de acciones. Siempre opera sobre el ticket del thread actual.

    Args:
    - titulo: nuevo título (opcional)
    - descripcion: nueva descripción (opcional)
    - prioridad: 'low' | 'medium' | 'high' | 'urgent' (opcional)
    - status: 'open' | 'ongoing' | 'closed' (opcional)
    - acciones: lista de acciones/documentos requeridos; reemplaza la lista actual (opcional)
    """
    try:
        if not any([titulo, descripcion, prioridad, status, acciones]):
            return ToolMessage(
                content="Debes proporcionar al menos un campo para actualizar: título, descripción, prioridad, status o acciones.",
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
        if titulo is not None:
            ticket_update["title"] = titulo
        if prioridad is not None:
            ticket_update["priority"] = prioridad
        if status is not None:
            ticket_update["status"] = status

        await supabase.table("tickets").update(ticket_update).eq("id", thread_id).eq("company_id", company_id).execute()

        # Update document / embeddings when description or title/priority change
        if (descripcion or titulo or prioridad) and document_id:
            doc_resp = (
                await supabase.table("documents")
                .select("metadata, content")
                .eq("id", document_id)
                .execute()
            )
            existing_meta = (
                doc_resp.data[0].get("metadata", {}) if doc_resp.data else {}
            )
            existing_content = doc_resp.data[0].get("content", "") if doc_resp.data else ""
            updated_meta = {**existing_meta, "updated_at": now, "modified": True}
            if titulo is not None:
                updated_meta["title"] = titulo
            if prioridad is not None:
                updated_meta["priority"] = prioridad
            new_content = descripcion if descripcion is not None else existing_content

            vector_store = await _get_pg_vector_store()
            updated_doc = Document(
                id=document_id, page_content=new_content, metadata=updated_meta
            )
            await vector_store.aadd_documents([updated_doc])

        # Replace actions if provided
        if acciones is not None:
            await supabase.table("actions").delete().eq("ticket_id", thread_id).execute()
            if acciones:
                actions_to_insert = [
                    {
                        "ticket_id": thread_id,
                        "title": a.title,
                        "description": a.description,
                        "status": a.status,
                        "created_by": "customer_chat",
                        "metadata": a.metadata,
                    }
                    for a in acciones
                ]
                await supabase.table("actions").insert(actions_to_insert).execute()

        parts = []
        if titulo is not None:
            parts.append(f"título → '{titulo}'")
        if descripcion is not None:
            parts.append("descripción actualizada")
        if prioridad is not None:
            parts.append(f"prioridad → '{prioridad}'")
        if status is not None:
            parts.append(f"estado → '{status}'")
        if acciones is not None:
            parts.append(f"acciones → {len(acciones)} documento(s)/acción(es)")

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

@tool(description="Solicita al usuario uno o más archivos")
async def solicitar_archivo(
    runtime: ToolRuntime[AppContext] = None,
) -> ToolMessage:
    """
    Solicita al cliente que suba un archivo específico necesario para procesar su solicitud.
    """
    interrupt_payload = {
        "action": "file_upload_request",
        "thread_id": get_thread_id(),
        "user_id": get_user().id,
    }
    resume_value = interrupt(interrupt_payload)

    # Rejection: user cancelled or declined (frontend may send "no" / "rejected" / "cancelled")
    if resume_value is None or resume_value == "":
        return ToolMessage(
            content="El usuario no subió ningún archivo (cancelado o sin respuesta).",
            tool_call_id=runtime.tool_call_id,
            name="solicitar_archivo",
        )
    if isinstance(resume_value, str) and resume_value.strip().lower() in ("no", "rejected", "cancelled", "cancel"):
        return ToolMessage(
            content="El usuario canceló la subida del archivo.",
            tool_call_id=runtime.tool_call_id,
            name="solicitar_archivo",
        )

    # Success: resume is JSON string or dict (single file) or list of file infos (multiple files)
    file_infos: List[dict] = []
    if isinstance(resume_value, dict) and ("filename" in resume_value or "s3_key" in resume_value):
        file_infos = [resume_value]
    elif isinstance(resume_value, list) and all(
        isinstance(x, dict) and ("filename" in x or "s3_key" in x) for x in resume_value
    ):
        file_infos = list(resume_value)
    elif isinstance(resume_value, str):
        try:
            parsed = json.loads(resume_value)
            if isinstance(parsed, dict) and ("filename" in parsed or "s3_key" in parsed):
                file_infos = [parsed]
            elif isinstance(parsed, list) and all(
                isinstance(x, dict) and ("filename" in x or "s3_key" in x) for x in parsed
            ):
                file_infos = list(parsed)
        except (json.JSONDecodeError, TypeError):
            pass

    if file_infos:
        if len(file_infos) == 1:
            fi = file_infos[0]
            filename = fi.get("filename", "archivo")
            size = fi.get("size")
            size_str = f" ({size} bytes)" if size is not None else ""
            content = f"Archivo subido correctamente: {filename}{size_str}. Clave: {fi.get('s3_key', '')}"
        else:
            parts = [
                f"- {f.get('filename', 'archivo')} ({f.get('s3_key', '')})"
                for f in file_infos
            ]
            content = f"Archivos subidos correctamente ({len(file_infos)}):\n" + "\n".join(parts)
        return ToolMessage(
            content=content,
            tool_call_id=runtime.tool_call_id,
            name="solicitar_archivo",
        )

    # Fallback: unexpected resume shape
    return ToolMessage(
        content=f"Respuesta del usuario sobre el archivo: {resume_value}",
        tool_call_id=runtime.tool_call_id,
        name="solicitar_archivo",
    )

