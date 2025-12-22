from pydantic import BaseModel, Field
from typing import TypedDict, Optional, Literal, List
from dataclasses import dataclass
from datetime import datetime
from langgraph.graph import MessagesState

@dataclass
class TriageContext:
    user_id: str
    tenant_id : str

class Ticket(BaseModel):
    id: str
    assigned_to : str
    title: str
    description: str = Field(description="Descripción exhaustiva y estructurada de una única solicitud, incluyendo detalles clave y contexto del correo electrónico o evento de calendario. Enfócate en el problema principal, remitente, urgencia y cualquier acción requerida.")
    related_threads: Optional[List[str]] = None
    status: Literal["open", "closed"] = "open"
    updated_at: datetime = Field(default_factory=datetime.now)

class InputTriageState(MessagesState):
    gmail_triage_event: dict
    outlook_triage_event: dict

class TriageState(InputTriageState):
    triage_context : str

class OutputTriageState(TypedDict):
    ticket: Ticket


