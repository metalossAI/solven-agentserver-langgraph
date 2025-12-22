from pydantic import BaseModel, Field
from langchain.agents import AgentState
from typing import Annotated, Optional
from langgraph.graph import add_messages
from src.backend import S3Backend

def replace_value(left: Optional[str], right: Optional[str]) -> Optional[str]:
    """Reducer that replaces the old value with the new one"""
    return right if right is not None else left
    
class SkillsState(AgentState):
    """
    State for the skilled legal writer agent.
    Tracks currently loaded skill.
    """
    current_skill: Annotated[Optional[str], replace_value] = Field(
        default=None,
        description="Currently loaded skill path (e.g., 'escrituras/compraventa')"
    )
