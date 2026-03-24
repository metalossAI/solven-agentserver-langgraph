"""
Deterministic triage graph.

Flow:
  START
    → research_event   (deep agent → TriageDecision stored in state)
    → persist_ticket   (create / patch / discard to Supabase)
    ↘ id_invalid  → research_event  (bounded retry with error feedback)
    → download_attachments  (skipped on discard)
    → finalize_output
    → END
"""

import os
import uuid
from datetime import datetime, timezone
from typing import Any

from dotenv import load_dotenv
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage
from langchain_openrouter.chat_models import ChatOpenRouter
from langchain_postgres import PGVectorStore
from langgraph.config import get_config
from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime
from supabase import create_async_client

from deepagents import SubAgent, SubAgentMiddleware, create_deep_agent
from src.embeddings import embeddings
from src.utils.vector_store import get_pg_engine

from src.agent_triage.models import (
    Accion,
    AttachmentDownloadResult,
    AttachmentDownloadResults,
    DeterministicTriageState,
    SuggestedActionsResponse,
    TicketDraft,
    TriageContext,
    TriageDecision,
)
from src.agent_email.gmail_tools import GmailAttachmentSpec, _download_one_attachment
from src.agent_email.outlook_tools import OutlookAttachmentSpec, _download_one_outlook_attachment
from src.agent_triage.tools import (
    buscar_tickets,
    gmail_tools_triage,
    leer_ticket,
    outlook_tools_triage,
)
from src.backend import SolvenS3Backend
from src.utils.config import get_event_message_from_config, get_user

load_dotenv()

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SECRET_KEY")

_TRIAGE_MODEL = ChatOpenRouter(
    model="x-ai/grok-4.1-fast",
    api_key=os.getenv("OPENROUTER_API_KEY"),
)

# ---------------------------------------------------------------------------
# System prompt injected into the research deep agent
# ---------------------------------------------------------------------------
_RESEARCH_SYSTEM_PROMPT = """\
## ROL
Gestor de expedientes notariales. Procesas eventos (emails) y determinas si se debe crear, actualizar o descartar un ticket.

## OBJETIVO
Para cada evento debes:
1. Leer el contenido del email y sus adjuntos (OBLIGATORIO antes de decidir).
2. Buscar tickets similares con la herramienta `buscar_tickets`.
3. Si hay coincidencia, leer el ticket existente con `leer_ticket`.
4. Producir un único objeto `TriageDecision` con:
   - `action`: "create" | "patch" | "discard"
   - `ticket`: objeto con todos los datos del expediente y la lista de adjuntos encontrados.

## REGLAS CRÍTICAS
- Sin analizar adjuntos → NO decidir.
- Los adjuntos son la única fuente de verdad documental.
- Un trámite = un único ticket. Evita duplicados.
- Para `patch`, ticket.id DEBE ser el UUID exacto del ticket existente (obtenido de `buscar_tickets` o `leer_ticket`).
- Para `create`, deja ticket.id vacío (el sistema genera el UUID).
- Si el evento no es una solicitud notarial procesable → acción "discard".

## ADJUNTOS — CAMPOS OBLIGATORIOS
Para cada adjunto en ticket.attachments debes rellenar:
- `source`: "gmail" o "outlook" según el proveedor del email.
- `message_id`: ID del mensaje de email que contiene el adjunto. Obtenlo de las herramientas de listado.
- `attachment_id`: ID específico del adjunto dentro del mensaje. Obtenlo de gmail_list_threads o outlook_list_attachments.
- `filename`: nombre del archivo tal como aparece en el email.
Sin `message_id` y `attachment_id` el adjunto NO podrá descargarse en el siguiente paso.

## FLUJO
1. Leer el email y obtener message_id + lista de adjuntos con sus attachment_ids.
2. Llamar `buscar_tickets` con el resumen del evento.
3. Si hay coincidencia clara → `patch` con el ticket.id correcto.
4. Si no hay coincidencia → `create`.
5. Si el evento es irrelevante (newsletter, OOO, spam, etc.) → `discard`.
"""


def _state_get(state: DeterministicTriageState | dict[str, Any], key: str, default: Any = None) -> Any:
    if isinstance(state, dict):
        return state.get(key, default)
    return getattr(state, key, default)


# ---------------------------------------------------------------------------
# Node 1: research_event
# Deep agent with email + ticket tools → produces TriageDecision
# ---------------------------------------------------------------------------
async def research_event_node(
    state: DeterministicTriageState, runtime: Runtime[TriageContext]
) -> dict[str, Any]:
    event_message = get_event_message_from_config() or ""

    # On retry: inject the ID correction feedback into the human message
    id_validation_error: str | None = _state_get(state, "id_validation_error")
    if id_validation_error:
        input_message = (
            f"{event_message}\n\n"
            f"[CORRECCIÓN REQUERIDA — ticket.id inválido]\n"
            f"{id_validation_error}\n"
            "Busca el ticket correcto con `buscar_tickets` y usa su UUID exacto en ticket.id."
        )
    else:
        input_message = event_message

    research_agent = create_deep_agent(
        model=_TRIAGE_MODEL,
        tools=[buscar_tickets, leer_ticket],
        subagents=[
            SubAgent(
                name="asistente_correo",
                description="Asiste sobre tareas de correo en Gmail y Outlook",
                system_prompt="",
                model=_TRIAGE_MODEL,
                tools=gmail_tools_triage + outlook_tools_triage
            )
        ],
        response_format=TriageDecision,
        system_prompt=_RESEARCH_SYSTEM_PROMPT,
        context_schema=TriageContext,
    )

    result = await research_agent.ainvoke(
        {"messages": [HumanMessage(content=input_message)]}
    )

    triage: TriageDecision = result["structured_response"]
    return {"triage": triage}


# ---------------------------------------------------------------------------
# Node 2: persist_ticket
# Commits the triage decision to Supabase.
# For create: generates a server-side UUID (ignores any model-provided id).
# For patch: validates that ticket.id exists and belongs to this company.
# For discard: upserts with status=discarded (uses server-side UUID too).
# ---------------------------------------------------------------------------
async def persist_ticket_node(
    state: DeterministicTriageState, runtime: Runtime[TriageContext]
) -> dict[str, Any]:
    triage: TriageDecision | None = _state_get(state, "triage")
    if triage is None:
        return {
            "last_persist_status": "error",
            "errors": [*(_state_get(state, "errors", []) or []), "TriageDecision ausente en estado."],
        }

    user = get_user()
    company_id = user.company_id
    if not company_id:
        return {
            "last_persist_status": "error",
            "errors": [*(_state_get(state, "errors", []) or []), "Usuario sin compañía asignada."],
        }

    action = triage.action
    ticket: TicketDraft = triage.ticket
    retry_count = int(_state_get(state, "retry_count", 0) or 0)
    max_retries = int(_state_get(state, "max_retries", 2) or 2)

    # --- ID resolution ---
    if action in ("create", "discard"):
        # Always generate a fresh server-side UUID; never trust the model for creates.
        ticket_id = str(uuid.uuid4())
    else:
        # patch: model must supply a valid existing UUID
        ticket_id = ticket.id or ""

    if action == "patch":
        if not ticket_id:
            return {
                "last_persist_status": "id_invalid",
                "id_validation_error": "No se proporcionó ticket.id para la acción patch.",
                "retry_count": retry_count + 1,
                "errors": [*(_state_get(state, "errors", []) or []), "Patch sin ticket.id."],
            }
        try:
            uuid.UUID(ticket_id)
        except ValueError:
            return {
                "last_persist_status": "id_invalid",
                "id_validation_error": f"ticket.id '{ticket_id}' no es un UUID válido.",
                "retry_count": retry_count + 1,
                "errors": [*(_state_get(state, "errors", []) or []), f"UUID inválido: {ticket_id}"],
            }
        try:
            supabase_async = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
            existing = (
                await supabase_async.table("tickets")
                .select("id")
                .eq("id", ticket_id)
                .eq("company_id", company_id)
                .execute()
            )
            if not existing.data:
                return {
                    "last_persist_status": "id_invalid",
                    "id_validation_error": (
                        f"ticket.id '{ticket_id}' no existe o no pertenece a company_id '{company_id}'."
                    ),
                    "retry_count": retry_count + 1,
                    "errors": [
                        *(_state_get(state, "errors", []) or []),
                        "Ticket objetivo no encontrado para patch.",
                    ],
                }
        except Exception as e:
            return {
                "last_persist_status": "error",
                "errors": [*(_state_get(state, "errors", []) or []), f"Error validando ticket.id: {e}"],
            }

    # --- Document upsert (required by tickets.document_id NOT NULL) ---
    # The document uses the same UUID as the ticket so document_id = ticket_id.
    # PGVectorStore.aadd_documents upserts on id, so this is safe for both create and patch.
    try:
        doc = Document(
            id=ticket_id,
            page_content=ticket.description or ticket.title,
            metadata={
                "ticket_id": ticket_id,
                "company_id": company_id,
                "customer_email": ticket.customer_email or "",
                "title": ticket.title,
                "priority": ticket.priority,
                "type": "ticket_description",
                "created_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        pg_engine = await get_pg_engine()
        vector_store = await PGVectorStore.create(
            engine=pg_engine,
            table_name="documents",
            embedding_service=embeddings,
            id_column="id",
            content_column="content",
            embedding_column="embedding",
            metadata_json_column="metadata",
        )
        await vector_store.aadd_documents([doc])
    except Exception as e:
        return {
            "last_persist_status": "error",
            "errors": [*(_state_get(state, "errors", []) or []), f"Error creando documento: {e}"],
        }

    # --- Ticket upsert ---
    now_iso = datetime.now(timezone.utc).isoformat()
    status = "discarded" if action == "discard" else "open"
    payload = {
        "id": ticket_id,
        "company_id": company_id,
        "title": ticket.title,
        "customer_email": ticket.customer_email or "",
        "priority": ticket.priority,
        "status": status,
        "channel": "email",
        "assigned_by": "AI",
        "assigned_to": user.id,
        "related_threads": ticket.related_ticket_ids,
        "document_id": ticket_id,
        "updated_at": now_iso,
    }

    try:
        supabase_async = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        await supabase_async.table("tickets").upsert(payload, on_conflict="id").execute()
    except Exception as e:
        return {
            "last_persist_status": "error",
            "errors": [*(_state_get(state, "errors", []) or []), f"Error en upsert: {e}"],
        }

    # Align runtime context so downstream nodes and backends use the correct workspace.
    if getattr(runtime, "context", None) is not None:
        runtime.context.workspace_id = ticket_id
        runtime.context.ticket = {"id": ticket_id, "title": ticket.title, "status": status}

    # Write the resolved id back into the triage ticket so downstream nodes see it.
    ticket.id = ticket_id

    return {
        "triage": triage,
        "persisted_ticket_id": ticket_id,
        "workspace_id": ticket_id,
        "last_persist_status": "ok",
        "id_validation_error": None,
        "retry_count": 0,
        "max_retries": max_retries,
    }


# ---------------------------------------------------------------------------
# Routing after persist
# ---------------------------------------------------------------------------
def route_after_persist(state: DeterministicTriageState) -> str:
    status = _state_get(state, "last_persist_status", "ok")
    retry_count = int(_state_get(state, "retry_count", 0) or 0)
    max_retries = int(_state_get(state, "max_retries", 2) or 2)

    if status == "id_invalid" and retry_count <= max_retries:
        return "research_event"

    triage: TriageDecision | None = _state_get(state, "triage")
    if triage and triage.action == "discard":
        return "finalize_output"

    return "download_attachments"


# ---------------------------------------------------------------------------
# Node 3: download_attachments
# Hybrid downloader:
# 1) Download-only deep agent (gmail/outlook tools + filesystem backend)
# 2) Deterministic direct fallback if agent fails
# ---------------------------------------------------------------------------

_DOWNLOAD_SYSTEM_PROMPT = """\
Eres un especialista en descarga de adjuntos de correo electrónico para expedientes notariales.

Tu única tarea es descargar cada adjunto listado y guardarlo en el workspace.

HERRAMIENTAS:
- Gmail: usa `gmail_get_attachment(attachment=GmailAttachmentSpec(message_id=..., attachment_id=..., file_name=...))`
         o `gmail_download_attachments` para descargar varios a la vez.
- Outlook: usa `outlook_download_attachment(message_id=..., attachment_id=..., file_name=...)`
           o `outlook_download_attachments` para varios.

REGLAS:
- Usa SIEMPRE `message_id` y `attachment_id` del listado proporcionado para identificar cada adjunto.
- Guarda cada archivo en /adjuntos/<filename> usando el filesystem del workspace.
- Si `attachment_id` o `message_id` están vacíos para un adjunto, márcalo como error sin intentar descargarlo.
- No realices ninguna otra tarea: solo descarga y guarda.
- Reporta el resultado de CADA adjunto individualmente (ok=True/False, path o error).
"""


async def _download_attachments_direct_fallback(
    attachments: list[Any], runtime: Runtime[TriageContext]
) -> list[AttachmentDownloadResult]:
    results: list[AttachmentDownloadResult] = []
    for attachment in attachments:
        source = (attachment.source or "").strip().lower()
        message_id = (attachment.message_id or "").strip()
        attachment_id = (attachment.attachment_id or "").strip()
        filename = (attachment.filename or "").strip()

        if source not in {"gmail", "outlook"}:
            results.append(
                AttachmentDownloadResult(
                    attachment_id=attachment_id or "unknown",
                    filename=filename or "unknown",
                    ok=False,
                    error=f"source inválido: '{source or 'vacío'}'",
                )
            )
            continue

        if not message_id or not attachment_id or not filename:
            missing = []
            if not message_id:
                missing.append("message_id")
            if not attachment_id:
                missing.append("attachment_id")
            if not filename:
                missing.append("filename")
            results.append(
                AttachmentDownloadResult(
                    attachment_id=attachment_id or "unknown",
                    filename=filename or "unknown",
                    ok=False,
                    error=f"Campos obligatorios ausentes: {', '.join(missing)}",
                )
            )
            continue

        try:
            if source == "gmail":
                provider_result = await _download_one_attachment(
                    runtime,
                    GmailAttachmentSpec(
                        message_id=message_id,
                        attachment_id=attachment_id,
                        file_name=filename,
                    ),
                )
            else:
                provider_result = await _download_one_outlook_attachment(
                    runtime,
                    OutlookAttachmentSpec(
                        message_id=message_id,
                        attachment_id=attachment_id,
                        file_name=filename,
                    ),
                )

            ok = bool(provider_result.get("success"))
            path = provider_result.get("path")
            error = provider_result.get("message") if not ok else None
            result_attachment_id = provider_result.get("attachment_id") or attachment_id
            result_filename = provider_result.get("file_name") or filename
            results.append(
                AttachmentDownloadResult(
                    attachment_id=str(result_attachment_id),
                    filename=str(result_filename),
                    ok=ok,
                    path=str(path) if path else None,
                    error=str(error) if error else None,
                )
            )
        except Exception as e:
            results.append(
                AttachmentDownloadResult(
                    attachment_id=attachment_id,
                    filename=filename,
                    ok=False,
                    error=f"Error descargando adjunto: {e}",
                )
            )
    return results


async def download_attachments_node(
    state: DeterministicTriageState, runtime: Runtime[TriageContext]
) -> dict[str, Any]:
    triage: TriageDecision | None = _state_get(state, "triage")
    workspace_id = _state_get(state, "workspace_id")

    if not workspace_id:
        return {
            "errors": [
                *(_state_get(state, "errors", []) or []),
                "workspace_id no disponible antes de descarga de adjuntos.",
            ]
        }

    attachments = triage.ticket.attachments if triage else []
    if not attachments:
        return {"download_results": []}

    event_message = get_event_message_from_config() or ""
    attachment_lines = "\n".join(
        f"- [{i + 1}] filename={a.filename}"
        f" | source={a.source}"
        f" | message_id={a.message_id or 'DESCONOCIDO'}"
        f" | attachment_id={a.attachment_id or 'DESCONOCIDO'}"
        for i, a in enumerate(attachments)
    )
    input_message = (
        f"Contexto del email original:\n{event_message}\n\n"
        f"Adjuntos a descargar ({len(attachments)} en total):\n{attachment_lines}\n\n"
        "Usa message_id y attachment_id de cada adjunto para descargarlo con la herramienta correspondiente "
        "(gmail_get_attachment o outlook_download_attachment según source). "
        "Guarda cada archivo en /adjuntos/<filename> y devuelve un resultado por adjunto."
    )

    download_agent = create_deep_agent(
        model=_TRIAGE_MODEL,
        backend=SolvenS3Backend,
        tools=gmail_tools_triage + outlook_tools_triage,
        response_format=AttachmentDownloadResults,
        system_prompt=_DOWNLOAD_SYSTEM_PROMPT,
        context_schema=TriageContext,
    )

    try:
        result = await download_agent.ainvoke(
            {"messages": [HumanMessage(content=input_message)]}
        )
        structured = result.get("structured_response")
        if isinstance(structured, AttachmentDownloadResults):
            return {"download_results": structured.results}
    except Exception as e:
        # Fall through to deterministic fallback path.
        error_msg = f"Error en agente de descarga (fallback directo aplicado): {e}"
        fallback_results = await _download_attachments_direct_fallback(attachments, runtime)
        return {
            "download_results": fallback_results,
            "errors": [*(_state_get(state, "errors", []) or []), error_msg],
        }

    fallback_results = await _download_attachments_direct_fallback(attachments, runtime)
    return {"download_results": fallback_results}


# ---------------------------------------------------------------------------
# Node 4: suggest_actions
# Uses structured LLM output to generate a DAG of suggested actions for
# the persisted ticket, then writes them to Supabase `actions` table.
# Skipped for discard or when persist failed.
# ---------------------------------------------------------------------------

_ACTIONS_SYSTEM_PROMPT = """\
Eres un gestor experto en expedientes notariales.
Dado el contenido de un ticket, genera la lista mínima y completa de acciones necesarias para completar el trámite.

REGLAS:
- Cada acción debe tener un 'key' único en snake_case (e.g. 'solicitar_nota_simple').
- Usa 'depends_on' para expresar dependencias reales: si acción B necesita el resultado de A, incluye el key de A en depends_on de B.
- Asigna 'order' sólo a acciones sin dependencias para indicar cuáles pueden ejecutarse en paralelo y en qué orden preferente.
- Las acciones deben ser concretas y accionables (no genéricas como "revisar").
- Incluye todas las fases: verificación documental, comunicaciones, redacción, firma, registro, entrega.
"""

_ACTIONS_HUMAN_TMPL = """\
Ticket: {title}
Descripción: {description}
Adjuntos detectados: {attachments}
Tipo de acto: {act_type}

Genera las acciones necesarias para completar este expediente notarial.
"""


async def suggest_actions_node(
    state: DeterministicTriageState, runtime: Runtime[TriageContext]
) -> dict[str, Any]:
    triage: TriageDecision | None = _state_get(state, "triage")
    ticket_id = _state_get(state, "persisted_ticket_id")

    if not triage or triage.action == "discard" or not ticket_id:
        return {"suggested_actions": []}

    if _state_get(state, "last_persist_status") != "ok":
        return {"suggested_actions": []}

    ticket = triage.ticket
    attachment_names = [a.filename for a in ticket.attachments] if ticket.attachments else []
    act_type = (ticket.metadata or {}).get("tipo_acto", "desconocido")

    human_msg = _ACTIONS_HUMAN_TMPL.format(
        title=ticket.title,
        description=ticket.description or "",
        attachments=", ".join(attachment_names) if attachment_names else "ninguno",
        act_type=act_type,
    )

    structured_llm = _TRIAGE_MODEL.with_structured_output(SuggestedActionsResponse)
    try:
        result: SuggestedActionsResponse = await structured_llm.ainvoke(
            [
                {"role": "system", "content": _ACTIONS_SYSTEM_PROMPT},
                {"role": "user", "content": human_msg},
            ]
        )
    except Exception as e:
        return {
            "suggested_actions": [],
            "errors": [*(_state_get(state, "errors", []) or []), f"Error generando acciones: {e}"],
        }

    acciones: list[Accion] = result.acciones or []

    # Persist to DB — two-pass to build the dependency graph
    user = get_user()
    company_id = user.company_id
    if company_id and acciones:
        try:
            supabase_async = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

            # Pass 1: insert all actions; capture returned IDs so we can resolve keys → IDs.
            rows = [
                {
                    "ticket_id": ticket_id,
                    "key": a.key,
                    "title": a.title,
                    "description": a.description,
                    "status": a.status,
                    "order": a.order,
                    "created_by": "AI",
                    "metadata": a.metadata or {},
                }
                for a in acciones
            ]
            insert_resp = await supabase_async.table("actions").insert(rows).execute()
            inserted: list[dict] = insert_resp.data or []

            # Build key → db_id map from what Supabase returned.
            key_to_id: dict[str, str] = {
                row["key"]: row["id"]
                for row in inserted
                if row.get("key") and row.get("id")
            }

            # Pass 2: insert dependency edges for actions that have depends_on.
            dep_rows: list[dict] = []
            for a in acciones:
                if not a.depends_on or not a.key:
                    continue
                action_db_id = key_to_id.get(a.key)
                if not action_db_id:
                    continue
                for dep_key in a.depends_on:
                    dep_db_id = key_to_id.get(dep_key)
                    if dep_db_id:
                        dep_rows.append({
                            "action_id": action_db_id,
                            "depends_on_action_id": dep_db_id,
                        })

            if dep_rows:
                await supabase_async.table("action_dependencies").insert(dep_rows).execute()

        except Exception as e:
            return {
                "suggested_actions": acciones,
                "errors": [*(_state_get(state, "errors", []) or []), f"Error persistiendo acciones: {e}"],
            }

    return {"suggested_actions": acciones}


# ---------------------------------------------------------------------------
# Node 5: finalize_output
# Emits a summary AIMessage for the thread.
# ---------------------------------------------------------------------------
async def finalize_output_node(
    state: DeterministicTriageState, runtime: Runtime[TriageContext]
) -> dict[str, Any]:
    triage: TriageDecision | None = _state_get(state, "triage")
    persisted_ticket_id = _state_get(state, "persisted_ticket_id")
    downloads: list[AttachmentDownloadResult] = _state_get(state, "download_results", []) or []
    suggested_actions: list[Accion] = _state_get(state, "suggested_actions", []) or []
    errors = _state_get(state, "errors", []) or []

    action = triage.action if triage else "discard"
    ok_downloads = sum(1 for d in downloads if d.ok)
    fail_downloads = sum(1 for d in downloads if not d.ok)

    summary = (
        f"Triage completado. acción={action}, "
        f"ticket_id={persisted_ticket_id or 'n/a'}, "
        f"adjuntos_ok={ok_downloads}, adjuntos_error={fail_downloads}, "
        f"acciones_sugeridas={len(suggested_actions)}."
    )
    if errors:
        summary += f" Errores: {' | '.join(errors)}"

    return {"messages": [AIMessage(content=summary)]}


# ---------------------------------------------------------------------------
# Graph wiring
# ---------------------------------------------------------------------------
builder = StateGraph(DeterministicTriageState, context_schema=TriageContext)

builder.add_node("research_event", research_event_node)
builder.add_node("persist_ticket", persist_ticket_node)
builder.add_node("download_attachments", download_attachments_node)
builder.add_node("suggest_actions", suggest_actions_node)
builder.add_node("finalize_output", finalize_output_node)

builder.add_edge(START, "research_event")
builder.add_edge("research_event", "persist_ticket")
builder.add_conditional_edges(
    "persist_ticket",
    route_after_persist,
    {
        "research_event": "research_event",
        "download_attachments": "download_attachments",
        "finalize_output": "finalize_output",
    },
)
builder.add_edge("download_attachments", "suggest_actions")
builder.add_edge("suggest_actions", "finalize_output")
builder.add_edge("finalize_output", END)

graph = builder.compile()
