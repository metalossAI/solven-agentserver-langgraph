from dataclasses import dataclass
from typing import Optional, List
from langgraph.graph import MessagesState

class SolvenState(MessagesState):
    """
    The state of the agent.
    """
    # Steps Update
    steps: Optional[List[str]] = None
    # Documento
    document: Optional[str] = None
    # Contenido
    content: Optional[str] = None


@dataclass
class SolvenContext:
    thread_id: str
    user_id: str
    tenant_id: str