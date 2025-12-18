from pydantic import BaseModel, Field
from langchain.agents import AgentState
from typing import Annotated, Optional
from src.backend import S3Backend
    
class SkillsState(AgentState):
    """
    State for the skilled legal writer agent.
    Tracks currently loaded skill and its content.
    """
    current_skill: Optional[str] = Field(
        default=None,
        description="Currently loaded skill path (e.g., 'escrituras/compraventa')"
    )
    skill_content: Optional[str] = Field(
        default=None,
        description="Content of the loaded SKILL.md file"
    )
