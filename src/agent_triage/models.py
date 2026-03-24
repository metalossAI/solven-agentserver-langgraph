import re
from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, TypedDict

from langgraph.graph import MessagesState
from pydantic import BaseModel, Field, model_validator

from src.models import AppContext

TriageAction = Literal["discard", "create", "patch"]
EmailProvider = Literal["gmail", "outlook", "unknown"]


class TriageContext(AppContext):
    """Triage agent context that extends AppContext with triage-specific fields."""

    event_message: Optional[str] = Field(default=None, description="The event message for triage processing")


class AttachmentRef(BaseModel):
    """
    Attachment reference — only the fields required by both Gmail and Outlook download tools.

    GmailAttachmentSpec  / outlook_download_attachment both need:
        message_id, attachment_id, file_name (→ filename here)

    source tells the download agent which provider tool to call.
    """

    source: EmailProvider = Field(
        default_factory=str,
        description="Email provider: 'gmail' or 'outlook'. Determines which download tool to use.",
    )
    message_id: str = Field(
        default_factory=str,
        description="ID of the email message containing this attachment.",
    )
    attachment_id: str = Field(
        default_factory=str,
        description="Provider attachment ID within the message (from gmail_list_threads / outlook_list_attachments).",
    )
    filename: str = Field(
        default_factory=str,
        description="Attachment filename — passed as file_name to the download tools.",
    )


class Ticket(BaseModel):
    id: str
    assigned_to: str
    title: str
    description: str = Field(
        description=(
            "Descripción exhaustiva y estructurada de una única solicitud, incluyendo detalles clave "
            "y contexto del correo electrónico o evento de calendario."
        )
    )
    related_threads: Optional[List[str]] = None
    status: Literal["open", "closed"] = "open"
    updated_at: datetime = Field(default_factory=datetime.now)


class TicketDraft(BaseModel):
    """Structured ticket payload generated before persistence."""

    id: Optional[str] = None
    title: str
    description: str
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    priority: Literal["low", "medium", "high", "urgent"] = "medium"
    status: Literal["open", "discarded", "merged"] = "open"
    action: TriageAction
    reason: str = ""
    related_ticket_ids: List[str] = Field(default_factory=list)
    attachments: List[AttachmentRef] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TicketCandidate(BaseModel):
    """Candidate ticket found during research/search stage."""

    id: str
    score: Optional[float] = None
    summary: str = ""
    title: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class ActionDecision(BaseModel):
    """Decision object from the action selection node."""

    action: TriageAction
    reason: str
    target_ticket_ids: List[str] = Field(default_factory=list)
    confidence: Optional[float] = None


class AttachmentDownloadResult(BaseModel):
    """Result of a single attachment download operation."""

    attachment_id: str
    filename: str
    ok: bool
    path: Optional[str] = None
    error: Optional[str] = None


class Accion(BaseModel):
    """
    Acción sugerida para completar un expediente notarial.

    Las acciones forman un DAG: 'depends_on' lista los 'key' de acciones que deben
    completarse antes de que ésta pueda iniciarse. El campo 'key' es el identificador
    único dentro del ticket y es la referencia usada por otras acciones en 'depends_on'.
    """

    key: Optional[str] = Field(
        default=None,
        description=(
            "Identificador único corto en snake_case dentro del ticket. "
            "Usado como referencia en 'depends_on' de otras acciones. "
            "Ejemplo: 'verificar_identidad_partes', 'redactar_escritura'. "
            "Se genera automáticamente desde 'title' si no se proporciona."
        ),
    )
    title: str = Field(description="Título claro y accionable de la acción")
    description: Optional[str] = Field(
        default=None,
        description="Descripción detallada: qué se debe hacer exactamente y por qué",
    )
    status: Literal["pending", "completed", "blocked", "errored"] = Field(
        default="pending",
        description=(
            "Estado de la acción. 'blocked' se asigna automáticamente si algún "
            "elemento de 'depends_on' no está completado."
        ),
    )
    depends_on: List[str] = Field(
        default_factory=list,
        description=(
            "Lista de 'key' de acciones que deben completarse antes de poder iniciar ésta. "
            "Deja vacío si la acción es independiente. "
            "Ejemplo: ['verificar_identidad_partes'] significa que esta acción "
            "sólo puede iniciarse cuando 'verificar_identidad_partes' esté completada."
        ),
    )
    order: Optional[int] = Field(
        default=None,
        description=(
            "Orden de ejecución sugerido (0-indexed) para acciones sin dependencias explícitas. "
            "Las acciones con dependencias se ordenan automáticamente por el grafo."
        ),
    )
    metadata: Optional[dict] = Field(
        default=None,
        description="Metadatos adicionales en formato JSON (opcional)",
    )

    @model_validator(mode="before")
    @classmethod
    def _auto_key(cls, data: Any) -> Any:
        if isinstance(data, dict) and not data.get("key") and data.get("title"):
            slug = re.sub(r"[^a-z0-9]+", "_", data["title"].lower()).strip("_")[:50]
            data = {**data, "key": slug}
        return data


class SuggestedActionsResponse(BaseModel):
    """Structured output produced by the suggest_actions node."""

    acciones: List[Accion] = Field(
        description="Lista ordenada de acciones necesarias para completar el expediente. "
        "Las acciones forman un DAG donde 'depends_on' expresa el orden de ejecución obligatorio."
    )


class AttachmentDownloadResults(BaseModel):
    """Structured output produced by the download_attachments deep agent."""

    results: List[AttachmentDownloadResult] = Field(
        description=(
            "Resultado de cada intento de descarga. "
            "Para cada adjunto del email, incluye si se descargó correctamente, "
            "la ruta donde se guardó en el workspace (/adjuntos/...) o el error."
        )
    )


class TriageDecision(BaseModel):
    """
    Single structured output produced by the research node deep agent.
    Contains the action to perform and the full ticket payload.
    """

    action: Literal["create", "patch", "discard"] = Field(
        description="Action to execute: 'create' new ticket, 'patch' existing ticket, or 'discard' irrelevant event"
    )
    ticket: TicketDraft = Field(
        description=(
            "Full ticket payload. For 'patch', ticket.id must be set to an existing ticket UUID. "
            "For 'create', ticket.id should be left empty (system generates it). "
            "ticket.attachments must list all attachments found in the email."
        )
    )


class DeterministicTriageState(MessagesState):
    """State used by the deterministic triage graph implementation."""

    triage: Optional[TriageDecision] = None
    persisted_ticket_id: Optional[str] = None
    workspace_id: Optional[str] = None
    suggested_actions: List[Accion] = Field(default_factory=list)
    download_results: List[AttachmentDownloadResult] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    retry_count: int = 0
    max_retries: int = 2
    id_validation_error: Optional[str] = None
    last_persist_status: Literal["ok", "id_invalid", "error"] = "ok"


class CrearTicketInput(BaseModel):
    """Schema de entrada para crear un ticket con acciones opcionales."""

    titulo: str = Field(description="Título del ticket")
    descripcion: str = Field(description="Descripción detallada del ticket")
    nombre_cliente: str = Field(description="Nombre del cliente")
    correo_cliente: str = Field(description="Email del cliente que envió la solicitud")
    prioridad: str = Field(default="medium", description="Prioridad del ticket: 'low', 'medium', 'high', 'urgent'")
    acciones: Optional[List[Accion]] = Field(default=None, description="Lista opcional de acciones sugeridas")


class GestionarAccionesInput(BaseModel):
    """Schema de entrada para gestionar acciones de un ticket."""

    ticket_id: str = Field(description="ID del ticket al que se le gestionarán las acciones")
    acciones: List[Accion] = Field(description="Lista de acciones a agregar")
    modo: Literal["append", "insert"] = Field(default="append", description="Modo de gestión")


class InputTriageState(MessagesState):
    gmail_triage_event: dict
    outlook_triage_event: dict


class TriageState(InputTriageState):
    ticket: Ticket


class OutputTriageState(TypedDict):
    ticket: Ticket
