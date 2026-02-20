from typing import Optional, List
from langchain.tools import tool, ToolRuntime
from langchain_core.messages import ToolMessage
from langchain_core.documents import Document

from langgraph.graph.state import Command
from src.agent_triage.models import Ticket, Accion, CrearTicketInput, GestionarAccionesInput
from src.models import AppContext
import uuid
from datetime import datetime, timezone
import os

from src.embeddings import embeddings
from supabase import create_async_client
from langchain_postgres import PGVectorStore
from src.utils.vector_store import get_pg_engine

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SECRET_KEY")

from src.utils.tickets import get_ticket
from src.utils.config import get_company_id_from_config, get_user_id_from_config

@tool
async def buscar_tickets(query: str, runtime: ToolRuntime[AppContext]) -> ToolMessage:
    """
    Busca tickets relacionados usando búsqueda semántica basada en embeddings.
    Útil para encontrar tickets similares o relacionados con una consulta.

    Args:
    - query: texto de búsqueda para encontrar tickets relacionados
    """
    try:
        # Get company_id from config
        company_id = get_company_id_from_config()
        if not company_id:
            return ToolMessage(
                content="Error: No se encontró el ID de la compañía",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Use the search function from utils/vector_store.py which uses PGVectorStore
        from src.utils.vector_store import search
        result = await search(query=query, company_id=company_id, k=5)
        
        return ToolMessage(
            content=result,
            tool_call_id=runtime.tool_call_id
        )
        
    except Exception as e:
        import traceback
        print(f"[ERROR] buscar_tickets failed: {type(e).__name__}: {str(e)}", flush=True)
        return ToolMessage(
            content=f"Error al buscar tickets: {str(e)}. Por favor, intenta con términos más generales.",
            status="error",
            tool_call_id=runtime.tool_call_id
        )

@tool
async def leer_ticket(ticket_id: str, runtime: ToolRuntime[AppContext]) -> ToolMessage:
    """
    Lee el ticket seleccionado y su contenido desde la tabla de documentos.
    """
    try:
        # Get company_id from config
        company_id = get_company_id_from_config()
        if not company_id:
            return ToolMessage(
                content="Error: No se encontró el ID de la compañía",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        supabase_async = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        # Verify ticket belongs to user's company
        ticket_response = await supabase_async.table("tickets").select("*").eq("id", ticket_id).eq("company_id", company_id).execute()
        
        if not ticket_response.data or len(ticket_response.data) == 0:
            return ToolMessage(
                content=f"Error: No se encontró el ticket {ticket_id} o no pertenece a tu compañía",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        ticket = ticket_response.data[0]
        
        # Ticket can be a dict (from database) or a Ticket model
        # Handle both cases
        if isinstance(ticket, dict):
            ticket_id = ticket.get("id")
            document_id = ticket.get("document_id")
            ticket_title = ticket.get("title", "Sin título")
            ticket_customer = ticket.get("customer_email", "Desconocido")
            ticket_status = ticket.get("status", "unknown")
            ticket_channel = ticket.get("channel", "unknown")
            ticket_assigned = ticket.get("assigned_to", "No asignado")
            ticket_created = ticket.get("created_at", "")
        else:
            # Ticket is a Ticket model object
            ticket_id = ticket.id if hasattr(ticket, "id") else None
            document_id = ticket.document_id if hasattr(ticket, "document_id") else None
            ticket_title = ticket.title if hasattr(ticket, "title") else "Sin título"
            ticket_customer = ticket.customer_email if hasattr(ticket, "customer_email") else "Desconocido"
            ticket_status = ticket.status if hasattr(ticket, "status") else "unknown"
            ticket_channel = ticket.channel if hasattr(ticket, "channel") else "unknown"
            ticket_assigned = ticket.assigned_to if hasattr(ticket, "assigned_to") else "No asignado"
            ticket_created = str(ticket.created_at) if hasattr(ticket, "created_at") else ""
        
        if not document_id:
            return ToolMessage(
                content=f"Error: El ticket {ticket_id} no tiene contenido asociado",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        # Fetch document content and verify it belongs to the company
        doc_response = await supabase.table("documents").select("content, metadata").eq("id", document_id).execute()
        
        if not doc_response.data or len(doc_response.data) == 0:
            return ToolMessage(
                content=f"Error: No se pudo recuperar el contenido del ticket {ticket_id}",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        document = doc_response.data[0]
        content = document.get("content", "")
        metadata = document.get("metadata", {})
        
        # Verify document belongs to the company (check metadata)
        doc_company_id = metadata.get("company_id")
        if doc_company_id != company_id:
            return ToolMessage(
                content=f"Error: El documento del ticket {ticket_id} no pertenece a tu compañía",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Fetch actions for this ticket
        actions_response = await supabase.table("actions").select("*").eq("ticket_id", ticket_id).order("created_at").execute()
        actions = actions_response.data if actions_response.data else []
        
        # Format actions
        actions_text = ""
        if actions and len(actions) > 0:
            actions_text = "\n\nAcciones asociadas:\n"
            for idx, action in enumerate(actions, 1):
                action_title = action.get("title", "Sin título")
                action_description = action.get("description", "")
                action_status = action.get("status", "pending")
                action_created_by = action.get("created_by", "AI")
                actions_text += f"{idx}. [{action_status.upper()}] {action_title}"
                if action_description:
                    actions_text += f"\n   Descripción: {action_description}"
                actions_text += f"\n   Creada por: {action_created_by}\n"
        else:
            actions_text = "\n\nAcciones asociadas: Ninguna"
        
        # Format ticket information
        response = f"""
Ticket ID: {ticket_id}
Título: {ticket_title}
Cliente: {ticket_customer}
Estado: {ticket_status}
Canal: {ticket_channel}
Asignado a: {ticket_assigned}
Creado: {ticket_created}

Descripción:
{content}
{actions_text}
"""
        return ToolMessage(
            content=response.strip(),
            tool_call_id=runtime.tool_call_id
        )
    except Exception as e:
        print(f"[ERROR] leer_ticket failed: {type(e).__name__}: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return ToolMessage(
            content=f"Error al leer ticket: {str(e)}",
            status="error",
            tool_call_id=runtime.tool_call_id
        )

@tool(args_schema=CrearTicketInput)
async def crear_ticket(
    titulo: str, 
    descripcion: str,
    nombre_cliente: str,
    correo_cliente: str,
    prioridad: str = "medium",
    acciones: List[Accion] = [],
    runtime: ToolRuntime[AppContext] = None
) -> ToolMessage:
    """
    Crea un ticket con titulo, descripción, email del cliente y prioridad.
    Opcionalmente puede incluir acciones sugeridas para completar el ticket.
    """
    try:
        # Get company_id from config
        company_id = get_company_id_from_config()
        if not company_id:
            return ToolMessage(
                content="Error: No se encontró el ID de la compañía",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Get user_id from config
        user_id = get_user_id_from_config()
        if not user_id:
            # Try to get from metadata as fallback
            from langgraph.config import get_config
            config = get_config()
            metadata = config.get("metadata", {})
            user_id = metadata.get("user_id")
        
        if not user_id:
            return ToolMessage(
                content="Error: No se encontró el ID del usuario en la configuración",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Validate user_id is a valid UUID format
        try:
            uuid.UUID(user_id)
        except (ValueError, TypeError):
            return ToolMessage(
                content=f"Error: ID de usuario inválido: {user_id}",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Validate priority
        valid_priorities = ['low', 'medium', 'high', 'urgent']
        if prioridad not in valid_priorities:
            prioridad = 'medium'
        
        supabase_async = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        # Check if client already exists for this email and company
        print(f"[DEBUG] Checking if client exists for email {correo_cliente} and company {company_id}...", flush=True)
        existing_client = await supabase_async.table("clients").select("id, user_id").eq("email", correo_cliente).eq("company_id", company_id).execute()
        
        client_user_id = None
        if existing_client.data and len(existing_client.data) > 0:
            # Client already exists, use their user_id
            client_user_id = existing_client.data[0].get("user_id")
            print(f"[DEBUG] Client already exists with user_id: {client_user_id}", flush=True)
        else:
            # Client doesn't exist, create a new one
            # For clients created from email triggers, we'll use a generated UUID as userId
            # They can link it to their actual auth account later when they authenticate
            client_user_id = str(uuid.uuid4())
            print(f"[DEBUG] Creating new client with user_id: {client_user_id}", flush=True)
            
            client_data = {
                "user_id": client_user_id,
                "company_id": company_id,
                "full_name": nombre_cliente,
                "email": correo_cliente,
                "phone": None,
                "address": None,
                "company": None,
                "client_type": "individual",
                "tax_id": None,
                "notes": f"Cliente creado automáticamente desde ticket. Creado por: {user_id}",
                "is_active": True,
                "accepted_terms": False,
                "registration_completed": False,
            }
            
            try:
                client_response = await supabase_async.table("clients").insert(client_data).execute()
                if client_response.data and len(client_response.data) > 0:
                    print(f"[DEBUG] Client created successfully with id: {client_response.data[0].get('id')}", flush=True)
                else:
                    print(f"[WARNING] Client insert returned no data, but continuing...", flush=True)
            except Exception as e:
                print(f"[ERROR] Failed to create client: {str(e)}", flush=True)
                # Continue anyway - the ticket creation should still work
                # The client can be created manually later if needed
        
        # Generate a UUID for both ticket and document (they share the same ID)
        ticket_id = str(uuid.uuid4())
        print(f"[DEBUG] Creating ticket {ticket_id}, priority: {prioridad}", flush=True)
        
        # Create document with embeddings using PGVectorStore
        print(f"[DEBUG] Creating document with embeddings using PGVectorStore...", flush=True)
        
        # Prepare document metadata
        document_metadata = {
            "ticket_id": ticket_id,
            "company_id": company_id,
            "customer_email": correo_cliente,
            "title": titulo,
            "priority": prioridad,
            "type": "ticket_description",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Create LangChain Document object
        doc = Document(
            id=ticket_id,  # PGVectorStore uses id parameter directly
            page_content=descripcion,
            metadata=document_metadata
        )
        
        # Get PGEngine and create vector store using existing table schema
        pg_engine = await get_pg_engine()
        vector_store = await PGVectorStore.create(
            engine=pg_engine,
            table_name="documents",
            embedding_service=embeddings,
            # Map to existing column names
            id_column="id",
            content_column="content",
            embedding_column="embedding",
            metadata_json_column="metadata",
        )
        
        # Add document with embeddings (async, following PGVectorStore docs)
        try:
            await vector_store.aadd_documents([doc])
            print(f"[DEBUG] Document with embeddings created successfully", flush=True)
        except Exception as e:
            print(f"[ERROR] Failed to create document with embeddings: {str(e)}", flush=True)
            return ToolMessage(
                content=f"Error al crear el documento con embeddings: {str(e)}",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Now create ticket with document_id
        ticket_data = {
            "id": ticket_id,
            "company_id": company_id,
            "customer_email": correo_cliente,
            "channel": "email",
            "priority": prioridad,
            "assigned_to": user_id,
            "assigned_by": "AI",
            "title": titulo,
            "document_id": ticket_id,  # Reference to the document we just created
            "status": "open",
            "related_threads": [],
        }
        ticket_response = await supabase_async.table("tickets").insert(ticket_data).execute()
        
        if not ticket_response.data or len(ticket_response.data) == 0:
            # Rollback document creation if ticket creation fails (delete directly from DB)
            try:
                await supabase_async.table("documents").delete().eq("id", ticket_id).execute()
            except Exception as rollback_error:
                print(f"[ERROR] Failed to rollback document: {str(rollback_error)}", flush=True)
            return ToolMessage(
                content="Error al crear ticket en la base de datos",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Create actions if provided
        if acciones and len(acciones) > 0:
            print(f"[DEBUG] Creating {len(acciones)} actions for ticket {ticket_id}", flush=True)
            
            actions_to_insert = []
            for accion in acciones:
                action_data = {
                    "ticket_id": ticket_id,
                    "title": accion.title,
                    "description": accion.description,
                    "status": accion.status,  # Pydantic validates this is a valid Literal value
                    "created_by": "AI",
                    "metadata": accion.metadata,
                }
                actions_to_insert.append(action_data)
            
            if actions_to_insert:
                try:
                    actions_response = await supabase_async.table("actions").insert(actions_to_insert).execute()
                    print(f"[DEBUG] Created {len(actions_to_insert)} actions successfully", flush=True)
                except Exception as e:
                    print(f"[WARNING] Failed to create actions: {str(e)}", flush=True)
                    # Don't fail ticket creation if actions fail
        
        print(f"[DEBUG] Ticket created successfully", flush=True)
        actions_msg = f" con {len(acciones) if acciones else 0} acciones" if acciones else ""
        return ToolMessage(
            content=f"Ticket creado con id {ticket_id} (prioridad: {prioridad}) para el cliente {correo_cliente}, asignado al usuario {user_id}{actions_msg}",
            tool_call_id=runtime.tool_call_id
        )
    except Exception as e:
        print(f"[ERROR] crear_ticket failed: {type(e).__name__}: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return ToolMessage(
            content=f"Error al crear ticket: {str(e)}",
            status="error",
            tool_call_id=runtime.tool_call_id
        )

@tool
async def patch_ticket(
    ticket_id: str,
    prioridad: str = None, 
    descripcion: str = None, 
    rejection_reason: str = None, 
    runtime: ToolRuntime[AppContext] = None
) -> ToolMessage:
    """
    Actualiza un ticket existente. Puede actualizar la prioridad, la descripción o la razón de rechazo.
    NOTA: El estado del ticket se gestiona únicamente desde la aplicación, no puede ser modificado por el agente.
    NOTA: Para gestionar acciones (agregar, modificar), usa el tool 'gestionar_acciones'.

    Args:
    - ticket_id: ID del ticket a actualizar
    - prioridad: nueva prioridad del ticket ('low', 'medium', 'high', 'urgent') (opcional)
    - descripcion: nueva descripción del ticket (opcional)
    - rejection_reason: razón del rechazo (opcional, requerido si se rechaza)
    """
    try:
        supabase_async = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        if prioridad and prioridad not in ['low', 'medium', 'high', 'urgent']:
            return ToolMessage(
                content=f"Prioridad inválida: {prioridad}. Debe ser 'low', 'medium', 'high' o 'urgent'",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Get company_id from config
        company_id = get_company_id_from_config()
        if not company_id:
            return ToolMessage(
                content="Error: Usuario sin compañía asignada",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Verify ticket belongs to user's company
        ticket_check = await supabase_async.table("tickets").select("id, document_id").eq("id", ticket_id).eq("company_id", company_id).execute()
        
        if not ticket_check.data or len(ticket_check.data) == 0:
            return ToolMessage(
                content=f"Error: Ticket {ticket_id} no encontrado o no pertenece a tu compañía",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        ticket = ticket_check.data[0]
        document_id = ticket.get("document_id")
        
        # Always update updated_at if any field is being modified
        # Use UTC to stay consistent with Next.js which also stores UTC timestamps
        update_data = {
            "updated_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Update ticket metadata if priority or rejection_reason changed
        if prioridad:
            update_data["priority"] = prioridad
        
        if rejection_reason:
            update_data["rejection_reason"] = rejection_reason
        
        # Update the ticket with new data (always updates updated_at)
        update_response = await supabase_async.table("tickets").update(update_data).eq("id", ticket_id).eq("company_id", company_id).execute()
        
        if not update_response.data:
            return ToolMessage(
                content="Error al actualizar ticket",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Update document content if descripcion is provided
        if descripcion and document_id:
            print(f"[DEBUG] Updating document {document_id} for ticket {ticket_id}", flush=True)
            # Get existing document metadata and verify company ownership
            doc_response = await supabase_async.table("documents").select("metadata").eq("id", document_id).execute()
            
            if not doc_response.data or len(doc_response.data) == 0:
                return ToolMessage(
                    content=f"Error: No se encontró el documento {document_id}",
                    status="error",
                    tool_call_id=runtime.tool_call_id
                )
            
            existing_metadata = doc_response.data[0].get("metadata", {})
            
            # Verify document belongs to the company
            doc_company_id = existing_metadata.get("company_id")
            if doc_company_id != company_id:
                return ToolMessage(
                    content=f"Error: El documento {document_id} no pertenece a tu compañía",
                    status="error",
                    tool_call_id=runtime.tool_call_id
                )
            
            # Update metadata with modification timestamp and new priority if provided
            updated_metadata = {
                **existing_metadata,
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "modified": True
            }
            
            if prioridad:
                updated_metadata["priority"] = prioridad
            
            # Get PGEngine and create vector store for updating
            pg_engine = await get_pg_engine()
            vector_store = await PGVectorStore.create(
                engine=pg_engine,
                table_name="documents",
                embedding_service=embeddings,
                # Map to existing column names
                id_column="id",
                content_column="content",
                embedding_column="embedding",
                metadata_json_column="metadata",
            )
            
            # Upsert document with updated content and embeddings (keeping same ID)
            # PGVectorStore.aadd_documents will update existing document if ID matches
            try:
                updated_doc = Document(
                    id=document_id,  # Same ID preserves the document reference
                    page_content=descripcion,
                    metadata=updated_metadata
                )
                
                # This will upsert: update content, metadata, and regenerate embeddings
                await vector_store.aadd_documents([updated_doc])
                print(f"[DEBUG] Document {document_id} updated with new content and embeddings", flush=True)
            except Exception as e:
                print(f"[ERROR] Failed to update document with embeddings: {str(e)}", flush=True)
                import traceback
                traceback.print_exc()
                return ToolMessage(
                    content=f"Error al actualizar documento con embeddings: {str(e)}",
                    status="error",
                    tool_call_id=runtime.tool_call_id
                )
        
        response_msg = f"Ticket {ticket_id} actualizado correctamente"
        if prioridad:
            response_msg += f" - Prioridad: {prioridad}"
        if rejection_reason:
            response_msg += f" - Razón: {rejection_reason}"
        if descripcion:
            response_msg += f" - Descripción actualizada"
            
        return ToolMessage(
            content=response_msg,
            tool_call_id=runtime.tool_call_id
        )
    except Exception as e:
        print(f"[ERROR] patch_ticket failed for ticket_id={ticket_id}: {type(e).__name__}: {str(e)}", flush=True)
        return ToolMessage(
            content=f"Error al actualizar ticket: {str(e)}",
            status="error",
            tool_call_id=runtime.tool_call_id
        )

@tool
async def descartar_evento(
    titulo: str, 
    descripcion: str,
    nombre_cliente: str,
    correo_cliente: str,
    razon_descarte: str,
    prioridad: str = "low",
    runtime: ToolRuntime[AppContext] = None
) -> Command:
    """
    Descarta un evento creando un ticket con estado 'discarded' para referencia futura.
    Los usuarios pueden recuperar eventos descartados desde el frontend si resultan relevantes.
    Este tool finaliza el proceso de triage.

    Args:
    - titulo: título del evento/ticket
    - descripcion: descripción detallada del evento
    - nombre_cliente: nombre del cliente que envió el evento
    - correo_cliente: email del cliente que envió el evento
    - razon_descarte: razón por la cual se descarta el evento
    - prioridad: prioridad del ticket ('low', 'medium', 'high', 'urgent'). Por defecto 'low'
    """
    try:
        # Get company_id from config
        company_id = get_company_id_from_config()
        if not company_id:
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        ToolMessage(
                            content="Error: No se encontró el ID de la compañía",
                            status="error",
                            tool_call_id=runtime.tool_call_id
                        )
                    ]
                }
            )
        
        # Get user_id from config
        user_id = get_user_id_from_config()
        if not user_id:
            # Try to get from metadata as fallback
            from langgraph.config import get_config
            config = get_config()
            metadata = config.get("metadata", {})
            user_id = metadata.get("user_id")
        
        if not user_id:
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        ToolMessage(
                            content="Error: No se encontró el ID del usuario en la configuración",
                            status="error",
                            tool_call_id=runtime.tool_call_id
                        )
                    ]
                }
            )
        
        # Validate user_id is a valid UUID format
        try:
            uuid.UUID(user_id)
        except (ValueError, TypeError):
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        ToolMessage(
                            content=f"Error: ID de usuario inválido: {user_id}",
                            status="error",
                            tool_call_id=runtime.tool_call_id
                        )
                    ]
                }
            )
        
        # Validate priority
        valid_priorities = ['low', 'medium', 'high', 'urgent']
        if prioridad not in valid_priorities:
            prioridad = 'low'
        
        supabase_async = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        # Note: We don't create clients in descartar_evento - only crear_ticket creates new clients
        # Check if client exists for logging purposes only
        print(f"[DEBUG] Checking if client exists for email {correo_cliente} and company {company_id}...", flush=True)
        existing_client = await supabase_async.table("clients").select("id, user_id").eq("email", correo_cliente).eq("company_id", company_id).execute()
        
        if existing_client.data and len(existing_client.data) > 0:
            print(f"[DEBUG] Client exists with user_id: {existing_client.data[0].get('user_id')}", flush=True)
        else:
            print(f"[DEBUG] No existing client found for email {correo_cliente}. Client will not be created (only crear_ticket creates clients).", flush=True)
        
        # Generate a UUID for both ticket and document (they share the same ID)
        ticket_id = str(uuid.uuid4())
        print(f"[DEBUG] Creating discarded ticket {ticket_id}, priority: {prioridad}", flush=True)
        
        # Create document with embeddings using PGVectorStore
        print(f"[DEBUG] Creating document with embeddings using PGVectorStore...", flush=True)
        
        # Prepare document metadata with discard reason
        document_metadata = {
            "ticket_id": ticket_id,
            "company_id": company_id,
            "customer_email": correo_cliente,
            "title": titulo,
            "priority": prioridad,
            "type": "ticket_description",
            "status": "discarded",
            "discard_reason": razon_descarte,
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Create LangChain Document object
        doc = Document(
            id=ticket_id,
            page_content=descripcion,
            metadata=document_metadata
        )
        
        # Get PGEngine and create vector store using existing table schema
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
        
        # Add document with embeddings
        try:
            await vector_store.aadd_documents([doc])
            print(f"[DEBUG] Document with embeddings created successfully", flush=True)
        except Exception as e:
            print(f"[ERROR] Failed to create document with embeddings: {str(e)}", flush=True)
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        ToolMessage(
                            content=f"Error al crear el documento con embeddings: {str(e)}",
                            status="error",
                            tool_call_id=runtime.tool_call_id
                        )
                    ]
                }
            )
        
        # Create ticket with status="discarded"
        print(f"[DEBUG] Inserting discarded ticket into database...", flush=True)
        ticket_data = {
            "id": ticket_id,
            "company_id": company_id,
            "customer_email": correo_cliente,
            "channel": "email",
            "priority": prioridad,
            "assigned_to": user_id,
            "assigned_by": "AI",
            "title": titulo,
            "document_id": ticket_id,
            "status": "discarded",  # Key difference: discarded status
            "rejection_reason": razon_descarte,  # Store discard reason
            "related_threads": [],
        }
        
        print(f"[DEBUG] Ticket data prepared: {ticket_data}", flush=True)
        ticket_response = await supabase_async.table("tickets").insert(ticket_data).execute()
        
        if not ticket_response.data or len(ticket_response.data) == 0:
            print(f"[ERROR] Failed to insert discarded ticket, rolling back document", flush=True)
            # Rollback document creation if ticket creation fails
            try:
                await supabase_async.table("documents").delete().eq("id", ticket_id).execute()
            except Exception as rollback_error:
                print(f"[ERROR] Failed to rollback document: {str(rollback_error)}", flush=True)
            
            return Command(
                goto="__end__",
                update={
                    "messages": [
                        ToolMessage(
                            content="Error al crear ticket descartado en la base de datos",
                            status="error",
                            tool_call_id=runtime.tool_call_id
                        )
                    ]
                }
            )
        
        print(f"[DEBUG] Discarded ticket created successfully", flush=True)
        
        # End the graph execution
        return Command(
            goto="__end__",
            update={
                "messages": [
                    ToolMessage(
                        content=f"Evento descartado y guardado como ticket {ticket_id}. Razón: {razon_descarte}. El ticket puede ser recuperado desde el frontend si es necesario.",
                        tool_call_id=runtime.tool_call_id
                    )
                ]
            }
        )
        
    except Exception as e:
        print(f"[ERROR] descartar_evento failed: {type(e).__name__}: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return Command(
            goto="__end__",
            update={
                "messages": [
                    ToolMessage(
                        content=f"Error al descartar evento: {str(e)}",
                        status="error",
                        tool_call_id=runtime.tool_call_id
                    )
                ]
            }
        )

@tool
async def merge_tickets(ticket_ids: list[str], runtime: ToolRuntime[AppContext] = None) -> ToolMessage:
    """
    Fusiona múltiples tickets en uno nuevo. Combina los documentos y elimina los tickets fusionados.
    
    Args:
    - ticket_ids: Lista de IDs de tickets a fusionar (mínimo 2)
    
    Crea un nuevo ticket con:
    - El título más largo de los tickets fusionados
    - La prioridad más alta
    - El estado más avanzado
    - La combinación de todos los contenidos de documentos
    - Todos los threads relacionados combinados
    - El email del cliente del primer ticket
    """
    try:
        if not ticket_ids or len(ticket_ids) < 2:
            return ToolMessage(
                content="Error: Se requieren al menos 2 tickets para fusionar",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Get company_id from config
        company_id = get_company_id_from_config()
        if not company_id:
            return ToolMessage(
                content="Error: Usuario sin compañía asignada",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Get user_id from config
        user_id = get_user_id_from_config()
        if not user_id:
            # Try to get from metadata as fallback
            from langgraph.config import get_config
            config = get_config()
            metadata = config.get("metadata", {})
            user_id = metadata.get("user_id")
        
        if not user_id:
            return ToolMessage(
                content="Error: No se encontró el ID del usuario en la configuración",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Validate user_id is a valid UUID format
        try:
            uuid.UUID(user_id)
        except (ValueError, TypeError):
            return ToolMessage(
                content=f"Error: ID de usuario inválido: {user_id}",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        supabase_async = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        # Generate new UUID for merged ticket
        merged_ticket_id = str(uuid.uuid4())
        print(f"[DEBUG] Merging {len(ticket_ids)} tickets into new ticket {merged_ticket_id}", flush=True)
        
        # Fetch all tickets to merge
        tickets_response = await supabase_async.table("tickets").select("*").in_("id", ticket_ids).eq("company_id", company_id).execute()
        
        if not tickets_response.data or len(tickets_response.data) != len(ticket_ids):
            return ToolMessage(
                content=f"Error: No se encontraron todos los tickets o no pertenecen a tu compañía",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        tickets = tickets_response.data
        
        # Fetch all documents and verify they belong to the company
        documents_response = await supabase_async.table("documents").select("*").in_("id", ticket_ids).execute()
        
        if not documents_response.data or len(documents_response.data) != len(ticket_ids):
            return ToolMessage(
                content="Error: No se encontraron todos los documentos asociados a los tickets",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Verify all documents belong to the company
        documents = {}
        for doc in documents_response.data:
            doc_id = doc["id"]
            doc_metadata = doc.get("metadata", {})
            doc_company_id = doc_metadata.get("company_id")
            
            if doc_company_id != company_id:
                return ToolMessage(
                    content=f"Error: El documento {doc_id} no pertenece a tu compañía",
                    status="error",
                    tool_call_id=runtime.tool_call_id
                )
            
            documents[doc_id] = doc
        
        # Merge documents content
        merged_content_parts = []
        merged_metadata = {
            "ticket_id": merged_ticket_id,
            "company_id": company_id,
            "type": "ticket_description",
            "merged_from": ticket_ids,
            "merged_at": datetime.now(timezone.utc).isoformat()
        }
        
        # Priority order: urgent > high > medium > low
        priority_order = {"urgent": 4, "high": 3, "medium": 2, "low": 1}
        highest_priority = "medium"
        highest_priority_value = 2
        
        # Collect all content and metadata
        for ticket in tickets:
            ticket_id = ticket["id"]
            if ticket_id in documents:
                doc = documents[ticket_id]
                content = doc.get("content", "")
                metadata = doc.get("metadata", {})
                
                # Add content with separator
                if content:
                    title = metadata.get("title", ticket.get("title", "Sin título"))
                    merged_content_parts.append(f"=== {title} (ID: {ticket_id}) ===\n{content}\n")
                
                # Merge metadata
                if metadata:
                    merged_metadata.update({
                        k: v for k, v in metadata.items() 
                        if k not in ["ticket_id", "company_id", "type"]
                    })
                
                # Track highest priority
                ticket_priority = ticket.get("priority", "medium")
                if priority_order.get(ticket_priority, 2) > highest_priority_value:
                    highest_priority = ticket_priority
                    highest_priority_value = priority_order.get(ticket_priority, 2)
        
        merged_content = "\n\n".join(merged_content_parts)
        
        # Determine merged ticket properties
        # Use longest title
        titles = [t.get("title", "") for t in tickets if t.get("title")]
        merged_title = max(titles, key=len) if titles else "Ticket Fusionado"
        
        # Use most advanced status: closed > ongoing > open
        status_order = {"closed": 3, "ongoing": 2, "open": 1, "deleted": 0}
        merged_status = "open"
        merged_status_value = 1
        for ticket in tickets:
            ticket_status = ticket.get("status", "open")
            if status_order.get(ticket_status, 1) > merged_status_value:
                merged_status = ticket_status
                merged_status_value = status_order.get(ticket_status, 1)
        
        # Merge related threads
        all_threads = set()
        for ticket in tickets:
            all_threads.update(ticket.get("related_threads", []))
        
        # Get customer email from first ticket
        customer_email = tickets[0].get("customer_email", "unknown")
        
        # Add customer email and title to merged metadata
        merged_metadata["customer_email"] = customer_email
        merged_metadata["title"] = merged_title
        merged_metadata["priority"] = highest_priority
        merged_metadata["created_at"] = datetime.now(timezone.utc).isoformat()
        
        # Create new merged document with embeddings using PGVectorStore
        print(f"[DEBUG] Creating merged document {merged_ticket_id} with embeddings...", flush=True)
        
        # Get PGEngine and create vector store using existing table schema
        pg_engine = await get_pg_engine()
        vector_store = await PGVectorStore.create(
            engine=pg_engine,
            table_name="documents",
            embedding_service=embeddings,
            # Map to existing column names
            id_column="id",
            content_column="content",
            embedding_column="embedding",
            metadata_json_column="metadata",
        )
        
        # Create LangChain Document object
        merged_doc = Document(
            id=merged_ticket_id,
            page_content=merged_content,
            metadata=merged_metadata
        )
        
        # Add document with embeddings (async, following PGVectorStore docs)
        try:
            await vector_store.aadd_documents([merged_doc])
            print(f"[DEBUG] Merged document with embeddings created successfully", flush=True)
        except Exception as e:
            print(f"[ERROR] Failed to create merged document with embeddings: {str(e)}", flush=True)
            return ToolMessage(
                content=f"Error al crear documento fusionado con embeddings: {str(e)}",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Create new merged ticket
        print(f"[DEBUG] Creating merged ticket {merged_ticket_id}...", flush=True)
        ticket_data = {
            "id": merged_ticket_id,
            "company_id": company_id,
            "customer_email": customer_email,
            "channel": tickets[0].get("channel", "email"),
            "priority": highest_priority,
            "assigned_to": user_id,
            "assigned_by": "AI",
            "title": merged_title,
            "document_id": merged_ticket_id,
            "status": merged_status,
            "related_threads": list(all_threads),
        }
        ticket_response = await supabase_async.table("tickets").insert(ticket_data).execute()
        
        if not ticket_response.data or len(ticket_response.data) == 0:
            # Rollback document creation if ticket creation fails (delete directly from DB)
            try:
                await supabase_async.table("documents").delete().eq("id", merged_ticket_id).execute()
            except Exception as rollback_error:
                print(f"[ERROR] Failed to rollback merged document: {str(rollback_error)}", flush=True)
            return ToolMessage(
                content="Error al crear ticket fusionado",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Delete original tickets and documents
        print(f"[DEBUG] Deleting {len(ticket_ids)} original tickets and documents...", flush=True)
        await supabase_async.table("tickets").delete().in_("id", ticket_ids).execute()
        
        # Delete original documents directly from database (using custom id column)
        try:
            await supabase_async.table("documents").delete().in_("id", ticket_ids).execute()
            print(f"[DEBUG] Original documents deleted from database", flush=True)
        except Exception as e:
            print(f"[WARNING] Failed to delete some documents: {str(e)}", flush=True)
        
        response_msg = (
            f"Tickets fusionados exitosamente en nuevo ticket {merged_ticket_id} ({merged_title}). "
            f"Se fusionaron {len(ticket_ids)} tickets: {', '.join(ticket_ids)}. "
            f"Prioridad: {highest_priority}, Estado: {merged_status}"
        )
        
        print(f"[DEBUG] Merge completed successfully", flush=True)
        return ToolMessage(
            content=response_msg,
            tool_call_id=runtime.tool_call_id
        )
        
    except Exception as e:
        print(f"[ERROR] merge_tickets failed: {type(e).__name__}: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return ToolMessage(
            content=f"Error al fusionar tickets: {str(e)}",
            status="error",
            tool_call_id=runtime.tool_call_id
        )

@tool
async def leer_acciones(ticket_id: str, runtime: ToolRuntime[AppContext]) -> ToolMessage:
    """
    Lee todas las acciones asociadas a un ticket. Útil para verificar acciones existentes antes de agregar nuevas.
    
    Args:
    - ticket_id: ID del ticket del cual se quieren leer las acciones
    """
    try:
        # Get company_id from config
        company_id = get_company_id_from_config()
        if not company_id:
            return ToolMessage(
                content="Error: No se encontró el ID de la compañía",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        supabase_async = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        # Verify ticket exists and belongs to user's company
        ticket_check = await supabase_async.table("tickets").select("id, title").eq("id", ticket_id).eq("company_id", company_id).execute()
        
        if not ticket_check.data or len(ticket_check.data) == 0:
            return ToolMessage(
                content=f"Error: Ticket {ticket_id} no encontrado o no pertenece a tu compañía",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        ticket = ticket_check.data[0]
        ticket_title = ticket.get("title", "Sin título")
        
        # Fetch all actions for this ticket
        actions_response = await supabase_async.table("actions").select("*").eq("ticket_id", ticket_id).order("created_at").execute()
        actions = actions_response.data if actions_response.data else []
        
        # Format actions response
        if not actions or len(actions) == 0:
            response = f"Ticket: {ticket_title} (ID: {ticket_id})\n\nNo hay acciones asociadas a este ticket."
        else:
            response = f"Ticket: {ticket_title} (ID: {ticket_id})\n\nAcciones encontradas ({len(actions)}):\n"
            for idx, action in enumerate(actions, 1):
                action_title = action.get("title", "Sin título")
                action_description = action.get("description", "")
                action_status = action.get("status", "pending")
                action_created_by = action.get("created_by", "AI")
                action_id = action.get("id", "")
                response += f"\n{idx}. ID: {action_id}\n"
                response += f"   Título: {action_title}\n"
                if action_description:
                    response += f"   Descripción: {action_description}\n"
                response += f"   Estado: {action_status}\n"
                response += f"   Creada por: {action_created_by}\n"
        
        return ToolMessage(
            content=response.strip(),
            tool_call_id=runtime.tool_call_id
        )
    except Exception as e:
        print(f"[ERROR] leer_acciones failed: {type(e).__name__}: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return ToolMessage(
            content=f"Error al leer acciones: {str(e)}",
            status="error",
            tool_call_id=runtime.tool_call_id
        )

@tool(args_schema=GestionarAccionesInput)
async def gestionar_acciones(
    ticket_id: str,
    acciones: List[Accion],
    modo: str = "append",
    runtime: ToolRuntime[AppContext] = None
) -> ToolMessage:
    """
    Gestiona las acciones de un ticket existente. Permite agregar nuevas acciones o insertarlas.
    
    NOTA: Este tool primero lee las acciones existentes para evitar duplicados.
          En modo 'append', se agregan las nuevas acciones al final sin eliminar las existentes.
          En modo 'insert', se insertan las nuevas acciones (comportamiento similar a append).
    """
    try:
        # Get company_id from config
        company_id = get_company_id_from_config()
        if not company_id:
            return ToolMessage(
                content="Error: No se encontró el ID de la compañía",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Get user_id from config (for created_by)
        user_id = get_user_id_from_config()
        if not user_id:
            # Try to get from metadata as fallback
            from langgraph.config import get_config
            config = get_config()
            metadata = config.get("metadata", {})
            user_id = metadata.get("user_id")
        
        # Use 'AI' as created_by if user_id not available
        created_by = user_id if user_id else "AI"
        
        supabase_async = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        # Verify ticket exists and belongs to user's company
        ticket_check = await supabase_async.table("tickets").select("id").eq("id", ticket_id).eq("company_id", company_id).execute()
        
        if not ticket_check.data or len(ticket_check.data) == 0:
            return ToolMessage(
                content=f"Error: Ticket {ticket_id} no encontrado o no pertenece a tu compañía",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # IMPORTANT: First read existing actions to avoid duplicates
        print(f"[DEBUG] Reading existing actions for ticket {ticket_id} to avoid duplicates...", flush=True)
        existing_actions_response = await supabase_async.table("actions").select("title, description").eq("ticket_id", ticket_id).execute()
        existing_actions = existing_actions_response.data if existing_actions_response.data else []
        
        # Create a set of existing action titles (normalized for comparison)
        existing_titles = set()
        for existing_action in existing_actions:
            title = existing_action.get("title", "").strip().lower()
            if title:
                existing_titles.add(title)
        
        print(f"[DEBUG] Found {len(existing_actions)} existing actions. Existing titles: {existing_titles}", flush=True)
        
        # Validate and prepare actions
        # Both 'append' and 'insert' modes add actions without deleting existing ones
        if not acciones or len(acciones) == 0:
            return ToolMessage(
                content="Error: Se requiere al menos una acción",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        actions_to_insert = []
        skipped_duplicates = []
        
        for accion in acciones:
            # Normalize title for comparison
            normalized_title = accion.title.strip().lower()
            
            # Check if action with same title already exists
            if normalized_title in existing_titles:
                skipped_duplicates.append(accion.title)
                print(f"[DEBUG] Skipping duplicate action: {accion.title}", flush=True)
                continue
            
            action_data = {
                "ticket_id": ticket_id,
                "title": accion.title,
                "description": accion.description,
                "status": accion.status,  # Pydantic validates this is a valid Literal value
                "created_by": created_by,
                "metadata": accion.metadata,
            }
            actions_to_insert.append(action_data)
        
        if not actions_to_insert:
            if skipped_duplicates:
                return ToolMessage(
                    content=f"Todas las acciones propuestas ya existen en el ticket {ticket_id}. Acciones duplicadas: {', '.join(skipped_duplicates)}. Usa 'leer_acciones' para ver las acciones existentes.",
                    status="error",
                    tool_call_id=runtime.tool_call_id
                )
            return ToolMessage(
                content="Error: No se pudo procesar ninguna acción válida",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
        # Insert actions
        try:
            actions_response = await supabase_async.table("actions").insert(actions_to_insert).execute()
            print(f"[DEBUG] Created {len(actions_to_insert)} actions successfully in mode '{modo}'", flush=True)
            
            modo_msg = "agregadas" if modo == "append" else "insertadas"
            response_msg = f"Se {modo_msg} {len(actions_to_insert)} acción(es) al ticket {ticket_id}"
            
            if skipped_duplicates:
                response_msg += f". Se omitieron {len(skipped_duplicates)} acción(es) duplicada(s): {', '.join(skipped_duplicates)}"
            
            return ToolMessage(
                content=response_msg,
                tool_call_id=runtime.tool_call_id
            )
        except Exception as e:
            print(f"[ERROR] Failed to insert actions: {str(e)}", flush=True)
            import traceback
            traceback.print_exc()
            return ToolMessage(
                content=f"Error al insertar acciones: {str(e)}",
                status="error",
                tool_call_id=runtime.tool_call_id
            )
        
    except Exception as e:
        print(f"[ERROR] gestionar_acciones failed: {type(e).__name__}: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return ToolMessage(
            content=f"Error al gestionar acciones: {str(e)}",
            status="error",
            tool_call_id=runtime.tool_call_id
        )