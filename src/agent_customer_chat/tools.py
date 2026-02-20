from langchain.tools import tool, ToolRuntime
from langgraph.types import interrupt
import uuid
from datetime import datetime, timezone
import os
from supabase import create_async_client

from src.models import AppContext
from src.utils.config import get_user_id_from_config, get_company_id_from_config, get_thread_id_from_config

SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_SERVICE_KEY = os.environ.get("SUPABASE_SECRET_KEY")

@tool
async def listar_solicitudes_cliente(runtime: ToolRuntime):
    """
    Lista todas las solicitudes (tickets) del cliente actual.
    Retorna información sobre el estado, título y descripción de cada solicitud.
    """
    try:
        user_id = get_user_id_from_config()
        company_id = get_company_id_from_config()
        
        if not user_id:
            return "Error: No se encontró el ID del usuario"
        
        if not company_id:
            return "Error: No se encontró el ID de la compañía"
        
        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        # Fetch tickets for this customer (customer_id = user_id) and company
        response = await supabase.table('tickets').select('*').eq('company_id', company_id).eq('customer_id', user_id).order('created_at', desc=True).execute()
        
        if not response.data:
            return "No tienes solicitudes registradas."
        
        tickets_list = []
        for ticket in response.data:
            status_map = {
                'open': 'Abierta',
                'ongoing': 'En proceso',
                'closed': 'Cerrada'
            }
            assigned_info = f"\n  Asignado a: {ticket.get('assigned_to', 'Sin asignar')}" if ticket.get('assigned_to') else ""
            tickets_list.append(
                f"• ID: {ticket['id']}\n"
                f"  Título: {ticket['title']}\n"
                f"  Estado: {status_map.get(ticket['status'], ticket['status'])}{assigned_info}\n"
                f"  Descripción: {ticket['description'][:100]}...\n"
                f"  Creada: {ticket['created_at']}"
            )
        
        return f"Tienes {len(tickets_list)} solicitud(es):\n\n" + "\n\n".join(tickets_list)
        
    except Exception as e:
        print(f"[listar_solicitudes_cliente] Error: {str(e)}", flush=True)
        return f"Error al listar solicitudes: {str(e)}"

@tool
async def crear_solicitud(titulo: str, descripcion: str, runtime: ToolRuntime[AppContext]):
    """
    Crea una nueva solicitud (ticket) para el cliente.
    
    Args:
        titulo: Título breve de la solicitud
        descripcion: Descripción detallada de la solicitud
    """
    try:
        user_id = get_user_id_from_config()
        company_id = get_company_id_from_config()
        
        if not user_id:
            return "Error: No se encontró el ID del usuario"
        
        if not company_id:
            return "Error: No se encontró el ID de la compañía"
        
        if not titulo or not descripcion:
            return "Error: Se requiere título y descripción para crear la solicitud"
        
        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        # Create ticket for the company, tracking customer_id but not assigning it
        ticket_data = {
            'id': get_thread_id_from_config(),
            'company_id': company_id,
            'customer_id': user_id,  # Track which customer created it
            'channel': 'chat',  # Ticket created via customer chat
            'assigned_to': None,  # Not assigned yet - company will assign it
            'assigned_by': 'customer',
            'title': titulo,
            'description': descripcion,
            'status': 'open',
            'related_threads': [],
            'created_at': datetime.now(timezone.utc).isoformat(),
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        
        response = await supabase.table('tickets').insert(ticket_data).execute()
        
        if response.data:
            ticket = response.data[0]
            return f"✅ Solicitud creada exitosamente:\n\nID: {ticket['id']}\nTítulo: {ticket['title']}\nEstado: Abierta\n\nUn miembro del equipo revisará tu solicitud y la asignará pronto."
        else:
            return "Error al crear la solicitud. Por favor intenta nuevamente."
            
    except Exception as e:
        print(f"[crear_solicitud] Error: {str(e)}", flush=True)
        return f"Error al crear solicitud: {str(e)}"

@tool
async def actualizar_solicitud(ticket_id: str, nuevo_estado: str, runtime: ToolRuntime):
    """
    Actualiza el estado de una solicitud existente.
    
    Args:
        ticket_id: ID de la solicitud a actualizar
        nuevo_estado: Nuevo estado ('open', 'ongoing', 'closed')
    """
    try:
        user_id = get_user_id_from_config()
        company_id = get_company_id_from_config()
        
        if not user_id:
            return "Error: No se encontró el ID del usuario"
        
        if not company_id:
            return "Error: No se encontró el ID de la compañía"
        
        if nuevo_estado not in ['open', 'ongoing', 'closed']:
            return "Error: Estado inválido. Usa 'open', 'ongoing' o 'closed'"
        
        supabase = await create_async_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
        
        # Verify ticket belongs to this customer (created by them)
        ticket_response = await supabase.table('tickets').select('*').eq('id', ticket_id).eq('company_id', company_id).eq('customer_id', user_id).execute()
        
        if not ticket_response.data:
            return "Error: No se encontró la solicitud o no tienes permiso para actualizarla"
        
        # Update ticket status
        update_data = {
            'status': nuevo_estado,
            'updated_at': datetime.now(timezone.utc).isoformat()
        }
        
        response = await supabase.table('tickets').update(update_data).eq('id', ticket_id).execute()
        
        if response.data:
            status_map = {
                'open': 'Abierta',
                'ongoing': 'En proceso',
                'closed': 'Cerrada'
            }
            return f"✅ Solicitud actualizada exitosamente a estado: {status_map.get(nuevo_estado, nuevo_estado)}"
        else:
            return "Error al actualizar la solicitud"
            
    except Exception as e:
        print(f"[actualizar_solicitud] Error: {str(e)}", flush=True)
        return f"Error al actualizar solicitud: {str(e)}"


@tool
async def solicitar_archivo(tipo_documento: str, descripcion: str, runtime: ToolRuntime):
    """
    Solicita al cliente que suba un archivo específico necesario para procesar su solicitud.
    Este tool pausa la ejecución hasta que el cliente suba el archivo solicitado.
    
    Args:
        tipo_documento: Tipo de documento requerido (ej: "DNI", "pasaporte", "escritura", "contrato")
        descripcion: Descripción clara de qué archivo se necesita y para qué se usará
    
    Returns:
        Información del archivo subido: nombre, tamaño, tipo, y ruta en S3
    """
    user_id = get_user_id_from_config()
    thread_id = get_thread_id_from_config()
    
    if not user_id:
        return "Error: No se encontró el ID del usuario"
    
    if not thread_id:
        return "Error: No se encontró el ID del hilo de conversación"
    
    # Create interrupt payload to request file upload
    interrupt_payload = {
        "action": "file_upload_request",
        "tipo_documento": tipo_documento,
        "descripcion": descripcion,
        "thread_id": thread_id,
        "user_id": user_id,
    }
    
    # Interrupt execution and wait for file upload
    # interrupt() raises an exception that LangGraph catches - it doesn't return normally
    # When resumed, the value passed to resume will be available here
    uploaded_file_info = interrupt(interrupt_payload)
    
    # When resumed, uploaded_file_info will contain the file information
    if isinstance(uploaded_file_info, dict):
        filename = uploaded_file_info.get("filename", "archivo")
        s3_key = uploaded_file_info.get("s3_key", "")
        file_size = uploaded_file_info.get("size", 0)
        content_type = uploaded_file_info.get("content_type", "")
        
        return f"✅ Archivo recibido exitosamente:\n\n" \
               f"Tipo: {tipo_documento}\n" \
               f"Nombre: {filename}\n" \
               f"Tamaño: {file_size} bytes\n" \
               f"Ubicación: {s3_key}\n\n" \
               f"Procederé a analizar el documento."
    else:
        return f"Error: Información de archivo inválida recibida"


# @tool
# def listar_horarios_disponibles(runtime: ToolRuntime):
#     pass

# @tool
# def crear_evento_calendario(runtime: ToolRuntime):
#     pass

# @tool
# def actualizar_evento_calendario(runtime: ToolRuntime):
#     pass