from pydantic import BaseModel, Field
from typing import TypedDict, Optional, Literal, List
from dataclasses import dataclass
from datetime import datetime
from langgraph.graph import MessagesState
from src.models import AppContext

class TriageContext(AppContext):
    """Triage agent context that extends AppContext with triage-specific fields."""
    event_message: Optional[str] = Field(default=None, description="The event message for triage processing")

class Ticket(BaseModel):
    id: str
    assigned_to : str
    title: str
    description: str = Field(description="Descripción exhaustiva y estructurada de una única solicitud, incluyendo detalles clave y contexto del correo electrónico o evento de calendario. Enfócate en el problema principal, remitente, urgencia y cualquier acción requerida.")
    related_threads: Optional[List[str]] = None
    status: Literal["open", "closed"] = "open"
    updated_at: datetime = Field(default_factory=datetime.now)

# Pydantic models for actions
class Accion(BaseModel):
    """Modelo para una acción sugerida de un ticket."""
    title: str = Field(description="Título de la acción (requerido)")
    description: Optional[str] = Field(default=None, description="Descripción detallada de la acción (opcional)")
    status: Literal["pending", "completed", "blocked", "errored"] = Field(default="pending", description="Estado de la acción. Por defecto 'pending'")
    metadata: Optional[dict] = Field(default=None, description="Metadatos adicionales en formato JSON (opcional)")

class CrearTicketInput(BaseModel):
    """Schema de entrada para crear un ticket con acciones opcionales."""
    titulo: str = Field(description="Título del ticket")
    descripcion: str = Field(description="Descripción detallada del ticket")
    nombre_cliente: str = Field(description="Nombre del cliente")
    correo_cliente: str = Field(description="Email del cliente que envió la solicitud")
    prioridad: str = Field(default="medium", description="Prioridad del ticket: 'low', 'medium', 'high', 'urgent'. Por defecto 'medium'")
    acciones: Optional[List[Accion]] = Field(default=None, description="Lista opcional de acciones sugeridas para completar el ticket")

class GestionarAccionesInput(BaseModel):
    """Schema de entrada para gestionar acciones de un ticket."""
    ticket_id: str = Field(description="ID del ticket al que se le gestionarán las acciones")
    acciones: List[Accion] = Field(description="Lista de acciones a agregar")
    modo: Literal["append", "insert"] = Field(default="append", description="Modo de gestión: 'append' para agregar al final, 'insert' para insertar acciones. Por defecto 'append'")

class InputTriageState(MessagesState):
    gmail_triage_event: dict
    outlook_triage_event: dict

class TriageState(InputTriageState):
    ticket : Ticket

class OutputTriageState(TypedDict):
    ticket: Ticket
