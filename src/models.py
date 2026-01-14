from pydantic import BaseModel, Field
from typing import Optional, List, Sequence, Annotated, Any, Dict
from langgraph.graph import MessagesState
from langgraph.graph.ui import AnyUIMessage, ui_message_reducer
from datetime import datetime
from typing import Literal

from deepagents import SubAgent

class SolvenState(MessagesState):
    """
    The state of the agent.
    """
    ui: Annotated[Sequence[AnyUIMessage], ui_message_reducer]

class Thread(BaseModel):
    id : str
    title : Optional[str] = None
    description : Optional[str] = None

class User(BaseModel):
    id : str
    name : str
    email : str
    role : str
    company_id : str



class AppContext(BaseModel):
    model_config = {"arbitrary_types_allowed": True}
    
    thread: Optional[Thread] = None
    user: Optional[User] = None
    company_id: Optional[str] = None
    backend : Optional[Any] = None  # S3Backend - using Any to avoid schema issues
    ticket: Optional['Ticket'] = None # the upstandig ticket context which will serve as link wiht for customer communications

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
    status: Literal["open", "ongoing", "closed", "deleted"] = "open"
    updated_at: datetime = Field(default_factory=datetime.now)

class Skill(BaseModel):
    category : Literal["escrituras", "atencion"]

class SkillResponse(BaseModel):
    skills: List[Skill]