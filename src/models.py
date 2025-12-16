from dataclasses import dataclass
from pydantic import BaseModel, Field
from typing import Optional, List, Sequence, Annotated, Any
from langgraph.graph import MessagesState
from langgraph.graph.ui import AnyUIMessage, ui_message_reducer
from datetime import datetime
from typing import Literal

class SolvenState(MessagesState):
    """
    The state of the agent.
    """
    ui: Annotated[Sequence[AnyUIMessage], ui_message_reducer]


@dataclass
class AppContext:
    thread_id: Optional[str]
    user_id: str
    tenant_id: str
    initial_context: Optional[dict] = None

# Store Models to ensure orderd long term memory
class Event(BaseModel):
    id : str
    origin : str
    status : Literal["open", "closed"] = "open"
    description : str

class EventQueue(BaseModel):
    events: List[Event]

class ThreadContext(BaseModel):
    title : str = Field(description="Title of the thread")
    description : str = Field(description="Initial description of the thread")
    status_report : str = Field(description="Current status report of the thread")
    key_notes : List[str] = Field(default_factory=list, description="Key notes of the thread")
    last_updated : datetime = Field(default_factory=datetime.now)
    created_at : datetime = Field(default_factory=datetime.now)

class ThreadSummary(BaseModel):
    context : ThreadContext = Field(description="Up to date context of the thread")
    events : EventQueue = Field(description="Events of the thread")
    last_updated : datetime = Field(default_factory=datetime.now, description="Last updated timestamp")
    created_at : datetime = Field(default_factory=datetime.now, description="Created timestamp")


class Ticket(BaseModel):
    id: str
    title: str
    description: str
    related_threads: Optional[List[str]] = None
    status: Literal["open", "closed"] = "open"
    updated_at: datetime = Field(default_factory=datetime.now)