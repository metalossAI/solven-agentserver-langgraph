from dataclasses import dataclass
from typing import Optional, List, Sequence, Annotated, Any
from langgraph.graph import MessagesState
from langgraph.graph.ui import AnyUIMessage, ui_message_reducer

class SolvenState(MessagesState):
    """
    The state of the agent.
    """
    ui: Annotated[Sequence[AnyUIMessage], ui_message_reducer]


@dataclass
class SolvenContext:
    thread_id: str
    user_id: str
    tenant_id: str